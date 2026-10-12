"""Weekly Journal — Edward's record of what he learned + decided."""

from __future__ import annotations

from fastapi import APIRouter

from services.journal_service import build_weekly_journal

router = APIRouter()


@router.get("/journal/week")
async def journal_week():
    """Aggregated 7-day view of observations, proposals, wakeup ticks."""
    return await build_weekly_journal()
