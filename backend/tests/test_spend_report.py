"""``GET /api/health/spend?days=7`` (muse-and-beyond plan Phase 3,
2026-09-24): who spends the week, how fast, and what it bought.

Pure aggregation over ``codex_calls`` and ``llm_meter_samples`` (tested
here without a database), then counts from the tables that record what the
spend produced: memories extracted and used (``turn_log``), mind wakes by
outcome (``ledger_events``), pushes sent and opened (``push_log``),
documents written, Ellis's verdicts (``user_signals``).

The meter-%-per-1M-tokens regression refuses to answer on thin data: it
reports ``n`` and a status instead of a number it cannot back.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from services import spend_report_service as sr

T0 = datetime(2026, 9, 20, 0, 0, 0)


def _call(minutes, **kw):
    row = {
        "created_at": T0 + timedelta(minutes=minutes), "purpose": "graph.streaming._stream_llm_with_events",
        "priority": "interactive", "turn_source": "chat", "model": "gpt-6-astra", "effort": "medium",
        "input_tokens": 1000, "cached_tokens": 800, "output_tokens": 100, "reasoning_tokens": 40,
        "latency_ms": 2000, "ttft_ms": 900, "status": "ok",
    }
    row.update(kw)
    return row


def _sample(minutes, used, window="primary", reset_hours=100.0):
    at = T0 + timedelta(minutes=minutes)
    return {"sampled_at": at, "window": window, "used_percent": used, "window_minutes": 10080,
            "resets_at": T0 + timedelta(hours=reset_hours), "source": "headers"}


# ------------------------------------------------------------- totals --

def test_summarize_calls_by_group():
    rows = [
        _call(0, latency_ms=1000),
        _call(1, latency_ms=3000, cached_tokens=0),
        _call(2, turn_source="pulse", model="gpt-5.6-terra", status="error", input_tokens=0, cached_tokens=0,
              output_tokens=0, reasoning_tokens=0, latency_ms=500),
        _call(3, turn_source=None, status="rate_limited", input_tokens=None, cached_tokens=None,
              output_tokens=None, reasoning_tokens=None, latency_ms=None),
    ]
    by_source = sr.summarize_calls(rows, "turn_source")
    chat = by_source["chat"]
    assert chat["calls"] == 2
    assert chat["input_tokens"] == 2000 and chat["cached_tokens"] == 800 and chat["uncached_input_tokens"] == 1200
    assert chat["output_tokens"] == 200 and chat["reasoning_tokens"] == 80
    assert chat["cache_hit_ratio"] == 0.4
    assert chat["latency_ms"]["p50"] == 2000
    assert chat["errors"] == {}
    assert by_source["pulse"]["errors"] == {"error": 1}
    assert by_source["pulse"]["cache_hit_ratio"] is None
    assert by_source["unknown"]["errors"] == {"rate_limited": 1}

    by_model = sr.summarize_calls(rows, "model")
    assert set(by_model) == {"gpt-6-astra", "gpt-5.6-terra"}
    assert list(by_model) == ["gpt-6-astra", "gpt-5.6-terra"], "biggest spender first"


# -------------------------------------------------------------- meter --

def test_burn_rate_over_the_last_hours():
    samples = [_sample(m, 10 + m / 60.0) for m in range(0, 24 * 60 + 1, 30)]  # +1%/h
    now = T0 + timedelta(hours=24)
    burn = sr.burn_rate(samples, now=now, hours=24)
    assert burn["percent_per_hour"] == pytest.approx(1.0)
    assert burn["n"] == 49
    six = sr.burn_rate(samples, now=now, hours=6)
    assert six["percent_per_hour"] == pytest.approx(1.0)
    assert six["span_hours"] == pytest.approx(6.0)


def test_burn_rate_counts_what_was_spent_after_a_reset():
    samples = [_sample(0, 96), _sample(60, 98), _sample(120, 1, reset_hours=300), _sample(180, 3, reset_hours=300)]
    burn = sr.burn_rate(samples, now=T0 + timedelta(hours=3), hours=24)
    # 2 before the reset, 1 across it (from zero), 2 after: 5 points in 3 hours
    assert burn["percent_per_hour"] == pytest.approx(5 / 3)


def test_burn_rate_needs_two_samples():
    assert sr.burn_rate([_sample(0, 5)], now=T0 + timedelta(hours=1), hours=24) is None
    assert sr.burn_rate([], now=T0, hours=24) is None


def test_reset_outlook_projects_used_percent_at_reset():
    samples = [_sample(0, 40, reset_hours=50), _sample(60, 42, reset_hours=50)]
    now = T0 + timedelta(hours=2)
    out = sr.reset_outlook(samples, now=now, burn_per_hour=1.5)
    assert out["used_percent"] == 42
    assert out["hours_to_reset"] == pytest.approx(48.0)
    assert out["projected_at_reset"] == pytest.approx(42 + 1.5 * 48)
    assert sr.reset_outlook([], now=now, burn_per_hour=1.0) is None
    assert sr.reset_outlook(samples, now=now, burn_per_hour=None)["projected_at_reset"] is None


def test_meter_series_is_per_window_and_sorted():
    samples = [_sample(10, 2), _sample(0, 1), _sample(5, 50, window="secondary")]
    series = sr.meter_series(samples)
    assert [p["used_percent"] for p in series["primary"]] == [1, 2]
    assert [p["used_percent"] for p in series["secondary"]] == [50]


# --------------------------------------------------------- regression --

def _synthetic_week(seed=7, noise=0.0):
    """Two models; each costs a known meter-% per 1M tokens of each kind."""
    rng = random.Random(seed)
    truth = {
        "gpt-6-astra": {"uncached_input": 2.0, "cached_input": 0.2, "output": 8.0},
        "gpt-5.6-terra": {"uncached_input": 0.5, "cached_input": 0.05, "output": 2.0},
    }
    calls, samples, used = [], [], 0.0
    samples.append(_sample(0, used))
    for step in range(1, 200):
        start = (step - 1) * 30
        delta = 0.0
        for model, cost in truth.items():
            for _ in range(rng.randint(1, 4)):
                inp = rng.randint(2_000, 60_000)
                cached = rng.randint(0, inp)
                out = rng.randint(100, 4_000)
                calls.append(_call(start + rng.randint(1, 29), model=model, input_tokens=inp, cached_tokens=cached,
                                   output_tokens=out))
                delta += ((inp - cached) * cost["uncached_input"] + cached * cost["cached_input"]
                          + out * cost["output"]) / 1_000_000
        used += delta + (rng.gauss(0, noise) if noise else 0.0)
        samples.append(_sample(step * 30, used))
    return truth, calls, samples


def test_estimate_recovers_the_cost_per_million_tokens_per_model():
    truth, calls, samples = _synthetic_week()
    est = sr.estimate_meter_cost(samples, calls, min_interval_s=1800)
    assert est["status"] == "ok"
    assert est["n"] == 199
    for model, cost in truth.items():
        got = est["models"][model]
        for kind, value in cost.items():
            assert got[kind] == pytest.approx(value, rel=1e-3), (model, kind)
    assert est["r2"] == pytest.approx(1.0, abs=1e-6)


def test_estimate_refuses_thin_data():
    truth, calls, samples = _synthetic_week()
    est = sr.estimate_meter_cost(samples[:6], calls, min_interval_s=1800)
    assert est["status"] == "insufficient"
    assert est["n"] == 5 and est["k"] == 6
    assert est.get("models") in (None, {})


def test_estimate_skips_intervals_across_a_reset():
    samples = [_sample(0, 90), _sample(30, 95), _sample(60, 2, reset_hours=300), _sample(90, 4, reset_hours=300)]
    intervals = sr.meter_intervals(samples, min_interval_s=1800)
    assert [(round(i["delta"], 3)) for i in intervals] == [5.0, 2.0]


def test_estimate_drops_token_kinds_a_model_never_used():
    truth, calls, samples = _synthetic_week()
    for c in calls:
        c["cached_tokens"] = 0
    est = sr.estimate_meter_cost(samples, calls, min_interval_s=1800)
    assert est["status"] in ("ok", "unstable")
    assert est["models"]["gpt-6-astra"]["cached_input"] is None


# -------------------------------------------------------- attribution --

def test_estimated_percent_uses_the_regression_when_it_is_sound():
    truth, calls, samples = _synthetic_week()
    est = sr.estimate_meter_cost(samples, calls, min_interval_s=1800)
    call = _call(0, model="gpt-6-astra", input_tokens=1_100_000, cached_tokens=100_000, output_tokens=500_000)
    got = sr.estimated_percent(call, est)
    assert got == pytest.approx(1.0 * 2.0 + 0.1 * 0.2 + 0.5 * 8.0, rel=1e-3)


def test_estimated_percent_falls_back_to_the_pooled_rate_then_to_nothing():
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 2.0, "models": {}}
    call = _call(0, input_tokens=600_000, cached_tokens=100_000, output_tokens=400_000)
    assert sr.estimated_percent(call, pooled) == pytest.approx(2.0)
    assert sr.estimated_percent(call, {"status": "insufficient", "pooled_percent_per_1m_tokens": None}) is None


def test_groups_carry_an_estimated_share_of_the_meter():
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    rows = [_call(0, turn_source="pulse", input_tokens=2_000_000, cached_tokens=0, output_tokens=0),
            _call(1, turn_source="chat", input_tokens=500_000, cached_tokens=0, output_tokens=500_000)]
    by = sr.summarize_calls(rows, "turn_source", est=pooled)
    assert by["pulse"]["estimated_percent"] == pytest.approx(2.0)
    assert by["chat"]["estimated_percent"] == pytest.approx(1.0)
    assert sr.summarize_calls(rows, "turn_source")["pulse"]["estimated_percent"] is None


def test_interactive_burn_is_a_distribution_over_hours():
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    rows = []
    for hour in range(10):
        rows.append(_call(hour * 60 + 5, priority="interactive", input_tokens=(hour + 1) * 100_000, cached_tokens=0,
                          output_tokens=0))
        rows.append(_call(hour * 60 + 6, priority="background", input_tokens=5_000_000, cached_tokens=0,
                          output_tokens=0))
    out = sr.interactive_burn(rows, pooled, now=T0 + timedelta(hours=10))
    assert out["hours"] == 10
    assert out["p50"] == pytest.approx(0.55)
    assert out["p95"] == pytest.approx(0.955)
    assert out["priorities"] == ["interactive", "triage"]
    assert sr.interactive_burn(rows, {"status": "insufficient", "pooled_percent_per_1m_tokens": None},
                               now=T0 + timedelta(hours=10)) is None


# ------------------------------------------------------------ compose --

def test_compose_spend_carries_every_section():
    rows = [_call(m) for m in range(0, 600, 10)]
    samples = [_sample(m, 10 + m / 120.0) for m in range(0, 601, 30)]
    now = T0 + timedelta(minutes=600)
    report = sr.compose_spend(days=7, calls=rows, samples=samples, outputs={"documents_created": 2}, now=now)
    assert report["window_days"] == 7
    assert report["totals"]["calls"] == 60
    assert set(report["by"]) == {"purpose", "turn_source", "model"}
    assert report["burn"]["last_24h"]["percent_per_hour"] == pytest.approx(0.5)
    assert report["burn"]["last_6h"]["percent_per_hour"] == pytest.approx(0.5)
    assert report["outlook"]["hours_to_reset"] == pytest.approx(90.0)
    assert report["meter_cost"]["status"] == "insufficient"
    assert report["bought"] == {"documents_created": 2}
    assert "primary" in report["meter"]
    # the pooled rate still prices each group and the interactive burn
    assert report["by"]["turn_source"]["chat"]["estimated_percent"] == pytest.approx(
        report["meter_cost"]["pooled_percent_per_1m_tokens"] * 60 * 1100 / 1e6)
    assert report["interactive_burn"]["hours"] == 10


# ---------------------------------------------------------------- I/O --

@pytest.mark.integration
async def test_build_spend_report_reads_the_tables(clean_db):
    from services.database import (
        async_session, CodexCallModel, LLMMeterSampleModel, TurnLogModel, LedgerEventModel, PushLogModel,
        DocumentModel, UserSignalModel,
    )

    now = datetime.utcnow()
    async with async_session() as s:
        s.add(CodexCallModel(created_at=now - timedelta(hours=1), purpose="p", turn_source="mind", model="gpt-6-astra",
                             input_tokens=100, cached_tokens=50, output_tokens=10, reasoning_tokens=5, latency_ms=100,
                             status="ok"))
        s.add(LLMMeterSampleModel(sampled_at=now - timedelta(hours=2), window="primary", used_percent=10.0,
                                  window_minutes=10080, resets_at=now + timedelta(days=3), source="headers"))
        s.add(LLMMeterSampleModel(sampled_at=now - timedelta(hours=1), window="primary", used_percent=12.0,
                                  window_minutes=10080, resets_at=now + timedelta(days=3), source="headers"))
        s.add(TurnLogModel(id="t1", source="chat", started_at=now - timedelta(hours=1), outcome="completed",
                           memories_extracted=3, memories_used=2))
        s.add(LedgerEventModel(kind="mind", source="morning", outcome="acted"))
        s.add(LedgerEventModel(kind="mind", source="self", outcome="left_alone"))
        s.add(PushLogModel(source="mind", title="t", body="b", sent=1, opened_at=now))
        s.add(PushLogModel(source="mind", title="t", body="b", sent=1))
        s.add(DocumentModel(title="Edward: state", content="x"))
        s.add(UserSignalModel(kind="mind_verdict"))
        await s.commit()

    report = await sr.build_spend_report(days=7)
    assert report["totals"]["calls"] == 1
    assert report["by"]["turn_source"]["mind"]["cached_tokens"] == 50
    assert report["burn"]["last_24h"]["percent_per_hour"] == pytest.approx(2.0, rel=0.01)
    bought = report["bought"]
    assert bought["memories_extracted"] == 3 and bought["memories_used"] == 2
    assert bought["mind_wakes"] == {"acted": 1, "left_alone": 1}
    assert bought["pushes_sent"] == 2 and bought["pushes_opened"] == 1
    assert bought["documents_created"] == 1
    assert bought["verdicts"] == {"mind_verdict": 1}


@pytest.mark.integration
async def test_the_route_answers(clean_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import spend

    app = FastAPI()
    app.include_router(spend.router, prefix="/api")
    with TestClient(app) as client:
        res = client.get("/api/health/spend?days=3")
    assert res.status_code == 200
    body = res.json()
    assert body["window_days"] == 3
    assert body["meter_cost"]["status"] == "insufficient"


def test_main_registers_the_spend_router_behind_auth():
    import inspect
    import main
    from middleware.auth import PUBLIC_ROUTES, PUBLIC_PREFIXES

    src = inspect.getsource(main)
    assert "spend.router" in src
    assert "/api/health/spend" not in PUBLIC_ROUTES
    assert not any("/api/health/spend".startswith(p) for p in PUBLIC_PREFIXES)


# ----------------------------------------------- surplus's r_int (2026-09-24) --

def test_interactive_burn_carries_a_day_smoothed_p95_for_the_surplus_reserve():
    """The surplus reserve is ``r_int · hours``. The hourly p95 is Ellis's
    busiest hour; multiplied by a hundred hours it reserves the whole
    window. ``p95_day`` is the p95 of trailing-24h averages: a heavy day of
    chat, sustained — what a reserve should cover."""
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    rows = []
    for hour in range(48):
        if hour % 24 in (0, 9, 20):                 # three busy hours a day, 2 %/h each
            rows.append(_call(hour * 60 + 5, priority="interactive", input_tokens=2_000_000, cached_tokens=0,
                              output_tokens=0))
    out = sr.interactive_burn(rows, pooled, now=T0 + timedelta(hours=48))
    assert out["p95"] == pytest.approx(2.0)
    assert out["p95_day"] == pytest.approx(6.0 / 24, rel=0.05), "a day with 6 % of chat is 0.25 %/h sustained"
    assert out["mean"] == pytest.approx(0.25, rel=0.05)


def test_surplus_work_is_not_counted_as_owners_interactive_burn():
    """A research or prep wake is a chat-path turn (priority interactive).
    Counted as Ellis's burn it would grow the reserve that holds surplus
    back — the more surplus ran, the less it could run."""
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    rows = [
        _call(5, priority="interactive", input_tokens=1_000_000, cached_tokens=0, output_tokens=0),
        _call(65, priority="interactive", purpose="surplus:research", input_tokens=5_000_000, cached_tokens=0,
              output_tokens=0),
    ]
    out = sr.interactive_burn(rows, pooled, now=T0 + timedelta(hours=2))
    assert out["max"] == pytest.approx(1.0)


def test_autonomous_turns_are_not_counted_as_owners_interactive_burn():
    """Mind wakes and the surfaces pulse run at interactive priority too
    (2026-09-25 audit: 127 of 134 wakes in three days were chat-path turns
    labelled ``unknown``). r_int is the reserve for *Ellis's* chat; counting
    Edward's own turns in it grew the reserve that holds surplus back. They
    are recognised by their purpose (``mind:<reason>``, ``pulse``) and, for
    rows written before the labels, by the turn's source."""
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    big = dict(input_tokens=5_000_000, cached_tokens=0, output_tokens=0)
    rows = [
        _call(5, priority="interactive", input_tokens=1_000_000, cached_tokens=0, output_tokens=0),
        _call(10, purpose="mind:evening", turn_source="mind", **big),
        _call(15, purpose="mind:free", turn_source="mind", **big),
        _call(20, purpose="pulse", turn_source="pulse", **big),
        _call(25, purpose="unknown", turn_source="mind", **big),     # before the labels
        _call(30, purpose="graph.streaming._stream_llm_with_events", turn_source="pulse", **big),
        _call(35, purpose="surplus:research", turn_source="mind", **big),
    ]
    out = sr.interactive_burn(rows, pooled, now=T0 + timedelta(hours=1))
    assert out["max"] == pytest.approx(1.0), "only Ellis's chat call counts"
    assert sr.is_autonomous_call({"purpose": "mind:morning"}) is True
    assert sr.is_autonomous_call({"purpose": "pulse"}) is True
    assert sr.is_autonomous_call({"purpose": "surplus:free_time"}) is True
    assert sr.is_autonomous_call({"purpose": None, "turn_source": "pulse"}) is True
    assert sr.is_autonomous_call({"purpose": "x", "turn_source": "chat"}) is False
    assert sr.is_autonomous_call({"purpose": "mindful", "turn_source": "voice"}) is False, "a prefix, not a substring"
    assert sr.is_autonomous_call({"purpose": None, "turn_source": "heartbeat"}) is False


