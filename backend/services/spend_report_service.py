"""``GET /api/health/spend?days=7`` — who spends the Codex week, how fast,
and what it bought (2026-09-24).

The account has one weekly quota window shared by every GPT model. This
report reads the spend ledger (``codex_calls``, one row per Codex request,
and ``llm_meter_samples``, the meter as a time series; both written by
``services/codex_call_ledger.py``) and answers:

* **Who spends**: calls, input / cached / output / reasoning tokens, cache
  hit ratio, p50/p95 latency and errors, by purpose, turn source and model,
  each with its estimated share of the meter.
* **How fast**: the meter series, burn in %/h over the last 24 h and 6 h,
  hours to the reset, and the used % projected at the reset at that burn;
  the hourly burn of calls nobody may defer (p50/p95 — the reserve a
  surplus policy must keep).
* **What a token costs**: meter-% per 1M tokens of each kind (uncached
  input, cached input, output) per model, by least squares over meter
  deltas. It reports ``n`` and says ``insufficient`` rather than print a
  number the data cannot back. Other clients on the account (Codex CLI on
  a laptop, the ChatGPT app) spend the same quota and show up as noise.
* **What it bought**: memories extracted and used, mind wakes by outcome,
  pushes sent and opened, documents written, the owner's verdicts — counted from
  the tables that already record them.

Everything above the I/O section is pure and tested without a database
(``tests/test_spend_report.py``).
"""

from __future__ import annotations

import bisect
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.health_report_service import percentile

logger = logging.getLogger(__name__)

# Meter deltas are regressed over intervals at least this long: the meter
# moves in whole or tenth percents, so minute-to-minute deltas are mostly 0.
METER_INTERVAL_S = 1800
# A new window: its reset moved by more than this, or used% fell this far.
RESET_JUMP_S = 3600
RESET_DROP_PERCENT = 20.0
TOKEN_KINDS = ("uncached_input", "cached_input", "output")


