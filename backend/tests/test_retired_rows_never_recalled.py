"""A retired memory stays out of every recall path (memory audit, 2026-09-27).

The audit soft-retires rows by stamping ``valid_until``; ``retrieve_memories``
already filters them, but four side paths read memories by id or by search
without that filter, so retired rows kept reaching Edward's wakes:

  * spreading activation (``_spread_connected_memories``) pulls rows linked to
    a primary hit through ``memory_connections`` — 409 of the audit's retired
    rows still had an edge to a live row;
  * entity recall (``retrieve_entity_memories``) loads an entity's linked
    memories by id;
  * ``remember_search`` and speculative recall (``search_memories``);
  * the reflection buffer (``load_enrichments``) replays rows found up to a
    day ago.

The memories page and verified forget still see retired rows: only recall
is live-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


async def _mem(content, *, retired=False, memory_type="fact"):
    from sqlalchemy import update

    from services.database import MemoryModel, async_session
    from services.memory_service import Memory, store_memory

    m = await store_memory(Memory(id=None, content=content, memory_type=memory_type,
                                  importance=0.6, confidence=0.9))
    if retired:
        async with async_session() as s:
            await s.execute(update(MemoryModel).where(MemoryModel.id == m.id)
                            .values(valid_until=datetime.utcnow() - timedelta(hours=1)))
            await s.commit()
    return m


async def test_spreading_activation_skips_retired_rows_and_still_fills_its_cap(clean_db):
    from services.database import MemoryConnectionModel, async_session
    from services.memory_service import _spread_connected_memories

    primary = await _mem("Parker Lowe works as a carpenter")
    retired = await _mem("Stay out of Parker's direct messages", retired=True)
    live = await _mem("Parker strongly dislikes conflict")
    async with async_session() as s:
        s.add_all([
            MemoryConnectionModel(memory_id_a=primary.id, memory_id_b=retired.id, connection_type="same_person", strength=0.9),
            MemoryConnectionModel(memory_id_a=primary.id, memory_id_b=live.id, connection_type="same_person", strength=0.5),
        ])
        await s.commit()

    got = await _spread_connected_memories({primary.id}, min_confidence=0.0, cap=1)

    assert [m.id for m in got] == [live.id], "the retired row took the only slot"


async def test_entity_recall_skips_retired_rows(clean_db):
    from services.database import EntityMentionModel, EntityModel, async_session
    from services.memory_service import retrieve_entity_memories

    live = await _mem("Zebulon Quark lives in Cleveland")
    retired = await _mem("Never message Zebulon Quark directly", retired=True)
    async with async_session() as s:
        e = EntityModel(name="Zebulon Quark", entity_type="person")
        s.add(e)
        await s.flush()
        s.add_all([EntityMentionModel(entity_id=e.id, memory_id=live.id, relationship="subject"),
                   EntityMentionModel(entity_id=e.id, memory_id=retired.id, relationship="subject")])
        await s.commit()

    got = {m.id for m in await retrieve_entity_memories("what's new with Zebulon Quark?", limit=5)}

    assert live.id in got
    assert retired.id not in got


async def test_search_memories_live_only_for_recall_but_not_for_the_memories_page(clean_db):
    from services.memory_service import search_memories

    live = await _mem("Ellis drinks oat milk in his morning coffee", memory_type="preference")
    retired = await _mem("Ellis drinks cream in his morning coffee", memory_type="preference", retired=True)

    recall, _ = await search_memories(query="morning coffee", limit=10, live_only=True)
    page, _ = await search_memories(query="morning coffee", limit=10)

    assert live.id in {m.id for m in recall} and retired.id not in {m.id for m in recall}
    assert {live.id, retired.id} <= {m.id for m in page}, "the memories page still lists retired rows"


async def test_remember_search_never_shows_a_retired_row(clean_db):
    from services.graph import tools

    tools._clear_memory_search_cache()
    await _mem("Ellis's search for a vintage kayak is ongoing")
    await _mem("Remind Ellis on July 23 about the vintage kayak, in-app only", retired=True)

    out = await tools.remember_search.ainvoke({"query": "vintage kayak"})

    assert "ongoing" in out
    assert "in-app only" not in out


async def test_load_enrichments_drops_rows_retired_since_they_were_found(clean_db):
    from services.database import MemoryEnrichmentModel, async_session
    from services.reflection_service import load_enrichments

    live = await _mem("Ellis works at Acme Group")
    retired = await _mem("Ellis must only get in-app reminders", retired=True)
    async with async_session() as s:
        for m in (live, retired):
            s.add(MemoryEnrichmentModel(conversation_id="c-enrich", memory_id=m.id, memory_content=m.content,
                                        memory_type=m.memory_type, score=0.5))
        await s.commit()

    got = {m.id for m in await load_enrichments("c-enrich")}

    assert got == {live.id}
