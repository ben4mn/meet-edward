"""The fake chat.db the AREA 3 tests build on (``tests/fixtures/fake_chatdb.py``).

It must read back through the listener's real readers, so the inbound and
media tests exercise the same SQL that runs against Ellis's chat.db.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.fixtures.fake_chatdb import FakeChatDB, apple_ts, attributed_body, typedstream_length


def test_typedstream_lengths_are_little_endian():
    assert typedstream_length(5) == b"\x05"
    assert typedstream_length(300) == b"\x81\x2c\x01"            # 300 = 0x012c, LE
    assert typedstream_length(1000) == b"\x81\xe8\x03"
    assert typedstream_length(70_000) == b"\x82" + (70_000).to_bytes(4, "little")
    body = attributed_body("x" * 300)
    assert b"NSString\x01\x94\x84\x01+\x81\x2c\x01" + b"x" * 300 in body


def test_short_attributed_body_decodes():
    from services.heartbeat.imessage_decoder import decode_attributed_body

    assert decode_attributed_body(attributed_body("@Edward look at used tickets")) == "@Edward look at used tickets"


def test_apple_ts_round_trips_through_the_listener():
    from services.heartbeat.listener_imessage import _apple_timestamp_to_datetime

    when = datetime(2026, 10, 11, 0, 28, 5, tzinfo=timezone.utc)
    back = _apple_timestamp_to_datetime(apple_ts(when))
    assert abs((back.replace(tzinfo=timezone.utc) - when).total_seconds()) < 1


def test_fake_chat_db_reads_back_through_the_listener(tmp_path, monkeypatch):
    from services.heartbeat import listener_imessage as listener

    db = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    db.add_chat("chat123456", display_name="Trivia crew", is_group=True,
                participants=["+12015550101", "parker@example.com"])
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    first = db.add_message("chat123456", "who's in for Saturday?", sender="+12015550101", date=t0)
    mention = db.add_message("chat123456", "@Edward look at used tickets", is_from_me=True, body_only=True,
                             thread_originator_guid="EDWARD-GUID", date=t0 + timedelta(minutes=1))
    att = db.add_attachment(mention, filename="~/Library/Messages/Attachments/ab/IMG_3533.heic",
                            mime_type="image/heic", uti="public.heic", total_bytes=1234)

    thread = listener.get_chat_thread("chat123456", limit=10)
    texts = [m.get("text") for m in thread]
    assert any("who's in for Saturday?" in (t or "") for t in texts)
    assert any("@Edward look at used tickets" in (t or "") for t in texts)

    new = listener._query_chat_db(0)
    assert [m["rowid"] for m in new] == [first, mention]
    assert new[1]["is_from_me"] is True and new[1]["is_group"] is True
    assert new[1]["guid"] == "iMessage;+;chat123456"                 # the chat's guid
    assert new[1]["attachments"][0]["rowid"] == att

    roster = {p["handle"] for p in listener.get_chat_participants("chat123456")}
    assert roster == {"+12015550101", "parker@example.com"}
    found = listener.find_attachment(att)
    assert found is not None and found["mime_type"] == "image/heic"

    row = db.message(mention)
    assert row["thread_originator_guid"] == "EDWARD-GUID" and row["text"] is None and row["error"] == 0
    assert db.max_rowid() == mention


def test_a_one_to_one_chat_is_created_on_first_message(tmp_path, monkeypatch):
    from services.heartbeat import listener_imessage as listener

    db = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    rowid = db.add_message("+12015550101", "hey", sender="+12015550101")
    msgs = listener._query_chat_db(0)
    assert [m["rowid"] for m in msgs] == [rowid]
    assert msgs[0]["is_group"] is False and msgs[0]["sender_id"] == "+12015550101"
    assert {p["handle"] for p in listener.get_chat_participants("+12015550101")} == {"+12015550101"}
