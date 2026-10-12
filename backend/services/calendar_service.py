"""The owner's calendar, read from Calendar's own database (2026-10-11).

Why: apple-mcp's calendar call — a bun process driving Calendar over
AppleScript — timed out 201 times in 8.4 days, and each timeout made a
wake's pack say "calendar unreadable". macOS keeps the
calendar in ``~/Library/Group Containers/group.com.apple.calendar/
Calendar.sqlitedb``; the backend already holds Full Disk Access (it reads
``chat.db`` the same way), so this module reads that file in-process,
read-only, in milliseconds. No new permission, no AppleScript, no MCP.

The encoding (checked on this Mac by schema and aggregate counts only):

* times are Core Data seconds since 2001-01-01 UTC;
* ``OccurrenceCache`` holds the expanded occurrences of every event,
  recurring ones included, **one row per day an occurrence touches**: the
  first row has ``occurrence_start_date`` NULL and ``occurrence_date`` =
  the start; later rows have ``occurrence_date`` = that day's start and the
  real start in ``occurrence_start_date``. So the start is
  ``COALESCE(occurrence_start_date, occurrence_date)`` and an occurrence is
  unique by (event, start);
* all-day occurrences run from local midnight to the last second of their
  last day; they are shown on the owner's dates, midnight to midnight.

The schema is private and may change with a macOS update, so every
failure raises ``CalendarUnavailable`` (never an empty list as if it were
the truth) and ``display_service`` falls back to apple-mcp, then to its last
good list. ``status()`` says which source served last (the health report's
``calendar`` line). ``EDWARD_CALENDAR_SQLITE=off`` goes back to apple-mcp
only; ``EDWARD_CALENDAR_DB`` points elsewhere (tests).

Read-only: this module never writes the owner's calendar. Changes the owner asks for go
through the EventKit helper (``eventkit_service``, 2026-10-11); ``event_by_id``
re-reads the event a ``cal-<UUID>-<occurrence>`` id names, so the helper gets
its title and occurrence as hints.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
# ADAPT: the calendar is read straight from macOS Calendar's private SQLite store.
#   How it works here: the backend's python holds Full Disk Access (granted in System Settings), so it
#   can open this file read-only; the schema is undocumented and checked on one macOS version.
#   To adapt: grant Full Disk Access to the python that runs the backend, or set EDWARD_CALENDAR_SQLITE=off
#   to use apple-mcp only; EDWARD_CALENDAR_DB points at another copy of the store.
DEFAULT_DB = "~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb"
FLAG = "EDWARD_CALENDAR_SQLITE"
OPEN_TIMEOUT_S = 2.0
MAX_ROWS = 500
CANCELLED = 3

_SQL = f"""
SELECT o.event_id,
       COALESCE(o.occurrence_start_date, o.occurrence_date) AS s,
       MAX(o.occurrence_end_date) AS e,
       i.summary, COALESCE(i.all_day, 0), c.title, l.title, i.UUID
  FROM OccurrenceCache o
  JOIN CalendarItem i ON i.ROWID = o.event_id
  LEFT JOIN Calendar c ON c.ROWID = i.calendar_id
  LEFT JOIN Location l ON l.ROWID = i.location_id
 WHERE COALESCE(o.occurrence_start_date, o.occurrence_date) < :end
   AND o.occurrence_end_date > :start
   AND o.next_reminder_date IS NULL
   AND COALESCE(i.hidden, 0) = 0
   AND COALESCE(i.status, 0) != {CANCELLED}
 GROUP BY o.event_id, s
 ORDER BY s, i.summary
 LIMIT {MAX_ROWS}
"""


class CalendarUnavailable(RuntimeError):
    """The calendar database can't be read right now (and why)."""


@dataclass(frozen=True)
class CalEvent:
    id: str
    title: str
    start: datetime          # aware, the owner's zone
    end: datetime
    all_day: bool
    location: str = ""
    calendar: str = ""

    def as_display(self) -> Dict[str, Any]:
        """The shape ``display_service`` serves (``/api/display/calendar``,
        the mind's pack, the pulse's signals, meeting prep)."""
        from services.display_service import _kind_for_event

        return {"id": self.id, "title": self.title, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "where": self.location, "kind": _kind_for_event(self.title), "all_day": self.all_day,
                "calendar": self.calendar}

    def line(self) -> str:
        if self.all_day:
            days = (self.end.date() - self.start.date()).days
            when = self.start.strftime("%a %b %-d") + (f" (+{days - 1} days)" if days > 1 else "") + ", all day"
        else:
            when = (self.start.strftime("%a %b %-d, %-I:%M %p").replace("AM", "am").replace("PM", "pm")
                    + "–" + self.end.strftime("%-I:%M %p").replace("AM", "am").replace("PM", "pm"))
        bits = [when, self.title]
        if self.location:
            bits.append(f"@ {self.location}")
        if self.calendar:
            bits.append(f"[{self.calendar}]")
        return " · ".join(bits)


