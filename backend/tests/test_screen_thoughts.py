"""The thinking stream on the Mac mini screen (the stage, 2026-09-28).

While a mind wake or the pulse really runs, ``/screen`` shows what Edward
is thinking: one short line per reasoning summary, and a receipt per tool
(the name only — never its args or result). When the turn ends it folds
into one line ("Earlier · 2:10 pm — …"). ``services/screen_thoughts.py``
keeps it in-process; ``turn_ledger``'s hooks feed it from both tool loops;
``GET /api/screen/live`` serves ``snapshot()``.

Nothing here may break or slow a turn: every hook is guarded, and a turn
from any source but ``mind`` / ``pulse`` is ignored outright.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from services import provenance_service, screen_thoughts
from services import turn_ledger as tl

T0 = datetime(2026, 9, 28, 21, 30, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _iso(minutes: float) -> str:
    return _at(minutes).isoformat()


def _rec(source: str = "mind", cid: str = "edward-mind") -> tl.TurnRecord:
    return tl.TurnRecord.start(conversation_id=cid, source=source, model="m", provider="openai", message="wake")


def _reasoned(*texts: str) -> AIMessage:
    """A model response carrying Codex reasoning summaries."""
    return AIMessage(content="", additional_kwargs={"reasoning_items": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": t} for t in texts]}]})


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    screen_thoughts.reset()
    monkeypatch.setattr(tl, "_schedule", lambda coro: coro.close())  # no turn_log writes
    yield
    screen_thoughts.reset()


# ------------------------------------------------------------ the stream --

def test_a_wake_reads_as_live_thinking_then_folds():
    rec = _rec("mind")
    with provenance_service.source_context(wake_reason="morning"):
        screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.note_reasoning(
        rec, ["**Checking the calendar**\n\nEllis has the dentist at 3. A note on Today will do."], now=_at(1))
    screen_thoughts.tool_started(rec, "leave_note", now=_at(2))

    mid = screen_thoughts.snapshot(now=_at(2))
    assert mid["thinking"]["receipts"] == [{"tool": "leave_note", "state": "running", "at": _iso(2)}]

    screen_thoughts.tool_finished(rec, "leave_note", True, now=_at(3))
    assert screen_thoughts.snapshot(now=_at(3)) == {
        "thinking": {
            "turn": rec.id,
            "source": "mind",
            "about": "morning",
            "since": _iso(0),
            "lines": [{"text": "Ellis has the dentist at 3. A note on Today will do.", "at": _iso(1)}],
            "receipts": [{"tool": "leave_note", "state": "ok", "at": _iso(2)}],
        },
        "folded": [],
        "now": _iso(3),
    }

    screen_thoughts.end(rec, now=_at(4))
    assert screen_thoughts.snapshot(now=_at(5)) == {
        "thinking": None,
        "folded": [{"text": "Ellis has the dentist at 3. A note on Today will do.", "about": "morning",
                    "at": _iso(4), "tools": 1}],
        "now": _iso(5),
    }


def test_the_pulse_is_about_the_surfaces_and_a_failed_tool_reads_error():
    rec = _rec("pulse", "edward-surfaces")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.tool_started(rec, "compose_screen", now=_at(1))
    screen_thoughts.tool_finished(rec, "compose_screen", False, now=_at(2))
    live = screen_thoughts.snapshot(now=_at(2))["thinking"]
    assert live["about"] == "surfaces"
    assert live["receipts"] == [{"tool": "compose_screen", "state": "error", "at": _iso(1)}]


def test_a_turn_that_said_nothing_folds_to_the_stock_line():
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.end(rec, now=_at(1))
    fold = screen_thoughts.snapshot(now=_at(1))["folded"][0]
    assert fold["text"] == "I looked things over."
    assert fold["about"] is None and fold["tools"] == 0


@pytest.mark.parametrize("outcome", ["error", "stopped"])
@pytest.mark.parametrize("said", [[], ["I'll reply to Parker now."]])
def test_a_wake_that_did_not_finish_never_folds_as_if_it_had(outcome, said):
    # Review 2026-09-28: a wake whose model call failed folded as "I looked
    # things over.", and one cut off by its timeout folded as its last
    # half-finished thought — "Earlier · 2:10 pm — I'll reply to Parker now."
    rec = tl.begin_turn(conversation_id="edward-mind", source="mind", model="m", provider="openai", message="wake")
    screen_thoughts.note_reasoning(rec, said, now=_at(1))
    screen_thoughts.tool_started(rec, "send_message", now=_at(2))
    tl.end_turn(rec, outcome, RuntimeError("codex 500") if outcome == "error" else None)
    fold = screen_thoughts.snapshot()["folded"][0]
    assert fold["text"] == screen_thoughts.FOLD_UNFINISHED == "I didn't get to finish that."
    assert fold["tools"] == 1


@pytest.mark.parametrize("outcome", ["completed", "exhausted", "empty"])
def test_a_turn_that_ended_folds_to_its_last_line(outcome):
    rec = tl.begin_turn(conversation_id="edward-mind", source="mind", model="m", provider="openai", message="wake")
    screen_thoughts.note_reasoning(rec, ["A note on Today will do."], now=_at(1))
    if outcome != "empty":
        rec.response_chars = 10
    tl.end_turn(rec, "completed" if outcome == "empty" else outcome)
    assert screen_thoughts.snapshot()["folded"][0]["text"] == "A note on Today will do."


@pytest.mark.parametrize("source", ["chat", "voice", "widget", "alexa", "heartbeat", "scheduled_event", "worker"])
def test_other_turns_never_reach_the_screen(source):
    rec = _rec(source, "c1")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.note_reasoning(rec, ["Ellis asked about the weather."], now=_at(1))
    screen_thoughts.tool_started(rec, "web_search", now=_at(1))
    screen_thoughts.tool_finished(rec, "web_search", True, now=_at(2))
    screen_thoughts.end(rec, now=_at(3))
    assert screen_thoughts.snapshot(now=_at(3)) == {"thinking": None, "folded": [], "now": _iso(3)}


def test_the_newest_live_turn_is_the_one_shown():
    first, second = _rec("mind"), _rec("pulse", "edward-surfaces")
    screen_thoughts.begin(first, now=_at(0))
    screen_thoughts.begin(second, now=_at(1))
    assert screen_thoughts.snapshot(now=_at(2))["thinking"]["turn"] == second.id
    screen_thoughts.end(second, now=_at(3))
    assert screen_thoughts.snapshot(now=_at(3))["thinking"]["turn"] == first.id


# ------------------------------------------------------------------ lines --

def test_a_secret_in_a_summary_hides_the_whole_line():
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.note_reasoning(
        rec, ["The key in the note is sk-proj-abcdefghijklmnopqrstuvwxyz0123456789, I should not use it."], now=_at(1))
    lines = screen_thoughts.snapshot(now=_at(1))["thinking"]["lines"]
    assert [ln["text"] for ln in lines] == ["[hidden: looked like a secret]"]
    assert "sk-proj" not in json.dumps(screen_thoughts.snapshot(now=_at(1)))


def test_a_secret_cut_off_by_the_cap_is_still_hidden():
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    long = "I am reading the settings page carefully. " * 5 + "Its token is ghp_" + "q7Xk2Lm9Pz4Rt8Wv1Ny6Bc3Hd5Fj0Gs2Ka9Qe" + " and more."
    screen_thoughts.note_reasoning(rec, [long], now=_at(1))
    assert screen_thoughts.snapshot(now=_at(1))["thinking"]["lines"][0]["text"] == "[hidden: looked like a secret]"


def test_a_known_live_secret_hides_the_line(monkeypatch):
    from services.graph import turn_trace

    monkeypatch.setattr(turn_trace, "known_secrets_safely", lambda: {"ELEVENLABS_API_KEY": "plainlookingvalue123456"})
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.note_reasoning(rec, ["It says plainlookingvalue123456 in the file."], now=_at(1))
    assert screen_thoughts.snapshot(now=_at(1))["thinking"]["lines"][0]["text"] == "[hidden: looked like a secret]"


@pytest.mark.parametrize("raw, shown", [
    ("**Weighing a note**\n\nBiscuit's meds are due at 6, so a note on Today is enough.",
     "Biscuit's meds are due at 6, so a note on Today is enough."),
    ("## Plan\n- check the `calendar`\n- then **rest**", "check the calendar then rest"),
    ("# Looking at messages", "Looking at messages"),
    ("1. Read the thread.\n2. Decide.", "Read the thread. Decide."),
    ("**Just a heading**", "Just a heading"),
])
def test_markdown_is_stripped(raw, shown):
    assert screen_thoughts.thought_line(raw) == shown


def test_a_long_summary_is_cut_at_a_sentence_or_a_word():
    sentences = "I read the calendar for today. " * 12
    line = screen_thoughts.thought_line(sentences)
    assert len(line) <= 220
    assert line.endswith("today.")  # whole sentences, cut between them

    words = "reading " * 60
    line = screen_thoughts.thought_line(words)
    assert len(line) <= 220
    assert line.endswith("reading…")


def test_lines_keep_the_last_eight_and_repeats_are_said_once():
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    for i in range(10):
        screen_thoughts.note_reasoning(rec, [f"Thought number {i}."], now=_at(i + 1))
    screen_thoughts.note_reasoning(rec, ["Thought number 9."], now=_at(12))  # the same summary again
    lines = screen_thoughts.snapshot(now=_at(12))["thinking"]["lines"]
    assert [ln["text"] for ln in lines] == [f"Thought number {i}." for i in range(2, 10)]


def test_receipts_keep_the_last_twelve_and_never_carry_args():
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    for i in range(15):
        screen_thoughts.tool_started(rec, f"tool_{i}", now=_at(i))
        screen_thoughts.tool_finished(rec, f"tool_{i}", True, now=_at(i))
    live = screen_thoughts.snapshot(now=_at(15))["thinking"]
    assert [r["tool"] for r in live["receipts"]] == [f"tool_{i}" for i in range(3, 15)]
    assert all(set(r) == {"tool", "state", "at"} for r in live["receipts"])
    screen_thoughts.end(rec, now=_at(16))
    assert screen_thoughts.snapshot(now=_at(16))["folded"][0]["tools"] == 15


def test_a_finish_without_a_start_still_leaves_a_receipt():
    """The loop's precheck answers a call without running it."""
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.tool_finished(rec, "web_search", False, now=_at(1))
    assert screen_thoughts.snapshot(now=_at(1))["thinking"]["receipts"] == [
        {"tool": "web_search", "state": "error", "at": _iso(1)}]


