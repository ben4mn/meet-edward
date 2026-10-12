"""The tool loop's small decisions, as pure functions (tools-spec §5.3–§5.5).

Kept from Part D: invalid JSON is answered, never run as ``{}``; a turn
that stops early says why and what ran, never "I completed the requested
actions"; the loop's scaffolding never reaches a checkpoint.

New with the toolset: the identical-failure stop (the old circuit breaker,
now working on the uniform failure contract — it was blind to ~80% of
failures), and the reply check (§5.5) that replaced the required-tool
nudge and forced ``tool_choice``.

Gone, with their tests: the generic "you MUST use your tools" nudge, the
forced ``tool_choice`` (a 400 on Opus/Sonnet 5.5), the repeat guard and
reuse cache (0 fires in 8.4 days; identical commands legitimately re-run),
the required-tool nudge, and the plan nudge.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from services.graph import tool_policy as tp
from services.graph.history_window import strip_scaffolding
from services.toolset.policy import TurnPolicy

pytestmark = pytest.mark.unit


class TestTheOldGuardsAreGone:
    @pytest.mark.parametrize("name", ["generic_nudge_allowed", "missing_required_tools", "required_tool_nudge",
                                      "forced_tool_name", "named_tool_choice", "RepeatGuard", "TIME_VARYING_TOOLS",
                                      "REQUIRED_TOOL_EQUIVALENTS"])
    def test_removed(self, name):
        assert not hasattr(tp, name)


class TestIdenticalFailureStop:
    def test_the_third_identical_failure_is_refused(self):
        t = tp.FailureTracker()
        args = {"command": "curl https://x"}
        for _ in range(2):
            assert t.refusal("bash", args) is None
            t.record("bash", args, is_error=True, text="Tool error: curl: (6) Could not resolve host: x")
        out = t.refusal("bash", dict(args))
        assert out.startswith("Tool error: this exact call failed twice already (")
        assert "Could not resolve host" in out
        assert "another route" in out

    def test_key_order_does_not_matter(self):
        t = tp.FailureTracker()
        for _ in range(2):
            t.record("memory", {"action": "update", "id": "m1"}, is_error=True, text="Tool error: nope")
        assert t.refusal("memory", {"id": "m1", "action": "update"}) is not None

    def test_a_change_is_a_new_call(self):
        t = tp.FailureTracker()
        for _ in range(2):
            t.record("bash", {"command": "ls /x"}, is_error=True, text="Tool error: no")
        assert t.refusal("bash", {"command": "ls /y"}) is None

    def test_success_never_counts_and_resets_nothing(self):
        t = tp.FailureTracker()
        a = {"query": "q"}
        t.record("web_fetch", a, is_error=True, text="Tool error: 503")
        t.record("web_fetch", a, is_error=False, text="page")
        t.record("web_fetch", a, is_error=False, text="page")
        assert t.refusal("web_fetch", a) is None
        t.record("web_fetch", a, is_error=True, text="Tool error: 503")
        assert t.refusal("web_fetch", a) is not None

    def test_identical_successes_rerun_freely(self):
        """Polling a job log, re-reading a file after an edit."""
        t = tp.FailureTracker()
        for _ in range(10):
            assert t.refusal("bash", {"command": "tail -5 jobs/x.log"}) is None
            t.record("bash", {"command": "tail -5 jobs/x.log"}, is_error=False, text="…")


class TestReplyCheck:
    CHAT = "iMessage;+;chat123"
    POLICY = TurnPolicy(source="mind", may_reach_people=True, reply_to=(CHAT,))

    def test_a_send_into_the_chat(self):
        assert tp.reply_satisfied(self.POLICY, [("send_message", {"chat_id": "chat123", "message": "hi"}, True)])

    def test_a_failed_send_is_not_a_reply(self):
        assert not tp.reply_satisfied(self.POLICY, [("send_message", {"chat_id": "chat123"}, False)])

    def test_a_send_elsewhere_is_not_a_reply(self):
        assert not tp.reply_satisfied(self.POLICY, [("send_message", {"chat_id": "chat999"}, True)])

    def test_a_stand_down_for_it(self):
        assert tp.reply_satisfied(self.POLICY, [("messages", {"action": "stand_down", "chat_id": "chat123"}, True)])
        assert not tp.reply_satisfied(self.POLICY, [("messages", {"action": "recent"}, True)])

    def test_tell_owner_does_not_answer_someone_elses_chat(self):
        """Critique #23 (review 2026-10-11): a note to Ellis leaves the person
        who asked with nothing — in someone else's chat only a send there
        or a stand-down answers it."""
        assert not tp.reply_satisfied(self.POLICY, [("tell_owner", {"message": "Not mine to answer"}, True)])
        friend = TurnPolicy(source="ping", may_reach_people=True, reply_to=("chat123",), third_party=True)
        assert not tp.reply_satisfied(friend, [("tell_owner", {"message": "Matt asked about dinner"}, True)])

    def test_a_legacy_mention_wake_is_not_answered_by_a_note(self):
        from services import mind_service
        from services.toolset.policy import legacy_policy

        p = legacy_policy("mind", allowed_tools=list(mind_service.LEGACY_WAKE_TOOLS), required_tools=["send_imessage"])
        for name in ("tell_owner", "leave_note", "send_push_notification"):
            assert not tp.reply_satisfied(p, [(name, {"message": "Matt asked about dinner"}, True)]), name
        assert tp.reply_satisfied(p, [("send_imessage", {"chat_id": "chat-family", "message": "7pm"}, True)])

    def test_in_owners_own_turn_tell_owner_counts(self):
        mine = TurnPolicy(source="ping_owner", owner_asked=True, may_reach_people=True, reply_to=(self.CHAT,))
        assert tp.reply_satisfied(mine, [("tell_owner", {"message": "Left it on Today"}, True)])
        chat = TurnPolicy(source="chat", owner_asked=True, any_reply=True)
        assert tp.reply_satisfied(chat, [("leave_note", {"title": "x"}, True)])

    def test_legacy_names_count(self):
        assert tp.reply_satisfied(self.POLICY, [("send_imessage", {"chat_id": "chat123"}, True)])
        assert tp.reply_satisfied(self.POLICY, [("stand_down", {"chat_id": "chat123"}, True)])
        assert not tp.reply_satisfied(self.POLICY, [("leave_note", {"title": "x"}, True)])

    def test_any_reply_for_a_legacy_required_send(self):
        p = TurnPolicy(source="mind", may_reach_people=True, any_reply=True)
        assert tp.reply_satisfied(p, [("send_message", {"to": "Parker"}, True)])
        assert not tp.reply_satisfied(p, [("memory", {"action": "remember"}, True)])

    def test_reply_note(self):
        assert tp.reply_note(self.POLICY).startswith("[SYSTEM] You were asked in iMessage;+;chat123")
        assert 'send_message(chat_id="iMessage;+;chat123")' in tp.reply_note(self.POLICY)
        assert tp.reply_note(TurnPolicy(any_reply=True)).startswith("[SYSTEM] You were asked")


class TestResultClassification:
    @pytest.mark.parametrize("text", ["Tool error: x", "Unknown tool: nope", "BLOCKED: x"])
    def test_failures(self, text):
        assert tp.is_failure_result(text) is True
        assert tp.is_unproductive_result(text) is True

    def test_a_real_result_is_productive(self):
        assert tp.is_unproductive_result("3 results") is False
        assert tp.is_failure_result("fine") is False


class TestInvalidJson:
    def test_invalid_json_args_are_answered_not_run(self):
        raw = '{"query": "unterminated' + "x" * 900
        msg = tp.invalid_json_error("web_search", {"__invalid_json__": raw})
        assert msg.startswith("Tool error: the arguments for web_search were not valid JSON: ")
        assert msg.endswith(raw[:500])

    def test_valid_args_pass(self):
        assert tp.invalid_json_error("web_search", {"query": "x"}) is None
        assert tp.invalid_json_error("web_search", {}) is None


class TestHonestEnding:
    def test_says_why_and_what_was_done(self):
        text = tp.honest_ending(
            "it ran out of tool rounds (60)",
            [("browser", "ok"), ("look", "ok"), ("look", "Tool error: x"), ("send_message", "Unknown tool: x")],
        )
        assert text.startswith("I stopped before finishing: it ran out of tool rounds (60).")
        assert "browser" in text and "look ×2 (1 failed)" in text
        assert "send_message (failed)" in text
        assert "completed the requested actions" not in text

    def test_no_tools(self):
        text = tp.honest_ending("the model returned nothing", [])
        assert "No tools ran" in text


class TestScaffoldingNeverPersists:
    def test_every_scaffold_kind_is_stripped(self):
        msgs = [
            HumanMessage(content="do the thing"),
            AIMessage(content="I would do it"),
            HumanMessage(content="[SYSTEM] You have tools available but didn't use any. You MUST use your tools."),
            AIMessage(content="", tool_calls=[{"id": "c", "name": "x", "args": {}}]),
            AIMessage(content=""),
            HumanMessage(content="[SYSTEM] Three rounds in a row failed. Step back: …"),
            HumanMessage(content="[SYSTEM] You were asked in chat1 and haven't replied there. …"),
            HumanMessage(content="[SYSTEM] Stop here: you are out of time for this turn (600s). …"),
            HumanMessage(content="[SYSTEM] You have not yet called the required tool(s): send_imessage."),
            HumanMessage(content="You still have 2 incomplete plan step(s):\n- a\n- b"),
            AIMessage(content="final"),
        ]
        out = strip_scaffolding(msgs)
        texts = [m.content for m in out]
        assert texts == ["do the thing", "I would do it", "", "final"]
        assert out[2].tool_calls
