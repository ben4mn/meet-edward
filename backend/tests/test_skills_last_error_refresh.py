"""R/G TDD for Task #22 — skill last_error must clear on successful connect.

5 skills in prod show errors from 2026-05-09 — env vars that ARE set in
``.env`` now (VAPID, BRAVE_SEARCH_API_KEY, MCP_APPLE_ENABLED, etc.).
The settings UI claims they're broken; they aren't. Settings is lying
because ``_update_skill_db`` treats ``last_error=None`` as "don't
touch" while ``get_all_skills`` passes that exact value to mean "clear
the error." Result: stale errors never get wiped.

Fix: callers asking to clear pass an explicit empty string (or the
helper learns to distinguish "no update" from "clear").
"""

from __future__ import annotations

from datetime import datetime

import pytest


@pytest.mark.integration
async def test_connected_skill_clears_stale_last_error(clean_db, monkeypatch):
    from services.database import async_session, SkillModel
    from sqlalchemy import select
    from services import skills_service

    # Seed a skill row with a stale error (mirrors prod state).
    async with async_session() as session:
        session.add(SkillModel(
            id="brave_search",
            enabled=True,
            last_error="BRAVE_SEARCH_API_KEY not set",
            last_connected_at=datetime(2026, 5, 9, 15, 14, 37),
        ))
        await session.commit()

    # Force the live status getter for this skill to return CONNECTED.
    def _fake_connected_status():
        return {"status": "connected", "status_message": None, "metadata": None}

    monkeypatch.setitem(
        skills_service.SKILL_DEFINITIONS["brave_search"],
        "get_status",
        _fake_connected_status,
    )

    await skills_service.get_all_skills()

    async with async_session() as session:
        row = (await session.execute(
            select(SkillModel).where(SkillModel.id == "brave_search")
        )).scalar_one()
    assert row.last_error is None, (
        f"stale last_error should clear once status=connected; "
        f"got {row.last_error!r}"
    )
    assert row.last_connected_at is not None
    assert row.last_connected_at > datetime(2026, 5, 9, 23, 59), (
        "last_connected_at should advance to ~now on a successful check"
    )


@pytest.mark.integration
async def test_error_status_updates_last_error_text(clean_db, monkeypatch):
    """If a skill *becomes* broken, last_error should update to the new
    message — not keep the old one or stay None."""
    from services.database import async_session, SkillModel
    from sqlalchemy import select
    from services import skills_service

    async with async_session() as session:
        session.add(SkillModel(
            id="brave_search",
            enabled=True,
            last_error=None,
        ))
        await session.commit()

    def _fake_error_status():
        return {
            "status": "error",
            "status_message": "API rate limit exceeded",
            "metadata": None,
        }

    monkeypatch.setitem(
        skills_service.SKILL_DEFINITIONS["brave_search"],
        "get_status",
        _fake_error_status,
    )

    await skills_service.get_all_skills()

    async with async_session() as session:
        row = (await session.execute(
            select(SkillModel).where(SkillModel.id == "brave_search")
        )).scalar_one()
    assert row.last_error == "API rate limit exceeded"


@pytest.mark.integration
async def test_disabled_skill_state_does_not_clobber_last_error(
    clean_db, monkeypatch
):
    """Disabled skills shouldn't have their stored last_error touched by
    the status loop — they're not being checked."""
    from services.database import async_session, SkillModel
    from sqlalchemy import select
    from services import skills_service

    async with async_session() as session:
        session.add(SkillModel(
            id="brave_search",
            enabled=False,
            last_error="stored from a prior state",
        ))
        await session.commit()

    await skills_service.get_all_skills()

    async with async_session() as session:
        row = (await session.execute(
            select(SkillModel).where(SkillModel.id == "brave_search")
        )).scalar_one()
    assert row.last_error == "stored from a prior state"
