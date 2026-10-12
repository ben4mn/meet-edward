"""A self he can hold in his head — mind rebuild Phase 9.

One short document ("Edward: state") says who he is and what he is doing.
Every wake reads it; the Sunday reflection rewrites it; the manual tells
him to edit it whenever something about himself changes. Open intentions
are listed with their count so an unbounded list is visible to him and to
Ellis — the count and the weekly instruction to close stale ones are the
bound, not a cap.
"""

from __future__ import annotations

import pytest


def test_state_document_is_loaded_into_every_wake():
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(reason="evening", state_document="I am Edward. This week I am watching ACME for Ellis."))
    assert "Who you are and what you are doing (your state document):" in text
    assert "I am Edward. This week I am watching ACME for Ellis." in text
    assert "you have no state document" not in text.lower()


def test_state_document_is_clipped_not_truncated_mid_word():
    from services.mind_service import STATE_DOC_MAX_CHARS, clip_state_document, render_pack
    from tests.test_mind_service import _pack

    body = ("watching " * 900).strip()  # 8,099 chars, no word straddles 4,000 by luck — check anyway
    clipped, was_clipped = clip_state_document(body)
    assert was_clipped and len(clipped) <= STATE_DOC_MAX_CHARS == 4000
    assert clipped.endswith("watching"), "cut on a word boundary"
    short, was_clipped = clip_state_document("short")
    assert (short, was_clipped) == ("short", False)

    text = render_pack(_pack(state_document=body))
    assert "clipped" in text.lower() and "shorten" in text.lower()
    text = render_pack(_pack(state_document="short"))
    assert "clipped" not in text.lower()


def test_a_missing_state_document_is_invited():
    from services.mind_service import STATE_DOC_TITLE, render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(state_document=None))
    assert "you have no state document" in text.lower()
    assert f'save_document(title="{STATE_DOC_TITLE}"' in text
    assert STATE_DOC_TITLE == "Edward: state"


def test_the_sunday_nightly_asks_for_a_rewrite():
    from datetime import datetime, timezone

    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    sunday = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)
    text = render_pack(_pack(reason="nightly", now=sunday, state_document="I am Edward."))
    low = text.lower()
    assert "rewrite your state document" in low and "edit_document" in text
    assert "a page, not a log" in low
    assert "rewrite your state document" not in render_pack(_pack(reason="self", state_document="x")).lower()


def test_manual_tells_him_to_keep_it_current():
    from services.graph.tools import get_mind_tools_description

    manual = get_mind_tools_description()
    assert "Edward: state" in manual and "edit_document" in manual
    assert "4,000" in manual or "4000" in manual


def test_open_intentions_are_listed_with_count():
    from services.memory_service import Memory
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    intentions = [Memory(id=f"i{n}", content=f"Watching thing {n}", memory_type="intention", importance=0.5,
                         origin="thought", about="self") for n in range(7)]
    thoughts = [Memory(id=f"t{n}", content=f"Thought {n}", memory_type="context", importance=0.5,
                       origin="thought", about="self") for n in range(2)]
    text = render_pack(_pack(thoughts=intentions + thoughts))
    assert "Open intentions (7):" in text
    assert "Recent thoughts (2):" in text
    assert "(memory_id: i6)" in text and "(memory_id: t1)" in text
    empty = render_pack(_pack(thoughts=[]))
    assert "Open intentions (0):" in empty


@pytest.mark.integration
async def test_collect_pack_loads_the_state_document(clean_db):
    import services.mind_service as ms
    from services.document_service import Document, get_document_by_title, save_document

    assert await get_document_by_title("Edward: state") is None
    await save_document(Document(id=None, title="Edward: state", content="I am Edward. I watch ACME for Ellis.",
                                 tags="state,self"))
    doc = await get_document_by_title("edward: state")
    assert doc is not None and doc.content.startswith("I am Edward"), "title match is case-insensitive"
    ms.reset_state()
    pack = await ms.collect_pack("manual")
    assert pack.state_document == "I am Edward. I watch ACME for Ellis."
