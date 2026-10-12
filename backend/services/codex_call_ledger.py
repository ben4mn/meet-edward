"""The Codex call ledger — one row per Codex request, and the meter as a
time series (2026-09-24).

Why: the account has one weekly quota window shared by every GPT model,
and nothing said who spent it. ``usage_events`` recorded Anthropic field
names with ``source="chat"`` for every turn, so cached and reasoning tokens
were invisible and mind and pulse spend was labelled chat; ``codex exec``
discarded its usage; the rate gate kept only the latest meter reading.

* ``codex_calls`` — written for every HTTP request ``ChatCodexResponses``
  makes (success or failure: ``ok`` | ``error`` | ``rate_limited`` |
  ``timeout`` | ``cancelled``) and for each ``codex exec`` ``turn.completed``
  (``turn_source="codex_exec"``). This module is the only writer; other
  code may read the table.
* ``llm_meter_samples`` — each meter reading the gate adopts (``headers`` or
  ``wham``), at most one row a minute per window (the throttle lives in the
  gate).

``record_call`` / ``record_meter_samples`` are async and never raise.
``schedule_call`` / ``schedule_meter_samples`` are the fire-and-forget forms
the hot paths use, so an LLM call never waits on the database; they honour
``WRITES_ENABLED`` (``tests/conftest.py`` turns it off for unit tests, whose
event loops close under a pending write). Both tables are pruned with the
other ledgers (``turn_ledger.prune``, 30 days).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Set

logger = logging.getLogger(__name__)

WRITES_ENABLED = True

CALL_STATUSES = ("ok", "error", "rate_limited", "timeout", "cancelled")

# Column -> max length for the string columns (the rest are ints).
_STR_LIMITS = {
    "purpose": 60,
    "priority": 20,
    "turn_source": 40,
    "conversation_id": 200,
    "turn_id": 40,
    "model": 60,
    "effort": 20,
    "status": 20,
    "error_class": 160,
}
_INT_FIELDS = (
    "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens",
    "latency_ms", "ttft_ms", "http_status",
)

_pending: Set["asyncio.Task"] = set()


# ------------------------------------------------------------------ pure --

def call_row(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the known columns, bound the strings, coerce the counts."""
    row: Dict[str, Any] = {}
    for key, limit in _STR_LIMITS.items():
        value = fields.get(key)
        row[key] = None if value is None or value == "" else str(value)[:limit]
    for key in _INT_FIELDS:
        value = fields.get(key)
        try:
            row[key] = None if value is None else int(value)
        except (TypeError, ValueError):
            row[key] = None
    if row["status"] not in CALL_STATUSES:
        row["status"] = "error" if row["status"] else "ok"
    # Stamped in UTC here, not by the server default: Postgres' now() into
    # a naive column is the *server's* zone (the host Mac's), and the
    # spend report lines these rows up against meter samples in UTC.
    created_at = fields.get("created_at")
    row["created_at"] = created_at if isinstance(created_at, datetime) else utc_now()
    return row


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_naive(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def meter_sample_rows(meter: Any, source: str, windows: Iterable[str]) -> List[Dict[str, Any]]:
    """Rows for the named windows of a ``QuotaMeter`` reading."""
    rows: List[Dict[str, Any]] = []
    sampled_at = float(getattr(meter, "sampled_at", 0.0) or 0.0)
    for name in windows:
        w = (getattr(meter, "windows", None) or {}).get(name)
        if w is None:
            continue
        rows.append({
            "sampled_at": _utc_naive(sampled_at),
            "window": str(name)[:20],
            "used_percent": float(w.used_percent),
            "window_minutes": int(w.window_seconds // 60),
            "resets_at": _utc_naive(sampled_at + max(0.0, float(w.reset_after_seconds))),
            "source": str(source or "headers")[:20],
        })
    return rows


# --------------------------------------------------------------- writes --

async def record_call(**fields: Any) -> None:
    """Insert one ``codex_calls`` row. Unknown keys are ignored. Never raises."""
    try:
        from services.database import async_session, CodexCallModel

        row = call_row(fields)
        async with async_session() as session:
            session.add(CodexCallModel(**row))
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — a ledger that cannot write is not an outage
        logger.debug("codex call ledger write failed: %s", exc)
        return
    try:
        from services import turn_ledger

        turn_ledger.maybe_prune()
    except Exception:  # noqa: BLE001
        pass


async def record_meter_samples(rows: List[Dict[str, Any]]) -> None:
    """Insert ``llm_meter_samples`` rows. Never raises."""
    if not rows:
        return
    try:
        from services.database import async_session, LLMMeterSampleModel

        async with async_session() as session:
            for row in rows:
                session.add(LLMMeterSampleModel(**row))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("meter sample write failed: %s", exc)


def _schedule(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()  # no loop (shutdown, a sync caller) — nothing to record on
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def schedule_call(**fields: Any) -> None:
    """Fire-and-forget ``record_call``; never raises, never blocks."""
    if not WRITES_ENABLED:
        return
    try:
        fields.setdefault("created_at", utc_now())  # when the call ended, not when the write lands
        _schedule(record_call(**fields))
    except Exception as exc:  # noqa: BLE001
        logger.debug("codex call ledger schedule failed: %s", exc)


def schedule_meter_samples(meter: Any, source: str, windows: Iterable[str]) -> None:
    """Fire-and-forget ``record_meter_samples`` for a gate reading."""
    if not WRITES_ENABLED:
        return
    try:
        rows = meter_sample_rows(meter, source, windows)
        if rows:
            _schedule(record_meter_samples(rows))
    except Exception as exc:  # noqa: BLE001
        logger.debug("meter sample schedule failed: %s", exc)


async def drain() -> None:
    """Wait for pending writes (tests, shutdown)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)
