"""Delivering the runner's own reply or fallback into the ping's chat
(AREA 3, pings-media-spec §2.9, 2026-10-11).

The runner posts text only when the turn didn't: its final text, or a
template. Every path that could double-reply or leak is pinned:

- the egress secret scan replaces a credential and tells Ellis;
- the same text into the same chat within 10 minutes is not sent again
  (an aware window: a 3-hour-old row is not a duplicate);
- a send that reported failure but whose row is in chat.db was delivered —
  no second send (path a);
- one retry, then an honest failure;
- a chat.db ``error`` on the row is never a reason to resend.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from services import imessage_sent, imessage_service
from services.pings import delivery, hooks

PING = {"id": 9, "chat_identifier": "chat123", "chat_guid": "iMessage;+;chat123", "chat_name": "Trivia crew",
        "is_group": True, "sender_label": "Parker", "from_owner": False}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(delivery, "RECHECK_AFTER_S", 0.01)
    imessage_sent.reset_for_tests()
    yield
    imessage_sent.reset_for_tests()


class Transport:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, chat, message, *, purpose="tool"):
        self.calls.append((chat, message, purpose))
        ctx = hooks.current_ping()
        result = self.results.pop(0) if self.results else {"success": True}
        out = {"success": False, "error": None, "route": None, "chat_identifier": chat, "message_rowid": None,
               "message_guid": None, "confirmed": False, **result}
        if ctx is not None:
            hooks.note_send_end(hooks.note_send_start(chat), ok=out["success"], purpose=purpose,
                                rowid=out["message_rowid"], route=out["route"], error=out["error"])
        return out


@pytest.fixture
def told(monkeypatch):
    got = []

    async def fake_tell(text_, *, urgent=False, link=None, key=None, origin=None):
        got.append({"text": text_, "urgent": urgent, "link": link, "origin": origin})

    monkeypatch.setattr(delivery, "_notify_owner", fake_tell)
    return got


async def test_a_plain_reply_is_signed_and_sent_once(clean_db, monkeypatch, told):
    transport = Transport([{"success": True, "route": "chat_id", "message_rowid": 81, "confirmed": True}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)

    d = await delivery.deliver(PING, "The cheapest is $142.", purpose="reply")

    assert d.ok and not d.deduped and d.rowid == 81 and d.route == "chat_id"
    assert len(transport.calls) == 1
    chat, sent, purpose = transport.calls[0]
    assert chat == "chat123" and purpose == "reply"
    assert sent.endswith("- Edward 🤖") and sent.startswith("The cheapest is $142.")
    assert told == []


_LEAK = ('<invoke name="send_message">\n<parameter name="chat_id">chat123</parameter>\n'
         '<parameter name="message">The cheapest is $142.</parameter>\n</invoke>')


async def test_a_tool_call_written_as_text_is_never_posted(clean_db, monkeypatch, told):
    """The last line of defence (bench §6.1): whatever path handed delivery
    the text, the markup never reaches the chat — the honest template
    goes instead."""
    from services.pings import fallback

    transport = Transport([{"success": True, "route": "chat_id", "message_rowid": 82, "confirmed": True}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)

    d = await delivery.deliver(PING, _LEAK, purpose="reply")

    assert d.ok and len(transport.calls) == 1
    sent = transport.calls[0][1]
    assert "<invoke" not in sent and "parameter" not in sent and "$142" not in sent
    assert sent.startswith(fallback.fallback_text("garbled"))


async def test_the_answer_around_a_written_call_is_posted_alone(clean_db, monkeypatch, told):
    transport = Transport([{"success": True, "route": "chat_id", "message_rowid": 83, "confirmed": True}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)

    d = await delivery.deliver(PING, "Saturday works, 10 am.\n" + _LEAK, purpose="reply")

    assert d.ok
    sent = transport.calls[0][1]
    assert sent.startswith("Saturday works, 10 am.") and "<invoke" not in sent


async def test_a_send_inside_the_ping_context_is_noted_on_it(clean_db, monkeypatch, told):
    transport = Transport([{"success": True, "route": "chat_id", "message_rowid": 81, "confirmed": True}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    ctx = hooks.new_context(ping_id=9, chat_identifier="chat123", chat_guid=None, is_group=True,
                            conversation_id="ping-9", must_answer=True, third_party=True)
    await delivery.deliver(PING, "Hi", purpose="fallback", ctx=ctx)
    assert ctx["sends"] and ctx["sends"][0]["purpose"] == "fallback"
    assert hooks.current_ping() is None


async def test_a_secret_is_replaced_and_owner_is_told(clean_db, monkeypatch, told):
    transport = Transport([{"success": True, "route": "chat_id"}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    key = "sk-ant-api03-" + "Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3hJ5kL7"

    d = await delivery.deliver(PING, f"Here is the key: {key}", purpose="reply")

    assert d.ok and d.scrubbed
    sent = transport.calls[0][1]
    assert key not in sent and "password or key" in sent
    assert len(told) == 1 and key not in told[0]["text"]
    assert told[0]["origin"] == ("ping:owner" if PING.get("from_owner") else "ping:other")


async def test_a_broken_scan_fails_closed(clean_db, monkeypatch, told):
    from services import secret_scan

    def boom(*a, **k):
        raise RuntimeError("scan broke")

    monkeypatch.setattr(secret_scan, "find_secrets", boom)
    transport = Transport([{"success": True}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    d = await delivery.deliver(PING, "harmless words", purpose="reply")
    assert d.scrubbed and "harmless words" not in transport.calls[0][1]


async def _ledger_row(message: str, sent_at: datetime, chat: str = "chat123", ping_id=PING["id"]):
    from services.database import async_session

    async with async_session() as session:
        await session.execute(text(
            "INSERT INTO imessage_outgoing_sends (chat_identifier, text_hash, sent_at, ok, text_norm_hash, "
            "message_rowid, route, ping_id) VALUES (:c, 'x', :at, true, :h, 31, 'chat_id', :pid)"),
            {"c": chat, "at": sent_at, "h": imessage_sent.text_hash(message), "pid": ping_id})
        await session.commit()


async def test_the_same_text_within_ten_minutes_is_not_sent_again(clean_db, monkeypatch, told):
    await _ledger_row("Sorry — this took longer than 10 minutes", datetime.now(timezone.utc) - timedelta(minutes=2))
    transport = Transport([])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)

    d = await delivery.deliver(PING, "Sorry — this took longer than 10 minutes", purpose="fallback")

    assert d.ok and d.deduped and d.rowid == 31
    assert transport.calls == []


async def test_another_pings_same_template_is_still_sent(clean_db, monkeypatch, told):
    """Review 2026-10-11 (verified): one ping's timeout template, 3 min old, made
    the next ping's own a "deduped" no-op and its asker heard nothing."""
    await _ledger_row("Sorry — this took longer than 10 minutes", datetime.now(timezone.utc) - timedelta(minutes=3),
                      ping_id=1)
    transport = Transport([{"success": True, "route": "chat_id", "message_rowid": 82}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)

    d = await delivery.deliver(PING, "Sorry — this took longer than 10 minutes", purpose="fallback")

    assert d.ok and not d.deduped and d.rowid == 82
    assert len(transport.calls) == 1


async def test_a_three_hour_old_copy_is_not_a_duplicate(clean_db, monkeypatch, told):
    await _ledger_row("Done", datetime.now(timezone.utc) - timedelta(hours=3))
    transport = Transport([{"success": True}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    d = await delivery.deliver(PING, "Done", purpose="reply")
    assert d.ok and not d.deduped and len(transport.calls) == 1


async def test_a_failed_send_whose_row_is_in_chat_db_was_delivered(clean_db, monkeypatch, told):
    transport = Transport([{"success": False, "error": "Messages timed out after 30 s"}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    looked = []

    def found(chat, since, text_=None):
        looked.append((chat, since, text_))
        return {"rowid": 90, "guid": "G", "error": 0}

    monkeypatch.setattr(imessage_service, "recent_from_me_since", found)

    d = await delivery.deliver(PING, "Answer", purpose="reply")

    assert d.ok and d.route == "found" and d.rowid == 90
    assert len(transport.calls) == 1, "never a second send when the first one landed"
    assert looked[0][1].tzinfo is not None


async def test_the_row_found_on_the_second_look_was_delivered(clean_db, monkeypatch, told):
    transport = Transport([{"success": False, "error": "timeout"}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    looks = iter([None, {"rowid": 91, "guid": "G", "error": 0}])
    monkeypatch.setattr(imessage_service, "recent_from_me_since", lambda *a, **k: next(looks))
    d = await delivery.deliver(PING, "Answer", purpose="reply")
    assert d.ok and d.rowid == 91 and len(transport.calls) == 1


async def test_one_retry_then_an_honest_failure(clean_db, monkeypatch, told):
    transport = Transport([{"success": False, "error": "-1728 one"}, {"success": False, "error": "-1728 two"}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    monkeypatch.setattr(imessage_service, "recent_from_me_since", lambda *a, **k: None)

    d = await delivery.deliver(PING, "Answer", purpose="reply")

    assert not d.ok and "-1728 two" in d.error
    assert len(transport.calls) == 2


async def test_the_retry_can_succeed(clean_db, monkeypatch, told):
    transport = Transport([{"success": False, "error": "-1728"}, {"success": True, "route": "participant"}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    monkeypatch.setattr(imessage_service, "recent_from_me_since", lambda *a, **k: None)
    d = await delivery.deliver(PING, "Answer", purpose="reply")
    assert d.ok and d.route == "participant" and len(transport.calls) == 2


async def test_a_chat_db_error_on_the_row_is_never_a_reason_to_resend(clean_db, monkeypatch, told):
    transport = Transport([{"success": False, "error": "timeout"}])
    monkeypatch.setattr(imessage_service, "send_to_chat", transport)
    monkeypatch.setattr(imessage_service, "recent_from_me_since",
                        lambda *a, **k: {"rowid": 92, "guid": "G", "error": 22})
    d = await delivery.deliver(PING, "Answer", purpose="reply")
    assert d.ok and len(transport.calls) == 1
