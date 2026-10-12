"""
Scheduled events service for Edward.

Handles CRUD operations and due-event queries for scheduled events
like reminders, messages, and self-assigned tasks.

All datetimes are stored as naive UTC in PostgreSQL (TIMESTAMP WITHOUT TIME ZONE).
Any tz-aware inputs are converted to UTC then stripped of tzinfo before storage.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from sqlalchemy import select, update, and_
from croniter import croniter

from services.database import async_session, ScheduledEventModel

VALID_DELIVERY_CHANNELS = {"sms", "imessage", "chat", "alexa", None}

# Whose an event is, for the Claude spend cap (review 2026-10-11): the
# creators that are the owner (the REST routes say "user", a goal they set
# "owner"), and the turn sources in which the owner speaks
# (memory_service.OWNER_SPEAKS_SOURCES).
OWNER_CREATORS = ("user", "owner")
OWNER_TURN_SOURCES = ("chat", "voice", "widget", "alexa", "ping_owner", "owner_ping")
# Edward's own threads, for an event made before ``created_in`` was stamped
# (mind_service.EDWARD_OWN_THREAD_IDS when it imports).
_EDWARD_THREADS_FALLBACK = frozenset({"edward-mind", "edward-surfaces"})


def _current_turn_source() -> Optional[str]:
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001
        return None
    source = getattr(rec, "source", None) if rec is not None else None
    return str(source) if source else None


# What a follow-through needs of the ping being answered when an event is
# made (2026-10-11; docs/architecture/pings.md): the chat to reply in.
PING_STAMP_KEYS = ("ping_id", "chat_identifier", "chat_guid", "is_group", "conversation_id")


def ping_stamp(ctx) -> Optional[dict]:
    """``event_metadata.ping`` from the pings' current-ping context: the chat
    an event made while answering a ping should follow up in. None without
    a ping or a chat. Pure."""
    if not isinstance(ctx, dict) or not ctx.get("chat_identifier"):
        return None
    return {k: ctx.get(k) for k in PING_STAMP_KEYS}


def _current_ping_stamp() -> Optional[dict]:
    """``ping_stamp`` of ``services.pings.hooks.CURRENT_PING``, read lazily
    (``services.pings`` may be absent); None on any failure."""
    try:
        from services.pings import hooks
    except ImportError:
        return None
    try:
        return ping_stamp(hooks.CURRENT_PING.get())
    except Exception:  # noqa: BLE001
        return None


def reach_stamp(policy) -> Optional[dict]:
    """``event_metadata.reach`` for an event made under ``policy``: what
    that turn could do, so the event's own turn never gets more
    (``scheduler_service.event_reach``; review 2026-10-11 — the 03:00
    nightly scheduling a task that pushed at 07:01). None for no policy or
    a turn the owner was asking in. Pure."""
    if policy is None or bool(getattr(policy, "owner_asked", False)):
        return None
    return {"may_reach_people": bool(getattr(policy, "may_reach_people", False)),
            "may_push": bool(getattr(policy, "may_push", False))}


def _current_reach_stamp() -> Optional[dict]:
    """``reach_stamp`` of the running turn's policy — only one its caller
    set (``policy_is_set``): a default policy says nothing about the
    turn. None without ``services.toolset.policy``, or on any failure."""
    try:
        from services.toolset import policy as policy_mod
    except ImportError:
        return None
    try:
        if not policy_mod.policy_is_set():
            return None
        return reach_stamp(policy_mod.current_policy())
    except Exception:  # noqa: BLE001
        return None


def _edward_threads() -> frozenset:
    try:
        from services.mind_service import EDWARD_OWN_THREAD_IDS

        return frozenset(EDWARD_OWN_THREAD_IDS)
    except Exception:  # noqa: BLE001
        return _EDWARD_THREADS_FALLBACK


def event_is_owners(event) -> bool:
    """Did the owner make this event — so its turn may spend the reserve
    they keep past the daily Claude cap? Pure but for the thread list.

    Yes when the REST routes or a goal of theirs made it (``created_by``),
    or when it was made in their own turn (``event_metadata.created_in`` in
    ``OWNER_TURN_SOURCES``). No when it was made in any other turn (a wake,
    the pulse, another event, a heartbeat reply). An event made before the
    stamp is theirs unless it lives in one of Edward's own threads (the
    mind's follow-ups there) — the old behaviour otherwise."""
    if str(getattr(event, "created_by", "") or "").strip().lower() in OWNER_CREATORS:
        return True
    try:
        meta = json.loads(getattr(event, "event_metadata", None) or "{}") or {}
    except (TypeError, ValueError):
        meta = {}
    # The toolset's ``schedule`` stamps whose ask it was (2026-10-11): a
    # task the owner asked for in their own ping counts as theirs.
    if isinstance(meta, dict) and isinstance(meta.get("requested_by_owner"), bool):
        return meta["requested_by_owner"]
    created_in = meta.get("created_in") if isinstance(meta, dict) else None
    if created_in:
        return str(created_in) in OWNER_TURN_SOURCES
    return getattr(event, "conversation_id", None) not in _edward_threads()


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
    """Format a naive-UTC datetime in the user's configured timezone."""
    from services.timezone_utils import format_local
    return format_local(dt, fmt)


