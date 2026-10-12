"""Noticing: Edward reads what comes in, notices the few things that matter,
and a pure gate decides whether anything reaches the owner (2026-10-11).

Why (docs/architecture/noticing.md): Edward's proactivity used to be a
schedule — morning and evening ticks, a surplus queue, a ``messages`` wake
on Opus for every inbound batch (≈ $6.8/day) — and 0 of 33 pushes were
opened. The catches the owner valued (a flight's check-in closing, a lost
item waiting at an airport desk, a credential pasted into a chat) were
*noticings*: two facts that disagreed, a deadline closing, someone waiting
on the owner. So:

  inbound batch → ``request_notice`` → (code pre-filter) → the noticer
  (the background model, ``mind:notice``) → ``noticings`` rows → ``speak_gate.decide``
  (pure) → silent | hold | journal | note | ambient | push | thread → the
  speaker (an Opus ``speak`` wake) → ``reach_service.tell_owner``.

``EDWARD_PROACTIVITY`` (read at call time) picks how much of that runs:

* ``off`` (the default) — store and stamp: each non-ping event is stamped
  ``stored:<pass>`` so Today never floods; no model call;
* ``shadow`` — the noticer and the gate run; each decision is recorded
  (``status='shadow'``) and nothing is said;
* ``notice`` — the whole path.

An unknown value means ``off``. Pings, mentions and the owner's self-chat lines
are never stamped here: the ping runner (or the mention wake) owns them.

This module also keeps the upkeep the ``*/5`` ``proactive_sweep`` runs
(``sweep``: held items due, failed passes retried, outcomes, expiry, a
30-day prune), the code-written digest the nightly and the speaker read,
and ``proactivity_report`` — the one place to see whether any of it works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services import llm_utils  # the noticer's model is looked up here (tests stub it)
from services.owner import owner_name, owner_text

logger = logging.getLogger(__name__)

MODE_ENV = "EDWARD_PROACTIVITY"
MODES = ("off", "shadow", "notice")
PUSH_SCOPE_ENV = "EDWARD_NOTICE_PUSH"
PUSH_SCOPES = ("floor", "gate")
RETENTION_DAYS = 30
ESCALATION_LOOKBACK_H = 24
STALL_INBOUND_6H = 10
STALL_SWEEP_MIN = 15
FAILING_MIN_PASSES = 4
SPEAK_FAILURES_24H = 2
CODE_ORIGINS = ("care", "code", "sweep")

# Kept in sync with pings.detect.MENTION_RE; used when that module
# is not importable.
_MENTION_FALLBACK = re.compile(r"(?<![\w.@])@edward\b", re.I)

_STARTED_AT = datetime.now(timezone.utc)
_last_sweep_at: Optional[datetime] = None
_last_prune_day: Optional[date] = None
_TASKS: "set[asyncio.Task]" = set()
_warned: "set[str]" = set()


# ---------------------------------------------------------------- flags --

def mode() -> str:
    """``off`` | ``shadow`` | ``notice`` from ``EDWARD_PROACTIVITY``; an
    unknown value means ``off`` (warned once per value)."""
    raw = (os.getenv(MODE_ENV) or "off").strip().lower()
    if raw in MODES:
        return raw
    if raw not in _warned:
        _warned.add(raw)
        logger.warning("%s=%r is not one of %s — noticing is off", MODE_ENV, raw, "|".join(MODES))
    return "off"


def push_scope() -> str:
    """``floor`` (the default: only travel or household closing within 3 h
    may push) or ``gate`` (the category ladder decides)."""
    raw = (os.getenv(PUSH_SCOPE_ENV) or "floor").strip().lower()
    return raw if raw in PUSH_SCOPES else "floor"


def noticer_available() -> bool:
    """Whether the noticer pass is built. Until it is, every mode stores
    and stamps."""
    return callable(globals().get("notice_pass"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _brief(exc: BaseException) -> str:
    first = (str(exc).splitlines() or [""])[0]
    return f"{type(exc).__name__}: {first[:160]}"


def _raw(row: Any) -> Dict[str, Any]:
    try:
        data = json.loads(getattr(row, "raw_data", None) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _mention_re():
    try:
        from services.pings.detect import MENTION_RE

        return MENTION_RE
    except Exception:  # noqa: BLE001
        return _MENTION_FALLBACK


def is_ping_event(row: Any) -> bool:
    """Never stamped, never noticed: a ping (status or marker), a mention
    (the marker or ``@edward`` anywhere in the text) or the owner's self-chat line.
    The ping runner or the mention wake owns these; the mention wake reads
    "still waiting" from ``notified_at``. Only an iMessage can be one
    (2026-10-11): an email or calendar row with ``@edward`` in it gets no
    mention wake, so it is noticed like any other event. A row without a
    source (the health report's ``_Row``) is read as an iMessage."""
    if (getattr(row, "triage_status", None) or "") == "ping":
        return True
    if (getattr(row, "source", None) or "imessage") != "imessage":
        return False
    raw = _raw(row)
    if raw.get("ping") or raw.get("owner_direct") or raw.get("has_mentions") or raw.get("has_owner_direct"):
        return True
    text = " ".join(str(x) for x in (getattr(row, "summary", None), raw.get("full_text")) if x)
    return bool(_mention_re().search(text))


# ---------------------------------------------------------- the hand-off --

def request_notice(event_ids: Optional[Iterable[Any]] = None, *, source: str = "imessage") -> bool:
    """The hand-off for inbound events (the listeners) and the ``messages`` shim
    (``event_ids=None``). Returns at once and never raises: the pass runs as
    a task. ``False`` when there is no running loop."""
    try:
        ids = [str(i) for i in event_ids] if event_ids is not None else None
    except Exception:  # noqa: BLE001
        ids = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    try:
        print(f"[notice] request events={len(ids) if ids is not None else 'escalated'} source={source}")
        if mode() != "off" and noticer_available():
            coro = _enqueue(ids, source=source)  # the coalesced noticer pass
        else:
            coro = _safe_stub(ids, source=source)
        task = loop.create_task(coro)
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[notice] request failed: %s", exc)
        return False


async def drain(timeout: float = 30.0) -> None:
    """Wait for the passes in flight (tests, shutdown)."""
    loop = asyncio.get_running_loop()
    for _ in range(10):
        pending = [t for t in list(_TASKS) if not t.done() and t.get_loop() is loop]
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout)


async def _safe_stub(ids: Optional[List[str]], *, source: str) -> None:
    try:
        await stub_pass(ids, source=source)
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] stub pass FAILED ({type(exc).__name__})")


async def _load_events(ids: Optional[Sequence[str]], now: datetime, *, owners_lines: bool = False) -> List[Any]:
    """The events by id; with ``ids=None`` the escalated, unstamped events of
    the last day (what the retired ``messages`` wake was shown) — and, for the
    noticer (``owners_lines``), the owner's own unstamped lines too: the
    identity filter dismisses them, but they carry the owner's commitments
    ("en route") and the credentials they paste."""
    from sqlalchemy import and_, or_, select

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        if ids is None:
            since = _naive(now) - timedelta(hours=ESCALATION_LOOKBACK_H)
            which = HeartbeatEventModel.triage_status == "escalated"
            if owners_lines:
                which = or_(which, and_(HeartbeatEventModel.is_from_user.is_(True),
                                        HeartbeatEventModel.created_at >= _naive(now) - timedelta(hours=OWNER_LINES_H)))
            stmt = select(HeartbeatEventModel).where(
                which, HeartbeatEventModel.notified_at.is_(None), HeartbeatEventModel.created_at >= since)
        else:
            if not ids:
                return []
            stmt = select(HeartbeatEventModel).where(HeartbeatEventModel.id.in_(list(ids)))
        return list((await s.execute(stmt.order_by(HeartbeatEventModel.created_at.asc()))).scalars().all())


async def _stamp(event_ids: Sequence[str], batch_id: str, now: datetime) -> int:
    """Stamp ``notified_at`` / ``notification_batch_id`` on events not yet
    stamped. Never a ping: callers pass only non-ping ids, and the update
    re-checks the status."""
    if not event_ids:
        return 0
    from sqlalchemy import or_, update

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        res = await s.execute(
            update(HeartbeatEventModel)
            .where(HeartbeatEventModel.id.in_(list(event_ids)), HeartbeatEventModel.notified_at.is_(None),
                   or_(HeartbeatEventModel.triage_status.is_(None), HeartbeatEventModel.triage_status != "ping"))
            .values(notified_at=_naive(now), notification_batch_id=batch_id[:200]))
        await s.commit()
        return int(res.rowcount or 0)


async def _ledger(*, source: str, outcome: str, ref_id: Optional[str], detail: Dict[str, Any],
                  reason: Optional[str] = None, kind: str = "notice") -> Optional[int]:
    try:
        from services.event_ledger import record_event_now

        return await record_event_now(kind=kind, source=source, outcome=outcome, reason=reason, ref_id=ref_id,
                                      detail=detail)
    except Exception:  # noqa: BLE001
        return None


async def stub_pass(event_ids: Optional[Sequence[str]] = None, *, pass_id: Optional[str] = None,
                    source: str = "imessage") -> Dict[str, Any]:
    """``off`` mode (and every mode until the noticer lands): load the
    events, set aside pings, stamp the rest ``stored:<pass>``, write one
    ledger row. ``event_ids=None`` means the escalated, unstamped events of
    the last day (what the retired ``messages`` wake used to stamp)."""
    pass_id = pass_id or str(uuid.uuid4())
    now = _utcnow()
    rows = await _load_events(event_ids, now)
    pings = [r for r in rows if is_ping_event(r)]
    keep = [r.id for r in rows if not is_ping_event(r) and r.notified_at is None]
    stamped = await _stamp(keep, f"stored:{pass_id}", now)
    detail = {"events": len(rows), "stamped": stamped, "pings": len(pings), "source": source}
    await _ledger(source="stub", outcome="ok", ref_id=pass_id, detail=detail)
    print(f"[notice] pass {pass_id[:8]} stored events={len(rows)} stamped={stamped} pings={len(pings)}")
    return {"pass_id": pass_id, **detail}


# ------------------------------------------------------------ noticings --

async def add_noticing(*, kind: str, claim: str, origin: str = "noticer", category: Optional[str] = None,
                       sources: Sequence[Dict[str, Any]] = (), chat_identifier: Optional[str] = None,
                       who: Sequence[str] = (), dedupe_key: Optional[str] = None,
                       closes_at: Optional[datetime] = None, suggested: Optional[str] = None,
                       sensitive: bool = False, status: str = "open", channel: Optional[str] = None,
                       pass_id: Optional[str] = None, surfaced_at: Optional[datetime] = None,
                       push_log_id: Optional[str] = None, note_id: Optional[str] = None,
                       due_at: Optional[datetime] = None, decision: Optional[str] = None,
                       decision_reason: Optional[str] = None, noticing_id: Optional[str] = None) -> Optional[str]:
    """Write one ``noticings`` row; its id, or ``None`` when it could not be
    written. A code-made row (``care`` / ``code`` / ``sweep``) is keyed: the
    same ``dedupe_key`` from the same origin within 30 days returns the
    existing id, so a restart never re-alerts. Never raises."""
    try:
        from sqlalchemy import select

        from services.database import NoticingModel, async_session

        now = _naive(_utcnow())
        async with async_session() as s:
            if dedupe_key and origin in CODE_ORIGINS:
                existing = (await s.execute(
                    select(NoticingModel.id).where(
                        NoticingModel.origin == origin, NoticingModel.dedupe_key == dedupe_key[:160],
                        NoticingModel.created_at >= now - timedelta(days=RETENTION_DAYS)).limit(1)
                )).scalar_one_or_none()
                if existing:
                    return existing
            nid = noticing_id or str(uuid.uuid4())
            s.add(NoticingModel(
                id=nid, created_at=now, origin=str(origin)[:16], pass_id=pass_id, kind=str(kind)[:24],
                category=(str(category)[:24] if category else None), claim=str(claim or ""),
                sensitive=bool(sensitive), sources_json=json.dumps(list(sources or []), default=str),
                chat_identifier=(str(chat_identifier)[:255] if chat_identifier else None),
                who_json=json.dumps([str(w) for w in who or []]), dedupe_key=(dedupe_key[:160] if dedupe_key else None),
                closes_at=_naive(closes_at) if closes_at else None, suggested=(suggested or None),
                status=str(status)[:16], channel=channel, surfaced_at=_naive(surfaced_at) if surfaced_at else None,
                push_log_id=push_log_id, note_id=note_id, due_at=_naive(due_at) if due_at else None,
                decision=decision, decision_reason=decision_reason,
                decided_at=now if decision else None))
            await s.commit()
            return nid
    except Exception as exc:  # noqa: BLE001
        logger.warning("[notice] add_noticing failed (%s): %s", kind, exc)
        return None


# ---------------------------------------------------------------- sweep --

def last_sweep_at() -> Optional[datetime]:
    return _last_sweep_at


async def _prune(now: datetime) -> int:
    """Noticings older than 30 days, once a local day."""
    global _last_prune_day
    try:
        from services.timezone_utils import utc_to_local

        today = utc_to_local(now).date()
    except Exception:  # noqa: BLE001
        today = now.date()
    if _last_prune_day == today:
        return 0
    from sqlalchemy import delete

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        res = await s.execute(delete(NoticingModel).where(
            NoticingModel.created_at < _naive(now) - timedelta(days=RETENTION_DAYS)))
        await s.commit()
    _last_prune_day = today
    return int(res.rowcount or 0)


