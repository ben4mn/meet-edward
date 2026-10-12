"""R/G TDD for iMessage thread-context plumbing.

Covers two failures that together starve Edward of context in active group
chats like "Weekend":

Step 1 (this file, first block) — ``get_chat_thread`` crashes whenever any
recent message in the requested chat has ``text=NULL`` and the body sitting
only in ``attributedBody`` (reactions, link previews, photo captions,
rich-text). The bug is a typo at line 461 calling
``_extract_text_from_attributed_body(...)`` when only
``_extract_text_from_attributed_body_legacy`` exists. The caller in
``triage_service`` catches the resulting ``NameError`` and falls back to
empty thread_context — Edward sees nothing.

Step 2 (second block) — the thread limit is hardcoded to 15 at
``triage_service.py:784``. We drive a config-aware, group-vs-direct
selection.
"""

from __future__ import annotations

import plistlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Helpers: build a minimal chat.db so get_chat_thread() runs end-to-end.      #
# --------------------------------------------------------------------------- #


_CHAT_DB_SCHEMA = """
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY,
    text TEXT,
    attributedBody BLOB,
    is_from_me INTEGER DEFAULT 0,
    date INTEGER DEFAULT 0,
    handle_id INTEGER,
    associated_message_type INTEGER DEFAULT 0
);
CREATE TABLE handle (
    ROWID INTEGER PRIMARY KEY,
    id TEXT
);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY,
    chat_identifier TEXT,
    display_name TEXT,
    style INTEGER DEFAULT 0,
    guid TEXT
);
CREATE TABLE chat_message_join (
    message_id INTEGER,
    chat_id INTEGER
);
"""


def _make_attributed_body(text: str) -> bytes:
    """A minimal NSKeyedArchiver-shaped bplist carrying ``text``.

    Mirrors ``tests/test_imessage_decoder.py::_make_attributed_body`` so the
    decoder's happy path applies and we exercise ``get_chat_thread``'s
    fall-through path identically to production.
    """
    data = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            {
                "NS.string": plistlib.UID(2),
                "NSAttributes": plistlib.UID(3),
                "$class": plistlib.UID(4),
            },
            text,
            {"NSDictionary": plistlib.UID(0)},
            {
                "$classname": "NSMutableAttributedString",
                "$classes": [
                    "NSMutableAttributedString",
                    "NSAttributedString",
                    "NSObject",
                ],
            },
        ],
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_BINARY)


