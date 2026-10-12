"""The mind and the pulse think on their own model and effort (plan Phase 2,
2026-09-24).

Before this every wake ran on ``settings.model`` at the one chat effort, so
the surfaces pulse (15–25 turns a day) cost what a Sunday reflection cost,
and a stale ``settings.provider`` could switch off the OpenAI-only branches
of the tool loop. ``route_for_wake`` is the single decision; ``wake`` passes
it through with the generic tool nudge off.
"""
from __future__ import annotations

import pytest

from services import expression_service as es
from services import mind_service as ms
from tests.test_expression_service import _S, _const, _signals


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "EDWARD_MIND_MODEL", "EDWARD_MIND_REASONING_EFFORT", "EDWARD_MIND_EFFORT_MAP",
        "EDWARD_PULSE_MODEL", "EDWARD_PULSE_REASONING_EFFORT", "EDWARD_CHAT_REASONING_EFFORT",
    ):
        monkeypatch.delenv(var, raising=False)


def test_mind_wakes_default_to_the_settings_model_at_high():
    model, effort = ms.route_for_wake("morning", "mind", "gpt-6-astra")
    assert model == "gpt-6-astra"
    assert effort == "high"


def test_reflect_thinks_hardest_and_a_mention_answers_quickly():
    assert ms.route_for_wake("reflect", "mind", "gpt-6-astra")[1] == "xhigh"
    assert ms.route_for_wake("mention", "mind", "gpt-6-astra")[1] == "medium"


def test_the_pulse_runs_on_the_pulse_knobs(monkeypatch):
    monkeypatch.setenv("EDWARD_PULSE_MODEL", "gpt-5.6-terra")
    model, effort = ms.route_for_wake("surfaces", "pulse", "gpt-6-astra")
    assert model == "gpt-5.6-terra"
    assert effort == "low"


def test_env_knobs_move_the_mind(monkeypatch):
    monkeypatch.setenv("EDWARD_MIND_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("EDWARD_MIND_REASONING_EFFORT", "medium")
    model, effort = ms.route_for_wake("evening", "mind", "gpt-6-astra")
    assert model == "gpt-5.6-terra"
    assert effort == "medium"


async def test_a_surfaces_wake_passes_its_route_and_never_nudges(monkeypatch):
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Left it."

    es.reset_pulse_state()
    monkeypatch.setattr(es, "collect_signals", _const(_signals()))
    monkeypatch.setattr(es, "_ensure_conversation", _const(None))
    monkeypatch.setattr(es, "recent_decisions", _const([]))
    monkeypatch.setattr(es, "_quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setenv("EDWARD_PULSE_MODEL", "gpt-5.6-terra")

    await es.run_expression_pulse("manual")
    kw = calls[0]
    assert kw["model"] == "gpt-5.6-terra"
    assert kw["reasoning_effort"] == "low"
    assert kw["nudge_for_tools"] is False
    assert "provider" not in kw, "the provider is derived from the model id, never the settings row"
