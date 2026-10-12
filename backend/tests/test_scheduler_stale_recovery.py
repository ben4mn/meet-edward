"""Recovery of scheduled events orphaned in status='processing'.

Regression: get_due_events() claims events by setting status='processing',
but if the backend dies (restart.sh, launchctl kickstart, crash) before
mark_event_completed/failed runs, the row stays 'processing' forever —
the scheduler only ever selects 'pending'. As of 2026-07-03 the live DB
had 17 such orphans, some dead since April (Pepper med reminders, USPS
checks, internal wakeup ticks).
"""

from datetime import timedelta

import pytest

from services.database import async_session, ScheduledEventModel
from services.scheduled_events_service import _utcnow

pytestmark = pytest.mark.integration


async def _seed_event(**overrides) -> str:
    now = _utcnow()
    fields = dict(
        description="test event",
        scheduled_at=now - timedelta(hours=3),
        next_fire_at=now - timedelta(hours=3),
        status="processing",
        created_by="edward",
        fire_count=5,
        updated_at=now - timedelta(hours=3),
    )
    fields.update(overrides)
    event = ScheduledEventModel(**fields)
    async with async_session() as session:
        session.add(event)
        await session.commit()
        return event.id


async def _get_event(event_id: str) -> ScheduledEventModel:
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )
        return result.scalar_one()


@pytest.mark.asyncio
async def test_stale_recurring_processing_event_is_rearmed(clean_db):
    from services.scheduled_events_service import recover_stale_processing_events

    event_id = await _seed_event(recurrence_pattern="0 7 * * *")

    recovered = await recover_stale_processing_events(stale_after_seconds=600)

    assert recovered == 1
    event = await _get_event(event_id)
    assert event.status == "pending"
    assert event.next_fire_at > _utcnow(), "recurring event must be re-armed in the future"
    assert "recover" in (event.last_result or "").lower()


@pytest.mark.asyncio
async def test_stale_one_time_processing_event_is_marked_failed(clean_db):
    from services.scheduled_events_service import recover_stale_processing_events

    event_id = await _seed_event(recurrence_pattern=None)

    recovered = await recover_stale_processing_events(stale_after_seconds=600)

    assert recovered == 1
    event = await _get_event(event_id)
    assert event.status == "failed"
    assert "recover" in (event.last_result or "").lower()


@pytest.mark.asyncio
async def test_fresh_processing_event_is_left_alone(clean_db):
    """An event actively executing right now must not be touched."""
    from services.scheduled_events_service import recover_stale_processing_events

    event_id = await _seed_event(
        recurrence_pattern="0 7 * * *", updated_at=_utcnow()
    )

    recovered = await recover_stale_processing_events(stale_after_seconds=600)

    assert recovered == 0
    event = await _get_event(event_id)
    assert event.status == "processing"


@pytest.mark.asyncio
async def test_startup_recovery_reclaims_even_fresh_orphans(clean_db):
    """At process start nothing can legitimately be 'processing'."""
    from services.scheduled_events_service import recover_stale_processing_events

    event_id = await _seed_event(
        recurrence_pattern="0 7 * * *", updated_at=_utcnow()
    )

    recovered = await recover_stale_processing_events(stale_after_seconds=0)

    assert recovered == 1
    event = await _get_event(event_id)
    assert event.status == "pending"


@pytest.mark.asyncio
async def test_scheduler_startup_runs_recovery(clean_db, monkeypatch):
    """start_scheduler() must reclaim orphans before the polling loop starts."""
    import services.scheduler_service as scheduler_service

    calls = []

    async def fake_recover(stale_after_seconds):
        calls.append(stale_after_seconds)
        return 0

    async def fake_ensure_wakeup():
        return None

    monkeypatch.setattr(
        scheduler_service, "recover_stale_processing_events", fake_recover
    )
    monkeypatch.setattr(scheduler_service, "_ensure_wakeup_events", fake_ensure_wakeup)

    await scheduler_service.start_scheduler()
    try:
        assert calls == [0], "startup must recover ALL processing orphans (age 0)"
    finally:
        await scheduler_service.stop_scheduler()
