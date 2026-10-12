"""Regression test: consolidation narrative lookup crashed on the
search_documents return shape.

``search_documents`` returns ``(List[Document], total)`` but the
narrative-synthesis loop iterated the tuple itself, so every entity
narrative burned an LLM call then died with ``'list' object has no
attribute 'tags'`` before saving — hourly, per entity, for weeks
(81 hits in the last 3k log lines).
"""

import asyncio
from types import SimpleNamespace

import services.consolidation_service as consolidation_service
from services.consolidation_service import _find_existing_narrative


def make_doc(doc_id, title, tags):
    return SimpleNamespace(id=doc_id, title=title, tags=tags)


def test_finds_matching_entity_narrative(monkeypatch):
    docs = [
        make_doc("d1", "Narrative: Pepper", "narrative,system_generated,entity:Pepper"),
        make_doc("d2", "Narrative: Other", "narrative,entity:Other"),
    ]

    async def fake_search(query, limit=1, **kwargs):
        return docs, len(docs)

    monkeypatch.setattr(consolidation_service, "search_documents", fake_search)
    found = asyncio.run(_find_existing_narrative("Pepper"))
    assert found is docs[0]


def test_returns_none_when_no_tag_match(monkeypatch):
    docs = [make_doc("d1", "Narrative: Other", "narrative,entity:Other")]

    async def fake_search(query, limit=1, **kwargs):
        return docs, 1

    monkeypatch.setattr(consolidation_service, "search_documents", fake_search)
    assert asyncio.run(_find_existing_narrative("Pepper")) is None


def test_handles_docs_with_no_tags(monkeypatch):
    docs = [make_doc("d1", "Narrative: Pepper", None)]

    async def fake_search(query, limit=1, **kwargs):
        return docs, 1

    monkeypatch.setattr(consolidation_service, "search_documents", fake_search)
    assert asyncio.run(_find_existing_narrative("Pepper")) is None
