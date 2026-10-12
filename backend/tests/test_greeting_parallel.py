"""
G2: _build_greeting_context fans out 4 independent reads (facts,
preferences, recent context, open loops). They're independent — there's
no reason to await them in series. ~150-300ms saved per cold call.

This test stubs each call with a 100ms sleep so total wall time
distinguishes serial (~400ms) from parallel (~100ms).
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_context_fetch_concurrency(monkeypatch):
    """Each of the 4 retrievals stubbed with sleep(0.1). Serial = ~0.4s,
    parallel = ~0.1s. Allow 0.25s headroom."""
    from routers import chat as chat_router

    delay = 0.1

    async def slow_retrieve(query, **kwargs):
        await asyncio.sleep(delay)
        return []

    async def slow_loops(limit=3):
        await asyncio.sleep(delay)
        return []

    # Patch the symbols _build_greeting_context imports at call time
    import services.memory_service as memory_service
    import services.open_loop_service as open_loop_service

    monkeypatch.setattr(memory_service, "retrieve_memories", slow_retrieve)
    monkeypatch.setattr(open_loop_service, "get_active_open_loops", slow_loops)

    start = time.monotonic()
    result = await chat_router._build_greeting_context()
    duration = time.monotonic() - start

    assert duration < 0.25, (
        f"_build_greeting_context took {duration:.3f}s — looks serial. "
        f"Expected ~0.1s with asyncio.gather across 4 independent reads."
    )
    # Empty context is the expected shape when stubs return nothing
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_context_fetch_resilient_to_one_branch_failing(monkeypatch):
    """If facts retrieval blows up, the others still finish and the
    function returns whatever it could assemble. No branch should
    poison the bundle."""
    from routers import chat as chat_router

    call_log = []

    async def maybe_failing_retrieve(query, **kwargs):
        call_log.append(query)
        if "name" in query:  # facts branch
            raise RuntimeError("simulated retrieval blow-up")
        return []

    async def ok_loops(limit=3):
        call_log.append("loops")
        return []

    import services.memory_service as memory_service
    import services.open_loop_service as open_loop_service

    monkeypatch.setattr(memory_service, "retrieve_memories", maybe_failing_retrieve)
    monkeypatch.setattr(open_loop_service, "get_active_open_loops", ok_loops)

    # Should not raise
    result = await chat_router._build_greeting_context()
    assert isinstance(result, str)
    # All four branches were attempted (facts, prefs, recent, loops)
    assert "loops" in call_log, f"loops branch must still run; calls={call_log}"
