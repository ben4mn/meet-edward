"""Mail's health, and the pieces reading and sending email share (2026-10-11).

iCloud mail reaches Edward only while Mail.app runs on the host Mac: it
fills the Envelope Index the listener and ``messages(mail_search)`` read. It
was once down for four weeks and nothing said so, and the mail listeners were
dark for months behind what looked like a quiet inbox. So:

* ``icloud_status()`` — Mail.app running (``pgrep -x Mail``), the index
  readable, and how old the newest mail in it is;
* ``email_block()`` — that, each listener's last clean poll and Gmail's
  connection, with a verdict and warnings: ``/api/health/report`` →
  ``email``, and ``cached_public_summary`` for the watchdog's ``GET
  /api/health/email`` (a verdict and warnings only: no address, no subject;
  kept 60 s, since the route is public);
* ``owner_addresses()`` — every address that is the owner's, so
  ``send_message`` never emails the owner from a turn they are not part of
  (nothing hard-coded);
* ``html_to_text()``, ``html_links()`` and ``when_text()`` — how an email reads
  in a turn.

Nothing here restarts Mail.app or writes to Mail's files.
"""

from __future__ import annotations

import asyncio
import html as _html
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.owner import owner_emails, owner_text

INDEX_STALE_HOURS = 24
LISTENER_SILENT_MIN = 30
OWNER_ADDRESSES_TTL_S = 600
SENT_ADDRESSES_TOP = 5

MAIL_APP_DOWN = "Mail.app isn't running on this Mac, so new iCloud mail isn't arriving"
INDEX_UNREADABLE = "Mail's index isn't readable (Full Disk Access?)"
GMAIL_LAPSED = owner_text("Gmail's sign-in has lapsed — {OWNER} reconnects it in Settings → Gmail")
GMAIL_NOT_CONNECTED = "Gmail isn't connected"

PUBLIC_SUMMARY_TTL_S = 60

_owner_cache: Optional[tuple] = None  # (monotonic, [addresses])
_clock = time.monotonic
_public_cache: Optional[tuple] = None  # (clock, summary)
_public_inflight: Optional["asyncio.Task"] = None


def reset_for_tests() -> None:
    global _owner_cache, _public_cache, _public_inflight
    _owner_cache = None
    _public_cache = None
    _public_inflight = None


# ------------------------------------------------------------- words --

def when_text(dt: Optional[datetime]) -> str:
    """"Oct 11 9:02 am" in the owner's zone (the repo's one clock format), with
    the year — "Sep 6, 2025 8:43 pm" — when it isn't this year there, so old
    mail never reads as recent."""
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from services.timezone_utils import utc_to_local

        local = utc_to_local(dt)
        this_year = utc_to_local(datetime.now(timezone.utc)).year
    except Exception:  # noqa: BLE001
        local = dt
        this_year = datetime.now(timezone.utc).year
    fmt = "%b %-d %-I:%M %p" if local.year == this_year else "%b %-d, %Y %-I:%M %p"
    return local.strftime(fmt).replace(" AM", " am").replace(" PM", " pm")


def html_to_text(html: Optional[str]) -> str:
    """An HTML email as text: no style or script, block tags as line
    breaks, tags stripped, entities decoded, whitespace collapsed, at most
    two blank lines in a row."""
    t = re.sub(r"(?is)<(style|script|head)\b.*?</\1\s*>", "", html or "")
    t = re.sub(r"(?s)<!--.*?-->", "", t)
    t = re.sub(r"(?i)<\s*/?\s*(br|p|div|li|tr|h[1-6]|table|blockquote)\b[^>]*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t).replace("\xa0", " ")
    out: List[str] = []
    blank = 0
    for line in t.split("\n"):
        line = " ".join(line.split())
        if line:
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 2:
                out.append("")
    return "\n".join(out).strip()


_HREF = re.compile(r"""(?is)<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""")
HTML_LINKS_MAX = 20


def html_links(html: Optional[str]) -> List[str]:
    """The http(s) targets of an HTML email's links, in order, once each,
    at most 20 — read before ``html_to_text`` strips the tags, since
    receipts and tracking mail rarely print the URL (2026-10-11 review)."""
    out: List[str] = []
    for m in _HREF.finditer(html or ""):
        url = _html.unescape(next(g for g in m.groups() if g is not None)).strip()
        if url.lower().startswith(("http://", "https://")) and url not in out:
            out.append(url)
            if len(out) >= HTML_LINKS_MAX:
                break
    return out


def merge_links(*lists: List[str], limit: int = HTML_LINKS_MAX) -> List[str]:
    """Several link lists as one, in order, once each, at most ``limit``."""
    out: List[str] = []
    for urls in lists:
        for url in urls or []:
            if url not in out:
                out.append(url)
    return out[:limit]


# ------------------------------------------------------------ iCloud --

def _mail_app_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "Mail"], capture_output=True, timeout=2).returncode == 0
    except Exception:  # noqa: BLE001 — unknown reads as not running
        return False


