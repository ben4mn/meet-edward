"""
Hot-memory feedback loop (Track B3).

Consolidation only reviews memories touched (created/accessed) within
``lookback_hours``. Memories that were actively used in a conversation
but have stale timestamps miss the window and never get clustered.

The hot-memory set is a small in-process TTL collection populated by
retrieval: every memory surfaced in a turn gets marked hot. The next
consolidation cycle unions the hot set into its lookback query, so
actively-used memories are always under consideration.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select


async def _insert_memory(session, content: str, created_at=None) -> str:
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
    )
    if created_at is not None:
        mem.created_at = created_at
    session.add(mem)
    return mem_id


def test_mark_and_iter_hot():
    """Basic set semantics: mark ids hot, iter returns them."""
    from services.dream import hot_memories as hot

    hot.clear()
    hot.mark_hot(["a", "b", "c"])
    active = set(hot.iter_active())
    assert active == {"a", "b", "c"}


def test_hot_memories_expire_after_ttl():
    """Entries older than TTL must not appear in iter_active."""
    from services.dream import hot_memories as hot
    import time

    hot.clear()
    # Mark with a tiny TTL override for testability.
    hot.mark_hot(["x"], ttl_seconds=0.05)
    time.sleep(0.1)
    active = set(hot.iter_active())
    assert "x" not in active, (
        f"Expected 'x' to expire after 50ms, but iter_active returned {active}"
    )


@pytest.mark.integration
async def test_consolidation_reviews_hot_memory_beyond_lookback(clean_db, monkeypatch):
    """A memory whose created_at is older than the lookback window must
    still appear in the consolidation review when it's marked hot.
    """
    from services import database
    from services.dream import hot_memories as hot
    from services.consolidation_service import _run_consolidation_cycle
    from services.database import ConsolidationConfigModel

    # Set lookback to 1 hour so a 5-hour-old memory is OUT by timestamp.
    async with database.async_session() as session:
        cfg = ConsolidationConfigModel(id="default", lookback_hours=1)
        session.add(cfg)
        await session.commit()

    async with database.async_session() as session:
        # Two old memories, one hot + one not. Plus two recent fillers so
        # the reviewed set (hot + recent) passes the >=3 skip gate.
        old_time = datetime.utcnow() - timedelta(hours=5)
        id_hot = await _insert_memory(session, "hot topic one", created_at=old_time)
        id_cold = await _insert_memory(session, "cold unrelated memory", created_at=old_time)
        id_recent1 = await _insert_memory(session, "recent filler memory one")
        id_recent2 = await _insert_memory(session, "recent filler memory two")
        # Set last_accessed to now UTC so the recency OR-branch matches
        # despite the DB server running in CDT (a pre-existing tz bug).
        from services.database import MemoryModel
        from sqlalchemy import update

        now_utc = datetime.utcnow()
        await session.execute(
            update(MemoryModel)
            .where(MemoryModel.id.in_([id_recent1, id_recent2]))
            .values(last_accessed=now_utc)
        )
        await session.commit()

    hot.clear()
    hot.mark_hot([id_hot])

    # Record the memory IDs seen during the cluster prompt — we assert that
    # ``id_hot`` is in the prompt (i.e., the cycle actually reviewed it)
    # but ``id_cold`` is not.
    captured_prompt = {}

    class _PromptCapturingLLM:
        async def ainvoke(self, messages):
            # cluster is the first call
            prompt_text = messages[-1].content
            captured_prompt["cluster"] = prompt_text
            class _R:
                content = '{"clusters": []}'
            return _R()

    monkeypatch.setattr(
        "services.llm_utils.build_background_llm",
        lambda *a, **k: _PromptCapturingLLM(),
    )

    await _run_consolidation_cycle()

    assert "cluster" in captured_prompt, "cycle should have made a cluster call"
    assert id_hot in captured_prompt["cluster"], (
        "A hot-marked memory outside the lookback window must still be included "
        "in the consolidation review."
    )
    assert id_cold not in captured_prompt["cluster"], (
        "A cold memory outside the lookback window should NOT be included "
        "(control — otherwise the hot-memory mechanism isn't why the hot "
        "memory appeared)."
    )
