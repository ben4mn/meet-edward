"""A verified forget (plan Phase 5, 2026-09-24).

Muse ships a "forget" skill that removes matches "to the best of its
ability", and deleting a chat does not remove what it learned. Edward's
forget is a plan first (what matches, and why), then a delete of exactly
what Ellis saw, then counts — and a second look that says what still
matches. It must hit "Alex" and not "Alexa", "Alexandra" or everything
else; the precision set below pins that.
"""

from __future__ import annotations

import json

import pytest

from services import forget_service as fs


# ---------------------------------------------------------------- pure --

@pytest.mark.unit
@pytest.mark.parametrize("subject,terms", [
    ("Alex", ["alex"]),
    ("forget everything about Alex", ["alex"]),
    ("everything you know about Alex's birthday", ["alex", "birthday"]),
    ("my divorce", ["divorce"]),
    ("Ellis's sister", ["sister"]),
    ("the Acme job", ["acme", "job"]),
])
def test_subject_terms(subject, terms):
    assert fs.subject_terms(subject) == terms


@pytest.mark.unit
@pytest.mark.parametrize("subject", ["", "   ", "about", "everything", "Ellis", "me", "the"])
def test_subject_terms_refuse_what_would_mean_everything(subject):
    with pytest.raises(ValueError):
        fs.subject_terms(subject)


@pytest.mark.unit
@pytest.mark.parametrize("text,terms,hit", [
    ("Alex loves bouldering", ["alex"], True),
    ("Alex's birthday is May 3", ["alex"], True),
    ("ALEX called", ["alex"], True),
    ("Alexa plays jazz", ["alex"], False),
    ("Ellis's sister Alexandra", ["alex"], False),
    ("Alex's birthday is May 3", ["alex", "birthday"], True),
    ("Alex loves bouldering", ["alex", "birthday"], False),
    ("Two birthdays in May", ["birthday"], True),
    ("", ["alex"], False),
])
def test_matches_subject_is_whole_word(text, terms, hit):
    assert fs.matches_subject(text, terms) is hit


# --------------------------------------------------------- integration --

async def _wipe_side_tables():
    from sqlalchemy import text

    from services.database import async_session

    async with async_session() as session:
        for table in ("entity_mentions", "entities", "graph_ingest_queue"):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()


async def _seed():
    from services.conversation_service import create_conversation
    from services.database import (
        EntityMentionModel,
        EntityModel,
        GraphIngestQueueModel,
        MemoryConnectionModel,
        MemoryEnrichmentModel,
        MemoryFlagModel,
        async_session,
    )
    from services.document_service import Document, save_document
    from services.memory_service import Memory, store_memory

    await _wipe_side_tables()
    texts = [
        "Alex loves bouldering on Saturdays",           # 0 keyword
        "Alex's birthday is May 3",                     # 1 keyword
        "Ellis met Alex at the Springfield office",           # 2 keyword
        "Rivera invited Ellis to dinner next week",       # 3 entity only
        "Alexa plays jazz in the kitchen every morning",  # 4 no
        "Ellis's sister Alexandra lives in Boston",       # 5 no
        "Ellis likes sushi",                              # 6 no
        "Jordan moved to Denver",                       # 7 no
    ]
    mems = [await store_memory(Memory(id=None, content=t, memory_type="fact", importance=0.5)) for t in texts]
    async with async_session() as session:
        ent = EntityModel(name="Alex Rivera", entity_type="person", aliases=json.dumps(["AR"]))
        other = EntityModel(name="Jordan", entity_type="person")
        session.add_all([ent, other])
        await session.flush()
        session.add_all([
            EntityMentionModel(entity_id=ent.id, memory_id=mems[3].id, context_snippet="Rivera invited Ellis"),
            EntityMentionModel(entity_id=ent.id, memory_id=mems[0].id, context_snippet="Alex loves"),
            EntityMentionModel(entity_id=other.id, memory_id=mems[7].id, context_snippet="Jordan moved"),
            MemoryEnrichmentModel(conversation_id="c-1", memory_id=mems[0].id, memory_content=texts[0],
                                  memory_type="fact"),
            MemoryEnrichmentModel(conversation_id="c-1", memory_id=mems[6].id, memory_content=texts[6],
                                  memory_type="fact"),
            MemoryConnectionModel(memory_id_a=mems[0].id, memory_id_b=mems[6].id, connection_type="related"),
            MemoryConnectionModel(memory_id_a=mems[6].id, memory_id_b=mems[7].id, connection_type="related"),
            MemoryFlagModel(memory_id=mems[1].id, flag_type="stale", description="old"),
            GraphIngestQueueModel(memory_id=mems[2].id, content=texts[2], status="pending"),
            GraphIngestQueueModel(memory_id=mems[6].id, content=texts[6], status="pending"),
        ])
        await session.commit()
    await save_document(Document(id=None, title="Notes on Alex", content="Alex likes climbing gyms", tags="people"))
    await save_document(Document(id=None, title="Sushi places", content="Omakase on Division", tags="food"))
    await save_document(Document(id=None, title="Edward: state", content="Watching Alex's move for Ellis",
                                 tags="state,self"))
    await create_conversation(conversation_id="c-party", title="Planning Alex's party")
    await create_conversation(conversation_id="c-food", title="Groceries")
    return mems


