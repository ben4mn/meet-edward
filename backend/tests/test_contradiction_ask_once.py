"""A contradiction is asked about once, not on every wakeup.

Ellis (2026-09-07): "clear out any contradictions he keeps asking me about,
they are all old." The triage pushed a disambiguation question for every
``real_conflict`` flag and left the flag unresolved, so each wakeup
enrichment re-triaged the same three flags and asked again. Now a flag
carries an "[asked <date>]" marker after the first question, is left
alone while Ellis might still answer, and is resolved as stale after a week.
"""

from datetime import datetime, timedelta

import pytest

from services.contradiction_triage_service import (
    STALE_AFTER_DAYS,
    ask_policy,
    asked_at,
    mark_asked,
)

NOW = datetime(2026, 9, 7, 17, 30)

# Both rows are Ellis's own word (his chat turn, user_stated), so the pair is his
# call: only such a pair is asked, or reaches the model (integration review,
# 2026-09-27). Two default rows are a pair the label decides, left for
# consolidation.
OWNERS_WORD = dict(source_type="user_stated",
                 source_ref='{"kind": "extraction", "profile": "chat", "turn_source": "chat"}')


def test_fresh_flag_is_asked():
    assert ask_policy("Rowan prefers her phone; Ellis expects a flip phone", NOW) == "ask"
    assert ask_policy(None, NOW) == "ask"


def test_marked_flag_carries_a_parseable_timestamp():
    desc = mark_asked("Rowan prefers her phone", NOW)
    assert desc.startswith("Rowan prefers her phone")
    assert asked_at(desc) == NOW.replace(second=0, microsecond=0)


def test_marking_twice_keeps_one_marker():
    once = mark_asked("x", NOW)
    twice = mark_asked(once, NOW + timedelta(days=1))
    assert twice.count("[asked ") == 1
    assert asked_at(twice) == (NOW + timedelta(days=1)).replace(second=0, microsecond=0)


def test_recently_asked_flag_waits():
    desc = mark_asked("x", NOW)
    assert ask_policy(desc, NOW + timedelta(days=2)) == "wait"


def test_flag_asked_over_a_week_ago_is_stale():
    desc = mark_asked("x", NOW)
    assert ask_policy(desc, NOW + timedelta(days=STALE_AFTER_DAYS, minutes=1)) == "stale"
    assert STALE_AFTER_DAYS == 7


@pytest.mark.integration
async def test_triage_unresolved_asks_once_then_waits_then_resolves(clean_db, monkeypatch):
    from sqlalchemy import select
    from services import contradiction_triage_service as svc
    from services.database import MemoryFlagModel, MemoryModel, async_session

    async with async_session() as session:
        a = MemoryModel(content="Ellis wants a flip phone", memory_type="fact", importance=0.5, **OWNERS_WORD)
        b = MemoryModel(content="Ellis wants to keep his iPhone", memory_type="fact", importance=0.5, **OWNERS_WORD)
        session.add_all([a, b])
        await session.flush()
        flag = MemoryFlagModel(memory_id=a.id, related_memory_id=b.id, flag_type="contradiction",
                               description="phone conflict", resolved=False)
        session.add(flag)
        await session.commit()
        flag_id = flag.id

    llm_calls = []
    pushes = []

    async def fake_ask(flag, m_a, m_b):
        llm_calls.append(flag.id)
        return {"classification": "real_conflict", "question": "Flip phone or iPhone?"}

    async def fake_push(decision, flag):
        pushes.append(decision["question"])

    monkeypatch.setattr(svc, "_ask_llm", fake_ask)
    monkeypatch.setattr(svc, "_push_disambiguation", fake_push)

    first = await svc.triage_unresolved()
    assert [r["action"] for r in first] == ["asked_user"]
    assert pushes == ["Flip phone or iPhone?"]

    second = await svc.triage_unresolved()
    assert [r["action"] for r in second] == ["waiting_for_user"]
    assert len(llm_calls) == 1 and len(pushes) == 1, "asked exactly once"

    # A week later nobody answered: the flag is closed as stale, no new push.
    monkeypatch.setattr(svc, "_now", lambda: datetime.utcnow() + timedelta(days=8))
    third = await svc.triage_unresolved()
    assert [r["action"] for r in third] == ["stale_resolved"]
    async with async_session() as session:
        f = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id))).scalar_one()
        assert f.resolved is True
    assert len(pushes) == 1
