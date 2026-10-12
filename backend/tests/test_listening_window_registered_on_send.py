"""
Regression: a listening window must register every time Edward sends an
iMessage — via any path.

Context: on 2026-05-28 Edward replied to two iMessage threads via the
scheduled_events path (Codex weekly cap had blocked the live heartbeat
dispatch). Because that path bypasses the inline window registration
in triage_service, the follow-up inbound messages from those threads
sat at triage_status=pending and got dismissed by Layer 1 — Edward
went silent instead of carrying the conversation. This test pins the
fix: the send_imessage tool wrapper now calls
``register_listening_window_for_chat`` after every successful send.
"""

import pytest
from datetime import datetime, timedelta


@pytest.mark.asyncio
async def test_send_imessage_registers_listening_window(monkeypatch):
    from services.graph import tools as graph_tools
    from services.heartbeat import triage_service

    # Snapshot + reset the in-memory window store.
    triage_service._active_listeners.clear()

    # Stub the AppleScript layer to "succeed" without actually firing osascript.
    monkeypatch.setattr(
        "services.imessage_service.is_available", lambda: True
    )
    monkeypatch.setattr(
        "services.imessage_service.send_imessage",
        lambda recipient, message: {"success": True, "recipient": recipient},
    )

    # Stub the persistent conversation lookup and settings so we don't need a DB.
    async def _fake_get_or_create(chat_identifier, chat_name=None, is_group=False):
        return "conv-1111"

    monkeypatch.setattr(
        "services.imessage_conversation_service.get_or_create_for_chat",
        _fake_get_or_create,
    )

    class _FakeSettings:
        system_prompt = "you are edward"
        model = "gpt-5.5"
        temperature = 0.7

    async def _fake_get_settings():
        return _FakeSettings()

    monkeypatch.setattr(
        "services.settings_service.get_settings", _fake_get_settings
    )

    # Avoid touching the real conversations table.
    async def _fake_mark_user_notified(conversation_id):
        return None

    monkeypatch.setattr(
        "services.conversation_service.mark_user_notified",
        _fake_mark_user_notified,
    )

    # Call the tool wrapper as Edward would.
    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "+15555550176", "message": "hi"}
    )

    assert "iMessage sent" in result

    # The listening window for that chat_identifier must now exist
    # and not yet be expired.
    assert "+15555550176" in triage_service._active_listeners, (
        "send_imessage should register a listening window for the recipient "
        "so the next inbound from that thread is auto-handled"
    )
    win = triage_service._active_listeners["+15555550176"]
    assert win.conversation_id == "conv-1111"
    assert win.expires_at > datetime.utcnow()
    assert win.expires_at <= datetime.utcnow() + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_send_imessage_to_group_registers_window_keyed_on_chat_id(monkeypatch):
    from services.graph import tools as graph_tools
    from services.heartbeat import triage_service

    triage_service._active_listeners.clear()

    monkeypatch.setattr("services.imessage_service.is_available", lambda: True)
    monkeypatch.setattr(
        "services.imessage_service.send_imessage_to_chat",
        lambda chat_id, message: {"success": True, "chat_identifier": chat_id},
    )

    async def _fake_get_or_create(chat_identifier, chat_name=None, is_group=False):
        return "conv-group"

    monkeypatch.setattr(
        "services.imessage_conversation_service.get_or_create_for_chat",
        _fake_get_or_create,
    )

    class _FakeSettings:
        system_prompt = "edward"
        model = "gpt-5.5"
        temperature = 0.7

    async def _fake_get_settings():
        return _FakeSettings()

    monkeypatch.setattr(
        "services.settings_service.get_settings", _fake_get_settings
    )

    async def _fake_mark_user_notified(conversation_id):
        return None

    monkeypatch.setattr(
        "services.conversation_service.mark_user_notified",
        _fake_mark_user_notified,
    )

    result = await graph_tools.send_imessage.ainvoke(
        {
            "contact": "ignored-when-chat-id-set",
            "message": "hi group",
            "chat_id": "chat100000000000000001",
        }
    )

    assert "iMessage sent" in result
    # Window keyed on the group chat_id, not the contact arg.
    assert "chat100000000000000001" in triage_service._active_listeners
    assert "ignored-when-chat-id-set" not in triage_service._active_listeners