@pytest.mark.integration
async def test_plan_forget_hits_alex_and_nothing_else(clean_db):
    mems = await _seed()
    plan = await fs.plan_forget("Alex")
    got = {m["id"] for m in plan.memories}
    assert got == {mems[0].id, mems[1].id, mems[2].id, mems[3].id}
    why = {m["id"]: m["why"] for m in plan.memories}
    assert "alex" in why[mems[0].id].lower()
    assert "Alex Rivera" in why[mems[3].id]
    assert [d["title"] for d in plan.documents] == ["Notes on Alex"]
    assert [d["title"] for d in plan.protected_documents] == ["Edward: state"]
    assert [c["title"] for c in plan.conversations] == ["Planning Alex's party"]
    assert len(plan.enrichment_ids) == 1 and len(plan.connection_ids) == 1 and len(plan.flag_ids) == 1
    assert len(plan.entity_ids) == 1 and len(plan.mention_ids) == 2 and len(plan.queue_ids) == 1

    narrow = await fs.plan_forget("Alex's birthday")
    assert {m["id"] for m in narrow.memories} == {mems[1].id}


@pytest.mark.integration
async def test_apply_forget_deletes_exactly_the_plan_and_verifies(clean_db):
    from sqlalchemy import func, select

    from services.database import (
        EntityMentionModel,
        EntityModel,
        LedgerEventModel,
        MemoryConnectionModel,
        MemoryEnrichmentModel,
        MemoryFlagModel,
        async_session,
    )
    from services.document_service import list_documents
    from services.memory_service import get_memory_by_id

    mems = await _seed()
    plan = await fs.plan_forget("Alex")
    result = await fs.apply_forget(plan_id=plan.id, source="api")
    assert result["deleted"] == {"memories": 4, "documents": 0, "enrichments": 1, "connections": 1, "flags": 1,
                                 "entity_mentions": 2, "entities": 1, "graph_queue": 1}
    assert result["still_matching"]["memories"] == 0
    assert result["still_matching"]["documents"] == 1, "documents stay unless asked"
    assert result["conversations_mentioning"] == 1
    for i in range(4):
        assert await get_memory_by_id(mems[i].id) is None
    for i in range(4, 8):
        assert await get_memory_by_id(mems[i].id) is not None
    docs, _ = await list_documents()
    assert {d.title for d in docs} == {"Notes on Alex", "Sushi places", "Edward: state"}
    async with async_session() as session:
        assert (await session.execute(select(func.count()).select_from(MemoryEnrichmentModel))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(MemoryConnectionModel))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(MemoryFlagModel))).scalar() == 0
        assert (await session.execute(select(func.count()).select_from(EntityModel))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(EntityMentionModel))).scalar() == 1
        row = (await session.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "forget"))).scalar_one()
    assert row.source == "api" and row.outcome == "applied"
    assert "alex" not in ((row.reason or "") + (row.detail_json or "")).lower(), "counts only, never the content"
    assert json.loads(row.detail_json)["memories"] == 4

    with pytest.raises(ValueError):
        await fs.apply_forget(plan_id="no-such-plan")


