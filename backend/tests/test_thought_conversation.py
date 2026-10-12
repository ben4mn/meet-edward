"""R/G TDD for the missing piece: every proactive push must land on a
real conversation containing Edward's reasoning.

Today the wakeup-tick push URL points at `/settings/journal` (a static
page) and the dispatcher digest URL points at *one* of the source iMessage
chats — neither lets Ellis tap in and start talking *to Edward* about
what he was thinking.

This module ships:

* ``services.thought_conversation_service.create_thought_conversation``
  — creates a conversation with ``source="edward_thought"`` and an
  ``initial_message`` body that the conversations API surfaces as
  Edward's first assistant turn.
* Conversations router synthesizes that initial message when the
  LangGraph checkpoint is empty.
* Wakeup push/whisper and the dispatcher's digest path now create
  thought conversations and point pushes there.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest


@pytest.mark.integration
async def test_create_thought_conversation_persists_narrative(clean_db):
    from services.thought_conversation_service import create_thought_conversation
    from services.database import async_session, ConversationModel
    from sqlalchemy import select

    conv_id = await create_thought_conversation(
        title="3 messages while you were busy",
        narrative=(
            "Rowan wants to confirm 7pm. Sam re-sent the rental link. "
            "Sarah said hi. You haven't replied to any of them."
        ),
    )

    async with async_session() as session:
        conv = (await session.execute(
            select(ConversationModel).where(ConversationModel.id == conv_id)
        )).scalar_one()
    assert conv.source == "edward_thought"
    assert conv.title.startswith("3 messages")
    assert "Rowan" in (conv.initial_message or "")


@pytest.mark.integration
async def test_thought_conversation_returns_narrative_as_first_message(clean_db):
    """The conversations API should surface initial_message as a
    synthetic assistant turn when there are no real checkpoint messages.
    """
    from services.thought_conversation_service import create_thought_conversation
    from routers.conversations import get_conversation_with_messages

    conv_id = await create_thought_conversation(
        title="You missed some pings",
        narrative="Rowan: 'still coming over?' — 6h ago, no reply yet.",
    )

    resp = await get_conversation_with_messages(conv_id)
    assert resp.messages, "synthetic narrative message should appear"
    first = resp.messages[0]
    assert first.role == "assistant"
    assert "Rowan" in first.content
