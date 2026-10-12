"""Persist the rate gate's meter across restarts.

The meter is what every Codex response reports about the account's usage
window. Restarting blind means the first background calls after a deploy
run against no meter at all; carrying the last sample over (aged on read)
keeps the pace honest through launchd bounces and evolution deploys.

Saving is best-effort by design: if the row cannot be written the worst
case is a cold start, so nothing here may raise into a caller's path.
"""

from __future__ import annotations

import pytest

WEEK = 7 * 24 * 3600


def _headers(used):
    return {
        "x-codex-plan-type": "pro",
        "x-codex-primary-used-percent": str(used),
        "x-codex-primary-window-minutes": "10080",
        "x-codex-primary-reset-after-seconds": str(WEEK // 2),
    }


def test_store_surface():
    from services import llm_rate_gate_store as s

    for name in ("load_gate_state", "save_gate_state", "restore_gate_from_db", "persist_gate"):
        assert hasattr(s, name), f"llm_rate_gate_store must expose {name}"


async def test_round_trips_through_the_database(clean_db):
    from services.llm_rate_gate import LLMRateGate
    from services.llm_rate_gate_store import load_gate_state, save_gate_state

    assert await load_gate_state() is None, "nothing stored yet"

    gate = LLMRateGate()
    gate.note_usage_headers(_headers(22))
    await save_gate_state(gate.export_state())
    loaded = await load_gate_state()

    assert loaded["meter"]["windows"]["primary"]["used_percent"] == 22
    assert loaded["meter"]["plan_type"] == "pro"


async def test_save_overwrites_rather_than_appending(clean_db):
    from sqlalchemy import text

    from services.database import async_session
    from services.llm_rate_gate import LLMRateGate
    from services.llm_rate_gate_store import load_gate_state, save_gate_state

    for used in (10, 20):
        gate = LLMRateGate()
        gate.note_usage_headers(_headers(used))
        await save_gate_state(gate.export_state())

    async with async_session() as session:
        n = (await session.execute(text("SELECT count(*) FROM llm_rate_gate_state"))).scalar()

    assert n == 1, "the gate has one state row, not a history"
    assert (await load_gate_state())["meter"]["windows"]["primary"]["used_percent"] == 20


async def test_restore_applies_the_stored_meter_to_the_gate(clean_db):
    from services.llm_rate_gate import LLMRateGate
    from services.llm_rate_gate_store import restore_gate_from_db, save_gate_state

    src = LLMRateGate()
    src.note_usage_headers(_headers(64))
    await save_gate_state(src.export_state())

    gate = LLMRateGate()
    await restore_gate_from_db(gate)

    assert gate.meter == src.meter


async def test_restore_with_no_row_leaves_the_gate_alone(clean_db):
    from services.llm_rate_gate import LLMRateGate
    from services.llm_rate_gate_store import restore_gate_from_db

    gate = LLMRateGate()
    await restore_gate_from_db(gate)
    assert gate.meter is None


async def test_a_legacy_row_without_a_meter_is_harmless(clean_db):
    """Rows written by the budget pacer (budget + last_grow_wall only)."""
    from sqlalchemy import text

    from services.database import async_session
    from services.llm_rate_gate import LLMRateGate
    from services.llm_rate_gate_store import restore_gate_from_db

    async with async_session() as session:
        await session.execute(text(
            "INSERT INTO llm_rate_gate_state (id, budget, last_grow_wall) VALUES ('default', 46.0, 1.0)"
        ))
        await session.commit()

    gate = LLMRateGate()
    await restore_gate_from_db(gate)
    assert gate.meter is None


async def test_save_never_raises(monkeypatch):
    from services import llm_rate_gate_store as store

    class Boom:
        def __call__(self, *a, **kw):
            raise RuntimeError("db is down")

    monkeypatch.setattr(store, "async_session", Boom())
    await store.save_gate_state({"meter": None})


async def test_load_never_raises(monkeypatch):
    from services import llm_rate_gate_store as store

    class Boom:
        def __call__(self, *a, **kw):
            raise RuntimeError("db is down")

    monkeypatch.setattr(store, "async_session", Boom())
    assert await store.load_gate_state() is None
