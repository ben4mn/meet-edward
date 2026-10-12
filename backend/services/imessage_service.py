"""
iMessage service using AppleScript.

A simple, direct approach to send iMessages without MCP dependencies.
Requires macOS with Messages.app configured.

The transport (2026-10-11; docs/architecture/pings.md). Every send goes
through ``send_to_chat``:

1. **Routing comes from chat.db**, not from spelling: ``chat_info`` reads the
   chat's ``style`` (43 = group), ``service_name`` and ``guid``, and the pure
   ``applescript_targets`` lists the AppleScript targets to try in order. A
   ``-1728`` ("Can't get chat") moves on to the next one. Many group chats
   have a 32-hex ``chat_identifier``; the old ``startswith("chat")`` rule sent
   them to ``participant "<hex>"`` — an undeliverable new chat — and called it
   delivered.
2. **The read-back decides.** After osascript, chat.db is polled for the
   from-me row with the same normalised text (or the row the listener
   already claimed, ``imessage_sent.claimed_rowid``). A row found is a
   confirmed send even after an osascript timeout (Messages often sends
   anyway); no row with osascript ok is ``confirmed: False`` (the chat is
   marked unconfirmed for the self-chat breaker); no row with osascript
   failed is ``success: False`` with the real error. **A failure is never
   reported as delivered.**
3. **Ping bookkeeping**, synchronously before returning: a send into the
   chat of the ping a turn is answering is noted on its context
   (``pings.hooks.note_send_start``/``note_send_end``), and the echo index
   (``imessage_sent``) learns the row.
4. **The in-flight guard**: an autonomous turn (mind, pulse, a scheduled
   event, the heartbeat) cannot send into a chat whose ping turn is running.
5. **The ledger** gets successful sends only, with an **aware UTC**
   ``sent_at`` and the read-back columns.

``_run_osascript`` is the only way to osascript; every test patches it, and
conftest refuses a real one.
"""

import asyncio
import hashlib
import re
import sqlite3
import subprocess
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Configuration
IMESSAGE_ENABLED = os.getenv("IMESSAGE_ENABLED", "true").lower() == "true"

# Monotonic count of outgoing iMessages this process has sent (the bounded
# echo deque it replaced saturated at 20 and could not answer "did this turn
# send anything?"; echoes are now recognised by ROWID, ``imessage_sent``). The
# heartbeat's respond path brackets a reply turn with this counter to tell a
# turn that died before sending from one that sent and then timed out — the
# latter must never be retried, or the recipient gets the reply twice.
_outgoing_send_count = 0

OSASCRIPT_TIMEOUT_S = 30
# What chat.db holds as the text of an attachment-only message.
OBJECT_REPLACEMENT_CHAR = "\ufffc"
# The read-back: chat.db is polled this often, for this long, for the row.
READBACK_TIMEOUT_S = 5.0
READBACK_INTERVAL_S = 0.25
# chat.db's chat.style for a group.
STYLE_GROUP = 43
_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
# Turns nobody asked for: they may not talk over a ping turn in its chat.
AUTONOMOUS_SOURCES = frozenset({"mind", "pulse", "scheduled_event", "heartbeat"})
INFLIGHT_REFUSAL = "Edward is answering a ping in this chat right now; leave it to that reply."
# osascript errors that mean "that spelling of the chat doesn't exist": try the next target.
_NEXT_TARGET_MARKERS = ("-1728", "can't get chat", "can’t get chat")
# Files: Messages reads a copy staged here (a folder it can always read);
# EDWARD_IMESSAGE_OUTBOX overrides it. Staged folders live a day.
DEFAULT_OUTBOX = "~/Library/Messages/Attachments/Edward"
OUTBOX_KEEP_S = 24 * 3600
ATTACHMENT_OSASCRIPT_TIMEOUT_S = 60
ATTACHMENT_READBACK_TIMEOUT_S = 15.0
_OUTBOX_DIR = re.compile(r"^[0-9a-f]{32}$")


def _note_outgoing_send_count() -> None:
    global _outgoing_send_count
    _outgoing_send_count += 1


def outgoing_send_count() -> int:
    """How many iMessages this process has sent since start."""
    return _outgoing_send_count


