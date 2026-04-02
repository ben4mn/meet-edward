"""Router for conversation management endpoints."""

import asyncio
import logging
from fastapi import APIRouter, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

logger = logging.getLogger(__name__)

from services.conversation_service import (
    get_conversations,
    get_conversation,
    update_conversation,
    delete_conversation,
    search_conversations,
)
from services.checkpoint_store import get_messages
from services.graph import get_legacy_graph

router = APIRouter()


class ConversationResponse(BaseModel):
    id: str
    title: str
    source: str = "user"  # "user" | "scheduled_event" | "external_message"
    channel: str = "text"  # "text" | "voice"
    notified_user: bool = False  # True if Edward tried to get user's attention
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse]
    has_more: bool = False


class MessageAttachmentResponse(BaseModel):
    file_id: Optional[str] = None
    filename: str
    mime_type: str
    size: Optional[int] = None


class MessageResponse(BaseModel):
    role: str
    content: str
    attachments: Optional[List[MessageAttachmentResponse]] = None
    is_trigger: bool = False
    trigger_type: Optional[str] = None


class CCSessionSummaryResponse(BaseModel):
    task_id: str
    description: str
    status: str
    result_summary: Optional[str] = None
    error: Optional[str] = None


class ConversationWithMessagesResponse(BaseModel):
    id: str
    title: str
    source: str = "user"
    channel: str = "text"
    notified_user: bool = False
    created_at: datetime
    updated_at: datetime
    message_count: int
    messages: List[MessageResponse]
    cc_sessions: List[CCSessionSummaryResponse] = []


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    include_scheduled: bool = False,
    search: Optional[str] = None,
):
    """List all conversations, ordered by most recently updated.

    Args:
        limit: Maximum number of conversations to return
        offset: Number of conversations to skip
        include_scheduled: If True, include scheduled event conversations (default: False)
        search: Optional search query for full-text search over title and tags
    """
    if search and search.strip():
        conversations, total = await search_conversations(
            query=search.strip(),
            limit=limit,
            offset=offset,
            include_scheduled=include_scheduled,
        )
        has_more = (offset + limit) < total
    else:
        conversations, has_more = await get_conversations(
            limit=limit, offset=offset, include_scheduled=include_scheduled
        )

    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                id=c.id,
                title=c.title,
                source=c.source or "user",
                channel=c.channel or "text",
                notified_user=c.notified_user or False,
                created_at=c.created_at,
                updated_at=c.updated_at,
                message_count=c.message_count,
            )
            for c in conversations
        ],
        has_more=has_more,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationWithMessagesResponse)
