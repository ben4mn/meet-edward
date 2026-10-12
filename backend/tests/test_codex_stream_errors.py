"""D8 (muse-and-beyond plan, 2026-09-24): a failed stream is an error,
never an empty answer.

The SSE parser used to ignore ``response.failed``, ``response.incomplete``
and ``error`` events, so a server error looked like an empty completion.
The tool loop then nudged, and a turn could end on the canned "I completed
the requested actions…" — Edward saying he did something he did not do.
Unparseable function-call arguments silently became ``{}``, so a tool ran
with no arguments at all.

Now:

* ``response.failed`` / ``error`` raise ``CodexStreamError(code, message,
  retryable)``; a stream that ends before ``response.completed`` is
  ``stream_truncated``; a connection reset mid-stream is ``connection_error``.
* Retryable failures (``server_error``, overloaded / 5xx-like codes,
  truncation, resets) are retried **once** after a short jittered backoff —
  in ``_astream`` only while nothing has reached the consumer yet.
* ``response.incomplete`` for ``max_output_tokens`` returns what was
  produced, marked ``additional_kwargs["incomplete_reason"]``; any other
  incomplete reason is an error.
* Bad argument JSON becomes ``{"__invalid_json__": raw[:2000]}`` (contract 2
  in the agent brief) on every path.
* A total per-request deadline (``CODEX_REQUEST_TIMEOUT_SECONDS``, default
  300) on top of httpx's per-read timeout.
"""

from __future__ import annotations

import asyncio
import json
from typing import List

import httpx
import pytest
from langchain_core.messages import HumanMessage

pytestmark = pytest.mark.unit

_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


def _text(delta: str) -> str:
    return _sse({"type": "response.output_text.delta", "delta": delta})


def _completed(usage=None) -> str:
    return _sse({"type": "response.completed", "response": {
        "model": "gpt-6-astra", "status": "completed", "output": [],
        "usage": usage or {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }})


def _failed(code: str, message: str = "boom") -> str:
    return _sse({"type": "response.failed", "response": {
        "id": "resp_x", "status": "failed", "error": {"code": code, "message": message},
    }})


class _FakeStreamResponse:
    def __init__(self, status_code: int = 200, sse_lines: List = (), error_body: str = "", raise_after=None, delay=None):
        self.status_code = status_code
        self._sse_lines = list(sse_lines)
        self._error_body = error_body
        self._raise_after = raise_after
        self._delay = delay
        self.headers = {}

    async def aiter_bytes(self):
        if self._error_body:
            yield self._error_body.encode("utf-8")

    async def aiter_text(self):
        for line in self._sse_lines:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield line
        if self._raise_after is not None:
            raise self._raise_after


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
        if not self._responses:
            raise AssertionError("stream called more times than scripted")
        return _FakeStreamCM(self._responses.pop(0))


@pytest.fixture
def ccm(monkeypatch):
    from services import codex_chat_model

    monkeypatch.setattr(codex_chat_model, "RETRY_BACKOFF_MIN_S", 0.0)
    monkeypatch.setattr(codex_chat_model, "RETRY_BACKOFF_MAX_S", 0.0)
    return codex_chat_model


def _client(ccm, monkeypatch, responses):
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(ccm.httpx, "AsyncClient", client)
    return client


def _llm(ccm):
    return ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)


# ------------------------------------------------------------ failures --

