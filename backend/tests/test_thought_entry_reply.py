"""Replying inside a journal entry keeps the entry (2026-09-25).

A wake that acts writes one ``edward_thought`` conversation whose
``initial_message`` is the entry ("Why I looked / What I noticed / What I
did / Why") and whose ``context_pack`` says what the wake was looking at.
Ellis reads it and replies "why?". Before this:

* neither chat loop read ``initial_message`` or ``context_pack`` (only the
  graph's unused ``respond_node`` did), so Edward answered "why?" without
  the entry he was asked about;
* ``GET /api/conversations/{id}`` synthesised the entry only while the
  checkpoint was empty, so the moment Ellis replied the entry vanished from
  the thread he was reading.

Now the first turn in such a conversation seeds the entry as an assistant
message (sent to the model *and* checkpointed with the turn), every turn in
a journal entry carries its context in the system prompt, and the API puts
the entry first whenever the checkpoint does not already hold it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from services import thought_journal as tj

SEND = {"name": "send_imessage", "ok": True, "held": False,
        "args": {"contact": "Parker", "message": "Ellis's in a meeting till 4", "chat_id": "chat123"},
        "result": "iMessage sent to group chat chat123"}
NARRATIVE = ("**Why I looked** — A mention: Parker mentioned me in Family\n\n"
             "**What I did**\n- Sent an iMessage to Parker (Family): “Ellis's in a meeting till 4”\n\n"
             "**Why** — Parker asked me directly; the calendar says a meeting until 4.")
EXCERPT = "[WAKE: mention]\nWaiting on you:\n- ev-1 Parker in Family: @edward is Ellis free at 3?"


def _pack(**over):
    kw = dict(ledger_id=7, reason="mention", wake_thread="edward-mind", turn_id="turn-1", actions=[SEND],
              acted_at=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc), pack_excerpt=EXCERPT,
              names={"chat123": "Family"})
    kw.update(over)
    return json.dumps(tj.entry_context_pack(**kw), default=str)


# ------------------------------------------------------------------ pure --

class TestEntryTurnContext:
    pytestmark = pytest.mark.unit

    def test_a_first_reply_opens_with_the_entry_and_carries_its_context(self):
        opening, block = tj.entry_turn_context(initial_message=NARRATIVE, context_pack=_pack(), history=[])
        assert opening == NARRATIVE
        assert "## This conversation is one of your journal entries" in block
        assert "A mention" in block and "`edward-mind`" in block
        assert "- Sent an iMessage to Parker (Family): “Ellis's in a meeting till 4”" in block
        assert "ev-1 Parker in Family: @edward is Ellis free at 3?" in block
        # The entry itself is in the history; the prompt does not repeat it.
        assert "What you wrote then" not in block

    def test_a_later_reply_is_not_seeded_again_but_keeps_the_context(self):
        history = [AIMessage(content=NARRATIVE), HumanMessage(content="why?"), AIMessage(content="Because…")]
        opening, block = tj.entry_turn_context(initial_message=NARRATIVE, context_pack=_pack(), history=history)
        assert opening is None
        assert "ev-1 Parker in Family" in block and "What you wrote then" not in block

    def test_a_thread_that_no_longer_holds_the_entry_gets_it_in_the_prompt(self):
        """An entry Ellis replied to before this fix (or one trimmed out of a
        long thread): the words he is asking about come back through the
        prompt, since they can no longer be put first in the history."""
        history = [HumanMessage(content="why?"), AIMessage(content="I'm not sure what you mean.")]
        opening, block = tj.entry_turn_context(initial_message=NARRATIVE, context_pack=_pack(), history=history)
        assert opening is None
        assert "What you wrote then:" in block and "Parker asked me directly" in block

    def test_the_context_is_bounded(self):
        opening, block = tj.entry_turn_context(
            initial_message=NARRATIVE, context_pack=_pack(pack_excerpt="x" * 20_000),
            history=[HumanMessage(content="why?")])
        assert len(block) <= tj.MAX_ENTRY_PROMPT_CHARS
        assert "What you wrote then:" in block  # the narrative outranks the pack

    def test_an_older_thought_gets_its_pack_on_the_first_reply_only(self):
        """The pre-journal thought conversations carry a raw signal pack;
        they keep what ``respond_node`` gave them: the pack on the first
        reply, then the history carries it."""
        legacy = json.dumps({"unread_messages": [{"sender": "Rowan", "summary": "still on for 7?"}]})
        opening, block = tj.entry_turn_context(initial_message="You've got an unread from Rowan.",
                                               context_pack=legacy, history=[])
        assert opening == "You've got an unread from Rowan."
        assert "signal pack" in block and "still on for 7?" in block
        history = [AIMessage(content="You've got an unread from Rowan."), HumanMessage(content="what unreads?"),
                   AIMessage(content="Rowan asked if you're still on for 7.")]
        assert tj.entry_turn_context(initial_message="You've got an unread from Rowan.",
                                     context_pack=legacy, history=history) == (None, "")

    def test_a_plain_conversation_gets_nothing(self):
        assert tj.entry_turn_context(initial_message=None, context_pack=None, history=[]) == (None, "")
        assert tj.entry_turn_context(initial_message="  ", context_pack="{not json", history=[]) == (None, "")
        assert tj.entry_turn_context(initial_message=None, context_pack=None,
                                     history=[HumanMessage(content="hi")]) == (None, "")


class TestTheApiPutsTheEntryFirst:
    pytestmark = pytest.mark.unit

    def _m(self, role, content):
        from routers.conversations import MessageResponse

        return MessageResponse(role=role, content=content)

    def test_an_empty_thread_shows_the_entry(self):
        from routers.conversations import with_opening

        out = with_opening([], NARRATIVE)
        assert [(m.role, m.content) for m in out] == [("assistant", NARRATIVE)]

    def test_a_thread_that_holds_the_entry_is_left_alone(self):
        from routers.conversations import with_opening

        msgs = [self._m("assistant", NARRATIVE), self._m("user", "why?"), self._m("assistant", "Because…")]
        assert with_opening(msgs, NARRATIVE) == msgs

    def test_a_thread_that_lost_the_entry_gets_it_back_first(self):
        from routers.conversations import with_opening

        msgs = [self._m("user", "why?"), self._m("assistant", "Because…")]
        out = with_opening(msgs, NARRATIVE)
        assert [m.content for m in out] == [NARRATIVE, "why?", "Because…"]

    def test_no_opening_changes_nothing(self):
        from routers.conversations import with_opening

        msgs = [self._m("user", "hi")]
        assert with_opening(msgs, None) == msgs and with_opening(msgs, "   ") == msgs


# ----------------------------------------------------------- the loops --

async def _entry() -> str:
    tid = await tj.record_entry(reason="mention", woke_for="Parker mentioned me in Family", reasoning=[],
                                closing_note="Parker asked me directly; the calendar says a meeting until 4.",
                                actions=[SEND], ledger_id=None, wake_thread="edward-mind", turn_id="turn-1",
                                pack_excerpt=EXCERPT, names={"chat123": "Family"})
    assert tid
    return tid


def _patch(monkeypatch, script):
    """The real loops with a scripted model; the entry lookup stays real."""
    from services.graph import streaming
    from tests.fixtures.tool_loop import patch_loop_io

    real = streaming._load_entry_row
    h = patch_loop_io(monkeypatch, tools=[], script=script)
    monkeypatch.setattr(streaming, "_load_entry_row", real)
    return h


async def _api_messages(monkeypatch, graph, tid):
    from httpx import ASGITransport, AsyncClient

    import routers.conversations as rc
    from main import app
    from services.auth_service import create_access_token

    async def _graph():
        return graph

    monkeypatch.setattr(rc, "get_graph", _graph)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        r = await c.get(f"/api/conversations/{tid}")
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def _count(messages, text):
    return sum(1 for m in messages if isinstance(m, AIMessage) and m.content == text)


@pytest.mark.integration
async def test_replying_in_an_entry_keeps_the_entry_streaming(clean_db, monkeypatch):
    from services.conversation_service import get_conversation
    from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs, run_stream

    tid = await _entry()
    narrative = (await get_conversation(tid)).initial_message
    graph = FakeGraph()
    h = _patch(monkeypatch, [ai("Parker asked me directly, so I answered."), ai("No, only that.")])

    await run_stream(message="why did you answer Parker?", **base_kwargs(graph, conversation_id=tid))
    sent = h.calls[0]["messages"]
    # The model reads the entry before Ellis's question…
    assert isinstance(sent[1], AIMessage) and sent[1].content == narrative
    assert isinstance(sent[2], HumanMessage) and sent[2].content == "why did you answer Parker?"
    # …and what the wake was looking at.
    assert "## This conversation is one of your journal entries" in sent[0].content
    assert "ev-1 Parker in Family: @edward is Ellis free at 3?" in sent[0].content
    # The checkpoint keeps it first, so the thread Ellis reads keeps it.
    assert graph.messages[0].content == narrative
    msgs = await _api_messages(monkeypatch, graph, tid)
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert msgs[0]["content"] == narrative

    await run_stream(message="anything else?", **base_kwargs(graph, conversation_id=tid))
    assert _count(h.calls[1]["messages"], narrative) == 1
    assert _count(graph.messages, narrative) == 1
    assert "ev-1 Parker in Family" in h.calls[1]["messages"][0].content
    msgs = await _api_messages(monkeypatch, graph, tid)
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == narrative


@pytest.mark.integration
async def test_replying_in_an_entry_keeps_the_entry_non_streaming(clean_db, monkeypatch):
    """A scheduled event Ellis set from inside an entry fires there too."""
    from services.conversation_service import get_conversation
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs

    tid = await _entry()
    narrative = (await get_conversation(tid)).initial_message
    graph = FakeGraph()
    h = _patch(monkeypatch, [ai("Because he asked me directly."), ai("Still nothing new.")])

    await streaming.chat_with_memory(message="why?", turn_source="chat", **base_kwargs(graph, conversation_id=tid))
    sent = h.calls[0]["messages"]
    assert isinstance(sent[1], AIMessage) and sent[1].content == narrative
    assert "ev-1 Parker in Family" in sent[0].content
    assert graph.messages[0].content == narrative

    await streaming.chat_with_memory(message="and now?", turn_source="chat", **base_kwargs(graph, conversation_id=tid))
    assert _count(h.calls[1]["messages"], narrative) == 1 and _count(graph.messages, narrative) == 1


@pytest.mark.integration
async def test_an_entry_replied_to_before_the_fix_shows_its_entry_again(clean_db, monkeypatch):
    from services.conversation_service import get_conversation
    from tests.fixtures.tool_loop import FakeGraph

    tid = await _entry()
    narrative = (await get_conversation(tid)).initial_message
    graph = FakeGraph([HumanMessage(content="why?", id="h1"), AIMessage(content="Not sure.", id="a1")])
    msgs = await _api_messages(monkeypatch, graph, tid)
    assert [m["content"] for m in msgs] == [narrative, "why?", "Not sure."]


@pytest.mark.integration
async def test_an_ordinary_chat_is_untouched(clean_db, monkeypatch):
    from services.conversation_service import create_conversation
    from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs, run_stream

    await create_conversation("chat-1", title="Dinner", source="user")
    graph = FakeGraph()
    h = _patch(monkeypatch, [ai("Pasta.")])
    await run_stream(message="what's for dinner?", **base_kwargs(graph, conversation_id="chat-1"))
    sent = h.calls[0]["messages"]
    assert [type(m).__name__ for m in sent] == ["SystemMessage", "HumanMessage"]
    assert "journal entries" not in sent[0].content
