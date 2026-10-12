"""The owner's verdict on a heartbeat decision ("right call?").

Stored in ``heartbeat_feedback``; one row per (event, verdict) press. The
mind reads the verdicts (``list_feedback``: its pack and the nightly's
"anything new?" check).

From 2026-09-12 a "should have told me" / "should have noted" verdict was
also copied into ``heartbeat_false_dismissal_signals`` for the legacy
triage engine's learners. That engine and its learners are gone, nothing
reads that table any more, and the copy went with them.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

VERDICTS: tuple[str, ...] = (
    "right_call",
    "should_have_told_me",
    "should_have_noted",
    "not_worth_noting",
)


def normalize_verdict(v: Optional[str]) -> str:
    """Accept 'right_call', 'Right call', 'right-call'; reject anything else."""
    key = re.sub(r"[\s\-]+", "_", (v or "").strip().lower())
    if key not in VERDICTS:
        raise ValueError(f"unknown verdict: {v!r}")
    return key


async def record_feedback(event_id: str, verdict: str, note: Optional[str] = None) -> Dict:
    from sqlalchemy import select
    from services.database import async_session, HeartbeatEventModel, HeartbeatFeedbackModel

    verdict = normalize_verdict(verdict)
    async with async_session() as session:
        event = (
            await session.execute(select(HeartbeatEventModel).where(HeartbeatEventModel.id == event_id))
        ).scalar_one_or_none()
        if event is None:
            raise LookupError("event not found")
        row = HeartbeatFeedbackModel(
            id=str(uuid.uuid4()),
            event_id=event_id,
            verdict=verdict,
            triage_status=event.triage_status,
            contact_key=event.person_key or event.chat_identifier or event.sender,
            note=note,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(row)
        await session.commit()
        return _to_dict(row)


async def list_feedback(limit: int = 200) -> List[Dict]:
    from sqlalchemy import select, desc
    from services.database import async_session, HeartbeatFeedbackModel

    async with async_session() as session:
        rows = (
            await session.execute(
                select(HeartbeatFeedbackModel).order_by(desc(HeartbeatFeedbackModel.created_at)).limit(limit)
            )
        ).scalars().all()
    return [_to_dict(r) for r in rows]


def _to_dict(r) -> Dict:
    return {
        "id": r.id,
        "event_id": r.event_id,
        "verdict": r.verdict,
        "triage_status": r.triage_status,
        "contact_key": r.contact_key,
        "note": r.note,
        "created_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
    }
