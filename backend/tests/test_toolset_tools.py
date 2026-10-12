"""``messages``, ``tell_owner``, ``screen`` and ``plan`` (tools-spec §3.8,
§3.10, §3.13, §3.14): each wraps today's implementation behind one action
schema, fails under the uniform contract, and obeys the turn's policy.
"""

from __future__ import annotations

import sys
import types

import pytest

from services.graph import tools as legacy
from services.toolset import messaging as M
from services.toolset import plan as PL
from services.toolset import policy as P
from services.toolset import screen as SC
from services.toolset import tell_owner as TB
from services.toolset.result import ToolFailure

pytestmark = pytest.mark.unit

OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
WAKE = P.TurnPolicy(source="mind")
PUSHY_WAKE = P.TurnPolicy(source="mind", may_push=True)


class Recorder:
    def __init__(self, reply="ok"):
        self.calls = []
        self.reply = reply

    async def ainvoke(self, args):
        self.calls.append(dict(args))
        return self.reply(args) if callable(self.reply) else self.reply


def _patch(monkeypatch, name, reply="ok"):
    rec = Recorder(reply)
    monkeypatch.setattr(legacy, name, rec)
    return rec


class TestMessages:
    async def test_actions_route(self, monkeypatch):
        recent = _patch(monkeypatch, "get_recent_messages", "Recent messages (2 in the last 24h): …")
        thread = _patch(monkeypatch, "read_imessage_thread", "thread …")
        parts = _patch(monkeypatch, "list_chat_participants", "Participants …")
        who = _patch(monkeypatch, "lookup_contact", "Found 1 contact(s)")
        phone = _patch(monkeypatch, "lookup_phone", "Found 1 contact(s)")
        sd = _patch(monkeypatch, "stand_down", "Stood down in chat1.")
        await M.messages_impl(action="recent", contact="Parker", hours=6)
        await M.messages_impl(action="thread", chat_id="chat1", limit=20, before_rowid=99)
        await M.messages_impl(action="participants", chat_id="chat1")
        await M.messages_impl(action="who", query="Parker")
        await M.messages_impl(action="who", query="+1 201 555 0100")
        out = await M.messages_impl(action="stand_down", chat_id="chat1", requested_by="Rowan")
        assert recent.calls == [{"contact": "Parker", "hours": 6}]
        assert thread.calls == [{"chat_identifier": "chat1", "limit": 20, "before_rowid": 99}]
        assert parts.calls == [{"chat_identifier": "chat1"}]
        assert who.calls == [{"query": "Parker"}] and phone.calls == [{"phone_number": "+1 201 555 0100"}]
        assert sd.calls == [{"chat_id": "chat1", "requested_by": "Rowan"}] and out.startswith("Stood down")

    async def test_failures_raise(self, monkeypatch):
        _patch(monkeypatch, "get_recent_messages", "iMessage history unavailable — no readable chat.db")
        with pytest.raises(ToolFailure, match="unavailable"):
            await M.messages_impl(action="recent")
        with pytest.raises(ToolFailure, match="chat_id"):
            await M.messages_impl(action="thread")
        with pytest.raises(ToolFailure, match="action must be"):
            await M.messages_impl(action="send")


