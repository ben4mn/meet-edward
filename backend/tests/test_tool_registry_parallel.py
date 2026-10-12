"""
Tests for tool registry skill-lookup parallelization.

`_get_skill_states` issues 18 `await is_skill_enabled(...)` calls in series
on every cache miss (default TTL 5s). Each is a DB round-trip. With 18
branches at ~3ms each, that's ~50ms wasted per miss.

Fix: gather the lookups so they run concurrently, and bump the TTL to 30s
since skills rarely toggle and `refresh_registry()` is called explicitly
on toggle.
"""

import asyncio
import time

import pytest


async def test_skill_state_lookups_run_concurrently(monkeypatch):
    """Force a cache miss and verify all skill lookups happen in parallel.

    With 18 skills each taking 50ms serially, the function would take ~900ms.
    In parallel it should complete in ~50ms (one skill's latency).
    """
    from services import tool_registry

    async def slow_is_skill_enabled(skill_id: str) -> bool:
        await asyncio.sleep(0.05)
        return False

    import services.skills_service as skills_service
    monkeypatch.setattr(skills_service, "is_skill_enabled", slow_is_skill_enabled)

    # Bust the cache to force a miss
    tool_registry._skill_cache = {}
    tool_registry._cache_timestamp = 0

    start = time.monotonic()
    await tool_registry._get_skill_states(force_refresh=True)
    elapsed = time.monotonic() - start

    # Serial would be 18 * 0.05 = 0.9s. Parallel should be well under 0.3s.
    assert elapsed < 0.3, (
        f"Skill lookups must run in parallel; took {elapsed:.3f}s (serial would be ~0.9s)"
    )


async def test_cache_ttl_is_at_least_30_seconds():
    """Bump the TTL — skills rarely toggle and refresh_registry() is explicit."""
    from services import tool_registry

    assert tool_registry._CACHE_TTL_SECONDS >= 30, (
        "Cache TTL should be ≥30s: skill toggles call refresh_registry() explicitly, "
        "so the TTL is just a safety net — no reason for an aggressive 5s."
    )
