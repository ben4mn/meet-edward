"""R/G TDD for the ``list_chat_participants`` LLM tool.

For poll-style multi-turn workflows in a group chat, Edward needs to
enumerate the *full* roster of a chat — not just senders who've spoken
recently. ``lookup_contact`` resolves a known phone/email → name, but
doesn't tell him *which* phones are in the chat. This tool reads
``chat_handle_join`` for the roster, then auto-resolves each handle
through the existing contacts service.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_imessage_thread_context import (
    _build_chat_db as _build_chat_db_base,
    _CHAT_DB_SCHEMA,
)


# --------------------------------------------------------------------------- #
# Helper: extend the test chat.db to also write chat_handle_join rows, since   #
# the participants lookup joins through that table (not chat_message_join).    #
# --------------------------------------------------------------------------- #


def _build_chat_db_with_participants(
    path: Path,
    chat_identifier: str,
    participants: list[str],
    *,
    is_group: bool = True,
) -> None:
    """Build a fake chat.db with explicit participants, regardless of whether
    they've sent any messages — the production query joins through
    ``chat_handle_join``, which is the source of truth for membership."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_CHAT_DB_SCHEMA)
        # chat_handle_join doesn't exist in the base schema; add it.
        conn.executescript(
            "CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);"
        )
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, style, guid) VALUES (?, ?, ?, ?)",
            (1, chat_identifier, 43 if is_group else 0, f"iMessage;-;{chat_identifier}"),
        )
        for idx, handle in enumerate(participants, start=1):
            conn.execute(
                "INSERT INTO handle (ROWID, id) VALUES (?, ?)", (idx, handle)
            )
            conn.execute(
                "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
                (1, idx),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fake_chat_db_with_roster(tmp_path, monkeypatch):
    db_path = tmp_path / "chat.db"

    def _build(chat_identifier: str, participants: list[str], *, is_group: bool = True):
        _build_chat_db_with_participants(
            db_path, chat_identifier, participants, is_group=is_group
        )
        from services.heartbeat import listener_imessage as listener

        monkeypatch.setattr(listener, "CHAT_DB_PATH", str(db_path))
        return db_path

    return _build


# --------------------------------------------------------------------------- #
# Tool surface — gating + registration                                         #
# --------------------------------------------------------------------------- #


def test_list_chat_participants_tool_is_importable():
    from services.graph.tools import list_chat_participants

    assert getattr(list_chat_participants, "name", "") == "list_chat_participants"


def test_list_chat_participants_is_gated_under_imessage_skill():
    from services.tool_registry import SKILL_TOOL_MAPPING

    assert "list_chat_participants" in SKILL_TOOL_MAPPING["imessage_applescript"]


def test_list_chat_participants_returned_when_skill_enabled():
    from services.tool_registry import _get_messaging_tools

    tools = _get_messaging_tools({"imessage_applescript": True})
    names = {getattr(t, "name", "") for t in tools}
    assert "list_chat_participants" in names


def test_list_chat_participants_omitted_when_skill_disabled():
    from services.tool_registry import _get_messaging_tools

    tools = _get_messaging_tools({"imessage_applescript": False})
    names = {getattr(t, "name", "") for t in tools}
    assert "list_chat_participants" not in names


# --------------------------------------------------------------------------- #
# Behaviour                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_chat_participants_enumerates_full_roster(
    fake_chat_db_with_roster, monkeypatch
):
    """The tool reads chat_handle_join, so it sees silent members too —
    not just senders who've spoken (which is what ``get_chat_thread`` is
    limited to)."""
    from services.graph import tools as gtools

    fake_chat_db_with_roster(
        "team-Weekend",
        ["+15555550100", "+15555550101", "+15555550102", "+15555550103"],
    )
    # Mock contact resolution — no real AppleScript in unit tests.
    monkeypatch.setattr(
        gtools, "lookup_phone", lambda phone: {"success": True, "matches": []}
    )

    output = await gtools.list_chat_participants.ainvoke(
        {"chat_identifier": "team-Weekend"}
    )
    assert isinstance(output, str)
    # Every handle appears in the output, regardless of who's spoken.
    for handle in ["+15555550100", "+15555550101", "+15555550102", "+15555550103"]:
        assert handle in output


@pytest.mark.asyncio
async def test_list_chat_participants_resolves_saved_contacts(
    fake_chat_db_with_roster, monkeypatch
):
    """Saved-as-contact participants surface with their friendly name in
    addition to the raw handle — that's the whole point of this tool for
    poll-running."""
    from services.graph import tools as gtools

    fake_chat_db_with_roster(
        "team-Weekend",
        ["+15555550100", "+15555550101", "+15555550102"],
    )

    # Stub lookup_phone with a fake contacts directory.
    directory = {
        "+15555550100": "Quinn",
        "+15555550101": "Theo",
        # 0102 intentionally unknown
    }

    def fake_lookup_phone(phone: str) -> dict:
        name = directory.get(phone)
        if name:
            return {"success": True, "matches": [{"name": name}]}
        return {"success": True, "matches": []}

    # Patch the CONTACTS helper, not services.graph.tools.lookup_phone. The
    # latter is the LangChain @tool of the same name, which shadows the imported
    # helper; patching it used to work only because production resolved the same
    # shadowed name and crashed on the string it returns. See
    # tests/test_list_chat_participants_lookup_shadowing.py. Participants are
    # named through lookup_handle since 2026-10-11 (an Apple ID email too).
    monkeypatch.setattr(
        "services.contacts_service.lookup_handle", fake_lookup_phone, raising=True
    )

    output = await gtools.list_chat_participants.ainvoke(
        {"chat_identifier": "team-Weekend"}
    )
    assert "Quinn" in output
    assert "Theo" in output
    # Unknown contact still surfaces as the raw handle so Edward knows the
    # gap is "person not in Contacts," not "person doesn't exist."
    assert "+15555550102" in output


@pytest.mark.asyncio
async def test_list_chat_participants_returns_helpful_message_for_unknown_chat(
    fake_chat_db_with_roster, monkeypatch
):
    from services.graph import tools as gtools

    fake_chat_db_with_roster("team-Weekend", ["+15555550100"])
    monkeypatch.setattr(
        gtools, "lookup_phone", lambda phone: {"success": True, "matches": []}
    )

    output = await gtools.list_chat_participants.ainvoke(
        {"chat_identifier": "some-other-chat"}
    )
    assert isinstance(output, str)
    assert output  # non-empty
    assert "no participants" in output.lower() or "not found" in output.lower()


@pytest.mark.asyncio
async def test_list_chat_participants_counts_total(
    fake_chat_db_with_roster, monkeypatch
):
    """A simple count is enough for Edward to reason about poll
    completeness — the count appears in the output."""
    from services.graph import tools as gtools

    fake_chat_db_with_roster(
        "team-Weekend",
        ["+15555550100", "+15555550101", "+15555550102", "+15555550103"],
    )
    monkeypatch.setattr(
        gtools, "lookup_phone", lambda phone: {"success": True, "matches": []}
    )

    output = await gtools.list_chat_participants.ainvoke(
        {"chat_identifier": "team-Weekend"}
    )
    # Either an explicit count "4 participants" or a list that contains all 4.
    assert "4" in output
