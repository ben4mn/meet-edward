"""D1: the history window must never evict the current task.

Regression origin (docs/muse-and-beyond-plan-2026-09-24.md, Part D1): the
mind and the pulse pass ``max_history_chars=24_000`` and the non-streaming
loop re-applied that window on every tool iteration. Inside a turn the
oldest group is the wake pack — the task itself — so a 6K pack followed by
three 14–16K browser reads left the model looking at
``[AI→browser_read, Tool]`` from iteration 3 on: a tool result with no
reason for having called it. It explored until the 240 s ``wait_for``
fired, and the turn-end trim then removed the pack from the thread for good.

The window now has two parts: ``max_history_chars`` bounds what came
*before* the turn's trigger, and the turn itself gets its own budget where
only this turn's oldest tool results are elided — never the trigger.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.graph import turn_window as tw
from services.graph.history_window import message_cost, stale_message_ids, trim_history

pytestmark = pytest.mark.unit


def _call(call_id: str, name: str = "browser_read", **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def _tool(call_id: str, n: int, ch: str = "r") -> ToolMessage:
    return ToolMessage(content=ch * n, tool_call_id=call_id)


def _pack_turn():
    """Old history, then a 6K pack and three 15K tool results this turn."""
    history = [HumanMessage(content="old question " * 500), AIMessage(content="old answer " * 500)]
    trigger = HumanMessage(content="P" * 6_000)
    turn = [
        trigger,
        _call("c1", url="https://a"), _tool("c1", 15_000, "a"),
        _call("c2", ref=3), _tool("c2", 15_000, "b"),
        _call("c3"), _tool("c3", 15_000, "c"),
    ]
    return history, trigger, turn


class TestToolResultCap:
    def test_short_results_pass_through(self):
        assert tw.cap_tool_result("ok") == "ok"

    def test_long_result_is_capped_with_a_marker(self):
        out = tw.cap_tool_result("x" * 40_000)
        assert len(out) <= tw.TOOL_RESULT_MAX_CHARS
        assert out.startswith("x" * 1000)
        assert "[truncated:" in out
        # The marker says how much is missing so the model can decide to ask again.
        assert str(40_000 - out.index("\n[truncated:")) in out

    def test_a_tool_error_keeps_its_prefix(self):
        out = tw.cap_tool_result("Tool error: " + "e" * 30_000)
        assert out.startswith("Tool error:")


class TestListResults:
    """Image and PDF results (``look``) are content blocks: only their text
    is capped (tools-spec §5.3.5), never the image."""

    def test_the_cap_is_30k(self):
        assert tw.TOOL_RESULT_MAX_CHARS == 30_000

    def test_text_blocks_are_capped_images_kept(self):
        img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "A" * 90_000}}
        out = tw.cap_tool_result([{"type": "text", "text": "t" * 50_000}, img])
        assert isinstance(out, list) and out[1] is img
        assert len(out[0]["text"]) <= tw.TOOL_RESULT_MAX_CHARS and "[truncated:" in out[0]["text"]

    def test_the_turn_is_not_elided_when_told_not_to(self):
        """Under Claude, editing earlier results mid-turn breaks thinking
        and the cache (tools-spec §5.6): the whole turn goes as it is."""
        history, trigger, turn = _pack_turn()
        msgs = history + turn
        out = tw.window_for_call(msgs, len(history), turn_max_chars=20_000, elide_turn=False)
        assert out[len(history):] == turn
        out = tw.window_for_call(msgs, len(history), turn_max_chars=20_000)
        assert any(tw.is_elided(m.content) for m in out if isinstance(m, ToolMessage))


class TestElision:
    def test_elided_text_keeps_a_head_and_says_what_is_gone(self):
        out = tw.elide_text("h" * 15_000)
        assert out.startswith("h" * tw.ELIDED_HEAD_CHARS)
        assert "[elided: 13500 chars — call the tool again if you need it]" in out
        assert tw.is_elided(out)

    def test_short_text_is_not_elided(self):
        assert tw.elide_text("short") == "short"


class TestMessageCost:
    def test_tool_call_arguments_count(self):
        """A 10K ``type_text`` argument is 10K characters on the wire."""
        plain = AIMessage(content="hi")
        call = AIMessage(content="hi", tool_calls=[{"id": "c", "name": "browser_type", "args": {"text": "t" * 10_000}}])
        assert message_cost(call) >= message_cost(plain) + 10_000

    def test_trim_history_counts_arguments(self):
        msgs = [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[{"id": "c", "name": "save_document", "args": {"content": "d" * 5_000}}]),
            ToolMessage(content="saved", tool_call_id="c"),
            HumanMessage(content="next"),
        ]
        out = trim_history(msgs, max_chars=1_000)
        assert out == [msgs[-1]]


class TestWindowForCall:
    def test_the_trigger_survives_a_small_history_window(self):
        """The Part D1 simulation: with the mind's 24K window the pack used
        to be gone by iteration 3. Now it is on the wire at iteration 4."""
        history, trigger, turn = _pack_turn()
        messages = history + turn
        out = tw.window_for_call(messages, turn_start=len(history), history_max_chars=24_000)
        assert any(m is trigger for m in out)
        # Nothing of this turn is dropped at the default turn budget (51K < 120K).
        for m in turn:
            assert any(o is m or getattr(o, "tool_call_id", None) == getattr(m, "tool_call_id", "-") for o in out)

    def test_history_before_the_turn_is_bounded_by_the_window(self):
        history, trigger, turn = _pack_turn()
        out = tw.window_for_call(history + turn, turn_start=len(history), history_max_chars=1_000)
        assert not any(m is history[0] or m is history[1] for m in out)
        assert out[0] is trigger

    def test_over_the_turn_budget_the_oldest_tool_result_is_elided_first(self):
        history, trigger, turn = _pack_turn()
        out = tw.window_for_call(history + turn, turn_start=len(history), history_max_chars=24_000,
                                 turn_max_chars=40_000)
        tools = [m for m in out if isinstance(m, ToolMessage)]
        assert [t.tool_call_id for t in tools] == ["c1", "c2", "c3"], "no tool group is dropped"
        assert tw.is_elided(tools[0].content), "the oldest result of the turn goes first"
        assert not tw.is_elided(tools[-1].content), "the newest result stays whole"
        assert out[out.index(trigger)].content == "P" * 6_000, "the trigger is never elided"
        # The originals are untouched: the checkpoint keeps what the tool said.
        assert turn[2].content == "a" * 15_000

    def test_elision_stops_once_under_budget(self):
        history, trigger, turn = _pack_turn()
        out = tw.window_for_call(history + turn, turn_start=len(history), history_max_chars=0,
                                 turn_max_chars=40_000)
        elided = [m for m in out if isinstance(m, ToolMessage) and tw.is_elided(m.content)]
        assert len(elided) == 1

    def test_never_drops_even_when_nothing_more_can_be_elided(self):
        trigger = HumanMessage(content="T" * 50_000)
        msgs = [trigger, _call("c1"), _tool("c1", 500)]
        out = tw.window_for_call(msgs, turn_start=0, turn_max_chars=10_000)
        assert out[0] is trigger and len(out) == 3

    def test_no_tool_message_is_ever_orphaned(self):
        history = [
            HumanMessage(content="h" * 3_000),
            _call("old"), _tool("old", 3_000),
            AIMessage(content="a" * 100),
        ]
        _, trigger, turn = _pack_turn()
        out = tw.window_for_call(history + turn, turn_start=len(history), history_max_chars=2_000,
                                 turn_max_chars=30_000)
        live = {c["id"] for m in out if isinstance(m, AIMessage) for c in (m.tool_calls or [])}
        for m in out:
            if isinstance(m, ToolMessage):
                assert m.tool_call_id in live
        for m in out:
            if isinstance(m, AIMessage) and m.tool_calls:
                answered = {t.tool_call_id for t in out if isinstance(t, ToolMessage)}
                assert all(c["id"] in answered for c in m.tool_calls), "a call kept without its result"

    def test_without_a_turn_start_it_is_the_old_window(self):
        msgs = [HumanMessage(content=f"m{i}" + "x" * 100) for i in range(40)]
        assert tw.window_for_call(msgs, turn_start=None, history_max_chars=1_000) == trim_history(msgs, 1_000)


class TestClosingAnInterruptedTurn:
    def test_unanswered_calls_get_results_and_the_running_one_says_so(self):
        trigger = HumanMessage(content="pack")
        call = AIMessage(content="", tool_calls=[
            {"id": "a", "name": "send_imessage", "args": {}},
            {"id": "b", "name": "browser_open", "args": {}},
            {"id": "c", "name": "leave_note", "args": {}},
        ])
        msgs = [HumanMessage(content="old"), trigger, call, ToolMessage(content="Sent.", tool_call_id="a")]
        out = tw.close_open_tool_calls(msgs, turn_start=1)
        results = {m.tool_call_id: m.content for m in out if isinstance(m, ToolMessage)}
        assert results == {"a": "Sent.", "b": tw.INTERRUPTED_RESULT, "c": tw.NOT_RUN_RESULT}
        assert out[:4] == msgs

    def test_a_complete_turn_is_unchanged(self):
        msgs = [HumanMessage(content="q"), _call("x"), _tool("x", 5), AIMessage(content="done")]
        assert tw.close_open_tool_calls(msgs, 0) == msgs


class TestCheckpointNeverDropsTheTrigger:
    def test_the_latest_trigger_is_pinned(self):
        history, trigger, turn = _pack_turn()
        msgs = history + turn + [AIMessage(content="done")]
        for i, m in enumerate(msgs):
            m.id = f"m{i}"
        stale = stale_message_ids(msgs, max_chars=24_000, pinned=[trigger])
        assert trigger.id not in stale
        assert msgs[-1].id not in stale
        # Without the pin the old behaviour holds (and would drop the trigger).
        assert trigger.id in stale_message_ids(msgs, max_chars=24_000)
