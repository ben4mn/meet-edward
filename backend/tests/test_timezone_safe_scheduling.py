"""The user's timezone is not the host's — cron advance and the tools that
parse "local" times must go through ``get_user_tz()``, never a naive
``datetime.astimezone()`` (which silently means the *process's* zone).

2026-09-21: ``settings.timezone`` was America/Los_Angeles while the mini
runs on America/Chicago. ``_next_cron_utc`` computed the next fire in
Pacific and converted it back as if it were Central — two hours early —
so every recurring row was due again the moment it finished: the ``*/15``
pulse row fired 10,214 times (about once a minute for a week) and the
07:00 / 18:00 mind ticks fired 63 and 81 times in two-hour storms.
``wake_me`` and ``schedule_event`` made the mirror mistake, so 34 of 46
``wake_me`` calls were refused as "in the past".
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def host_chicago_user_pacific(monkeypatch):
    """Pin the process zone to Springfield (so the test is deterministic on any
    host) and the *user* zone to Los Angeles — the live mismatch."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Chicago"
    time.tzset()
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz", ZoneInfo("America/Los_Angeles"))
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Los_Angeles")
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_next_cron_utc_uses_the_user_zone_not_the_host_zone(host_chicago_user_pacific):
    from services.scheduled_events_service import _next_cron_utc

    # 02:26 UTC = 19:26 Pacific → the next quarter hour is 19:30 Pacific = 02:30 UTC.
    after = datetime(2026, 9, 22, 2, 26, 0)
    assert _next_cron_utc("*/15 * * * *", after) == datetime(2026, 9, 22, 2, 30, 0)

    # 23:05 UTC Sep 21 = 16:05 Pacific → next 18:00 Pacific = 01:00 UTC Sep 22.
    # The bug returned 23:00 UTC Sep 21: already past, so the tick re-fired
    # every poll until Pacific time itself reached 18:00 — a two-hour storm.
    after = datetime(2026, 9, 21, 23, 5, 0)
    assert _next_cron_utc("0 18 * * *", after) == datetime(2026, 9, 22, 1, 0, 0)


@pytest.mark.parametrize("cron", ["*/15 * * * *", "0 7 * * *", "0 18 * * *", "0 9 * * 0", "30 16 * * 1-5"])
@pytest.mark.parametrize("minute", [0, 7, 26, 59])
def test_next_cron_utc_is_always_after_the_time_it_advances_from(host_chicago_user_pacific, cron, minute):
    from services.scheduled_events_service import _next_cron_utc

    for hour in range(0, 24, 3):
        after = datetime(2026, 9, 21, hour, minute, 0)
        nxt = _next_cron_utc(cron, after)
        assert nxt > after, f"{cron} after {after} gave {nxt}, not in the future"


class _FakeEvent:
    def __init__(self, scheduled_at):
        self.id = "evt-1"
        self.next_fire_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)


async def test_wake_me_reads_when_in_the_user_zone(host_chicago_user_pacific, monkeypatch):
    from services import scheduled_events_service
    from services.graph.tools import wake_me

    captured = {}

    async def fake_create_event(**kwargs):
        captured.update(kwargs)
        return _FakeEvent(kwargs["scheduled_at"])

    monkeypatch.setattr(scheduled_events_service, "create_event", fake_create_event)

    out = await wake_me.ainvoke({"when": "2099-01-01T16:30:00", "why": "check ACME at the close"})
    assert out.startswith("ok"), out
    # 16:30 in Los Angeles (PST, UTC-8) is 00:30 UTC the next day; the host
    # zone (Springfield, UTC-6) would have made it 22:30 UTC — two hours early.
    assert captured["scheduled_at"].astimezone(timezone.utc) == datetime(2099, 1, 2, 0, 30, tzinfo=timezone.utc)


async def test_schedule_event_reads_scheduled_at_in_the_user_zone(host_chicago_user_pacific, monkeypatch):
    from services import scheduled_events_service
    from services.graph.tools import schedule_event

    captured = {}

    async def fake_create_event(**kwargs):
        captured.update(kwargs)
        return _FakeEvent(kwargs["scheduled_at"])

    monkeypatch.setattr(scheduled_events_service, "create_event", fake_create_event)

    out = await schedule_event.ainvoke({"description": "Remind Ellis to drink water", "scheduled_at": "2099-01-01T16:30:00"})
    assert "scheduled" in out.lower(), out
    assert captured["scheduled_at"].astimezone(timezone.utc) == datetime(2099, 1, 2, 0, 30, tzinfo=timezone.utc)


async def test_wake_me_still_refuses_a_time_that_is_past_in_the_user_zone(host_chicago_user_pacific, monkeypatch):
    from services import scheduled_events_service
    from services.graph.tools import wake_me

    async def fake_create_event(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("create_event must not be called for a past time")

    monkeypatch.setattr(scheduled_events_service, "create_event", fake_create_event)
    past = (datetime.now(ZoneInfo("America/Los_Angeles")) - timedelta(hours=1)).replace(tzinfo=None, microsecond=0)
    assert (await wake_me.ainvoke({"when": past.isoformat(), "why": "x"})).startswith("Tool error:")