# Sends now run in worker threads (asyncio.to_thread — osascript blocks up
# to 30s), where get_running_loop() raises. Callers on the loop register it
# here so ledger/commitment coroutines can be scheduled back onto it from
# any thread. Skipping the ledger is NOT safe: without a row, Edward's own
# iCloud-echoed reply in the owner's self-chat looks like an owner ping and the
# direct-channel triage would answer itself in a loop.
_MAIN_LOOP: "asyncio.AbstractEventLoop | None" = None


def note_main_loop(loop: "asyncio.AbstractEventLoop") -> None:
    """Record the app's event loop so worker-thread sends can schedule their
    bookkeeping coroutines back onto it."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def _schedule_coroutine(coro) -> bool:
    """Run ``coro`` on the app loop from any thread. Returns False (and
    closes the coroutine) when no loop is available."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        loop.create_task(coro)
        return True
    if _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)
        return True
    coro.close()
    return False


def _record_outgoing_send(chat_identifier: str | None, message: str, **fields: Any) -> None:
    """Persist a successful send to the ledger. Best-effort, non-blocking.

    The listener consults this ledger to tell Edward's own iCloud-echoed
    replies from the owner's own messages (both arrive with is_from_me=True), and
    the ping runner reads ``ping_id``/``purpose`` after a restart.
    ``fields`` are the read-back columns (``route``, ``message_rowid``,
    ``message_guid``, ``chat_guid``, ``purpose``, ``ping_id``,
    ``turn_source``, ``confirmed_at``). ``sent_at`` is bound **aware UTC**:
    the column is timestamptz, and a naive value read as local time put
    rows hours off on a Mac whose local zone is not UTC.
    """
    try:
        text_hash = hashlib.sha256(message[:200].encode("utf-8")).hexdigest()
        from services import imessage_sent

        norm_hash = imessage_sent.text_hash(message)
    except Exception:
        return
    allowed = ("route", "message_rowid", "message_guid", "chat_guid", "purpose", "ping_id", "turn_source",
               "confirmed_at")
    extra = {k: v for k, v in fields.items() if k in allowed and v is not None}
    sent_at = datetime.now(timezone.utc)

    async def _insert():
        try:
            from services.database import async_session, ImessageOutgoingSendModel

            async with async_session() as session:
                session.add(ImessageOutgoingSendModel(
                    chat_identifier=(chat_identifier or None) and str(chat_identifier)[:255],
                    text_hash=text_hash,
                    sent_at=sent_at,
                    ok=True,
                    text_norm_hash=norm_hash,
                    text_len=len(message),
                    **extra,
                ))
                await session.commit()
        except Exception as e:
            print(f"[iMessage] Failed to record outgoing send: {type(e).__name__}")

    if not _schedule_coroutine(_insert()):
        print(
            "[iMessage] WARNING: outgoing send NOT recorded in ledger "
            "(no event loop available) — echo suppression degraded"
        )


def is_available() -> bool:
    """Check if iMessage is available (macOS only)."""
    if not IMESSAGE_ENABLED:
        return False
    # Check if we're on macOS
    return os.uname().sysname == "Darwin"


def looks_like_imessage_handle(recipient: str) -> bool:
    """True when recipient is a deliverable iMessage handle (phone/email).

    AppleScript's ``participant "<text>"`` does NOT resolve contact names —
    Messages happily creates an undeliverable chat addressed to the literal
    text and reports success (2026-07-07: two replies to "Nate Brooks" went
    into a chat whose identifier was literally "nate brooks").
    """
    r = (recipient or "").strip()
    if not r:
        return False
    if "@" in r:
        local, _, domain = r.rpartition("@")
        return bool(local) and "." in domain and " " not in r
    digits = re.sub(r"\D", "", r)
    if len(digits) < 7:
        return False
    return re.fullmatch(r"[0-9+()\-.\s]+", r) is not None


# ----------------------------------------------------------- chat.db ----

def _chat_db_path() -> str:
    """The listener's chat.db (one path; tests re-point the listener's)."""
    from services.heartbeat import listener_imessage

    return listener_imessage.CHAT_DB_PATH


def _connect_chat_db() -> Optional[sqlite3.Connection]:
    path = _chat_db_path()
    if not path or not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}", uri=True, timeout=2)
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error:
        return None


def _tail(chat_identifier: str) -> str:
    t = str(chat_identifier or "").strip()
    return t.rsplit(";", 1)[1].strip() if ";" in t else t


