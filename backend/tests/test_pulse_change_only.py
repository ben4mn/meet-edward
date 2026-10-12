"""The surfaces pulse is change-only (proactivity rebuild, 2026-10-11).

The ``*/15`` tick stays a model-composed surfaces wake (Ellis's 2026-09-03
rule: his judgment, not a menu) but only runs when something the surfaces
show could have moved: the pet board, the calendar, the widget, the
household screen, this Mac's screen, his hands, who is at the hub.
Messages and presence no longer count — they made 1,330 "changes" a week
(prod audit §4) — and there are no floors: a quiet pack is never re-run
because time passed. The first tick after a start still runs. A
non-``cron`` pulse (Ellis's ask, an ambient noticing, the nightly's morning
scene) always runs and may carry an ``extra`` line. The wake runs under a
pulse ``TurnPolicy`` (no reaching people, no pushes) when AREA 2's policy
exists, with the legacy tool tuple for ``EDWARD_TOOLSET=legacy``.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import timedelta

import pytest

from services import expression_service as es
from tests.area1_fakes import fake_module
from tests.test_expression_service import NOW, _const, _signals

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ pure --

def test_signal_parts_leave_out_messages_and_presence():
    parts = es.signal_parts(_signals())
    assert "messages" not in parts and "presence" not in parts
    assert {"board", "calendar", "widget", "house_screen", "mini_screen"} <= set(parts)


def test_a_messages_or_presence_change_is_no_change():
    before = es.signal_parts(_signals())
    moved = _signals(recent_messages=_signals().recent_messages + [{"sender": "+1666", "text": "On my way"}],
                     attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None})
    assert es.changed_parts(before, es.signal_parts(moved)) == []


def test_owner_sitting_down_at_the_mini_is_no_change():
    """Review 2026-10-11: the ``mini_screen`` change key carried "Ellis is at
    this Mac right now" (keyboard or mouse in the last 2 minutes), so
    Ellis sitting down or getting up ran a model pulse — presence by the back
    door, the floor-like cadence the change-only pulse removed. It stays in
    the trigger text, never in the key."""
    from tests.test_expression_screen import _his_scene

    scene = _his_scene()
    away = _signals(mini_screen={**scene, "owner_at_mini": False})
    here = _signals(mini_screen={**scene, "owner_at_mini": True})
    changed = es.changed_parts(es.signal_parts(away), es.signal_parts(here))
    assert changed == []
    assert es.should_pulse(reason="cron", changed=changed, last_pulse_at=NOW, quota="ok")[0] is False
    assert "Ellis is at this Mac right now" in es.describe_mini_screen(here.mini_screen)
    # A real change of the screen still counts.
    moved = _signals(mini_screen={**scene, "owner_at_mini": True, "last_said": {"text": "Biscuit's dose is late",
                                                                              "at": NOW.isoformat()}})
    assert es.changed_parts(es.signal_parts(away), es.signal_parts(moved)) == ["mini_screen"]


def test_cron_runs_on_a_change_or_first_and_never_on_a_floor():
    long_ago = NOW - timedelta(hours=9)
    assert es.should_pulse(reason="cron", changed=["board"], last_pulse_at=long_ago, quota="ok")[0]
    assert es.should_pulse(reason="cron", changed=[], last_pulse_at=None, quota="ok")[0]
    go, why = es.should_pulse(reason="cron", changed=[], last_pulse_at=long_ago, quota="ok")
    assert not go and "nothing changed" in why and "floor" not in why
    assert es.should_pulse(reason="cron", changed=["calendar"], last_pulse_at=long_ago, quota="paced")[0]


def test_other_reasons_always_run_unless_the_quota_is_out():
    assert es.should_pulse(reason="manual", changed=[], last_pulse_at=NOW, quota="ok")[0]
    assert es.should_pulse(reason="notice", changed=[], last_pulse_at=NOW, quota="paced")[0]
    go, why = es.should_pulse(reason="cron", changed=["board"], last_pulse_at=NOW, quota="exhausted")
    assert not go and "exhausted" in why
    assert not es.should_pulse(reason="nightly", changed=[], last_pulse_at=NOW, quota="exhausted")[0]


def test_the_floors_and_the_surplus_knob_are_gone():
    for gone in ("pulse_floor_minutes", "PULSE_FLOOR_MINUTES", "PULSE_NIGHT_FLOOR_MINUTES",
                 "PULSE_PACED_FLOOR_MINUTES", "PULSE_SURPLUS_FLOOR_MINUTES", "_surplus_quickens", "PULSE_TOOLS"):
        assert not hasattr(es, gone), gone
    assert "update_widget" in es.LEGACY_PULSE_TOOLS and "send_message" not in es.LEGACY_PULSE_TOOLS


def test_extra_reaches_the_trigger_before_the_ask():
    text = es.build_pulse_trigger(_signals(), extra="Set the morning scene for Ellis.")
    assert "Set the morning scene for Ellis." in text
    assert text.index("Set the morning scene") < text.index("Decide what, if anything")
    assert "Set the morning scene" not in es.build_pulse_trigger(_signals())


# ------------------------------------------------------------- the runs --

@dataclass(frozen=True)
class _Policy:
    source: str
    owner_asked: bool
    may_reach_people: bool
    may_push: bool
    reply_to: tuple = ()
    max_turn_usd: float | None = None
    deadline_s: float | None = None


@pytest.fixture
def turn_policy(monkeypatch):
    """AREA 2's ``services.toolset.policy.TurnPolicy``, as its spec gives it."""
    pkg = types.ModuleType("services.toolset")
    pkg.__path__ = []
    mod = types.ModuleType("services.toolset.policy")
    mod.TurnPolicy = _Policy
    fake_module(monkeypatch, "services.toolset", pkg)
    fake_module(monkeypatch, "services.toolset.policy", mod)
    return _Policy


