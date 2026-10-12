"""Pay-per-token spend: the Claude call ledger and the hard daily cap
(2026-10-10).

Under ``EDWARD_LLM_PROVIDER=anthropic`` there is no weekly Codex window to
pace against and nothing expires at a reset: every token is paid for from a
prepaid balance. This module replaces the Codex meter's role there.

**The ledger.** ``record_claude_call`` prices a call
(``services/llm_pricing.py``) and writes one ``llm_calls`` row with the
same spend labels as the Codex ledger — ``mind:<reason>``, ``pulse``,
``surplus:<job>``, ``turn:<source>``, a caller's module — read the same way
(``call_purpose.current_purpose`` first, then the model's own ``purpose``,
then the stack walk), plus the turn's source, id and conversation.

**The cap** (the owner's local day, ``settings.timezone``; $5 by default,
a design decision of 2026-10-11)::

    background   stops at  cap × share            ($5 × 0.6 = $3)
    Edward's own turns (wakes, the pulse, the noticer,
      events he scheduled himself)   stop at  cap  ($5)
    the owner's turns  stop past cap + reserve     ($5 + $3 = $8)

Background work done *for the owner's own turn* (``OWNER_TURN_SOURCES``:
chat, voice, widget, Alexa) — the memory extraction of what they just said,
the retrieval for their answer — runs on their turn's limit, not the
background share (review 2026-10-11): a refused extraction is dropped, not
queued, so a standing instruction they gave after the background line would
never be stored. Edward's own work is marked by a purpose scope (``mind:*``,
``pulse``, ``surplus:*``) or ``own_work_scope()`` (a scheduled event the
owner did not make, ``scheduled_events_service.event_is_owners``) and never
counts as the owner's, whatever turn record it runs under.

``check_spend('background')`` raises ``LLMRateLimitDeferred`` — the class
the background loops already absorb under the Codex gate;
``interactive`` / ``triage`` raise ``LLMSpendCapReached``, whose message is
a sentence Edward can say as it is. ``surplus`` never runs: with nothing
expiring there is no spare quota to spend.

**The day's total** is cached in memory (``CACHE_TTL_SECONDS``) and bumped
at once by every call this process records, so the check costs nothing per
call. A cold or stale cache reads the table, so the stop holds across a
restart and sees what other processes (the graph worker) spent. An
unreadable table fails *open* with a WARNING — the owner must be answered.

**Credits** (optional). With ``EDWARD_CREDITS_START_USD`` and
``EDWARD_CREDITS_START_DATE`` both set: remaining = start − spend since that
local date, and ``days_left`` at the trailing three days' burn. There is no
API that reads the real Anthropic balance with an ordinary key, so this is
tracked locally (``CREDITS_NOTE``). Unset, credits are not tracked: the
block says so (``source: "untracked"``) and the cap works the same.

How ``ChatClaude`` (``services/claude_chat_model.py``) uses it, before and
after **every** HTTP request — the first, each ``pause_turn`` continuation
and the one retry (``ChatClaude._admit`` / ``_record``; wired 2026-10-11)::

    await get_llm_rate_gate().acquire_spend(priority, purpose=...)   # the first request
    await llm_spend.check_spend(priority, purpose=...)               # the others
    ... the request ...
    llm_spend.record_claude_call(model, request_usage, purpose=..., priority=...,
                                 latency_ms=..., ttft_ms=..., ok=..., error=...,
                                 conversation_id=..., served_model=response_model)

The check sits outside the code that records the request, so a refused
call sends and records nothing. ``record_claude_call`` is synchronous,
fire-and-forget and never raises; it returns the call's cost in USD. Failed
requests are recorded too (``ok=False``, whatever usage arrived), each on
its own usage — never a turn's summed usage.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from services import llm_pricing
from services.llm_rate_gate import LLMRateLimitDeferred
from services.owner import owner_name

logger = logging.getLogger(__name__)

# Fire-and-forget row writes; tests/conftest.py turns them off for unit
# tests (a write on a loop about to close), as for codex_call_ledger.
WRITES_ENABLED = True

PROVIDER_ENV = "EDWARD_LLM_PROVIDER"
ANTHROPIC = "anthropic"
CODEX = "codex"

DEFAULT_DAILY_CAP_USD = 5.0  # design decision, 2026-10-11: $5 a day
DEFAULT_BACKGROUND_SHARE = 0.6
DEFAULT_INTERACTIVE_RESERVE_USD = 3.0

# The day's total is re-read from the table at most this often (it is
# bumped in memory by every call in between).
CACHE_TTL_SECONDS = 30.0
# Near a limit the cache is not trusted for long (review 2026-10-11): each
# process caches for 30 s, so one could admit calls against a total missing
# what another (the graph worker) had just committed. Within this many
# dollars of the limit that decides a call, a reading older than
# NEAR_LIMIT_MAX_AGE_SECONDS is refreshed before the call is admitted.
NEAR_LIMIT_USD = 0.50
NEAR_LIMIT_MAX_AGE_SECONDS = 2.0
# After a failed read, try the table again this soon.
RETRY_AFTER_FAILURE_SECONDS = 5.0
# A reload waits at most this long for this process's pending row writes,
# so a row is not counted both in the table and as pending.
SETTLE_TIMEOUT_SECONDS = 1.0
# The health report warns past this share of the cap.
WARN_FRACTION = 0.8
# Burn rate for ``days_left``: the trailing three days.
BURN_WINDOW_DAYS = 3
# ``llm_calls`` is kept this long (the other ledgers 30 days): the credits
# remaining are a sum since the purchase date.
RETENTION_DAYS = 400

CREDITS_NOTE = "tracked locally; check console.anthropic.com for the authoritative balance"
CREDITS_UNTRACKED_NOTE = ("not tracked: set EDWARD_CREDITS_START_USD and EDWARD_CREDITS_START_DATE to track "
                          "prepaid credits; console.anthropic.com has the balance")

# Turns in which the owner is speaking — memory_service.OWNER_SPEAKS_SOURCES
# (pinned equal by tests): background work inside one runs on their limit.
OWNER_TURN_SOURCES = ("chat", "voice", "widget", "alexa", "ping_owner", "owner_ping")

_STR_LIMITS = {"provider": 20, "model": 60, "purpose": 60, "priority": 20, "turn_source": 40, "turn_id": 40,
               "conversation_id": 200}
MAX_ERROR_CHARS = 500


# ------------------------------------------------------------ config --

def provider() -> str:
    """``anthropic`` (the default, also when ``EDWARD_LLM_PROVIDER`` is
    unset), or ``codex`` when it asks for the Codex fallback —
    ``claude_routing.llm_provider``, the one reading of the switch. Read
    per call — no restart needed."""
    try:
        from services.claude_routing import llm_provider

        return ANTHROPIC if llm_provider() == ANTHROPIC else CODEX
    except Exception:  # noqa: BLE001 — never let the switch break a call
        raw = (os.getenv(PROVIDER_ENV) or "").strip().lower()
        return CODEX if raw in ("codex", "openai", "gpt") else ANTHROPIC


def is_anthropic() -> bool:
    return provider() == ANTHROPIC


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip().lstrip("$"))
    except (TypeError, ValueError):
        return default
    if value != value or value < minimum:   # NaN or below the floor
        return default
    return value


@dataclass(frozen=True)
class SpendLimits:
    cap: float = DEFAULT_DAILY_CAP_USD
    share: float = DEFAULT_BACKGROUND_SHARE
    reserve: float = DEFAULT_INTERACTIVE_RESERVE_USD

    @property
    def background_stop(self) -> float:
        return self.cap * self.share

    @property
    def hard_stop(self) -> float:
        return self.cap + self.reserve

    @classmethod
    def from_env(cls) -> "SpendLimits":
        share = _env_float("EDWARD_BACKGROUND_SPEND_SHARE", DEFAULT_BACKGROUND_SHARE)
        return cls(
            cap=_env_float("EDWARD_DAILY_SPEND_CAP_USD", DEFAULT_DAILY_CAP_USD),
            share=min(1.0, share),
            reserve=_env_float("EDWARD_INTERACTIVE_RESERVE_USD", DEFAULT_INTERACTIVE_RESERVE_USD),
        )

    def to_dict(self) -> Dict[str, float]:
        return {"cap_usd": self.cap, "background_share": self.share, "background_stop_usd": self.background_stop,
                "interactive_reserve_usd": self.reserve, "hard_stop_usd": self.hard_stop}


def credits_start_usd() -> Optional[float]:
    """``EDWARD_CREDITS_START_USD``, or None when unset or unreadable."""
    raw = (os.getenv("EDWARD_CREDITS_START_USD") or "").strip().lstrip("$")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value != value or value < 0:   # NaN or negative
        return None
    return value


def credits_start_date() -> Optional[date]:
    """``EDWARD_CREDITS_START_DATE`` (YYYY-MM-DD), or None when unset or unreadable."""
    raw = (os.getenv("EDWARD_CREDITS_START_DATE") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def credits_tracked() -> bool:
    """Prepaid credits are tracked only when both the start amount and the
    start date are set."""
    return credits_start_usd() is not None and credits_start_date() is not None


# ------------------------------------------------------------- pure --

class LLMSpendCapReached(Exception):
    """An interactive or triage call refused by the daily spend cap.

    The message is a sentence in Edward's voice (the chat path shows it as
    it is). ``decision`` is the ``SpendDecision`` behind it. Deliberately
    not an ``LLMRateLimitDeferred``: nothing should quietly retry it."""

    def __init__(self, message: str, decision: Optional["SpendDecision"] = None):
        super().__init__(message)
        self.decision = decision


def _usd(value: float) -> str:
    return f"${value:,.2f}"


@dataclass(frozen=True)
class SpendDecision:
    """``code``: ``ok`` | ``background_share`` | ``cap`` (Edward's own
    turns at the cap) | ``hard_stop`` (past cap + reserve) |
    ``pay_per_token`` (surplus)."""

    allowed: bool
    code: str
    reason: str
    spent: float
    limit: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "code": self.code, "reason": self.reason, "spent": self.spent,
                "limit": self.limit}


PAY_PER_TOKEN_REASON = ("Claude is paid per token: nothing expires at a reset, so there is no spare quota "
                        "for spare-time work")


def spend_decision(priority: Optional[str], spent: float, limits: SpendLimits, *,
                   autonomous: bool = False, owners_turn: bool = False) -> SpendDecision:
    """May a call at ``priority`` run with ``spent`` dollars spent today?
    ``owners_turn``: background work done for the owner's own turn (the
    memory extraction of what they said), which runs on their limit. Pure."""
    p = (priority or "interactive").strip().lower()
    if p == "surplus":
        return SpendDecision(False, "pay_per_token", PAY_PER_TOKEN_REASON, spent)
    if p == "background" and owners_turn:
        stop = limits.hard_stop
        if spent >= stop:
            return SpendDecision(False, "hard_stop", (
                f"I've spent {_usd(spent)} on Claude today — past my {_usd(limits.cap)} daily cap and the "
                f"{_usd(limits.reserve)} I keep for you — so remembering what you said waits until "
                f"midnight"), spent, stop)
        return SpendDecision(True, "ok", f"{_usd(spent)} of {_usd(stop)} for remembering your own turn today",
                             spent, stop)
    if p == "background":
        stop = limits.background_stop
        if spent >= stop:
            return SpendDecision(False, "background_share", (
                f"my background work stops at {_usd(stop)} a day ({limits.share:.0%} of the "
                f"{_usd(limits.cap)} cap) and I've spent {_usd(spent)} on Claude today; it starts again "
                f"after midnight (what you tell me is still remembered until {_usd(limits.hard_stop)})"),
                spent, stop)
        return SpendDecision(True, "ok", f"{_usd(spent)} of {_usd(stop)} for background work today", spent, stop)
    if autonomous:
        if spent >= limits.cap:
            return SpendDecision(False, "cap", (
                f"I've spent {_usd(spent)} on Claude today, which reaches my {_usd(limits.cap)} daily cap, so my "
                f"own work (wakes, the screens) waits until midnight; {owner_name()}'s messages "
                f"still have a {_usd(limits.reserve)} reserve"), spent, limits.cap)
        return SpendDecision(True, "ok", f"{_usd(spent)} of the {_usd(limits.cap)} cap today", spent, limits.cap)
    stop = limits.hard_stop
    if spent >= stop:
        return SpendDecision(False, "hard_stop", (
            f"I've spent {_usd(spent)} on Claude today — past my {_usd(limits.cap)} daily cap and the "
            f"{_usd(limits.reserve)} I keep for you — so I can't answer until midnight. Raising "
            f"EDWARD_DAILY_SPEND_CAP_USD lets me back in sooner."), spent, stop)
    return SpendDecision(True, "ok", f"{_usd(spent)} of {_usd(stop)} (cap plus your reserve) today", spent, stop)


def is_autonomous(purpose: Optional[str], turn_source: Optional[str] = None) -> bool:
    """Edward's own turn (a wake, the pulse, a surplus job) rather than
    the owner's — the spend report's rule (``is_autonomous_call``). Pure."""
    from services.spend_report_service import is_autonomous_call

    return is_autonomous_call({"purpose": purpose, "turn_source": turn_source})


# Edward's own work that no purpose label names: an ordinary scheduled
# event the owner did not make (``scheduler_service._execute_event``). Tasks
# created inside — the turn's extraction, a worker it spawns — inherit it.
_own_work: contextvars.ContextVar[bool] = contextvars.ContextVar("edward_own_work", default=False)


@contextmanager
def own_work_scope() -> Iterator[None]:
    """Every Claude call made inside (and in tasks created inside) is
    Edward's own: it stops at the cap, never in the owner's reserve."""
    token = _own_work.set(True)
    try:
        yield
    finally:
        _own_work.reset(token)


def in_own_work() -> bool:
    return bool(_own_work.get())


def local_day_bounds(now_utc: datetime, tz: Any) -> Tuple[date, datetime, datetime]:
    """(The owner's local date, its start and end as naive UTC — the column's
    convention). ``now_utc`` naive is read as UTC. Pure."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    day = now_utc.astimezone(tz).date()
    return day, _local_midnight_utc(day, tz), _local_midnight_utc(day + timedelta(days=1), tz)


