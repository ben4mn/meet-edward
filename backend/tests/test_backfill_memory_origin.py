"""The origin backfill writes nothing on a dry run and only the two known groups on apply."""

from __future__ import annotations

import pytest


def test_plan_lines_are_plain():
    from scripts.backfill_memory_origin import plan_backfill

    lines = plan_backfill(3, 2)
    assert lines[0].endswith("3 row(s)") and "read" in lines[0]
    assert lines[1].endswith("2 row(s)") and "thought" in lines[1]


@pytest.mark.integration
async def test_dry_run_writes_nothing_and_apply_stamps_only_known_groups(clean_db):
    from sqlalchemy import select

    from scripts.backfill_memory_origin import run
    from services.database import IMessageChatConversationModel, MemoryModel, async_session
    from services.memory_service import Memory, store_memory

    async with async_session() as s:
        s.add(IMessageChatConversationModel(chat_identifier="chat123", conversation_id="conv-hb"))
        await s.commit()

    hb = await store_memory(Memory(id=None, content="Parker's flight moved to 6pm", memory_type="context",
                                   importance=0.5, source_conversation_id="conv-hb"))
    pulse = await store_memory(Memory(id=None, content="Put the dinner reminder on the widget", memory_type="context",
                                      importance=0.4, source_conversation_id="edward-surfaces"))
    chat = await store_memory(Memory(id=None, content="Ellis's dog is Cricket", memory_type="fact",
                                     importance=0.8, source_conversation_id="user-chat-1"))

    report = await run(apply=False)
    assert report == {"heartbeat": 1, "pulse": 1, "applied": 0}
    async with async_session() as s:
        rows = {r.id: r for r in (await s.execute(select(MemoryModel))).scalars().all()}
    assert all(r.origin == "heard" and r.about == "owner" for r in rows.values())

    report = await run(apply=True)
    assert report["applied"] == 2
    async with async_session() as s:
        rows = {r.id: r for r in (await s.execute(select(MemoryModel))).scalars().all()}
    assert (rows[hb.id].origin, rows[hb.id].about) == ("read", "owner")
    assert (rows[pulse.id].origin, rows[pulse.id].about) == ("thought", "self")
    assert (rows[chat.id].origin, rows[chat.id].about) == ("heard", "owner")

    # Idempotent.
    report = await run(apply=True)
    assert report["applied"] == 0
