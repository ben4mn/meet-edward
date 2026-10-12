"""MCPSessionHolder — keep one MCP subprocess session open across many
tool calls.

Without this, ``MultiServerMCPClient.get_tools()`` returns wrappers
that open a fresh stdio session (= cold-start a fresh subprocess) per
tool invocation. apple-mcp's ``bun run index.ts`` is ~5 s of cold start
each, so a trip-planning chat with 7 tool calls spent 35 s in MCP
overhead alone — and the production log showed 8483 restarts in one
day from per-call sessions.

The holder owns a long-running asyncio task that holds
``client.session(name)`` open. Tool calls are serialized through a
queue so concurrent callers share the single session instead of racing
to open a second one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MCP_CALL_TIMEOUT_SECONDS = 30.0


def mcp_call_timeout_seconds() -> float:
    """The bound on one MCP tool call (``MCP_CALL_TIMEOUT_SECONDS``, 30 s by
    default). Read when a holder is built, so a restart picks up a change."""
    raw = os.environ.get("MCP_CALL_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_MCP_CALL_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_MCP_CALL_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_MCP_CALL_TIMEOUT_SECONDS


def _looks_like_transport_failure(exc: BaseException) -> bool:
    """B6: identify exceptions that mean the underlying MCP stdio is
    dead and the session needs to be reopened. Conservative — when in
    doubt, recycle: a needless recycle costs ~5s of subprocess
    cold-start once; not recycling on a real transport failure leaves
    every subsequent call_tool failing for the rest of the
    conversation."""
    if isinstance(exc, (
        ConnectionError, ConnectionResetError, BrokenPipeError, EOFError,
    )):
        return True
    name = type(exc).__name__
    # anyio.BrokenResourceError, anyio.ClosedResourceError live in
    # anyio.exceptions and are stringly-matched to keep this module
    # free of an anyio dependency at import time.
    if name in ("BrokenResourceError", "ClosedResourceError"):
        return True
    msg = str(exc).lower()
    if "broken pipe" in msg or "stream closed" in msg or "broken resource" in msg:
        return True
    return False


def _result_text(result: Any) -> str:
    """Best-effort flatten of an MCP CallToolResult into a string."""
    try:
        parts = []
        for block in getattr(result, "content", []) or []:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            else:
                parts.append(str(block))
        return "".join(parts) if parts else str(result)
    except Exception:
        return str(result)


class MCPSessionHolder:
    """Owns one open MCP session and serializes calls through it.

    Lifecycle:
        h = MCPSessionHolder(client, "apple")
        await h.start()   # opens session, lists tools
        result = await h.call_tool("calendar", {"op": "list"})
        await h.stop()    # closes session
    """

    def __init__(self, client, server_name: str):
        self._client = client
        self._server_name = server_name
        self._task: Optional[asyncio.Task] = None
        self._ready: asyncio.Event = asyncio.Event()
        self._stop: asyncio.Event = asyncio.Event()
        # B6: signal from inside _run (or via _recycle_session) that
        # the current session is hosed and a fresh one should be opened.
        self._recycle: asyncio.Event = asyncio.Event()
        # Whether _run_one_session has completed its FIRST open. Used
        # to distinguish a hard startup failure (surface via _start_error)
        # from a transient mid-flight failure (retry transparently).
        self._first_open_done: bool = False
        # Queue items: (name, args, future_to_resolve, timeout_seconds)
        self._queue: asyncio.Queue[Tuple[str, Dict[str, Any], asyncio.Future, float]] = asyncio.Queue()
        self._tools: List[Dict[str, Any]] = []
        self._start_error: Optional[BaseException] = None
        # N5: per-call timeout. apple-mcp's Mail tool can hang 120s on
        # a closed Mail.app — long enough that the upstream proxy cuts
        # the SSE stream and Edward's chat surfaces a generic error.
        # 30s is short enough to keep the user-visible response within
        # the proxy window, long enough that legit calls don't trip.
        # ``MCP_CALL_TIMEOUT_SECONDS`` overrides it (2026-09-24).
        self._call_timeout_seconds: float = mcp_call_timeout_seconds()

    @property
    def tools(self) -> List[Dict[str, Any]]:
        """Return the list of tools discovered at start, each as a dict
        with name, description, inputSchema. Empty until start() returns."""
        return list(self._tools)

    async def start(self) -> None:
        """Open the session, list tools, and begin servicing calls."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._start_error is not None:
            raise self._start_error

    async def stop(self) -> None:
        """Stop accepting new calls and close the session."""
        if self._task is None:
            return
        self._stop.set()
        # Unblock the queue.get in _run if it's waiting
        try:
            self._queue.put_nowait(("__shutdown__", {}, asyncio.get_event_loop().create_future(), 0.0))
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._task, timeout=10)
        except asyncio.TimeoutError:
            self._task.cancel()
            with __import__("contextlib").suppress(Exception):
                await self._task
        self._task = None

    async def call_tool(self, name: str, args: Dict[str, Any], timeout: Optional[float] = None) -> str:
        """Submit a tool call and wait for its result. Concurrent callers
        share the same session — calls serialize through the queue.

        N5: bounded by ``timeout`` (default ``self._call_timeout_seconds``,
        30s). On timeout raises ``asyncio.TimeoutError`` so the LLM-facing
        wrapper can return ``"Tool error: timeout after Ns"`` and let
        the LLM recover within the SSE response window. The worker bounds
        the same call by the same number (``_serve_one``), so a wedged call
        cannot hold the queue for the callers behind it.
        """
        if self._task is None:
            raise RuntimeError("MCPSessionHolder.call_tool: holder not started")
        if self._stop.is_set():
            raise RuntimeError("MCPSessionHolder.call_tool: holder is stopped")
        bound = float(timeout) if timeout is not None and timeout > 0 else self._call_timeout_seconds
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        await self._queue.put((name, args, fut, bound))
        try:
            return await asyncio.wait_for(fut, timeout=bound)
        except asyncio.TimeoutError as e:
            if not fut.done():
                fut.cancel()
            logger.warning(
                "MCPSessionHolder(%s).call_tool(%s) timed out after %.1fs",
                self._server_name, name, bound,
            )
            # Re-raise with a human-readable message so the LLM gets
            # "Tool error: timeout after 30s — ..." instead of just
            # "Tool error: " (TimeoutError carries no message by default).
            raise TimeoutError(
                f"timeout after {bound:.0f}s — "
                f"{self._server_name}.{name} hung"
            ) from e

    async def _serve_one(self, session, name: str, args: Dict[str, Any], fut: asyncio.Future,
                         bound: float) -> None:
        """Run one queued call on the open session, bounded (2026-09-24).

        Until then the worker awaited ``session.call_tool`` with no limit:
        the caller gave up after 30 s, but the worker stayed on the wedged
        call forever and every call queued behind it timed out in turn —
        the calendar in every wake's pack, the pulse, chat tools — until a
        restart. Now a call that outlives its bound fails and the session
        is reopened (a wedged subprocess is the likely cause; a reopen
        costs one cold start). A call whose caller already gave up while
        it waited in the queue is not sent at all.
        """
        if fut.done():
            return
        try:
            result = await asyncio.wait_for(session.call_tool(name, args), timeout=bound)
            if not fut.done():
                fut.set_result(_result_text(result))
        except asyncio.TimeoutError:
            logger.warning(
                "MCPSessionHolder(%s): %s did not answer within %.1fs — "
                "failing it and reopening the session",
                self._server_name, name, bound,
            )
            if not fut.done():
                fut.set_exception(TimeoutError(
                    f"timeout after {bound:.0f}s — {self._server_name}.{name} hung"
                ))
            self._recycle.set()
        except asyncio.CancelledError:
            if not fut.done():
                fut.cancel()
            raise
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)
            # Connection-level errors mean the session is
            # toast — recycle so the next call gets a
            # fresh transport instead of failing forever.
            if _looks_like_transport_failure(e):
                logger.warning(
                    "MCPSessionHolder(%s): transport error "
                    "on %s — recycling session",
                    self._server_name, name,
                )
                self._recycle.set()

    async def _run_one_session(self) -> bool:
        """Open ONE session and serve queued calls until either:
          - stop is requested → returns False (terminal)
          - a recycle is requested → returns True (caller should re-open)
          - the session ctx errors during entry → returns False, error
            recorded in self._start_error if this was the first session

        Splitting the run loop this way is what makes auto-recovery
        possible: when the underlying stdio transport breaks, we exit
        cleanly, the outer loop opens a fresh session, and queued/new
        callers see a working transport again.
        """
        try:
            async with self._client.session(self._server_name) as session:
                # Discover tools once per session (re-listing on recycle
                # is fine and catches schema changes in the rare case
                # the subprocess restarted with new tools).
                try:
                    listing = await session.list_tools()
                    self._tools = [
                        {
                            "name": t.name,
                            "description": getattr(t, "description", "") or "",
                            "inputSchema": getattr(t, "inputSchema", None)
                            or {"type": "object", "properties": {}},
                        }
                        for t in (listing.tools if hasattr(listing, "tools") else [])
                    ]
                except Exception as e:
                    logger.warning(
                        "MCPSessionHolder(%s): list_tools failed: %s",
                        self._server_name, e,
                    )
                    self._tools = []

                self._ready.set()
                self._first_open_done = True
                self._recycle.clear()

                while not self._stop.is_set() and not self._recycle.is_set():
                    try:
                        # Sleep briefly between fetches so a recycle
                        # signal between calls is observed without
                        # blocking forever on queue.get.
                        name, args, fut, bound = await asyncio.wait_for(
                            self._queue.get(), timeout=0.5,
                        )
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break
                    if name == "__shutdown__":
                        if not fut.done():
                            fut.set_result("")
                        return False
                    await self._serve_one(session, name, args, fut, bound or self._call_timeout_seconds)
                # If we set recycle ourselves, signal the caller to retry.
                return self._recycle.is_set()
        except BaseException as e:
            # Could be: (a) failed to enter session (first time), (b)
            # session __aexit__ raised on cleanup, (c) recycle requested
            # mid-flight and the cleanup happened to raise. (a) is fatal
            # and must be surfaced via _start_error so start() raises;
            # (b)/(c) are transient and we retry.
            first_open = not self._first_open_done
            if first_open:
                self._start_error = e
                self._first_open_done = True
                self._ready.set()
                # Drain queued futures — caller can't recover.
                while not self._queue.empty():
                    try:
                        _, _, fut, _ = self._queue.get_nowait()
                        if not fut.done():
                            fut.set_exception(e)
                    except Exception:
                        break
                return False
            # Post-startup failure — log and retry unless explicitly stopped.
            logger.warning(
                "MCPSessionHolder(%s): session ended with %s — "
                "reopening for next call_tool",
                self._server_name, type(e).__name__,
            )
            return not self._stop.is_set()

    async def _run(self) -> None:
        """Outer loop — keep one session open at a time, reopening
        whenever a transient failure recycles the current one."""
        while not self._stop.is_set():
            should_retry = await self._run_one_session()
            if not should_retry:
                return
            # Recycle requested. Loop back and open a fresh session.

    async def _recycle_session(self) -> None:
        """Public-ish hook for tests + manual recovery: signal the
        running session loop to close its current session and reopen
        a fresh one. Returns once the new session is ready."""
        if self._task is None or self._task.done():
            return
        self._ready.clear()
        self._recycle.set()
        await self._ready.wait()
