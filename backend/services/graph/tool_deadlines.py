"""Deadlines where the tool loop waits (2026-09-24; docs/architecture/tool-loop.md).

Nothing in the loop had one. A tool call was awaited unbounded — the
Apple Calendar MCP call has no timeout, and one hung call held the wake
lock and every later wake and pulse with it. An LLM call had only httpx's
120 s *per-read* limit, so a stream that trickled never ended. Streaming
chat had no turn deadline at all.

* ``run_tool_with_deadline`` — a hung tool becomes ``Tool error: <name>
  timed out after Ns``, which the model reads and the turn ledger counts
  as a failure. The tool's own work may still finish in the background
  (a thread or an osascript send cannot be cancelled); the loop stops
  waiting for it.
* ``invoke_with_deadline`` / ``aiter_with_deadline`` — an LLM call raises
  ``LLMCallTimeout`` (a ``TimeoutError``) that the loop turns into an
  honest sentence. For a stream only time spent waiting on the model
  counts; a slow SSE write to the phone between chunks does not.
* ``STREAM_TURN_MAX_SECONDS`` — the streaming loop stops calling tools
  after this and makes one final no-tools call to say what it did and
  what is left.

Constants are read at call time, so tests (and a hot fix) can change them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Awaitable, Dict, Optional

# One model call. Well past a slow high-effort answer (~60-120 s), well
# short of a wedged stream.
LLM_CALL_TIMEOUT_SECONDS: float = 300
# One tool call, unless overridden below.
TOOL_TIMEOUT_SECONDS: float = 120
# A whole streaming chat turn. The owner is watching; ten minutes of tools is
# already a long time to look at a spinner.
STREAM_TURN_MAX_SECONDS: float = 600

# A tool says its own deadline in ``metadata["timeout_s"]`` (the toolset,
# 2026-10-11; ``None`` = no outer limit — ``bash`` kills its own process
# group). These cover the legacy names (``EDWARD_TOOLSET=legacy``) and
# tests: exact names first, then prefixes.
TOOL_TIMEOUT_OVERRIDES: Dict[str, Optional[float]] = {
    "web_search": 45,
    "fetch_page_content": 45,
}
TOOL_TIMEOUT_PREFIXES: Dict[str, Optional[float]] = {
    "browser_": 90,
}
# What a turn keeps back for its closing call when its deadline clamps a
# tool: a wake cut mid-tool by its caller lost its answer.
CLOSING_RESERVE_SECONDS: float = 45
MIN_TOOL_SECONDS: float = 5
_NOT_SET = object()


class LLMCallTimeout(TimeoutError):
    """An LLM call that did not finish within its deadline."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        super().__init__(f"LLM call timed out after {_fmt(seconds)}")


def _fmt(seconds: float) -> str:
    return f"{seconds:g}s"


def tool_timeout_for(tool: Any) -> Optional[float]:
    """A tool's deadline: its ``metadata["timeout_s"]`` when it says one
    (``None`` = no outer limit), else the overrides by name, else
    ``TOOL_TIMEOUT_SECONDS``. Takes a tool or a name."""
    if not isinstance(tool, str):
        md = getattr(tool, "metadata", None)
        if isinstance(md, dict) and "timeout_s" in md:
            return md["timeout_s"]
        name = str(getattr(tool, "name", "") or "")
    else:
        name = tool
    if name in TOOL_TIMEOUT_OVERRIDES:
        return TOOL_TIMEOUT_OVERRIDES[name]
    for prefix, seconds in TOOL_TIMEOUT_PREFIXES.items():
        if (name or "").startswith(prefix):
            return seconds
    return TOOL_TIMEOUT_SECONDS


def clamp_to_turn(seconds: Optional[float], remaining: Optional[float]) -> Optional[float]:
    """A tool's deadline inside a turn with ``remaining`` seconds left: no
    later than the turn's end minus ``CLOSING_RESERVE_SECONDS`` (and never
    under ``MIN_TOOL_SECONDS``), so the turn stops gracefully instead of
    being cancelled mid-tool by its caller. Pure."""
    if remaining is None:
        return seconds
    cap = max(MIN_TOOL_SECONDS, remaining - CLOSING_RESERVE_SECONDS)
    return cap if seconds is None else min(seconds, cap)


def timeout_error(name: str, seconds: float) -> str:
    return f"Tool error: {name} timed out after {_fmt(seconds)}"


async def run_tool_with_deadline(name: str, awaitable: Awaitable[Any], seconds: Any = _NOT_SET) -> Any:
    """Await one tool call under its deadline (``seconds``, else the
    tool's by name); a timeout is a tool error."""
    if seconds is _NOT_SET:
        seconds = tool_timeout_for(name)
    if seconds is None:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError:
        print(f"[TOOL DEADLINE] {name} timed out after {_fmt(seconds)}")
        return timeout_error(name, seconds)


async def invoke_with_deadline(awaitable: Awaitable[Any], timeout: Optional[float] = None) -> Any:
    """Await one LLM call; ``LLMCallTimeout`` past the deadline."""
    seconds = LLM_CALL_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise LLMCallTimeout(seconds) from exc


async def aiter_with_deadline(stream: AsyncIterator[Any], timeout: Optional[float] = None) -> AsyncIterator[Any]:
    """Yield from ``stream`` while the time spent waiting on it stays under
    ``timeout``; ``LLMCallTimeout`` past that. The wait for each item runs in
    the caller's task (``asyncio.timeout``), so an httpx/anyio stream is
    never resumed from another task."""
    budget = LLM_CALL_TIMEOUT_SECONDS if timeout is None else timeout
    remaining = budget
    iterator = stream.__aiter__()
    try:
        while True:
            if remaining <= 0:
                raise LLMCallTimeout(budget)
            started = time.monotonic()
            try:
                async with asyncio.timeout(remaining):
                    item = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise LLMCallTimeout(budget) from exc
            remaining -= time.monotonic() - started
            yield item
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001 — closing a dead stream must not mask the timeout
                pass
