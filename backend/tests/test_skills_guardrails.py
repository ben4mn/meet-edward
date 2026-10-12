"""Guardrail tests for silent skill disabling.

Incident 2026-07-05: all 21 skills were disabled by a direct DB write and
nobody noticed for two days (iMessage replies silently stopped delivering).

Guardrails under test:
1. `set_skill_enabled(..., False)` fires a best-effort push notification.
2. `init_skills()` detects the "all skills disabled" state on boot and raises
   the alarm (log + push + a system heartbeat event that lands in the next
   briefing even if push is dead).
3. A Postgres trigger writes `skills_audit` rows for ANY writer that flips
   `skills.enabled` — including raw SQL that bypasses the app.
4. `check_mass_disable_burst()` alerts when many disables land in a short
   window (called from the triage cycle).
"""

from datetime import datetime, timedelta

import pytest
from unittest.mock import AsyncMock

from services import skills_service


@pytest.fixture(autouse=True)
def _reset_alert_dedup():
    skills_service._last_mass_disable_alert = None
    yield
    skills_service._last_mass_disable_alert = None


def _patch_db_free(monkeypatch):
    """Stub out DB/registry side effects so set_skill_enabled is unit-testable."""
    monkeypatch.setattr(skills_service, "_update_skill_db", AsyncMock())
    monkeypatch.setattr(skills_service, "get_all_skills", AsyncMock(return_value=[]))
    import services.tool_registry as tool_registry

    monkeypatch.setattr(tool_registry, "refresh_registry", AsyncMock())


@pytest.mark.asyncio
async def test_disable_sends_push(monkeypatch):
    _patch_db_free(monkeypatch)
    import services.push_service as push_service

    push = AsyncMock(return_value={"sent": 1})
    monkeypatch.setattr(push_service, "send_push_notification", push)

    await skills_service.set_skill_enabled("contacts_lookup", False)

    assert push.await_count == 1
    kwargs = push.await_args.kwargs
    blob = " ".join(str(v) for v in kwargs.values())
    assert "contacts_lookup" in blob


@pytest.mark.asyncio
async def test_enable_does_not_push(monkeypatch):
    _patch_db_free(monkeypatch)
    import services.push_service as push_service

    push = AsyncMock(return_value={"sent": 1})
    monkeypatch.setattr(push_service, "send_push_notification", push)

    await skills_service.set_skill_enabled("contacts_lookup", True)

    assert push.await_count == 0


@pytest.mark.asyncio
async def test_disable_push_failure_is_swallowed(monkeypatch):
    _patch_db_free(monkeypatch)
    import services.push_service as push_service

    monkeypatch.setattr(
        push_service,
        "send_push_notification",
        AsyncMock(side_effect=RuntimeError("push backend down")),
    )

    skill = await skills_service.set_skill_enabled("contacts_lookup", False)
    # No exception; normal return path.
    assert skill is None or skill.id == "contacts_lookup"


# ---------------------------------------------------------------------------
# Integration: boot detector + audit trigger + burst detector
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_init_skills_alerts_when_all_disabled(clean_db, monkeypatch):
    """A boot into the all-disabled state that something switched off must
    raise the alarm: push + a system heartbeat event that will surface in the
    next briefing. (A fresh install, where every skill starts off, is not
    the incident: tests/test_public_fresh_install_skills.py.)"""
    import services.push_service as push_service
    from services.database import async_session, HeartbeatEventModel
    from sqlalchemy import select

    push = AsyncMock(return_value={"sent": 1})
    monkeypatch.setattr(push_service, "send_push_notification", push)
    # Status sweep probes real integrations (osascript etc.) — not under test.
    monkeypatch.setattr(skills_service, "get_all_skills", AsyncMock(return_value=[]))

    await skills_service.init_skills()  # seeds every skill disabled
    async with async_session() as session:
        from services.database import SkillModel
        rows = (await session.execute(select(SkillModel))).scalars().all()
        rows[0].enabled = True
        await session.commit()
        rows[0].enabled = False  # the audit trigger records enabled -> disabled
        await session.commit()
    push.reset_mock()
    await skills_service.init_skills()

    assert push.await_count >= 1
    async with async_session() as session:
        result = await session.execute(
            select(HeartbeatEventModel).where(
                HeartbeatEventModel.event_type == "skills_mass_disable"
            )
        )
        events = result.scalars().all()
    assert len(events) == 1
    assert events[0].source == "system"
    assert events[0].triage_status == "noted"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skills_audit_trigger_records_raw_sql_disable(clean_db):
    """The Postgres trigger must catch writers that bypass the app entirely."""
    from services.database import engine, init_db
    from sqlalchemy import text

    await init_db()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO skills (id, enabled) VALUES ('contacts_lookup', true) "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
        )
        await conn.execute(text("DELETE FROM skills_audit"))
        await conn.execute(text("UPDATE skills SET enabled = false WHERE id = 'contacts_lookup'"))
        rows = (
            await conn.execute(
                text(
                    "SELECT skill_id, old_enabled, new_enabled FROM skills_audit"
                )
            )
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "contacts_lookup"
    assert rows[0][1] is True and rows[0][2] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mass_disable_burst_detector(clean_db, monkeypatch):
    """>=5 disables within 5 minutes triggers one (deduped) alert."""
    import services.push_service as push_service
    from services.database import engine, init_db
    from sqlalchemy import text

    push = AsyncMock(return_value={"sent": 1})
    monkeypatch.setattr(push_service, "send_push_notification", push)

    await init_db()
    async with engine.begin() as conn:
        for i in range(6):
            await conn.execute(
                text(
                    "INSERT INTO skills_audit (skill_id, old_enabled, new_enabled) "
                    f"VALUES ('skill_{i}', true, false)"
                )
            )

    fired_1 = await skills_service.check_mass_disable_burst()
    fired_2 = await skills_service.check_mass_disable_burst()  # deduped

    assert fired_1 is True
    assert fired_2 is False
    assert push.await_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mass_disable_burst_quiet_below_threshold(clean_db, monkeypatch):
    import services.push_service as push_service
    from services.database import engine, init_db
    from sqlalchemy import text

    push = AsyncMock(return_value={"sent": 1})
    monkeypatch.setattr(push_service, "send_push_notification", push)

    await init_db()
    async with engine.begin() as conn:
        for i in range(3):
            await conn.execute(
                text(
                    "INSERT INTO skills_audit (skill_id, old_enabled, new_enabled) "
                    f"VALUES ('skill_{i}', true, false)"
                )
            )

    assert await skills_service.check_mass_disable_burst() is False
    assert push.await_count == 0
