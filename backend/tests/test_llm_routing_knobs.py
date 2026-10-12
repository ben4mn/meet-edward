"""Phase 2 routing knobs (docs/muse-and-beyond-plan-2026-09-24.md, Part E4).

Until now the mind ran on ``settings.model`` for everything and effort was
one environment variable for every chat-path turn, so there was no way to
say "mind on Astra/high, pulse on Terra/low". Every knob here resolves
through ``services.model_catalog``: an unknown model id is rewritten to the
recommended one, and an effort the model does not support falls back to
the model's default instead of a 400.
"""

from __future__ import annotations

import json

import pytest

from services import llm_utils
from services import model_catalog as mc

pytestmark = pytest.mark.unit

_ENV = (
    "EDWARD_CHAT_REASONING_EFFORT", "EDWARD_BACKGROUND_REASONING_EFFORT", "EDWARD_BACKGROUND_MODEL",
    "EDWARD_MIND_MODEL", "EDWARD_MIND_REASONING_EFFORT", "EDWARD_MIND_EFFORT_MAP",
    "EDWARD_PULSE_MODEL", "EDWARD_PULSE_REASONING_EFFORT",
)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EDWARD_VOICE_MODEL", "gpt-5.6-luna")
    p = tmp_path / "models_cache.json"
    levels = lambda *e: [{"effort": x} for x in e]  # noqa: E731
    p.write_text(json.dumps({"models": [
        {"slug": "gpt-6-astra", "display_name": "Astra", "default_reasoning_level": "medium",
         "supported_reasoning_levels": levels("low", "medium", "high", "xhigh"), "visibility": "list"},
        {"slug": "gpt-5.6-terra", "display_name": "Terra", "default_reasoning_level": "medium",
         "supported_reasoning_levels": levels("low", "medium", "high", "xhigh"), "visibility": "list"},
        {"slug": "gpt-5.6-luna", "display_name": "Luna", "default_reasoning_level": "low",
         "supported_reasoning_levels": levels("low", "medium", "high"), "visibility": "list"},
        {"slug": "gpt-5.5", "display_name": "5.5", "default_reasoning_level": "medium",
         "supported_reasoning_levels": levels("low", "medium", "high"), "visibility": "list"},
    ]}))
    monkeypatch.setattr(mc, "CATALOG_PATH", str(p))
    mc.clear_cache()
    return p