async def get_conversation_with_messages(conversation_id: str):
    """Get a conversation with its messages from the checkpoint."""
    conversation = await get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages from checkpoint store (new format: dicts)
    messages = []
    try:
        raw_messages = await get_messages(conversation_id)

        # If empty, try legacy LangGraph checkpoint for old conversations
        if not raw_messages:
            legacy_graph = await get_legacy_graph()
            if legacy_graph:
                try:
                    config = {"configurable": {"thread_id": conversation_id}}
                    state = await asyncio.wait_for(legacy_graph.aget_state(config), timeout=10.0)
                    if state and state.values and "messages" in state.values:
                        for msg in state.values["messages"]:
                            role = "user"
                            if hasattr(msg, 'type'):
                                if msg.type in ('system', 'tool'):
                                    continue
                                if msg.type == "ai":
                                    role = "assistant"
                            raw_messages.append({"role": role, "content": msg.content if hasattr(msg, 'content') else str(msg)})
                except asyncio.TimeoutError:
                    logger.warning(f"Legacy aget_state timed out for conversation {conversation_id}")
                except Exception as e:
                    logger.warning(f"Legacy graph fallback failed: {e}")

        for msg in raw_messages:
            try:
                role = msg.get("role", "user")
                # Skip system and tool_result messages
                if role not in ("user", "assistant"):
                    continue

                raw_content = msg.get("content", "")
                attachments_out: Optional[List[MessageAttachmentResponse]] = None

                # Read attachment metadata from message metadata (if present)
                msg_meta = msg.get("metadata", {}) or {}
                kwargs_attachments = msg_meta.get("attachments", [])
                meta_by_index = {
                    m["block_index"]: m for m in kwargs_attachments
                } if kwargs_attachments else {}

                if isinstance(raw_content, list):
                    text_parts = []
                    attachment_list = []
                    for block_idx, block in enumerate(raw_content):
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            if block_type == "text":
                                text_parts.append(block.get("text", ""))
                            elif block_type == "tool_use":
                                continue  # Skip tool_use blocks in display
                            elif block_type == "tool_result":
                                continue  # Skip tool_result blocks in display
                            elif block_type == "image":
                                source = block.get("source", {})
                                meta = meta_by_index.get(block_idx, {})
                                attachment_list.append(MessageAttachmentResponse(
                                    file_id=meta.get("file_id") or block.get("file_id"),
                                    filename=meta.get("filename") or block.get("filename", "image"),
                                    mime_type=meta.get("mime_type") or source.get("media_type", "image/png"),
                                ))
                            elif block_type == "file":
                                source = block.get("source", {})
                                meta = meta_by_index.get(block_idx, {})
                                attachment_list.append(MessageAttachmentResponse(
                                    file_id=meta.get("file_id") or block.get("file_id"),
                                    filename=meta.get("filename") or block.get("filename", "file"),
                                    mime_type=meta.get("mime_type") or source.get("media_type", "application/octet-stream"),
                                ))
                        else:
                            text_parts.append(str(block))
                    content = "".join(text_parts)
                    if attachment_list:
                        attachments_out = attachment_list
                else:
                    content = raw_content if isinstance(raw_content, str) else str(raw_content)

                # Detect trigger messages and flag them instead of filtering
                is_trigger = False
                trigger_type = None
                stripped = content.strip()
                if stripped.startswith("[SCHEDULED EVENT]"):
                    is_trigger = True
                    trigger_type = "scheduled_event"
                elif stripped.startswith("[HEARTBEAT EVENT]"):
                    is_trigger = True
                    trigger_type = "heartbeat"

                if content.strip() or attachments_out:
                    messages.append(MessageResponse(
                        role=role,
                        content=content,
                        attachments=attachments_out,
                        is_trigger=is_trigger,
                        trigger_type=trigger_type,
                    ))
            except Exception as e:
                print(f"Skipping message in checkpoint: {e}")
                continue
    except Exception as e:
        print(f"Error getting messages from checkpoint: {e}")

    # Load CC session summaries for this conversation
    cc_sessions: List[CCSessionSummaryResponse] = []
    try:
        from services.orchestrator_service import list_tasks
        tasks = await list_tasks(parent_conversation_id=conversation_id)
        for t in tasks:
            if t.get("task_type") == "cc_session":
                cc_sessions.append(CCSessionSummaryResponse(
                    task_id=t["id"],
                    description=t.get("task_description", ""),
                    status=t.get("status", "unknown"),
                    result_summary=t.get("result_summary"),
                    error=t.get("error"),
                ))
    except Exception as e:
        logger.warning(f"Failed to load CC sessions for {conversation_id}: {e}")

    return ConversationWithMessagesResponse(
        id=conversation.id,
        title=conversation.title,
        source=conversation.source or "user",
        channel=conversation.channel or "text",
        notified_user=conversation.notified_user or False,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=conversation.message_count,
        messages=messages,
        cc_sessions=cc_sessions,
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation_endpoint(conversation_id: str, request: UpdateConversationRequest):
    """Update a conversation's title."""
    conversation = await update_conversation(conversation_id, title=request.title)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        source=conversation.source or "user",
        channel=conversation.channel or "text",
        notified_user=conversation.notified_user or False,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=conversation.message_count,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str):
    """Delete a conversation and its checkpoint."""
    # Delete from conversations table
    deleted = await delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Note: LangGraph checkpoints are not deleted here
    # They will be orphaned but that's acceptable for now

    return {"status": "deleted", "id": conversation_id}
