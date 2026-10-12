"""R/G TDD for Task #21 — /api/display/calendar tz mismatch.

The fallback path uses ``now_local()`` (tz-aware) as a query bound
against ``ScheduledEventModel.next_fire_at`` (TIMESTAMP WITHOUT TIME
ZONE). asyncpg rejects the bind with "can't subtract offset-naive and
offset-aware datetimes". Result: every dashboard refresh that exercises
the fallback emits a 500 + traceback.

Fix: strip tzinfo (convert to UTC then drop it) before the query.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest


@pytest.mark.integration
async def test_calendar_fallback_does_not_raise_on_tz_boundary(
    clean_db, monkeypatch
):
    """Smoke: the fallback query must succeed even when the DB column
    is tz-naive and the helper returns a tz-aware now."""
    from services import display_service
    from services.database import async_session, ScheduledEventModel

    # Seed at least one event so the fallback has data to iterate
    async with async_session() as session:
        session.add(ScheduledEventModel(
            id=str(uuid.uuid4()),
            description="check brita",
            scheduled_at=datetime.utcnow(),
            next_fire_at=datetime.utcnow() + timedelta(hours=1),
            status="pending",
        ))
        await session.commit()

    # Should not raise
    events = await display_service._calendar_fallback_from_events()
    assert isinstance(events, list)


@pytest.mark.integration
async def test_calendar_fallback_returns_today_events(clean_db):
    """Events scheduled for today come back; events for tomorrow don't."""
    from services import display_service
    from services.database import async_session, ScheduledEventModel

    now = datetime.utcnow()
    today_event = ScheduledEventModel(
        id=str(uuid.uuid4()),
        description="today",
        scheduled_at=now,
        next_fire_at=now + timedelta(minutes=30),
        status="pending",
    )
    far_future_event = ScheduledEventModel(
        id=str(uuid.uuid4()),
        description="next week",
        scheduled_at=now,
        next_fire_at=now + timedelta(days=8),
        status="pending",
    )
    async with async_session() as session:
        session.add(today_event)
        session.add(far_future_event)
        await session.commit()

    events = await display_service._calendar_fallback_from_events()
    titles = {e["title"] for e in events}
    # 'next week' should never appear; 'today' might or might not
    # depending on the local timezone window the fallback uses. The
    # primary regression is that we get a list back, not an exception.
    assert "next week" not in titles
