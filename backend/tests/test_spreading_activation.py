"""
Spreading-activation retrieval tests.

The consolidation service creates ``memory_connections`` rows linking related
memories, but ``retrieve_memories`` only rescored memories that were already
in the retrieved set via vector / BM25. Connected memories that happened not
to hit either search were never pulled in — so the connections table was
almost-orphaned data from retrieval's perspective.

These tests drive a small extension: after the existing hybrid retrieval +
in-set boost, union in connected memories (capped at +5 per query) with a
score of ``0.1 * connection.strength``. That makes consolidation's work
visible to retrieval without any UI work.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


async def _insert_memory(session, content: str) -> str:
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
    )
    session.add(mem)
    return mem_id


async def _insert_connection(session, mem_a: str, mem_b: str, strength: float = 1.0) -> None:
    from services.database import MemoryConnectionModel

    id_a, id_b = (mem_a, mem_b) if mem_a < mem_b else (mem_b, mem_a)
    conn = MemoryConnectionModel(
        id=str(uuid.uuid4()),
        memory_id_a=id_a,
        memory_id_b=id_b,
        connection_type="same_topic",
        strength=strength,
    )
    session.add(conn)


@pytest.mark.integration
async def test_connected_memory_is_pulled_into_results(clean_db):
    """A memory that doesn't match the query via vector/BM25 but IS connected
    to a retrieved memory should surface in the result set.

    Without spreading-activation unioning, only the query-matching memory is
    returned. With it, both memories are returned (or at least the connected
    one is visible). This encodes the behavior described in the redesign
    plan's Track A2.
    """
    from services import database, memory_service

    async with database.async_session() as session:
        # M1 is totally unrelated to the query semantically — no vector/BM25
        # hit. Content is a fact about a user's favorite number.
        id_unrelated = await _insert_memory(session, "user's favorite number is 42")
        # M2 matches the query directly.
        id_match = await _insert_memory(session, "user visited Yosemite National Park last weekend")
        await _insert_connection(session, id_unrelated, id_match, strength=1.0)
        await session.commit()

    results = await memory_service.retrieve_memories(
        query="Yosemite",
        limit=5,
        update_access=False,
        # Keep min_confidence permissive so seeded 0.8-confidence memories are not filtered.
        min_confidence=0.0,
    )

    result_ids = {m.id for m in results}
    assert id_match in result_ids, (
        "The direct Yosemite memory must be retrieved by the primary hybrid search."
    )
    assert id_unrelated in result_ids, (
        "The connected-but-unrelated memory should be pulled into results via "
        "spreading activation. If missing: retrieve_memories is not unioning "
        "connected memories when they aren't already in the retrieved set."
    )


@pytest.mark.integration
async def test_no_connection_means_no_spreading_pull(clean_db):
    """Control: without a memory_connections row, the unrelated memory stays out."""
    from services import database, memory_service

    async with database.async_session() as session:
        id_unrelated = await _insert_memory(
            session,
            "the sum of the interior angles of a triangle is 180 degrees",
        )
        id_match = await _insert_memory(
            session, "user visited Yosemite National Park last weekend"
        )
        # No connection inserted.
        await session.commit()

    results = await memory_service.retrieve_memories(
        query="Yosemite",
        limit=5,
        update_access=False,
        min_confidence=0.0,
    )

    result_ids = {m.id for m in results}
    assert id_match in result_ids
    assert id_unrelated not in result_ids, (
        "Without a memory_connections row, the unrelated memory must NOT "
        "appear — otherwise the prior test is meaningless (it would pass "
        "even without spreading activation)."
    )


@pytest.mark.integration
async def test_spreading_activation_cap(clean_db):
    """Connected-memory union is capped at +5 per query to prevent context bloat."""
    from services import database, memory_service

    async with database.async_session() as session:
        id_match = await _insert_memory(
            session, "user visited Yosemite National Park last weekend"
        )
        connected_ids = []
        # Use distractor content semantically far from "Yosemite" so only the
        # spreading-activation path could surface these — not vector / BM25.
        for i in range(10):
            cid = await _insert_memory(
                session,
                f"the nth prime number where n equals {i} has an algebraic property",
            )
            await _insert_connection(session, cid, id_match, strength=1.0)
            connected_ids.append(cid)
        await session.commit()

    results = await memory_service.retrieve_memories(
        query="Yosemite",
        limit=20,
        update_access=False,
        min_confidence=0.0,
    )

    result_ids = {m.id for m in results}
    pulled_in = [cid for cid in connected_ids if cid in result_ids]
    assert len(pulled_in) <= 5, (
        f"Spreading activation should cap at 5 extra memories per query, "
        f"got {len(pulled_in)}. Prevents context bloat when a memory has "
        f"dozens of connections."
    )
    assert len(pulled_in) >= 1, (
        "At least some pulled-ins should appear — if zero, the union "
        "logic is missing entirely."
    )


@pytest.mark.integration
async def test_a_retired_connected_memory_is_not_pulled_in(clean_db):
    """Retiring a row (``valid_until``) leaves its connections in place; the
    union must not bring it back (2026-09-27 — the memory audit retired
    ~600 rows, many sensitive)."""
    from datetime import timedelta

    from sqlalchemy import update

    from services import database, memory_service
    from services.database import MemoryModel

    async with database.async_session() as session:
        id_retired = await _insert_memory(session, "user's favorite number is 42")
        id_match = await _insert_memory(session, "user visited Yosemite National Park last weekend")
        await _insert_connection(session, id_retired, id_match, strength=1.0)
        await session.commit()
        await session.execute(update(MemoryModel).where(MemoryModel.id == id_retired)
                              .values(valid_until=datetime.utcnow() - timedelta(days=1)))
        await session.commit()

    results = await memory_service.retrieve_memories(query="Yosemite", limit=5, update_access=False,
                                                     min_confidence=0.0)

    result_ids = {m.id for m in results}
    assert id_match in result_ids
    assert id_retired not in result_ids, "a retired row came back through its connection"


@pytest.mark.integration
async def test_retired_connections_do_not_crowd_out_live_ones(clean_db):
    """The union took the ``cap`` strongest candidates and only then dropped
    the retired ones, so strong edges to retired rows left no room for a
    live neighbour."""
    from datetime import timedelta

    from sqlalchemy import update

    from services import database, memory_service
    from services.database import MemoryModel

    async with database.async_session() as session:
        id_match = await _insert_memory(session, "user visited Yosemite National Park last weekend")
        retired_ids = []
        for i in range(5):
            rid = await _insert_memory(session, f"retired note number {i}")
            await _insert_connection(session, rid, id_match, strength=1.0)
            retired_ids.append(rid)
        id_live = await _insert_memory(session, "user's hiking boots are size 10")
        await _insert_connection(session, id_live, id_match, strength=0.5)
        await session.commit()
        await session.execute(update(MemoryModel).where(MemoryModel.id.in_(retired_ids))
                              .values(valid_until=datetime.utcnow() - timedelta(days=1)))
        await session.commit()

    pulled = await memory_service._spread_connected_memories({id_match}, min_confidence=0.0, cap=5)

    assert [m.id for m in pulled] == [id_live]


@pytest.mark.integration
async def test_a_row_below_the_confidence_floor_does_not_take_a_slot(clean_db):
    """The live-only change (2026-09-27) sliced the ``cap`` strongest live
    candidates and applied the confidence floor after, so a strong edge to
    a low-confidence row took the slot and yielded nothing — before it the
    walk went on past it (integration review, 2026-09-27)."""
    from sqlalchemy import update

    from services import database, memory_service
    from services.database import MemoryModel

    async with database.async_session() as session:
        id_match = await _insert_memory(session, "user visited Yosemite National Park last weekend")
        id_doubtful = await _insert_memory(session, "user may have lost a hiking pole")
        await _insert_connection(session, id_doubtful, id_match, strength=0.9)
        id_sure = await _insert_memory(session, "user's hiking boots are size 10")
        await _insert_connection(session, id_sure, id_match, strength=0.5)
        await session.commit()
        await session.execute(update(MemoryModel).where(MemoryModel.id == id_doubtful).values(confidence=0.2))
        await session.commit()

    pulled = await memory_service._spread_connected_memories({id_match}, min_confidence=0.5, cap=1)

    assert [m.id for m in pulled] == [id_sure]
