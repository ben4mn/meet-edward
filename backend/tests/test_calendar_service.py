"""Ellis's calendar read straight from Calendar's own database (AREA 2,
2026-10-11; tools-spec §4.4): apple-mcp's calendar call timed out 201
times in 8.4 days and every wake's pack read "calendar unreadable".

The fixture is a SQLite file built here with the four tables and the
encodings this Mac's Calendar uses (checked 2026-10-11 by schema and
aggregate counts only, no event content): Core Data seconds since
2001-01-01; ``OccurrenceCache`` holds one row per day an occurrence
touches — the first with ``occurrence_start_date`` NULL, the following
days with ``occurrence_date`` at that day's start and the real start in
``occurrence_start_date``; all-day occurrences run local midnight to the
last second of their last day. No test reads Ellis's real calendar
(conftest turns the sqlite path off; these tests point it at the fixture).
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import calendar_service as cs

pytestmark = pytest.mark.unit

CHI = ZoneInfo("America/Chicago")
EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def cd(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds()


def build_db(path, rows):
    """rows: dicts with summary, start, end (aware), all_day, calendar,
    location, hidden, status; multi-day occurrences get continuation rows."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE Calendar (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER, title TEXT);
        CREATE TABLE Location (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, address TEXT);
        CREATE TABLE CalendarItem (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, summary TEXT, location_id INTEGER,
            start_date REAL, end_date REAL, all_day INTEGER, calendar_id INTEGER, status INTEGER, hidden INTEGER,
            UUID TEXT, entity_type INTEGER);
        CREATE TABLE OccurrenceCache (day REAL, event_id INTEGER, calendar_id INTEGER, store_id INTEGER,
            occurrence_date REAL, occurrence_start_date REAL, occurrence_end_date REAL,
            latest_possible_alarm REAL, earliest_possible_alarm REAL, next_reminder_date REAL);
    """)
    cals = {}
    for r in rows:
        cal = r.get("calendar", "Home")
        if cal not in cals:
            cals[cal] = con.execute("INSERT INTO Calendar (title) VALUES (?)", (cal,)).lastrowid
        loc_id = None
        if r.get("location"):
            loc_id = con.execute("INSERT INTO Location (title) VALUES (?)", (r["location"],)).lastrowid
        item = con.execute(
            "INSERT INTO CalendarItem (summary, location_id, start_date, end_date, all_day, calendar_id, status,"
            " hidden, UUID, entity_type) VALUES (?,?,?,?,?,?,?,?,?,2)",
            (r["summary"], loc_id, cd(r["start"]), cd(r["end"]), 1 if r.get("all_day") else 0, cals[cal],
             r.get("status", 0), r.get("hidden", 0), f"uuid-{r['summary']}")).lastrowid
        start, end = r["start"], r["end"]
        day = start.astimezone(CHI).replace(hour=0, minute=0, second=0, microsecond=0)
        first = True
        while day.astimezone(timezone.utc) < end:
            occ_date = start if first else day
            con.execute(
                "INSERT INTO OccurrenceCache (day, event_id, calendar_id, store_id, occurrence_date,"
                " occurrence_start_date, occurrence_end_date) VALUES (?,?,?,?,?,?,?)",
                (cd(day), item, cals[cal], 1, cd(occ_date), None if first else cd(start), cd(end)))
            first = False
            day = (day + timedelta(days=1, hours=2)).replace(hour=0)
    con.commit()
    con.close()


@pytest.fixture
def calendar_db(tmp_path, monkeypatch):
    path = tmp_path / "Calendar.sqlitedb"
    monkeypatch.setenv("EDWARD_CALENDAR_DB", str(path))
    monkeypatch.setenv("EDWARD_CALENDAR_SQLITE", "on")
    monkeypatch.setattr(cs, "_user_tz", lambda: CHI)
    cs._status.clear()
    return path


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CHI)


DAY_START = at(2026, 10, 11)
DAY_END = at(2026, 10, 12)


class TestEventsBetween:
    async def test_timed_events_in_owners_zone(self, calendar_db):
        build_db(calendar_db, [
            {"summary": "Vet — Biscuit", "start": at(2026, 10, 11, 9, 30), "end": at(2026, 10, 11, 10, 0),
             "location": "Lakeview Animal Hospital", "calendar": "Home"},
            {"summary": "Tomorrow thing", "start": at(2026, 10, 12, 9), "end": at(2026, 10, 12, 10)},
        ])
        events = await cs.events_between(DAY_START, DAY_END)
        assert [e.title for e in events] == ["Vet — Biscuit"]
        e = events[0]
        assert e.start == at(2026, 10, 11, 9, 30) and e.end == at(2026, 10, 11, 10, 0)
        assert e.start.tzinfo is not None and e.location == "Lakeview Animal Hospital" and e.calendar == "Home"
        assert e.all_day is False

    async def test_a_multi_day_event_is_one_occurrence(self, calendar_db):
        build_db(calendar_db, [
            {"summary": "Lake weekend", "start": at(2026, 10, 9, 18), "end": at(2026, 10, 12, 1)},
        ])
        events = await cs.events_between(DAY_START, DAY_END)
        assert len(events) == 1
        assert events[0].start == at(2026, 10, 9, 18) and events[0].end == at(2026, 10, 12, 1)

    async def test_all_day_events_float_on_owners_dates(self, calendar_db):
        build_db(calendar_db, [
            {"summary": "Ellis's birthday", "start": at(2026, 10, 11), "end": at(2026, 10, 11, 23, 59) + timedelta(seconds=59),
             "all_day": True, "calendar": "Birthdays"},
        ])
        events = await cs.events_between(DAY_START, DAY_END)
        assert len(events) == 1 and events[0].all_day is True
        assert events[0].start == at(2026, 10, 11) and events[0].end == at(2026, 10, 12)

    async def test_hidden_and_cancelled_events_are_left_out(self, calendar_db):
        build_db(calendar_db, [
            {"summary": "Hidden", "start": at(2026, 10, 11, 8), "end": at(2026, 10, 11, 9), "hidden": 1},
            {"summary": "Cancelled", "start": at(2026, 10, 11, 8), "end": at(2026, 10, 11, 9), "status": 3},
            {"summary": "Kept", "start": at(2026, 10, 11, 8), "end": at(2026, 10, 11, 9)},
        ])
        assert [e.title for e in await cs.events_between(DAY_START, DAY_END)] == ["Kept"]

    async def test_the_query_filters_title_location_and_calendar(self, calendar_db):
        build_db(calendar_db, [
            {"summary": "Standup", "start": at(2026, 10, 11, 9), "end": at(2026, 10, 11, 9, 15), "calendar": "Work"},
            {"summary": "Dinner", "start": at(2026, 10, 11, 19), "end": at(2026, 10, 11, 21), "location": "Avec"},
        ])
        assert [e.title for e in await cs.events_between(DAY_START, DAY_END, query="avec")] == ["Dinner"]
        assert [e.title for e in await cs.events_between(DAY_START, DAY_END, query="WORK")] == ["Standup"]

    async def test_display_shape(self, calendar_db):
        build_db(calendar_db, [
            {"summary": "Interview", "start": at(2026, 10, 11, 14), "end": at(2026, 10, 11, 15), "location": "Zoom"},
        ])
        rows = [e.as_display() for e in await cs.events_between(DAY_START, DAY_END)]
        assert rows[0]["title"] == "Interview" and rows[0]["where"] == "Zoom" and rows[0]["kind"] == "work"
        assert rows[0]["all_day"] is False and rows[0]["start"].startswith("2026-10-11T14:00:00-05:00")
        assert rows[0]["id"]


class TestUnavailable:
    async def test_missing_file(self, calendar_db):
        with pytest.raises(cs.CalendarUnavailable, match="no calendar database"):
            await cs.events_between(DAY_START, DAY_END)
        assert cs.status()["source"] == "unavailable"

    async def test_schema_change(self, calendar_db):
        con = sqlite3.connect(calendar_db)
        con.execute("CREATE TABLE CalendarItem (ROWID INTEGER PRIMARY KEY, summary TEXT)")
        con.commit()
        con.close()
        with pytest.raises(cs.CalendarUnavailable, match="schema"):
            await cs.events_between(DAY_START, DAY_END)

    async def test_flag_off(self, calendar_db, monkeypatch):
        monkeypatch.setenv("EDWARD_CALENDAR_SQLITE", "off")
        with pytest.raises(cs.CalendarUnavailable, match="off"):
            await cs.events_between(DAY_START, DAY_END)

    async def test_an_immutable_read_is_marked_stale(self, calendar_db, monkeypatch):
        build_db(calendar_db, [{"summary": "X", "start": at(2026, 10, 11, 9), "end": at(2026, 10, 11, 10)}])
        real = cs._connect

        def flaky(path, immutable):
            if not immutable:
                raise sqlite3.OperationalError("unable to open database file")
            return real(path, immutable)
        monkeypatch.setattr(cs, "_connect", flaky)
        events, stale = await cs.read_between(DAY_START, DAY_END)
        assert [e.title for e in events] == ["X"] and stale is True


class TestTheDisplayHook:
    """display_service.get_calendar reads the database first, apple-mcp
    when it can't, and keeps the field contract its readers check
    (``source == "apple-mcp"`` means Apple Calendar answered)."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from services import display_service as ds
        ds._calendar_cache.clear()
        ds._calendar_cache.update({"data": None, "fetched_at": 0.0})
        monkeypatch.setattr(ds, "now_local", lambda: at(2026, 10, 11, 12))
        monkeypatch.setattr(cs, "_now", lambda: at(2026, 10, 11, 12))

    async def test_sqlite_first(self, calendar_db, monkeypatch):
        from services import display_service as ds
        build_db(calendar_db, [{"summary": "Vet", "start": at(2026, 10, 11, 9), "end": at(2026, 10, 11, 10)}])

        async def mcp():
            raise AssertionError("apple-mcp should not be asked")
        monkeypatch.setattr(ds, "_calendar_via_mcp", mcp)
        cal = await ds.get_calendar()
        assert cal["source"] == "apple-mcp" and cal["via"] == "sqlite"
        assert [e["title"] for e in cal["events"]] == ["Vet"] and not cal.get("stale")
        assert cs.status()["source"] == "sqlite"

    async def test_mcp_when_the_database_cannot_be_read(self, calendar_db, monkeypatch):
        from services import display_service as ds

        async def mcp():
            return [{"id": "m1", "title": "From MCP", "start": "2026-10-11T09:00:00-05:00",
                     "end": "2026-10-11T10:00:00-05:00", "where": "", "kind": "personal", "all_day": False}]
        monkeypatch.setattr(ds, "_calendar_via_mcp", mcp)
        cal = await ds.get_calendar()
        assert cal["source"] == "apple-mcp" and cal["via"] == "mcp" and cal["events"][0]["title"] == "From MCP"
        assert cs.status()["source"] == "mcp"

    async def test_neither_is_unreadable_not_empty(self, calendar_db, monkeypatch):
        from services import display_service as ds

        async def mcp():
            return None

        async def jobs():
            return []
        monkeypatch.setattr(ds, "_calendar_via_mcp", mcp)
        monkeypatch.setattr(ds, "_calendar_fallback_from_events", jobs)
        cal = await ds.get_calendar()
        assert cal["source"] == "scheduled_events"
        assert cs.status()["source"] == "unavailable"


class TestToday:
    async def test_today_is_owners_day(self, calendar_db, monkeypatch):
        monkeypatch.setattr(cs, "_now", lambda: at(2026, 10, 11, 23, 30))
        build_db(calendar_db, [
            {"summary": "Late", "start": at(2026, 10, 11, 23), "end": at(2026, 10, 11, 23, 45)},
            {"summary": "Next day", "start": at(2026, 10, 12, 0, 30), "end": at(2026, 10, 12, 1)},
        ])
        assert [e.title for e in await cs.today_events()] == ["Late"]


class TestHealthLine:
    def test_the_health_report_says_which_source_served(self, calendar_db):
        from services import health_report_service as hr

        cs.note_source("mcp")
        assert hr.calendar_section()["source"] == "mcp"
        cs.note_source("unavailable", "neither answered")
        sec = hr.calendar_section()
        assert sec["source"] == "unavailable" and sec["error"] == "neither answered"