def _index_newest() -> Dict[str, Any]:
    from services import email_management_service as ems

    try:
        con = ems.connect_index()
    except Exception as exc:  # noqa: BLE001
        return {"index_readable": False, "error": type(exc).__name__}
    try:
        newest = con.execute("SELECT MAX(date_received) FROM messages").fetchone()[0]
    except sqlite3.Error as exc:
        return {"index_readable": False, "error": type(exc).__name__}
    finally:
        con.close()
    return {"index_readable": True, "newest": newest}


async def icloud_status() -> Dict[str, Any]:
    running = await asyncio.to_thread(_mail_app_running)
    idx = await asyncio.to_thread(_index_newest)
    out: Dict[str, Any] = {"mail_app_running": running, "index_readable": idx.get("index_readable", False),
                           "newest_received_at": None, "index_age_hours": None}
    if idx.get("error"):
        out["error"] = idx["error"]
    newest = idx.get("newest")
    if newest:
        try:
            dt = datetime.fromtimestamp(float(newest), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            dt = None
        if dt is not None:
            out["newest_received_at"] = dt.isoformat()
            out["index_age_hours"] = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    return out


def staleness_note(status: Dict[str, Any]) -> Optional[str]:
    """The line ``mail_search`` adds when iCloud mail may be behind."""
    newest = status.get("newest_received_at")
    when = when_text(datetime.fromisoformat(newest)) if newest else "unknown"
    if not status.get("mail_app_running"):
        return (f"(iCloud: Mail.app isn't running on this Mac, so new iCloud mail isn't arriving — the newest it "
                f"has is from {when}.)")
    age = status.get("index_age_hours")
    if age is not None and age > INDEX_STALE_HOURS:
        return f"(iCloud: the newest mail in Mail's index is from {when} — it may be behind.)"
    return None


# ------------------------------------------------------- the block --

async def _listener_tracks() -> Dict[str, Any]:
    """Which mail tracks are switched on (``heartbeat_config``); {} when
    unreadable."""
    try:
        from sqlalchemy import select

        from services.database import HeartbeatConfigModel, async_session

        async with async_session() as session:
            row = (await session.execute(
                select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default")
            )).scalar_one_or_none()
        if row is None:
            return {}
        return {"email_enabled": bool(row.email_enabled), "email_poll_seconds": row.email_poll_seconds,
                "gmail_enabled": bool(row.gmail_enabled), "gmail_poll_seconds": row.gmail_poll_seconds}
    except Exception:  # noqa: BLE001
        return {}


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _listener_warning(label: str, state: Dict[str, Any], poll_seconds: Any = None) -> Optional[str]:
    """A warning when an enabled listener is stopped, failing or silent for
    30 min (or three of its polls, if longer); a listener started less than
    that long ago is not judged yet. Never its error text: the public
    summary carries these."""
    now = datetime.now(timezone.utc)
    try:
        silent_min = max(LISTENER_SILENT_MIN, 3 * int(poll_seconds or 0) / 60)
    except (TypeError, ValueError):
        silent_min = LISTENER_SILENT_MIN
    status = str(state.get("status") or "")
    if status in ("stopped", "error"):
        return f"{label} is {status}"
    last_ok = _parse(state.get("last_ok_at"))
    if last_ok is None:
        started = _parse(state.get("started_at"))
        if started is not None and (now - started).total_seconds() < silent_min * 60:
            return None
        return f"{label} hasn't polled cleanly since it started"
    age_min = (now - last_ok).total_seconds() / 60
    if age_min > silent_min:
        return f"{label} hasn't polled cleanly in {int(age_min)} min"
    return None


async def email_block() -> Dict[str, Any]:
    """Mail's health for the report: iCloud (Mail.app, the index, the
    listener) and Gmail (connected, signed in, the listener), a verdict and
    the warnings."""
    from services import gmail_service
    from services.heartbeat import listener_email, listener_gmail

    tracks = await _listener_tracks()
    icloud = await icloud_status()
    icloud["listener"] = listener_email.listener_state()
    icloud["listener_enabled"] = bool(tracks.get("email_enabled"))
    warnings: List[str] = []
    info: List[str] = []

    if not icloud["mail_app_running"]:
        warnings.append(MAIL_APP_DOWN)
    if not icloud["index_readable"]:
        warnings.append(INDEX_UNREADABLE)
    elif icloud.get("index_age_hours") is not None and icloud["index_age_hours"] > INDEX_STALE_HOURS:
        warnings.append(f"the newest mail in Mail's index is {int(icloud['index_age_hours'])} h old")
    if icloud["listener_enabled"]:
        w = _listener_warning("the iCloud mail listener", icloud["listener"], tracks.get("email_poll_seconds"))
        if w:
            warnings.append(w)
    else:
        info.append("the iCloud mail listener is off")

    gmail: Dict[str, Any] = {"connected": None, "account": None, "listener": listener_gmail.listener_state(),
                             "listener_enabled": bool(tracks.get("gmail_enabled"))}
    try:
        gmail["connected"] = bool(await gmail_service.is_configured())
        gmail["account"] = await gmail_service.get_email_address() if gmail["connected"] else None
    except Exception as exc:  # noqa: BLE001
        gmail["error"] = type(exc).__name__
    if gmail["connected"]:
        if gmail_service.token_refresh_failed():
            gmail["signed_in"] = False
            warnings.append(GMAIL_LAPSED)
        if gmail["listener_enabled"]:
            w = _listener_warning("the Gmail listener", gmail["listener"], tracks.get("gmail_poll_seconds"))
            if w:
                warnings.append(w)
        else:
            info.append("Gmail is connected; \"watch my inbox\" is off")
    elif gmail["connected"] is False:
        info.append(GMAIL_NOT_CONNECTED)

    return {"icloud": icloud, "gmail": gmail, "verdict": "warn" if warnings else "ok",
            "warnings": warnings, "info": info}


def public_summary(block: Dict[str, Any]) -> Dict[str, str]:
    """What ``GET /api/health/email`` may say to anyone: the verdict and
    the warnings (written without addresses or subjects)."""
    return {"verdict": str(block.get("verdict") or "unknown"),
            "summary": "; ".join(str(w) for w in block.get("warnings") or [])}


async def _compute_public_summary() -> Dict[str, str]:
    global _public_cache, _public_inflight
    try:
        out = public_summary(await email_block())
    except Exception as exc:  # noqa: BLE001
        out = {"verdict": "unknown", "summary": f"the email check failed ({type(exc).__name__})"}
    _public_cache = (_clock(), out)
    _public_inflight = None
    return out


async def cached_public_summary() -> Dict[str, str]:
    """``public_summary(email_block())`` kept for 60 s, and hits that arrive
    together share one check: ``GET /api/health/email`` is public and
    reachable through the tunnel, and a check runs pgrep, opens Mail's index
    and asks Postgres (2026-10-11 review)."""
    global _public_inflight
    hit = _public_cache
    if hit is not None and _clock() - hit[0] < PUBLIC_SUMMARY_TTL_S:
        return dict(hit[1])
    loop = asyncio.get_running_loop()
    task = _public_inflight
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_compute_public_summary())
        _public_inflight = task
    return dict(await asyncio.shield(task))