# ---- seams --------------------------------------------------------------------------------

def _user_tz():
    from services.timezone_utils import get_user_tz

    return get_user_tz()


def _now() -> datetime:
    return datetime.now(_user_tz())


def enabled() -> bool:
    return (os.getenv(FLAG) or "on").strip().lower() not in ("off", "0", "false", "no")


def db_path() -> Path:
    return Path(os.path.expanduser(os.getenv("EDWARD_CALENDAR_DB") or DEFAULT_DB))


_status: Dict[str, Any] = {}


def note_source(source: str, error: Optional[str] = None) -> None:
    """Which source served the calendar last: sqlite | mcp | unavailable."""
    _status.update(source=source, at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   error=(error or None))


def status() -> Dict[str, Any]:
    out = {"source": _status.get("source") or "unread", "at": _status.get("at"), "enabled": enabled()}
    if _status.get("error"):
        out["error"] = _status["error"]
    return out


# ---- reading ---------------------------------------------------------------------------------

def _cd(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_user_tz())
    return (dt - CORE_DATA_EPOCH).total_seconds()


def _from_cd(seconds: float) -> datetime:
    return CORE_DATA_EPOCH + timedelta(seconds=float(seconds))


def _connect(path: Path, immutable: bool) -> sqlite3.Connection:
    flag = "immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"file:{quote(str(path))}?{flag}", uri=True, timeout=1.0, check_same_thread=False)


def _query(path: Path, start: float, end: float) -> Tuple[List[tuple], bool]:
    """(rows, stale). ``mode=ro`` sees the WAL; ``immutable=1`` (only when
    ro can't open) does not, so its answer is marked stale."""
    return _execute(path, _SQL, {"start": start, "end": end})


def _execute(path: Path, sql: str, params: Dict[str, Any]) -> Tuple[List[tuple], bool]:
    stale = False
    try:
        con = _connect(path, immutable=False)
        try:
            return con.execute(sql, params).fetchall(), False
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg:
            raise CalendarUnavailable(f"the calendar database's schema changed ({exc})") from exc
        if "unable to open" not in msg and "readonly" not in msg and "locked" not in msg:
            raise CalendarUnavailable(f"can't read the calendar database ({exc})") from exc
        stale = True
    try:
        con = _connect(path, immutable=True)
        try:
            return con.execute(sql, params).fetchall(), stale
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg:
            raise CalendarUnavailable(f"the calendar database's schema changed ({exc})") from exc
        raise CalendarUnavailable(f"can't open the calendar database ({exc})") from exc
    except sqlite3.DatabaseError as exc:
        raise CalendarUnavailable(f"can't read the calendar database ({exc})") from exc


def _event(row: tuple, tz) -> CalEvent:
    event_id, s, e, summary, all_day, cal_title, loc_title, uuid = row
    start = _from_cd(s).astimezone(tz)
    end = _from_cd(e if e is not None else s).astimezone(tz)
    is_all_day = bool(all_day)
    if is_all_day:
        first = start.date()
        last = (end - timedelta(seconds=1)).date() if end > start else first
        if end.time() != datetime.min.time():
            last = end.date()
        start = datetime(first.year, first.month, first.day, tzinfo=tz)
        nxt = last + timedelta(days=1)
        end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return CalEvent(id=f"cal-{uuid or event_id}-{int(s)}", title=str(summary or "(untitled)"),
                    start=start, end=end, all_day=is_all_day, location=str(loc_title or ""),
                    calendar=str(cal_title or ""))


