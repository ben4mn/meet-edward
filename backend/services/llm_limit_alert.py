"""Tell the owner when Codex is rate-limiting Edward — once, not per request.

Codex fallback only (``EDWARD_LLM_PROVIDER=codex``).

Astra is the heaviest Codex tier, so the ChatGPT subscription's rolling
window can trip. Two signals reach here from ``codex_chat_model``:

- ``429`` — a rolling-window cooldown; the rate gate paces background work.
- ``usage_limit_reached`` — the account's cap (weekly on Pro), shared by
  every GPT model; nothing gets through until the reset.

Every event is logged at WARNING with the stable tag ``CODEX_LIMIT`` so
``grep CODEX_LIMIT /tmp/edward-backend.log`` is the trigger for humans and
scripts alike. Pushes are throttled by :class:`LimitAlertPolicy`: one per
quiet hour for cooldowns, with the hard cap allowed to break through once
per hour on its own — a push per 429 would train the owner to ignore them.

The push text is static on purpose (no LLM in the failure path).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

from services.push_service import send_push_notification

logger = logging.getLogger(__name__)

HARD_CAP = "usage_limit_reached"


class LimitAlertPolicy:
    """Pure throttle + counter. No I/O, injectable clock."""

    QUIET_SECONDS = 3600.0
    WINDOW_SECONDS = 4 * 3600.0

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock = clock or time.monotonic
        self._events: Deque[Tuple[float, str]] = deque()
        self._last_push_at: Optional[float] = None
        self._last_hard_push_at: Optional[float] = None

    def _prune(self, now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def record(self, kind: str) -> None:
        now = self._clock()
        self._events.append((now, kind))
        self._prune(now)

    def recent_count(self) -> int:
        self._prune(self._clock())
        return len(self._events)

    def should_push(self, kind: str) -> bool:
        """Record the event and say whether a push is due."""
        self.record(kind)
        now = self._clock()
        if kind == HARD_CAP:
            last = self._last_hard_push_at
        else:
            last = self._last_push_at
        if last is not None and now - last < self.QUIET_SECONDS:
            return False
        self._last_push_at = now
        if kind == HARD_CAP:
            self._last_hard_push_at = now
        return True


def _minutes(seconds: Optional[float]) -> str:
    if not seconds:
        return "unknown"
    return f"{max(1, round(seconds / 60))} min"


def _duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "unknown"
    seconds = int(seconds)
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{max(1, seconds // 60)} min"


def format_alert(
    kind: str,
    model: str,
    recent: int,
    cooldown_s: Optional[float],
    reset_after_s: Optional[float] = None,
    used_percent: Optional[float] = None,
) -> Tuple[str, str]:
    usage = f" Meter: {used_percent:.0f}% of the window used." if used_percent is not None else ""
    if kind == HARD_CAP:
        title = "Edward: Codex usage cap hit"
        body = (
            f"The ChatGPT account's usage cap is reached (hit while running {model}). "
            f"It is shared by every GPT model, so a different model will not help; it resets in "
            f"{_duration(reset_after_s)}. Chat still tries each turn; background work pauses "
            f"until then.{usage} {recent} limit events in the last 4h."
        )
    else:
        title = "Edward: Codex rate limit"
        body = (
            f"{model} is being rate limited by ChatGPT: {recent} in the last 4h, "
            f"cooldown {_minutes(cooldown_s)}.{usage} Chat may stall briefly; background "
            f"work is paced against the meter."
        )
    return title, body


_policy: Optional[LimitAlertPolicy] = None


def get_policy() -> LimitAlertPolicy:
    global _policy
    if _policy is None:
        _policy = LimitAlertPolicy()
    return _policy


async def handle_limit_event(kind: str, model: str, cooldown_seconds: Optional[float]) -> bool:
    """Log the event (always) and push the owner (throttled). Returns whether a
    push was attempted. Never raises."""
    policy = get_policy()
    try:
        push_due = policy.should_push(kind)
        recent = policy.recent_count()
        logger.warning(
            "CODEX_LIMIT kind=%s model=%s cooldown=%ss recent_4h=%d push=%s",
            kind, model, "-" if cooldown_seconds is None else f"{cooldown_seconds:.0f}",
            recent, "sent" if push_due else "suppressed",
        )
        if not push_due:
            return False
        reset_after = used = None
        try:
            from services.llm_rate_gate import get_llm_rate_gate

            primary = get_llm_rate_gate().status()["windows"].get("primary")
            if primary:
                reset_after = primary.get("reset_after_seconds")
                used = primary.get("used_percent")
        except Exception:  # noqa: BLE001 — the meter is a nicety here
            pass
        title, body = format_alert(kind, model, recent, cooldown_seconds, reset_after_s=reset_after, used_percent=used)
        await send_push_notification(title, body, url="/settings/room/work", tag="codex-limit", source="codex_limit")
        return True
    except Exception as e:
        logger.warning("CODEX_LIMIT alert failed: %s", e)
        return False
