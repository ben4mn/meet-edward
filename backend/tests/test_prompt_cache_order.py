"""The static part of the system prompt comes first, so Codex can cache it
(2026-09-25 production audit).

The ~18K-char tool manual was appended at the *end* of the memory context,
after the memories, the state documents and the relevant documents — all
of which change every turn. Prompt caching matches a prefix, so the mind
got 0 cached tokens on every wake. Now ``compose_system_prompt`` takes the
manual as ``tool_manual`` and puts it directly after ``base``, followed by
the fixed directives; the per-turn blocks (memories, briefing, orchestrator,
time) come after all of it.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit

MANUAL = "\n\n## Memory Tools\n" + "remember things carefully. " * 400


def _common_prefix_len(a: str, b: str) -> int:
    return len(os.path.commonprefix([a, b]))


def test_two_turns_with_different_memories_share_base_plus_manual():
    from services.graph.streaming import compose_system_prompt

    base = "You are Edward."
    one = compose_system_prompt(base=base, tool_manual=MANUAL,
                                memory_context="\n\n## Relevant Context\n- Ellis likes tea (memory_id: a)",
                                briefing_context="\n\n## Recent Awareness\nParker texted.",
                                time_context="\n\nCurrent date and time: Thursday 07:00",
                                state_documents=[{"id": "1"}])
    two = compose_system_prompt(base=base, tool_manual=MANUAL,
                                memory_context="\n\n## Relevant Context\n- Biscuit's dose is 6pm (memory_id: b)",
                                briefing_context="",
                                time_context="\n\nCurrent date and time: Thursday 18:00",
                                state_documents=[{"id": "1"}])

    shared = _common_prefix_len(one, two)
    assert shared >= len(base) + len(MANUAL), (shared, len(base) + len(MANUAL))


def test_the_manual_and_directives_come_before_every_per_turn_block():
    from services.graph.streaming import TOOL_USAGE_DIRECTIVE, compose_system_prompt

    out = compose_system_prompt(base="BASE", tool_manual=MANUAL,
                                memory_context="\n\nMEMORIES", briefing_context="\n\nBRIEFING",
                                session_briefing_context="\n\nSESSION", orchestrator_context="\n\nORCH",
                                time_context="\n\nTIME", state_documents=[])
    assert out.startswith("BASE" + MANUAL)
    manual_at = out.index(MANUAL)
    directives_at = out.index(TOOL_USAGE_DIRECTIVE)
    for dynamic in ("MEMORIES", "BRIEFING", "SESSION", "ORCH", "TIME"):
        assert manual_at < directives_at < out.index(dynamic), dynamic


def test_no_manual_means_no_manual():
    from services.graph.streaming import compose_system_prompt

    assert "## Memory Tools" not in compose_system_prompt(base="BASE", state_documents=[])


async def test_the_loop_puts_the_tool_manual_before_the_memories(monkeypatch):
    """Driven through the real ``chat_with_memory``: the system message the
    model receives has the bound tools' manual ahead of the memory block."""
    from langchain_core.messages import SystemMessage

    from services.graph import streaming
    from services.tool_registry import get_tool_descriptions
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, patch_loop_io

    tools = [FakeTool("leave_note")]
    h = patch_loop_io(monkeypatch, tools=tools, script=[ai("left it")])
    monkeypatch.setattr(streaming, "build_memory_context",
                        lambda *a, **k: "\n\n## Relevant Context\n- MEMORY-XYZ (memory_id: m1)")
    await streaming.chat_with_memory(message="a wake pack", turn_source="mind", skip_extraction=True,
                                     **base_kwargs(FakeGraph()))

    system = next(m for m in h.calls[0]["messages"] if isinstance(m, SystemMessage)).content
    manual = get_tool_descriptions(tools)
    assert manual and manual in system
    assert system.index(manual) < system.index("MEMORY-XYZ")
    assert system.startswith("be edward" + manual)


async def test_the_streaming_loop_puts_the_tool_manual_before_the_memories(monkeypatch):
    from langchain_core.messages import SystemMessage

    from services.graph import streaming
    from services.tool_registry import get_tool_descriptions
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, patch_loop_io, run_stream

    tools = [FakeTool("leave_note")]
    h = patch_loop_io(monkeypatch, tools=tools, script=[ai("done")])
    monkeypatch.setattr(streaming, "build_memory_context",
                        lambda *a, **k: "\n\n## Relevant Context\n- MEMORY-XYZ (memory_id: m1)")
    await run_stream(message="hello", **base_kwargs(FakeGraph()))

    system = next(m for m in h.calls[0]["messages"] if isinstance(m, SystemMessage)).content
    manual = get_tool_descriptions(tools)
    assert system.index(manual) < system.index("MEMORY-XYZ")
