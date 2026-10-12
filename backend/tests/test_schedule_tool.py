"""``schedule`` — reminders, tasks, self-wakes and Ellis's calendar
(tools-spec §3.12; the writes Ellis asks for are in
test_schedule_calendar_writes.py).

``add`` stamps ``requested_by_owner`` from the turn's policy, so a task Ellis
asked for fires with his authority and one Edward set for himself does
not; ``kind=wake`` is the mind's own wake (AREA 1 owns what it does); the
calendar is read through ``calendar_service`` when it exists and changes
only through the write actions.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from services.toolset import policy as P
from services.toolset import schedule as S
from services.toolset.result import ToolFailure

OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
WAKE = P.TurnPolicy(source="mind")


@pytest.fixture
def created(monkeypatch):
    from services import scheduled_events_service as ses

    made = []

    async def create_event(**kw):
        made.append(kw)
        return types.SimpleNamespace(id=f"ev{len(made)}", next_fire_at=datetime(2030, 1, 1, 15, 0))

    monkeypatch.setattr(ses, "create_event", create_event)
    return made


async def run(policy, **kw):
    with P.policy_scope(policy):
        return await S.schedule_impl(**kw)


def _future(hours=3) -> str:
    from services.timezone_utils import now_local

    return (now_local() + timedelta(hours=hours)).replace(tzinfo=None, microsecond=0).isoformat()


@pytest.mark.unit
class TestAdd:
    async def test_a_task_owner_asked_for_carries_his_stamp(self, created):
        out = await run(OWNER, action="add", when=_future(), what="Text Rowan happy birthday")
        assert "ev1" in out
        meta = created[0]["event_metadata"]
        assert meta["requested_by_owner"] is True and meta["kind"] == "task"
        assert created[0]["description"] == "Text Rowan happy birthday"

    async def test_a_task_edward_set_himself_does_not(self, created):
        await run(WAKE, action="add", when=_future(), what="Check ACME")
        assert created[0]["event_metadata"]["requested_by_owner"] is False

    async def test_a_wake_is_a_mind_wake(self, created):
        await run(WAKE, action="add", kind="wake", when=_future(), what="See if Parker answered", memory_id="m1")
        kw = created[0]
        assert kw["conversation_id"] == "edward-mind"
        assert kw["event_metadata"]["type"] == "mind_wake"
        assert kw["event_metadata"]["why"] == "See if Parker answered"
        assert kw["memory_id"] == "m1"
        assert kw["description"].startswith("[mind wake]")

    async def test_a_turn_that_may_not_schedule_is_refused(self, created):
        """A surplus job (reflect_lite, research, free time) or the pulse:
        an event would fire later with a fired event's reach, so a 3 am
        reflection could text someone at 9 (review 2026-10-11)."""
        quiet = P.TurnPolicy(source="mind", may_schedule=False, may_wake_later=False)
        with pytest.raises(ToolFailure, match="can't schedule"):
            await run(quiet, action="add", when=_future(), what="Text Parker about the trip")
        with pytest.raises(ToolFailure, match="can't schedule"):
            await run(quiet, action="add", kind="wake", when=_future(), what="look again")
        assert created == []

    async def test_reflect_lite_may_still_wake_itself(self, created):
        p = P.TurnPolicy(source="mind", may_schedule=False, may_wake_later=True)
        with pytest.raises(ToolFailure, match="can't schedule"):
            await run(p, action="add", kind="remind", when=_future(), what="x")
        await run(p, action="add", kind="wake", when=_future(), what="See if Parker answered")
        assert len(created) == 1 and created[0]["event_metadata"]["type"] == "mind_wake"

    async def test_a_zoneless_time_is_owners(self, created):
        from services.timezone_utils import local_naive_to_utc

        when = _future(5)
        await run(OWNER, action="add", when=when, what="x")
        assert created[0]["scheduled_at"] == local_naive_to_utc(datetime.fromisoformat(when))

    async def test_bad_or_past_times_are_refused(self, created):
        with pytest.raises(ToolFailure, match="ISO"):
            await run(OWNER, action="add", when="tomorrow-ish", what="x")
        with pytest.raises(ToolFailure, match="future"):
            await run(OWNER, action="add", when="2020-01-01T09:00:00", what="x")
        with pytest.raises(ToolFailure, match="what"):
            await run(OWNER, action="add", when=_future())
        assert created == []

    async def test_a_repeat_is_a_cron(self, created):
        await run(OWNER, action="add", when=_future(), what="water the plants", repeat="0 9 * * *")
        assert created[0]["recurrence_pattern"] == "0 9 * * *"

    async def test_a_reason_is_remembered_as_an_intention(self, created, monkeypatch):
        import services.memory_service as ms

        stored = []

        async def store(memories):
            stored.extend(memories)
            return [types.SimpleNamespace(id="m77")]

        monkeypatch.setattr(ms, "store_memories_with_conflict_detection", store)
        await run(WAKE, action="add", when=_future(), what="Check ACME", why="Ellis's bonus depends on it")
        assert stored and stored[0].memory_type == "intention"
        assert created[0]["memory_id"] == "m77"


@pytest.mark.unit
class TestListAndCancel:
    async def test_routes(self, monkeypatch):
        from services.graph import tools as legacy

        calls = []

        class R:
            def __init__(self, name, reply):
                self.name, self.reply = name, reply

            async def ainvoke(self, args):
                calls.append((self.name, dict(args)))
                return self.reply

        monkeypatch.setattr(legacy, "list_scheduled_events", R("list", "Found 1 event(s):\n- x"))
        monkeypatch.setattr(legacy, "cancel_scheduled_event", R("cancel", "Event ev1 cancelled."))
        assert (await run(WAKE, action="list")).startswith("Found")
        assert (await run(WAKE, action="cancel", id="ev1")) == "Event ev1 cancelled."
        assert calls == [("list", {"status": None}), ("cancel", {"event_id": "ev1"})]
        monkeypatch.setattr(legacy, "cancel_scheduled_event",
                            R("cancel", "Could not cancel event ev2. It may already be completed or cancelled."))
        with pytest.raises(ToolFailure, match="Could not cancel"):
            await run(WAKE, action="cancel", id="ev2")


@pytest.mark.unit
class TestTheCalendarRead:
    async def test_it_reads_through_calendar_service(self, monkeypatch):
        seen = {}

        async def events_between(start, end, query=None):
            seen.update(start=start, end=end, query=query)
            return [{"title": "Dentist", "start": datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc),
                     "end": datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc), "location": "Main St",
                     "all_day": False}]

        fake = types.ModuleType("services.calendar_service")
        fake.events_between = events_between
        fake.CalendarUnavailable = type("CalendarUnavailable", (Exception,), {})
        monkeypatch.setitem(sys.modules, "services.calendar_service", fake)
        out = await run(WAKE, action="calendar", query="dentist")
        assert "Dentist" in out and "Main St" in out
        assert seen["query"] == "dentist"
        assert seen["end"] - seen["start"] >= timedelta(days=7)

    async def test_unavailable_is_said_never_an_empty_day(self, monkeypatch):
        class CalendarUnavailable(Exception):
            pass

        async def events_between(start, end, query=None):
            raise CalendarUnavailable("schema changed")

        fake = types.ModuleType("services.calendar_service")
        fake.events_between = events_between
        fake.CalendarUnavailable = CalendarUnavailable
        monkeypatch.setitem(sys.modules, "services.calendar_service", fake)
        with pytest.raises(ToolFailure, match="schema changed"):
            await run(WAKE, action="calendar")

    def test_the_calendar_changes_only_through_the_write_actions(self):
        assert set(S.SCHEDULE_ACTIONS) - set(S.WRITE_ACTIONS) == {"add", "list", "cancel", "calendar", "reminders"}
