"""R/G TDD red for P1 — make the usage signal honest.

``retrieve_memories`` bumps ``access_count`` and ``last_accessed`` inside the
scoring loop, over every candidate the vector/keyword queries returned. That
happens BEFORE the min_score threshold, BEFORE the confidence floor, and
BEFORE the final ``[:limit]`` slice. The queries each fetch ``limit * 2``, so
a single retrieval can mark up to 4x more memories "accessed" than it ever
returns.

The consequence is not cosmetic. ``compute_temporal_boost`` reads
``last_accessed`` and ``access_count``; a memory that merely sits near common
queries in embedding space gets its recency refreshed on every turn and can
therefore never decay. Junk self-reinforces. Live evidence: knowledge-tier
facts average 217 accesses in a 1700-row store.

Two separate signals are needed, because they answer different questions:

  * ``access_count`` / ``last_accessed`` — "retrieval returned this to a
    caller." Drives the decay curve.
  * ``used_count`` / ``last_used_at`` — "this was actually put in front of the
    model on a turn." Drives the tier ladder in P3.

Contract:
  * Only memories in the RETURNED list get their access bumped.
  * MemoryModel gains ``used_count`` (int) and ``last_used_at`` (datetime).
  * ``mark_memories_used(ids)`` bumps those, and only those.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_memory_model_has_used_count_column():
    from services.database import MemoryModel

    assert hasattr(MemoryModel, "used_count"), (
        "MemoryModel needs a ``used_count`` column — the count of turns where "
        "this memory was actually placed in the model's context, as distinct "
        "from times it merely scored well enough to be returned."
    )


def test_memory_model_has_last_used_at_column():
    from services.database import MemoryModel

    assert hasattr(MemoryModel, "last_used_at"), (
        "MemoryModel needs ``last_used_at`` so decay can distinguish 'last "
        "surfaced' from 'last actually useful'."
    )


def test_mark_memories_used_exists():
    from services import memory_service

    assert hasattr(memory_service, "mark_memories_used"), (
        "memory_service must expose ``mark_memories_used(ids)`` for the "
        "respond path to call once it knows which memories it injected."
    )


# ---------------------------------------------------------------------------
# Behaviour — integration
# ---------------------------------------------------------------------------


async def _insert(session, **kwargs):
    from services import memory_service
    from services.database import MemoryModel

    content = kwargs["content"]
    mem = MemoryModel(
        id=kwargs.get("id") or str(uuid.uuid4()),
        content=content,
        embedding=memory_service.get_embedding(content),
        memory_type=kwargs.get("memory_type", "fact"),
        importance=kwargs.get("importance", 0.5),
        temporal_nature=kwargs.get("temporal_nature", "timeless"),
        source_type=kwargs.get("source_type", "user_stated"),
        confidence=kwargs.get("confidence", 0.9),
        access_count=kwargs.get("access_count", 0),
        reinforcement_count=kwargs.get("reinforcement_count", 0),
    )
    session.add(mem)
    return mem


async def test_access_is_bumped_only_for_returned_memories(clean_db):
    """Seed 8 memories on one topic, ask for 2. Exactly 2 may be bumped."""
    from sqlalchemy import text

    from services import database, memory_service

    async with database.async_session() as session:
        for i in range(8):
            await _insert(session, content=f"Ellis went sailing on lake number {i}")
        await session.commit()

    results = await memory_service.retrieve_memories(
        "sailing on the lake", limit=2, min_score=0.0
    )
    returned_ids = {m.id for m in results}
    assert len(returned_ids) <= 2

    async with database.async_session() as session:
        rows = (
            await session.execute(
                text("SELECT id, access_count FROM memories WHERE access_count > 0")
            )
        ).fetchall()

    bumped_ids = {r.id for r in rows}
    assert bumped_ids == returned_ids, (
        "access_count was bumped for memories that were never returned. "
        f"bumped={len(bumped_ids)} returned={len(returned_ids)}. Scoring a "
        "candidate is not using it — counting it as access is what lets "
        "irrelevant memories refresh their own recency and never decay."
    )


async def test_update_access_false_bumps_nothing(clean_db):
    from sqlalchemy import text

    from services import database, memory_service

    async with database.async_session() as session:
        for i in range(4):
            await _insert(session, content=f"Ellis went sailing on lake number {i}")
        await session.commit()

    await memory_service.retrieve_memories(
        "sailing on the lake", limit=2, min_score=0.0, update_access=False
    )

    async with database.async_session() as session:
        n = (
            await session.execute(
                text("SELECT count(*) FROM memories WHERE access_count > 0")
            )
        ).scalar()

    assert n == 0, "update_access=False must leave every counter untouched."


async def test_mark_memories_used_bumps_only_given_ids(clean_db):
    from sqlalchemy import text

    from services import database, memory_service

    ids = [str(uuid.uuid4()) for _ in range(3)]
    async with database.async_session() as session:
        for i, mid in enumerate(ids):
            await _insert(session, id=mid, content=f"Ellis owns bicycle number {i}")
        await session.commit()

    await memory_service.mark_memories_used([ids[0], ids[2]])

    async with database.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, used_count, last_used_at FROM memories "
                    "ORDER BY content"
                )
            )
        ).fetchall()

    by_id = {r.id: r for r in rows}
    assert by_id[ids[0]].used_count == 1
    assert by_id[ids[2]].used_count == 1
    assert by_id[ids[1]].used_count == 0, (
        "A memory that was not injected must not gain a use."
    )
    assert by_id[ids[0]].last_used_at is not None
    assert by_id[ids[1]].last_used_at is None


async def test_mark_memories_used_accumulates(clean_db):
    from sqlalchemy import text

    from services import database, memory_service

    mid = str(uuid.uuid4())
    async with database.async_session() as session:
        await _insert(session, id=mid, content="Ellis's dog is named Cricket")
        await session.commit()

    await memory_service.mark_memories_used([mid])
    await memory_service.mark_memories_used([mid])
    await memory_service.mark_memories_used([mid])

    async with database.async_session() as session:
        used = (
            await session.execute(
                text("SELECT used_count FROM memories WHERE id = :id"), {"id": mid}
            )
        ).scalar()

    assert used == 3, "Repeated use must accumulate — that is the P3 tier signal."


async def test_mark_memories_used_tolerates_empty_and_unknown_ids(clean_db):
    from services import memory_service

    await memory_service.mark_memories_used([])
    await memory_service.mark_memories_used([str(uuid.uuid4())])
