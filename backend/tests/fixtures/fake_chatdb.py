"""A throwaway Messages ``chat.db`` for tests (AREA 3, 2026-10-11).

Lifted from ``test_imessage_thread_context.py``'s inline schema and widened
to the columns the ping and media work reads: ``message.guid``,
``thread_originator_guid`` and ``error``; ``attachment.uti``,
``transfer_state`` and ``total_bytes``; ``chat.style``, ``chat.guid`` and
``chat.service_name``. Nothing here touches Ellis's real ``chat.db``: the file
lives under the test's ``tmp_path`` and ``point_listener`` re-points
``listener_imessage.CHAT_DB_PATH`` with ``monkeypatch``.

    db = FakeChatDB(tmp_path / "chat.db")
    db.point_listener(monkeypatch)
    db.add_chat("chat123", display_name="Trivia crew", is_group=True, participants=["+12015550101"])
    rowid = db.add_message("chat123", "@edward look at this", sender="+12015550101")
    db.add_attachment(rowid, filename="~/Library/Messages/Attachments/x/IMG_1.heic", mime_type="image/heic")
"""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union

APPLE_EPOCH_OFFSET = 978307200  # 2001-01-01T00:00:00Z as a Unix timestamp

STYLE_GROUP = 43
STYLE_DIRECT = 45

SCHEMA = """
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT UNIQUE NOT NULL,
    text TEXT,
    attributedBody BLOB,
    handle_id INTEGER DEFAULT 0,
    service TEXT DEFAULT 'iMessage',
    error INTEGER DEFAULT 0,
    date INTEGER DEFAULT 0,
    is_from_me INTEGER DEFAULT 0,
    associated_message_type INTEGER DEFAULT 0,
    associated_message_guid TEXT,
    thread_originator_guid TEXT,
    balloon_bundle_id TEXT,
    payload_data BLOB,
    is_audio_message INTEGER DEFAULT 0,
    cache_has_attachments INTEGER DEFAULT 0
);
CREATE TABLE handle (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    service TEXT DEFAULT 'iMessage'
);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT UNIQUE NOT NULL,
    style INTEGER,
    chat_identifier TEXT,
    service_name TEXT,
    display_name TEXT
);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER DEFAULT 0);
CREATE TABLE attachment (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT UNIQUE NOT NULL,
    filename TEXT,
    uti TEXT,
    mime_type TEXT,
    transfer_state INTEGER DEFAULT 5,
    is_outgoing INTEGER DEFAULT 0,
    transfer_name TEXT,
    total_bytes INTEGER DEFAULT 0
);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
"""

_TYPEDSTREAM_HEADER = (
    b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@"
    b"\x84\x84\x84\x12NSAttributedString\x00"
    b"\x84\x84\x08NSObject\x00"
    b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
)
_TYPEDSTREAM_TRAILER = b"\x86\x84\x02iI\x01\x01\x86"


def typedstream_length(n: int) -> bytes:
    """A typedstream length prefix as Messages writes it: one byte below
    0x80, else 0x81 + uint16 or 0x82 + uint32, both **little-endian**
    (1,859 of 1,859 real 0x81 blobs read that way, 2026-10-11)."""
    if n < 0x80:
        return bytes([n])
    if n < 0x10000:
        return b"\x81" + n.to_bytes(2, "little")
    return b"\x82" + n.to_bytes(4, "little")


def attributed_body(text: str) -> bytes:
    """An NSArchiver ``streamtyped`` attributedBody carrying ``text``."""
    utf = text.encode("utf-8")
    return _TYPEDSTREAM_HEADER + typedstream_length(len(utf)) + utf + _TYPEDSTREAM_TRAILER


