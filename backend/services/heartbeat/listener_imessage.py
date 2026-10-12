"""
iMessage listener for Edward's heartbeat system.

Polls ~/Library/Messages/chat.db for new messages every 10 seconds.
Writes HeartbeatEventModel rows to PostgreSQL, deduped by source_id.

Pings (2026-10-11; docs/architecture/pings.md): each new row
is checked for being Edward's own send coming back (structurally — the
chat.db ROWID the transport read back, an in-flight send, an ok ledger row,
then the signature) and classified (``services/pings/detect.py``). A ping
gets a ``pings`` row in the same transaction under a SAVEPOINT and its event
is stored ``triage_status='ping'``, so it never enters triage; the ping
runner answers it. A failed ping row costs only the ping row: the message
then goes the old way and the mind's mention wake answers it.

The owner's own messages are labelled with ``owner.owner_name()``
(``contact_name``, a ping's ``sender_label``); the pings runner compares
against the same call.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)

# Window within which an ok send-ledger row with the same normalised text
# makes a from-me row Edward's echo. iCloud sync usually echoes a send
# within seconds; 120s gives plenty of slack. Bound aware UTC (sent_at is
# timestamptz; a naive cutoff missed a row stamped now on this Mac).
SELF_SEND_DEDUP_WINDOW_SECONDS = 120

# raw_data.full_text keeps this much of a message (was 1,000).
FULL_TEXT_MAX = 4000

# A batch whose full store fails this many polls in a row is stored without
# ping registration or the describer, so one bad row cannot stop ingestion.
POISON_BATCH_FAILURES = 3

from services.database import (
    async_session,
    HeartbeatConfigModel,
    HeartbeatEventModel,
    ExternalContactModel,
    ImessageOutgoingSendModel,
)
from services import imessage_sent
from services.owner import owner_handles as env_owner_handles, owner_name
from services.heartbeat.imessage_decoder import decode_attributed_body
from services.media import hooks as media_hooks
from services.pings import detect as ping_detect
from services.pings import hooks as pings_hooks
from services.contact_identity_service import (
    append_identity_raw_data,
    resolve_contact_identity,
)
from services.push_event_flags import (
    is_informational_airline_rbm_push,
    mark_no_reply_expected,
)
from sqlalchemy import and_, or_, select, update

# Apple epoch offset: seconds between Unix epoch (1970) and Apple epoch (2001)
APPLE_EPOCH_OFFSET = 978307200

_listener_task: asyncio.Task | None = None
_last_seen_rowid: int = 0
_POLL_INTERVAL = 10  # seconds (fallback when config is unavailable)

# Debounce for inbound-message-triggered triage cycles: coalesce bursts so a
# rapid exchange doesn't fire a cycle per message.
EVENT_TRIGGERED_TRIAGE_MIN_INTERVAL_SECONDS = 60
_last_event_triggered_triage: float = 0.0


@dataclass
class StoreResult:
    """What a poll's store pass found — drives immediate-triage triggering.

    The flags count only rows that take the old path; a row recorded as an
    open ping is the ping runner's (``pings`` counts those)."""
    stored: int
    has_mentions: bool
    has_owner_direct: bool
    has_inbound_direct: bool
    pings: int = 0


def _is_owner_direct(msg: dict, owner_handles) -> bool:
    """True when a message is the owner talking in their own self-chat — the
    direct command channel to Edward (no literal @edward needed). Edward's own
    echoes are excluded by the caller (``_echo_reason``)."""
    return ping_detect.is_self_chat(msg, frozenset(str(h) for h in (owner_handles or ()) if h))


# Store-pass failures by the batch's starting watermark (consecutive; cleared
# on a good store). Keyed by where the batch starts, not its max ROWID, so
# new messages arriving behind a poison row cannot keep resetting the count.
_store_failures: dict[int, int] = {}

# ADAPT: reading chat.db (and the AddressBook stores) needs Full Disk Access.
#   How it works here: the backend runs under a python3.12 that holds Full Disk
#   Access, so it can open ~/Library/Messages/chat.db read-only every 10 s.
#   To adapt: grant Full Disk Access to the python that runs the backend
#   (System Settings > Privacy & Security); without it the listener logs and stops.
CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

# SQL to fetch new messages (include attributedBody for mentions/reactions where text is NULL).
# Tapbacks and other associated messages (associated_message_type != 0) are
# not ingested: a tapback on "@edward look" reads 'Loved “@edward look”'.
FETCH_MESSAGES_SQL = """
SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, m.date, m.service,
       {message_columns},
       h.id AS sender_id, c.chat_identifier, c.display_name, c.style, c.guid,
       {attachments_column}
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
LEFT JOIN chat c ON cmj.chat_id = c.ROWID
WHERE m.ROWID > ? AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL OR {has_attachments})
      AND m.associated_message_type = 0
ORDER BY m.ROWID ASC
"""

# One row per attachment, RS-separated; fields US-separated (2026-10-11: a
# path or name holding "|" can no longer shift the fields). uti,
# transfer_state and total_bytes are read when this chat.db has them.
_ATTACHMENTS_COLUMN = """(SELECT group_concat(a.ROWID || CHAR(31) || COALESCE(a.mime_type, '') || CHAR(31) || COALESCE(a.filename, '') || CHAR(31) || COALESCE(a.transfer_name, '') || CHAR(31) || {att_uti} || CHAR(31) || {att_state} || CHAR(31) || {att_bytes}, CHAR(30))
          FROM message_attachment_join maj JOIN attachment a ON a.ROWID = maj.attachment_id
         WHERE maj.message_id = m.ROWID) AS attachments"""
_HAS_ATTACHMENTS = "EXISTS (SELECT 1 FROM message_attachment_join maj2 WHERE maj2.message_id = m.ROWID)"


def _has_attachment_tables(conn) -> bool:
    try:
        n = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('attachment', 'message_attachment_join')"
        ).fetchone()[0]
        return int(n) == 2
    except sqlite3.Error:
        return False


def _with_attachment_sql(sql: str, conn) -> str:
    """Fill the attachment placeholders; a chat.db without the tables (tests,
    exotic macOS builds) gets a NULL column and no extra rows. Also fills
    ``{message_columns}`` with the optional message columns this chat.db has."""
    if "{message_columns}" in sql:
        sql = sql.replace("{message_columns}", _message_columns_sql(conn))
    if _has_attachment_tables(conn):
        cols = _table_columns(conn, "attachment")
        column = _ATTACHMENTS_COLUMN
        for placeholder, col in (("{att_uti}", "uti"), ("{att_state}", "transfer_state"), ("{att_bytes}", "total_bytes")):
            column = column.replace(placeholder, f"COALESCE(a.{col}, '')" if col in cols else "''")
        return sql.replace("{attachments_column}", column).replace("{has_attachments}", _HAS_ATTACHMENTS)
    return sql.replace("{attachments_column}", "NULL AS attachments").replace("{has_attachments}", "0")


# (chat.db path, table) -> its column names, read once by PRAGMA table_info.
_COLUMN_CACHE: dict[tuple[str, str], frozenset] = {}

# Message columns the ping work reads that an older or test chat.db may lack.
_OPTIONAL_MESSAGE_COLUMNS = (("guid", "message_guid"), ("thread_originator_guid", "thread_originator_guid"))


