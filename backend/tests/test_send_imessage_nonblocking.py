"""osascript iMessage sends must not block the event loop.

The AppleScript send is a blocking ``subprocess.run`` with a 30s timeout.
Before this change the tool called it directly on the event loop, freezing
the entire backend (FastAPI, heartbeat loop, scheduler) for up to 30s per
send. It must run in a worker thread via ``asyncio.to_thread``.
"""

import asyncio
import threading
import time

import pytest

from services.graph import tools as graph_tools


@pytest.fixture(autouse=True)
def _stub_imessage(monkeypatch):
    import services.imessage_service as imsg

    calls = {}

    def slow_send(contact, message):
        calls["thread"] = threading.current_thread()
        time.sleep(0.3)  # deliberately blocking
        return {"success": True, "to": contact}

    def slow_send_chat(chat_id, message):
        calls["thread"] = threading.current_thread()
        time.sleep(0.3)
        return {"success": True, "to": chat_id}

    monkeypatch.setattr(imsg, "send_imessage", slow_send)
    monkeypatch.setattr(imsg, "send_imessage_to_chat", slow_send_chat)
    monkeypatch.setattr(imsg, "is_available", lambda: True)

    # Listening-window registration touches the DB — not under test.
    import services.heartbeat.triage_service as ts

    monkeypatch.setattr(
        ts, "register_listening_window_for_chat",
        lambda *a, **kw: _async_none(),
    )
    return calls


def _async_none():
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


async def test_send_runs_off_the_event_loop(_stub_imessage):
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    try:
        result = await graph_tools.send_imessage.ainvoke(
            {"contact": "+15555550174", "message": "ping"}
        )
    finally:
        task.cancel()

    assert "sent" in result.lower()
    # The 0.3s blocking send must not have frozen the loop: the ticker keeps
    # running while the send executes in a worker thread.
    assert ticks >= 10, f"event loop was blocked during send (ticks={ticks})"
    assert _stub_imessage["thread"] is not threading.main_thread()


async def test_group_send_runs_off_the_event_loop(_stub_imessage):
    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "ignored", "message": "ping", "chat_id": "chat123"}
    )
    assert "sent" in result.lower()
    assert _stub_imessage["thread"] is not threading.main_thread()


async def test_send_message_over_imessage_runs_off_the_event_loop(_stub_imessage, monkeypatch):
    """``send_message(channel="imessage")`` called the blocking transport on
    the loop; since 2026-10-11 the transport also reads chat.db back (up to
    5 s), so it must run in a worker thread too."""
    import services.skills_service as skills

    async def enabled(_skill):
        return True

    monkeypatch.setattr(skills, "is_skill_enabled", enabled)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    try:
        result = await graph_tools.send_message.ainvoke(
            {"recipient": "+15555550174", "message": "ping", "channel": "imessage"}
        )
    finally:
        task.cancel()
    assert "sent" in result.lower()
    # The decisive fact is the worker thread; the ticks only show the loop
    # kept turning (loose: this machine also serves production).
    assert ticks >= 3, f"event loop was blocked during send (ticks={ticks})"
    assert _stub_imessage["thread"] is not threading.main_thread()
