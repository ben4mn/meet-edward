"""
T4: _spawn_post_turn_work must return promptly even when memory
extraction / search-tag generation / reflection take a long time —
they're fire-and-forget background tasks. Without this, slow LLM
calls in post-turn would push the user-visible DONE event later.

Today's structure looks correct (asyncio.create_task on each), but
this test pins the contract so future refactors can't accidentally
re-introduce a synchronous await that blocks the response.
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_spawn_post_turn_work_returns_promptly(monkeypatch) -> None:
    """Each background service stub takes 5 seconds. _spawn_post_turn_work
    must still return in well under 1 second — it just schedules the work."""
    from services.graph import streaming

    slow_calls = {"extract": 0, "search_tag": 0, "reflection": 0, "implication": 0}

    async def slow_extract(*args, **kwargs):
        slow_calls["extract"] += 1
        await asyncio.sleep(5)
        return []

    async def slow_search_tag(*args, **kwargs):
        slow_calls["search_tag"] += 1
        await asyncio.sleep(5)

    async def slow_reflection(*args, **kwargs):
        slow_calls["reflection"] += 1
        await asyncio.sleep(5)

    async def slow_implication(*args, **kwargs):
        slow_calls["implication"] += 1
        await asyncio.sleep(5)

    # Patch every blocking call _spawn_post_turn_work touches.
    import services.memory_service
    import services.search_tag_service
    import services.reflection_service
    import services.implication_service

    monkeypatch.setattr(services.memory_service, "extract_and_store_memories", slow_extract)
    monkeypatch.setattr(services.search_tag_service, "generate_search_tags_safe", slow_search_tag)
    monkeypatch.setattr(services.reflection_service, "should_reflect", lambda *a, **k: True)
    monkeypatch.setattr(services.reflection_service, "run_reflection_safe", slow_reflection)
    monkeypatch.setattr(services.implication_service, "run_implication_safe", slow_implication)

    start = time.monotonic()
    await streaming._spawn_post_turn_work(
        messages_for_extraction=[{"role": "human", "content": "hi"}],
        conversation_id="test-conv",
        retrieved_memories=[],
        turn_count=1,
    )
    duration = time.monotonic() - start

    assert duration < 1.0, (
        f"_spawn_post_turn_work blocked the caller for {duration:.2f}s — "
        "it must just create_task() and return. Otherwise slow post-turn "
        "work delays the user-visible DONE event."
    )

    # Tasks should have at least started — sanity check that they
    # were scheduled, not silently dropped.
    await asyncio.sleep(0.05)  # let the event loop turn so create_task'd coros begin
    assert slow_calls["extract"] >= 1, "memory extraction task wasn't even scheduled"
    assert slow_calls["search_tag"] >= 1, "search-tag task wasn't even scheduled"
    assert slow_calls["reflection"] >= 1, "reflection task wasn't even scheduled"
