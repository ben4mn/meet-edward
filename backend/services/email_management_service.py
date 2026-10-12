"""
Email management service for Edward.

Provides inbox search, email actions (delete, move to junk) with audit trails,
and sender preference learning. Reads from Apple Mail's SQLite database,
writes via AppleScript, and logs everything to PostgreSQL.
"""

import asyncio
import os
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import logging

from sqlalchemy import select, update, func, case

from services.database import (
    async_session,
    EmailActionModel,
    EmailSenderPrefModel,
)

logger = logging.getLogger(__name__)

# Mail.app Envelope Index path (EDWARD_MAIL_ENVELOPE_INDEX overrides it; see
# the ADAPT note in services/heartbeat/listener_email.py)
MAIL_DB_PATH = os.path.expanduser(
    os.getenv("EDWARD_MAIL_ENVELOPE_INDEX") or "~/Library/Mail/V10/MailData/Envelope Index"
)


def connect_index(path: Optional[str] = None) -> sqlite3.Connection:
    """Mail's Envelope Index, read-only: ``mode=ro`` sees the WAL;
    ``immutable=1`` only when ro can't open (it misses the WAL). Raises
    FileNotFoundError when there is no index."""
    p = path or MAIL_DB_PATH
    if not os.path.exists(p):
        raise FileNotFoundError("Mail's Envelope Index isn't there")
    try:
        con = sqlite3.connect(f"file:{quote(p)}?mode=ro", uri=True, timeout=1.0, check_same_thread=False)
        con.execute("SELECT 1 FROM messages LIMIT 1").fetchall()
        return con
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{quote(p)}?immutable=1", uri=True, timeout=1.0, check_same_thread=False)


def _extract_domain(email: str) -> str:
    """Extract domain from an email address."""
    if not email:
        return ""
    match = re.search(r"@([\w.-]+)", email)
    return match.group(1).lower() if match else ""


