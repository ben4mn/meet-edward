"""
Open loop tracking service for Edward.

Manages unresolved items (tasks, follow-ups, commitments) that Edward
detects in conversations and should track until resolved.
"""

import uuid
from typing import List, Optional
from datetime import datetime

from sqlalchemy import select, func, update

from services.database import async_session, OpenLoopModel


async def create_open_loop(
    description: str,
    source_memory_id: Optional[str] = None,
    source_conversation_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    priority: float = 0.5,
) -> OpenLoopModel:
    """Create a new open loop."""
    async with async_session() as session:
        loop = OpenLoopModel(
            id=str(uuid.uuid4()),
            description=description,
            source_memory_id=source_memory_id,
            source_conversation_id=source_conversation_id,
            entity_id=entity_id,
            priority=priority,
        )
        session.add(loop)
        await session.commit()
        await session.refresh(loop)
        print(f"[OPEN_LOOP] Created: {description[:80]}")
        return loop


async def get_active_open_loops(limit: int = 20) -> List[OpenLoopModel]:
    """Get active (open) loops, ordered by priority then age."""
    async with async_session() as session:
        result = await session.execute(
            select(OpenLoopModel)
            .where(OpenLoopModel.status == "open")
            .order_by(OpenLoopModel.priority.desc(), OpenLoopModel.detected_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def get_open_loops(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[OpenLoopModel]:
    """Get open loops with optional status filter."""
    async with async_session() as session:
        query = select(OpenLoopModel)
        if status:
            query = query.where(OpenLoopModel.status == status)
        query = (
            query.order_by(OpenLoopModel.detected_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(query)
        return list(result.scalars().all())


async def resolve_open_loop(loop_id: str) -> Optional[OpenLoopModel]:
    """Mark an open loop as resolved."""
    async with async_session() as session:
        result = await session.execute(
            select(OpenLoopModel).where(OpenLoopModel.id == loop_id)
        )
        loop = result.scalar_one_or_none()
        if not loop:
            return None
        loop.status = "resolved"
        loop.resolved_at = datetime.utcnow()
        await session.commit()
        await session.refresh(loop)
        print(f"[OPEN_LOOP] Resolved: {loop.description[:80]}")
        return loop


async def snooze_open_loop(loop_id: str, until: datetime) -> Optional[OpenLoopModel]:
    """Snooze an open loop until a specific time."""
    async with async_session() as session:
        result = await session.execute(
            select(OpenLoopModel).where(OpenLoopModel.id == loop_id)
        )
        loop = result.scalar_one_or_none()
        if not loop:
            return None
        loop.status = "snoozed"
        loop.snooze_until = until
        await session.commit()
        await session.refresh(loop)
        return loop


async def update_nudge(loop_id: str) -> None:
    """Update last_nudged_at and increment nudge_count."""
    async with async_session() as session:
        result = await session.execute(
            select(OpenLoopModel).where(OpenLoopModel.id == loop_id)
        )
        loop = result.scalar_one_or_none()
        if loop:
            loop.last_nudged_at = datetime.utcnow()
            loop.nudge_count = (loop.nudge_count or 0) + 1
            await session.commit()


async def expire_old_loops(days: int = 30) -> int:
    """Mark loops older than N days with no nudges as expired."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        result = await session.execute(
            select(OpenLoopModel).where(
                OpenLoopModel.status == "open",
                OpenLoopModel.detected_at < cutoff,
                OpenLoopModel.nudge_count == 0,
            )
        )
        loops = list(result.scalars().all())
        for loop in loops:
            loop.status = "expired"
        await session.commit()
        if loops:
            print(f"[OPEN_LOOP] Expired {len(loops)} old loops")
        return len(loops)


async def reactivate_snoozed_loops() -> int:
    """Reactivate snoozed loops whose snooze_until has passed."""
    async with async_session() as session:
        result = await session.execute(
            select(OpenLoopModel).where(
                OpenLoopModel.status == "snoozed",
                OpenLoopModel.snooze_until <= datetime.utcnow(),
            )
        )
        loops = list(result.scalars().all())
        for loop in loops:
            loop.status = "open"
            loop.snooze_until = None
        await session.commit()
        return len(loops)


async def get_open_loop_count() -> int:
    """Get count of active open loops."""
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(OpenLoopModel)
            .where(OpenLoopModel.status == "open")
        )
        return result.scalar() or 0
