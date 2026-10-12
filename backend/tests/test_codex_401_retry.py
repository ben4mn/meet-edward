"""
Regression: ChatCodexResponses must recover from a 401 token_expired by
calling CodexAuthManager.force_refresh() and retrying the request once.

Failure signature in /tmp/edward-backend.log before the fix:

    Codex API error (HTTP 401): {"error": {"message": "Provided
    authentication token is expired. ...", "code": "token_expired"}}

The bug is structural: ChatCodexResponses.api_key is captured at
construction time, so even if the singleton CodexAuthManager refreshes
proactively (every ~50 min), the in-memory chat model keeps the stale
token forever. Every background-LLM caller (heartbeat triage,
consolidation, reflection, deep retrieval, search tags, memory
extraction) silently breaks until the process restarts.

This test stubs httpx so the first request returns 401 and the second
returns a tiny SSE stream with the text "hi", and asserts:
  1. force_refresh() is called exactly once
  2. The chat model retries with the refreshed token
  3. The final AIMessage content is "hi"
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage


# A minimal valid JWT-shaped token so _resolve_account_id() can decode the
# middle segment without raising. The middle segment base64-decodes to
# {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_test"}}.
_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN_EXPIRED = f"hdr.{_JWT_PAYLOAD}.sig-expired"
FAKE_TOKEN_FRESH = f"hdr.{_JWT_PAYLOAD}.sig-fresh"


class _FakeStreamResponse:
    """Mimics httpx response inside `async with client.stream(...) as resp`."""

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
    """Replacement for httpx.AsyncClient that returns scripted responses."""

    def __init__(self, responses: List[_FakeStreamResponse]):
        self._responses = list(responses)
        self.calls: List[dict] = []

    def __call__(self, *args, **kwargs):
        return self  # `httpx.AsyncClient(timeout=120)` — instance returned

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


@pytest.mark.asyncio
async def test_chat_codex_retries_on_401_token_expired(monkeypatch):
    from services import codex_chat_model
    from services import codex_auth_service

    # Wire up a fake auth manager singleton. force_refresh "rotates" the
    # api_key; the chat model is expected to pick the fresh value off the
    # manager after force_refresh succeeds.
    fake_mgr = MagicMock()
    fake_mgr.get_api_key = MagicMock(side_effect=[FAKE_TOKEN_FRESH])
    fake_mgr.force_refresh = AsyncMock(return_value=True)

    monkeypatch.setattr(codex_chat_model, "get_codex_auth_manager", lambda: fake_mgr)
    monkeypatch.setattr(codex_auth_service, "get_codex_auth_manager", lambda: fake_mgr)

    expired_body = json.dumps({
        "error": {
            "message": "Provided authentication token is expired. Please sign in again.",
            "code": "token_expired",
        },
        "status": 401,
    })
    fake_client = _FakeAsyncClient([
        _FakeStreamResponse(401, sse_lines=[], error_body=expired_body),
        _FakeStreamResponse(200, sse_lines=_DONE_SSE),
    ])
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", fake_client)

    llm = codex_chat_model.ChatCodexResponses(
        model="gpt-5.4",
        temperature=0,
        max_tokens=64,
        api_key=FAKE_TOKEN_EXPIRED,
    )

    result = await llm._agenerate([HumanMessage(content="say hi")])

    assert fake_mgr.force_refresh.await_count == 1, (
        "force_refresh should be awaited exactly once on 401"
    )
    assert len(fake_client.calls) == 2, (
        f"expected exactly two HTTP attempts (1 fail + 1 retry), got {len(fake_client.calls)}"
    )
    assert fake_client.calls[1]["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN_FRESH}", (
        "retry must use the refreshed token, not the stale one"
    )
    assert result.generations[0].message.content == "hi"
