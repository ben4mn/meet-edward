"""A stale calendar is said to be stale (2026-09-26).

Since 2026-09-25 ``display_service.get_calendar`` serves the last list
apple-mcp gave — for up to half an hour, flagged ``stale`` with the time of
that read in ``fetched_at`` — while Apple Calendar is not answering. Its
``source`` stays ``apple-mcp``, and every reader treated that as current:
the mind's pack, the pulse's trigger and the surplus queue's meeting prep
all read a list that could be half an hour old as today's calendar.

Now the pack and the pulse say "as of 2:05pm — Apple Calendar is not
answering", and meeting prep waits for a fresh list (a meeting moved in the
meantime would be prepared at the wrong time).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services import expression_service as es

pytestmark = pytest.mark.unit

CHICAGO = ZoneInfo("America/Chicago")
READ_AT = "2026-09-26T19:05:00.123456Z"  # 2:05pm in Springfield
EVENTS = [{"start": "2026-09-26T09:00:00-05:00", "title": "Standup"},
          {"start": "2026-09-26T15:30:00-05:00", "title": "Dentist"}]
FRESH = {"source": "apple-mcp", "fetched_at": READ_AT, "events": EVENTS}
STALE = {**FRESH, "stale": True}
AS_OF = re.compile(r"as of \d{1,2}:\d{2}[ap]m — Apple Calendar is not answering")


def _serve(monkeypatch, cal):
    from services import display_service

    async def get_calendar():
        return cal

    monkeypatch.setattr(display_service, "get_calendar", get_calendar)


# ---------------------------------------------------------------- pure --

def test_the_note_says_when_the_list_was_read_and_why_it_is_old():
    assert es.stale_calendar_note(STALE, tz=CHICAGO) == "as of 2:05pm — Apple Calendar is not answering"


def test_a_current_or_unreadable_calendar_has_no_note():
    assert es.stale_calendar_note(FRESH, tz=CHICAGO) is None
    assert es.stale_calendar_note({"source": "scheduled_events", "events": [], "stale": True}, tz=CHICAGO) is None
    assert es.stale_calendar_note({}, tz=CHICAGO) is None


def test_a_stale_list_without_a_read_time_still_says_so():
    note = es.stale_calendar_note({**STALE, "fetched_at": "garbage"}, tz=CHICAGO)
    assert note == "Apple Calendar is not answering — this is its last list"


# --------------------------------------------------------------- pulse --

def _signals(**kw):
    base = dict(now=datetime(2026, 9, 26, 19, 30, tzinfo=timezone.utc), attention={}, recent_messages=[],
                board_summary="", current_widget=None, current_screen=None,
                calendar=["9:00am Standup", "3:30pm Dentist"])
    base.update(kw)
    return es.PulseSignals(**base)


def test_the_pulse_trigger_says_a_stale_calendar_is_stale():
    text = es.build_pulse_trigger(_signals(calendar_note="as of 2:05pm — Apple Calendar is not answering"))
    assert "Today (as of 2:05pm — Apple Calendar is not answering): 9:00am Standup · 3:30pm Dentist" in text
    assert "Today: 9:00am Standup · 3:30pm Dentist" in es.build_pulse_trigger(_signals())


def test_a_stale_empty_calendar_is_not_an_empty_day_in_the_pulse():
    text = es.build_pulse_trigger(_signals(calendar=[], calendar_note="as of 2:05pm — Apple Calendar is not answering"))
    assert "Today (as of 2:05pm — Apple Calendar is not answering): nothing on it then" in text


def test_staleness_alone_does_not_wake_the_pulse():
    """Apple Calendar flapping between answering and not must not read as
    "the calendar changed" every tick: only the events are the signal."""
    fresh = es.signal_parts(_signals())
    stale = es.signal_parts(_signals(calendar_note="as of 2:05pm — Apple Calendar is not answering"))
    assert es.changed_parts(fresh, stale) == []


async def test_the_pulse_reads_the_note_from_the_calendar(monkeypatch):
    _serve(monkeypatch, STALE)
    lines, note = await es._calendar_signal()
    assert lines == ["9:00am Standup", "3:30pm Dentist"]
    assert note and AS_OF.fullmatch(note)

    _serve(monkeypatch, FRESH)
    assert await es._calendar_signal() == (["9:00am Standup", "3:30pm Dentist"], None)


# ---------------------------------------------------------------- mind --

async def test_the_minds_pack_says_a_stale_calendar_is_stale(monkeypatch):
    from tests.test_mind_service import _pack
    from services import mind_service as ms

    _serve(monkeypatch, STALE)
    unavailable: list = []
    lines = await ms._calendar_source(unavailable)
    assert lines == ["9:00am Standup", "3:30pm Dentist"], "the list itself is unchanged"
    [entry] = unavailable
    assert entry.startswith("calendar (") and AS_OF.search(entry)

    text = ms.render_pack(_pack(calendar=lines, unavailable=unavailable))
    assert "Today: 9:00am Standup · 3:30pm Dentist" in text
    assert AS_OF.search(text.split("Today:")[0]), "said before the list is read"


async def test_a_current_calendar_is_not_marked(monkeypatch):
    from services import mind_service as ms

    _serve(monkeypatch, FRESH)
    unavailable: list = []
    assert await ms._calendar_source(unavailable) == ["9:00am Standup", "3:30pm Dentist"]
    assert unavailable == []


async def test_a_stale_empty_calendar_is_not_an_empty_day_in_the_pack(monkeypatch):
    from tests.test_mind_service import _pack
    from services import mind_service as ms

    _serve(monkeypatch, {**STALE, "events": []})
    unavailable: list = []
    lines = await ms._calendar_source(unavailable)
    text = ms.render_pack(_pack(calendar=lines, unavailable=unavailable))
    assert "do not assume the day is empty" in text
