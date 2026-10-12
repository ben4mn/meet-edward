"""A PWA chat turn is the current turn in every step (review of
edward/audit-small, 2026-09-27).

``routers/chat._yield_with_heartbeats`` runs each step of the chat stream
(each ``__anext__``) in its own task, so a quiet stretch can send an SSE
heartbeat without cancelling the step. Each of those tasks copied the
*outer* context, so what a step set in a context variable was gone in the
next one. ``turn_ledger.begin_turn`` sets the turn record in the first
step; from the second step on, and in the post-turn work, a PWA chat turn
had no turn:

* the send guard (``graph/tools._send_guard``) let everything through and
  ``_note_sent`` recorded nothing — no "Already sent" for a text Ellis's
  number had just sent;
* no used-memory credit (``memory_service.credit_cited_memories``), no
  ``note_memories`` from the context builder, and the post-turn extraction
  ran with ``turn_id=None`` (``turn_log.memories_extracted`` never set);
* the spend ledger (``codex_chat_model._turn_context``) could not label
  the turn's model calls ``turn:chat``;
* ``remember`` in a later step and the post-turn extraction wrote no
  ``turn_id`` / ``turn_source`` into their provenance.

Now every step of one stream runs in one context of its own: a copy of the
caller's, taken when the stream starts, shared by the whole turn and never
leaking back into the caller's.
"""

from __future__ import annotations

import asyncio
import contextvars
import json

import pytest
from langchain_core.messages import AIMessage


def _spy_turns(monkeypatch):
    """The records ``begin_turn`` starts, in order."""
    from services import turn_ledger

    begun = []
    real_begin = turn_ledger.begin_turn

    def spy(**kw):
        rec = real_begin(**kw)
        begun.append(rec)
        return rec

    monkeypatch.setattr(turn_ledger, "begin_turn", spy)
    return begun


async def _pwa_turn(conversation_id, message, graph):
    """One turn the way ``POST /api/chat`` runs it: the router's
    ``_wrap_chat_stream`` behind ``_yield_with_heartbeats``."""
    from routers import chat as chat_router
    from services.graph.tools import set_current_conversation_id

    set_current_conversation_id(conversation_id)  # the router's generate() does
    inner = chat_router._wrap_chat_stream(
        message=message, conversation_id=conversation_id, system_prompt="be edward",
        model="gpt-6-astra", temperature=0.4, graph=graph, attachments=None, provider="openai",
    )
    chunks = [c async for c in chat_router._yield_with_heartbeats(inner, heartbeat_interval=10.0)]
    assert '"type": "done"' in chunks[-1]
    return [json.loads(c[len("data: "):]) for c in chunks if c.startswith("data: ")]


async def test_every_step_of_a_stream_runs_in_one_context_of_its_own():
    from routers.chat import _yield_with_heartbeats

    var: contextvars.ContextVar = contextvars.ContextVar("turn_probe", default=None)
    seen = []

    async def turn():
        var.set("the turn")  # the first step, as begin_turn does
        yield {"type": "progress", "step": "memory_search", "status": "started"}
        await asyncio.sleep(0.25)  # a quiet stretch: heartbeats go out meanwhile
        seen.append(var.get())
        yield {"type": "content", "content": "hi"}
        seen.append(var.get())
        yield {"type": "done"}

    chunks = [c async for c in _yield_with_heartbeats(turn(), heartbeat_interval=0.1)]
    assert any(c.startswith(": heartbeat") for c in chunks)
    assert seen == ["the turn", "the turn"], "what the first step set holds in every later step"
    assert var.get() is None, "the turn's context does not leak into the caller's"


async def test_a_pwa_chat_turn_is_the_current_turn_in_every_step(monkeypatch):
    from services import codex_chat_model, turn_ledger
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, call, patch_loop_io

    cid = "chat-every-step"
    seen = {}

    def probe(args):  # a tool runs in a later step than begin_turn
        seen["tool"] = turn_ledger.current_turn()
        return "ok"

    def closing_call(messages):  # the model call after the tool round
        seen["model"] = codex_chat_model._turn_context()
        return AIMessage(content="All set.")

    patch_loop_io(monkeypatch, tools=[FakeTool("probe", probe)], script=[ai("", call("probe")), closing_call])
    begun = _spy_turns(monkeypatch)

    noted = []
    real_note = turn_ledger.TurnRecord.note_memories

    def spy_note(self, retrieved=None, used=None):
        noted.append((self.id, used))
        return real_note(self, retrieved, used)

    monkeypatch.setattr(turn_ledger.TurnRecord, "note_memories", spy_note)

    post_turn = []

    async def spawn(**kw):
        post_turn.append(kw)

    monkeypatch.setattr(streaming, "_spawn_post_turn_work", spawn)

    await _pwa_turn(cid, "What's on today?", FakeGraph())

    (rec,) = begun
    assert rec.source == "chat" and rec.outcome == "completed"
    assert seen["tool"] is rec, "a tool call sees its turn (the send guard, remember's provenance)"
    assert seen["model"] == {"turn_source": "chat", "turn_id": rec.id, "conversation_id": cid}, \
        "the spend ledger labels the model call with its turn"
    assert (rec.id, 0) in noted, "the context builder notes the memories it used on the turn"
    assert [p["turn_id"] for p in post_turn] == [rec.id], "the post-turn extraction knows its turn"
    assert turn_ledger.current_turn() is None, "the turn stays out of the caller's context"


