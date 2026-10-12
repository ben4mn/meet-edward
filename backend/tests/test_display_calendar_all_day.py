"""An all-day event says so (review 2026-09-26, screen-calendar-all-day).

/screen showed an all-day event as "12:00 am – 12:00 am · in now"; the hub
got it right by inferring from the shape. apple-mcp flags all-day events
(`isAllDay`), so the normalised event carries ``all_day`` and the pages
only infer it when the flag is missing.
"""

from __future__ import annotations

import json

import pytest


class _Result:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_the_all_day_flag_rides_through(monkeypatch):
    from services import display_service
    from services import mcp_client

    monkeypatch.setattr(display_service, "_find_calendar_tool", lambda: object())

    events = [
        {"id": "a", "title": "Biscuit's vet week", "startDate": "2026-09-26T00:00:00-05:00",
         "endDate": "2026-09-27T00:00:00-05:00", "isAllDay": True},
        {"id": "b", "title": "Call with the contractor", "startDate": "2026-09-26T19:34:00-05:00",
         "endDate": "2026-09-26T20:00:00-05:00", "isAllDay": False},
        {"id": "c", "title": "Standup", "startDate": "2026-09-26T09:00:00-05:00",
         "endDate": "2026-09-26T09:15:00-05:00"},
        {"id": "d", "title": "Holiday", "start": "2026-09-26T00:00:00-05:00",
         "end": "2026-09-27T00:00:00-05:00", "allDay": True},
    ]

    async def fake_call(tool, args, timeout=None):
        return _Result(json.dumps(events))

    monkeypatch.setattr(mcp_client, "call_tool_bounded", fake_call)
    out = await display_service._calendar_via_mcp()
    by_id = {e["id"]: e for e in out}
    assert by_id["a"]["all_day"] is True
    assert by_id["b"]["all_day"] is False
    assert by_id["c"]["all_day"] is False
    assert by_id["d"]["all_day"] is True
