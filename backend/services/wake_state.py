"""Edward's own live state — is he waking right now, and what is around him.

2026-09-25, the calm-presence design (``docs/design-system-2026-09-25.md``):
one live Edward on every screen whose mood comes from a real signal. "The
animation is a claim, so it must be true" — never "working" when nothing
runs, never "fine" when his hands are down. This module is where those
signals meet:

* ``begin(reason, conversation_id)`` / ``end()`` / ``current()`` — the wakes
  running in this process right now. ``mind_service.wake`` calls them
  around every wake, and the surfaces pulse's turn is a wake
  (``wake("surfaces", …)``), so both are covered. Task-safe: each wake
  holds a token, and ``end()`` without one ends the calling task's own.
* ``snapshot()`` — ``GET /api/mind/now``: the running wake, the mind's last
  few ledger rows, when he wakes next, the approvals and unread thoughts
  waiting on the owner, the quota meter and his hands in Chrome. Every source is
  read on its own short bound; one that fails leaves null or 0, never a
  500. ``compose_now`` (pure) assembles it.

Not ``/api/presence``: that is the owner's device beacons.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

UTC = timezone.utc
MAX_RECENT = 5
SOURCE_TIMEOUT_SECONDS = 2.0
# The pulse's turn is a wake too, but a mind wake is the better answer to
# "what is he doing" when both run at once.
PULSE_REASONS = ("surfaces",)
# The scheduled rows that wake the mind: a ``wake_me`` and the ticks (the
# nightly, since 2026-10-11).
WAKE_EVENT_TYPES = ("mind_wake", "wakeup_tick")
# A speak wake's closing line is what he told the owner — a noticing's
# claim, which never reaches a glanceable surface (docs/architecture/
# noticing.md). The rows here feed the presence sheet, Today's hero and the
# stage, so they carry this fixed line instead (the stage lane and the
# frontend use the same words).
SPEAK_LINE = "Something came up — I left a note."


def reason_label(reason: Optional[str]) -> str:
    """Words for a wake's reason ("Nightly reflection", "Something I
    noticed"), from the thought journal's table; the reason itself when it
    has none."""
    from services.thought_journal import REASON_LABELS

    return REASON_LABELS.get(str(reason or ""), str(reason or ""))


# ---- the wakes running now ------------------------------------------------------------

@dataclass(frozen=True)
class _Wake:
    reason: str
    conversation_id: str
    started_at: datetime
    seq: int


_active: Dict[int, _Wake] = {}
_seq = itertools.count(1)
_mine: contextvars.ContextVar[tuple] = contextvars.ContextVar("edward_wake_tokens", default=())


def begin(reason: str, conversation_id: str, *, now: Optional[datetime] = None) -> int:
    """A wake started; returns its token (also remembered for this task)."""
    token = next(_seq)
    _active[token] = _Wake(reason=str(reason or "?"), conversation_id=str(conversation_id or ""),
                           started_at=_aware(now) or datetime.now(UTC), seq=token)
    _mine.set(_mine.get() + (token,))
    return token


def end(token: Optional[int] = None) -> None:
    """A wake finished: ``token``, else the calling task's latest. Idempotent."""
    mine = _mine.get()
    if token is None:
        if not mine:
            return
        token = mine[-1]
    _active.pop(token, None)
    if token in mine:
        _mine.set(tuple(t for t in mine if t != token))


def current() -> Optional[Dict[str, Any]]:
    """The wake to show: the earliest-started mind wake running now, else
    the pulse's turn, else None."""
    if not _active:
        return None
    wakes = list(_active.values())
    mind = [w for w in wakes if w.reason not in PULSE_REASONS]
    w = min(mind or wakes, key=lambda x: (x.started_at, x.seq))
    return {"reason": w.reason, "started_at": _z(w.started_at), "conversation_id": w.conversation_id}


def reset() -> None:
    """Tests: forget every running wake."""
    _active.clear()
    _mine.set(())


# ---- pure ------------------------------------------------------------------------------

