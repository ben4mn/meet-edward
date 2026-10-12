"""
Regression: get_recent_messages never returned a single message body.

Found 2026-08-22. Asked "what did Rowan say?", Edward called the tool most
obviously named for the job and got back ~130 lines of this:

    Recent chats: chatName:missing value, chatId:iMessage;-;+15555550171,
    chatName:Team Weekend, chatId:iMessage;+;chat100000000000000001, ...

services/imessage_service.py:308 ran an AppleScript that enumerated every chat's
name and id and nothing else. It also ignored BOTH its parameters — `contact` was
never referenced, `limit` was never referenced — and the tool wrapper silently
dropped its own `hours` argument on the floor.

So Edward burned a lot of context on a wall of phone numbers and then correctly
reported that he could not see any message content.

Meanwhile the heartbeat listener has been reading chat.db properly this whole
time (1,277 events in 14 days, zero empty). This rewires the tool onto that same
reader and makes `contact` and `hours` actually mean something.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

APPLE_EPOCH_OFFSET = 978307200

SCHEMA = """
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
    is_from_me INTEGER DEFAULT 0, date INTEGER DEFAULT 0,
    handle_id INTEGER, associated_message_type INTEGER DEFAULT 0
);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT,
    style INTEGER DEFAULT 0, guid TEXT
);
CREATE TABLE chat_message_join (message_id INTEGER, chat_id INTEGER);
"""


def _apple_ts(dt: datetime) -> int:
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1e9)


@pytest.fixture()
def chat_db(tmp_path, monkeypatch):
    """Two chats: a 1:1 with Rowan and a group, with real timestamps."""
    now = datetime.now(timezone.utc)
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO chat (ROWID, chat_identifier, display_name, style, guid) VALUES (1,'+15555550171',NULL,0,'g1')"
    )
    conn.execute(
        "INSERT INTO chat (ROWID, chat_identifier, display_name, style, guid) VALUES (2,'chat805','Team Weekend',43,'g2')"
    )
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (1,'+15555550171')")
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (2,'+15555550173')")

    rows = [
        # rowid, text, from_me, handle, chat, age_hours
        (1, "ancient history", 0, 1, 1, 400),
        (2, "How was the hike?", 1, None, 1, 3),
        (3, "Good. We just got back to the car.", 0, 1, 1, 2),
        (4, "Who is bringing the snacks?", 0, 2, 2, 1),
        (5, "Sleep well, see you tomorrow!", 1, None, 1, 0.5),
    ]
    for rowid, text, from_me, handle, chat, age in rows:
        conn.execute(
            "INSERT INTO message (ROWID,text,attributedBody,is_from_me,date,handle_id,associated_message_type)"
            " VALUES (?,?,NULL,?,?,?,0)",
            (rowid, text, from_me, _apple_ts(now - timedelta(hours=age)), handle),
        )
        conn.execute(
            "INSERT INTO chat_message_join (message_id, chat_id) VALUES (?,?)",
            (rowid, chat),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "services.heartbeat.listener_imessage.CHAT_DB_PATH", str(db), raising=False
    )
    return db


class TestReaderLayer:
    def test_returns_actual_message_text(self, chat_db):
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        texts = [m["text"] for m in get_recent_messages_across_chats(hours=24)]
        assert "Sleep well, see you tomorrow!" in texts
        assert "How was the hike?" in texts

    def test_hours_window_is_honoured(self, chat_db):
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        texts = [m["text"] for m in get_recent_messages_across_chats(hours=24)]
        assert "ancient history" not in texts, "400h-old message must fall outside a 24h window"

        wide = [m["text"] for m in get_recent_messages_across_chats(hours=500)]
        assert "ancient history" in wide

    def test_narrow_window_excludes_older_messages(self, chat_db):
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        texts = [m["text"] for m in get_recent_messages_across_chats(hours=1.5)]
        assert "Sleep well, see you tomorrow!" in texts
        assert "How was the hike?" not in texts

    def test_contact_filter_narrows_to_one_thread(self, chat_db):
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        texts = [
            m["text"]
            for m in get_recent_messages_across_chats(hours=24, contact="+15555550171")
        ]
        assert "How was the hike?" in texts
        assert "Who is bringing the snacks?" not in texts, "group-chat message must be filtered out"

    def test_contact_filter_matches_group_display_name(self, chat_db):
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        texts = [
            m["text"]
            for m in get_recent_messages_across_chats(hours=24, contact="Weekend")
        ]
        assert "Who is bringing the snacks?" in texts

    def test_results_are_chronological(self, chat_db):
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        stamps = [m["timestamp"] for m in get_recent_messages_across_chats(hours=24)]
        assert stamps == sorted(stamps)


class TestToolSurface:
    @pytest.mark.asyncio
    async def test_tool_output_contains_message_bodies_not_chat_ids(self, chat_db):
        from services.graph.tools import get_recent_messages

        out = await get_recent_messages.ainvoke({})
        assert "Sleep well, see you tomorrow!" in out
        assert "chatName:missing value" not in out
        assert "Full message history requires direct database access" not in out

    @pytest.mark.asyncio
    async def test_tool_honours_contact_argument(self, chat_db):
        from services.graph.tools import get_recent_messages

        out = await get_recent_messages.ainvoke({"contact": "+15555550171"})
        assert "How was the hike?" in out
        assert "Who is bringing the snacks?" not in out

    @pytest.mark.asyncio
    async def test_tool_honours_hours_argument(self, chat_db):
        from services.graph.tools import get_recent_messages

        # The tool's `hours` is an int; the reader accepts floats (covered above).
        out = await get_recent_messages.ainvoke({"hours": 1})
        assert "Sleep well, see you tomorrow!" in out          # 0.5h old
        assert "How was the hike?" not in out           # 3h old

    @pytest.mark.asyncio
    async def test_empty_window_says_so_clearly(self, chat_db):
        from services.graph.tools import get_recent_messages

        out = await get_recent_messages.ainvoke({"contact": "nobody-by-this-name"})
        assert "no" in out.lower()
        assert "nobody-by-this-name" in out


class TestUnavailableIsNotConfusedWithEmpty:
    """A missing chat.db must not masquerade as a quiet 24 hours.

    The old implementation had an explicit is_available() gate. Reading chat.db
    directly means "database not there" and "nobody texted" both arrive as an
    empty list — and reporting the former as the latter is precisely the
    silent-empty failure this whole batch of fixes is about.
    """

    @pytest.mark.asyncio
    async def test_missing_chat_db_is_reported_as_unavailable(
        self, tmp_path, monkeypatch
    ):
        from services.graph.tools import get_recent_messages

        monkeypatch.setattr(
            "services.heartbeat.listener_imessage.CHAT_DB_PATH",
            str(tmp_path / "definitely-not-here.db"),
            raising=False,
        )
        out = await get_recent_messages.ainvoke({})
        assert "unavailable" in out.lower() or "not available" in out.lower()
        assert "No messages found" not in out

    @pytest.mark.asyncio
    async def test_present_but_quiet_still_reports_empty(self, chat_db):
        """Contrast case: the db IS there, the window is just quiet."""
        from services.graph.tools import get_recent_messages

        out = await get_recent_messages.ainvoke({"contact": "nobody-at-all"})
        assert "No messages found" in out
