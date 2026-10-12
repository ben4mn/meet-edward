"""services/expression_service.py — Edward's surfaces run on his judgment.

Why (2026-09-03, Ellis): the widget and the household screen were being
filled by a deterministic composer (doses, bowls). Ellis wants them to be
Edward's: every pulse he looks at what is going on and decides — summarise
the day's messages, get Ellis's attention, tell a joke, put a picture or a
video on the screen, or leave things as they are. The composed card
survives only as a fallback for when Edward cannot be reached.
"""

import json
from datetime import datetime, timezone

import pytest

from services import expression_service as es
from services import mind_service as ms

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)


def _signals(**kw):
    base = dict(
        now=NOW, attention={"owner_present": True, "active_surface": "mac:mini", "in_imessage_thread": None},
        recent_messages=[{"sender": "+1555", "text": "Dinner at 7?", "is_from_me": False, "chat_identifier": "+1555", "chat_name": None}],
        board_summary="Biscuit: 2 doses due; bowls: Biscuit AM –, Maple AM ✓",
        current_widget={"subtitle": "Something is late", "updated_by": "system", "updated_at": "2026-09-03T18:00:00"},
        current_screen=None,
        calendar=["7:00pm Dinner with Rowan"],
        recent_pulse_notes=["18:30: left the widget on the calendar; nothing new"],
    )
    base.update(kw)
    return es.PulseSignals(**base)


class TestPrompt:
    def test_system_prompt_names_both_surfaces_and_who_sees_them(self):
        p = es.EXPRESSION_SYSTEM_PROMPT
        for needle in ("update_widget", "update_widget_code", "show_on_screen", "clear_screen"):
            assert needle in p
        assert "household" in p.lower() and "ellis" in p.lower()   # the owner by name (EDWARD_OWNER_NAME)
        assert "leave" in p.lower()  # doing nothing is a valid choice

    def test_system_prompt_is_not_a_menu(self):
        p = es.EXPRESSION_SYSTEM_PROMPT.lower()
        assert "joke" in p and "summar" in p and "attention" in p
        assert "every" in p and "must" not in p.split("never")[0][:300]

    def test_trigger_carries_the_signals_compactly(self):
        t = es.build_pulse_trigger(_signals())
        assert "Dinner at 7?" in t and "7:00pm Dinner" in t and "mac:mini" in t
        assert "Something is late" in t and "18:30" in t
        assert len(t) < 6000

    def test_trigger_truncates_long_message_lists(self):
        msgs = [{"sender": f"+1{i:03d}", "text": "x" * 300, "is_from_me": False, "chat_identifier": "c", "chat_name": None} for i in range(80)]
        t = es.build_pulse_trigger(_signals(recent_messages=msgs))
        assert t.count("+1") <= es.MAX_MESSAGES_IN_PULSE + 2 and len(t) < 8000

    def test_trigger_marks_owner_away(self):
        t = es.build_pulse_trigger(_signals(attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None}))
        assert "away" in t.lower() or "not at" in t.lower()

    def test_trigger_names_a_running_script_over_the_sections_it_hides(self):
        """The phone runs a script in preference to sections (2026-09-27):
        the pulse read its own old sections as what the widget said while
        Friday's script was what the iPhone showed."""
        widget = {"subtitle": "Friday", "updated_by": "edward", "updated_at": "2026-09-25T20:00:00",
                  "has_script": True, "sections": [{"type": "text", "content": "Pet board: all fed"}]}
        t = es.build_pulse_trigger(_signals(current_widget=widget))
        assert "custom Scriptable script" in t
        assert "it currently says: " not in t