# ---------------------------------------------------------- lifetimes --

def test_a_live_turn_older_than_twenty_minutes_vanishes():
    rec = _rec("mind")
    screen_thoughts.begin(rec, now=_at(0))
    screen_thoughts.note_reasoning(rec, ["Looking around."], now=_at(1))
    assert screen_thoughts.snapshot(now=_at(19))["thinking"] is not None
    assert screen_thoughts.snapshot(now=_at(21))["thinking"] is None


def test_folds_keep_the_last_three_oldest_first():
    for i in range(5):
        rec = _rec("mind")
        screen_thoughts.begin(rec, now=_at(i))
        screen_thoughts.note_reasoning(rec, [f"Wake {i}."], now=_at(i))
        screen_thoughts.end(rec, now=_at(i + 0.5))
    folded = screen_thoughts.snapshot(now=_at(6))["folded"]
    assert [f["text"] for f in folded] == ["Wake 2.", "Wake 3.", "Wake 4."]


def test_an_untracked_end_or_a_second_end_folds_nothing():
    rec = _rec("mind")
    screen_thoughts.end(rec, now=_at(0))  # never begun
    screen_thoughts.begin(rec, now=_at(1))
    screen_thoughts.end(rec, now=_at(2))
    screen_thoughts.end(rec, now=_at(3))
    assert len(screen_thoughts.snapshot(now=_at(3))["folded"]) == 1


