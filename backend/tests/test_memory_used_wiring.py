"""R/G TDD red for P1c — every prompt build credits the memories it showed.

``memory_ids_in_context`` can read the ids back out of a built context, but
that is inert until the streaming path calls it. streaming.py builds a memory
context in two places (the streaming turn and the non-streaming turn) and both
must credit what they rendered — otherwise ``used_count`` stays zero for half
the traffic and P3's tier ladder reads a signal that is only half collected.

This is a structural guard rather than an end-to-end run: exercising
``stream_chat`` needs a live LLM, DB, tool registry and checkpointer. What
actually goes wrong here in practice is a third call site being added later
without the credit, and that is exactly what this catches.
"""

from __future__ import annotations

import inspect
import re


def _streaming_source() -> str:
    from services.graph import streaming

    return inspect.getsource(streaming)


def test_every_build_memory_context_call_is_credited():
    """Each build_memory_context call must be followed by a use credit."""
    src = _streaming_source()

    # Ignore the definition itself; count the invocations.
    call_sites = [
        m.start()
        for m in re.finditer(r"(?<!def )build_memory_context\(", src)
    ]
    assert len(call_sites) >= 2, (
        f"Expected at least 2 build_memory_context call sites, found "
        f"{len(call_sites)} — the guard below is calibrated against them."
    )

    credit_sites = [m.start() for m in re.finditer(r"mark_memories_used", src)]

    assert len(credit_sites) >= len(call_sites), (
        f"{len(call_sites)} prompt builds but only {len(credit_sites)} "
        "mark_memories_used credits. A build that renders memories into the "
        "prompt without crediting them leaves used_count under-counting, and "
        "the tier ladder that reads it will never promote."
    )

    # Each credit must sit reasonably close after a call site, not in some
    # unrelated corner of the module.
    for credit in credit_sites:
        assert any(
            0 < credit - call < 4000 for call in call_sites
        ), "A mark_memories_used credit is not attached to a prompt build."


def test_credit_reads_the_rendered_context_not_the_input_list():
    """Crediting the input list would over-count capped/truncated memories."""
    src = _streaming_source()

    assert "memory_ids_in_context(memory_context)" in src, (
        "The credit must be computed from the RENDERED context string. "
        "Crediting the retrieved_memories list instead would credit memories "
        "that MAX_NORMAL_MEMORIES or the char budget dropped — reintroducing "
        "the exact over-counting P1 exists to remove."
    )


def test_credit_is_non_blocking_and_guarded():
    src = _streaming_source()

    assert "asyncio.create_task(mark_memories_used" in src, (
        "Use marking must not block the turn — it is a statistic, not state "
        "the response depends on."
    )