class TestPulse:
    async def test_pulse_runs_one_turn_in_the_fixed_conversation(self, monkeypatch):
        calls = []

        async def fake_chat(**kw):
            calls.append(kw)
            return "Put the dinner reminder on the widget."

        es.reset_pulse_state()
        monkeypatch.setattr(es, "collect_signals", _const(_signals()))
        monkeypatch.setattr(es, "_ensure_conversation", _const(None))
        monkeypatch.setattr(es, "recent_decisions", _const([]))
        monkeypatch.setattr(ms, "chat_with_memory", fake_chat)
        monkeypatch.setattr(ms, "_settings", _const(_S()))
        monkeypatch.setattr(ms, "_graph", _const(object()))
        out = await es.run_expression_pulse("cron")
        assert out["ok"] is True and "dinner" in out["note"].lower()
        kw = calls[0]
        assert kw["conversation_id"] == es.EXPRESSION_CONVERSATION_ID
        assert kw["system_prompt"].startswith(es.EXPRESSION_SYSTEM_PROMPT)
        assert "Dinner at 7?" in kw["message"]
        assert kw.get("required_tools") is None  # never forced to act

    async def test_pulse_falls_back_to_composed_card_when_edward_is_unreachable(self, monkeypatch):
        async def boom(**kw):
            raise RuntimeError("codex 401")

        fallback = []

        async def fake_refresh(reason, **kw):
            fallback.append(reason)
            return {"subtitle": "fallback"}

        es.reset_pulse_state()
        monkeypatch.setattr(es, "collect_signals", _const(_signals()))
        monkeypatch.setattr(es, "_ensure_conversation", _const(None))
        monkeypatch.setattr(es, "recent_decisions", _const([]))
        monkeypatch.setattr(ms, "chat_with_memory", boom)
        monkeypatch.setattr(ms, "_settings", _const(_S()))
        monkeypatch.setattr(ms, "_graph", _const(object()))
        monkeypatch.setattr(es, "refresh_widget_expression", fake_refresh)
        out = await es.run_expression_pulse("cron")
        assert out["ok"] is False and fallback == ["pulse-fallback"]

    async def test_pulse_remembers_its_last_notes(self, monkeypatch):
        es._recent_notes.clear()

        async def fake_chat(**kw):
            return "Left it alone."

        monkeypatch.setattr(es, "collect_signals", _const(_signals()))
        monkeypatch.setattr(es, "_ensure_conversation", _const(None))
        monkeypatch.setattr(es, "recent_decisions", _const([]))
        monkeypatch.setattr(ms, "chat_with_memory", fake_chat)
        monkeypatch.setattr(ms, "_settings", _const(_S()))
        monkeypatch.setattr(ms, "_graph", _const(object()))
        es.reset_pulse_state()
        for _ in range(es.MAX_RECENT_NOTES + 2):
            await es.run_expression_pulse("manual")  # the clock would skip an unchanged pack
        assert len(es._recent_notes) == es.MAX_RECENT_NOTES and "Left it alone" in es._recent_notes[-1]


class TestScheduler:
    async def test_expression_pulse_event_routes_to_the_service(self, monkeypatch):
        from types import SimpleNamespace
        from services import scheduler_service

        ran, done = [], []

        async def fake_pulse(reason):
            ran.append(reason)
            return {"ok": True, "note": "n"}

        async def fake_complete(event_id, result=None, **kw):
            done.append(event_id)

        monkeypatch.setattr(es, "run_expression_pulse", fake_pulse)
        monkeypatch.setattr(scheduler_service, "mark_event_completed", fake_complete)
        ev = SimpleNamespace(id="e", event_metadata=json.dumps({"type": "expression_pulse"}), description="x", delivery_channel=None, conversation_id=None)
        await scheduler_service._execute_event(ev)
        assert ran == ["cron"] and done == ["e"]

    def test_cron_seed_is_the_pulse_not_the_composer(self):
        from services import scheduler_service
        kinds = [k for k, _ in scheduler_service.EXTRA_CRON_EVENTS]
        assert "expression_pulse" in kinds and "widget_refresh" not in kinds
        assert "widget_refresh" in scheduler_service.RETIRED_CRON_EVENTS


class _S:
    system_prompt = "You are Edward."; model = "gpt-5.4"; temperature = 0.7; provider = "openai"


def _const(v):
    async def _f(*a, **k):
        return v
    return _f


class TestWidgetContentsInTrigger:
    """Regression (2026-09-03 20:20): after Biscuit's meds were retired, Edward
    left 'Biscuit is late — 3 doses late' on the widget because the trigger only
    told him the widget's subtitle ('House status'), not what it said."""

    def test_trigger_shows_what_the_widget_currently_says(self):
        cw = {"subtitle": "House status", "updated_by": "edward", "updated_at": "x",
              "sections": [{"type": "header", "title": "Biscuit is late"}, {"type": "text", "content": "3 doses late."}]}
        t = es.build_pulse_trigger(_signals(current_widget=cw))
        assert "Biscuit is late" in t and "3 doses late" in t

    def test_widget_sections_are_summarised_compactly(self):
        secs = [{"type": "header", "title": "H"}, {"type": "text", "content": "x" * 500},
                {"type": "list", "items": [{"label": "a", "detail": "b"}, {"label": "c"}]},
                {"type": "stats_row", "stats": [{"label": "Biscuit", "value": "AM ✓"}]}]
        out = es.summarise_widget_sections(secs)
        assert out.startswith("H | ") and "a: b" in out and "c" in out and "Biscuit=AM ✓" in out
        assert len(out) <= es.MAX_WIDGET_SUMMARY_CHARS


