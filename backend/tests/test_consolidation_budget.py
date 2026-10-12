"""Consolidation budget follows the quota (2026-09-25).

Ellis: "give it ultimate freedom and tons of compute power". The weekly Codex
meter sat at 41 % with hours to the reset while the hourly consolidation
cycle stopped at its stored 15 LLM calls. While the week has spare quota —
the surplus decision allows spending with at least 15 % headroom — the
hourly cycle gets three times its stored budget, for that cycle only: the
stored config is never written, and a caller's explicit budget (the
surplus queue's ``consolidation_deep``) still wins.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.test_consolidation_cost_caps import _insert_memory, _ScriptedLLM, _set_config, _use_llm


def _allowed(headroom=30.0):
    from services.llm_rate_gate import SurplusDecision

    return SurplusDecision(True, "ok", used=41.0, hours_to_reset=30.0, reserve=5.0, headroom=headroom,
                           target_rate=headroom / 30.0, code="ok")


def _refused():
    from services.llm_rate_gate import SurplusDecision

    return SurplusDecision(False, "no headroom", used=88.0, headroom=-2.0, code="no_headroom")


class _Cfg:
    def __init__(self, calls):
        self.max_llm_calls_per_cycle = calls


@pytest.mark.unit
def test_budget_for_cycle_triples_with_spare_quota():
    from services.consolidation_service import budget_for_cycle

    assert budget_for_cycle(_Cfg(15), _allowed(30.0)) == 45
    assert budget_for_cycle(_Cfg(15), _allowed(15.0)) == 45, "15 % headroom is enough"
    assert budget_for_cycle(_Cfg(15), _allowed(14.9)) == 15
    assert budget_for_cycle(_Cfg(15), _refused()) == 15
    assert budget_for_cycle(_Cfg(15), None) == 15
    assert budget_for_cycle(_Cfg(4), _allowed()) == 12
    assert budget_for_cycle(_Cfg(None), _allowed()) == 45, "no stored cap reads as the default 15"
    assert budget_for_cycle(None, _refused()) == 15
    assert budget_for_cycle(None, _allowed()) == 45


@pytest.mark.unit
def test_budget_for_cycle_never_writes_the_config():
    from services.consolidation_service import budget_for_cycle

    cfg = _Cfg(15)
    budget_for_cycle(cfg, _allowed())
    assert cfg.max_llm_calls_per_cycle == 15


async def _two_clusters():
    from services import database

    async with database.async_session() as session:
        a1 = await _insert_memory(session, "alpha one")
        a2 = await _insert_memory(session, "alpha two")
        b1 = await _insert_memory(session, "bravo one")
        b2 = await _insert_memory(session, "bravo two")
        await _insert_memory(session, "noise filler memory")
        await session.commit()
    return (
        '{"clusters": ['
        f'{{"label": "alpha", "type": "same_topic", "memory_ids": ["{a1}", "{a2}"]}}, '
        f'{{"label": "bravo", "type": "same_topic", "memory_ids": ["{b1}", "{b2}"]}}'
        ']}'
    )


@pytest.mark.integration
async def test_the_hourly_cycle_spends_the_wider_budget_while_the_week_has_spare_quota(clean_db, monkeypatch):
    from services import consolidation_service as cs
    from services import database
    from services.database import ConsolidationConfigModel

    cluster = await _two_clusters()
    await _set_config(max_cluster_size=100, max_pairs_per_cluster=100, max_llm_calls_per_cycle=1)
    stub = _ScriptedLLM([cluster])
    _use_llm(monkeypatch, stub)
    monkeypatch.setattr(cs, "_surplus_decision_now", lambda: _allowed(30.0))

    out = await cs._run_consolidation_cycle()
    assert len(stub.calls) >= 2, "a stored cap of 1 becomes 3 for this cycle"
    assert len(stub.calls) <= 3
    assert out["llm_calls"] == len(stub.calls)
    async with database.async_session() as session:
        cfg = (await session.execute(select(ConsolidationConfigModel))).scalar_one()
    assert cfg.max_llm_calls_per_cycle == 1, "the stored config is untouched"


@pytest.mark.integration
async def test_without_spare_quota_the_stored_budget_holds(clean_db, monkeypatch):
    from services import consolidation_service as cs

    cluster = await _two_clusters()
    await _set_config(max_cluster_size=100, max_pairs_per_cluster=100, max_llm_calls_per_cycle=1)
    stub = _ScriptedLLM([cluster])
    _use_llm(monkeypatch, stub)
    monkeypatch.setattr(cs, "_surplus_decision_now", lambda: _refused())

    await cs._run_consolidation_cycle()
    assert len(stub.calls) == 1


@pytest.mark.integration
async def test_an_explicit_budget_still_wins(clean_db, monkeypatch):
    from services import consolidation_service as cs

    cluster = await _two_clusters()
    await _set_config(max_cluster_size=100, max_pairs_per_cluster=100, max_llm_calls_per_cycle=1)
    stub = _ScriptedLLM([cluster])
    _use_llm(monkeypatch, stub)
    monkeypatch.setattr(cs, "_surplus_decision_now", lambda: _allowed(30.0))

    await cs.run_cycle(max_llm_calls=1)
    assert len(stub.calls) == 1, "the caller named the budget"
