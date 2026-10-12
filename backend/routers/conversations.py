"""Router for conversation management endpoints."""

import asyncio
import logging
from services.timezone_utils import format_iso_utc
from fastapi import APIRouter, HTTPException
from typing import Any, Dict, Optional, List
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
from services.graph import get_graph

router = APIRouter()


class ThoughtResponse(BaseModel):
    """A journal entry: a wake that chose to act (thought journal,
    2026-09-25; ``services/thought_journal.py``). ``ledger_id`` is the
    wake's ``ledger_events`` row — ``POST /api/mind/verdict`` takes it —
    and ``verdict`` is the owner's latest on it."""
    kind: str = "mind_action"
    ledger_id: int
    reason: str
    acted_at: str  # ISO 8601 UTC with Z suffix
    outward: bool
    verdict: Optional[str] = None  # "right_call" | "wrong_call" | None
    # A message delivered to a person or a push that reached the owner
    # (``thought_journal.reached_someone``; followups L3, 2026-09-27) — the
    # "reached out" chip reads this, never ``outward``, which a Today note
    # or a held send also sets. None: an entry written before the field.
    reached: Optional[bool] = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    source: str = "user"  # "user" | "scheduled_event" | "external_message"
    channel: str = "text"  # "text" | "voice"
    notified_user: bool = False  # True if Edward tried to get user's attention
    created_at: str  # ISO 8601 UTC with Z suffix
    updated_at: str
    last_activity_at: Optional[str] = None  # latest message in a heartbeat thread
    message_count: int
    thought: Optional[ThoughtResponse] = None  # set on journal entries only


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
    # The turn's thinking and tool use, kept by the streaming loop on its
    # answer (services/graph/turn_trace.py): reasoning, tools, memory_hits.
    trace: Optional[Dict[str, Any]] = None


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
    created_at: str
    updated_at: str
    message_count: int
    messages: List[MessageResponse]
    cc_sessions: List[CCSessionSummaryResponse] = []
    thought: Optional[ThoughtResponse] = None  # set on journal entries only


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    include_scheduled: bool = False,
    search: Optional[str] = None,
    group: Optional[str] = None,
):
    """List all conversations, ordered by most recently updated.

    Args:
        limit: Maximum number of conversations to return
        offset: Number of conversations to skip
        include_scheduled: If True, include scheduled event conversations (default: False)
        search: Optional search query for full-text search over title and tags
        group: "yours" (the owner's chats + inbound) or "edwards" (everything Edward started)
    """
    if search and search.strip():
        conversations, total = await search_conversations(
            query=search.strip(),
            limit=limit,
            offset=offset,
            include_scheduled=include_scheduled,
            group=group,
        )
        has_more = (offset + limit) < total
    else:
        conversations, has_more = await get_conversations(
            limit=limit, offset=offset, include_scheduled=include_scheduled, group=group
        )

    thoughts = await _thought_fields(conversations)
    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                id=c.id,
                title=c.title,
                source=c.source or "user",
                channel=c.channel or "text",
                notified_user=c.notified_user or False,
                created_at=format_iso_utc(c.created_at),
                updated_at=format_iso_utc(c.updated_at),
                last_activity_at=(
                    format_iso_utc(getattr(c, "last_activity_at", None))
                    if getattr(c, "last_activity_at", None) else None
                ),
                message_count=c.message_count,
                thought=thoughts.get(str(c.id)),
            )
            for c in conversations
        ],
        has_more=has_more,
    )


async def _thought_fields(conversations) -> dict:
    """conversation id → ``thought`` for the journal entries among them,
    the owner's verdicts read in one query. A failure here never breaks a
    listing: the field is simply left out."""
    try:
        from services.thought_journal import thought_fields

        return await thought_fields(conversations)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"thought fields unavailable: {e}")
        return {}


def serialize_checkpoint_message(msg) -> Optional[MessageResponse]:
    """Turn one LangGraph checkpoint message into a MessageResponse.

    Returns None for system/tool messages and for empty messages. Pure:
    no DB, no graph. Attachment metadata lives in ``additional_kwargs``
    (written by ``_build_human_message``); voice-memo marker blocks are
    surfaced as audio attachments and kept out of the visible text.
    """
    if hasattr(msg, 'type') and msg.type in ('system', 'tool'):
        return None

    role = "user"
    if hasattr(msg, 'type'):
        if msg.type == "ai":
            role = "assistant"
        elif msg.type == "human":
            role = "user"

    raw_content = msg.content if hasattr(msg, 'content') else str(msg)
    attachments: Optional[List[MessageAttachmentResponse]] = None

    additional_kwargs = getattr(msg, 'additional_kwargs', {}) or {}
    kwargs_attachments = additional_kwargs.get("attachments", [])
    meta_by_index = {
        m["block_index"]: m for m in kwargs_attachments
    } if kwargs_attachments else {}

    if isinstance(raw_content, list):
        text_parts = []
        attachment_list = []
        for block_idx, block in enumerate(raw_content):
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            block_type = block.get("type", "")
            meta = meta_by_index.get(block_idx, {})
            if block_type == "text":
                if meta.get("dehydrated") and meta.get("file_id"):
                    # A PWA upload whose bytes left saved history
                    # (history_window.strip_media_blocks → media.dehydrate,
                    # 2026-10-11): still the photo chip, from file storage.
                    attachment_list.append(MessageAttachmentResponse(
                        file_id=meta.get("file_id"),
                        filename=meta.get("filename") or "image",
                        mime_type=meta.get("mime_type") or "image/jpeg",
                    ))
                elif meta.get("kind") == "voice_memo":
                    attachment_list.append(MessageAttachmentResponse(
                        file_id=meta.get("file_id"),
                        filename=meta.get("filename") or "voice-memo",
                        mime_type=meta.get("mime_type") or "audio/mp4",
                    ))
                else:
                    text_parts.append(block.get("text", ""))
            elif block_type == "image":
                source = block.get("source", {})
                attachment_list.append(MessageAttachmentResponse(
                    file_id=meta.get("file_id") or block.get("file_id"),
                    filename=meta.get("filename") or block.get("filename", "image"),
                    mime_type=meta.get("mime_type") or source.get("media_type", "image/png"),
                ))
            elif block_type in ("file", "document"):
                source = block.get("source", {})
                attachment_list.append(MessageAttachmentResponse(
                    file_id=meta.get("file_id") or block.get("file_id"),
                    filename=meta.get("filename") or block.get("filename", "file"),
                    mime_type=meta.get("mime_type") or source.get("media_type", "application/octet-stream"),
                ))
        content = "".join(text_parts)
        if attachment_list:
            attachments = attachment_list
    else:
        content = raw_content if isinstance(raw_content, str) else str(raw_content)

    from services.trigger_detection import detect_trigger
    is_trigger, trigger_type = detect_trigger(content)

    if not content.strip() and not attachments:
        return None
    from services.graph.turn_trace import trace_of

    return MessageResponse(
        role=role,
        content=content,
        attachments=attachments,
        is_trigger=is_trigger,
        trigger_type=trigger_type,
        trace=trace_of(msg),
    )