async def read_between(start: datetime, end: datetime, query: Optional[str] = None) -> Tuple[List[CalEvent], bool]:
    """(events overlapping [start, end), stale). Raises CalendarUnavailable."""
    if not enabled():
        raise CalendarUnavailable(f"reading the calendar database is off ({FLAG}=off)")
    path = db_path()
    if not path.exists():
        note_source("unavailable", "no calendar database")
        raise CalendarUnavailable(f"no calendar database at {path}")
    t0 = time.monotonic()
    try:
        rows, stale = await asyncio.wait_for(asyncio.to_thread(_query, path, _cd(start), _cd(end)), OPEN_TIMEOUT_S)
    except asyncio.TimeoutError:
        note_source("unavailable", "timed out")
        raise CalendarUnavailable(f"the calendar database took longer than {OPEN_TIMEOUT_S:g}s") from None
    except CalendarUnavailable as exc:
        note_source("unavailable", str(exc)[:200])
        raise
    tz = _user_tz()
    events = [_event(r, tz) for r in rows]
    if query:
        q = query.strip().lower()
        events = [e for e in events if q in e.title.lower() or q in e.location.lower() or q in e.calendar.lower()]
    if (time.monotonic() - t0) > 1.0:
        print(f"[calendar] sqlite read took {time.monotonic() - t0:.1f}s")
    return events, stale


_BY_ID_SQL = f"""
SELECT o.event_id,
       COALESCE(o.occurrence_start_date, o.occurrence_date) AS s,
       MAX(o.occurrence_end_date) AS e,
       i.summary, COALESCE(i.all_day, 0), c.title, l.title, i.UUID
  FROM CalendarItem i
  JOIN OccurrenceCache o ON o.event_id = i.ROWID
  LEFT JOIN Calendar c ON c.ROWID = i.calendar_id
  LEFT JOIN Location l ON l.ROWID = i.location_id
 WHERE i.UUID = :uuid
   AND o.next_reminder_date IS NULL
   AND (:cd IS NULL OR COALESCE(o.occurrence_start_date, o.occurrence_date) = :cd)
 GROUP BY o.event_id, s
 ORDER BY CASE WHEN s >= :now THEN 0 ELSE 1 END, ABS(s - :now)
 LIMIT 1
"""
_ITEM_SQL = """
SELECT i.ROWID, i.start_date, i.end_date, i.summary, COALESCE(i.all_day, 0), c.title, l.title, i.UUID
  FROM CalendarItem i
  LEFT JOIN Calendar c ON c.ROWID = i.calendar_id
  LEFT JOIN Location l ON l.ROWID = i.location_id
 WHERE i.UUID = :uuid
 LIMIT 1
"""


async def event_by_id(uuid: str, cd: Optional[int] = None) -> Optional[CalEvent]:
    """The event ``schedule``'s calendar read printed as ``cal-<uuid>-<cd>``:
    that occurrence (Core Data seconds), or with no ``cd`` the next one from
    now (else the latest), else the item itself when its occurrences aren't
    cached. Read-only, for the EventKit helper's hints (title, occurrence).
    None when there is no such event; raises ``CalendarUnavailable``."""
    if not enabled():
        raise CalendarUnavailable(f"reading the calendar database is off ({FLAG}=off)")
    path = db_path()
    if not path.exists():
        raise CalendarUnavailable(f"no calendar database at {path}")
    params = {"uuid": str(uuid or ""), "cd": cd, "now": _cd(_now())}

    def read() -> Optional[tuple]:
        rows, _stale = _execute(path, _BY_ID_SQL, params)
        if rows:
            return rows[0]
        if cd is not None:
            return None
        rows, _stale = _execute(path, _ITEM_SQL, {"uuid": params["uuid"]})
        return rows[0] if rows and rows[0][1] is not None else None

    try:
        row = await asyncio.wait_for(asyncio.to_thread(read), OPEN_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise CalendarUnavailable(f"the calendar database took longer than {OPEN_TIMEOUT_S:g}s") from None
    return _event(row, _user_tz()) if row else None


async def events_between(start: datetime, end: datetime, query: Optional[str] = None) -> List[CalEvent]:
    """The owner's events overlapping [start, end), in their zone, all-day
    ones on their dates. Raises ``CalendarUnavailable``, never answers [] for "can't
    read"."""
    events, _stale = await read_between(start, end, query)
    return events


def day_bounds(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    now = now or _now()
    tz = _user_tz()
    local = now.astimezone(tz)
    start = datetime(local.year, local.month, local.day, tzinfo=tz)
    nxt = start.date() + timedelta(days=1)
    return start, datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)


async def today_events() -> List[CalEvent]:
    start, end = day_bounds()
    return await events_between(start, end)


async def today_display_events() -> Tuple[List[Dict[str, Any]], bool]:
    """Today's events in ``display_service``'s shape, and whether the read
    was stale (an immutable read that misses the WAL)."""
    start, end = day_bounds()
    events, stale = await read_between(start, end)
    return [e.as_display() for e in events], stale