async def sweep(now: Optional[datetime] = None) -> Dict[str, Any]:
    """The ``*/5`` upkeep (``proactive_sweep``; 1A routes it). Code only, no
    model call unless something it finds is due. Never raises; each step's
    failure lands in ``error``."""
    global _last_sweep_at
    now = now or _utcnow()
    out: Dict[str, Any] = {"mode": mode(), "due": 0, "retried": 0, "outcomes": 0, "expired": 0, "pruned": 0,
                           "stranded": 0}
    errors: List[str] = []
    steps = [("pruned", _prune)]
    for name in ("_sweep_intentions", "_sweep_stranded", "_sweep_due", "_sweep_retry_failed_passes",
                 "_sweep_outcomes", "_sweep_expiry"):
        fn = globals().get(name)
        if callable(fn):
            steps.append((name, fn))
    for name, fn in steps:
        try:
            got = await fn(now)
            if isinstance(got, dict):
                out.update(got)
            elif name == "pruned":
                out["pruned"] = int(got or 0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {_brief(exc)}")
    if errors:
        out["error"] = "; ".join(errors)[:400]
    _last_sweep_at = now
    return out


# --------------------------------------------------------------- digest --

def _mode_line(m: str) -> str:
    if m == "off":
        return ("Noticing is off (EDWARD_PROACTIVITY=off): messages are stored, nothing is noticed or said on "
                "your own.")
    if m == "shadow":
        return "Noticing is in shadow: the noticer and the gate run and record; nothing is said."
    return f"Noticing is on (push scope: {push_scope()})."


async def digest(now: Optional[datetime] = None) -> List[str]:
    """Code-written lines for the nightly pack, the speak pack and the health
    report: the mode, then today's counts and how the owner likes to be
    reached. No model writes them."""
    m = mode()
    lines = [_mode_line(m)]
    extra = globals().get("_digest_lines")
    if callable(extra):
        try:
            lines.extend(await extra(now or _utcnow()))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"(the noticing digest could not be read: {type(exc).__name__})")
    return lines


# --------------------------------------------------------------- report --

def _local_date(dt: datetime) -> date:
    try:
        from services.timezone_utils import utc_to_local

        return utc_to_local(_aware(dt)).date()
    except Exception:  # noqa: BLE001
        return _aware(dt).date()


DAY_FIELDS = ("passes", "failed", "parse_failures", "deferred", "noticed", "held", "silent", "shadow", "spoken",
              "pushes", "notes", "ambient", "thread", "acted", "opened", "seen", "ignored", "dismissed",
              "speak_failures", "care_alerts", "fallback_pushes", "autonomous_pushes")


def _pings_half(rep: Optional[Dict[str, Any]], err: Optional[str]) -> str:
    if err:
        return f"pings: ({err})"
    if not isinstance(rep, dict) or rep.get("error"):
        return f"pings: ({(rep or {}).get('error', 'unavailable')})"
    by = rep.get("by_status") or {}
    total = int(rep.get("total") or sum(int(v or 0) for v in by.values()))
    answered = int(by.get("answered") or 0) + int(by.get("fallback") or 0)
    within = int(rep.get("answered_within_2m") or 0)
    late = max(0, answered - within)
    unanswered = max(0, total - answered - int(by.get("legacy") or 0) - int(by.get("declined") or 0))
    return f"pings: {within}/{total} answered ≤2 min, {late} late, {unanswered} unanswered"


async def _pings_report(days: int) -> tuple:
    try:
        import importlib

        pings_hooks = importlib.import_module("services.pings.hooks")
    except Exception:  # noqa: BLE001
        return None, "the ping runner is not installed"
    try:
        return await pings_hooks.report(days), None
    except Exception as exc:  # noqa: BLE001
        return None, f"unavailable: {type(exc).__name__}"


def _care_check_at() -> Optional[str]:
    """When ``care_watch`` last checked, from its ``last_check()``
    (``{"at": <ISO UTC>, "result": …}``); None before the first check in
    this process or when unreadable."""
    try:
        from services import care_watch

        v = care_watch.last_check().get("at")
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        return _aware(v).isoformat() if isinstance(v, datetime) else None
    except Exception:  # noqa: BLE001
        return None


async def proactivity_report(days: int = 7, now: Optional[datetime] = None) -> Dict[str, Any]:
    """``report["proactivity"]`` in ``GET /api/health/report``.
    Never raises: a failure is an ``error`` and a ``failing`` verdict."""
    now = now or _utcnow()
    m = mode()
    out: Dict[str, Any] = {"mode": m, "push_scope": push_scope()}
    try:
        out.update(await _report_body(days, now, m))
    except Exception as exc:  # noqa: BLE001
        out["error"] = _brief(exc)
        out["verdict"] = "failing"
    return out


async def _report_body(days: int, now: datetime, m: str) -> Dict[str, Any]:
    from sqlalchemy import select

    from services.database import (HeartbeatEventModel, LedgerEventModel, NoticingModel, PushLogModel,
                                   async_session)

    days = max(1, int(days or 7))
    today = _local_date(now)
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    since = _naive(now) - timedelta(days=days + 1)
    six_h = _naive(now) - timedelta(hours=6)
    async with async_session() as s:
        noticings = (await s.execute(select(
            NoticingModel.origin, NoticingModel.status, NoticingModel.channel, NoticingModel.outcome,
            NoticingModel.created_at, NoticingModel.updated_at, NoticingModel.due_at, NoticingModel.closes_at,
            NoticingModel.surfaced_at, NoticingModel.category).where(NoticingModel.created_at >= since))).all()
        ledger = (await s.execute(select(
            LedgerEventModel.kind, LedgerEventModel.source, LedgerEventModel.outcome, LedgerEventModel.detail_json,
            LedgerEventModel.created_at).where(
                LedgerEventModel.kind.in_(("notice", "reach", "care_alert")),
                LedgerEventModel.created_at >= since))).all()
        pushes = (await s.execute(select(PushLogModel.source, PushLogModel.sent_at).where(
            PushLogModel.source.like("reach:%"), PushLogModel.sent > 0, PushLogModel.held_reason.is_(None),
            PushLogModel.sent_at >= since))).all()
        inbound_rows = (await s.execute(select(
            HeartbeatEventModel.id, HeartbeatEventModel.triage_status, HeartbeatEventModel.raw_data,
            HeartbeatEventModel.summary).where(
                HeartbeatEventModel.created_at >= six_h, HeartbeatEventModel.is_from_user.is_not(True)))).all()

    from services.reach_service import EXEMPT_SOURCES

    buckets: Dict[date, Dict[str, int]] = {d: {f: 0 for f in DAY_FIELDS} for d in day_list}

    def bump(when: Optional[datetime], key: str, n: int = 1) -> None:
        if when is None:
            return
        b = buckets.get(_local_date(when))
        if b is not None:
            b[key] += n

    for kind, source, outcome, detail_json, created in ledger:
        if kind == "notice":
            bump(created, "passes")
            if outcome == "failed":
                bump(created, "failed")
                try:
                    if (json.loads(detail_json or "{}") or {}).get("error_class") == "parse":
                        bump(created, "parse_failures")
                except (TypeError, ValueError):
                    pass
            elif outcome == "deferred":
                bump(created, "deferred")
        elif kind == "care_alert" and outcome == "pushed":
            bump(created, "care_alerts")      # care_watch's ledger row is the one that counts
        elif kind == "reach" and source == "fallback" and outcome == "push":
            bump(created, "fallback_pushes")
    for source, sent_at in pushes:
        if source not in EXEMPT_SOURCES:
            bump(sent_at, "autonomous_pushes")
    for origin, status, channel, outcome, created, *_rest in noticings:
        if origin == "care":
            continue                          # counted from care_watch's own ledger row above
        bump(created, "noticed")
        if status in ("held", "silent", "shadow"):
            bump(created, status)
        elif status == "spoken":
            bump(created, "spoken")
            key = {"push": "pushes", "note": "notes", "ambient": "ambient", "thread": "thread"}.get(channel or "")
            if key:
                bump(created, key)
        elif status == "failed":
            bump(created, "speak_failures")
        if outcome in ("acted", "opened", "seen", "ignored", "dismissed"):
            bump(created, outcome)

    day_rows = [{"day": d.isoformat(), **buckets[d]} for d in day_list]
    tot = {f: sum(r[f] for r in day_rows) for f in DAY_FIELDS}

    # Stalls: the things a dead noticer would look like.
    naive_now = _naive(now)
    inbound_6h = sum(1 for _id, st, raw, summ in inbound_rows
                     if (st or "") not in ("ping", "dismissed")
                     and not is_ping_event(_Row(st, raw, summ)))
    passes_6h = sum(1 for k, *_x, created in ledger if k == "notice" and created >= six_h)
    failed_6h = sum(1 for k, _s, o, _d, created in ledger if k == "notice" and o == "failed" and created >= six_h)
    held_past_due = sum(1 for _o, st, _c, _out, _cr, _up, due, *_x in noticings
                        if st == "held" and due is not None and due < naive_now - timedelta(minutes=STALL_SWEEP_MIN))
    ambient_past_expiry = 0
    for _o, st, ch, _out, _cr, _up, _due, closes, surfaced, _cat in noticings:
        if st == "spoken" and ch == "ambient":
            expiry = closes or ((surfaced + timedelta(hours=2)) if surfaced else None)
            if expiry is not None and expiry < naive_now - timedelta(minutes=STALL_SWEEP_MIN):
                ambient_past_expiry += 1
    speak_failures_24h = sum(1 for _o, st, _c, _out, cr, up, *_x in noticings
                             if st == "failed" and (up or cr) >= naive_now - timedelta(hours=24))
    swept = _last_sweep_at
    sweep_stale = (swept is None and now - _STARTED_AT > timedelta(minutes=STALL_SWEEP_MIN)) or (
        swept is not None and now - _aware(swept) > timedelta(minutes=STALL_SWEEP_MIN))

    if sweep_stale or (m != "off" and inbound_6h >= STALL_INBOUND_6H and passes_6h == 0):
        verdict = "stalled"
    elif m == "off":
        verdict = "off"
    elif ((passes_6h >= FAILING_MIN_PASSES and failed_6h * 2 >= passes_6h) or held_past_due > 0
          or ambient_past_expiry > 0 or speak_failures_24h >= SPEAK_FAILURES_24H):
        verdict = "failing"
    else:
        verdict = "ok"

    rep, err = await _pings_report(days)
    trust = (f"{_pings_half(rep, err)} · proactive: {tot['noticed']} noticed / {tot['spoken']} spoken / "
             f"{tot['pushes']} pushed / {tot['ignored']} ignored")
    ratio = f"{tot['noticed'] / tot['spoken']:.1f}:1" if tot["spoken"] else "–"
    headline = (f"{days} days: {tot['passes']} noticer passes ({tot['failed']} failed, {tot['deferred']} "
                f"deferred), {tot['noticed']} noticed, {tot['spoken']} spoken, ratio {ratio}")
    categories: Dict[str, Any] = {}
    cats = globals().get("_category_levels")
    if callable(cats):
        try:
            categories = await cats(now)
        except Exception as exc:  # noqa: BLE001
            categories = {"error": _brief(exc)}
    return {
        "trust": trust,
        "headline": headline,
        "days": day_rows,
        "categories": categories,
        "stalls": {"inbound_6h": inbound_6h, "passes_6h": passes_6h, "failed_6h": failed_6h,
                   "held_past_due": held_past_due, "ambient_past_expiry": ambient_past_expiry,
                   "speak_failures_24h": speak_failures_24h,
                   "last_sweep_at": _aware(swept).isoformat() if swept else None,
                   "last_care_check_at": _care_check_at()},
        "verdict": verdict,
    }


class _Row:
    """Just enough of a heartbeat event for ``is_ping_event``."""

    __slots__ = ("triage_status", "raw_data", "summary")

    def __init__(self, triage_status: Optional[str], raw_data: Optional[str], summary: Optional[str]) -> None:
        self.triage_status = triage_status
        self.raw_data = raw_data
        self.summary = summary


# =====================================================================
# The noticer (shadow / notice)
# =====================================================================

NOTICER_PROMPT = owner_text("""You read {OWNER}'s messages for Edward, their assistant, and write private notes ("noticings") about the few
things that could matter to {OWNER}. Only Edward's own code reads your output. Most batches have nothing in them:
answer {"noticings": []}.

A noticing is one of:
- conflict: two facts {OWNER} owns disagree — a vendor's time against what {OWNER} said they are doing, two bookings
  that overlap, a plan and a message that contradicts it. Cite both sources.
- deadline: something {OWNER} must do that closes at a time, with nothing showing they have done it.
- unanswered: someone is waiting on an answer only {OWNER} can give, and it matters (a request, plans, money, work).
  Not banter, not "ok", not a question someone already answered.
- security: a login code {OWNER} didn't ask for, a credential pasted into a message, a suspicious account alert.
- household: something time-bounded at home that whoever is home should know (a delivery or service arriving
  within the hour, a gate left open).
- follow_through: something Edward himself promised in a chat and hasn't done.

Never a noticing:
- what a message says, when {OWNER} can read it themselves. "Parker asks for $20" is a relay, not a noticing.
- confirmations and logistics (shipped, delivered, your order is ready, lunch is on shelf B4, your ride is
  here) unless they conflict with something or close soon.
- jokes, memes, reactions, group chatter, newsletters, marketing.
- anything under "Coming up" by itself — Edward's intentions, scheduled events, {OWNER}'s calendar (a bill, an
  appointment, a holiday): it is background, never a source and never a noticing on its own; {OWNER} and Edward
  put it there. It only helps you judge a message (a time that clashes, the trip a text is about).

"category" is exactly one of these words:
- travel: flights, trains, check-in, boarding, gates, hotels, rental cars — anything about a trip. A check-in or
  boarding deadline is travel, never logistics.
- household: the home and whoever is in it — pets, a delivery or a service arriving, the house, the gate.
- money: bills, payments, refunds, banking.
- work: {OWNER}'s job and the people they work with.
- social: friends, plans, invitations.
- family: {OWNER}'s family.
- security: codes, credentials, account and login alerts.
- health: health, doctors, prescriptions.
- logistics: errands and orders that are none of the above (a pickup, a ride, an order ready).

Rules:
- Cite evidence: "sources" are only the hb:… ids of message lines in the pack — nothing else is evidence. A
  conflict needs two. {OWNER}'s own lines from other chats are evidence too — that is how you see "you said
  you're en route".
- "claim": one or two plain sentences with names and times as the messages give them. Never copy a password,
  code or key; say "a code" or "what looks like a key".
- "closes_at": when it stops mattering, ISO 8601 with the offset, or null.
- "suggested" is exactly one of push, ambient, note, thread, none: push only if {OWNER} would want their phone to
  buzz for it now; ambient for household things everyone at home should see; note otherwise; thread only for
  follow_through in a chat that asked Edward; none if unsure.
- "dedupe_key": short and stable for the same underlying thing ("NW482-checkin-2026-10-03"). If the pack
  already lists it as open, skip it unless something new happened — then reuse its key.
- "sensitive": true for health, money trouble, family conflict, anything private.
Output only JSON: {"noticings": [ … ]} with the fields kind, category, claim, sources, who, closes_at,
suggested, dedupe_key, sensitive.""")

