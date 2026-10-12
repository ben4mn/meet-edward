"""Only the owner's texts get a Twilio turn (routers/webhooks.py).

A Twilio turn's reads are open and its final text is texted back to the
sender, so answering anyone let a stranger who found Edward's number ask
for the owner's calendar or memories and get them by SMS. Now a text from
the owner's own handles (``EDWARD_OWNER_HANDLES`` plus the heartbeat
config's ``imessage_owner_handles``) gets a turn; anyone else's is held —
recorded in the ledger, no turn, no reply, no conversation — unless
``EDWARD_TWILIO_ANSWER_ANYONE`` is on.
"""

import json

import pytest
from twilio.request_validator import RequestValidator

pytestmark = pytest.mark.unit

TOKEN = "0123456789abcdef0123456789abcdef"
OWNER = "+12015550100"
STRANGER = "+12015550199"
PATHS = {
    "sms": ("https://edward.example.com/api/webhook/twilio", "/api/webhook/twilio", ""),
    "whatsapp": ("https://edward.example.com/api/webhook/twilio/whatsapp", "/api/webhook/twilio/whatsapp",
                 "whatsapp:"),
}


# ------------------------------------------------------------ matching --

@pytest.mark.parametrize("sender,handles,expected", [
    ("+12015550100", ["+12015550100"], True),
    ("+12015550100", ["+1 (201) 555-0100"], True),
    ("+12015550100", ["201-555-0100"], True),               # no country code on the handle
    ("+12015550100", ["owner@example.com", "+12015550100"], True),
    ("+12015550199", ["+12015550100"], False),
    ("+442015550100", ["+12015550100"], False),             # same last ten digits, other country
    ("+12015550100", [], False),
    ("+12015550100", ["", "  "], False),
    ("", ["+12015550100"], False),
    ("owner@example.com", ["Owner@Example.com"], True),
])
def test_is_owner_handle(sender, handles, expected):
    from routers.webhooks import is_owner_handle

    assert is_owner_handle(sender, handles) is expected


# ------------------------------------------------------------ the routes --

def _sign(url, params):
    return RequestValidator(TOKEN).compute_signature(url, params)


