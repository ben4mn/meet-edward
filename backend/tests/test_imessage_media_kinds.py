"""What an iMessage attachment is, read from chat.db (AREA 3 P1, 2026-10-11).

The media audit (docs/research-2026-10-10/media-audit.md) found link
previews and audio messages labelled ``file``: both have a NULL mime type.
A link preview read ``[file: 3F2A….pluginPayloadAttachment #n]`` although
Messages already holds the page's title and URL in ``payload_data``
(LPLinkMetadata), offline. Now:

- an attachment carries ``uti``, ``transfer_state`` and ``total_bytes``
  when chat.db has them, and its kind falls back to the UTI and the
  extension (CAF audio is ``audio``);
- a URL balloon's preview reads ``[link: "<title>" (<site>) attachment:<rowid>]``,
  parsed from ``payload_data`` (``services/media/linkmeta.py``), never
  fetched; the message carries ``link`` for the store and the ping turn;
- the describer never sends a link preview or an audio file to vision.
"""

from __future__ import annotations

import pytest

from tests.fixtures.fake_chatdb import URL_BALLOON, FakeChatDB, link_payload

pytestmark = pytest.mark.unit

PARKER = "+12015550101"


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    d.add_chat(PARKER)
    return d


def _one(listener):
    rows = listener._query_chat_db(0)
    assert len(rows) == 1
    return rows[0]


def test_kind_falls_back_to_uti_and_extension():
    from services.heartbeat.listener_imessage import describe_attachment_kind

    assert describe_attachment_kind(None, name="Audio Message.caf", uti="com.apple.coreaudio-format") == "audio"
    assert describe_attachment_kind(None, name="Audio Message.caf") == "audio"
    assert describe_attachment_kind(None, name="clip.mov", uti="com.apple.quicktime-movie") == "video"
    assert describe_attachment_kind(None, name="IMG_1.HEIC", uti="public.heic") == "image"
    assert describe_attachment_kind(None, name="doc.pdf") == "pdf"
    assert describe_attachment_kind(None, name="x.pluginPayloadAttachment") == "file"
    assert describe_attachment_kind("image/png", name="x.caf") == "image", "a mime type wins"
    assert describe_attachment_kind(None) == "file"


def test_an_audio_message_reads_as_audio_with_its_transfer_facts(db):
    from services.heartbeat import listener_imessage as listener

    rowid = db.add_message(PARKER, None, sender=PARKER, is_audio_message=True)
    att = db.add_attachment(rowid, filename="~/Library/Messages/Attachments/a/Audio Message.caf", mime_type=None,
                            uti="com.apple.coreaudio-format", transfer_state=5, total_bytes=48213)
    msg = _one(listener)
    assert msg["text"] == f"[audio: Audio Message.caf attachment:{att}]"
    [a] = msg["attachments"]
    assert a["uti"] == "com.apple.coreaudio-format" and a["transfer_state"] == 5 and a["total_bytes"] == 48213


def test_a_url_balloon_reads_its_title_from_chat_db(db):
    from services.heartbeat import listener_imessage as listener

    payload = link_payload(url="https://tickets.example/riverside-fest", title="Riverside Fest — resale tickets",
                           site="TicketHub", summary="Sat & Sun passes from $189")
    rowid = db.add_message(PARKER, "https://tickets.example/riverside-fest", sender=PARKER,
                           balloon_bundle_id=URL_BALLOON, payload_data=payload)
    att = db.add_attachment(rowid, filename="~/Library/Messages/Attachments/b/3F2A-11.pluginPayloadAttachment",
                            mime_type=None, uti="dyn.ah62d4rv4ge80q5pxqz")
    msg = _one(listener)
    assert msg["text"] == ('https://tickets.example/riverside-fest '
                           f'[link: "Riverside Fest — resale tickets" (TicketHub) attachment:{att}]')
    assert msg["link"] == {"url": "https://tickets.example/riverside-fest", "original_url": None,
                           "title": "Riverside Fest — resale tickets", "summary": "Sat & Sun passes from $189",
                           "site": "TicketHub"}
    assert msg["attachments"][0]["kind"] == "link"