# ----------------------------------------------- the owner's addresses --

def _sent_addresses() -> List[str]:
    """The addresses the owner sends from in Mail.app: the top senders of
    the Sent mailboxes."""
    from services import email_management_service as ems

    con = ems.connect_index()
    try:
        rows = con.execute(
            """
            SELECT a.address, COUNT(*) AS n
            FROM messages m
            JOIN mailboxes mb ON m.mailbox = mb.ROWID
            JOIN addresses a ON m.sender = a.ROWID
            WHERE mb.url LIKE '%/Sent%' AND a.address LIKE '%@%'
            GROUP BY lower(a.address)
            ORDER BY n DESC
            LIMIT ?
            """,
            (SENT_ADDRESSES_TOP,),
        ).fetchall()
    finally:
        con.close()
    return [str(r[0]) for r in rows if r and r[0]]


async def owner_addresses() -> List[str]:
    """Every email address that is the owner's: ``owner.owner_emails()``
    (``EDWARD_OWNER_EMAILS`` and ``GMAIL_ACCOUNT``, read per call), the
    connected Gmail account, the addresses they send from in Mail.app, and
    the email handles in ``heartbeat_config.imessage_owner_handles`` and
    ``EDWARD_OWNER_HANDLES``. The lookups are cached 10 min. Never raises."""
    global _owner_cache
    out: List[str] = []

    def add(addr: Any) -> None:
        a = str(addr or "").strip()
        if "@" in a and a.lower() not in {x.lower() for x in out}:
            out.append(a)

    for addr in owner_emails():
        add(addr)
    now = time.monotonic()
    if _owner_cache is not None and now - _owner_cache[0] < OWNER_ADDRESSES_TTL_S:
        for a in _owner_cache[1]:
            add(a)
        return out

    found: List[str] = []
    try:
        from services import gmail_service

        found.append(await gmail_service.get_email_address() or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        found.extend(await asyncio.to_thread(_sent_addresses))
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.graph import tools as legacy

        found.extend(h for h in await legacy._owner_handles() if "@" in str(h))
    except Exception:  # noqa: BLE001
        pass
    found = [f for f in found if "@" in str(f or "")]
    _owner_cache = (now, found)
    for a in found:
        add(a)
    return out
