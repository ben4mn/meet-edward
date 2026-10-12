"""
Regression: read_imessage_thread returned "no messages found" for EVERY chat.

Found 2026-08-22 while simulating Edward reading Ellis's iMessages. Live symptom,
for a 1:1 chat with over 100,000 matching rows in chat.db:

    read_imessage_thread: no messages found for +15555550171.

Two independent defects, both in the before_rowid handling:

1. The model supplied before_rowid=0 (it could not express "omitted" — see
   tests/test_codex_schema_preserves_null.py). ``before_rowid is not None`` is
   True for 0, so the query became "AND m.ROWID < 0" and matched nothing.
   ROWID is a positive autoincrement; a non-positive bound can never be a real
   request, so treat it as "no bound".

2. The error path used plain truthiness — ``if before_rowid:`` — which is False
   for 0, so the tool printed the GENERIC "no messages found for {chat}" instead
   of the paginated message naming the rowid. The one line that would have
   revealed the cause was the one it never printed. The two guards must agree.
"""

from __future__ import annotations

import pytest

from tests.test_imessage_thread_context import _build_chat_db


CHAT_ID = "+15555550171"

ROWS = [
    {"text": "How was the hike?", "attributedBody": None, "is_from_me": True, "sender": None},
    {"text": "Good. We just got back to the car.", "attributedBody": None, "is_from_me": False, "sender": CHAT_ID},
    {"text": "Glad you had fun", "attributedBody": None, "is_from_me": True, "sender": None},
]


@pytest.fixture()
def chat_db(tmp_path, monkeypatch):
    db = tmp_path / "chat.db"
    _build_chat_db(db, CHAT_ID, ROWS)
    monkeypatch.setattr(
        "services.heartbeat.listener_imessage.CHAT_DB_PATH", str(db), raising=False
    )
    return db


class TestGetChatThreadBoundHandling:
    """The data layer must not turn a zero bound into an impossible query."""

    def test_zero_before_rowid_is_treated_as_no_bound(self, chat_db):
        from services.heartbeat.listener_imessage import get_chat_thread

        assert len(get_chat_thread(CHAT_ID, 10, 0)) == len(ROWS)

    def test_negative_before_rowid_is_treated_as_no_bound(self, chat_db):
        from services.heartbeat.listener_imessage import get_chat_thread

        assert len(get_chat_thread(CHAT_ID, 10, -1)) == len(ROWS)

    def test_none_before_rowid_still_returns_everything(self, chat_db):
        from services.heartbeat.listener_imessage import get_chat_thread

        assert len(get_chat_thread(CHAT_ID, 10, None)) == len(ROWS)

    def test_a_real_bound_still_pages(self, chat_db):
        """Genuine pagination must keep working — this is the tool's whole job."""
        from services.heartbeat.listener_imessage import get_chat_thread

        everything = get_chat_thread(CHAT_ID, 10, None)
        oldest = min(m["rowid"] for m in everything)
        newest = max(m["rowid"] for m in everything)

        assert get_chat_thread(CHAT_ID, 10, oldest) == []
        assert len(get_chat_thread(CHAT_ID, 10, newest)) == len(ROWS) - 1


class TestToolSurface:
    @pytest.mark.asyncio
    async def test_tool_returns_messages_when_model_sends_zero(self, chat_db):
        """The exact live failure: before_rowid=0 must not blank the thread."""
        from services.graph.tools import read_imessage_thread

        out = await read_imessage_thread.ainvoke(
            {"chat_identifier": CHAT_ID, "limit": 5, "before_rowid": 0}
        )
        assert "no messages found" not in out
        assert "Glad you had fun" in out

    @pytest.mark.asyncio
    async def test_exhausted_pagination_names_the_rowid(self, chat_db):
        """When a REAL bound runs out, say so — don't fall back to the generic line."""
        from services.graph.tools import read_imessage_thread

        everything = __import__(
            "services.heartbeat.listener_imessage", fromlist=["get_chat_thread"]
        ).get_chat_thread(CHAT_ID, 10, None)
        oldest = min(m["rowid"] for m in everything)

        out = await read_imessage_thread.ainvoke(
            {"chat_identifier": CHAT_ID, "limit": 5, "before_rowid": oldest}
        )
        assert f"before rowid {oldest}" in out
