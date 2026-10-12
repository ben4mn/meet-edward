"""A fresh install starts with every skill off. The boot-time guard against
a mass skill-disable (the 2026-07 incident) must not mistake that for the
incident: it would put a false alarm on a new owner's Today."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from services import skills_service
from services.database import HeartbeatEventModel, SkillModel, async_session


async def _alarm_rows() -> int:
    async with async_session() as s:
        rows = (await s.execute(select(HeartbeatEventModel).where(
            HeartbeatEventModel.event_type == "skills_mass_disable"))).scalars().all()
    return len(rows)


async def _all_skills_off() -> None:
    async with async_session() as s:
        for row in (await s.execute(select(SkillModel))).scalars().all():
            row.enabled = False
        await s.commit()


@pytest.mark.asyncio
async def test_a_fresh_install_with_every_skill_off_raises_no_alarm(clean_db, monkeypatch):
    monkeypatch.setattr(skills_service, "_push_skills_alert", _no_push)
    # The status sweep probes real integrations (osascript etc.): not under test.
    monkeypatch.setattr(skills_service, "get_all_skills", AsyncMock(return_value=[]))
    await skills_service.init_skills()
    await _all_skills_off()
    async with async_session() as s:
        await s.execute(text("DELETE FROM skills_audit"))
        await s.execute(text("DELETE FROM heartbeat_events WHERE event_type = 'skills_mass_disable'"))
        await s.commit()
    await skills_service._check_mass_disable_on_boot()
    assert await _alarm_rows() == 0


@pytest.mark.asyncio
async def test_skills_someone_switched_off_still_raise_the_alarm(clean_db, monkeypatch):
    monkeypatch.setattr(skills_service, "_push_skills_alert", _no_push)
    # The status sweep probes real integrations (osascript etc.): not under test.
    monkeypatch.setattr(skills_service, "get_all_skills", AsyncMock(return_value=[]))
    await skills_service.init_skills()
    async with async_session() as s:
        await s.execute(text("DELETE FROM heartbeat_events WHERE event_type = 'skills_mass_disable'"))
        row = (await s.execute(select(SkillModel))).scalars().first()
        row.enabled = True
        await s.commit()
    await _all_skills_off()  # the audit trigger records enabled -> disabled
    await skills_service._check_mass_disable_on_boot()
    assert await _alarm_rows() == 1


async def _no_push(title: str, body: str) -> None:
    return None
