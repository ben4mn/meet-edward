"""
T1 — MCPSessionHolder keeps a single MCP subprocess session open
across many tool calls, instead of letting MultiServerMCPClient.get_tools()
spawn a fresh `bun run index.ts` subprocess per call (~5s cold start each).

Real impact in production: apple-mcp logged 8483 restarts in one day —
that's 8483 cold starts that should have been ~1. Trip-planning chats
that use 7 calendar tools spent 35s in MCP overhead alone.

After T1 each tool call reuses the open session; throughput goes from
~5s/call to ~50ms/call.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _FakeClientSession:
    """Mimics mcp.ClientSession's call_tool/list_tools surface."""

    def __init__(self, tools):
        self._tools = tools
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=t["name"],
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {"type": "object", "properties": {}}),
                )
                for t in self._tools
            ]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=f"ok:{name}")],
            isError=False,
        )


class _FakeMCPClient:
    """Mimics MultiServerMCPClient.session(name) — counts opens so we
    can verify the holder reuses a single session."""

    def __init__(self, tools):
        self._tools = tools
        self.open_count = 0
        self.close_count = 0

    @asynccontextmanager
    async def session(self, server_name, *, auto_initialize=True):
        self.open_count += 1
        sess = _FakeClientSession(self._tools)
        try:
            yield sess
        finally:
            self.close_count += 1


@pytest.mark.asyncio
async def test_holder_reuses_single_session_across_many_calls() -> None:
    from services.mcp_session_holder import MCPSessionHolder

    client = _FakeMCPClient([
        {"name": "calendar", "description": "cal"},
        {"name": "notes", "description": "notes"},
    ])
    holder = MCPSessionHolder(client, "apple")
    await holder.start()

    try:
        for i in range(5):
            result = await holder.call_tool("calendar", {"op": "list", "i": i})
            assert "ok:calendar" in str(result)

        assert client.open_count == 1, (
            f"5 tool calls should share ONE session, not {client.open_count} — "
            "this is the whole point of the holder"
        )
    finally:
        await holder.stop()
        assert client.close_count == 1, "session must be cleanly closed on stop()"


@pytest.mark.asyncio
async def test_holder_lists_tools_once_at_start() -> None:
    from services.mcp_session_holder import MCPSessionHolder

    client = _FakeMCPClient([
        {"name": "calendar", "description": "cal", "inputSchema": {"type": "object"}},
    ])
    holder = MCPSessionHolder(client, "apple")
    await holder.start()
    try:
        tools = holder.tools
        assert [t["name"] for t in tools] == ["calendar"]
        assert tools[0]["description"] == "cal"
    finally:
        await holder.stop()


@pytest.mark.asyncio
async def test_holder_serializes_concurrent_calls_through_one_session() -> None:
    """If two callers race, both calls must use the same session — they
    just queue. Otherwise we'd accidentally spin up a second subprocess."""
    from services.mcp_session_holder import MCPSessionHolder

    client = _FakeMCPClient([{"name": "calendar", "description": ""}])
    holder = MCPSessionHolder(client, "apple")
    await holder.start()
    try:
        results = await asyncio.gather(*[
            holder.call_tool("calendar", {"i": i}) for i in range(8)
        ])
        assert len(results) == 8
        assert client.open_count == 1, (
            f"concurrent calls must NOT open extra sessions, got {client.open_count}"
        )
    finally:
        await holder.stop()
