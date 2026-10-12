"""R/G TDD for the ``read_imessage_thread`` LLM tool.

Even after Steps 1-3, Edward's automatic thread context is bounded
(75 messages for groups). For pathological long-running conversations he
should be able to *ask for more* mid-turn — page back further into the
chat to see what happened earlier. This tool is the escape hatch.

Gated under the existing ``imessage_applescript`` skill alongside
``send_imessage`` and ``get_recent_messages``, so toggling iMessage off
disables it cleanly.
"""

from __future__ import annotations

import plistlib
import pytest

from tests.test_imessage_thread_context import _build_chat_db, _make_attributed_body


# --------------------------------------------------------------------------- #
# Tool surface                                                                 #
# --------------------------------------------------------------------------- #


def test_read_imessage_thread_tool_is_importable():
    """The LLM tool function must exist at the canonical location."""
    from services.graph.tools import read_imessage_thread

    assert read_imessage_thread is not None
    # LangChain @tool decorator wraps the function — check tool metadata.
    assert getattr(read_imessage_thread, "name", "") == "read_imessage_thread"


def test_read_imessage_thread_is_gated_under_imessage_skill():
    from services.tool_registry import SKILL_TOOL_MAPPING

    assert "read_imessage_thread" in SKILL_TOOL_MAPPING["imessage_applescript"], (
        "read_imessage_thread should be gated under the existing "
        "imessage_applescript skill (same gate as send_imessage)"
    )


def test_read_imessage_thread_is_returned_when_skill_enabled():
    from services.tool_registry import _get_messaging_tools

    tools = _get_messaging_tools({"imessage_applescript": True})
    tool_names = {getattr(t, "name", "") for t in tools}
    assert "read_imessage_thread" in tool_names


def test_read_imessage_thread_is_omitted_when_skill_disabled():
    from services.tool_registry import _get_messaging_tools

    tools = _get_messaging_tools({"imessage_applescript": False})
    tool_names = {getattr(t, "name", "") for t in tools}
    assert "read_imessage_thread" not in tool_names


# --------------------------------------------------------------------------- #
# Behavior — must read from chat.db end-to-end.                                #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_chat_db(tmp_path, monkeypatch):
    """Same shape as test_imessage_thread_context.fake_chat_db but bound to a
    fresh ``CHAT_DB_PATH`` for the duration of one test."""
    db_path = tmp_path / "chat.db"

    def _build(chat_identifier: str, rows: list[dict], *, is_group: bool = False):
        _build_chat_db(db_path, chat_identifier, rows, is_group=is_group)
        from services.heartbeat import listener_imessage as listener

        monkeypatch.setattr(listener, "CHAT_DB_PATH", str(db_path))
        return db_path

    return _build


@pytest.mark.asyncio
async def test_read_imessage_thread_returns_formatted_window(fake_chat_db):
    """Tool returns a human-readable thread block including all requested
    messages. Format mirrors what triage embeds in the trigger so the LLM
    has a consistent surface across the auto-fetch path and the on-demand
    path."""
    from services.graph.tools import read_imessage_thread

    fake_chat_db(
        "Weekend-test",
        [
            {"text": "yo", "attributedBody": None, "sender": "+15555550100"},
            {"text": "anyone going to the game", "attributedBody": None,
             "sender": "+15555550101"},
            {"text": "im in", "attributedBody": None, "sender": "+15555550100"},
            {
                "text": None,
                "attributedBody": _make_attributed_body("yo same"),
                "sender": "+15555550102",
            },
        ],
        is_group=True,
    )

    # LangChain @tool exposes the underlying function via .ainvoke / .invoke.
    output = await read_imessage_thread.ainvoke(
        {"chat_identifier": "Weekend-test", "limit": 10}
    )

    assert isinstance(output, str)
    assert "yo" in output
    assert "anyone going to the game" in output
    assert "yo same" in output, (
        "attributedBody-only rows must be decoded and surfaced too"
    )


@pytest.mark.asyncio
async def test_read_imessage_thread_paginates_with_before_rowid(fake_chat_db):
    """Edward gets back rowids in the formatted output (or in the meta
    footer); passing ``before_rowid`` to a follow-up call returns strictly
    older messages."""
    from services.graph.tools import read_imessage_thread

    fake_chat_db(
        "Weekend-test",
        [{"text": f"msg-{i}", "attributedBody": None,
          "sender": "+15555550100"} for i in range(10)],
        is_group=True,
    )

    first = await read_imessage_thread.ainvoke(
        {"chat_identifier": "Weekend-test", "limit": 3}
    )
    # The tool surfaces rowids so the LLM can scroll back; the meta footer
    # contract is: "oldest_rowid=N" appears in the response.
    assert "oldest_rowid=" in first, (
        "tool output must expose oldest_rowid so the LLM can paginate"
    )

    oldest_rowid_str = first.split("oldest_rowid=")[1].split()[0].split("\n")[0]
    oldest_rowid = int(oldest_rowid_str)

    second = await read_imessage_thread.ainvoke(
        {
            "chat_identifier": "Weekend-test",
            "limit": 3,
            "before_rowid": oldest_rowid,
        }
    )

    # The second window should be strictly older — none of its messages
    # should match the most-recent message we saw in the first window.
    assert "msg-9" not in second, "before_rowid must exclude rows ≥ given id"
    assert "msg-8" not in second
    assert "msg-7" not in second


@pytest.mark.asyncio
async def test_read_imessage_thread_returns_helpful_message_for_unknown_chat(
    fake_chat_db,
):
    """No matching messages should not return an empty string — the LLM
    needs to know its lookup didn't find anything."""
    from services.graph.tools import read_imessage_thread

    fake_chat_db("Weekend-test", [{"text": "hi", "sender": "+15555550100"}])

    output = await read_imessage_thread.ainvoke(
        {"chat_identifier": "some-other-chat", "limit": 10}
    )
    assert isinstance(output, str)
    assert output  # non-empty
    assert "no messages" in output.lower() or "0 messages" in output.lower()
