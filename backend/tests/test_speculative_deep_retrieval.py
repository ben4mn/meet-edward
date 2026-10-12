"""
Speculative deep retrieval (Track B2).

Deep retrieval was fire-and-forget: the result only showed up as
enrichments for turn N+1. Two-turn conversations never saw it.

The speculative path kicks off deep retrieval at the start of the turn,
waits up to ``timeout`` seconds, and — if it finishes in time — merges
the result into the CURRENT turn's memory context. On timeout the
helper falls through to the original behavior: the background task
eventually completes and writes results as enrichments for the next turn.

This exercise targets a small testable helper rather than the full
streaming loop. The helper is the seam we'll call from
``backend/services/graph/streaming.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import List

import pytest
from sqlalchemy import select


# The target API lives at services.dream.speculative.speculative_deep_retrieve
# It returns (memories_for_current_turn, consumed: bool) where consumed=True
# means the memories were fetched in time and should be merged into the turn;
# consumed=False means the caller should not merge (a background fallback
# task was scheduled to write enrichments instead).


async def _fake_deep_retrieve_fast(*args, **kwargs) -> list:
    """Simulate deep retrieval returning quickly."""
    await asyncio.sleep(0.01)
    from services.memory_service import Memory

    return [
        Memory(
            id=str(uuid.uuid4()),
            content="fast retrieval result",
            memory_type="fact",
            importance=0.6,
            temporal_nature="timeless",
            score=0.4,
        )
    ]


async def _fake_deep_retrieve_slow(*args, **kwargs) -> list:
    """Simulate deep retrieval that takes longer than the speculative timeout."""
    await asyncio.sleep(0.3)  # 300ms, well above a 100ms test timeout
    from services.memory_service import Memory

    return [
        Memory(
            id=str(uuid.uuid4()),
            content="slow retrieval result",
            memory_type="fact",
            importance=0.6,
            temporal_nature="timeless",
            score=0.4,
        )
    ]


async def test_fast_path_merges_into_current_turn(monkeypatch):
    """When deep retrieval finishes within the timeout, the memories are
    returned for the current turn and ``consumed=True``.
    """
    from services.dream import speculative

    monkeypatch.setattr(
        "services.deep_retrieval_service.deep_retrieve_memories",
        _fake_deep_retrieve_fast,
    )

    memories, consumed = await speculative.speculative_deep_retrieve(
        message="tell me about my hiking",
        recent_msgs=[{"role": "user", "content": "tell me about my hiking"}],
        conversation_id="spec-fast",
        already_retrieved_ids=[],
        timeout=0.5,
    )

    assert consumed is True, (
        "Fast deep retrieval should be consumed into the current turn."
    )
    assert len(memories) == 1
    assert memories[0].content == "fast retrieval result"


async def test_slow_path_falls_through_to_fallback(monkeypatch):
    """When deep retrieval exceeds the timeout, the helper returns
    ``([], False)`` and the current turn proceeds without those memories.
    The fallback task continues in the background.
    """
    from services.dream import speculative

    monkeypatch.setattr(
        "services.deep_retrieval_service.deep_retrieve_memories",
        _fake_deep_retrieve_slow,
    )

    memories, consumed = await speculative.speculative_deep_retrieve(
        message="tell me about my hiking",
        recent_msgs=[{"role": "user", "content": "tell me about my hiking"}],
        conversation_id="spec-slow",
        already_retrieved_ids=[],
        timeout=0.1,
    )

    assert consumed is False, (
        "Slow deep retrieval should not block the current turn."
    )
    assert memories == [], "No memories should be returned when timed out."


@pytest.mark.integration
async def test_slow_path_writes_enrichments_as_fallback(clean_db, monkeypatch):
    """When the speculative window expires, the background task still runs
    to completion and writes its results to ``memory_enrichments`` for
    the next turn — preserving the original fire-and-forget behavior.
    """
    from services import database
    from services.dream import speculative
    from services.database import MemoryEnrichmentModel

    monkeypatch.setattr(
        "services.deep_retrieval_service.deep_retrieve_memories",
        _fake_deep_retrieve_slow,
    )

    conversation_id = "spec-slow-enrich"
    memories, consumed = await speculative.speculative_deep_retrieve(
        message="anything",
        recent_msgs=[{"role": "user", "content": "anything"}],
        conversation_id=conversation_id,
        already_retrieved_ids=[],
        timeout=0.05,
    )
    assert consumed is False

    # Give the background fallback task time to complete.
    await asyncio.sleep(0.6)

    async with database.async_session() as session:
        result = await session.execute(
            select(MemoryEnrichmentModel).where(
                MemoryEnrichmentModel.conversation_id == conversation_id
            )
        )
        rows = list(result.scalars().all())

    assert rows, (
        "The slow-path fallback must still write enrichments for the next "
        "turn — the feature shouldn't get worse when speculation misses."
    )
