"""
Test for pre-LLM retrieval fan-out parallelization.

Before the fix, `stream_with_memory_events` serially awaits seven
independent context-assembly calls (entity memories, enrichments,
documents, heartbeat briefing, session briefing, open loops, tool
binding). The fix wraps them in `asyncio.gather` so they run
concurrently.

Rather than exercising the full streaming pipeline (which has many
dependencies), we verify the helper that does the gather is exposed and
that the gathered calls actually overlap.
"""

import asyncio
import time

import pytest

from services.graph import streaming


async def test_collect_llm_context_runs_fetches_concurrently(monkeypatch):
    """All fan-out fetches must run concurrently.

    Each stubbed dependency sleeps 100ms. Serial execution would take
    ~700ms; parallel execution should complete in ~100-200ms.
    """
    assert hasattr(streaming, "_collect_llm_context"), (
        "streaming.py must expose _collect_llm_context() — a helper that "
        "parallelizes the pre-LLM retrieval fan-out"
    )

    # Stub the 7 fan-out dependencies with uniform sleeps.
    async def slow_entity(*_a, **_kw):
        await asyncio.sleep(0.1)
        return []

    async def slow_enrichments(*_a, **_kw):
        await asyncio.sleep(0.1)
        return []

    async def slow_relevant_docs(*_a, **_kw):
        await asyncio.sleep(0.1)
        return []

    async def slow_state_docs(*_a, **_kw):
        await asyncio.sleep(0.1)
        return []

    async def slow_briefing(*_a, **_kw):
        await asyncio.sleep(0.1)
        return None

    async def slow_open_loops(*_a, **_kw):
        await asyncio.sleep(0.1)
        return []

    async def slow_tools(*_a, **_kw):
        await asyncio.sleep(0.1)
        return []

    import services.memory_service as memory_service
    import services.reflection_service as reflection_service
    import services.document_service as document_service
    import services.heartbeat.heartbeat_service as heartbeat_service
    import services.open_loop_service as open_loop_service

    monkeypatch.setattr(memory_service, "retrieve_entity_memories", slow_entity)
    monkeypatch.setattr(reflection_service, "load_enrichments", slow_enrichments)
    monkeypatch.setattr(document_service, "retrieve_relevant_documents", slow_relevant_docs)
    monkeypatch.setattr(document_service, "get_state_documents", slow_state_docs)
    monkeypatch.setattr(heartbeat_service, "get_pending_briefing", slow_briefing)
    monkeypatch.setattr(open_loop_service, "get_active_open_loops", slow_open_loops)
    monkeypatch.setattr(streaming, "get_available_tools", slow_tools)

    start = time.monotonic()
    ctx = await streaming._collect_llm_context(
        message="hello",
        conversation_id="c1",
        turn_count=5,  # skip session briefing
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.35, (
        f"Fan-out must run in parallel; took {elapsed:.3f}s (serial would be ~0.7s)"
    )
    assert isinstance(ctx, dict), "Helper must return a dict bundle"
    for key in (
        "entity_memories",
        "enriched_memories",
        "relevant_documents",
        "state_documents",
        "briefing",
        "open_loops",
        "tools",
    ):
        assert key in ctx, f"Context bundle missing key '{key}'"
