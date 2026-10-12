"""The Settings → Model picker must offer the current Codex catalog.

Codex OAuth serves whatever the ChatGPT subscription's catalog has; the
picker is a static list in ``routers/settings.py`` that fell behind
(``gpt-5-mini`` / ``gpt-4.1-mini`` are not in the catalog at all, and
GPT-6 Astra is). Edward passes ``settings.model`` straight through to the
Codex Responses endpoint, so listing a model is all it takes to route
through it.
"""

import asyncio

from routers.settings import get_available_models


def _openai_ids():
    payload = asyncio.run(get_available_models())
    return [m["id"] for m in payload["providers"]["openai"]["models"]]


def test_astra_is_offered_and_recommended_first():
    ids = _openai_ids()
    assert "gpt-6-astra" in ids
    assert ids[0] == "gpt-6-astra"


def test_current_codex_catalog_only():
    """Every id offered must be one the Codex endpoint actually serves —
    the catalog the CLI caches, not a list someone typed."""
    from services import model_catalog
    catalog = {m.slug for m in model_catalog.load_catalog()}
    assert set(_openai_ids()) <= catalog
    assert "gpt-5.4" not in _openai_ids()  # retired 2026-09-04


def test_flat_list_matches_openai_provider_list():
    payload = asyncio.run(get_available_models())
    flat_openai = [m["id"] for m in payload["models"] if m["id"].startswith("gpt-")]
    assert flat_openai == _openai_ids()
