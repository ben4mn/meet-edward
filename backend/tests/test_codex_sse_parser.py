"""
S2 — first half: extract the Codex SSE parsing logic out of `_agenerate`
into a reusable async generator that yields typed events. This is the
seam that lets `_astream` exist without duplicating parse logic.

Today `_agenerate` accumulates everything into one `text_content` string
before returning. Once the parser is its own thing, we can:
  - feed it the same raw SSE chunks from a streaming HTTP response
  - have `_agenerate` consume it and join into a single AIMessage
  - have `_astream` consume it and yield one ChatGenerationChunk per
    text_delta event so the UI can render token-by-token.

This file pins the helper's contract.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Iterable, List

import pytest


async def _async_iter(items: Iterable[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_parser_yields_text_deltas_in_order() -> None:
    from services.codex_chat_model import _iter_codex_sse_events

    chunks = [
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n',
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":" there"}\n\n',
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"!"}\n\n',
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":{"in":1,"out":2}}}\n\n',
        'data: [DONE]\n\n',
    ]
    events: List[dict] = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]

    text_deltas = [e for e in events if e["type"] == "text_delta"]
    assert [e["text"] for e in text_deltas] == ["Hi", " there", "!"], (
        f"text deltas should arrive in order, got {events}"
    )

    completed = [e for e in events if e["type"] == "response_completed"]
    assert len(completed) == 1
    assert completed[0]["response"]["model"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_parser_assembles_function_call_from_streamed_arguments() -> None:
    from services.codex_chat_model import _iter_codex_sse_events

    # Codex streams a tool call as: output_item.added (metadata) →
    # function_call_arguments.delta (args fragments) → output_item.done (final).
    chunks = [
        'event: response.output_item.added\n'
        'data: {"type":"response.output_item.added","item":'
        '{"id":"item_1","type":"function_call","name":"calendar","call_id":"fc_1"}}\n\n',
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"item_1","delta":"{\\"op\\":"}\n\n',
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"item_1","delta":"\\"list\\"}"}\n\n',
        'event: response.output_item.done\n'
        'data: {"type":"response.output_item.done","item":'
        '{"id":"item_1","type":"function_call","name":"calendar","call_id":"fc_1","arguments":"{\\"op\\":\\"list\\"}"}}\n\n',
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{}}\n\n',
        'data: [DONE]\n\n',
    ]
    events = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 1, f"expected exactly one tool_call event, got {events}"
    tc = tool_calls[0]
    assert tc["name"] == "calendar"
    assert tc["args"] == {"op": "list"}
    assert tc["id"] == "fc_1"


@pytest.mark.asyncio
async def test_parser_handles_chunk_boundaries_inside_a_data_line() -> None:
    """The HTTP body can split arbitrarily — the parser must reassemble
    a data: line that spans two chunks. Without this the LLM would
    occasionally drop tokens at chunk boundaries."""
    from services.codex_chat_model import _iter_codex_sse_events

    # 'Hello' delta is split across two chunks.
    chunks = [
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.de',
        'lta","delta":"Hello"}\n\n'
        'data: [DONE]\n\n',
    ]
    events = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]
    text_deltas = [e["text"] for e in events if e["type"] == "text_delta"]
    assert text_deltas == ["Hello"], f"chunk-boundary reassembly failed: {events}"


@pytest.mark.asyncio
async def test_parser_skips_malformed_data_lines_without_crashing() -> None:
    from services.codex_chat_model import _iter_codex_sse_events

    chunks = [
        'data: {not valid json}\n\n'
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        'data: [DONE]\n\n',
    ]
    events = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]
    text_deltas = [e["text"] for e in events if e["type"] == "text_delta"]
    assert text_deltas == ["ok"]
