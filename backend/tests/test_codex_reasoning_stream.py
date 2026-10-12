"""
N4: Codex Responses API emits reasoning_summary deltas alongside
text deltas when ``reasoning.summary='auto'`` (already configured
in _build_request). Today _iter_codex_sse_events only surfaces
text — the reasoning is silently dropped.

Surfacing it lets the UI show "Edward is reasoning..." with actual
content, rather than an opaque thinking spinner. The user-perceived
"long wait before any output" gets replaced by streaming reasoning
text.

Two layers:

* Parser yields ``{"type": "reasoning_delta", "text": ...}`` events.
* ``_astream`` emits them as ``ChatGenerationChunk`` whose message
  has the reasoning text in ``additional_kwargs["reasoning_delta"]``.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List

import pytest
from langchain_core.messages import HumanMessage


_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"


async def _async_iter(items):
    for x in items:
        yield x


@pytest.mark.asyncio
async def test_parser_yields_reasoning_deltas() -> None:
    from services.codex_chat_model import _iter_codex_sse_events

    chunks = [
        'event: response.reasoning_summary_text.delta\n'
        'data: {"type":"response.reasoning_summary_text.delta","delta":"Considering "}\n\n',
        'event: response.reasoning_summary_text.delta\n'
        'data: {"type":"response.reasoning_summary_text.delta","delta":"the request..."}\n\n',
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n',
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{}}\n\n'
        'data: [DONE]\n\n',
    ]
    events = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]

    reasoning = [e for e in events if e["type"] == "reasoning_delta"]
    assert [r["text"] for r in reasoning] == ["Considering ", "the request..."], (
        f"expected reasoning deltas in order, got: {events}"
    )

    text = [e for e in events if e["type"] == "text_delta"]
    assert [t["text"] for t in text] == ["Hello"], (
        "regular text deltas must continue to work alongside reasoning"
    )


class _FakeStreamResponse:
    def __init__(self, status_code, sse_lines, error_body=""):
        self.status_code = status_code
        self._sse = sse_lines
        self._error = error_body

    async def aiter_bytes(self):
        if self._error:
            yield self._error.encode()

    async def aiter_text(self):
        for line in self._sse:
            yield line


class _FakeStreamCM:
    def __init__(self, r):
        self._r = r

    async def __aenter__(self):
        return self._r

    async def __aexit__(self, *exc):
        return None


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, *a, **kw):
        return _FakeStreamCM(self._responses.pop(0))


@pytest.mark.asyncio
async def test_astream_surfaces_reasoning_in_chunk_metadata(monkeypatch):
    from services import codex_chat_model

    sse = [
        'event: response.reasoning_summary_text.delta\n'
        'data: {"type":"response.reasoning_summary_text.delta","delta":"Thinking step 1. "}\n\n',
        'event: response.reasoning_summary_text.delta\n'
        'data: {"type":"response.reasoning_summary_text.delta","delta":"Thinking step 2. "}\n\n',
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"Done."}\n\n',
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":{}}}\n\n'
        'data: [DONE]\n\n',
    ]
    fake_client = _FakeAsyncClient([_FakeStreamResponse(200, sse_lines=sse)])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4", temperature=0, max_tokens=64, api_key=FAKE_TOKEN,
    )

    chunks = [c async for c in llm._astream([HumanMessage(content="hi")])]

    reasoning_pieces = []
    text_pieces = []
    for ch in chunks:
        msg = ch.message
        ak = getattr(msg, "additional_kwargs", None) or {}
        rd = ak.get("reasoning_delta")
        if rd:
            reasoning_pieces.append(rd)
        if isinstance(msg.content, str) and msg.content:
            text_pieces.append(msg.content)

    assert reasoning_pieces == ["Thinking step 1. ", "Thinking step 2. "], (
        f"expected reasoning surfaced in additional_kwargs, got reasoning={reasoning_pieces}"
    )
    assert text_pieces == ["Done."], "regular text path must still work"
