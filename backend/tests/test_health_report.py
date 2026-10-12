"""``GET /api/health/report`` — the one call a health check starts from.

Pure summaries over ledger rows; the DB fetch is thin.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from services import health_report_service as hr


def _turn(**kw):
    base = dict(
        id="t", conversation_id="c", source="chat", model="m", provider="openai",
        started_at=datetime(2026, 9, 12, 10, 0), outcome="completed", ttft_ms=800,
        total_ms=4000, iterations=1, tool_calls=0, tool_failures=0, tools_json=None,
        memories_retrieved=3, memories_used=1, memories_extracted=0,
        repeat_of_previous=False, abandoned_previous=False, fast_context=False,
        error_class=None, response_chars=120,
    )
    base.update(kw)
    return base


def test_percentile_basic():
    assert hr.percentile([], 50) is None
    assert hr.percentile([5], 95) == 5
    assert hr.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5.5
    assert hr.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) >= 9


def test_summarize_turns_groups_by_source_and_outcome():
    rows = [
        _turn(source="chat"),
        _turn(source="chat", outcome="stopped", ttft_ms=None),
        _turn(source="chat", outcome="exhausted", iterations=30, tool_calls=40),
        _turn(source="voice", fast_context=True, ttft_ms=300),
        _turn(source="heartbeat", outcome="error", error_class="TimeoutError"),
        _turn(source="heartbeat", outcome="running"),
    ]
    s = hr.summarize_turns(rows)
    assert s["total"] == 6
    assert s["by_source"]["chat"]["count"] == 3
    assert s["by_source"]["chat"]["outcomes"] == {"completed": 1, "stopped": 1, "exhausted": 1}
    assert s["by_source"]["voice"]["ttft_ms"]["p50"] == 300
    assert s["by_source"]["heartbeat"]["outcomes"]["running"] == 1
    assert s["outcomes"]["error"] == 1
    assert s["errors"][0]["error_class"] == "TimeoutError"
    assert s["iterations"]["max"] == 30


def test_summarize_tool_failures_ranks_tool_and_class():
    tools = lambda *items: json.dumps([{"n": n, "ok": ok, "ms": 1, "err": err} for n, ok, err in items])
    rows = [
        _turn(tools_json=tools(("send_message", False, "missing_required_parameters"), ("web_search", True, None))),
        _turn(tools_json=tools(("send_message", False, "missing_required_parameters"))),
        _turn(tools_json=tools(("apple_calendar", False, "timeout"))),
        _turn(tools_json="not json"),
    ]
    out = hr.summarize_tool_failures(rows)
    assert out["calls"] == 4 and out["failures"] == 3
    assert out["top"][0] == {"tool": "send_message", "error_class": "missing_required_parameters", "count": 2}
    assert out["by_tool"]["send_message"] == {"calls": 2, "failures": 2}


def test_observation_trend_buckets_hits_per_day():
    today = datetime(2026, 9, 12, 12, 0)
    hits = [
        {"kind": "tool_failure", "seen_at": today},
        {"kind": "tool_failure", "seen_at": today - timedelta(days=1)},
        {"kind": "tool_failure", "seen_at": today - timedelta(days=1)},
        {"kind": "user_correction", "seen_at": today - timedelta(days=2)},
    ]
    trend = hr.observation_trend(hits, days=3, now=today)
    assert trend["days"] == ["2026-09-10", "2026-09-11", "2026-09-12"]
    assert trend["by_kind"]["tool_failure"] == [0, 2, 1]
    assert trend["by_kind"]["user_correction"] == [1, 0, 0]


def test_summarize_signals_counts_by_kind_and_detail():
    rows = [
        {"kind": "today_action", "detail_json": json.dumps({"need_kind": "pet_dose", "action": "Given"})},
        {"kind": "today_action", "detail_json": json.dumps({"need_kind": "pet_dose", "action": "Given"})},
        {"kind": "today_action", "detail_json": json.dumps({"need_kind": "open_loop", "action": "Snooze"})},
        {"kind": "today_action_failed", "detail_json": "{}"},
    ]
    s = hr.summarize_signals(rows)
    assert s["by_kind"] == {"today_action": 3, "today_action_failed": 1}
    assert s["today_actions"][0] == {"need_kind": "pet_dose", "action": "Given", "count": 2}


def test_compose_report_carries_window_and_storage():
    rep = hr.compose_report(
        days=7, turns=[_turn()], tool_rows=[_turn()], hits=[], observations=[], signals=[],
        storage={"turn_log": "8192 bytes", "disk_free_gb": 20}, now=datetime(2026, 9, 12),
    )
    assert rep["window_days"] == 7
    assert rep["turns"]["total"] == 1
    assert rep["storage"]["disk_free_gb"] == 20
    assert "observations" in rep and "signals" in rep


# ------------------------------------------------------- second round --

def _ev(**kw):
    base = dict(kind="rate_gate", source="reflection_service.generate_queries", outcome="deferred",
                reason="cooldown for another 60s", ref_id=None, detail_json=None,
                created_at=datetime(2026, 9, 12, 10, 0))
    base.update(kw)
    return base


def test_summarize_events_by_kind():
    rows = [
        _ev(), _ev(), _ev(source="consolidation_service.run_cycle"),
        _ev(kind="scheduled_event", source="scheduler", outcome="completed", detail_json=json.dumps({"attempts": 1, "delivered": True})),
        _ev(kind="scheduled_event", source="scheduler", outcome="completed", detail_json=json.dumps({"attempts": 2, "delivered": False})),
        _ev(kind="scheduled_event", source="scheduler", outcome="failed", reason="TimeoutError"),
        _ev(kind="browser", source="click", outcome="failed", reason="page_did_not_settle"),
        _ev(kind="decision", source="pulse", outcome="left_alone", reason="Nothing new."),
        _ev(kind="decision", source="pulse", outcome="changed", reason="Disc game on the widget."),
        _ev(kind="voice_session", source="normal", outcome="ended", detail_json=json.dumps({"turns": 3, "bridges": 1, "barge_ins": 0, "interrupts": 1, "restarts": 2, "errors": 0, "duration_s": 90})),
    ]
    s = hr.summarize_events(rows)
    assert s["rate_gate"]["deferrals"] == 3
    assert s["rate_gate"]["by_caller"][0] == {"caller": "reflection_service.generate_queries", "count": 2}
    assert s["scheduled_events"]["outcomes"] == {"completed": 2, "failed": 1}
    assert s["scheduled_events"]["delivered"] == 1 and s["scheduled_events"]["retried"] == 1
    assert s["browser"]["failures"] == 1 and s["browser"]["top"][0]["action"] == "click"
    assert s["decisions"]["pulse"] == {"left_alone": 1, "changed": 1}
    assert s["decisions"]["recent"][0]["reason"] == "Nothing new."
    assert s["voice"]["sessions"] == 1 and s["voice"]["turns"] == 3 and s["voice"]["restarts"] == 2


def test_summarize_log_text_ranks_tracebacks_by_innermost_edward_frame():
    log = """INFO: fine