def with_opening(messages: List[MessageResponse], initial: Optional[str]) -> List[MessageResponse]:
    """``messages`` with Edward's opening words first.

    A thought conversation (a journal entry) opens with ``initial_message``.
    Until 2026-09-25 it was shown only while the checkpoint was empty, so
    the entry vanished from the thread the moment the owner replied to it. Now it
    is left alone when an assistant message already says it (the first
    reply checkpoints it) and put first otherwise. Pure."""
    text = (initial or "").strip()
    if not text:
        return messages
    if any(m.role == "assistant" and (m.content or "").strip() == text for m in messages):
        return messages
    return [MessageResponse(role="assistant", content=initial, attachments=None,
                            is_trigger=False, trigger_type=None)] + list(messages)


OPEN_ENTRY_TEXT = ("I'm still finishing the thought behind this — the full entry (what I saw, what I did "
                   "and why) will be here in a moment. Open it again shortly.")


def _open_entry_placeholder(conversation_id: str) -> Optional[ConversationWithMessagesResponse]:
    """A push or note from a wake links to the entry the wake writes when its
    turn ends (thought journal, 2026-09-25). Tapped before then, the link
    answers this instead of a 404."""
    try:
        from services.thought_journal import is_open_entry

        if not is_open_entry(conversation_id):
            return None
    except Exception:  # noqa: BLE001
        return None
    now = format_iso_utc(datetime.utcnow())
    return ConversationWithMessagesResponse(
        id=conversation_id, title="Edward is still writing this", source="edward_thought", channel="text",
        notified_user=False, created_at=now, updated_at=now, message_count=0,
        messages=[MessageResponse(role="assistant", content=OPEN_ENTRY_TEXT)],
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationWithMessagesResponse)
async def get_conversation_with_messages(conversation_id: str):
    """Get a conversation with its messages from the checkpoint."""
    conversation = await get_conversation(conversation_id)
    if not conversation:
        placeholder = _open_entry_placeholder(conversation_id)
        if placeholder is not None:
            return placeholder
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages from LangGraph checkpoint
    messages = []
    try:
        graph = await get_graph()
        config = {"configurable": {"thread_id": conversation_id}}
        try:
            state = await asyncio.wait_for(graph.aget_state(config), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(f"aget_state timed out for conversation {conversation_id}")
            state = None

        if state and state.values and "messages" in state.values:
            for msg in state.values["messages"]:
                try:
                    serialized = serialize_checkpoint_message(msg)
                except Exception as e:
                    print(f"Skipping message in checkpoint: {e}")
                    continue
                if serialized is not None:
                    messages.append(serialized)
    except Exception as e:
        print(f"Error getting messages from checkpoint: {e}")

    # Edward-initiated thought conversations (journal entries): his opening
    # words come first whatever the checkpoint holds. The first reply now
    # checkpoints them (graph/streaming._entry_turn_context); a thread the owner
    # replied to before that, or one trimmed past them, gets them back here.
    messages = with_opening(messages, getattr(conversation, "initial_message", None))

    # Clear notification flag when user reads the conversation
    if conversation.notified_user:
        try:
            from services.conversation_service import clear_user_notified
            await clear_user_notified(conversation_id)
        except Exception as e:
            logger.warning(f"Failed to clear notified_user for {conversation_id}: {e}")

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

    thoughts = await _thought_fields([conversation])
    return ConversationWithMessagesResponse(
        id=conversation.id,
        title=conversation.title,
        source=conversation.source or "user",
        channel=conversation.channel or "text",
        notified_user=conversation.notified_user or False,
        created_at=format_iso_utc(conversation.created_at),
        updated_at=format_iso_utc(conversation.updated_at),
        message_count=conversation.message_count,
        messages=messages,
        cc_sessions=cc_sessions,
        thought=thoughts.get(str(conversation.id)),
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation_endpoint(conversation_id: str, request: UpdateConversationRequest):
    """Update a conversation's title."""
    conversation = await update_conversation(conversation_id, title=request.title)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    thoughts = await _thought_fields([conversation])
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        source=conversation.source or "user",
        channel=conversation.channel or "text",
        notified_user=conversation.notified_user or False,
        created_at=format_iso_utc(conversation.created_at),
        updated_at=format_iso_utc(conversation.updated_at),
        message_count=conversation.message_count,
        thought=thoughts.get(str(conversation.id)),
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
