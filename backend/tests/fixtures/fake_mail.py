"""A throwaway Mail.app store and a fake Gmail API for tests (2026-10-11).

Nothing here touches Ellis's real mail. ``FakeMailStore`` writes an Envelope
Index (the tables and columns the listener and ``messages(mail_*)`` read —
schema checked read-only against Mail V10 on 2026-10-11) plus ``.emlx``
files laid out the way Mail lays them out
(``V10/<account>/<mailbox>.mbox/<store>/Data/<digits>/Messages/<rowid>.emlx``)
under the test's ``tmp_path``; ``point`` re-points every module that reads
the index. ``FakeGmail`` stands in for ``googleapiclient``'s service object:
``users().messages().list/get/send``, ``users().threads().get``,
``users().history().list`` and ``users().getProfile``, recording each call.

    store = FakeMailStore(tmp_path).point(monkeypatch)
    rowid = store.add("parker@example.com", "Parker Smith", "Dinner Friday?", body="Are we on?")
    gmail = FakeGmail("owner@gmail.com").install(monkeypatch)
    gmail.add("m1", thread="t1", sender="Shop <deals@shop.example>", subject="Sale", body="50% off")
"""

from __future__ import annotations

import base64
import email.utils
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ACCOUNT = "11111111-2222-3333-4444-555555555555"
STORE = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"

SCHEMA = """
CREATE TABLE messages (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    sender INTEGER,
    subject INTEGER,
    date_received INTEGER,
    mailbox INTEGER,
    read INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    unsubscribe_type INTEGER DEFAULT 0,
    automated_conversation INTEGER DEFAULT 0
);
CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT);
CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, comment TEXT);
CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT);
CREATE TABLE message_global_data (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    model_category INTEGER,
    model_subcategory INTEGER
);
"""


def _epoch(when: Union[datetime, int, float, None]) -> int:
    if when is None:
        return int(datetime.now(timezone.utc).timestamp()) - 60
    if isinstance(when, datetime):
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return int(when.timestamp())
    return int(when)


def hours_ago(h: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=h)


