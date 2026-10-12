"""Switching GPT models must not break Edward — the Codex adapter's part.

* The request's ``reasoning.effort`` comes from the catalog for the model
  (it was hardcoded "medium" for every tier), and an unsupported request
  falls back to the model's default instead of a 400.
* A 400 "model is not supported" names the model and where to change it.
* A 429 that names a multi-day reset is the account's *weekly* cap, shared
  by every GPT model: it is reported as the hard cap (kind
  ``usage_limit_reached``), the error says switching models will not help,
  and the gate marks the meter exhausted. A short reset stays a plain 429.
* The alert text tells the truth: a cap is not a cooldown.
"""

from __future__ import annotations

import asyncio
import json
from typing import List

import pytest
from langchain_core.messages import HumanMessage

from services import codex_chat_model as ccm
from services import model_catalog as mc
from services.llm_rate_gate import LLMRateGate

pytestmark = pytest.mark.unit

_JWT_PAYLOAD = "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"
WEEK = 7 * 24 * 3600


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    p = tmp_path / "models_cache.json"
    p.write_text(json.dumps({"models": [
        {"slug": "gpt-6-astra", "display_name": "Astra", "default_reasoning_level": "medium",
         "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh")],
         "visibility": "list", "supported_in_api": True, "input_modalities": ["text", "image"]},
        {"slug": "gpt-5.6-luna", "display_name": "Luna", "default_reasoning_level": "low",
         "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high")],
         "visibility": "list", "supported_in_api": True, "input_modalities": ["text", "image"]},
    ]}))
    monkeypatch.setattr(mc, "CATALOG_PATH", str(p))
    mc.clear_cache()
    return p


@pytest.fixture
def fresh_gate(monkeypatch):
    gate = LLMRateGate()
    monkeypatch.setattr("services.llm_rate_gate._gate", gate)
    return gate


class _FakeStreamResponse:
    def __init__(self, status_code: int, sse_lines: List[str] = (), error_body: str = "", headers=None):
        self.status_code = status_code
        self._sse_lines = list(sse_lines)
        self._error_body = error_body
        self.headers = headers or {}

    async def aiter_bytes(self):
        if self._error_body:
            yield self._error_body.encode("utf-8")

    async def aiter_text(self):
        for line in self._sse_lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return None


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method, url, *, json, headers):
        self.calls.append({"json": json, "headers": headers})
        return _FakeStreamCM(self._responses.pop(0))


def _completed():
    return ('data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
            'data: {"type":"response.completed","response":{"model":"x","usage":{}}}\n\n'
            'data: [DONE]\n\n')


def _run(llm, monkeypatch, responses):
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(ccm.httpx, "AsyncClient", client)

    async def go():
        return [c async for c in llm._astream([HumanMessage(content="hi")])]

    return client, asyncio.run(go())


class TestReasoningEffort:
    def test_effort_defaults_to_the_catalog_value_for_the_model(self, catalog):
        _, body = ccm.ChatCodexResponses(model="gpt-5.6-luna", api_key=FAKE_TOKEN)._build_request([HumanMessage(content="x")])
        assert body["reasoning"]["effort"] == "low"
        _, body = ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)._build_request([HumanMessage(content="x")])
        assert body["reasoning"]["effort"] == "medium"

    def test_a_requested_effort_is_honoured_when_supported_else_the_default(self, catalog):
        _, body = ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN, reasoning_effort="xhigh")._build_request([HumanMessage(content="x")])
        assert body["reasoning"]["effort"] == "xhigh"
        _, body = ccm.ChatCodexResponses(model="gpt-5.6-luna", api_key=FAKE_TOKEN, reasoning_effort="xhigh")._build_request([HumanMessage(content="x")])
        assert body["reasoning"]["effort"] == "low"

    def test_unknown_model_keeps_the_old_medium(self, catalog):
        _, body = ccm.ChatCodexResponses(model="gpt-9-nova", api_key=FAKE_TOKEN)._build_request([HumanMessage(content="x")])
        assert body["reasoning"]["effort"] == "medium"

    def test_bind_tools_carries_the_effort(self, catalog):
        llm = ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN, reasoning_effort="high")
        assert llm.bind_tools([]).reasoning_effort == "high"

    def test_env_override_for_background_effort(self, catalog, monkeypatch):
        from services.llm_utils import build_background_llm
        monkeypatch.setenv("EDWARD_BACKGROUND_MODEL", "gpt-6-astra")
        monkeypatch.setenv("EDWARD_BACKGROUND_REASONING_EFFORT", "low")
        assert build_background_llm().reasoning_effort == "low"
        monkeypatch.delenv("EDWARD_BACKGROUND_REASONING_EFFORT")
        assert build_background_llm().reasoning_effort is None

    def test_env_override_for_chat_effort(self, catalog, monkeypatch):
        """Sol's catalog default is "low"; EDWARD_CHAT_REASONING_EFFORT is how
        Ellis buys Edward more thinking on the chat path (2026-09-20). Read per
        call; the background tiers are not touched."""
        from services.llm_utils import build_background_llm, build_chat_llm
        monkeypatch.delenv("EDWARD_BACKGROUND_REASONING_EFFORT", raising=False)
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert build_chat_llm("gpt-6-astra").reasoning_effort == "high"
        assert build_background_llm().reasoning_effort is None
        monkeypatch.delenv("EDWARD_CHAT_REASONING_EFFORT")
        assert build_chat_llm("gpt-6-astra").reasoning_effort is None

    def test_chat_effort_override_never_slows_a_spoken_turn(self, catalog, monkeypatch):
        """Voice turns come through the same builder on EDWARD_VOICE_MODEL;
        first-token latency is the whole point there."""
        from services.llm_utils import build_chat_llm
        monkeypatch.setenv("EDWARD_VOICE_MODEL", "gpt-5.6-luna")
        monkeypatch.setenv("EDWARD_CHAT_REASONING_EFFORT", "high")
        assert build_chat_llm("gpt-5.6-luna").reasoning_effort is None
        assert build_chat_llm("gpt-5.6-terra").reasoning_effort == "high"


