"""iMessage attachments reach Edward.

Why (2026-09-03, Ellis): a photo sent over iMessage arrived as a single
U+FFFC glyph (or was dropped when the row had no text at all). Nothing
joined chat.db's `attachment` table, so Edward could neither see nor act
on images and files. Now every chat.db reader carries an `attachments`
list, attachment-only rows survive with a readable placeholder, and the
thread view names them so Edward can ask for one.
"""

import sqlite3
from pathlib import Path

import pytest

from services.heartbeat import listener_imessage as listener
from services.heartbeat.listener_imessage import (
    attachment_placeholder,
    describe_attachment_kind,
    parse_attachment_blob,
    strip_object_replacement,
)

pytestmark = pytest.mark.unit

SCHEMA = """
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, is_from_me INTEGER DEFAULT 0,
    date INTEGER DEFAULT 0, handle_id INTEGER, associated_message_type INTEGER DEFAULT 0, service TEXT DEFAULT 'iMessage');
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT, style INTEGER DEFAULT 0, guid TEXT);
CREATE TABLE chat_message_join (message_id INTEGER, chat_id INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT, transfer_name TEXT, total_bytes INTEGER DEFAULT 0);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
"""


def _build(path: Path, rows):
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO chat VALUES (1, '+15551234567', NULL, 0, 'g')")
    conn.execute("INSERT INTO handle VALUES (1, '+15551234567')")
    att_id = 1
    for idx, r in enumerate(rows, start=1):
        conn.execute("INSERT INTO message (ROWID, text, attributedBody, is_from_me, date, handle_id) VALUES (?,?,?,?,?,?)",
                     (idx, r.get("text"), None, 1 if r.get("is_from_me") else 0, idx * 1_000_000_000, 1))
        conn.execute("INSERT INTO chat_message_join VALUES (?, 1)", (idx,))
        for a in r.get("attachments", []):
            conn.execute("INSERT INTO attachment VALUES (?,?,?,?,?)", (att_id, a["filename"], a["mime"], a.get("name"), a.get("bytes", 100)))
            conn.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (idx, att_id))
            att_id += 1
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "chat.db"
    monkeypatch.setattr(listener, "CHAT_DB_PATH", str(p))
    return p


class TestPure:
    def test_parse_attachment_blob(self):
        blob = "7|image/heic|~/Library/Messages/Attachments/ab/IMG_1.HEIC|IMG_1.HEIC\x1e8|application/pdf|/tmp/x.pdf|x.pdf"
        atts = parse_attachment_blob(blob)
        assert atts[0] == {"rowid": 7, "mime_type": "image/heic", "path": str(Path.home() / "Library/Messages/Attachments/ab/IMG_1.HEIC"), "name": "IMG_1.HEIC"}
        assert atts[1]["mime_type"] == "application/pdf" and parse_attachment_blob(None) == []

    def test_kind_and_placeholder(self):
        assert describe_attachment_kind("image/heic") == "image"
        assert describe_attachment_kind("video/quicktime") == "video"
        assert describe_attachment_kind("application/pdf") == "pdf"
        assert describe_attachment_kind(None) == "file"
        atts = [{"rowid": 7, "mime_type": "image/heic", "path": "/x/IMG_1.HEIC", "name": "IMG_1.HEIC"}]
        assert attachment_placeholder(atts) == "[image: IMG_1.HEIC attachment:7]"

    def test_placeholder_carries_the_description_once_there_is_one(self, monkeypatch):
        from services.media import hooks as media_hooks

        monkeypatch.delenv("EDWARD_MEDIA", raising=False)
        atts = [{"rowid": 449321, "mime_type": "image/heic", "path": "/x/IMG_3533.HEIC", "name": "IMG_3533.HEIC"}]
        media_hooks.note_description("attachment:449321", "a screenshot of a Ticketmaster order")
        try:
            assert attachment_placeholder(atts) == (
                '[image: IMG_3533.HEIC attachment:449321 - "a screenshot of a Ticketmaster order"]')
            monkeypatch.setenv("EDWARD_MEDIA", "off")
            assert attachment_placeholder(atts) == "[image: IMG_3533.HEIC attachment:449321]"
        finally:
            with media_hooks._desc_lock:
                media_hooks._descriptions.pop("attachment:449321", None)

    def test_strip_object_replacement(self):
        assert strip_object_replacement("￼") == ""
        assert strip_object_replacement("look ￼ at this") == "look at this"


