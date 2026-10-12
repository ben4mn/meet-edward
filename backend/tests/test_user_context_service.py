"""R/G TDD red for D2 — user_context_service.

Exposes a cheap "is the user likely interruptible right now?" signal that
the proactive coordinator + push dispatcher consume. Inputs: the current
wall-clock time (local tz), the user's calendar events, and configured
quiet hours. Outputs: a ``UserContext`` dataclass with booleans for
``in_meeting``, ``in_focus_block``, ``quiet_hours_active``, ``likely_asleep``
plus a ``time_of_day`` bucket string.

No LLM calls, no network; this is a pure signal-derivation service that
can be overridden in tests by passing arguments explicitly. The default
(no-args) call reads the live calendar + heartbeat_config. Tests only
exercise the pure path.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest


def test_user_context_service_is_importable():
    from services import user_context_service

    assert hasattr(user_context_service, "get_user_context"), (
        "user_context_service must expose ``get_user_context``"
    )
    assert hasattr(user_context_service, "UserContext"), (
        "user_context_service must expose the ``UserContext`` dataclass"
    )


@pytest.mark.asyncio
async def test_time_of_day_buckets():
    """Verify the time_of_day classifier. Uses explicit times (naive-UTC
    converted to local) so tests are deterministic."""
    from services.user_context_service import get_user_context

    tz = ZoneInfo("America/Chicago")
    # noon local — "midday"
    noon = datetime(2026, 4, 22, 12, 0, tzinfo=tz)
    ctx = await get_user_context(at_time=noon, calendar_events=[])
    assert ctx.time_of_day == "midday", f"got {ctx.time_of_day!r}"

    # 02:00 local — "late_night"
    two_am = datetime(2026, 4, 22, 2, 0, tzinfo=tz)
    ctx = await get_user_context(at_time=two_am, calendar_events=[])
    assert ctx.time_of_day == "late_night", f"got {ctx.time_of_day!r}"

    # 19:00 local — "evening"
    evening = datetime(2026, 4, 22, 19, 0, tzinfo=tz)
    ctx = await get_user_context(at_time=evening, calendar_events=[])
    assert ctx.time_of_day == "evening", f"got {ctx.time_of_day!r}"


@pytest.mark.asyncio
async def test_quiet_hours_active_between_22_and_07():
    from services.user_context_service import get_user_context

    tz = ZoneInfo("America/Chicago")

    # 23:30 local — inside quiet (22:00-07:00)
    ctx = await get_user_context(
        at_time=datetime(2026, 4, 22, 23, 30, tzinfo=tz),
        calendar_events=[],
        quiet_hours=(22, 7),
    )
    assert ctx.quiet_hours_active is True
    assert ctx.likely_asleep is True

    # 09:00 local — outside quiet
    ctx = await get_user_context(
        at_time=datetime(2026, 4, 22, 9, 0, tzinfo=tz),
        calendar_events=[],
        quiet_hours=(22, 7),
    )
    assert ctx.quiet_hours_active is False
    assert ctx.likely_asleep is False


@pytest.mark.asyncio
async def test_in_meeting_when_calendar_event_is_active_now():
    from services.user_context_service import get_user_context

    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 22, 14, 0, tzinfo=tz)

    event = {
        "title": "1:1 with Manager",
        "start": (now - timedelta(minutes=30)).isoformat(),
        "end": (now + timedelta(minutes=30)).isoformat(),
    }
    ctx = await get_user_context(at_time=now, calendar_events=[event])
    assert ctx.in_meeting is True


@pytest.mark.asyncio
async def test_in_focus_block_detected_by_title_keyword():
    from services.user_context_service import get_user_context

    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 22, 14, 0, tzinfo=tz)

    event = {
        "title": "Focus Block — no interruptions",
        "start": (now - timedelta(minutes=10)).isoformat(),
        "end": (now + timedelta(minutes=50)).isoformat(),
    }
    ctx = await get_user_context(at_time=now, calendar_events=[event])
    assert ctx.in_focus_block is True
    # A focus block is also a form of being unavailable — in_meeting should
    # be True as a coarse "don't interrupt" signal.
    assert ctx.in_meeting is True


@pytest.mark.asyncio
async def test_past_event_does_not_trigger_in_meeting():
    from services.user_context_service import get_user_context

    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 22, 14, 0, tzinfo=tz)

    event = {
        "title": "Meeting that ended 10m ago",
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": (now - timedelta(minutes=10)).isoformat(),
    }
    ctx = await get_user_context(at_time=now, calendar_events=[event])
    assert ctx.in_meeting is False
    assert ctx.in_focus_block is False