def _table_columns(conn, table: str) -> frozenset:
    key = (CHAT_DB_PATH, table)
    cols = _COLUMN_CACHE.get(key)
    if cols is None:
        try:
            cols = frozenset(str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
        except sqlite3.Error:
            return frozenset()
        _COLUMN_CACHE[key] = cols
    return cols


def _message_columns_sql(conn) -> str:
    cols = _table_columns(conn, "message")
    parts = [f"m.{c} AS {alias}" if c in cols else f"NULL AS {alias}" for c, alias in _OPTIONAL_MESSAGE_COLUMNS]
    parts.append("m.balloon_bundle_id AS balloon_bundle_id" if "balloon_bundle_id" in cols
                 else "NULL AS balloon_bundle_id")
    # payload_data only for a URL balloon: other apps' payloads can be large
    # and are never read.
    parts.append(f"CASE WHEN m.balloon_bundle_id LIKE '%{URL_BALLOON_MARK}%' THEN m.payload_data END AS payload_data"
                 if {"balloon_bundle_id", "payload_data"} <= cols else "NULL AS payload_data")
    return ", ".join(parts)


URL_BALLOON_MARK = "URLBalloonProvider"



_LEGACY_RUN = re.compile(r"[\x20-\x7e\u00a0-\uffff]+(?:[\r\n]+[\x20-\x7e\u00a0-\uffff]+)*")


def _extract_text_from_attributed_body_legacy(blob: bytes) -> Optional[str]:
    """Legacy regex-based decoder — kept only as a fallback if plist parsing
    fails on a malformed blob. New code calls decode_attributed_body first."""
    if not blob:
        return None
    try:
        decoded = blob.decode("utf-8", errors="replace")
        # The text content sits between the NSString marker and the next control sequence.
        # Find the longest readable substring that looks like actual message text.
        # Runs separated only by line breaks are one run (2026-10-11): a
        # newline is a control byte, so a multi-line message used to split
        # here and only its first line survived.
        parts = [p for p in re.findall(_LEGACY_RUN, decoded) if len(p) >= 3]
        # Filter out Cocoa class names, internal keys, and garbled fragments
        # Cocoa class names AND the "$"-prefixed NSKeyedArchiver keys. Omitting
        # the latter let "X$versionY$archiverT$topX$objects" through — and being
        # the longest printable run in the blob, it was then picked as "the
        # message" and shown to the owner as if they had typed it.
        skip = {"streamtyped", "NSString", "NSObject", "NSAttributedString",
                "NSMutableAttributedString", "NSMutableString", "NSDictionary",
                "NSNumber", "NSValue", "NSMutableData", "NSData", "NSArray",
                "__kIMMessagePartAttributeName",
                "$version", "$archiver", "$objects", "$top", "$null",
                "$class", "$classname", "$classes", "NSKeyedArchiver",
                "bplist00"}
        text_parts = []
        for p in parts:
            p = p.strip().strip("\ufffd")  # trim replacement chars from edges
            if not p or len(p) < 2:
                continue
            if any(s in p for s in skip):
                continue
            # Apple keeps inventing __kIM* attribute names, and enumerating them
            # one at a time is a losing game — __kIMCalendarEventAttributeName
            # reached the message history that way. Anything carrying that
            # prefix is framing by construction.
            if "__kIM" in p:
                continue
            # Foundation keyed fields (NS.string, NS.rangeval, NS.special, ...).
            # Anchored on "NS." so ordinary text with dots — people really do
            # send "Bounce.example.org" — is untouched.
            if "NS." in p:
                continue
            text_parts.append(p)
        if text_parts:
            # Take the FIRST surviving run, not the longest. In typedstream
            # layout the message sits right after the NSString marker, ahead of
            # the attribute names — and junk can easily be longer than the
            # message. A row that said "Grab milk on the way home" was
            # rendered as "NS.rangeval.locationZNS.special" purely because that
            # run was six characters longer.
            best = text_parts[0]
            # Strip leading +/length prefix byte artifacts (e.g. "+9" before actual text)
            best = re.sub(r"^[+\d]{1,3}", "", best).strip()
            return best if best else None
    except Exception:
        pass
    return None


def _apple_timestamp_to_datetime(apple_ts: int) -> datetime:
    """Convert Apple Core Data timestamp (nanoseconds since 2001-01-01) to UTC datetime."""
    if apple_ts is None:
        return datetime.now(timezone.utc)
    unix_ts = apple_ts / 1e9 + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


# ---- attachments -------------------------------------------------------------
#
# chat.db keeps attachments in `attachment` joined through
# `message_attachment_join`; the message text holds U+FFFC per attachment.
# We aggregate them into one column per row (ROWID|mime|filename|name, RS-
# separated) so every reader can carry an `attachments` list.

OBJECT_REPLACEMENT = "\ufffc"
_ATT_SEP = "\x1e"


def strip_object_replacement(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.replace(OBJECT_REPLACEMENT, " ").split())


_FIELD_SEP = "\x1f"


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_attachment_blob(blob: Optional[str]) -> list[dict]:
    """``{rowid, mime_type, path, name}`` per attachment, plus ``uti``,
    ``transfer_state`` and ``total_bytes`` from the US-separated form (None
    when this chat.db lacks the column). The older ``|`` form still parses."""
    out: list[dict] = []
    if not blob:
        return out
    for part in str(blob).split(_ATT_SEP):
        if _FIELD_SEP in part:
            fields = part.split(_FIELD_SEP)
            if len(fields) < 4 or not fields[0].strip().isdigit():
                continue
            rowid, mime, filename = int(fields[0]), fields[1] or None, fields[2] or ""
            name = fields[3] or Path(filename).name or f"attachment-{rowid}"
            extra = (fields + ["", "", ""])[4:7]
            out.append({"rowid": rowid, "mime_type": mime,
                        "path": str(Path(filename).expanduser()) if filename else "", "name": name,
                        "uti": extra[0] or None, "transfer_state": _int_or_none(extra[1]),
                        "total_bytes": _int_or_none(extra[2])})
            continue
        fields = part.split("|", 3)
        if len(fields) < 3 or not fields[0].strip().isdigit():
            continue
        rowid, mime, filename = int(fields[0]), fields[1] or None, fields[2] or ""
        name = (fields[3] if len(fields) > 3 else "") or Path(filename).name or f"attachment-{rowid}"
        path = str(Path(filename).expanduser()) if filename else ""
        out.append({"rowid": rowid, "mime_type": mime, "path": path, "name": name})
    return out


_KIND_BY_UTI = (
    ("com.apple.coreaudio-format", "audio"), ("public.audio", "audio"), ("public.mpeg-4-audio", "audio"),
    ("com.apple.m4a-audio", "audio"), ("org.3gpp.adaptive-multi-rate-audio", "audio"),
    ("com.apple.quicktime-movie", "video"), ("public.mpeg-4", "video"), ("public.movie", "video"),
    ("public.heic", "image"), ("public.heics", "image"), ("public.heif", "image"), ("public.jpeg", "image"),
    ("public.png", "image"), ("com.compuserve.gif", "image"), ("org.webmproject.webp", "image"),
    ("public.image", "image"), ("com.adobe.pdf", "pdf"),
)
_KIND_BY_EXTENSION = {
    ".caf": "audio", ".m4a": "audio", ".amr": "audio", ".mp3": "audio", ".aac": "audio", ".opus": "audio",
    ".mov": "video", ".mp4": "video", ".m4v": "video",
    ".heic": "image", ".heics": "image", ".heif": "image", ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".gif": "image", ".webp": "image",
    ".pdf": "pdf",
}


def describe_attachment_kind(mime_type: Optional[str], *, name: Optional[str] = None, uti: Optional[str] = None) -> str:
    """image | video | audio | pdf | file. The mime type decides; chat.db
    leaves it NULL for audio messages (CAF) and link previews, so the UTI and
    then the extension stand in (2026-10-11: audio messages read ``file``)."""
    m = (mime_type or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    if m == "application/pdf":
        return "pdf"
    if m:
        return "file"
    u = (uti or "").lower()
    for prefix, kind in _KIND_BY_UTI:
        if u == prefix:
            return kind
    return _KIND_BY_EXTENSION.get(Path(str(name or "")).suffix.lower(), "file")


def attachment_kind(att: dict) -> str:
    """The kind the listener settled on (``link`` for a URL balloon's
    preview), else what its mime type, UTI and name say."""
    return att.get("kind") or describe_attachment_kind(att.get("mime_type"), name=att.get("name"), uti=att.get("uti"))


def _clean_title(value: Optional[str], limit: int = 120) -> str:
    return " ".join(str(value or "").replace('"', "'").replace("]", ")").split())[:limit]


def _link_meta(balloon: Optional[str], payload: Optional[bytes]) -> Optional[dict]:
    """``{url, original_url, title, summary, site}`` from a URL balloon's
    ``payload_data`` (``services/media/linkmeta.py``): what Messages already
    fetched on the sender's phone. Nothing is fetched here. None when it is
    not a URL balloon, media is off, or the payload does not parse."""
    if not balloon or URL_BALLOON_MARK not in str(balloon) or not payload or not media_hooks.enabled():
        return None
    try:
        from services.media.linkmeta import parse_link_payload

        meta = parse_link_payload(bytes(payload))
    except Exception:  # noqa: BLE001 - an unreadable preview is just no preview
        return None
    if not meta:
        return None
    limits = {"url": 1000, "original_url": 1000, "title": 200, "summary": 300, "site": 80}
    return {k: (" ".join(str(meta.get(k)).split())[:n] if meta.get(k) else None) for k, n in limits.items()}


def _annotate_attachments(attachments: list[dict], balloon: Optional[str], link: Optional[dict]) -> None:
    """A URL balloon's ``.pluginPayloadAttachment`` is its preview picture:
    kind ``link``, with the page's title and site when the payload had them."""
    if not balloon or URL_BALLOON_MARK not in str(balloon):
        return
    for a in attachments:
        if str(a.get("name") or a.get("path") or "").endswith(".pluginPayloadAttachment"):
            a["kind"] = "link"
            if link:
                a["link_title"] = link.get("title")
                a["link_site"] = link.get("site")


def attachment_ref(att: dict) -> str:
    """How an iMessage attachment is addressed everywhere: ``attachment:<chat.db rowid>``
    (``look("attachment:449321")`` opens it)."""
    return f"attachment:{att.get('rowid')}"


def _placeholder_default(a: dict) -> str:
    kind = attachment_kind(a)
    if kind == "link":
        title, site = _clean_title(a.get("link_title")), _clean_title(a.get("link_site"), 60)
        if not title:
            return f"[link preview {attachment_ref(a)}]"
        site_part = f" ({site})" if site and site.casefold() != title.casefold() else ""
        return f'[link: "{title}"{site_part} {attachment_ref(a)}]'
    return f"[{kind}: {a.get('name')} {attachment_ref(a)}]"


def attachment_placeholder(attachments: list[dict]) -> str:
    """``[image: IMG_3533.heic attachment:449321]`` per attachment, with its
    description inside once the describer has one (``media.hooks.placeholder``).
    A URL balloon's preview reads ``[link: "<title>" (<site>) attachment:<rowid>]``."""
    return " ".join(media_hooks.placeholder(a, _placeholder_default(a)) for a in attachments)


def merge_text_and_attachments(text: Optional[str], attachments: list[dict]) -> str:
    """Caption (minus U+FFFC) followed by named placeholders."""
    clean = strip_object_replacement(text)
    if not attachments:
        return clean or (text or "")
    ph = attachment_placeholder(attachments)
    return f"{clean} {ph}".strip() if clean else ph


def _row_content(row) -> tuple[str, str, list[dict], Optional[dict], Optional[str]]:
    """One chat.db row as ``(text, caption, attachments, link, balloon)``:
    the text column or the decoded attributedBody, the attachments with
    their kinds, a URL balloon's preview metadata, and the merged text with
    placeholders. Shared by the listener and the thread readers."""
    text = row["text"]
    if not text and row["attributedBody"]:
        text = decode_attributed_body(row["attributedBody"])
        if not text:
            text = _extract_text_from_attributed_body_legacy(row["attributedBody"])
    keys = row.keys()
    balloon = row["balloon_bundle_id"] if "balloon_bundle_id" in keys else None
    link = _link_meta(balloon, row["payload_data"] if "payload_data" in keys else None)
    attachments = parse_attachment_blob(row["attachments"])
    _annotate_attachments(attachments, balloon, link)
    caption = strip_object_replacement(text)
    return merge_text_and_attachments(text, attachments), caption, attachments, link, balloon


_OPTIONAL_ATTACHMENT_COLUMNS = ("transfer_state", "total_bytes", "uti")


def find_attachment(rowid: int) -> Optional[dict]:
    """Look one attachment up by its chat.db ROWID. ``chat_identifier`` is the
    chat it was sent in (None when chat.db cannot say); ``transfer_state``,
    ``total_bytes`` and ``uti`` ride along when this chat.db has them."""
    if not Path(CHAT_DB_PATH).exists():
        return None
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        cols = _table_columns(conn, "attachment")
        extras = [c for c in _OPTIONAL_ATTACHMENT_COLUMNS if c in cols]
        extra_sql = "".join(f", a.{c}" for c in extras)
        chat_sql = (", (SELECT c.chat_identifier FROM message_attachment_join maj "
                    "JOIN chat_message_join cmj ON cmj.message_id = maj.message_id "
                    "JOIN chat c ON c.ROWID = cmj.chat_id WHERE maj.attachment_id = a.ROWID "
                    "ORDER BY maj.message_id DESC LIMIT 1)")
        try:
            row = conn.execute(
                f"SELECT a.ROWID, a.mime_type, a.filename, a.transfer_name{extra_sql}{chat_sql} "
                "FROM attachment a WHERE a.ROWID = ?", (int(rowid),)).fetchone()
        except sqlite3.Error:
            row = conn.execute(f"SELECT a.ROWID, a.mime_type, a.filename, a.transfer_name{extra_sql}, NULL "
                               "FROM attachment a WHERE a.ROWID = ?", (int(rowid),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row:
        return None
    filename = row[2] or ""
    out = {"rowid": row[0], "mime_type": row[1], "path": str(Path(filename).expanduser()) if filename else "",
           "name": row[3] or Path(filename).name or f"attachment-{row[0]}", "chat_identifier": row[-1]}
    for i, col in enumerate(extras):
        out[col] = row[4 + i]
    return out


# Describer tasks in flight (a bare create_task can be collected mid-run).
_describe_tasks: set = set()


def _schedule_attachment_description(event_id: Optional[str], attachments: list[dict]) -> None:
    """After the store commits, describe images in the background and fold
    the text into the event so triage and the ACT prompt can read it. Takes
    the event's explicit id (it used to get ``event.id`` before the
    flush, which was None, so no photo was ever described)."""
    if not event_id or not attachments:
        return
    try:
        task = asyncio.get_running_loop().create_task(_describe_event_attachments(event_id, attachments))
    except RuntimeError:
        return
    _describe_tasks.add(task)
    task.add_done_callback(_describe_tasks.discard)


async def _describe_event_attachments(event_id: str, attachments: list[dict]) -> None:
    """Describe each image (``attachment_vision.describe_image``), note each
    description for ``attachment:<rowid>`` (``media.hooks.note_description``:
    placeholders and saved history show it), then write
    ``raw_data.attachment_descriptions`` as before."""
    try:
        from services.attachment_vision import describe_image

        lines = []
        for a in attachments:
            kind = attachment_kind(a)
            tag = f"[{kind} {a.get('name') or 'attachment'} {attachment_ref(a)}]"
            if kind == "image" and a.get("path"):
                desc = None
                try:
                    desc = await describe_image(a["path"])
                except Exception as exc:  # noqa: BLE001
                    print(f"[attachments] describe failed for {attachment_ref(a)}: {pings_hooks.brief_error(exc)}")
                if desc and not str(desc).startswith("("):
                    media_hooks.note_description(attachment_ref(a), desc)
                    lines.append(f"{tag}: {desc}")
                    continue
            lines.append(tag)
        if not lines:
            return
        async with async_session() as session:
            event = await session.get(HeartbeatEventModel, event_id)
            if event is None:
                return
            try:
                data = json.loads(event.raw_data or "{}")
            except (ValueError, TypeError):
                data = {}
            data["attachment_descriptions"] = lines
            event.raw_data = json.dumps(data)
            described = "; ".join(lines)
            event.summary = f"{(event.summary or '')} — {described}"[:200]
            status = event.triage_status
            await session.commit()
        print(f"[attachments] described {len(lines)} attachment(s) on {event_id[:8]}")
        if status == "pending":
            from services.heartbeat import heartbeat_service
            heartbeat_service.request_triage("attachment described")
    except Exception as exc:  # noqa: BLE001
        print(f"[attachments] description task failed for {event_id[:8]}: {pings_hooks.brief_error(exc)}")


def _query_chat_db(last_rowid: int) -> list[dict]:
    """Query chat.db for new messages. Runs in thread (sqlite3 is sync)."""
    if not Path(CHAT_DB_PATH).exists():
        return []

    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(_with_attachment_sql(FETCH_MESSAGES_SQL, conn), (last_rowid,))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            # Prefer text column; fall back to extracting from attributedBody.
            # Use the bplist-aware decoder first; the legacy regex helper only
            # runs when the blob is malformed enough that plistlib rejects it.
            text, caption, attachments, link, balloon = _row_content(row)
            if not text:
                continue  # Skip messages with no extractable text

            results.append({
                "rowid": row["ROWID"],
                "text": text,
                "caption": caption,                         # the text without attachment placeholders
                "is_from_me": bool(row["is_from_me"]),
                "date": row["date"],
                "service": row["service"],
                "sender_id": row["sender_id"],
                "chat_identifier": row["chat_identifier"],
                "display_name": row["display_name"],
                "is_group": row["style"] == 43,
                "guid": row["guid"],                        # the chat's guid
                "message_guid": row["message_guid"],
                "thread_originator_guid": row["thread_originator_guid"],
                "associated_message_type": 0,               # the query takes only 0
                "balloon_bundle_id": balloon,
                "link": link,                               # a URL balloon's preview metadata, or None
                "attachments": attachments,
            })
        return results
    finally:
        conn.close()


async def _load_persisted_rowid() -> int | None:
    """Load the persisted iMessage high-water mark from the database.

    Returns None if no value has been persisted yet (cold start).
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(HeartbeatConfigModel.imessage_last_rowid).where(
                    HeartbeatConfigModel.id == "default"
                )
            )
            return result.scalar_one_or_none()
    except Exception as e:
        print(f"[Heartbeat] iMessage listener: failed to load persisted rowid: {e}")
        return None


async def _persist_rowid(rowid: int) -> None:
    """Persist the current high-water mark to the database.

    Mirrors the email listener pattern. Called on every successful poll and
    immediately after cold-start seeding so restarts don't re-race iCloud sync.
    """
    try:
        async with async_session() as session:
            await session.execute(
                update(HeartbeatConfigModel)
                .where(HeartbeatConfigModel.id == "default")
                .values(imessage_last_rowid=rowid)
            )
            await session.commit()
    except Exception as e:
        print(f"[Heartbeat] iMessage listener: failed to persist rowid: {e}")


def _get_max_rowid() -> int:
    """Get the current max ROWID from chat.db."""
    if not Path(CHAT_DB_PATH).exists():
        return 0
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        cursor = conn.execute("SELECT MAX(ROWID) FROM message")
        row = cursor.fetchone()
        return row[0] or 0
    finally:
        conn.close()


# In-memory cache: handle (phone or Apple ID email) -> contact name (or None
# if unknown), resolved once per session
_contact_name_cache: dict[str, Optional[str]] = {}


async def _resolve_contact_name(phone_number: str) -> Optional[str]:
    """Resolve a sender handle — a phone or an Apple ID email — to a contact
    name: external contacts, then the owner's address book (``lookup_handle``),
    cached by handle."""
    if not phone_number:
        return None

    if phone_number in _contact_name_cache:
        return _contact_name_cache[phone_number]

    # Tier 1: Check ExternalContactModel (fast, DB-only)
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ExternalContactModel.contact_name).where(
                    ExternalContactModel.phone_number == phone_number
                )
            )
            name = result.scalar_one_or_none()
            if name:
                _contact_name_cache[phone_number] = name
                return name
    except Exception:
        pass

    # Tier 2: the owner's address book — a phone or an email (runs in a thread)
    try:
        from services.contacts_service import lookup_handle

        result = await asyncio.to_thread(lookup_handle, phone_number)
        if result.get("success") and result.get("matches"):
            name = result["matches"][0]["name"]
            _contact_name_cache[phone_number] = name
            return name
    except Exception as e:
        print(f"[Heartbeat] Contact lookup failed for {phone_number}: {e}")

    _contact_name_cache[phone_number] = None
    return None


def _has_edward_signature(text) -> bool:
    """True when a message ends with Edward's signature (every send path signs)."""
    from utils.message_signature import _SIGNATURE_PATTERNS

    stripped = str(text or "").rstrip()
    return any(re.search(p, stripped, re.IGNORECASE) for p in _SIGNATURE_PATTERNS)


def is_by_edward(rowid, is_from_me, text) -> bool:
    """Did Edward write this chat.db row? The transport read its ROWID back,
    or it is from-me and signed. Readers label such lines "Edward"."""
    return imessage_sent.is_edward_rowid(rowid) or (bool(is_from_me) and _has_edward_signature(text))


async def _ledger_echo(session, chat: Optional[str], text: str) -> bool:
    """An ok send-ledger row with this normalised text, for this chat, sent in
    the last ``SELF_SEND_DEDUP_WINDOW_SECONDS`` (bound aware UTC). A row
    written before the read-back columns (no ``text_norm_hash``) matches on
    the old 200-character hash. Runs under a SAVEPOINT; False on any error."""
    norm = imessage_sent.text_hash(text)
    legacy = hashlib.sha256(text[:200].encode("utf-8")).hexdigest()
    cutoff = imessage_sent.aware_utcnow() - timedelta(seconds=SELF_SEND_DEDUP_WINDOW_SECONDS)
    model = ImessageOutgoingSendModel
    try:
        async with session.begin_nested():
            chats = (await session.execute(
                select(model.chat_identifier).where(
                    or_(model.text_norm_hash == norm,
                        and_(model.text_norm_hash.is_(None), model.text_hash == legacy)),
                    model.ok.is_(True),
                    model.sent_at >= cutoff,
                ).limit(20)
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001 - the signature check still stands behind it
        print(f"[pings] echo ledger check failed: {pings_hooks.brief_error(exc)}")
        return False
    key = pings_hooks.chat_key(chat)
    return any(c is None or not key or pings_hooks.chat_key(c) == key for c in chats)


async def _echo_reason(session, msg: dict) -> Optional[str]:
    """Why a from-me row is Edward's own send coming back, or None (the
    owner wrote it). First match wins (2026-10-11): the ROWID the transport
    read back, an in-flight send to this chat with this text (claimed here,
    so the transport finds its row), an ok ledger row, the signature."""
    if not msg.get("is_from_me"):
        return None
    rowid = msg["rowid"]
    chat = msg.get("chat_identifier")
    # What Edward sent is the caption: the placeholders the listener appends
    # for attachments would hide the signature and change the hash.
    text = msg.get("caption") or msg.get("text") or ""
    if imessage_sent.is_edward_rowid(rowid):
        return "rowid"
    if imessage_sent.claim_inflight(chat, text, rowid) or (
            msg.get("guid") and imessage_sent.claim_inflight(msg.get("guid"), text, rowid)):
        return "inflight"
    # A file-only row has no caption: its in-flight send (``send_attachment``)
    # began with U+FFFC, what chat.db holds as such a message's text.
    if not msg.get("caption") and msg.get("attachments") and imessage_sent.claim_inflight(
            chat, OBJECT_REPLACEMENT, rowid):
        return "inflight"
    if text and await _ledger_echo(session, chat, text):
        return "hash"
    if _has_edward_signature(text):
        return "signature"
    return None


def _classify(msg: dict, *, is_echo: bool, owner_handles: frozenset, edward_guids: frozenset):
    """``detect.classify`` with the self-chat breaker's input: an Edward send
    into this chat in the last 60 s whose row the transport never found."""
    unconfirmed = (not is_echo and ping_detect.is_self_chat(msg, owner_handles)
                   and imessage_sent.unconfirmed_recently(msg.get("chat_identifier")))
    verdict = ping_detect.classify(msg, is_echo=is_echo, owner_handles=owner_handles,
                                   edward_guids=edward_guids, unconfirmed_send=unconfirmed)
    if unconfirmed and verdict is None:
        print(f"[pings] breaker: self-chat row {msg.get('rowid')} came within "
              f"{imessage_sent.UNCONFIRMED_WINDOW_S}s of an Edward send whose row was never found; not a ping")
    return verdict


def merge_owner_handles(db_handles) -> list[str]:
    """The owner's handles: heartbeat_config's ``imessage_owner_handles``
    (the database; no UI writes it) followed by ``EDWARD_OWNER_HANDLES``
    (``owner.owner_handles()``), each as ``str``, empties dropped, the first
    spelling of a duplicate kept. Pure but for the environment."""
    out: list[str] = []
    seen: set[str] = set()
    for h in list(db_handles or ()) + list(env_owner_handles()):
        value = str(h) if h is not None else ""
        if not value.strip() or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


async def _load_listener_config() -> tuple[list[str], bool]:
    """(owner_handles, event_triggered_triage_enabled) from heartbeat_config,
    the owner handles merged with ``EDWARD_OWNER_HANDLES``. Resilient: any
    failure returns safe defaults (the env handles still count) so the poll
    loop never dies on a config read."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default")
            )
            config = result.scalar_one_or_none()
    except Exception:
        return merge_owner_handles(()), True

    if config is None:
        return merge_owner_handles(()), True

    owner_handles: list[str] = []
    raw = getattr(config, "imessage_owner_handles", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                owner_handles = [str(h) for h in parsed]
        except (ValueError, TypeError):
            owner_handles = []

    enabled = getattr(config, "imessage_event_triggered_triage", True)
    return merge_owner_handles(owner_handles), (True if enabled is None else bool(enabled))


async def _get_poll_interval() -> float:
    """Poll interval from heartbeat_config.imessage_poll_seconds (clamped to
    a 2s floor), falling back to the hardcoded default on any failure."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(HeartbeatConfigModel.imessage_poll_seconds).where(
                    HeartbeatConfigModel.id == "default"
                )
            )
            value = result.scalar_one_or_none()
    except Exception:
        return float(_POLL_INTERVAL)

    if not value:
        return float(_POLL_INTERVAL)
    return float(max(2, int(value)))




async def _store_events(messages: list[dict], owner_handles=None, *, degraded: bool = False) -> StoreResult:
    """Store new message events in PostgreSQL, and record each ping.

    Per row: dedup by ``source_id``; the echo check (from-me rows); contact
    and identity; the event with an explicit id, flushed; then, for a ping,
    ``pings.hooks.create_ping`` under a SAVEPOINT. A ping recorded ``open``
    stores its event ``triage_status='ping'`` (never triage, escalations or
    Today); a ``legacy`` one (EDWARD_PINGS=off) or a failed one stays
    ``pending`` and takes the old path. After the commit: ``kick`` the
    runner and schedule the describer with the real event ids.

    ``degraded`` (a poison batch, see ``_poll_once``): no ping rows, no
    media items and no describer — every message is stored and a mention
    goes to the mind's mention wake.

    Media items go through ``media.hooks.register_imessage`` (its own
    SAVEPOINT) and ``media.hooks.enqueue`` after the commit; both are inert
    until P1b's store lands.
    """
    stored = 0
    pings = 0
    has_mentions = False
    has_owner_direct = False
    has_inbound_direct = False
    owners = frozenset(str(h) for h in (owner_handles or ()) if h)
    edward_guids = imessage_sent.edward_guids()
    runner_on = pings_hooks.enabled()
    to_describe: list[tuple[str, list]] = []
    media_ids: list[int] = []
    async with async_session() as session:
        for msg in messages:
            source_id = f"imessage:{msg['rowid']}"

            # Dedup check
            existing = await session.execute(
                select(HeartbeatEventModel.id).where(
                    HeartbeatEventModel.source_id == source_id
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Skip iCloud-synced historical messages (Apple timestamp > 24h old)
            msg_dt = _apple_timestamp_to_datetime(msg["date"])
            age_hours = (datetime.now(timezone.utc) - msg_dt).total_seconds() / 3600
            if age_hours > 24:
                continue

            text = msg["text"] or ""
            from_me = bool(msg["is_from_me"])

            # Edward sends from the owner's Apple ID, so his own sends come
            # back as is_from_me rows, the same shape as the owner typing on
            # their phone. Recognised structurally (2026-10-11; see
            # services/imessage_sent.py).
            echo = await _echo_reason(session, msg) if from_me else None

            if degraded:
                verdict = None
                is_mention = echo is None and ping_detect.has_mention(text)
                is_owner_direct = echo is None and ping_detect.is_self_chat(msg, owners)
            else:
                verdict = _classify(msg, is_echo=echo is not None, owner_handles=owners, edward_guids=edward_guids)
                is_mention = verdict is not None and verdict.kind == "mention"
                is_owner_direct = verdict is not None and verdict.kind == "owner_direct"

            # Resolve contact name
            contact_name = None
            if from_me:
                contact_name = owner_name()
            elif msg["sender_id"]:
                contact_name = await _resolve_contact_name(msg["sender_id"])

            data = {
                "rowid": msg["rowid"],
                "service": msg["service"],
                "date": msg["date"],
                "full_text": text[:FULL_TEXT_MAX] if text else None,
                "is_group": msg.get("is_group", False),
                "guid": msg.get("guid"),
                "message_guid": msg.get("message_guid"),
                "thread_originator_guid": msg.get("thread_originator_guid"),
                "owner_direct": is_owner_direct,
                "attachments": msg.get("attachments") or [],
            }
            if msg.get("link"):
                data["link"] = msg["link"]
            if echo:
                data["edward_echo"] = echo
            raw_data = json.dumps(data)
            if is_informational_airline_rbm_push(
                sender=msg["sender_id"],
                label=contact_name or msg["display_name"],
                chat_id=msg["chat_identifier"],
                text=text,
                raw_data=raw_data,
            ):
                raw_data = mark_no_reply_expected(
                    raw_data,
                    reason="informational_airline_rbm_push",
                )

            identity = await resolve_contact_identity(
                sender=msg["sender_id"],
                contact_name=contact_name,
                chat_identifier=msg["chat_identifier"],
                chat_name=msg["display_name"],
                source="imessage",
                raw_data=raw_data,
            )
            raw_data = append_identity_raw_data(raw_data, identity)

            # An explicit id: the describer and the ping row need it
            # before the commit, and the column default only fills at flush.
            event_id = str(uuid.uuid4())
            event = HeartbeatEventModel(
                id=event_id,
                source="imessage",
                event_type="message_sent" if from_me else "message_received",
                sender=msg["sender_id"],
                contact_name=contact_name,
                person_key=identity.person_key,
                display_label=identity.display_label,
                identity_provenance=identity.provenance,
                chat_identifier=msg["chat_identifier"],
                chat_name=msg["display_name"],
                summary=text[:200],
                raw_data=raw_data,
                source_id=source_id,
                is_from_user=from_me,
                triage_status="dismissed" if echo else "pending",
            )
            session.add(event)
            # A failure of the event itself surfaces here, as it always did,
            # outside the ping hook.
            await session.flush()
            stored += 1

            # Media items (P1b's store; [] until it lands, or when media is
            # off): under their own SAVEPOINT, so a failure costs only them.
            if msg.get("attachments") and not degraded and not echo:
                media_ids.extend(await media_hooks.register_imessage(session, event_id=event_id, msg=msg))

            ping_id = None
            status = "open" if runner_on else "legacy"
            if verdict is not None:
                try:
                    ping_id = await pings_hooks.create_ping(
                        session, source_ref=source_id, channel="imessage", kind=verdict.kind,
                        from_owner=verdict.from_owner, third_party=not verdict.from_owner,
                        sender_known=verdict.from_owner or contact_name is not None,
                        asked_at=msg_dt.replace(tzinfo=None), status=status, heartbeat_event_id=event_id,
                        message_rowid=msg["rowid"], message_guid=msg.get("message_guid"),
                        chat_identifier=msg["chat_identifier"], chat_guid=msg.get("guid"),
                        chat_name=msg.get("display_name"), is_group=bool(msg.get("is_group")),
                        sender=msg["sender_id"],
                        sender_label=owner_name() if verdict.from_owner else (
                            contact_name or identity.display_label or msg["sender_id"]),
                        text=text,
                    )
                except Exception as exc:  # noqa: BLE001 - create_ping never raises; belt and braces
                    print(f"[pings] could not record the ping for {source_id}: {pings_hooks.brief_error(exc)}")
                    ping_id = None

            if ping_id is not None and status == "open":
                try:
                    payload = json.loads(event.raw_data or "{}")
                except (TypeError, ValueError):
                    payload = {}
                payload["ping"] = {"kind": verdict.kind, "ping_id": ping_id}
                event.raw_data = json.dumps(payload)
                event.triage_status = "ping"
                pings += 1
            elif echo is None:
                # The old path: what it asked a triage cycle for.
                if is_mention:
                    has_mentions = True
                if is_owner_direct:
                    has_owner_direct = True
                if not from_me and not msg.get("is_group"):
                    has_inbound_direct = True

            if msg.get("attachments") and not degraded and not echo:
                to_describe.append((event_id, msg["attachments"]))

        if stored:
            await session.commit()

    # After the commit: the runner reads committed rows, and the describer
    # finds its event by id.
    if pings:
        pings_hooks.kick()
    if media_ids:
        media_hooks.enqueue(media_ids)
    for event_id, attachments in to_describe:
        _schedule_attachment_description(event_id, attachments)
    return StoreResult(
        stored=stored,
        has_mentions=has_mentions,
        has_owner_direct=has_owner_direct,
        has_inbound_direct=has_inbound_direct,
        pings=pings,
    )


def should_request_triage(result: "StoreResult", *, now: float, last: float, enabled: bool) -> Optional[str]:
    """Pure: which reason (if any) justifies asking for a triage cycle now."""
    if result.has_mentions:
        return "@edward mention"
    if result.has_owner_direct:
        return "owner direct ping"
    if result.has_inbound_direct and enabled and now - last >= EVENT_TRIGGERED_TRIAGE_MIN_INTERVAL_SECONDS:
        return "inbound 1:1 message"
    return None


async def _poll_once() -> None:
    """Single poll iteration."""
    global _last_seen_rowid, _last_event_triggered_triage

    start = _last_seen_rowid
    messages = await asyncio.to_thread(_query_chat_db, start)
    if not messages:
        return

    max_rowid = max(m["rowid"] for m in messages)

    owner_handles, event_triggered_enabled = await _load_listener_config()
    try:
        result = await _store_events(messages, owner_handles=owner_handles)
    except Exception as exc:
        # Poison-batch escape (2026-10-11): one row that breaks the full
        # store must not stop iMessage ingestion. On the third failure in a
        # row for this batch, store it without ping registration or the
        # describer; a mention then goes to the mind's mention wake. If that
        # fails too, the next poll retries, as before.
        failures = _store_failures.get(start, 0) + 1
        _store_failures.clear()
        _store_failures[start] = failures
        if failures < POISON_BATCH_FAILURES:
            raise
        print(f"[pings] poison batch up to rowid {max_rowid}: stored without ping registration "
              f"after {failures} failures ({pings_hooks.brief_error(exc)})")
        result = await _store_events(messages, owner_handles=owner_handles, degraded=True)
    _store_failures.clear()

    # Advance the watermark only AFTER a successful store. Advancing before
    # meant a transient store failure silently lost the whole batch for the
    # life of the process (rows were above the mark on the next poll).
    # Re-processing after a failure is safe: _store_events dedups by source_id.
    _last_seen_rowid = max_rowid

    if result.stored:
        print(f"[Heartbeat] iMessage listener: stored {result.stored} new events (last rowid: {_last_seen_rowid})")

    # Persist the updated high-water mark so a restart picks up from here
    # instead of re-racing iCloud sync via _get_max_rowid().
    await _persist_rowid(max_rowid)

    # Ask for a triage cycle without waiting for it. Mentions and the owner's
    # own self-chat always request one; ordinary inbound 1:1s are coalesced to
    # one request per EVENT_TRIGGERED_TRIAGE_MIN_INTERVAL_SECONDS. The
    # runner in heartbeat_service does the actual cycle, so the poll loop
    # never stalls behind Layer 2/3.
    now = time.monotonic()
    reason = should_request_triage(result, now=now, last=_last_event_triggered_triage, enabled=event_triggered_enabled)
    if reason:
        if reason == "inbound 1:1 message":
            _last_event_triggered_triage = now
        print(f"[Heartbeat] {reason} detected — requesting triage cycle")
        from services.heartbeat import heartbeat_service
        heartbeat_service.request_triage(reason)


async def _listener_loop() -> None:
    """Main polling loop.

    Startup sequence (a persistent high-water mark):
      1. Load persisted rowid from HeartbeatConfigModel.imessage_last_rowid.
      2. If present and <= current chat.db max → use persisted value (warm start).
      3. If persisted value exceeds current chat.db max (chat.db was wiped or
         restored from an older backup) → re-seed from current max and log.
      4. If no persisted value → cold start. Seed from chat.db max and persist
         immediately so a fast restart doesn't re-race iCloud sync.
    """
    global _last_seen_rowid

    try:
        persisted = await _load_persisted_rowid()
        current_max = await asyncio.to_thread(_get_max_rowid)
    except Exception as e:
        print(f"[Heartbeat] iMessage listener FAILED to start: {e}")
        print(f"[Heartbeat] This usually means Full Disk Access is not granted for the Python process.")
        return

    if persisted is None:
        # Cold start — seed from current max and persist immediately.
        _last_seen_rowid = current_max
        await _persist_rowid(current_max)
        print(f"[Heartbeat] iMessage listener started (cold seed at rowid {_last_seen_rowid})")
    elif persisted > current_max:
        # chat.db was wiped or restored from an older backup — re-seed.
        print(
            f"[Heartbeat] iMessage listener: persisted rowid {persisted} exceeds "
            f"current max {current_max}; re-seeding (chat.db likely wiped)"
        )
        _last_seen_rowid = current_max
        await _persist_rowid(current_max)
    else:
        _last_seen_rowid = persisted
        print(f"[Heartbeat] iMessage listener started (restored rowid {_last_seen_rowid} from database)")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] iMessage poll error: {e}")
        # Re-read each iteration so config edits apply without a restart.
        await asyncio.sleep(await _get_poll_interval())


async def start_imessage_listener() -> None:
    """Start the iMessage listener."""
    global _listener_task
    if _listener_task is not None:
        return

    if not Path(CHAT_DB_PATH).exists():
        print(f"[Heartbeat] iMessage listener skipped: {CHAT_DB_PATH} not found")
        return

    # The send transport records its read-back through the main loop, and
    # the echo index is rebuilt from the send ledger's ok rows (30 days).
    try:
        from services import imessage_service

        note_main_loop = getattr(imessage_service, "note_main_loop", None)
        if note_main_loop is not None:
            note_main_loop(asyncio.get_running_loop())
    except Exception as exc:  # noqa: BLE001
        print(f"[pings] note_main_loop failed: {pings_hooks.brief_error(exc)}")
    try:
        loaded = await imessage_sent.load(days=30)
        print(f"[pings] echo index: {loaded} of Edward's sends loaded")
    except Exception as exc:  # noqa: BLE001
        print(f"[pings] echo index load failed: {pings_hooks.brief_error(exc)}")

    _listener_task = asyncio.create_task(_listener_loop())


async def stop_imessage_listener() -> None:
    """Stop the iMessage listener."""
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    print("[Heartbeat] iMessage listener stopped")


# Defaults used when no HeartbeatConfig row exists yet, or when the
# migration that adds the thread-limit columns hasn't run. Group chats need
# a wider window because 4-5 participants interleave: 15 messages in a busy
# group chat is often only a couple of minutes of actual discourse.
_DEFAULT_THREAD_LIMIT_DIRECT = 30
_DEFAULT_THREAD_LIMIT_GROUP = 75


def _thread_limit_for(is_group: bool, config) -> int:
    """Pick the chat-thread fetch limit based on chat shape and live config.

    ``config`` is a ``HeartbeatConfigModel`` instance (or anything with the
    same two attributes); ``None`` means no config row yet — use defaults.
    Missing attributes also fall through to defaults so a freshly-deployed
    backend running against an un-migrated DB row stays safe.
    """
    default = _DEFAULT_THREAD_LIMIT_GROUP if is_group else _DEFAULT_THREAD_LIMIT_DIRECT
    if config is None:
        return default
    attr = "imessage_thread_limit_group" if is_group else "imessage_thread_limit_direct"
    value = getattr(config, attr, None)
    return value if value else default


def get_chat_thread(
    chat_identifier: str,
    limit: int = 15,
    before_rowid: Optional[int] = None,
) -> list[dict]:
    """Read recent messages from a specific chat thread in chat.db.

    ``before_rowid`` (optional) pages further back: only rows with ROWID
    strictly less than the given value are returned. Used by the
    ``read_imessage_thread`` LLM tool to scroll older history on demand.
    """
    if not chat_identifier or not Path(CHAT_DB_PATH).exists():
        return []

    sql = """
    SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, m.date,
           {message_columns},
           h.id AS sender_id,
       {attachments_column}
    FROM message m
    LEFT JOIN handle h ON m.handle_id = h.ROWID
    LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
    LEFT JOIN chat c ON cmj.chat_id = c.ROWID
    WHERE c.chat_identifier = ?
          AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL OR {has_attachments})
          AND m.associated_message_type = 0
          {before_clause}
    ORDER BY m.ROWID DESC
    LIMIT ?
    """
    # ROWID is a positive autoincrement, so a non-positive bound can never be a
    # genuine "page back from here" request — it only ever arrives when the model
    # fills in an optional arg it cannot omit. Treat it as no bound rather than
    # emitting "ROWID < 0", which silently matches nothing.
    if before_rowid is not None and before_rowid <= 0:
        before_rowid = None

    params: list = [chat_identifier]
    if before_rowid is not None:
        sql = sql.replace("{before_clause}", "AND m.ROWID < ?")
        params.append(before_rowid)
    else:
        sql = sql.replace("{before_clause}", "")
    params.append(limit)

    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(_with_attachment_sql(sql, conn), tuple(params))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            text, caption, attachments, _link, _balloon = _row_content(row)
            if not text:
                continue

            dt = _apple_timestamp_to_datetime(row["date"])
            results.append({
                "rowid": row["ROWID"],
                "sender": row["sender_id"] or "me",
                "text": text,
                "is_from_me": bool(row["is_from_me"]),
                "by_edward": is_by_edward(row["ROWID"], row["is_from_me"], caption or text),
                "timestamp": dt,
                "attachments": attachments,
            })
        # Reverse to chronological order (query was DESC)
        results.reverse()
        return results
    finally:
        conn.close()


def get_outbound_since(chat_identifier: str, since: datetime) -> bool:
    """Did the user send any iMessage in this chat after ``since``?

    Used by the notification dispatcher as a final pre-send stale check:
    between the dispatcher LLM deciding "push" and the push actually
    firing, the owner may have already replied themselves. This is a tight,
    indexed read against chat.db that should run in well under 50ms.

    Apple's ``date`` column is nanoseconds since the Mac OS X epoch
    (2001-01-01 UTC); we convert ``since`` into that same scale and use
    ``>`` on it.
    """
    if not chat_identifier or not Path(CHAT_DB_PATH).exists():
        return False

    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    apple_since_ns = int((since.timestamp() - APPLE_EPOCH_OFFSET) * 1e9)

    sql = """
    SELECT 1
    FROM message m
    JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
    JOIN chat c ON cmj.chat_id = c.ROWID
    WHERE c.chat_identifier = ?
          AND m.is_from_me = 1
          AND m.date > ?
    LIMIT 1
    """
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        cursor = conn.execute(sql, (chat_identifier, apple_since_ns))
        return cursor.fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _handle_clauses(handles) -> tuple[list[str], list]:
    """SQL for "the sender or the chat is one of these handles": a phone on
    its last 10 digits (a suffix match), an email without case."""
    clauses: list[str] = []
    params: list = []
    for raw in handles or ():
        handle = str(raw or "").strip()
        if not handle:
            continue
        if "@" in handle:
            clauses.append("(lower(h.id) = ? OR lower(c.chat_identifier) = ?)")
            params.extend([handle.lower(), handle.lower()])
            continue
        digits = re.sub(r"\D", "", handle)
        if len(digits) >= 7:
            tail = f"%{digits[-10:]}"
            clauses.append("(h.id LIKE ? OR c.chat_identifier LIKE ?)")
            params.extend([tail, tail])
    return clauses, params


def get_recent_messages_across_chats(
    hours: float = 24,
    contact: Optional[str] = None,
    limit: int = 40,
    handles: Optional[list] = None,
) -> list[dict]:
    """Read recent messages across every chat, newest window first.

    This is what the ``get_recent_messages`` LLM tool should always have used.
    The old AppleScript implementation enumerated chat names and ids and never
    returned a single message body, so Edward could not answer "what did X say?".

    ``contact`` matches loosely against the chat identifier, the group display
    name, and the sender handle, so both "+15555550171" and "Weekend" work.
    ``handles`` (a person's phones and emails, from the owner's contacts) also
    match the sender or the chat: a phone on its last 10 digits, an email
    without case. Results come back in chronological order, like
    ``get_chat_thread``.
    """
    if not Path(CHAT_DB_PATH).exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=float(hours))
    apple_cutoff = int((cutoff.timestamp() - APPLE_EPOCH_OFFSET) * 1e9)

    sql = """
    SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, m.date,
           {message_columns},
           h.id AS sender_id, c.chat_identifier, c.display_name, c.style,
       {attachments_column}
    FROM message m
    LEFT JOIN handle h ON m.handle_id = h.ROWID
    LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
    LEFT JOIN chat c ON cmj.chat_id = c.ROWID
    WHERE m.date > ?
          AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL OR {has_attachments})
          AND m.associated_message_type = 0
          {contact_clause}
    ORDER BY m.date DESC
    LIMIT ?
    """
    params: list = [apple_cutoff]
    clauses, clause_params = _handle_clauses(handles)
    if contact and contact.strip():
        needle = f"%{contact.strip()}%"
        clauses.insert(0, "(c.chat_identifier LIKE ? OR c.display_name LIKE ? OR h.id LIKE ?)")
        clause_params[:0] = [needle, needle, needle]
    if clauses:
        sql = sql.replace("{contact_clause}", "AND (" + " OR ".join(clauses) + ")")
        params.extend(clause_params)
    else:
        sql = sql.replace("{contact_clause}", "")
    params.append(limit)

    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(_with_attachment_sql(sql, conn), tuple(params)).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        text, caption, attachments, _link, _balloon = _row_content(row)
        if not text:
            continue
        results.append({
            "rowid": row["ROWID"],
            "sender": row["sender_id"] or "me",
            "text": text,
            "is_from_me": bool(row["is_from_me"]),
            "by_edward": is_by_edward(row["ROWID"], row["is_from_me"], caption or text),
            "timestamp": _apple_timestamp_to_datetime(row["date"]),
            "chat_identifier": row["chat_identifier"],
            "chat_name": row["display_name"],
            "is_group": row["style"] == 43,
            "attachments": attachments,
        })
    results.reverse()  # query was DESC; hand back chronological
    return results


def get_chat_participants(chat_identifier: str) -> list[dict]:
    """Read the full handle roster of a chat — including silent members.

    Joins through ``chat_handle_join``, which is the source of truth for
    chat membership. Use this to enumerate participants for poll-style
    workflows where ``get_chat_thread`` would miss anyone who hasn't
    spoken recently.
    """
    if not chat_identifier or not Path(CHAT_DB_PATH).exists():
        return []

    sql = """
    SELECT DISTINCT h.id AS handle
    FROM chat c
    JOIN chat_handle_join chj ON c.ROWID = chj.chat_id
    JOIN handle h ON chj.handle_id = h.ROWID
    WHERE c.chat_identifier = ?
    ORDER BY h.id
    """
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(sql, (chat_identifier,))
        return [{"handle": row["handle"]} for row in cursor.fetchall()]
    finally:
        conn.close()


def format_chat_thread(messages: list[dict], contact_cache: dict[str, Optional[str]] = None) -> str:
    """Format a chat thread into a readable string for trigger context."""
    if not messages:
        return ""

    from services.timezone_utils import utc_to_local

    now = datetime.now(timezone.utc)
    today_local = utc_to_local(now).date()
    yesterday_local = today_local - timedelta(days=1)
    lines = ["Recent messages in this chat:"]

    for msg in messages:
        dt = msg["timestamp"]
        local_dt = utc_to_local(dt)
        local_date = local_dt.date()
        clock = local_dt.strftime("%-I:%M %p")

        # Relative part
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            relative = "just now"
        elif seconds < 3600:
            mins = seconds // 60
            relative = f"{mins} min ago"
        elif seconds < 86400:
            hours = seconds // 3600
            relative = f"{hours}h ago"
        else:
            days = seconds // 86400
            relative = f"{days}d ago"

        # Hybrid absolute + relative
        if local_date == today_local:
            time_str = f"{clock} ({relative})"
        elif local_date == yesterday_local:
            time_str = f"yesterday {clock} ({relative})"
        else:
            time_str = f"{local_dt.strftime('%b %-d')} {clock} ({relative})"

        # Sender display name
        if msg["is_from_me"]:
            sender = f"You ({owner_name()})"
        elif contact_cache and msg["sender"] in contact_cache and contact_cache[msg["sender"]]:
            sender = contact_cache[msg["sender"]]
        else:
            sender = msg["sender"]

        lines.append(f"[{time_str}] {sender}: {msg['text']}")

    return "\n".join(lines)


def get_listener_status() -> str:
    """Get the current listener status."""
    if _listener_task is None:
        return "stopped"
    if _listener_task.done():
        return "error"
    return "running"