Traceback (most recent call last):
  File "/Users/me/edward/backend/services/wakeup_service.py", line 1956, in run_tick
    decision = await _ask_llm(signals)
  File "/Users/me/edward/backend/services/wakeup_service.py", line 1748, in _ask_llm
    response = await llm.ainvoke([
  File "/Users/me/edward/backend/.venv/lib/python3.12/site-packages/langchain_core/x.py", line 1, in y
    raise exceptions[0]
openai.RateLimitError: Error code: 429 - {'id': 'abc123'}
WARNING: HTTP 429 from codex
Traceback (most recent call last):
  File "/Users/me/edward/backend/services/wakeup_service.py", line 1748, in _ask_llm
    response = await llm.ainvoke([
openai.RateLimitError: Error code: 429 - {'id': 'def456'}
CODEX_LIMIT weekly cap
LLMRateLimitDeferred: Codex rate gate
"""
    s = hr.summarize_log_text(log)
    assert s["tracebacks"] == 2
    assert s["top"][0] == {"where": "services/wakeup_service.py:_ask_llm", "error": "openai.RateLimitError", "count": 2}
    assert s["http_429"] == 1 and s["codex_limit"] == 1 and s["gate_deferrals"] == 1


def test_summarize_wakes_and_workers():
    events = [
        {"kind": "mind", "source": "morning", "outcome": "acted"},
        {"kind": "mind", "source": "morning", "outcome": "left_alone"},
        {"kind": "mind", "source": "self", "outcome": "left_alone"},
        {"kind": "decision", "source": "pulse", "outcome": "changed"},
    ]
    w = hr.summarize_wakes(events)
    assert w["wakes"] == 3
    assert w["by_source"]["morning"] == {"acted": 1, "left_alone": 1}
    assert w["by_outcome"] == {"acted": 1, "left_alone": 2}

    tasks = [
        {"status": "completed", "task_type": "internal_worker", "started_at": datetime(2026, 9, 12, 10), "completed_at": datetime(2026, 9, 12, 10, 2), "error": None},
        {"status": "failed", "task_type": "cc_session", "started_at": datetime(2026, 9, 12, 10), "completed_at": datetime(2026, 9, 12, 10, 5), "error": "TimeoutError: codex exec 300s"},
        {"status": "running", "task_type": "internal_worker", "started_at": datetime(2026, 9, 12, 11), "completed_at": None, "error": None},
    ]
    r = hr.summarize_workers(tasks)
    assert r["by_status"] == {"completed": 1, "failed": 1, "running": 1}
    assert r["duration_s"]["p50"] == 210
    assert r["errors"][0]["error_class"].startswith("TimeoutError")


def test_compose_report_includes_second_round_sections():
    rep = hr.compose_report(
        days=7, turns=[], tool_rows=[], hits=[], observations=[], signals=[], storage={},
        events=[_ev()], workers=[], log_text="Traceback (most recent call last):\n  File \"/Users/me/edward/backend/services/a.py\", line 1, in f\n    x\nValueError: boom\n",
        now=datetime(2026, 9, 12),
    )
    assert rep["events"]["rate_gate"]["deferrals"] == 1
    assert rep["log"]["tracebacks"] == 1
    assert "wakes" in rep and "workers" in rep


def test_summarize_mind_section():
    from datetime import datetime
    import json

    events = [
        {"kind": "mind", "source": "morning", "outcome": "acted", "created_at": datetime(2026, 9, 18, 7),
         "detail_json": json.dumps({"tools": ["leave_note", "remember"]})},
        {"kind": "mind", "source": "messages", "outcome": "left_alone", "created_at": datetime(2026, 9, 18, 9),
         "detail_json": json.dumps({"tools": []})},
        {"kind": "mind", "source": "evening", "outcome": "acted", "created_at": datetime(2026, 9, 17, 18),
         "detail_json": json.dumps({"tools": ["send_push_notification"]})},
        {"kind": "decision", "source": "pulse", "outcome": "changed", "created_at": datetime(2026, 9, 18, 8)},
    ]
    signals = [{"kind": "mind_verdict", "detail_json": json.dumps({"verdict": "right_call"})},
               {"kind": "mind_verdict", "detail_json": json.dumps({"verdict": "wrong_call"})},
               {"kind": "today_action", "detail_json": "{}"}]
    m = hr.summarize_mind(events, days=7, wake_tokens=[12000, 20000, 30000], verdicts=signals, open_intentions=4,
                          now=datetime(2026, 9, 18, 12))
    assert m["wakes"] == 3 and m["wakes_per_day"] == 1.5
    assert m["per_day"]["2026-09-18"] == {"morning": 1, "messages": 1}
    assert m["left_alone_share"] == 0.33
    assert m["actions_by_tool"] == {"leave_note": 1, "remember": 1, "send_push_notification": 1}
    assert m["tokens_per_wake"]["p50"] == 20000
    assert m["verdicts"] == {"right_call": 1, "wrong_call": 1} and m["open_intentions"] == 4
    rep = hr.compose_report(days=7, turns=[], tool_rows=[], hits=[], observations=[], signals=signals, storage={},
                            events=events, wake_tokens=[1000], open_intentions=2)
    assert rep["mind"]["open_intentions"] == 2 and rep["mind"]["verdicts"]["wrong_call"] == 1


def test_compose_report_carries_the_timezone_config():
    """2026-09-24: settings.timezone vs the host zone, so a health check
    sees at once that every tick fires at the wrong hour."""
    cfg = {"timezone": "America/Los_Angeles", "host_timezone": "America/Chicago", "mismatch": True}
    rep = hr.compose_report(days=7, turns=[], tool_rows=[], hits=[], observations=[], signals=[], storage={},
                            config=cfg)
    assert rep["config"] == cfg
    assert hr.compose_report(days=7, turns=[], tool_rows=[], hits=[], observations=[], signals=[],
                             storage={})["config"] == {}


import pytest  # noqa: E402


@pytest.mark.integration
async def test_build_report_includes_the_timezone_config(clean_db, monkeypatch):
    from services import scheduler_service

    monkeypatch.setattr(scheduler_service, "host_timezone_name", lambda: "America/Los_Angeles")
    rep = await hr.build_report(days=1)
    cfg = rep["config"]
    assert set(cfg) >= {"timezone", "host_timezone", "mismatch"}
    assert cfg["host_timezone"] == "America/Los_Angeles"
    assert cfg["timezone"] == "America/Chicago" and cfg["mismatch"] is True
