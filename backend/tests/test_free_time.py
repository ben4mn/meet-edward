"""One budgeted free-time run a day (Ellis's answer #4, 2026-10-11).

Ellis chose to keep a small budget for free time: retire the
07:00/18:00/Sunday ticks and the surplus queue, but keep one budgeted
free-time run a day (≈ $0.30–0.50 per-run cap) for the Mac's stage and
Edward's own projects — no push, no send. The review found it unimplemented: nothing seeded it, the mind had
no ``free`` reason, and the docs said free time was gone for good.

Now: one ``wakeup_tick`` (``free``, 13:00 in Ellis's zone) runs
``wake("free")`` in ``edward-free-time``, under a policy that reaches
nobody and never pushes, with a per-turn dollar cap (AREA 2's
``TurnPolicy.max_turn_usd``, $0.40) and a lean history; its legacy tools
are reading, remembering, documents and his own stage — no send, no push,
no note, no ``schedule``. A line it says on the stage is shown, never
spoken; ``tell_owner`` from it is refused; it writes the usual ``mind``
ledger row (``source="free"``) and stops at the daily cap (``mind:free``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.area1_fakes import install_turn_policy

pytestmark = pytest.mark.unit


def test_the_free_tick_is_seeded_once_a_day_in_owners_zone():
    from services import scheduler_service as sched

    assert ("free", sched.FREE_CRON) in sched.WAKEUP_TICKS
    assert sched.FREE_CRON == "0 13 * * *"
    assert sched.wake_reason_for_tick("free") == "free"
    assert "free" not in sched.RETIRED_WAKEUP_KINDS
    import services.mind_service as ms

    meta = {"type": "wakeup_tick", "kind": "free"}
    assert sched.event_timeout_for(meta) >= ms.wake_timeout_for("free") + ms.PACK_TIMEOUT_SECONDS


def test_free_is_a_reason_with_its_own_thread_window_and_bound():
    import services.mind_service as ms

    assert "free" in ms.REASONS and "free" in ms.WAKE_REASONS
    assert ms.thread_for("free", ms.MIND_CONVERSATION_ID) == ms.FREE_CONVERSATION_ID == "edward-free-time"
    assert ms.FREE_CONVERSATION_ID in ms.EDWARD_OWN_THREAD_IDS
    assert ms.FREE_CONVERSATION_ID not in ms.RETIRED_THREAD_IDS
    assert ms.history_chars_for("free", None) == ms.FREE_MAX_HISTORY_CHARS <= 12_000
    assert ms.wake_timeout_for("free") == ms.FREE_TIMEOUT_SECONDS
    assert "free" not in ms.RETRY_REASONS and not ms.moves_last_wake("free")


def test_the_free_policy_reaches_nobody_and_is_capped_per_run():
    import services.mind_service as ms

    v = ms.policy_values("free", timeout_s=ms.FREE_TIMEOUT_SECONDS)
    assert (v["owner_asked"], v["may_reach_people"], v["may_push"], v["reply_to"]) == (False, False, False, ())
    assert 0.30 <= v["max_turn_usd"] <= 0.50


def test_the_free_policy_carries_its_cap_into_turn_policy(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    p = ms.make_policy(ms.policy_values("free", timeout_s=ms.FREE_TIMEOUT_SECONDS))
    assert p.max_turn_usd == ms.FREE_MAX_TURN_USD and not p.may_push and not p.may_reach_people


def test_one_run_stays_inside_owners_ceiling_whatever_the_model_writes():
    """Review 2026-10-11 (minor): the per-run cap is soft — it stops new
    rounds, it cannot stop the round already running nor the closing call
    after it — and the mind route allowed 32,000 output tokens a call
    (≈ $0.32 on Sonnet alone), so one free run could land at $0.6–0.8
    against Ellis's "≈ $0.30–0.50". Rounds now stop at $0.30 and every call
    of the run, the closing one included, writes at most 8,000 tokens."""
    import services.mind_service as ms
    from services.llm_pricing import PRICES

    v = ms.policy_values("free", timeout_s=ms.FREE_TIMEOUT_SECONDS)
    assert v["max_output_tokens"] == ms.FREE_MAX_OUTPUT_TOKENS
    out_per_token = PRICES["claude-sonnet-5-5"].base.output / 1_000_000
    # The cap, one more round, and the closing call, each at its full output.
    assert ms.FREE_MAX_TURN_USD + 2 * ms.FREE_MAX_OUTPUT_TOKENS * out_per_token <= 0.50


def test_only_free_time_caps_its_output():
    import services.mind_service as ms

    for reason in ("nightly", "speak", "self", "surfaces", "manual"):
        assert "max_output_tokens" not in ms.policy_values(reason, timeout_s=300), reason


def test_its_legacy_tools_are_the_stage_and_his_own_projects():
    import services.mind_service as ms

    tools = set(ms.legacy_tools_for("free", ms.policy_values("free", timeout_s=480)))
    assert tools == set(ms.LEGACY_FREE_TOOLS)
    assert {"compose_screen", "say_on_screen", "save_document", "remember", "web_search"} <= tools
    never = {"send_imessage", "send_message", "send_sms", "send_whatsapp", "send_push_notification", "tell_owner",
             "leave_note", "schedule", "schedule_event", "wake_me", "show_on_screen", "update_widget",
             "clear_screen", "stand_down", "forget_about"}
    assert not tools & never, tools & never


def test_the_free_pack_says_what_free_time_is():
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(reason="free", now=datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)))
    low = text.lower()
    assert "[wake: free]" in low and "free time" in low
    assert "this mac's screen" in low and "compose_screen" in text and "say_on_screen" in text
    assert "do not push or message anyone" in low


@pytest.mark.integration
async def test_tell_owner_from_free_time_is_refused(clean_db, monkeypatch):
    from services import reach_service
    from services.call_purpose import purpose_scope

    with purpose_scope("mind:free"):
        out = await reach_service.tell_owner("Ellis's 9:31 pm text asked about the deck — want it? (not sent)",
                                           title="Deck")
    assert out.startswith("Tool error: this turn can't reach Ellis")


@pytest.mark.integration
async def test_a_line_free_time_says_on_the_stage_is_never_spoken(clean_db):
    from services import screen_service
    from services.call_purpose import purpose_scope

    noon = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
    with purpose_scope("mind:free"):
        out = await screen_service.say("Working on a map of your week.", aloud=True, now=noon)
    assert out["quiet"] is True and out["line"]["aloud"] is False


async def test_the_free_tick_runs_the_free_wake(monkeypatch):
    from services import scheduler_service as sched
    import services.mind_service as ms

    seen = []

    async def wake(reason, **kw):
        seen.append(reason)
        return {"ok": True, "outcome": "acted", "note": "Sketched the week on the stage."}

    done = []

    async def completed(event_id, result=None):
        done.append(event_id)

    monkeypatch.setattr(ms, "wake", wake)
    monkeypatch.setattr(sched, "mark_event_completed", completed)
    from types import SimpleNamespace

    ev = SimpleNamespace(id="free-1", event_metadata='{"type": "wakeup_tick", "kind": "free"}',
                         description="Free wake — the mind decides")
    await sched._execute_event(ev)
    assert seen == ["free"] and done == ["free-1"]