class TestSignalFacts:
    """Regression (2026-09-03 20:22): with Biscuit off her protocol the board line
    said only 'bowls AM – PM –', so Edward kept believing his own earlier
    widget text ('3 doses late') and put it on the shared screen. The board
    summary must state plainly when nobody has medication scheduled, and the
    calendar line must not be polluted with Edward's own scheduled jobs."""

    def test_board_summary_says_no_medications_when_none(self):
        board = {"pets": [{"name": "Biscuit", "meds": [], "feedings": {"am": {"done": False}, "pm": {"done": False}}}]}
        out = es._board_summary(board)
        assert "no medication" in out.lower()

    def test_board_summary_counts_late_and_due(self):
        board = {"pets": [{"name": "Biscuit", "meds": [{"status": "overdue"}, {"status": "due"}], "feedings": {}}]}
        out = es._board_summary(board)
        assert "1 dose(s) late" in out and "1 dose(s) due" in out

    def test_calendar_lines_skip_edwards_own_scheduled_jobs(self):
        events = [
            {"start": "2026-09-03T20:30:00-05:00", "title": "expression_pulse — Edward decides what his widget shows", "source": "scheduled_event"},
            {"start": "2026-09-03T19:00:00-05:00", "title": "Dinner with Rowan", "source": "calendar"},
            {"start": "2026-09-03T21:00:00-05:00", "title": "Send Ellis an in-app chat reminder tonight saying: 'Watch the video'"},
        ]
        lines = es.calendar_lines(events)
        assert lines == ["7:00pm Dinner with Rowan"]

    def test_prompt_tells_him_stale_text_is_his_to_fix(self):
        assert "no longer true" in es.EXPRESSION_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Evolution plan, 2026-09-14 (docs/pulse-evolution-plan-2026-09-14.md):
# a lean turn, a short window with a durable memory, change-driven cadence.
# ---------------------------------------------------------------------------

class TestLeanTurn:
    def test_pulse_tools_is_a_short_allowlist_of_surface_tools(self):
        tools = set(es.LEGACY_PULSE_TOOLS)
        assert {"update_widget", "update_widget_code", "clear_widget_code", "get_widget_state_tool",
                "show_on_screen", "clear_screen", "compose_screen", "say_on_screen", "reset_screen_layout",
                "bring_up_screen", "look_at_screen", "browser_open", "browser_done",
                "web_search", "remember_search", "search_documents"} <= tools
        assert not tools & {"send_message", "send_imessage", "execute_code", "spawn_worker",
                            "send_push_notification", "schedule_event", "save_document", "wish_for_feature"}
        assert "set_screen_layout" not in tools  # the stage replaced the tile grid (2026-09-28)
        assert len(tools) < 35

    async def test_pulse_turn_passes_the_allowlist_and_a_short_window(self, monkeypatch):
        calls = []

        async def fake_chat(**kw):
            calls.append(kw)
            return "Left it."

        es.reset_pulse_state()
        monkeypatch.setattr(es, "collect_signals", _const(_signals()))
        monkeypatch.setattr(es, "_ensure_conversation", _const(None))
        monkeypatch.setattr(es, "recent_decisions", _const([]))
        monkeypatch.setattr(ms, "chat_with_memory", fake_chat)
        monkeypatch.setattr(ms, "_settings", _const(_S()))
        monkeypatch.setattr(ms, "_graph", _const(object()))
        monkeypatch.setattr(es, "_quota_state", lambda: "ok")
        await es.run_expression_pulse("manual")
        kw = calls[0]
        assert kw["allowed_tools"] == list(es.LEGACY_PULSE_TOOLS)
        assert kw["max_history_chars"] == es.PULSE_MAX_HISTORY_CHARS
        assert es.PULSE_MAX_HISTORY_CHARS <= 30_000


