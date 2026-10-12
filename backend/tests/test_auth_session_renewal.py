"""A session that is in use keeps living.

The cookie and the JWT inside it lasted exactly 30 days from login and
nothing ever renewed them, so every device hit a cliff: on 2026-09-20 at
22:3x the /screen tab in Ellis's Chrome went from 200s to 85,000 401s in a
day (its pollers never re-authenticate), and the phone PWA was due the
same fate. The middleware now re-issues the cookie whenever a valid token
has less than half its life left — a device used weekly never expires,
one left in a drawer still does.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch) -> TestClient:
    from middleware.auth import AuthMiddleware
    from services import auth_service

    monkeypatch.setattr(auth_service, "is_password_configured_cached", lambda: True)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    return TestClient(app)


def test_token_seconds_remaining_reads_the_expiry():
    from services import auth_service

    fresh = auth_service.create_access_token()
    remaining = auth_service.token_seconds_remaining(fresh)
    assert remaining is not None and 29 * 86400 < remaining <= 30 * 86400
    assert auth_service.token_seconds_remaining("not-a-token") is None
    assert auth_service.token_seconds_remaining(auth_service.create_access_token(expires_delta=timedelta(seconds=-5))) is None


def test_a_token_past_half_life_is_renewed_on_any_authenticated_request(monkeypatch):
    from services import auth_service

    client = _client(monkeypatch)
    old = auth_service.create_access_token(expires_delta=timedelta(days=10))
    client.cookies.set("edward_token", old)
    r = client.get("/api/ping")
    assert r.status_code == 200 and r.json() == {"ok": True}

    set_cookie = r.headers.get("set-cookie", "")
    assert "edward_token=" in set_cookie, "a half-spent session must be re-issued"
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=lax" in set_cookie.replace("SameSite=Lax", "SameSite=lax")
    assert "Max-Age=2592000" in set_cookie  # 30 days, like login

    renewed = r.cookies.get("edward_token")
    assert renewed and renewed != old
    assert auth_service.token_seconds_remaining(renewed) > 29 * 86400


def test_a_fresh_token_is_left_alone(monkeypatch):
    from services import auth_service

    client = _client(monkeypatch)
    client.cookies.set("edward_token", auth_service.create_access_token())
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert "edward_token=" not in r.headers.get("set-cookie", "")


def test_an_expired_token_is_still_refused_and_not_renewed(monkeypatch):
    from services import auth_service

    client = _client(monkeypatch)
    client.cookies.set("edward_token", auth_service.create_access_token(expires_delta=timedelta(seconds=-10)))
    r = client.get("/api/ping")
    assert r.status_code == 401
    assert "edward_token=" not in r.headers.get("set-cookie", "")


def test_no_cookie_is_still_refused(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/api/ping").status_code == 401
