"""The Codex model catalog is the source of truth for which GPT models exist.

Why (2026-09-11, Ellis): switching between GPT models kept breaking Edward.
The picker was a static list that fell behind the catalog (it still offered
the retired gpt-5.4 and not gpt-5.6-sol), nothing validated a saved id, and
a bad id failed every turn with HTTP 400 until someone noticed. The Codex CLI
keeps the account's live catalog in ``~/.codex/models_cache.json``; Edward
reads that file instead of guessing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from services import model_catalog as mc

pytestmark = pytest.mark.unit


def _write_catalog(path: Path, models=None) -> Path:
    models = models if models is not None else [
        {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "description": "Top model.",
         "default_reasoning_level": "medium",
         "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh")],
         "visibility": "list", "supported_in_api": True, "input_modalities": ["text", "image"],
         "context_window": 272000, "priority": 1},
        {"slug": "gpt-reserve", "display_name": "GPT-Reserve", "visibility": "hide",
         "supported_in_api": True, "input_modalities": ["text", "image"], "priority": 2},
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "description": "Fast and affordable.",
         "default_reasoning_level": "low",
         "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high")],
         "visibility": "list", "supported_in_api": True, "input_modalities": ["text", "image"],
         "context_window": 272000, "priority": 8},
        {"slug": "gpt-5.3-codex-spark", "display_name": "GPT-5.3-Codex-Spark",
         "visibility": "list", "supported_in_api": True, "input_modalities": ["text"],
         "context_window": 128000, "priority": 9},
    ]
    path.write_text(json.dumps({"fetched_at": "2026-09-04T00:00:00Z", "models": models}))
    return path


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    p = _write_catalog(tmp_path / "models_cache.json")
    monkeypatch.setattr(mc, "CATALOG_PATH", str(p))
    mc.clear_cache()
    return p


@pytest.fixture
def no_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "CATALOG_PATH", str(tmp_path / "missing.json"))
    mc.clear_cache()


class TestLoading:
    def test_listed_models_come_in_catalog_order_and_hide_hidden_ones(self, catalog):
        slugs = [m.slug for m in mc.listed_models()]
        assert slugs == ["gpt-6-astra", "gpt-5.6-luna", "gpt-5.3-codex-spark"]
        assert mc.catalog_is_live() is True

    def test_model_facts(self, catalog):
        luna = mc.get_model("gpt-5.6-luna")
        assert luna.display_name == "GPT-5.6-Luna"
        assert luna.supports_images is True
        assert luna.default_effort == "low"
        assert luna.efforts == ("low", "medium", "high")
        assert mc.supports_images("gpt-5.3-codex-spark") is False
        assert mc.is_known("gpt-reserve") is True      # hidden but real
        assert mc.is_known("gpt-5.4") is False

    def test_reloads_when_the_file_changes(self, catalog):
        assert mc.is_known("gpt-5.7-nova") is False
        _write_catalog(catalog, [
            {"slug": "gpt-5.7-nova", "display_name": "Nova", "visibility": "list",
             "supported_in_api": True, "input_modalities": ["text", "image"]},
        ])
        import os
        os.utime(catalog, (2_000_000_000, 2_000_000_000))
        assert mc.is_known("gpt-5.7-nova") is True

    def test_missing_file_falls_back_to_the_static_list(self, no_catalog):
        assert mc.catalog_is_live() is False
        slugs = [m.slug for m in mc.listed_models()]
        assert slugs[0] == "gpt-6-astra" and "gpt-5.6-luna" in slugs

    def test_corrupt_file_falls_back_too(self, tmp_path, monkeypatch):
        p = tmp_path / "models_cache.json"
        p.write_text("{not json")
        monkeypatch.setattr(mc, "CATALOG_PATH", str(p))
        mc.clear_cache()
        assert mc.catalog_is_live() is False
        assert mc.listed_models()


class TestResolution:
    def test_known_model_passes_through(self, catalog):
        assert mc.resolve_chat_model("gpt-5.6-luna") == ("gpt-5.6-luna", None)

    def test_retired_model_is_rewritten_to_the_recommended_one_with_a_reason(self, catalog):
        model, reason = mc.resolve_chat_model("gpt-5.4")
        assert model == "gpt-6-astra"
        assert "gpt-5.4" in reason and "catalog" in reason

    def test_anthropic_and_empty_ids_go_to_the_recommended_model(self, catalog):
        assert mc.resolve_chat_model("claude-sonnet-4-6")[0] == "gpt-6-astra"
        assert mc.resolve_chat_model("")[0] == "gpt-6-astra"
        assert mc.resolve_chat_model(None)[0] == "gpt-6-astra"

    def test_recommended_is_first_listed_when_astra_is_gone(self, catalog):
        _write_catalog(catalog, [
            {"slug": "gpt-5.6-luna", "display_name": "Luna", "visibility": "list",
             "supported_in_api": True, "input_modalities": ["text", "image"]},
        ])
        mc.clear_cache()
        assert mc.recommended_model() == "gpt-5.6-luna"
        assert mc.resolve_chat_model("gpt-6-astra")[0] == "gpt-5.6-luna"

    def test_without_a_live_catalog_any_gpt_id_is_trusted(self, no_catalog):
        assert mc.resolve_chat_model("gpt-9-future") == ("gpt-9-future", None)
        assert mc.resolve_chat_model("claude-x")[0] == "gpt-6-astra"

    def test_validate_choice_names_the_catalog(self, catalog):
        assert mc.validate_model_choice("gpt-5.6-luna") is None
        problem = mc.validate_model_choice("gpt-5.4")
        assert "gpt-5.4" in problem and "gpt-6-astra" in problem and "gpt-5.6-luna" in problem
        assert "Pick one of" in problem and "using" not in problem   # a refusal, not a rewrite

    def test_validate_choice_is_lenient_without_a_live_catalog(self, no_catalog):
        assert mc.validate_model_choice("gpt-9-future") is None
        assert mc.validate_model_choice("claude-x") is not None

    def test_vision_model_prefers_the_asked_model_when_it_can_see(self, catalog):
        assert mc.vision_model("gpt-5.6-luna") == "gpt-5.6-luna"
        assert mc.vision_model("gpt-5.3-codex-spark") == "gpt-6-astra"
        assert mc.vision_model("gpt-5.4") == "gpt-6-astra"

    def test_default_effort_only_when_supported(self, catalog):
        assert mc.default_effort("gpt-5.6-luna") == "low"
        assert mc.default_effort("gpt-5.3-codex-spark") is None
        assert mc.effort_for("gpt-5.6-luna", "xhigh") == "low"      # unsupported → default
        assert mc.effort_for("gpt-6-astra", "xhigh") == "xhigh"
        assert mc.effort_for("gpt-unknown", "high") == "high"       # unknown model: trust the caller


class TestPickerAndSave:
    def test_picker_offers_exactly_the_listed_catalog(self, catalog):
        from routers.settings import get_available_models
        payload = asyncio.run(get_available_models())
        openai = payload["providers"]["openai"]["models"]
        assert [m["id"] for m in openai] == ["gpt-6-astra", "gpt-5.6-luna", "gpt-5.3-codex-spark"]
        assert openai[0]["name"].endswith("(Recommended)")
        assert openai[1]["supports_images"] is True and openai[2]["supports_images"] is False
        assert payload["catalog"]["live"] is True
        assert payload["background_model"]  # the tier Edward's enrichment runs on is visible

    def test_anthropic_is_not_offered(self, catalog):
        """Policy: Anthropic is a disabled backup. Offering it invites a silent
        rewrite to the chat default."""
        from routers.settings import get_available_models
        payload = asyncio.run(get_available_models())
        assert payload["providers"]["anthropic"]["available"] is False
        assert payload["providers"]["anthropic"]["models"] == []
        assert all(m["id"].startswith("gpt-") for m in payload["models"])

    def test_saving_an_unknown_model_is_refused_before_it_can_break_a_turn(self, catalog):
        from fastapi import HTTPException
        from models import SettingsUpdate
        from routers.settings import save_settings
        with pytest.raises(HTTPException) as exc:
            asyncio.run(save_settings(SettingsUpdate(model="gpt-5.4")))
        assert exc.value.status_code == 400
        assert "gpt-5.4" in exc.value.detail and "gpt-6-astra" in exc.value.detail


class TestBuilders:
    def test_build_chat_llm_resolves_through_the_catalog(self, catalog, caplog):
        from services.llm_utils import build_chat_llm
        assert build_chat_llm("gpt-5.6-luna").model == "gpt-5.6-luna"
        with caplog.at_level("WARNING"):
            assert build_chat_llm("gpt-5.4").model == "gpt-6-astra"
        assert any("gpt-5.4" in r.message for r in caplog.records)

    def test_background_model_falls_back_when_not_in_the_catalog(self, catalog, monkeypatch):
        from services import llm_utils
        monkeypatch.setenv("EDWARD_BACKGROUND_MODEL", "gpt-5.4")
        assert llm_utils.background_model() == "gpt-6-astra"
        monkeypatch.setenv("EDWARD_BACKGROUND_MODEL", "gpt-5.6-luna")
        assert llm_utils.background_model() == "gpt-5.6-luna"
        assert llm_utils.build_background_llm().model == "gpt-5.6-luna"

    def test_vision_calls_get_a_model_that_can_see(self, catalog, monkeypatch):
        from services import llm_utils
        monkeypatch.setenv("EDWARD_BACKGROUND_MODEL", "gpt-5.3-codex-spark")
        assert llm_utils.build_background_llm().model == "gpt-5.3-codex-spark"
        assert llm_utils.build_background_llm(vision=True).model == "gpt-6-astra"