class TestErrors:
    def test_model_not_supported_is_a_clear_error(self, catalog, fresh_gate, monkeypatch):
        llm = ccm.ChatCodexResponses(model="gpt-5.4", api_key=FAKE_TOKEN)
        body = '{"detail":"The \'gpt-5.4\' model is not supported when using Codex with a ChatGPT account."}'
        with pytest.raises(ValueError) as exc:
            _run(llm, monkeypatch, [_FakeStreamResponse(400, error_body=body)])
        msg = str(exc.value)
        assert "gpt-5.4" in msg and "not available" in msg and "Settings" in msg
        assert "gpt-6-astra" in msg  # the catalog's answer

    def test_weekly_cap_429_is_the_hard_cap_and_every_model_shares_it(self, catalog, fresh_gate, monkeypatch):
        reported = []
        monkeypatch.setattr(ccm, "_report_limit", lambda kind, model, cooldown: reported.append((kind, model, cooldown)))
        llm = ccm.ChatCodexResponses(model="gpt-5.6-luna", api_key=FAKE_TOKEN)
        body = json.dumps({"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached",
                                     "plan_type": "pro", "resets_at": 1789435731, "resets_in_seconds": 442_000}})
        headers = {"x-codex-primary-used-percent": "100", "x-codex-primary-window-minutes": "10080",
                   "x-codex-primary-reset-after-seconds": "442000"}
        with pytest.raises(ValueError) as exc:
            _run(llm, monkeypatch, [_FakeStreamResponse(429, error_body=body, headers=headers)])
        msg = str(exc.value)
        assert "weekly" in msg.lower() and "every GPT model" in msg and "5d" in msg
        assert reported and reported[0][0] == "usage_limit_reached"
        assert fresh_gate.meter is not None and fresh_gate.meter.limit_reached
        assert fresh_gate.background_decision()[0] is False

    def test_short_429_stays_a_rate_limit(self, catalog, fresh_gate, monkeypatch):
        reported = []
        monkeypatch.setattr(ccm, "_report_limit", lambda kind, model, cooldown: reported.append((kind, model, cooldown)))
        llm = ccm.ChatCodexResponses(model="gpt-5.6-luna", api_key=FAKE_TOKEN)
        body = json.dumps({"error": {"type": "usage_limit_reached", "resets_in_seconds": 90}})
        with pytest.raises(ValueError) as exc:
            _run(llm, monkeypatch, [_FakeStreamResponse(429, error_body=body)])
        assert "cooldown" in str(exc.value)
        assert reported[0][0] == "429"

    def test_a_200_feeds_the_meter(self, catalog, fresh_gate, monkeypatch):
        llm = ccm.ChatCodexResponses(model="gpt-5.6-luna", api_key=FAKE_TOKEN)
        headers = {"x-codex-plan-type": "pro", "x-codex-primary-used-percent": "23",
                   "x-codex-primary-window-minutes": "10080", "x-codex-primary-reset-after-seconds": "400000"}
        _run(llm, monkeypatch, [_FakeStreamResponse(200, sse_lines=[_completed()], headers=headers)])
        assert fresh_gate.meter.windows["primary"].used_percent == 23
        assert fresh_gate.meter.plan_type == "pro"


class TestAlertText:
    def test_hard_cap_text_does_not_suggest_switching_models(self):
        from services.llm_limit_alert import format_alert
        title, body = format_alert(kind="usage_limit_reached", model="gpt-5.6-luna", recent=3, cooldown_s=None,
                                   reset_after_s=442_000, used_percent=100)
        assert "cap" in title.lower()
        assert "switch" not in body.lower()
        assert "every GPT model" in body and "5d" in body

    def test_rate_limit_text_mentions_usage_when_known(self):
        from services.llm_limit_alert import format_alert
        _, body = format_alert(kind="429", model="gpt-6-astra", recent=1, cooldown_s=1800, used_percent=87)
        assert "87%" in body and "30 min" in body

    async def test_handle_limit_event_reads_the_meter_from_the_gate(self, fresh_gate, monkeypatch):
        import services.llm_limit_alert as alert
        from services.llm_rate_gate import QuotaMeter
        sent = []

        async def _fake_push(title, body, **kw):
            sent.append(body)

        monkeypatch.setattr(alert, "send_push_notification", _fake_push)
        alert._policy = alert.LimitAlertPolicy()
        fresh_gate.note_meter(QuotaMeter.from_headers({
            "x-codex-primary-used-percent": "100", "x-codex-primary-window-minutes": "10080",
            "x-codex-primary-reset-after-seconds": "200000"}, now=fresh_gate._wall_clock()))
        await alert.handle_limit_event(kind="usage_limit_reached", model="gpt-5.6-luna", cooldown_seconds=None)
        assert sent and "2d" in sent[0]