SPEAK_SYSTEM_PROMPT = owner_text("""## Speaking up

You noticed something in {OWNER}'s messages, and your code decided it may reach them — at most through the
channel the pack names. This turn decides whether it really should, and says it once, well.

1. Verify first. Re-read the sources in the pack, and look further with your tools if you need to. If they
   don't support the claim, or {OWNER} has already dealt with it, end with one line starting "DROP:" and why.
   Nothing reaches {OWNER}.
2. Do the work before you ask. If a draft, a link, a lookup or a filled form would make {OWNER}'s answer one tap,
   prepare it now. Never send, book, buy or reply for them here: hold the irreversible step and say so
   ("not sent", "not done").
3. Say it once, in this shape: what you saw and where it came from (whose message, when) → one clause on what
   it means → what is missing → what you prepared → one question they can answer yes/no or by choosing.
   Hedge only what you infer. Name people and events. No greeting, no "let me know", no feelings.
   A push is under 180 characters; the rest goes in the note it opens.
4. Reach them with tell_owner — urgent only if the pack allows a push. For a follow-through allowed in a thread,
   reply there with send_message instead.
5. You may choose a lower channel, or silence. You can never raise it. The system enforces the budget, quiet
   hours and the shape; if tell_owner answers "Tool error", fix what it says once.
End with one line: what you told {OWNER}; or "DROP: …"; or "LEFT: …" if you chose silence.""")

# The pack's background (2026-10-11): listed like the messages, the
# calendar's "Toll bill" became a noticing cited as "calendar:…" and was
# dropped — every drop in ~100 real passes.
COMING_UP_HEADER = "## Coming up (background: never a source, never a noticing on its own)"
def _heading(text: str) -> str:
    """A rendered ``## …`` heading with its first word capitalised ("## The
    owner's …" when no name is set)."""
    head, _, rest = text.partition("## ")
    return head + "## " + rest[:1].upper() + rest[1:] if rest else text


OWNER_LINES_HEADER = _heading(owner_text("## {OWNER}'s own lines, last 6 hours (all chats)"))
COALESCE_S = 60.0
MAX_EVENTS_PER_CALL = 40
NOTICE_TIMEOUT_S = 45.0
NOTICE_MAX_TOKENS = 2048
ECHO_WINDOW_S = 90
RETRY_FAILED_AFTER_MIN = 10
CONTEXT_MAX_CHARS = 32_000
EVENT_TEXT_CHARS = 4_000
CHAT_LINES = 10
SPEAK_CHAT_LINES = 20
LINE_CHARS = 300
OWNER_LINES_H = 6
OWNER_LINES_MAX = 40
OPEN_NOTICINGS_H = 72
HISTORY_DAYS = 30
SPEAK_TIMEOUT_S = 180.0
SPEAK_URGENT_TIMEOUT_S = 90.0
SPEAK_URGENT_WINDOW = timedelta(minutes=30)
SPEAK_RETRY_AFTER = timedelta(minutes=10)
MAX_SPEAK_ATTEMPTS = 2
# An ``open`` noticing untouched this long has no speaker: the wake is
# bounded at SPEAK_TIMEOUT_S + 60 s, so past that (and a margin) a restart
# or a crash left it (review 2026-10-11). The sweep fails it, due at once.
STRANDED_AFTER = timedelta(seconds=SPEAK_TIMEOUT_S + 60 + 120)
SPEAK_BUDGET_ENV = "EDWARD_SPEAK_BUDGET"
NOTICE_MODEL_ENV = "EDWARD_NOTICE_MODEL"
SPEAK_CONVERSATION_ID = "edward-speak"
SEND_TOOLS = frozenset({"send_message", "send_imessage", "send_sms", "send_whatsapp"})
SUGGESTIONS = ("push", "ambient", "note", "thread", "none")
_TAPBACK = re.compile("^(loved|liked|disliked|laughed at|emphasized|questioned|reacted\\b.*\\bto)\\s+[\u201c\"'\u2018]",
                      re.I)
_SECRET_PHRASE = {
    "anthropic_key": "an Anthropic key", "openai_key": "an OpenAI key", "github_token": "a GitHub token",
    "slack_token": "a Slack token", "aws_access_key": "an AWS key", "google_api_key": "a Google API key",
    "jwt": "a login token", "oauth_token": "a login token", "private_key": "a private key",
    "bearer_token": "a bearer token", "vapid_private_key": "a push key", "generic_secret": "a password or key",
    "google_oauth_token": "a Google login token", "google_refresh_token": "a Google login token",
}

_pending_ids: List[str] = []
_timer: Optional["asyncio.Task"] = None
# Noticings whose surface (the speak wake, the ambient pulse) runs in this
# process now: the stranded sweep never takes them for dead.
_SURFACING: set = set()
_pass_lock: Optional[tuple] = None   # (loop, asyncio.Lock)


def _lock() -> "asyncio.Lock":
    global _pass_lock
    loop = asyncio.get_running_loop()
    if _pass_lock is None or _pass_lock[0] is not loop:
        _pass_lock = (loop, asyncio.Lock())
    return _pass_lock[1]


def _track(coro) -> "asyncio.Task":
    task = asyncio.get_running_loop().create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _enqueue(ids: Optional[List[str]], *, source: str = "imessage") -> None:
    """Coalesce: the first id starts a 60 s timer; ids that arrive meanwhile
    (or during a running pass) join the next pass."""
    global _timer
    try:
        if ids is None:
            rows = await _load_events(None, _utcnow(), owners_lines=True)
            ids = [r.id for r in rows if not is_ping_event(r)]
        for i in ids:
            if i not in _pending_ids:
                _pending_ids.append(i)
        if _pending_ids and (_timer is None or _timer.done()
                             or _timer.get_loop() is not asyncio.get_running_loop()):
            _timer = _track(_fire_after(COALESCE_S))
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] enqueue failed ({type(exc).__name__})")


async def _fire_after(delay: float) -> None:
    global _timer
    await asyncio.sleep(max(0.0, float(delay)))
    async with _lock():
        ids = list(_pending_ids)
        _pending_ids.clear()
        for start in range(0, len(ids), 400):
            try:
                await notice_pass(ids[start:start + 400])
            except Exception as exc:  # noqa: BLE001
                print(f"[notice] pass crashed ({type(exc).__name__}: {str(exc)[:120]})")
    if _pending_ids:
        _timer = _track(_fire_after(COALESCE_S))


# ------------------------------------------------------------ text --

def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "output_text"):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "".join(parts)
    return str(content)


def event_text(row: Any) -> str:
    """The message as the pack shows it: the media presenter when present, else
    the stored full text (≤ 4,000), else the summary, plus its media lines."""
    try:
        from services.media import present

        got = present.event_text(row)
        if isinstance(got, str) and got.strip():
            return got[:EVENT_TEXT_CHARS]
    except Exception:  # noqa: BLE001
        pass
    raw = _raw(row)
    text = str(raw.get("full_text") or getattr(row, "summary", None) or "")[:EVENT_TEXT_CHARS]
    media = raw.get("media_lines") or []
    if isinstance(media, list) and media:
        text = (text + "\n" + "\n".join(str(m) for m in media[:6])).strip()
    return text


def _signed(text: str) -> bool:
    try:
        from services.mind_service import has_edward_signature

        return has_edward_signature(text)
    except Exception:  # noqa: BLE001
        return bool(re.search(r"[-—]\s*Edward\s*(🤖)?\s*$", (text or "").rstrip(), re.I))


def speaker(row: Any) -> str:
    raw = _raw(row)
    if raw.get("by_edward") or raw.get("edward_echo") or (
            getattr(row, "is_from_user", False) and _signed(event_text(row))):
        return "Edward"
    if getattr(row, "is_from_user", False):
        return owner_name()
    return (getattr(row, "display_label", None) or getattr(row, "contact_name", None)
            or getattr(row, "sender", None) or "someone")


def _chat_name(row: Any) -> str:
    return getattr(row, "chat_name", None) or getattr(row, "chat_identifier", None) or "a chat"


def _local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    try:
        from services.timezone_utils import utc_to_local

        return utc_to_local(_aware(dt))
    except Exception:  # noqa: BLE001
        return _aware(dt)


def _clock(dt: Optional[datetime]) -> str:
    loc = _local(dt)
    return loc.strftime("%-I:%M %p").lower() if loc else "?"


def _parse_dt(value: Any) -> Optional[datetime]:
    """ISO 8601 → aware UTC; a naive value is read in the owner's zone. ``None``
    when it cannot be read."""
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        try:
            from services.timezone_utils import get_user_tz

            dt = dt.replace(tzinfo=get_user_tz())
        except Exception:  # noqa: BLE001
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------- pre-filter --

async def _recent_sends(now: datetime) -> List[tuple]:
    """(chat, sent_at aware) of Edward's own iMessage sends in the last day."""
    try:
        from sqlalchemy import select

        from services.database import ImessageOutgoingSendModel, async_session

        async with async_session() as s:
            rows = (await s.execute(select(ImessageOutgoingSendModel.chat_identifier, ImessageOutgoingSendModel.sent_at)
                                    .where(ImessageOutgoingSendModel.sent_at >= _aware(now) - timedelta(days=1)))).all()
        return [(c, _aware(t)) for c, t in rows if t is not None]
    except Exception:  # noqa: BLE001
        return []


async def _stood_down_chats(now: datetime) -> frozenset:
    try:
        from sqlalchemy import select

        from services.database import ChatStanddownModel, async_session

        async with async_session() as s:
            rows = (await s.execute(select(ChatStanddownModel).where(ChatStanddownModel.released_at.is_(None)))).scalars().all()
        return frozenset(r.chat_identifier for r in rows
                         if r.chat_identifier and (r.expires_at is None or _aware(r.expires_at) > _aware(now)))
    except Exception:  # noqa: BLE001
        return frozenset()


def _is_echo(row: Any, sends: Sequence[tuple]) -> bool:
    raw = _raw(row)
    if raw.get("edward_echo") or raw.get("by_edward"):
        return True
    if not getattr(row, "is_from_user", False):
        return False
    if _signed(event_text(row)):
        return True
    from services.speak_gate import chat_key

    at = _aware(getattr(row, "created_at", None))
    key = chat_key(getattr(row, "chat_identifier", None))
    if at is None or not key:
        return False
    for chat, sent_at in sends:
        if chat_key(chat) == key and sent_at <= at <= sent_at + timedelta(seconds=ECHO_WINDOW_S):
            return True
    return False


def _skip_reason(row: Any, sends: Sequence[tuple], stood_down: frozenset) -> Optional[str]:
    from services.speak_gate import chat_key

    if _is_echo(row, sends):
        return "echo"
    text = event_text(row).strip()
    raw = _raw(row)
    if _TAPBACK.match(text):
        return "tapback"
    if not text and not raw.get("media_lines") and not raw.get("attachments"):
        return "empty"
    key = chat_key(getattr(row, "chat_identifier", None))
    if key and key in {chat_key(c) for c in stood_down}:
        return "stand-down"
    return None


def _secret_findings(text: str) -> list:
    try:
        from services import secret_scan

        return secret_scan.find_secrets(text, secret_scan.known_secrets())
    except Exception:  # noqa: BLE001
        return []


def safe_text(row: Any) -> str:
    """The event's text with any secret replaced — what a model may read."""
    text = event_text(row)
    if _secret_findings(text):
        from services.reach_service import redact

        return redact(text) or "[secret]"
    return text


def _security_claim(row: Any, findings: list) -> str:
    kind = str(getattr(findings[0], "kind", "") or "")
    phrase = "one of Edward's own keys" if kind.startswith("env:") else _SECRET_PHRASE.get(kind, "a key")
    return (f"Your {_clock(getattr(row, 'created_at', None))} message in {_chat_name(row)} contains what looks "
            f"like {phrase}; rotate it if it's live.")


def _source_of(row: Any) -> Dict[str, Any]:
    raw = _raw(row)
    out = {"ref": f"hb:{row.id}", "chat": getattr(row, "chat_identifier", None),
           "at": _aware(getattr(row, "created_at", None)).isoformat() if getattr(row, "created_at", None) else None,
           "from_me": bool(getattr(row, "is_from_user", False))}
    if raw.get("rowid") is not None:
        out["rowid"] = raw.get("rowid")
    if raw.get("guid"):
        out["guid"] = raw.get("guid")
    return out


# ------------------------------------------------------------ quota --

def _quota_word() -> str:
    """``ok`` | ``paced`` | ``exhausted``: the spend cap under Claude, the
    Codex meter otherwise. A broken check is ``exhausted`` — fail closed,
    never a silent "ok"."""
    try:
        from services import llm_spend

        if llm_spend.provider() == llm_spend.ANTHROPIC:
            word = llm_spend.quota_state_cached()
        else:
            from services.expression_service import quota_state

            word = quota_state()
        word = str(word or "").strip().lower()
        if word not in ("ok", "paced", "exhausted"):
            raise ValueError(f"unknown quota word {word!r}")
        return word
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] quota check broken ({type(exc).__name__}: {str(exc)[:120]}) — treating it as exhausted")
        return "exhausted"


# ---------------------------------------------------------- context --

def _owner_cap() -> str:
    """The owner's name to start a sentence with ("The owner" when unset)."""
    name = owner_name()
    return name[:1].upper() + name[1:]


async def _presence_line() -> str:
    try:
        from services.presence_service import get_attention_state

        a = await asyncio.wait_for(get_attention_state(), 5)
        who = _owner_cap()
        if getattr(a, "owner_present", False):
            where = f"{who} is at {getattr(a, 'active_surface', None) or 'one of their screens'}"
            if getattr(a, "in_imessage_thread", None):
                where += f", texting in {a.in_imessage_thread}"
            return where
        return f"{who} is away from their screens"
    except Exception:  # noqa: BLE001
        return f"{_owner_cap()}'s presence is unknown"


async def _chat_lines(chat: str, before: datetime, exclude: set, limit: int) -> List[Any]:
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel).where(HeartbeatEventModel.chat_identifier == chat,
                                              HeartbeatEventModel.created_at <= _naive(before))
            .order_by(HeartbeatEventModel.created_at.desc()).limit(limit + len(exclude)))).scalars().all()
    rows = [r for r in rows if r.id not in exclude]
    return list(reversed(rows[:limit]))