@pytest.mark.integration
async def test_apply_forget_with_documents_spares_the_state_document(clean_db):
    from services.document_service import list_documents

    await _seed()
    result = await fs.apply_forget(subject="Alex", include_documents=True, source="api")
    assert result["deleted"]["documents"] == 1
    assert result["still_matching"]["documents"] == 0
    assert result["protected_documents"] == ["Edward: state"]
    docs, _ = await list_documents()
    assert {d.title for d in docs} == {"Sushi places", "Edward: state"}


@pytest.mark.integration
async def test_forget_about_tool_previews_then_needs_a_later_turn(clean_db):
    from services import turn_ledger
    from services.graph.tools import forget_about, set_current_conversation_id
    from services.memory_service import get_memory_by_id

    mems = await _seed()
    set_current_conversation_id("c-chat")
    t1 = turn_ledger.begin_turn(conversation_id="c-chat", source="chat", model=None, provider=None)
    try:
        preview = await forget_about.ainvoke({"subject": "Alex"})
        assert "4 memories" in preview and "Alex loves bouldering" in preview
        assert "Notes on Alex" in preview and "confirm" in preview.lower()
        assert await get_memory_by_id(mems[0].id) is not None, "a preview deletes nothing"
        same_turn = await forget_about.ainvoke({"subject": "Alex", "confirm": True})
        assert same_turn.startswith("Tool error:") and await get_memory_by_id(mems[0].id) is not None
    finally:
        turn_ledger.end_turn(t1, "completed")

    t2 = turn_ledger.begin_turn(conversation_id="c-chat", source="chat", model=None, provider=None)
    try:
        done = await forget_about.ainvoke({"subject": "alex", "confirm": True})
    finally:
        turn_ledger.end_turn(t2, "completed")
    assert "Forgot" in done and "4 memories" in done
    assert "0" in done and "still" in done.lower()
    assert await get_memory_by_id(mems[0].id) is None


@pytest.mark.integration
async def test_forget_about_without_a_preview_only_previews(clean_db):
    from services.graph.tools import forget_about, set_current_conversation_id
    from services.memory_service import get_memory_by_id

    mems = await _seed()
    set_current_conversation_id("c-other")
    out = await forget_about.ainvoke({"subject": "Jordan", "confirm": True})
    assert "Nothing was deleted" in out
    assert await get_memory_by_id(mems[7].id) is not None


@pytest.mark.integration
async def test_forget_about_is_refused_outside_a_conversation_with_owner(clean_db):
    from services import turn_ledger
    from services.graph.tools import forget_about

    await _seed()
    rec = turn_ledger.begin_turn(conversation_id="edward-mind", source="mind", model=None, provider=None)
    try:
        out = await forget_about.ainvoke({"subject": "Alex"})
    finally:
        turn_ledger.end_turn(rec, "completed")
    assert out.startswith("Tool error:")


@pytest.mark.integration
async def test_forget_route_previews_and_confirms(clean_db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import memories as memories_router

    await _seed()
    app = FastAPI()
    app.include_router(memories_router.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.post("/api/memories/forget", json={"subject": "Alex"})
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        assert r.json()["status"] == "preview" and len(plan["memories"]) == 4
        assert plan["related"]["enrichments"] == 1 and plan["conversations"]["count"] == 1
        r = await client.post("/api/memories/forget", json={"subject": "Alex", "confirm": True,
                                                            "plan_id": plan["id"]})
        assert r.status_code == 200 and r.json()["result"]["deleted"]["memories"] == 4
        r = await client.post("/api/memories/forget", json={"subject": "everything"})
        assert r.status_code == 400
