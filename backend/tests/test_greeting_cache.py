"""
G3: greetings are regenerated from scratch on every page load — a
3-5s LLM call. Quick refreshes pay full cost. Add a 60s TTL cache so
repeat loads return instantly while staying fresh enough to vary.

Tests stub the LLM-call helper to count invocations.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_greeting_cached_within_ttl(monkeypatch):
    """Two calls within the TTL window: LLM invoked exactly once,
    second call returns the cached value."""
    from routers import chat as chat_router

    call_count = {"n": 0}

    async def fake_build():
        call_count["n"] += 1
        return f"Greeting #{call_count['n']}"

    chat_router._clear_greeting_cache()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", fake_build)

    g1 = await chat_router._get_or_build_greeting()
    g2 = await chat_router._get_or_build_greeting()

    assert g1 == g2 == "Greeting #1", (
        f"second call should hit cache; got g1={g1!r}, g2={g2!r}, "
        f"calls={call_count['n']}"
    )
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_stale_greeting_is_served_and_refreshed_in_the_background(monkeypatch):
    """Stale-while-revalidate. Past the soft TTL the caller still gets the
    cached line instantly (no 2s LLM wait on every open of the app); the
    refresh runs in the background and the *next* open sees the new line."""
    from routers import chat as chat_router

    call_count = {"n": 0}

    async def fake_build():
        call_count["n"] += 1
        return f"Greeting #{call_count['n']}"

    chat_router._clear_greeting_cache()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", fake_build)
    monkeypatch.setattr(chat_router, "_GREETING_CACHE_TTL_SECONDS", 0.05)

    first = await chat_router._get_or_build_greeting()
    await asyncio.sleep(0.1)  # exceed the soft TTL
    second = await chat_router._get_or_build_greeting()
    assert first == second == "Greeting #1", "stale line is served at once"

    await chat_router._await_greeting_refresh()
    third = await chat_router._get_or_build_greeting()
    assert third == "Greeting #2"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_one_background_refresh_at_a_time(monkeypatch):
    from routers import chat as chat_router

    call_count = {"n": 0}

    async def fake_build():
        call_count["n"] += 1
        await asyncio.sleep(0.05)
        return f"Greeting #{call_count['n']}"

    chat_router._clear_greeting_cache()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", fake_build)
    monkeypatch.setattr(chat_router, "_GREETING_CACHE_TTL_SECONDS", 0.01)

    await chat_router._get_or_build_greeting()
    await asyncio.sleep(0.03)
    # Three opens in quick succession while stale: one refresh, not three.
    await chat_router._get_or_build_greeting()
    await chat_router._get_or_build_greeting()
    await chat_router._get_or_build_greeting()
    await chat_router._await_greeting_refresh()
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_very_old_greeting_is_regenerated_synchronously(monkeypatch):
    """Past the hard TTL the line is too old to show (wrong time of day);
    wait for a fresh one."""
    from routers import chat as chat_router

    call_count = {"n": 0}

    async def fake_build():
        call_count["n"] += 1
        return f"Greeting #{call_count['n']}"

    chat_router._clear_greeting_cache()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", fake_build)
    monkeypatch.setattr(chat_router, "_GREETING_CACHE_TTL_SECONDS", 0.01)
    monkeypatch.setattr(chat_router, "_GREETING_CACHE_HARD_TTL_SECONDS", 0.05)

    first = await chat_router._get_or_build_greeting()
    await asyncio.sleep(0.1)
    second = await chat_router._get_or_build_greeting()
    assert first == "Greeting #1"
    assert second == "Greeting #2"


@pytest.mark.asyncio
async def test_clear_cache_drops_entry(monkeypatch):
    from routers import chat as chat_router

    call_count = {"n": 0}

    async def fake_build():
        call_count["n"] += 1
        return f"Greeting #{call_count['n']}"

    chat_router._clear_greeting_cache()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", fake_build)

    await chat_router._get_or_build_greeting()
    chat_router._clear_greeting_cache()
    await chat_router._get_or_build_greeting()

    assert call_count["n"] == 2, "explicit clear must invalidate the cache"


# ---------------------------------------------------------------------------
# ?fresh=1 (review 2026-09-26, greeting-stale-after-action): Today asks for a
# fresh line about 800 ms after Ellis resolves a need. A cached line older
# than ~20 s is rebuilt instead of served from the 60 s soft cache; at most
# one forced rebuild per 20 s, so a run of taps costs one LLM call.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_rebuilds_a_line_older_than_twenty_seconds(monkeypatch):
    import time as _time
    from routers import chat as chat_router

    calls = {"n": 0}

    async def fake_build():
        calls["n"] += 1
        return f"Greeting #{calls['n']}"

    chat_router._clear_greeting_cache()
    chat_router._reset_fresh_greeting_gate()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", fake_build)

    assert await chat_router._get_or_build_greeting() == "Greeting #1"
    # A tap right away: the line is seconds old, served as it is.
    assert await chat_router._get_or_build_greeting(fresh=True) == "Greeting #1"
    assert calls["n"] == 1
    # 25 s later (still inside the 60 s soft cache): fresh rebuilds at once.
    chat_router._greeting_cache["timestamp"] = _time.time() - 25
    assert await chat_router._get_or_build_greeting(fresh=True) == "Greeting #2"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_fresh_rebuilds_at_most_once_per_twenty_seconds(monkeypatch):
    import time as _time
    from routers import chat as chat_router

    calls = {"n": 0}

    async def failing_build():
        calls["n"] += 1
        raise RuntimeError("model busy")

    chat_router._clear_greeting_cache()
    chat_router._reset_fresh_greeting_gate()
    chat_router._greeting_cache["value"] = "Old line"
    chat_router._greeting_cache["timestamp"] = _time.time() - 40
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", failing_build)

    # The rebuild fails: the old line stays, and the next taps do not retry.
    assert await chat_router._get_or_build_greeting(fresh=True) == "Old line"
    assert await chat_router._get_or_build_greeting(fresh=True) == "Old line"
    assert await chat_router._get_or_build_greeting(fresh=True) == "Old line"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_the_stream_honours_fresh(monkeypatch):
    import json as _json
    import time as _time
    from routers import chat as chat_router

    async def fake_astream():
        for piece in ["Biscuit's", " all set."]:
            yield piece

    chat_router._clear_greeting_cache()
    chat_router._reset_fresh_greeting_gate()
    chat_router._greeting_cache["value"] = "Biscuit's 6 o'clock meds are waiting."
    chat_router._greeting_cache["timestamp"] = _time.time() - 30
    monkeypatch.setattr(chat_router, "_astream_fresh_greeting", fake_astream)

    cached = [c async for c in chat_router._stream_greeting()]
    assert any("meds are waiting" in c for c in cached), "without fresh, the cached line"

    chunks = []
    async for raw in chat_router._stream_greeting(fresh=True):
        if raw.startswith("data: {"):
            chunks.append(_json.loads(raw[6:].strip())["chunk"])
    assert chunks == ["Biscuit's", " all set."]
    assert chat_router._greeting_cache["value"] == "Biscuit's all set."