async def _owners_lines(now: datetime, hours: float = OWNER_LINES_H, limit: int = OWNER_LINES_MAX) -> List[Any]:
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session

    sends = await _recent_sends(now)
    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel).where(HeartbeatEventModel.is_from_user.is_(True),
                                              HeartbeatEventModel.created_at >= _naive(now) - timedelta(hours=hours))
            .order_by(HeartbeatEventModel.created_at.desc()).limit(limit * 2))).scalars().all()
    return list(reversed([r for r in rows if not _is_echo(r, sends)][:limit]))


async def _open_noticings(now: datetime) -> List[Any]:
    from sqlalchemy import select

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        return list((await s.execute(
            select(NoticingModel).where(NoticingModel.status.in_(("open", "held", "shadow")),
                                        NoticingModel.created_at >= _naive(now) - timedelta(hours=OPEN_NOTICINGS_H))
            .order_by(NoticingModel.created_at.asc()).limit(40))).scalars().all())


async def _coming_up(now: datetime) -> List[str]:
    lines: List[str] = []
    try:
        from services.mind_service import open_intentions

        for it in (await open_intentions())[:15]:
            when = f" (next look {it['next_fire_at']})" if it.get("next_fire_at") else ""
            lines.append(f"- intention: {str(it.get('content') or '')[:200]}{when}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from sqlalchemy import select

        from services.database import ScheduledEventModel, async_session

        async with async_session() as s:
            evs = (await s.execute(select(ScheduledEventModel).where(
                ScheduledEventModel.status == "pending",
                ScheduledEventModel.next_fire_at <= _naive(now) + timedelta(hours=48),
                ScheduledEventModel.next_fire_at >= _naive(now) - timedelta(minutes=5))
                .order_by(ScheduledEventModel.next_fire_at.asc()).limit(15))).scalars().all()
        for ev in evs:
            if getattr(ev, "recurrence_pattern", None):
                continue   # Edward's plumbing (the pulse, the sweep, the nightly)
            lines.append(f"- scheduled {_clock(ev.next_fire_at)} {(_local(ev.next_fire_at) or now):%a}: "
                         f"{str(ev.description or '')[:160]}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import calendar_service

        events = await asyncio.wait_for(calendar_service.events_between(now, now + timedelta(hours=48)), 10)
        for e in list(events or [])[:15]:
            title = e.get("title") if isinstance(e, dict) else getattr(e, "title", None)
            start = e.get("start") if isinstance(e, dict) else getattr(e, "start", None)
            lines.append(f"- calendar {start}: {str(title or '')[:120]}")
    except Exception:  # noqa: BLE001
        pass
    return lines


def _line(row: Any, *, chars: int = LINE_CHARS, text: Optional[str] = None) -> str:
    t = " ".join((text if text is not None else event_text(row)).split())
    if len(t) > chars:
        t = t[: chars - 1] + "…"
    return t


async def _read_states(rows: Sequence[Any]) -> Dict[str, str]:
    """``{event id: "unread" | "read after 3 min"}`` for inbound lines with a
    chat.db ROWID. Empty when chat.db cannot be read."""
    want = {}
    for r in rows:
        raw = _raw(r)
        if not getattr(r, "is_from_user", False) and raw.get("rowid") is not None:
            try:
                want[int(raw["rowid"])] = r
            except (TypeError, ValueError):
                continue
    if not want:
        return {}
    try:
        from services import imessage_read_state

        states = await imessage_read_state.aread_state(list(want))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for rowid, st in states.items():
        r = want.get(rowid)
        if r is None:
            continue
        if not st.get("is_read") or not st.get("read_at"):
            out[r.id] = "unread"
        else:
            mins = max(0, int((st["read_at"] - _aware(r.created_at)).total_seconds() // 60))
            out[r.id] = f"read after {mins} min"
    return out


async def build_context(rows: Sequence[Any], now: datetime, redactions: Dict[str, str]) -> tuple:
    """The noticer's dynamic pack and the ``hb:`` refs it names. The owner's lines
    from every chat over 6 h are what join a vendor's text to "en route"
    sent elsewhere; the open noticings carry their full claims."""
    refs: Dict[str, Dict[str, Any]] = {}

    def ref(r: Any) -> str:
        refs[f"hb:{r.id}"] = _source_of(r)
        return f"hb:{r.id}"

    def text_of(r: Any) -> str:
        return redactions.get(r.id) if r.id in redactions else event_text(r)

    loc = _local(now) or now
    head = [f"Now: {loc:%A} {_clock(now)} ({getattr(loc.tzinfo, 'key', None) or loc.tzname()}). "
            f"{await _presence_line()}."]
    reads = await _read_states(rows)
    new = ["## New messages"]
    for r in rows:
        state = reads.get(r.id) or ("" if r.is_from_user else "")
        meta = f"{ref(r)} · {_chat_name(r)} · {speaker(r)} · {_clock(r.created_at)}" + (f" · {state}" if state else "")
        body = text_of(r).strip()
        new.append(f"- {meta}\n  " + body.replace("\n", "\n  "))
    sections = ["\n".join(head), "\n".join(new)]

    chats_section = ["## Recent lines in these chats"]
    seen_chats = []
    new_ids = {r.id for r in rows}
    for r in rows:
        c = getattr(r, "chat_identifier", None)
        if c and c not in seen_chats:
            seen_chats.append(c)
    newest = max((_aware(r.created_at) for r in rows if r.created_at), default=_aware(now))
    for c in seen_chats[:12]:
        try:
            lines = await _chat_lines(c, newest, new_ids, CHAT_LINES)
        except Exception:  # noqa: BLE001
            lines = []
        if not lines:
            continue
        name = next((_chat_name(r) for r in rows if getattr(r, "chat_identifier", None) == c), c)
        chats_section.append(f"### {name}")
        for x in lines:
            chats_section.append(f"- {ref(x)} {_clock(x.created_at)} {speaker(x)}: "
                                 f"{_line(x, text=redactions.get(x.id) or safe_text(x))}")
    if len(chats_section) > 1:
        sections.append("\n".join(chats_section))

    try:
        owner = await _owners_lines(now)
    except Exception:  # noqa: BLE001
        owner = []
    owner_lines = [f"- {ref(x)} · {_chat_name(x)} · {_clock(x.created_at)}: "
                 f"{_line(x, text=redactions.get(x.id) or safe_text(x))}" for x in owner if x.id not in new_ids]

    try:
        opens = await _open_noticings(now)
    except Exception:  # noqa: BLE001
        opens = []
    if opens:
        block = ["## Open noticings (already noted; skip unless something new happened, then reuse the key)"]
        for n in opens:
            closes = _local(n.closes_at).isoformat() if n.closes_at else "—"
            block.append(f"- [{n.status}] {n.kind} · key {n.dedupe_key or '—'} · closes {closes}: {n.claim}")
        sections.append("\n".join(block))
    coming = await _coming_up(now)
    if coming:
        sections.append(COMING_UP_HEADER + "\n" + "\n".join(coming))
    downs = await _stood_down_chats(now)
    if downs:
        sections.append("## Chats that told Edward to stay out\n" + "\n".join(f"- {d}" for d in sorted(downs)))

    def assemble(owner_block: List[str]) -> str:
        parts = list(sections[:2]) + ([(OWNER_LINES_HEADER + "\n" + "\n".join(owner_block))]
                                      if owner_block else []) + list(sections[2:])
        return "\n\n".join(parts)

    text = assemble(owner_lines)
    while len(text) > CONTEXT_MAX_CHARS and owner_lines:
        owner_lines = owner_lines[1:]          # the oldest of the owner's lines go first
        text = assemble(owner_lines)
    if len(text) > CONTEXT_MAX_CHARS:
        text = text[:CONTEXT_MAX_CHARS] + "\n…(clipped)"
    kept = {k: v for k, v in refs.items() if k in text}
    return text, kept


# ------------------------------------------------------------ parse --

# Words a model writes for the gate's categories and channels (review
# 2026-10-11): only ``travel`` and ``household`` can push, so "flight" or
# "check-in" must not fall to None and a note. Unknown words still do, and so
# does an ambiguous one ("service", "air"): a guess never makes a floor item.
CATEGORY_SYNONYMS: Dict[str, str] = {
    **{w: "travel" for w in ("flight", "flights", "airline", "airlines", "trip", "trips", "travels",
                             "check-in", "checkin", "check in", "boarding", "hotel", "hotels", "train",
                             "trains", "airport", "rental car", "vacation")},
    **{w: "household" for w in ("home", "house", "delivery", "deliveries", "pet", "pets", "yard", "chores")},
    **{w: "money" for w in ("finance", "finances", "financial", "bill", "bills", "payment", "payments",
                            "banking", "bank")},
    **{w: "work" for w in ("job", "office", "career")},
    **{w: "social" for w in ("friends", "friend", "plans", "invitation")},
    **{w: "family" for w in ("relatives", "kids", "parents")},
    **{w: "security" for w in ("account", "accounts", "login", "credential", "credentials", "privacy")},
    **{w: "health" for w in ("medical", "medicine", "doctor", "wellness")},
    **{w: "logistics" for w in ("errand", "errands", "order", "orders", "shipping", "pickup")},
}
# Never a guess of ``thread``: the gate turns a thread outside an open ping
# chat into a push.
SUGGESTION_SYNONYMS: Dict[str, str] = {
    "notify": "push", "notification": "push", "alert": "push", "buzz": "push", "urgent": "push",
    "screen": "ambient", "today": "note", "silent": "none",
}


def _category_word(raw: Any) -> Optional[str]:
    from services.speak_gate import CATEGORIES

    word = " ".join(str(raw or "").strip().lower().replace("_", " ").split())
    if word in CATEGORIES:
        return word
    return CATEGORY_SYNONYMS.get(word) or CATEGORY_SYNONYMS.get(word.replace(" ", "-"))


def _suggestion_word(raw: Any) -> str:
    word = str(raw or "none").strip().lower()
    if word in SUGGESTIONS:
        return word
    return SUGGESTION_SYNONYMS.get(word, "none")


def _shape(value: Any, *, keep: str = r"[\w:.\-]") -> str:
    """What a dropped field looked like, never what it said: one id-like
    token as written; anything else as its length (a string) or its type."""
    if isinstance(value, str):
        v = value.strip()
        if v and len(v) <= 80 and re.fullmatch(f"{keep}+", v) and not re.search(r"[A-Z]", v):
            return v
        return f"<{len(v)} chars>"
    return f"<{type(value).__name__}>"


def _date_shape(value: Any) -> str:
    """A ``closes_at`` the parser refused, as its format: digits → 9,
    letters → a, punctuation kept ("9999-99-99 aaaaaaa")."""
    if not isinstance(value, str):
        return f"<{type(value).__name__}>"
    return re.sub(r"[^\W\d_]", "a", re.sub(r"\d", "9", value.strip()))[:40]


def _raw_sources(it: Dict[str, Any]) -> List[Any]:
    raw = it.get("sources") or []
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


def _sources(it: Dict[str, Any]) -> List[str]:
    return [str(x).strip() for x in _raw_sources(it) if str(x).strip()]


def drop_reason(it: Any, refs: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Why ``validate_items`` drops this item — ``{"rule": …}`` plus the
    shape of what was wrong, never the claim or a message line — or None
    when it is kept. Pure."""
    from services.speak_gate import KINDS

    if not isinstance(it, dict):
        return {"rule": "not_an_object", "type": type(it).__name__}
    kind = str(it.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return {"rule": "unknown_kind", "kind": _shape(kind, keep=r"[a-z_\-]")}
    raw_sources = _raw_sources(it)
    sources = _sources(it)
    if not sources:
        return {"rule": "no_sources"}
    missing = [x for x in raw_sources if str(x).strip() and str(x).strip() not in refs]
    if missing:
        return {"rule": "source_not_in_pack", "missing": [_shape(x) for x in missing[:3]]}
    if kind == "conflict" and len(set(sources)) < 2:
        return {"rule": "conflict_needs_two"}
    closes_raw = it.get("closes_at")
    if closes_raw not in (None, "", "null") and _parse_dt(closes_raw) is None:
        return {"rule": "unreadable_closes_at", "closes_at": _date_shape(closes_raw)}
    return None


def drop_line(drop: Dict[str, Any]) -> str:
    """One log line for a drop: the rule and its shapes, never text."""
    rest = " ".join(f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}"
                    for k, v in drop.items() if k != "rule")
    return f"rule={drop.get('rule')}" + (f" {rest}" if rest else "")


def validate_items(payload: Any, refs: Dict[str, Dict[str, Any]],
                   drops: Optional[List[Dict[str, Any]]] = None) -> tuple:
    """(valid items, invalid count). Pure. An item is dropped when its kind
    is unknown, a source is not in the pack, a conflict has fewer than two
    sources, or ``closes_at`` cannot be read; ``drops``, when given, gets
    each drop's ``drop_reason``."""
    items = payload.get("noticings") if isinstance(payload, dict) else None
    valid: List[Dict[str, Any]] = []
    invalid = 0
    for it in items or []:
        why = drop_reason(it, refs)
        if why is not None:
            invalid += 1
            if drops is not None:
                drops.append(why)
            continue
        kind = str(it.get("kind") or "").strip().lower()
        sources = _sources(it)
        closes = _parse_dt(it.get("closes_at"))
        category = _category_word(it.get("category"))
        suggested = _suggestion_word(it.get("suggested"))
        first = refs[sources[0]]
        inbound = [refs[x] for x in sources if not refs[x].get("from_me")]
        # The pack shows a chat's name, never its identifier: the model's
        # chat_identifier is a guess ("Mom"), and the gate looks chats up by it.
        chat = (inbound[0] if inbound else first).get("chat")
        names = it.get("who") or []
        who = [str(w).strip() for w in ([names] if isinstance(names, str) else names) if str(w).strip()][:6]
        key = str(it.get("dedupe_key") or "").strip()[:160] or f"{kind}:{chat}:{sources[0]}"
        valid.append({"kind": kind, "category": category, "claim": str(it.get("claim") or "").strip()[:2000],
                      "sources": [refs[x] for x in dict.fromkeys(sources)], "chat_identifier": chat, "who": who,
                      "closes_at": closes, "suggested": suggested, "dedupe_key": key,
                      "sensitive": bool(it.get("sensitive"))})
    return valid, invalid


async def _existing_for(item: Dict[str, Any], now: datetime) -> Optional[Any]:
    """An open, held or shadow noticing for the same thing: the same key, or
    the same chat and kind with overlapping sources (72 h)."""
    from sqlalchemy import select

    from services.database import NoticingModel, async_session
    from services.speak_gate import chat_key

    refs = {s["ref"] for s in item["sources"]}
    async with async_session() as s:
        rows = (await s.execute(select(NoticingModel).where(
            NoticingModel.status.in_(("open", "held", "shadow")),
            NoticingModel.created_at >= _naive(now) - timedelta(hours=OPEN_NOTICINGS_H)))).scalars().all()
    for r in rows:
        if r.dedupe_key and r.dedupe_key == item["dedupe_key"]:
            return r
        try:
            theirs = {x.get("ref") for x in json.loads(r.sources_json or "[]")}
        except (TypeError, ValueError):
            theirs = set()
        if r.kind == item["kind"] and chat_key(r.chat_identifier) == chat_key(item["chat_identifier"]) and theirs & refs:
            return r
    return None


async def _merge_into(row: Any, item: Dict[str, Any]) -> bool:
    """Add the new sources (and the fresher claim and close) to an open
    noticing. False when it brought nothing new."""
    from sqlalchemy import update

    from services.database import NoticingModel, async_session

    try:
        mine = json.loads(row.sources_json or "[]")
    except (TypeError, ValueError):
        mine = []
    have = {x.get("ref") for x in mine}
    new = [x for x in item["sources"] if x["ref"] not in have]
    if not new:
        return False
    values: Dict[str, Any] = {"sources_json": json.dumps(mine + new, default=str), "claim": item["claim"] or row.claim,
                              "updated_at": _naive(_utcnow()), "status": "open"}
    if item.get("closes_at"):
        values["closes_at"] = _naive(item["closes_at"])
    async with async_session() as s:
        await s.execute(update(NoticingModel).where(NoticingModel.id == row.id).values(**values))
        await s.commit()
    return True


async def _stamp_pass(event_ids: Sequence[str], batch_id: str, now: datetime) -> int:
    """Stamp a pass's events: unstamped ones, and ones a failed pass stamped
    (the retry overwrites its own marker). Never a ping."""
    if not event_ids:
        return 0
    from sqlalchemy import or_, update

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        res = await s.execute(
            update(HeartbeatEventModel)
            .where(HeartbeatEventModel.id.in_(list(event_ids)),
                   or_(HeartbeatEventModel.notified_at.is_(None),
                       HeartbeatEventModel.notification_batch_id.like("notice-failed:%")),
                   or_(HeartbeatEventModel.triage_status.is_(None), HeartbeatEventModel.triage_status != "ping"))
            .values(notified_at=_naive(now), notification_batch_id=batch_id[:200]))
        await s.commit()
        return int(res.rowcount or 0)


# ------------------------------------------------------------- pass --

async def notice_pass(event_ids: Sequence[str], *, pass_id: Optional[str] = None,
                      retry_of: Optional[str] = None, purpose: str = "mind:notice") -> Dict[str, Any]:
    """One noticer pass over these events: pre-filter (code),
    context, one background-model call under ``mind:notice``, parse and validate, then a
    ``noticings`` row per item and the gate for each. Every pass writes one
    ``ledger_events`` row; a parse failure is ``failed``, never ``[]``."""
    import time as _time

    pass_id = pass_id or str(uuid.uuid4())
    now = _utcnow()
    t0 = _time.monotonic()
    rows = await _load_events(list(event_ids), now)
    pings = [r for r in rows if is_ping_event(r)]
    # Only what no pass has read yet (or a failed pass left for its retry):
    # a hand-off and the messages shim can name the same event twice.
    rows = [r for r in rows if not is_ping_event(r)
            and (r.notified_at is None or str(r.notification_batch_id or "").startswith("notice-failed:"))]
    sends = await _recent_sends(now)
    downs = await _stood_down_chats(now)
    keep, skipped = [], {}
    for r in rows:
        why = _skip_reason(r, sends, downs)
        if why:
            skipped.setdefault(why, []).append(r.id)
        else:
            keep.append(r)
    for why, ids in skipped.items():
        await _stamp_pass(ids, f"notice-skip:{why}:{pass_id}", now)

    # Secrets in the owner's own lines: a code-made security noticing; the model
    # only ever sees the line redacted.
    redactions: Dict[str, str] = {}
    code_ids: List[str] = []
    for r in keep:
        text = event_text(r)
        found = _secret_findings(text)
        if not found:
            continue
        from services.reach_service import redact

        redactions[r.id] = redact(text) or "[secret]"   # no model ever reads the value, whoever wrote it
        if r.is_from_user:
            nid = await add_noticing(kind="security", category="security", origin="code",
                                     claim=_security_claim(r, found), sources=[_source_of(r)],
                                     chat_identifier=r.chat_identifier, dedupe_key=f"security:{r.id}",
                                     suggested="note", pass_id=pass_id)
            if nid:
                code_ids.append(nid)

    detail: Dict[str, Any] = {"events": len(rows), "kept": len(keep), "pings": len(pings),
                              "skipped": sum(len(v) for v in skipped.values()), "noticed": 0, "invalid": 0,
                              "merged": 0, "code": len(code_ids)}
    result: Dict[str, Any] = {"pass_id": pass_id, **detail}
    for nid in code_ids:
        await _gate_safely(nid)

    if not keep:
        detail["ms"] = int((_time.monotonic() - t0) * 1000)
        await _ledger(source="noticer", outcome="ok", ref_id=pass_id, detail={**detail, "no_call": True})
        print(f"[notice] pass {pass_id[:8]} ok events={len(rows)} noticed=0 invalid=0 (nothing to read)")
        return {**result, "outcome": "ok", "ms": detail["ms"]}

    if _quota_word() == "exhausted":
        await _stamp_pass([r.id for r in keep], f"notice-deferred:{pass_id}", now)
        await _ledger(source="noticer", outcome="deferred", ref_id=pass_id, detail=detail, reason="spend cap")
        print(f"[notice] pass {pass_id[:8]} deferred (cap)")
        return {**result, "outcome": "deferred"}

    outcome, error_class = "ok", None
    noticed: List[str] = []
    try:
        valid_all: List[Dict[str, Any]] = []
        for start in range(0, len(keep), MAX_EVENTS_PER_CALL):
            chunk = keep[start:start + MAX_EVENTS_PER_CALL]
            context, refs = await build_context(chunk, now, redactions)
            payload = await _call_noticer(context, purpose=purpose)
            if not isinstance(payload, dict) or not isinstance(payload.get("noticings"), list):
                raise _ParseFailure("no {\"noticings\": [...]} object")
            drops: List[Dict[str, Any]] = []
            valid, invalid = validate_items(payload, refs, drops=drops)
            detail["invalid"] += invalid
            for d in drops:
                rules = detail.setdefault("invalid_rules", {})
                rules[d["rule"]] = rules.get(d["rule"], 0) + 1
                print(f"[notice] pass {pass_id[:8]} dropped an item: {drop_line(d)}")
            valid_all.extend(valid)
        for item in valid_all:
            existing = await _existing_for(item, now)
            if existing is not None:
                if await _merge_into(existing, item):
                    detail["merged"] += 1
                    noticed.append(existing.id)
                continue
            nid = await add_noticing(kind=item["kind"], claim=item["claim"], origin="noticer",
                                     category=item["category"], sources=item["sources"],
                                     chat_identifier=item["chat_identifier"], who=item["who"],
                                     dedupe_key=item["dedupe_key"], closes_at=item["closes_at"],
                                     suggested=item["suggested"], sensitive=item["sensitive"], pass_id=pass_id)
            if nid:
                detail["noticed"] += 1
                noticed.append(nid)
    except Exception as exc:  # noqa: BLE001 — a failed pass is recorded, never an empty one
        outcome = "failed"
        error_class = "parse" if isinstance(exc, _ParseFailure) else type(exc).__name__
        detail["error_class"] = error_class
    detail["ms"] = int((_time.monotonic() - t0) * 1000)
    ids = [r.id for r in keep]
    if outcome == "ok":
        await _stamp_pass(ids, f"notice:{pass_id}", now)
        rules = ",".join(f"{k}:{v}" for k, v in sorted(detail.get("invalid_rules", {}).items()))
        print(f"[notice] pass {pass_id[:8]} ok events={len(keep)} noticed={detail['noticed']} "
              f"invalid={detail['invalid']}{f' ({rules})' if rules else ''} ms={detail['ms']}")
    else:
        marker = f"notice-failed2:{retry_of}" if retry_of else f"notice-failed:{pass_id}"
        await _stamp_pass(ids, marker, now)
        print(f"[notice] pass {pass_id[:8]} FAILED ({error_class})")
    await _ledger(source="noticer", outcome=outcome, ref_id=pass_id,
                  detail={**detail, **({"retry_of": retry_of} if retry_of else {})})
    if outcome == "ok":
        for nid in noticed:
            await _gate_safely(nid)
    out = {**result, **detail, "outcome": outcome}
    if error_class:
        out["error_class"] = error_class
    return out


class _ParseFailure(Exception):
    pass


async def _call_noticer(context: str, *, purpose: str = "mind:notice") -> Any:
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.call_purpose import purpose_scope
    from services.json_parsing import extract_json_payload

    with purpose_scope(purpose if str(purpose or "").startswith("mind:notice") else "mind:notice"):
        llm = llm_utils.build_background_llm(route="background", priority="interactive",
                                             model=os.getenv(NOTICE_MODEL_ENV) or None,
                                             max_tokens=NOTICE_MAX_TOKENS, effort="medium")
        resp = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=NOTICER_PROMPT), HumanMessage(content=context)]), NOTICE_TIMEOUT_S)
    text = _text(getattr(resp, "content", resp))
    payload = extract_json_payload(text, expected="object", subsystem="noticer")
    if not payload:
        raise _ParseFailure("no JSON object")
    return payload


# ============================================================ the gate --

def speak_budget() -> int:
    raw = (os.getenv(SPEAK_BUDGET_ENV) or "").strip()
    try:
        return max(0, int(raw)) if raw else 8
    except ValueError:
        return 8


async def _load_noticing(nid: str) -> Optional[Any]:
    from sqlalchemy import select

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        return (await s.execute(select(NoticingModel).where(NoticingModel.id == nid))).scalar_one_or_none()


def to_gate_noticing(row: Any) -> Any:
    """The row as ``speak_gate.Noticing``, every time in the owner's zone. Pure but
    for the zone."""
    from services import speak_gate

    try:
        srcs = json.loads(row.sources_json or "[]")
    except (TypeError, ValueError):
        srcs = []
    sources = []
    for x in srcs:
        at = _parse_dt(x.get("at")) if isinstance(x, dict) else None
        if at is None:
            continue
        sources.append(speak_gate.Source(ref=str(x.get("ref") or ""), chat=x.get("chat"), at=_local(at),
                                         from_me=bool(x.get("from_me")),
                                         read_at=_local(_parse_dt(x.get("read_at"))) if x.get("read_at") else None))
    try:
        who = tuple(str(w) for w in json.loads(row.who_json or "[]"))
    except (TypeError, ValueError):
        who = ()
    return speak_gate.Noticing(id=row.id, kind=row.kind, category=row.category, claim=row.claim,
                               sources=tuple(sources), chat=row.chat_identifier, who=who,
                               closes_at=_local(row.closes_at) if row.closes_at else None,
                               suggested=row.suggested, dedupe_key=row.dedupe_key, sensitive=bool(row.sensitive),
                               origin=row.origin or "noticer")


async def _open_ping_chats(chat: Optional[str]) -> frozenset:
    """``{chat}`` when it addressed Edward within 7 days (the ping runner's
    ``addressed_recently``); empty without the ping runner."""
    if not chat:
        return frozenset()
    try:
        import importlib

        hooks = importlib.import_module("services.pings.hooks")
        return frozenset({chat}) if await hooks.addressed_recently(chat, 168) else frozenset()
    except Exception:  # noqa: BLE001
        return frozenset()


async def _busy_chats() -> frozenset:
    try:
        import importlib

        hooks = importlib.import_module("services.pings.hooks")
        busy = set(await hooks.open_chats())
        busy |= set(hooks.chats_in_flight())
        return frozenset(busy)
    except Exception:  # noqa: BLE001
        return frozenset()


async def _owner_in_chat(chat: Optional[str], now: datetime) -> tuple:
    """(The owner's latest non-echo line in the chat, how many of their lines in 24 h)."""
    if not chat:
        return None, 0
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session
    from services.speak_gate import chat_key

    sends = await _recent_sends(now)
    key = chat_key(chat)
    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel).where(HeartbeatEventModel.is_from_user.is_(True),
                                              HeartbeatEventModel.created_at >= _naive(now) - timedelta(days=7))
            .order_by(HeartbeatEventModel.created_at.desc()).limit(400))).scalars().all()
    mine = [r for r in rows if chat_key(r.chat_identifier) == key and not _is_echo(r, sends)
            and not (_raw(r).get("ping") or (r.triage_status or "") == "ping")]
    last = _local(mine[0].created_at) if mine else None
    day = sum(1 for r in mine if _aware(r.created_at) >= _aware(now) - timedelta(hours=24))
    return last, day


