"""
Session briefing service for Edward.

Generates a brief summary of what happened between conversations so Edward
has continuity when a new session starts: memories stored and updated,
scheduled events that fired, worker tasks finished, and memory
consolidation. (The old triage counts and coordination cases read tables
nothing writes since the triage slim, so they are gone.)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, and_

from services.database import (
    async_session,
    ConversationModel,
    MemoryModel,
    ScheduledEventModel,
    ConsolidationCycleModel,
)

logger = logging.getLogger(__name__)


async def get_last_conversation_end(exclude_conversation_id: str) -> Optional[datetime]:
    """Get the most recent conversation's last activity, excluding the current one."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel.updated_at)
            .where(ConversationModel.id != exclude_conversation_id)
            .order_by(ConversationModel.updated_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row.replace(tzinfo=None) if row.tzinfo else row
        return None


async def gather_briefing_data(since: datetime) -> dict:
    """Collect activity data from all subsystems since the given timestamp."""
    data = {
        "new_memories": 0,
        "updated_memories": 0,
        "fired_events": [],
        "completed_tasks": 0,
        "consolidation_summary": None,
    }

    async with async_session() as session:
        # New memories created since last conversation
        new_mem_result = await session.execute(
            select(func.count()).select_from(MemoryModel).where(
                MemoryModel.created_at >= since
            )
        )
        data["new_memories"] = new_mem_result.scalar() or 0

        # Updated memories (updated_at > since AND updated_at != created_at)
        updated_mem_result = await session.execute(
            select(func.count()).select_from(MemoryModel).where(
                and_(
                    MemoryModel.updated_at >= since,
                    MemoryModel.updated_at != MemoryModel.created_at,
                )
            )
        )
        data["updated_memories"] = updated_mem_result.scalar() or 0

        # Fired scheduled events
        fired_result = await session.execute(
            select(ScheduledEventModel.description).where(
                and_(
                    ScheduledEventModel.status == "completed",
                    ScheduledEventModel.last_fired_at >= since,
                )
            ).limit(5)
        )
        data["fired_events"] = [row[0] for row in fired_result.fetchall()]

        # Completed orchestrator tasks
        try:
            from services.database import OrchestratorTaskModel
            task_result = await session.execute(
                select(func.count()).select_from(OrchestratorTaskModel).where(
                    and_(
                        OrchestratorTaskModel.status == "completed",
                        OrchestratorTaskModel.completed_at >= since,
                    )
                )
            )
            data["completed_tasks"] = task_result.scalar() or 0
        except Exception:
            pass

        # Consolidation activity
        consol_result = await session.execute(
            select(
                func.sum(ConsolidationCycleModel.merges_performed),
                func.sum(ConsolidationCycleModel.connections_created),
                func.sum(ConsolidationCycleModel.contradictions_found),
            ).where(ConsolidationCycleModel.created_at >= since)
        )
        crow = consol_result.fetchone()
        if crow and any(v for v in crow if v):
            data["consolidation_summary"] = {
                "merges": crow[0] or 0,
                "connections": crow[1] or 0,
                "contradictions": crow[2] or 0,
            }

    return data


def _format_time_ago(dt: datetime) -> str:
    """Format a datetime as a human-readable 'time ago' string."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        minutes = int(delta.total_seconds() / 60)
        return f"{minutes}m ago"
    elif hours < 24:
        return f"{int(hours)}h ago"
    else:
        days = int(hours / 24)
        return f"{days}d ago"


async def generate_briefing(conversation_id: str) -> Optional[str]:
    """Generate a session briefing for a new conversation.

    Returns None if nothing noteworthy happened since the last conversation.
    """
    last_end = await get_last_conversation_end(conversation_id)
    if not last_end:
        return None

    data = await gather_briefing_data(last_end)

    # Check if anything actually happened
    has_activity = (
        data["new_memories"] > 0
        or data["updated_memories"] > 0
        or data["fired_events"]
        or data["completed_tasks"] > 0
        or data["consolidation_summary"]
    )
    if not has_activity:
        return None

    # Build briefing bullets directly (no LLM call needed for structured data)
    time_ago = _format_time_ago(last_end)
    bullets = []

    if data["new_memories"]:
        bullets.append(f"- {data['new_memories']} new memories stored")

    if data["updated_memories"]:
        bullets.append(f"- {data['updated_memories']} memories updated/reinforced")

    for desc in data["fired_events"]:
        bullets.append(f"- Scheduled event fired: \"{desc[:80]}\"")

    if data["completed_tasks"]:
        bullets.append(f"- {data['completed_tasks']} orchestrator task(s) completed")

    if data["consolidation_summary"]:
        c = data["consolidation_summary"]
        parts = []
        if c["merges"]:
            parts.append(f"{c['merges']} merges")
        if c["connections"]:
            parts.append(f"{c['connections']} connections")
        if c["contradictions"]:
            parts.append(f"{c['contradictions']} contradictions found")
        if parts:
            bullets.append(f"- Memory consolidation: {', '.join(parts)}")

    if not bullets:
        return None

    # Cap at ~800 chars
    briefing = f"## Since Your Last Conversation ({time_ago}):\n" + "\n".join(bullets)
    if len(briefing) > 800:
        briefing = briefing[:797] + "..."

    logger.info(f"Session briefing generated ({len(briefing)} chars)")
    return briefing
