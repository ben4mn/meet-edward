"""Weekly Journal aggregator.

Pulls a 7-day window of the learn-loop's exhaust — observations,
the mind's wakes, contradiction flags — into a single
shape the frontend renders as Edward's "what I did and noticed this
week" panel.

This is the social-proof layer of the Jarvis roadmap: without it the
system can be learning loudly and feel invisible. Surfacing the journal
makes the work observable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from services.database import (
    SelfObservationModel,
    async_session,
)


WINDOW_DAYS = 7


async def _observation_summary() -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    async with async_session() as session:
        # Counts by kind in-window
        result = await session.execute(
            select(SelfObservationModel.kind, func.count(SelfObservationModel.id))
            .where(SelfObservationModel.last_seen_at >= cutoff)
            .group_by(SelfObservationModel.kind)
            .order_by(desc(func.count(SelfObservationModel.id)))
        )
        by_kind = [{"kind": k, "count": int(c)} for k, c in result.all()]

        # Recent (last 10)
        result = await session.execute(
            select(SelfObservationModel)
            .where(SelfObservationModel.last_seen_at >= cutoff)
            .order_by(desc(SelfObservationModel.last_seen_at))
            .limit(10)
        )
        recent = [
            {
                "id": o.id,
                "kind": o.kind,
                "signature": o.signature,
                "occurrences": o.occurrences,
                "description": (o.description or "")[:200],
                "last_seen_at": o.last_seen_at.isoformat() if o.last_seen_at else None,
                "status": o.status,
            }
            for o in result.scalars().all()
        ]

    total = sum(row["count"] for row in by_kind)
    return {"by_kind": by_kind, "recent": recent, "total_in_window": total}


async def _wakes_summary() -> dict[str, Any]:
    """What the mind decided this week, from its ledger (mind rebuild Phase 3)."""
    from services.database import LedgerEventModel
    from services.mind_service import recent

    cutoff = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    async with async_session() as session:
        result = await session.execute(
            select(LedgerEventModel.outcome, func.count(LedgerEventModel.id))
            .where(LedgerEventModel.kind == "mind", LedgerEventModel.created_at >= cutoff)
            .group_by(LedgerEventModel.outcome)
        )
        by_outcome = [{"outcome": o or "unknown", "count": int(c)} for o, c in result.all()]
    return {
        "by_outcome": by_outcome,
        "recent": await recent(limit=15),
        "total_in_window": sum(r["count"] for r in by_outcome),
    }


async def build_weekly_journal() -> dict[str, Any]:
    """Top-level aggregator. Cheap; bounded by 7-day window."""
    observations = await _observation_summary()
    wakes = await _wakes_summary()
    return {
        "window_days": WINDOW_DAYS,
        "observations": observations,
        "wakes": wakes,
    }
