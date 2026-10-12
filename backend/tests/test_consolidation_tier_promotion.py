"""R/G TDD red for P3b — consolidation's tier pass must promote, not demote.

Two problems in consolidation's "auto-promote" step once _compute_tier reads
use as well as repetition:

  1. It calls ``_compute_tier(m.reinforcement_count)`` and never passes
     ``used_count``, so the use path can never fire and P3 is inert.

  2. It assigns ``expected_tier`` whenever it differs from the current tier —
     in either direction. Every one of the 67 knowledge-tier rows in the live
     store has ``used_count = 0``, because the column did not exist when they
     were promoted. Under the new rule they no longer qualify, so the next
     consolidation cycle would demote all of them.

Demotion is wrong on its own terms, not just as a migration problem. A
memory that reached knowledge earned it under the evidence available at the
time. What should happen when it stops being used is decay — and P2 handles
that, with knowledge protected from auto-retirement. Silently downgrading
something Edward already knows is a different and worse behaviour.

So the step promotes only, which is what its name has always claimed.
"""

from __future__ import annotations

import inspect

import pytest


def _promotion_source() -> str:
    from services import consolidation_service

    return inspect.getsource(consolidation_service)


def test_promotion_passes_used_count():
    src = _promotion_source()

    assert "_compute_tier(m.reinforcement_count or 0)" not in src, (
        "The tier pass still ignores used_count, so P3's use path can never "
        "fire and no memory can ever be promoted for being useful."
    )
    assert "used_count" in src, (
        "consolidation must read used_count when recomputing tiers."
    )


def test_promotion_never_demotes():
    """Guard the direction of travel."""
    from services.consolidation_service import _is_promotion

    assert _is_promotion("observation", "belief") is True
    assert _is_promotion("belief", "knowledge") is True
    assert _is_promotion("observation", "knowledge") is True

    assert _is_promotion("knowledge", "belief") is False, (
        "Demoting a memory that already reached knowledge discards evidence "
        "that was valid when it was gathered. Disuse is handled by decay."
    )
    assert _is_promotion("belief", "observation") is False
    assert _is_promotion("knowledge", "observation") is False

    assert _is_promotion("belief", "belief") is False


def test_promotion_handles_unknown_tiers():
    from services.consolidation_service import _is_promotion

    assert _is_promotion(None, "belief") is True
    assert _is_promotion("belief", None) is False
    assert _is_promotion("nonsense", "knowledge") is True
