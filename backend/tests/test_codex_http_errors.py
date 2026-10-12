"""Codex HTTP 5xx answers are transient and retried once (2026-09-25 audit).

``_stream_attempt`` turned every non-200 it had no special case for into a
plain ``ValueError("Codex API error (HTTP 503): …")``. The single jittered
retry in ``_astream`` / ``_agenerate`` only catches ``CodexStreamError``, so
a 500/502/503/504 — the most transient failure there is — failed the turn
at once, while the same outage reported as a ``response.failed`` event was
retried. Now a 5xx is ``CodexStreamError(f"http_{status}", body,
retryable=True, http_status=status)``; other statuses are unchanged.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from tests.test_codex_stream_errors import (  # noqa: F401  (ccm is a fixture)
    _FakeStreamResponse, _client, _completed, _llm, _text, ccm,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_5xx_then_a_200_answers(ccm, monkeypatch, status):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=status, error_body="upstream connect error"),
        _FakeStreamResponse(sse_lines=[_text("Here."), _completed()]),
    ])
    result = await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert result.generations[0].message.content == "Here."
    assert len(client.calls) == 2


async def test_the_streaming_path_retries_a_503_before_anything_was_yielded(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=503, error_body="Service Unavailable"),
        _FakeStreamResponse(sse_lines=[_text("Back."), _completed()]),
    ])
    chunks = [c async for c in _llm(ccm)._astream([HumanMessage(content="hi")])]
    assert "".join(c.message.content for c in chunks if isinstance(c.message.content, str)) == "Back."
    assert len(client.calls) == 2


async def test_two_5xx_raise_a_typed_retryable_error_after_one_retry(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=502, error_body="Bad Gateway"),
        _FakeStreamResponse(status_code=502, error_body="Bad Gateway"),
    ])
    with pytest.raises(ccm.CodexStreamError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert info.value.code == "http_502"
    assert info.value.retryable is True
    assert info.value.http_status == 502
    assert "Bad Gateway" in info.value.message
    assert len(client.calls) == 2


async def test_other_statuses_are_not_retried(ccm, monkeypatch):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=400, error_body='{"detail": "bad request"}'),
    ])
    with pytest.raises(ValueError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert not isinstance(info.value, ccm.CodexStreamError)
    assert "HTTP 400" in str(info.value)
    assert len(client.calls) == 1
