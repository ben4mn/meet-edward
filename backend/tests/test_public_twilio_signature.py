"""Inbound Twilio webhooks are checked, and the check fails closed.

The routes used to skip the signature check whenever ``TWILIO_WEBHOOK_URL``
was unset, so anyone who found the URL could start a model turn. Now every
request needs a valid ``X-Twilio-Signature`` for ``TWILIO_AUTH_TOKEN`` and
either the configured URL or the URL as received (scheme and host from
``X-Forwarded-Proto`` / ``X-Forwarded-Host`` when a tunnel sits in front).
"""

import pytest
from twilio.request_validator import RequestValidator

pytestmark = pytest.mark.unit

TOKEN = "0123456789abcdef0123456789abcdef"
PUBLIC_URL = "https://edward.example.com/api/webhook/twilio"
LOCAL_URL = "http://localhost:8000/api/webhook/twilio"
PARAMS = {"From": "+12015550100", "Body": "hello Edward", "MessageSid": "SM1"}
TUNNEL_HEADERS = {"X-Forwarded-Proto": "https", "Host": "edward.example.com"}


def _sign(url, params=PARAMS, token=TOKEN):
    return RequestValidator(token).compute_signature(url, params)


@pytest.fixture
def twilio(monkeypatch):
    from services import twilio_service

    monkeypatch.setattr(twilio_service, "TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("TWILIO_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TWILIO_WHATSAPP_WEBHOOK_URL", raising=False)
    return twilio_service


# ------------------------------------------------------------- the check --

def test_the_received_url_honours_the_proxy_headers():
    from services.twilio_service import received_webhook_url

    assert received_webhook_url(LOCAL_URL + "?a=1", TUNNEL_HEADERS) == PUBLIC_URL + "?a=1"
    assert received_webhook_url(LOCAL_URL, {"x-forwarded-proto": "https, http",
                                            "x-forwarded-host": "edward.example.com, proxy.local",
                                            "host": "localhost:8000"}) == PUBLIC_URL
    assert received_webhook_url(LOCAL_URL, {}) == LOCAL_URL


def test_no_auth_token_accepts_nothing(twilio, monkeypatch):
    monkeypatch.setattr(twilio, "TWILIO_AUTH_TOKEN", None)
    ok, why = twilio.check_webhook_signature(request_url=LOCAL_URL, headers={"X-Twilio-Signature": "x"},
                                             params=PARAMS)
    assert not ok and "TWILIO_AUTH_TOKEN" in why


def test_an_unsigned_request_is_refused_without_a_configured_url(twilio):
    ok, why = twilio.check_webhook_signature(request_url=LOCAL_URL, headers=TUNNEL_HEADERS, params=PARAMS)
    assert not ok and "X-Twilio-Signature" in why


def test_a_signature_for_the_url_as_received_is_accepted(twilio):
    headers = {**TUNNEL_HEADERS, "X-Twilio-Signature": _sign(PUBLIC_URL)}
    ok, why = twilio.check_webhook_signature(request_url=LOCAL_URL, headers=headers, params=PARAMS)
    assert ok, why


def test_a_signature_for_the_configured_url_is_accepted(twilio):
    configured = "https://hooks.example.com/sms"
    headers = {"X-Twilio-Signature": _sign(configured)}
    ok, why = twilio.check_webhook_signature(request_url=LOCAL_URL, headers=headers, params=PARAMS,
                                             configured_url=configured)
    assert ok, why


@pytest.mark.parametrize("signature", [
    _sign(PUBLIC_URL, token="ffffffffffffffffffffffffffffffff"),   # another account's token
    _sign("https://elsewhere.example.com/api/webhook/twilio"),     # another URL
    _sign(PUBLIC_URL, params={**PARAMS, "Body": "something else"}),  # other params
    "not-a-signature",
])
def test_a_wrong_signature_is_refused_and_the_log_says_why(twilio, signature):
    headers = {**TUNNEL_HEADERS, "X-Twilio-Signature": signature}
    ok, why = twilio.check_webhook_signature(request_url=LOCAL_URL, headers=headers, params=PARAMS)
    assert not ok
    assert "doesn't match" in why and PUBLIC_URL in why
    assert TOKEN not in why and signature not in why


# ------------------------------------------------------------- the routes --

@pytest.fixture
def client(twilio, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import webhooks

    turns = []

    async def conversation(number, channel="sms"):
        return "conv-1", False

    async def respond(*args, **kwargs):
        turns.append(args)

    async def no_stored_handles():
        return []

    monkeypatch.setattr(webhooks, "get_or_create_contact_conversation", conversation)
    monkeypatch.setattr(webhooks, "process_inbound_and_respond", respond)
    # The sender is the owner: only the owner's texts start a turn
    # (tests/test_public_twilio_owner_gate.py).
    monkeypatch.setattr(webhooks, "_stored_owner_handles", no_stored_handles)
    monkeypatch.setenv("EDWARD_OWNER_HANDLES", PARAMS["From"])
    monkeypatch.delenv("EDWARD_TWILIO_ANSWER_ANYONE", raising=False)
    webhooks._rate_limit_cache.clear()
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api")
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000")
    c.turns = turns
    return c


@pytest.mark.parametrize("path", ["/api/webhook/twilio", "/api/webhook/twilio/whatsapp"])
async def test_an_unsigned_webhook_never_starts_a_turn(client, path):
    async with client:
        r = await client.post(path, data=PARAMS, headers=TUNNEL_HEADERS)
    assert r.status_code == 403
    assert client.turns == []


async def test_without_a_token_the_route_refuses(client, twilio, monkeypatch):
    monkeypatch.setattr(twilio, "TWILIO_AUTH_TOKEN", "")
    headers = {**TUNNEL_HEADERS, "X-Twilio-Signature": _sign(PUBLIC_URL)}
    async with client:
        r = await client.post("/api/webhook/twilio", data=PARAMS, headers=headers)
    assert r.status_code == 403
    assert client.turns == []


async def test_a_signed_webhook_behind_a_tunnel_starts_its_turn(client):
    headers = {**TUNNEL_HEADERS, "X-Twilio-Signature": _sign(PUBLIC_URL)}
    async with client:
        r = await client.post("/api/webhook/twilio", data=PARAMS, headers=headers)
    assert r.status_code == 200, r.text
    assert len(client.turns) == 1 and client.turns[0][:2] == ("+12015550100", "hello Edward")
