"""
Stream progress around web_search and fetch_page_content tools so the
UI shows what URL/query is in flight as it happens. Without this, the
user sees ``tool_start`` then ~2-5 seconds of silence before the next
LLM event.

Contract pinned by these tests:
* Calling ``execute_tool_call_with_events`` for ``web_search`` emits a
  ``progress`` event with status ``"started"`` carrying the query
  before the tool runs, and a ``progress`` event with status
  ``"completed"`` carrying the result count after it returns.
* Same shape for ``fetch_page_content`` with the URL.
"""

from __future__ import annotations

import pytest


class _StubTool:
    def __init__(self, name, result):
        self.name = name
        self._result = result

    async def ainvoke(self, args):
        return self._result


@pytest.mark.asyncio
async def test_web_search_emits_progress_started_and_completed() -> None:
    from services.graph.streaming import execute_tool_call_with_events

    tools = [_StubTool(
        "web_search",
        "Found 3 results:\n1. Title A\n2. Title B\n3. Title C",
    )]

    tool_call = {
        "name": "web_search",
        "args": {"query": "latest ai news"},
        "id": "call_1",
    }

    events = []
    async for ev in execute_tool_call_with_events(tool_call, tools, "conv-1"):
        if "_result" in ev:
            continue
        events.append(ev)

    progress = [e for e in events if e.get("type") == "progress"]
    starts = [e for e in progress if e.get("status") == "started"]
    completes = [e for e in progress if e.get("status") == "completed"]

    assert any("latest ai news" in (e.get("message") or "") for e in starts), (
        f"expected a progress 'started' event mentioning the query, "
        f"got: {progress}"
    )
    assert completes, f"expected a progress 'completed' event, got: {progress}"
    # The completed event should mention the result count somewhere —
    # either in the message or the count field.
    completed = completes[0]
    has_count = (
        completed.get("count") is not None
        or any(c.isdigit() for c in (completed.get("message") or ""))
    )
    assert has_count, f"completed progress should carry a count, got: {completed}"


@pytest.mark.asyncio
async def test_fetch_page_content_emits_progress_with_url() -> None:
    from services.graph.streaming import execute_tool_call_with_events

    tools = [_StubTool(
        "fetch_page_content",
        "# Page Title\n\nLong page body here." * 50,
    )]

    tool_call = {
        "name": "fetch_page_content",
        "args": {"url": "https://example.com/article"},
        "id": "call_2",
    }

    events = []
    async for ev in execute_tool_call_with_events(tool_call, tools, "conv-2"):
        if "_result" in ev:
            continue
        events.append(ev)

    progress = [e for e in events if e.get("type") == "progress"]
    starts = [e for e in progress if e.get("status") == "started"]

    assert any("example.com/article" in (e.get("message") or "") for e in starts), (
        f"expected progress 'started' event mentioning the URL, got: {progress}"
    )
    completes = [e for e in progress if e.get("status") == "completed"]
    assert completes, f"expected a completed progress event, got: {progress}"
