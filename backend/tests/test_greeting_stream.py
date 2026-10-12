"""
G5: when the cache is cold, the user still waits 3-5s for a sudden
full-sentence greeting. Stream it via SSE so the first character
lands in <500ms even on a true cache miss.

The streaming endpoint reuses the cache from G3 — a cache hit returns
the full text in one chunk (no LLM call). A miss runs _astream and
relays each delta as an SSE data: event.
"""

from __future__ import annotations

import json

import pytest


async def _consume_sse(stream) -> list[dict]:
    """Read an iterable async generator of SSE bytes/strings into a list
    of parsed event dicts (skipping [DONE])."""
    parsed = []
    buffer = ""
    async for chunk in stream:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return parsed
                try:
                    parsed.append(json.loads(data))
                except json.JSONDecodeError:
                    continue
    return parsed


@pytest.mark.asyncio
async def test_stream_emits_one_chunk_per_delta(monkeypatch):
    from routers import chat as chat_router

    chat_router._clear_greeting_cache()

    async def fake_astream():
        for piece in ["Hey", " ", "Ellis", "."]:
            yield piece

    monkeypatch.setattr(chat_router, "_astream_fresh_greeting", fake_astream)

    body = await _consume_sse(chat_router._stream_greeting())

    chunks = [e.get("chunk") for e in body if "chunk" in e]
    assert chunks == ["Hey", " ", "Ellis", "."], (
        f"expected one event per delta, got {body}"
    )


@pytest.mark.asyncio
async def test_stream_cache_hit_returns_one_chunk_no_llm(monkeypatch):
    """When the cache is fresh, no LLM/astream call — single chunk
    with the cached text."""
    from routers import chat as chat_router

    chat_router._clear_greeting_cache()
    chat_router._greeting_cache["timestamp"] = __import__("time").time()
    chat_router._greeting_cache["value"] = "Yeah, hi."

    astream_calls = {"n": 0}

    async def should_not_fire():
        astream_calls["n"] += 1
        yield "should not appear"

    monkeypatch.setattr(chat_router, "_astream_fresh_greeting", should_not_fire)

    body = await _consume_sse(chat_router._stream_greeting())

    chunks = [e["chunk"] for e in body if "chunk" in e]
    assert chunks == ["Yeah, hi."], f"cache hit must return cached text, got {body}"
    assert astream_calls["n"] == 0, "cache hit must not trigger LLM"


@pytest.mark.asyncio
async def test_stream_populates_cache_after_miss(monkeypatch):
    from routers import chat as chat_router

    chat_router._clear_greeting_cache()

    async def fake_astream():
        for piece in ["Hello", " ", "world", "."]:
            yield piece

    monkeypatch.setattr(chat_router, "_astream_fresh_greeting", fake_astream)

    await _consume_sse(chat_router._stream_greeting())

    assert chat_router._greeting_cache["value"] == "Hello world.", (
        f"cache should be populated after stream completes, got "
        f"{chat_router._greeting_cache['value']!r}"
    )
