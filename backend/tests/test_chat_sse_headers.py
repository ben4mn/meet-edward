"""
Pin the SSE response headers on /api/chat so cloudflared and Next.js
don't coalesce per-token frames into one big chunk.

The streaming pipeline is correct end-to-end (astream → SSE event per
chunk → data: ... \\n\\n per yield). What was missing: the response
didn't tell intermediaries "do not buffer". The canonical hint is
``X-Accel-Buffering: no`` (used by sse_starlette and Edward's own
orchestrator router at backend/routers/orchestrator.py:171). Without
it the bubble snaps in fully formed at the end of generation.

This test wires a minimal FastAPI app with the chat router, stubs out
the LLM/graph/db side effects, and just inspects the response headers.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_test_app(monkeypatch) -> FastAPI:
    """Mount the real chat router but stub every external boundary so
    the request returns immediately with valid SSE framing. We're only
    interested in the response headers — we don't consume the body."""

    # Stub the graph + settings so we don't need DB / OpenAI.
    class _Settings:
        system_prompt = "test"
        model = "gpt-5.4"
        temperature = 0.7
        provider = "openai"

    async def _fake_get_settings():
        return _Settings()

    async def _fake_get_graph():
        return object()  # opaque — only stream_with_memory_events touches it

    async def _fake_create_conversation(cid, title):
        return None

    async def _fake_conversation_exists(cid):
        return True

    async def _fake_increment_message_count(cid):
        return None

    async def _fake_stream_events(*args, **kwargs) -> AsyncIterator[dict]:
        # One CONTENT event then DONE so the response actually closes.
        yield {"type": "content", "conversation_id": kwargs.get("conversation_id", "x"), "content": "hi"}
        yield {"type": "done", "conversation_id": kwargs.get("conversation_id", "x")}

    monkeypatch.setattr("routers.chat.get_settings", _fake_get_settings)
    monkeypatch.setattr("routers.chat.get_graph", _fake_get_graph)
    monkeypatch.setattr("routers.chat.create_conversation", _fake_create_conversation)
    monkeypatch.setattr("routers.chat.conversation_exists", _fake_conversation_exists)
    monkeypatch.setattr("routers.chat.increment_message_count", _fake_increment_message_count)
    monkeypatch.setattr("routers.chat.stream_with_memory_events", _fake_stream_events)

    # heartbeat side effects: register/unregister are imported lazily inside
    # generate(); stub the module-level functions so they no-op.
    import services.heartbeat.heartbeat_service as hb
    monkeypatch.setattr(hb, "register_active_chat", lambda cid: None)
    monkeypatch.setattr(hb, "unregister_active_chat", lambda cid: None)

    # ContextVar setter is a real function — fine to leave as-is.

    from routers.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    return app


def test_chat_response_sets_no_buffering_headers(monkeypatch) -> None:
    """The /api/chat SSE response MUST include x-accel-buffering: no
    and cache-control: no-cache, no-transform so cloudflared and Next.js
    forward each per-token frame to the browser as it's produced.

    Today only ``cache-control: no-cache`` is set — the user-visible
    symptom is the assistant message snapping in as one late chunk."""
    app = _build_test_app(monkeypatch)
    client = TestClient(app)

    with client.stream("POST", "/api/chat", json={"message": "hi"}) as resp:
        assert resp.status_code == 200, f"chat endpoint failed: {resp.status_code}"

        # Lowercase the header keys — Starlette normalizes to lowercase.
        headers = {k.lower(): v for k, v in resp.headers.items()}

        assert headers.get("content-type", "").startswith("text/event-stream"), (
            f"expected text/event-stream content-type, got {headers.get('content-type')!r}"
        )

        accel = headers.get("x-accel-buffering")
        assert accel == "no", (
            "SSE response is missing X-Accel-Buffering: no — without this "
            "cloudflared/Next.js coalesce per-token frames into one big "
            f"final chunk. Headers were: {headers}"
        )

        cc = (headers.get("cache-control") or "").lower()
        assert "no-cache" in cc and "no-transform" in cc, (
            "Cache-Control should be 'no-cache, no-transform' to prevent "
            "intermediaries from rewriting/buffering the SSE stream. "
            f"Got: {cc!r}"
        )

        # Drain the body so the stream context manager exits cleanly.
        for _ in resp.iter_raw():
            pass


def test_greeting_stream_sets_no_buffering_headers(monkeypatch) -> None:
    """Same buffering pathology, same fix on the greeting SSE route."""
    # Pre-populate the in-process greeting cache so the endpoint takes
    # the cache-hit branch (single yield + DONE) and never touches an
    # LLM. The cache TTL is 60s — way more than this test needs.
    import time as _time
    import routers.chat as chat_mod
    chat_mod._greeting_cache["value"] = "hey"
    chat_mod._greeting_cache["timestamp"] = _time.time()

    from routers.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    client = TestClient(app)

    with client.stream("GET", "/api/greeting/stream") as resp:
        assert resp.status_code == 200, f"greeting/stream failed: {resp.status_code}"

        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert headers.get("x-accel-buffering") == "no", (
            "Greeting SSE response missing X-Accel-Buffering: no. "
            f"Headers were: {headers}"
        )

        for _ in resp.iter_raw():
            pass
