"""
Consolidation cost-cap tests.

``_run_consolidation_cycle`` currently has unbounded O(clusters * n^2) pair
work: a cluster with 100 memories would create ~5000 ``memory_connections``
rows and one LLM contradiction call per cluster with no cap on how many
clusters. In the worst case this is a real runaway.

Add three caps on ``ConsolidationConfigModel``:
  - ``max_cluster_size`` — truncate a cluster to N memories before pair work.
  - ``max_pairs_per_cluster`` — hard ceiling on connections created per cluster.
  - ``max_llm_calls_per_cycle`` — early-exit the outer loop once the budget
    is spent (cluster call counts too).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


def _use_llm(monkeypatch, stub) -> None:
    """Stub the background LLM everywhere a cycle can reach it.

    ``consolidation_service`` imports ``build_background_llm`` from
    ``llm_utils`` at call time, but ``memory_service`` (the merge step,
    ``_llm_merge_content``) imported it by name at module load: patching
    only ``services.llm_utils`` left every merge calling real Codex — on
    the mini that spends quota, in a sandbox it retried every pair until
    the test timed out (2026-09-24).
    """
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)
    monkeypatch.setattr("services.memory_service.build_background_llm", lambda *a, **k: stub)


class _ScriptedLLM:
    """Minimal LLM stub that returns scripted responses for each ainvoke."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        assert isinstance(messages, list), "must pass a list of messages"
        self.calls.append(messages)
        if not self._responses:
            # Fall through to an empty contradictions response; cycles that
            # hit budget caps won't call us again.
            content = '{"contradictions": []}'
        else:
            content = self._responses.pop(0)

        class _R:
            def __init__(self, c):
                self.content = c

        return _R(content)


async def _insert_memory(session, content: str, origin: str = "heard") -> str:
    """``origin="read"`` for a row that may be merged: the model's default
    heard/owner is Ellis's row, which consolidation never merges (2026-09-27)."""
    from services import memory_service
    from services.database import MemoryModel

    mem_id = str(uuid.uuid4())
    mem = MemoryModel(
        id=mem_id,
        content=content,
        embedding=memory_service.get_embedding(content),
        memory_type="fact",
        importance=0.6,
        temporal_nature="timeless",
        source_type="user_stated",
        confidence=0.8,
        last_accessed=datetime.utcnow(),
        origin=origin,
    )
    session.add(mem)
    return mem_id


async def _set_config(**kwargs) -> None:
    """Upsert the single ConsolidationConfigModel row with given fields."""
    from services import database
    from services.database import ConsolidationConfigModel

    async with database.async_session() as session:
        existing = await session.execute(
            select(ConsolidationConfigModel).where(ConsolidationConfigModel.id == "default")
        )
        cfg = existing.scalar_one_or_none()
        if cfg is None:
            cfg = ConsolidationConfigModel(id="default")
            session.add(cfg)
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        await session.commit()


@pytest.mark.integration
async def test_max_cluster_size_truncates_cluster(clean_db, monkeypatch):
    """A cluster with 10 memories under max_cluster_size=5 must produce at
    most C(5, 2) = 10 ``memory_connections`` rows — not C(10, 2) = 45.
    """
    from services import database
    from services.consolidation_service import _run_consolidation_cycle
    from services.database import MemoryConnectionModel

    ids: list[str] = []
    async with database.async_session() as session:
        for i in range(10):
            ids.append(await _insert_memory(session, f"memory item {i} about widgets"))
        await session.commit()

    await _set_config(max_cluster_size=5, max_pairs_per_cluster=100, max_llm_calls_per_cycle=20)

    cluster_ids_json = ", ".join(f'"{mid}"' for mid in ids)
    cluster_response = (
        '{"clusters": [{"label": "widgets", "type": "same_topic", '
        f'"memory_ids": [{cluster_ids_json}]}}]}}'
    )
    contradiction_response = '{"contradictions": []}'
    stub = _ScriptedLLM([cluster_response, contradiction_response])
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    async with database.async_session() as session:
        conn_result = await session.execute(select(MemoryConnectionModel))
        connections = list(conn_result.scalars().all())

    # C(5, 2) = 10 pairs max when the cluster is truncated to 5.
    assert len(connections) <= 10, (
        f"max_cluster_size=5 should cap pair work at C(5,2)=10, "
        f"got {len(connections)} memory_connections rows."
    )