class TestChangeDrivenCadence:
    """Change-only since 2026-10-11 (``test_pulse_change_only.py`` has the
    rest): messages and presence are not changes, and there are no floors."""

    def test_signal_parts_are_stable_for_the_same_pack(self):
        assert es.signal_parts(_signals()) == es.signal_parts(_signals())
        assert set(es.signal_parts(_signals())) >= {"board", "calendar", "widget", "house_screen", "mini_screen"}

    def test_changed_parts_name_what_moved(self):
        before = es.signal_parts(_signals())
        after = es.signal_parts(_signals(attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None},
                                         board_summary="Biscuit: 1 dose late"))
        assert es.changed_parts(before, after) == ["board"]
        assert es.changed_parts(None, after) == []  # first tick after a restart: nothing to compare

    def test_describe_changes_reads_like_a_sentence(self):
        line = es.describe_changes(["board", "widget"], widget_by="owner")
        assert "the pet board changed" in line and "widget was changed by owner" in line

    async def test_cron_tick_skips_when_nothing_changed_and_runs_on_change(self, monkeypatch):
        calls = []

        async def fake_chat(**kw):
            calls.append(kw)
            return "Left it."

        recorded = []
        from services import event_ledger
        monkeypatch.setattr(event_ledger, "record_event", lambda **kw: recorded.append(kw))
        es.reset_pulse_state()
        sig = _signals()
        monkeypatch.setattr(es, "collect_signals", _const(sig))
        monkeypatch.setattr(es, "_ensure_conversation", _const(None))
        monkeypatch.setattr(es, "recent_decisions", _const([]))
        monkeypatch.setattr(ms, "chat_with_memory", fake_chat)
        monkeypatch.setattr(ms, "_settings", _const(_S()))
        monkeypatch.setattr(ms, "_graph", _const(object()))
        monkeypatch.setattr(es, "_quota_state", lambda: "ok")

        first = await es.run_expression_pulse("cron")
        assert first["ok"] and not first.get("skipped") and len(calls) == 1
        second = await es.run_expression_pulse("cron")
        assert second["ok"] and second["skipped"] and len(calls) == 1
        assert "nothing changed" in second["note"]

        changed = _signals(board_summary="Biscuit: 1 dose(s) late")
        monkeypatch.setattr(es, "collect_signals", _const(changed))
        third = await es.run_expression_pulse("cron")
        assert not third.get("skipped") and len(calls) == 2
        assert "Since your last pulse" in calls[1]["message"] and "the pet board changed" in calls[1]["message"]
        detail = [r for r in recorded if r.get("kind") == "decision"][-1]["detail"]
        assert detail["skipped_ticks"] == 1 and detail["changed"] == ["board"]

    def test_trigger_opens_with_what_changed(self):
        text = es.build_pulse_trigger(_signals(), changes="3 new messages; Ellis is now away")
        assert "Since your last pulse: 3 new messages; Ellis is now away" in text
        assert "Since your last pulse" not in es.build_pulse_trigger(_signals())


class TestDurableMemory:
    def test_decision_lines_render_from_ledger_rows(self):
        rows = [
            {"outcome": "changed", "reason": "Put the weekend forecast on the widget.", "created_at": NOW,
             "detail_json": json.dumps({"surfaces": ["widget"]})},
            {"outcome": "left_alone", "reason": "Nothing new.", "created_at": NOW, "detail_json": None},
        ]
        lines = es.decision_lines(rows)
        assert len(lines) == 2
        assert "changed widget" in lines[0] and "Put the weekend forecast" in lines[0]
        assert "left alone" in lines[1]


class TestTurnOptions:
    def test_chat_with_memory_takes_an_allowlist_and_a_window(self):
        import inspect
        from services.graph import streaming
        params = inspect.signature(streaming.chat_with_memory).parameters
        assert "allowed_tools" in params and "max_history_chars" in params
        assert "max_chars" in inspect.signature(streaming._trim_checkpoint_history).parameters

    def test_allowlist_keeps_only_named_tools(self):
        from types import SimpleNamespace
        from services.graph.streaming import _apply_tool_allowlist
        tools = [SimpleNamespace(name="update_widget"), SimpleNamespace(name="send_message"), SimpleNamespace(name="web_search")]
        kept = [t.name for t in _apply_tool_allowlist(tools, ["update_widget", "web_search"])]
        assert kept == ["update_widget", "web_search"]
        assert _apply_tool_allowlist(tools, None) is tools
