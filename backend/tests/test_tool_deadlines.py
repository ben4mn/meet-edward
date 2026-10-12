"""D4: deadlines where the loop waits (docs/muse-and-beyond-plan-2026-09-24.md).

Nothing in the tool loop had a deadline: a tool call was awaited unbounded
(an apple-mcp call that hangs holds the wake lock forever), an LLM call had
only httpx's 120 s *per-read* limit, and streaming chat had no turn
deadline at all. A hung call now becomes a ``Tool error:`` the model can
read, or a ``LLMCallTimeout`` the loop turns into an honest sentence.
"""

from __future__ import annotations

import asyncio

import pytest

from services.graph import tool_deadlines as td

pytestmark = pytest.mark.unit


class TestToolTimeouts:
    """Per-tool ``metadata["timeout_s"]`` (tools-spec §5.3.4) — the 60 s
    exec deadline used to cut the shell's own 120 s limit."""

    def test_the_default(self):
        assert td.tool_timeout_for("memory") == td.TOOL_TIMEOUT_SECONDS == 120

    def test_a_tool_says_its_own_deadline(self):
        from types import SimpleNamespace

        assert td.tool_timeout_for(SimpleNamespace(name="bash", metadata={"timeout_s": 195})) == 195
        assert td.tool_timeout_for(SimpleNamespace(name="x", metadata={"timeout_s": None})) is None
        assert td.tool_timeout_for(SimpleNamespace(name="x", metadata=None)) == 120

    def test_the_old_names_keep_their_deadlines(self):
        assert td.tool_timeout_for("browser_open") == 90
        assert td.tool_timeout_for("web_search") == 45
        assert td.tool_timeout_for("fetch_page_content") == 45

    def test_a_turn_deadline_clamps_the_tool(self):
        assert td.clamp_to_turn(120, remaining=300) == 120
        assert td.clamp_to_turn(120, remaining=100) == 100 - td.CLOSING_RESERVE_SECONDS
        assert td.clamp_to_turn(None, remaining=100) == 100 - td.CLOSING_RESERVE_SECONDS
        assert td.clamp_to_turn(120, remaining=20) == td.MIN_TOOL_SECONDS
        assert td.clamp_to_turn(120, remaining=None) == 120


class TestRunToolWithDeadline:
    async def test_a_hung_tool_becomes_a_tool_error(self, monkeypatch):
        monkeypatch.setitem(td.TOOL_TIMEOUT_OVERRIDES, "slow_tool", 0.05)

        async def hang():
            await asyncio.sleep(5)

        result = await td.run_tool_with_deadline("slow_tool", hang())
        assert result == "Tool error: slow_tool timed out after 0.05s"

    async def test_an_explicit_deadline_wins(self):
        async def hang():
            await asyncio.sleep(5)

        out = await td.run_tool_with_deadline("slow", hang(), seconds=0.05)
        assert out == "Tool error: slow timed out after 0.05s"

    async def test_whole_seconds_read_as_whole_seconds(self):
        assert td.timeout_error("browser_open", 90) == "Tool error: browser_open timed out after 90s"

    async def test_a_quick_tool_returns_its_result(self):
        async def quick():
            return "ok"

        assert await td.run_tool_with_deadline("remember_search", quick()) == "ok"


class TestLLMDeadline:
    async def test_invoke_raises_a_clear_timeout(self):
        async def hang():
            await asyncio.sleep(5)

        with pytest.raises(td.LLMCallTimeout) as exc:
            await td.invoke_with_deadline(hang(), timeout=0.05)
        assert isinstance(exc.value, TimeoutError)
        assert "0.05" in str(exc.value)

    async def test_stream_deadline_covers_the_whole_call(self):
        async def slow_stream():
            for i in range(100):
                await asyncio.sleep(0.02)
                yield i

        got = []
        with pytest.raises(td.LLMCallTimeout):
            async for item in td.aiter_with_deadline(slow_stream(), timeout=0.1):
                got.append(item)
        assert 0 < len(got) < 100

    async def test_time_spent_by_the_consumer_does_not_count(self):
        """Only waiting on the model counts: a slow SSE write to the phone
        between chunks must not be charged to the model."""
        async def quick_stream():
            for i in range(3):
                yield i

        got = []
        async for item in td.aiter_with_deadline(quick_stream(), timeout=0.05):
            got.append(item)
            await asyncio.sleep(0.04)
        assert got == [0, 1, 2]

    def test_defaults(self):
        assert td.LLM_CALL_TIMEOUT_SECONDS == 300
        assert td.STREAM_TURN_MAX_SECONDS == 600
