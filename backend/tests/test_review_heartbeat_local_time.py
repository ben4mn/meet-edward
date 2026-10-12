"""
Regression: review_heartbeat stamped every event 5 hours in the future.

Found 2026-08-22. Edward's briefing rendered:

    Aug 22, 04:22 AM — outgoing from +15555550171: Sleep well, see you tomorrow! 🌙

That message was actually sent at 11:22 PM on Aug 21, local time. Every event in
every briefing was off by the UTC offset, so Edward's sense of *when* things
happened in Ellis's messages was systematically wrong.

Cause: services/graph/tools.py formatted the naive-UTC created_at column with a
bare strftime and no conversion:

    time_str = e.created_at.strftime("%b %d, %I:%M %p")

heartbeat_events.created_at is naive UTC by design — the pool pins
`timezone: UTC` (services/database.py:29). The rest of the codebase converts
through timezone_utils.utc_to_local before display; format_chat_thread does it
correctly, which is why read_imessage_thread rendered the SAME message as
"yesterday 11:22 PM" while the briefing said 04:22 AM.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.timezone_utils import utc_to_local


def _event(**kw):
    base = dict(
        created_at=datetime(2026, 8, 22, 4, 22, 34),  # naive UTC
        is_from_user=True,
        sender="+15555550171",
        summary="Sleep well, see you tomorrow!",
        chat_name=None,
        triage_status="dismissed",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestEventLineIsLocalTime:
    def test_line_uses_local_clock_not_utc(self):
        from services.graph.tools import _format_heartbeat_event_line

        line = _format_heartbeat_event_line(_event())
        expected = utc_to_local(
            datetime(2026, 8, 22, 4, 22, 34, tzinfo=timezone.utc)
        ).strftime("%b %d, %I:%M %p")
        assert expected in line

    def test_the_specific_live_regression(self):
        """America/Chicago: 04:22 UTC is 11:22 PM the previous day."""
        from services.graph.tools import _format_heartbeat_event_line

        line = _format_heartbeat_event_line(_event())
        local = utc_to_local(datetime(2026, 8, 22, 4, 22, 34, tzinfo=timezone.utc))
        if local.utcoffset().total_seconds() == -5 * 3600:
            assert "11:22 PM" in line
            assert "Aug 21" in line
            assert "04:22 AM" not in line

    def test_line_still_carries_the_other_fields(self):
        from services.graph.tools import _format_heartbeat_event_line

        line = _format_heartbeat_event_line(_event(chat_name="Team Weekend"))
        assert "[dismissed]" in line
        assert "outgoing" in line
        assert "+15555550171" in line
        assert "Team Weekend" in line
        assert "Sleep well, see you tomorrow!" in line

    def test_incoming_direction(self):
        from services.graph.tools import _format_heartbeat_event_line

        assert "incoming" in _format_heartbeat_event_line(_event(is_from_user=False))

    def test_missing_timestamp_is_tolerated(self):
        from services.graph.tools import _format_heartbeat_event_line

        assert "unknown" in _format_heartbeat_event_line(_event(created_at=None))

    def test_missing_summary_is_tolerated(self):
        from services.graph.tools import _format_heartbeat_event_line

        assert "(no summary)" in _format_heartbeat_event_line(_event(summary=None))

    def test_tz_aware_timestamp_is_handled(self):
        """Belt and braces — the column is naive, but don't double-convert."""
        from services.graph.tools import _format_heartbeat_event_line

        aware = datetime(2026, 8, 22, 4, 22, 34, tzinfo=timezone.utc)
        assert _format_heartbeat_event_line(
            _event(created_at=aware)
        ) == _format_heartbeat_event_line(_event())
