"""
S2 — second half: ChatCodexResponses._astream yields one
ChatGenerationChunk per response.output_text.delta event from Codex's
SSE stream, instead of buffering the whole response in _agenerate.

Without this, even calling LLM.astream(...) on a Codex chat model
falls back to LangChain's BaseChatModel.astream → ainvoke (one giant
chunk). Edward's chat path is the consumer; once this lands, S1 can
forward those chunks to the user-facing SSE pipeline.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage


_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"


class _FakeStreamResponse:
    def __init__(self, status_code: int, sse_lines: List[str], error_body: str = ""):
        self.status_code = status_code
        self._sse_lines = sse_lines
        self._error_body = error_body

    async def aiter_bytes(self):
        if self._error_body:
            yield self._error_body.encode("utf-8")

    async def aiter_text(self):
        for line in self._sse_lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return None


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method, url, *, json, headers):
        self.calls.append({"json": json, "headers": headers})
        return _FakeStreamCM(self._responses.pop(0))


def _delta_event(text: str) -> str:
    return (
        'event: response.output_text.delta\n'
        f'data: {json.dumps({"type": "response.output_text.delta", "delta": text})}\n\n'
    )


def _completed_event() -> str:
    return (
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":{}}}\n\n'
        'data: [DONE]\n\n'
    )


@pytest.mark.asyncio
async def test_astream_yields_one_chunk_per_text_delta(monkeypatch):
    from services import codex_chat_model

    sse = [
        _delta_event("Hello"),
        _delta_event(", "),
        _delta_event("world"),
        _delta_event("!"),
        _completed_event(),
    ]
    fake_client = _FakeAsyncClient([_FakeStreamResponse(200, sse_lines=sse)])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4", temperature=0, max_tokens=64, api_key=FAKE_TOKEN,
    )

    chunks = [c async for c in llm._astream([HumanMessage(content="hi")])]

    text_pieces = []
    for chunk in chunks:
        msg = chunk.message
        if msg.content:
            text_pieces.append(msg.content if isinstance(msg.content, str)
                               else "".join(b.get("text", "") for b in msg.content if isinstance(b, dict)))
    assert "".join(text_pieces) == "Hello, world!", (
        f"reassembled stream should equal full text. got pieces: {text_pieces}"
    )
    # And — most importantly — the deltas must arrive as SEPARATE chunks,
    # not one mega-chunk. That's the whole point of _astream.
    nonempty_text_chunks = [p for p in text_pieces if p]
    assert len(nonempty_text_chunks) >= 4, (
        f"expected >=4 separate text chunks (one per delta), got {len(nonempty_text_chunks)}: "
        f"{nonempty_text_chunks}"
    )


@pytest.mark.asyncio
async def test_astream_surfaces_tool_calls_in_final_chunk(monkeypatch):
    from services import codex_chat_model

    sse = [
        _delta_event("ok"),
        ('event: response.output_item.added\n'
         'data: {"type":"response.output_item.added","item":'
         '{"id":"item_1","type":"function_call","name":"calendar","call_id":"fc_1"}}\n\n'),
        ('event: response.function_call_arguments.delta\n'
         'data: {"type":"response.function_call_arguments.delta","item_id":"item_1","delta":"{\\"op\\":\\"list\\"}"}\n\n'),
        ('event: response.output_item.done\n'
         'data: {"type":"response.output_item.done","item":'
         '{"id":"item_1","type":"function_call","name":"calendar","call_id":"fc_1","arguments":"{\\"op\\":\\"list\\"}"}}\n\n'),
        _completed_event(),
    ]
    fake_client = _FakeAsyncClient([_FakeStreamResponse(200, sse_lines=sse)])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4", temperature=0, max_tokens=64, api_key=FAKE_TOKEN,
    )

    chunks = [c async for c in llm._astream([HumanMessage(content="hi")])]

    # Collect tool calls from any chunk that carries them
    all_tool_calls = []
    for c in chunks:
        msg = c.message
        for tc in (getattr(msg, "tool_call_chunks", None) or []):
            all_tool_calls.append(tc)
        for tc in (getattr(msg, "tool_calls", None) or []):
            all_tool_calls.append(tc)
    assert any(
        (tc.get("name") == "calendar") for tc in all_tool_calls
    ), f"tool_call should surface via _astream chunks, got: {all_tool_calls}"
