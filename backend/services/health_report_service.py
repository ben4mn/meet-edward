"""``GET /api/health/report`` — the one call a health check starts from.

Pure summaries over the ledgers (``turn_log``, ``self_observation_hits``
+ ``self_observations``, ``user_signals``) plus the storage footprint of
the ledgers themselves, so growth is watched from the same place.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

TOP_N = 10


def percentile(values: Iterable[Optional[float]], p: float) -> Optional[float]:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    frac = k - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def _stats(values: List[Optional[float]]) -> Dict[str, Any]:
    xs = [v for v in values if v is not None]
    return {
        "n": len(xs),
        "p50": percentile(xs, 50),
        "p95": percentile(xs, 95),
        "max": max(xs) if xs else None,
    }


def summarize_turns(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_source: Dict[str, Dict[str, Any]] = {}
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[r.get("source") or "unknown"].append(r)
    for source, items in sorted(groups.items()):
        by_source[source] = {
            "count": len(items),
            "outcomes": dict(Counter(i.get("outcome") or "unknown" for i in items)),
            "ttft_ms": _stats([i.get("ttft_ms") for i in items]),
            "total_ms": _stats([i.get("total_ms") for i in items]),
            "iterations": _stats([i.get("iterations") for i in items]),
            "tool_calls": sum(int(i.get("tool_calls") or 0) for i in items),
            "tool_failures": sum(int(i.get("tool_failures") or 0) for i in items),
            "memories_retrieved_avg": _avg([i.get("memories_retrieved") for i in items]),
            "memories_used_avg": _avg([i.get("memories_used") for i in items]),
            "memories_extracted_avg": _avg([i.get("memories_extracted") for i in items if i.get("memories_extracted") is not None]),
            "turns_extracting_nothing": sum(1 for i in items if i.get("memories_extracted") == 0),
            "repeat_of_previous": sum(1 for i in items if i.get("repeat_of_previous")),
            "abandoned_previous": sum(1 for i in items if i.get("abandoned_previous")),
        }
    errors = Counter((r.get("error_class") or "unknown") for r in rows if r.get("outcome") == "error")
    return {
        "total": len(rows),
        "outcomes": dict(Counter(r.get("outcome") or "unknown" for r in rows)),
        "by_source": by_source,
        "iterations": _stats([r.get("iterations") for r in rows]),
        "exhausted": sum(1 for r in rows if r.get("outcome") == "exhausted"),
        "errors": [{"error_class": k, "count": v} for k, v in errors.most_common(TOP_N)],
    }


def _avg(values: List[Optional[float]]) -> Optional[float]:
    xs = [float(v) for v in values if v is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def summarize_tool_failures(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    calls = failures = 0
    top: Counter = Counter()
    by_tool: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "failures": 0})
    for r in rows:
        raw = r.get("tools_json")
        if not raw:
            continue
        try:
            tools = json.loads(raw)
        except Exception:
            continue
        if not isinstance(tools, list):
            continue
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = str(t.get("n") or "?")
            calls += 1
            by_tool[name]["calls"] += 1
            if not t.get("ok"):
                failures += 1
                by_tool[name]["failures"] += 1
                top[(name, str(t.get("err") or "unknown"))] += 1
    return {
        "calls": calls,
        "failures": failures,
        "top": [{"tool": n, "error_class": e, "count": c} for (n, e), c in top.most_common(TOP_N)],
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1]["failures"])),
    }


def observation_trend(hits: List[Dict[str, Any]], days: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    day_keys = [(now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    index = {d: i for i, d in enumerate(day_keys)}
    by_kind: Dict[str, List[int]] = {}
    for h in hits:
        seen = h.get("seen_at")
        if not isinstance(seen, datetime):
            continue
        i = index.get(seen.strftime("%Y-%m-%d"))
        if i is None:
            continue
        by_kind.setdefault(h.get("kind") or "unknown", [0] * days)[i] += 1
    return {"days": day_keys, "by_kind": by_kind}


def summarize_signals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind = Counter(r.get("kind") or "unknown" for r in rows)
    today: Counter = Counter()
    for r in rows:
        if r.get("kind") != "today_action":
            continue
        try:
            d = json.loads(r.get("detail_json") or "{}")
        except Exception:
            d = {}
        today[(str(d.get("need_kind") or "?"), str(d.get("action") or "?"))] += 1
    return {
        "by_kind": dict(by_kind),
        "today_actions": [{"need_kind": k, "action": a, "count": c} for (k, a), c in today.most_common(TOP_N)],
    }


def _loads(raw: Any) -> Dict[str, Any]:
    try:
        d = json.loads(raw) if raw else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def summarize_events(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The ``ledger_events`` table by kind (see ``services/event_ledger.py``)."""
    by_kind: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_kind[r.get("kind") or "unknown"].append(r)

    gate = by_kind.get("rate_gate", [])
    callers = Counter(r.get("source") or "unknown" for r in gate)
    reasons = Counter((r.get("reason") or "")[:60] for r in gate)

    sched = by_kind.get("scheduled_event", [])
    sched_details = [_loads(r.get("detail_json")) for r in sched]

    browser = by_kind.get("browser", [])
    browser_top = Counter((r.get("source") or "?", (r.get("reason") or "")[:80]) for r in browser)

    decisions = by_kind.get("decision", [])
    pulse = Counter(r.get("outcome") or "?" for r in decisions if r.get("source") == "pulse")
    recent = sorted(decisions, key=lambda r: r.get("created_at") or datetime.min, reverse=True)[:5]

    voice = [_loads(r.get("detail_json")) for r in by_kind.get("voice_session", [])]

    def _sum(key: str) -> int:
        return sum(int(v.get(key) or 0) for v in voice)

    return {
        "rate_gate": {
            "deferrals": len(gate),
            "by_caller": [{"caller": c, "count": n} for c, n in callers.most_common(TOP_N)],
            "reasons": [{"reason": c, "count": n} for c, n in reasons.most_common(5)],
        },
        "scheduled_events": {
            "fired": len(sched),
            "outcomes": dict(Counter(r.get("outcome") or "?" for r in sched)),
            "delivered": sum(1 for d in sched_details if d.get("delivered")),
            "retried": sum(1 for d in sched_details if int(d.get("attempts") or 0) > 1),
            "errors": [{"error_class": c, "count": n} for c, n in Counter((r.get("reason") or "unknown") for r in sched if r.get("outcome") == "failed").most_common(5)],
        },
        "browser": {
            "failures": len(browser),
            "top": [{"action": a, "reason": why, "count": n} for (a, why), n in browser_top.most_common(TOP_N)],
        },
        "decisions": {
            "pulse": dict(pulse),
            "recent": [
                {"source": r.get("source"), "outcome": r.get("outcome"), "reason": r.get("reason"),
                 "at": r["created_at"].isoformat() + "Z" if isinstance(r.get("created_at"), datetime) else None}
                for r in recent
            ],
        },
        "voice": {
            "sessions": len(voice),
            "turns": _sum("turns"), "bridges": _sum("bridges"), "barge_ins": _sum("barge_ins"),
            "interrupts": _sum("interrupts"), "restarts": _sum("restarts"), "errors": _sum("errors"),
            "duration_s": _stats([v.get("duration_s") for v in voice]),
        },
    }