def test_autonomous_burn_is_the_minds_and_the_pulses_non_deferrable_spend():
    """Wakes and the pulse run at interactive priority, so the gate never
    defers them: the surplus reserve must hold room for them beside Ellis's
    chat or the week overshoots its target into background's ceiling
    (review 2026-09-26). Surplus-launched wakes are paced by the surplus
    decision itself and stay out."""
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    big = dict(input_tokens=2_000_000, cached_tokens=0, output_tokens=0)
    rows = [
        _call(5, priority="interactive", input_tokens=1_000_000, cached_tokens=0, output_tokens=0),  # Ellis
        _call(10, purpose="mind:evening", turn_source="mind", **big),
        _call(15, purpose="pulse", turn_source="pulse", **big),
        _call(20, purpose="unknown", turn_source="mind", **big),                  # before the labels
        _call(25, purpose="surplus:free_time", turn_source="mind", **big),       # paced by surplus
        _call(30, purpose="mind:self", turn_source="mind", priority="background", **big),  # deferrable
    ]
    out = sr.autonomous_burn(rows, pooled, now=T0 + timedelta(hours=1))
    assert out["max"] == pytest.approx(6.0), "the evening wake, the pulse and the unlabelled mind turn"
    assert sr.interactive_burn(rows, pooled, now=T0 + timedelta(hours=1))["max"] == pytest.approx(1.0)
    assert sr.autonomous_burn([], pooled, now=T0) is None


