"""
B6: MCP holder isolation + chat-wrapper resilience.

Two failure modes that surfaced as "Sorry, I encountered an error.
(stream interrupted)" in the United test:

(1) When apple-mcp's stdio transport breaks mid-call (subprocess
    dies, pipe closes), the holder's `_run` task exits permanently
    and every subsequent call_tool fails with "holder not started".
    A whole class of tools goes dark for the rest of the conversation.
    Fix: on session crash, the holder transparently reopens the
    session and the next call_tool succeeds against the fresh one.

(2) `_wrap_chat_stream` catches Exception but not BaseException.
    If ANYTHING cancels the chat task (TaskGroup cascade, asyncio
    shutdown), CancelledError propagates through the wrapper and
    the SSE stream closes with no content event — frontend shows
    the canned 'encountered an error' message. Fix: widen catch
    so even cancellations emit a graceful explainer event.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# (1) MCPSessionHolder auto-restart
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal stand-in for an open MCP session. Tracks which calls
    succeeded vs which raised."""

    def __init__(self, name="fake", tool_results=None, tool_raises=None):
        self.name = name
        self._tool_results = tool_results or {}
        self._tool_raises = tool_raises or {}
        self.calls_made = []

    async def list_tools(self):
        class _Listing:
            tools = []
        return _Listing()

    async def call_tool(self, name, args):
        self.calls_made.append((name, args))
        if name in self._tool_raises:
            exc = self._tool_raises.pop(name)
            raise exc
        result = MagicMock()
        result.content = [MagicMock(text=self._tool_results.get(name, "ok"))]
        return result


class _FakeSessionCM:
    """async context manager wrapper for FakeSession. Optionally
    raises during __aexit__ to simulate stdio transport breakage."""

    def __init__(self, session, raise_on_exit=None):
        self._session = session
        self._raise_on_exit = raise_on_exit

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        if self._raise_on_exit and exc_type is None:
            raise self._raise_on_exit
        return False


class _FakeClient:
    """Stand-in for the langchain MultiServerMCPClient. Hands out
    sessions in a configurable sequence — lets a test simulate
    'session 1 crashes, session 2 is fresh and works'."""

    def __init__(self, session_factories):
        self._factories = list(session_factories)
        self.sessions_handed_out = 0

    def session(self, server_name):
        if not self._factories:
            raise RuntimeError("FakeClient: no more sessions configured")
        cm = self._factories.pop(0)
        self.sessions_handed_out += 1
        return cm


@pytest.mark.asyncio
async def test_holder_reopens_session_after_call_failure():
    """When session.call_tool raises a transport error, the holder
    must transparently reopen the underlying session so the NEXT
    call_tool works. Without this, the first apple-mcp transport
    glitch nukes calendar/email/etc for the rest of the conversation."""
    from services.mcp_session_holder import MCPSessionHolder

    sess1 = _FakeSession(
        tool_raises={"calendar": ConnectionResetError("pipe closed")},
    )
    sess2 = _FakeSession(tool_results={"calendar": "events: 0"})
    client = _FakeClient([
        _FakeSessionCM(sess1),
        _FakeSessionCM(sess2),
    ])

    holder = MCPSessionHolder(client, "apple")
    await holder.start()
    try:
        # First call hits the broken session and fails.
        with pytest.raises(ConnectionResetError):
            await holder.call_tool("calendar", {})
        # Second call should auto-reopen onto sess2 and succeed.
        result = await holder.call_tool("calendar", {})
        assert "events" in result, f"expected sess2 result, got: {result!r}"
        assert client.sessions_handed_out == 2, (
            "holder must reopen the session after a transport failure"
        )
    finally:
        await holder.stop()