class TestTellOwner:
    @pytest.fixture(autouse=True)
    def _push_skill(self, monkeypatch):
        async def on():
            return True

        monkeypatch.setattr(TB, "_push_skill_on", on)

    async def test_a_note_by_default(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "services.reach_service", raising=False)
        monkeypatch.setattr(TB, "_reach_service", lambda: None)
        note = _patch(monkeypatch, "leave_note", "ok")
        with P.policy_scope(WAKE):
            out = await TB.tell_owner_impl(message="Parker asked about Friday", title="Parker")
        assert out.startswith("Left Ellis a note")
        assert note.calls == [{"title": "Parker", "body": "Parker asked about Friday", "needs_you": False,
                               "url": None}]

    async def test_urgent_needs_may_push(self, monkeypatch):
        monkeypatch.setattr(TB, "_reach_service", lambda: None)
        push = _patch(monkeypatch, "send_push_notification", "Push notification sent to 2 device(s).")
        with P.policy_scope(WAKE):
            with pytest.raises(ToolFailure, match="can't push"):
                await TB.tell_owner_impl(message="Biscuit's pill is late", urgent=True)
        assert push.calls == []
        with P.policy_scope(PUSHY_WAKE):
            out = await TB.tell_owner_impl(message="Biscuit's pill is late", urgent=True)
        assert out.startswith("Pushed Ellis")
        assert push.calls[0]["urgency"] == "normal" and push.calls[0]["body"] == "Biscuit's pill is late"

    async def test_a_failed_push_raises(self, monkeypatch):
        monkeypatch.setattr(TB, "_reach_service", lambda: None)
        _patch(monkeypatch, "send_push_notification", "No active push subscriptions. User has not enabled notifications.")
        with P.policy_scope(OWNER):
            with pytest.raises(ToolFailure, match="subscriptions"):
                await TB.tell_owner_impl(message="x", urgent=True)

    async def test_reach_service_when_it_exists(self, monkeypatch):
        seen = []

        async def tell_owner(message, title=None, urgent=False, link=None):
            seen.append((message, title, urgent, link))
            return "Left it as a note — 2 of 2 pushes used today."

        fake = types.SimpleNamespace(tell_owner=tell_owner)
        monkeypatch.setattr(TB, "_reach_service", lambda: fake)
        with P.policy_scope(PUSHY_WAKE):
            out = await TB.tell_owner_impl(message="m", title="t", urgent=True, link="/chat?c=1")
        assert seen == [("m", "t", True, "/chat?c=1")] and out.startswith("Left it as a note")

        async def refuse(message, title=None, urgent=False, link=None):
            return "Tool error: that repeats what you told him at 7:34 pm"

        monkeypatch.setattr(TB, "_reach_service", lambda: types.SimpleNamespace(tell_owner=refuse))
        with P.policy_scope(PUSHY_WAKE):
            with pytest.raises(ToolFailure, match="repeats"):
                await TB.tell_owner_impl(message="m")

    async def test_empty_and_dry_run(self, monkeypatch):
        monkeypatch.setattr(TB, "_reach_service", lambda: None)
        note = _patch(monkeypatch, "leave_note", "ok")
        with pytest.raises(ToolFailure, match="empty"):
            await TB.tell_owner_impl(message="  ")
        with P.policy_scope(P.TurnPolicy(source="mind", may_push=True, dry_run_outward=True)):
            out = await TB.tell_owner_impl(message="hi", urgent=True)
        assert out.startswith("[dry run]") and note.calls == []


class TestTellOwnerDescription:
    def test_it_names_no_push_cap_owner_did_not_ask_for(self, monkeypatch):
        """Ellis's 2026-10-11 answer #2: push with no limit (being ignored is
        judgment input, never a hard block). A cap in the manual only made
        the model leave a note where a push was needed (review 2026-10-11)."""
        import re

        monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
        text = TB.tell_owner.description
        assert not re.search(r"\b(one|two|three|four|five|\d+)\s+(a|per)\s+day\b", text, re.IGNORECASE), text
        assert "caps pushes" not in text
        assert "urgent" in text


class TestScreen:
    async def test_actions_route(self, monkeypatch):
        compose = _patch(monkeypatch, "compose_screen", "Your screen now: clock, me on the floor.")
        say = _patch(monkeypatch, "say_on_screen", "Said on your screen at 7:34 pm.")
        show = _patch(monkeypatch, "show_on_screen", "Showing text on the household screen until cleared.")
        clear = _patch(monkeypatch, "clear_screen", "Screen cleared.")
        reset = _patch(monkeypatch, "reset_screen_layout", "Screen reset to the stock scene.")
        up = _patch(monkeypatch, "bring_up_screen", "Your screen is up in Chrome (found the tab).")
        await SC.screen_impl(action="compose", pieces=[{"kind": "clock"}], why="calm evening")
        await SC.screen_impl(action="say", line="hello", aloud=True)
        await SC.screen_impl(action="show", kind="text", body="hi", screen="house", seconds=60)
        await SC.screen_impl(action="clear", screen="mini")
        await SC.screen_impl(action="reset")
        await SC.screen_impl(action="bring_up")
        assert compose.calls == [{"pieces": [{"kind": "clock"}], "me": None, "why": "calm evening", "hold_minutes": 0}]
        assert say.calls == [{"line": "hello", "aloud": True}]
        assert show.calls[0]["kind"] == "text" and show.calls[0]["seconds"] == 60
        assert clear.calls == [{"screen": "mini"}] and reset.calls == [{}] and up.calls == [{}]

    async def test_a_held_screen_is_a_failure(self, monkeypatch):
        _patch(monkeypatch, "compose_screen", "Not changed: Ellis asked for what is on your screen, and it holds until 9 pm.")
        with pytest.raises(ToolFailure, match="holds until"):
            await SC.screen_impl(action="compose", pieces=[{"kind": "clock"}])

    async def test_widget_actions_need_the_widget_skill(self, monkeypatch):
        widget = _patch(monkeypatch, "update_widget", 'Widget updated with 1 section(s). Title: "Edward"')

        async def off(skill_id):
            return False

        async def on(skill_id):
            return True

        import services.skills_service as ss
        from services import tool_registry

        monkeypatch.setattr(tool_registry, "_skill_cache", {})
        monkeypatch.setattr(tool_registry, "_cache_timestamp", 0)
        monkeypatch.setattr(ss, "is_skill_enabled", off)
        with pytest.raises(ToolFailure, match="widget"):
            await SC.screen_impl(action="widget", sections=[{"type": "text", "content": "hi"}])
        assert widget.calls == []
        monkeypatch.setattr(ss, "is_skill_enabled", on)
        monkeypatch.setattr(tool_registry, "_skill_cache", {})
        monkeypatch.setattr(tool_registry, "_cache_timestamp", 0)
        out = await SC.screen_impl(action="widget", sections=[{"type": "text", "content": "hi"}], title="Edward")
        assert out.startswith("Widget updated")

    async def test_a_dry_run_changes_nothing(self, monkeypatch):
        say = _patch(monkeypatch, "say_on_screen", "Said")
        with P.policy_scope(P.TurnPolicy(source="mind", dry_run_outward=True)):
            out = await SC.screen_impl(action="say", line="hi")
        assert out.startswith("[dry run]") and say.calls == []


