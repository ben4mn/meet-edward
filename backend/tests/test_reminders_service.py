"""Ellis's Reminders, read straight from Reminders' own databases (2026-10-11).

There was no connection to Reminders at all: apple-mcp's JXA path pops a
consent dialog under Edward's identity and was never bound to the model
(integrations audit). Reminders keeps one Core Data store per account under
``~/Library/Group Containers/group.com.apple.reminders/Container_v1/Stores/
Data-*.sqlite``; the backend holds Full Disk Access, so ``reminders_service``
reads every store read-only and merges them.

The fixtures are SQLite files built here with the columns this Mac's stores
have (checked 2026-10-11 by ``PRAGMA table_info`` and aggregate counts only,
no reminder content): ``ZREMCDREMINDER`` (title, notes, completed, marked
for deletion, due date in Core Data seconds, all-day, list, the CloudKit and
CalDAV identifiers, a 16-byte ``ZIDENTIFIER``) and ``ZREMCDBASELIST``. No
test reads Ellis's real Reminders (conftest turns the reader off; these tests
point it at the fixture).
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import reminders_service as rs

pytestmark = pytest.mark.unit

CHI = ZoneInfo("America/Chicago")
EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def cd(dt: datetime) -> int:
    return int((dt - EPOCH).total_seconds())


def build_store(path, lists, reminders, *, drop_column=None):
    """lists: names (or (name, marked_for_deletion)); reminders: dicts."""
    reminder_cols = ["Z_PK INTEGER PRIMARY KEY", "Z_ENT INTEGER", "ZALLDAY INTEGER", "ZCOMPLETED INTEGER",
                     "ZFLAGGED INTEGER", "ZMARKEDFORDELETION INTEGER", "ZPRIORITY INTEGER", "ZLIST INTEGER",
                     "ZPARENTREMINDER INTEGER", "ZDUEDATE TIMESTAMP", "ZCKIDENTIFIER VARCHAR",
                     "ZDACALENDARITEMUNIQUEIDENTIFIER VARCHAR", "ZNOTES VARCHAR", "ZTITLE VARCHAR",
                     "ZIDENTIFIER BLOB"]
    if drop_column:
        reminder_cols = [c for c in reminder_cols if not c.startswith(drop_column + " ")]
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ZREMCDBASELIST (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZMARKEDFORDELETION INTEGER,"
                " ZNAME VARCHAR, ZCKIDENTIFIER VARCHAR)")
    con.execute(f"CREATE TABLE ZREMCDREMINDER ({', '.join(reminder_cols)})")
    ids = {}
    for entry in lists:
        name, gone = (entry, 0) if isinstance(entry, str) else entry
        ids[name] = con.execute("INSERT INTO ZREMCDBASELIST (ZNAME, ZMARKEDFORDELETION) VALUES (?, ?)",
                                (name, gone)).lastrowid
    for r in reminders:
        row = {
            "ZTITLE": r["title"], "ZNOTES": r.get("notes"), "ZCOMPLETED": 1 if r.get("completed") else 0,
            "ZMARKEDFORDELETION": 1 if r.get("deleted") else 0, "ZALLDAY": 1 if r.get("all_day") else 0,
            "ZFLAGGED": 1 if r.get("flagged") else 0, "ZPRIORITY": r.get("priority", 0),
            "ZLIST": ids.get(r.get("list", "Reminders")), "ZDUEDATE": r.get("due"),
            "ZCKIDENTIFIER": r.get("ck", f"CK-{r['title']}"),
            "ZDACALENDARITEMUNIQUEIDENTIFIER": r.get("da"),
            "ZIDENTIFIER": r.get("ident"),
        }
        if drop_column:
            row.pop(drop_column, None)
        cols = ", ".join(row)
        con.execute(f"INSERT INTO ZREMCDREMINDER ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    con.commit()
    con.close()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("EDWARD_REMINDERS_DIR", str(tmp_path))
    monkeypatch.setenv("EDWARD_REMINDERS_SQLITE", "on")
    monkeypatch.setattr(rs, "_user_tz", lambda: CHI)
    rs._status.clear()
    return tmp_path


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CHI)


class TestListNames:
    """Every list's name, so a read of a list that doesn't exist says so
    instead of "No open reminders in Groceries" (review 2026-10-11)."""

    async def test_every_list_in_every_store_but_deleted_ones(self, stores):
        build_store(stores / "Data-A.sqlite", ["Pets", "Reminders"], [])
        build_store(stores / "Data-B.sqlite", ["Groceries", ("Gone list", 1), "Pets"], [])
        assert await rs.list_names() == ["Groceries", "Pets", "Reminders"]

    async def test_unreadable_raises(self, stores):
        with pytest.raises(rs.RemindersUnavailable):
            await rs.list_names()


class TestOpenReminders:
    async def test_every_store_merged_and_finished_ones_left_out(self, stores):
        build_store(stores / "Data-A.sqlite", ["Pets", "Reminders"], [
            {"title": "Call the vet", "list": "Pets", "due": cd(at(2026, 10, 12, 17)), "flagged": True},
            {"title": "Old done thing", "list": "Pets", "completed": True},
            {"title": "Deleted thing", "list": "Reminders", "deleted": True},
        ])
        build_store(stores / "Data-B.sqlite", ["Groceries", ("Gone list", 1)], [
            {"title": "Oat milk", "list": "Groceries"},
            {"title": "In a deleted list", "list": "Gone list"},
        ])
        items = await rs.open_reminders()
        assert sorted(r.title for r in items) == ["Call the vet", "Oat milk"]
        assert {r.list for r in items} == {"Pets", "Groceries"}

    async def test_a_timed_due_date_is_in_owners_zone(self, stores):
        build_store(stores / "Data-A.sqlite", ["Pets"], [
            {"title": "Call the vet", "list": "Pets", "due": cd(at(2026, 10, 12, 17))},
        ])
        (r,) = await rs.open_reminders()
        assert r.all_day is False
        assert r.due == at(2026, 10, 12, 17) and r.due.utcoffset() == timedelta(hours=-5)
        assert r.flagged is False and r.id == "CK-Call the vet"

    async def test_an_all_day_due_date_floats_on_its_date(self, stores):
        # Stored as midnight UTC of the day: in Springfield that instant is the
        # evening before, but the reminder is due on the 12th wherever Ellis is.
        build_store(stores / "Data-A.sqlite", ["Reminders"], [
            {"title": "Renew the tags", "list": "Reminders", "all_day": True,
             "due": cd(datetime(2026, 10, 12, tzinfo=timezone.utc))},
        ])
        (r,) = await rs.open_reminders()
        assert r.all_day is True
        assert r.due.date().isoformat() == "2026-10-12"
        assert r.due.strftime("%b %-d") == "Oct 12"

    async def test_no_due_date_comes_last(self, stores):
        build_store(stores / "Data-A.sqlite", ["Reminders"], [
            {"title": "Someday"},
            {"title": "Soon", "due": cd(at(2026, 10, 12, 9))},
        ])
        items = await rs.open_reminders()
        assert [r.title for r in items] == ["Soon", "Someday"]
        assert items[1].due is None

    async def test_list_and_query_filters(self, stores):
        build_store(stores / "Data-A.sqlite", ["Pets", "Groceries"], [
            {"title": "Call the vet", "list": "Pets"},
            {"title": "Buy cat food", "list": "Groceries", "notes": "the wet kind for Biscuit"},
            {"title": "Oat milk", "list": "Groceries"},
        ])
        assert [r.title for r in await rs.open_reminders(list_name="pets")] == ["Call the vet"]
        assert [r.title for r in await rs.open_reminders(list_name="groc")] == ["Buy cat food", "Oat milk"]
        assert [r.title for r in await rs.open_reminders(query="biscuit")] == ["Buy cat food"]
        assert [r.title for r in await rs.open_reminders(list_name="Groceries", query="milk")] == ["Oat milk"]

    async def test_the_limit(self, stores):
        build_store(stores / "Data-A.sqlite", ["Reminders"], [{"title": f"r{i:02d}"} for i in range(10)])
        assert len(await rs.open_reminders(limit=4)) == 4

    async def test_hints_carry_every_identifier(self, stores):
        ident = uuid.UUID("12345678-1234-5678-9abc-def012345678")
        build_store(stores / "Data-A.sqlite", ["Pets"], [
            {"title": "Call the vet", "list": "Pets", "ck": "CK-1", "da": "DA-1", "ident": ident.bytes},
        ])
        (r,) = await rs.open_reminders()
        assert r.hints["ids"] == ["CK-1", "DA-1", "12345678-1234-5678-9ABC-DEF012345678"]
        assert r.hints["title"] == "Call the vet" and r.hints["list"] == "Pets"

    async def test_a_reminder_without_a_cloudkit_id_still_has_one(self, stores):
        # An account store (Exchange/CalDAV) leaves ZCKIDENTIFIER NULL.
        build_store(stores / "Data-A.sqlite", ["Work"], [
            {"title": "Expense report", "list": "Work", "ck": None, "da": "DA-9"},
        ])
        (r,) = await rs.open_reminders()
        assert r.id == "DA-9"


class TestReminderById:
    async def test_found_by_any_identifier(self, stores):
        ident = uuid.UUID("12345678-1234-5678-9abc-def012345678")
        build_store(stores / "Data-A.sqlite", ["Pets"], [
            {"title": "Call the vet", "list": "Pets", "ck": "CK-1", "da": "DA-1", "ident": ident.bytes},
        ])
        for key in ("CK-1", "DA-1", "12345678-1234-5678-9ABC-DEF012345678", "12345678-1234-5678-9abc-def012345678"):
            r = await rs.reminder_by_id(key)
            assert r is not None and r.title == "Call the vet", key
        assert await rs.reminder_by_id("nope") is None


class TestNeverAnEmptyListForCantRead:
    async def test_no_store_is_unavailable(self, stores):
        with pytest.raises(rs.RemindersUnavailable, match="no Reminders"):
            await rs.open_reminders()

    async def test_schema_drift_is_unavailable(self, stores):
        build_store(stores / "Data-A.sqlite", ["Pets"], [{"title": "x", "list": "Pets"}], drop_column="ZDUEDATE")
        with pytest.raises(rs.RemindersUnavailable, match="schema changed.*ZDUEDATE"):
            await rs.open_reminders()

    async def test_a_missing_table_is_unavailable(self, stores):
        con = sqlite3.connect(stores / "Data-A.sqlite")
        con.execute("CREATE TABLE ZSOMETHINGELSE (Z_PK INTEGER)")
        con.commit()
        con.close()
        with pytest.raises(rs.RemindersUnavailable, match="schema changed"):
            await rs.open_reminders()

    async def test_an_unreadable_store_is_unavailable(self, stores):
        (stores / "Data-A.sqlite").write_bytes(b"this is not a database" * 100)
        with pytest.raises(rs.RemindersUnavailable):
            await rs.open_reminders()

    async def test_off_is_unavailable(self, stores, monkeypatch):
        build_store(stores / "Data-A.sqlite", ["Pets"], [{"title": "x", "list": "Pets"}])
        monkeypatch.setenv("EDWARD_REMINDERS_SQLITE", "off")
        with pytest.raises(rs.RemindersUnavailable, match="off"):
            await rs.open_reminders()

    async def test_it_opens_read_only(self, stores, monkeypatch):
        build_store(stores / "Data-A.sqlite", ["Pets"], [{"title": "x", "list": "Pets"}])
        seen = []
        real = sqlite3.connect

        def spy(target, *a, **k):
            seen.append(str(target))
            return real(target, *a, **k)

        monkeypatch.setattr(rs.sqlite3, "connect", spy)
        await rs.open_reminders()
        assert seen and all(t.startswith("file:") and ("mode=ro" in t or "immutable=1" in t) for t in seen)

    async def test_status_says_which_source_served(self, stores):
        build_store(stores / "Data-A.sqlite", ["Pets"], [{"title": "x", "list": "Pets"}])
        await rs.open_reminders()
        assert rs.status()["source"] == "sqlite"
        (stores / "Data-A.sqlite").unlink()
        with pytest.raises(rs.RemindersUnavailable):
            await rs.open_reminders()
        st = rs.status()
        assert st["source"] == "unavailable" and st["error"]


def test_tests_never_read_owners_reminders():
    """conftest turns the reader off for every test that doesn't point it
    at a fixture."""
    import os

    assert os.environ.get("EDWARD_REMINDERS_SQLITE") == "off"
