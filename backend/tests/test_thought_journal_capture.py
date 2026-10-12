"""What a turn did, captured for the thought journal (2026-09-25).

A journal entry is built from records, not from the model's say-so: the
tool executors note every call in the journal's action set on the turn
(``TurnRecord.actions``: name, bounded args, result head, ok, held), and
the loops note the reasoning summaries the Codex adapter returns
(``TurnRecord.reasoning``). Capture is bookkeeping: it never raises into a
turn.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from services import turn_ledger as tl
from services.graph import streaming
from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io, run_stream


def _rec(source="mind"):
    return tl.TurnRecord.start(conversation_id="edward-mind", source=source, model="m", provider="openai")


def _reasoning(*texts, rid="r1"):
    return {"reasoning_items": [{"type": "reasoning", "id": rid, "encrypted_content": "blob",
                                 "summary": [{"type": "summary_text", "text": t} for t in texts]}]}


# ---------------------------------------------------------------- pure --

@pytest.mark.unit
def test_note_action_keeps_only_journal_tools():
    rec = _rec()
    rec.note_action("remember_search", {"query": "Parker"}, "3 memories")
    rec.note_action("web_search", {"query": "x"}, "results")
    rec.note_action("send_imessage", {"contact": "Parker", "message": "hi", "chat_id": "chat1"},
                    "iMessage sent to group chat chat1")
    rec.note_action("remember", {"content": "Parker asked"}, "ok (memory_id: m-1)")
    rec.note_action("mail", {"operation": "unread"}, "3 unread")
    rec.note_action("mail", {"operation": "send", "to": "a@b.c", "subject": "s", "body": "b"}, "Email sent")
    assert [a["name"] for a in rec.actions] == ["send_imessage", "remember", "mail"]
    first = rec.actions[0]
    assert first == {"name": "send_imessage", "args": {"contact": "Parker", "message": "hi", "chat_id": "chat1"},
                     "result": "iMessage sent to group chat chat1", "ok": True}


@pytest.mark.unit
def test_note_action_marks_failures_and_held_sends():
    rec = _rec()
    rec.note_action("send_imessage", {"contact": "Parker", "message": "hi"}, "Failed to send iMessage: timeout")
    rec.note_action("send_imessage", {"contact": "Parker", "message": "hi"},
                    "Waiting for Ellis's approval (#4). It runs when he taps Approve.")
    assert rec.actions[0]["ok"] is False and "held" not in rec.actions[0]
    assert rec.actions[1]["ok"] is True and rec.actions[1]["held"] is True


@pytest.mark.unit
def test_note_action_bounds_args_and_result():
    class Odd:
        def __repr__(self):
            return "<odd>"

    rec = _rec()
    rec.note_action("save_document", {"title": "t", "content": "x" * 5000, "tags": None,
                                      "nested": {"deep": ["y" * 1000, Odd(), 3, 2.5, True]}},
                    "Document saved: " + "z" * 2000)
    a = rec.actions[0]
    assert len(a["args"]["content"]) <= tl.ACTION_ARG_MAX_CHARS
    assert a["args"]["tags"] is None
    deep = a["args"]["nested"]["deep"]
    assert len(deep[0]) <= tl.ACTION_ARG_MAX_CHARS and deep[1] == "<odd>" and deep[2:] == [3, 2.5, True]
    assert len(a["result"]) <= tl.ACTION_RESULT_MAX_CHARS
    import json
    json.dumps(a)  # JSON-safe


@pytest.mark.unit
def test_note_action_is_capped_per_turn():
    rec = _rec()
    for i in range(tl.MAX_ACTIONS_PER_TURN + 10):
        rec.note_action("remember", {"content": f"m{i}"}, "ok")
    assert len(rec.actions) == tl.MAX_ACTIONS_PER_TURN


@pytest.mark.unit
def test_note_reasoning_reads_summary_texts_dedupes_and_bounds():
    rec = _rec()
    rec.note_reasoning(AIMessage(content="", additional_kwargs=_reasoning("**Reading**\n\nParker asked.", "Then the calendar.")))
    rec.note_reasoning(AIMessage(content="", additional_kwargs=_reasoning("**Reading**\n\nParker asked.")))
    rec.note_reasoning(AIMessage(content="no reasoning here"))
    rec.note_reasoning(None)
    rec.note_reasoning(AIMessage(content="", additional_kwargs={"reasoning_items": [
        {"type": "reasoning", "summary": [{"type": "other", "text": "skip"}, "junk", {"type": "summary_text"}]},
        "junk"]}))
    assert rec.reasoning == ["**Reading**\n\nParker asked.", "Then the calendar."]
    for _ in range(20):
        rec.note_reasoning(AIMessage(content="", additional_kwargs=_reasoning("t" * 500 + str(_), rid=str(_))))
    assert sum(len(t) for t in rec.reasoning) <= tl.REASONING_MAX_CHARS


@pytest.mark.unit
def test_module_hooks_are_quiet_without_a_turn():
    tl._current.set(None)
    tl.note_action_result("send_imessage", {"contact": "x", "message": "y"}, "iMessage sent")
    tl.note_response_reasoning(AIMessage(content="", additional_kwargs=_reasoning("x")))


@pytest.mark.unit
def test_capture_never_raises_into_a_turn(monkeypatch):
    from services import thought_journal

    def boom(*a, **k):
        raise RuntimeError("tiering broke")

    monkeypatch.setattr(thought_journal, "action_tier", boom)
    rec = _rec()
    tl._current.set(rec)
    try:
        tl.note_action_result("send_imessage", {"contact": "x", "message": "y"}, "iMessage sent")
        assert rec.actions == []
    finally:
        tl._current.set(None)


# ------------------------------------------------------------ the loops --

SEND = call("send_imessage", contact="Parker", message="Ellis's in a meeting till 4", chat_id="chat1")


@pytest.fixture(autouse=True)
def _open_boundary(monkeypatch):
    """The boundary lets every call through here (its own tests live in
    test_taint_executor.py); a test that wants a refusal patches it again."""
    async def run_it(name, args):
        return None

    monkeypatch.setattr(streaming, "_boundary_check", run_it)


async def test_the_non_streaming_loop_records_actions_and_reasoning(monkeypatch):
    tools = [FakeTool("send_imessage", "iMessage sent to group chat chat1"),
             FakeTool("remember_search", "nothing"), FakeTool("remember", "ok (memory_id: m-1)")]
    script = [
        ai("", call("remember_search", query="Parker"), additional_kwargs=_reasoning("Parker asked where Ellis is.")),
        ai("", SEND, call("remember", content="Parker asked at 3"),
           additional_kwargs=_reasoning("The calendar says a meeting till 4.", rid="r2")),
        ai("I told Parker Ellis is in a meeting until 4."),
    ]
    patch_loop_io(monkeypatch, tools=tools, script=script)
    out = await streaming.chat_with_memory(message="[WAKE: mention]", turn_source="mind", skip_extraction=True,
                                           **base_kwargs(FakeGraph(), conversation_id="edward-mind"))
    assert out == "I told Parker Ellis is in a meeting until 4."
    turn = tl.last_finished_turn()
    assert [a["name"] for a in turn.actions] == ["send_imessage", "remember"]
    assert turn.actions[0]["args"]["chat_id"] == "chat1" and turn.actions[0]["ok"] is True
    assert turn.reasoning == ["Parker asked where Ellis is.", "The calendar says a meeting till 4."]


async def test_a_refused_send_is_recorded_as_held(monkeypatch):
    tools = [FakeTool("send_imessage", "iMessage sent to group chat chat1")]
    patch_loop_io(monkeypatch, tools=tools, script=[ai("", SEND), ai("Asked Ellis first.")])

    async def held(name, args):
        return "Waiting for Ellis's approval (#9). It runs when he taps Approve."

    monkeypatch.setattr(streaming, "_boundary_check", held)
    await streaming.chat_with_memory(message="[WAKE: messages]", turn_source="mind", skip_extraction=True,
                                     **base_kwargs(FakeGraph(), conversation_id="edward-mind"))
    turn = tl.last_finished_turn()
    assert tools[0].calls == []
    assert turn.actions == [{"name": "send_imessage", "args": SEND["args"],
                             "result": "Waiting for Ellis's approval (#9). It runs when he taps Approve.",
                             "ok": True, "held": True}]


async def test_a_tool_that_raises_is_recorded_as_failed(monkeypatch):
    tools = [FakeTool("send_imessage", RuntimeError("osascript died"))]
    patch_loop_io(monkeypatch, tools=tools, script=[ai("", SEND), ai("Could not send.")])
    await streaming.chat_with_memory(message="[WAKE: messages]", turn_source="mind", skip_extraction=True,
                                     **base_kwargs(FakeGraph(), conversation_id="edward-mind"))
    turn = tl.last_finished_turn()
    assert turn.actions[0]["ok"] is False and "osascript died" in turn.actions[0]["result"]


async def test_the_streaming_loop_records_actions_too(monkeypatch):
    tools = [FakeTool("send_imessage", "iMessage sent to group chat chat1")]
    script = [ai("", SEND, additional_kwargs=_reasoning("Parker needs an answer.")), ai("Told Parker.")]
    patch_loop_io(monkeypatch, tools=tools, script=script)
    events = await run_stream(message="tell Parker I'm in a meeting", turn_source="chat",
                              **base_kwargs(FakeGraph()))
    assert any(e.get("type") == "done" for e in events)
    turn = tl.last_finished_turn()
    assert [a["name"] for a in turn.actions] == ["send_imessage"]
    assert turn.reasoning == ["Parker needs an answer."]