def _get_message_id_by_rowid(rowid: int) -> Optional[str]:
    """Look up the Mail.app message_id for a given ROWID. Sync, runs in thread."""
    if not os.path.exists(MAIL_DB_PATH):
        logger.warning("Mail DB not found at %s", MAIL_DB_PATH)
        return None
    conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        cursor = conn.execute(
            "SELECT message_id FROM messages WHERE ROWID = ?", (rowid,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    except sqlite3.Error as e:
        logger.error("SQLite error looking up message_id for ROWID %s: %s", rowid, e)
        return None
    finally:
        conn.close()


def _get_inbox_rowids() -> list[int]:
    """Get INBOX mailbox ROWIDs."""
    if not os.path.exists(MAIL_DB_PATH):
        logger.warning("Mail DB not found at %s", MAIL_DB_PATH)
        return []
    conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        cursor = conn.execute(
            "SELECT ROWID FROM mailboxes WHERE url LIKE '%/INBOX'"
        )
        rowids = [row[0] for row in cursor.fetchall()]
        if not rowids:
            logger.warning("No INBOX mailboxes found in Mail DB")
        return rowids
    except sqlite3.Error as e:
        logger.error("SQLite error reading inbox mailboxes: %s", e)
        return []
    finally:
        conn.close()


def _query_inbox_messages(
    sender_filter: Optional[str] = None,
    subject_filter: Optional[str] = None,
    limit: int = 20,
    unread_only: bool = True,
    category: Optional[int] = None,
) -> list[dict]:
    """
    Query inbox messages from Mail's SQLite database. Sync — runs in thread.

    Args:
        sender_filter: Partial match on sender address
        subject_filter: Partial match on subject
        limit: Max results (default 20)
        unread_only: Only unread messages (default True)
        category: Apple ML model_category filter (0=primary, 1=transactions, 2=updates, 3=marketing)
    """
    if not os.path.exists(MAIL_DB_PATH):
        logger.warning("Mail DB not found at %s", MAIL_DB_PATH)
        return []

    conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        inbox_rowids = _get_inbox_rowids()

        conditions = []
        params: list = []

        if inbox_rowids:
            placeholders = ",".join("?" for _ in inbox_rowids)
            conditions.append(f"m.mailbox IN ({placeholders})")
            params.extend(inbox_rowids)

        if unread_only:
            conditions.append("m.read = 0")

        if sender_filter:
            conditions.append("a.address LIKE ?")
            params.append(f"%{sender_filter}%")

        if subject_filter:
            conditions.append("s.subject LIKE ?")
            params.append(f"%{subject_filter}%")

        if category is not None:
            conditions.append("mgd.model_category = ?")
            params.append(category)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        query = f"""
            SELECT m.ROWID, s.subject, a.address as sender,
                   m.date_received, m.read,
                   m.unsubscribe_type, m.automated_conversation,
                   mgd.model_category, mgd.model_subcategory
            FROM messages m
            LEFT JOIN subjects s ON m.subject = s.ROWID
            LEFT JOIN addresses a ON m.sender = a.ROWID
            LEFT JOIN message_global_data mgd ON m.message_id = mgd.message_id
            {where}
            ORDER BY m.date_received DESC
            LIMIT ?
            """

        # Diagnostic: check total message count to verify WAL visibility
        total_cur = conn.execute("SELECT MAX(ROWID) FROM messages")
        max_rowid = total_cur.fetchone()[0]
        print(f"[search_inbox] max_rowid={max_rowid}, inbox_rowids={inbox_rowids}, "
              f"sender={sender_filter}, subject={subject_filter}, unread_only={unread_only}, "
              f"category={category}, params={params}")

        cursor = conn.execute(query, params)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            # Convert date_received to ISO string
            if d.get("date_received"):
                try:
                    dt = datetime.fromtimestamp(d["date_received"], tz=timezone.utc)
                    d["date_received_iso"] = dt.isoformat()
                except (OSError, ValueError):
                    d["date_received_iso"] = ""
            results.append(d)
        print(f"[search_inbox] returned {len(results)} results")
        if not results:
            logger.info(
                "search_inbox returned 0 results (filters: sender=%s, subject=%s, unread_only=%s, category=%s)",
                sender_filter, subject_filter, unread_only, category,
            )
        return results
    except sqlite3.Error as e:
        print(f"[search_inbox] SQLite error: {e}")
        logger.error("SQLite error querying inbox: %s", e)
        return []
    finally:
        conn.close()


async def search_inbox(
    sender: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 20,
    unread_only: bool = True,
    category: Optional[int] = None,
) -> list[dict]:
    """Search inbox messages. Async wrapper around sync SQLite query."""
    return await asyncio.to_thread(
        _query_inbox_messages, sender, subject, limit, unread_only, category
    )


def _delete_email_applescript(message_id: str) -> dict:
    """Delete an email by message_id via AppleScript. Sync — runs in thread."""
    script = f'''
    tell application "Mail"
        set targetMessages to (every message of every mailbox whose message id is "{message_id}")
        repeat with msgList in targetMessages
            repeat with msg in msgList
                delete msg
            end repeat
        end repeat
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "AppleScript timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _move_to_junk_applescript(message_id: str) -> dict:
    """Move an email to Junk by message_id via AppleScript. Sync — runs in thread."""
    script = f'''
    tell application "Mail"
        set targetMessages to (every message of every mailbox whose message id is "{message_id}")
        set junkBox to junk mailbox
        repeat with msgList in targetMessages
            repeat with msg in msgList
                move msg to junkBox
            end repeat
        end repeat
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "AppleScript timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def delete_email(email_rowid: int, conversation_id: Optional[str] = None) -> dict:
    """Delete an email with audit trail."""
    # Look up message details from SQLite
    msg_id = await asyncio.to_thread(_get_message_id_by_rowid, email_rowid)
    if not msg_id:
        return {"success": False, "error": f"Message ROWID {email_rowid} not found in Mail database"}

    # Get sender info for audit
    sender_info = await asyncio.to_thread(_get_sender_info_by_rowid, email_rowid)
    sender = sender_info.get("sender", "")
    subject = sender_info.get("subject", "")
    domain = _extract_domain(sender)

    # Write pending audit row
    action_id = str(uuid.uuid4())
    async with async_session() as session:
        action = EmailActionModel(
            id=action_id,
            email_rowid=email_rowid,
            sender=sender,
            sender_domain=domain,
            subject=subject[:500] if subject else None,
            action="delete",
            action_source="llm",
            status="pending",
            conversation_id=conversation_id,
        )
        session.add(action)
        await session.commit()

    # Execute AppleScript in thread
    result = await asyncio.to_thread(_delete_email_applescript, msg_id)

    # Update audit status
    async with async_session() as session:
        new_status = "completed" if result["success"] else "failed"
        await session.execute(
            update(EmailActionModel)
            .where(EmailActionModel.id == action_id)
            .values(status=new_status, error=result.get("error"))
        )
        await session.commit()

    return result


async def move_to_junk(email_rowid: int, conversation_id: Optional[str] = None) -> dict:
    """Move an email to Junk with audit trail."""
    msg_id = await asyncio.to_thread(_get_message_id_by_rowid, email_rowid)
    if not msg_id:
        return {"success": False, "error": f"Message ROWID {email_rowid} not found in Mail database"}

    sender_info = await asyncio.to_thread(_get_sender_info_by_rowid, email_rowid)
    sender = sender_info.get("sender", "")
    subject = sender_info.get("subject", "")
    domain = _extract_domain(sender)

    action_id = str(uuid.uuid4())
    async with async_session() as session:
        action = EmailActionModel(
            id=action_id,
            email_rowid=email_rowid,
            sender=sender,
            sender_domain=domain,
            subject=subject[:500] if subject else None,
            action="move_to_junk",
            action_source="llm",
            status="pending",
            conversation_id=conversation_id,
        )
        session.add(action)
        await session.commit()

    result = await asyncio.to_thread(_move_to_junk_applescript, msg_id)

    async with async_session() as session:
        new_status = "completed" if result["success"] else "failed"
        await session.execute(
            update(EmailActionModel)
            .where(EmailActionModel.id == action_id)
            .values(status=new_status, error=result.get("error"))
        )
        await session.commit()

    return result


def _get_sender_info_by_rowid(rowid: int) -> dict:
    """Look up sender and subject for a ROWID. Sync — runs in thread."""
    if not os.path.exists(MAIL_DB_PATH):
        logger.warning("Mail DB not found at %s", MAIL_DB_PATH)
        return {}
    conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            """
            SELECT a.address as sender, s.subject
            FROM messages m
            LEFT JOIN addresses a ON m.sender = a.ROWID
            LEFT JOIN subjects s ON m.subject = s.ROWID
            WHERE m.ROWID = ?
            """,
            (rowid,),
        )
        row = cursor.fetchone()
        return dict(row) if row else {}
    except sqlite3.Error as e:
        logger.error("SQLite error looking up sender info for ROWID %s: %s", rowid, e)
        return {}
    finally:
        conn.close()


async def get_actions(
    sender_domain: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Query email action audit log."""
    async with async_session() as session:
        query = select(EmailActionModel).order_by(EmailActionModel.created_at.desc())
        if sender_domain:
            query = query.where(EmailActionModel.sender_domain == sender_domain)
        if action:
            query = query.where(EmailActionModel.action == action)
        query = query.limit(limit)
        result = await session.execute(query)
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "email_rowid": r.email_rowid,
                "sender": r.sender,
                "sender_domain": r.sender_domain,
                "subject": r.subject,
                "action": r.action,
                "action_source": r.action_source,
                "status": r.status,
                "error": r.error,
                "conversation_id": r.conversation_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def get_sender_stats(sender_domain: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Aggregate email action stats by sender domain."""
    async with async_session() as session:
        query = (
            select(
                EmailActionModel.sender_domain,
                func.count().label("total_actions"),
                func.sum(
                    case((EmailActionModel.action == "delete", 1), else_=0)
                ).label("deletes"),
                func.sum(
                    case((EmailActionModel.action == "move_to_junk", 1), else_=0)
                ).label("junked"),
                func.sum(
                    case((EmailActionModel.action == "unsubscribe", 1), else_=0)
                ).label("unsubscribes"),
                func.max(EmailActionModel.created_at).label("last_action"),
            )
            .group_by(EmailActionModel.sender_domain)
            .order_by(func.count().desc())
            .limit(limit)
        )
        if sender_domain:
            query = query.where(EmailActionModel.sender_domain == sender_domain)

        result = await session.execute(query)
        return [
            {
                "sender_domain": row.sender_domain,
                "total_actions": row.total_actions,
                "deletes": row.deletes or 0,
                "junked": row.junked or 0,
                "unsubscribes": row.unsubscribes or 0,
                "last_action": row.last_action.isoformat() if row.last_action else None,
            }
            for row in result.all()
        ]


async def set_sender_preference(
    sender_domain: str,
    preference: str,
    source: str = "user",
    sender_address: Optional[str] = None,
) -> dict:
    """Set or update handling preference for a sender domain."""
    if preference not in ("keep", "delete", "junk", "unsubscribe"):
        return {"success": False, "error": f"Invalid preference: {preference}"}

    async with async_session() as session:
        existing = await session.execute(
            select(EmailSenderPrefModel).where(
                EmailSenderPrefModel.sender_domain == sender_domain
            )
        )
        pref = existing.scalar_one_or_none()

        if pref:
            pref.preference = preference
            pref.source = source
            if sender_address:
                pref.sender_address = sender_address
        else:
            pref = EmailSenderPrefModel(
                sender_domain=sender_domain,
                sender_address=sender_address,
                preference=preference,
                source=source,
            )
            session.add(pref)

        await session.commit()
        return {
            "success": True,
            "sender_domain": sender_domain,
            "preference": preference,
            "source": source,
        }


async def check_sender_preference(sender_address: str) -> Optional[str]:
    """Check if there's a learned preference for a sender's domain. Returns preference or None."""
    domain = _extract_domain(sender_address)
    if not domain:
        return None

    async with async_session() as session:
        result = await session.execute(
            select(EmailSenderPrefModel.preference).where(
                EmailSenderPrefModel.sender_domain == domain
            )
        )
        row = result.scalar_one_or_none()
        return row


async def get_all_preferences(limit: int = 100) -> list[dict]:
    """Get all sender preferences."""
    async with async_session() as session:
        result = await session.execute(
            select(EmailSenderPrefModel)
            .order_by(EmailSenderPrefModel.updated_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "sender_domain": r.sender_domain,
                "sender_address": r.sender_address,
                "preference": r.preference,
                "source": r.source,
                "action_count": r.action_count,
                "last_applied_at": r.last_applied_at.isoformat() if r.last_applied_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


# ---- Edward's reads (messages(mail_search | mail_read), 2026-10-11) ----------
#
# Read-only (``connect_index``), every mailbox unless one is named, read and
# unread alike unless asked, deleted mail never. A query word matches the
# sender's address or name, or the subject; every word must match somewhere.

def mailbox_name(url: Optional[str]) -> str:
    """A mailbox URL's last path component, URL-decoded ("Sent Messages")."""
    from urllib.parse import unquote, urlparse

    path = urlparse(url or "").path or ""
    parts = [unquote(p) for p in path.split("/") if p]
    return parts[-1] if parts else (url or "")


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _search_mail_sync(terms: str, mailbox: Optional[str], unread_only: bool, limit: int) -> list[dict]:
    con = connect_index()
    con.row_factory = sqlite3.Row
    try:
        conditions = ["m.deleted = 0"]
        params: list = []
        for word in (terms or "").split():
            conditions.append("(a.address LIKE ? ESCAPE '\\' OR a.comment LIKE ? ESCAPE '\\' "
                              "OR s.subject LIKE ? ESCAPE '\\')")
            params.extend([_like(word)] * 3)
        if mailbox:
            want = mailbox.strip().lower()
            boxes = con.execute("SELECT ROWID, url FROM mailboxes").fetchall()
            if want == "inbox":
                matched = [r[0] for r in boxes if (r[1] or "").upper().endswith("/INBOX")]
            else:
                matched = [r[0] for r in boxes if mailbox_name(r[1]).lower() == want]
            if not matched:
                names = sorted({mailbox_name(r[1]) for r in boxes if r[1]})
                raise LookupError(f"no mailbox named {mailbox!r} in Mail — there are: {', '.join(names[:30])}")
            conditions.append(f"m.mailbox IN ({','.join('?' for _ in matched)})")
            params.extend(matched)
        if unread_only:
            conditions.append("m.read = 0")
        params.append(int(limit))
        rows = con.execute(
            f"""
            SELECT m.ROWID AS rowid, s.subject, a.address AS sender, a.comment AS sender_name,
                   m.date_received, m.read, mb.url AS mailbox_url
            FROM messages m
            LEFT JOIN subjects s ON m.subject = s.ROWID
            LEFT JOIN addresses a ON m.sender = a.ROWID
            LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
            WHERE {" AND ".join(conditions)}
            ORDER BY m.date_received DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


async def search_mail(terms: str = "", mailbox: Optional[str] = None, unread_only: bool = False,
                      limit: int = 20) -> list[dict]:
    """Mail.app's mail matching ``terms`` (sender address or name, or
    subject), newest first. Raises FileNotFoundError / sqlite3.Error when
    the index can't be read and LookupError for an unknown mailbox."""
    return await asyncio.to_thread(_search_mail_sync, terms, mailbox, unread_only, limit)


def _envelope(rowid: int) -> Optional[dict]:
    con = connect_index()
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT m.ROWID AS rowid, s.subject, a.address AS sender, a.comment AS sender_name,
                   m.date_received, m.read, m.deleted, mb.url AS mailbox_url
            FROM messages m
            LEFT JOIN subjects s ON m.subject = s.ROWID
            LEFT JOIN addresses a ON m.sender = a.ROWID
            LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
            WHERE m.ROWID = ?
            """,
            (rowid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _mail_root() -> str:
    """``~/Library/Mail/V10``: the directory above ``MailData``."""
    return os.path.dirname(os.path.dirname(MAIL_DB_PATH))


def find_emlx(rowid: int, mailbox_url: Optional[str] = None) -> Optional[str]:
    """The message's ``.emlx`` (or ``.partial.emlx``), or None. Mail keeps
    ``V10/<account>/<mailbox>.mbox/<store>/Data/<digits>/Messages/<rowid>.emlx``
    with the digits of ``rowid // 1000`` reversed (checked read-only on this
    Mac, 2026-10-11). The mailbox URL names the account and the mailbox: the
    exact directory first, then that mailbox's tree; only when the mailbox's
    directory isn't there, the account's (or every account's) tree."""
    import glob
    from urllib.parse import unquote, urlparse

    root = _mail_root()
    names = [f"{rowid}.emlx", f"{rowid}.partial.emlx"]
    account = None
    candidates: list[str] = []
    if mailbox_url:
        u = urlparse(mailbox_url)
        account = u.netloc or None
        parts = [unquote(p) for p in (u.path or "").split("/") if p]
        mbox = os.path.join(root, account, *[p + ".mbox" for p in parts]) if account and parts else None
        if mbox and os.path.isdir(mbox):
            digits = list(str(rowid // 1000))[::-1] if rowid >= 1000 else []
            for name in names:
                candidates.append(os.path.join(glob.escape(mbox), "*", "Data", *digits, "Messages", name))
            for name in names:
                candidates.append(os.path.join(glob.escape(mbox), "**", "Messages", name))
    if not candidates:
        known = bool(account) and os.path.isdir(os.path.join(root, account))
        scope = os.path.join(glob.escape(root), glob.escape(account) if known else "*")
        for name in names:
            candidates.append(os.path.join(scope, "**", "Messages", name))
    for pattern in candidates:
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None


def _read_email_body_sync(rowid: int) -> dict:
    """A message as Edward reads it: the envelope from the index, then the
    headers, text body (HTML converted) and attachment names from its
    ``.emlx``. ``downloaded`` is False when Mail has no body for it yet."""
    from email import policy
    from email.parser import BytesParser

    from services.mail_service import html_links, html_to_text, merge_links

    try:
        env = _envelope(rowid)
    except (OSError, sqlite3.Error) as e:
        return {"error": f"Mail's index isn't readable ({type(e).__name__})"}
    if not env or env.get("deleted"):
        return {"error": f"no message icloud:{rowid} in Mail"}
    out = {
        "rowid": rowid,
        "sender": env.get("sender") or "",
        "sender_name": env.get("sender_name") or "",
        "subject": env.get("subject") or "",
        "date_received": env.get("date_received"),
        "mailbox_url": env.get("mailbox_url") or "",
        "read": bool(env.get("read")),
        "to": "", "cc": "", "date": "", "reply_to": "", "message_id": "", "references": "",
        "body": "", "links": [], "attachments": [], "partial": False, "downloaded": False,
    }
    path = find_emlx(rowid, env.get("mailbox_url"))
    if not path:
        return out
    out["partial"] = path.endswith(".partial.emlx")
    try:
        with open(path, "rb") as f:
            first = f.readline()
            try:
                raw = f.read(int(first.strip()))
            except ValueError:
                raw = f.read()
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as e:  # noqa: BLE001
        out["error_detail"] = f"couldn't parse the message file ({type(e).__name__})"
        return out

    def header(name: str) -> str:
        try:
            return str(msg.get(name, "") or "")
        except Exception:  # noqa: BLE001 — a malformed header
            return ""

    if header("From"):
        out["sender_header"] = header("From")
    out.update(to=header("To"), cc=header("Cc"), date=header("Date"), reply_to=header("Reply-To"),
               message_id=header("Message-ID"), references=header("References"))
    if header("Subject"):
        out["subject"] = header("Subject")

    text_body = None
    html_body = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if filename or disposition == "attachment":
            try:
                size = len(part.get_payload(decode=True) or b"")
            except Exception:  # noqa: BLE001
                size = 0
            out["attachments"].append({"name": filename or "attachment", "size": size})
            continue
        ctype = part.get_content_type()
        try:
            if ctype == "text/plain" and text_body is None:
                text_body = part.get_content()
            elif ctype == "text/html" and html_body is None:
                html_body = part.get_content()
        except Exception:  # noqa: BLE001 — an undecodable part
            continue
    body = (text_body or "").strip() or (html_to_text(html_body) if html_body else "")
    out["body"] = body
    out["downloaded"] = bool(body)
    out["links"] = merge_links(re.findall(r'https?://[^\s<>"\')\]]+', body), html_links(html_body))
    return out


async def read_email_body(rowid: int) -> dict:
    """Read one Mail.app message (``_read_email_body_sync``)."""
    return await asyncio.to_thread(_read_email_body_sync, rowid)


async def delete_sender_preference(sender_domain: str) -> dict:
    """Remove a sender preference."""
    async with async_session() as session:
        result = await session.execute(
            select(EmailSenderPrefModel).where(
                EmailSenderPrefModel.sender_domain == sender_domain
            )
        )
        pref = result.scalar_one_or_none()
        if not pref:
            return {"success": False, "error": f"No preference found for {sender_domain}"}
        await session.delete(pref)
        await session.commit()
        return {"success": True, "sender_domain": sender_domain}


async def increment_pref_action_count(sender_domain: str) -> None:
    """Increment the action count and update last_applied_at for a sender preference."""
    async with async_session() as session:
        await session.execute(
            update(EmailSenderPrefModel)
            .where(EmailSenderPrefModel.sender_domain == sender_domain)
            .values(
                action_count=EmailSenderPrefModel.action_count + 1,
                last_applied_at=func.now(),
            )
        )
        await session.commit()
