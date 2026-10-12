"""Thoughts decay; intentions hold while they are live — mind rebuild Phase 9.

A store that only grows is a store he eventually cannot read. A thought
(``origin="thought"``) defaults to ``temporary`` unless it is an
``intention``; an intention linked to a pending scheduled event is
protected from the forgetting pass; the Sunday reflection runs that pass
for real instead of a manual script.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("memory_type,origin,expected", [
    ("context", "thought", "temporary"),
    ("fact", "thought", "temporary"),
    ("preference", "thought", "temporary"),
    ("intention", "thought", "evolving"),
    ("fact", "heard", "timeless"),
    ("fact", None, "timeless"),
    ("context", "did", "temporary"),
])
def test_thoughts_default_to_temporary(memory_type, origin, expected):
    from services.memory_service import default_temporal_nature

    assert default_temporal_nature(memory_type, origin=origin) == expected


def test_coerce_uses_the_origin_for_the_default():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields({"content": "Ellis seems tired of ACME", "memory_type": "fact",
                                         "origin": "thought", "about": "owner"})
    assert fields["temporal_nature"] == "temporary"
    fields, _ = coerce_extracted_fields({"content": "Watch ACME", "memory_type": "intention",
                                         "origin": "thought", "about": "self"})
    assert fields["temporal_nature"] == "evolving"
    fields, _ = coerce_extracted_fields({"content": "Ellis's dog is Cricket", "memory_type": "fact"})
    assert fields["temporal_nature"] == "timeless"


def test_pending_intention_is_protected_from_decay():
    from services.memory_decay_service import is_protected, should_retire
    from tests.test_memory_decay import _mem

    intention = _mem(id="i1", memory_type="intention", temporal_nature="evolving",
                     created_at=NOW - timedelta(days=200))
    assert is_protected(intention, protected_ids={"i1"}) is True
    assert is_protected(intention) is False
    assert should_retire(intention, now=NOW, protected_ids={"i1"}) is False
    assert should_retire(intention, now=NOW) is True, "an intention nothing is waiting on ages out like anything else"
    thought = _mem(id="t1", memory_type="context", temporal_nature="temporary", created_at=NOW - timedelta(days=60))
    assert is_protected(thought, protected_ids={"t1"}) is False, "only intentions are held by an event"


SUNDAY_3AM = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)  # 03:00 Sunday in Springfield


def _sunday_nightly(monkeypatch, ms, *, sunday=True):
    """The decay pass runs after Sunday's nightly (2026-10-11; it ran after
    the Sunday 09:00 reflect before): pin the clock, say the day had
    material, and keep triage and the pulse out of it."""
    from services import expression_service
    from services import timezone_utils
    from tests.test_mind_service import _const

    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(ms, "_now", lambda: SUNDAY_3AM if sunday else SUNDAY_3AM + timedelta(days=1))
    monkeypatch.setattr(ms, "nightly_has_material", _const(True))
    monkeypatch.setattr(ms, "_nightly_triage", _const(None))
    monkeypatch.setattr(expression_service, "pulse_in_background", lambda reason: None)


async def test_the_sunday_nightly_runs_the_decay_pass_for_real(monkeypatch):
    import services.mind_service as ms
    from tests.test_mind_service import _stub

    runs, records = [], []

    async def chat(**kw):
        return "Closed two intentions; rewrote my state document."

    async def fake_decay(dry_run=True, **kw):
        runs.append(dry_run)
        return {"scanned": 10, "candidates": 3, "retired": 3, "dry_run": dry_run}

    async def record(**kw):
        records.append(kw)
        return 1

    _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "decay_stale_memories", fake_decay)
    monkeypatch.setattr(ms, "_record_now", record)
    ms.reset_state()
    _sunday_nightly(monkeypatch, ms, sunday=False)
    await ms.wake("nightly")
    await ms.wake("self")
    assert runs == [], "a weeknight's nightly, or any other wake, runs no decay"
    _sunday_nightly(monkeypatch, ms)
    await ms.wake("nightly")
    assert runs == [False]
    assert records[-1]["source"] == "nightly" and records[-1]["detail"]["decay"] == {
        "candidates": 3, "retired": 3, "stamp": None}


async def test_the_nightly_ledger_row_names_the_stamp_its_decay_pass_wrote(monkeypatch):
    """Every row one pass retires carries the same ``valid_until`` (batch
    A2): the ledger row keeps that stamp, so a wrong forget is found and
    reverted by one value. The ids are left out — hundreds of them would
    not fit the row's 600 characters (2026-09-27)."""
    import services.mind_service as ms
    from tests.test_mind_service import _stub

    records = []

    async def chat(**kw):
        return "Closed an intention."

    async def fake_decay(dry_run=True, **kw):
        return {"scanned": 900, "candidates": 240, "retired": 240, "dry_run": dry_run,
                "retired_ids": [f"00000000-0000-0000-0000-{i:012d}" for i in range(240)],
                "stamp": "2026-09-27T09:00:05.123456", "by_rule": {"curve": 238, "instruction_horizon": 2}}

    async def record(**kw):
        records.append(kw)
        return 1

    _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "decay_stale_memories", fake_decay)
    monkeypatch.setattr(ms, "_record_now", record)
    ms.reset_state()
    _sunday_nightly(monkeypatch, ms)
    await ms.wake("nightly")
    decay = records[-1]["detail"]["decay"]
    assert decay["stamp"] == "2026-09-27T09:00:05.123456"
    assert decay["retired"] == 240
    assert "retired_ids" not in decay


