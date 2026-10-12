"""GET /api/settings/models says what each kind of turn actually runs on
(review follow-up 2026-09-26): the Edward tab's Model panel showed only the
chat picker, while the mind, the pulse and chat's effort are set by env
knobs nobody could see. Each is resolved exactly the way the turn resolves
it — ``mind_service.route_for_wake`` for wakes and the pulse,
``llm_utils.chat_effort_for`` + the catalog for chat."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

KNOBS = ("EDWARD_MIND_MODEL", "EDWARD_MIND_REASONING_EFFORT", "EDWARD_MIND_EFFORT_MAP", "EDWARD_PULSE_MODEL",
         "EDWARD_PULSE_REASONING_EFFORT", "EDWARD_CHAT_REASONING_EFFORT")


@pytest.fixture
def payload(monkeypatch):
    import routers.settings as rs
    import services.mind_service as ms

    for k in KNOBS:
        monkeypatch.delenv(k, raising=False)

    async def settings():
        return SimpleNamespace(model="gpt-6-astra")

    monkeypatch.setattr(rs, "get_settings", settings)
    return lambda: asyncio.run(rs.get_available_models())


def test_the_routing_matches_what_a_wake_and_the_pulse_resolve(payload):
    import services.mind_service as ms
    from services import model_catalog

    out = payload()
    mind_model, mind_effort = ms.route_for_wake("self", "mind", "gpt-6-astra")
    pulse_model, pulse_effort = ms.route_for_wake("surfaces", "pulse", "gpt-6-astra")
    assert out["mind_model"] == mind_model
    assert out["mind_effort"] == (model_catalog.effort_for(mind_model, mind_effort) or "medium")
    assert out["pulse_model"] == pulse_model
    assert out["pulse_effort"] == (model_catalog.effort_for(pulse_model, pulse_effort) or "medium")
    assert out["chat_model"] == "gpt-6-astra"
    assert out["chat_effort"] == (model_catalog.effort_for("gpt-6-astra", None) or "medium")
    for reason in ("mention", "nightly", "speak"):
        _, eff = ms.route_for_wake(reason, "mind", "gpt-6-astra")
        assert out["mind_efforts"][reason] == (model_catalog.effort_for(mind_model, eff) or "medium")
    assert "surfaces" not in out["mind_efforts"]
    assert "messages" not in out["mind_efforts"], "a shim, not a wake"


def test_the_env_knobs_show_through(payload, monkeypatch):
    from services import model_catalog

    monkeypatch.setenv("EDWARD_MIND_EFFORT_MAP", "*=medium")
    monkeypatch.setenv("EDWARD_PULSE_REASONING_EFFORT", "minimal")
    monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
    out = payload()
    assert out["mind_effort"] == model_catalog.effort_for(out["mind_model"], "medium")
    assert out["pulse_effort"] == (model_catalog.effort_for(out["pulse_model"], "minimal") or "medium")
    assert out["chat_effort"] == model_catalog.effort_for("gpt-6-astra", "high")
