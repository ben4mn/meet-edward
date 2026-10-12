"""The Work room's usage card after the surplus queue (2026-10-11).

``GET /api/settings/codex-usage`` asked ``surplus_service`` for the queue's
state and the top spenders, with an import outside any ``try``: once the
queue was deleted, the card would 500 on the Codex provider — AREA 4's
rollback path. The card keeps the meter, ``provider`` and the ``claude``
block, and carries no surplus block on either provider.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def gate(monkeypatch):
    from services.llm_rate_gate import LLMRateGate

    g = LLMRateGate()
    monkeypatch.setattr("services.llm_rate_gate._gate", g)
    return g


@pytest.fixture
def fake_block(monkeypatch):
    from services import llm_spend as ls

    async def _block(days=None):
        return {"provider": ls.provider(), "active": ls.is_anthropic(), "spent_today_usd": 0.5, "cap_usd": 10.0}

    monkeypatch.setattr(ls, "claude_block", _block)


async def test_the_card_under_codex_needs_no_surplus_queue(gate, fake_block, monkeypatch):
    from services import codex_usage_service as cus

    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "codex")  # unset means anthropic
    gate.note_usage_headers({"x-codex-plan-type": "pro", "x-codex-primary-used-percent": "22",
                             "x-codex-primary-window-minutes": "10080",
                             "x-codex-primary-reset-after-seconds": "300000"})
    out = await cus.get_usage_status(with_surplus=True)
    assert out["provider"] == "codex"
    assert out["meter"]["windows"]["primary"]["used_percent"] == 22
    assert out["claude"]["active"] is False
    assert "surplus" not in out and "spend_top" not in out
    assert "services.surplus_service" not in sys.modules


async def test_the_card_under_anthropic_says_nothing_of_a_surplus_queue(gate, fake_block, monkeypatch):
    from services import codex_usage_service as cus

    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "anthropic")

    async def _no_meter():
        raise AssertionError("no Codex meter is fetched under pay-per-token")

    monkeypatch.setattr(cus, "refresh_gate_meter", _no_meter)
    out = await cus.get_usage_status(with_surplus=True)
    assert out["provider"] == "anthropic"
    assert "surplus" not in out and "spend_top" not in out
