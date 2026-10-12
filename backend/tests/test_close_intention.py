"""Edward can close an intention (M4, 2026-09-27 follow-ups).

``remember_update`` changed content only and ``remember_forget`` deleted the
row, so nothing could say "this is done" and keep the history. The pack
told him to close stale intentions with "``remember_update`` with a past
valid_until" — a parameter the tool did not have. The 07:42 free wake:
"marking it closed in the text didn't retire it"; he logged a wish for
exactly this (a wishlist item); research ran four times on the
same briefing topic.

``close_intention(memory_id, outcome)``: stamps ``valid_until=now`` on a
live intention and appends what came of it to the row, which stays — with
its provenance — so ``how_do_i_know`` and the Memory room keep the story.
It refuses a non-intention and an already-closed one with ``Tool error:``
and answers ``ok (…)`` (CONTRACT for batch C: the thought journal counts a
reply starting "ok" as success). ``remember_update`` gains ``valid_until``
(ISO-8601; a time without a zone is Ellis's local time).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


async def _seed(content="Brief Parker before Monday's planning meeting", memory_type="intention", about="self",
                valid_until=None, source_ref=None):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=content, memory_type=memory_type, importance=0.7, origin="thought",
                          about=about, temporal_nature="evolving", confidence=0.8,
                          embedding=get_embedding(content), valid_until=valid_until,
                          source_ref=source_ref or json.dumps({"kind": "remember", "conversation_id": "edward-mind"}))
        s.add(row)
        await s.commit()
        return row.id


async def _row(memory_id):
    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return await s.get(MemoryModel, memory_id)


async def _close(memory_id, outcome="Briefed Parker on Friday; nothing left to do"):
    from services.graph.tools import close_intention

    return await close_intention.ainvoke({"memory_id": memory_id, "outcome": outcome})


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_closing_an_intention_retires_it_and_keeps_its_history(clean_db):
    mid = await _seed()
    before = datetime.utcnow()
    out = await _close(mid)
    assert out.startswith("ok"), out
    row = await _row(mid)
    assert row is not None, "the row stays"
    assert row.valid_until is not None and before - timedelta(seconds=1) <= row.valid_until <= datetime.utcnow()
    assert row.content.startswith("Brief Parker before Monday's planning meeting")
    assert "Briefed Parker on Friday" in row.content
    ref = json.loads(row.source_ref)
    assert ref["kind"] == "remember" and ref["conversation_id"] == "edward-mind", "where it came from stays on top"
    assert ref["closed"]["outcome"].startswith("Briefed Parker on Friday")


@pytest.mark.integration
async def test_a_closed_intention_leaves_the_pack(clean_db):
    from services import mind_service as ms

    closed = await _seed()
    kept = await _seed(content="Watch the seed catalogue for Ellis; tell him when the tomatoes are back")
    await _close(closed)

    now = datetime.now(timezone.utc)
    thoughts, _actions = await ms._self_memories(now)
    ids = {m.id for m in thoughts}
    assert closed not in ids and kept in ids
    pack = ms.render_pack(ms.MindPack(now=now, reason="morning", last_wake_at=None, attention={}, messages=[],
                                      calendar=[], board="", notes="", pushes="", verdicts=[], thoughts=thoughts,
                                      actions=[], surfaces={}))
    assert "Open intentions (1):" in pack
    assert "Brief Parker before Monday" not in pack

    assert closed not in {i["id"] for i in await ms.open_intentions()}


@pytest.mark.integration
async def test_it_refuses_what_is_not_an_intention(clean_db):
    mid = await _seed(content="Ellis keeps a vegetable garden", memory_type="fact", about="owner")
    out = await _close(mid)
    assert out.startswith("Tool error:") and "not an intention" in out
    assert (await _row(mid)).valid_until is None


@pytest.mark.integration
async def test_it_refuses_an_intention_already_closed(clean_db):
    mid = await _seed(valid_until=datetime.utcnow() - timedelta(hours=2))
    out = await _close(mid)
    assert out.startswith("Tool error:") and "already closed" in out


@pytest.mark.integration
async def test_it_refuses_an_unknown_id_and_an_empty_outcome(clean_db):
    assert (await _close("no-such-id")).startswith("Tool error:")
    mid = await _seed()
    assert (await _close(mid, outcome="  ")).startswith("Tool error:")
    assert (await _row(mid)).valid_until is None


@pytest.mark.integration
async def test_a_later_decision_to_do_it_again_is_a_new_open_intention(clean_db, monkeypatch):
    """Conflict detection matched retired rows too, so re-deciding a closed
    intention reinforced the closed row and the new decision vanished — and
    a fact decay had retired, said again by Ellis, stayed retired."""
    from services import memory_service
    from services.graph.tools import remember
    from services.memory_service import ConflictResolution

    mid = await _seed()
    await _close(mid)

    async def _update(existing, memory, similarity):  # what the resolver says for a reworded twin
        return ConflictResolution(action="UPDATE", reason="same plan")

    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _update)
    out = await remember.ainvoke({"content": "Brief Parker before Monday's planning meeting",
                                  "memory_type": "intention",
                                  "valid_until": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()})
    assert out.startswith("ok")
    new_id = out.split("memory_id: ")[1].split(")")[0]
    assert new_id != mid
    assert (await _row(new_id)).valid_until > datetime.utcnow()


@pytest.mark.integration
async def test_a_retired_row_said_again_is_a_new_live_row(clean_db):
    """The same words as a retired row matched it at >= 0.92 and reinforced
    it: the new decision (or Ellis's restated fact) stayed retired."""
    from services.graph.tools import remember

    retired = await _seed(valid_until=datetime.utcnow() - timedelta(hours=1))
    out = await remember.ainvoke({"content": "Brief Parker before Monday's planning meeting",
                                  "memory_type": "intention",
                                  "valid_until": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()})
    new_id = out.split("memory_id: ")[1].split(")")[0]
    assert new_id != retired
    assert (await _row(new_id)).valid_until > datetime.utcnow()
    assert (await _row(retired)).reinforcement_count in (0, None)


@pytest.mark.integration
async def test_forget_still_finds_retired_rows(clean_db):
    """Verified forget must reach retired rows about a subject too; only
    conflict detection is limited to live ones."""
    from services.memory_service import find_similar_memories

    mid = await _seed(valid_until=datetime.utcnow() - timedelta(days=1))
    assert mid in {m.id for m, _ in await find_similar_memories("Brief Parker before Monday's planning meeting",
                                                                threshold=0.9)}
    assert mid not in {m.id for m, _ in await find_similar_memories("Brief Parker before Monday's planning meeting",
                                                                    threshold=0.9, live_only=True)}


# ---------------------------------------------------------------------------
# remember_update gains valid_until
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_remember_update_sets_a_horizon_in_owners_zone(clean_db, monkeypatch):
    from zoneinfo import ZoneInfo

    from services import timezone_utils
    from services.graph.tools import remember_update

    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: ZoneInfo("America/Chicago"))
    mid = await _seed()
    local = (datetime.now(ZoneInfo("America/Chicago")) + timedelta(days=2)).replace(microsecond=0, tzinfo=None)
    out = await remember_update.ainvoke({"memory_id": mid, "valid_until": local.isoformat()})
    assert out.startswith("ok"), out
    row = await _row(mid)
    expected = local.replace(tzinfo=ZoneInfo("America/Chicago")).astimezone(timezone.utc).replace(tzinfo=None)
    assert row.valid_until == expected
    assert row.content == "Brief Parker before Monday's planning meeting", "content is untouched"


