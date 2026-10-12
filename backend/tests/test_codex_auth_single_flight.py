"""One Codex token refresh at a time, and only when the token is due
(2026-09-25 production audit).

During one outage hour the backend refreshed the Codex OAuth token 29
times — twice in the same second — because every call that saw a 401 ran
its own refresh: ``ChatCodexResponses._attempt_with_auth`` refreshed and
retried, then the tool loop saw "401" in the message and refreshed and
retried again, and concurrent turns did all of it in parallel. Refresh
tokens rotate, so parallel refreshes race each other for auth.json.

Now ``force_refresh`` is single-flight: callers queue on one lock, a
caller that waited behind a refresh shares its outcome, and a successful
refresh completed under ``REFRESH_REUSE_SECONDS`` (60) ago is reused
without a POST. There is no proactive refresh (2026-09-27): an expired
token (they last ~8 days) is refreshed by the first 401.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _jwt(exp: float | None) -> str:
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_test"}}
    if exp is not None:
        claims["exp"] = int(exp)
    return f"{b64({'alg': 'RS256'})}.{b64(claims)}.sig"


def _auth_file(tmp_path: Path, access: str, refresh: str = "rt-1") -> Path:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
        "tokens": {"id_token": "id", "access_token": access, "refresh_token": refresh, "account_id": "acct_test"},
    }))
    return auth


class _Resp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _SlowClient:
    """``httpx.AsyncClient`` whose POST takes a moment, so two callers overlap."""

    def __init__(self, responses, delay: float = 0.05):
        self._responses = list(responses)
        self._delay = delay
        self.posts: list[dict] = []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, data=None, **kw):
        self.posts.append({"url": url, "data": data})
        await asyncio.sleep(self._delay)
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


@pytest.fixture
def svc(monkeypatch, tmp_path):
    from services import codex_auth_service

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    auth = _auth_file(tmp_path, access=_jwt(time.time() + 8 * 86400))
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", str(auth))
    writes: list[dict] = []
    real_write = codex_auth_service._write_auth_file

    def _counting_write(path, update):
        writes.append(dict(update))
        real_write(path, update)

    monkeypatch.setattr(codex_auth_service, "_write_auth_file", _counting_write)
    codex_auth_service._test_writes = writes
    return codex_auth_service


def _ok(access: str, refresh: str = "rt-2") -> _Resp:
    return _Resp(200, {"access_token": access, "refresh_token": refresh, "id_token": "id-2"})


async def test_two_concurrent_force_refreshes_make_one_post_and_one_write(svc, monkeypatch):
    new_access = _jwt(time.time() + 8 * 86400) + "x"
    client = _SlowClient([_ok(new_access)])
    monkeypatch.setattr(svc.httpx, "AsyncClient", client)
    mgr = svc.CodexAuthManager()

    first, second = await asyncio.gather(mgr.force_refresh(), mgr.force_refresh())

    assert (first, second) == (True, True)
    assert len(client.posts) == 1, "the second caller must share the first refresh"
    assert len(svc._test_writes) == 1, "auth.json written once"
    assert mgr.get_api_key() == new_access


async def test_a_refresh_completed_under_a_minute_ago_is_reused(svc, monkeypatch):
    client = _SlowClient([_ok("tok-2", "rt-2"), _ok("tok-3", "rt-3")], delay=0)
    monkeypatch.setattr(svc.httpx, "AsyncClient", client)
    mgr = svc.CodexAuthManager()

    assert await mgr.force_refresh() is True
    assert await mgr.force_refresh() is True
    assert len(client.posts) == 1
    assert mgr.get_api_key() == "tok-2"

    # A minute later a 401 may mean something new: refresh again.
    mgr._last_refresh_ok_at -= svc.REFRESH_REUSE_SECONDS + 1
    assert await mgr.force_refresh() is True
    assert len(client.posts) == 2
    assert mgr.get_api_key() == "tok-3"


async def test_callers_that_waited_behind_a_failed_refresh_share_the_failure(svc, monkeypatch):
    client = _SlowClient([_Resp(400, {"error": "invalid_grant"})])
    monkeypatch.setattr(svc.httpx, "AsyncClient", client)
    mgr = svc.CodexAuthManager()

    results = await asyncio.gather(mgr.force_refresh(), mgr.force_refresh(), mgr.force_refresh())

    assert results == [False, False, False]
    assert len(client.posts) == 1


# ------------------------------------------------------ proactive refresh --
#
# There is none (2026-09-27, followups L4). ``get_access_token`` refreshed
# shortly before the JWT's ``exp``, but nothing called it: every caller —
# the chat model, ``build_*_llm``, the usage meter — reads ``get_api_key()``,
# and an expired token is refreshed by the first 401 (single-flight, above).

def test_there_is_no_proactive_refresh_path(svc):
    assert not hasattr(svc.CodexAuthManager, "get_access_token")
    for gone in ("REFRESH_BEFORE_EXPIRY_SECONDS", "_token_expiry_for", "jwt_exp"):
        assert not hasattr(svc, gone), gone
    assert not hasattr(svc.CodexAuthManager(), "_token_expiry")


async def test_an_expired_token_is_refreshed_by_the_first_401_once(svc, monkeypatch):
    """What replaces the proactive refresh: the 401 path, one POST however
    many calls saw the 401 at once."""
    Path(svc.CODEX_AUTH_PATH).write_text(json.dumps({"tokens": {
        "access_token": _jwt(time.time() - 60), "refresh_token": "rt"}}))
    fresh = _jwt(time.time() + 8 * 86400)
    client = _SlowClient([_ok(fresh)])
    monkeypatch.setattr(svc.httpx, "AsyncClient", client)
    mgr = svc.CodexAuthManager()

    assert mgr.get_api_key() != fresh, "nothing refreshes before a call is refused"
    assert await asyncio.gather(mgr.force_refresh(), mgr.force_refresh()) == [True, True]
    assert len(client.posts) == 1 and mgr.get_api_key() == fresh
