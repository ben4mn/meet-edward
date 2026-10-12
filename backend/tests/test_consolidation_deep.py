"""Consolidation with surplus overrides (muse-and-beyond plan §E5, 2026-09-24).

The surplus queue's ``consolidation_deep`` job runs one cycle with a
bigger budget — about 60 LLM calls, a 24 h lookback, up to 10 entity
narratives — passed as parameters: the stored config (15 calls, 2 h, the
hourly loop's) is never touched. Its calls go through the rate gate at
``surplus`` priority, labelled ``surplus:consolidation_deep``, and the
cycle reports what it produced.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tests.test_consolidation_cost_caps import _insert_memory, _ScriptedLLM, _set_config


@pytest.mark.integration
async def test_overrides_widen_the_cycle_without_touching_the_config(clean_db, monkeypatch):
    from services import consolidation_service as cs
    from services import database
    from services.database import ConsolidationConfigModel, MemoryModel

    async with database.async_session() as session:
        ids = [await _insert_memory(session, f"widget fact {i}") for i in range(6)]
        await session.commit()
    # Created 20 hours ago: outside the stored 2 h lookback, inside 24 h.
    async with database.async_session() as session:
        for m in (await session.execute(select(MemoryModel))).scalars().all():
            m.created_at = datetime.utcnow() - timedelta(hours=20)
            m.last_accessed = datetime.utcnow() - timedelta(hours=20)
        await session.commit()
    await _set_config(enabled=True, lookback_hours=2, max_llm_calls_per_cycle=1)

    cluster = ('{"clusters": [{"label": "widgets", "type": "same_topic", "memory_ids": ['
               + ", ".join(f'"{i}"' for i in ids) + ']}]}')
    stub = _ScriptedLLM([cluster, '{"contradictions": []}'])
    built = []

    def build(*a, **kw):
        built.append(kw)
        return stub

    monkeypatch.setattr("services.llm_utils.build_background_llm", build)
    monkeypatch.setattr("services.memory_service.build_background_llm", lambda *a, **k: stub)

    out = await cs.run_cycle(lookback_hours=24, max_llm_calls=60, max_narratives=10, priority="surplus",
                             purpose="surplus:consolidation_deep", effort="medium")
    assert out["memories_reviewed"] == 6
    assert out["llm_calls"] >= 2, "past the stored cap of one call"
    assert out["connections_created"] > 0
    assert built[0]["priority"] == "surplus" and built[0]["effort"] == "medium"
    assert getattr(stub, "purpose", None) == "surplus:consolidation_deep"

    async with database.async_session() as session:
        cfg = (await session.execute(select(ConsolidationConfigModel))).scalar_one()
    assert (cfg.lookback_hours, cfg.max_llm_calls_per_cycle) == (2, 1), "the stored config is untouched"


@pytest.mark.integration
async def test_the_stored_config_still_governs_the_hourly_cycle(clean_db, monkeypatch):
    from services import consolidation_service as cs
    from services import database
    from services.database import MemoryModel

    async with database.async_session() as session:
        for i in range(4):
            await _insert_memory(session, f"old widget fact {i}")
        await session.commit()
    async with database.async_session() as session:
        for m in (await session.execute(select(MemoryModel))).scalars().all():
            m.created_at = datetime.utcnow() - timedelta(hours=20)
            m.last_accessed = datetime.utcnow() - timedelta(hours=20)
        await session.commit()
    await _set_config(enabled=True, lookback_hours=2)
    stub = _ScriptedLLM([])
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)
    out = await cs._run_consolidation_cycle()
    assert out["skipped"] is True and stub.calls == [], "20-hour-old memories are outside the stored 2 h"


@pytest.mark.integration
async def test_a_deferred_surplus_cluster_call_aborts_the_cycle(clean_db, monkeypatch):
    from services import consolidation_service as cs
    from services import database
    from services.database import ConsolidationCycleModel
    from services.llm_rate_gate import LLMRateLimitDeferred

    async with database.async_session() as session:
        for i in range(4):
            await _insert_memory(session, f"widget fact {i}")
        await session.commit()

    class _Deferred:
        async def ainvoke(self, messages):
            raise LLMRateLimitDeferred("Codex rate gate: surplus LLM call deferred, no headroom")

    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: _Deferred())
    with pytest.raises(LLMRateLimitDeferred):
        await cs.run_cycle(lookback_hours=24, max_llm_calls=60, priority="surplus", purpose="surplus:x")
    async with database.async_session() as session:
        assert (await session.execute(select(ConsolidationCycleModel))).scalars().all() == [], \
            "a surplus cycle that never ran records no empty cycle"


@pytest.mark.integration
async def test_consolidation_enabled_reads_the_config(clean_db):
    from services import consolidation_service as cs

    await _set_config(enabled=False)
    assert await cs.consolidation_enabled() is False
    await _set_config(enabled=True)
    assert await cs.consolidation_enabled() is True