def chat_info(chat_identifier: str) -> Optional[dict]:
    """The chat as chat.db knows it, or None (no chat.db, no row, an error):
    ``{"chat_identifier", "guid", "style", "is_group", "display_name",
    "service", "chat_rowid"}``. Accepts any spelling (``chat123``,
    ``any;+;chat123``, a chat.db guid, a phone). When a handle has more than
    one chat (iMessage and SMS), the one with the latest message wins."""
    given = str(chat_identifier or "").strip()
    if not given:
        return None
    conn = _connect_chat_db()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT c.ROWID, c.guid, c.style, c.chat_identifier, c.display_name, c.service_name, "
            "(SELECT MAX(cmj.message_date) FROM chat_message_join cmj WHERE cmj.chat_id = c.ROWID) AS last "
            "FROM chat c WHERE c.chat_identifier = ? OR c.guid = ? "
            "ORDER BY (c.guid = ?) DESC, last DESC, c.ROWID DESC LIMIT 1",
            (_tail(given), given, given)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row:
        return None
    return {"chat_identifier": row[3], "guid": row[1], "style": row[2], "is_group": row[2] == STYLE_GROUP,
            "display_name": row[4], "service": row[5], "chat_rowid": row[0]}


def _looks_like_group(chat_identifier: str) -> bool:
    given = str(chat_identifier or "")
    tail = _tail(given)
    return ";+;" in given or tail.startswith("chat") or bool(_HEX32.match(tail))


def applescript_targets(chat_identifier: str, info: Optional[dict]) -> List[Tuple[str, str]]:
    """``[(route, target)]`` to try, in order. Pure.

    - a group (chat.db style 43; without chat.db: ``;+;``, ``chat…`` or 32
      hex): the id as given when it holds ``;+;``, ``any;+;<tail>``, the
      chat.db guid, ``iMessage;+;<tail>`` — de-duplicated;
    - a 1:1 chat that is not iMessage (SMS, RCS) with a known guid: the
      guid, then the participant;
    - otherwise the participant, and only when the handle is a phone or an
      email (Messages would make an undeliverable chat out of a name)."""
    given = str(chat_identifier or "").strip()
    tail = _tail(given)
    is_group = bool(info["is_group"]) if info else _looks_like_group(given)
    out: List[Tuple[str, str]] = []

    def add(route: str, target: Optional[str]) -> None:
        if target and (route, target) not in out:
            out.append((route, target))

    guid = (info or {}).get("guid")
    if is_group:
        if ";+;" in given:
            add("chat_id", given)
        add("chat_id", f"any;+;{tail}")
        add("chat_id", guid)
        add("chat_id", f"iMessage;+;{tail}")
        return out
    service = str((info or {}).get("service") or "")
    if guid and service and service.lower() != "imessage":
        add("chat_id", guid)
    elif ";-;" in given and not info:
        add("chat_id", given)
    if looks_like_imessage_handle(tail):
        add("participant", tail)
    return out


