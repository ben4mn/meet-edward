"""
Regression: ChatCodexResponses must trigger force_refresh + retry on
ANY HTTP 401 from the Codex endpoint, not only the ``token_expired``
variant that the original retry shim was written for.

Failure signature we hit in prod (graph-worker queue + main backend
log, June 2026):

    ValueError: Codex API error (HTTP 401): {
      "error": {
        "message": "Your authentication token has been invalidated. ...",
        "code": "token_invalidated"
      },
      "status": 401
    }

The body says ``token_invalidated`` — the OAuth token was revoked
server-side (sign-out elsewhere, OAuth session lifetime, etc.). The
existing matcher (`"token_expired" in error_body`) does NOT match this
string, so the request escapes the retry shim and the entire
background-LLM pipeline (graph ingestion, heartbeat, consolidation,
reflection, deep retrieval, search tags, memory extraction) dies on
the first 401 until the process restarts.

The fix must treat any 401 as "auth needs a refresh" rather than
string-matching one variant — server-side error codes are not stable
contract.

A second test pins the no-loop guarantee: a second 401 after refresh
must NOT trigger another refresh+retry. Otherwise a permanently dead
token (e.g. revoked refresh) would burn an infinite loop instead of
surfacing a clean error.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage


_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN_STALE = f"hdr.{_JWT_PAYLOAD}.sig-stale"
FAKE_TOKEN_FRESH = f"hdr.{_JWT_PAYLOAD}.sig-fresh"


class _FakeStreamResponse:
    def __init__(self, status_code: int, sse_lines: List[str], error_body: str = ""):
        self.status_code = status_code
        self._sse_lines = sse_lines
        self._error_body = error_body

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        if self._error_body:
            yield self._error_body.encode("utf-8")

    async def aiter_text(self) -> AsyncIterator[str]:
        for line in self._sse_lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, responses: List[_FakeStreamResponse]):
        self._responses = list(responses)
        self.calls: List[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def stream(self, method: str, url: str, *, json: dict, headers: dict):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        if not self._responses:
            raise AssertionError("FakeAsyncClient.stream called more times than scripted")
        return _FakeStreamCM(self._responses.pop(0))


_DONE_SSE = [
    'event: response.output_text.delta\n',
    'data: {"type":"response.output_text.delta","delta":"hi"}\n',
    '\n',
    'event: response.completed\n',
    'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":{}}}\n',
    '\n',
    'data: [DONE]\n',
]


_INVALIDATED_BODY = json.dumps({
    "error": {
        "message": "Your authentication token has been invalidated. Please try signing in again.",
        "type": "invalid_request_error",
        "code": "token_invalidated",
        "param": None,
    },
    "status": 401,
})


_GENERIC_401_BODY = json.dumps({
    "error": {
        "message": "Unauthorized.",
        "type": "invalid_request_error",
    },
    "status": 401,
})


@pytest.mark.asyncio
async def test_retries_on_401_token_invalidated(monkeypatch):
    """The production failure mode: 401 with ``code: token_invalidated``.
    Must trigger force_refresh + retry, just like token_expired does."""
    from services import codex_chat_model
    from services import codex_auth_service

    fake_mgr = MagicMock()
    fake_mgr.get_api_key = MagicMock(side_effect=[FAKE_TOKEN_FRESH])
    fake_mgr.force_refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(codex_chat_model, "get_codex_auth_manager", lambda: fake_mgr)
    monkeypatch.setattr(codex_auth_service, "get_codex_auth_manager", lambda: fake_mgr)

    fake_client = _FakeAsyncClient([
        _FakeStreamResponse(401, sse_lines=[], error_body=_INVALIDATED_BODY),
        _FakeStreamResponse(200, sse_lines=_DONE_SSE),
    ])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4",
        temperature=0,
        max_tokens=64,
        api_key=FAKE_TOKEN_STALE,
    )

    result = await llm._agenerate([HumanMessage(content="say hi")])

    assert fake_mgr.force_refresh.await_count == 1, (
        f"force_refresh must be called on token_invalidated 401, "
        f"got {fake_mgr.force_refresh.await_count} calls"
    )
    assert len(fake_client.calls) == 2, (
        f"expected 1 fail + 1 retry, got {len(fake_client.calls)} HTTP attempts"
    )
    assert fake_client.calls[1]["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN_FRESH}"
    assert result.generations[0].message.content == "hi"


@pytest.mark.asyncio
async def test_retries_on_401_with_unknown_code(monkeypatch):
    """Server-side OpenAI error codes change over time. Any 401 must be
    treated as 'auth needs a refresh', regardless of body shape."""
    from services import codex_chat_model
    from services import codex_auth_service

    fake_mgr = MagicMock()
    fake_mgr.get_api_key = MagicMock(side_effect=[FAKE_TOKEN_FRESH])
    fake_mgr.force_refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(codex_chat_model, "get_codex_auth_manager", lambda: fake_mgr)
    monkeypatch.setattr(codex_auth_service, "get_codex_auth_manager", lambda: fake_mgr)

    fake_client = _FakeAsyncClient([
        _FakeStreamResponse(401, sse_lines=[], error_body=_GENERIC_401_BODY),
        _FakeStreamResponse(200, sse_lines=_DONE_SSE),
    ])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4",
        temperature=0,
        max_tokens=64,
        api_key=FAKE_TOKEN_STALE,
    )

    result = await llm._agenerate([HumanMessage(content="say hi")])

    assert fake_mgr.force_refresh.await_count == 1
    assert len(fake_client.calls) == 2
    assert result.generations[0].message.content == "hi"


@pytest.mark.asyncio
async def test_does_not_loop_on_repeated_401(monkeypatch):
    """If the refreshed token also 401s (e.g. refresh token itself
    revoked), surface a clean error — do NOT loop refresh+retry forever."""
    from services import codex_chat_model
    from services import codex_auth_service

    fake_mgr = MagicMock()
    fake_mgr.get_api_key = MagicMock(return_value=FAKE_TOKEN_FRESH)
    fake_mgr.force_refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(codex_chat_model, "get_codex_auth_manager", lambda: fake_mgr)
    monkeypatch.setattr(codex_auth_service, "get_codex_auth_manager", lambda: fake_mgr)

    fake_client = _FakeAsyncClient([
        _FakeStreamResponse(401, sse_lines=[], error_body=_INVALIDATED_BODY),
        _FakeStreamResponse(401, sse_lines=[], error_body=_INVALIDATED_BODY),
    ])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4",
        temperature=0,
        max_tokens=64,
        api_key=FAKE_TOKEN_STALE,
    )

    with pytest.raises(ValueError) as exc_info:
        await llm._agenerate([HumanMessage(content="say hi")])

    msg = str(exc_info.value)
    assert "401" in msg, f"final error must surface the 401 so callers know to re-auth: {msg!r}"
    assert fake_mgr.force_refresh.await_count == 1, (
        f"force_refresh must run AT MOST once per request — got "
        f"{fake_mgr.force_refresh.await_count} (would loop forever)"
    )
    assert len(fake_client.calls) == 2, (
        f"expected 1 fail + 1 retry then give up, got {len(fake_client.calls)} attempts"
    )
