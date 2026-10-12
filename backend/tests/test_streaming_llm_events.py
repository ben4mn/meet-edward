"""
S1: streaming.py needs to consume the LLM via .astream(...) so each
text delta reaches the SSE pipeline as it's produced, instead of
buffering the whole reply and dumping it as one CONTENT event.

The chat loop is too entangled to unit-test directly, so this pins
the seam: a small helper _stream_llm_with_events that takes an LLM
and yields typed events:

    {"type": "content_delta", "text": "..."}      (one per delta)
    {"type": "final", "message": AIMessage}       (exactly one, last)

The chat loop then trivially turns content_delta events into
EventType.CONTENT SSE events, and uses the final AIMessage to branch
on tool_calls just as today.
"""

from __future__ import annotations

from typing import AsyncIterator, List

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk


class _ScriptedStreamLLM:
    """LLM stub whose astream yields the chunks we tell it to."""

    def __init__(self, chunks: List[ChatGenerationChunk]):
        self._chunks = chunks

    async def astream(self, messages, **kwargs) -> AsyncIterator:
        for ch in self._chunks:
            # langchain BaseChatModel.astream yields BaseMessageChunk objects
            # (the .message of a ChatGenerationChunk). Mimic that here.
            yield ch.message


def _content_chunk(text: str) -> ChatGenerationChunk:
    return ChatGenerationChunk(message=AIMessageChunk(content=text))


def _toolcall_chunk(name: str, args: dict, call_id: str) -> ChatGenerationChunk:
    return ChatGenerationChunk(
        message=AIMessageChunk(
            content="",
            tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        )
    )


def _meta_chunk(model: str, usage: dict) -> ChatGenerationChunk:
    return ChatGenerationChunk(
        message=AIMessageChunk(content="", response_metadata={"model": model, "usage": usage})
    )


@pytest.mark.asyncio
async def test_stream_llm_yields_one_event_per_text_delta() -> None:
    from services.graph.streaming import _stream_llm_with_events

    llm = _ScriptedStreamLLM([
        _content_chunk("Hello"),
        _content_chunk(", "),
        _content_chunk("world"),
        _content_chunk("!"),
        _meta_chunk("gpt-5.4", {"input_tokens": 10, "output_tokens": 4}),
    ])

    events = [e async for e in _stream_llm_with_events(llm, [HumanMessage(content="hi")])]

    deltas = [e for e in events if e["type"] == "content_delta"]
    assert [e["text"] for e in deltas] == ["Hello", ", ", "world", "!"], (
        f"expected 4 separate content_delta events, got: {events}"
    )

    finals = [e for e in events if e["type"] == "final"]
    assert len(finals) == 1, f"expected exactly one final event, got: {events}"
    msg = finals[0]["message"]
    assert isinstance(msg, AIMessage)
    assert msg.content == "Hello, world!"
    assert msg.response_metadata.get("model") == "gpt-5.4"
    assert (msg.response_metadata.get("usage") or {}).get("input_tokens") == 10


@pytest.mark.asyncio
async def test_stream_llm_collects_tool_calls_into_final_message() -> None:
    from services.graph.streaming import _stream_llm_with_events

    llm = _ScriptedStreamLLM([
        _content_chunk(""),  # no text
        _toolcall_chunk("calendar", {"op": "list"}, "fc_1"),
        _toolcall_chunk("widget", {"refresh": True}, "fc_2"),
        _meta_chunk("gpt-5.4", {}),
    ])

    events = [e async for e in _stream_llm_with_events(llm, [HumanMessage(content="hi")])]

    final = next(e for e in events if e["type"] == "final")["message"]
    names = [tc["name"] for tc in (final.tool_calls or [])]
    assert names == ["calendar", "widget"], (
        f"both tool calls must reach the final AIMessage so the outer loop "
        f"can dispatch them, got names={names}"
    )
    # And no spurious content_delta events for the empty content chunks
    deltas = [e for e in events if e["type"] == "content_delta"]
    assert deltas == [], f"empty-content chunks must not emit deltas, got: {deltas}"


@pytest.mark.asyncio
async def test_stream_llm_passes_tool_args_delta_through() -> None:
    """Codex's _astream surfaces partial tool-call JSON via
    additional_kwargs['tool_args_delta']. _stream_llm_with_events must
    forward those as typed events so the chat loop can emit them as
    EventType.TOOL_ARGS_DELTA SSE events."""
    from services.graph.streaming import _stream_llm_with_events

    def _args_delta_chunk(delta: str, name: str, call_id: str) -> ChatGenerationChunk:
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                additional_kwargs={
                    "tool_args_delta": {"id": call_id, "name": name, "delta": delta}
                },
            )
        )

    llm = _ScriptedStreamLLM([
        _args_delta_chunk('{"que', "web_search", "call_1"),
        _args_delta_chunk('ry":"hello"}', "web_search", "call_1"),
        _meta_chunk("gpt-5.4", {}),
    ])

    events = [e async for e in _stream_llm_with_events(llm, [HumanMessage(content="hi")])]

    deltas = [e for e in events if e["type"] == "tool_args_delta"]
    assert len(deltas) == 2, f"expected 2 tool_args_delta events, got: {events}"
    assert [d["delta"] for d in deltas] == ['{"que', 'ry":"hello"}']
    assert all(d["name"] == "web_search" and d["id"] == "call_1" for d in deltas)
