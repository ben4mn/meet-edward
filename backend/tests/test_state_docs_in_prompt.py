"""State documents reach the system prompt (2026-09-25 production audit).

``build_memory_context`` gave the "Active State" block a 2,000-char budget
and ``break``-ed at the first document that did not fit. The newest state
document is always ``Edward: state`` (~4K chars), so the loop stopped on
it and **no** state document ever reached a system prompt — Ellis's
``Project: …`` and ``Person: …`` pages included.

Now every entry is clipped to ``STATE_DOC_ENTRY_CHARS`` with a pointer to
``read_document`` for the rest, an entry that still does not fit is
skipped (never the end of the loop), the block's total is
``MAX_STATE_DOC_CHARS`` (6,000), and a state document the turn's trigger
already carries in full (a wake's pack opens with ``Edward: state``) is
not repeated.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.unit


@dataclass
class _Doc:
    id: str
    title: str
    content: str
    tags: str = "state"


def _words(n_chars: int, word: str = "lorem") -> str:
    out = []
    size = 0
    i = 0
    while size < n_chars:
        w = f"{word}{i}"
        out.append(w)
        size += len(w) + 1
        i += 1
    return " ".join(out)[:n_chars]


def _state_block(context: str) -> str:
    start = context.index("## Active State")
    rest = context[start + 1:]
    nxt = rest.find("\n## ")
    return context[start:] if nxt < 0 else context[start:start + 1 + nxt]


def test_a_long_state_document_no_longer_hides_every_other_one():
    from services.graph.streaming import MAX_STATE_DOC_CHARS, build_memory_context

    state = _Doc("doc-state", "Edward: state", _words(4000, "self"))
    project = _Doc("doc-proj", "Project: X", _words(800, "proj"))
    context = build_memory_context([], state_documents=[state, project])

    assert "Project: X" in context, "the project page must reach the prompt"
    assert "(document_id: doc-proj)" in context
    assert project.content in context, "an 800-char page fits whole"
    block = _state_block(context)
    assert len(block) <= MAX_STATE_DOC_CHARS + 100, len(block)


def test_an_oversize_entry_is_clipped_with_a_pointer_to_the_rest():
    from services.graph.streaming import STATE_DOC_ENTRY_CHARS, build_memory_context

    state = _Doc("doc-state", "Edward: state", _words(4000, "self"))
    context = build_memory_context([], state_documents=[state])

    assert "Edward: state" in context
    assert "read_document doc-state for the rest" in context
    assert state.content[:200] in context
    assert state.content not in context, "clipped, not whole"
    block = _state_block(context)
    assert len(block) <= STATE_DOC_ENTRY_CHARS + 200, len(block)


def test_the_total_budget_holds_and_the_loop_continues_past_what_does_not_fit():
    from services.graph.streaming import MAX_STATE_DOC_CHARS, build_memory_context

    docs = [_Doc(f"doc-{i}", f"Person: P{i}", _words(3000, f"p{i}x")) for i in range(6)]
    docs.append(_Doc("doc-small", "Goal: tiny", "Run a 10K."))
    context = build_memory_context([], state_documents=docs)
    block = _state_block(context)

    assert len(block) <= MAX_STATE_DOC_CHARS, len(block)
    assert MAX_STATE_DOC_CHARS >= 6000
    assert "Goal: tiny" in block, "a small page after the budget-busters still fits"
    assert "Person: P0" in block and "Person: P1" in block


def test_a_state_document_the_trigger_already_carries_is_not_repeated():
    from services.graph.streaming import build_memory_context

    state = _Doc("doc-state", "Edward: state", _words(4000, "self"))
    project = _Doc("doc-proj", "Project: X", _words(800, "proj"))
    pack = "Who you are and what you are doing (your state document):\n" + state.content[:3900]
    context = build_memory_context([], state_documents=[state, project], shown_in_trigger=pack)

    assert "Edward: state" not in context, "the pack already opens with it"
    assert "Project: X" in context


def test_no_state_documents_means_no_state_block():
    from services.graph.streaming import build_memory_context

    assert "Active State" not in build_memory_context([], state_documents=[])
    assert "Active State" not in build_memory_context([], state_documents=[_Doc("d", "Empty", "")],
                                                      shown_in_trigger="")


# ------------------------------------------------ both loops pass the trigger --

async def _capture_context_kwargs(monkeypatch, run):
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeTool, ai, patch_loop_io

    patch_loop_io(monkeypatch, tools=[FakeTool("leave_note")], script=[ai("left it")])
    seen: list[dict] = []

    def _capture(*a, **k):
        seen.append(k)
        return ""

    monkeypatch.setattr(streaming, "build_memory_context", _capture)
    await run()
    return seen


async def test_the_non_streaming_loop_hands_its_trigger_to_the_state_block(monkeypatch):
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, base_kwargs

    pack = "Who you are and what you are doing (your state document):\nI am Edward."
    seen = await _capture_context_kwargs(monkeypatch, lambda: streaming.chat_with_memory(
        message=pack, turn_source="mind", skip_extraction=True, **base_kwargs(FakeGraph())))
    assert seen and seen[0].get("shown_in_trigger") == pack


async def test_the_streaming_loop_hands_its_trigger_to_the_state_block(monkeypatch):
    from tests.fixtures.tool_loop import FakeGraph, base_kwargs, run_stream

    seen = await _capture_context_kwargs(monkeypatch, lambda: run_stream(
        message="what's on today?", **base_kwargs(FakeGraph())))
    assert seen and seen[0].get("shown_in_trigger") == "what's on today?"
