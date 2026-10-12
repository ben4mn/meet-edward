"""``/api/approvals`` — the owner's answers to sends held at the boundary.

``EDWARD_TAINT_APPROVALS=on`` holds a send to a third party from a
tainted, unattended turn (``taint_service``) and asks the owner with a push;
the service worker's Approve / Deny buttons and Today's need post here.
Behind the session cookie like every other route, so only the owner's own
browser can answer (``approvals_service`` has the rest of the why).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import approvals_service

router = APIRouter()


class DecisionRequest(BaseModel):
    decision: str


@router.get("/approvals")
async def list_approvals(status: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500)):
    try:
        rows = await approvals_service.list_approvals(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"approvals": [approvals_service.public(r) for r in rows]}


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: int):
    row = await approvals_service.get_approval(approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no approval #{approval_id}")
    return approvals_service.public(row)


@router.post("/approvals/{approval_id}")
async def decide(approval_id: int, req: DecisionRequest):
    try:
        row = await approvals_service.decide(approval_id, req.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except approvals_service.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return approvals_service.public(row)