_FRAME_RE = re.compile(r'^\s+File "([^"]+)", line \d+, in (\S+)')


def summarize_log_text(text: str) -> Dict[str, Any]:
    """Tracebacks in the backend log, ranked by innermost Edward frame + error type."""
    tracebacks = 0
    top: Counter = Counter()
    in_tb = False
    where: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("Traceback (most recent call last):"):
            tracebacks += 1
            in_tb, where = True, None
            continue
        if not in_tb:
            continue
        m = _FRAME_RE.match(line)
        if m:
            path, func = m.group(1), m.group(2)
            if "/edward/backend/" in path and "/.venv/" not in path:
                where = f"{path.split('/edward/backend/', 1)[1]}:{func}"
            continue
        if line.startswith((" ", "\t")) or not line.strip():
            continue
        error = line.split(":", 1)[0].strip()[:80] or "unknown"
        top[(where or "unknown", error)] += 1
        in_tb = False
    return {
        "tracebacks": tracebacks,
        "top": [{"where": w, "error": e, "count": n} for (w, e), n in top.most_common(TOP_N)],
        "http_429": text.count("HTTP 429"),
        "codex_limit": text.count("CODEX_LIMIT"),
        "gate_deferrals": text.count("LLMRateLimitDeferred"),
    }


def summarize_wakes(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The mind's wakes (ledger kind ``mind``): by reason and outcome."""
    rows = [e for e in events if e.get("kind") == "mind"]
    by_source: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_source[r.get("source") or "?"][r.get("outcome") or "?"] += 1
    return {
        "wakes": len(rows),
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "by_outcome": dict(Counter(r.get("outcome") or "?" for r in rows)),
    }


def _error_class_text(text: Optional[str]) -> str:
    return (text or "unknown").split("\n", 1)[0].split(":", 1)[0].strip()[:80] or "unknown"


def summarize_workers(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    durations = []
    for r in rows:
        a, b = r.get("started_at"), r.get("completed_at")
        if isinstance(a, datetime) and isinstance(b, datetime):
            durations.append((b - a).total_seconds())
    errors = Counter(_error_class_text(r.get("error")) for r in rows if r.get("status") == "failed")
    return {
        "tasks": len(rows),
        "by_status": dict(Counter(r.get("status") or "?" for r in rows)),
        "by_type": dict(Counter(r.get("task_type") or "?" for r in rows)),
        "duration_s": _stats(durations),
        "errors": [{"error_class": c, "count": n} for c, n in errors.most_common(5)],
    }


def summarize_memory_report(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The daily memory report (``scripts/memory_health_report.py``) writes
    a ``memory_report`` ledger row instead of pushing the owner a link
    (2026-10-11): the newest row's time, outcome and summary counts, and how
    many ran in the window. None when none ran."""
    rows = [e for e in events if e.get("kind") == "memory_report"]
    if not rows:
        return None
    newest = max(rows, key=lambda r: r.get("created_at") or datetime.min)
    at = newest.get("created_at")
    return {
        "at": at.isoformat() + "Z" if isinstance(at, datetime) else None,
        "outcome": newest.get("outcome"),
        "reason": newest.get("reason"),
        "summary": _loads(newest.get("detail_json")),
        "reports": len(rows),
    }


def summarize_mind(events: List[Dict[str, Any]], *, days: int, wake_tokens: Optional[List[Optional[float]]] = None,
                   verdicts: Optional[List[Dict[str, Any]]] = None, open_intentions: int = 0,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """What a health check asks of the mind (Phase 12): wakes per day by
    reason, tokens per wake, actions by tool, the left-alone share, the
    owner's verdicts, open intentions."""
    now = now or datetime.utcnow()
    rows = [e for e in events if e.get("kind") == "mind"]
    per_day: Dict[str, Counter] = defaultdict(Counter)
    tools: Counter = Counter()
    for r in rows:
        at = r.get("created_at")
        day = at.date().isoformat() if isinstance(at, datetime) else str(at or "?")[:10]
        per_day[day][r.get("source") or "?"] += 1
        for t in _loads(r.get("detail_json")).get("tools") or []:
            tools[str(t)] += 1
    counted_days = max(1, min(days, len(per_day)) if per_day else days)
    outcomes = Counter(r.get("outcome") or "?" for r in rows)
    return {
        "wakes": len(rows),
        "per_day": {d: dict(c) for d, c in sorted(per_day.items())},
        "wakes_per_day": round(len(rows) / counted_days, 1),
        "by_outcome": dict(outcomes),
        "left_alone_share": round(outcomes.get("left_alone", 0) / len(rows), 2) if rows else None,
        "actions_by_tool": dict(tools.most_common(15)),
        "tokens_per_wake": _stats(list(wake_tokens or [])),
        "verdicts": dict(Counter(_loads(v.get("detail_json")).get("verdict") or "?" for v in (verdicts or [])
                                 if v.get("kind") == "mind_verdict")),
        "open_intentions": int(open_intentions),
    }


def compose_report(
    *,
    days: int,
    turns: List[Dict[str, Any]],
    tool_rows: List[Dict[str, Any]],
    hits: List[Dict[str, Any]],
    observations: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    storage: Dict[str, Any],
    events: Optional[List[Dict[str, Any]]] = None,
    workers: Optional[List[Dict[str, Any]]] = None,
    log_text: Optional[str] = None,
    now: Optional[datetime] = None,
    wake_tokens: Optional[List[Optional[float]]] = None,
    open_intentions: int = 0,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    return {
        # settings.timezone vs the host's zone (2026-09-24): with them apart
        # every tick fires at the wrong hour (scheduler_service.check_timezone).
        "config": dict(config or {}),
        "generated_at": now.isoformat() + "Z",
        "window_days": days,
        "turns": summarize_turns(turns),
        "tools": summarize_tool_failures(tool_rows),
        "observations": {
            "trend": observation_trend(hits, days, now=now),
            "open": observations[:25],
        },
        "signals": summarize_signals(signals),
        "events": summarize_events(events or []),
        "wakes": summarize_wakes(events or []),
        "mind": summarize_mind(events or [], days=days, wake_tokens=wake_tokens, verdicts=signals,
                               open_intentions=open_intentions, now=now),
        "workers": summarize_workers(workers or []),
        "memory_report": summarize_memory_report(events or []),
        "log": summarize_log_text(log_text or ""),
        "storage": storage,
    }


# ------------------------------------------------------------------ I/O --

async def build_report(days: int = 7) -> Dict[str, Any]:
    from sqlalchemy import select, text
    from services.database import (
        async_session, TurnLogModel, SelfObservationHitModel, SelfObservationModel,
        LedgerEventModel, OrchestratorTaskModel,
    )
    from services import user_signals_service as us

    since = datetime.utcnow() - timedelta(days=days)
    async with async_session() as session:
        turn_rows = (
            await session.execute(select(TurnLogModel).where(TurnLogModel.started_at >= since))
        ).scalars().all()
        turns = [_row_dict(r) for r in turn_rows]

        hit_rows = (
            await session.execute(
                select(SelfObservationHitModel.seen_at, SelfObservationModel.kind)
                .join(SelfObservationModel, SelfObservationModel.id == SelfObservationHitModel.observation_id)
                .where(SelfObservationHitModel.seen_at >= since)
            )
        ).all()
        hits = [{"seen_at": seen, "kind": kind} for seen, kind in hit_rows]

        obs_rows = (
            await session.execute(
                select(SelfObservationModel)
                .where(SelfObservationModel.status == "open")
                .where(SelfObservationModel.last_seen_at >= since)
                .order_by(SelfObservationModel.last_seen_at.desc())
                .limit(25)
            )
        ).scalars().all()
        observations = [
            {
                "kind": o.kind, "source": o.source, "signature": o.signature,
                "description": (o.description or "")[:200], "occurrences": o.occurrences,
                "last_seen_at": o.last_seen_at.isoformat() + "Z" if o.last_seen_at else None,
            }
            for o in obs_rows
        ]

        event_rows = (
            await session.execute(select(LedgerEventModel).where(LedgerEventModel.created_at >= since))
        ).scalars().all()
        events = [_row_dict(r) for r in event_rows]
        task_rows = (
            await session.execute(
                select(OrchestratorTaskModel.status, OrchestratorTaskModel.task_type, OrchestratorTaskModel.started_at,
                       OrchestratorTaskModel.completed_at, OrchestratorTaskModel.error)
                .where(OrchestratorTaskModel.created_at >= since)
            )
        ).all()
        workers = [
            {"status": s, "task_type": t, "started_at": a, "completed_at": b, "error": e}
            for s, t, a, b, e in task_rows
        ]

        storage: Dict[str, Any] = {}
        try:
            for table in ("turn_log", "self_observation_hits", "user_signals", "ledger_events", "self_observations",
                          "codex_calls", "llm_meter_samples", "llm_calls"):
                size = (await session.execute(text(f"SELECT pg_total_relation_size('{table}')"))).scalar_one()
                storage[table] = int(size)
            storage["database"] = int((await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one())
        except Exception as exc:
            storage["error"] = str(exc)[:120]
    try:
        import shutil

        storage["disk_free_gb"] = shutil.disk_usage("/").free // (1024 ** 3)
    except Exception:
        pass

    signals = await us.recent_signals(days)
    wake_tokens: List[Optional[float]] = []
    open_intentions = 0
    try:
        from services.database import UsageEventModel
        from services.mind_service import MIND_CONVERSATION_ID, open_intentions as _open_intentions

        async with async_session() as session:
            wake_tokens = [float(v or 0) for v in (await session.execute(
                select(UsageEventModel.input_tokens).where(UsageEventModel.conversation_id == MIND_CONVERSATION_ID,
                                                            UsageEventModel.created_at >= since)
            )).scalars().all()]
        open_intentions = len(await _open_intentions())
    except Exception as exc:  # noqa: BLE001
        print(f"[health] mind section incomplete: {exc}")
    config: Dict[str, Any] = {}
    try:
        from services.scheduler_service import current_timezone_config

        config.update(await current_timezone_config())
    except Exception as exc:  # noqa: BLE001
        config["error"] = str(exc)[:120]
    report = compose_report(
        days=days, turns=turns, tool_rows=turns, hits=hits, observations=observations,
        signals=signals, storage=storage, events=events,
        workers=workers, log_text=_read_log_tail(), wake_tokens=wake_tokens, open_intentions=open_intentions,
        config=config,
    )
    # Trust at the boundary (plan Phase 6, 2026-09-24): tainted turns, what
    # the gate would have held, egress it blocked, approvals. Read this after
    # a shadow week before turning EDWARD_TAINT_APPROVALS on.
    try:
        from services import taint_service
        report["trust"] = await taint_service.build_taint_report(days)
    except Exception as exc:  # noqa: BLE001 — one section must not sink the report
        report["trust"] = {"error": str(exc)[:120]}
    # The owner's calendar (2026-10-11): which source served it last — sqlite
    # (Calendar's own database), mcp (apple-mcp) or unavailable — plus the
    # EventKit helper's access (a subprocess, up to 5 s) and Reminders: off
    # the event loop.
    import asyncio

    report["calendar"] = await asyncio.to_thread(calendar_section)
    # Proactivity (2026-10-11): the trust line and a verdict — a dead
    # noticer must not look like a quiet day.
    report["proactivity"] = await proactivity_section(days)
    # Pings (2026-10-11): did every @Edward, self-chat message and
    # reply to Edward get an answer in its chat, and how fast. Only the
    # error's class and first line: a database error's DETAIL carries a row.
    try:
        from services.pings import hooks as pings_hooks

        report["pings"] = await pings_hooks.report(days)
    except Exception as exc:  # noqa: BLE001
        from services.pings.hooks import brief_error

        report["pings"] = {"error": brief_error(exc)}
    # Mail (2026-10-11): Mail.app running, Mail's index fresh, each listener's
    # last clean poll, Gmail signed in — a dead mail flow must not look like
    # a quiet inbox.
    report["email"] = await email_section()
    # Pay per token (2026-10-10): today's Claude spend against the daily cap,
    # with a warning past 80 % of it (services/llm_spend.py).
    try:
        from services import llm_spend

        block = await llm_spend.claude_block()
        report["spend"] = {"claude": llm_spend.headline(block) if "error" not in block else block}
    except Exception as exc:  # noqa: BLE001
        report["spend"] = {"claude": {"error": str(exc)[:120]}}
    return report


def calendar_section() -> Dict[str, Any]:
    """Which source served the calendar last, plus what macOS lets the
    EventKit helper do (``eventkit``: installed, calendar / reminders
    access — the helper's ``access`` never prompts) and how Reminders were
    read last (``reminders``). One part failing never hides the others."""
    try:
        from services import calendar_service

        out: Dict[str, Any] = dict(calendar_service.status())
    except Exception as exc:  # noqa: BLE001
        out = {"error": str(exc)[:120]}
    try:
        from services import eventkit_service

        out["eventkit"] = eventkit_service.access_status()
    except Exception as exc:  # noqa: BLE001
        out["eventkit"] = {"error": str(exc)[:120]}
    try:
        from services import reminders_service

        out["reminders"] = reminders_service.status()
    except Exception as exc:  # noqa: BLE001
        out["reminders"] = {"error": str(exc)[:120]}
    return out


async def proactivity_section(days: int) -> Dict[str, Any]:
    """``noticing_service.proactivity_report(days)``: the mode, the trust
    line (pings answered vs noticed / spoken / pushed / ignored), the
    per-day counts, the stalls and a verdict. Until that module is merged,
    or when it fails, the section says why with ``verdict: "unknown"`` —
    it never sinks the report, and never pretends to be ``ok``."""
    mode = (os.environ.get("EDWARD_PROACTIVITY") or "off").strip().lower() or "off"
    try:
        from services import noticing_service
    except ImportError:
        return {"mode": mode, "verdict": "unknown", "error": "noticing_service is not merged"}
    try:
        return await noticing_service.proactivity_report(days)
    except Exception as exc:  # noqa: BLE001 — one section must not sink the report
        return {"mode": mode, "verdict": "unknown", "error": f"{type(exc).__name__}: {exc}"[:200]}


LOG_PATH = os.environ.get("EDWARD_BACKEND_LOG", "/tmp/edward-backend.log")
LOG_TAIL_BYTES = 20 * 1024 * 1024


def _read_log_tail(path: str = LOG_PATH, max_bytes: int = LOG_TAIL_BYTES) -> str:
    """The current backend log (last ``max_bytes``); it rotates at 100 MB."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            return fh.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _row_dict(r) -> Dict[str, Any]:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}


async def email_section() -> Dict[str, Any]:
    """``mail_service.email_block()``; when it fails, ``verdict: unknown``
    and why — it never sinks the report."""
    try:
        from services import mail_service

        return await mail_service.email_block()
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "unknown", "error": f"{type(exc).__name__}: {exc}"[:200]}
