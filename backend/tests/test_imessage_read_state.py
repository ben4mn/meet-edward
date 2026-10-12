"""``imessage_read_state`` — read state, tapbacks and inline replies from chat.db.

Why (proactivity-spec §2.4, §2.8, 2026-10-11): the evidence's worst relays
were texts Ellis had already read — the median read is 2.6 minutes. The
noticer and the gate need "read after 3 min", and the outcome classifier
needs Ellis's tapback or inline reply on Edward's message. chat.db is opened
read-only (``mode=ro``, ``PRAGMA query_only=ON``), never ``immutable`` (that
cannot see rows still in the WAL). These tests build a throwaway sqlite
file with chat.db's columns; nothing here opens Ellis's real chat.db.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from services import imessage_read_state as rs

pytestmark = pytest.mark.unit

APPLE = 978307200


def apple_ns(dt: datetime) -> int:
    return int((dt.timestamp() - APPLE) * 1e9)


@pytest.fixture
def chat_db(tmp_path):
    path = tmp_path / "chat.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, is_from_me INTEGER,
                    date INTEGER, date_read INTEGER, is_read INTEGER, associated_message_guid TEXT,
                    associated_message_type INTEGER, thread_originator_guid TEXT)""")
    sent = datetime(2026, 10, 3, 21, 31, tzinfo=timezone.utc)
    read = datetime(2026, 10, 3, 21, 34, tzinfo=timezone.utc)
    rows = [
        (100, "G-100", "check-in closes at 10", 0, apple_ns(sent), apple_ns(read), 1, None, 0, None),
        (101, "G-101", "unread one", 0, apple_ns(sent), 0, 0, None, 0, None),
        (102, "G-EDWARD", "UA958: want me to check in? - Edward", 1, apple_ns(sent), 0, 1, None, 0, None),
        (103, "G-103", None, 1, apple_ns(read), 0, 1, "p:0/G-EDWARD", 2001, None),          # Ellis liked it
        (104, "G-104", None, 0, apple_ns(read), 0, 0, "bp:G-EDWARD", 2003, None),           # someone laughed
        (105, "G-105", None, 1, apple_ns(read), 0, 1, "p:0/G-EDWARD", 3001, None),          # removed the like
        (106, "G-106", "yes please", 1, apple_ns(read), 0, 1, None, 0, "G-EDWARD"),         # inline reply
    ]
    conn.executemany("INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(path)


def test_read_state_by_rowid(chat_db):
    out = rs.read_state([100, 101, 999], path=chat_db)
    assert out[100]["is_read"] is True
    assert out[100]["read_at"] == datetime(2026, 10, 3, 21, 34, tzinfo=timezone.utc)
    assert out[101]["is_read"] is False and out[101]["read_at"] is None
    assert 999 not in out


def test_reactions_are_tapbacks_on_the_guid(chat_db):
    out = rs.reactions_to(["G-EDWARD"], path=chat_db)
    kinds = sorted((r["type"], r["from_me"]) for r in out["G-EDWARD"])
    assert kinds == [(2001, True), (2003, False)]   # 3001 (a removal) is not a reaction


def test_replies_are_inline_replies(chat_db):
    out = rs.replies_to(["G-EDWARD"], path=chat_db)
    assert [r["rowid"] for r in out["G-EDWARD"]] == [106]
    assert out["G-EDWARD"][0]["from_me"] is True


def test_the_connection_is_read_only(chat_db):
    conn = rs.connect(chat_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM message")
    finally:
        conn.close()


def test_a_missing_db_or_column_is_empty_not_an_error(tmp_path):
    assert rs.read_state([1], path=str(tmp_path / "nope.db")) == {}
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, is_from_me INTEGER, date INTEGER)")
    conn.commit()
    conn.close()
    assert rs.replies_to(["G"], path=str(path)) == {}
    assert rs.read_state([1], path=str(path)) == {}


def test_empty_inputs_never_open_the_db(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("opened chat.db for nothing")

    monkeypatch.setattr(rs, "connect", boom)
    assert rs.read_state([]) == {} and rs.reactions_to([]) == {} and rs.replies_to([]) == {}


def test_the_default_path_is_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EDWARD_CHAT_DB", str(tmp_path / "x.db"))
    assert rs.chat_db_path() == str(tmp_path / "x.db")


def test_apple_dates_in_seconds_or_nanoseconds():
    dt = datetime(2026, 10, 3, 21, 31, tzinfo=timezone.utc)
    assert rs.apple_to_datetime(apple_ns(dt)) == dt
    assert rs.apple_to_datetime(int(dt.timestamp() - APPLE)) == dt
    assert rs.apple_to_datetime(0) is None and rs.apple_to_datetime(None) is None


async def test_async_wrappers_run_in_a_thread(chat_db):
    out = await rs.aread_state([100], path=chat_db)
    assert out[100]["is_read"] is True
