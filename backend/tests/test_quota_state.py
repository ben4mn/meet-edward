"""The quota check the mind and the pulse gate on (2026-09-24).

``expression_service._quota_state()`` called ``gate.meter()``, but ``meter``
is a property (``llm_rate_gate.LLMRateGate.meter``). The ``TypeError`` was
swallowed and the check answered ``"ok"`` every time: the mind never
skipped on an exhausted week and the pulse's paced/exhausted floors never
applied. These tests use the real gate object — no mock of the check —
so the same class of bug cannot hide again.
"""

from __future__ import annotations

import time

import pytest

from services import expression_service as es
from services import llm_rate_gate
from services.llm_rate_gate import LLMRateGate, QuotaMeter, QuotaWindow

WEEK = 7 * 24 * 3600.0

pytestmark = pytest.mark.unit


def _gate_with(monkeypatch, meter):
    gate = LLMRateGate()
    gate.note_meter(meter)
    monkeypatch.setattr(llm_rate_gate, "_gate", gate)
    return gate


def test_limit_reached_is_exhausted(monkeypatch):
    now = time.time()
    _gate_with(monkeypatch, QuotaMeter(
        windows={"primary": QuotaWindow(used_percent=100.0, window_seconds=WEEK, reset_after_seconds=3 * 86400)},
        limit_reached=True, sampled_at=now,
    ))
    assert es._quota_state() == "exhausted"
    assert es.quota_state() == "exhausted"


def test_over_the_pace_line_is_paced(monkeypatch):
    now = time.time()
    # One day into the week (~14% elapsed, pace line ~29%) at 60% used.
    _gate_with(monkeypatch, QuotaMeter(
        windows={"primary": QuotaWindow(used_percent=60.0, window_seconds=WEEK, reset_after_seconds=6 * 86400)},
        sampled_at=now,
    ))
    assert es._quota_state() == "paced"


def test_under_the_pace_line_is_ok(monkeypatch):
    now = time.time()
    _gate_with(monkeypatch, QuotaMeter(
        windows={"primary": QuotaWindow(used_percent=5.0, window_seconds=WEEK, reset_after_seconds=6 * 86400)},
        sampled_at=now,
    ))
    assert es._quota_state() == "ok"


def test_an_exhausted_window_that_has_since_reset_is_not_exhausted(monkeypatch):
    # The 429 said "resets in an hour" two hours ago: the meter is stale,
    # not a reason to keep the mind asleep until the next sample arrives.
    now = time.time()
    _gate_with(monkeypatch, QuotaMeter(
        windows={"primary": QuotaWindow(used_percent=100.0, window_seconds=WEEK, reset_after_seconds=3600)},
        limit_reached=True, sampled_at=now - 7200,
    ))
    assert es._quota_state() == "ok"


def test_no_meter_yet_is_ok(monkeypatch):
    monkeypatch.setattr(llm_rate_gate, "_gate", LLMRateGate())
    assert es._quota_state() == "ok"


def test_a_broken_check_is_logged_once_an_hour(monkeypatch, capsys):
    def boom():
        raise RuntimeError("gate unavailable")

    monkeypatch.setattr(llm_rate_gate, "get_llm_rate_gate", boom)
    es._reset_quota_error_log()
    assert es._quota_state() == "ok"
    assert es._quota_state() == "ok"
    out = capsys.readouterr().out
    assert out.count("quota check failed") == 1, out
    assert "RuntimeError" in out
