"""Gmail OAuth: a server-side state and PKCE verifier (2026-10-11 audit, security 3).

The public ``GET /api/gmail/oauth/callback`` took only ``code``: once a
client id existed, anyone could run a flow with Edward's public client id
and swap Ellis's connection for their own Gmail. And ``get_oauth_url``'s Flow
auto-generated a PKCE verifier that was thrown away, so ``exchange_code``
(a fresh Flow, no verifier) could never complete with Google.

Now the authenticated ``GET /api/gmail/oauth/url`` stores ``{state:
verifier}`` server-side (10 min, single use, bounded) and the callback
refuses — 400, nothing stored — a missing, unknown, expired or reused
state; a valid one exchanges the code with its own verifier. Every Google
call is stubbed here; the network is refused for the whole module.
"""

from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from services import gmail_service

CALLBACK = "/api/gmail/oauth/callback"
OWNER = "owner@example.com"


@pytest.fixture(autouse=True)
def _nothing_reaches_google(monkeypatch):
    """Token exchanges go through requests, API calls through httplib2."""
    import httplib2
    import requests.adapters

    def refuse(*_a, **_k):
        raise AssertionError("a Gmail OAuth test tried to reach the network")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)
    monkeypatch.setattr(httplib2.Http, "request", refuse)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(gmail_service, "GMAIL_CLIENT_ID", "edward-test.apps.googleusercontent.com")
    monkeypatch.setattr(gmail_service, "GMAIL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(gmail_service, "GMAIL_REDIRECT_URI", "https://edward.example.com/api/gmail/oauth/callback")
    monkeypatch.delenv("GMAIL_ACCOUNT", raising=False)
    pending = getattr(gmail_service, "_pending_oauth", None)
    if pending is not None:
        pending.clear()

    async def no_refresh():
        return None

    from services import tool_registry
    monkeypatch.setattr(tool_registry, "refresh_registry", no_refresh)


@pytest.fixture
def google(monkeypatch):
    """The token endpoint and the profile call, stubbed; records what reached them."""
    from requests_oauthlib import OAuth2Session

    calls: list[dict] = []
    profile = {"emailAddress": OWNER}

    def fake_fetch_token(self, token_url, **kwargs):
        calls.append({"token_url": token_url, **kwargs})
        self.token = {
            "access_token": f"ya29.test-access-{len(calls)}",
            "refresh_token": f"1//test-refresh-{len(calls)}",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": time.time() + 3600,
            "scope": list(gmail_service.GMAIL_SCOPES),
        }
        return self.token

    class _Exec:
        def execute(self):
            return dict(profile)

    class _Users:
        def getProfile(self, userId):  # noqa: N802 — the Gmail API's own name
            assert userId == "me"
            return _Exec()

    class _Service:
        def users(self):
            return _Users()

    monkeypatch.setattr(OAuth2Session, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(gmail_service, "_build_service", lambda creds: _Service())
    return type("Google", (), {"calls": calls, "profile": profile})


@pytest.fixture
async def gmail_db(clean_db):
    """conftest's truncate list has no gmail_credentials; clear it here."""
    from services.database import GmailCredentialsModel, async_session

    async def wipe():
        async with async_session() as session:
            await session.execute(delete(GmailCredentialsModel))
            await session.commit()

    await wipe()
    yield
    await wipe()


@pytest.fixture
async def client():
    from routers import gmail

    app = FastAPI()
    app.include_router(gmail.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _start() -> dict:
    """What Ellis's Connect button gets: the Google URL, and its query."""
    url = await gmail_service.get_oauth_url()
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    return query


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def _stored():
    return await gmail_service._load_credentials_row()


# --------------------------------------------------------------- refusals --

@pytest.mark.integration
async def test_callback_without_state_is_refused_and_stores_nothing(gmail_db, google, client):
    await _start()
    r = await client.get(CALLBACK, params={"code": "4/attacker-code"})
    assert r.status_code == 400
    assert "state" in r.json()["error"].lower()
    assert google.calls == []
    assert await _stored() is None


@pytest.mark.integration
async def test_callback_with_unknown_state_is_refused_and_stores_nothing(gmail_db, google, client):
    await _start()
    r = await client.get(CALLBACK, params={"code": "4/attacker-code", "state": "made-up-state"})
    assert r.status_code == 400
    assert "start again" in r.json()["error"].lower()
    assert google.calls == []
    assert await _stored() is None


@pytest.mark.integration
async def test_callback_with_expired_state_is_refused_and_stores_nothing(gmail_db, google, client, monkeypatch):
    start = await _start()
    later = time.monotonic() + 11 * 60
    monkeypatch.setattr(gmail_service, "_clock", lambda: later, raising=False)
    r = await client.get(CALLBACK, params={"code": "4/owner-code", "state": start["state"]})
    assert r.status_code == 400
    assert "expired" in r.json()["error"].lower()
    assert google.calls == []
    assert await _stored() is None


@pytest.mark.integration
async def test_a_state_is_spent_by_its_first_use(gmail_db, google, client):
    start = await _start()
    first = await client.get(CALLBACK, params={"code": "4/owner-code", "state": start["state"]})
    assert first.status_code in (302, 307)
    assert first.headers["location"].endswith("gmail=connected")
    row = await _stored()
    assert row.refresh_token == "1//test-refresh-1"

    again = await client.get(CALLBACK, params={"code": "4/attacker-code", "state": start["state"]})
    assert again.status_code == 400
    assert len(google.calls) == 1
    assert (await _stored()).refresh_token == "1//test-refresh-1"


@pytest.mark.integration
async def test_a_declined_consent_spends_its_state(gmail_db, google, client):
    start = await _start()
    r = await client.get(CALLBACK, params={"error": "access_denied", "state": start["state"]})
    assert r.headers["location"].endswith("gmail=error")
    r = await client.get(CALLBACK, params={"code": "4/late-code", "state": start["state"]})
    assert r.status_code == 400
    assert google.calls == []
    assert await _stored() is None


# ------------------------------------------------------------- the exchange --

@pytest.mark.integration
async def test_a_valid_state_exchanges_the_code_with_its_own_verifier(gmail_db, google, client):
    start = await _start()
    assert start["code_challenge_method"] == "S256"
    assert len(start["state"]) >= 32

    r = await client.get(CALLBACK, params={"code": "4/owner-code", "state": start["state"]})
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("gmail=connected")

    assert len(google.calls) == 1
    call = google.calls[0]
    assert call["code"] == "4/owner-code"
    verifier = call.get("code_verifier")
    assert verifier, "the exchange must carry the PKCE verifier the URL's challenge was made from"
    assert 43 <= len(verifier) <= 128
    assert _s256(verifier) == start["code_challenge"]

    row = await _stored()
    assert row.email_address == OWNER
    assert row.refresh_token == "1//test-refresh-1"


@pytest.mark.integration
async def test_each_connect_gets_its_own_state_and_verifier(gmail_db, google, client):
    a, b = await _start(), await _start()
    assert a["state"] != b["state"]
    assert a["code_challenge"] != b["code_challenge"]
    # The older one still works, with its own verifier.
    r = await client.get(CALLBACK, params={"code": "4/owner-code", "state": a["state"]})
    assert r.status_code in (302, 307)
    assert _s256(google.calls[0]["code_verifier"]) == a["code_challenge"]


@pytest.mark.unit
async def test_pending_states_are_bounded():
    limit = gmail_service._OAUTH_STATES_MAX
    states = [(await _start())["state"] for _ in range(limit + 3)]
    assert len(gmail_service._pending_oauth) == limit
    assert states[0] not in gmail_service._pending_oauth
    assert states[-1] in gmail_service._pending_oauth


# ------------------------------------------------------------ whose Gmail --

async def _connect_as(client, google, email: str):
    google.profile["emailAddress"] = email
    start = await _start()
    return await client.get(CALLBACK, params={"code": f"4/code-for-{email}", "state": start["state"]})


@pytest.mark.integration
async def test_an_account_other_than_gmail_account_is_refused(gmail_db, google, client, monkeypatch):
    monkeypatch.setenv("GMAIL_ACCOUNT", OWNER)
    r = await _connect_as(client, google, "someone.else@example.org")
    assert r.status_code == 400
    error = r.json()["error"]
    assert "someone.else@example.org" in error and OWNER in error
    assert await _stored() is None


@pytest.mark.integration
async def test_gmail_account_matches_without_regard_to_case(gmail_db, google, client, monkeypatch):
    monkeypatch.setenv("GMAIL_ACCOUNT", " Owner@Example.COM ")
    r = await _connect_as(client, google, OWNER)
    assert r.status_code in (302, 307)
    assert (await _stored()).email_address == OWNER


@pytest.mark.integration
async def test_an_account_google_does_not_name_is_refused(gmail_db, google, client):
    r = await _connect_as(client, google, "")
    assert r.status_code == 400
    assert await _stored() is None


@pytest.mark.integration
async def test_a_different_account_cannot_replace_the_connected_one(gmail_db, google, client):
    assert (await _connect_as(client, google, OWNER)).status_code in (302, 307)
    before = await _stored()

    r = await _connect_as(client, google, "someone.else@example.org")
    assert r.status_code == 400
    assert "disconnect" in r.json()["error"].lower()
    after = await _stored()
    assert after.email_address == OWNER
    assert after.refresh_token == before.refresh_token
    assert after.access_token == before.access_token


# ---------------------------------------------- the stored row, once connected --
# gmail_credentials' timestamps are naive UTC (the column convention); an
# aware ``updated_at`` made every update of the row fail in asyncpg, so a
# second connect, a saved token refresh and the listener's history mark
# could never be written.


@pytest.mark.integration
async def test_the_connected_account_may_connect_again(gmail_db, google, client):
    assert (await _connect_as(client, google, OWNER)).status_code in (302, 307)
    r = await _connect_as(client, google, OWNER.upper())
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("gmail=connected")
    row = await _stored()
    assert row.refresh_token == "1//test-refresh-2"


@pytest.mark.integration
async def test_the_listeners_history_mark_is_saved(gmail_db, google, client):
    assert (await _connect_as(client, google, OWNER)).status_code in (302, 307)
    await gmail_service.update_last_history_id("12345")
    assert await gmail_service.get_last_history_id() == "12345"


@pytest.mark.integration
async def test_a_refusal_logs_its_reason_never_the_addresses(gmail_db, google, client, monkeypatch, capsys):
    monkeypatch.setenv("GMAIL_ACCOUNT", OWNER)
    r = await _connect_as(client, google, "someone.else@example.org")
    assert r.status_code == 400
    out = capsys.readouterr().out
    assert "not_gmail_account" in out
    assert "@" not in out
