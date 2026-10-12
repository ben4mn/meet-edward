"""R/G TDD red for C Phase 1 — consolidation must retire superseded
memories by setting ``valid_until``, not just lower confidence.

When the background LLM detects a contradiction between two memories about
the same subject, consolidation picks a winner (by source-type hierarchy,
then reinforcement count) and downgrades the loser's confidence. That's
good, but the loser still leaks into retrieval as low-confidence context.
Setting ``valid_until = NOW()`` retires the loser cleanly — the temporal-
edge filter then removes it from retrieval entirely.

Contract:
  * A new module-level ``resolve_contradictions()`` coroutine exists in
    ``services.consolidation_service``.
  * When run with unresolved contradiction flags, it picks a winner per
    flag, sets the loser's ``confidence`` to 0.2 AND sets the loser's
    ``valid_until`` to "now" (UTC-naive).
  * Resolved flags get ``resolved=True``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_resolve_contradictions_is_exposed():
    from services import consolidation_service

    assert hasattr(consolidation_service, "resolve_contradictions"), (
        "consolidation_service must expose a standalone "
        "``resolve_contradictions`` coroutine that the cycle calls and tests "
        "can drive directly."
    )


@pytest.mark.integration
async def test_resolve_contradictions_sets_valid_until_on_loser(clean_db):
    from services.memory_service import Memory, store_memory, get_memory_by_id
    from services.database import (
        async_session,
        MemoryFlagModel,
    )
    from services.consolidation_service import resolve_contradictions
    import uuid

    winner = await store_memory(Memory(
        id=None,
        content="Ellis quit coffee in April 2026",
        memory_type="fact",
        importance=0.7,
        source_type="user_stated",  # highest rank
        confidence=0.95,
    ))
    loser = await store_memory(Memory(
        id=None,
        content="Ellis drinks coffee every morning",
        memory_type="preference",
        importance=0.6,
        source_type="inferred",  # lower rank
        confidence=0.85,
        reinforcement_count=1,
    ))

    # Insert an unresolved contradiction flag pointing at both.
    async with async_session() as session:
        flag = MemoryFlagModel(
            id=str(uuid.uuid4()),
            memory_id=loser.id,
            related_memory_id=winner.id,
            flag_type="contradiction",
            description="Contradictory coffee claims",
            resolved=False,
        )
        session.add(flag)
        await session.commit()
        flag_id = flag.id

    # Run the extracted resolver.
    await resolve_contradictions()

    # The loser should now have confidence = 0.2 AND a valid_until in the
    # past (or <= now). The winner should be untouched.
    refreshed_loser = await get_memory_by_id(loser.id)
    refreshed_winner = await get_memory_by_id(winner.id)

    assert refreshed_loser is not None and refreshed_winner is not None
    assert refreshed_loser.confidence == pytest.approx(0.2, abs=1e-6), (
        f"loser confidence should be 0.2, got {refreshed_loser.confidence}"
    )

    # Fetch valid_until directly off the model (Memory dataclass may not
    # expose it yet — read the SQLAlchemy row).
    from sqlalchemy import select
    from services.database import MemoryModel

    async with async_session() as session:
        row = await session.execute(
            select(MemoryModel.valid_until, MemoryModel.id).where(
                MemoryModel.id.in_([loser.id, winner.id])
            )
        )
        by_id = {r.id: r.valid_until for r in row.fetchall()}

    loser_valid_until = by_id[loser.id]
    winner_valid_until = by_id[winner.id]

    assert loser_valid_until is not None, (
        "loser's valid_until must be set to retire it from retrieval"
    )
    # valid_until should be roughly "now" — within 1 minute of now.
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = abs((now_utc_naive - loser_valid_until).total_seconds())
    assert delta < 60, (
        f"loser's valid_until should be ~now (UTC naive), "
        f"got {loser_valid_until} (delta={delta}s from now)"
    )

    assert winner_valid_until is None, (
        "winner must NOT be retired — its valid_until must remain NULL"
    )

    # Flag should be marked resolved.
    async with async_session() as session:
        result = await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )
        refreshed_flag = result.scalar_one_or_none()
        assert refreshed_flag is not None
        assert refreshed_flag.resolved is True