@pytest.mark.integration
async def test_remember_update_reads_an_explicit_zone_and_can_retire(clean_db):
    from services.graph.tools import remember_update

    mid = await _seed(memory_type="context", content="Parker is at the gym")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    out = await remember_update.ainvoke({"memory_id": mid, "valid_until": past.isoformat().replace("+00:00", "Z")})
    assert out.startswith("ok")
    assert (await _row(mid)).valid_until == past.replace(tzinfo=None)


@pytest.mark.integration
async def test_remember_update_refuses_a_bad_time_and_an_empty_call(clean_db):
    from services.graph.tools import remember_update

    mid = await _seed()
    assert (await remember_update.ainvoke({"memory_id": mid, "valid_until": "next week"})).startswith("Tool error:")
    assert (await remember_update.ainvoke({"memory_id": mid})).startswith("Tool error:")
    assert (await _row(mid)).valid_until is None


# ---------------------------------------------------------------------------
# Where it is bound and described
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_it_is_an_always_available_memory_tool_with_a_manual_entry():
    from services.graph.tools import MEMORY_TOOLS, get_memory_tools_description

    assert "close_intention" in {t.name for t in MEMORY_TOOLS}
    manual = get_memory_tools_description()
    assert "close_intention(memory_id, outcome)" in manual
    assert "valid_until" in manual.split("remember_update", 1)[1].split("\n\n", 1)[0]


@pytest.mark.unit
def test_the_wakes_that_tidy_intentions_are_given_it():
    from services import mind_service as ms

    for tools in (ms.LEGACY_WAKE_TOOLS, ms.LEGACY_QUIET_TOOLS):
        assert "close_intention" in tools
    assert "close_intention" in ms.ACTING_TOOLS


@pytest.mark.unit
def test_the_manual_section_is_included_for_it_alone():
    """Legacy registry: the memory section is switched on by close_intention
    too. Under the toolset ``memory(action="close")`` is in the manual."""
    from services.toolset.manual import TOOL_MANUAL

    from services import tool_registry

    import inspect

    src = inspect.getsource(tool_registry._legacy_tool_descriptions)
    line = next(l for l in src.splitlines() if '"remember_update"' in l and "get_memory_tools_description" in l)
    assert '"close_intention"' in line
    assert "close an intention" in TOOL_MANUAL


@pytest.mark.unit
def test_the_reflections_say_to_close_with_it():
    from services import mind_service as ms

    for now in (datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc), datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)):
        reason = "nightly"  # Sunday's, then a weeknight's
        pack = ms.render_pack(ms.MindPack(now=now, reason=reason,
                                          last_wake_at=None, attention={}, messages=[], calendar=[], board="",
                                          notes="", pushes="", verdicts=[], thoughts=[], actions=[], surfaces={}))
        assert "`close_intention`" in pack, reason
