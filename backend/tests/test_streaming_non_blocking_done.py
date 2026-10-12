"""
Tests for non-blocking post-turn work.

Current bug: `stream_with_memory_events` awaits `extract_and_store_memories`
(with a 30s timeout) before yielding the `DONE` event. This adds 500-2000ms
to the perceived end-of-response on every turn.

Fix: extract the post-turn bundle into a helper that spawns extraction (and
its dependent implication task), search-tag generation, and reflection as
background tasks. The helper must return quickly so the generator can emit
DONE immediately. Extraction continues in the background.
"""

import asyncio
import time

import pytest

from services.graph import streaming


@pytest.fixture(autouse=True)
def _quiet_other_learners(monkeypatch):
    """These tests are about extraction. The helper also fires search tags
    and reflection, which build a real background LLM: until conftest's
    guard (2026-09-27) the search-tag call here sent a real Codex request
    whenever it ran before the test's loop closed."""
    import services.reflection_service as reflection_service
    import services.search_tag_service as search_tag_service

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(search_tag_service, "generate_search_tags_safe", _noop)
    monkeypatch.setattr(reflection_service, "run_reflection_safe", _noop)


async def test_spawn_post_turn_work_returns_before_extraction_completes(monkeypatch):
    """The post-turn helper must schedule extraction, not await it.

    RED: the helper `_spawn_post_turn_work` doesn't exist yet, and the
    inline block in `stream_with_memory_events` awaits extraction (with a
    30s timeout) before yielding DONE.
    """
    extraction_started = asyncio.Event()
    extraction_finished = asyncio.Event()

    async def slow_extract(**kwargs):
        extraction_started.set()
        await asyncio.sleep(1.5)
        extraction_finished.set()
        return []

    # Patch the import the helper uses.
    import services.memory_service as memory_service
    monkeypatch.setattr(memory_service, "extract_and_store_memories", slow_extract)

    assert hasattr(streaming, "_spawn_post_turn_work"), (
        "streaming.py must expose _spawn_post_turn_work() — a helper that "
        "schedules the post-turn bundle without blocking on extraction"
    )

    start = time.monotonic()
    await streaming._spawn_post_turn_work(
        messages_for_extraction=[{"role": "user", "content": "hi"}],
        conversation_id="test-conv",
        retrieved_memories=[],
        turn_count=1,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.2, (
        f"_spawn_post_turn_work must return promptly (scheduling, not awaiting); "
        f"took {elapsed:.3f}s"
    )
    assert not extraction_finished.is_set(), (
        "Extraction must still be running in the background after the helper returns"
    )

    # Give the background task a chance to complete so pytest doesn't warn
    # about a pending task on event-loop teardown.
    try:
        await asyncio.wait_for(extraction_finished.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        pass


async def test_spawn_post_turn_work_chains_implication_after_extraction(monkeypatch):
    """When extraction yields stored memories, implication analysis must run.

    The fire-and-forget implication task depends on the extraction output
    (needs stored_ids). That dependency must be preserved by chaining the
    implication call inside the extraction task's continuation.
    """

    class _FakeMemory:
        def __init__(self, mid):
            self.id = mid

    async def fake_extract(**kwargs):
        await asyncio.sleep(0.05)
        return [_FakeMemory("m1"), _FakeMemory("m2")]

    import services.memory_service as memory_service
    monkeypatch.setattr(memory_service, "extract_and_store_memories", fake_extract)

    implication_calls = []

    async def fake_implication(conversation_id, msgs, stored_ids):
        implication_calls.append(stored_ids)

    import services.implication_service as impl_mod
    monkeypatch.setattr(impl_mod, "run_implication_safe", fake_implication)

    await streaming._spawn_post_turn_work(
        messages_for_extraction=[{"role": "user", "content": "foo"}],
        conversation_id="test-conv",
        retrieved_memories=[],
        turn_count=1,
    )

    # Wait for the chained implication task to fire.
    for _ in range(50):
        if implication_calls:
            break
        await asyncio.sleep(0.05)

    assert implication_calls == [["m1", "m2"]], (
        "Implication analysis must receive stored memory IDs from extraction"
    )