_LONG_REFLECTION = ["remember_search", "close_intention", "remember", "read_document", "list_goals",
                    "update_goal", "edit_document", "remember_update", "list_scheduled_events",
                    "update_memory_confidence", "graph_memory_search", "cancel_scheduled_event"]
_STAMP = "2026-09-27T14:00:05.123456"


def _reflection_tools(n):
    import itertools

    return [{"n": t, "ok": True, "ms": 120} for t in itertools.islice(itertools.cycle(_LONG_REFLECTION), n)]


@pytest.mark.parametrize("n_calls", [11, 30, 120])
async def test_a_long_reflection_keeps_the_stamp_through_the_ledgers_600_character_cap(monkeypatch, n_calls):
    """``detail.decay.stamp`` is what ``run_memory_decay.py --revert-stamp``
    reads to undo a wrong weekly forget (2026-09-27 review). ``_record_now``
    put it after one tool name per call, and ``bounded_detail`` cuts the
    JSON text at 600 characters: about 28 calls of a reflection — inside its
    default 30 rounds — left a row that did not parse, the stamp in the part
    cut off. The real ``_record_now`` and ``bounded_detail`` here; only the
    database write is captured. A journal entry's id added afterwards
    (``detail_with_thought``) keeps it too."""
    import json

    import services.mind_service as ms
    from services import event_ledger, thought_journal

    written = []

    async def capture(**kw):
        written.append(event_ledger.bounded_detail(kw["detail"]))
        return 7

    monkeypatch.setattr(event_ledger, "record_event_now", capture)
    decay = {"candidates": 42, "retired": 17, "stamp": _STAMP}
    await ms._record_now(source="reflect", outcome="acted", note="Closed intentions.",
                         tools=_reflection_tools(n_calls), turn_id="t1", detail={"decay": decay})
    text = written[-1]
    assert len(text) <= event_ledger.MAX_DETAIL_CHARS
    row = json.loads(text)
    assert row["decay"] == decay
    # The tools list is shortened, never the row cut: what is left is the
    # calls in order, and how many were left out.
    assert row["tools"] == [t["n"] for t in _reflection_tools(n_calls)][: len(row["tools"])]
    assert len(row["tools"]) + row.get("tools_omitted", 0) == n_calls
    with_entry = json.loads(thought_journal.detail_with_thought(text, "thought-1"))
    assert with_entry["thought_id"] == "thought-1" and with_entry["decay"] == decay


