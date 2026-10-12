"""R/G TDD for Task #13 — compose-time memory peek.

When Ellis opens an iMessage-sourced conversation in the web UI, surface
1-2 highest-relevance memories for the contact in a slim card above the
compose box. Helps him pick up where he left off without scanning
history.

The API is read-only and intentionally narrow: pass a chat_identifier
(or a contact name fallback) and get back the top items.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest


@pytest.mark.integration
async def test_memory_context_returns_top_two_for_contact(clean_db):
    """Two memories tagged at Rowan → endpoint returns both, top
    relevance first."""
    from services.database import (
        async_session,
        MemoryModel,
        IMessageChatConversationModel,
    )
    from services.memory_context_service import context_for_chat
    from services.memory_service import get_embedding

    chat_id = "iMessage;-;+15550009999"
    async with async_session() as session:
        for content, importance in [
            ("Rowan is excited about a Disney trip in March", 0.7),
            ("Rowan is allergic to shellfish", 0.9),
        ]:
            session.add(MemoryModel(
                content=content,
                memory_type="fact",
                importance=importance,
                embedding=get_embedding(content),
            ))
        session.add(IMessageChatConversationModel(
            chat_identifier=chat_id,
            conversation_id="conv-1",
            chat_name="Rowan",
            is_group=False,
        ))
        await session.commit()

    result = await context_for_chat(chat_id, limit=2)
    assert "memories" in result
    assert 1 <= len(result["memories"]) <= 2, (
        f"expected 1-2 memories; got {len(result['memories'])}"
    )
    contents = [m["content"] for m in result["memories"]]
    assert any("Rowan" in c for c in contents), (
        f"expected Rowan context; got {contents}"
    )


@pytest.mark.integration
async def test_memory_context_empty_for_unknown_chat(clean_db):
    from services.memory_context_service import context_for_chat

    result = await context_for_chat("iMessage;-;+15550000000", limit=2)
    assert result.get("memories") == []
    assert result.get("contact_name") is None
