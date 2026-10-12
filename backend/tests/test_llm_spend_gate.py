"""Under ``EDWARD_LLM_PROVIDER=anthropic`` the rate gate decides on the
Claude spend cap, not the Codex meter (2026-10-10).

Every gated call already goes through ``LLMRateGate.acquire(priority,
purpose)``, so the switch lives there, once, rather than at forty call
sites: background work defers on the day's spend (``llm_spend.check_spend``),
Ellis's turns are refused only past cap + reserve, and the Codex meter — a
weekly window Edward no longer spends — is not consulted, refreshed or
shown as his quota. The surplus queue never runs: with nothing expiring at
a reset there is no use-it-or-lose-it quota to spend. With the switch unset
the gate is exactly what it was.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services import llm_spend as ls
from services.llm_rate_gate import LLMRateGate, LLMRateLimitDeferred, QuotaMeter, surplus_decision

WEEK = 7 * 24 * 3600


def _headers(used, reset=WEEK // 2):
    return {"x-codex-plan-type": "pro", "x-codex-primary-used-percent": str(used),
            "x-codex-primary-window-minutes": "10080", "x-codex-primary-reset-after-seconds": str(reset)}


async def _no_sleep(_s):
    return None


@pytest.fixture(autouse=True)
def _ten_dollar_cap(monkeypatch):
    """These cases read the thresholds of a $10 cap (a $6 background stop,
    $13 with the reserve). The default became $5 on Ellis's answer
    (2026-10-11); ``test_llm_spend`` pins that."""
    monkeypatch.setenv("EDWARD_DAILY_SPEND_CAP_USD", "10")


@pytest.fixture
def gate(monkeypatch):
    g = LLMRateGate(sleep=_no_sleep)
    monkeypatch.setattr("services.llm_rate_gate._gate", g)
    return g


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "anthropic")


@pytest.fixture
def spent(monkeypatch):
    """Today's spend as the table reports it (Springfield day)."""
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: ZoneInfo("America/Chicago"))

    class S:
        usd = 0.0
        reads = 0

    async def _spent(start, end):
        S.reads += 1
        return S.usd

    monkeypatch.setattr(ls, "_db_spent_between", _spent)
    return S


# ------------------------------------------------------------- acquire --

async def test_background_defers_on_the_spend_cap_not_a_healthy_meter(gate, anthropic, spent):
    gate.note_usage_headers(_headers(5))           # the Codex meter would allow it
    spent.usd = 6.0
    with pytest.raises(LLMRateLimitDeferred) as err:
        await gate.acquire("background", purpose="memory_service.extract")
    assert "$6.00" in str(err.value)


async def test_background_runs_under_the_share_even_with_an_exhausted_codex_meter(gate, anthropic, spent):
    gate.note_usage_headers(_headers(100, reset=3600))
    refreshed = []

    async def _refresh():
        refreshed.append(1)

    gate.refresh_meter = _refresh
    spent.usd = 2.0
    assert await gate.acquire("background", purpose="memory_service.extract") is True
    assert refreshed == []                         # nothing asks chatgpt.com for a meter


async def test_owners_turn_is_refused_past_cap_plus_reserve_without_waiting(gate, anthropic, spent):
    slept = []

    async def _sleep(s):
        slept.append(s)

    gate._sleep = _sleep
    gate.note_429('{"error": {"resets_in_seconds": 30}}')   # a Codex cooldown is open
    spent.usd = 12.0
    assert await gate.acquire("interactive", purpose="turn:chat") is True
    assert slept == []                             # no Codex cooldown to wait out
    spent.usd = 13.0
    ls.reset_cache()
    with pytest.raises(ls.LLMSpendCapReached):
        await gate.acquire("interactive", purpose="turn:chat")


async def test_a_wake_is_refused_at_the_cap(gate, anthropic, spent):
    spent.usd = 10.0
    with pytest.raises(ls.LLMSpendCapReached):
        await gate.acquire("interactive", purpose="mind:evening")


async def test_the_codex_path_ignores_the_claude_spend(gate, spent):
    gate.note_usage_headers(_headers(5))
    spent.usd = 500.0
    assert await gate.acquire("background", purpose="memory_service.extract") is True
    assert await gate.acquire("interactive", purpose="turn:chat") is True
    assert spent.reads == 0


# ------------------------------------------------------------ surplus --

@pytest.mark.unit
def test_pay_per_token_has_no_surplus_whatever_the_meter_says():
    now = time.time()
    meter = QuotaMeter.from_headers(_headers(10, reset=3 * 24 * 3600), now=now)
    assert surplus_decision(meter, now, 0.1).allowed
    d = surplus_decision(meter, now, 0.1, pay_per_token=True)
    assert not d.allowed and d.code == "pay_per_token" and "per token" in d.reason


