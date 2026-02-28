"""
Scheduled events service for Edward.

Handles CRUD operations and due-event queries for scheduled events
like reminders, messages, and self-assigned tasks.

All datetimes are stored as naive UTC in PostgreSQL (TIMESTAMP WITHOUT TIME ZONE).
Any tz-aware inputs are converted to UTC then stripped of tzinfo before storage.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import select, update, and_
from croniter import croniter

from services.database import async_session, ScheduledEventModel

VALID_DELIVERY_CHANNELS = {"sms", "imessage", "chat", None}


def _validate_delivery_channel(channel: Optional[str]) -> None:
    """Raise ValueError if channel is not a recognised value."""
    if channel not in VALID_DELIVERY_CHANNELS:
        raise ValueError(
            f"Invalid delivery_channel '{channel}'. "
            f"Must be one of: 'sms', 'imessage', 'chat', or null."
        )


def _to_naive_utc(dt: datetime) -> datetime:
    """Convert a datetime to naive UTC for PostgreSQL storage."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _format_local(dt: datetime, fmt: str = '%A, %B %d, %Y at %I:%M %p') -> str:
    """Format a naive-UTC datetime in the server's local timezone."""
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime(fmt)


def _utcnow() -> datetime:
    """Get current time as naive UTC."""
    return datetime.utcnow()


def _next_cron_utc(cron_pattern: str, after: datetime) -> datetime:
    """
    Compute next cron fire time in naive UTC.

    Cron patterns are interpreted in the server's local timezone (since that's
    what the user sees), then converted to naive UTC for storage.
    """
    # Convert naive UTC 'after' to local for cron calculation
    local_after = after.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    cron = croniter(cron_pattern, local_after)
    local_next = cron.get_next(datetime)
    # Convert local back to naive UTC
    return _to_naive_utc(local_next.astimezone())


