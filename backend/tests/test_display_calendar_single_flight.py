"""The Apple Calendar read is shared, cached, and survives a bad minute
(2026-09-25 production audit).

``display_service.get_calendar`` had a 30 s cache, no single-flight and no
memory of a failure. ``/screen`` polls the display signals twice every two
seconds and each poll reads the calendar, so when apple-mcp was slow every
poll started its own 10 s MCP call: 17 MCP timeouts in 35 minutes, each
one reopening the MCP session under the other callers, and every wake's
pack saying "Not readable this wake: calendar".

Now:

* one read at a time — callers that arrive during a read wait for it;
* a good read is kept ``_CALENDAR_TTL_SECONDS`` (120–300 s);
* a failed read is remembered ``_CALENDAR_FAILURE_TTL_SECONDS`` (60 s),
  and for up to ``_CALENDAR_STALE_MAX_SECONDS`` (30 min) the last good
  list for today is served, flagged ``stale``;
* the signals poller never waits on the calendar (``get_calendar_nowait``).
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit

EVENTS = [{"id": "e1", "title": "Vet: Biscuit", "start": "2026-09-25T15:00:00-05:00",
           "end": "2026-09-25T15:30:00-05:00", "where": "", "kind": "personal"}]


class _Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def ds(monkeypatch):
    from services import display_service

    monkeypatch.setattr(display_service, "_calendar_cache", {"data": None, "fetched_at": 0.0})
    clock = _Clock()
    monkeypatch.setattr(display_service.time, "time", clock)
    display_service._test_clock = clock

    async def _fallback():
        return [{"id": "job", "title": "expression_pulse", "start": "2026-09-25T10:00:00-05:00"}]

    monkeypatch.setattr(display_service, "_calendar_fallback_from_events", _fallback)
    return display_service


def _scripted_mcp(ds, monkeypatch, answers, delay: float = 0.0):
    calls = []

    async def _mcp():
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return None if answer is None else list(answer)

    monkeypatch.setattr(ds, "_calendar_via_mcp", _mcp)
    return calls


def test_the_ttls_are_the_audit_numbers(ds):
    assert 120 <= ds._CALENDAR_TTL_SECONDS <= 300
    assert ds._CALENDAR_FAILURE_TTL_SECONDS == 60
    assert ds._CALENDAR_STALE_MAX_SECONDS == 30 * 60


async def test_five_concurrent_reads_make_one_mcp_call(ds, monkeypatch):
    calls = _scripted_mcp(ds, monkeypatch, [EVENTS], delay=0.05)

    results = await asyncio.gather(*(ds.get_calendar() for _ in range(5)))

    assert len(calls) == 1
    assert all(r["events"] == EVENTS and r["source"] == "apple-mcp" for r in results)


async def test_a_good_read_is_kept_past_thirty_seconds(ds, monkeypatch):
    calls = _scripted_mcp(ds, monkeypatch, [EVENTS])
    await ds.get_calendar()
    ds._test_clock.t += 100
    await ds.get_calendar()
    assert len(calls) == 1
    ds._test_clock.t += ds._CALENDAR_TTL_SECONDS
    await ds.get_calendar()
    assert len(calls) == 2


async def test_a_failure_after_a_success_serves_the_earlier_events_flagged_stale(ds, monkeypatch):
    calls = _scripted_mcp(ds, monkeypatch, [EVENTS, None])
    first = await ds.get_calendar()
    assert first.get("stale") is not True

    ds._test_clock.t += ds._CALENDAR_TTL_SECONDS + 1
    second = await ds.get_calendar()

    assert len(calls) == 2
    assert second["source"] == "apple-mcp"
    assert second["events"] == EVENTS
    assert second["stale"] is True
    assert second["fetched_at"] == first["fetched_at"], "says how old the list is"


async def test_a_failure_is_remembered_for_a_minute(ds, monkeypatch):
    calls = _scripted_mcp(ds, monkeypatch, [None])
    first = await ds.get_calendar()
    assert first["source"] == "scheduled_events"
    ds._test_clock.t += 30
    await ds.get_calendar()
    assert len(calls) == 1, "a read that just failed is not retried by every poll"
    ds._test_clock.t += 31
    await ds.get_calendar()
    assert len(calls) == 2


async def test_the_last_good_list_expires_after_thirty_minutes(ds, monkeypatch):
    _scripted_mcp(ds, monkeypatch, [EVENTS, None])
    await ds.get_calendar()
    ds._test_clock.t += ds._CALENDAR_STALE_MAX_SECONDS + 1
    out = await ds.get_calendar()
    assert out["source"] == "scheduled_events"
    assert out.get("stale") is not True


async def test_yesterdays_list_is_never_served_as_today(ds, monkeypatch):
    _scripted_mcp(ds, monkeypatch, [EVENTS, None])
    await ds.get_calendar()
    ds._calendar_cache["last_good_day"] = "1999-01-01"
    ds._test_clock.t += ds._CALENDAR_TTL_SECONDS + 1
    out = await ds.get_calendar()
    assert out["source"] == "scheduled_events"


async def test_nowait_never_waits_and_refreshes_in_the_background(ds, monkeypatch):
    calls = _scripted_mcp(ds, monkeypatch, [EVENTS], delay=0.2)

    assert ds.get_calendar_nowait() is None, "nothing cached yet: no answer, no wait"
    await asyncio.sleep(0)  # the background read starts
    assert ds.get_calendar_nowait() is None
    await asyncio.sleep(0.3)
    assert len(calls) == 1, "one background read, however often it was asked"
    assert ds.get_calendar_nowait()["events"] == EVENTS


async def test_the_signals_poller_does_not_wait_on_a_slow_calendar(ds, monkeypatch):
    from services import display_signals

    def _no_db():
        raise RuntimeError("no database in this test")

    monkeypatch.setattr(display_signals, "async_session", _no_db)
    _scripted_mcp(ds, monkeypatch, [EVENTS], delay=5.0)
    out = await asyncio.wait_for(display_signals.get_signals_summary(), timeout=2.0)
    assert out["calendar_near_fire_at"] is None