def _build_chat_db(
    path: Path,
    chat_identifier: str,
    rows: list[dict],
    *,
    is_group: bool = False,
) -> None:
    """Create a fake chat.db at ``path`` with the rows we want returned.

    Each entry in ``rows`` is:
        {"text": str|None, "attributedBody": bytes|None,
         "is_from_me": bool, "sender": str|None}
    Rows are inserted in order; ROWID assigned by autoincrement.
    """
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_CHAT_DB_SCHEMA)
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, style, guid) VALUES (?, ?, ?, ?)",
            (1, chat_identifier, 43 if is_group else 0, f"iMessage;-;{chat_identifier}"),
        )
        # Use the row's ROWID as the chat_message_join entry so the SELECT can find them.
        handles: dict[str, int] = {}
        next_handle = 1
        for idx, r in enumerate(rows, start=1):
            sender = r.get("sender")
            handle_id: int | None = None
            if sender:
                if sender not in handles:
                    conn.execute(
                        "INSERT INTO handle (ROWID, id) VALUES (?, ?)",
                        (next_handle, sender),
                    )
                    handles[sender] = next_handle
                    next_handle += 1
                handle_id = handles[sender]
            conn.execute(
                """INSERT INTO message
                   (ROWID, text, attributedBody, is_from_me, date, handle_id, associated_message_type)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    idx,
                    r.get("text"),
                    r.get("attributedBody"),
                    1 if r.get("is_from_me") else 0,
                    int(idx) * 1_000_000_000,  # placeholder Apple ts
                    handle_id,
                ),
            )
            conn.execute(
                "INSERT INTO chat_message_join (message_id, chat_id) VALUES (?, ?)",
                (idx, 1),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fake_chat_db(tmp_path, monkeypatch):
    """Yield a callable that builds a chat.db and points the listener at it."""

    db_path = tmp_path / "chat.db"

    def _build(chat_identifier: str, rows: list[dict], *, is_group: bool = False) -> Path:
        _build_chat_db(db_path, chat_identifier, rows, is_group=is_group)
        from services.heartbeat import listener_imessage as listener

        monkeypatch.setattr(listener, "CHAT_DB_PATH", str(db_path))
        return db_path

    return _build


# --------------------------------------------------------------------------- #
# Step 1 — get_chat_thread must not raise on attributedBody-only rows.        #
# --------------------------------------------------------------------------- #


def test_get_chat_thread_decodes_attributed_body_when_text_is_null(fake_chat_db):
    """A row with text=NULL but a valid attributedBody must be returned with
    its decoded payload, not silently dropped or raised over."""
    from services.heartbeat.listener_imessage import get_chat_thread

    fake_chat_db(
        "Weekend-test",
        [
            {
                "text": None,
                "attributedBody": _make_attributed_body("hey did anyone see the game"),
                "sender": "+15555550100",
            },
        ],
    )

    result = get_chat_thread("Weekend-test", limit=10)
    assert len(result) == 1, "row with attributedBody should have been returned"
    assert result[0]["text"] == "hey did anyone see the game"


def test_get_chat_thread_does_not_raise_when_any_row_has_binary_body(fake_chat_db):
    """Mixed batch: text rows + attributedBody-only rows. Currently the
    NameError on the binary row crashes the whole fetch and the caller in
    triage_service silently dumps thread context. After the fix, all five
    rows return cleanly."""
    from services.heartbeat.listener_imessage import get_chat_thread

    fake_chat_db(
        "Weekend-test",
        [
            {"text": "yo", "attributedBody": None, "sender": "+15555550101"},
            {"text": "lol", "attributedBody": None, "sender": "+15555550102"},
            {
                "text": None,
                "attributedBody": _make_attributed_body("dude that was crazy"),
                "sender": "+15555550101",
            },
            {"text": "fr", "attributedBody": None, "sender": "+15555550103"},
            {
                "text": None,
                "attributedBody": _make_attributed_body("we should hang"),
                "sender": "+15555550102",
            },
        ],
    )

    # Today: NameError on the third iteration before we get any results.
    result = get_chat_thread("Weekend-test", limit=10)
    assert len(result) == 5, (
        "all five rows should be returned — text rows and attributedBody-only rows alike"
    )
    texts = [r["text"] for r in result]
    assert "dude that was crazy" in texts
    assert "we should hang" in texts


# --------------------------------------------------------------------------- #
# Step 2 — thread limit must be configurable and group-vs-direct aware.       #
# --------------------------------------------------------------------------- #


class _FakeConfig:
    """Cheap stand-in for HeartbeatConfigModel without needing the DB.

    The helper only reads two attributes; treating the config as a simple
    namespace object keeps the limit-selection test focused on its own
    logic instead of SQLAlchemy session plumbing.
    """

    def __init__(self, *, group=None, direct=None):
        if group is not None:
            self.imessage_thread_limit_group = group
        if direct is not None:
            self.imessage_thread_limit_direct = direct


def test_thread_limit_helper_returns_group_value_for_group_chat():
    from services.heartbeat.listener_imessage import _thread_limit_for

    cfg = _FakeConfig(group=75, direct=30)
    assert _thread_limit_for(is_group=True, config=cfg) == 75


def test_thread_limit_helper_returns_direct_value_for_one_on_one():
    from services.heartbeat.listener_imessage import _thread_limit_for

    cfg = _FakeConfig(group=75, direct=30)
    assert _thread_limit_for(is_group=False, config=cfg) == 30


def test_thread_limit_helper_picks_up_config_override():
    """Tuning the limits live via PATCH config must flow through."""
    from services.heartbeat.listener_imessage import _thread_limit_for

    cfg = _FakeConfig(group=120, direct=8)
    assert _thread_limit_for(is_group=True, config=cfg) == 120
    assert _thread_limit_for(is_group=False, config=cfg) == 8


def test_thread_limit_helper_falls_back_to_defaults_when_config_missing():
    """No config row yet → use the new defaults (group 75, direct 30),
    not the legacy hardcoded 15."""
    from services.heartbeat.listener_imessage import _thread_limit_for

    assert _thread_limit_for(is_group=True, config=None) == 75
    assert _thread_limit_for(is_group=False, config=None) == 30


def test_thread_limit_helper_falls_back_when_config_has_no_thread_limit_fields():
    """Old config rows from before the migration ran shouldn't crash."""
    from services.heartbeat.listener_imessage import _thread_limit_for

    cfg = _FakeConfig()  # neither attribute set
    assert _thread_limit_for(is_group=True, config=cfg) == 75
    assert _thread_limit_for(is_group=False, config=cfg) == 30


def test_heartbeat_config_model_carries_thread_limit_columns():
    """Sanity that the SQLAlchemy model exposes the new columns with
    sensible defaults — so live config rows pick them up after migration."""
    from services.database import HeartbeatConfigModel

    assert hasattr(HeartbeatConfigModel, "imessage_thread_limit_direct"), (
        "HeartbeatConfigModel must expose imessage_thread_limit_direct"
    )
    assert hasattr(HeartbeatConfigModel, "imessage_thread_limit_group"), (
        "HeartbeatConfigModel must expose imessage_thread_limit_group"
    )
    # Reading default values off the SQLAlchemy column descriptor.
    direct_col = HeartbeatConfigModel.__table__.c.imessage_thread_limit_direct
    group_col = HeartbeatConfigModel.__table__.c.imessage_thread_limit_group
    assert direct_col.default.arg == 30
    assert group_col.default.arg == 75