def _local_midnight_utc(day: date, tz: Any) -> datetime:
    return datetime.combine(day, dtime.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def _bounded(value: Any, limit: int) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)[:limit]


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(round(float(value)))
    except (TypeError, ValueError):
        return None


def utc_now() -> datetime:
    """Now as naive UTC (the column's convention), from the module clock."""
    return _now_utc().astimezone(timezone.utc).replace(tzinfo=None)


def call_row(*, model: Any, usage: Any, purpose: Any = None, priority: Any = None, latency_ms: Any = None,
             ttft_ms: Any = None, ok: Any = True, error: Any = None, conversation_id: Any = None,
             turn_source: Any = None, turn_id: Any = None, provider: str = ANTHROPIC,
             served_model: Any = None, created_at: Optional[datetime] = None) -> Dict[str, Any]:
    """The ``llm_calls`` row for one call: priced, bounded. Pure."""
    priced = llm_pricing.price(model, usage, served_model=served_model)
    error_text = None if error is None or error == "" else " ".join(str(error).split())[:MAX_ERROR_CHARS]
    return {
        "created_at": created_at if isinstance(created_at, datetime) else utc_now(),
        "provider": _bounded(provider, _STR_LIMITS["provider"]) or ANTHROPIC,
        "model": _bounded(served_model or model, _STR_LIMITS["model"]),
        "purpose": _bounded(purpose, _STR_LIMITS["purpose"]),
        "priority": _bounded(priority, _STR_LIMITS["priority"]),
        "input_tokens": priced.input_tokens,
        "cache_read_tokens": priced.cache_read_tokens,
        "cache_write_tokens": priced.cache_write_tokens,
        "output_tokens": priced.output_tokens,
        "thinking_tokens": priced.thinking_tokens,
        "web_searches": priced.web_searches,
        "cost_usd": round(float(priced.total), 8),
        "latency_ms": _int_or_none(latency_ms),
        "ttft_ms": _int_or_none(ttft_ms),
        "ok": bool(ok) if ok is not None else True,
        "error": error_text,
        "conversation_id": _bounded(conversation_id, _STR_LIMITS["conversation_id"]),
        "turn_source": _bounded(turn_source, _STR_LIMITS["turn_source"]),
        "turn_id": _bounded(turn_id, _STR_LIMITS["turn_id"]),
    }