def test_the_hooks_never_raise_on_odd_input():
    screen_thoughts.begin(None)
    screen_thoughts.note_reasoning(None, ["x"])
    screen_thoughts.tool_started(object(), "x")
    screen_thoughts.tool_finished(object(), None, True)
    rec = _rec("mind")
    screen_thoughts.begin(rec)
    screen_thoughts.note_reasoning(rec, [None, 5, "", "   "])
    screen_thoughts.end(None)
    assert screen_thoughts.snapshot()["thinking"]["lines"] == []


# --------------------------------------------------- turn_ledger hooks --

def test_the_turn_ledger_feeds_a_mind_turn_through_its_hooks():
    with provenance_service.source_context(wake_reason="evening"):
        rec = tl.begin_turn(conversation_id="edward-mind", source="mind", model="m", provider="openai")
    tl.note_response_reasoning(_reasoned("**Looking**\n\nNothing needs Ellis tonight."))
    tl.note_response_reasoning(_reasoned("**Looking**\n\nNothing needs Ellis tonight."))  # same response again
    tl.note_tool_started("leave_note")
    live = screen_thoughts.snapshot()["thinking"]
    assert live["about"] == "evening"
    assert [ln["text"] for ln in live["lines"]] == ["Nothing needs Ellis tonight."]
    assert live["receipts"][0]["tool"] == "leave_note" and live["receipts"][0]["state"] == "running"

    tl.note_tool_result("leave_note", "Left a note on Today.", ms=12)
    assert screen_thoughts.snapshot()["thinking"]["receipts"][0]["state"] == "ok"
    tl.end_turn(rec, "completed")
    snap = screen_thoughts.snapshot()
    assert snap["thinking"] is None
    assert snap["folded"][0]["text"] == "Nothing needs Ellis tonight." and snap["folded"][0]["tools"] == 1


def test_the_tool_start_hook_does_not_change_what_the_turn_records():
    rec = tl.begin_turn(conversation_id="edward-mind", source="mind", model="m", provider="openai")
    tl.note_tool_started("leave_note")
    tl.note_tool_result("leave_note", "Left a note on Today.", ms=12)
    assert rec.tools == [{"n": "leave_note", "ok": True, "ms": 12}]
    assert rec._open_tools == {}
    tl.end_turn(rec, "completed")


