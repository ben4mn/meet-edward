"""
N9: SSE heartbeat. Edward's chat sometimes goes silent for 30+ seconds
between LLM calls (waiting on tool execution, then waiting on the LLM
to compose its next move). Cloudflared / Next.js cut the connection
on extended silence and the frontend shows a generic error.

The fix: when the inner stream goes quiet, emit SSE comment lines
(": heartbeat\\n\\n"). These are valid SSE syntax — invisible to the
JSON-parsing client but visible to proxies, which treat them as
liveness signals.

This test pins the wrapper helper that interleaves events with
heartbeats.
"""

from __future__ import annotations

import asyncio

import pytest


async def _slow_event_stream():
    """Yields one event after a 0.6s pause — long enough that with
    a 0.2s heartbeat interval we should see ~2-3 heartbeats first."""
    await asyncio.sleep(0.6)
    yield {"type": "content", "content": "ok"}


async def _empty_stream():
    """Stream that has no events — pure idle."""
    if False:
        yield  # generator marker
    await asyncio.sleep(0.4)


@pytest.mark.asyncio
async def test_heartbeat_emitted_during_idle() -> None:
    """A 0.6s-quiet stream with 0.2s heartbeat interval must produce
    >=2 heartbeat comments before the eventual event."""
    from routers.chat import _yield_with_heartbeats

    out = []
    async for chunk in _yield_with_heartbeats(_slow_event_stream(), heartbeat_interval=0.2):
        out.append(chunk)

    heartbeats = [c for c in out if c.startswith(":")]
    events = [c for c in out if c.startswith("data:")]

    assert len(heartbeats) >= 2, (
        f"expected >=2 heartbeats during a 0.6s idle, got {len(heartbeats)}: {out}"
    )
    assert len(events) == 1, f"expected one real event at the end, got: {out}"
    assert "ok" in events[0]


@pytest.mark.asyncio
async def test_heartbeat_skipped_when_events_flow() -> None:
    """If real events stream every 50ms, the heartbeat (200ms interval)
    should NEVER fire — fast streams must not emit useless heartbeats."""
    from routers.chat import _yield_with_heartbeats

    async def fast_stream():
        for i in range(8):
            await asyncio.sleep(0.05)
            yield {"type": "content", "content": f"chunk-{i}"}

    out = []
    async for chunk in _yield_with_heartbeats(fast_stream(), heartbeat_interval=0.2):
        out.append(chunk)

    heartbeats = [c for c in out if c.startswith(":")]
    events = [c for c in out if c.startswith("data:")]

    assert len(events) == 8
    assert len(heartbeats) == 0, (
        f"no heartbeats expected when events flow faster than the interval, "
        f"got {len(heartbeats)}: {heartbeats}"
    )


@pytest.mark.asyncio
async def test_heartbeat_emits_valid_sse_comment_format() -> None:
    """Heartbeat must be a valid SSE comment line — ': <text>\\n\\n'."""
    from routers.chat import _yield_with_heartbeats

    out = []
    async for chunk in _yield_with_heartbeats(_slow_event_stream(), heartbeat_interval=0.2):
        out.append(chunk)

    for hb in (c for c in out if c.startswith(":")):
        assert hb.startswith(": "), f"heartbeat must start with ': ', got {hb!r}"
        assert hb.endswith("\n\n"), f"heartbeat must end with double newline, got {hb!r}"


@pytest.mark.asyncio
async def test_heartbeat_preserves_contextvar_across_subtasks() -> None:
    """Regression: N9 spawns asyncio.ensure_future per __anext__,
    which copies the parent's context. If callers set ContextVars
    AFTER iterating begins, those values get lost across subtask
    boundaries. The fix is to set ContextVars at the CALLER level
    BEFORE _yield_with_heartbeats runs — this test pins that the
    helper preserves the parent's context faithfully so that pattern
    works."""
    import contextvars
    from routers.chat import _yield_with_heartbeats

    test_var: contextvars.ContextVar = contextvars.ContextVar(
        "test_var_for_heartbeat", default=None
    )

    seen_in_generator: list = []

    async def gen_that_reads_contextvar():
        await asyncio.sleep(0.3)  # force at least one heartbeat
        seen_in_generator.append(test_var.get())
        yield {"type": "content", "content": "ok"}
        await asyncio.sleep(0.3)
        seen_in_generator.append(test_var.get())
        yield {"type": "content", "content": "ok2"}

    test_var.set("conv-12345")
    out = []
    async for chunk in _yield_with_heartbeats(gen_that_reads_contextvar(), heartbeat_interval=0.1):
        out.append(chunk)

    assert seen_in_generator == ["conv-12345", "conv-12345"], (
        f"Generator must see the parent's ContextVar value across "
        f"all yields, even when heartbeat-spawned subtasks intervene. "
        f"Got: {seen_in_generator}"
    )
