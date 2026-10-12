"""
Pin that /api/chat actually emits SSE frames to the wire as the inner
generator yields them — i.e., it does NOT collect into a list and dump
at the end.

Today the underlying streaming machinery does the right thing (token
events flow through ``_yield_with_heartbeats`` which yields per event).
This test catches future regressions where someone wraps the response
generator in ``list(...)``, awaits ``response.text()`` upstream, or
otherwise coalesces frames before they hit the network.

Strategy: stub ``stream_with_memory_events`` so it yields N content
events with a deliberate sleep between them. Then drive the FastAPI
app through a hand-rolled ASGI scope and record the wall-clock
arrival time of every ``http.response.body`` send. We can't use
``httpx.ASGITransport`` for this because it accumulates body chunks
into a single response object before yielding via aiter_raw, hiding
exactly the framing we want to verify.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import pytest
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_chat_response_sends_each_chunk_distinctly(monkeypatch) -> None:
    """5 content events with 80 ms gaps must produce >=4 distinct
    ``http.response.body`` sends, each at least 30 ms apart. If they
    arrive in one burst at the end, the response is being buffered
    somewhere in the ASGI pipeline — the user-visible "one big payload"
    bug."""

    GAP_MS = 80
    NUM_EVENTS = 5

    class _Settings:
        system_prompt = "test"
        model = "gpt-5.4"
        temperature = 0.7
        provider = "openai"

    async def _fake_get_settings():
        return _Settings()

    async def _fake_get_graph():
        return object()

    async def _fake_create_conversation(cid, title):
        return None

    async def _fake_conversation_exists(cid):
        return True

    async def _fake_increment_message_count(cid):
        return None

    async def _slow_stream(*args, **kwargs) -> AsyncIterator[dict]:
        cid = kwargs.get("conversation_id", "x")
        for i in range(NUM_EVENTS):
            await asyncio.sleep(GAP_MS / 1000)
            yield {"type": "content", "conversation_id": cid, "content": f"chunk-{i}"}
        yield {"type": "done", "conversation_id": cid}

    monkeypatch.setattr("routers.chat.get_settings", _fake_get_settings)
    monkeypatch.setattr("routers.chat.get_graph", _fake_get_graph)
    monkeypatch.setattr("routers.chat.create_conversation", _fake_create_conversation)
    monkeypatch.setattr("routers.chat.conversation_exists", _fake_conversation_exists)
    monkeypatch.setattr("routers.chat.increment_message_count", _fake_increment_message_count)
    monkeypatch.setattr("routers.chat.stream_with_memory_events", _slow_stream)

    import services.heartbeat.heartbeat_service as hb
    monkeypatch.setattr(hb, "register_active_chat", lambda cid: None)
    monkeypatch.setattr(hb, "unregister_active_chat", lambda cid: None)

    from routers.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router, prefix="/api")

    # Build an ASGI scope manually so we can record per-`send` timestamps.
    body = b'{"message": "hi"}'
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }

    request_done = False

    async def receive():
        nonlocal request_done
        if not request_done:
            request_done = True
            return {"type": "http.request", "body": body, "more_body": False}
        # No more incoming data — wait forever (the response should still flush).
        await asyncio.Event().wait()

    sent_events: list[tuple[float, dict]] = []
    t0 = time.monotonic()

    async def send(msg):
        sent_events.append((time.monotonic() - t0, msg))

    await app(scope, receive, send)

    # Filter the body sends — they're what would hit the wire.
    body_sends = [
        (t, m) for (t, m) in sent_events
        if m.get("type") == "http.response.body"
    ]

    # Skip empty trailing sends (Starlette emits a final `more_body: False`
    # with empty body to close the response). We care about the ones with
    # actual SSE payload.
    nonempty_body_times = [t for (t, m) in body_sends if m.get("body")]

    distinct_clusters = 0
    last = -1.0
    for t in nonempty_body_times:
        if (t - last) * 1000 >= 30:
            distinct_clusters += 1
            last = t

    assert distinct_clusters >= 4, (
        f"Expected at least 4 distinct ASGI body sends separated by "
        f">=30 ms (out of {NUM_EVENTS} events emitted with {GAP_MS} ms "
        f"gaps), got {distinct_clusters}. Send times (s): "
        f"{nonempty_body_times}. This means the response is being "
        f"buffered upstream of the wire — the user sees the whole reply "
        f"land as one chunk at the end."
    )

    # Also assert the spread is roughly what we'd expect.
    if nonempty_body_times:
        spread_ms = (nonempty_body_times[-1] - nonempty_body_times[0]) * 1000
        expected_min_spread = GAP_MS * (NUM_EVENTS - 1) * 0.5
        assert spread_ms >= expected_min_spread, (
            f"Total spread between first and last body send was "
            f"{spread_ms:.1f} ms; expected at least "
            f"{expected_min_spread:.1f} ms. Buffering suspected."
        )
