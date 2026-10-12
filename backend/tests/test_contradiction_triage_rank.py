"""What the daily contradiction triage raises with Ellis first (2026-09-27).

It took the open flags oldest first. With one question a day (see
``test_contradiction_note_dedupe.py``) the order decides what Ellis is asked
at all: a conflict with something he said himself, between rows that reach
hundreds of turns, now comes before an old pair of chatter —
``consolidation_service.contradiction_priority``. And a flag whose side has
lapsed since it was raised is closed without asking anyone.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select


CHAT_REF = json.dumps({"kind": "extraction", "profile": "chat", "turn_source": "chat"})
WAKE_REF = json.dumps({"kind": "extraction", "profile": "mind", "turn_source": "mind"})


async def _memory(content: str, **kw) -> str:
    from services.database import MemoryModel, async_session

    fields = dict(memory_type="fact", importance=0.5, origin="read", source_type="inferred")
    fields.update(kw)
    async with async_session() as session:
        m = MemoryModel(content=content, **fields)
        session.add(m)
        await session.commit()
        return m.id


async def _flag(a: str, b: str, created_at: datetime) -> str:
    from services.database import MemoryFlagModel, async_session

    async with async_session() as session:
        f = MemoryFlagModel(memory_id=a, related_memory_id=b, flag_type="contradiction",
                            description="conflict", resolved=False, created_at=created_at)
        session.add(f)
        await session.commit()
        return f.id


class _Conflict:
    def __init__(self):
        self.flags = []

    async def ainvoke(self, messages):
        self.flags.append(messages[-1].content)

        class _R:
            content = json.dumps({"classification": "real_conflict", "question": "Which is current?"})
        return _R()


@pytest.mark.integration
async def test_owners_own_word_is_raised_before_older_chatter(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts
    from services.conversation_service import create_conversation

    await create_conversation("conv-owner", title="Ellis", source="user")
    old = datetime.utcnow() - timedelta(days=5)
    chatter = await _flag(await _memory("Rowan is at the gym"), await _memory("Rowan is at home"), old)
    # The other side is a wake's row: were it one a label outranks, the pair
    # would be consolidation's to settle, not a question (2026-09-27).
    owners = await _flag(await _memory("Ellis wants Edward to reply in the Fantasy group", origin="heard",
                                     source_type="user_stated", source_conversation_id="conv-owner"),
                       await _memory("Edward sends Ellis in-app reminders only", source_ref=WAKE_REF),
                       old + timedelta(days=2))
    asked = []

    async def push(decision, flag):
        asked.append(flag.id)

    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: _Conflict())
    monkeypatch.setattr(cts, "_push_disambiguation", push)

    await cts.triage_unresolved(limit=10, max_asks=1)

    assert asked == [owners], "the newer flag on Ellis's own word is the one question asked"
    assert chatter not in asked


@pytest.mark.integration
async def test_owners_own_word_is_raised_before_a_heavily_used_pair(clean_db, monkeypatch):
    """Weight and reach order the pairs that are not Ellis's; they never
    outrank one that is."""
    from services import contradiction_triage_service as cts

    old = datetime.utcnow() - timedelta(days=5)
    loud = await _flag(await _memory("Edward speaks only as Edward", importance=1.0, used_count=1200),
                       await _memory("Ellis finds his message-reading AI bot creepy", importance=0.9,
                                     used_count=937), old)
    # Ellis's own word from his chat turn, against a wake's row. It was a pin
    # until the integration review (2026-09-27): a pin is settled and never
    # asked about (tests/test_contradiction_settled_rows.py).
    owners = await _flag(await _memory("Ellis wants Edward to reply in the Fantasy group", importance=0.5,
                                     origin="heard", source_ref=CHAT_REF),
                       await _memory("Edward sends Ellis in-app reminders only", importance=0.5,
                                     source_ref=WAKE_REF), old)
    asked = []

    async def push(decision, flag):
        asked.append(flag.id)

    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: _Conflict())
    monkeypatch.setattr(cts, "_push_disambiguation", push)

    await cts.triage_unresolved(limit=10, max_asks=1)

    assert asked == [owners], "Ellis's own word first, however much the other pair is used"
    assert loud not in asked


@pytest.mark.integration
async def test_a_reminder_echo_is_not_raised_as_owners_word(clean_db, monkeypatch):
    """A heard + user_stated row a reminder turn minted (the blanket
    "in-app only" rules) is not Ellis's word; one heard in his own chat is,
    read from where the row came from."""
    from services import contradiction_triage_service as cts
    from services.conversation_service import create_conversation

    await create_conversation("conv-owner", title="Ellis", source="user")
    await create_conversation("conv-reminder", title="Reminder", source="scheduled_event")
    old = datetime.utcnow() - timedelta(days=5)
    echo = await _flag(await _memory("Ellis instructs Edward to use in-app/chat reminders only", importance=0.9,
                                     used_count=4000, origin="heard", source_type="user_stated",
                                     source_conversation_id="conv-reminder"),
                       await _memory("Edward speaks only as Edward", importance=0.9, used_count=2500), old)
    owners = await _flag(await _memory("Ellis wants Edward to reply in the Fantasy group", importance=0.5,
                                     origin="heard", source_type="user_stated", source_conversation_id="conv-owner"),
                       await _memory("Edward stays out of group chats", importance=0.5, source_ref=WAKE_REF),
                       old + timedelta(days=1))
    asked = []

    async def push(decision, flag):
        asked.append(flag.id)

    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: _Conflict())
    monkeypatch.setattr(cts, "_push_disambiguation", push)

    await cts.triage_unresolved(limit=10, max_asks=1)

    assert asked == [owners], "Ellis's word is what he said in his own chat, not a reminder's echo"
    assert echo not in asked


@pytest.mark.integration
async def test_a_flag_whose_side_lapsed_is_closed_without_asking(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts
    from services.database import MemoryFlagModel, MemoryModel, async_session

    stamp = datetime.utcnow() - timedelta(days=1)
    live = await _memory("Edward speaks only as Edward")
    lapsed = await _memory("Edward sends Ellis in-app reminders only", valid_until=stamp)
    fid = await _flag(live, lapsed, datetime.utcnow() - timedelta(days=3))
    llm = _Conflict()
    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: llm)

    results = await cts.triage_unresolved(limit=10, priority="surplus", ask_via="note")

    assert [r["action"] for r in results] == ["lapsed_resolved"]
    assert llm.flags == [], "nothing to ask the model or Ellis"
    async with async_session() as session:
        flag = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == fid))).scalar_one()
        rows = {m.id: m.valid_until for m in (await session.execute(select(MemoryModel))).scalars().all()}
    assert flag.resolved is True
    assert rows[live] is None and rows[lapsed] == stamp, "closing the flag touches no memory"
