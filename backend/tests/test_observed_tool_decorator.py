"""
``@observed_tool`` decorator (Track F2).

Wraps an async tool function so that repeated failures (or latency
spikes) become ``self_observations`` without the tool itself having to
care. Designed to wrap entries in ``services/graph/tools.py``.

Contract:
  - Successful calls pass through unchanged. The decorator records the
    call's latency in a rolling window for spike detection.
  - Two consecutive failures with the same signature emit a
    ``kind='tool_failure'`` observation via ``record_observation``.
  - A single failure does NOT emit yet — one blip is noise. ``occurrences``
    on the signature row is bumped on every subsequent failure after
    the threshold is hit.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _reset_failure_tracker():
    # Clear the decorator's module-level state between tests so counts
    # don't leak.
    try:
        from services import observe

        observe._consecutive_failures.clear()  # type: ignore[attr-defined]
        observe._rolling_latency.clear()  # type: ignore[attr-defined]
    except Exception:
        pass
    yield


@pytest.mark.integration
async def test_single_failure_does_not_emit(clean_db):
    from services.observe import observed_tool
    from services.database import async_session, SelfObservationModel

    @observed_tool(name="fake_tool", source="test")
    async def fake():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await fake()

    async with async_session() as session:
        r = await session.execute(select(SelfObservationModel))
        rows = list(r.scalars().all())

    assert len(rows) == 0, (
        "Single failure should NOT emit — one blip is noise. The decorator "
        "requires two consecutive failures before writing an observation."
    )


@pytest.mark.integration
async def test_two_consecutive_failures_emit_one_observation(clean_db):
    from services.observe import observed_tool
    from services.database import async_session, SelfObservationModel

    @observed_tool(name="fake_tool", source="test")
    async def fake():
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await fake()

    async with async_session() as session:
        r = await session.execute(select(SelfObservationModel))
        rows = list(r.scalars().all())

    assert len(rows) == 1, (
        "Two consecutive failures with the same tool should produce one "
        f"observation row. Got {len(rows)}."
    )
    obs = rows[0]
    assert obs.kind == "tool_failure"
    assert "fake_tool" in obs.signature
    # First time we crossed the threshold; occurrences still 1 (created fresh).
    assert obs.occurrences == 1


@pytest.mark.integration
async def test_success_resets_consecutive_failure_count(clean_db):
    """A successful call between failures resets the counter — two
    alternating fail/ok/fail must NOT emit.
    """
    from services.observe import observed_tool
    from services.database import async_session, SelfObservationModel

    mode = {"fail": True}

    @observed_tool(name="alt_tool", source="test")
    async def flaky():
        if mode["fail"]:
            raise RuntimeError("boom")
        return "ok"

    # fail, ok, fail — only the two failures happened but the ok reset
    # the streak so neither counts as "consecutive".
    with pytest.raises(RuntimeError):
        await flaky()
    mode["fail"] = False
    assert await flaky() == "ok"
    mode["fail"] = True
    with pytest.raises(RuntimeError):
        await flaky()

    async with async_session() as session:
        r = await session.execute(select(SelfObservationModel))
        rows = list(r.scalars().all())

    assert len(rows) == 0, (
        "Interleaved successful call must break the consecutive-failure "
        f"streak; got {len(rows)} observation(s)."
    )
