"""R/G TDD red for C Phase 1 (entity-keyed lookup).

The entity-retrieval scaffolding already exists:
  * ``services.entity_service.detect_entities_in_text`` — substring match
    on entity names + aliases.
  * ``services.memory_service.retrieve_entity_memories`` — loads memories
    linked to detected entities via ``entity_mentions``.
  * ``services.graph.streaming`` already calls the above as part of the
    parallel pre-turn retrieval bundle.

This test pins the behavior: given a memory linked to an entity via
``entity_mentions``, a query mentioning the entity's name returns that
memory with a score boost.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.integration
async def test_entity_keyed_retrieval_returns_linked_memory(clean_db):
    """Given an entity "Rowan" linked to a memory, query "what about
    Rowan?" returns the linked memory even if the memory content itself
    doesn't score well via embedding."""
    from services.database import (
        async_session,
        EntityModel,
        EntityMentionModel,
    )
    from services.memory_service import (
        Memory,
        store_memory,
        retrieve_entity_memories,
    )

    mem = await store_memory(Memory(
        id=None,
        content=(
            "The partner I share a dog with prefers voice calls over texts"
        ),
        memory_type="preference",
        importance=0.7,
        confidence=0.9,
    ))

    entity_id = str(uuid.uuid4())
    async with async_session() as session:
        session.add(EntityModel(
            id=entity_id,
            name="Rowan",
            entity_type="person",
        ))
        session.add(EntityMentionModel(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            memory_id=mem.id,
            relationship="subject",
        ))
        await session.commit()

    found = await retrieve_entity_memories(
        "What does Rowan think about calls?",
        limit=5,
    )

    ids_returned = {m.id for m in found}
    assert mem.id in ids_returned, (
        "Entity-keyed retrieval must return the memory linked via "
        f"entity_mentions to the detected entity. Got: {ids_returned}"
    )


@pytest.mark.integration
async def test_entity_keyed_retrieval_empty_when_no_match(clean_db):
    from services.memory_service import retrieve_entity_memories

    result = await retrieve_entity_memories(
        "a totally unrelated query about xyzzy",
        limit=5,
    )
    assert result == [], (
        "No entities match -> no entity-keyed hits"
    )


@pytest.mark.integration
async def test_entity_keyed_retrieval_skips_a_retired_memory(clean_db):
    """``get_memory_by_id`` returns a retired row too; the entity path must
    not bring it into the turn (2026-09-27 — the memory audit retired ~600
    rows, many sensitive)."""
    from datetime import datetime, timedelta

    from services.database import async_session, EntityModel, EntityMentionModel
    from services.memory_service import Memory, store_memory, retrieve_entity_memories

    live = await store_memory(Memory(id=None, content="Rowan prefers voice calls over texts",
                                     memory_type="preference", importance=0.7, confidence=0.9))
    retired = await store_memory(Memory(id=None, content="Rowan had a hospital visit in July",
                                        memory_type="fact", importance=0.7, confidence=0.9,
                                        valid_until=datetime.utcnow() - timedelta(days=1)))
    entity_id = str(uuid.uuid4())
    async with async_session() as session:
        session.add(EntityModel(id=entity_id, name="Rowan", entity_type="person"))
        for mem in (live, retired):
            session.add(EntityMentionModel(id=str(uuid.uuid4()), entity_id=entity_id, memory_id=mem.id,
                                           relationship="subject"))
        await session.commit()

    found = {m.id for m in await retrieve_entity_memories("How is Rowan?", limit=5)}

    assert live.id in found
    assert retired.id not in found, "a retired row reached the turn through its entity"


@pytest.mark.integration
async def test_retired_mentions_do_not_crowd_out_live_ones(clean_db):
    """The lookup read ``limit`` mentions, newest first, and only then
    dropped the retired rows: five retired mentions ahead of a live one
    left the turn with nothing about the person."""
    from datetime import datetime, timedelta

    from services.database import async_session, EntityModel, EntityMentionModel
    from services.memory_service import Memory, store_memory, retrieve_entity_memories

    live = await store_memory(Memory(id=None, content="Rowan prefers voice calls over texts",
                                     memory_type="preference", importance=0.7, confidence=0.9))
    retired = [
        await store_memory(Memory(id=None, content=f"Rowan retired detail {i}", memory_type="fact",
                                  importance=0.7, confidence=0.9,
                                  valid_until=datetime.utcnow() - timedelta(days=1)))
        for i in range(5)
    ]
    entity_id = str(uuid.uuid4())
    async with async_session() as session:
        session.add(EntityModel(id=entity_id, name="Rowan", entity_type="person"))
        session.add(EntityMentionModel(id=f"a-{uuid.uuid4()}", entity_id=entity_id, memory_id=live.id,
                                       relationship="subject"))
        for mem in retired:  # read first: the lookup orders by mention id, descending
            session.add(EntityMentionModel(id=f"z-{uuid.uuid4()}", entity_id=entity_id, memory_id=mem.id,
                                           relationship="subject"))
        await session.commit()

    found = {m.id for m in await retrieve_entity_memories("How is Rowan?", limit=5)}

    assert found == {live.id}
