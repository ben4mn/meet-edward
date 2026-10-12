"""Process-wide rate gate for Codex-backed LLM calls, paced by the real meter.

Every Codex Responses call answers with the account's usage meter in its
headers — ``x-codex-primary-used-percent``, ``-window-minutes``,
``-reset-after-seconds`` and the same trio for ``secondary`` — and
``GET /backend-api/wham/usage`` returns the same numbers on demand. This
gate reads that meter and does two things:

1. **Reactive cooldown.** On a 429 the caller reports the body via
   :meth:`LLMRateGate.note_429`; the gate opens a short global cooldown
   (capped at :attr:`MAX_COOLDOWN_SECONDS`) and marks the meter exhausted
   until the reset the body names. ``interactive`` / ``triage`` calls wait
   briefly through it; ``background`` calls defer (raise
   :class:`LLMRateLimitDeferred`, which the loops' existing ``except
   Exception`` handlers absorb — they retry on their next scheduled run).

2. **Pacing against the meter.** Background work may run while every
   window is under a *pace line* — :attr:`BACKGROUND_HEADSTART_PERCENT`
   plus the fraction of the window already elapsed — and under a hard
   :attr:`BACKGROUND_CEILING_PERCENT`. So a week's quota is spent evenly
   rather than in a burst, and the last slice is always left for chat and
   triage. Interactive and triage work is never paced and never deferred.

3. **Surplus** (2026-09-24). Unused quota
   evaporates at the weekly reset, and a gate that can only stop work never
   spends it. ``surplus_decision`` says how much of the window is spare
   after keeping the owner's chat a reserve, and at what rate the surplus queue
   (``services/surplus_service.py``) should burn it; ``acquire("surplus")``
   defers like ``background`` but on that decision. Surplus sits below
   background: it never spends into the room background work needs
   (``SURPLUS_CEILING_MARGIN_PERCENT``, 2026-09-25). Kill switch:
   ``EDWARD_SURPLUS=off``.

4. **Pay per token** (2026-10-10). Under ``EDWARD_LLM_PROVIDER=anthropic``
   there is no weekly window: every token is paid from a prepaid balance.
   ``acquire`` then decides on the daily Claude spend cap
   (``services/llm_spend.py``) instead of the meter — background defers at
   the cap's background share, Edward's own turns at the cap, the owner's turns
   only past cap + reserve (``LLMSpendCapReached``) — and surplus never
   runs. ``meter`` reads as None (the Codex window is not his quota), and
   ``background_decision`` / ``status`` follow the spend.

History: the first gate guessed a per-window call budget and calibrated it
from 429s. On 2026-09-09 the *weekly* cap answered one, the guess collapsed
to ~40 calls per 4h against ~150 of chat traffic, and every background call
— image descriptions included — was deferred for two days with no real 429
in the log. See ``tests/test_llm_rate_gate.py`` and
``tests/test_surplus_policy.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


class LLMRateLimitDeferred(ValueError):
    """Raised when a background LLM call is skipped (during a 429 cooldown
    or while the meter says the window is ahead of pace or nearly spent).

    Subclasses ValueError so existing ``except Exception`` handlers in
    the background loops log-and-continue without code changes.
    """


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{max(1, seconds // 60)}m"


@dataclass(frozen=True)
class QuotaWindow:
    """One usage window as the API reported it at ``sampled_at`` (wall time)."""

    used_percent: float
    window_seconds: float
    reset_after_seconds: float

    def reset_after_at(self, sampled_at: float, now: float) -> float:
        return self.reset_after_seconds - max(0.0, now - sampled_at)

    def has_reset_by(self, sampled_at: float, now: float) -> bool:
        return self.reset_after_at(sampled_at, now) <= 0

    def elapsed_fraction(self, sampled_at: float, now: float) -> float:
        if self.window_seconds <= 0:
            return 1.0
        remaining = self.reset_after_at(sampled_at, now)
        return min(1.0, max(0.0, 1.0 - remaining / self.window_seconds))

    def to_dict(self) -> dict:
        return {
            "used_percent": self.used_percent,
            "window_seconds": self.window_seconds,
            "reset_after_seconds": self.reset_after_seconds,
        }


@dataclass(frozen=True)
class QuotaMeter:
    windows: Dict[str, QuotaWindow] = field(default_factory=dict)
    plan_type: Optional[str] = None
    limit_reached: bool = False
    sampled_at: float = 0.0

    # ----- construction -----

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str], now: float) -> Optional["QuotaMeter"]:
        h = {str(k).lower(): v for k, v in headers.items()}
        windows: Dict[str, QuotaWindow] = {}
        for name in ("primary", "secondary"):
            used = cls._num(h.get(f"x-codex-{name}-used-percent"))
            minutes = cls._num(h.get(f"x-codex-{name}-window-minutes"))
            reset = cls._num(h.get(f"x-codex-{name}-reset-after-seconds"))
            if used is None or minutes is None or minutes <= 0:
                continue
            windows[name] = QuotaWindow(
                used_percent=used,
                window_seconds=minutes * 60.0,
                reset_after_seconds=reset if reset is not None else minutes * 60.0,
            )
        if "x-codex-primary-used-percent" in h and "primary" not in windows:
            return None  # the meter was there but unreadable
        if not windows:
            return None
        return cls(
            windows=windows,
            plan_type=h.get("x-codex-plan-type") or None,
            limit_reached=any(w.used_percent >= 100 for w in windows.values()),
            sampled_at=now,
        )

    @classmethod
    def from_usage_payload(cls, payload: Any, now: float) -> Optional["QuotaMeter"]:
        """From ``GET /backend-api/wham/usage``."""
        if not isinstance(payload, dict):
            return None
        rl = payload.get("rate_limit")
        if not isinstance(rl, dict):
            return None
        windows: Dict[str, QuotaWindow] = {}
        for name in ("primary", "secondary"):
            w = rl.get(f"{name}_window")
            if not isinstance(w, dict):
                continue
            used = cls._num(w.get("used_percent"))
            size = cls._num(w.get("limit_window_seconds"))
            reset = cls._num(w.get("reset_after_seconds"))
            if used is None or size is None or size <= 0:
                continue
            windows[name] = QuotaWindow(used, size, reset if reset is not None else size)
        if not windows:
            return None
        return cls(
            windows=windows,
            plan_type=payload.get("plan_type") or None,
            limit_reached=bool(rl.get("limit_reached")) or any(w.used_percent >= 100 for w in windows.values()),
            sampled_at=now,
        )

    # ----- persistence -----

    def to_dict(self) -> dict:
        return {
            "windows": {k: w.to_dict() for k, w in self.windows.items()},
            "plan_type": self.plan_type,
            "limit_reached": self.limit_reached,
            "sampled_at": self.sampled_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["QuotaMeter"]:
        if not isinstance(data, dict) or not isinstance(data.get("windows"), dict):
            return None
        windows: Dict[str, QuotaWindow] = {}
        try:
            for k, w in data["windows"].items():
                windows[str(k)] = QuotaWindow(
                    float(w["used_percent"]), float(w["window_seconds"]), float(w["reset_after_seconds"])
                )
            sampled_at = float(data.get("sampled_at") or 0.0)
        except (TypeError, ValueError, KeyError, AttributeError):
            return None
        if not windows:
            return None
        return cls(
            windows=windows,
            plan_type=data.get("plan_type") or None,
            limit_reached=bool(data.get("limit_reached")),
            sampled_at=sampled_at,
        )

    def exhausted(self, reset_after_seconds: float, now: float) -> "QuotaMeter":
        """The meter after a 429: the primary window is out until ``reset``."""
        primary = self.windows.get("primary")
        size = primary.window_seconds if primary else max(reset_after_seconds, 1.0)
        windows = dict(self.windows)
        windows["primary"] = QuotaWindow(100.0, size, reset_after_seconds)
        return QuotaMeter(windows=windows, plan_type=self.plan_type, limit_reached=True, sampled_at=now)


# Background work stops at this share of any window, whatever the pace says.
BACKGROUND_CEILING_PERCENT = 90.0
# Background work may run this far ahead of the elapsed fraction of a window.
BACKGROUND_HEADSTART_PERCENT = 15.0


def background_decision(
    meter: Optional[QuotaMeter],
    now: float,
    *,
    ceiling: float = BACKGROUND_CEILING_PERCENT,
    headstart: float = BACKGROUND_HEADSTART_PERCENT,
) -> Tuple[bool, str]:
    """(may background work run now?, one-line reason). Pure."""
    if meter is None or not meter.windows:
        return True, "no meter yet"
    parts = []
    for name, w in meter.windows.items():
        if w.has_reset_by(meter.sampled_at, now):
            parts.append(f"{name} window reset since the last sample")
            continue
        remaining = w.reset_after_at(meter.sampled_at, now)
        if w.used_percent >= 100 or (meter.limit_reached and name == "primary"):
            return False, (
                f"{name} window exhausted ({w.used_percent:.0f}% used); resets in {_fmt_duration(remaining)}"
            )
        if w.used_percent >= ceiling:
            return False, (
                f"{name} window at {w.used_percent:.0f}% (ceiling {ceiling:.0f}% keeps the rest for chat); "
                f"resets in {_fmt_duration(remaining)}"
            )
        pace = headstart + 100.0 * w.elapsed_fraction(meter.sampled_at, now)
        if w.used_percent >= pace:
            return False, (
                f"{name} window at {w.used_percent:.0f}% is ahead of pace ({pace:.0f}% line, "
                f"{w.elapsed_fraction(meter.sampled_at, now) * 100:.0f}% of the window elapsed); "
                f"resets in {_fmt_duration(remaining)}"
            )
        parts.append(f"{name} {w.used_percent:.0f}% used (pace line {pace:.0f}%)")
    return True, "; ".join(parts)


# ---- surplus (2026-09-24) -------------------------------------------------------------

# Chat always keeps at least this much of the window in hand.
SURPLUS_RESERVE_MIN_PERCENT = 3.0
# Surplus sits below background work and stops this far short of
# wherever ``background_decision`` would refuse it — the ceiling and the
# pace line alike: a job allowed at the line still spends a percent or two,
# and extraction, which is never retried, must keep running. (Was a 97%
# target until 2026-09-25: surplus would have held the window over the 90%
# ceiling for the last day of every week, starving extraction as on Sep 14.)
SURPLUS_CEILING_MARGIN_PERCENT = 5.0
# Where the surplus queue aims the window at its reset.
SURPLUS_TARGET_PERCENT = BACKGROUND_CEILING_PERCENT - SURPLUS_CEILING_MARGIN_PERCENT
# The owner's interactive burn (%/h) assumed when the spend ledger is too thin to
# measure it (``surplus_service.interactive_rate``): a reserve of a percent
# an hour holds surplus back until the last few days of a quiet week.
DEFAULT_INTERACTIVE_BURN = 1.0
# A meter sample older than this cannot justify spending (the gate's own
# staleness line: past it, ``acquire`` refreshes the meter first).
SURPLUS_MAX_METER_AGE_SECONDS = 900.0


def _pay_per_token() -> bool:
    """``EDWARD_LLM_PROVIDER=anthropic``: the spend cap, not the meter, decides."""
    try:
        from services import llm_spend

        return llm_spend.is_anthropic()
    except Exception:  # noqa: BLE001 — a broken switch must not stop the Codex path
        return False


def surplus_enabled(value: Optional[str] = None) -> bool:
    """``EDWARD_SURPLUS=off`` (or 0 / false / no) stops every surplus call
    and the surplus queue; anything else, or unset, leaves it on.

    Unset on Claude (``EDWARD_LLM_PROVIDER=anthropic``, 2026-10-10) means
    off: the queue spends a prepaid Codex week nobody is using, and on a
    pay-per-token API that is money. ``EDWARD_SURPLUS=on`` turns it on."""
    raw = os.getenv("EDWARD_SURPLUS", "") if value is None else value
    if value is None and not (raw or "").strip():
        try:
            from services.claude_routing import anthropic_enabled

            if anthropic_enabled():
                return False
        except Exception:  # noqa: BLE001
            pass
    return (raw or "").strip().lower() not in ("off", "0", "false", "no")


@dataclass(frozen=True)
class SurplusDecision:
    """May surplus work run now, and how fast should it spend?

    ``code`` is machine-readable: ``ok`` | ``disabled`` | ``no_meter`` |
    ``stale`` | ``reset`` | ``limit`` | ``cooldown`` | ``no_headroom`` |
    ``background`` (within the margin of where background work is refused) |
    ``pay_per_token`` (the Claude API: nothing expires, nothing is spare).
    The numbers are filled whenever the meter has them, allowed or not, so
    the card can show why."""

    allowed: bool
    reason: str
    used: Optional[float] = None
    hours_to_reset: Optional[float] = None
    reserve: Optional[float] = None
    headroom: Optional[float] = None
    target_rate: Optional[float] = None
    r_int: Optional[float] = None
    code: str = "ok"

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed, "reason": self.reason, "code": self.code, "used": self.used,
            "hours_to_reset": self.hours_to_reset, "reserve": self.reserve, "headroom": self.headroom,
            "target_rate": self.target_rate, "r_int": self.r_int,
        }


def surplus_decision(
    meter: Optional[QuotaMeter],
    now: float,
    r_int_pct_per_hour: Optional[float],
    *,
    reserve_min: float = SURPLUS_RESERVE_MIN_PERCENT,
    target: float = SURPLUS_TARGET_PERCENT,
    cooldown_s: float = 0.0,
    max_age_s: float = SURPLUS_MAX_METER_AGE_SECONDS,
    enabled: bool = True,
    pay_per_token: bool = False,
) -> SurplusDecision:
    """How much of the primary window surplus work may spend, and how fast. Pure.

    With ``u`` the used %, ``h`` the hours to the reset and ``r_int`` the owner's
    interactive burn in %/h::

        reserve     = max(reserve_min, r_int · h)   what chat may still need
        headroom    = target − reserve − u          what surplus may spend
        target_rate = headroom / h                  %/h to spend it evenly

    Why the week lands at ``target``: if chat keeps burning ``r_int`` it
    spends ``r_int · h`` by the reset, so ``u + r_int·h + headroom =
    target``. In the last ``reserve_min / r_int`` hours the floor holds
    ``reserve_min`` back for chat instead, and the week lands between
    ``target − reserve_min`` and ``target`` (chat's to use). The decision
    is recomputed from the live meter every time, so the controller is
    proportional: a heavy hour of chat raises ``u`` and the next
    ``target_rate`` drops by itself; a quiet day leaves more headroom.

    Not allowed with no meter, a sample older than ``max_age_s``, a window
    that reset since the sample (wait for a fresh reading), a window at its
    cap or ``limit_reached``, a 429 cooldown, headroom ≤ 0, a window within
    ``SURPLUS_CEILING_MARGIN_PERCENT`` of background's ceiling or pace line
    (surplus never takes background work's room), ``enabled=False``
    (``EDWARD_SURPLUS=off``), or ``pay_per_token`` (the Claude API bills
    every token and nothing expires at a reset, so nothing is spare).
    """
    r_int = r_int_pct_per_hour if (r_int_pct_per_hour is not None and r_int_pct_per_hour >= 0) \
        else DEFAULT_INTERACTIVE_BURN
    if pay_per_token:
        from services.llm_spend import PAY_PER_TOKEN_REASON

        return SurplusDecision(False, PAY_PER_TOKEN_REASON, r_int=r_int, code="pay_per_token")
    if not enabled:
        return SurplusDecision(False, "surplus is switched off (EDWARD_SURPLUS=off)", r_int=r_int, code="disabled")
    if meter is None or not meter.windows:
        return SurplusDecision(False, "no usage meter yet", r_int=r_int, code="no_meter")

    name = "primary" if "primary" in meter.windows else next(iter(meter.windows))
    w = meter.windows[name]
    used = float(w.used_percent)
    remaining_s = w.reset_after_at(meter.sampled_at, now)
    hours = max(0.0, remaining_s / 3600.0)
    reserve = max(reserve_min, r_int * hours)
    headroom = target - reserve - used
    target_rate = (headroom / hours) if (hours > 0 and headroom > 0) else 0.0

    def verdict(allowed: bool, reason: str, code: str) -> SurplusDecision:
        return SurplusDecision(allowed, reason, used=used, hours_to_reset=hours, reserve=reserve,
                               headroom=headroom, target_rate=target_rate, r_int=r_int, code=code)

    age = now - meter.sampled_at
    if age > max_age_s:
        return verdict(False, f"the meter is {_fmt_duration(age)} old; waiting for a fresh reading", "stale")
    if remaining_s <= 0:
        return verdict(False, f"the {name} window reset since the last sample; waiting for a fresh reading", "reset")
    if meter.limit_reached or used >= 100:
        return verdict(False, f"{name} window exhausted ({used:.0f}% used); resets in {_fmt_duration(remaining_s)}",
                       "limit")
    for other, ow in meter.windows.items():
        if other == name or ow.has_reset_by(meter.sampled_at, now):
            continue
        if ow.used_percent >= target:
            return verdict(False, f"{other} window at {ow.used_percent:.0f}% (surplus stops at {target:.0f}%)",
                           "limit")
    if cooldown_s > 0:
        return verdict(False, f"cooldown for another {cooldown_s:.0f}s after a 429", "cooldown")
    if headroom <= 0:
        return verdict(False, (
            f"{used:.0f}% used; chat's reserve is {reserve:.1f}% ({r_int:.2f}%/h × {hours:.0f}h, "
            f"floor {reserve_min:.0f}%) — no headroom under the {target:.0f}% target"), "no_headroom")
    margin = SURPLUS_CEILING_MARGIN_PERCENT
    bg_ok, bg_reason = background_decision(meter, now, ceiling=BACKGROUND_CEILING_PERCENT - margin,
                                           headstart=BACKGROUND_HEADSTART_PERCENT - margin)
    if not bg_ok:
        return verdict(False, f"background work comes first (surplus stops {margin:.0f}% short): {bg_reason}",
                       "background")
    return verdict(True, (
        f"{used:.0f}% used, {headroom:.1f}% headroom over a {reserve:.1f}% chat reserve; "
        f"spend {target_rate:.2f}%/h to land at {target:.0f}% in {_fmt_duration(remaining_s)}"), "ok")


class LLMRateGate:
    DEFAULT_COOLDOWN_SECONDS = 60.0
    # Cap how long an interactive call will block waiting for the window.
    # Was 90s until 2026-09-10. A heartbeat reply turn makes several gated
    # calls inside RESPOND_TIMEOUT_SECONDS; at 90s each it timed out, and
    # that failure path re-sent iMessages Edward had already delivered.
    INTERACTIVE_MAX_WAIT_SECONDS = 5.0
    # Ceiling on any single cooldown. A weekly cap answers with
    # resets_in_seconds of several days; the meter — not the cooldown — is
    # what keeps background work off a cap that is out for days.
    MAX_COOLDOWN_SECONDS = 3600.0
    # Minimum spacing between background calls (stampede guard only).
    BACKGROUND_MIN_INTERVAL_SECONDS = 5.0
    # Small spread after a cooldown so all deferred loops don't stampede.
    JITTER_MAX_SECONDS = 3.0
    # A meter older than this is refreshed (``refresh_meter``) before a
    # background decision is made on it.
    METER_STALE_SECONDS = 900.0
    # Meter updates arrive with every call; persist at most this often
    # (a 429 always persists).
    PERSIST_MIN_INTERVAL_SECONDS = 60.0
    # The meter's time series (``llm_meter_samples``) keeps at most one
    # reading a minute per window — enough to regress spend on tokens,
    # without a row per call.
    SAMPLE_ROW_MIN_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        wall_clock: Optional[Callable[[], float]] = None,
    ):
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._sleep = sleep or asyncio.sleep
        self._cooldown_until = 0.0
        self._last_background_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._meter: Optional[QuotaMeter] = None
        self._last_429_wall: Optional[float] = None
        self._last_persist_wall: Optional[float] = None
        self._call_times: Deque[float] = deque()
        # Set by the persistence layer: called with export_state() when the
        # state moves (throttled). Kept a plain attribute — no DB dependency.
        self.on_state_change: Optional[Callable[[dict], None]] = None
        # Set by startup: an async callable that fetches a fresh meter and
        # feeds it back through note_meter(). Used when the sample is stale.
        self.refresh_meter: Optional[Callable[[], Awaitable[None]]] = None
        # Called with (meter, source, windows) when a newer reading is
        # adopted and a window is due a time-series row (throttled per
        # window). ``get_llm_rate_gate`` points it at the spend ledger
        # (2026-09-24); a bare gate records nothing.
        self.on_meter_sample: Optional[Callable[[QuotaMeter, str, list], None]] = None
        self._last_sample_row_at: Dict[str, float] = {}
        # Surplus (2026-09-24): the owner's interactive burn in %/h, set by the
        # surplus controller from the spend ledger each tick; the default
        # until it has measured one. Surplus calls keep their own spacing so
        # they never delay a background call.
        self._surplus_r_int: float = DEFAULT_INTERACTIVE_BURN
        self._last_surplus_at: Optional[float] = None

    # ----- meter -----

    @property
    def meter(self) -> Optional[QuotaMeter]:
        """The Codex meter — None under pay-per-token, where the weekly
        window is not Edward's quota (the reading is kept in ``_meter``)."""
        if _pay_per_token():
            return None
        return self._meter

    def note_meter(self, meter: Optional[QuotaMeter], source: str = "headers") -> None:
        """Adopt a sample unless we already hold a newer one."""
        if meter is None:
            return
        if self._meter is not None and meter.sampled_at < self._meter.sampled_at:
            return
        self._meter = meter
        self._emit_meter_sample(meter, source)
        self._emit_state_change()

    def note_usage_headers(self, headers: Mapping[str, str]) -> None:
        self.note_meter(QuotaMeter.from_headers(headers, now=self._wall_clock()), source="headers")

    def note_usage_snapshot(self, payload: Any) -> None:
        self.note_meter(QuotaMeter.from_usage_payload(payload, now=self._wall_clock()), source="wham")

    def _emit_meter_sample(self, meter: QuotaMeter, source: str) -> None:
        """Hand a real reading to the time series, ≤ 1 row a minute per
        window. A 429's exhausted meter is synthetic and never comes here."""
        hook = self.on_meter_sample
        if hook is None:
            return
        due = []
        for name in meter.windows:
            last = self._last_sample_row_at.get(name)
            if last is None or meter.sampled_at - last >= self.SAMPLE_ROW_MIN_INTERVAL_SECONDS:
                due.append(name)
        if not due:
            return
        for name in due:
            self._last_sample_row_at[name] = meter.sampled_at
        try:
            hook(meter, source, due)
        except Exception:  # noqa: BLE001 — a series that cannot save is not a pacer problem
            pass

    def meter_age(self) -> Optional[float]:
        if self._meter is None:
            return None
        return max(0.0, self._wall_clock() - self._meter.sampled_at)

    # ----- 429 reporting -----

    def note_429(self, error_body: str) -> float:
        """Record a 429; returns the cooldown duration applied."""
        seconds = self._parse_resets_in_seconds(error_body)
        reset_after = seconds
        if seconds is None:
            seconds = self.DEFAULT_COOLDOWN_SECONDS
        seconds = min(seconds, self.MAX_COOLDOWN_SECONDS)
        seconds += random.uniform(0, self.JITTER_MAX_SECONDS)
        now = self._clock()
        until = now + seconds
        if until > self._cooldown_until:
            self._cooldown_until = until
        wall = self._wall_clock()
        self._last_429_wall = wall
        base = self._meter or QuotaMeter(sampled_at=wall)
        self._meter = base.exhausted(reset_after if reset_after is not None else seconds, now=wall)
        self._emit_state_change(force=True)
        return seconds

    def note_success(self) -> None:
        """A 200 is direct evidence the limit that opened the cooldown has
        lifted. The meter itself is refreshed by the same response's headers."""
        self._cooldown_until = 0.0

    @staticmethod
    def _parse_resets_in_seconds(error_body: str) -> Optional[float]:
        try:
            data = json.loads(error_body)
            value = data.get("error", {}).get("resets_in_seconds")
            if value is not None:
                return max(0.0, float(value))
        except (ValueError, TypeError, AttributeError):
            pass
        return None

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - self._clock())

    # ----- diagnostics -----

    def _prune(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._call_times and self._call_times[0] <= cutoff:
            self._call_times.popleft()

    def background_decision(self) -> Tuple[bool, str]:
        if _pay_per_token():
            from services import llm_spend

            return llm_spend.background_decision_cached()
        return background_decision(self._meter, now=self._wall_clock())

    # ----- surplus -----

    @property
    def surplus_r_int(self) -> float:
        return self._surplus_r_int

    def note_interactive_burn(self, pct_per_hour: Optional[float]) -> None:
        """The controller's measure of the owner's interactive burn (%/h); None or
        a negative value restores the default."""
        try:
            value = float(pct_per_hour) if pct_per_hour is not None else None
        except (TypeError, ValueError):
            value = None
        self._surplus_r_int = value if value is not None and value >= 0 else DEFAULT_INTERACTIVE_BURN

    def surplus_decision(self) -> SurplusDecision:
        return surplus_decision(self._meter, self._wall_clock(), self._surplus_r_int,
                                cooldown_s=self.cooldown_remaining(), max_age_s=self.METER_STALE_SECONDS,
                                enabled=surplus_enabled(), pay_per_token=_pay_per_token())

    async def ensure_fresh_meter(self) -> None:
        """Refresh the meter when it is missing or stale (the surplus tick
        asks before deciding; ``acquire`` does the same for its own calls)."""
        await self._maybe_refresh()

    def status(self) -> dict:
        now_wall = self._wall_clock()
        self._prune(self._clock())
        allowed, reason = self.background_decision()
        meter = self.meter
        windows = {}
        if meter is not None:
            for name, w in meter.windows.items():
                remaining = w.reset_after_at(meter.sampled_at, now_wall)
                windows[name] = {
                    "used_percent": w.used_percent,
                    "window_minutes": int(w.window_seconds // 60),
                    "reset_after_seconds": max(0.0, remaining),
                    "reset_at": now_wall + max(0.0, remaining),
                    "elapsed_fraction": w.elapsed_fraction(meter.sampled_at, now_wall),
                }
        return {
            "provider": "anthropic" if _pay_per_token() else "codex",
            "plan_type": meter.plan_type if meter else None,
            "limit_reached": bool(meter and meter.limit_reached),
            "windows": windows,
            "meter_age_seconds": self.meter_age(),
            "background_allowed": allowed,
            "background_reason": reason,
            "background_ceiling_percent": BACKGROUND_CEILING_PERCENT,
            "background_headstart_percent": BACKGROUND_HEADSTART_PERCENT,
            "cooldown_remaining": self.cooldown_remaining(),
            "last_429_wall": self._last_429_wall,
            "calls_last_hour": len(self._call_times),
            "surplus": self.surplus_decision().to_dict(),
        }

    # ----- persistence -----

    def export_state(self) -> dict:
        return {
            "meter": self._meter.to_dict() if self._meter else None,
            "last_429_wall": self._last_429_wall,
        }

    def restore_state(self, state: Any) -> None:
        """Tolerant of anything: a corrupt row must never stop the backend."""
        if not isinstance(state, dict):
            return
        meter = QuotaMeter.from_dict(state.get("meter"))
        if meter is not None:
            self._meter = meter
        raw = state.get("last_429_wall")
        try:
            self._last_429_wall = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            pass

    def _emit_state_change(self, force: bool = False) -> None:
        hook = self.on_state_change
        if hook is None:
            return
        wall = self._wall_clock()
        if not force and self._last_persist_wall is not None and wall - self._last_persist_wall < self.PERSIST_MIN_INTERVAL_SECONDS:
            return
        self._last_persist_wall = wall
        try:
            hook(self.export_state())
        except Exception:  # noqa: BLE001 — a pacer that cannot save is still a pacer
            pass

    # ----- acquisition -----

    async def _maybe_refresh(self) -> None:
        if self.refresh_meter is None:
            return
        age = self.meter_age()
        if age is not None and age < self.METER_STALE_SECONDS:
            return
        try:
            await self.refresh_meter()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rate gate: meter refresh failed (non-fatal): %s", exc)

    def _record_deferral(self, reason: str, purpose: str = "") -> None:
        """Ledger row: which background job was turned away, and why.

        ``purpose`` is stamped on the model by ``build_background_llm`` at
        construction; the stack seen from inside a langchain task never
        reaches the job that built the model, so the walk is only a fallback.
        """
        try:
            from services import event_ledger

            source = purpose or event_ledger.caller_label()
            event_ledger.record_event(kind="rate_gate", source=source, outcome="deferred", reason=reason)
        except Exception:  # noqa: BLE001
            pass

    async def acquire(self, priority: str = "interactive", purpose: str = "") -> bool:
        """Gate a Codex call. Returns True when the call may proceed.

        - ``background``: deferred during cooldowns and while the meter is
          ahead of pace or past the ceiling; spaced by a small minimum gap.
        - ``surplus`` (2026-09-24): like ``background``, but on
          ``surplus_decision`` — spare quota over chat's reserve — instead
          of the pace line, with its own spacing.
        - ``triage`` / ``interactive``: never deferred; wait (bounded)
          through a cooldown, then proceed.

        Under ``EDWARD_LLM_PROVIDER=anthropic`` (2026-10-10) the daily
        Claude spend cap decides instead (``llm_spend.check_spend``):
        background and surplus raise ``LLMRateLimitDeferred``, interactive
        and triage ``LLMSpendCapReached``; the Codex meter and its
        cooldown are not consulted.
        """
        if _pay_per_token():
            return await self.acquire_spend(priority, purpose)
        if priority == "surplus":
            return await self._acquire_surplus(purpose)
        if priority == "background":
            if self.cooldown_remaining() > 0:
                why = f"cooldown for another {self.cooldown_remaining():.0f}s"
                self._record_deferral(why, purpose)
                raise LLMRateLimitDeferred(f"Codex rate gate: background LLM call deferred, {why}")
            await self._maybe_refresh()
            allowed, reason = self.background_decision()
            if not allowed:
                self._record_deferral(reason, purpose)
                raise LLMRateLimitDeferred(f"Codex rate gate: background LLM call deferred, {reason}")
            wait = 0.0
            async with self._lock:
                now = self._clock()
                self._prune(now)
                if self._last_background_at is not None:
                    wait = max(0.0, self.BACKGROUND_MIN_INTERVAL_SECONDS - (now - self._last_background_at))
                reserved = now + wait
                self._last_background_at = reserved
                self._call_times.append(reserved)
            # Sleep outside the lock: a paced call must never block others.
            if wait > 0:
                await self._sleep(wait)
            return True

        remaining = self.cooldown_remaining()
        if remaining > 0:
            await self._sleep(min(remaining, self.INTERACTIVE_MAX_WAIT_SECONDS))
        self._call_times.append(self._clock())
        return True

    async def acquire_spend(self, priority: str = "interactive", purpose: str = "") -> bool:
        """Pay per token: the spend cap decides; background keeps its
        stampede spacing. Raises as ``llm_spend.check_spend`` does.

        ``acquire`` lands here under ``EDWARD_LLM_PROVIDER=anthropic``;
        ``ChatClaude`` calls it directly before its first request, whatever
        the switch says — a Claude call is paid per token even when built
        by hand (``scripts/claude_smoke.py``)."""
        from services import llm_spend

        await llm_spend.check_spend(priority, purpose=purpose or None)
        wait = 0.0
        async with self._lock:
            now = self._clock()
            self._prune(now)
            if priority == "background":
                if self._last_background_at is not None:
                    wait = max(0.0, self.BACKGROUND_MIN_INTERVAL_SECONDS - (now - self._last_background_at))
                self._last_background_at = now + wait
            self._call_times.append(now + wait)
        if wait > 0:
            await self._sleep(wait)
        return True

    async def _acquire_surplus(self, purpose: str) -> bool:
        if self.cooldown_remaining() > 0:
            why = f"cooldown for another {self.cooldown_remaining():.0f}s"
            self._record_deferral(f"surplus: {why}", purpose)
            raise LLMRateLimitDeferred(f"Codex rate gate: surplus LLM call deferred, {why}")
        await self._maybe_refresh()
        decision = self.surplus_decision()
        if not decision.allowed:
            self._record_deferral(f"surplus: {decision.reason}", purpose)
            raise LLMRateLimitDeferred(f"Codex rate gate: surplus LLM call deferred, {decision.reason}")
        wait = 0.0
        async with self._lock:
            now = self._clock()
            self._prune(now)
            if self._last_surplus_at is not None:
                wait = max(0.0, self.BACKGROUND_MIN_INTERVAL_SECONDS - (now - self._last_surplus_at))
            reserved = now + wait
            self._last_surplus_at = reserved
            self._call_times.append(reserved)
        if wait > 0:
            await self._sleep(wait)
        return True


_gate: Optional[LLMRateGate] = None


def _record_meter_sample(meter: QuotaMeter, source: str, windows: list) -> None:
    """The process gate's time-series hook: fire-and-forget rows in
    ``llm_meter_samples`` (services/codex_call_ledger.py)."""
    try:
        from services import codex_call_ledger

        codex_call_ledger.schedule_meter_samples(meter, source, windows)
    except Exception:  # noqa: BLE001
        pass


def get_llm_rate_gate() -> LLMRateGate:
    global _gate
    if _gate is None:
        _gate = LLMRateGate()
        _gate.on_meter_sample = _record_meter_sample
    return _gate