class TestChatEffort:
    def test_explicit_effort_wins_over_the_env(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert llm_utils.build_chat_llm("gpt-6-astra", reasoning_effort="xhigh").reasoning_effort == "xhigh"

    def test_env_applies_when_nothing_explicit(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert llm_utils.build_chat_llm("gpt-6-astra", turn_source="mind").reasoning_effort == "high"

    def test_voice_exemption_is_keyed_on_the_turn_source(self, catalog, monkeypatch):
        """A spoken turn keeps the model default whichever model it runs on."""
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert llm_utils.build_chat_llm("gpt-6-astra", turn_source="voice").reasoning_effort is None
        # Chat on the voice model is not a spoken turn.
        assert llm_utils.build_chat_llm("gpt-5.6-luna", turn_source="chat").reasoning_effort == "high"

    def test_the_hubs_voice_is_a_spoken_turn_too(self, catalog, monkeypatch):
        """``hub_voice`` (2026-10-11) is the household hub's live voice:
        not Ellis, but spoken, so first-token latency is still the point."""
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert llm_utils.build_chat_llm("gpt-6-astra", turn_source="hub_voice").reasoning_effort is None

    def test_model_id_fallback_when_the_source_is_unknown(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert llm_utils.build_chat_llm("gpt-5.6-luna").reasoning_effort is None

    def test_an_explicit_effort_reaches_a_voice_turn(self, catalog):
        assert llm_utils.build_chat_llm("gpt-5.6-luna", reasoning_effort="medium",
                                        turn_source="voice").reasoning_effort == "medium"

    def test_unsupported_effort_falls_back_to_the_model_default(self, catalog):
        assert llm_utils.build_chat_llm("gpt-5.5", reasoning_effort="xhigh").reasoning_effort == "medium"


class TestBackgroundEffort:
    def test_explicit_wins_over_env(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_BACKGROUND_MODEL", "gpt-6-astra")
        monkeypatch.setenv("EDWARD_BACKGROUND_REASONING_EFFORT", "low")
        assert llm_utils.build_background_llm(effort="high").reasoning_effort == "high"
        assert llm_utils.build_background_llm().reasoning_effort == "low"

    def test_unsupported_falls_back(self, catalog):
        assert llm_utils.build_background_llm(model="gpt-5.5", effort="xhigh").reasoning_effort == "medium"


class TestMindKnobs:
    def test_mind_model_defaults_to_settings(self, catalog):
        assert llm_utils.mind_model_for("gpt-6-astra") == "gpt-6-astra"

    def test_mind_model_env(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_MIND_MODEL", "gpt-5.6-terra")
        assert llm_utils.mind_model_for("gpt-6-astra") == "gpt-5.6-terra"

    def test_mind_model_is_validated(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_MIND_MODEL", "gpt-5.4")
        assert llm_utils.mind_model_for("gpt-6-astra") == "gpt-6-astra"

    def test_built_in_efforts_per_reason(self, catalog):
        assert llm_utils.mind_effort_for("reflect") == "xhigh"
        assert llm_utils.mind_effort_for("mention") == "medium"
        assert llm_utils.mind_effort_for("messages") == "medium"
        assert llm_utils.mind_effort_for("morning") == "high"
        assert llm_utils.mind_effort_for("self") == "high"

    def test_default_effort_env(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_MIND_REASONING_EFFORT", "medium")
        assert llm_utils.mind_effort_for("evening") == "medium"
        assert llm_utils.mind_effort_for("reflect") == "xhigh"

    def test_effort_map_env(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_MIND_EFFORT_MAP", "reflect=high, mention=low ,evening=xhigh,bogus")
        assert llm_utils.mind_effort_for("reflect") == "high"
        assert llm_utils.mind_effort_for("mention") == "low"
        assert llm_utils.mind_effort_for("evening") == "xhigh"
        assert llm_utils.mind_effort_for("messages") == "medium"

    def test_star_in_the_map_replaces_the_built_ins(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_MIND_EFFORT_MAP", "*=medium,reflect=high")
        assert llm_utils.mind_effort_for("mention") == "medium"
        assert llm_utils.mind_effort_for("morning") == "medium"
        assert llm_utils.mind_effort_for("reflect") == "high"

    def test_surfaces_wake_never_defaults_to_high(self, catalog):
        """The pulse runs through ``wake("surfaces")`` 15–25 times a day; a
        generic wiring must not put it on the mind's high effort."""
        assert llm_utils.mind_effort_for("surfaces") == llm_utils.pulse_effort() == "low"

    def test_efforts_resolve_through_the_catalog_when_a_model_is_named(self, catalog):
        assert llm_utils.mind_effort_for("reflect", model="gpt-5.5") == "medium"
        assert llm_utils.mind_effort_for("reflect", model="gpt-6-astra") == "xhigh"


class TestPulseKnobs:
    def test_pulse_model_defaults_to_terra_when_listed(self, catalog):
        assert llm_utils.pulse_model_for("gpt-6-astra") == "gpt-5.6-terra"

    def test_pulse_model_falls_back_to_settings_without_terra(self, tmp_path, monkeypatch, catalog):
        p = tmp_path / "only_astra.json"
        p.write_text(json.dumps({"models": [
            {"slug": "gpt-6-astra", "display_name": "Astra", "visibility": "list"},
        ]}))
        monkeypatch.setattr(mc, "CATALOG_PATH", str(p))
        mc.clear_cache()
        assert llm_utils.pulse_model_for("gpt-6-astra") == "gpt-6-astra"

    def test_pulse_model_env(self, catalog, monkeypatch):
        monkeypatch.setenv("EDWARD_PULSE_MODEL", "gpt-5.6-luna")
        assert llm_utils.pulse_model_for("gpt-6-astra") == "gpt-5.6-luna"

    def test_pulse_effort(self, catalog, monkeypatch):
        assert llm_utils.pulse_effort() == "low"
        monkeypatch.setenv("EDWARD_PULSE_REASONING_EFFORT", "medium")
        assert llm_utils.pulse_effort() == "medium"
        monkeypatch.setenv("EDWARD_PULSE_REASONING_EFFORT", "xhigh")
        assert llm_utils.pulse_effort(model="gpt-5.5") == "medium"


class TestProvider:
    @pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-5.6-luna", "o3", "o4-mini"])
    def test_gpt_and_o_series_are_openai(self, model):
        assert llm_utils.provider_for(model) == "openai"

    def test_a_stale_anthropic_settings_row_does_not_win_over_a_gpt_model(self):
        """The migration defaulted old settings rows to 'anthropic'; the
        OpenAI-only branches (401 refresh, forced tool choice) must still run."""
        assert llm_utils.provider_for("gpt-6-astra", "anthropic") == "openai"

    def test_claude_ids_are_rewritten_to_gpt_so_the_built_llm_is_openai(self):
        assert llm_utils.provider_for("claude-sonnet-4-5", "anthropic") == "openai"

    def test_no_model(self):
        assert llm_utils.provider_for(None) == "openai"


# ------------------------------------------------------------ depth follows quota --

def _allowed(headroom=25.0):
    from services.llm_rate_gate import SurplusDecision

    return SurplusDecision(True, "ok", used=41.0, hours_to_reset=30.0, reserve=5.0, headroom=headroom,
                           target_rate=headroom / 30.0, code="ok")


def _refused():
    from services.llm_rate_gate import SurplusDecision

    return SurplusDecision(False, "no headroom", used=88.0, headroom=-2.0, code="no_headroom")


class TestDepthFollowsQuota:
    """2026-09-25 (Ellis: "tons of compute power… lean into that"): while the
    week has spare quota — surplus allowed with ≥ 20 % headroom — the mind
    and the pulse think one level harder, capped at what the model
    supports. Never a spoken turn; an explicit env setting still wins."""

    def test_one_level_up_with_headroom(self, catalog):
        up = llm_utils.effort_with_headroom
        assert up("low", _allowed(), model="gpt-6-astra") == "medium"
        assert up("medium", _allowed(), model="gpt-6-astra") == "high"
        assert up("high", _allowed(), model="gpt-6-astra") == "xhigh"
        assert up("xhigh", _allowed(), model="gpt-6-astra") == "xhigh"

    def test_capped_at_what_the_catalog_supports(self, catalog):
        up = llm_utils.effort_with_headroom
        assert up("high", _allowed(), model="gpt-5.5") == "high"
        assert up("medium", _allowed(), model="gpt-5.6-luna") == "high"
        assert up("high", _allowed(), model="gpt-5.6-luna") == "high"

    def test_only_with_twenty_percent_headroom_and_surplus_allowed(self, catalog):
        up = llm_utils.effort_with_headroom
        assert up("medium", _allowed(19.9), model="gpt-6-astra") == "medium"
        assert up("medium", _allowed(20.0), model="gpt-6-astra") == "high"
        assert up("medium", _refused(), model="gpt-6-astra") == "medium"
        assert up("medium", None, model="gpt-6-astra") == "medium"

    def test_never_for_voice(self, catalog):
        assert llm_utils.effort_with_headroom("low", _allowed(), model="gpt-6-astra", turn_source="voice") == "low"
        assert llm_utils.effort_with_headroom("low", _allowed(), model="gpt-6-astra",
                                              turn_source="hub_voice") == "low"

    def test_no_base_means_the_models_default(self, catalog):
        up = llm_utils.effort_with_headroom
        assert up(None, _allowed(), model="gpt-6-astra") == "high", "astra's default is medium"
        assert up(None, _allowed()) is None, "no model, no default to raise"

    def test_the_mind_and_the_pulse_no_longer_route_deeper_with_spare_quota(self, catalog):
        """The spare-quota raise went with the surplus queue (2026-10-11):
        ``route_for_wake`` takes no surplus decision any more."""
        from services import mind_service as ms

        assert ms.route_for_wake("self", "mind", "gpt-6-astra") == ("gpt-6-astra", "high")
        assert ms.route_for_wake("mention", "mind", "gpt-6-astra")[1] == "medium"
        assert ms.route_for_wake("surfaces", "pulse", "gpt-6-astra")[1] == "low"

    def test_the_env_settings_still_win(self, catalog, monkeypatch):
        from services import mind_service as ms

        monkeypatch.setenv("EDWARD_MIND_EFFORT_MAP", "nightly=medium")
        assert ms.route_for_wake("nightly", "mind", "gpt-6-astra")[1] == "medium"
        assert ms.route_for_wake("self", "mind", "gpt-6-astra")[1] == "high"
        monkeypatch.setenv("EDWARD_MIND_EFFORT_MAP", "*=low")
        assert ms.route_for_wake("self", "mind", "gpt-6-astra")[1] == "low"
        monkeypatch.delenv("EDWARD_MIND_EFFORT_MAP")
        monkeypatch.setenv("EDWARD_MIND_REASONING_EFFORT", "medium")
        assert ms.route_for_wake("self", "mind", "gpt-6-astra")[1] == "medium"
        monkeypatch.setenv("EDWARD_PULSE_REASONING_EFFORT", "medium")
        assert ms.route_for_wake("surfaces", "pulse", "gpt-6-astra")[1] == "medium"


