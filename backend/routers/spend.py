"""``GET /api/health/spend?days=7`` — who spends the Codex week, how fast,
and what it bought (``services/spend_report_service.py``). Behind the auth
middleware like every other ``/api/*`` route."""

from __future__ import annotations

from fastapi import APIRouter, Query

from services.spend_report_service import build_spend_report

router = APIRouter()


@router.get("/health/spend")
async def health_spend(days: int = Query(7, ge=1, le=30)):
    return await build_spend_report(days)