@pytest.fixture
def pulse(monkeypatch):
    """The pulse with its signals, conversation and ledger stubbed; ``wake``
    replaced by a recorder that accepts ``policy`` (1B's signature)."""
    import services.mind_service as ms

    state = {"signals": _signals(), "wakes": [], "fallback": []}

    async def collect(**kw):
        return state["signals"]

    async def wake(reason, *, policy=None, **kw):
        state["wakes"].append({"reason": reason, "policy": policy, **kw})
        return {"ok": True, "note": "Left it."}

    async def refresh(reason, **kw):
        state["fallback"].append(reason)
        return {}

    es.reset_pulse_state()
    monkeypatch.setattr(es, "collect_signals", collect)
    monkeypatch.setattr(es, "_ensure_conversation", _const(None))
    monkeypatch.setattr(es, "recent_decisions", _const([]))
    monkeypatch.setattr(es, "_quota_state", lambda: "ok")
    monkeypatch.setattr(es, "refresh_widget_expression", refresh)
    monkeypatch.setattr(ms, "wake", wake)
    yield state
    es.reset_pulse_state()


async def test_a_quiet_cron_tick_runs_no_wake(pulse):
    assert not (await es.run_expression_pulse("cron")).get("skipped")   # the first since start
    out = await es.run_expression_pulse("cron")
    assert out["skipped"] and len(pulse["wakes"]) == 1
    assert "floor" not in out["note"]


async def test_a_messages_only_change_runs_no_wake(pulse):
    await es.run_expression_pulse("cron")
    pulse["signals"] = _signals(recent_messages=_signals().recent_messages + [{"sender": "+1666", "text": "hi"}])
    assert (await es.run_expression_pulse("cron"))["skipped"]
    assert len(pulse["wakes"]) == 1


async def test_a_presence_only_change_runs_no_wake(pulse):
    await es.run_expression_pulse("cron")
    pulse["signals"] = _signals(attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None})
    assert (await es.run_expression_pulse("cron"))["skipped"]
    assert len(pulse["wakes"]) == 1


async def test_a_board_change_runs_a_wake_under_a_pulse_policy(pulse, turn_policy):
    await es.run_expression_pulse("cron")
    pulse["signals"] = _signals(board_summary="Biscuit: 1 dose(s) late")
    out = await es.run_expression_pulse("cron")
    assert not out.get("skipped") and len(pulse["wakes"]) == 2
    w = pulse["wakes"][-1]
    assert w["reason"] == "surfaces" and "the pet board changed" in w["message"]
    assert w["policy"] == turn_policy(source="pulse", owner_asked=False, may_reach_people=False, may_push=False)
    assert tuple(w["tools"]) == es.LEGACY_PULSE_TOOLS
    assert w["conversation_id"] == es.EXPRESSION_CONVERSATION_ID and w["turn_source"] == "pulse"


async def test_extra_reaches_the_wake(pulse):
    await es.run_expression_pulse("notice", extra="Put the dog-waste visit on the house screen until 3:40 pm.")
    assert "dog-waste visit" in pulse["wakes"][-1]["message"]


async def test_no_policy_is_passed_to_a_wake_that_does_not_take_one(monkeypatch, pulse, turn_policy):
    import services.mind_service as ms

    seen = []

    async def old_wake(reason, *, extra=None, message=None, conversation_id=None, tools=None, system_prompt=None,
                       turn_source="mind", skip_extraction=False, max_history_chars=None, record=True):
        seen.append(message)
        return {"ok": True, "note": "Left it."}

    monkeypatch.setattr(ms, "wake", old_wake)
    out = await es.run_expression_pulse("manual")
    assert out["ok"] and seen


async def test_without_area2_the_pulse_still_runs_with_its_legacy_tools(monkeypatch, pulse):
    fake_module(monkeypatch, "services.toolset.policy", None)   # import raises ImportError
    out = await es.run_expression_pulse("manual")
    assert out["ok"]
    w = pulse["wakes"][-1]
    assert w["policy"] is None and tuple(w["tools"]) == es.LEGACY_PULSE_TOOLS


async def test_an_exhausted_quota_leaves_the_fallback_card(monkeypatch, pulse):
    monkeypatch.setattr(es, "_quota_state", lambda: "exhausted")
    out = await es.run_expression_pulse("cron")
    assert out["skipped"] and pulse["wakes"] == []
    assert pulse["fallback"] == ["pulse-quota"]