@pytest.mark.integration
async def test_max_pairs_per_cluster_caps_connections(clean_db, monkeypatch):
    """Even without cluster truncation, the per-cluster connection count
    must not exceed ``max_pairs_per_cluster``.
    """
    from services import database
    from services.consolidation_service import _run_consolidation_cycle
    from services.database import MemoryConnectionModel

    ids: list[str] = []
    async with database.async_session() as session:
        for i in range(6):
            ids.append(await _insert_memory(session, f"memory item {i} about widgets"))
        await session.commit()

    await _set_config(max_cluster_size=100, max_pairs_per_cluster=3, max_llm_calls_per_cycle=20)

    cluster_ids_json = ", ".join(f'"{mid}"' for mid in ids)
    cluster_response = (
        '{"clusters": [{"label": "widgets", "type": "same_topic", '
        f'"memory_ids": [{cluster_ids_json}]}}]}}'
    )
    contradiction_response = '{"contradictions": []}'
    stub = _ScriptedLLM([cluster_response, contradiction_response])
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    async with database.async_session() as session:
        conn_result = await session.execute(select(MemoryConnectionModel))
        connections = list(conn_result.scalars().all())

    assert len(connections) <= 3, (
        f"max_pairs_per_cluster=3 should cap connections per cluster at 3, "
        f"got {len(connections)}."
    )


@pytest.mark.integration
async def test_max_llm_calls_per_cycle_early_exits(clean_db, monkeypatch):
    """With max_llm_calls_per_cycle=1 and 2 clusters returned, only the
    cluster call should fire — no contradiction calls.
    """
    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    async with database.async_session() as session:
        a1 = await _insert_memory(session, "alpha one")
        a2 = await _insert_memory(session, "alpha two")
        b1 = await _insert_memory(session, "bravo one")
        b2 = await _insert_memory(session, "bravo two")
        _noise = await _insert_memory(session, "noise filler memory")
        await session.commit()

    await _set_config(max_cluster_size=100, max_pairs_per_cluster=100, max_llm_calls_per_cycle=1)

    cluster_response = (
        '{"clusters": ['
        f'{{"label": "alpha", "type": "same_topic", "memory_ids": ["{a1}", "{a2}"]}}, '
        f'{{"label": "bravo", "type": "same_topic", "memory_ids": ["{b1}", "{b2}"]}}'
        ']}'
    )
    stub = _ScriptedLLM([cluster_response])  # no contradiction responses scripted
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    # Only the cluster call should have been made.
    assert len(stub.calls) == 1, (
        f"max_llm_calls_per_cycle=1 should let only the cluster call through, "
        f"got {len(stub.calls)} calls."
    )


class _MergesFail:
    """Clusters, finds no contradictions, and fails every merge — the shape
    of a background LLM that is down (a 500, an expired token)."""

    def __init__(self, cluster_response):
        self._cluster = cluster_response
        self.calls = []
        self.merges = 0

    async def ainvoke(self, messages):
        self.calls.append(messages)
        text = " ".join(str(getattr(m, "content", m)) for m in messages)
        if "Merge these two related memories" in text:
            self.merges += 1
            raise RuntimeError("codex 500")

        class _R:
            def __init__(self, c):
                self.content = c

        if len(self.calls) == 1:
            return _R(self._cluster)
        return _R('{"contradictions": []}')


@pytest.mark.integration
async def test_failed_merges_count_against_the_cycle_budget(clean_db, monkeypatch):
    """A failed merge is a spent call (2026-09-24): before, only a merge
    that succeeded counted, so a failing LLM was retried for every pair on
    every cycle — and on the mini each retry was a real Codex call."""
    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    ids: list[str] = []
    async with database.async_session() as session:
        for i in range(5):
            ids.append(await _insert_memory(session, f"Ellis takes his coffee black with oat milk {i}", origin="read"))
        await session.commit()

    await _set_config(max_cluster_size=100, max_pairs_per_cluster=100, max_llm_calls_per_cycle=3)
    cluster_ids_json = ", ".join(f'"{mid}"' for mid in ids)
    stub = _MergesFail(
        '{"clusters": [{"label": "coffee", "type": "same_topic", '
        f'"memory_ids": [{cluster_ids_json}]}}]}}'
    )
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    # cluster (1) + contradictions (2) leave one call for merging.
    assert stub.merges == 1, f"a failing merge must spend the budget, got {stub.merges} merge attempts"
    assert len(stub.calls) <= 3
