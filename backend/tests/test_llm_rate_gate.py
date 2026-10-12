"""The Codex rate gate paces Edward against the account's real usage meter.

History. The first gate (2026-07) guessed a per-window call budget because
"the API exposes no quota meter", calibrated it down on every 429 and grew it
1.1x per clean window. On 2026-09-09 the account's *weekly* cap answered a
429, the gate took the ~38 calls then in its 4h window as the ceiling,
persisted it, and from then on deferred every background call — image
descriptions, dreams, consolidation, search tags — for two days with zero
real 429s in the log (150 chat+triage calls per 4h against a budget of 46).

The premise was wrong. Every Codex response carries
``x-codex-primary-used-percent`` / ``-window-minutes`` /
``-reset-after-seconds`` (and ``secondary`` likewise), and
``GET /backend-api/wham/usage`` returns the same meter. So the gate now:

* reads the meter from every response (:meth:`LLMRateGate.note_usage_headers`)
  or a usage payload (:meth:`note_usage_snapshot`);
* lets background work run while the window is under a pace line
  (``BACKGROUND_HEADSTART_PERCENT`` + the elapsed fraction of the window)
  and under a hard ceiling (``BACKGROUND_CEILING_PERCENT``), so a week's
  quota is spent evenly and the last slice is kept for chat and triage;
* never defers ``interactive`` / ``triage`` work — it waits briefly through a
  429 cooldown and proceeds;
* ages a stale sample and asks for a fresh one (``refresh_meter`` hook)
  before deciding on old numbers.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from services.llm_rate_gate import (
    LLMRateGate,
    LLMRateLimitDeferred,
    QuotaMeter,
    QuotaWindow,
    background_decision,
    get_llm_rate_gate,
)

pytestmark = pytest.mark.unit


def make_429_body(resets_in_seconds=32):
    return json.dumps({
        "error": {
            "type": "usage_limit_reached",
            "message": "The usage limit has been reached",
            "plan_type": "pro",
            "resets_at": 1781137697,
            "resets_in_seconds": resets_in_seconds,
        }
    })


WEEK = 7 * 24 * 3600


def headers(primary_used=22, primary_window_min=10080, primary_reset=438272,
            secondary_used=None, secondary_window_min=0, secondary_reset=0, plan="pro"):
    h = {
        "x-codex-plan-type": plan,
        "x-codex-primary-used-percent": str(primary_used),
        "x-codex-primary-window-minutes": str(primary_window_min),
        "x-codex-primary-reset-after-seconds": str(primary_reset),
        "x-codex-secondary-window-minutes": str(secondary_window_min),
        "x-codex-secondary-reset-after-seconds": str(secondary_reset),
        "x-codex-secondary-reset-at": "",
    }
    if secondary_used is not None:
        h["x-codex-secondary-used-percent"] = str(secondary_used)
    return h


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_gate():
    clock = FakeClock()
    gate = LLMRateGate(clock=clock.time, sleep=clock.sleep, wall_clock=clock.time)
    return gate, clock


# ----- parsing the meter --------------------------------------------------

class TestMeterParsing:
    def test_headers_become_a_meter(self):
        m = QuotaMeter.from_headers(headers(), now=1000.0)
        assert m.plan_type == "pro"
        p = m.windows["primary"]
        assert p.used_percent == 22 and p.window_seconds == WEEK and p.reset_after_seconds == 438272
        assert "secondary" not in m.windows          # window-minutes 0 = no such window
        assert m.sampled_at == 1000.0

    def test_secondary_window_is_kept_when_present(self):
        m = QuotaMeter.from_headers(headers(secondary_used=40, secondary_window_min=300, secondary_reset=9000), now=1.0)
        s = m.windows["secondary"]
        assert s.used_percent == 40 and s.window_seconds == 18000 and s.reset_after_seconds == 9000

    def test_headers_without_the_meter_give_none(self):
        assert QuotaMeter.from_headers({"content-type": "text/event-stream"}, now=1.0) is None
        assert QuotaMeter.from_headers({"x-codex-primary-used-percent": "junk"}, now=1.0) is None

    def test_usage_payload_becomes_the_same_meter(self):
        payload = {
            "plan_type": "pro",
            "rate_limit": {
                "allowed": True, "limit_reached": False,
                "primary_window": {"used_percent": 22, "limit_window_seconds": WEEK, "reset_after_seconds": 438419},
                "secondary_window": None,
            },
        }
        m = QuotaMeter.from_usage_payload(payload, now=5.0)
        assert m.windows["primary"].used_percent == 22 and m.windows["primary"].window_seconds == WEEK
        assert m.limit_reached is False and m.plan_type == "pro"
        assert QuotaMeter.from_usage_payload({"nope": 1}, now=5.0) is None

    def test_round_trips_through_a_dict(self):
        m = QuotaMeter.from_headers(headers(secondary_used=3, secondary_window_min=300, secondary_reset=100), now=42.0)
        again = QuotaMeter.from_dict(m.to_dict())
        assert again == m
        assert QuotaMeter.from_dict(None) is None and QuotaMeter.from_dict({"windows": "x"}) is None


# ----- the pure decision --------------------------------------------------

class TestBackgroundDecision:
    def test_no_meter_means_allowed(self):
        ok, reason = background_decision(None, now=0.0)
        assert ok and "no meter" in reason

    def test_on_pace_is_allowed(self):
        m = QuotaMeter.from_headers(headers(primary_used=22, primary_reset=int(WEEK * 0.78)), now=0.0)
        ok, reason = background_decision(m, now=0.0)
        assert ok
        assert "22%" in reason

    def test_far_ahead_of_pace_is_deferred_until_the_line_catches_up(self):
        # 40% used with 95% of the week still to go: pace line = 15 + 5 = 20.
        m = QuotaMeter.from_headers(headers(primary_used=40, primary_reset=int(WEEK * 0.95)), now=0.0)
        ok, reason = background_decision(m, now=0.0)
        assert not ok and "40%" in reason and "pace" in reason
        # ...and is allowed again once enough of the window has elapsed.
        later = WEEK * 0.30
        ok, _ = background_decision(m, now=later)
        assert ok

    def test_the_ceiling_holds_even_late_in_the_window(self):
        m = QuotaMeter.from_headers(headers(primary_used=92, primary_reset=600), now=0.0)
        ok, reason = background_decision(m, now=0.0)
        assert not ok and "ceiling" in reason and "resets in" in reason

    def test_every_window_must_pass(self):
        m = QuotaMeter.from_headers(
            headers(primary_used=5, primary_reset=WEEK - 100, secondary_used=95, secondary_window_min=300, secondary_reset=17000),
            now=0.0,
        )
        ok, reason = background_decision(m, now=0.0)
        assert not ok and "secondary" in reason

    def test_a_window_that_has_reset_since_the_sample_counts_as_fresh(self):
        m = QuotaMeter.from_headers(headers(primary_used=99, primary_window_min=300, primary_reset=100), now=0.0)
        assert background_decision(m, now=50.0)[0] is False
        ok, reason = background_decision(m, now=200.0)
        assert ok and "reset" in reason

    def test_limit_reached_defers_until_the_reset(self):
        m = QuotaMeter.from_headers(headers(primary_used=100, primary_reset=3600), now=0.0)
        ok, reason = background_decision(m, now=0.0)
        assert not ok and "resets in" in reason


# ----- the gate -----------------------------------------------------------

class TestCooldown:
    def test_note_429_sets_cooldown_from_resets_in_seconds(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=32))
        assert 32 <= gate.cooldown_remaining() <= 32 + LLMRateGate.JITTER_MAX_SECONDS

    def test_note_429_without_parseable_body_uses_default(self):
        gate, clock = make_gate()
        gate.note_429("not json at all")
        assert gate.cooldown_remaining() >= LLMRateGate.DEFAULT_COOLDOWN_SECONDS

    def test_cooldown_is_clamped_so_a_weekly_limit_cannot_wedge_the_gate(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=442_000))
        assert gate.cooldown_remaining() <= LLMRateGate.MAX_COOLDOWN_SECONDS + LLMRateGate.JITTER_MAX_SECONDS

    def test_a_weekly_429_marks_the_meter_exhausted_until_the_reset(self):
        """The cooldown is short; the *meter* is what keeps background work
        off a cap that really is out for days — while chat still gets its
        bounded wait and an honest error."""
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=97, primary_reset=442_000))
        gate.note_429(make_429_body(resets_in_seconds=442_000))
        clock.now += LLMRateGate.MAX_COOLDOWN_SECONDS + 10   # cooldown over
        assert gate.cooldown_remaining() == 0
        with pytest.raises(LLMRateLimitDeferred) as exc:
            asyncio.run(gate.acquire("background"))
        assert "resets in" in str(exc.value)
        assert asyncio.run(gate.acquire("interactive")) is True

    def test_a_successful_call_clears_the_cooldown(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=442_000))
        assert gate.cooldown_remaining() > 0
        gate.note_success()
        assert gate.cooldown_remaining() == 0

    def test_a_fresh_meter_after_a_429_reopens_background_work(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=442_000))
        gate.note_success()
        gate.note_usage_headers(headers(primary_used=10, primary_reset=WEEK - 1000))
        assert asyncio.run(gate.acquire("background")) is True

    def test_a_fresh_429_rearms_the_cooldown_after_a_success(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=442_000))
        gate.note_success()
        gate.note_429(make_429_body(resets_in_seconds=60))
        assert gate.cooldown_remaining() > 0
        with pytest.raises(LLMRateLimitDeferred):
            asyncio.run(gate.acquire("background"))

    def test_background_call_during_cooldown_is_deferred(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=32))
        with pytest.raises(LLMRateLimitDeferred):
            asyncio.run(gate.acquire("background"))

    def test_interactive_call_waits_briefly_then_proceeds_during_a_cooldown(self):
        gate, clock = make_gate()
        gate.note_429(make_429_body(resets_in_seconds=10))
        assert asyncio.run(gate.acquire("interactive")) is True
        assert 0 < sum(clock.sleeps) <= LLMRateGate.INTERACTIVE_MAX_WAIT_SECONDS

    def test_interactive_wait_is_short_enough_to_stay_inside_a_reply_turn(self):
        assert LLMRateGate.INTERACTIVE_MAX_WAIT_SECONDS <= 10.0


class TestAcquire:
    def test_background_calls_are_spaced_but_not_starved_when_healthy(self):
        gate, clock = make_gate()

        async def two_calls():
            await gate.acquire("background")
            await gate.acquire("background")

        asyncio.run(two_calls())
        assert sum(clock.sleeps) >= LLMRateGate.BACKGROUND_MIN_INTERVAL_SECONDS

    def test_interactive_calls_are_not_paced_when_healthy(self):
        gate, clock = make_gate()

        async def two_calls():
            await gate.acquire("interactive")
            await gate.acquire("interactive")

        asyncio.run(two_calls())
        assert clock.sleeps == []

    def test_background_defers_when_the_meter_is_ahead_of_pace(self):
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=60, primary_reset=int(WEEK * 0.9)))
        with pytest.raises(LLMRateLimitDeferred) as exc:
            asyncio.run(gate.acquire("background"))
        assert "60%" in str(exc.value)

    def test_interactive_and_triage_never_defer_on_the_meter(self):
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=99, primary_reset=int(WEEK * 0.9)))
        assert asyncio.run(gate.acquire("interactive")) is True
        assert asyncio.run(gate.acquire("triage")) is True
        assert clock.sleeps == []

    def test_a_newer_sample_replaces_an_older_one_but_not_the_reverse(self):
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=10))
        clock.now += 100
        gate.note_usage_headers(headers(primary_used=30))
        assert gate.meter.windows["primary"].used_percent == 30
        gate.note_meter(QuotaMeter.from_headers(headers(primary_used=5), now=clock.now - 500))
        assert gate.meter.windows["primary"].used_percent == 30

    def test_stale_meter_is_refreshed_before_a_background_decision(self):
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=10, primary_reset=WEEK - 10))
        asked = []

        async def refresh():
            asked.append(clock.now)
            gate.note_usage_headers(headers(primary_used=95, primary_reset=WEEK - 10))

        gate.refresh_meter = refresh
        asyncio.run(gate.acquire("background"))          # fresh: no refresh
        assert asked == []
        clock.now += LLMRateGate.METER_STALE_SECONDS + 1
        with pytest.raises(LLMRateLimitDeferred):
            asyncio.run(gate.acquire("background"))      # stale: refreshed, then deferred
        assert asked

    def test_a_failing_refresh_never_breaks_a_call(self):
        gate, clock = make_gate()

        async def boom():
            raise RuntimeError("network")

        gate.refresh_meter = boom
        assert asyncio.run(gate.acquire("background")) is True


class TestStatusAndPersistence:
    def test_status_describes_the_meter_and_the_decision(self):
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=22, primary_reset=438272))
        s = gate.status()
        assert s["plan_type"] == "pro"
        assert s["windows"]["primary"]["used_percent"] == 22
        assert s["windows"]["primary"]["window_minutes"] == 10080
        assert s["background_allowed"] is True and "22%" in s["background_reason"]
        assert s["cooldown_remaining"] == 0
        assert "meter_age_seconds" in s

    def test_status_without_a_meter(self):
        gate, _ = make_gate()
        s = gate.status()
        assert s["windows"] == {} and s["background_allowed"] is True

    def test_state_round_trips(self):
        gate, clock = make_gate()
        gate.note_usage_headers(headers(primary_used=22))
        gate.note_429(make_429_body(5))
        state = gate.export_state()
        other, _ = make_gate()
        other.restore_state(state)
        assert other.meter == gate.meter
        assert other.status()["last_429_wall"] == gate.status()["last_429_wall"]

    @pytest.mark.parametrize("payload", [None, "junk", {"meter": "x"}, {"meter": {"windows": 3}}, {"budget": 46.0}])
    def test_restore_tolerates_garbage(self, payload):
        gate, _ = make_gate()
        gate.restore_state(payload)
        assert gate.meter is None

    def test_state_changes_reach_the_persistence_hook_but_not_on_every_call(self):
        gate, clock = make_gate()
        saved = []
        gate.on_state_change = lambda state: saved.append(state)
        gate.note_usage_headers(headers(primary_used=1))
        assert len(saved) == 1
        gate.note_usage_headers(headers(primary_used=2))     # within the throttle window
        assert len(saved) == 1
        clock.now += LLMRateGate.PERSIST_MIN_INTERVAL_SECONDS + 1
        gate.note_usage_headers(headers(primary_used=3))
        assert len(saved) == 2
        gate.note_429(make_429_body(5))                      # a 429 always persists
        assert len(saved) == 3

    def test_a_failing_hook_never_breaks_the_gate(self):
        gate, _ = make_gate()

        def boom(_state):
            raise RuntimeError("db down")

        gate.on_state_change = boom
        gate.note_usage_headers(headers())
        gate.note_429(make_429_body(5))


def test_singleton_gate():
    assert get_llm_rate_gate() is get_llm_rate_gate()


def test_background_llm_builder_uses_background_priority():
    from services.llm_utils import build_background_llm
    from services.codex_chat_model import ChatCodexResponses

    llm = build_background_llm()
    if isinstance(llm, ChatCodexResponses):
        assert llm.priority == "background"