@pytest.mark.integration
async def test_a_long_nightly_writes_a_row_whose_stamp_parses(clean_db, monkeypatch):
    """The same through ``wake("nightly")`` on a Sunday and ``ledger_events`` on the test
    database: 35 tool calls, the row reads back with its stamp."""
    import json
    import types

    from sqlalchemy import select

    import services.mind_service as ms
    from services import turn_ledger
    from services.database import LedgerEventModel, async_session
    from tests.test_mind_service import _const, _pack, _S

    async def chat(**kw):
        return "Closed intentions; rewrote my state document."

    async def fake_decay(dry_run=True, **kw):
        return {"scanned": 900, "candidates": 42, "retired": 17, "dry_run": dry_run, "stamp": _STAMP}

    turn = types.SimpleNamespace(tools=_reflection_tools(35), id="turn-35", actions=[])
    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "decay_stale_memories", fake_decay)
    monkeypatch.setattr(turn_ledger, "last_finished_turn", lambda: turn)
    ms.reset_state()
    _sunday_nightly(monkeypatch, ms)

    out = await ms.wake("nightly")
    assert out["ok"] is True and out["ledger_id"]
    async with async_session() as s:
        row = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.id == out["ledger_id"]))).scalar_one()
    assert json.loads(row.detail_json)["decay"]["stamp"] == _STAMP


async def test_a_failing_decay_pass_does_not_fail_the_wake(monkeypatch):
    import services.mind_service as ms
    from tests.test_mind_service import _stub

    async def chat(**kw):
        return "fine"

    async def bad_decay(dry_run=True, **kw):
        raise RuntimeError("db away")

    _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "decay_stale_memories", bad_decay)
    ms.reset_state()
    _sunday_nightly(monkeypatch, ms)
    out = await ms.wake("nightly")
    assert out["ok"] and not out["skipped"]


@pytest.mark.integration
async def test_decay_pass_holds_an_intention_until_its_event_is_done(clean_db):
    from sqlalchemy import text

    from services.database import async_session
    from services.memory_decay_service import decay_stale_memories, pending_intention_ids
    from services.memory_service import Memory, store_memory
    from services.scheduled_events_service import create_event, mark_event_completed

    mid = (await store_memory(Memory(id=None, content="Watching ACME for Ellis's bonus", memory_type="intention",
                                     importance=0.5, origin="thought", about="self", temporal_nature="evolving"))).id
    async with async_session() as s:
        await s.execute(text("UPDATE memories SET created_at = :old WHERE id = :id"),
                        {"old": (NOW - timedelta(days=400)).replace(tzinfo=None), "id": mid})
        await s.commit()
    ev = await create_event(description="look at ACME", scheduled_at=NOW + timedelta(days=3),
                            conversation_id="edward-mind", memory_id=mid)
    assert mid in await pending_intention_ids()

    out = await decay_stale_memories(dry_run=True, now=NOW)
    assert out["candidates"] == 0, "held while the event is pending"

    await mark_event_completed(ev.id, result="done")
    assert mid not in await pending_intention_ids()
    out = await decay_stale_memories(dry_run=False, now=NOW)
    assert out["retired"] == 1


@pytest.mark.integration
async def test_wake_me_can_hold_an_intention(clean_db):
    import json

    from sqlalchemy import select

    from services.database import ScheduledEventModel, async_session
    from services.graph.tools import wake_me
    from services.memory_service import Memory, store_memory

    mid = (await store_memory(Memory(id=None, content="Watching ACME", memory_type="intention", importance=0.5,
                                     origin="thought", about="self"))).id
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    out = await wake_me.ainvoke({"when": when, "why": "see whether ACME recovered", "memory_id": mid})
    assert "Tool error" not in out, out
    async with async_session() as s:
        row = (await s.execute(select(ScheduledEventModel))).scalars().one()
    meta = json.loads(row.event_metadata)
    assert meta["type"] == "mind_wake" and meta["memory_id"] == mid
