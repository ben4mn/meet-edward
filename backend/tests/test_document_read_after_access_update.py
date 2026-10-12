"""``get_document_by_id`` bumps ``access_count`` with an ORM UPDATE and then
reads the row it just expired.

The health report on 2026-09-21 showed ``read_document`` failing on every
call (6/6) with "greenlet_spawn has not been called; can't call
await_only() here": the ORM-enabled ``update(...).values(last_accessed=
func.now())`` expires those attributes on the loaded row, and
``_model_to_document`` touching them after ``commit()`` triggers a lazy
refresh — implicit IO inside an async session. Edward could not read his
own state document.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_get_document_by_id_returns_the_document_and_counts_the_read(clean_db):
    from services.document_service import Document, get_document_by_id, save_document

    saved = await save_document(Document(id=None, title="Edward: state", content="I am Edward.", tags="state,self"))

    first = await get_document_by_id(saved.id)
    assert first is not None
    assert (first.title, first.content, first.tags) == ("Edward: state", "I am Edward.", "state,self")

    second = await get_document_by_id(saved.id)
    assert second is not None and second.access_count == first.access_count + 1
    assert second.last_accessed is not None


@pytest.mark.integration
async def test_read_document_tool_returns_the_content(clean_db):
    from services.document_service import Document, save_document
    from services.graph.tools import read_document

    saved = await save_document(Document(id=None, title="Edward: state", content="I am Edward.", tags="state,self"))
    out = await read_document.ainvoke({"document_id": saved.id})
    assert not out.startswith("Tool error"), out
    assert "# Edward: state" in out and "I am Edward." in out
