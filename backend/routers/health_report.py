"""``GET /api/health/report?days=7`` — the ledgers summarized for a health check."""

from __future__ import annotations

from fastapi import APIRouter, Query

from services.health_report_service import build_report

router = APIRouter()


@router.get("/health/report")
async def health_report(days: int = Query(7, ge=1, le=30)):
    return await build_report(days)


@router.get("/health/email")
async def health_email():
    """Public, for the watchdog (which has no cookie): mail's verdict and its
    warnings — Mail.app down, Mail's index stale, a silent listener, Gmail's
    sign-in lapsed — and nothing else: no address, no subject (2026-10-11).
    Kept 60 s (``mail_service.cached_public_summary``): anyone can reach it
    through a public tunnel, if one is set up. The full block is ``/api/health/report`` →
    ``email``."""
    from services import mail_service

    return await mail_service.cached_public_summary()
