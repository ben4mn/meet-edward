"""``schedule_event(..., why=…)`` writes an intention memory and links it;
cancelling the event retires the intention — mind rebuild Phase 4."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.integration
async def test_schedule_event_with_why_writes_an_intention_and_links_it(clean_db):
    from services.graph.tools import schedule_event
    from services.memory_service import get_memory_by_id
    from services.scheduled_events_service import list_events

    when = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    out = await schedule_event.ainvoke({
        "description": "Check ACME at the close and tell Ellis if it moved more than 3%",
        "scheduled_at": when,
        "recurrence_pattern": "30 16 * * 1-5",
        "why": "Ellis's savings depend on Acme stock and he was worried about the downgrade",
    })
    assert out.startswith("Event scheduled"), out
    events = await list_events(status="pending")
    assert len(events) == 1
    meta = json.loads(events[0].event_metadata)
    memory = await get_memory_by_id(meta["memory_id"])
    assert memory is not None
    assert (memory.memory_type, memory.origin, memory.about) == ("intention", "thought", "self")
    assert "savings" in memory.content and "Check ACME" in memory.content
    assert memory.valid_until is not None and memory.valid_until > datetime.utcnow() + timedelta(days=29)


@pytest.mark.integration
async def test_schedule_event_without_why_writes_no_memory(clean_db):
    from sqlalchemy import func, select

    from services.database import MemoryModel, async_session
    from services.graph.tools import schedule_event

    when = (datetime.now() + timedelta(hours=2)).replace(microsecond=0).isoformat()
    out = await schedule_event.ainvoke({"description": "Remind Ellis to drink water", "scheduled_at": when})
    assert out.startswith("Event scheduled")
    async with async_session() as s:
        assert (await s.execute(select(func.count(MemoryModel.id)))).scalar() == 0


@pytest.mark.integration
async def test_cancel_retires_the_intention(clean_db):
    from services.graph.tools import cancel_scheduled_event, schedule_event
    from services.memory_service import get_memory_by_id, retrieve_memories
    from services.scheduled_events_service import list_events

    when = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    await schedule_event.ainvoke({"description": "Check ACME at the close", "scheduled_at": when,
                                  "why": "Ellis's savings depend on Acme stock"})
    ev = (await list_events(status="pending"))[0]
    memory_id = json.loads(ev.event_metadata)["memory_id"]
    assert memory_id in {m.id for m in await retrieve_memories("Acme", limit=5, min_confidence=0.0, min_score=0.0)}

    out = await cancel_scheduled_event.ainvoke({"event_id": ev.id})
    assert "cancelled" in out
    memory = await get_memory_by_id(memory_id)
    assert memory.valid_until is not None and memory.valid_until <= datetime.utcnow() + timedelta(seconds=5)
    assert memory_id not in {m.id for m in await retrieve_memories("Acme", limit=5, min_confidence=0.0, min_score=0.0)}
