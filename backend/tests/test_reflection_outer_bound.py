"""Reflection's outer bound sits above its own query budget (2026-09-25 audit).

``run_reflection_safe`` wrapped the whole reflection in
``wait_for(timeout=10)`` while the query generation inside it waits
``REFLECTION_TIMEOUT_SECONDS`` (20 s) — so a reflection whose queries took
11-20 s was cancelled after the model had answered (and been paid), and
the enrichments were never stored. The outer bound is now the inner
budget plus ``REFLECTION_OUTER_MARGIN_SECONDS`` (15 s for the memory
searches and the write), read from ``services.dream.context`` on every
call rather than hard-coded.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.unit


def test_the_outer_bound_is_the_query_budget_plus_fifteen():
    from services import reflection_service
    from services.dream import context

    assert reflection_service.REFLECTION_OUTER_MARGIN_SECONDS == 15.0
    assert reflection_service.reflection_outer_timeout() == context.REFLECTION_TIMEOUT_SECONDS + 15.0
    assert reflection_service.reflection_outer_timeout() > context.REFLECTION_TIMEOUT_SECONDS


async def test_the_bound_follows_the_query_budget(monkeypatch):
    from services import reflection_service
    from services.dream import context

    monkeypatch.setattr(context, "REFLECTION_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(reflection_service, "REFLECTION_OUTER_MARGIN_SECONDS", 0.03)
    finished = []

    async def slow(*a, **k):
        await asyncio.sleep(1.0)
        finished.append(True)
        return 0

    monkeypatch.setattr(reflection_service, "run_reflection", slow)
    t0 = time.monotonic()
    await reflection_service.run_reflection_safe("c1", [], [])
    assert time.monotonic() - t0 < 0.5, "cut off at inner + margin, not a hard-coded 10 s"
    assert finished == []


async def test_a_reflection_slower_than_the_old_ten_seconds_still_finishes(monkeypatch):
    """Scaled down: a run longer than its query budget (0.25 s vs 0.2 s)
    but inside budget + margin finishes and stores what it found — the
    11-20 s reflections the old 10 s bound cancelled."""
    from services import reflection_service
    from services.dream import context

    monkeypatch.setattr(context, "REFLECTION_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(reflection_service, "REFLECTION_OUTER_MARGIN_SECONDS", 0.15)
    finished = []

    async def takes_a_while(*a, **k):
        await asyncio.sleep(0.25)
        finished.append(True)
        return 3

    monkeypatch.setattr(reflection_service, "run_reflection", takes_a_while)
    await reflection_service.run_reflection_safe("c1", [], [])
    assert finished == [True]