async def _history(now: datetime, exclude: Optional[str] = None) -> tuple:
    from sqlalchemy import select

    from services import speak_gate
    from services.database import NoticingModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(NoticingModel).where(
            NoticingModel.surfaced_at.is_not(None),
            NoticingModel.surfaced_at >= _naive(now) - timedelta(days=HISTORY_DAYS),
            NoticingModel.origin != "care"))).scalars().all()
    out = []
    for r in rows:
        if r.id == exclude:
            continue
        try:
            refs = frozenset(x.get("ref") for x in json.loads(r.sources_json or "[]") if isinstance(x, dict))
        except (TypeError, ValueError):
            refs = frozenset()
        out.append(speak_gate.Surfaced(category=r.category, channel=r.channel, outcome=r.outcome,
                                       at=_local(r.surfaced_at), dedupe_key=r.dedupe_key, chat=r.chat_identifier,
                                       kind=r.kind, refs=refs))
    return tuple(out)


async def _speak_counts(now: datetime, exclude: Optional[str] = None) -> tuple:
    """(speak turns today in the owner's day, {chat key: speak turns in the last
    hour}) from the ``speak`` ledger rows. A retry of the same noticing does
    not count against its own chat."""
    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session
    from services.push_ledger import local_day_bounds_utc

    start, end = local_day_bounds_utc(now)
    async with async_session() as s:
        rows = (await s.execute(select(LedgerEventModel).where(
            LedgerEventModel.kind == "speak",
            LedgerEventModel.created_at >= min(start, _naive(now) - timedelta(hours=1))))).scalars().all()
    today = 0
    by_chat: Dict[str, int] = {}
    hour_ago = _naive(now) - timedelta(hours=1)
    for r in rows:
        if start <= r.created_at < end:
            today += 1
        if r.created_at >= hour_ago and r.ref_id != exclude:
            try:
                chat = (json.loads(r.detail_json or "{}") or {}).get("chat")
            except (TypeError, ValueError):
                chat = None
            if chat:
                by_chat[chat] = by_chat.get(chat, 0) + 1
    return today, by_chat


