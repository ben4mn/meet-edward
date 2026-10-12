"""
N5: a hung apple-mcp tool (e.g. Mail.app failing to launch with the
default 120s AppleEvent timeout) should NOT stall Edward's chat for
2+ minutes — long enough that the upstream proxy cuts the SSE stream
and the user sees a generic error.

MCPSessionHolder.call_tool now wraps the underlying session.call_tool
in asyncio.wait_for with a per-call default timeout. On timeout, the
caller gets a clear error string so the LLM can recover within the
SSE stream's window.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


class _SlowSession:
    def __init__(self, sleep_seconds: float):
        self._sleep = sleep_seconds

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(name="mail", description="", inputSchema={"type": "object"}),
        ])

    async def call_tool(self, name, arguments):
        await asyncio.sleep(self._sleep)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="late")],
            isError=False,
        )


class _SlowClient:
    def __init__(self, sleep_seconds: float):
        self._sleep = sleep_seconds

    @asynccontextmanager
    async def session(self, name, *, auto_initialize=True):
        yield _SlowSession(self._sleep)


@pytest.mark.asyncio
async def test_call_tool_times_out_when_session_hangs() -> None:
    """A 60s-sleeping session.call_tool, holder timeout 0.2s — must
    bail in well under a second."""
    from services.mcp_session_holder import MCPSessionHolder

    holder = MCPSessionHolder(_SlowClient(60), "apple")
    holder._call_timeout_seconds = 0.2  # type: ignore[attr-defined]
    await holder.start()

    t0 = time.monotonic()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await holder.call_tool("mail", {})
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, (
            f"hung tool call should bail near the timeout, took {elapsed:.2f}s"
        )
    finally:
        await holder.stop()


@pytest.mark.asyncio
async def test_default_timeout_is_30_seconds() -> None:
    """Sanity check the default — 30s is short enough to recover
    inside the SSE window, long enough that legit tool calls don't
    get falsely cut."""
    from services.mcp_session_holder import MCPSessionHolder

    holder = MCPSessionHolder(_SlowClient(0.01), "apple")
    assert holder._call_timeout_seconds == 30.0, (  # type: ignore[attr-defined]
        f"default per-call timeout should be 30s, got {holder._call_timeout_seconds}"  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_fast_call_succeeds() -> None:
    """A normal-fast tool call (10ms) goes through unchanged."""
    from services.mcp_session_holder import MCPSessionHolder

    holder = MCPSessionHolder(_SlowClient(0.01), "apple")
    holder._call_timeout_seconds = 1.0  # type: ignore[attr-defined]
    await holder.start()

    try:
        result = await holder.call_tool("mail", {})
        assert "late" in str(result), f"got {result!r}"
    finally:
        await holder.stop()
