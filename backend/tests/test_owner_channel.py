"""``owner_channel`` — where an urgent reach to Ellis lands (proactivity-spec §4.5, G8).

Why (2026-10-11): push and Today are channels Ellis does not live in; he lives
in iMessage. Which channel an urgent reach uses is Ellis's answer to a
question (§15 Q1), so it is a flag: ``EDWARD_OWNER_CHANNEL=push`` (the
default), ``imessage_self`` (his self-chat, only once AREA 3's structural
echo detection exists — otherwise Edward's text would come back as Ellis's
ping), or ``edward_number`` (Twilio SMS from Edward's own number). Every
channel writes ``push_log`` so the budget counts it the same way, and every
failure falls back to a push. The transports are stubbed: nothing is sent.
"""

import json

import pytest
from sqlalchemy import select

from services import owner_channel

pytestmark = pytest.mark.integration


class Calls:
    def __init__(self):
        self.push, self.imessage, self.sms = [], [], []


@pytest.fixture
def calls(monkeypatch):
    from services import imessage_service, push_service, twilio_service

    c = Calls()

    async def fake_push(title, body, url=None, tag=None, source="unknown", conversation_id=None,
                        respect_quiet_hours=False, **kw):
        from services import push_ledger

        c.push.append({"title": title, "body": body, "url": url, "source": source})
        lid = await push_ledger.record(source=source, title=title, body=body, url=url, tag=tag,
                                       conversation_id=conversation_id, delivery_id=None, sent=1, failed=0,
                                       held_reason=None)
        return {"sent": 1, "failed": 0, "total": 1, "ledger_id": lid}

    def fake_imessage(recipient, message):
        c.imessage.append({"to": recipient, "text": message})
        return {"success": True}

    async def fake_sms(to_number, message):
        c.sms.append({"to": to_number, "text": message})
        return {"sid": "SM1", "status": "queued"}

    monkeypatch.setattr(push_service, "send_push_notification", fake_push)
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(imessage_service, "send_imessage", fake_imessage)
    monkeypatch.setattr(twilio_service, "send_sms", fake_sms)
    monkeypatch.setattr(twilio_service, "is_configured", lambda: True)
    monkeypatch.setattr(owner_channel, "structural_echo_available", lambda: True)
    monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example")
    return c


async def _owner_handles(*handles):
    from services.database import HeartbeatConfigModel, async_session

    async with async_session() as s:
        cfg = await s.get(HeartbeatConfigModel, "default")
        if cfg is None:
            cfg = HeartbeatConfigModel(id="default")
            s.add(cfg)
        cfg.imessage_owner_handles = json.dumps(list(handles))
        await s.commit()


async def _push_log():
    from services.database import PushLogModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(PushLogModel))).scalars().all())


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [(None, "push"), ("push", "push"), ("imessage_self", "imessage_self"),
                                            ("EDWARD_NUMBER", "edward_number"), ("pigeon", "push")])
def test_channel_reads_the_flag(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    else:
        monkeypatch.setenv("EDWARD_OWNER_CHANNEL", value)
    assert owner_channel.channel() == expected


async def test_push_is_the_default(clean_db, calls, monkeypatch):
    monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    out = await owner_channel.deliver(title="Flight check-in", body="Check-in closes at 10", url="/chat?c=x", origin="speak")
    assert out["sent"] == 1 and out["channel"] == "push" and out["ledger_id"]
    assert calls.push[0]["source"] == "reach:speak"
    assert calls.imessage == [] and calls.sms == []


async def test_imessage_self_texts_owners_own_handle_signed_and_logs_it(clean_db, calls, monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "imessage_self")
    await _owner_handles("owner@example.com", "+12015550100")
    out = await owner_channel.deliver(title="Flight check-in", body="Check-in closes at 10. Open it? (not done)",
                                    url="/chat?c=entry-1", origin="speak")
    assert out["channel"] == "imessage_self" and out["sent"] == 1 and out["ledger_id"]
    sent = calls.imessage[0]
    assert sent["to"] == "owner@example.com"
    assert sent["text"].startswith("Edward: ") and "https://edward.example/chat?c=entry-1" in sent["text"]
    assert calls.push == []
    rows = await _push_log()
    assert len(rows) == 1 and rows[0].source == "reach:speak" and rows[0].sent == 1
    assert rows[0].url == "/chat?c=entry-1"


async def test_imessage_self_falls_back_to_push_without_structural_echo_detection(clean_db, calls, monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "imessage_self")
    monkeypatch.setattr(owner_channel, "structural_echo_available", lambda: False)
    await _owner_handles("owner@example.com")
    out = await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert out["channel"] == "push" and calls.imessage == [] and len(calls.push) == 1
    assert "echo" in out.get("fallback_reason", "")


async def test_imessage_self_falls_back_when_the_send_fails(clean_db, calls, monkeypatch):
    from services import imessage_service

    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "imessage_self")
    monkeypatch.setattr(imessage_service, "send_imessage", lambda r, m: {"success": False, "error": "timeout"})
    await _owner_handles("owner@example.com")
    out = await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert out["channel"] == "push" and len(calls.push) == 1


async def test_imessage_self_falls_back_with_no_owner_handle(clean_db, calls, monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "imessage_self")
    await _owner_handles()
    out = await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert out["channel"] == "push"


async def test_edward_number_texts_owners_phone_by_sms(clean_db, calls, monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "edward_number")
    await _owner_handles("owner@example.com", "+12015550100")
    out = await owner_channel.deliver(title="Flight check-in", body="Check-in closes at 10", url="/today", origin="care")
    assert out["channel"] == "edward_number" and out["sent"] == 1
    assert calls.sms[0]["to"] == "+12015550100" and "Check-in closes at 10" in calls.sms[0]["text"]
    rows = await _push_log()
    assert rows[0].source == "reach:care"


async def test_edward_number_falls_back_when_twilio_is_not_configured(clean_db, calls, monkeypatch):
    from services import twilio_service

    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "edward_number")
    monkeypatch.setattr(twilio_service, "is_configured", lambda: False)
    await _owner_handles("+12015550100")
    out = await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert out["channel"] == "push" and calls.sms == []


async def test_edward_number_falls_back_with_no_phone_handle(clean_db, calls, monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "edward_number")
    await _owner_handles("owner@example.com")
    out = await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert out["channel"] == "push"


async def test_edward_number_falls_back_when_the_sms_fails(clean_db, calls, monkeypatch):
    from services import twilio_service

    async def boom(to, msg):
        raise RuntimeError("twilio down")

    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "edward_number")
    monkeypatch.setattr(twilio_service, "send_sms", boom)
    await _owner_handles("+12015550100")
    out = await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert out["channel"] == "push" and len(calls.push) == 1


async def test_every_channel_counts_against_the_budget(clean_db, calls, monkeypatch):
    from services import reach_service

    await _owner_handles("owner@example.com", "+12015550100")
    for value in ("push", "imessage_self", "edward_number"):
        monkeypatch.setenv("EDWARD_OWNER_CHANNEL", value)
        await owner_channel.deliver(title="t", body="b", url="/today", origin="self")
    assert await reach_service.autonomous_pushes_today() == 3


async def test_reach_service_delivers_through_the_channel(clean_db, calls, monkeypatch):
    from services import reach_service

    monkeypatch.setenv("EDWARD_OWNER_CHANNEL", "edward_number")
    await _owner_handles("+12015550100")
    out = await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", title="Biscuit's dose", urgent=True,
                                       origin="care")
    assert out.startswith("Pushed") and calls.sms and not calls.push
