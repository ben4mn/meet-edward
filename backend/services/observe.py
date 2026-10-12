"""
Self-observation helper (Track F1).

``record_observation(...)`` is the single write path for the
``self_observations`` table. Repeated calls with the same ``signature``
collapse into one row with an incrementing ``occurrences`` counter —
so a flaky tool that fails 20 times in an hour is one observation, not
20. The journal, the wakeup signal pack and the Dreaming panel read
them; a pattern that keeps recurring is what Edward puts on his wishlist.

The helper is fire-and-forget safe: any DB error is logged and
swallowed. Never raise back into the caller's hot path.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import select

from services.database import async_session, SelfObservationModel, SelfObservationHitModel

logger = logging.getLogger(__name__)

# Module-level tracking for @observed_tool. In-process only; resets on
# restart. For cross-process accounting the observations table is the
# source of truth.
_consecutive_failures: dict[str, int] = {}
_rolling_latency: dict[str, list[float]] = {}
_LATENCY_WINDOW = 50
_FAILURE_THRESHOLD = 2  # two consecutive failures before emitting


HIT_EVIDENCE_MAX_CHARS = 500


def _hit(observation_id: str, seen_at: datetime, evidence: Optional[Mapping[str, Any]]) -> SelfObservationHitModel:
    """One time-series row per call; evidence capped so the table stays small."""
    ev = None
    if evidence:
        try:
            ev = json.dumps(evidence)[:HIT_EVIDENCE_MAX_CHARS]
        except Exception:
            ev = None
    return SelfObservationHitModel(observation_id=observation_id, seen_at=seen_at, evidence_json=ev)


async def record_observation(
    *,
    kind: str,
    source: str,
    signature: str,
    description: str,
    severity: float = 0.3,
    confidence: float = 0.7,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Upsert an observation keyed on ``signature``.

    Returns the row id on success, ``None`` on failure. Never raises.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(SelfObservationModel).where(
                    SelfObservationModel.signature == signature
                )
            )
            existing = result.scalar_one_or_none()
            now = datetime.utcnow()
            if existing is not None:
                # Re-open a stale observation if it re-occurs — with a
                # fresh occurrence count, so MIN_OCCURRENCES gates new
                # signal after a fix landed instead of the historical
                # pile (one signature reached 9,116 before this).
                if existing.status in ("superseded", "ignored"):
                    existing.status = "open"
                    existing.occurrences = 1
                else:
                    existing.occurrences = (existing.occurrences or 0) + 1
                existing.last_seen_at = now
                existing.description = description  # keep latest wording
                if evidence is not None:
                    try:
                        existing.evidence_json = json.dumps(evidence)
                    except Exception:
                        pass
                if severity is not None:
                    existing.severity = severity
                if confidence is not None:
                    existing.confidence = confidence
                session.add(_hit(existing.id, now, evidence))
                await session.commit()
                return existing.id

            row = SelfObservationModel(
                kind=kind,
                source=source,
                signature=signature,
                description=description,
                severity=severity,
                confidence=confidence,
                evidence_json=(json.dumps(evidence) if evidence else None),
                occurrences=1,
                status="open",
                last_seen_at=now,
            )
            session.add(row)
            await session.flush()
            session.add(_hit(row.id, now, evidence))
            await session.commit()
            await session.refresh(row)
            return row.id
    except Exception as e:
        logger.warning(
            "record_observation failed for signature=%s: %s", signature, e
        )
        return None


def observed_tool(*, name: str, source: str) -> Callable:
    """Decorator: wrap an async tool so repeated failures become observations.

    Only emits after ``_FAILURE_THRESHOLD`` consecutive failures with the
    same tool name so one blip isn't noise. A successful call resets the
    streak. Latency is tracked in a bounded rolling window per tool —
    future work can use it for spike-detection observations.
    """

    def _decorator(fn: Callable) -> Callable:
        sig = f"tool_failure.{source}.{name}"

        @functools.wraps(fn)
        async def _wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                count = _consecutive_failures.get(sig, 0) + 1
                _consecutive_failures[sig] = count
                if count >= _FAILURE_THRESHOLD:
                    await record_observation(
                        kind="tool_failure",
                        source=source,
                        signature=sig,
                        description=f"{name} failed {count} times in a row: {exc!r}",
                        severity=min(0.3 + 0.1 * count, 0.9),
                        confidence=0.8,
                        evidence={
                            "tool": name,
                            "consecutive_failures": count,
                            "last_error": repr(exc),
                        },
                    )
                raise
            else:
                # Success resets the streak.
                _consecutive_failures.pop(sig, None)
                # Bounded rolling latency window for future spike checks.
                elapsed = time.monotonic() - start
                lat = _rolling_latency.setdefault(sig, [])
                lat.append(elapsed)
                if len(lat) > _LATENCY_WINDOW:
                    del lat[0 : len(lat) - _LATENCY_WINDOW]
                return result

        # For tests/debug: let callers introspect the decorator's state.
        _wrapper.observe_signature = sig  # type: ignore[attr-defined]
        return _wrapper

    return _decorator