def _n(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() + "Z" if isinstance(value, datetime) else None


# ------------------------------------------------------------- totals --

def estimated_percent(call: Dict[str, Any], est: Optional[Dict[str, Any]]) -> Optional[float]:
    """The meter % one call probably cost: its tokens priced by its model's
    regression coefficients when the regression is sound (``ok``), else at
    the pooled rate; ``None`` when there is no rate at all."""
    if not est:
        return None
    inp, cached, out = _n(call.get("input_tokens")), _n(call.get("cached_tokens")), _n(call.get("output_tokens"))
    coef = (est.get("models") or {}).get(str(call.get("model") or "unknown")) if est.get("status") == "ok" else None
    if coef:
        parts = {"uncached_input": max(0, inp - cached), "cached_input": cached, "output": out}
        return sum((coef.get(kind) or 0.0) * tokens / 1e6 for kind, tokens in parts.items())
    pooled = est.get("pooled_percent_per_1m_tokens")
    if pooled is None:
        return None
    return (inp + out) / 1e6 * pooled


def _group_stats(items: Sequence[Dict[str, Any]], est: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    inp = sum(_n(i.get("input_tokens")) for i in items)
    cached = sum(_n(i.get("cached_tokens")) for i in items)
    latency = [i.get("latency_ms") for i in items if i.get("latency_ms") is not None]
    ttft = [i.get("ttft_ms") for i in items if i.get("ttft_ms") is not None]
    costs = [estimated_percent(i, est) for i in items] if est else []
    priced = [c for c in costs if c is not None]
    return {
        "calls": len(items),
        "input_tokens": inp,
        "cached_tokens": cached,
        "uncached_input_tokens": max(0, inp - cached),
        "output_tokens": sum(_n(i.get("output_tokens")) for i in items),
        "reasoning_tokens": sum(_n(i.get("reasoning_tokens")) for i in items),
        "cache_hit_ratio": round(cached / inp, 3) if inp else None,
        # This group's share of the meter, priced by estimate_meter_cost.
        "estimated_percent": sum(priced) if priced else None,
        "latency_ms": {"p50": percentile(latency, 50), "p95": percentile(latency, 95)},
        "ttft_ms": {"p50": percentile(ttft, 50), "p95": percentile(ttft, 95)},
        "errors": dict(Counter(i.get("status") for i in items if i.get("status") not in (None, "ok"))),
    }


def summarize_calls(rows: Iterable[Dict[str, Any]], key: str,
                    est: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Per value of ``key`` (purpose, turn_source, model): the group's
    stats, biggest spender (input + output tokens) first. With ``est``
    (``estimate_meter_cost``) each group also carries its estimated share
    of the meter."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "unknown")].append(r)
    stats = {name: _group_stats(items, est) for name, items in groups.items()}
    return dict(sorted(stats.items(), key=lambda kv: (-(kv[1]["input_tokens"] + kv[1]["output_tokens"]),
                                                      -kv[1]["calls"], kv[0])))


INTERACTIVE_PRIORITIES = ("interactive", "triage")
# Edward's own turns (2026-09-25): mind wakes (``mind:<reason>``), the
# surfaces pulse (``pulse``) and surplus jobs (``surplus:<job>``) run at
# interactive priority but nobody is waiting on them — they are not the
# chat the reserve is kept for. Rows written before wakes were labelled
# carry only their turn's source.
AUTONOMOUS_PURPOSE_PREFIXES = ("mind:", "surplus:")
AUTONOMOUS_PURPOSES = ("pulse",)
AUTONOMOUS_TURN_SOURCES = ("mind", "pulse")


def is_autonomous_call(call: Dict[str, Any]) -> bool:
    """Was this Codex call one of Edward's own turns rather than the owner's? Pure."""
    purpose = str(call.get("purpose") or "")
    if purpose in AUTONOMOUS_PURPOSES or purpose.startswith(AUTONOMOUS_PURPOSE_PREFIXES):
        return True
    return call.get("turn_source") in AUTONOMOUS_TURN_SOURCES


def interactive_burn(calls: Iterable[Dict[str, Any]], est: Optional[Dict[str, Any]], *, now: datetime,
                     priorities: Sequence[str] = INTERACTIVE_PRIORITIES) -> Optional[Dict[str, Any]]:
    """The meter %/h the owner's own calls (chat, voice, triage) spent, hour by
    hour from the first recorded call to ``now`` — quiet hours count as 0.
    Its p95 is the chat part of the reserve a surplus policy keeps in hand
    (``r_int``). ``None`` without calls or a rate."""
    # A surplus job's wake, a mind wake and the pulse are chat-path turns at
    # interactive priority; they are not the owner's chat (2026-09-24, widened
    # 2026-09-25) — the mind's and the pulse's are ``autonomous_burn``.
    return _burn(calls, est, now=now, priorities=priorities, include=lambda c: not is_autonomous_call(c))


def autonomous_burn(calls: Iterable[Dict[str, Any]], est: Optional[Dict[str, Any]], *, now: datetime,
                    priorities: Sequence[str] = INTERACTIVE_PRIORITIES) -> Optional[Dict[str, Any]]:
    """The meter %/h Edward's own non-deferrable turns spent — mind wakes
    and the surfaces pulse run at interactive priority, so the rate gate
    never defers them. The surplus reserve adds this to the owner's chat or the
    week overshoots its target into background's ceiling (review
    2026-09-26). Surplus-launched wakes (``surplus:<job>``) are paced by the
    surplus decision itself and stay out. Same shape as
    ``interactive_burn``; ``None`` without calls or a rate."""
    return _burn(calls, est, now=now, priorities=priorities,
                 include=lambda c: is_autonomous_call(c)
                 and not str(c.get("purpose") or "").startswith("surplus:"))


def _burn(calls: Iterable[Dict[str, Any]], est: Optional[Dict[str, Any]], *, now: datetime,
          priorities: Sequence[str], include) -> Optional[Dict[str, Any]]:
    rows = [c for c in calls if isinstance(c.get("created_at"), datetime)]
    if not rows:
        return None
    start = min(c["created_at"] for c in rows).replace(minute=0, second=0, microsecond=0)
    hours = max(1, int(-(-(now - start).total_seconds() // 3600)))
    buckets = [0.0] * hours
    priced_any = False
    for c in rows:
        if c.get("priority") not in priorities:
            continue
        if not include(c):
            continue
        cost = estimated_percent(c, est)
        if cost is None:
            continue
        priced_any = True
        index = min(hours - 1, max(0, int((c["created_at"] - start).total_seconds() // 3600)))
        buckets[index] += cost
    if not priced_any:
        return None
    return {
        "hours": hours,
        "p50": percentile(buckets, 50),
        "p95": percentile(buckets, 95),
        "max": max(buckets),
        "mean": sum(buckets) / hours,
        # The surplus reserve is r_int · hours to the reset (2026-09-24).
        # The hourly p95 is the owner's busiest hour; held for a hundred hours it
        # reserves the whole window. This is the p95 of trailing-24h
        # averages — a heavy day of chat, sustained. None under a day.
        "p95_day": _p95_of_daily_means(buckets),
        "priorities": list(priorities),
    }


def _p95_of_daily_means(buckets: Sequence[float], window: int = 24) -> Optional[float]:
    if len(buckets) < window:
        return None
    means = [sum(buckets[i - window + 1:i + 1]) / window for i in range(window - 1, len(buckets))]
    return percentile(means, 95)


# -------------------------------------------------------------- meter --

def _points(samples: Iterable[Dict[str, Any]], window: str) -> List[Dict[str, Any]]:
    pts = [s for s in samples if s.get("window") == window and isinstance(s.get("sampled_at"), datetime)
           and s.get("used_percent") is not None]
    return sorted(pts, key=lambda s: s["sampled_at"])


def _is_reset(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Did the window reset between two consecutive readings?"""
    ra, rb = a.get("resets_at"), b.get("resets_at")
    if isinstance(ra, datetime) and isinstance(rb, datetime) and (rb - ra).total_seconds() > RESET_JUMP_S:
        return True
    return float(b["used_percent"]) < float(a["used_percent"]) - RESET_DROP_PERCENT


def meter_series(samples: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    samples = list(samples)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for window in sorted({str(s.get("window")) for s in samples if s.get("window")}):
        out[window] = [
            {"at": _iso(p["sampled_at"]), "used_percent": p["used_percent"], "resets_at": _iso(p.get("resets_at")),
             "source": p.get("source")}
            for p in _points(samples, window)
        ]
    return out


def burn_rate(samples: Iterable[Dict[str, Any]], *, now: datetime, hours: float,
              window: str = "primary") -> Optional[Dict[str, Any]]:
    """Meter % spent per hour over the last ``hours``: the sum of rises
    between consecutive readings (after a reset, the new reading counts
    from zero), over the span the readings cover. ``None`` with fewer than
    two readings."""
    since = now - timedelta(hours=hours)
    pts = [p for p in _points(samples, window) if since <= p["sampled_at"] <= now]
    if len(pts) < 2:
        return None
    spent = 0.0
    for a, b in zip(pts, pts[1:]):
        if _is_reset(a, b):
            spent += max(0.0, float(b["used_percent"]))
        else:
            spent += max(0.0, float(b["used_percent"]) - float(a["used_percent"]))
    span_h = (pts[-1]["sampled_at"] - pts[0]["sampled_at"]).total_seconds() / 3600.0
    if span_h <= 0:
        return None
    return {"percent_per_hour": spent / span_h, "spent_percent": spent, "span_hours": span_h, "n": len(pts)}


def reset_outlook(samples: Iterable[Dict[str, Any]], *, now: datetime, burn_per_hour: Optional[float],
                  window: str = "primary") -> Optional[Dict[str, Any]]:
    """Where the window stands and where it lands at the reset if the
    current burn holds."""
    pts = _points(samples, window)
    if not pts:
        return None
    last = pts[-1]
    used = float(last["used_percent"])
    resets_at = last.get("resets_at")
    hours = max(0.0, (resets_at - now).total_seconds() / 3600.0) if isinstance(resets_at, datetime) else None
    reset_since = isinstance(resets_at, datetime) and resets_at <= now
    projected = None
    to_exhaustion = None
    if burn_per_hour is not None and hours is not None and not reset_since:
        projected = used + burn_per_hour * hours
        if burn_per_hour > 0:
            to_exhaustion = max(0.0, (100.0 - used) / burn_per_hour)
    return {
        "used_percent": used,
        "sampled_at": _iso(last["sampled_at"]),
        "resets_at": _iso(resets_at),
        "hours_to_reset": hours,
        "window_reset_since_sample": bool(reset_since),
        "burn_percent_per_hour": burn_per_hour,
        "projected_at_reset": projected,
        "hours_to_exhaustion": to_exhaustion,
        "exhausted_before_reset": bool(projected is not None and projected >= 100.0),
    }


# --------------------------------------------------------- regression --

def meter_intervals(samples: Iterable[Dict[str, Any]], *, min_interval_s: int = METER_INTERVAL_S,
                    window: str = "primary") -> List[Dict[str, Any]]:
    """Consecutive stretches of at least ``min_interval_s`` inside one
    window cycle, with the meter's rise across each. A reset inside a
    stretch discards it."""
    pts = _points(samples, window)
    intervals: List[Dict[str, Any]] = []
    if not pts:
        return intervals
    start = prev = pts[0]
    for p in pts[1:]:
        if _is_reset(prev, p):
            start = prev = p
            continue
        prev = p
        if (p["sampled_at"] - start["sampled_at"]).total_seconds() >= min_interval_s:
            intervals.append({
                "start": start["sampled_at"], "end": p["sampled_at"],
                "delta": float(p["used_percent"]) - float(start["used_percent"]),
            })
            start = p
    return intervals


def _solve(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting; ``None`` when singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    scale = max((abs(v) for row in a for v in row), default=0.0) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) <= 1e-12 * scale:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def estimate_meter_cost(samples: Iterable[Dict[str, Any]], calls: Iterable[Dict[str, Any]], *,
                        min_interval_s: int = METER_INTERVAL_S, window: str = "primary") -> Dict[str, Any]:
    """Meter-% per 1M tokens of each kind per model, by ordinary least
    squares of each interval's meter rise on the tokens spent in it.

    Token kinds a model never used are left out (and reported ``None``).
    With fewer intervals than ``max(2k, k + 5)`` for ``k`` unknowns, or
    token kinds that always move together, the answer is ``insufficient``;
    a negative coefficient makes it ``unstable``.
    """
    samples = list(samples)
    intervals = meter_intervals(samples, min_interval_s=min_interval_s, window=window)
    calls = sorted((c for c in calls if isinstance(c.get("created_at"), datetime)), key=lambda c: c["created_at"])
    times = [c["created_at"] for c in calls]
    models = sorted({str(c.get("model") or "unknown") for c in calls})
    columns = [(m, k) for m in models for k in TOKEN_KINDS]
    index = {col: j for j, col in enumerate(columns)}

    rows: List[List[float]] = []
    deltas: List[float] = []
    total_tokens = 0.0
    for iv in intervals:
        feats = [0.0] * len(columns)
        lo = bisect.bisect_right(times, iv["start"])
        hi = bisect.bisect_right(times, iv["end"])
        for c in calls[lo:hi]:
            m = str(c.get("model") or "unknown")
            inp, cached, out = _n(c.get("input_tokens")), _n(c.get("cached_tokens")), _n(c.get("output_tokens"))
            feats[index[(m, "uncached_input")]] += max(0, inp - cached) / 1e6
            feats[index[(m, "cached_input")]] += cached / 1e6
            feats[index[(m, "output")]] += out / 1e6
        total_tokens += sum(feats)
        rows.append(feats)
        deltas.append(iv["delta"])

    active = [j for j in range(len(columns)) if any(r[j] != 0.0 for r in rows)]
    n, k = len(rows), len(active)
    base: Dict[str, Any] = {
        "n": n,
        "k": k,
        "min_interval_minutes": min_interval_s // 60,
        "pooled_percent_per_1m_tokens": (sum(deltas) / total_tokens) if total_tokens > 0 else None,
        "note": "meter % per 1M tokens; other clients on the account spend the same quota and add noise",
    }
    if k == 0 or n < max(2 * k, k + 5):
        return {**base, "status": "insufficient", "reason": f"{n} intervals for {k} unknowns", "models": {}}

    x = [[r[j] for j in active] for r in rows]
    xtx = [[sum(row[i] * row[j] for row in x) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * y for row, y in zip(x, deltas)) for i in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return {**base, "status": "insufficient", "reason": "token kinds move together; cannot separate them",
                "models": {}}

    fitted = [sum(b * v for b, v in zip(beta, row)) for row in x]
    mean = sum(deltas) / n
    ss_tot = sum((y - mean) ** 2 for y in deltas)
    ss_res = sum((y - f) ** 2 for y, f in zip(deltas, fitted))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None

    coef: Dict[tuple, float] = {columns[j]: b for j, b in zip(active, beta)}
    out_models = {m: {kind: coef.get((m, kind)) for kind in TOKEN_KINDS} for m in models}
    unstable = any(b < 0 for b in beta)
    return {**base, "status": "unstable" if unstable else "ok", "r2": r2, "models": out_models}


# ------------------------------------------------------------ compose --

def compose_spend(*, days: int, calls: List[Dict[str, Any]], samples: List[Dict[str, Any]],
                  outputs: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    burn_24 = burn_rate(samples, now=now, hours=24)
    burn_6 = burn_rate(samples, now=now, hours=6)
    cost = estimate_meter_cost(samples, calls)
    return {
        "generated_at": _iso(now),
        "window_days": days,
        "totals": _group_stats(calls, cost),
        "by": {
            "purpose": summarize_calls(calls, "purpose", est=cost),
            "turn_source": summarize_calls(calls, "turn_source", est=cost),
            "model": summarize_calls(calls, "model", est=cost),
        },
        "meter": meter_series(samples),
        "burn": {"last_24h": burn_24, "last_6h": burn_6},
        "interactive_burn": interactive_burn(calls, cost, now=now),
        "autonomous_burn": autonomous_burn(calls, cost, now=now),
        "outlook": reset_outlook(samples, now=now,
                                 burn_per_hour=burn_24["percent_per_hour"] if burn_24 else None),
        "meter_cost": cost,
        "bought": outputs,
    }


# ---------------------------------------------------------------- I/O --

async def _bought(session, since: datetime) -> Dict[str, Any]:
    """What the spend produced, from the tables that already record it.
    Each count is isolated: one missing table degrades the section."""
    from sqlalchemy import func, select
    from services.database import (
        TurnLogModel, LedgerEventModel, PushLogModel, DocumentModel, UserSignalModel,
    )

    out: Dict[str, Any] = {}

    async def _try(name: str, fn) -> None:
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            out.setdefault("errors", {})[name] = str(exc)[:120]
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass

    async def memories():
        extracted, used, turns = (await session.execute(
            select(func.coalesce(func.sum(TurnLogModel.memories_extracted), 0),
                   func.coalesce(func.sum(TurnLogModel.memories_used), 0),
                   func.count())
            .where(TurnLogModel.started_at >= since)
        )).one()
        out.update(memories_extracted=int(extracted), memories_used=int(used), turns=int(turns))

    async def wakes():
        rows = (await session.execute(
            select(LedgerEventModel.outcome, func.count())
            .where(LedgerEventModel.kind == "mind", LedgerEventModel.created_at >= since)
            .group_by(LedgerEventModel.outcome)
        )).all()
        out["mind_wakes"] = {str(o): int(c) for o, c in rows}

    async def pushes():
        sent, opened = (await session.execute(
            select(func.count().filter(PushLogModel.sent > 0),
                   func.count().filter(PushLogModel.opened_at.isnot(None)))
            .where(PushLogModel.sent_at >= since)
        )).one()
        out.update(pushes_sent=int(sent), pushes_opened=int(opened))

    async def documents():
        out["documents_created"] = int((await session.execute(
            select(func.count()).select_from(DocumentModel).where(DocumentModel.created_at >= since)
        )).scalar_one())

    async def verdicts():
        rows = (await session.execute(
            select(UserSignalModel.kind, func.count())
            .where(UserSignalModel.created_at >= since)
            .group_by(UserSignalModel.kind)
        )).all()
        out["verdicts"] = {str(k): int(c) for k, c in rows}

    for name, fn in (("memories", memories), ("mind_wakes", wakes), ("pushes", pushes),
                     ("documents", documents), ("verdicts", verdicts)):
        await _try(name, fn)
    return out


async def build_spend_report(days: int = 7) -> Dict[str, Any]:
    report = await _spend(days, with_outputs=True)
    # Pay per token (2026-10-10): today's Claude spend against the daily cap,
    # the credits left and a per-day series (services/llm_spend.py).
    try:
        from services import llm_spend

        report["claude"] = await llm_spend.claude_block(days=days)
    except Exception as exc:  # noqa: BLE001 — one section must not sink the report
        report["claude"] = {"error": str(exc)[:120]}
    return report


async def spend_snapshot(days: int = 7) -> Dict[str, Any]:
    """The report without "what it bought" — the ledger reads only. The
    surplus controller's r_int and the usage card's top spenders come from
    here (``services/surplus_service.py`` caches it for a few minutes)."""
    return await _spend(days, with_outputs=False)


async def _spend(days: int, *, with_outputs: bool) -> Dict[str, Any]:
    from sqlalchemy import select
    from services.database import async_session, CodexCallModel, LLMMeterSampleModel

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=days)
    call_cols = (
        CodexCallModel.created_at, CodexCallModel.purpose, CodexCallModel.priority, CodexCallModel.turn_source,
        CodexCallModel.model, CodexCallModel.effort, CodexCallModel.input_tokens, CodexCallModel.cached_tokens,
        CodexCallModel.output_tokens, CodexCallModel.reasoning_tokens, CodexCallModel.latency_ms,
        CodexCallModel.ttft_ms, CodexCallModel.status,
    )
    sample_cols = (
        LLMMeterSampleModel.sampled_at, LLMMeterSampleModel.window, LLMMeterSampleModel.used_percent,
        LLMMeterSampleModel.window_minutes, LLMMeterSampleModel.resets_at, LLMMeterSampleModel.source,
    )
    calls: List[Dict[str, Any]] = []
    samples: List[Dict[str, Any]] = []
    outputs: Dict[str, Any] = {}
    async with async_session() as session:
        try:
            calls = [dict(r._mapping) for r in (await session.execute(
                select(*call_cols).where(CodexCallModel.created_at >= since)
            )).all()]
            samples = [dict(r._mapping) for r in (await session.execute(
                select(*sample_cols).where(LLMMeterSampleModel.sampled_at >= since)
                .order_by(LLMMeterSampleModel.sampled_at)
            )).all()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("spend report: ledger read failed: %s", exc)
            await session.rollback()
        if with_outputs:
            outputs = await _bought(session, since)
    return compose_spend(days=days, calls=calls, samples=samples, outputs=outputs, now=now)
