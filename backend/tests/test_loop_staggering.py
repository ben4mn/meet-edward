"""Phase 1b: stagger background loops so they don't stampede at boot.

The consolidation loop (and, until 2026-09-12, dream synthesis) ran its
first cycle immediately on startup, firing background LLM calls in the
same instant. A per-loop ``startup_delay`` phase-offsets the first cycle
so the work (and the budget it spends through the rate gate) is spread
across time.
"""

import asyncio

import pytest


def _run_consolidation_until_first_config_read(*, startup_delay):
    """Drive the consolidation loop with patched sleep and a session that
    stops the loop the instant it first reads its config (the start of the
    first cycle), capturing every sleep before that point."""
    import services.consolidation_service as cons

    sleeps = []
    reads = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    class _Session:
        async def __aenter__(self):
            reads.append("config")
            raise asyncio.CancelledError  # the first cycle has begun

        async def __aexit__(self, *exc):
            return False

    orig_sleep = cons.asyncio.sleep
    orig_session = cons.async_session
    cons.asyncio.sleep = fake_sleep
    cons.async_session = lambda: _Session()
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(cons._consolidation_loop(startup_delay=startup_delay))
    finally:
        cons.asyncio.sleep = orig_sleep
        cons.async_session = orig_session
    return sleeps, reads


def test_consolidation_loop_waits_startup_delay_before_first_cycle():
    sleeps, reads = _run_consolidation_until_first_config_read(startup_delay=42)
    assert sleeps == [42]        # startup delay honored before any cycle
    assert reads == ["config"]   # then the first cycle began


def test_consolidation_loop_no_delay_runs_cycle_immediately():
    sleeps, reads = _run_consolidation_until_first_config_read(startup_delay=0)
    assert sleeps == []          # no leading delay sleep
    assert reads == ["config"]