class TestQuery:
    def test_attachment_only_message_survives_with_placeholder(self, db):
        _build(db, [
            {"text": "￼", "attachments": [{"filename": "~/Library/Messages/Attachments/ab/IMG_1.HEIC", "mime": "image/heic", "name": "IMG_1.HEIC"}]},
            {"text": None, "attachments": [{"filename": "/tmp/doc.pdf", "mime": "application/pdf", "name": "doc.pdf"}]},
            {"text": "plain"},
        ])
        rows = listener._query_chat_db(0)
        assert [r["rowid"] for r in rows] == [1, 2, 3]
        assert rows[0]["text"] == "[image: IMG_1.HEIC attachment:1]"
        assert rows[0]["attachments"][0]["mime_type"] == "image/heic"
        assert rows[1]["text"] == "[pdf: doc.pdf attachment:2]"
        assert rows[2]["attachments"] == []

    def test_caption_plus_photo_keeps_caption_and_names_the_photo(self, db):
        _build(db, [{"text": "look at this ￼", "attachments": [{"filename": "/a/p.jpeg", "mime": "image/jpeg", "name": "p.jpeg"}]}])
        rows = listener._query_chat_db(0)
        assert rows[0]["text"] == "look at this [image: p.jpeg attachment:1]"

    def test_thread_and_recent_carry_attachments(self, db):
        _build(db, [{"text": None, "attachments": [{"filename": "/a/p.jpeg", "mime": "image/jpeg", "name": "p.jpeg"}]}])
        thread = listener.get_chat_thread("+15551234567", limit=10)
        assert thread and thread[0]["attachments"][0]["name"] == "p.jpeg" and "[image: p.jpeg" in thread[0]["text"]
        text = listener.format_chat_thread(thread)
        assert "[image: p.jpeg attachment:1]" in text

    def test_recent_across_chats_includes_attachment_rows(self, db, monkeypatch):
        _build(db, [{"text": None, "attachments": [{"filename": "/a/p.jpeg", "mime": "image/jpeg", "name": "p.jpeg"}]}])
        rows = listener.get_recent_messages_across_chats(hours=10**6, contact=None, limit=10)
        assert rows and rows[0]["attachments"]


@pytest.mark.integration
async def test_store_events_records_attachments_in_raw_data(clean_db, monkeypatch):
    import json
    from unittest.mock import AsyncMock
    monkeypatch.setattr(listener, "_resolve_contact_name", AsyncMock(return_value="Rowan"))
    monkeypatch.setattr(listener, "_schedule_attachment_description", lambda *a, **k: None)
    import time
    msg = {"rowid": 900001, "text": "[image: p.jpeg attachment:1]", "is_from_me": False,
           "date": int((time.time() - listener.APPLE_EPOCH_OFFSET) * 1e9), "service": "iMessage",
           "sender_id": "+15551234567", "chat_identifier": "+15551234567", "display_name": None,
           "is_group": False, "guid": "g900001",
           "attachments": [{"rowid": 1, "mime_type": "image/jpeg", "path": "/a/p.jpeg", "name": "p.jpeg"}]}
    result = await listener._store_events([msg], owner_handles=[])
    assert result.stored == 1
    from sqlalchemy import select
    from services import database
    from services.database import HeartbeatEventModel
    async with database.async_session() as session:
        ev = (await session.execute(select(HeartbeatEventModel))).scalar_one()
    assert json.loads(ev.raw_data)["attachments"][0]["name"] == "p.jpeg"


class TestFindAttachment:
    def test_find_attachment_names_its_chat(self, db):
        _build(db, [{"text": None, "attachments": [{"filename": "/a/p.jpeg", "mime": "image/jpeg", "name": "p.jpeg",
                                                    "bytes": 2048}]}])
        att = listener.find_attachment(1)
        assert att["chat_identifier"] == "+15551234567"
        assert att["name"] == "p.jpeg" and att["total_bytes"] == 2048

    def test_find_attachment_unknown(self, db):
        _build(db, [])
        assert listener.find_attachment(99) is None


@pytest.mark.integration
async def test_the_describer_notes_each_description(clean_db, monkeypatch):
    """The describer (R7, now with real ids) notes each image's description for
    ``attachment:<rowid>``, so placeholders and saved history carry it."""
    import json
    import uuid

    from services import attachment_vision, database
    from services.media import hooks as media_hooks

    monkeypatch.delenv("EDWARD_MEDIA", raising=False)
    calls = []

    async def fake_describe(path, **_k):
        calls.append(path)
        return "a cat on a laptop"

    monkeypatch.setattr(attachment_vision, "describe_image", fake_describe)
    import services.heartbeat.heartbeat_service as hb

    monkeypatch.setattr(hb, "request_triage", lambda reason: None)
    event_id = str(uuid.uuid4())
    async with database.async_session() as session:
        session.add(database.HeartbeatEventModel(id=event_id, source="imessage", event_type="message_received",
                                                 summary="look", raw_data=json.dumps({"rowid": 5}),
                                                 source_id="imessage:5"))
        await session.commit()
    atts = [{"rowid": 77001, "mime_type": "image/heic", "path": "/a/IMG_9.HEIC", "name": "IMG_9.HEIC"},
            {"rowid": 77002, "mime_type": "application/pdf", "path": "/a/x.pdf", "name": "x.pdf"}]
    try:
        await listener._describe_event_attachments(event_id, atts)
        assert media_hooks.describe_ref("attachment:77001") == "a cat on a laptop"
        assert media_hooks.describe_ref("attachment:77002") is None
        async with database.async_session() as session:
            ev = await session.get(database.HeartbeatEventModel, event_id)
        lines = json.loads(ev.raw_data)["attachment_descriptions"]
        assert lines == ["[image IMG_9.HEIC attachment:77001]: a cat on a laptop", "[pdf x.pdf attachment:77002]"]
        assert calls == ["/a/IMG_9.HEIC"]
    finally:
        with media_hooks._desc_lock:
            media_hooks._descriptions.pop("attachment:77001", None)