@pytest.mark.asyncio
async def test_response_failed_server_error_is_retried_once_then_answers(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_failed("server_error", "An error occurred while processing your request.")]),
        _FakeStreamResponse(sse_lines=[_text("Done."), _completed()]),
    ])
    result = await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert result.generations[0].message.content == "Done."
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_a_second_failure_raises_a_typed_error_and_never_an_empty_answer(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_failed("server_error")]),
        _FakeStreamResponse(sse_lines=[_failed("server_error")]),
    ])
    with pytest.raises(ccm.CodexStreamError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert info.value.code == "server_error"
    assert info.value.retryable is True
    assert len(client.calls) == 2, "retried exactly once"
    assert "I completed the requested actions" not in str(info.value)


@pytest.mark.asyncio
async def test_a_non_retryable_failure_raises_at_once(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_failed("context_length_exceeded", "Your input exceeds the context window")]),
    ])
    with pytest.raises(ccm.CodexStreamError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert info.value.code == "context_length_exceeded"
    assert info.value.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_failures_are_left_to_the_gate_not_retried(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_failed("rate_limit_exceeded", "Rate limit reached. Please try again in 2s")]),
    ])
    with pytest.raises(ccm.CodexStreamError):
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_error_event_raises_in_both_shapes(ccm, monkeypatch):
    _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_sse({"type": "error", "code": "invalid_prompt", "message": "bad", "param": None})]),
        _FakeStreamResponse(sse_lines=[_sse({"type": "error", "error": {"type": "invalid_request_error",
                                                                        "code": "invalid_value", "message": "nope"}})]),
    ])
    with pytest.raises(ccm.CodexStreamError) as first:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert first.value.code == "invalid_prompt" and first.value.message == "bad"
    with pytest.raises(ccm.CodexStreamError) as second:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert second.value.code == "invalid_value"


@pytest.mark.asyncio
async def test_a_stream_that_ends_before_completed_is_truncated_and_retried(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_sse({"type": "response.created", "response": {}})]),
        _FakeStreamResponse(sse_lines=[_text("ok"), _completed()]),
    ])
    result = await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert result.generations[0].message.content == "ok"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_a_connection_reset_mid_stream_is_retried(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_sse({"type": "response.created", "response": {}})],
                            raise_after=httpx.RemoteProtocolError("peer closed connection without sending complete message body")),
        _FakeStreamResponse(sse_lines=[_text("ok"), _completed()]),
    ])
    result = await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert result.generations[0].message.content == "ok"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_astream_does_not_retry_once_text_has_reached_the_consumer(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_text("Half an ans")], raise_after=httpx.ReadError("Connection reset by peer")),
        _FakeStreamResponse(sse_lines=[_text("never sent"), _completed()]),
    ])
    seen = []
    with pytest.raises(ccm.CodexStreamError) as info:
        async for chunk in _llm(ccm)._astream([HumanMessage(content="hi")]):
            seen.append(chunk.message.content)
    assert info.value.code == "connection_error"
    assert "".join(seen) == "Half an ans", "no replayed text"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_astream_retries_when_nothing_was_yielded_yet(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_failed("server_error")]),
        _FakeStreamResponse(sse_lines=[_text("fine"), _completed()]),
    ])
    text = "".join([c.message.content async for c in _llm(ccm)._astream([HumanMessage(content="hi")])
                    if isinstance(c.message.content, str)])
    assert text == "fine"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_incomplete_for_max_output_tokens_returns_what_was_produced(ccm, monkeypatch):
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=[
        _text("The first half"),
        _sse({"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "output": [], "usage": {"input_tokens": 50, "output_tokens": 4096, "total_tokens": 4146},
        }}),
    ])])
    msg = (await _llm(ccm)._agenerate([HumanMessage(content="hi")])).generations[0].message
    assert msg.content == "The first half"
    assert msg.additional_kwargs["incomplete_reason"] == "max_output_tokens"
    assert msg.usage_metadata["output_tokens"] == 4096


