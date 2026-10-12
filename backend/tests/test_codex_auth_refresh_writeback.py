"""Edward's own OAuth refresh must work, and must keep auth.json in step.

From the log: every refresh Edward attempted answered
``{"code": "invalid_client", "message": "Invalid client specified."}`` —
the client id was a made-up ``app_scp_cs_prod_001``. The Codex CLI's client
id is ``app_EMoamEEZ73f0CkXaXp7hrann`` (see ``strings $(which codex)``).
Edward survived only because the CLI rewrote auth.json on its own schedule;
between those rewrites every call 401'd (~400 lines on 2026-09-02).

Refresh tokens rotate. Once Edward refreshes, the refresh token in
auth.json is dead for the CLI (evolution's ``codex exec``) unless Edward
writes the new pair back — the CLI does the same, atomically, preserving
the other keys.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CLI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _auth_file(tmp_path: Path, access="old-access", refresh="old-refresh") -> Path:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {"id_token": "old-id", "access_token": access, "refresh_token": refresh, "account_id": "acct_test"},
        "last_refresh": "2026-09-01T00:00:00Z",
    }))
    return auth


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Client:
    def __init__(self, resp):
        self._resp = resp
        self.posts = []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, data=None, **kw):
        self.posts.append({"url": url, "data": data})
        return self._resp


def test_client_id_is_the_codex_clis():
    from services import codex_auth_service
    assert codex_auth_service.CODEX_CLIENT_ID == CLI_CLIENT_ID


async def test_a_successful_refresh_uses_the_cli_client_and_writes_auth_json_back(monkeypatch, tmp_path):
    from services import codex_auth_service
    auth = _auth_file(tmp_path)
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", str(auth))
    client = _Client(_Resp(200, {"access_token": "new-access", "refresh_token": "new-refresh", "id_token": "new-id"}))
    monkeypatch.setattr(codex_auth_service.httpx, "AsyncClient", client)

    mgr = codex_auth_service.CodexAuthManager()
    assert await mgr.force_refresh() is True
    assert client.posts[0]["data"]["client_id"] == CLI_CLIENT_ID
    assert mgr.get_api_key() == "new-access"

    on_disk = json.loads(auth.read_text())
    assert on_disk["tokens"]["access_token"] == "new-access"
    assert on_disk["tokens"]["refresh_token"] == "new-refresh"
    assert on_disk["tokens"]["id_token"] == "new-id"
    assert on_disk["tokens"]["account_id"] == "acct_test"      # untouched
    assert on_disk["auth_mode"] == "chatgpt" and "OPENAI_API_KEY" in on_disk
    assert on_disk["last_refresh"] != "2026-09-01T00:00:00Z"
    assert stat.S_IMODE(os.stat(auth).st_mode) == 0o600


async def test_a_refused_refresh_writes_nothing(monkeypatch, tmp_path):
    from services import codex_auth_service
    auth = _auth_file(tmp_path)
    before = auth.read_text()
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", str(auth))
    monkeypatch.setattr(codex_auth_service.httpx, "AsyncClient", _Client(_Resp(401, {"error": {"code": "invalid_client"}})))

    mgr = codex_auth_service.CodexAuthManager()
    assert await mgr.force_refresh() is False
    assert auth.read_text() == before


async def test_write_back_failure_does_not_lose_the_refreshed_token(monkeypatch, tmp_path):
    from services import codex_auth_service
    auth = _auth_file(tmp_path)
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", str(auth))
    monkeypatch.setattr(codex_auth_service.httpx, "AsyncClient", _Client(_Resp(200, {"access_token": "new-access"})))

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(codex_auth_service, "_write_auth_file", boom)
    mgr = codex_auth_service.CodexAuthManager()
    assert await mgr.force_refresh() is True
    assert mgr.get_api_key() == "new-access"
