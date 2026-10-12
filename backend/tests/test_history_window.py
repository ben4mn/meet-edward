"""Unit tests for conversation history windowing.

Regression origin (2026-08-25): the per-contact heartbeat conversation for
Rowan had grown to 2,333 messages / 3.73M characters because the chat path
sent `[system] + messages` in full on every tool-loop iteration with no
trimming. At that size gpt-5.4 returned empty completions — no text, no tool
calls — and the required-tool nudge loop appended two more messages per
retry, so each failure made the next one worse (296 empty AI turns and 198
nudges accumulated). An @edward mention from Rowan then burned the full
300s respond timeout twice and never sent a reply.
"""

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.graph.history_window import dead_turn_ids, drop_dead_turns, trim_history

pytestmark = pytest.mark.unit


def _ai_with_call(call_id: str, name: str = "send_imessage") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": {}}],
    )


class TestDropDeadTurns:
    def test_removes_empty_ai_turn_and_its_nudge(self):
        msgs = [
            HumanMessage(content="[HEARTBEAT EVENT] ..."),
            AIMessage(content=""),
            HumanMessage(
                content="[SYSTEM] You have tools available but didn't use any. "
                "You MUST use your tools to complete this task."
            ),
            AIMessage(content="real answer"),
        ]
        out = drop_dead_turns(msgs)
        assert [type(m).__name__ for m in out] == ["HumanMessage", "AIMessage"]
        assert out[-1].content == "real answer"

    def test_removes_required_tool_nudge_variant(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content=""),
            HumanMessage(
                content="[SYSTEM] You have not yet called the required tool(s): "
                "send_imessage. You MUST call these tools before finishing."
            ),
        ]
        assert drop_dead_turns(msgs) == msgs[:1]

    def test_keeps_empty_ai_that_carries_tool_calls(self):
        """An AIMessage with empty content but real tool_calls is a normal
        tool-calling turn, not a dead turn — dropping it orphans its result."""
        call = _ai_with_call("c1")
        msgs = [
            HumanMessage(content="do it"),
            call,
            ToolMessage(content="sent", tool_call_id="c1"),
        ]
        assert drop_dead_turns(msgs) == msgs

    def test_keeps_ordinary_history_untouched(self):
        msgs = [
            HumanMessage(content="hello"),
            AIMessage(content="hi there"),
            HumanMessage(content="thanks"),
        ]
        assert drop_dead_turns(msgs) == msgs

    def test_collapses_the_rowan_failure_shape(self):
        """26 consecutive empty-turn/nudge pairs collapse to nothing."""
        msgs = [HumanMessage(content="[HEARTBEAT EVENT] ...")]
        for _ in range(26):
            msgs.append(AIMessage(content=""))
            msgs.append(HumanMessage(content="[SYSTEM] You have tools available but didn't use any."))
        assert len(drop_dead_turns(msgs)) == 1


class TestTrimHistory:
    def test_under_budget_is_unchanged(self):
        msgs = [HumanMessage(content="a" * 10), AIMessage(content="b" * 10)]
        assert trim_history(msgs, max_chars=1000) == msgs

    def test_over_budget_keeps_the_most_recent(self):
        msgs = [HumanMessage(content=f"msg{i}" + "x" * 100) for i in range(50)]
        out = trim_history(msgs, max_chars=1000)
        assert len(out) < len(msgs)
        assert out[-1] is msgs[-1], "must keep the newest message"
        total = sum(len(str(m.content)) for m in out)
        assert total <= 1000

    def test_never_orphans_a_tool_message(self):
        """Dropping an AIMessage while keeping its ToolMessage produces a
        tool_call_id with no originating call — OpenAI rejects that outright."""
        msgs = [HumanMessage(content="x" * 5000)]
        msgs.append(_ai_with_call("call-1"))
        msgs.append(ToolMessage(content="r" * 900, tool_call_id="call-1"))
        out = trim_history(msgs, max_chars=1000)
        kept_ids = {
            tc["id"]
            for m in out
            if isinstance(m, AIMessage)
            for tc in (m.tool_calls or [])
        }
        for m in out:
            if isinstance(m, ToolMessage):
                assert m.tool_call_id in kept_ids, "orphaned ToolMessage survived trimming"

    def test_drops_whole_tool_group_when_parent_does_not_fit(self):
        msgs = [
            _ai_with_call("call-1"),
            ToolMessage(content="r" * 400, tool_call_id="call-1"),
            HumanMessage(content="q" * 400),
        ]
        out = trim_history(msgs, max_chars=450)
        assert not any(isinstance(m, ToolMessage) for m in out)

    def test_always_keeps_at_least_the_last_message(self):
        msgs = [HumanMessage(content="a" * 100), HumanMessage(content="b" * 9999)]
        out = trim_history(msgs, max_chars=10)
        assert len(out) == 1
        assert out[0] is msgs[-1]

    def test_empty_input(self):
        assert trim_history([], max_chars=100) == []