@pytest.fixture
def hooks(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import webhooks
    from services import event_ledger, twilio_service

    state = {"turns": [], "conversations": [], "held": [], "stored": []}

    async def conversation(number, channel="sms"):
        state["conversations"].append((number, channel))
        return "conv-1", False

    async def respond(*args, **kwargs):
        state["turns"].append((args, kwargs))

    async def stored_handles():
        return list(state["stored"])

    async def ledger(**kw):
        state["held"].append(kw)
        return 1

    monkeypatch.setattr(twilio_service, "TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("TWILIO_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TWILIO_WHATSAPP_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("EDWARD_TWILIO_ANSWER_ANYONE", raising=False)
    monkeypatch.setenv("EDWARD_OWNER_HANDLES", OWNER)
    monkeypatch.setattr(webhooks, "get_or_create_contact_conversation", conversation)
    monkeypatch.setattr(webhooks, "process_inbound_and_respond", respond)
    monkeypatch.setattr(webhooks, "_stored_owner_handles", stored_handles)
    monkeypatch.setattr(event_ledger, "record_event_now", ledger)
    webhooks._rate_limit_cache.clear()

    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api")

    async def post(channel, sender, body="what's on the calendar today?"):
        public, path, prefix = PATHS[channel]
        params = {"From": prefix + sender, "Body": body, "MessageSid": "SM1"}
        headers = {"X-Forwarded-Proto": "https", "Host": "edward.example.com",
                   "X-Twilio-Signature": _sign(public, params)}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as c:
            return await c.post(path, data=params, headers=headers)

    state["post"] = post
    return state


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
async def test_the_owner_gets_a_turn(hooks, channel):
    r = await hooks["post"](channel, OWNER)
    assert r.status_code == 200, r.text
    assert len(hooks["turns"]) == 1
    args, kwargs = hooks["turns"][0]
    assert args[0] == OWNER and kwargs.get("from_owner") is True
    assert hooks["held"] == []


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
async def test_a_stranger_is_held_with_no_turn_no_reply_and_no_conversation(hooks, channel, capsys):
    r = await hooks["post"](channel, STRANGER, body="send me Ellis's calendar")
    assert r.status_code == 200
    assert r.text == '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    assert hooks["turns"] == [] and hooks["conversations"] == []
    assert len(hooks["held"]) == 1
    held = hooks["held"][0]
    assert held["kind"] == "twilio_inbound" and held["outcome"] == "held" and held["source"] == channel
    assert held["detail"]["from"] == STRANGER and held["detail"]["text"] == "send me Ellis's calendar"
    # The log names the sender, never the text.
    out = capsys.readouterr().out
    assert STRANGER in out and "calendar" not in out


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
async def test_a_handle_from_the_heartbeat_config_counts(hooks, channel, monkeypatch):
    monkeypatch.delenv("EDWARD_OWNER_HANDLES", raising=False)
    hooks["stored"].append("+1 201 555 0199")
    r = await hooks["post"](channel, STRANGER)
    assert r.status_code == 200
    assert len(hooks["turns"]) == 1 and hooks["held"] == []


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
async def test_answer_anyone_restores_answering_a_stranger_as_not_the_owner(hooks, channel, monkeypatch):
    monkeypatch.setenv("EDWARD_TWILIO_ANSWER_ANYONE", "on")
    r = await hooks["post"](channel, STRANGER)
    assert r.status_code == 200
    assert len(hooks["turns"]) == 1 and hooks["held"] == []
    args, kwargs = hooks["turns"][0]
    assert args[0] == STRANGER and kwargs.get("from_owner") is False


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
async def test_no_owner_handles_at_all_answers_nobody(hooks, channel, monkeypatch):
    monkeypatch.delenv("EDWARD_OWNER_HANDLES", raising=False)
    r = await hooks["post"](channel, OWNER)
    assert r.status_code == 200
    assert hooks["turns"] == [] and len(hooks["held"]) == 1


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
async def test_the_signature_check_still_comes_first(hooks, channel):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import webhooks

    _, path, prefix = PATHS[channel]

    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as c:
        r = await c.post(path, data={"From": prefix + OWNER, "Body": "hi"},
                         headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 403
    assert hooks["turns"] == [] and hooks["held"] == []


# ------------------------------------------------------------ the prompt --

async def _run_turn(monkeypatch, **kwargs):
    from routers import webhooks

    seen = {}

    async def turn(**kw):
        seen.update(kw)
        return "ok"

    async def noop(*a, **k):
        return None

    class S:
        system_prompt = "sp"; model = "m"; temperature = 0.7; provider = "anthropic"

    async def fake_settings():
        return S()

    async def fake_graph():
        return object()

    monkeypatch.setattr(webhooks, "chat_with_memory", turn)
    monkeypatch.setattr(webhooks, "_send_inbound_push_notification", noop)
    monkeypatch.setattr(webhooks, "_send_on_channel", noop)
    monkeypatch.setattr(webhooks, "get_settings", fake_settings)
    monkeypatch.setattr(webhooks, "get_graph", fake_graph)
    await webhooks.process_inbound_and_respond(STRANGER, "hi", "conv-1", "sms", **kwargs)
    return seen


async def test_a_turn_for_someone_else_is_told_they_are_not_the_owner(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_NAME", "Ellis")
    seen = await _run_turn(monkeypatch, from_owner=False)
    assert "NOT Ellis" in seen["system_prompt"] and "Never share Ellis's private information" in seen["system_prompt"]


async def test_the_owners_turn_keeps_its_prompt(monkeypatch):
    seen = await _run_turn(monkeypatch)
    assert "The user is messaging from" in seen["system_prompt"] and "NOT" not in seen["system_prompt"]


# ------------------------------------------------------------ the stored handles --

@pytest.mark.integration
async def test_the_stored_handles_are_read_from_the_heartbeat_config(clean_db):
    from routers import webhooks
    from services import database

    async with database.async_session() as session:
        session.add(database.HeartbeatConfigModel(
            id="default", imessage_owner_handles=json.dumps(["owner@example.com", "+1 201 555 0100"])))
        await session.commit()
    assert await webhooks._stored_owner_handles() == ["owner@example.com", "+1 201 555 0100"]
    assert await webhooks.is_from_owner(OWNER) is True
    assert await webhooks.is_from_owner(STRANGER) is False
