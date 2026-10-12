"""
Hot-memory feedback loop.

Retrieval (normal + deep) writes memory ids into this set. Consolidation
reads it and unions into its lookback query, so actively-used memories
participate in the next clustering cycle even if their DB timestamps
would otherwise exclude them.

In-process TTL set. Single-user app — no distribution concerns.
"""

from __future__ import annotations

import time
from typing import Iterable, List

# {memory_id: expires_at (monotonic seconds)}
_entries: dict[str, float] = {}
_DEFAULT_TTL_SECONDS = 2 * 3600.0  # 2 hours — roughly the consolidation lookback


def mark_hot(ids: Iterable[str], ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
    """Mark memory ids as actively used. Safe to call repeatedly."""
    if not ids:
        return
    expires_at = time.monotonic() + ttl_seconds
    for mid in ids:
        if not mid:
            continue
        existing = _entries.get(mid)
        if existing is None or existing < expires_at:
            _entries[mid] = expires_at


def iter_active() -> List[str]:
    """Return ids that haven't expired. Prunes expired entries as a side effect."""
    now = time.monotonic()
    expired = [mid for mid, exp in _entries.items() if exp <= now]
    for mid in expired:
        _entries.pop(mid, None)
    return list(_entries.keys())


def clear() -> None:
    """Drop every entry. Tests use this; production generally doesn't."""
    _entries.clear()
