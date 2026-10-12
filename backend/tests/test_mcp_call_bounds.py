"""Every MCP call is bounded — in the caller and in the holder's worker
(plan 2026-09-24, C1 "silent stoppers").

The holder already gave up on the *caller's* side after 30 s, but its
worker awaited ``session.call_tool`` with no limit. One wedged apple-mcp
call therefore held the worker forever: every later call — the calendar in
every wake's pack, the pulse's signals, every chat tool — queued behind it
and timed out in turn, and nothing recovered until a restart. Now the
worker bounds the call too, fails it, and reopens the session; a call whose
caller has already given up is never sent.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, log):
        self._log = log

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(name="hang", description="", inputSchema={"type": "object"}),
            SimpleNamespace(name="fast", description="", inputSchema={"type": "object"}),
        ])

    async def call_tool(self, name, arguments):
        self._log.append(name)
        if name == "hang":
            await asyncio.sleep(3600)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"ok:{name}")])


class _Client:
    def __init__(self):
        self.calls = []
        self.opens = 0

    @asynccontextmanager
    async def session(self, name, *, auto_initialize=True):
        self.opens += 1
        yield _Session(self.calls)


async def test_a_wedged_call_does_not_poison_the_queue():
    from services.mcp_session_holder import MCPSessionHolder

    client = _Client()
    holder = MCPSessionHolder(client, "apple")
    holder._call_timeout_seconds = 0.3
    await holder.start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await holder.call_tool("hang", {})
        t0 = time.monotonic()
        out = await holder.call_tool("fast", {}, timeout=2.0)
        assert out == "ok:fast"
        assert time.monotonic() - t0 < 1.5
        assert client.opens >= 2, "a timed-out call recycles the session"
    finally:
        await holder.stop()


async def test_a_call_whose_caller_gave_up_is_never_sent():
    from services.mcp_session_holder import MCPSessionHolder

    client = _Client()
    holder = MCPSessionHolder(client, "apple")
    await holder.start()
    try:
        hung = asyncio.ensure_future(holder.call_tool("hang", {}, timeout=0.4))
        await asyncio.sleep(0.05)
        with pytest.raises(asyncio.TimeoutError):
            await holder.call_tool("fast", {}, timeout=0.1)  # queued behind the hung call
        with pytest.raises(asyncio.TimeoutError):
            await hung
        await asyncio.sleep(0.3)
        assert client.calls.count("fast") == 0, client.calls
        assert await holder.call_tool("fast", {}, timeout=2.0) == "ok:fast"
    finally:
        await holder.stop()


def test_default_timeout_comes_from_the_environment(monkeypatch):
    from services import mcp_session_holder

    monkeypatch.setenv("MCP_CALL_TIMEOUT_SECONDS", "12")
    assert mcp_session_holder.mcp_call_timeout_seconds() == 12.0
    assert mcp_session_holder.MCPSessionHolder(_Client(), "apple")._call_timeout_seconds == 12.0
    monkeypatch.setenv("MCP_CALL_TIMEOUT_SECONDS", "garbage")
    assert mcp_session_holder.mcp_call_timeout_seconds() == 30.0
    monkeypatch.delenv("MCP_CALL_TIMEOUT_SECONDS")
    assert mcp_session_holder.mcp_call_timeout_seconds() == 30.0


async def test_the_langchain_wrapper_is_bounded_and_says_what_hung():
    from services.mcp_client import _build_langchain_tools_from_holder, call_tool_bounded
    from services.mcp_session_holder import MCPSessionHolder

    holder = MCPSessionHolder(_Client(), "apple")
    holder._call_timeout_seconds = 0.2
    await holder.start()
    try:
        tools = {t.name: t for t in _build_langchain_tools_from_holder(holder)}
        with pytest.raises(asyncio.TimeoutError) as err:
            await tools["hang"].ainvoke({})
        assert "apple.hang" in str(err.value)
        # A caller can ask for a shorter bound than the default.
        t0 = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await call_tool_bounded(tools["hang"], {}, timeout=0.1)
        assert time.monotonic() - t0 < 0.5
        assert await call_tool_bounded(tools["fast"], {}, timeout=2.0) == "ok:fast"
    finally:
        await holder.stop()


async def test_call_tool_bounded_bounds_a_tool_without_a_holder():
    from services.mcp_client import call_tool_bounded

    class _Hangs:
        name = "calendar"

        async def ainvoke(self, args):
            await asyncio.sleep(3600)

    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await call_tool_bounded(_Hangs(), {}, timeout=0.1)
    assert time.monotonic() - t0 < 0.5


async def test_calendar_list_gives_up_after_its_own_short_bound(monkeypatch):
    from services import display_service

    class _Hangs:
        name = "calendar"

        async def ainvoke(self, args):
            await asyncio.sleep(3600)

    monkeypatch.setattr("services.display_service._find_calendar_tool", lambda: _Hangs())
    monkeypatch.setattr(display_service, "CALENDAR_MCP_TIMEOUT_SECONDS", 0.1)
    t0 = time.monotonic()
    assert await display_service._calendar_via_mcp() is None
    assert time.monotonic() - t0 < 0.5


def test_calendar_bound_is_ten_seconds():
    from services import display_service

    assert display_service.CALENDAR_MCP_TIMEOUT_SECONDS == 10