def credits_view(*, start_usd: float, start_date: date, spent: float, burn_spent: float,
                 burn_span_days: float) -> Dict[str, Any]:
    """Credits remaining and how long they last at the recent burn. Pure."""
    remaining = start_usd - spent
    burn = (burn_spent / burn_span_days) if burn_span_days > 0 else 0.0
    if remaining <= 0:
        days_left: Optional[float] = 0.0
    elif burn > 0:
        days_left = remaining / burn
    else:
        days_left = None
    return {
        "start_usd": start_usd,
        "start_date": start_date.isoformat(),
        "spent_usd": spent,
        "remaining_usd": remaining,
        "burn_per_day_usd": burn,
        "burn_window_days": BURN_WINDOW_DAYS,
        "days_left": days_left,
        "source": "tracked",
        "note": CREDITS_NOTE,
    }


GROUP_FIELDS = ("calls", "errors", "cost_usd", "input_tokens", "cache_read_tokens", "cache_write_tokens",
                "output_tokens", "thinking_tokens", "web_searches")


def group_spend(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    """Per ``key`` (purpose, model): the summed fields of grouped rows,
    dearest first. ``rows`` carry ``GROUP_FIELDS``. Pure."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        name = str(r.get(key) or "unknown")
        g = out.setdefault(name, {f: 0 for f in GROUP_FIELDS})
        for f in GROUP_FIELDS:
            g[f] += float(r.get(f) or 0) if f == "cost_usd" else int(r.get(f) or 0)
    for g in out.values():
        g["cost_usd"] = round(g["cost_usd"], 6)
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["cost_usd"], -kv[1]["calls"], kv[0])))


def compose_claude_block(*, spent: float, limits: SpendLimits, day: date, tz_name: str, groups: List[Dict[str, Any]],
                         credits: Optional[Dict[str, Any]], active: bool,
                         by_day: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The ``claude`` block of ``/api/health/spend`` and
    ``/api/settings/codex-usage``. Pure."""
    cap = limits.cap
    background_allowed = spent < limits.background_stop
    own_allowed = spent < cap
    interactive_allowed = spent < limits.hard_stop
    if not interactive_allowed:
        state = "stopped"
    elif not own_allowed:
        state = "own_work_paused"
    elif not background_allowed:
        state = "background_paused"
    else:
        state = "ok"
    bg = spend_decision("background", spent, limits)
    out: Dict[str, Any] = {
        "provider": ANTHROPIC if active else CODEX,
        "active": active,
        "day": day.isoformat(),
        "timezone": tz_name,
        "spent_today_usd": round(spent, 4),
        **limits.to_dict(),
        "remaining_today_usd": round(max(0.0, cap - spent), 4),
        "percent_of_cap": round(spent / cap * 100.0, 1) if cap > 0 else None,
        "state": state,
        "background_allowed": background_allowed,
        "background_reason": bg.reason,
        "own_work_allowed": own_allowed,
        "interactive_allowed": interactive_allowed,
        "calls_today": sum(int(g.get("calls") or 0) for g in groups),
        "errors_today": sum(int(g.get("errors") or 0) for g in groups),
        "by_purpose": group_spend(groups, "purpose"),
        "by_model": group_spend(groups, "model"),
        "credits": credits,
    }
    if by_day is not None:
        out["by_day"] = by_day
    return out


def headline(block: Dict[str, Any]) -> Dict[str, Any]:
    """``spend.claude`` for the health report: the numbers a health check
    reads first, and a warning past ``WARN_FRACTION`` of the cap. Pure."""
    credits = block.get("credits") or {}
    spent = float(block.get("spent_today_usd") or 0.0)
    cap = float(block.get("cap_usd") or 0.0)
    warning = None
    if cap > 0 and spent > WARN_FRACTION * cap:
        warning = (f"Claude spend today is {_usd(spent)} — {spent / cap:.0%} of the {_usd(cap)} daily cap "
                   f"(background stops at {_usd(float(block.get('background_stop_usd') or 0.0))}).")
    keys = ("provider", "active", "day", "timezone", "spent_today_usd", "cap_usd", "background_share",
            "background_stop_usd", "interactive_reserve_usd", "hard_stop_usd", "remaining_today_usd",
            "percent_of_cap", "state", "background_allowed", "calls_today", "errors_today")
    out = {k: block.get(k) for k in keys}
    out.update({
        "credits_remaining_usd": credits.get("remaining_usd"),
        "credits_days_left": credits.get("days_left"),
        "credits_note": credits.get("note"),
        "top_purposes": [{"purpose": k, "cost_usd": v["cost_usd"], "calls": v["calls"]}
                         for k, v in list((block.get("by_purpose") or {}).items())[:5]],
        "warning": warning,
    })
    return out


# ----------------------------------------------------------- context --

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clock() -> float:
    return time.monotonic()


def _user_tz():
    from services import timezone_utils

    return timezone_utils.get_user_tz()


def _user_tz_name() -> str:
    from services import timezone_utils

    try:
        return timezone_utils.current_tz_name()
    except Exception:  # noqa: BLE001
        return str(_user_tz())


def _tool_conversation_id() -> Optional[str]:
    import sys

    mod = sys.modules.get("services.graph.tools")
    getter = getattr(mod, "get_current_conversation_id", None) if mod is not None else None
    try:
        cid = getter() if getter is not None else None
    except Exception:  # noqa: BLE001
        cid = None
    return str(cid) if cid else None


def call_context(purpose: Optional[str] = None, conversation_id: Optional[str] = None) -> Dict[str, Optional[str]]:
    """The labels a call carries, read as the Codex ledger reads them: a
    job's ``purpose_scope`` wins over the model's own label, else the
    stack walk names the caller; the turn (``turn_ledger.current_turn``)
    gives its source, id and conversation. Never raises."""
    ctx: Dict[str, Optional[str]] = {"purpose": None, "turn_source": None, "turn_id": None,
                                     "conversation_id": conversation_id or None}
    try:
        from services.call_purpose import current_purpose

        ctx["purpose"] = current_purpose() or purpose or None
    except Exception:  # noqa: BLE001
        ctx["purpose"] = purpose or None
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001
        rec = None
    if rec is not None:
        ctx["turn_source"] = getattr(rec, "source", None)
        ctx["turn_id"] = getattr(rec, "id", None)
        ctx["conversation_id"] = ctx["conversation_id"] or getattr(rec, "conversation_id", None)
    if not ctx["conversation_id"]:
        ctx["conversation_id"] = _tool_conversation_id()
    if not ctx["purpose"]:
        try:
            from services.event_ledger import caller_label

            ctx["purpose"] = caller_label()
        except Exception:  # noqa: BLE001
            pass
    return ctx


# ------------------------------------------------- the day's total --

@dataclass
class _DayCache:
    day: Optional[date] = None
    base: float = 0.0            # today's spend as of the last read (table + writes pending then)
    mark: float = 0.0            # ``_recorded`` when ``base`` was taken
    loaded_at: Optional[float] = None


_cache = _DayCache()
_recorded = 0.0                  # every dollar this process recorded (monotone)
_inflight = 0.0                  # recorded, row not yet committed
# Dollars recorded whose row never reached the table (writes off, no event
# loop, a failed insert), by the owner's local day: a re-read of the table adds
# them back, so a lost write never lowers the day's total.
_unwritten: Dict[date, float] = {}
_pending: Set["asyncio.Task"] = set()
_last_read_warning: Optional[float] = None
_last_cap_log: Dict[str, float] = {}


def reset_cache() -> None:
    """Forget the day's total and everything this process recorded — what
    a restart does. For tests."""
    global _cache, _recorded, _inflight, _last_read_warning
    _cache = _DayCache()
    _recorded = 0.0
    _inflight = 0.0
    _unwritten.clear()
    _pending.clear()
    _last_read_warning = None
    _last_cap_log.clear()


def _note_recorded(cost: float) -> None:
    global _recorded
    _recorded += max(0.0, float(cost or 0.0))


def _note_unwritten(created_at: Optional[datetime], cost: float) -> None:
    """Keep a cost whose row did not land, against its local day."""
    try:
        when = created_at if isinstance(created_at, datetime) else utc_now()
        day, _s, _e = local_day_bounds(when, _user_tz())
    except Exception:  # noqa: BLE001
        return
    _unwritten[day] = _unwritten.get(day, 0.0) + max(0.0, float(cost or 0.0))
    for old in [d for d in _unwritten if d < day - timedelta(days=1)]:
        _unwritten.pop(old, None)


def _total() -> float:
    return _cache.base + (_recorded - _cache.mark)


def _cache_age() -> float:
    """Seconds since the day's total was last read from the table (infinite
    when never)."""
    if _cache.loaded_at is None:
        return float("inf")
    return max(0.0, _clock() - _cache.loaded_at)


def spent_today_cached(now: Optional[datetime] = None) -> Optional[float]:
    """Today's spend from memory (no I/O), or None when the cache holds no
    reading for today — the in-memory decisions use it."""
    try:
        day, _s, _e = local_day_bounds(now or _now_utc(), _user_tz())
    except Exception:  # noqa: BLE001
        return None
    if _cache.day != day or _cache.loaded_at is None:
        return None
    return _total()


async def _settle_pending() -> None:
    """Let this process's in-flight row writes land (bounded) before the
    table is read, so a row is not counted there and as pending too."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    mine = [t for t in list(_pending) if not t.done() and t.get_loop() is loop]
    if mine:
        try:
            await asyncio.wait(mine, timeout=SETTLE_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001
            pass


async def spent_today(now: Optional[datetime] = None, *, fresh: bool = False) -> float:
    """Dollars spent on the owner's local day so far: the cached total while it
    is fresh, else the table re-read (plus this process's pending writes).
    Never raises; an unreadable table fails open (logged)."""
    global _last_read_warning
    now = now or _now_utc()
    day, start, end = local_day_bounds(now, _user_tz())
    c = _cache
    if (not fresh and c.day == day and c.loaded_at is not None
            and _clock() - c.loaded_at < CACHE_TTL_SECONDS):
        return _total()
    await _settle_pending()
    pending, mark, lost = _inflight, _recorded, _unwritten.get(day, 0.0)
    try:
        in_table = await _db_spent_between(start, end)
    except Exception as exc:  # noqa: BLE001
        mono = _clock()
        if _last_read_warning is None or mono - _last_read_warning >= 300:
            _last_read_warning = mono
            logger.warning("CLAUDE_SPEND could not read today's spend (%s: %s); the cap is not enforced "
                           "until it can", type(exc).__name__, exc)
        if c.day == day and c.loaded_at is not None:
            c.loaded_at = _clock() - CACHE_TTL_SECONDS + RETRY_AFTER_FAILURE_SECONDS
            return _total()
        c.day, c.base, c.mark = day, pending + lost, mark
        c.loaded_at = _clock() - CACHE_TTL_SECONDS + RETRY_AFTER_FAILURE_SECONDS
        return _total()
    c.day, c.base, c.mark, c.loaded_at = day, float(in_table) + pending + lost, mark, _clock()
    return _total()


# ------------------------------------------------------------ check --

def _record_deferral(reason: str, purpose: Optional[str]) -> None:
    """The same ``rate_gate`` / ``deferred`` ledger row the Codex gate writes."""
    try:
        from services import event_ledger

        source = purpose or event_ledger.caller_label()
        event_ledger.record_event(kind="rate_gate", source=source, outcome="deferred", reason=f"claude spend: {reason}")
    except Exception:  # noqa: BLE001
        pass


def _log_cap(decision: SpendDecision, purpose: Optional[str]) -> None:
    """One WARNING per code a minute — every refused call is not news."""
    mono = _clock()
    last = _last_cap_log.get(decision.code)
    if last is not None and mono - last < 60:
        return
    _last_cap_log[decision.code] = mono
    logger.warning("CLAUDE_SPEND_CAP code=%s spent=%.2f limit=%s purpose=%s — %s", decision.code, decision.spent,
                   "-" if decision.limit is None else f"{decision.limit:.2f}", purpose or "-", decision.reason)


async def check_spend(priority: str = "interactive", purpose: Optional[str] = None) -> None:
    """Before a Claude call: return when it may run, else raise.

    ``background`` (and ``surplus``) raise ``LLMRateLimitDeferred``;
    ``interactive`` / ``triage`` raise ``LLMSpendCapReached``. Edward's own
    turns (``mind:*``, ``pulse``, ``surplus:*``, a turn whose source is the
    mind or the pulse, or anything inside ``own_work_scope``) stop at the
    cap; the owner's run into the reserve, and so does the background work
    done for their own turn (``OWNER_TURN_SOURCES``). Costs nothing while the day's
    total is cached."""
    p = (priority or "interactive").strip().lower()
    limits = SpendLimits.from_env()
    ctx = call_context(purpose)
    label = ctx.get("purpose")
    if p == "surplus":
        decision = spend_decision("surplus", 0.0, limits)
    else:
        source = ctx.get("turn_source")
        edwards = in_own_work() or is_autonomous(label, source)

        def decide(spent: float) -> SpendDecision:
            return spend_decision(
                p, spent, limits,
                autonomous=p != "background" and edwards,
                owners_turn=p == "background" and not edwards and source in OWNER_TURN_SOURCES,
            )

        spent = await spent_today()
        decision = decide(spent)
        if (decision.allowed and decision.limit is not None and decision.limit - spent <= NEAR_LIMIT_USD
                and _cache_age() > NEAR_LIMIT_MAX_AGE_SECONDS):
            decision = decide(await spent_today(fresh=True))
    if decision.allowed:
        return
    if p in ("background", "surplus"):
        _record_deferral(decision.reason, label)
        raise LLMRateLimitDeferred(f"Claude spend gate: {p} LLM call deferred — {decision.reason}")
    _log_cap(decision, label)
    raise LLMSpendCapReached(decision.reason, decision)


def background_decision_cached() -> Tuple[bool, str]:
    """(may background work run, why) from the cached total — no I/O. A
    cold cache allows it; the call's own ``check_spend`` reads the table."""
    spent = spent_today_cached()
    if spent is None:
        return True, "no spend read yet today"
    d = spend_decision("background", spent, SpendLimits.from_env())
    return d.allowed, d.reason


def quota_state_cached() -> str:
    """``ok`` / ``paced`` (background stopped) / ``exhausted`` (Edward's own
    turns stopped) from the cached total — the pulse's and the mind's
    quota word under pay-per-token. No I/O; a cold cache reads ``ok``."""
    spent = spent_today_cached()
    if spent is None:
        return "ok"
    limits = SpendLimits.from_env()
    if spent >= limits.cap:
        return "exhausted"
    if spent >= limits.background_stop:
        return "paced"
    return "ok"


# The note a wake skipped at the cap leaves in the ledger (the Codex one
# says "Codex quota exhausted"); frontend/lib/mindLog.ts reads "spend cap".
QUOTA_SKIP_NOTE = "today's Claude spend cap reached"


def next_local_midnight(now: Optional[datetime] = None) -> datetime:
    """When the owner's local day ends — when the cap's day starts again — as an
    aware UTC datetime."""
    now = now or _now_utc()
    _day, _start, end = local_day_bounds(now, _user_tz())
    return end.replace(tzinfo=timezone.utc)


def quota_now(now: Optional[datetime] = None) -> Dict[str, Any]:
    """``GET /api/mind/now``'s ``quota`` under pay-per-token, from the
    cached day (no I/O): ``exhausted`` once Edward's own turns stopped at
    the cap (``quota_state_cached``), ``used_percent`` of the cap, and the
    reset at the owner's next local midnight. A cold cache reads as not spent."""
    spent = spent_today_cached(now)
    limits = SpendLimits.from_env()
    used = round(spent / limits.cap * 100.0, 2) if (spent is not None and limits.cap > 0) else None
    try:
        resets_at: Optional[datetime] = next_local_midnight(now)
    except Exception:  # noqa: BLE001
        resets_at = None
    return {"used_percent": used, "exhausted": quota_state_cached() == "exhausted", "resets_at": resets_at,
            "period": "day"}


# ----------------------------------------------------------- record --

def _schedule_write(row: Dict[str, Any], cost: float) -> bool:
    global _inflight
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False   # no loop (a sync caller, shutdown): counted in memory only
    _inflight += cost
    task = loop.create_task(_write(row, cost))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return True


async def _write(row: Dict[str, Any], cost: float) -> None:
    global _inflight
    landed = False
    try:
        landed = await _insert(row)
    finally:
        if not landed:
            _note_unwritten(row.get("created_at"), cost)
        _inflight = max(0.0, _inflight - cost)


async def _insert(row: Dict[str, Any]) -> bool:
    try:
        from services.database import async_session, LLMCallModel

        async with async_session() as session:
            session.add(LLMCallModel(**row))
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — a ledger that cannot write is not an outage
        logger.warning("llm_calls write failed (the call still counts in memory): %s", exc)
        return False
    try:
        from services import turn_ledger

        turn_ledger.maybe_prune()
    except Exception:  # noqa: BLE001
        pass
    return True


def _build(model, usage, purpose, priority, latency_ms, ttft_ms, ok, error, conversation_id, served_model,
           provider_name) -> Dict[str, Any]:
    ctx = call_context(purpose, conversation_id)
    return call_row(model=model, usage=usage, purpose=ctx["purpose"], priority=priority, latency_ms=latency_ms,
                    ttft_ms=ttft_ms, ok=ok, error=error, conversation_id=ctx["conversation_id"],
                    turn_source=ctx["turn_source"], turn_id=ctx["turn_id"], provider=provider_name,
                    served_model=served_model)


def record_claude_call(model: Any, usage: Any, purpose: Optional[str] = None, priority: Optional[str] = "interactive",
                       latency_ms: Any = None, ttft_ms: Any = None, ok: bool = True, error: Any = None,
                       conversation_id: Optional[str] = None, *, served_model: Any = None,
                       provider: str = ANTHROPIC) -> float:
    """Price one Claude call, count it toward today at once, and write its
    ``llm_calls`` row in the background. Returns the cost in USD; never
    raises (a broken record returns 0.0).

    ``model`` is the model asked for; ``served_model`` the one that
    answered when a server-side fallback served it (``response.model``).
    ``usage`` is the response's ``usage`` (an SDK object or a dict; for a
    stream, the final cumulative usage)."""
    try:
        row = _build(model, usage, purpose, priority, latency_ms, ttft_ms, ok, error, conversation_id,
                     served_model, provider)
        cost = float(row["cost_usd"])
        _note_recorded(cost)
        if not (WRITES_ENABLED and _schedule_write(row, cost)):
            _note_unwritten(row["created_at"], cost)
        return cost
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_claude_call failed: %s", exc)
        return 0.0


async def record_claude_call_now(model: Any, usage: Any, purpose: Optional[str] = None,
                                 priority: Optional[str] = "interactive", latency_ms: Any = None, ttft_ms: Any = None,
                                 ok: bool = True, error: Any = None, conversation_id: Optional[str] = None, *,
                                 served_model: Any = None, provider: str = ANTHROPIC) -> float:
    """``record_claude_call``, awaiting the row's write. Never raises."""
    global _inflight
    try:
        row = _build(model, usage, purpose, priority, latency_ms, ttft_ms, ok, error, conversation_id,
                     served_model, provider)
        cost = float(row["cost_usd"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_claude_call_now failed: %s", exc)
        return 0.0
    _note_recorded(cost)
    _inflight += cost
    await _write(row, cost)
    return cost


async def drain() -> None:
    """Wait for pending row writes (tests, shutdown)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


# -------------------------------------------------------------- I/O --

async def _db_spent_between(start: datetime, end: datetime) -> float:
    from sqlalchemy import func, select
    from services.database import async_session, LLMCallModel

    async with async_session() as session:
        value = (await session.execute(
            select(func.coalesce(func.sum(LLMCallModel.cost_usd), 0))
            .where(LLMCallModel.created_at >= start, LLMCallModel.created_at < end)
        )).scalar_one()
    return float(value or 0)


async def _db_spent_since(since: datetime) -> float:
    from sqlalchemy import func, select
    from services.database import async_session, LLMCallModel

    async with async_session() as session:
        value = (await session.execute(
            select(func.coalesce(func.sum(LLMCallModel.cost_usd), 0)).where(LLMCallModel.created_at >= since)
        )).scalar_one()
    return float(value or 0)


async def _db_groups(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    from sqlalchemy import false, func, select
    from services.database import async_session, LLMCallModel

    m = LLMCallModel
    async with async_session() as session:
        rows = (await session.execute(
            select(
                m.purpose, m.model, m.priority,
                func.count().label("calls"),
                func.count().filter(m.ok == false()).label("errors"),
                func.coalesce(func.sum(m.cost_usd), 0).label("cost_usd"),
                func.coalesce(func.sum(m.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(m.cache_read_tokens), 0).label("cache_read_tokens"),
                func.coalesce(func.sum(m.cache_write_tokens), 0).label("cache_write_tokens"),
                func.coalesce(func.sum(m.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(m.thinking_tokens), 0).label("thinking_tokens"),
                func.coalesce(func.sum(m.web_searches), 0).label("web_searches"),
            )
            .where(m.created_at >= start, m.created_at < end)
            .group_by(m.purpose, m.model, m.priority)
        )).all()
    return [dict(r._mapping) for r in rows]


async def _db_by_day(since: datetime, tz_name: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from services.database import async_session

    async with async_session() as session:
        rows = (await session.execute(text(
            "SELECT ((created_at AT TIME ZONE 'UTC') AT TIME ZONE :tz)::date AS day, "
            "COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM llm_calls WHERE created_at >= :since GROUP BY 1 ORDER BY 1"
        ), {"tz": tz_name, "since": since})).all()
    return [{"day": r.day.isoformat(), "calls": int(r.calls), "cost_usd": round(float(r.cost_usd), 4)} for r in rows]


async def credits_status(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Credits remaining since ``EDWARD_CREDITS_START_DATE`` (the owner's
    local midnight) and the days they last at the trailing three days' burn.
    The burn window never reaches back past the start, and is at least a day
    long so the first hours do not extrapolate wildly. Without both credits
    variables nothing is tracked: ``{"source": "untracked", "note": …}``
    (no ``remaining_usd``, so the Fuel card and the health report show none)."""
    start_usd = credits_start_usd()
    start_date = credits_start_date()
    if start_usd is None or start_date is None:
        return {"source": "untracked", "note": CREDITS_UNTRACKED_NOTE}
    now = now or _now_utc()
    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    start_utc = _local_midnight_utc(start_date, _user_tz())
    spent = await _db_spent_since(start_utc) if now_naive >= start_utc else 0.0
    window_start = max(now_naive - timedelta(days=BURN_WINDOW_DAYS), start_utc)
    if now_naive > window_start:
        burn_spent = await _db_spent_since(window_start)
        span_days = max(1.0, (now_naive - window_start).total_seconds() / 86400.0)
    else:
        burn_spent, span_days = 0.0, 1.0
    return credits_view(start_usd=start_usd, start_date=start_date, spent=spent, burn_spent=burn_spent,
                        burn_span_days=span_days)


async def claude_block(days: Optional[int] = None) -> Dict[str, Any]:
    """Today's Claude spend by purpose and model, the cap and where it
    stands, and the credits — for the spend report, the health report and
    the usage card. ``days`` adds a per-local-day series. Never raises:
    a failed read comes back as ``{"error": …}`` with the limits."""
    limits = SpendLimits.from_env()
    tz = _user_tz()
    tz_name = _user_tz_name()
    now = _now_utc()
    day, start, end = local_day_bounds(now, tz)
    try:
        spent = await spent_today(now, fresh=True)
        groups = await _db_groups(start, end)
        credits = await credits_status(now)
    except Exception as exc:  # noqa: BLE001 — one section must not sink a report
        logger.warning("claude spend block unavailable: %s", exc)
        return {"provider": provider(), "active": is_anthropic(), "day": day.isoformat(), "timezone": tz_name,
                **limits.to_dict(), "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                "credits": {"note": CREDITS_NOTE if credits_tracked() else CREDITS_UNTRACKED_NOTE}}
    by_day = None
    if days:
        try:
            since = _local_midnight_utc(day - timedelta(days=max(1, int(days)) - 1), tz)
            by_day = await _db_by_day(since, tz_name)
        except Exception as exc:  # noqa: BLE001 — the series is a nicety; the rest stands
            logger.warning("claude spend series unavailable: %s", exc)
    out = compose_claude_block(spent=spent, limits=limits, day=day, tz_name=tz_name, groups=groups, credits=credits,
                               active=is_anthropic())
    if days:
        out["by_day"] = by_day          # None when the series could not be read
    return out