def apple_ts(dt: datetime) -> int:
    """chat.db's ``message.date``: nanoseconds since 2001-01-01 UTC. A naive
    ``dt`` is taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1e9)


class FakeChatDB:
    """A minimal chat.db with helpers to add chats, messages and attachments."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        if self.path.exists():
            self.path.unlink()
        self._chats: dict[str, int] = {}
        self._handles: dict[str, int] = {}
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success and is always closed."""
        conn = sqlite3.connect(str(self.path))
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def point_listener(self, monkeypatch) -> "FakeChatDB":
        """Point ``listener_imessage.CHAT_DB_PATH`` (and its readers) here."""
        from services.heartbeat import listener_imessage

        monkeypatch.setattr(listener_imessage, "CHAT_DB_PATH", str(self.path))
        return self

    # ------------------------------------------------------------ rows ----

    def _handle(self, conn: sqlite3.Connection, handle: str, service: str = "iMessage") -> int:
        if handle not in self._handles:
            cur = conn.execute("INSERT INTO handle (id, service) VALUES (?, ?)", (handle, service))
            self._handles[handle] = int(cur.lastrowid)
        return self._handles[handle]

    def add_chat(self, chat_identifier: str, *, display_name: Optional[str] = None, is_group: bool = False,
                 guid: Optional[str] = None, service_name: str = "iMessage",
                 participants: Iterable[str] = ()) -> int:
        """A chat row (style 43 for a group, 45 for a 1:1) and its roster. A
        1:1 chat's ``chat_identifier`` is also its participant."""
        style = STYLE_GROUP if is_group else STYLE_DIRECT
        guid = guid or f"{service_name};{'+' if is_group else '-'};{chat_identifier}"
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO chat (guid, style, chat_identifier, service_name, display_name) VALUES (?, ?, ?, ?, ?)",
                (guid, style, chat_identifier, service_name, display_name))
            chat_rowid = int(cur.lastrowid)
            members = list(participants) or ([] if is_group else [chat_identifier])
            for member in members:
                conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
                             (chat_rowid, self._handle(conn, member, service_name)))
        self._chats[chat_identifier] = chat_rowid
        return chat_rowid

    def add_message(self, chat_identifier: str, text: Optional[str] = None, *, is_from_me: bool = False,
                    sender: Optional[str] = None, date: Optional[datetime] = None,
                    body_only: bool = False, attributed: Optional[bytes] = None, guid: Optional[str] = None,
                    thread_originator_guid: Optional[str] = None, error: int = 0, service: str = "iMessage",
                    associated_message_type: int = 0, rowid: Optional[int] = None,
                    balloon_bundle_id: Optional[str] = None, payload_data: Optional[bytes] = None,
                    is_audio_message: bool = False, associated_message_guid: Optional[str] = None) -> int:
        """One message in ``chat_identifier`` (created as a 1:1 chat when
        unknown). ``body_only`` stores ``text`` only in attributedBody, as
        Messages does for mentions, replies and rich text. Returns the ROWID."""
        if chat_identifier not in self._chats:
            self.add_chat(chat_identifier)
        when = apple_ts(date or datetime.now(timezone.utc))
        body = attributed if attributed is not None else (
            attributed_body(text) if (body_only and text is not None) else None)
        with self._connect() as conn:
            handle_id = 0 if (is_from_me or not sender) else self._handle(conn, sender, service)
            cur = conn.execute(
                "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, error, date, is_from_me, "
                "associated_message_type, thread_originator_guid, balloon_bundle_id, payload_data, is_audio_message, "
                "associated_message_guid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rowid, guid or str(uuid.uuid4()).upper(), None if body_only else text, body, handle_id, service,
                 int(error), when, 1 if is_from_me else 0, int(associated_message_type), thread_originator_guid,
                 balloon_bundle_id, payload_data, 1 if is_audio_message else 0, associated_message_guid))
            msg_rowid = int(cur.lastrowid)
            conn.execute("INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?, ?, ?)",
                         (self._chats[chat_identifier], msg_rowid, when))
        return msg_rowid

    def add_attachment(self, message_rowid: int, *, filename: str, mime_type: Optional[str] = None,
                       transfer_name: Optional[str] = None, uti: Optional[str] = None, transfer_state: int = 5,
                       total_bytes: int = 0, is_outgoing: bool = False) -> int:
        """An attachment on a message (transfer_state 5 = downloaded). Returns its ROWID."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO attachment (guid, filename, uti, mime_type, transfer_state, is_outgoing, transfer_name, "
                "total_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()).upper(), filename, uti, mime_type, int(transfer_state), 1 if is_outgoing else 0,
                 transfer_name or Path(filename).name, int(total_bytes)))
            att_rowid = int(cur.lastrowid)
            conn.execute("INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
                         (int(message_rowid), att_rowid))
            conn.execute("UPDATE message SET cache_has_attachments = 1 WHERE ROWID = ?", (int(message_rowid),))
        return att_rowid

    def message(self, rowid: int) -> Optional[dict]:
        """One message row as a dict (for assertions)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM message WHERE ROWID = ?", (int(rowid),)).fetchone()
        return dict(row) if row else None

    def max_rowid(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])


URL_BALLOON = "com.apple.messages.URLBalloonProvider"


def link_payload(*, url: str, title: Optional[str] = None, site: Optional[str] = None,
                 summary: Optional[str] = None, original_url: Optional[str] = None) -> bytes:
    """A URL balloon's ``payload_data``: an NSKeyedArchiver plist holding a
    ``richLinkMetadata`` (LPLinkMetadata) with NSURL values, as Messages
    writes it."""
    import plistlib

    UID = plistlib.UID
    objects: list = ["$null"]

    def add(obj) -> "plistlib.UID":
        objects.append(obj)
        return UID(len(objects) - 1)

    cls = add({"$classname": "LPLinkMetadata", "$classes": ["LPLinkMetadata", "NSObject"]})
    url_cls = add({"$classname": "NSURL", "$classes": ["NSURL", "NSObject"]})

    def nsurl(u: str):
        return add({"NS.base": UID(0), "NS.relative": add(u), "$class": url_cls})

    meta: dict = {"URL": nsurl(url), "$class": cls}
    if original_url:
        meta["originalURL"] = nsurl(original_url)
    if title:
        meta["title"] = add(title)
    if site:
        meta["siteName"] = add(site)
    if summary:
        meta["summary"] = add(summary)
    meta_uid = add(meta)
    root = add({"richLinkMetadata": meta_uid, "$class": add({"$classname": "RichLink", "$classes": ["RichLink"]})})
    plist = {"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": root}, "$objects": objects}
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)
