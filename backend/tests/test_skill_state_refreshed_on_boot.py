"""R/G TDD for Phase B — skill last_error must refresh at boot.

5 skills in prod still show last_connected_at=2026-05-09 because
init_skills only ensures rows exist — it never runs get_all_skills(),
which is the function that actually pings each skill's status getter
and clears stale last_error on success.

Fix: init_skills awaits get_all_skills() after seeding rows.
"""

from __future__ import annotations

from datetime import datetime

import pytest


@pytest.mark.integration
async def test_init_skills_clears_stale_last_error_for_connected_skills(
    clean_db, monkeypatch
):
    from services.database import async_session, SkillModel
    from sqlalchemy import select
    from services import skills_service

    # Seed a stale error (mirrors what's in prod today)
    async with async_session() as session:
        session.add(SkillModel(
            id="brave_search",
            enabled=True,
            last_error="BRAVE_SEARCH_API_KEY not set",
            last_connected_at=datetime(2026, 5, 9, 15, 14, 37),
        ))
        await session.commit()

    # Force the live status getter to report CONNECTED (env present)
    def _fake_connected():
        return {"status": "connected", "status_message": None, "metadata": None}

    monkeypatch.setitem(
        skills_service.SKILL_DEFINITIONS["brave_search"],
        "get_status",
        _fake_connected,
    )

    # Run init_skills — should now also drive a status sweep
    await skills_service.init_skills()

    async with async_session() as session:
        row = (await session.execute(
            select(SkillModel).where(SkillModel.id == "brave_search")
        )).scalar_one()
    assert row.last_error is None, (
        f"init_skills must refresh status so stale errors clear; "
        f"got {row.last_error!r}"
    )
    assert row.last_connected_at is not None
    assert row.last_connected_at > datetime(2026, 5, 9, 23, 59), (
        "last_connected_at should advance to ~now on boot if the skill is connected"
    )


@pytest.mark.integration
async def test_init_skills_records_new_error_on_boot(
    clean_db, monkeypatch
):
    """If a skill is now failing (env vanished), init_skills should
    record the new error message — not leave the row stale."""
    from services.database import async_session, SkillModel
    from sqlalchemy import select
    from services import skills_service

    async with async_session() as session:
        session.add(SkillModel(id="brave_search", enabled=True, last_error=None))
        await session.commit()

    def _fake_error():
        return {
            "status": "error",
            "status_message": "API key revoked",
            "metadata": None,
        }

    monkeypatch.setitem(
        skills_service.SKILL_DEFINITIONS["brave_search"],
        "get_status",
        _fake_error,
    )

    await skills_service.init_skills()

    async with async_session() as session:
        row = (await session.execute(
            select(SkillModel).where(SkillModel.id == "brave_search")
        )).scalar_one()
    assert row.last_error == "API key revoked"
