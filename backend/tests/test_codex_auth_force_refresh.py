"""
Regression: CodexAuthManager.force_refresh() must not claim success
when the access token didn't actually change.

Original failure mode: when the OAuth refresh token itself is revoked,
``_refresh_codex`` falls back to re-reading ``~/.codex/auth.json``. If
that file STILL has the same stale token (because no external
``codex login`` has happened yet), the method returns True purely
because ``_access_token is not None``. The caller (the 401 retry path
in ChatCodexResponses) then retries with the same dead token, gets
another 401, and surfaces a confusing "refresh succeeded but call
still failed" error instead of a clean "you need to re-auth."

The contract we pin:
  - File reload picked up a NEW token → return True
  - File reload returned the SAME stale token → return False
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest


def _make_auth_file(tmp_path: Path, access_token: str, refresh_token: str = "rt-original") -> Path:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "id-token",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": "acct_test",
        },
    }))
    return auth


class _FakePostResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self) -> dict:
        return self._body


class _FakeAsyncClientCtx:
    """`async with httpx.AsyncClient(...) as client:` returning self."""

    def __init__(self, post_response: _FakePostResponse):
        self._post_response = post_response
        self.post_calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self) -> "_FakeAsyncClientCtx":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def post(self, url: str, data: dict | None = None, **kwargs) -> _FakePostResponse:
        self.post_calls.append({"url": url, "data": data})
        return self._post_response


@pytest.mark.asyncio
async def test_force_refresh_returns_false_when_token_unchanged(monkeypatch, tmp_path):
    """Refresh endpoint says refresh_token revoked AND the auth.json
    file on disk still has the same stale access token.
    force_refresh() must return False — there's nothing to retry with."""
    from services import codex_auth_service

    stale_token = "stale-access-token"
    auth_file = _make_auth_file(tmp_path, access_token=stale_token)
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", str(auth_file))

    # Refresh endpoint refuses (refresh_token itself is invalid)
    fake_client = _FakeAsyncClientCtx(_FakePostResponse(
        status_code=400,
        body={"error": "invalid_grant", "error_description": "refresh_token revoked"},
    ))
    monkeypatch.setattr(codex_auth_service.httpx, "AsyncClient", fake_client)

    mgr = codex_auth_service.CodexAuthManager()
    assert mgr._access_token == stale_token, "test setup: file should be loaded on init"

    ok = await mgr.force_refresh()

    assert ok is False, (
        "force_refresh must return False when the access token didn't change — "
        "otherwise the 401 retry path will reuse the same dead token and look "
        "like an infinite-loop bug to the caller. Token after: "
        f"{mgr._access_token!r}"
    )
    assert mgr._access_token == stale_token, (
        "manager should still hold the stale token (so callers can see what's there), "
        "but force_refresh's return value must signal 'no new auth available'"
    )


@pytest.mark.asyncio
async def test_force_refresh_returns_true_when_file_has_new_token(monkeypatch, tmp_path):
    """Refresh endpoint refuses, but an external `codex login` has
    written a NEW access_token to auth.json. force_refresh() must
    detect the change, adopt the new token, and return True."""
    from services import codex_auth_service

    stale_token = "stale-access-token"
    fresh_token = "newly-minted-access-token"

    auth_file = _make_auth_file(tmp_path, access_token=stale_token)
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", str(auth_file))

    # Refresh endpoint refuses…
    fake_client = _FakeAsyncClientCtx(_FakePostResponse(
        status_code=400,
        body={"error": "invalid_grant"},
    ))
    monkeypatch.setattr(codex_auth_service.httpx, "AsyncClient", fake_client)

    mgr = codex_auth_service.CodexAuthManager()
    assert mgr._access_token == stale_token

    # …but meanwhile, an external `codex login` rewrote the file.
    _make_auth_file(tmp_path, access_token=fresh_token, refresh_token="rt-rotated")

    ok = await mgr.force_refresh()

    assert ok is True, (
        "force_refresh must return True after picking up an externally-rewritten "
        "auth.json with a different access token"
    )
    assert mgr._access_token == fresh_token, (
        f"manager should adopt the fresh token, got {mgr._access_token!r}"
    )
