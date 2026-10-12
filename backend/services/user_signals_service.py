"""The owner's implicit verdicts from the UI — ``user_signals``.

The frontend posts a small row when the owner acts on a Today need (or the
action fails). Kinds are an allowlist so the table cannot become a
dumping ground; detail is capped. Pruned with the turn ledger.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SIGNAL_KINDS = frozenset({
    "today_action",         # the owner ran a need's action (detail: need_kind, action)
    "today_action_failed",  # the action errored (detail: need_kind, action, error)
    "chat_stopped",         # stop pressed (the ledger also sees this server-side)
    "response_retry",       # the owner re-sent / regenerated
    "feedback",             # free-form thumbs (detail: verdict, note)
    "mind_verdict",         # the owner's "right call?" on a wake (ref_id: ledger id; detail: verdict, note)
    "idea_tried",           # "Try it" on a Today idea (ref_id: note id; detail: title)
    "idea_dismissed",       # "Not now" on a Today idea (ref_id: note id; detail: title)
})
MAX_DETAIL_CHARS = 1000


def normalize_signal(kind: Optional[str], detail: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Validate the kind and serialize a bounded detail JSON."""
    kind = (kind or "").strip()
    if kind not in SIGNAL_KINDS:
        raise ValueError(f"unknown signal kind {kind!r}; one of {sorted(SIGNAL_KINDS)}")
    detail = dict(detail or {})
    text = json.dumps(detail, separators=(",", ":"), default=str)
    if len(text) > MAX_DETAIL_CHARS:
        # Keep the shape, trim the values, never raise over size.
        trimmed = {k: (v[:120] if isinstance(v, str) else v) for k, v in detail.items()}
        text = json.dumps(trimmed, separators=(",", ":"), default=str)[:MAX_DETAIL_CHARS]
    return kind, text


async def record_signal(
    kind: str, detail_json: str, conversation_id: Optional[str] = None, ref_id: Optional[str] = None
) -> str:
    from services.database import async_session, UserSignalModel

    async with async_session() as session:
        row = UserSignalModel(kind=kind, detail_json=detail_json, conversation_id=conversation_id, ref_id=ref_id)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def recent_signals(days: int = 7) -> List[Dict[str, Any]]:
    from sqlalchemy import select
    from services.database import async_session, UserSignalModel

    since = datetime.utcnow() - timedelta(days=days)
    async with async_session() as session:
        rows = (
            await session.execute(
                select(UserSignalModel).where(UserSignalModel.created_at >= since).order_by(UserSignalModel.created_at.desc()).limit(2000)
            )
        ).scalars().all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "conversation_id": r.conversation_id,
            "ref_id": r.ref_id,
            "detail_json": r.detail_json,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]