async def create_event(
    description: str,
    scheduled_at: datetime,
    recurrence_pattern: Optional[str] = None,
    conversation_id: Optional[str] = None,
    delivery_channel: Optional[str] = None,
    created_by: str = "edward",
) -> ScheduledEventModel:
    """Create a new scheduled event."""
    _validate_delivery_channel(delivery_channel)
    if recurrence_pattern and not croniter.is_valid(recurrence_pattern):
        raise ValueError(f"Invalid cron pattern: {recurrence_pattern}")

    scheduled_at = _to_naive_utc(scheduled_at)
    event_id = str(uuid.uuid4())

    async with async_session() as session:
        event = ScheduledEventModel(
            id=event_id,
            conversation_id=conversation_id,
            description=description,
            scheduled_at=scheduled_at,
            next_fire_at=scheduled_at,
            recurrence_pattern=recurrence_pattern,
            status="pending",
            created_by=created_by,
            delivery_channel=delivery_channel,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event


async def list_events(
    status: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 50,
    sort_order: str = "asc",
    search: Optional[str] = None,
) -> List[ScheduledEventModel]:
    """List events with optional filters, ordered by next_fire_at."""
    async with async_session() as session:
        query = select(ScheduledEventModel)

        if status:
            query = query.where(ScheduledEventModel.status == status)
        if conversation_id:
            query = query.where(ScheduledEventModel.conversation_id == conversation_id)
        if search:
            query = query.where(ScheduledEventModel.description.ilike(f"%{search}%"))

        if sort_order == "desc":
            query = query.order_by(ScheduledEventModel.scheduled_at.desc())
        else:
            query = query.order_by(ScheduledEventModel.scheduled_at.asc())

        query = query.limit(limit)
        result = await session.execute(query)
        return list(result.scalars().all())


async def get_event(event_id: str) -> Optional[ScheduledEventModel]:
    """Get a single event by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )
        return result.scalar_one_or_none()


async def cancel_event(event_id: str) -> bool:
    """Cancel an event by setting status to 'cancelled'."""
    async with async_session() as session:
        result = await session.execute(
            update(ScheduledEventModel)
            .where(ScheduledEventModel.id == event_id)
            .where(ScheduledEventModel.status.in_(["pending", "processing"]))
            .values(status="cancelled", updated_at=_utcnow())
        )
        await session.commit()
        return result.rowcount > 0


async def delete_event(event_id: str) -> bool:
    """Permanently delete an event."""
    async with async_session() as session:
        result = await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )
        event = result.scalar_one_or_none()
        if event:
            await session.delete(event)
            await session.commit()
            return True
        return False


async def get_due_events() -> List[ScheduledEventModel]:
    """
    Atomically fetch and claim due events for processing.

    SELECT + UPDATE in one transaction prevents double-pickup.
    """
    now = _utcnow()

    async with async_session() as session:
        # Select pending events that are due
        result = await session.execute(
            select(ScheduledEventModel).where(
                and_(
                    ScheduledEventModel.next_fire_at <= now,
                    ScheduledEventModel.status == "pending",
                )
            ).with_for_update()
        )
        events = list(result.scalars().all())

        if not events:
            return []

        # Mark them as processing
        event_ids = [e.id for e in events]
        await session.execute(
            update(ScheduledEventModel)
            .where(ScheduledEventModel.id.in_(event_ids))
            .values(status="processing")
        )
        await session.commit()

        # Refresh to get updated status
        for event in events:
            await session.refresh(event)

        return events


async def mark_event_completed(event_id: str, result: Optional[str] = None) -> None:
    """
    Mark an event as completed.

    For recurring events: compute next fire time and reset to pending.
    For one-time events: set status to completed.
    """
    async with async_session() as session:
        event_result = await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )
        event = event_result.scalar_one_or_none()
        if not event:
            return

        now = _utcnow()
        event.last_fired_at = now
        event.fire_count = (event.fire_count or 0) + 1
        event.last_result = result
        event.updated_at = now

        if event.recurrence_pattern:
            # Compute next fire time from now (skip past missed occurrences)
            event.next_fire_at = _next_cron_utc(event.recurrence_pattern, now)
            event.status = "pending"
        else:
            event.status = "completed"

        await session.commit()


async def mark_event_failed(event_id: str, error: str) -> None:
    """
    Mark an event as failed.

    For recurring events: still advance to next fire time so they don't get stuck.
    For one-time events: set status to failed.
    """
    async with async_session() as session:
        event_result = await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )
        event = event_result.scalar_one_or_none()
        if not event:
            return

        now = _utcnow()
        event.last_fired_at = now
        event.fire_count = (event.fire_count or 0) + 1
        event.last_result = f"ERROR: {error}"
        event.updated_at = now

        if event.recurrence_pattern:
            # Advance to next fire time even on failure
            event.next_fire_at = _next_cron_utc(event.recurrence_pattern, now)
            event.status = "pending"
        else:
            event.status = "failed"

        await session.commit()


async def update_event(
    event_id: str,
    description: Optional[str] = None,
    scheduled_at: Optional[datetime] = None,
    recurrence_pattern: Optional[str] = None,
    status: Optional[str] = None,
    delivery_channel: Optional[str] = None,
) -> Optional[ScheduledEventModel]:
    """Update event fields."""
    async with async_session() as session:
        result = await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            return None

        if description is not None:
            event.description = description
        if scheduled_at is not None:
            scheduled_at = _to_naive_utc(scheduled_at)
            event.scheduled_at = scheduled_at
            event.next_fire_at = scheduled_at
        if recurrence_pattern is not None:
            if recurrence_pattern and not croniter.is_valid(recurrence_pattern):
                raise ValueError(f"Invalid cron pattern: {recurrence_pattern}")
            event.recurrence_pattern = recurrence_pattern or None
        if status is not None:
            event.status = status
        if delivery_channel is not None:
            _validate_delivery_channel(delivery_channel or None)
            event.delivery_channel = delivery_channel or None

        event.updated_at = _utcnow()
        await session.commit()
        await session.refresh(event)
        return event