def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _z(value: Any) -> Optional[str]:
    """A timestamp as Z-suffixed UTC to the second. Naive means UTC (every
    naive column Edward writes is UTC — the DB session is pinned to it)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return _aware(value).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _wake_row(row: Dict[str, Any]) -> Dict[str, Any]:
    line = " ".join(str(row.get("note") or "").split())
    if line and row.get("source") == "speak":
        line = SPEAK_LINE
    thought = row.get("thought_id")
    return {"at": _z(row.get("at")), "reason": row.get("source"), "outcome": row.get("outcome"),
            "line": line or None, "thought_id": thought if isinstance(thought, str) and thought else None}


def quota_view(meter: Any, now_wall: float) -> Dict[str, Any]:
    """The meter's primary window: used %, exhausted?, and when it resets.
    A window that has reset since the sample reads as unknown — the old
    number is no longer true. Pure."""
    from services.expression_service import quota_state_for

    out: Dict[str, Any] = {"used_percent": None, "exhausted": False, "resets_at": None}
    if meter is None:
        return out
    try:
        out["exhausted"] = quota_state_for(meter, now_wall, True) == "exhausted"
    except Exception:  # noqa: BLE001
        pass
    windows = getattr(meter, "windows", None) or {}
    if not windows:
        return out
    w = windows.get("primary") or next(iter(windows.values()))
    remaining = w.reset_after_at(meter.sampled_at, now_wall)
    if remaining <= 0:
        return out
    out["used_percent"] = round(float(w.used_percent), 2)
    out["resets_at"] = datetime.fromtimestamp(now_wall + remaining, UTC)
    return out


def hands_view(status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The Chrome extension link: connected, or down for how long. Pure."""
    if not isinstance(status, dict) or "connected" not in status:
        return {"connected": None, "down_for_s": None}
    if status.get("connected"):
        return {"connected": True, "down_for_s": None}
    down = status.get("down_for_s")
    return {"connected": False, "down_for_s": int(down) if isinstance(down, (int, float)) else None}


def compose_now(*, now: datetime, waking: Optional[Dict[str, Any]], recent: Optional[Sequence[Dict[str, Any]]],
                next_wake_at: Any, approvals_waiting: Optional[int], unread_thoughts: Optional[int],
                quota: Optional[Dict[str, Any]], hands: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``GET /api/mind/now``'s body. Pure; every timestamp Z-suffixed UTC."""
    rows = [_wake_row(r) for r in list(recent or [])[:MAX_RECENT]]
    q = quota or {}
    h = hands or {}
    return {
        "now": _z(now),
        "waking": ({"reason": waking.get("reason"), "started_at": _z(waking.get("started_at")),
                    "conversation_id": waking.get("conversation_id")} if waking else None),
        "last": rows[0] if rows else None,
        "recent": rows,
        "next_wake_at": _z(next_wake_at),
        "approvals_waiting": int(approvals_waiting or 0),
        "unread_thoughts": int(unread_thoughts or 0),
        # ``period``: "week" (the Codex window) or "day" (the Claude spend
        # cap, 2026-10-11) — what his rest is waiting for.
        "quota": {"used_percent": q.get("used_percent"), "exhausted": bool(q.get("exhausted", False)),
                  "resets_at": _z(q.get("resets_at")), "period": q.get("period") or "week"},
        "hands": {"connected": h.get("connected"), "down_for_s": h.get("down_for_s")},
    }


# ---- I/O: each source on its own bound ----------------------------------------------------

async def _recent() -> List[Dict[str, Any]]:
    from services import mind_service

    return await mind_service.recent(limit=MAX_RECENT)


async def _next_scheduled_wake() -> Optional[datetime]:
    """The earliest pending ``wake_me`` or tick row (naive UTC)."""
    from sqlalchemy import or_, select

    from services.database import ScheduledEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(
            select(ScheduledEventModel.next_fire_at, ScheduledEventModel.event_metadata)
            .where(ScheduledEventModel.status == "pending",
                   or_(*[ScheduledEventModel.event_metadata.like(f'%"{t}"%') for t in WAKE_EVENT_TYPES]))
            .order_by(ScheduledEventModel.next_fire_at.asc()).limit(20)
        )).all()
    for at, raw in rows:
        try:
            meta = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("type") in WAKE_EVENT_TYPES:
            return at
    return None


