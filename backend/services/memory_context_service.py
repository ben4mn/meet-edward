"""Per-chat memory peek — anticipation hook for the chat UI.

When the owner opens a heartbeat-sourced conversation, we want to surface
the 1-2 most-relevant memories for that contact in a slim card above the
compose box. Helps them pick up where they left off without scrolling
through chat history.

Reuses ``memory_service.retrieve_memories`` so we get the same hybrid
vector + BM25 ranking the LLM uses. Query is the contact name when
available, otherwise the chat identifier.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select

from services.database import (
    IMessageChatConversationModel,
    async_session,
)
from services.memory_service import retrieve_memories


async def _lookup_chat_name(chat_identifier: str) -> Optional[str]:
    if not chat_identifier:
        return None
    async with async_session() as session:
        row = (await session.execute(
            select(IMessageChatConversationModel)
            .where(IMessageChatConversationModel.chat_identifier == chat_identifier)
        )).scalar_one_or_none()
        return row.chat_name if row is not None else None


async def context_for_chat(
    chat_identifier: str,
    limit: int = 2,
) -> dict[str, Any]:
    """Return up to ``limit`` highest-relevance memories for the chat's
    contact, plus the chat's stored display name.

    The frontend renders this above the compose input on
    ``source="heartbeat"`` conversations.
    """
    contact_name = await _lookup_chat_name(chat_identifier)
    query = contact_name or chat_identifier
    if not query:
        return {"contact_name": None, "memories": []}
    try:
        memories = await retrieve_memories(
            query=query,
            limit=max(1, min(limit, 5)),
            min_score=0.25,
            update_access=False,
        )
    except Exception:
        memories = []
    # What Edward has read or been told about this contact (Phase 8: the
    # relationship fact graph is folded into ``memories about=contact``).
    try:
        contact_memories = await retrieve_memories(
            query=query,
            limit=3,
            min_score=0.05,  # the about=contact filter does the narrowing; a name alone scores low
            update_access=False,
            about="contact",
        )
        seen = {m.id for m in memories}
        fact_snippets = [m.content for m in contact_memories if m.id not in seen]
    except Exception:
        fact_snippets = []
    return {
        "contact_name": contact_name,
        "memories": [
            {
                "id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "importance": m.importance,
            }
            for m in memories
        ],
        "relationship_facts": fact_snippets,
    }