async def _owner_ceilings(now: datetime) -> Dict[str, str]:
    try:
        from services import speak_gate
        from services.mind_service import _instructions_source

        return speak_gate.ceilings_from_owner(await _instructions_source(now))
    except Exception:  # noqa: BLE001
        return {}


async def gate_context(n: Any, row: Any, now: datetime) -> Any:
    """Everything ``speak_gate.decide`` reads, gathered once (I/O). Each piece
    fails toward silence: an unreadable push count is a spent budget."""
    from services import reach_service, speak_gate

    last, day_lines = await _owner_in_chat(n.chat, now)
    budget = reach_service.push_budget()
    pushes = await reach_service.autonomous_pushes_today(now) if budget is not None else 0
    speaks_today, by_chat = await _speak_counts(now, exclude=row.id)
    return speak_gate.GateContext(
        now=_local(now),
        stood_down=await _stood_down_chats(now),
        busy_chats=await _busy_chats(),
        owner_last_line={n.chat: last} if (n.chat and last) else {},
        live_chats=frozenset({n.chat}) if (n.chat and day_lines >= speak_gate.LIVE_CHAT_LINES_24H) else frozenset(),
        open_ping_chats=await _open_ping_chats(n.chat),
        history=await _history(now, exclude=row.id),
        pushes_today=(budget or 0) if pushes is None else int(pushes),
        speaks_today=speaks_today,
        speaks_by_chat_1h=by_chat,
        push_scope=push_scope(),
        quota=_quota_word(),
        ceilings_from_owner=await _owner_ceilings(now),
        push_budget=budget,
        speak_budget=speak_budget(),
    )


async def _update_noticing(nid: str, **values: Any) -> None:
    from sqlalchemy import update

    from services.database import NoticingModel, async_session

    values["updated_at"] = _naive(_utcnow())
    for k in ("due_at", "decided_at", "surfaced_at", "outcome_at"):
        if isinstance(values.get(k), datetime):
            values[k] = _naive(values[k])
    async with async_session() as s:
        await s.execute(update(NoticingModel).where(NoticingModel.id == nid).values(**values))
        await s.commit()


async def _gate_safely(nid: str, now: Optional[datetime] = None) -> None:
    try:
        await gate_and_act(nid, now=now)
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] gate failed for {nid[:8]} ({type(exc).__name__}: {str(exc)[:160]})")
        try:
            await _after_gate_failure(nid, f"gate failed: {type(exc).__name__}")
        except Exception as inner:  # noqa: BLE001
            print(f"[notice] could not mark {nid[:8]} failed ({type(inner).__name__})")


async def _after_gate_failure(nid: str, note: str) -> None:
    """A gate that raised leaves the row where it was (``open`` for a new
    one): fail it, due a re-gate by the sweep, and count the attempt so a
    gate that keeps raising gives up (review 2026-10-11)."""
    if nid in _SURFACING:
        return
    row = await _load_noticing(nid)
    if row is None or row.status not in ("open", "held", "failed"):
        return
    attempts = int(row.attempts or 0) + 1
    due = _utcnow() + SPEAK_RETRY_AFTER if attempts < MAX_SPEAK_ATTEMPTS else None
    await _update_noticing(nid, status="failed", attempts=attempts, due_at=due, decision_reason=note[:500])


async def _after_surface_failure(row: Any, n: Any, d: Any, note: str) -> None:
    """A surface that raised (or whose process died — see
    ``_sweep_stranded``) leaves the row ``open``: what ``_speak``'s own
    failure branch does — the templated push for a floor item closing soon,
    else failed and due a re-gate while attempts remain."""
    fresh = await _load_noticing(row.id)
    if fresh is None or fresh.status != "open":
        return
    later = _utcnow()
    closing_soon = (n is not None and n.closes_at is not None
                    and n.closes_at - _local(later) < SPEAK_URGENT_WINDOW)
    if d is not None and getattr(d, "floor_push", False) and closing_soon and not getattr(d, "is_fallback", False):
        await _fallback_push(fresh, n)
        return
    if int(fresh.attempts or 0) < MAX_SPEAK_ATTEMPTS:
        await _update_noticing(row.id, status="failed", due_at=later + SPEAK_RETRY_AFTER, decision_reason=note[:500])
    else:
        await _update_noticing(row.id, status="failed", due_at=None, decision_reason=note[:500])


async def gate_and_act(nid: str, now: Optional[datetime] = None) -> Optional[Any]:
    """Decide this noticing (pure gate over gathered facts), record the
    decision, and — in ``notice`` mode — act on it."""
    from services import speak_gate

    now = now or _utcnow()
    row = await _load_noticing(nid)
    if row is None:
        return None
    n = to_gate_noticing(row)
    ctx = await gate_context(n, row, now)
    d = speak_gate.decide(n, ctx)
    m = mode()
    values: Dict[str, Any] = {"decision": d.channel, "decision_reason": d.reason[:500], "decided_at": now,
                              "floor_push": bool(d.floor_push), "due_at": None}
    if d.channel == "dropped":
        values["status"] = "dropped"
    elif d.channel in ("silent", "journal"):
        values["status"] = "silent"
    elif d.channel == "hold":
        values.update(status="held", due_at=d.hold_until)
    elif m != "notice":
        values["status"] = "shadow"
    else:
        values["status"] = "open"
    await _update_noticing(nid, **values)
    print(f"[notice] gate {nid[:8]} {row.kind}/{row.category or '-'} → {d.channel} ({d.reason})"
          + (" [shadow]" if values["status"] == "shadow" else ""))
    if values["status"] == "open":
        # The speak turn's slot is reserved now (a ``speak`` ledger row), so
        # the next decision — in this pass or the sweep — already counts it;
        # the turn itself runs as a tracked task: an Opus turn (≤ 180 s) never
        # holds up the noticer or the */5 sweep's 120 s budget.
        _SURFACING.add(nid)
        try:
            reservation = None
            if not d.is_fallback and d.channel in ("note", "push", "thread"):
                reservation = await _speak_reserve(row, d)
            _track(_surface_safely(row, n, d, now, reservation))
        except BaseException:
            _SURFACING.discard(nid)
            raise
    return d


async def _surface_safely(row: Any, n: Any, d: Any, now: datetime, reservation: Optional[int]) -> None:
    try:
        await _surface(row, n, d, now, reservation)
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] surface failed for {row.id[:8]} ({type(exc).__name__}: {str(exc)[:160]})")
        await _speak_finish(reservation, "failed", f"{type(exc).__name__}")
        try:
            await _after_surface_failure(row, n, d, f"surface failed: {type(exc).__name__}")
        except Exception as inner:  # noqa: BLE001
            print(f"[notice] could not mark {row.id[:8]} failed ({type(inner).__name__})")
    finally:
        _SURFACING.discard(row.id)


# ========================================================== the speaker --

def speak_policy_kwargs(channel: str, chat: Optional[str], timeout_s: float) -> Dict[str, Any]:
    """The ``TurnPolicy`` for a speak wake, derived from the gate's
    decision: it may push only for ``push``, reach people only for
    ``thread`` (and only that chat). A thread's closing line answers the
    reply check (``final_text_is_reply``), so a speaker that verifies and
    ends ``DROP:`` is never nudged into posting anyway (review
    2026-10-11). Pure."""
    out: Dict[str, Any] = {"source": "mind", "owner_asked": False, "may_push": channel == "push",
                           "may_reach_people": channel == "thread",
                           "reply_to": (chat,) if (channel == "thread" and chat) else (),
                           "deadline_s": max(30, int(timeout_s) - 30)}
    if channel == "thread":
        out["final_text_is_reply"] = True
    return out


def legacy_speak_tools(channel: str) -> List[str]:
    """``allowed_tools`` for ``EDWARD_TOOLSET=legacy`` (v2 ignores it and
    reads the policy)."""
    tools = ["tell_owner", "remember_search", "graph_memory_search", "search_documents", "read_document",
             "read_imessage_thread", "get_recent_messages", "lookup_contact", "web_search", "fetch_page_content",
             "leave_note"]
    if channel == "push":
        tools.append("send_push_notification")
    if channel == "thread":
        tools += ["send_message", "send_imessage"]
    return tools


def render_ambient(n: Any, d: Any) -> str:
    """The surfaces wake's ask for a household item. Pure."""
    until = n.closes_at.isoformat() if n.closes_at else None
    return (f"Something for whoever is home: {n.claim} Put one short line on the house screen or the widget"
            + (f" until {_clock(n.closes_at)} (until={until})" if until else " for two hours")
            + ". Nothing private, no names beyond what the message gives.")


# The speak pack's owner-facing headings (model-facing, rendered once).
_SPEAK_ADVICE = owner_text("How {OWNER} has answered this kind of thing:")
_SPEAK_OWNER_LINES = _heading(owner_text("## {OWNER}'s own lines, last 6 hours"))
_SPEAK_SAID_TODAY = owner_text("## Already said to {OWNER} today")
_SPEAK_HOW_REACHED = owner_text("## How {OWNER} likes to be reached")
_SPEAK_INSTRUCTIONS = _heading(owner_text("## {OWNER}'s standing instructions"))


def render_speak_pack(*, n: Any, d: Any, sources: Sequence[Dict[str, Any]], chats: Dict[str, List[str]],
                      owner_lines: Sequence[str], said_today: Sequence[str], digest_lines: Sequence[str],
                      instructions: Sequence[str], pushes_left: Optional[int], now: datetime) -> str:
    """The speak wake's message. Pure."""
    closes = f"{_clock(n.closes_at)} ({n.closes_at.isoformat()})" if n.closes_at else "—"
    allowed = {"push": "a push (tell_owner urgent=true), or lower", "note": "a note on Today (tell_owner), or silence",
               "thread": "a reply in that chat (send_message), or a note", "ambient": "the house screen"}.get(
        d.channel, d.channel)
    out = [f"# Something you noticed — {_local(now):%A} {_clock(now)}",
           f"Claim: {n.claim}",
           f"Kind: {n.kind} · category: {n.category or '—'} · who: {', '.join(n.who) or '—'} · closes: {closes}",
           f"Allowed at most: {allowed}. Why: {d.reason}. "
           + (f"Pushes left today: {pushes_left}." if pushes_left is not None else "No daily push limit.")]
    if getattr(d, "advice", ""):
        out.append(f"{_SPEAK_ADVICE} {d.advice}.")
    out.append("\n## The sources, in full")
    for s in sources:
        out.append(f"- {s['ref']} · {s.get('chat_name') or s.get('chat') or '?'} · {s.get('speaker') or '?'} · "
                   f"{s.get('clock') or '?'}" + (f" · {s['read']}" if s.get("read") else ""))
        out.append("  " + str(s.get("text") or "").replace("\n", "\n  "))
    for name, lines in chats.items():
        if lines:
            out.append(f"\n## {name}: the last lines")
            out.extend(lines)
    if owner_lines:
        out.append("\n" + _SPEAK_OWNER_LINES)
        out.extend(owner_lines)
    out.append("\n" + _SPEAK_SAID_TODAY)
    out.extend(list(said_today) or ["(nothing)"])
    if digest_lines:
        out.append("\n" + _SPEAK_HOW_REACHED)
        out.extend(f"- {x}" for x in digest_lines)
    if instructions:
        out.append("\n" + _SPEAK_INSTRUCTIONS)
        out.extend(f"- {x}" for x in instructions)
    return "\n".join(out)


async def _speak_pack(row: Any, n: Any, d: Any, now: datetime) -> str:
    from sqlalchemy import select

    from services import reach_service
    from services.database import HeartbeatEventModel, NoticingModel, async_session

    ids = [s.ref[3:] for s in n.sources if s.ref.startswith("hb:")]
    mem_ids = [s.ref[4:] for s in n.sources if s.ref.startswith("mem:")]
    mems: Dict[str, str] = {}
    if mem_ids:
        try:
            from services.database import MemoryModel

            async with async_session() as s:
                mems = {m.id: m.content for m in (await s.execute(
                    select(MemoryModel).where(MemoryModel.id.in_(mem_ids)))).scalars().all()}
        except Exception:  # noqa: BLE001
            mems = {}
    async with async_session() as s:
        evs = {r.id: r for r in (await s.execute(select(HeartbeatEventModel)
                                                 .where(HeartbeatEventModel.id.in_(ids)))).scalars().all()} if ids else {}
        said = (await s.execute(select(NoticingModel).where(
            NoticingModel.surfaced_at >= _naive(now) - timedelta(hours=24), NoticingModel.id != row.id)
            .order_by(NoticingModel.surfaced_at.asc()))).scalars().all()
    reads = await _read_states(list(evs.values()))
    sources = []
    for src in n.sources:
        ev = evs.get(src.ref[3:]) if src.ref.startswith("hb:") else None
        if src.ref.startswith("mem:"):
            text = "Your own promise: " + (mems.get(src.ref[4:]) or "(the intention is gone)")
        else:
            text = safe_text(ev) if ev is not None else "(the message is no longer stored)"
        sources.append({"ref": src.ref, "chat": src.chat, "chat_name": _chat_name(ev) if ev else src.chat,
                        "speaker": speaker(ev) if ev else (owner_name() if src.from_me else "?"),
                        "clock": _clock(src.at), "read": reads.get(ev.id) if ev else None, "text": text})
    chats: Dict[str, List[str]] = {}
    for chat in dict.fromkeys(src.chat for src in n.sources if src.chat):
        try:
            lines = await _chat_lines(chat, now, set(), SPEAK_CHAT_LINES)
        except Exception:  # noqa: BLE001
            lines = []
        name = next((_chat_name(x) for x in lines), chat)
        chats[name] = [f"- {_clock(x.created_at)} {speaker(x)}: {_line(x, text=safe_text(x))}" for x in lines]
    try:
        owner = [f"- {_chat_name(x)} · {_clock(x.created_at)}: {_line(x, text=safe_text(x))}"
               for x in await _owners_lines(now)]
    except Exception:  # noqa: BLE001
        owner = []
    said_today = [f"- {_clock(x.surfaced_at)} ({x.channel}): {x.claim[:300]}" for x in said]
    try:
        digest_lines = await digest(now)
    except Exception:  # noqa: BLE001
        digest_lines = []
    try:
        from services.mind_service import _instructions_source

        instructions = await _instructions_source(now)
    except Exception:  # noqa: BLE001
        instructions = []
    budget = reach_service.push_budget()
    left: Optional[int] = None
    if budget is not None:
        pushes = await reach_service.autonomous_pushes_today(now)
        left = max(0, budget - (pushes if pushes is not None else budget))
    return render_speak_pack(n=n, d=d, sources=sources, chats=chats, owner_lines=owner, said_today=said_today,
                             digest_lines=digest_lines, instructions=instructions, pushes_left=left, now=now)


