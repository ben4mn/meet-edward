"""A send that has begun is finished and recorded, even when the turn is
cancelled under it (2026-09-27 review).

The chat router now cancels the step in flight when Ellis leaves (Stop, an
app switch, a dropped network — ``routers/chat._yield_with_heartbeats``).
A send tool awaits its transport and then notes the send for the
ten-minute "Already sent" guard (``_note_sent``). ``send_imessage`` runs
osascript in a worker thread, so a cancellation there left the text going
out while the note was skipped — and a re-ask sent it again from Ellis's
number. A Twilio request cancelled mid-flight may or may not have gone
out. The transport and its note are now one unit behind
``asyncio.shield``: the send finishes, is recorded, and only then does the
cancellation go on.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from services.graph import tools as graph_tools
from tests.test_mind_enabled_gaps import _sender  # noqa: F401 — the fixture


@pytest.fixture
def _chat_turn(_sender, monkeypatch):  # noqa: F811
    async def no_standdown(target):
        return None

    monkeypatch.setattr(graph_tools, "_active_standdown_for", no_standdown)
    _sender.state["source"] = "chat"
    return _sender


async def _cancel_mid_send(coro, after_s=0.1):
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(after_s)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_an_imessage_cancelled_mid_send_is_still_recorded(_chat_turn, monkeypatch):
    import services.imessage_service as imsg

    def slow_dm(contact, message):
        time.sleep(0.3)  # osascript, in its worker thread
        _chat_turn.sent.append(("dm", contact, message))
        return {"success": True, "to": contact}

    monkeypatch.setattr(imsg, "send_imessage", slow_dm)
    await _cancel_mid_send(graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "Landing at 6:40"}))
    assert len(_chat_turn.sent) == 1, "the text went out; the cancel waited for it"
    again = await graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "Landing at 6:40"})
    assert again.startswith("Already sent to +15551230000 at "), again
    assert len(_chat_turn.sent) == 1


async def test_an_sms_cancelled_mid_request_is_finished_and_recorded(_chat_turn, monkeypatch):
    import services.twilio_service as tw

    async def slow_sms(to, message):
        await asyncio.sleep(0.3)  # the Twilio request on the wire
        _chat_turn.sent.append(("sms", to, message))
        return {"to": to, "status": "queued"}

    monkeypatch.setattr(tw, "is_configured", lambda: True)
    monkeypatch.setattr(tw, "send_sms", slow_sms)
    await _cancel_mid_send(graph_tools.send_sms.ainvoke({"phone_number": "+15551230000", "message": "Gate B12"}))
    assert len(_chat_turn.sent) == 1
    again = await graph_tools.send_sms.ainvoke({"phone_number": "+15551230000", "message": "Gate B12"})
    assert again.startswith("Already sent to +15551230000 at "), again
    assert len(_chat_turn.sent) == 1