@pytest.mark.asyncio
async def test_incomplete_for_any_other_reason_is_an_error(ccm, monkeypatch):
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=[
        _sse({"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": [],
        }}),
    ])])
    with pytest.raises(ccm.CodexStreamError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert "content_filter" in info.value.code
    assert info.value.retryable is False


# ---------------------------------------------------------- bad JSON --

async def _aiter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_invalid_arguments_json_is_marked_not_silently_emptied():
    from services.codex_chat_model import _iter_codex_sse_events

    raw = '{"to": "+1555", "message": "hi'  # cut off
    events = [e async for e in _iter_codex_sse_events(_aiter([
        _sse({"type": "response.output_item.done", "item": {
            "id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "send_imessage", "arguments": raw}}),
        _completed(),
    ]))]
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls[0]["args"] == {"__invalid_json__": raw}


@pytest.mark.asyncio
async def test_invalid_arguments_json_in_the_completed_fallback_and_the_tail():
    from services.codex_chat_model import _iter_codex_sse_events

    long_raw = "{" + "x" * 5000
    completed_only = [e async for e in _iter_codex_sse_events(_aiter([
        _sse({"type": "response.completed", "response": {"output": [
            {"id": "fc_2", "type": "function_call", "call_id": "call_2", "name": "remember", "arguments": long_raw},
        ]}}),
    ]))]
    args = [e for e in completed_only if e["type"] == "tool_call"][0]["args"]
    assert args == {"__invalid_json__": long_raw[:2000]}

    tail = [e async for e in _iter_codex_sse_events(_aiter([
        _sse({"type": "response.output_item.added", "item": {"id": "fc_3", "type": "function_call",
                                                              "name": "browser_click", "call_id": "call_3"}}),
        _sse({"type": "response.function_call_arguments.delta", "item_id": "fc_3", "delta": "{\"ref\": 4"}),
        _sse({"type": "response.completed", "response": {"output": []}}),
    ]))]
    assert [e for e in tail if e["type"] == "tool_call"][0]["args"] == {"__invalid_json__": "{\"ref\": 4"}


@pytest.mark.asyncio
async def test_empty_arguments_mean_no_arguments():
    from services.codex_chat_model import _iter_codex_sse_events

    events = [e async for e in _iter_codex_sse_events(_aiter([
        _sse({"type": "response.output_item.done", "item": {
            "id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "browser_tabs", "arguments": ""}}),
        _completed(),
    ]))]
    assert [e for e in events if e["type"] == "tool_call"][0]["args"] == {}


@pytest.mark.asyncio
async def test_arguments_that_are_json_but_not_an_object_are_invalid():
    from services.codex_chat_model import _iter_codex_sse_events

    events = [e async for e in _iter_codex_sse_events(_aiter([
        _sse({"type": "response.output_item.done", "item": {
            "id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "remember", "arguments": "[1, 2]"}}),
        _completed(),
    ]))]
    assert [e for e in events if e["type"] == "tool_call"][0]["args"] == {"__invalid_json__": "[1, 2]"}


# ----------------------------------------------------------- deadline --

@pytest.mark.asyncio
async def test_a_total_deadline_bounds_a_slow_stream(ccm, monkeypatch):
    monkeypatch.setenv("CODEX_REQUEST_TIMEOUT_SECONDS", "0.3")
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(sse_lines=[_text("a"), _text("b"), _text("c"), _completed()], delay=0.2),
    ])
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(ccm.CodexStreamError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert info.value.code == "request_timeout"
    assert info.value.retryable is False
    assert loop.time() - t0 < 2.0
    assert len(client.calls) == 1


def test_the_deadline_defaults_to_five_minutes(ccm, monkeypatch):
    monkeypatch.delenv("CODEX_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert ccm.request_timeout_seconds() == 300.0
    monkeypatch.setenv("CODEX_REQUEST_TIMEOUT_SECONDS", "nonsense")
    assert ccm.request_timeout_seconds() == 300.0
    monkeypatch.setenv("CODEX_REQUEST_TIMEOUT_SECONDS", "90")
    assert ccm.request_timeout_seconds() == 90.0


def test_classify_retryable_codes():
    from services.codex_chat_model import is_retryable_stream_code

    for code in ("server_error", "internal_error", "overloaded", "server_is_overloaded",
                 "service_unavailable", "502", "stream_truncated", "connection_error"):
        assert is_retryable_stream_code(code), code
    for code in ("rate_limit_exceeded", "insufficient_quota", "usage_limit_reached", "context_length_exceeded",
                 "invalid_prompt", "request_timeout", "incomplete:content_filter", ""):
        assert not is_retryable_stream_code(code), code
