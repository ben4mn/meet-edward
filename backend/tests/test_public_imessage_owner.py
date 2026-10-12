"""The iMessage listener names the owner from configuration, never a literal.

The owner's own rows are labelled with ``owner.owner_name()`` (conftest sets
EDWARD_OWNER_NAME=Ellis), and the owner's handles come from heartbeat_config
merged with ``EDWARD_OWNER_HANDLES``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from services.heartbeat import listener_imessage as listener
from services.media import links


def test_the_db_handles_and_the_env_handles_are_merged(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_HANDLES", "+12015550100, ellis@example.com")
    merged = listener.merge_owner_handles(["+12015550100", "ellis@icloud.example"])
    assert merged == ["+12015550100", "ellis@icloud.example", "ellis@example.com"]


def test_no_handles_anywhere_is_empty(monkeypatch):
    monkeypatch.delenv("EDWARD_OWNER_HANDLES", raising=False)
    assert listener.merge_owner_handles([]) == []
    assert listener.merge_owner_handles(None) == []
    assert listener.merge_owner_handles(["", None, "  "]) == []


async def test_an_unreadable_config_still_has_the_env_handles(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_HANDLES", "+12015550100")

    def boom():
        raise RuntimeError("database down")

    with patch.object(listener, "async_session", boom):
        handles, enabled = await listener._load_listener_config()
    assert handles == ["+12015550100"] and enabled is True


def test_the_owners_own_lines_carry_the_owners_name():
    now = datetime.now(timezone.utc)
    text = listener.format_chat_thread([
        {"timestamp": now, "is_from_me": True, "sender": "me", "text": "on my way"},
        {"timestamp": now, "is_from_me": False, "sender": "+12015550101", "text": "see you"},
    ], {"+12015550101": "Parker"})
    assert "You (Ellis): on my way" in text
    assert "Parker: see you" in text


def test_link_notes_name_the_configured_owner():
    for note in (links.UNKNOWN_SENDER_NOTE, links.SIGN_IN_NOTE):
        assert "{OWNER}" not in note and "Ellis" in note