def _escape(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


def _script_for(route: str, target: str, message: str) -> str:
    if route == "chat_id":
        return f'''
    tell application "Messages"
        set targetChat to a reference to chat id "{_escape(target)}"
        send "{_escape(message)}" to targetChat
    end tell
    '''
    return f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{_escape(target)}" of targetService
        send "{_escape(message)}" to targetBuddy
    end tell
    '''


def _run_osascript(script: str, timeout: float = OSASCRIPT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """The one way to osascript. Raises ``subprocess.TimeoutExpired``."""
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)


def _decoded_text(text: Optional[str], body: Optional[bytes]) -> str:
    if text:
        return text
    if not body:
        return ""
    try:
        from services.heartbeat.imessage_decoder import decode_attributed_body

        decoded = decode_attributed_body(body)
        if decoded:
            return decoded
        from services.heartbeat.listener_imessage import _extract_text_from_attributed_body_legacy

        return _extract_text_from_attributed_body_legacy(body) or ""
    except Exception:  # noqa: BLE001
        return ""


def _max_message_rowid() -> Optional[int]:
    conn = _connect_chat_db()
    if conn is None:
        return None
    try:
        return int(conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _chat_clause(chat_identifier: str) -> Tuple[str, tuple]:
    """SQL for "this chat": its ``chat_identifier`` or guid as given, and
    for a 1:1 handle any spelling of it — a phone on its last ten digits
    (never a group, whose 32-hex or ``chat…`` id can end in digits), an
    email without case. A contact's number comes as the address book
    stores it ("(201) 555-0111"); chat.db holds "+12015550111"."""
    given = str(chat_identifier or "").strip()
    tail = _tail(given)
    clause, params = "c.chat_identifier = ? OR c.guid = ?", [tail, given]
    digits = re.sub(r"\D", "", tail)
    if "@" in tail:
        clause += " OR lower(c.chat_identifier) = ?"
        params.append(tail.lower())
    elif len(digits) >= 10 and re.fullmatch(r"[0-9+()\-.\s]+", tail):
        clause += f" OR (COALESCE(c.style, 0) != {STYLE_GROUP} AND c.chat_identifier LIKE ?)"
        params.append(f"%{digits[-10:]}")
    return f"({clause})", tuple(params)


def _from_me_rows_after(chat_identifier: str, after_rowid: int) -> List[tuple]:
    conn = _connect_chat_db()
    if conn is None:
        return []
    clause, params = _chat_clause(chat_identifier)
    try:
        return conn.execute(
            "SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.error FROM message m "
            "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID JOIN chat c ON c.ROWID = cmj.chat_id "
            f"WHERE m.ROWID > ? AND m.is_from_me = 1 AND {clause} ORDER BY m.ROWID ASC LIMIT 50",
            (int(after_rowid), *params)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _guid_for_rowid(rowid: int) -> Optional[str]:
    conn = _connect_chat_db()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT guid FROM message WHERE ROWID = ?", (int(rowid),)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _find_sent_row(chat_identifier: str, before_rowid: Optional[int], message: str,
                   token: int) -> Optional[Dict[str, Any]]:
    """One look for the send's row: the row the listener claimed, else the
    first from-me row after ``before_rowid`` with the same normalised text."""
    from services import imessage_sent

    claimed = imessage_sent.claimed_rowid(token)
    if claimed is not None:
        return {"rowid": int(claimed), "guid": _guid_for_rowid(claimed)}
    if before_rowid is None:
        return None
    norm = imessage_sent.normalize(message)
    for rowid, guid, text, body, _error in _from_me_rows_after(chat_identifier, before_rowid):
        if imessage_sent.normalize(_decoded_text(text, body)) == norm:
            return {"rowid": int(rowid), "guid": guid}
    return None


def _read_back(chat_identifier: str, before_rowid: Optional[int], message: str, token: int,
               poll: bool) -> Optional[Dict[str, Any]]:
    found = _find_sent_row(chat_identifier, before_rowid, message, token)
    if found is not None or not poll:
        return found
    deadline = time.monotonic() + READBACK_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(READBACK_INTERVAL_S)
        found = _find_sent_row(chat_identifier, before_rowid, message, token)
        if found is not None:
            return found
    return None


def recent_from_me_since(chat_identifier: str, since: datetime, text: Optional[str] = None) -> Optional[dict]:
    """The newest from-me row in this chat after ``since`` (aware; naive is
    taken as UTC) that is Edward-signed, or whose normalised text equals
    ``text``: ``{"rowid", "guid", "error"}``, or None. chat.db, read-only.
    A SIGKILL can lose the ledger write; chat.db keeps the row."""
    from services import imessage_sent
    from services.heartbeat.listener_imessage import APPLE_EPOCH_OFFSET
    from services.mind_service import has_edward_signature

    conn = _connect_chat_db()
    if conn is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    apple_since = int((since.timestamp() - APPLE_EPOCH_OFFSET) * 1e9)
    clause, params = _chat_clause(chat_identifier)
    try:
        rows = conn.execute(
            "SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.error FROM message m "
            "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID JOIN chat c ON c.ROWID = cmj.chat_id "
            f"WHERE m.is_from_me = 1 AND m.date > ? AND {clause} ORDER BY m.ROWID DESC LIMIT 50",
            (apple_since, *params)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    want = imessage_sent.normalize(text) if text else None
    for rowid, guid, body_text, body, error in rows:
        decoded = _decoded_text(body_text, body)
        if has_edward_signature(decoded) or (want and imessage_sent.normalize(decoded) == want):
            return {"rowid": int(rowid), "guid": guid, "error": int(error or 0)}
    return None


# ---------------------------------------------------------- the send ----

def _current_turn_source() -> Optional[str]:
    try:
        from services import turn_ledger

        rec = turn_ledger.current_turn()
        return getattr(rec, "source", None) if rec is not None else None
    except Exception:  # noqa: BLE001
        return None


def _inflight_refusal(chat_identifier: str, turn_source: Optional[str]) -> Optional[str]:
    if turn_source not in AUTONOMOUS_SOURCES:
        return None
    try:
        from services.pings import hooks as pings_hooks

        if pings_hooks.chat_key(chat_identifier) in pings_hooks.chats_in_flight():
            return INFLIGHT_REFUSAL
    except Exception:  # noqa: BLE001 - the guard never blocks a send by failing
        return None
    return None


def _is_next_target_error(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(marker in low for marker in _NEXT_TARGET_MARKERS)


def send_to_chat(chat_identifier: str, message: str, *, purpose: str = "tool") -> dict:
    """Send ``message`` (already signed by the caller) into a chat or to a
    handle. Blocking: callers on the loop use ``asyncio.to_thread``.

    Returns ``{"success", "error", "route", "chat_identifier",
    "message_rowid", "message_guid", "confirmed"}``. ``success`` is True
    only when osascript said ok or the read-back found the row; a failure
    carries the real error and is never delivered."""
    from services import imessage_sent
    from services.pings import hooks as pings_hooks

    target = str(chat_identifier or "").strip()
    result: Dict[str, Any] = {"success": False, "error": None, "route": None, "chat_identifier": target,
                              "message_rowid": None, "message_guid": None, "confirmed": False}
    if not is_available():
        result["error"] = "iMessage not available"
        return result
    if not target:
        result["error"] = "no chat or handle to send to"
        return result
    turn_source = _current_turn_source()
    refusal = _inflight_refusal(target, turn_source)
    if refusal:
        result["error"] = refusal
        return result

    info = chat_info(target)
    targets = applescript_targets(target, info)
    if not targets:
        result["error"] = (f"'{target}' is not a chat Messages knows, nor a phone number or email address. "
                           "Reply by the chat_id from the message context, or resolve the contact first.")
        return result

    ctx = pings_hooks.note_send_start(target)
    token = imessage_sent.begin(target, message)
    osascript_ok = False
    timed_out = False
    error: Optional[str] = None
    route: Optional[str] = None
    found: Optional[Dict[str, Any]] = None
    resolved = False
    try:
        before_rowid = _max_message_rowid()
        for route_name, dest in targets:
            route = route_name
            try:
                proc = _run_osascript(_script_for(route_name, dest, message), timeout=OSASCRIPT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Not a failure yet: Messages often sends anyway. Never try
                # another target after a timeout (that could send twice).
                timed_out = True
                error = f"Messages timed out after {OSASCRIPT_TIMEOUT_S} s sending to {dest}"
                break
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                break
            if proc.returncode == 0:
                osascript_ok, error = True, None
                break
            error = (proc.stderr or "").strip() or f"osascript exited {proc.returncode}"
            if not _is_next_target_error(error):
                break

        found = _read_back(target, before_rowid, message, token, poll=osascript_ok or timed_out)
        success = osascript_ok or found is not None
        result.update(success=success, route=route if success else None, error=None if success else error,
                      confirmed=found is not None,
                      message_rowid=(found or {}).get("rowid"), message_guid=(found or {}).get("guid"))
        if success:
            imessage_sent.resolve(token, rowid=result["message_rowid"], guid=result["message_guid"])
        else:
            imessage_sent.abandon(token)
        resolved = True
    finally:
        if not resolved:
            imessage_sent.abandon(token)
        pings_hooks.note_send_end(ctx, ok=bool(result["success"]), purpose=purpose, rowid=result["message_rowid"],
                                  route=result["route"], error=result["error"])

    if result["success"]:
        _note_outgoing_send_count()
        _record_outgoing_send(
            target, message, route=result["route"], message_rowid=result["message_rowid"],
            message_guid=result["message_guid"], chat_guid=(info or {}).get("guid"), purpose=purpose,
            ping_id=(ctx or {}).get("ping_id"), turn_source=turn_source,
            confirmed_at=datetime.now(timezone.utc) if result["confirmed"] else None)
    else:
        print(f"[iMessage] send to {target} failed: {str(result['error'])[:200]}")
    return result


# ------------------------------------------------------------ files ----

def _outbox() -> Path:
    return Path(os.getenv("EDWARD_IMESSAGE_OUTBOX") or DEFAULT_OUTBOX).expanduser()


def _outbox_folders_in_use(outbox: Path) -> Optional[set]:
    """The staged folders (32-hex names) a chat.db attachment row still
    names as its file, or None when chat.db can't say."""
    conn = _connect_chat_db()
    if conn is None:
        return None
    try:
        rows = conn.execute("SELECT filename FROM attachment WHERE filename LIKE ?",
                            (f"%/{outbox.name}/%",)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {part for (filename,) in rows for part in str(filename or "").split("/") if _OUTBOX_DIR.match(part)}


def _prune_outbox(outbox: Path, now: Optional[float] = None) -> None:
    """Remove staged folders (32-hex names, ours) older than a day that no
    message points at. Whether Messages keeps the staged path as the
    attachment's file or copies it into its own store isn't verified on
    this macOS: a folder chat.db still names is kept, and without chat.db
    nothing is removed."""
    import shutil

    now = now or time.time()
    try:
        entries = list(outbox.iterdir())
    except OSError:
        return
    old = []
    for entry in entries:
        try:
            if entry.is_dir() and _OUTBOX_DIR.match(entry.name) and now - entry.stat().st_mtime > OUTBOX_KEEP_S:
                old.append(entry)
        except OSError:
            continue
    if not old:
        return
    in_use = _outbox_folders_in_use(outbox)
    if in_use is None:
        return
    for entry in old:
        if entry.name not in in_use:
            shutil.rmtree(entry, ignore_errors=True)


def _stage(path: str) -> Path:
    """A copy of ``path`` in its own folder under the outbox (the original
    is never moved). Raises OSError."""
    import shutil
    import uuid

    outbox = _outbox()
    outbox.mkdir(parents=True, exist_ok=True)
    _prune_outbox(outbox)
    folder = outbox / uuid.uuid4().hex
    folder.mkdir()
    staged = folder / Path(path).name
    shutil.copy2(path, staged)
    return staged


def _attachment_script_for(route: str, target: str, staged: str) -> str:
    if route == "chat_id":
        return f'''
    tell application "Messages"
        set targetChat to a reference to chat id "{_escape(target)}"
        set theFile to POSIX file "{_escape(staged)}"
        send theFile to targetChat
    end tell
    '''
    return f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{_escape(target)}" of targetService
        set theFile to POSIX file "{_escape(staged)}"
        send theFile to targetBuddy
    end tell
    '''


def _find_attachment_row(chat_identifier: str, before_rowid: int, name: str) -> Optional[Dict[str, Any]]:
    """The first from-me message after ``before_rowid`` in this chat that
    carries an attachment named ``name`` and no error."""
    conn = _connect_chat_db()
    if conn is None:
        return None
    clause, params = _chat_clause(chat_identifier)
    try:
        row = conn.execute(
            "SELECT m.ROWID, m.guid FROM message m "
            "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID JOIN chat c ON c.ROWID = cmj.chat_id "
            "JOIN message_attachment_join maj ON maj.message_id = m.ROWID "
            "JOIN attachment a ON a.ROWID = maj.attachment_id "
            f"WHERE m.ROWID > ? AND m.is_from_me = 1 AND COALESCE(m.error, 0) = 0 AND a.transfer_name = ? "
            f"AND {clause} ORDER BY m.ROWID ASC LIMIT 1",
            (int(before_rowid), name, *params)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {"rowid": int(row[0]), "guid": row[1]} if row else None


def _claimed_attachment_row(token: int, name: str) -> Optional[Dict[str, Any]]:
    """The row the listener claimed against this in-flight file send, when
    it carries an attachment of that name and no error (a photo the owner
    sent meanwhile is not this send)."""
    from services import imessage_sent

    claimed = imessage_sent.claimed_rowid(token)
    if claimed is None:
        return None
    conn = _connect_chat_db()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT m.ROWID, m.guid FROM message m "
            "JOIN message_attachment_join maj ON maj.message_id = m.ROWID "
            "JOIN attachment a ON a.ROWID = maj.attachment_id "
            "WHERE m.ROWID = ? AND COALESCE(m.error, 0) = 0 AND a.transfer_name = ? LIMIT 1",
            (int(claimed), name)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {"rowid": int(row[0]), "guid": row[1]} if row else None


def _find_file_send(chat_identifier: str, before_rowid: int, name: str, token: int) -> Optional[Dict[str, Any]]:
    return _claimed_attachment_row(token, name) or _find_attachment_row(chat_identifier, before_rowid, name)


def _attachment_read_back(chat_identifier: str, before_rowid: Optional[int], name: str, token: int,
                          poll: bool) -> Optional[Dict[str, Any]]:
    if before_rowid is None:
        return None
    found = _find_file_send(chat_identifier, before_rowid, name, token)
    if found is not None or not poll:
        return found
    deadline = time.monotonic() + ATTACHMENT_READBACK_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(READBACK_INTERVAL_S)
        found = _find_file_send(chat_identifier, before_rowid, name, token)
        if found is not None:
            return found
    return None


def send_attachment(chat_identifier: str, path: str, *, purpose: str = "tool") -> dict:
    """Send the file at ``path`` into a chat or to a handle, routed like a
    text (``chat_info`` + ``applescript_targets``). Blocking: callers on the
    loop use ``asyncio.to_thread``.

    A copy is staged under the outbox and sent as ``POSIX file``; success
    only when chat.db shows a from-me message after the send, in that chat,
    with an attachment of that name and no error — "osascript ok" alone is
    a failure. Returns ``{"success", "error", "route", "chat_identifier",
    "message_rowid", "message_guid", "confirmed", "staged"}``."""
    from services import imessage_sent
    from services.pings import hooks as pings_hooks

    target = str(chat_identifier or "").strip()
    result: Dict[str, Any] = {"success": False, "error": None, "route": None, "chat_identifier": target,
                              "message_rowid": None, "message_guid": None, "confirmed": False, "staged": None}
    if not is_available():
        result["error"] = "iMessage not available"
        return result
    if not target:
        result["error"] = "no chat or handle to send to"
        return result
    turn_source = _current_turn_source()
    refusal = _inflight_refusal(target, turn_source)
    if refusal:
        result["error"] = refusal
        return result
    info = chat_info(target)
    targets = applescript_targets(target, info)
    if not targets:
        result["error"] = (f"'{target}' is not a chat Messages knows, nor a phone number or email address. "
                           "Reply by the chat_id from the message context, or resolve the contact first.")
        return result
    source = Path(str(path or "")).expanduser()
    if not source.is_file():
        result["error"] = f"no such file: {path}"
        return result
    before_rowid = _max_message_rowid()
    if before_rowid is None:
        # Unconfirmable means "failed", which invites a second try: refuse first.
        result["error"] = "chat.db isn't readable, so a file send couldn't be confirmed — not sending it"
        return result
    try:
        staged = _stage(str(source))
    except OSError as exc:
        result["error"] = f"couldn't stage {source.name} for Messages ({type(exc).__name__}: {exc})"
        return result
    result["staged"] = str(staged)
    name = staged.name

    ctx = pings_hooks.note_send_start(target)
    token = imessage_sent.begin(target, OBJECT_REPLACEMENT_CHAR)
    osascript_ok = timed_out = resolved = False
    error: Optional[str] = None
    route: Optional[str] = None
    try:
        for route_name, dest in targets:
            route = route_name
            try:
                proc = _run_osascript(_attachment_script_for(route_name, dest, str(staged)),
                                      timeout=ATTACHMENT_OSASCRIPT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Never another target after a timeout: Messages may be sending it.
                timed_out = True
                error = f"Messages timed out after {ATTACHMENT_OSASCRIPT_TIMEOUT_S} s sending {name} to {dest}"
                break
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                break
            if proc.returncode == 0:
                osascript_ok, error = True, None
                break
            error = (proc.stderr or "").strip() or f"osascript exited {proc.returncode}"
            if not _is_next_target_error(error):
                break

        # Read back by the chat as chat.db spells it (any spelling of a handle
        # matches, ``_chat_clause``), or the row the listener claimed.
        found = _attachment_read_back((info or {}).get("chat_identifier") or target, before_rowid, name, token,
                                      poll=osascript_ok or timed_out)
        if found is None and (osascript_ok or timed_out):
            error = ("Messages took the file but no message appeared in the chat" if osascript_ok
                     else f"{error}, and no message appeared in the chat")
        success = found is not None
        result.update(success=success, route=route if success else None, error=None if success else error,
                      confirmed=success, message_rowid=(found or {}).get("rowid"),
                      message_guid=(found or {}).get("guid"))
        if success or osascript_ok or timed_out:
            # No row after osascript ok (or a timeout) may still land later:
            # resolve without one marks the chat unconfirmed for the
            # self-chat breaker, as an unconfirmed text does. Still a failure.
            imessage_sent.resolve(token, rowid=result["message_rowid"], guid=result["message_guid"])
        else:
            imessage_sent.abandon(token)
        resolved = True
    finally:
        if not resolved:
            imessage_sent.abandon(token)
        pings_hooks.note_send_end(ctx, ok=bool(result["success"]), purpose=purpose, rowid=result["message_rowid"],
                                  route=result["route"], error=result["error"])

    if result["success"]:
        _note_outgoing_send_count()
        _record_outgoing_send(
            target, f"[attachment] {name}", route=result["route"], message_rowid=result["message_rowid"],
            message_guid=result["message_guid"], chat_guid=(info or {}).get("guid"), purpose=purpose,
            ping_id=(ctx or {}).get("ping_id"), turn_source=turn_source, confirmed_at=datetime.now(timezone.utc))
    else:
        print(f"[iMessage] attachment to {target} failed: {str(result['error'])[:200]}")
    return result


def send_imessage(recipient: str, message: str) -> dict:
    """Send an iMessage to a phone number or email (``send_to_chat``).

    Returns ``{"success": True, "recipient", "message", …}`` or
    ``{"success": False, "error", …}``."""
    if not is_available():
        return {"success": False, "error": "iMessage not available"}

    if not looks_like_imessage_handle(recipient):
        return {
            "success": False,
            "error": (
                f"'{recipient}' is not a phone number or email address. Messages "
                "would create an undeliverable chat addressed to the literal text. "
                "Resolve the contact to a real handle first (lookup_contact), or "
                "reply by chat_id."
            ),
        }
    result = send_to_chat(recipient, message)
    if not result["success"]:
        return {**result, "success": False, "error": result.get("error") or "Unknown error sending iMessage"}
    return {**result, "recipient": recipient, "message": "iMessage sent successfully"}


def send_imessage_to_chat(chat_identifier: str, message: str) -> dict:
    """Send an iMessage into a chat by its identifier — a group (``chat…``,
    32 hex, ``any;+;…``), a 1:1 chat, or a phone/email (``send_to_chat``).

    Returns ``{"success": True, "chat_identifier", "message", …}`` or
    ``{"success": False, "error", …}``."""
    if not is_available():
        return {"success": False, "error": "iMessage not available"}
    result = send_to_chat(chat_identifier, message)
    if not result["success"]:
        return {**result, "success": False, "error": result.get("error") or "Unknown error sending iMessage to chat"}
    return {**result, "chat_identifier": chat_identifier, "message": "iMessage sent to the chat successfully"}


def get_recent_messages(contact: Optional[str] = None, limit: int = 10) -> dict:
    """
    Get recent iMessages using AppleScript.

    Note: This is limited compared to MCP - AppleScript access to message
    history is restricted. For full history, would need to query the
    chat.db SQLite database directly (requires Full Disk Access).

    Args:
        contact: Optional contact to filter by
        limit: Number of messages to retrieve

    Returns:
        dict with messages or error
    """
    if not is_available():
        return {"success": False, "error": "iMessage not available"}

    # AppleScript to get recent chats (limited functionality)
    script = '''
    tell application "Messages"
        set chatList to {}
        repeat with c in (chats)
            set end of chatList to {chatName: name of c, chatId: id of c}
        end repeat
        return chatList
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return {
                "success": True,
                "chats": result.stdout.strip(),
                "note": "Full message history requires direct database access"
            }
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or "Could not retrieve messages"
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_status() -> dict:
    """
    Get the current status of the iMessage AppleScript service.

    Returns:
        Dict with status info: status, status_message, metadata
    """
    import os as _os

    if not IMESSAGE_ENABLED:
        return {
            "status": "error",
            "status_message": "Set IMESSAGE_ENABLED=true in environment",
            "metadata": None
        }

    # Check if we're on macOS
    if _os.uname().sysname != "Darwin":
        return {
            "status": "error",
            "status_message": "Not running on macOS",
            "metadata": None
        }

    # Try to verify Messages.app is accessible
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Messages" to return name'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return {
                "status": "connected",
                "status_message": "Messages.app accessible",
                "metadata": None
            }
        else:
            return {
                "status": "error",
                "status_message": result.stderr.strip() or "Messages.app not accessible",
                "metadata": None
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "status_message": "Timeout checking Messages.app",
            "metadata": None
        }
    except Exception as e:
        return {
            "status": "error",
            "status_message": str(e),
            "metadata": None
        }
