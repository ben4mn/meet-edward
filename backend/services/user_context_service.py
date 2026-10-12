"""User context signal service.

Returns a lightweight ``UserContext`` describing whether the user is
likely interruptible right now. Consumed by the proactive coordinator
and the LLM-initiated push dispatcher to decide whether to inject a
briefing, push a notification, or defer.

Signals:
  * ``time_of_day`` — "late_night" / "morning" / "midday" / "afternoon" / "evening"
  * ``in_meeting`` — any active calendar event overlaps ``at_time``
  * ``in_focus_block`` — active event with "focus" in the title
  * ``quiet_hours_active`` — ``at_time`` falls inside configured quiet hours
  * ``likely_asleep`` — alias for quiet_hours + late_night bucket

No LLM calls, no external network. The default (no-arg) call reads the
live Apple Calendar + the configured quiet hours (``EDWARD_QUIET_HOURS``) — tests pass
``at_time`` and ``calendar_events`` explicitly for determinism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from services.timezone_utils import now_local, get_user_tz


@dataclass
class UserContext:
    at_time: datetime
    time_of_day: str
    in_meeting: bool
    in_focus_block: bool
    quiet_hours_active: bool
    likely_asleep: bool


def _bucket_time_of_day(local_hour: int) -> str:
    """Coarse time-of-day classifier.

    Buckets:
      * late_night: 22:00-05:59
      * morning:    06:00-10:59
      * midday:     11:00-13:59
      * afternoon:  14:00-16:59
      * evening:    17:00-21:59
    """
    if local_hour >= 22 or local_hour < 6:
        return "late_night"
    if local_hour < 11:
        return "morning"
    if local_hour < 14:
        return "midday"
    if local_hour < 17:
        return "afternoon"
    return "evening"


def _quiet_hours_active(hour: int, start: int, end: int) -> bool:
    """True if ``hour`` falls in the [start, end) window.

    Handles wrap-around (e.g. 22-7 means 22-24 and 0-7); equal hours mean
    none (``speak_gate.in_quiet_hours``).
    """
    from services.speak_gate import in_quiet_hours

    return in_quiet_hours(hour, start, end)


def _parse_event_time(raw: str, fallback_tz) -> Optional[datetime]:
    """Parse an ISO datetime string the Apple Calendar MCP might return.

    Accepts ``Z`` suffix, offset ``+HH:MM`` / ``-HH:MM``, or no tz (treat
    as server-local). Returns tz-aware datetime or None.
    """
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=fallback_tz)
    return dt


async def get_user_context(
    at_time: Optional[datetime] = None,
    calendar_events: Optional[list[dict]] = None,
    quiet_hours: Optional[tuple[int, int]] = None,
) -> UserContext:
    """Compute a user-context snapshot.

    Args:
        at_time: tz-aware current moment. Defaults to ``now_local()``.
        calendar_events: list of events with ``title``, ``start``, ``end``
            ISO strings. ``None`` means "read from Apple Calendar MCP" (not
            yet wired — returns empty list for now).
        quiet_hours: (start_hour, end_hour) local-time window where the
            user is considered offline. Defaults to the configured quiet
            hours (``speak_gate.quiet_hours()``: ``EDWARD_QUIET_HOURS``,
            else 22:00-07:00).

    Returns:
        ``UserContext`` with derived signals.
    """
    tz = get_user_tz()
    if at_time is None:
        at_time = now_local()
    elif at_time.tzinfo is None:
        # Bare-naive input — treat as local, not UTC.
        at_time = at_time.replace(tzinfo=tz)
    else:
        at_time = at_time.astimezone(tz)

    local_hour = at_time.hour
    time_of_day = _bucket_time_of_day(local_hour)
    if quiet_hours is None:
        from services.speak_gate import quiet_hours as configured_quiet_hours

        quiet_hours = configured_quiet_hours()
    start_h, end_h = quiet_hours
    quiet_active = _quiet_hours_active(local_hour, start_h, end_h)
    likely_asleep = quiet_active and time_of_day == "late_night"

    in_meeting = False
    in_focus_block = False

    if calendar_events is None:
        calendar_events = []

    for evt in calendar_events:
        start = _parse_event_time(evt.get("start") or "", tz)
        end = _parse_event_time(evt.get("end") or "", tz)
        if start is None or end is None:
            continue
        if not (start <= at_time < end):
            continue
        # Event is active now.
        in_meeting = True
        title = (evt.get("title") or "").lower()
        if "focus" in title:
            in_focus_block = True

    return UserContext(
        at_time=at_time,
        time_of_day=time_of_day,
        in_meeting=in_meeting,
        in_focus_block=in_focus_block,
        quiet_hours_active=quiet_active,
        likely_asleep=likely_asleep,
    )
