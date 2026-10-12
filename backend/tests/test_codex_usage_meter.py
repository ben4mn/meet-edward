"""The settings card shows the real Codex meter, not an inference.

``GET /api/settings/codex-usage`` (formerly ``/api/evolution/codex-usage``)
used to infer throttling from the last 'try again at X' string in
evolution_history because "no real quota API exists". One does: every Codex
response carries x-codex-* usage headers and ``GET /backend-api/wham/usage``
returns the same meter. Evolution and its history table are gone
(2026-09-12); the payload is the meter, the pace decision and the plan.
"""

from __future__ import annotations

import pytest

from services import codex_usage_service as cus
from services.llm_rate_gate import LLMRateGate, QuotaMeter

pytestmark = pytest.mark.unit

WEEK = 7 * 24 * 3600


@pytest.fixture
def gate(monkeypatch):
    g = LLMRateGate()
    monkeypatch.setattr("services.llm_rate_gate._gate", g)
    return g


def _headers(used, reset=WEEK // 2):
    return {"x-codex-plan-type": "pro", "x-codex-primary-used-percent": str(used),
            "x-codex-primary-window-minutes": "10080", "x-codex-primary-reset-after-seconds": str(reset)}


async def test_status_carries_the_meter_and_the_pace_decision(gate, monkeypatch):
    gate.note_usage_headers(_headers(22))

    async def no_refresh():
        raise AssertionError("a fresh meter must not be re-fetched")

    monkeypatch.setattr(cus, "refresh_gate_meter", no_refresh)
    out = await cus.get_usage_status()
    assert out["status"] == "available" and out["plan_type"] == "pro"
    assert out["meter"]["windows"]["primary"]["used_percent"] == 22
    assert out["meter"]["windows"]["primary"]["window_minutes"] == 10080
    assert out["meter"]["windows"]["primary"]["reset_at"]          # ISO string
    assert out["background_allowed"] is True and "22%" in out["background_reason"]
    for gone in ("throttled", "throttled_until", "window_hours", "recent_cycles", "last_error_message"):
        assert gone not in out                                      # evolution's signals are gone


async def test_an_exhausted_meter_is_throttled_until_its_reset(gate, monkeypatch):
    gate.note_usage_headers(_headers(100, reset=3600))
    out = await cus.get_usage_status()
    assert out["status"] == "throttled"
    assert out["meter"]["limit_reached"] is True
    assert out["meter"]["windows"]["primary"]["reset_at"]
    assert out["background_allowed"] is False


async def test_a_missing_meter_is_fetched_once(gate, monkeypatch):
    asked = []

    async def fake_refresh():
        asked.append(1)
        gate.note_usage_headers(_headers(5))
        return True

    monkeypatch.setattr(cus, "refresh_gate_meter", fake_refresh)
    out = await cus.get_usage_status()
    assert asked == [1]
    assert out["meter"]["windows"]["primary"]["used_percent"] == 5


async def test_no_meter_at_all_is_still_a_valid_payload(gate, monkeypatch):
    async def fake_refresh():
        return False

    monkeypatch.setattr(cus, "refresh_gate_meter", fake_refresh)
    out = await cus.get_usage_status()
    assert out["meter"]["windows"] == {} and out["plan_type"] is None
    assert out["status"] == "available"


class TestFetch:
    async def test_fetch_uses_the_oauth_token_and_account(self, monkeypatch):
        calls = []

        class _Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {"plan_type": "pro", "rate_limit": {"primary_window": {
                    "used_percent": 22, "limit_window_seconds": WEEK, "reset_after_seconds": 1000}}}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers=None):
                calls.append((url, headers))
                return _Resp()

        class _Mgr:
            _auth_source = "codex_oauth"
            _account_id = "acct_1"

            def get_api_key(self):
                return "tok"

        monkeypatch.setattr(cus.httpx, "AsyncClient", _Client)
        monkeypatch.setattr("services.codex_auth_service.get_codex_auth_manager", lambda: _Mgr())
        payload = await cus.fetch_live_usage()
        assert payload["plan_type"] == "pro"
        url, headers = calls[0]
        assert url == cus.CODEX_USAGE_URL
        assert headers["Authorization"] == "Bearer tok" and headers["ChatGPT-Account-Id"] == "acct_1"

    async def test_fetch_is_none_on_an_api_key_deployment(self, monkeypatch):
        class _Mgr:
            _auth_source = "api_key"

            def get_api_key(self):
                return "sk-x"

        monkeypatch.setattr("services.codex_auth_service.get_codex_auth_manager", lambda: _Mgr())
        assert await cus.fetch_live_usage() is None

    async def test_refresh_feeds_the_gate(self, gate, monkeypatch):
        async def fake_fetch(timeout=6.0):
            return {"plan_type": "pro", "rate_limit": {"primary_window": {
                "used_percent": 41, "limit_window_seconds": WEEK, "reset_after_seconds": 1000}}}

        monkeypatch.setattr(cus, "fetch_live_usage", fake_fetch)
        assert await cus.refresh_gate_meter() is True
        assert gate.meter.windows["primary"].used_percent == 41


@pytest.mark.integration
async def test_the_card_reads_the_meter_from_the_settings_route(clean_db, gate, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    gate.note_usage_headers(_headers(37))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.get("/api/settings/codex-usage")
        assert r.status_code == 200, r.text
        assert r.json()["meter"]["windows"]["primary"]["used_percent"] == 37
        r = await client.get("/api/evolution/codex-usage")
        assert r.status_code == 404
