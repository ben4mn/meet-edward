"""R/G TDD for Phase A — wake-up pushes leave substantive chat threads.

The bug Ellis hit: a whisper push said "you have a few unread notifications
worth looking at." Tapping it landed in a thought conversation, but the
conversation's initial assistant message was the same 8-word body — no
actual list of unreads. When Ellis replied asking "what unreads?", Edward
had no memory of the signal pack and was useless.

Fix:

1. Wake-up LLM produces TWO text fields — ``push_body`` (terse, ≤140
   chars) and ``chat_opener`` (substantive, includes the items).
2. Push uses ``push_body``. Conversation's ``initial_message`` is the
   ``chat_opener``.
3. Conversation has a new ``context_pack`` column holding the
   wake-up's signal_pack JSON, so Ellis's first reply hands Edward back
   the original context.
"""

from __future__ import annotations

import json

import pytest


@pytest.mark.integration
async def test_create_thought_conversation_accepts_context_pack(clean_db):
    from services.thought_conversation_service import create_thought_conversation
    from services.database import async_session, ConversationModel
    from sqlalchemy import select

    pack = {"unread_messages": [{"sender": "Rowan", "summary": "still on for 7?"}]}
    conv_id = await create_thought_conversation(
        title="A few unreads worth a look",
        narrative=(
            "You've got 3 unread iMessages that read as time-sensitive:\n"
            "- Rowan: 'still on for 7?' (2h ago)\n"
            "- Sam: 'sent the rental link, lmk' (3h ago)\n"
            "- Sarah: 'happy birthday!' (5h ago)"
        ),
        context_pack=pack,
    )

    async with async_session() as session:
        conv = (await session.execute(
            select(ConversationModel).where(ConversationModel.id == conv_id)
        )).scalar_one()
    assert conv.source == "edward_thought"
    stored_pack = json.loads(conv.context_pack)
    assert stored_pack == pack