def fallback_text(n: Any) -> tuple:
    """(title, body) of the templated push: only the source's sender, its
    time and the close — no claim, no model. Pure."""
    inbound = [s for s in n.sources if not s.from_me] or list(n.sources)
    newest = max(inbound, key=lambda s: s.at) if inbound else None
    who = (n.who[0] if n.who else None) or (newest.chat if newest else None) or "someone"
    when = f" at {_clock(newest.at)}" if newest else ""
    before = f"before {_clock(n.closes_at)}" if n.closes_at else "soon"
    body = f"A text from {who}{when} looks like it needs you {before} — I couldn't check it in time. Tap for what I saw."
    return (f"Needs you {before}"[:60], body)


async def _fallback_push(row: Any, n: Any) -> None:
    from services import reach_service

    title, body = fallback_text(n)
    try:
        out = await reach_service.tell_owner(body, title=title, urgent=True, origin="fallback", noticing_id=row.id,
                                           dedupe_key=row.dedupe_key)
        print(f"[notice] fallback {row.id[:8]}: {out}")
    except Exception as exc:  # noqa: BLE001
        await _update_noticing(row.id, status="failed", decision_reason=f"fallback failed: {type(exc).__name__}")


async def _ambient(row: Any, n: Any, d: Any, now: datetime) -> bool:
    try:
        import inspect

        from services import expression_service

        fn = expression_service.run_expression_pulse
        if "extra" not in inspect.signature(fn).parameters:
            return False
        await fn("notice", extra=render_ambient(n, d))
        await _update_noticing(row.id, status="spoken", channel="ambient", surfaced_at=now)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] ambient failed for {row.id[:8]} ({type(exc).__name__})")
        return False


async def _surface(row: Any, n: Any, d: Any, now: datetime, reservation: Optional[int] = None) -> None:
    """Act on a decision in ``notice`` mode."""
    from dataclasses import replace as _replace

    if d.is_fallback:
        await _fallback_push(row, n)
        return
    if d.channel == "ambient":
        if await _ambient(row, n, d, now):
            return
        d = _replace(d, channel="note", reason=f"{d.reason}; no ambient surface")
    await _speak(row, n, d, now, reservation)
    if "ambient" in getattr(d, "also", ()):
        await _ambient(row, n, d, now)


async def _speak_reserve(row: Any, d: Any) -> Optional[int]:
    """One ``speak`` ledger row per speak turn, written before it starts
    (outcome ``started``) — the speak budget counts it from then on."""
    from services.speak_gate import chat_key

    return await _ledger(kind="speak", source=d.channel, outcome="started", ref_id=row.id,
                         detail={"chat": chat_key(row.chat_identifier) or None, "noticing": row.id,
                                 "category": row.category, "kind": row.kind})


async def _speak_finish(reservation: Optional[int], outcome: str, note: Optional[str]) -> None:
    if not reservation:
        return
    try:
        from sqlalchemy import update

        from services.database import LedgerEventModel, async_session

        async with async_session() as s:
            await s.execute(update(LedgerEventModel).where(LedgerEventModel.id == reservation).values(
                outcome=outcome[:20], reason=(" ".join((note or "").split())[:300] or None)))
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


_CLOSING_MARK = re.compile(r"(?:(?<=\s)|^)(DROP|LEFT):", re.IGNORECASE)


def closing_drop(note: Any) -> Optional[str]:
    """The speaker's DROP, read where its prompt puts it: "End with one line:
    what you told the owner; or "DROP: …"; or "LEFT: …"". The wake's note is
    flattened to one line, so the closing line is the last ``DROP:`` /
    ``LEFT:`` that starts the note or follows a space. Its reason when that
    is a DROP, else None. The first real ``--speak`` (2026-10-11) ended
    "… Nothing is due tonight. DROP: the claim misreads the BA text", and a
    ``startswith("DROP:")`` read it as "speaker left it". Pure."""
    text = str(note or "").strip()
    marks = list(_CLOSING_MARK.finditer(text))
    if not marks or marks[-1].group(1).upper() != "DROP":
        return None
    return text[marks[-1].end():].strip()


async def _speak(row: Any, n: Any, d: Any, now: datetime, reservation: Optional[int] = None) -> None:
    """The Opus ``speak`` wake: verify, prepare, one offer, through
    ``tell_owner`` (or ``send_message`` for a thread). It can only lower."""
    import inspect

    from services import mind_service, reach_service

    timeout = (SPEAK_URGENT_TIMEOUT_S if (n.closes_at is not None and n.closes_at - _local(now) < SPEAK_URGENT_WINDOW)
               else SPEAK_TIMEOUT_S)
    if reservation is None:
        reservation = await _speak_reserve(row, d)
    attempts = int(row.attempts or 0) + 1
    await _update_noticing(row.id, attempts=attempts)
    pack = await _speak_pack(row, n, d, now)
    kwargs: Dict[str, Any] = dict(message=pack, conversation_id=getattr(mind_service, "SPEAK_CONVERSATION_ID",
                                                                        SPEAK_CONVERSATION_ID),
                                  system_prompt=SPEAK_SYSTEM_PROMPT, skip_extraction=True,
                                  memory_query=(n.claim or "")[:400], max_history_chars=0,
                                  tools=legacy_speak_tools(d.channel))
    try:
        params = inspect.signature(mind_service.wake).parameters
        accepts_any = any(p.kind == p.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        params, accepts_any = {}, True
    if accepts_any or "policy" in params:
        try:
            # The TurnPolicy, built with only the fields it has.
            policy = mind_service.make_policy(speak_policy_kwargs(d.channel, n.chat, timeout))
            if policy is not None:
                kwargs["policy"] = policy
        except Exception:  # noqa: BLE001 — legacy rides allowed_tools
            pass
    if accepts_any or "timeout_s" in params:
        kwargs["timeout_s"] = timeout
    with reach_service.speak_scope(noticing_id=row.id, channel=d.channel, floor_push=d.floor_push, who=n.who,
                                   sensitive=n.sensitive, dedupe_key=n.dedupe_key, category=n.category,
                                   closes_at=n.closes_at) as scope:
        try:
            out = await asyncio.wait_for(mind_service.wake("speak", **kwargs), timeout + 60)
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "note": f"{type(exc).__name__}: {str(exc)[:160]}", "error_class": type(exc).__name__,
                   "transient": True, "reached": False}
    out = out or {}
    note = str(out.get("note") or "").strip()
    thought_id = out.get("thought_id")
    sent = any(isinstance(t, dict) and t.get("n") in SEND_TOOLS and t.get("ok", True) for t in out.get("tools") or [])
    later = _utcnow()
    if out.get("skipped") and not scope.delivered:
        await _update_noticing(row.id, status="silent", decision_reason=f"the speak wake was skipped: {note[:300]}")
        await _speak_finish(reservation, "skipped", note)
    elif scope.delivered:
        await _update_noticing(row.id, status="spoken", thought_id=thought_id,
                               **({"ledger_id": int(out["ledger_id"])} if str(out.get("ledger_id") or "").isdigit()
                                  else {}))
        await _speak_finish(reservation, "spoken", note)
    elif sent:
        await _update_noticing(row.id, status="spoken", channel="thread", surfaced_at=later, thought_id=thought_id,
                               **({"ledger_id": int(out["ledger_id"])} if str(out.get("ledger_id") or "").isdigit()
                                  else {}))
        await _speak_finish(reservation, "spoken", note)
    elif out.get("ok") and closing_drop(note) is not None:
        await _update_noticing(row.id, status="dropped", decision_reason=str(closing_drop(note))[:500],
                               thought_id=thought_id)
        await _speak_finish(reservation, "dropped", note)
    elif out.get("ok"):
        await _update_noticing(row.id, status="silent", decision_reason=f"speaker left it: {note[:400]}",
                               thought_id=thought_id)
        await _speak_finish(reservation, "silent", note)
    else:
        await _speak_finish(reservation, "failed", note)
        closing_soon = n.closes_at is not None and n.closes_at - _local(later) < SPEAK_URGENT_WINDOW
        if d.floor_push and closing_soon:
            await _fallback_push(row, n)
        elif attempts < MAX_SPEAK_ATTEMPTS and not out.get("reached"):
            await _update_noticing(row.id, status="failed", due_at=later + SPEAK_RETRY_AFTER,
                                   decision_reason=f"speak failed: {note[:300]}")
        else:
            await _update_noticing(row.id, status="failed", due_at=None,
                                   decision_reason=f"speak failed: {note[:300]}")


# ============================================================= the sweep --

async def _sweep_stranded(now: datetime) -> Dict[str, Any]:
    """``open`` noticings nobody is surfacing: a speak wake the process died
    under (every deploy is a SIGKILL kickstart), or a row whose gate never
    ran. Each becomes ``failed``, due now while attempts remain, so
    ``_sweep_due`` re-gates it with fresh facts; past them it stays failed.
    Never one this process is still surfacing (review 2026-10-11)."""
    if mode() == "off":
        return {"stranded": 0}
    from sqlalchemy import func, select

    from services.database import NoticingModel, async_session

    cutoff = _naive(now) - STRANDED_AFTER
    touched = func.coalesce(NoticingModel.updated_at, NoticingModel.decided_at, NoticingModel.created_at)
    async with async_session() as s:
        rows = list((await s.execute(select(NoticingModel).where(
            NoticingModel.status == "open", touched <= cutoff).limit(50))).scalars().all())
    stranded = 0
    for r in rows:
        if r.id in _SURFACING:
            continue
        note = "left open with no speaker (a restart or a crash); re-gated by the sweep"
        if int(r.attempts or 0) < MAX_SPEAK_ATTEMPTS:
            await _update_noticing(r.id, status="failed", due_at=now, decision_reason=note)
        else:
            await _update_noticing(r.id, status="failed", due_at=None, decision_reason=note)
        stranded += 1
    if stranded:
        print(f"[notice] sweep: {stranded} noticing(s) left open with no speaker, failed for a re-gate")
    return {"stranded": stranded}


async def _sweep_due(now: datetime) -> Dict[str, Any]:
    """Held items whose time came, and failed speaks due a retry, re-gated
    with fresh facts (the read state may have silenced them since)."""
    if mode() == "off":
        return {"due": 0}
    from sqlalchemy import and_, or_, select

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        ids = list((await s.execute(select(NoticingModel.id).where(
            NoticingModel.due_at.is_not(None), NoticingModel.due_at <= _naive(now),
            or_(NoticingModel.status == "held",
                and_(NoticingModel.status == "failed", NoticingModel.attempts < MAX_SPEAK_ATTEMPTS)))
            .order_by(NoticingModel.due_at.asc()).limit(20))).scalars().all())
    for nid in ids:
        await _gate_safely(nid, now=None)
    return {"due": len(ids)}


async def _sweep_retry_failed_passes(now: datetime) -> Dict[str, Any]:
    """A failed noticer pass is retried once, ten minutes on, by its batch id;
    a second failure stamps ``notice-failed2:``."""
    if mode() == "off":
        return {"retried": 0}
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(HeartbeatEventModel.id, HeartbeatEventModel.notification_batch_id).where(
            HeartbeatEventModel.notification_batch_id.like("notice-failed:%"),
            HeartbeatEventModel.notified_at <= _naive(now) - timedelta(minutes=RETRY_FAILED_AFTER_MIN)))).all()
    batches: Dict[str, List[str]] = {}
    for eid, batch in rows:
        batches.setdefault(batch.split(":", 1)[1], []).append(eid)
    # Each retry is a model call (≤ 45 s): it runs as a tracked task, so the
    # */5 sweep stays inside its 120 s event budget.
    for pass_id, ids in list(batches.items())[:3]:
        _track(_retry_safely(ids, pass_id))
    return {"retried": min(len(batches), 3)}


async def _retry_safely(ids: List[str], pass_id: str) -> None:
    try:
        await notice_pass(ids, retry_of=pass_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[notice] retry of {pass_id[:8]} crashed ({type(exc).__name__})")


async def _sweep_expiry(now: datetime) -> Dict[str, Any]:
    """Ambient items past their close (or two hours) are expired."""
    from sqlalchemy import select

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(NoticingModel).where(NoticingModel.status == "spoken",
                                                            NoticingModel.channel == "ambient"))).scalars().all()
        n = 0
        for r in rows:
            expiry = r.closes_at or ((r.surfaced_at + timedelta(hours=2)) if r.surfaced_at else None)
            if expiry is not None and expiry < _naive(now):
                r.status = "expired"
                r.updated_at = _naive(now)
                n += 1
        await s.commit()
    return {"expired": n}


# ============================================================ outcomes --

from dataclasses import dataclass as _dataclass, field as _field  # noqa: E402

OUTCOMES = ("opened", "acted", "seen", "dismissed", "ignored", "rejected")
SEEN_WITHIN = timedelta(minutes=10)
IN_EXCHANGE_BEFORE = timedelta(minutes=30)
DEFAULT_WINDOW = timedelta(hours=24)
OUTCOME_LOOKBACK = timedelta(days=HISTORY_DAYS)


@_dataclass(frozen=True)
class OwnerLine:
    chat: Optional[str]
    at: datetime
    chat_name: Optional[str] = None


@_dataclass(frozen=True)
class OutcomeItem:
    channel: Optional[str]
    surfaced_at: datetime
    closes_at: Optional[datetime]
    chat: Optional[str]
    who: Tuple[str, ...] = ()
    kind: Optional[str] = None
    origin: str = "noticer"