class FakeMailStore:
    def __init__(self, tmp_path: Path):
        self.root = Path(tmp_path) / "Mail" / "V10"
        self.index = self.root / "MailData" / "Envelope Index"
        self.index.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
        self._mailboxes: Dict[str, int] = {}
        self._next_message_id = 1000

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.index))

    # ------------------------------------------------------------ pointing --

    def point(self, monkeypatch) -> "FakeMailStore":
        from services import email_management_service
        from services.heartbeat import listener_email

        monkeypatch.setattr(email_management_service, "MAIL_DB_PATH", str(self.index))
        monkeypatch.setattr(listener_email, "MAIL_DB_PATH", str(self.index))
        try:
            from services import mail_service

            mail_service.reset_for_tests()
        except Exception:  # noqa: BLE001 — not merged yet in a red run
            pass
        return self

    # ------------------------------------------------------------- writing --

    def mailbox(self, url: str) -> int:
        if url not in self._mailboxes:
            with self._conn() as c:
                cur = c.execute("INSERT INTO mailboxes (url) VALUES (?)", (url,))
                self._mailboxes[url] = int(cur.lastrowid)
        return self._mailboxes[url]

    def _row(self, table: str, **values: Any) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self._conn() as c:
            cur = c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
            return int(cur.lastrowid)

    def add(self, sender: str, name: Optional[str], subject: str, *, received=None,
            mailbox: str = f"imap://{ACCOUNT}/INBOX", read: int = 0, deleted: int = 0,
            body: Optional[str] = "Hello.", html: Optional[str] = None, to: str = "owner@icloud.example",
            reply_to: Optional[str] = None, message_id: Optional[str] = None,
            attachments: Optional[List[tuple]] = None, partial: bool = False, write_file: bool = True,
            category: int = 0) -> int:
        """One message: an index row and (unless ``write_file`` is False)
        its ``.emlx``. Returns the ROWID."""
        box = self.mailbox(mailbox)
        sender_row = self._row("addresses", address=sender, comment=name or "")
        subject_row = self._row("subjects", subject=subject)
        self._next_message_id += 1
        mid = self._next_message_id
        rowid = self._row("messages", message_id=mid, sender=sender_row, subject=subject_row,
                          date_received=_epoch(received), mailbox=box, read=read, deleted=deleted)
        self._row("message_global_data", message_id=mid, model_category=category, model_subcategory=None)
        if write_file:
            msg = EmailMessage()
            msg["From"] = email.utils.formataddr((name or "", sender)) if name else sender
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = email.utils.format_datetime(datetime.fromtimestamp(_epoch(received), tz=timezone.utc))
            msg["Message-ID"] = message_id or f"<icloud-{rowid}@example.com>"
            if reply_to:
                msg["Reply-To"] = reply_to
            if body is not None:
                msg.set_content(body)
            if html is not None:
                if body is not None:
                    msg.add_alternative(html, subtype="html")
                else:
                    msg.set_content(html, subtype="html")
            for att_name, data in attachments or []:
                msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=att_name)
            self.write_emlx(rowid, mailbox, msg.as_bytes(), partial=partial)
        return rowid

    def emlx_dir(self, rowid: int, mailbox: str) -> Path:
        from urllib.parse import unquote, urlparse

        u = urlparse(mailbox)
        parts = [unquote(p) for p in (u.path or "").split("/") if p]
        box_dir = self.root / (u.netloc or ACCOUNT)
        for p in parts:
            box_dir = box_dir / f"{p}.mbox"
        digits = list(str(rowid // 1000))[::-1] if rowid >= 1000 else []
        return box_dir.joinpath(STORE, "Data", *digits, "Messages")

    def write_emlx(self, rowid: int, mailbox: str, raw: bytes, *, partial: bool = False) -> Path:
        d = self.emlx_dir(rowid, mailbox)
        d.mkdir(parents=True, exist_ok=True)
        path = d / (f"{rowid}.partial.emlx" if partial else f"{rowid}.emlx")
        plist = b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>flags</key>' \
                b"<integer>0</integer></dict></plist>\n"
        path.write_bytes(f"{len(raw)}\n".encode() + raw + plist)
        return path

    def set_received(self, rowid: int, when) -> None:
        with self._conn() as c:
            c.execute("UPDATE messages SET date_received = ? WHERE ROWID = ?", (_epoch(when), rowid))


# ------------------------------------------------------------------ Gmail --

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeHttpError(Exception):
    """Shaped like googleapiclient's HttpError for the code that reads
    ``resp.status`` / ``reason``."""

    def __init__(self, status: int, reason: str):
        super().__init__(f"<HttpError {status} \"{reason}\">")
        self.resp = type("Resp", (), {"status": status, "reason": reason})()
        self.status_code = status
        self.reason = reason


class FakeGmail:
    def __init__(self, address: str = "owner@gmail.example"):
        self.address = address
        self.store: Dict[str, dict] = {}
        self.calls: List[tuple] = []
        self.sent: List[dict] = []
        self.history_records: List[dict] = []
        self.history_error: Optional[Exception] = None
        self.send_error: Optional[Exception] = None
        self.send_labels: Optional[List[str]] = ["SENT"]
        self.labels_after_get: List[str] = ["SENT"]
        self.confirm_error: Optional[Exception] = None  # the get that confirms a send's labels fails
        self.history_id = "5000"

    # ------------------------------------------------------------- seeding --

    def add(self, mid: str, *, thread: Optional[str] = None, sender: str = "Parker <parker@example.com>",
            subject: str = "Hello", body: Optional[str] = "Hi Ellis.", html: Optional[str] = None,
            to: str = "owner@gmail.example", internal_date: Optional[datetime] = None,
            labels=("INBOX", "UNREAD"), message_id_header: Optional[str] = None,
            reply_to: Optional[str] = None, attachments: Optional[List[tuple]] = None,
            references: Optional[str] = None) -> dict:
        when = internal_date or hours_ago(1)
        headers = [
            {"name": "From", "value": sender},
            {"name": "To", "value": to},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": email.utils.format_datetime(when)},
            {"name": "Message-ID", "value": message_id_header or f"<{mid}@mail.example>"},
        ]
        if reply_to:
            headers.append({"name": "Reply-To", "value": reply_to})
        if references:
            headers.append({"name": "References", "value": references})
        parts = []
        if body is not None:
            parts.append({"mimeType": "text/plain", "body": {"data": _b64(body), "size": len(body)}})
        if html is not None:
            parts.append({"mimeType": "text/html", "body": {"data": _b64(html), "size": len(html)}})
        for name, size in attachments or []:
            parts.append({"mimeType": "application/pdf", "filename": name,
                          "body": {"attachmentId": f"att-{name}", "size": size}})
        msg = {
            "id": mid,
            "threadId": thread or mid,
            "labelIds": list(labels),
            "snippet": (body or html or "")[:80],
            "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts},
        }
        self.store[mid] = msg
        return msg

    # ------------------------------------------------------------- the API --

    def users(self):
        return self

    def getProfile(self, userId):  # noqa: N802 — the API's own name
        self.calls.append(("getProfile", userId))
        return _Call(lambda: {"emailAddress": self.address, "historyId": self.history_id})

    def messages(self):  # noqa: F811 — the API's own shape: users().messages()
        return _Messages(self)

    def threads(self):
        return _Threads(self)

    def history(self):  # noqa: F811
        return _History(self)

    def install(self, monkeypatch, *, connected: bool = True) -> "FakeGmail":
        from services import gmail_service

        async def service():
            return self if connected else None

        async def configured():
            return connected

        async def address():
            return self.address if connected else None

        monkeypatch.setattr(gmail_service, "_get_service", service)
        monkeypatch.setattr(gmail_service, "is_configured", configured)
        monkeypatch.setattr(gmail_service, "get_email_address", address)
        return self


class _Messages:
    def __init__(self, g: FakeGmail):
        self.g = g

    def list(self, userId, q=None, maxResults=20, labelIds=None, **_kw):  # noqa: N803
        self.g.calls.append(("list", q, maxResults))
        msgs = sorted(self.g.store.values(), key=lambda m: int(m["internalDate"]), reverse=True)
        return _Call(lambda: {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in msgs[:maxResults]]})

    def get(self, userId, id, format="full", metadataHeaders=None, **_kw):  # noqa: A002, N803
        self.g.calls.append(("get", id, format))

        def run():
            if id in self.g.store:
                return self.g.store[id]
            if id in {s.get("id") for s in self.g.sent}:
                if self.g.confirm_error is not None:
                    raise self.g.confirm_error
                return {"id": id, "labelIds": list(self.g.labels_after_get)}
            raise FakeHttpError(404, "Not Found")
        return _Call(run)

    def send(self, userId, body=None, media_body=None, **_kw):  # noqa: N803
        def run():
            if self.g.send_error is not None:
                raise self.g.send_error
            raw = media_body.getbytes(0, media_body.size()) if media_body is not None else b""
            sent = {"id": f"sent-{len(self.g.sent) + 1}", "threadId": (body or {}).get("threadId") or "new-thread",
                    "body": dict(body or {}), "raw": raw,
                    "mimetype": getattr(media_body, "mimetype", lambda: None)()}
            self.g.sent.append(sent)
            out = {"id": sent["id"], "threadId": sent["threadId"]}
            if self.g.send_labels is not None:
                out["labelIds"] = list(self.g.send_labels)
            return out
        self.g.calls.append(("send", dict(body or {})))
        return _Call(run)


class _Threads:
    def __init__(self, g: FakeGmail):
        self.g = g

    def get(self, userId, id, format="full", **_kw):  # noqa: A002
        self.g.calls.append(("thread", id, format))

        def run():
            msgs = [m for m in self.g.store.values() if m["threadId"] == id]
            if not msgs:
                raise FakeHttpError(404, "Not Found")
            return {"id": id, "messages": sorted(msgs, key=lambda m: int(m["internalDate"]))}
        return _Call(run)


class _History:
    def __init__(self, g: FakeGmail):
        self.g = g

    def list(self, userId, startHistoryId, historyTypes=None, labelId=None, pageToken=None, **_kw):  # noqa: N803
        self.g.calls.append(("history", startHistoryId))

        def run():
            if self.g.history_error is not None:
                raise self.g.history_error
            return {"history": list(self.g.history_records), "historyId": self.g.history_id}
        return _Call(run)
