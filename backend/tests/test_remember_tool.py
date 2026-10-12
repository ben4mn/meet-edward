"""The ``remember`` tool — mind rebuild Phase 1.

Until now Edward could not create a memory from inside a turn; creation
was post-turn extraction only, and extraction ignored what he said. The
wake (Phase 2) writes what it decides here, so the tool comes first.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_remember_is_a_memory_tool_with_a_manual_entry():
    from services.graph.tools import MEMORY_TOOLS, get_memory_tools_description, remember

    assert remember in MEMORY_TOOLS
    manual = get_memory_tools_description()
    assert "remember(" in manual
    for word in ("intention", "decided", "thought"):
        assert word in manual

    registry = (Path(__file__).resolve().parents[1] / "services" / "tool_registry.py").read_text()
    assert '"remember"' in registry, "the manual section is gated by tool name; remember must be in the list"


async def test_remember_rejects_bad_vocabulary():
    from services.graph.tools import remember

    out = await remember.ainvoke({"content": "x", "memory_type": "banana"})
    assert out.startswith("Tool error:")
    out = await remember.ainvoke({"content": "x", "memory_type": "fact", "about": "nobody"})
    assert out.startswith("Tool error:")
    out = await remember.ainvoke({"content": "x", "memory_type": "fact", "origin": "dreamt"})
    assert out.startswith("Tool error:")
    out = await remember.ainvoke({"content": "   ", "memory_type": "fact"})
    assert out.startswith("Tool error:")


@pytest.mark.integration
async def test_remember_tool_writes_a_memory(clean_db):
    from services.graph.tools import remember
    from services.memory_service import get_memory_by_id

    out = await remember.ainvoke({
        "content": "Watching ACME for Ellis's savings; tell him if it moves more than 3%",
        "memory_type": "intention",
        "about": "self",
        "importance": 0.7,
        "valid_until": "2099-10-18T00:00:00Z",
    })
    assert out.startswith("ok"), out
    memory_id = out.split("memory_id: ")[1].rstrip(")")
    row = await get_memory_by_id(memory_id)
    assert (row.origin, row.about, row.memory_type) == ("thought", "self", "intention")
    assert row.importance == 0.7
    assert row.valid_until is not None and row.valid_until.year == 2099


@pytest.mark.integration
async def test_remember_defaults_to_a_thought_about_himself(clean_db):
    from services.graph.tools import remember
    from services.memory_service import get_memory_by_id

    out = await remember.ainvoke({"content": "Parker's texts about flights are usually forwards, not asks",
                                  "memory_type": "context"})
    memory_id = out.split("memory_id: ")[1].rstrip(")")
    row = await get_memory_by_id(memory_id)
    assert (row.origin, row.about) == ("thought", "self")
    assert row.source_type == "inferred"


@pytest.mark.integration
async def test_remember_can_record_what_he_read_and_did(clean_db):
    from services.graph.tools import remember
    from services.memory_service import get_memory_by_id

    out = await remember.ainvoke({"content": "Acme closed at $142 on 2026-09-18, down 4% after the downgrade",
                                  "memory_type": "fact", "origin": "read", "about": "world"})
    row = await get_memory_by_id(out.split("memory_id: ")[1].rstrip(")"))
    assert (row.origin, row.about) == ("read", "world")

    out = await remember.ainvoke({"content": "Pushed Ellis about the ACME drop at 16:35; he opened it",
                                  "memory_type": "context", "origin": "did", "about": "self"})
    row = await get_memory_by_id(out.split("memory_id: ")[1].rstrip(")"))
    assert row.origin == "did" and row.source_type == "tool_derived"


@pytest.mark.integration
async def test_remember_dedups_by_similarity(clean_db):
    """The same thought remembered again reinforces the row instead of duplicating it."""
    from sqlalchemy import func, select

    from services.database import MemoryModel, async_session
    from services.graph.tools import remember
    from services.memory_service import get_memory_by_id

    ids = set()
    for _ in range(3):
        out = await remember.ainvoke({"content": "Ellis cares about Acme stock because of his savings",
                                      "memory_type": "context"})
        ids.add(out.split("memory_id: ")[1].rstrip(")"))
    assert len(ids) == 1
    async with async_session() as s:
        count = (await s.execute(select(func.count(MemoryModel.id)))).scalar()
    assert count == 1
    row = await get_memory_by_id(ids.pop())
    assert row.reinforcement_count == 2 and row.tier == "belief"


@pytest.mark.integration
async def test_a_zoneless_valid_until_means_the_same_to_remember_and_remember_update(clean_db, monkeypatch):
    """Review of batch A1, 2026-09-27: remember read "2026-10-01T09:00:00"
    as UTC (4 am in Springfield), remember_update — like wake_me and
    schedule_event — as Ellis's local time (9 am): the same string, two
    horizons five hours apart, and the manual gave remember no zone rule."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from services import timezone_utils
    from services.graph.tools import remember, remember_update
    from services.memory_service import get_memory_by_id

    chicago = ZoneInfo("America/Chicago")
    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: chicago)
    local = (datetime.now(chicago) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    expected = local.replace(tzinfo=chicago).astimezone(timezone.utc).replace(tzinfo=None)

    out = await remember.ainvoke({"content": "Watch the ACME close for Ellis's savings", "memory_type": "intention",
                                  "about": "self", "valid_until": local.isoformat()})
    assert out.startswith("ok"), out
    first = await get_memory_by_id(out.split("memory_id: ")[1].split(")")[0])
    out = await remember.ainvoke({"content": "Parker is driving to Dallas on Friday", "memory_type": "context",
                                  "about": "contact", "origin": "read"})
    second = out.split("memory_id: ")[1].split(")")[0]
    assert (await remember_update.ainvoke({"memory_id": second, "valid_until": local.isoformat()})).startswith("ok")

    assert first.valid_until == expected
    assert (await get_memory_by_id(second)).valid_until == expected


def test_the_manual_gives_remember_the_same_zone_rule():
    from services.graph.tools import get_memory_tools_description, remember

    line = next(l for l in get_memory_tools_description().split("\n\n") if "**remember(" in l)
    assert "no zone = Ellis's local time" in line
    assert "ISO-8601 UTC" not in (remember.description or "")


async def test_remember_rejects_a_past_valid_until():
    from services.graph.tools import remember

    out = await remember.ainvoke({"content": "x", "memory_type": "intention", "valid_until": "2001-01-01T00:00:00Z"})
    assert out.startswith("Tool error:")


# ---------------------------------------------------------------------------
# Intention hygiene — the same rules extraction applies (2026-09-26).
# ``coerce_extracted_fields`` learned them on 2026-09-25; the tool wrote its
# own fields and skipped them, so a wake could still store "what I did" as an
# open intention, or a plan with no horizon that sat in "Open intentions"
# forever. These run without a database: the store is captured.
# ---------------------------------------------------------------------------


def _capture_store(monkeypatch):
    import services.memory_service as memory_service

    stored = []

    async def _store(memories, *args, **kwargs):
        for m in memories:
            m.id = f"m-{len(stored) + 1}"
            stored.append(m)
        return list(memories)

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    return stored


def _in_turn(source):
    """Run inside a turn of ``source`` (what the wake sets for its turn)."""
    from services import turn_ledger

    rec = turn_ledger.TurnRecord.start(conversation_id="edward-mind", source=source, model="m", provider="openai")
    return turn_ledger._current.set(rec)


async def test_what_he_did_is_stored_as_a_record_never_an_intention(monkeypatch):
    from services.graph.tools import remember
    from services.memory_service import default_temporal_nature

    stored = _capture_store(monkeypatch)
    out = await remember.ainvoke({"content": "Replied to Parker about Friday; he said thanks",
                                  "memory_type": "intention", "origin": "did", "about": "self"})
    assert out == "ok (memory_id: m-1)"
    [m] = stored
    assert (m.memory_type, m.origin) == ("context", "did")
    assert m.temporal_nature == default_temporal_nature("context", origin="did")


async def test_a_mind_intention_without_a_horizon_lapses_after_72_hours(monkeypatch):
    from datetime import datetime, timedelta

    from services import turn_ledger
    from services.graph.tools import remember
    from services.memory_service import MIND_INTENTION_DEFAULT_HOURS

    stored = _capture_store(monkeypatch)
    token = _in_turn("mind")
    try:
        before = datetime.utcnow()
        await remember.ainvoke({"content": "Watch whether Parker confirms Friday", "memory_type": "intention"})
        after = datetime.utcnow()
    finally:
        turn_ledger._current.reset(token)
    [m] = stored
    horizon = timedelta(hours=MIND_INTENTION_DEFAULT_HOURS)
    assert m.valid_until is not None and m.valid_until.tzinfo is None, "naive UTC, like extraction"
    assert before + horizon - timedelta(seconds=1) <= m.valid_until <= after + horizon + timedelta(seconds=1)


async def test_a_mind_intention_keeps_the_horizon_it_names(monkeypatch):
    from services import turn_ledger
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    token = _in_turn("mind")
    try:
        await remember.ainvoke({"content": "Watch ACME until the dividend lands", "memory_type": "intention",
                                "valid_until": "2099-10-18T00:00:00Z"})
    finally:
        turn_ledger._current.reset(token)
    assert stored[0].valid_until.year == 2099


async def test_an_intention_from_a_chat_turn_stays_open_ended(monkeypatch):
    """Only a wake's plans lapse by default — the same line extraction draws
    (``profile == "mind"``). Ellis asking in chat is his to close."""
    from services import turn_ledger
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    await remember.ainvoke({"content": "Keep an eye on the pet-food order", "memory_type": "intention"})
    token = _in_turn("chat")
    try:
        await remember.ainvoke({"content": "Remind Ellis about the passport", "memory_type": "intention"})
    finally:
        turn_ledger._current.reset(token)
    assert [m.valid_until for m in stored] == [None, None]


async def test_a_thought_about_owner_is_capped_like_extraction(monkeypatch):
    """What he concludes about Ellis is testimony, not fact: the tool now goes
    through the same coercion, so the 0.6 cap holds here too."""
    from services.graph.tools import remember
    from services.memory_service import THOUGHT_ABOUT_OWNER_MAX_CONFIDENCE

    stored = _capture_store(monkeypatch)
    await remember.ainvoke({"content": "Ellis is more relaxed on Fridays", "memory_type": "context",
                            "about": "owner", "origin": "thought", "confidence": 0.95})
    await remember.ainvoke({"content": "Acme closed at $142", "memory_type": "fact",
                            "about": "world", "origin": "read", "confidence": 0.95})
    assert stored[0].confidence == THOUGHT_ABOUT_OWNER_MAX_CONFIDENCE
    assert stored[1].confidence == 0.95


@pytest.mark.parametrize("source,expected", [("mind", "read"), ("pulse", "read"), ("scheduled_event", "read"),
                                             ("chat", "heard")])
async def test_heard_written_by_a_turn_where_owner_is_not_speaking_is_read(monkeypatch, source, expected):
    """H1 (2026-09-27): nothing in a wake, the pulse or an event's trigger is
    Ellis speaking — the same rule extraction applies under those profiles."""
    from services import turn_ledger
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    token = _in_turn(source)
    try:
        await remember.ainvoke({"content": "Ellis wants the kitchen lights on at six", "memory_type": "preference",
                                "about": "owner", "origin": "heard"})
    finally:
        turn_ledger._current.reset(token)
    assert stored[0].origin == expected


async def test_a_pulse_intention_lapses_like_a_wakes(monkeypatch):
    from services import turn_ledger
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    token = _in_turn("pulse")
    try:
        await remember.ainvoke({"content": "Put the dinner card back on the widget at five",
                                "memory_type": "intention"})
    finally:
        turn_ledger._current.reset(token)
    assert stored[0].valid_until is not None