async def test_the_gate_refuses_surplus_under_anthropic(gate, anthropic, spent):
    gate.note_usage_headers(_headers(10, reset=3 * 24 * 3600))
    d = gate.surplus_decision()
    assert not d.allowed and d.code == "pay_per_token"
    with pytest.raises(LLMRateLimitDeferred):
        await gate.acquire("surplus", purpose="surplus:research")


# ------------------------------------------------- the meter's readers --

async def test_the_codex_meter_is_not_edwards_quota_under_anthropic(gate, anthropic, spent):
    gate.note_usage_headers(_headers(100, reset=3600))
    assert gate.meter is None                      # every reader sees "no Codex meter"
    assert gate._meter is not None                 # kept, should the switch go back
    status = gate.status()
    assert status["provider"] == "anthropic" and status["windows"] == {}
    assert status["limit_reached"] is False


async def test_background_decision_reads_the_cached_spend(gate, anthropic, spent):
    assert gate.background_decision()[0] is True   # nothing read yet today: allowed
    spent.usd = 7.0
    await ls.spent_today()
    allowed, reason = gate.background_decision()
    assert allowed is False and "$7.00" in reason


async def test_the_codex_meter_still_reads_when_the_switch_is_unset(gate):
    gate.note_usage_headers(_headers(30))
    assert gate.meter is not None and gate.status()["provider"] == "codex"


@pytest.mark.parametrize("usd,state", [(1.0, "ok"), (6.0, "paced"), (10.0, "exhausted"), (12.0, "exhausted")])
async def test_the_pulse_and_the_minds_quota_word_follows_the_spend(gate, anthropic, spent, usd, state):
    from services import expression_service, mind_service

    gate.note_usage_headers(_headers(100, reset=3600))   # a spent Codex week must not read as exhausted
    spent.usd = usd
    await ls.spent_today()
    assert expression_service.quota_state() == state
    assert mind_service.quota_state() == state


async def test_a_spent_codex_week_does_not_stop_the_pulse_under_anthropic(gate, anthropic, spent):
    from services import expression_service

    gate.note_usage_headers(_headers(100, reset=3600))
    assert expression_service.quota_state() == "ok"


# ------------------------------------------- /api/mind/now's quota --
#
# Review 2026-10-11: under anthropic ``gate.meter`` reads None, so
# ``wake_state._quota_now`` always said ``exhausted: False`` — the mind
# skipped every wake past the cap while his presence on Today, /screen and
# the widget said he was fine ("the animation is a claim, so it must be
# true"). And a skipped wake's note still said "Codex quota exhausted".

def _next_chicago_midnight(now_utc):
    from datetime import timedelta

    chi = ZoneInfo("America/Chicago")
    local = now_utc.astimezone(chi).date() + timedelta(days=1)
    return datetime.combine(local, datetime.min.time(), tzinfo=chi).astimezone(timezone.utc)


async def test_mind_now_rests_him_once_the_days_cap_is_spent(gate, anthropic, spent):
    from services import wake_state

    gate.note_usage_headers(_headers(5))           # a healthy Codex week must not hide it
    spent.usd = 11.0
    await ls.spent_today()
    q = wake_state._quota_now()
    assert q["exhausted"] is True
    assert q["period"] == "day"
    assert q["used_percent"] == pytest.approx(110.0)
    assert q["resets_at"] == _next_chicago_midnight(datetime.now(timezone.utc))


async def test_mind_now_under_the_cap_is_not_resting(gate, anthropic, spent):
    from services import wake_state

    spent.usd = 4.0
    await ls.spent_today()
    q = wake_state._quota_now()
    assert q["exhausted"] is False and q["used_percent"] == pytest.approx(40.0) and q["period"] == "day"


async def test_mind_now_on_codex_is_the_week(gate):
    from services import wake_state

    gate.note_usage_headers(_headers(41))
    q = wake_state._quota_now()
    assert q["period"] == "week" and q["used_percent"] == pytest.approx(41.0) and q["exhausted"] is False


async def test_a_skipped_wake_names_the_days_cap_not_codex(gate, anthropic, spent, monkeypatch):
    from services import mind_service

    notes = []

    async def _record_now(**kw):
        notes.append(kw["note"])

    monkeypatch.setattr(mind_service, "_record_now", _record_now)
    spent.usd = 10.5
    await ls.spent_today()
    out = await mind_service.wake("morning")
    assert out.get("skipped") is True
    assert notes and "codex" not in notes[0].lower() and "spend cap" in notes[0]