async def test_a_text_the_pwa_chat_sent_is_not_sent_again_from_a_re_ask(monkeypatch):
    """The behaviour change: the ten-minute "Already sent" guard now holds for
    sends from the PWA chat, as it already did for voice and every other
    turn. Ellis re-asking (after a Stop, or not seeing the tool's answer) no
    longer sends the same text twice from his number."""
    import services.conversation_service as conversation_service
    import services.twilio_service as twilio
    from services.graph import tools as graph_tools
    from tests.fixtures.tool_loop import FakeGraph, ai, call, patch_loop_io

    sent = []

    async def fake_sms(to, message):
        sent.append((to, message))
        return {"to": to, "status": "queued"}

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(twilio, "is_configured", lambda: True)
    monkeypatch.setattr(twilio, "send_sms", fake_sms)
    monkeypatch.setattr(conversation_service, "mark_user_notified", nothing)
    graph_tools.reset_send_history()
    try:
        patch_loop_io(monkeypatch, tools=[graph_tools.send_sms], script=[
            ai("", call("send_sms", phone_number="+15551230000", message="Gate B12")),
            AIMessage(content="Sent."),
            ai("", call("send_sms", phone_number="+15551230000", message="Gate B12")),
            AIMessage(content="That one already went."),
        ])
        graph = FakeGraph()
        await _pwa_turn("chat-re-ask", "Text Sam: Gate B12", graph)
        events = await _pwa_turn("chat-re-ask", "Text Sam: Gate B12", graph)
    finally:
        graph_tools.reset_send_history()

    assert len(sent) == 1, sent
    results = [e["result"] for e in events if e.get("type") == "tool_end" and e.get("tool_name") == "send_sms"]
    assert len(results) == 1 and results[0].startswith("Already sent to +15551230000 at "), results


@pytest.mark.integration
async def test_what_a_pwa_chat_remembers_carries_its_turn(clean_db, monkeypatch):
    """``remember`` in a later step and the post-turn extraction write the
    turn's id and source into their provenance."""
    from sqlalchemy import select

    from services import memory_service
    from services.database import MemoryModel, async_session
    from services.graph.tools import remember
    from services.provenance_service import parse_source_ref
    from tests.fixtures.tool_loop import FakeGraph, ai, call, patch_loop_io

    real_extract = memory_service.extract_and_store_memories
    cid = "chat-provenance"
    patch_loop_io(monkeypatch, tools=[remember], script=[
        ai("", call("remember", content="Never book me anything before 9am.", memory_type="instruction",
                    about="owner", origin="heard", importance=0.9)),
        AIMessage(content="Got it — porch light on Fridays, nothing before 9."),
    ])
    monkeypatch.setattr(memory_service, "extract_and_store_memories", real_extract)
    begun = _spy_turns(monkeypatch)

    class _Extractor:
        async def ainvoke(self, messages):
            class R:
                content = json.dumps([{
                    "content": "Keep the porch light on on Fridays.", "memory_type": "instruction",
                    "importance": 0.9, "temporal_nature": "permanent", "source_type": "user_stated",
                    "confidence": 0.95, "origin": "heard", "about": "owner",
                }])

            return R()

    async def _nothing(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _Extractor())
    monkeypatch.setattr("services.entity_service.extract_entities", _nothing)
    monkeypatch.setattr("services.implication_service.run_implication_safe", _nothing)

    await _pwa_turn(cid, "Keep the porch light on on Fridays, and never book me anything before 9am.",
                    FakeGraph())
    (rec,) = begun

    rows = []
    for _ in range(100):  # the extraction runs after ``done``
        async with async_session() as s:
            rows = (await s.execute(select(MemoryModel).where(MemoryModel.memory_type == "instruction"))).scalars().all()
        if len(rows) >= 2:
            break
        await asyncio.sleep(0.05)
    refs = {parse_source_ref(r.source_ref).get("kind"): parse_source_ref(r.source_ref) for r in rows}
    assert set(refs) == {"extraction", "remember"}, [(r.content, r.source_ref) for r in rows]
    for kind, ref in refs.items():
        assert (ref.get("turn_id"), ref.get("turn_source")) == (rec.id, "chat"), (kind, ref)
