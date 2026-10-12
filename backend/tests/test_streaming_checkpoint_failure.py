"""
Regression: when graph.aupdate_state raises after Edward has already
streamed a successful response, the bare `await graph.aupdate_state(...)`
call in stream_with_memory_events used to propagate out of the SSE
generator. Net effect:
  1. Tools succeed, calendar event lands, etc.
  2. Edward's text reply streams to the browser as CONTENT events.
  3. aupdate_state raises (image attachments break checkpoint serialization).
  4. SSE stream errors → frontend ChatContext catch block overwrites the
     visible reply with "Sorry, I encountered an error. Please try again."
  5. User thinks Edward failed, even though the action succeeded.

Fix: a checkpoint-write failure must not kill the user-facing stream.
Wrap aupdate_state, log, and keep going so the DONE event still fires.
"""

from __future__ import annotations

import asyncio

import pytest


class _StubGraphAlwaysRaises:
    """Graph stub whose aupdate_state always raises, mimicking the
    image-attachment serialization failure."""

    async def aget_state(self, config):
        class _S:
            values = {}
        return _S()

    async def aupdate_state(self, *args, **kwargs):
        raise RuntimeError("simulated checkpoint serialization failure")


@pytest.mark.asyncio
async def test_safe_update_state_swallows_and_logs(caplog) -> None:
    from services.graph.streaming import _safe_update_state

    stub = _StubGraphAlwaysRaises()
    config = {"configurable": {"thread_id": "test-thread"}}
    state = {"messages": ["x"]}

    # Must not raise
    result = await _safe_update_state(stub, config, state, as_node="extract_memory")
    assert result is False, "expected False to signal failed write"

    # Must log the exception so the operator can debug
    msgs = [r.getMessage() for r in caplog.records]
    assert any("checkpoint" in m.lower() for m in msgs), (
        f"expected a checkpoint-related log line, got: {msgs}"
    )


@pytest.mark.asyncio
async def test_safe_update_state_passes_through_on_success() -> None:
    from services.graph.streaming import _safe_update_state

    seen_kwargs = {}

    class _OK:
        async def aupdate_state(self, config, state, **kwargs):
            seen_kwargs["config"] = config
            seen_kwargs["state"] = state
            seen_kwargs["kwargs"] = kwargs
            return None

    ok = _OK()
    config = {"configurable": {"thread_id": "t"}}
    state = {"messages": []}
    result = await _safe_update_state(ok, config, state, as_node="extract_memory")
    assert result is True
    assert seen_kwargs["config"] == config
    assert seen_kwargs["state"] == state
    assert seen_kwargs["kwargs"] == {"as_node": "extract_memory"}
