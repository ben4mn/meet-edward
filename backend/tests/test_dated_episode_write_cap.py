"""A dated episode is never written at knowledge — whichever path asks
(review of batch A1, 2026-09-27).

``_compute_tier(content=…)`` stops a dated episode at belief, but no
production path both passed ``content`` and could reach knowledge:
reinforcement passes content and no ``used_count``; consolidation's step
10 — the only place use promotes a row to knowledge — passes
``used_count`` and no content. Reproduced on the batch branch: "On July
10-12, 2026, Rowan and Ellis hiked…" at belief with 5 restatements and 10 uses
was knowledge after one consolidation cycle, exactly what memory audit
§7.4 forbids ("do not let use alone promote a dated episode").

Every tier write goes through ``update_memory``, so the cap lives there
too: asked to write knowledge on a row whose content names a past date, it
writes belief. A row that is already knowledge is left as it is — retiring
the six dated rows that already are is the audit's call, not a side effect
of a restatement.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.test_consolidation_cost_caps import _insert_memory, _ScriptedLLM, _set_config, _use_llm

DATED = "On July 10-12, 2026, Rowan and Ellis hiked the Ridge Trail and camped by the lake"
UNDATED = "Ellis plays the cello on weekends"


async def _set(mid, **fields):
    from services import database
    from services.database import MemoryModel

    async with database.async_session() as session:
        row = (await session.execute(select(MemoryModel).where(MemoryModel.id == mid))).scalar_one()
        for k, v in fields.items():
            setattr(row, k, v)
        await session.commit()


async def _tier(mid):
    from services import database
    from services.database import MemoryModel

    async with database.async_session() as session:
        return (await session.execute(select(MemoryModel.tier).where(MemoryModel.id == mid))).scalar_one()


@pytest.mark.integration
async def test_a_consolidation_cycle_does_not_promote_a_dated_episode_to_knowledge(clean_db, monkeypatch):
    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    async with database.async_session() as session:
        dated = await _insert_memory(session, DATED)
        undated = await _insert_memory(session, UNDATED)
        await _insert_memory(session, "memory filler about garden gadgets")
        await session.commit()
    for mid in (dated, undated):
        await _set(mid, reinforcement_count=6, used_count=12, tier="belief")

    await _set_config(max_llm_calls_per_cycle=5)
    _use_llm(monkeypatch, _ScriptedLLM(['{"clusters": []}']))
    monkeypatch.setattr("services.consolidation_service._surplus_decision_now", lambda: None, raising=False)
    await _run_consolidation_cycle()

    assert await _tier(dated) == "belief"
    assert await _tier(undated) == "knowledge", "the ladder still works for what is not an episode"


@pytest.mark.integration
async def test_the_cycle_does_not_count_a_promotion_update_memory_turns_down(clean_db, monkeypatch, caplog):
    """Step 10 now passes the row's content to ``_compute_tier`` (2026-09-27):
    before, it asked for knowledge on the dated episode every hour, counted
    and logged a promotion, and ``update_memory`` quietly wrote belief."""
    import logging

    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    async with database.async_session() as session:
        dated = await _insert_memory(session, DATED)
        undated = await _insert_memory(session, UNDATED)
        await _insert_memory(session, "memory filler about garden gadgets")
        await session.commit()
    for mid in (dated, undated):
        await _set(mid, reinforcement_count=6, used_count=12, tier="belief")

    await _set_config(max_llm_calls_per_cycle=5)
    _use_llm(monkeypatch, _ScriptedLLM(['{"clusters": []}']))
    monkeypatch.setattr("services.consolidation_service._surplus_decision_now", lambda: None, raising=False)
    with caplog.at_level(logging.INFO, logger="services.consolidation_service"):
        result = await _run_consolidation_cycle()

    promoted = [
        getattr(r, "memory_id", None) for r in caplog.records
        if r.getMessage() == "consolidation promoted memory tier"
    ]
    assert promoted == [undated]
    assert result["promotions"] == 1


@pytest.mark.integration
async def test_update_memory_writes_belief_when_asked_for_knowledge_on_an_episode(clean_db):
    from services import database
    from services.memory_service import update_memory

    async with database.async_session() as session:
        dated = await _insert_memory(session, DATED)
        undated = await _insert_memory(session, UNDATED)
        reworded = await _insert_memory(session, "Rowan and Ellis hiked the Ridge Trail")
        await session.commit()

    assert (await update_memory(dated, tier="knowledge")).tier == "belief"
    assert await _tier(dated) == "belief"
    assert (await update_memory(undated, tier="knowledge")).tier == "knowledge"
    # The content written in the same call is what is judged.
    await update_memory(reworded, content=DATED, tier="knowledge")
    assert await _tier(reworded) == "belief"


@pytest.mark.integration
async def test_a_row_already_at_knowledge_is_not_demoted_by_a_write(clean_db):
    from services import database
    from services.memory_service import update_memory

    async with database.async_session() as session:
        mid = await _insert_memory(session, DATED)
        await session.commit()
    await _set(mid, tier="knowledge")
    await update_memory(mid, tier="knowledge", reinforcement_count=7)
    assert await _tier(mid) == "knowledge"
