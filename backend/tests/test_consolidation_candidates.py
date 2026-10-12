"""Consolidation cycles skip low-importance and retired rows.

8 of the 24 hourly cycles on 2026-09-05 exhausted their 15-call LLM budget,
and the week's 1,006 calls produced 24 merges. Clustering "Rowan says
shoes cost $175" against "Target has $25 pillows" is where that budget went.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import update


async def _seed(content: str, importance: float, created_at=None, valid_until=None) -> str:
    from services.memory_service import Memory, store_memory
    from services.database import async_session, MemoryModel

    m = await store_memory(Memory(id=None, content=content, memory_type="context", importance=importance,
                                  source_type="user_stated", confidence=0.9, valid_until=valid_until))
    if created_at is not None:
        async with async_session() as session:
            await session.execute(update(MemoryModel).where(MemoryModel.id == m.id).values(created_at=created_at))
            await session.commit()
    return m.id


def test_min_importance_constant():
    from services.consolidation_service import CONSOLIDATION_MIN_IMPORTANCE
    assert CONSOLIDATION_MIN_IMPORTANCE == 0.5


async def test_candidates_skip_low_importance_retired_and_old(clean_db):
    from services.consolidation_service import load_candidate_memories

    keep = await _seed("Ellis is flying to Dreamforce on Tuesday", 0.7)
    low = await _seed("Rowan says shoes cost $175", 0.3)
    retired = await _seed("Old pet-board snapshot", 0.9, valid_until=datetime.utcnow() - timedelta(hours=1))
    old = await _seed("Ellis ran a marathon in 2024", 0.9, created_at=datetime.utcnow() - timedelta(days=30))

    rows = await load_candidate_memories(lookback_hours=2, hot_ids=[])
    ids = {r.id for r in rows}
    assert keep in ids
    assert low not in ids
    assert retired not in ids
    assert old not in ids


async def test_hot_ids_bypass_recency_but_not_the_floor(clean_db):
    from services.consolidation_service import load_candidate_memories

    hot_old = await _seed("Ellis's parents live in Ohio", 0.8, created_at=datetime.utcnow() - timedelta(days=30))
    hot_low = await _seed("Frosty's ice cream is 20 minutes out", 0.2, created_at=datetime.utcnow() - timedelta(days=30))

    rows = await load_candidate_memories(lookback_hours=2, hot_ids=[hot_old, hot_low])
    ids = {r.id for r in rows}
    assert hot_old in ids
    assert hot_low not in ids
