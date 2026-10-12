"""R/G TDD red for P1b — marking the memories the model actually saw.

``build_memory_context`` is the one place memories become prompt text. It
does two things that make "retrieved" and "seen by the model" diverge:

  * it slices ``memories[:MAX_NORMAL_MEMORIES]``
  * it truncates the enrichment block to fit MAX_MEMORY_CONTEXT_CHARS,
    cutting mid-string when the budget runs out

So the honest answer to "which memories did this turn use?" is: whichever
``(memory_id: ...)`` tags survived into the returned text. Parsing the built
context is not a workaround — it is the only place that reflects the
truncation, and it stays correct if the budget rules change.

Contract:
  * ``memory_ids_in_context(text)`` returns the ids present, in order,
    deduplicated.
  * An id whose line was cut off by budget truncation is NOT returned.
"""

from __future__ import annotations

import pytest


def test_helper_exists():
    from services.graph import streaming

    assert hasattr(streaming, "memory_ids_in_context")


def test_extracts_ids_in_order():
    from services.graph.streaming import memory_ids_in_context

    text = (
        "\n\n## Relevant Context from Previous Conversations:\n"
        "- [fact][user_stated, 0.9] Ellis's dog is Cricket  (memory_id: aaa-111)\n"
        "- [preference][inferred, 0.6] Ellis dislikes bananas  (memory_id: bbb-222)\n"
    )

    assert memory_ids_in_context(text) == ["aaa-111", "bbb-222"]


def test_deduplicates_repeated_ids():
    from services.graph.streaming import memory_ids_in_context

    text = "(memory_id: aaa-111)\n(memory_id: bbb-222)\n(memory_id: aaa-111)"

    assert memory_ids_in_context(text) == ["aaa-111", "bbb-222"]


def test_truncated_tail_is_not_counted():
    """The budget cut mid-tag; that memory never reached the model intact."""
    from services.graph.streaming import memory_ids_in_context

    text = (
        "- [fact] Ellis's dog is Cricket (memory_id: aaa-111)\n"
        "- [fact] Ellis lives in Ohio (memory_i"
    )

    assert memory_ids_in_context(text) == ["aaa-111"], (
        "A memory whose id tag was severed by truncation was not fully shown "
        "to the model and must not be credited with a use."
    )


def test_empty_and_none_are_safe():
    from services.graph.streaming import memory_ids_in_context

    assert memory_ids_in_context("") == []
    assert memory_ids_in_context(None) == []


def test_real_build_memory_context_output_round_trips():
    """Build a real context block and read the ids back out of it."""
    from services.graph.streaming import build_memory_context, memory_ids_in_context
    from services.memory_service import Memory

    memories = [
        Memory(
            id=f"mem-{i}",
            content=f"Ellis fact number {i}",
            memory_type="fact",
            importance=0.5,
        )
        for i in range(3)
    ]

    context = build_memory_context(memories)
    ids = memory_ids_in_context(context)

    assert ids == ["mem-0", "mem-1", "mem-2"], (
        "The helper must read back exactly the memories build_memory_context "
        f"actually rendered. Got {ids}."
    )


def test_memories_dropped_by_the_cap_are_not_returned():
    from services.graph import streaming
    from services.graph.streaming import build_memory_context, memory_ids_in_context
    from services.memory_service import Memory

    over_cap = streaming.MAX_NORMAL_MEMORIES + 5
    memories = [
        Memory(
            id=f"mem-{i}",
            content=f"Ellis fact number {i}",
            memory_type="fact",
            importance=0.5,
        )
        for i in range(over_cap)
    ]

    ids = memory_ids_in_context(build_memory_context(memories))

    assert len(ids) == streaming.MAX_NORMAL_MEMORIES, (
        "Memories sliced off by MAX_NORMAL_MEMORIES were never shown and must "
        "not be credited with a use."
    )
