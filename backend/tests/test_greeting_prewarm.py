"""
G4: pre-warm the greeting cache at backend startup so the first
chat-page load after a bounce is instant. Without this, the very
first user request after restart still pays the full cold-build cost
(~3-5s) — defeats the purpose of G3 for the most painful case.

The seam: a pre_warm_greeting_cache() coroutine that calls into the
existing _get_or_build_greeting helper. main.py's lifespan kicks it
off via asyncio.create_task during startup so it runs concurrently
with the rest of init and never blocks boot.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_pre_warm_populates_cache(monkeypatch):
    from routers import chat as chat_router

    chat_router._clear_greeting_cache()
    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", _fake_build("warmed"))

    await chat_router.pre_warm_greeting_cache()

    # Second call should hit the cache (no extra LLM invocation)
    monkeypatch.setattr(
        chat_router,
        "_generate_fresh_greeting",
        _fake_build("should-not-fire"),
    )
    second = await chat_router._get_or_build_greeting()
    assert second == "warmed", (
        f"pre-warm should populate the cache; second call should hit it. "
        f"Got {second!r}."
    )


@pytest.mark.asyncio
async def test_pre_warm_swallows_exceptions(monkeypatch, caplog):
    """A failing build must not propagate — startup mustn't break."""
    from routers import chat as chat_router

    chat_router._clear_greeting_cache()

    async def boom():
        raise RuntimeError("simulated cold-start blow-up")

    monkeypatch.setattr(chat_router, "_generate_fresh_greeting", boom)

    # Must not raise
    await chat_router.pre_warm_greeting_cache()

    # Cache stays empty so the next real user request will retry
    assert chat_router._greeting_cache["value"] is None


def _fake_build(value: str):
    async def _fake():
        return value
    return _fake