def _utcnow() -> datetime:
    """Get current time as naive UTC."""
    return datetime.utcnow()


def _next_cron_utc(cron_pattern: str, after: datetime) -> datetime:
    """
    Compute next cron fire time in naive UTC.

    Cron patterns are interpreted in the server's local timezone (since that's
    what the user sees), then converted to naive UTC for storage.
    """
    from services.timezone_utils import get_user_tz, local_naive_to_utc
    # Convert naive UTC 'after' to local for cron calculation
    user_tz = get_user_tz()
    local_after = after.replace(tzinfo=timezone.utc).astimezone(user_tz).replace(tzinfo=None)
    cron = croniter(cron_pattern, local_after)
    local_next = cron.get_next(datetime)
    # The cron result is a naive *user-local* time; read it in the user zone
    # (never ``.astimezone()``, which would mean the host zone) and store UTC.
    return _to_naive_utc(local_naive_to_utc(local_next))


def _description_tokens(description: str) -> set:
    import re

    return set(re.findall(r"[a-z0-9]{3,}", (description or "").lower()))


async def _find_duplicate_pending_event(
    session,
    description: str,
    scheduled_at: datetime,
    delivery_channel: Optional[str],
) -> Optional[ScheduledEventModel]:
    """Find an unfired near-twin of the event being created.

    Two heartbeat triggers for the same real-world happening (e.g. a
    delivery notification arriving twice) used to create two events with
    the same fire time and near-identical descriptions — both fired
    (double Alexa announcement, 2026-06-29). Same fire time + same channel
    + high description-token containment ⇒ same event.
    """
    result = await session.execute(
        select(ScheduledEventModel).where(
            and_(
                ScheduledEventModel.scheduled_at == scheduled_at,
                ScheduledEventModel.status.in_(("pending", "processing")),
            )
        )
    )
    new_tokens = _description_tokens(description)
    for existing in result.scalars().all():
        if (existing.delivery_channel or None) != (delivery_channel or None):
            continue
        existing_tokens = _description_tokens(existing.description)
        if not new_tokens or not existing_tokens:
            continue
        containment = len(new_tokens & existing_tokens) / min(
            len(new_tokens), len(existing_tokens)
        )
        if containment >= 0.8:
            return existing
    return None


INTENTION_RECURRING_HORIZON_DAYS = 30
INTENTION_ONESHOT_GRACE_DAYS = 1


def intention_valid_until(recurrence_pattern: Optional[str], scheduled_at: datetime, now: datetime) -> datetime:
    """How long the intention behind an event stays live: a day past a
    one-shot fire, thirty days for a recurring one (the Sunday reflection
    renews or retires it)."""
    if recurrence_pattern:
        return now + timedelta(days=INTENTION_RECURRING_HORIZON_DAYS)
    return scheduled_at + timedelta(days=INTENTION_ONESHOT_GRACE_DAYS)


def extended_horizon(current: Optional[datetime], fire_at: datetime, recurrence_pattern: Optional[str],
                     now: datetime) -> Optional[datetime]:
    """The ``valid_until`` a linked intention needs so it outlives the event
    that serves it, or None when it already does (or is open-ended). Pure.

    A wake's intention with no horizon lapses after 72 h
    (``MIND_INTENTION_DEFAULT_HOURS``); a ``wake_me`` five days out used to
    outlive the intention it promised to hold (review 2026-09-26)."""
    if current is None:
        return None
    needed = intention_valid_until(recurrence_pattern, fire_at, now)
    return needed if current < needed else None


async def _hold_linked_intention(memory_id: str, fire_at: datetime, recurrence_pattern: Optional[str]) -> None:
    """Stretch the linked intention's horizon past the event's fire. Best
    effort: a missing or non-intention memory is left alone."""
    from services.database import MemoryModel

    try:
        async with async_session() as session:
            row = (await session.execute(
                select(MemoryModel).where(MemoryModel.id == memory_id, MemoryModel.memory_type == "intention")
            )).scalar_one_or_none()
            if row is None:
                return
            until = extended_horizon(row.valid_until, fire_at, recurrence_pattern, datetime.utcnow())
            if until is None:
                return
            row.valid_until = until
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[events] could not hold intention {memory_id} until its wake: {exc}")


