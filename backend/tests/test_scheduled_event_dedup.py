"""Duplicate scheduled-event guard.

Regression (live, 2026-06-29): two heartbeat triggers created two
near-identical "Announce on Echo devices: Walmart delivery is at the
door..." events with the same scheduled_at, ~1 minute apart, and both
fired — the house got the same Alexa announcement twice. create_event
must treat a near-identical pending event at the same fire time as the
same event and return it instead of inserting a twin.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from services.database import async_session, ScheduledEventModel
from services.scheduled_events_service import create_event

pytestmark = pytest.mark.integration

WHEN = datetime(2026, 6, 29, 20, 11, 0)


async def _count_events() -> int:
    async with async_session() as session:
        result = await session.execute(select(ScheduledEventModel))
        return len(result.scalars().all())


@pytest.mark.asyncio
async def test_near_identical_event_at_same_time_is_deduped(clean_db):
    first = await create_event(
        description=(
            "Announce on Echo devices: Walmart delivery is at the door: "
            "Purina Tidy Cats and 34 other items"
        ),
        scheduled_at=WHEN,
        delivery_channel="alexa",
        conversation_id="conv-heartbeat-1",
    )
    second = await create_event(
        description=(
            "Announce on Echo devices: Walmart delivery is at the door: "
            "Purina Tidy Cats Free and 34 other items"
        ),
        scheduled_at=WHEN,
        delivery_channel="alexa",
        conversation_id="conv-heartbeat-2",
    )

    assert second.id == first.id, "near-duplicate should return the existing event"
    assert await _count_events() == 1


@pytest.mark.asyncio
async def test_same_description_different_time_is_not_deduped(clean_db):
    await create_event(
        description="Send Ellis a chat reminder: water the plants",
        scheduled_at=WHEN,
        delivery_channel="chat",
    )
    await create_event(
        description="Send Ellis a chat reminder: water the plants",
        scheduled_at=WHEN + timedelta(hours=6),
        delivery_channel="chat",
    )

    assert await _count_events() == 2


@pytest.mark.asyncio
async def test_different_action_at_same_time_is_not_deduped(clean_db):
    await create_event(
        description="Announce on Echo devices: Walmart delivery is at the door",
        scheduled_at=WHEN,
        delivery_channel="alexa",
    )
    await create_event(
        description="Send Ellis a push notification about the dentist appointment",
        scheduled_at=WHEN,
        delivery_channel="chat",
    )

    assert await _count_events() == 2


@pytest.mark.asyncio
async def test_completed_twin_does_not_block_a_new_event(clean_db):
    """Dedup only applies against events that have not fired yet."""
    first = await create_event(
        description="Announce on Echo devices: Walmart delivery is at the door",
        scheduled_at=WHEN,
        delivery_channel="alexa",
    )
    async with async_session() as session:
        stored = await session.get(ScheduledEventModel, first.id)
        stored.status = "completed"
        await session.commit()

    second = await create_event(
        description="Announce on Echo devices: Walmart delivery is at the door",
        scheduled_at=WHEN,
        delivery_channel="alexa",
    )

    assert second.id != first.id
    assert await _count_events() == 2