class TestDeadTurnIds:
    """`add_messages` is a merge reducer: omitting a message from a state
    update leaves it in the checkpoint. Persistently pruning a poisoned
    thread means emitting an explicit RemoveMessage per dead turn, which
    needs their ids."""

    def test_returns_ids_of_dead_turns_and_their_nudges(self):
        msgs = [
            HumanMessage(content="real", id="h1"),
            AIMessage(content="", id="a1"),
            HumanMessage(content="[SYSTEM] You have tools available but didn't use any.", id="h2"),
            AIMessage(content="answer", id="a2"),
        ]
        assert dead_turn_ids(msgs) == ["a1", "h2"]

    def test_ignores_messages_without_ids(self):
        msgs = [AIMessage(content=""), AIMessage(content="", id="a9")]
        assert dead_turn_ids(msgs) == ["a9"]

    def test_returns_nothing_for_a_clean_thread(self):
        msgs = [
            HumanMessage(content="hi", id="h1"),
            AIMessage(content="hello", id="a1"),
        ]
        assert dead_turn_ids(msgs) == []

    def test_does_not_target_tool_calling_turns(self):
        call = AIMessage(content="", id="a1", tool_calls=[{"id": "c1", "name": "x", "args": {}}])
        msgs = [call, ToolMessage(content="ok", tool_call_id="c1", id="t1")]
        assert dead_turn_ids(msgs) == []


class TestWindowingIsActuallyWired:
    """Pin the call sites. The old loop guard had this test; the
    windowing did not, so dropping a windowing call would have failed
    nothing while silently re-opening the 3.7M-char thread.

    Updated 2026-09-24 to the current shape: dead turns (and every other
    nudge) are removed by ``strip_scaffolding`` on load, and every LLM call
    is windowed by ``turn_window.window_for_call`` (history before the
    trigger bounded, the trigger never evicted). The original assertions
    counted ``trim_history(messages)`` four times; after the 2026-09-14
    ``max_history_chars`` change only two literal matches were left, so
    the test failed by inspection while the wiring was fine.
    """

    def _impl_sources(self):
        import inspect

        from services.graph import streaming

        return (inspect.getsource(streaming._stream_with_memory_events_impl),
                inspect.getsource(streaming._chat_with_memory_impl))

    def test_both_chat_paths_drop_dead_turns_on_load(self):
        for src in self._impl_sources():
            assert "strip_scaffolding(loaded)" in src, (
                "stream_with_memory_events and chat_with_memory must both drop dead turns and nudges"
            )

    def test_every_llm_call_in_both_paths_sends_a_windowed_history(self):
        """Both paths run ``tool_loop.run_tool_loop`` (2026-10-11): every
        model call there — the main one and the closing call — goes through
        ``_window``, which is ``turn_window.window_for_call``."""
        import inspect

        from services.graph import tool_loop

        for src in self._impl_sources():
            assert "tool_loop.run_tool_loop(" in src
        loop_src = inspect.getsource(tool_loop)
        assert "turn_window.window_for_call(" in inspect.getsource(tool_loop._window)
        assert loop_src.count("_window(ctx)") >= 2, "main + closing calls must send a windowed history"
        assert "+ trim_history(messages" not in loop_src and "+ ctx.messages\n" not in loop_src

    def test_strip_scaffolding_keeps_what_drop_dead_turns_keeps(self):
        from services.graph.history_window import strip_scaffolding

        msgs = [
            HumanMessage(content="[HEARTBEAT EVENT] ..."),
            AIMessage(content=""),
            HumanMessage(content="[SYSTEM] You have tools available but didn't use any."),
            AIMessage(content="real answer"),
        ]
        assert strip_scaffolding(msgs) == drop_dead_turns(msgs)


class TestStaleMessageIds:
    """Regression origin (2026-09-12): `trim_history` bounded what went over
    the wire, but the checkpoint kept every message — Rowan's heartbeat
    thread wrote a 9.5 MB `messages` blob on every turn and the checkpoint
    tables grew from 1.1 GB to 1.3 GB in two days. The ids returned here are
    what a RemoveMessage update needs to make the checkpoint match the window.
    """

    def test_nothing_stale_when_history_fits(self):
        from services.graph.history_window import stale_message_ids

        msgs = [HumanMessage(content="hi", id="h1"), AIMessage(content="hello", id="a1")]
        assert stale_message_ids(msgs, max_chars=1000) == []

    def test_oldest_messages_beyond_the_window_are_stale(self):
        from services.graph.history_window import stale_message_ids

        msgs = [
            HumanMessage(content="a" * 100, id="h1"),
            AIMessage(content="b" * 100, id="a1"),
            HumanMessage(content="c" * 100, id="h2"),
            AIMessage(content="d" * 100, id="a2"),
        ]
        assert stale_message_ids(msgs, max_chars=250) == ["h1", "a1"]

    def test_a_tool_result_never_outlives_its_call(self):
        from services.graph.history_window import stale_message_ids

        call = _ai_with_call("call-1")
        call.id = "a1"
        msgs = [
            HumanMessage(content="x" * 100, id="h1"),
            call,
            ToolMessage(content="y" * 100, tool_call_id="call-1", id="t1"),
            HumanMessage(content="z" * 100, id="h2"),
            AIMessage(content="w" * 100, id="a2"),
        ]
        stale = stale_message_ids(msgs, max_chars=250)
        assert "t1" in stale and "a1" in stale and "h1" in stale
        assert "h2" not in stale and "a2" not in stale

    def test_messages_without_ids_are_skipped(self):
        from services.graph.history_window import stale_message_ids

        msgs = [HumanMessage(content="a" * 100), AIMessage(content="b" * 100, id="a1"),
                HumanMessage(content="c" * 100, id="h2")]
        assert stale_message_ids(msgs, max_chars=120) == ["a1"]