@pytest.mark.asyncio
async def test_holder_reopens_after_aexit_raise():
    """If the session's __aexit__ raises (BrokenResourceError from
    the stdio TaskGroup re-raising on cleanup), the next call_tool
    still works via a fresh session. This is the actual production
    failure mode that crashed the United test."""
    from services.mcp_session_holder import MCPSessionHolder

    # Session 1 succeeds at call_tool, but its CM raises on exit.
    sess1 = _FakeSession(tool_results={"calendar": "first"})
    sess2 = _FakeSession(tool_results={"calendar": "second"})

    class _BrokenResource(Exception):
        pass

    # Inner ExceptionGroup wrapping is what langchain-mcp-adapters
    # actually raises; the holder's recovery shouldn't depend on the
    # exact exception type.
    client = _FakeClient([
        _FakeSessionCM(sess1, raise_on_exit=_BrokenResource("pipe broke")),
        _FakeSessionCM(sess2),
    ])

    holder = MCPSessionHolder(client, "apple")
    await holder.start()
    try:
        first = await holder.call_tool("calendar", {})
        assert first == "first"

        # Force the holder to recycle by simulating session loss —
        # production trigger is the stdout-reader task crashing.
        # We poke the holder's internal state to mimic that, then
        # verify call_tool still succeeds.
        await holder._recycle_session()  # type: ignore[attr-defined]

        second = await holder.call_tool("calendar", {})
        assert second == "second"
        assert client.sessions_handed_out == 2
    finally:
        await holder.stop()


# ---------------------------------------------------------------------------
# (2) chat wrapper catches BaseException
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrap_chat_stream_handles_cancelled_error(monkeypatch):
    """CancelledError mid-stream (e.g., a parent TaskGroup cancelling
    the chat task because some unrelated sibling crashed) must NOT
    cause an empty SSE stream. The wrapper must emit a graceful
    explainer + DONE event so the frontend shows what happened
    instead of the canned 'encountered an error'."""
    from routers.chat import _wrap_chat_stream

    async def _exploding_stream(**kwargs):
        # Yield one event so partial response is non-empty.
        yield {"type": "tool_start", "tool_name": "browse_my_chrome"}
        # Then get cancelled — CancelledError is a BaseException, not Exception.
        raise asyncio.CancelledError("simulated parent cancel")

    # routers.chat imported stream_with_memory_events at module load,
    # so we have to patch the router module's binding, not the source.
    monkeypatch.setattr("routers.chat.stream_with_memory_events", _exploding_stream)

    async def _fake_explainer(**kwargs):
        return "Edward: hit an issue mid-stream — try again."

    monkeypatch.setattr(
        "routers.chat._format_chat_error_explanation", _fake_explainer,
    )

    events = []
    try:
        async for ev in _wrap_chat_stream(
            message="x", conversation_id="c1", system_prompt="", model="m",
            temperature=0.7, graph=None, attachments=None, provider="codex",
        ):
            events.append(ev)
    except asyncio.CancelledError:
        pytest.fail(
            "wrapper let CancelledError escape — frontend will see "
            "empty stream + canned error message"
        )

    types = [e.get("type") if isinstance(e, dict) else None for e in events]
    assert "content" in types, (
        f"wrapper must emit a content event with the explainer, got types: {types}"
    )
    assert "done" in types, (
        f"wrapper must emit a done event so the SSE closes cleanly, got: {types}"
    )


@pytest.mark.asyncio
async def test_wrap_chat_stream_passes_through_normal_events(monkeypatch):
    """Sanity: normal stream still flows through untouched."""
    from routers.chat import _wrap_chat_stream

    async def _normal_stream(**kwargs):
        yield {"type": "content", "content": "hello "}
        yield {"type": "content", "content": "world"}
        yield {"type": "done"}

    monkeypatch.setattr(
        "routers.chat.stream_with_memory_events",
        _normal_stream,
    )

    events = []
    async for ev in _wrap_chat_stream(
        message="x", conversation_id="c1", system_prompt="", model="m",
        temperature=0.7, graph=None, attachments=None, provider="codex",
    ):
        events.append(ev)

    contents = [
        e["content"] for e in events
        if isinstance(e, dict) and e.get("type") == "content"
    ]
    assert "".join(contents) == "hello world"
