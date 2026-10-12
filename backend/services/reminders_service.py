"""The owner's Reminders, read from Reminders' own databases (2026-10-11).

Why: Edward had no connection to Reminders at all — apple-mcp's JXA path
pops a two-minute consent dialog under Edward's identity and was never bound
to the model (docs/architecture/integrations.md). Reminders
keeps one Core Data store per account in ``~/Library/Group Containers/
group.com.apple.reminders/Container_v1/Stores/Data-*.sqlite``; the backend
already holds Full Disk Access (it reads ``chat.db`` and Calendar's database
the same way), so this module reads every store in-process, read-only, and
merges them. No new permission, no AppleScript, no MCP. Writes go through
the EventKit helper (``eventkit_service``), never here.

The encoding (checked on this Mac 2026-10-11 by ``PRAGMA table_info`` and
aggregate counts only, no reminder content):

* ``ZREMCDREMINDER`` holds reminders: ``ZTITLE``, ``ZNOTES``, ``ZCOMPLETED``,
  ``ZMARKEDFORDELETION``, ``ZDUEDATE`` (Core Data seconds since 2001-01-01
  UTC), ``ZALLDAY``, ``ZFLAGGED``, ``ZPRIORITY``, ``ZLIST`` (→
  ``ZREMCDBASELIST.Z_PK``, whose ``ZNAME`` is the list) and three
  identifiers: ``ZCKIDENTIFIER`` (CloudKit; NULL in an Exchange/CalDAV
  account's store), ``ZDACALENDARITEMUNIQUEIDENTIFIER`` (CalDAV) and
  ``ZIDENTIFIER`` (a 16-byte UUID). All three ride as hints to the helper,
  which matches whichever EventKit calls ``calendarItemIdentifier``.
* An all-day due date is floating: the stored instant is midnight UTC of
  the day, so its UTC date is the owner's date (west of UTC the instant is
  the evening before).

The schema is private and may change with a macOS update, so the required
columns are checked with ``PRAGMA table_info`` before every read, and every
failure raises ``RemindersUnavailable`` (never an empty list passed off as
the truth). ``EDWARD_REMINDERS_SQLITE=off`` turns it off; ``EDWARD_REMINDERS_DIR``
points elsewhere (tests).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
# ADAPT: Reminders are read straight from macOS Reminders' private Core Data stores.
#   How it works here: the backend's python holds Full Disk Access, so it opens every Data-*.sqlite
#   store read-only; the schema is undocumented and checked on one macOS version.
#   To adapt: grant Full Disk Access to the python that runs the backend, or set EDWARD_REMINDERS_SQLITE=off;
#   EDWARD_REMINDERS_DIR points at another stores directory.
DEFAULT_DIR = "~/Library/Group Containers/group.com.apple.reminders/Container_v1/Stores"
FLAG = "EDWARD_REMINDERS_SQLITE"
OPEN_TIMEOUT_S = 2.0
MAX_ROWS = 2000

REQUIRED: Dict[str, Tuple[str, ...]] = {
    "ZREMCDREMINDER": ("Z_PK", "ZTITLE", "ZNOTES", "ZCOMPLETED", "ZMARKEDFORDELETION", "ZDUEDATE", "ZALLDAY",
                       "ZLIST", "ZCKIDENTIFIER", "ZFLAGGED", "ZPRIORITY"),
    "ZREMCDBASELIST": ("Z_PK", "ZNAME", "ZMARKEDFORDELETION"),
}
OPTIONAL = ("ZDACALENDARITEMUNIQUEIDENTIFIER", "ZIDENTIFIER", "ZPARENTREMINDER")


class RemindersUnavailable(RuntimeError):
    """The owner's Reminders can't be read right now (and why)."""


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    notes: str
    list: str
    due: Optional[datetime]   # aware, the owner's zone; an all-day one at local midnight of its date
    all_day: bool
    flagged: bool
    priority: int
    hints: Dict[str, Any] = field(default_factory=dict)


# ---- seams --------------------------------------------------------------------------------

def _user_tz():
    from services.timezone_utils import get_user_tz

    return get_user_tz()


def enabled() -> bool:
    return (os.getenv(FLAG) or "on").strip().lower() not in ("off", "0", "false", "no")


def stores_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("EDWARD_REMINDERS_DIR") or DEFAULT_DIR))


def store_paths() -> List[Path]:
    return sorted(p for p in stores_dir().glob("Data-*.sqlite") if p.is_file())


_status: Dict[str, Any] = {}


def _note(source: str, error: Optional[str] = None, stores: Optional[int] = None) -> None:
    _status.update(source=source, at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   error=(error or None), stores=stores)


def status() -> Dict[str, Any]:
    """Which source served Reminders last (sqlite | unavailable | unread) —
    the health report's ``calendar.reminders`` line."""
    out: Dict[str, Any] = {"source": _status.get("source") or "unread", "at": _status.get("at"),
                           "enabled": enabled()}
    if _status.get("stores") is not None:
        out["stores"] = _status["stores"]
    if _status.get("error"):
        out["error"] = _status["error"]
    return out