class TestPlan:
    @pytest.fixture(autouse=True)
    def _conv(self):
        legacy.set_current_conversation_id("conv-plan")
        legacy._active_plans.pop("conv-plan", None)
        legacy._pending_plan_events.pop("conv-plan", None)
        yield
        legacy._active_plans.pop("conv-plan", None)
        legacy._pending_plan_events.pop("conv-plan", None)

    async def test_the_first_list_creates_the_plan(self):
        out = await PL.plan_impl(items=[{"text": "Find flights", "status": "doing"}, {"text": "Book hotel"}])
        assert "0 of 2" in out
        events = legacy.get_pending_plan_events("conv-plan")
        assert [e["event_type"] for e in events] == ["plan_created"]
        steps = events[0]["plan_steps"]
        assert [(s["title"], s["status"]) for s in steps] == [("Find flights", "in_progress"),
                                                              ("Book hotel", "pending")]

    async def test_a_status_change_is_a_step_update(self):
        await PL.plan_impl(items=[{"text": "Find flights", "status": "doing"}, {"text": "Book hotel"}])
        legacy.get_pending_plan_events("conv-plan")
        await PL.plan_impl(items=[{"text": "Find flights", "status": "done", "note": "WN1234"},
                                  {"text": "Book hotel", "status": "doing"}])
        events = legacy.get_pending_plan_events("conv-plan")
        assert [(e["event_type"], e["step_status"]) for e in events] == [("plan_step_update", "completed"),
                                                                        ("plan_step_update", "in_progress")]
        assert events[0]["step_result"] == "WN1234"

    async def test_a_changed_list_is_a_plan_update_and_dropped_steps_leave(self):
        await PL.plan_impl(items=[{"text": "A"}, {"text": "B"}])
        legacy.get_pending_plan_events("conv-plan")
        await PL.plan_impl(items=[{"text": "A", "status": "doing"}, {"text": "B", "status": "dropped"},
                                  {"text": "C"}])
        events = legacy.get_pending_plan_events("conv-plan")
        assert [e["event_type"] for e in events] == ["plan_updated"]
        assert [s["title"] for s in events[0]["plan_steps"]] == ["A", "C"]

    async def test_all_done_completes_the_plan(self):
        await PL.plan_impl(items=[{"text": "A"}, {"text": "B"}])
        legacy.get_pending_plan_events("conv-plan")
        out = await PL.plan_impl(items=[{"text": "A", "status": "done"}, {"text": "B", "status": "done"}])
        events = legacy.get_pending_plan_events("conv-plan")
        assert events[-1]["event_type"] == "plan_completed"
        assert legacy.get_active_plan("conv-plan") is None
        assert "done" in out.lower()

    async def test_bad_items(self):
        with pytest.raises(ToolFailure):
            await PL.plan_impl(items=[])
        with pytest.raises(ToolFailure, match="status"):
            await PL.plan_impl(items=[{"text": "A", "status": "maybe"}])