def test_a_url_balloon_whose_payload_does_not_parse_still_reads_as_a_link(db):
    from services.heartbeat import listener_imessage as listener

    rowid = db.add_message(PARKER, "https://example.com/x", sender=PARKER, balloon_bundle_id=URL_BALLOON,
                           payload_data=b"not a plist")
    att = db.add_attachment(rowid, filename="/tmp/a3a-77.pluginPayloadAttachment", mime_type=None)
    msg = _one(listener)
    assert msg["text"] == f"https://example.com/x [link preview attachment:{att}]"
    assert msg.get("link") is None


def test_another_apps_balloon_is_not_a_link(db):
    from services.heartbeat import listener_imessage as listener

    rowid = db.add_message(PARKER, None, sender=PARKER,
                           balloon_bundle_id="com.apple.messages.MSMessageExtensionBalloonPlugin:0000000000:com.apple.PassbookUIService.PeerPaymentMessagesExtension")
    att = db.add_attachment(rowid, filename="/tmp/a3a-88.pluginPayloadAttachment", mime_type=None)
    msg = _one(listener)
    assert msg["text"] == f"[file: a3a-88.pluginPayloadAttachment attachment:{att}]"
    assert msg.get("link") is None


def test_the_thread_reader_shows_link_titles_too(db):
    from services.heartbeat import listener_imessage as listener

    rowid = db.add_message(PARKER, "https://ex.com/a", sender=PARKER, balloon_bundle_id=URL_BALLOON,
                           payload_data=link_payload(url="https://ex.com/a", title="A page"))
    att = db.add_attachment(rowid, filename="/tmp/a3a-99.pluginPayloadAttachment", mime_type=None)
    [line] = listener.get_chat_thread(PARKER, limit=5)
    assert line["text"] == f'https://ex.com/a [link: "A page" attachment:{att}]'
    [recent] = listener.get_recent_messages_across_chats(hours=1, limit=5)
    assert recent["text"] == line["text"]


def test_an_older_chat_db_without_the_columns_still_reads(tmp_path, monkeypatch):
    """test_imessage_attachments' minimal schema: no balloon or payload
    columns, no uti/transfer_state on attachments."""
    import sqlite3

    from services.heartbeat import listener_imessage as listener

    p = tmp_path / "old.db"
    conn = sqlite3.connect(str(p))
    conn.executescript("""
    CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, is_from_me INTEGER DEFAULT 0,
        date INTEGER DEFAULT 0, handle_id INTEGER, associated_message_type INTEGER DEFAULT 0, service TEXT);
    CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
    CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT, style INTEGER, guid TEXT);
    CREATE TABLE chat_message_join (message_id INTEGER, chat_id INTEGER);
    CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT, transfer_name TEXT);
    CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
    INSERT INTO chat VALUES (1, '+12015550101', NULL, 45, 'g');
    INSERT INTO handle VALUES (1, '+12015550101');
    INSERT INTO message (ROWID, text, date, handle_id) VALUES (1, NULL, 1, 1);
    INSERT INTO chat_message_join VALUES (1, 1);
    INSERT INTO attachment VALUES (1, '/a/Audio Message.caf', NULL, 'Audio Message.caf');
    INSERT INTO message_attachment_join VALUES (1, 1);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(listener, "CHAT_DB_PATH", str(p))
    [msg] = listener._query_chat_db(0)
    assert msg["text"] == "[audio: Audio Message.caf attachment:1]"
    assert msg.get("link") is None and msg["attachments"][0].get("uti") is None


async def test_the_describer_never_sends_a_link_preview_or_audio_to_vision(monkeypatch):
    from services import attachment_vision
    from services.heartbeat import listener_imessage as listener

    calls = []

    async def fake_describe(path, **_k):
        calls.append(path)
        return "x"

    monkeypatch.setattr(attachment_vision, "describe_image", fake_describe)
    atts = [{"rowid": 1, "mime_type": None, "path": "/a/3F.pluginPayloadAttachment", "name": "3F.pluginPayloadAttachment",
             "kind": "link"},
            {"rowid": 2, "mime_type": None, "path": "/a/Audio Message.caf", "name": "Audio Message.caf",
             "uti": "com.apple.coreaudio-format"}]
    await listener._describe_event_attachments("no-such-event", atts)
    assert calls == []