def test_a_chat_turn_through_the_hooks_leaves_nothing():
    rec = tl.begin_turn(conversation_id="c1", source="chat", model="m", provider="openai")
    tl.note_response_reasoning(_reasoned("Ellis wants the weather."))
    tl.note_tool_started("web_search")
    tl.note_tool_result("web_search", "sunny", ms=3)
    tl.end_turn(rec, "completed")
    assert screen_thoughts.snapshot()["thinking"] is None
    assert screen_thoughts.snapshot()["folded"] == []


async def test_the_streamed_loop_hooks_feed_it_too():
    rec = tl.TurnRecord.start(conversation_id="edward-mind", source="mind", model="m", provider="openai")
    screen_thoughts.begin(rec)

    async def _events():
        yield {"type": "tool_start", "tool_name": "browser_read"}
        yield {"type": "tool_end", "tool_name": "browser_read", "result": "Tool error: no tab"}
        yield {"type": "done"}

    async for _ in tl.observe_events(rec, _events()):
        pass
    fold = screen_thoughts.snapshot()["folded"][0]
    assert fold["tools"] == 1


def test_a_broken_stream_never_breaks_the_turn(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("screen gone")

    for name in ("begin", "note_reasoning", "tool_started", "tool_finished", "end"):
        monkeypatch.setattr(screen_thoughts, name, boom)
    rec = tl.begin_turn(conversation_id="edward-mind", source="mind", model="m", provider="openai")
    tl.note_response_reasoning(_reasoned("Fine."))
    tl.note_tool_started("leave_note")
    tl.note_tool_result("leave_note", "ok", ms=1)
    rec.note_content("Done.", streaming=False)
    tl.end_turn(rec, "completed")
    assert rec.outcome == "completed" and rec.reasoning == ["Fine."] and len(rec.tools) == 1


# ------------------------------------------------------------ the route --

async def _no_recent(now=None):
    return []


async def test_the_live_route_serves_the_snapshot(monkeypatch):
    from routers import screen as screen_router

    monkeypatch.setattr(screen_thoughts, "recent", _no_recent)  # round 2: tests/test_screen_recent_thoughts.py
    rec = _rec("pulse", "edward-surfaces")
    screen_thoughts.begin(rec)
    out = await screen_router.get_live()
    assert out["thinking"]["source"] == "pulse"
    assert set(out) == {"thinking", "folded", "now", "recent"}


async def test_the_live_route_never_500s(monkeypatch):
    from routers import screen as screen_router

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(screen_thoughts, "recent", _no_recent)
    monkeypatch.setattr(screen_thoughts, "snapshot", boom)
    out = await screen_router.get_live()
    assert out["thinking"] is None and out["folded"] == []
    assert datetime.fromisoformat(out["now"]).tzinfo is not None


def test_the_live_route_is_mounted():
    from main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/screen/live" in paths


# ------------------------------------------------ the non-streaming loop --

async def test_a_wake_in_the_non_streaming_loop_shows_its_tools_while_they_run(monkeypatch):
    """Wakes and the pulse run on ``chat_with_memory``; its loop now calls
    the tool-start hook before each call, so the screen shows the receipt
    as ``running`` while the tool is still working."""
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io

    seen = []

    def _while_running(args):
        seen.append(screen_thoughts.snapshot())
        return "Left a note on Today."

    tool = FakeTool("leave_note", _while_running)
    first = ai("", call("leave_note", title="Meds", body="the password is hunter2-please"))
    first.additional_kwargs["reasoning_items"] = [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "**Meds**\n\nBiscuit's meds are due at 6."}]}]
    patch_loop_io(monkeypatch, tools=[tool], script=[first, ai("Left Ellis a note.")])

    with provenance_service.source_context(wake_reason="evening"):
        await streaming.chat_with_memory(message="evening wake", turn_source="mind", skip_extraction=True,
                                         **base_kwargs(FakeGraph()))

    assert len(tool.calls) == 1
    during = seen[0]["thinking"]
    assert during["about"] == "evening"
    assert during["receipts"] == [{"tool": "leave_note", "state": "running", "at": during["receipts"][0]["at"]}]
    assert [ln["text"] for ln in during["lines"]] == ["Biscuit's meds are due at 6."]

    after = screen_thoughts.snapshot()
    assert after["thinking"] is None
    assert after["folded"][-1] == {"text": "Biscuit's meds are due at 6.", "about": "evening",
                                   "at": after["folded"][-1]["at"], "tools": 1}
    assert "hunter2" not in json.dumps(seen) + json.dumps(after)

    rec = tl.last_finished_turn()
    assert [t["n"] for t in rec.tools] == ["leave_note"] and rec._open_tools == {}