def test_the_spend_snapshot_carries_the_autonomous_burn():
    rows = [_call(5, purpose="mind:morning", turn_source="mind", input_tokens=1_000_000, cached_tokens=0,
                  output_tokens=0)]
    out = sr.compose_spend(days=7, calls=rows, samples=[], outputs={}, now=T0 + timedelta(hours=1))
    assert "autonomous_burn" in out


def test_the_day_smoothed_p95_needs_a_day_of_history():
    pooled = {"status": "insufficient", "pooled_percent_per_1m_tokens": 1.0, "models": {}}
    rows = [_call(m, priority="interactive", input_tokens=100_000, cached_tokens=0, output_tokens=0)
            for m in range(0, 600, 60)]
    out = sr.interactive_burn(rows, pooled, now=T0 + timedelta(hours=10))
    assert out["p95_day"] is None


@pytest.mark.integration
async def test_spend_snapshot_reads_the_ledger_without_the_outputs(clean_db):
    from services.database import async_session, CodexCallModel, LLMMeterSampleModel

    now = datetime.utcnow()
    async with async_session() as s:
        s.add(CodexCallModel(created_at=now - timedelta(hours=1), purpose="surplus:research", priority="surplus",
                             turn_source="mind", model="gpt-6-astra", input_tokens=100, cached_tokens=0,
                             output_tokens=10, status="ok"))
        s.add(LLMMeterSampleModel(sampled_at=now - timedelta(hours=2), window="primary", used_percent=10.0,
                                  window_minutes=10080, resets_at=now + timedelta(days=3), source="headers"))
        s.add(LLMMeterSampleModel(sampled_at=now - timedelta(hours=1), window="primary", used_percent=12.0,
                                  window_minutes=10080, resets_at=now + timedelta(days=3), source="headers"))
        await s.commit()
    snap = await sr.spend_snapshot(days=7)
    assert snap["totals"]["calls"] == 1
    assert "surplus:research" in snap["by"]["purpose"]
    assert snap["bought"] == {}
    assert snap["outlook"]["used_percent"] == 12.0
