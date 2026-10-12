"""Push engagement — did the owner act on what we pushed?

Two writers remain (mind rebuild Phase 8): ``mark_event_acted_on`` (the
push-outcome detector) and ``mark_recent_pushes_acted`` (a chat message
within half an hour of a push counts as acting on it). The per-entity
affinity table and the relationship-thread refresh that used to hang off
them are gone with the engine layer; what a person would remember is in
``memories``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from services.database import HeartbeatEventModel, async_session


async def mark_event_acted_on(event_id: str) -> bool:
    """Mark a heartbeat event as "user engaged with the push".

    Returns True if the row existed and was updated, False if not found.
    """
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as session:
        event = (await session.execute(
            select(HeartbeatEventModel).where(HeartbeatEventModel.id == event_id)
        )).scalar_one_or_none()
        if event is None:
            return False
        result = await session.execute(
            update(HeartbeatEventModel)
            .where(
                HeartbeatEventModel.id == event_id,
                HeartbeatEventModel.user_acted_on_it.is_(False),
            )
            .values(user_acted_on_it=True, user_acted_at=now_naive)
        )
        await session.commit()
    return (result.rowcount or 0) > 0


async def mark_recent_pushes_acted(within_minutes: int = 30) -> int:
    """Heuristic engagement classifier.

    When the user sends a chat message, any push we sent in the last
    ``within_minutes`` arguably "worked" — it either directly got them
    to Edward or it's part of the context they're responding to.

    Returns the number of events newly marked as acted.
    """
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now_naive - timedelta(minutes=within_minutes)
    async with async_session() as session:
        result = await session.execute(
            update(HeartbeatEventModel)
            .where(
                HeartbeatEventModel.notified_at.is_not(None),
                HeartbeatEventModel.notified_at > cutoff,
                HeartbeatEventModel.user_acted_on_it.is_(False),
            )
            .values(user_acted_on_it=True, user_acted_at=now_naive)
        )
        await session.commit()
    return int(result.rowcount or 0)
