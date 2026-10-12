"""The iMessage transport: routing by chat.db, read-back, honest failures
(AREA 3, pings-media-spec §2.5, 2026-10-11).

History: the old ``send_imessage_to_chat`` routed on spelling — anything
starting with ``chat`` was a group (``any;+;``), anything else a 1:1
participant. 15 of Ellis's groups have a 32-hex ``chat_identifier``, so a
reply into them went to ``participant "<hex>"``, Messages "sent" it into a
new undeliverable chat, and the tool reported delivered (R8). Now:

- the route comes from chat.db (``style``, ``service_name``, ``guid``);
  ``applescript_targets`` is pure and tried in order, a ``-1728`` moving on;
- after osascript the transport reads chat.db back for the from-me row; a
  row found is a confirmed send even after an osascript timeout, and a
  failure is ``success: False`` with the real error — never delivered;
- a send inside a ping turn is noted on the ping's context before the
  call returns; an autonomous send into a chat whose ping turn is running
  is refused;
- only successful sends reach the ledger, with an aware ``sent_at``.

Every test patches ``_run_osascript`` (conftest refuses a real one) and
points chat.db at a throwaway file.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import imessage_sent, imessage_service
from services.pings import hooks as pings_hooks
from tests.fixtures.fake_chatdb import FakeChatDB

HEX = "7f3c0a1b2c3d4e5f60718293a4b5ce21"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(imessage_service, "is_available", lambda: True)
    monkeypatch.setattr(imessage_service, "READBACK_TIMEOUT_S", 0.3)
    monkeypatch.setattr(imessage_service, "READBACK_INTERVAL_S", 0.02)
    imessage_sent.reset_for_tests()
    yield
    imessage_sent.reset_for_tests()


class FakeMessages:
    """Stands in for osascript: records each script, answers per target, and
    writes the from-me row into the fake chat.db the way Messages does."""

    def __init__(self, db: FakeChatDB, behaviour=None, default="ok"):
        self.db = db
        self.behaviour = dict(behaviour or {})
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def __call__(self, script: str, timeout: float = 30):
        m = re.search(r'chat id "((?:[^"\\]|\\.)*)"', script)
        route, target = ("chat_id", m.group(1)) if m else (
            "participant", re.search(r'participant "((?:[^"\\]|\\.)*)"', script).group(1))
        text = re.search(r'send "((?:[^"\\]|\\.)*)" to', script).group(1)
        text = text.replace('\\"', '"').replace("\\\\", "\\")
        self.calls.append((route, target))
        what = self.behaviour.get(target, self.default)
        tail = target.rsplit(";", 1)[-1]
        if what in ("ok", "timeout_sent"):
            self.db.add_message(tail, text, is_from_me=True)
        if what in ("timeout", "timeout_sent"):
            raise subprocess.TimeoutExpired(["osascript"], timeout)
        if what == "-1728":
            return subprocess.CompletedProcess(["osascript"], 1, "", f"execution error: Messages got an error: "
                                                                    f"Can't get chat id \"{target}\". (-1728)")
        if what == "fail":
            return subprocess.CompletedProcess(["osascript"], 1, "", "execution error: Not authorized (-1743)")
        return subprocess.CompletedProcess(["osascript"], 0, "", "")


@pytest.fixture
def db(tmp_path, monkeypatch):
    fake = FakeChatDB(tmp_path / "chat.db")
    fake.point_listener(monkeypatch)
    return fake


# -------------------------------------------------- applescript_targets ----

def test_a_32_hex_group_tries_any_then_the_chat_db_guid():
    info = {"chat_identifier": HEX, "guid": f"SMS;+;{HEX}", "style": 43, "is_group": True,
            "display_name": "Trivia crew", "service": "SMS"}
    assert imessage_service.applescript_targets(HEX, info) == [
        ("chat_id", f"any;+;{HEX}"), ("chat_id", f"SMS;+;{HEX}"), ("chat_id", f"iMessage;+;{HEX}")]


def test_a_32_hex_id_with_no_chat_db_row_is_still_a_group():
    targets = imessage_service.applescript_targets(HEX, None)
    assert targets[0] == ("chat_id", f"any;+;{HEX}")
    assert all(route == "chat_id" for route, _ in targets)


def test_a_chat_prefixed_group_and_dedupe():
    info = {"chat_identifier": "chat123", "guid": "iMessage;+;chat123", "style": 43, "is_group": True,
            "display_name": None, "service": "iMessage"}
    assert imessage_service.applescript_targets("chat123", info) == [
        ("chat_id", "any;+;chat123"), ("chat_id", "iMessage;+;chat123")]


def test_a_pre_prefixed_id_is_kept_first():
    targets = imessage_service.applescript_targets("iMessage;+;chat123", None)
    assert targets[0] == ("chat_id", "iMessage;+;chat123")
    assert ("chat_id", "any;+;chat123") in targets


def test_a_1to1_sms_chat_goes_by_its_guid_before_the_participant():
    info = {"chat_identifier": "+12015550101", "guid": "SMS;-;+12015550101", "style": 45, "is_group": False,
            "display_name": None, "service": "SMS"}
    assert imessage_service.applescript_targets("+12015550101", info) == [
        ("chat_id", "SMS;-;+12015550101"), ("participant", "+12015550101")]


def test_a_1to1_imessage_chat_and_a_bare_phone_or_email_go_by_participant():
    info = {"chat_identifier": "+12015550101", "guid": "iMessage;-;+12015550101", "style": 45, "is_group": False,
            "display_name": None, "service": "iMessage"}
    assert imessage_service.applescript_targets("+12015550101", info) == [("participant", "+12015550101")]
    assert imessage_service.applescript_targets("+15555550175", None) == [("participant", "+15555550175")]
    assert imessage_service.applescript_targets("friend@example.com", None) == [
        ("participant", "friend@example.com")]


def test_a_name_is_never_a_participant():
    assert imessage_service.applescript_targets("Nate Brooks", None) == []


# ------------------------------------------------------------ chat_info ----

def test_chat_info_reads_style_service_and_guid(db):
    db.add_chat(HEX, display_name="Trivia crew", is_group=True, guid=f"RCS;+;{HEX}", service_name="RCS",
                participants=["+12015550101", "+12015550102"])
    info = imessage_service.chat_info(HEX)
    assert info["is_group"] is True and info["style"] == 43
    assert info["guid"] == f"RCS;+;{HEX}" and info["service"] == "RCS"
    assert info["display_name"] == "Trivia crew"
    assert imessage_service.chat_info(f"any;+;{HEX}")["guid"] == f"RCS;+;{HEX}"


def test_chat_info_is_none_without_chat_db(monkeypatch):
    from services.heartbeat import listener_imessage

    monkeypatch.setattr(listener_imessage, "CHAT_DB_PATH", "/nonexistent/chat.db")
    assert imessage_service.chat_info("chat123") is None


# ----------------------------------------------------------- send_to_chat ----

def test_a_32_hex_group_send_falls_through_minus_1728_to_the_guid(db, monkeypatch):
    db.add_chat(HEX, display_name="Trivia crew", is_group=True, guid=f"SMS;+;{HEX}", service_name="SMS",
                participants=["+12015550101"])
    fake = FakeMessages(db, {f"any;+;{HEX}": "-1728"})
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)

    result = imessage_service.send_to_chat(HEX, "Saturday from $85 - Edward 🤖")

    assert fake.calls == [("chat_id", f"any;+;{HEX}"), ("chat_id", f"SMS;+;{HEX}")]
    assert result["success"] is True and result["confirmed"] is True
    assert result["route"] == "chat_id"
    assert result["message_rowid"] == db.max_rowid()
    assert result["message_guid"]
    assert imessage_sent.is_edward_rowid(result["message_rowid"])


def test_every_target_failing_is_an_honest_failure_with_no_ledger(db, monkeypatch):
    db.add_chat(HEX, is_group=True, guid=f"iMessage;+;{HEX}", participants=["+12015550101"])
    fake = FakeMessages(db, default="-1728")
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)
    recorded = []
    monkeypatch.setattr(imessage_service, "_record_outgoing_send", lambda *a, **k: recorded.append((a, k)))

    result = imessage_service.send_to_chat(HEX, "hello")

    assert result["success"] is False
    assert "-1728" in result["error"]
    assert recorded == []
    assert not imessage_sent.unconfirmed_recently(HEX)


def test_a_non_routing_error_stops_at_once(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    fake = FakeMessages(db, default="fail")
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)

    result = imessage_service.send_to_chat("chat123", "hello")

    assert result["success"] is False and "-1743" in result["error"]
    assert len(fake.calls) == 1


def test_an_osascript_timeout_with_the_row_present_is_a_confirmed_send(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    fake = FakeMessages(db, default="timeout_sent")
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)

    result = imessage_service.send_to_chat("chat123", "on it")

    assert result["success"] is True and result["confirmed"] is True
    assert len(fake.calls) == 1, "a timeout never tries another target: Messages may have sent"


def test_an_osascript_timeout_with_no_row_is_a_failure(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    monkeypatch.setattr(imessage_service, "_run_osascript", FakeMessages(db, default="timeout"))

    result = imessage_service.send_to_chat("chat123", "on it")

    assert result["success"] is False
    assert "timed out" in result["error"].lower()


def test_osascript_ok_but_no_row_is_unconfirmed_and_trips_the_breaker(db, monkeypatch):
    db.add_chat("+12015550199", participants=["+12015550199"])

    def ok_but_nothing(script, timeout=30):
        return subprocess.CompletedProcess(["osascript"], 0, "", "")

    monkeypatch.setattr(imessage_service, "_run_osascript", ok_but_nothing)

    result = imessage_service.send_to_chat("+12015550199", "hi")

    assert result["success"] is True and result["confirmed"] is False
    assert result["message_rowid"] is None
    assert imessage_sent.unconfirmed_recently("+12015550199")


def test_the_read_back_ignores_another_text_and_owner_rows(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])

    def other_text(script, timeout=30):
        db.add_message("chat123", "something Ellis typed", is_from_me=True)
        db.add_message("chat123", "hello", sender="+12015550101")
        return subprocess.CompletedProcess(["osascript"], 0, "", "")

    monkeypatch.setattr(imessage_service, "_run_osascript", other_text)
    result = imessage_service.send_to_chat("chat123", "hello")
    assert result["confirmed"] is False


def test_the_read_back_takes_the_row_the_listener_claimed(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])

    def listener_got_there_first(script, timeout=30):
        rowid = db.add_message("chat123", "hello there", is_from_me=True)
        assert imessage_sent.claim_inflight("chat123", "hello there", rowid)
        return subprocess.CompletedProcess(["osascript"], 0, "", "")

    monkeypatch.setattr(imessage_service, "_run_osascript", listener_got_there_first)
    result = imessage_service.send_to_chat("chat123", "hello there")
    assert result["confirmed"] is True and result["message_rowid"] == db.max_rowid()


def test_phone_and_email_chat_ids_send_by_participant(db, monkeypatch):
    fake = FakeMessages(db)
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)

    assert imessage_service.send_imessage_to_chat("+15555550175", "hello")["success"]
    assert imessage_service.send_imessage_to_chat("friend@example.com", "yo")["success"]
    assert fake.calls == [("participant", "+15555550175"), ("participant", "friend@example.com")]


def test_the_wrappers_keep_their_shapes(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    monkeypatch.setattr(imessage_service, "_run_osascript", FakeMessages(db))

    to_chat = imessage_service.send_imessage_to_chat("chat123", "hi all")
    assert to_chat["success"] is True and to_chat["chat_identifier"] == "chat123"
    assert "message" in to_chat

    to_person = imessage_service.send_imessage("+12015550133", "hi you")
    assert to_person["success"] is True and to_person["recipient"] == "+12015550133"

    refused = imessage_service.send_imessage("Nate Brooks", "hi")
    assert refused["success"] is False and "not a phone number" in refused["error"]


def test_a_name_as_chat_id_fails_honestly(db, monkeypatch):
    fake = FakeMessages(db)
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)
    result = imessage_service.send_imessage_to_chat("Nate Brooks", "hi")
    assert result["success"] is False and fake.calls == []


# ----------------------------------------------------- the ping context ----

async def test_a_send_in_a_ping_turn_is_noted_before_it_returns(db, monkeypatch):
    db.add_chat("chat123", is_group=True, guid="iMessage;+;chat123", participants=["+12015550101"])
    monkeypatch.setattr(imessage_service, "_run_osascript", FakeMessages(db))
    ctx = pings_hooks.new_context(ping_id=5, chat_identifier="chat123", chat_guid="iMessage;+;chat123",
                                  is_group=True, conversation_id="ping-5", must_answer=True, third_party=False)
    seen_inflight = []
    real_begin = imessage_sent.begin

    def spy_begin(chat, text, now=None):
        seen_inflight.append(ctx["inflight"])
        return real_begin(chat, text, now)

    monkeypatch.setattr(imessage_sent, "begin", spy_begin)
    token = pings_hooks.CURRENT_PING.set(ctx)
    try:
        result = await asyncio.to_thread(imessage_service.send_to_chat, "any;+;chat123", "Answer", purpose="reply")
    finally:
        pings_hooks.CURRENT_PING.reset(token)

    assert seen_inflight == [1]
    assert ctx["inflight"] == 0
    assert len(ctx["sends"]) == 1
    send = ctx["sends"][0]
    assert send["ok"] is True and send["purpose"] == "reply" and send["rowid"] == result["message_rowid"]


async def test_a_failed_send_in_a_ping_turn_is_noted_as_failed(db, monkeypatch):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    monkeypatch.setattr(imessage_service, "_run_osascript", FakeMessages(db, default="-1728"))
    ctx = pings_hooks.new_context(ping_id=5, chat_identifier="chat123", chat_guid=None, is_group=True,
                                  conversation_id="ping-5", must_answer=True, third_party=False)
    token = pings_hooks.CURRENT_PING.set(ctx)
    try:
        await asyncio.to_thread(imessage_service.send_to_chat, "chat123", "Answer")
    finally:
        pings_hooks.CURRENT_PING.reset(token)
    assert ctx["inflight"] == 0
    assert ctx["sends"][0]["ok"] is False and "-1728" in ctx["sends"][0]["error"]


def test_a_send_to_another_chat_is_not_noted_on_the_ping(db, monkeypatch):
    db.add_chat("chat999", is_group=True, participants=["+12015550101"])
    monkeypatch.setattr(imessage_service, "_run_osascript", FakeMessages(db))
    ctx = pings_hooks.new_context(ping_id=5, chat_identifier="chat123", chat_guid=None, is_group=True,
                                  conversation_id="ping-5", must_answer=True, third_party=False)
    token = pings_hooks.CURRENT_PING.set(ctx)
    try:
        imessage_service.send_to_chat("chat999", "elsewhere")
    finally:
        pings_hooks.CURRENT_PING.reset(token)
    assert ctx["sends"] == []


@pytest.mark.parametrize("source,refused", [("mind", True), ("pulse", True), ("scheduled_event", True),
                                            ("heartbeat", True), ("chat", False), ("ping", False),
                                            ("ping_owner", False)])
def test_the_in_flight_guard(db, monkeypatch, source, refused):
    from services import turn_ledger

    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    fake = FakeMessages(db)
    monkeypatch.setattr(imessage_service, "_run_osascript", fake)
    monkeypatch.setattr(pings_hooks, "chats_in_flight", lambda: {"chat123"})
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source=source, id="t1"))

    result = imessage_service.send_to_chat("any;+;chat123", "hello")

    if refused:
        assert result["success"] is False
        assert "answering a ping in this chat" in result["error"]
        assert fake.calls == []
    else:
        assert result["success"] is True and fake.calls


# ------------------------------------------------------------- the ledger ----

async def test_the_ledger_row_is_aware_and_carries_the_read_back(clean_db, db, monkeypatch):
    from sqlalchemy import select

    from services import turn_ledger
    from services.database import ImessageOutgoingSendModel, async_session

    db.add_chat(HEX, is_group=True, guid=f"iMessage;+;{HEX}", participants=["+12015550101"])
    monkeypatch.setattr(imessage_service, "_run_osascript", FakeMessages(db))
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source="ping", id="t1"))
    imessage_service.note_main_loop(asyncio.get_running_loop())
    ctx = pings_hooks.new_context(ping_id=12, chat_identifier=HEX, chat_guid=f"iMessage;+;{HEX}", is_group=True,
                                  conversation_id="ping-12", must_answer=True, third_party=False)
    token = pings_hooks.CURRENT_PING.set(ctx)
    try:
        result = await asyncio.to_thread(imessage_service.send_to_chat, HEX, "Saturday - Edward 🤖",
                                         purpose="reply")
    finally:
        pings_hooks.CURRENT_PING.reset(token)
    assert result["confirmed"]

    rows = []
    for _ in range(100):
        await asyncio.sleep(0.02)
        async with async_session() as session:
            rows = (await session.execute(select(ImessageOutgoingSendModel))).scalars().all()
        if rows:
            break
    assert len(rows) == 1
    row = rows[0]
    assert row.ok is True
    assert abs((row.sent_at - datetime.now(timezone.utc)).total_seconds()) < 60, "sent_at must be bound aware UTC"
    assert row.confirmed_at is not None
    assert row.message_rowid == result["message_rowid"] and row.message_guid == result["message_guid"]
    assert row.route == "chat_id" and row.chat_guid == f"iMessage;+;{HEX}"
    assert row.text_norm_hash == imessage_sent.text_hash("Saturday")
    assert row.text_len == len("Saturday - Edward 🤖")
    assert row.ping_id == 12 and row.purpose == "reply" and row.turn_source == "ping"
    assert row.chat_identifier == HEX


def test_the_seam_refuses_real_osascript_in_tests():
    with pytest.raises(RuntimeError, match="osascript"):
        imessage_service._run_osascript('tell application "Messages" to return name', timeout=1)


# --------------------------------------------------- recent_from_me_since ----

def test_recent_from_me_since_finds_a_signed_or_equal_row_after_since(db):
    db.add_chat("chat123", is_group=True, participants=["+12015550101"])
    since = datetime.now(timezone.utc) - timedelta(seconds=30)
    db.add_message("chat123", "old reply\n\n- Edward 🤖", is_from_me=True,
                   date=since - timedelta(minutes=5))
    assert imessage_service.recent_from_me_since("chat123", since) is None

    db.add_message("chat123", "Ellis typing", is_from_me=True)
    assert imessage_service.recent_from_me_since("chat123", since) is None

    rid = db.add_message("chat123", "fresh reply\n\n- Edward 🤖", is_from_me=True, error=22)
    found = imessage_service.recent_from_me_since("any;+;chat123", since)
    assert found["rowid"] == rid and found["error"] == 22 and found["guid"]

    rid2 = db.add_message("chat123", "Plain words", is_from_me=True)
    assert imessage_service.recent_from_me_since("chat123", since, "plain   WORDS - Edward 🤖")["rowid"] == rid2