async def create_event(
    description: str,
    scheduled_at: datetime,
    recurrence_pattern: Optional[str] = None,
    conversation_id: Optional[str] = None,
    delivery_channel: Optional[str] = None,
    created_by: str = "edward",
    event_metadata: Optional[dict] = None,
    memory_id: Optional[str] = None,
) -> ScheduledEventModel:
    """Create a new scheduled event. ``event_metadata`` (a dict) is stored
    as JSON; the scheduler routes ``{"type": "mind_wake"}`` rows to the mind.
    ``memory_id`` links the intention memory that explains why the event
    exists (mind rebuild Phase 4); it rides in the metadata. ``created_in``
    records the source of the turn that made it (``event_is_owners``), unless
    the caller set it; ``ping`` the chat of the ping being answered, if any
    (``ping_stamp``, 2026-10-11); ``reach`` what that turn's policy let it
    do, when it was not the owner's (``reach_stamp``, 2026-10-11)."""
    _validate_delivery_channel(delivery_channel)
    if memory_id:
        event_metadata = {**(event_metadata or {}), "memory_id": memory_id}
    made_in = _current_turn_source()
    if made_in and not (event_metadata or {}).get("created_in"):
        event_metadata = {**(event_metadata or {}), "created_in": made_in}
    # Made while answering a ping: the chat its follow-up replies in
    # (``mind_service.follow_through_for``). An existing stamp is kept.
    stamp = _current_ping_stamp()
    if stamp and not (event_metadata or {}).get("ping"):
        event_metadata = {**(event_metadata or {}), "ping": stamp}
    # Made in a turn that could not reach people or push (the nightly, a
    # speak wake held to a note): its event fires with no more than that.
    reach = _current_reach_stamp()
    if reach and not (event_metadata or {}).get("reach"):
        event_metadata = {**(event_metadata or {}), "reach": reach}
    if recurrence_pattern and not croniter.is_valid(recurrence_pattern):
        raise ValueError(f"Invalid cron pattern: {recurrence_pattern}")

    scheduled_at = _to_naive_utc(scheduled_at)
    event_id = str(uuid.uuid4())

    async with async_session() as session:
        duplicate = await _find_duplicate_pending_event(
            session, description, scheduled_at, delivery_channel
        )
        if duplicate is not None:
            return duplicate

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
            event_metadata=json.dumps(event_metadata) if event_metadata else None,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
    if memory_id:
        await _hold_linked_intention(memory_id, scheduled_at, recurrence_pattern)
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


def linked_memory_id(event_metadata: Optional[str]) -> Optional[str]:
    """The intention memory an event carries, if any."""
    try:
        meta = json.loads(event_metadata or "{}") or {}
    except (TypeError, ValueError):
        return None
    mid = meta.get("memory_id") if isinstance(meta, dict) else None
    return str(mid) if mid else None


async def cancel_event(event_id: str) -> bool:
    """Cancel an event by setting status to 'cancelled'. The intention
    behind it (if any) is retired with it, so the mind stops seeing it."""
    async with async_session() as session:
        row = (await session.execute(
            select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
        )).scalar_one_or_none()
        meta = row.event_metadata if row is not None else None
        result = await session.execute(
            update(ScheduledEventModel)
            .where(ScheduledEventModel.id == event_id)
            .where(ScheduledEventModel.status.in_(["pending", "processing"]))
            .values(status="cancelled", updated_at=_utcnow())
        )
        await session.commit()
        cancelled = result.rowcount > 0
    if cancelled:
        mid = linked_memory_id(meta)
        if mid:
            try:
                from services.memory_service import update_memory
                await update_memory(mid, valid_until=_utcnow())
            except Exception as exc:  # noqa: BLE001
                print(f"[events] could not retire intention {mid}: {exc}")
    return cancelled


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
            # updated_at in UTC from Python: the server's now() writes the
            # session zone into this naive-UTC column, and stale-processing
            # recovery compares it with UTC (2026-09-24).
            .values(status="processing", updated_at=now)
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


async def recover_stale_processing_events(stale_after_seconds: int = 1800) -> int:
    """Reclaim events orphaned in status='processing'.

    get_due_events() claims events by flipping them to 'processing'; if the
    process dies before mark_event_completed/failed runs, the row is stranded
    forever because the scheduler only selects 'pending'. Called with 0 at
    startup (nothing can legitimately be executing then) and with a generous
    threshold from the polling loop as a backstop for in-process crashes.

    Recurring events are re-armed at their next cron occurrence; one-time
    events are marked failed rather than fired late (they may be months
    stale by the time we notice).

    Returns the number of events recovered.
    """
    now = _utcnow()
    cutoff = now - timedelta(seconds=stale_after_seconds)

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledEventModel)
            .where(
                and_(
                    ScheduledEventModel.status == "processing",
                    ScheduledEventModel.updated_at <= cutoff,
                )
            )
            .with_for_update()
        )
        events = list(result.scalars().all())
        if not events:
            return 0

        note = "recovered: orphaned in 'processing' (execution interrupted)"
        for event in events:
            event.updated_at = now
            event.last_result = note
            if event.recurrence_pattern:
                event.next_fire_at = _next_cron_utc(event.recurrence_pattern, now)
                event.status = "pending"
            else:
                event.status = "failed"

        await session.commit()
        return len(events)


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
