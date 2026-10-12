"""Cross-area checks the lead owns at the overnight merge (2026-10-11).

pings-media-spec §4.1.6: a follow-through intention written in a ping turn
(AREA 3's runner stamps ``turn_source="ping"`` and the chat on what the turn
remembers) passes AREA 1's gate rule for the ``thread`` channel in that
chat — ``open_ping_chats`` comes from AREA 3's ``addressed_recently`` over
real ``pings`` rows — and in no other chat.

Nothing here reaches a model, Messages or Chrome.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import noticing_service, speak_gate
from services.pings import hooks, store

pytestmark = pytest.mark.integration


async def _answered_ping_in(chat: str, *, hours_ago: float = 3.0) -> int:
    from services.database import async_session

    async with async_session() as session:
        pid = await hooks.create_ping(
            session, source_ref=f"imessage:{chat}-501", channel="imessage", kind="mention", from_owner=False,
            third_party=True, sender_known=True, asked_at=hooks.utcnow() - timedelta(hours=hours_ago),
            chat_identifier=chat, chat_guid=f"iMessage;+;{chat}", chat_name="Trivia crew", is_group=True,
            sender="+12015550101", sender_label="Parker", text="@edward keep an eye out for used skis under $150",
            message_rowid=501)
        await session.commit()
    await store.mark(pid, status="answered")
    return pid


async def _promise_from_ping(mid: str, pid: int, chat: str) -> None:
    from services.database import MemoryModel, async_session

    ref = {"kind": "extraction", "turn_source": "ping", "chat_identifier": chat, "ping_id": pid,
           "conversation_id": f"ping-{pid}"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        s.add(MemoryModel(id=mid, content="Tell the Trivia crew when a used pair under $150 shows up",
                          memory_type="intention", origin="thought", about="self", source_ref=json.dumps(ref),
                          created_at=now - timedelta(days=1), valid_until=now - timedelta(minutes=30)))
        await s.commit()


def _follow_through(chat: str) -> speak_gate.Noticing:
    now = datetime.now(timezone.utc)
    return speak_gate.Noticing(
        id="n1", kind="follow_through", category="social",
        claim="You promised in Trivia crew: tell them when a used pair under $150 shows up.",
        sources=(speak_gate.Source(ref="mem:i1", chat=chat, at=now, from_me=False),), chat=chat, who=(),
        closes_at=None, suggested="thread", dedupe_key="intention:i1", sensitive=False, origin="sweep")


async def test_a_ping_promise_passes_the_thread_rule_for_its_chat(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    pid = await _answered_ping_in("chat-trivia")
    await _promise_from_ping("i1", pid, "chat-trivia")

    out = await noticing_service._sweep_intentions(datetime.now(timezone.utc))
    assert out["intentions"] == 1, "AREA 1's sweep reads AREA 3's turn_source"

    open_chats = await noticing_service._open_ping_chats("chat-trivia")
    assert open_chats == frozenset({"chat-trivia"}), "AREA 3's addressed_recently over the real pings row"
    assert await noticing_service._open_ping_chats("chat-elsewhere") == frozenset()

    evening = datetime.now(ZoneInfo("America/Chicago")).replace(hour=19, minute=5, second=0, microsecond=0)
    n = _follow_through("chat-trivia")
    assert speak_gate.decide(n, speak_gate.GateContext(now=evening, open_ping_chats=open_chats)).channel == "thread"
    elsewhere = _follow_through("chat-elsewhere")
    gone = speak_gate.decide(elsewhere, speak_gate.GateContext(
        now=evening, open_ping_chats=await noticing_service._open_ping_chats("chat-elsewhere")))
    assert gone.channel != "thread", "never into a chat that did not ask"
