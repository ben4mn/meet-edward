"""``/api/signals`` — the owner's implicit verdicts from the UI, one small row each."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import user_signals_service as us

router = APIRouter()


class SignalIn(BaseModel):
    kind: str
    detail: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None
    ref_id: Optional[str] = None


@router.post("/signals")
async def post_signal(body: SignalIn):
    try:
        kind, detail_json = us.normalize_signal(body.kind, body.detail)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    sid = await us.record_signal(kind, detail_json, conversation_id=body.conversation_id, ref_id=body.ref_id)
    return {"id": sid, "kind": kind}


@router.get("/signals")
async def list_signals(days: int = Query(7, ge=1, le=90)):
    return {"days": days, "signals": await us.recent_signals(days)}