@_dataclass(frozen=True)
class OutcomeSignals:
    verdict: Optional[str] = None                 # right_call | wrong_call
    push_opened_at: Optional[datetime] = None
    reaction_at: Optional[datetime] = None        # the owner's tapback on Edward's message for it
    reply_at: Optional[datetime] = None           # the owner's inline reply to it
    care_logged_at: Optional[datetime] = None     # the dose logged after the alert
    owner_lines: Tuple[OwnerLine, ...] = ()           # the owner's own non-echo lines after it surfaced
    owner_prior_in_chat_30m: bool = False           # they were already in exchange in that chat
    note_dismissed_at: Optional[datetime] = None


def _names(text: Optional[str], who: Sequence[str]) -> bool:
    low = (text or "").lower()
    return any(w and re.search(r"(?<!\w)" + re.escape(w.lower()) + r"(?!\w)", low) for w in who)


def classify_outcome(item: OutcomeItem, sig: OutcomeSignals, now: datetime) -> Optional[str]:
    """What came of it, first match wins: ``rejected`` (a wrong call),
    ``opened`` (the push opened, or a right call), ``acted`` (a tapback or
    reply on Edward's message, the dose logged, or the owner messaging the
    chat or a named person before it closed — unless they were already in
    exchange there in the 30 min before), ``dismissed`` (the note's "Done"),
    then once the window closed (``closes_at``, else a day): ``seen`` (a push
    followed within 10 min by any line of the owner's, or a confounded reply) or
    ``ignored``. ``None`` while it is still open. Pure."""
    from services.speak_gate import chat_key

    if sig.verdict == "wrong_call":
        return "rejected"
    if sig.push_opened_at is not None or sig.verdict == "right_call":
        return "opened"
    start = item.surfaced_at
    end = item.closes_at or (start + DEFAULT_WINDOW)
    if any(t is not None and t >= start for t in (sig.reaction_at, sig.reply_at, sig.care_logged_at)):
        return "acted"
    key = chat_key(item.chat)
    replied = [x for x in sig.owner_lines
               if start < x.at <= end and ((key and chat_key(x.chat) == key) or _names(x.chat_name, item.who))]
    if replied and not sig.owner_prior_in_chat_30m:
        return "acted"
    if sig.note_dismissed_at is not None:
        return "dismissed"
    if now < end:
        return None
    if replied:                                   # the owner was already talking there: neutral
        return "seen"
    if item.channel == "push" and any(start < x.at <= start + SEEN_WITHIN for x in sig.owner_lines):
        return "seen"
    return "ignored"


async def _signals_for(row: Any, now: datetime) -> OutcomeSignals:
    from sqlalchemy import select

    from services.database import (EdwardNoteModel, HeartbeatEventModel, PushLogModel, UserSignalModel,
                                   async_session)
    from services.speak_gate import chat_key

    surfaced = _aware(row.surfaced_at)
    end = _aware(row.closes_at) if row.closes_at else surfaced + DEFAULT_WINDOW
    verdict = opened = dismissed = care = None
    async with async_session() as s:
        if row.ledger_id:
            sig = (await s.execute(select(UserSignalModel).where(
                UserSignalModel.kind == "mind_verdict", UserSignalModel.ref_id == str(row.ledger_id))
                .order_by(UserSignalModel.created_at.desc()).limit(1))).scalar_one_or_none()
            if sig is not None:
                try:
                    verdict = (json.loads(sig.detail_json or "{}") or {}).get("verdict")
                except (TypeError, ValueError):
                    verdict = None
        if row.push_log_id:
            opened = (await s.execute(select(PushLogModel.opened_at).where(
                PushLogModel.id == row.push_log_id))).scalar_one_or_none()
        if row.note_id:
            dismissed = (await s.execute(select(EdwardNoteModel.dismissed_at).where(
                EdwardNoteModel.id == row.note_id))).scalar_one_or_none()
        lines = (await s.execute(select(HeartbeatEventModel).where(
            HeartbeatEventModel.is_from_user.is_(True),
            HeartbeatEventModel.created_at >= _naive(surfaced) - IN_EXCHANGE_BEFORE,
            HeartbeatEventModel.created_at <= _naive(max(end, surfaced + SEEN_WITHIN)))
            .order_by(HeartbeatEventModel.created_at.asc()).limit(300))).scalars().all()
        if row.origin == "care" and row.dedupe_key and row.dedupe_key.startswith("care:"):
            # care_watch keys one alert per pet and slot: care:<pet>:<care day>:<HH:MM>.
            try:
                from services.database import CareEventModel, CareItemModel

                parts = row.dedupe_key.split(":")
                pet, slot = parts[1], ":".join(parts[3:5]) if len(parts) >= 5 else None
                logged = (await s.execute(
                    select(CareEventModel.given_at, CareItemModel.at_time)
                    .join(CareItemModel, CareItemModel.id == CareEventModel.care_item_id)
                    .where(CareItemModel.pet_id == pet, CareEventModel.given_at >= _naive(surfaced))
                    .order_by(CareEventModel.given_at.asc()))).all()
                care = next((given for given, at_time in logged
                             if slot is None or (at_time is not None and at_time.strftime("%H:%M") == slot)), None)
            except Exception:  # noqa: BLE001
                care = None
    sends = await _recent_sends(now)
    key = chat_key(row.chat_identifier)
    after, prior = [], False
    for ln in lines:
        if _is_echo(ln, sends) or is_ping_event(ln):
            continue
        at = _aware(ln.created_at)
        if at <= surfaced:
            if key and chat_key(ln.chat_identifier) == key:
                prior = True
            continue
        after.append(OwnerLine(chat=ln.chat_identifier, at=at, chat_name=ln.chat_name))
    reaction = reply = None
    guids = await _edward_guids_for(row, surfaced, end)
    if guids:
        try:
            from services import imessage_read_state

            for items in (await imessage_read_state.areactions_to(guids)).values():
                for x in items:
                    if x.get("from_me") and x.get("at"):
                        reaction = min(reaction or x["at"], x["at"])
            for items in (await imessage_read_state.areplies_to(guids)).values():
                for x in items:
                    if x.get("from_me") and x.get("at"):
                        reply = min(reply or x["at"], x["at"])
        except Exception:  # noqa: BLE001
            pass
    return OutcomeSignals(verdict=verdict, push_opened_at=_aware(opened) if opened else None, reaction_at=reaction,
                          reply_at=reply, care_logged_at=_aware(care) if care else None, owner_lines=tuple(after),
                          owner_prior_in_chat_30m=prior, note_dismissed_at=_aware(dismissed) if dismissed else None)


async def _edward_guids_for(row: Any, start: datetime, end: datetime) -> List[str]:
    """Guids of Edward's own messages for this noticing (a thread reply, or
    the owner's iMessage channel), when the send ledger records them."""
    if row.channel not in ("thread", "imessage"):
        return []
    try:
        from sqlalchemy import text as sql

        from services.database import async_session

        async with async_session() as s:
            res = await s.execute(sql("SELECT * FROM imessage_outgoing_sends WHERE sent_at >= :a AND sent_at <= :b"),
                                  {"a": start, "b": end})
            rows = [dict(r._mapping) for r in res]
        from services.speak_gate import chat_key

        key = chat_key(row.chat_identifier)
        out = []
        for r in rows:
            g = r.get("message_guid") or r.get("guid")
            if g and (row.channel == "imessage" or chat_key(r.get("chat_identifier")) == key):
                out.append(str(g))
        return out
    except Exception:  # noqa: BLE001
        return []


async def _sweep_outcomes(now: datetime) -> Dict[str, Any]:
    """Classify what came of each surfaced noticing that has no outcome yet."""
    from sqlalchemy import select

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(NoticingModel).where(
            NoticingModel.status == "spoken", NoticingModel.outcome.is_(None),
            NoticingModel.surfaced_at.is_not(None),
            NoticingModel.surfaced_at >= _naive(now) - OUTCOME_LOOKBACK)
            .order_by(NoticingModel.surfaced_at.asc()).limit(50))).scalars().all()
    n = 0
    for row in rows:
        try:
            who = tuple(json.loads(row.who_json or "[]"))
        except (TypeError, ValueError):
            who = ()
        item = OutcomeItem(channel=row.channel, surfaced_at=_aware(row.surfaced_at),
                           closes_at=_aware(row.closes_at) if row.closes_at else None, chat=row.chat_identifier,
                           who=who, kind=row.kind, origin=row.origin or "noticer")
        try:
            outcome = classify_outcome(item, await _signals_for(row, now), _aware(now))
        except Exception as exc:  # noqa: BLE001
            print(f"[notice] outcome for {row.id[:8]} failed ({type(exc).__name__})")
            continue
        if outcome:
            await _update_noticing(row.id, outcome=outcome, outcome_at=now)
            n += 1
    return {"outcomes": n}


# ===================================================== levels and digest --

async def _category_levels(now: datetime) -> Dict[str, Any]:
    """Each category's ceiling, when it last moved, its last outcomes and when
    a demotion's probation ends — for the report and the digest."""
    from services import speak_gate

    history = await _history(now)
    local_now = _local(now)
    out: Dict[str, Any] = {}
    for cat in speak_gate.CATEGORIES:
        mine = sorted((h for h in history if h.category == cat and h.outcome), key=lambda h: h.at)
        level = speak_gate.channel_ceiling(cat, mine, local_now)
        since = None
        prev = speak_gate.channel_ceiling(cat, (), mine[0].at) if mine else level
        for i, h in enumerate(mine):
            lv = speak_gate.channel_ceiling(cat, mine[: i + 1], h.at)
            if lv != prev:
                since, prev = h.at, lv
        state = speak_gate.ladder_state(cat, mine, local_now)
        ends = state.get("probation_ends")
        probation = ends.date().isoformat() if isinstance(ends, datetime) else None
        out[cat] = {"level": level, "since": since.isoformat() if since else None,
                    "last": [h.outcome for h in mine[-4:]], "acted": sum(1 for h in mine if h.outcome in ("acted", "opened")),
                    "surfaced": len(mine), "probation_ends": probation}
    return out


async def _digest_lines(now: datetime) -> List[str]:
    """Today's counts and how the owner likes to be reached, in code."""
    from sqlalchemy import select

    from services.database import NoticingModel, async_session
    from services.push_ledger import local_day_bounds_utc

    start, end = local_day_bounds_utc(now)
    async with async_session() as s:
        rows = (await s.execute(select(NoticingModel.status, NoticingModel.channel, NoticingModel.origin).where(
            NoticingModel.created_at >= start, NoticingModel.created_at < end))).all()
    rows = [r for r in rows if r[2] != "care"]
    spoken = [r for r in rows if r[0] == "spoken"]
    by_ch: Dict[str, int] = {}
    for _st, ch, _o in spoken:
        by_ch[ch or "?"] = by_ch.get(ch or "?", 0) + 1
    chans = ", ".join(f"{v} {k}" for k, v in sorted(by_ch.items()))
    held = sum(1 for r in rows if r[0] == "held")
    silent = sum(1 for r in rows if r[0] in ("silent", "dropped"))
    lines = [f"Noticed {len(rows)}, spoke {len(spoken)}" + (f" ({chans})" if chans else "")
             + f", held {held}, silent {silent} today."]
    levels = await _category_levels(now)
    for cat, v in levels.items():
        if cat != "security" and not v["surfaced"]:
            continue
        name = cat.capitalize()
        if cat == "security":
            lines.append("Security: note (always).")
            continue
        bits = [f"{v['acted']} of {v['surfaced']} acted"]
        ignored = sum(1 for o in v["last"] if o == "ignored")
        if ignored:
            bits.append(f"{ignored} ignored lately")
        if v.get("probation_ends"):
            bits.append(f"probation ends {v['probation_ends'][5:]}")
        lines.append(f"{name}: {v['level']} ({'; '.join(bits)}).")
    return lines


# ================================================ promises whose time came --

PING_TURN_SOURCES = ("ping", "ping_owner")
INTENTION_LOOKBACK = timedelta(hours=24)


async def _sweep_intentions(now: datetime) -> Dict[str, Any]:
    """An intention made in a ping whose horizon arrived with no wake waiting
    on it becomes a ``follow_through`` noticing: the promise is kept, or the
    owner hears it was not. Closed intentions, ones a pending
    event carries, and promises not made in a ping are left alone. Keyed
    per intention, so a sweep never adds one twice."""
    if mode() == "off":
        return {"intentions": 0}
    from sqlalchemy import select

    from services.database import MemoryModel, ScheduledEventModel, async_session
    from services.scheduled_events_service import linked_memory_id

    naive = _naive(now)
    async with async_session() as s:
        rows = (await s.execute(select(MemoryModel).where(
            MemoryModel.memory_type == "intention", MemoryModel.valid_until.is_not(None),
            MemoryModel.valid_until <= naive, MemoryModel.valid_until >= naive - INTENTION_LOOKBACK)
            .limit(50))).scalars().all()
        waiting = {linked_memory_id(m) for (m,) in (await s.execute(select(ScheduledEventModel.event_metadata).where(
            ScheduledEventModel.status.in_(("pending", "processing")),
            ScheduledEventModel.event_metadata.is_not(None)))).all()}
    made = 0
    for m in rows:
        try:
            ref = json.loads(m.source_ref or "{}") or {}
        except (TypeError, ValueError):
            ref = {}
        if not isinstance(ref, dict) or ref.get("closed") or m.id in waiting:
            continue
        if ref.get("turn_source") not in PING_TURN_SOURCES and not ref.get("ping_id"):
            continue
        chat = ref.get("chat_identifier")
        key = f"intention:{m.id}"
        before = await _noticing_by_key(key)
        nid = await add_noticing(
            kind="follow_through", origin="sweep", category="social",
            claim=(f"You promised in {chat or 'a chat'}: {str(m.content or '')[:300]} — its time came with "
                   f"nothing scheduled."),
            sources=[{"ref": f"mem:{m.id}", "chat": chat,
                      "at": _aware(m.created_at).isoformat() if m.created_at else _aware(now).isoformat(),
                      "from_me": False}],
            chat_identifier=chat, dedupe_key=key, suggested="thread")
        if nid and not before:
            made += 1
            await _gate_safely(nid)
    return {"intentions": made}


async def _noticing_by_key(key: str) -> Optional[str]:
    from sqlalchemy import select

    from services.database import NoticingModel, async_session

    async with async_session() as s:
        return (await s.execute(select(NoticingModel.id).where(NoticingModel.dedupe_key == key).limit(1))
                ).scalar_one_or_none()
