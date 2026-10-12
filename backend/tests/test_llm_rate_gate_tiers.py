"""Rate-gate priority tiers against the real meter.

``background`` is the only tier the gate may refuse. ``triage`` (heartbeat
classification, send review, the briefing narrative) and ``interactive``
(chat, replies, a look at a photo Ellis asked about) are never deferred: they
wait a bounded few seconds through a 429 cooldown and then go. Bulk
enrichment must never starve message triage (543 deferrals in one day did,
2026-07-07), and a spent meter must never silence a reply.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from services.llm_rate_gate import LLMRateGate, LLMRateLimitDeferred

pytestmark = pytest.mark.unit

WEEK = 7 * 24 * 3600


def make_429_body(resets_in_seconds=32):
    return json.dumps({"error": {"type": "usage_limit_reached", "resets_in_seconds": resets_in_seconds}})


def headers(used, reset=WEEK // 2):
    return {
        "x-codex-primary-used-percent": str(used),
        "x-codex-primary-window-minutes": "10080",
        "x-codex-primary-reset-after-seconds": str(reset),
    }


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_gate():
    clock = FakeClock()
    return LLMRateGate(clock=clock.time, sleep=clock.sleep, wall_clock=clock.time), clock


async def test_background_defers_past_the_ceiling():
    gate, _ = make_gate()
    gate.note_usage_headers(headers(used=91, reset=100))
    with pytest.raises(LLMRateLimitDeferred):
        await gate.acquire("background")


@pytest.mark.parametrize("tier", ["triage", "interactive"])
async def test_other_tiers_pass_when_background_is_deferred(tier):
    gate, clock = make_gate()
    gate.note_usage_headers(headers(used=99, reset=100))
    assert await gate.acquire(tier) is True
    assert clock.sleeps == []


@pytest.mark.parametrize("tier", ["triage", "interactive"])
async def test_other_tiers_wait_through_a_cooldown_bounded(tier):
    gate, clock = make_gate()
    gate.note_429(make_429_body(resets_in_seconds=600))
    assert await gate.acquire(tier) is True
    assert 0 < sum(clock.sleeps) <= LLMRateGate.INTERACTIVE_MAX_WAIT_SECONDS


async def test_background_still_defers_during_cooldown():
    gate, _ = make_gate()
    gate.note_429(make_429_body(resets_in_seconds=600))
    with pytest.raises(LLMRateLimitDeferred):
        await gate.acquire("background")


async def test_every_tier_is_counted_for_diagnostics():
    gate, _ = make_gate()
    for tier in ("triage", "interactive", "background"):
        await gate.acquire(tier)
    assert gate.status()["calls_last_hour"] == 3
