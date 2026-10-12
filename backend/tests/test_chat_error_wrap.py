"""
N7: when the chat SSE generator raises an unhandled exception
mid-stream, the user shouldn't see "Sorry, I encountered an error".
We catch the exception in the chat router, run a quick LLM call to
explain what Edward was doing and why it failed, and emit that as
a CONTENT event before closing the stream.

The seam is a small async helper format_chat_error_explanation()
that takes the exception + tool context + partial response and
returns a 1-2 sentence Edward-voiced apology. Easy to unit-test
with a stubbed LLM.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import pytest


class _ScriptedLLM:
    """Mimics build_background_llm — single ainvoke returns the
    canned content."""

    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content=self._content)


@pytest.mark.asyncio
async def test_format_chat_error_explanation_uses_llm(monkeypatch):
    """Failure context + LLM stub → returned string is the LLM's
    response, with no canned 'Sorry, I encountered an error' text."""
    from routers import chat as chat_router

    captured = {}

    def fake_build_llm(temperature=0.7, max_tokens=200, **kwargs):
        captured["temperature"] = temperature
        captured["max_tokens"] = max_tokens
        return _ScriptedLLM(
            "I tried to check Mail for the United info but Mail.app didn't respond — "
            "want me to try via the web browser instead?"
        )

    import services.llm_utils
    monkeypatch.setattr(services.llm_utils, "build_background_llm", fake_build_llm)

    out = await chat_router._format_chat_error_explanation(
        user_message="check my united itinerary",
        partial_response="I'll check your Mail",
        last_tool_name="mail",
        error_text="timeout after 30s — apple.mail hung",
    )

    assert isinstance(out, str)
    assert "Sorry, I encountered an error" not in out
    assert "Mail" in out  # the LLM's apology references the tool


@pytest.mark.asyncio
async def test_format_chat_error_explanation_falls_back_when_llm_fails(monkeypatch):
    """If the explainer LLM ALSO fails, we return a templated message
    that still tells the user what failed — never just a raw error."""
    from routers import chat as chat_router

    class _BoomLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("llm too is broken")

    import services.llm_utils
    monkeypatch.setattr(services.llm_utils, "build_background_llm", lambda **_: _BoomLLM())

    out = await chat_router._format_chat_error_explanation(
        user_message="x",
        partial_response="",
        last_tool_name="apple.mail",
        error_text="timeout after 30s",
    )
    assert isinstance(out, str)
    assert out, "must always return a non-empty explanation"
    # Templated fallback should at least mention the tool name so the
    # user has actionable info
    assert "apple.mail" in out.lower() or "mail" in out.lower() or "tool" in out.lower()


@pytest.mark.asyncio
async def test_chat_generate_emits_explanation_on_exception(monkeypatch):
    """End-to-end at the generator level: stream_with_memory_events
    raises, generate() must yield a CONTENT event with a non-canned
    explanation, then DONE."""
    from routers import chat as chat_router

    async def _raising_stream(*args, **kwargs):
        # Simulate a normal start then a mid-stream failure
        from services.graph.streaming import EventType, create_event
        yield create_event(EventType.CONTENT, "conv-1", content="I'll try")
        raise RuntimeError("simulated mid-stream blow-up")

    async def fake_explain(**kwargs):
        return "I tried to start that, hit a snag — want me to retry?"

    monkeypatch.setattr(chat_router, "stream_with_memory_events", _raising_stream)
    monkeypatch.setattr(chat_router, "_format_chat_error_explanation", fake_explain)

    events_yielded = []
    async for evt in chat_router._wrap_chat_stream(
        message="hi",
        conversation_id="conv-1",
        system_prompt="",
        model="gpt-5.4",
        temperature=0.7,
        graph=None,
        attachments=None,
        provider="openai",
    ):
        events_yielded.append(evt)

    contents = [
        e for e in events_yielded
        if isinstance(e, dict) and e.get("type") == "content"
    ]
    assert any("retry" in (e.get("content") or "") for e in contents), (
        f"expected the LLM-shaped explanation in a CONTENT event, got {events_yielded}"
    )
    dones = [e for e in events_yielded if isinstance(e, dict) and e.get("type") == "done"]
    assert dones, "stream must close cleanly with DONE so frontend doesn't show generic error"
