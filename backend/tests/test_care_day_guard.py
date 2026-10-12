"""A dose logged from Today lands on the care day it was for (review
2026-09-26, today-stale-cache-actionable, server guard).

Today's "Log given" carries the board's ``care_day``; the hub route refuses
a log whose care day is no longer today's (a feed cached last night, tapped
after the 3 am roll-over) with 409 and words Ellis can act on, and logs
nothing. The hub's own taps send no care day and are never refused.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.today_service import TodayInputs, compose_today

NOW = datetime(2026, 9, 6, 1, 41, tzinfo=timezone.utc)  # 8:41 PM Springfield, Sat Sep 5

BOARD = {
    "care_day": "2026-09-05",
    "pets": [
        {
            "id": "p1",
            "name": "Biscuit",
            "meds": [
                {"id": "m1", "name": "Carprofen", "dose": "100 mg", "status": "overdue",
                 "minutes_late": 22, "at_time": "18:00", "kind": "med"},
            ],
        }
    ],
}


def test_log_given_carries_the_care_day_it_was_for():
    out = compose_today(TodayInputs(board=BOARD), now=NOW, tz_name="America/Chicago")
    log = next(a for a in out["needs"][0]["actions"] if a["id"] == "log")
    assert log["body"]["for_care_day"] == "2026-09-05"
    assert log["body"]["given_by"] == "Ellis"


def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       cookies={"edward_token": create_access_token()})


@pytest.fixture
def logged(monkeypatch):
    from services import hub_service

    calls = []

    async def fake_log(item_id, **kw):
        calls.append((item_id, kw))
        return {"logged": [item_id]}

    async def fake_touch(person):
        return None

    monkeypatch.setattr(hub_service, "log_care", fake_log)
    monkeypatch.setattr(hub_service, "touch_hub_person", fake_touch)
    return calls


@pytest.mark.asyncio
async def test_a_log_for_yesterdays_care_day_is_refused(logged, monkeypatch):
    from routers import hub as hub_router
    from datetime import date

    monkeypatch.setattr(hub_router, "_current_care_day", lambda: date(2026, 9, 6))
    async with _client() as client:
        r = await client.post("/api/hub/care/m1/log", json={"given_by": "Ellis", "for_care_day": "2026-09-05"})
    assert r.status_code == 409
    assert "yesterday's care day" in r.json()["detail"]
    assert logged == [], "nothing logged"


@pytest.mark.asyncio
async def test_todays_care_day_and_the_hubs_own_taps_log(logged, monkeypatch):
    from routers import hub as hub_router
    from datetime import date

    monkeypatch.setattr(hub_router, "_current_care_day", lambda: date(2026, 9, 6))
    async with _client() as client:
        r1 = await client.post("/api/hub/care/m1/log", json={"given_by": "Ellis", "for_care_day": "2026-09-06"})
        r2 = await client.post("/api/hub/care/m1/log", json={"given_by": "Rowan"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert [c[0] for c in logged] == ["m1", "m1"]