async def _approvals_waiting(now: datetime) -> int:
    """Sends held for the owner's approval and not yet expired (24 h)."""
    from sqlalchemy import func, select

    from services.approvals_service import APPROVAL_TTL_HOURS
    from services.database import PendingApprovalModel, async_session

    cutoff = now.astimezone(UTC).replace(tzinfo=None) - timedelta(hours=APPROVAL_TTL_HOURS)
    async with async_session() as s:
        return int((await s.execute(
            select(func.count()).select_from(PendingApprovalModel)
            .where(PendingApprovalModel.status == "pending", PendingApprovalModel.created_at > cutoff)
        )).scalar() or 0)


async def _unread_thoughts() -> int:
    """Conversations Edward started that carry the sidebar's unread dot:
    ``notified_user`` on an Edward-initiated source, in a thread the sidebar
    lists (``conversation_service._visible_filter``)."""
    from sqlalchemy import func, select

    from services.conversation_service import EDWARD_OWN_SOURCES, _visible_filter
    from services.database import ConversationModel, async_session

    async with async_session() as s:
        return int((await s.execute(
            select(func.count()).select_from(ConversationModel)
            .where(ConversationModel.notified_user == True,  # noqa: E712
                   ConversationModel.source.in_(list(EDWARD_OWN_SOURCES)), _visible_filter())
        )).scalar() or 0)


def _quota_now() -> Dict[str, Any]:
    """The quota his presence reads: the Codex week, or — on the Claude API
    (``EDWARD_LLM_PROVIDER=anthropic``) — the day's spend cap, which rests
    him at the cap until the owner's local midnight. On Claude the
    Codex week says nothing about him (``codex_meter_governs``): a spent
    window no Claude call refreshes never shows him resting. ``period`` says
    which."""
    from services.expression_service import codex_meter_governs

    if not codex_meter_governs():
        from services import llm_spend

        return llm_spend.quota_now()
    from services.llm_rate_gate import get_llm_rate_gate

    gate = get_llm_rate_gate()
    return {**quota_view(gate.meter, gate._wall_clock()), "period": "week"}


def _hands_now() -> Dict[str, Any]:
    from services import browser_ext_service

    return hands_view(browser_ext_service.status())


def _pending_wake_due() -> Optional[datetime]:
    from services import mind_service

    return mind_service.pending_wake_due()


async def _bounded(label: str, make: Callable[[], Awaitable[Any]], default: Any) -> Any:
    try:
        return await asyncio.wait_for(make(), timeout=SOURCE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — one source never takes the others down
        print(f"[mind/now] {label} unavailable: {type(exc).__name__}: {exc}")
        return default


def _sync(label: str, fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[mind/now] {label} unavailable: {type(exc).__name__}: {exc}")
        return default


async def snapshot(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Edward's live state (``GET /api/mind/now``). Never raises."""
    now = _aware(now) or datetime.now(UTC)
    recent, scheduled, approvals, unread = await asyncio.gather(
        _bounded("recent wakes", lambda: _recent(), []),
        _bounded("next wake", lambda: _next_scheduled_wake(), None),
        _bounded("approvals", lambda: _approvals_waiting(now), 0),
        _bounded("unread thoughts", lambda: _unread_thoughts(), 0),
    )
    candidates = [_aware(d) for d in (scheduled, _sync("pending wakes", _pending_wake_due, None)) if d]
    return compose_now(
        now=now,
        waking=_sync("waking", current, None),
        recent=recent,
        next_wake_at=min(candidates) if candidates else None,
        approvals_waiting=approvals,
        unread_thoughts=unread,
        quota=_sync("quota", _quota_now, None),
        hands=_sync("hands", _hands_now, None),
    )