# ---- reading ---------------------------------------------------------------------------------

def _connect(path: Path, immutable: bool) -> sqlite3.Connection:
    flag = "immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"file:{quote(str(path))}?{flag}", uri=True, timeout=1.0, check_same_thread=False)


def _columns(con: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _select(con: sqlite3.Connection, where: str, params: tuple) -> List[tuple]:
    have = {t: _columns(con, t) for t in REQUIRED}
    for table, needed in REQUIRED.items():
        if not have[table]:
            raise RemindersUnavailable(f"Reminders' schema changed: no {table} table")
        missing = [c for c in needed if c not in have[table]]
        if missing:
            raise RemindersUnavailable(f"Reminders' schema changed: {table} has no {', '.join(missing)}")
    optional = ", ".join(f"r.{c}" if c in have["ZREMCDREMINDER"] else f"NULL AS {c}" for c in OPTIONAL)
    sql = f"""
        SELECT r.Z_PK, r.ZTITLE, r.ZNOTES, r.ZDUEDATE, r.ZALLDAY, r.ZFLAGGED, r.ZPRIORITY, r.ZCKIDENTIFIER,
               {optional}, l.ZNAME
          FROM ZREMCDREMINDER r
          LEFT JOIN ZREMCDBASELIST l ON l.Z_PK = r.ZLIST
         WHERE COALESCE(r.ZMARKEDFORDELETION, 0) = 0
           AND COALESCE(l.ZMARKEDFORDELETION, 0) = 0
           AND ({where})
         LIMIT {MAX_ROWS}
    """
    return con.execute(sql, params).fetchall()


def _select_lists(con: sqlite3.Connection, where: str, params: tuple) -> List[tuple]:
    """Every list's name not marked for deletion (``where`` is unused)."""
    have = _columns(con, "ZREMCDBASELIST")
    if not have:
        raise RemindersUnavailable("Reminders' schema changed: no ZREMCDBASELIST table")
    missing = [c for c in REQUIRED["ZREMCDBASELIST"] if c not in have]
    if missing:
        raise RemindersUnavailable(f"Reminders' schema changed: ZREMCDBASELIST has no {', '.join(missing)}")
    return con.execute("SELECT ZNAME FROM ZREMCDBASELIST WHERE COALESCE(ZMARKEDFORDELETION, 0) = 0 "
                       f"AND ZNAME IS NOT NULL LIMIT {MAX_ROWS}").fetchall()


def _query(path: Path, where: str, params: tuple, select=_select) -> Tuple[List[tuple], bool]:
    """(rows, stale), like ``calendar_service._query``: ``mode=ro`` sees the
    WAL; ``immutable=1`` (only when ro can't open) does not, so its answer
    is marked stale."""
    stale = False
    try:
        con = _connect(path, immutable=False)
        try:
            return select(con, where, params), False
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "unable to open" not in msg and "readonly" not in msg and "locked" not in msg:
            raise RemindersUnavailable(f"can't read {path.name} ({exc})") from exc
        stale = True
    except sqlite3.DatabaseError as exc:
        raise RemindersUnavailable(f"can't read {path.name} ({exc})") from exc
    try:
        con = _connect(path, immutable=True)
        try:
            return select(con, where, params), stale
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise RemindersUnavailable(f"can't open {path.name} ({exc})") from exc


def _from_cd(seconds: Any) -> datetime:
    return CORE_DATA_EPOCH + timedelta(seconds=float(seconds))


def _ident(value: Any) -> Optional[str]:
    """``ZIDENTIFIER`` is 16 bytes of UUID; EventKit spells UUIDs upper-case."""
    if isinstance(value, (bytes, bytearray, memoryview)) and len(bytes(value)) == 16:
        return str(uuid.UUID(bytes=bytes(value))).upper()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _reminder(row: tuple, tz) -> Reminder:
    (_pk, title, notes, due, all_day, flagged, priority, ck, da, ident, _parent, list_name) = row
    ids = [i for i in (ck, da, _ident(ident)) if isinstance(i, str) and i.strip()]
    ids = list(dict.fromkeys(i.strip() for i in ids))
    is_all_day = bool(all_day)
    when: Optional[datetime] = None
    if due is not None:
        instant = _from_cd(due)
        if is_all_day:
            day = instant.astimezone(timezone.utc).date()
            when = datetime(day.year, day.month, day.day, tzinfo=tz)
        else:
            when = instant.astimezone(tz)
    title_s = str(title or "").strip() or "(untitled)"
    list_s = str(list_name or "")
    return Reminder(id=ids[0] if ids else f"pk-{_pk}", title=title_s, notes=str(notes or ""), list=list_s,
                    due=when, all_day=is_all_day, flagged=bool(flagged), priority=int(priority or 0),
                    hints={"ids": ids, "title": title_s, "list": list_s})


async def _read(where: str, params: tuple) -> List[Reminder]:
    rows = await _read_rows(where, params, _select)
    tz = _user_tz()
    return [_reminder(r, tz) for r in rows]


async def _read_rows(where: str, params: tuple, select) -> List[tuple]:
    if not enabled():
        raise RemindersUnavailable(f"reading Reminders' database is off ({FLAG}=off)")
    paths = store_paths()
    if not paths:
        _note("unavailable", "no Reminders database")
        raise RemindersUnavailable(f"no Reminders database in {stores_dir()}")

    def read_all() -> List[tuple]:
        rows: List[tuple] = []
        for p in paths:
            got, _stale = _query(p, where, params, select)
            rows.extend(got)
        return rows

    try:
        rows = await asyncio.wait_for(asyncio.to_thread(read_all), OPEN_TIMEOUT_S)
    except asyncio.TimeoutError:
        _note("unavailable", "timed out")
        raise RemindersUnavailable(f"Reminders' database took longer than {OPEN_TIMEOUT_S:g}s") from None
    except RemindersUnavailable as exc:
        _note("unavailable", str(exc)[:200])
        raise
    _note("sqlite", stores=len(paths))
    return rows


def _sort_key(r: Reminder):
    far = datetime.max.replace(tzinfo=timezone.utc)
    return (r.due is None, r.due.astimezone(timezone.utc) if r.due else far, r.title.lower())


async def open_reminders(list_name: Optional[str] = None, query: Optional[str] = None,
                         limit: Optional[int] = 60) -> List[Reminder]:
    """The owner's open reminders across every store, soonest due first (no due
    date last). ``list_name`` matches a list case-insensitively — exactly
    when one is called that, else any list whose name contains it;
    ``query`` matches the title or notes. Raises ``RemindersUnavailable``,
    never answers [] for "can't read"."""
    items = await _read("COALESCE(r.ZCOMPLETED, 0) = 0", ())
    if list_name and list_name.strip():
        want = list_name.strip().lower()
        exact = [r for r in items if r.list.lower() == want]
        items = exact or [r for r in items if want in r.list.lower()]
    if query and query.strip():
        q = query.strip().lower()
        items = [r for r in items if q in r.title.lower() or q in r.notes.lower()]
    items.sort(key=_sort_key)
    return items[:limit] if limit else items


async def list_names() -> List[str]:
    """Every Reminders list's name across the stores, sorted, once each —
    so a read of a list that doesn't exist says so (review 2026-10-11).
    Raises ``RemindersUnavailable``."""
    rows = await _read_rows("1 = 1", (), _select_lists)
    names: Dict[str, str] = {}
    for (name,) in rows:
        text = str(name or "").strip()
        if text:
            names.setdefault(text.casefold(), text)
    return sorted(names.values(), key=str.casefold)


async def reminder_by_id(ck: str) -> Optional[Reminder]:
    """The reminder with this identifier — CloudKit, CalDAV or its UUID —
    finished or not; None when no store has it. Raises
    ``RemindersUnavailable``."""
    key = (ck or "").strip()
    if not key:
        return None
    wanted = {key.lower()}
    try:
        wanted.add(str(uuid.UUID(key)).lower())
    except ValueError:
        pass
    # The optional identifier columns may be absent, so match in Python over
    # every reminder not deleted (a few hundred at most).
    rows = await _read("1 = 1", ())
    for r in rows:
        if any(i.lower() in wanted for i in r.hints.get("ids") or []):
            return r
    return None
