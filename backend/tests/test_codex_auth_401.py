"""A Codex 401 gets one refresh and one retry, in one place, with an
honest message (2026-09-25 production audit).

Before: ``ChatCodexResponses._attempt_with_auth`` refreshed and retried,
then the tool loop (``_invoke_with_retry`` and the streaming loop) saw
"401" in the message and refreshed and retried again — up to four
requests and three refreshes per call. During the outage the service
answered 401 "Incorrect API key provided" to perfectly good tokens, and
every turn told Ellis to "run codex login", which would not have helped.

Now:

* the error that leaves ``_attempt_with_auth`` is a ``CodexAuthError``
  with ``refresh_attempted = True``; the tool loop does not refresh again;
* when the shared (single-flight) refresh hands back the very token that
  was just rejected, there is no second request;
* a 401 whose body says "Incorrect API key provided" even after a refresh
  is ``transient``: the message says it is a service-side auth fault, not
  "run codex login".
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tests.test_codex_stream_errors import (  # noqa: F401  (ccm is a fixture)
    FAKE_TOKEN, _FakeStreamResponse, _client, _completed, _llm, _text, ccm,
)

pytestmark = pytest.mark.unit

INCORRECT_KEY = ('{"error": {"message": "Incorrect API key provided: eyJhbGci***. You can find your API key at '
                 'https://platform.openai.com/account/api-keys.", "type": "invalid_request_error", '
                 '"code": "invalid_api_key"}}')
EXPIRED = '{"error": {"message": "Provided authentication token is expired.", "code": "token_expired"}}'


class _FakeManager:
    def __init__(self, token_after: str | None = "fresh-token", ok: bool = True):
        self.token_after = token_after
        self.ok = ok
        self.refreshes = 0

    async def force_refresh(self) -> bool:
        self.refreshes += 1
        return self.ok

    def get_api_key(self):
        return self.token_after


@pytest.fixture
def mgr(ccm, monkeypatch):
    m = _FakeManager()
    monkeypatch.setattr(ccm, "get_codex_auth_manager", lambda: m)
    return m


async def test_a_401_then_a_200_refreshes_once_and_retries_with_the_new_token(ccm, monkeypatch, mgr):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=401, error_body=EXPIRED),
        _FakeStreamResponse(sse_lines=[_text("Hi."), _completed()]),
    ])
    result = await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert result.generations[0].message.content == "Hi."
    assert mgr.refreshes == 1
    assert client.calls[1]["headers"]["Authorization"] == "Bearer fresh-token"


async def test_incorrect_api_key_after_a_refresh_is_a_transient_service_fault(ccm, monkeypatch, mgr):
    client = _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=401, error_body=INCORRECT_KEY),
        _FakeStreamResponse(status_code=401, error_body=INCORRECT_KEY),
    ])
    with pytest.raises(ccm.CodexAuthError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    err = info.value
    assert err.refresh_attempted is True
    assert err.transient is True
    assert "codex login" not in str(err).lower()
    assert "service" in str(err).lower()
    assert "401" in str(err)
    assert mgr.refreshes == 1
    assert len(client.calls) == 2


async def test_a_refreshed_token_that_is_also_rejected_still_says_codex_login(ccm, monkeypatch, mgr):
    _client(ccm, monkeypatch, [
        _FakeStreamResponse(status_code=401, error_body=EXPIRED),
        _FakeStreamResponse(status_code=401, error_body=EXPIRED),
    ])
    with pytest.raises(ccm.CodexAuthError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert info.value.transient is False
    assert info.value.refresh_attempted is True
    assert "codex login" in str(info.value)


async def test_a_shared_refresh_that_returns_the_rejected_token_is_not_retried(ccm, monkeypatch, mgr):
    """Another call refreshed a moment ago and this call already used that
    token: the single-flight refresh reuses it, so a retry would send the
    same token the service just refused."""
    mgr.token_after = FAKE_TOKEN
    client = _client(ccm, monkeypatch, [_FakeStreamResponse(status_code=401, error_body=INCORRECT_KEY)])
    with pytest.raises(ccm.CodexAuthError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert len(client.calls) == 1
    assert info.value.transient is True


async def test_a_failed_refresh_raises_once_without_a_retry(ccm, monkeypatch, mgr):
    mgr.ok = False
    client = _client(ccm, monkeypatch, [_FakeStreamResponse(status_code=401, error_body=EXPIRED)])
    with pytest.raises(ccm.CodexAuthError) as info:
        await _llm(ccm)._agenerate([HumanMessage(content="hi")])
    assert info.value.refresh_attempted is True
    assert "codex login" in str(info.value)
    assert len(client.calls) == 1


def test_auth_errors_are_value_errors_for_existing_handlers(ccm):
    assert issubclass(ccm.CodexAuthError, ValueError)


# ----------------------------------------------- the tool loop's own retry --

class _Refresher:
    def __init__(self):
        self.calls = 0

    async def force_refresh(self):
        self.calls += 1
        return True


@pytest.fixture
def refresher(monkeypatch):
    from services import codex_auth_service

    r = _Refresher()
    monkeypatch.setattr(codex_auth_service, "get_codex_auth_manager", lambda: r)
    return r


class _Raiser:
    def __init__(self, exc):
        self.exc = exc

    async def ainvoke(self, messages):
        raise self.exc


async def test_invoke_with_retry_does_not_refresh_after_the_model_already_did(ccm, refresher):
    from services.graph import streaming

    err = ccm.CodexAuthError("Codex API error (HTTP 401): refreshed token also rejected.", transient=True)
    with pytest.raises(ccm.CodexAuthError) as info:
        await streaming._invoke_with_retry(_Raiser(err), [HumanMessage(content="hi")], "openai", "gpt-6-astra",
                                           0.5, [])
    assert info.value is err, "the honest error reaches the caller unchanged"
    assert refresher.calls == 0


async def test_invoke_with_retry_still_refreshes_a_raw_401(refresher, monkeypatch):
    """A 401 that no layer below refreshed (the API-key ``ChatOpenAI`` path)
    still gets the loop's one refresh and retry."""
    from services.graph import streaming

    class _Ok:
        async def ainvoke(self, messages):
            return AIMessage(content="ok")

    monkeypatch.setattr(streaming, "_build_llm", lambda *a, **k: _Ok())
    out = await streaming._invoke_with_retry(_Raiser(ValueError("Error code: 401 - Unauthorized")),
                                             [HumanMessage(content="hi")], "openai", "gpt-6-astra", 0.5, [])
    assert out.content == "ok"
    assert refresher.calls == 1


def test_auth_refresh_warranted_is_pure(ccm):
    from services.graph.streaming import _auth_refresh_warranted

    assert _auth_refresh_warranted(ValueError("HTTP 401 Unauthorized")) is True
    assert _auth_refresh_warranted(ValueError("Unauthorized")) is True
    assert _auth_refresh_warranted(ValueError("HTTP 500")) is False
    assert _auth_refresh_warranted(ccm.CodexAuthError("Codex API error (HTTP 401): x")) is False


async def test_the_streaming_loop_does_not_refresh_after_the_model_already_did(ccm, refresher, monkeypatch):
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, base_kwargs, patch_loop_io, run_stream

    err = ccm.CodexAuthError("Codex API error (HTTP 401): refreshed token also rejected.", transient=True)
    patch_loop_io(monkeypatch, tools=[FakeTool("web_search")], script=[err, AIMessage(content="never")])
    try:
        await run_stream(message="hello", **base_kwargs(FakeGraph()))
    except Exception:  # noqa: BLE001 — the loop may raise or report; either way no refresh
        pass
    assert refresher.calls == 0
