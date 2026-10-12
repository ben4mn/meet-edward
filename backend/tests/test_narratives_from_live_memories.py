"""Entity narratives are written from live memories only (2026-09-27).

Consolidation's step 11 builds a "Narrative: <person>" document from the
memories an entity's mentions link to, loaded one by one with
``get_memory_by_id`` — which returns a row whatever its ``valid_until``.
The memory audit retired ~600 rows, many of them sensitive (health,
family, money); without a check each hourly cycle would have written them
back into a document that rides the prompt by title and that Edward reads
in full. A retired row never reaches a narrative, and only live rows count
toward the five a narrative needs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tests.test_consolidation_cost_caps import _insert_memory, _set_config, _use_llm

pytestmark = pytest.mark.integration


class _LLM:
    """Clusters nothing, and records every narrative prompt."""

    def __init__(self):
        self.narratives = []

    async def ainvoke(self, messages):
        text = " ".join(str(getattr(m, "content", m)) for m in messages)

        class _R:
            def __init__(self, c):
                self.content = c

        if "Synthesize these memories" in text:
            self.narratives.append(str(messages[-1].content))
            return _R("Rowan is Ellis's partner.")
        return _R('{"clusters": []}')


async def _entity_with(live: int, retired: int, retired_first: bool = False, name: str = "Rowan",
                       mention_count: int | None = None, filler: bool = True) -> None:
    """An entity ``name`` whose mentions link ``live`` live rows and
    ``retired`` rows retired yesterday. ``retired_first`` gives the retired
    mentions the ids the lookup reads first (it orders by id, descending)."""
    from services import database
    from services.database import EntityMentionModel, EntityModel, MemoryModel

    gone = datetime.utcnow() - timedelta(days=1)
    async with database.async_session() as session:
        for _ in range(3 if filler else 0):  # a cycle with fewer than three candidates is skipped
            await _insert_memory(session, f"Ellis's calendar note {uuid.uuid4().hex[:6]}")
        entity = EntityModel(id=str(uuid.uuid4()), name=name, entity_type="person",
                             mention_count=mention_count if mention_count is not None else live + retired)
        session.add(entity)
        rows = [(f"{name} fact {i} (live)", None) for i in range(live)]
        rows += [(f"{name} RETIRED detail {i}", gone) for i in range(retired)]
        for n, (content, until) in enumerate(rows):
            mid = str(uuid.uuid4())
            session.add(MemoryModel(id=mid, content=content, memory_type="fact", importance=0.2,
                                    origin="read", about="contact", valid_until=until))
            prefix = "z" if (until is not None) == retired_first else "a"
            session.add(EntityMentionModel(id=f"{prefix}{n:03d}-{uuid.uuid4()}", entity_id=entity.id,
                                           memory_id=mid, relationship="subject"))
        await session.commit()


async def _narrative():
    from services import database
    from services.database import DocumentModel

    async with database.async_session() as session:
        return (await session.execute(
            select(DocumentModel).where(DocumentModel.title == "Narrative: Rowan"))).scalar_one_or_none()


async def test_retired_rows_do_not_count_toward_a_narrative(clean_db, monkeypatch):
    from services.consolidation_service import _run_consolidation_cycle

    await _entity_with(live=4, retired=3)
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.narratives == [], "four live rows are too few for a narrative"
    assert await _narrative() is None


async def test_a_narrative_never_carries_a_retired_row(clean_db, monkeypatch):
    from services.consolidation_service import _run_consolidation_cycle

    await _entity_with(live=5, retired=3)
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert len(stub.narratives) == 1
    prompt = stub.narratives[0]
    assert "RETIRED" not in prompt
    assert prompt.count("(live)") == 5
    assert await _narrative() is not None


async def test_retired_mentions_read_first_do_not_crowd_out_the_live_ones(clean_db, monkeypatch):
    """Ten retired mentions ahead of five live ones: the lookup reads past
    them, so the narrative is rewritten from what is live — an older one
    built before the retirement stops carrying the retired rows."""
    from services.consolidation_service import _run_consolidation_cycle

    await _entity_with(live=5, retired=10, retired_first=True)
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert len(stub.narratives) == 1
    assert "RETIRED" not in stub.narratives[0]
    assert stub.narratives[0].count("(live)") == 5


async def _save_narrative(name: str, content: str, tags: str | None = None) -> str:
    from services.document_service import Document, save_document

    doc = await save_document(Document(id=None, title=f"Narrative: {name}", content=content,
                                       tags=tags or f"narrative,system_generated,entity:{name}"))
    return doc.id


async def _document(doc_id: str):
    from services import database
    from services.database import DocumentModel

    async with database.async_session() as session:
        return (await session.execute(select(DocumentModel).where(DocumentModel.id == doc_id))).scalar_one_or_none()


async def test_a_narrative_left_with_under_five_live_rows_is_deleted(clean_db, monkeypatch):
    """An entity whose rows were retired below the five a narrative needs
    was skipped, so its narrative kept the retired text for good (5 of the
    16 on 2026-09-27). The narrative is Edward's own summary of those rows,
    so it goes; a later cycle writes a new one once five are live again.
    Only this entity's system-generated narrative goes — not another whose
    name starts the same, and not a document Ellis titled that way."""
    from services.consolidation_service import _run_consolidation_cycle

    await _entity_with(live=4, retired=3)
    stale = await _save_narrative("Rowan", "Rowan RETIRED detail 0, from before the audit.")
    other = await _save_narrative("Rowan Hale", "Rowan Hale is a colleague.")
    owners = await _save_narrative("Rowan", "Ellis's own note about Rowan.", tags="notes")
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.narratives == []
    assert await _document(stale) is None, "the narrative built on retired rows is gone"
    assert await _document(other) is not None
    assert await _document(owners) is not None


async def test_an_entity_with_too_few_live_rows_does_not_use_a_narrative_slot(clean_db, monkeypatch):
    """Skipped entities used one of the cycle's narrative slots each without
    writing anything, so three of them ahead of a live one kept it from
    ever being written."""
    from services.consolidation_service import _run_consolidation_cycle

    for i in range(3):
        await _entity_with(live=2, retired=5, name=f"Gone{i}", mention_count=100, filler=(i == 0))
    await _entity_with(live=5, retired=0, filler=False)
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle(max_narratives=3)  # most-mentioned first

    assert len(stub.narratives) == 1 and "Rowan fact" in stub.narratives[0]
    assert await _narrative() is not None


async def test_a_narrative_reads_live_rows_past_a_long_run_of_retired_mentions(clean_db, monkeypatch):
    """The live rows are found however many retired mentions come first —
    a fixed window of mentions could still see none of them."""
    from services.consolidation_service import _run_consolidation_cycle

    await _entity_with(live=5, retired=60, retired_first=True)
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert len(stub.narratives) == 1
    assert "RETIRED" not in stub.narratives[0]
    assert stub.narratives[0].count("(live)") == 5


@pytest.mark.parametrize("rich_first", [True, False])
async def test_a_thin_same_name_twin_never_deletes_the_rich_ones_narrative(clean_db, monkeypatch, rich_first):
    """The drop is by name (the title and the ``entity:<name>`` tag), but it
    was decided per entity row (integration review, 2026-09-27): 11 names
    in production are shared by entities of five or more mentions — Biscuit
    is a person with 8 live and a topic with 3. Whichever came second in
    the unordered hourly query decided: a thin twin after the rich one
    deleted the narrative it had just written, a model call wasted every
    cycle; before it, the narrative was deleted and written anew. A name's
    narrative goes only when no entity of that name has five live rows."""
    from services.consolidation_service import _run_consolidation_cycle

    twins = [dict(live=8, retired=0), dict(live=3, retired=0, mention_count=11)]
    for n, kw in enumerate(twins if rich_first else twins[::-1]):
        await _entity_with(name="Biscuit", filler=(n == 0), **kw)
    existing = await _save_narrative("Biscuit", "Biscuit is Ellis's dog.")
    await _set_config(enabled=True, max_llm_calls_per_cycle=20)
    stub = _LLM()
    _use_llm(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert len(stub.narratives) == 1
    kept = await _document(existing)
    assert kept is not None, "the thin twin deleted the name's narrative"
    assert kept.content == "Rowan is Ellis's partner.", "the rich twin's rewrite is what stays"
