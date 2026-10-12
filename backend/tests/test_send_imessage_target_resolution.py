"""Send-target resolution (2026-07-07 'Nate Brooks' incident).

Edward replied to a heartbeat event with send_imessage(contact="Nate
Brooks"). AppleScript's `participant "Nate Brooks"` doesn't resolve
names — Messages created an undeliverable chat addressed to the literal
text (it shows up in chat.db with chat_identifier "nate brooks") and the
send reported success.

Guards under test:
- imessage_service.send_imessage refuses recipients that aren't a phone
  number or email (never reaches osascript).
- The graph send_imessage tool resolves bare names via macOS Contacts to a
  real handle; ambiguous or unresolvable names return an instructive error
  pointing the model at chat_id / exact handles.
"""

import asyncio
import threading

import pytest
from unittest.mock import AsyncMock

from services import imessage_service as imsg
from services.graph import tools as graph_tools


# ---------------------------------------------------------------------------
# handle detector + service-level guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("handle", [
    "+15555550174",
    "555-555-0174",
    "(555) 555 0174",
    "15555550174",
    "owner@example.com",
])
def test_valid_handles_accepted(handle):
    assert imsg.looks_like_imessage_handle(handle) is True


@pytest.mark.parametrize("not_handle", [
    "Nate Brooks",
    "nate brooks",
    "Mom",
    "",
    "123",
    "Nate Brooks +1555",  # mixed junk
])
def test_names_and_junk_rejected(not_handle):
    assert imsg.looks_like_imessage_handle(not_handle) is False


def test_service_send_refuses_name_recipient(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("osascript must not run for a name recipient")

    monkeypatch.setattr(imsg.subprocess, "run", boom)
    monkeypatch.setattr(imsg, "is_available", lambda: True)

    result = imsg.send_imessage("Nate Brooks", "hi")

    assert result["success"] is False
    assert "phone" in result["error"].lower() or "email" in result["error"].lower()


def test_service_send_allows_phone_recipient(monkeypatch):
    class _Ok:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(imsg.subprocess, "run", lambda *a, **kw: _Ok())
    monkeypatch.setattr(imsg, "is_available", lambda: True)

    result = imsg.send_imessage("+15555550174", "hi")

    assert result["success"] is True


# ---------------------------------------------------------------------------
# tool-level name resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def _tool_env(monkeypatch):
    sent = {}

    def fake_send(recipient, message):
        sent["recipient"] = recipient
        sent["thread"] = threading.current_thread()
        return {"success": True, "recipient": recipient}

    monkeypatch.setattr(imsg, "send_imessage", fake_send)
    monkeypatch.setattr(imsg, "is_available", lambda: True)

    import services.heartbeat.triage_service as ts

    monkeypatch.setattr(
        ts, "register_listening_window_for_chat", AsyncMock(return_value=None)
    )
    return sent


async def test_tool_resolves_name_via_contacts(monkeypatch, _tool_env):
    import services.contacts_service as contacts

    monkeypatch.setattr(contacts, "lookup_contact", lambda q: {
        "success": True,
        "matches": [{"name": "Nate Brooks", "phones": ["+15551230000"], "emails": []}],
    })

    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "Nate Brooks", "message": "hey"}
    )

    assert _tool_env["recipient"] == "+15551230000"
    assert "+15551230000" in result


async def test_tool_errors_on_unresolvable_name(monkeypatch, _tool_env):
    import services.contacts_service as contacts

    monkeypatch.setattr(contacts, "lookup_contact", lambda q: {
        "success": True, "matches": [],
    })

    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "Zorbo The Unknown", "message": "hey"}
    )

    assert "recipient" not in _tool_env, "must not send to an unresolved name"
    assert "chat_id" in result


async def test_tool_errors_on_ambiguous_name(monkeypatch, _tool_env):
    import services.contacts_service as contacts

    monkeypatch.setattr(contacts, "lookup_contact", lambda q: {
        "success": True,
        "matches": [
            {"name": "Nate Brooks", "phones": ["+15551230000"], "emails": []},
            {"name": "Nate Forrest", "phones": ["+15559990000"], "emails": []},
        ],
    })

    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "Nate", "message": "hey"}
    )

    assert "recipient" not in _tool_env, "must not guess between multiple matches"
    assert "multiple" in result.lower()


async def test_tool_passes_real_handles_straight_through(monkeypatch, _tool_env):
    import services.contacts_service as contacts

    def no_lookup(q):
        raise AssertionError("no Contacts lookup needed for a real handle")

    monkeypatch.setattr(contacts, "lookup_contact", no_lookup)

    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "+15555550174", "message": "hey"}
    )

    assert _tool_env["recipient"] == "+15555550174"
    assert "sent" in result.lower()
