"""``/api/mind`` — wake Edward's mind by hand and read what it decided.

The scheduler and the heartbeat invite wakes on their own; these
endpoints are for the owner and for verification.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import mind_service

router = APIRouter()


class WakeRequest(BaseModel):
    reason: str = "manual"
    extra: str | None = None


@router.post("/mind/wake", status_code=202)
async def wake(req: WakeRequest):
    """Start a wake and answer at once (2026-09-25). A wake runs for minutes
    and a PWA served through a tunnel (Cloudflare gives up after 100 s, a
    524) cannot wait for it, so the wake runs as a tracked task (``request_wake``);
    ``GET /api/mind/now`` shows it running and ``/mind/recent`` its row.
    A press while the same wake is still pending or running joins it."""
    if req.reason not in mind_service.REASONS:
        raise HTTPException(status_code=400, detail=f"unknown reason {req.reason!r}; expected one of {list(mind_service.REASONS)}")
    started = mind_service.request_wake(req.reason, delay_s=0, extra=req.extra)
    return {"accepted": True, "reason": req.reason, "joined": not started}


@router.get("/mind/now")
async def now():
    """Edward's own live state for the PWA's live Edward (2026-09-25): the
    wake or pulse turn running now, his last wakes, the next one, what waits
    on the owner, the quota meter and his hands. Every source degrades on its
    own; never a 500. (The owner's device presence is ``/api/presence``.)"""
    from services import wake_state

    return await wake_state.snapshot()


@router.get("/mind/recent")
async def recent(limit: int = Query(20, ge=1, le=200)):
    return {"wakes": await mind_service.recent(limit=limit)}


class VerdictRequest(BaseModel):
    ledger_id: int
    verdict: str
    note: Optional[str] = None


@router.get("/mind/log")
async def log(days: int = Query(7, ge=1, le=90)):
    """The owner's window: every wake with its pushes and their verdict."""
    return await mind_service.log(days=days)


@router.get("/mind/intentions")
async def intentions():
    return {"intentions": await mind_service.open_intentions()}


@router.post("/mind/verdict")
async def verdict(req: VerdictRequest):
    try:
        sid = await mind_service.record_verdict(req.ledger_id, req.verdict, req.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if sid is None:
        raise HTTPException(status_code=404, detail="no such wake")
    return {"id": sid, "kind": "mind_verdict", "ledger_id": req.ledger_id}
