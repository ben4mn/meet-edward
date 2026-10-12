"""Twilio MMS: media arrives as attachments Edward can see."""

import base64

import pytest

from routers import webhooks
from routers.webhooks import parse_media_params

pytestmark = pytest.mark.unit


def test_parse_media_params_reads_numbered_fields():
    form = {"NumMedia": "2", "MediaUrl0": "https://api.twilio.com/m/1", "MediaContentType0": "image/jpeg",
            "MediaUrl1": "https://api.twilio.com/m/2", "MediaContentType1": "application/pdf"}
    media = parse_media_params(form)
    assert media == [("https://api.twilio.com/m/1", "image/jpeg"), ("https://api.twilio.com/m/2", "application/pdf")]
    assert parse_media_params({"NumMedia": "0"}) == [] and parse_media_params({}) == []


async def test_process_inbound_passes_attachments_to_chat(monkeypatch):
    seen = {}

    async def fake_chat(**kw):
        seen.update(kw)
        return "ok"

    async def fake_fetch(url):
        return b"\xff\xd8jpegbytes"

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(webhooks, "chat_with_memory", fake_chat)
    monkeypatch.setattr(webhooks, "fetch_twilio_media", fake_fetch)
    monkeypatch.setattr(webhooks, "_send_inbound_push_notification", noop)
    monkeypatch.setattr(webhooks, "_send_on_channel", noop)

    class S:
        system_prompt = "sp"; model = "gpt-5.4"; temperature = 0.7; provider = "openai"

    async def fake_settings():
        return S()

    async def fake_graph():
        return object()

    monkeypatch.setattr(webhooks, "get_settings", fake_settings)
    monkeypatch.setattr(webhooks, "get_graph", fake_graph)
    await webhooks.process_inbound_and_respond("+15550001111", "", "conv-1", "sms",
                                               media=[("https://api.twilio.com/m/1", "image/jpeg")])
    atts = seen["attachments"]
    assert len(atts) == 1 and atts[0]["mime_type"] == "image/jpeg"
    assert base64.b64decode(atts[0]["data"]).startswith(b"\xff\xd8")
    assert seen["message"].strip()  # an empty MMS body still yields a message


async def test_a_tool_call_written_as_text_is_never_texted_back(monkeypatch):
    """The Twilio reply is the turn's final text (bench §6.1): markup is
    stripped, and a reply that was only a call gets the honest line."""
    sent = []

    async def leak(**kw):
        return ('<invoke name="send_message">\n<parameter name="message">See you at 7</parameter>\n'
                '</invoke>')

    async def noop(*a, **k):
        return None

    async def capture(channel, to, body):
        sent.append(body)

    class S:
        system_prompt = "sp"; model = "gpt-5.4"; temperature = 0.7; provider = "openai"

    async def fake_settings():
        return S()

    async def fake_graph():
        return object()

    monkeypatch.setattr(webhooks, "chat_with_memory", leak)
    monkeypatch.setattr(webhooks, "_send_inbound_push_notification", noop)
    monkeypatch.setattr(webhooks, "_send_on_channel", capture)
    monkeypatch.setattr(webhooks, "get_settings", fake_settings)
    monkeypatch.setattr(webhooks, "get_graph", fake_graph)

    await webhooks.process_inbound_and_respond("+15550100", "are we on?", "twilio-1")

    assert len(sent) == 1
    assert "<invoke" not in sent[0] and "See you at 7" not in sent[0]
    assert sent[0] == webhooks.GARBLED_REPLY
