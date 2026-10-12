"""Read state, tapbacks and inline replies from the owner's chat.db — read-only.

Why (2026-10-11; docs/architecture/noticing.md): the relays that reviews
of past behaviour condemned were texts the owner had already read (the
median read was 2.6 minutes). The noticer's context says "read after 3
min", the gate's settle hold lets that state land, and the outcome
classifier counts the owner's tapback or inline reply on Edward's own
message as acting on it.

chat.db is opened with ``mode=ro`` and ``PRAGMA query_only=ON`` — never
``immutable=1``, which cannot see rows still in the WAL. Every function is
sync (sqlite3), returns ``{}`` when the file or a column is missing, and
never raises; ``a…`` wrappers run them in a thread. ``EDWARD_CHAT_DB``
points it elsewhere (tests use a throwaway file, never the real one).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import quote

logger = logging.getLogger(__name__)

CHAT_DB_ENV = "EDWARD_CHAT_DB"
APPLE_EPOCH_OFFSET = 978307200
TAPBACK_TYPES = range(2000, 2006)      # love, like, dislike, laugh, emphasize, question
MAX_IDS = 500


def chat_db_path() -> str:
    return os.getenv(CHAT_DB_ENV) or os.path.expanduser("~/Library/Messages/chat.db")


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """A read-only connection: ``mode=ro`` and ``query_only``. Raises when
    the file is missing (callers catch)."""
    p = path or chat_db_path()
    if not Path(p).exists():
        raise FileNotFoundError(p)
    conn = sqlite3.connect(f"file:{quote(p)}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def apple_to_datetime(value: Any) -> Optional[datetime]:
    """chat.db's dates: nanoseconds since 2001 (seconds in old rows); 0 or
    NULL is "never"."""
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    seconds = v / 1e9 if v > 10**11 else float(v)
    return datetime.fromtimestamp(seconds + APPLE_EPOCH_OFFSET, tz=timezone.utc).replace(microsecond=0)


def _columns(conn: sqlite3.Connection) -> Set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(message)").fetchall()}


def _clean(values: Iterable[Any], kind: type) -> List[Any]:
    out: List[Any] = []
    for v in values or ():
        try:
            out.append(kind(v))
        except (TypeError, ValueError):
            continue
        if len(out) >= MAX_IDS:
            break
    return list(dict.fromkeys(out))


def read_state(rowids: Iterable[Any], *, path: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
    """``{rowid: {"is_read", "read_at", "from_me"}}`` for the rows that exist."""
    ids = _clean(rowids, int)
    if not ids:
        return {}
    try:
        conn = connect(path)
    except Exception:  # noqa: BLE001
        return {}
    try:
        cols = _columns(conn)
        if not {"is_read", "date_read"} <= cols:
            return {}
        marks = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT ROWID AS rowid, is_read, date_read, is_from_me FROM message "
                            f"WHERE ROWID IN ({marks})", ids).fetchall()
        return {int(r["rowid"]): {"is_read": bool(r["is_read"]), "read_at": apple_to_datetime(r["date_read"]),
                                  "from_me": bool(r["is_from_me"])} for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat.db read state failed: %s", exc)
        return {}
    finally:
        conn.close()


def _target_guid(associated: Optional[str]) -> str:
    """``p:0/<guid>`` / ``bp:<guid>`` → ``<guid>``."""
    a = str(associated or "")
    if "/" in a:
        return a.split("/", 1)[1]
    if ":" in a:
        return a.split(":", 1)[1]
    return a


def reactions_to(guids: Iterable[Any], *, path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Tapbacks (``associated_message_type`` 2000–2005; a removal is 3000+
    and is not one) on each message guid: ``{guid: [{"type", "from_me", "at"}]}``."""
    wanted = _clean(guids, str)
    if not wanted:
        return {}
    try:
        conn = connect(path)
    except Exception:  # noqa: BLE001
        return {}
    try:
        if not {"associated_message_guid", "associated_message_type"} <= _columns(conn):
            return {}
        likes = []
        params: List[str] = []
        for g in wanted:
            likes.append("associated_message_guid LIKE ?")
            params.append(f"%{g}")
        rows = conn.execute(
            "SELECT associated_message_guid AS target, associated_message_type AS t, is_from_me, date FROM message "
            f"WHERE associated_message_type BETWEEN 2000 AND 2005 AND ({' OR '.join(likes)})", params).fetchall()
        out: Dict[str, List[Dict[str, Any]]] = {}
        want = set(wanted)
        for r in rows:
            g = _target_guid(r["target"])
            if g in want:
                out.setdefault(g, []).append({"type": int(r["t"]), "from_me": bool(r["is_from_me"]),
                                              "at": apple_to_datetime(r["date"])})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat.db reactions failed: %s", exc)
        return {}
    finally:
        conn.close()


def replies_to(guids: Iterable[Any], *, path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Inline replies (``thread_originator_guid``) to each message guid:
    ``{guid: [{"rowid", "from_me", "at"}]}``."""
    wanted = _clean(guids, str)
    if not wanted:
        return {}
    try:
        conn = connect(path)
    except Exception:  # noqa: BLE001
        return {}
    try:
        if "thread_originator_guid" not in _columns(conn):
            return {}
        marks = ",".join("?" * len(wanted))
        rows = conn.execute(f"SELECT ROWID AS rowid, thread_originator_guid AS g, is_from_me, date FROM message "
                            f"WHERE thread_originator_guid IN ({marks}) ORDER BY ROWID", wanted).fetchall()
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(str(r["g"]), []).append({"rowid": int(r["rowid"]), "from_me": bool(r["is_from_me"]),
                                                    "at": apple_to_datetime(r["date"])})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat.db replies failed: %s", exc)
        return {}
    finally:
        conn.close()


async def aread_state(rowids: Iterable[Any], *, path: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
    return await asyncio.to_thread(read_state, list(rowids or ()), path=path)


async def areactions_to(guids: Iterable[Any], *, path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    return await asyncio.to_thread(reactions_to, list(guids or ()), path=path)


async def areplies_to(guids: Iterable[Any], *, path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    return await asyncio.to_thread(replies_to, list(guids or ()), path=path)
