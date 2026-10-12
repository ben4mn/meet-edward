"""``schedule`` changes Ellis's calendar and Reminders when they ask (2026-10-11).

Few restrictions on Edward, by design: reads
— the calendar and the open Reminders — are open in every turn on every
surface. Writes — add, move or change, delete an event (inviting people
included); add, check off, delete a reminder — happen when Ellis is the one
asking: one predicate, ``TurnPolicy.may_change_owners_calendar()``, so letting
Edward act on his own initiative later is one line. The household hub's
voice is not Ellis, a friend's ping is not Ellis, and no wake, pulse or event
Edward set for himself is Ellis.

The EventKit helper is stubbed throughout (``eventkit_service``'s typed
helpers); nothing here reaches EventKit, Calendar or Reminders.
"""

from __future__ import annotations

import re
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import eventkit_service as ek
from services.toolset import names
from services.toolset import policy as P
from services.toolset import schedule as S
from services.toolset.result import ToolFailure

pytestmark = pytest.mark.unit

CHI = ZoneInfo("America/Chicago")
OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
HUB = P.default_policy("hub_voice")
FRIEND = P.TurnPolicy(source="ping", third_party=True, may_reach_people=True, reply_to=("chat123",))
MIND = P.default_policy("mind")
PULSE = P.default_policy("pulse")
WRITES = ("event_add", "event_update", "event_delete", "reminder_add", "reminder_complete", "reminder_delete")


@pytest.fixture(autouse=True)
def chicago(monkeypatch):
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz", CHI)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")


@pytest.fixture(autouse=True)
def fresh_adds():
    S.reset_add_history()
    yield
    S.reset_add_history()


@pytest.fixture
def helper(monkeypatch):
    """The six typed helpers, recording their payloads."""
    calls = []
    replies = {}

    def make(command):
        async def fake(payload):
            calls.append((command, dict(payload)))
            reply = replies.get(command)
            if isinstance(reply, BaseException):
                raise reply
            return reply or {"ok": True}
        return fake

    for attr, command in (("add_event", "event_add"), ("update_event", "event_update"),
                          ("delete_event", "event_delete"), ("add_reminder", "reminder_add"),
                          ("complete_reminder", "reminder_complete"), ("delete_reminder", "reminder_delete")):
        monkeypatch.setattr(ek, attr, make(command))
    return types.SimpleNamespace(calls=calls, replies=replies)


@pytest.fixture
def no_hints(monkeypatch):
    from services import calendar_service, reminders_service

    async def none(*a, **k):
        return None

    monkeypatch.setattr(calendar_service, "event_by_id", none)
    monkeypatch.setattr(reminders_service, "reminder_by_id", none)


async def run(policy, **kw):
    with P.policy_scope(policy):
        return await S.schedule_impl(**kw)


def event_reply(**over):
    ev = {"id": "E1", "calendar": "Home", "title": "Dentist", "start": "2026-10-12T14:00:00Z",
          "end": "2026-10-12T15:00:00Z", "all_day": False}
    ev.update(over)
    return {"ok": True, "event": ev, "attendees_invited": [], "attendees_failed": []}


# ------------------------------------------------------------ the predicate --

class TestTheOnePredicate:
    @pytest.mark.parametrize("source", ["chat", "voice", "widget", "alexa", "ping_owner"])
    def test_owners_own_turns_may(self, source):
        assert P.default_policy(source).may_change_owners_calendar() is True

    @pytest.mark.parametrize("source", ["hub_voice", "mind", "pulse", "heartbeat", "worker", "background",
                                        "twilio", "ping"])
    def test_nobody_else_may(self, source):
        assert P.default_policy(source).may_change_owners_calendar() is False

    def test_a_friends_ping_may_not(self):
        assert FRIEND.may_change_owners_calendar() is False

    def test_a_task_owner_scheduled_may_and_edwards_own_may_not(self, monkeypatch):
        from services import llm_spend

        monkeypatch.setattr(llm_spend, "in_own_work", lambda: False)
        assert P.default_policy("scheduled_event").may_change_owners_calendar() is True
        monkeypatch.setattr(llm_spend, "in_own_work", lambda: True)
        assert P.default_policy("scheduled_event").may_change_owners_calendar() is False

    def test_it_is_owner_asked_today(self):
        """One line to flip later: today it is exactly ``owner_asked``."""
        assert P.TurnPolicy(source="x", owner_asked=True).may_change_owners_calendar() is True
        assert P.TurnPolicy(source="chat", owner_asked=False).may_change_owners_calendar() is False


class TestRefusedOutsideOwnersTurn:
    @pytest.mark.parametrize("policy", [HUB, FRIEND, MIND, PULSE], ids=["hub_voice", "friend_ping", "mind", "pulse"])
    @pytest.mark.parametrize("action", WRITES)
    async def test_refused_and_the_helper_is_never_called(self, helper, no_hints, policy, action):
        with pytest.raises(ToolFailure) as exc:
            await run(policy, action=action, what="Dentist", start="2026-10-12T09:00:00", when="2026-10-12T09:00:00",
                      id="cal-U1-813456000" if action.startswith("event") else "rem-R1")
        assert str(exc.value) == S.CHANGE_REFUSAL
        assert helper.calls == []

    async def test_edwards_own_scheduled_event_is_refused(self, helper, no_hints, monkeypatch):
        from services import llm_spend

        monkeypatch.setattr(llm_spend, "in_own_work", lambda: True)
        with pytest.raises(ToolFailure, match="isn't theirs"):
            await run(P.default_policy("scheduled_event"), action="event_add", what="x", start="2026-10-12")
        assert helper.calls == []

    async def test_a_friends_ping_scope_leaves_it_to_the_policy(self):
        """pings.scope refuses nothing here (no new block): a friend may
        read Ellis's calendar and Reminders, and a write is refused by
        may_change_owners_calendar, not by the ping scope."""
        from services.pings import hooks, scope

        token = hooks.CURRENT_PING.set({"third_party": True, "chat_identifier": "chat123"})
        try:
            assert await scope.refusal("schedule", {"action": "reminders"}) is None
            assert await scope.refusal("schedule", {"action": "calendar"}) is None
            assert await scope.refusal("schedule", {"action": "event_add", "what": "x"}) is None
        finally:
            hooks.CURRENT_PING.reset(token)

    def test_the_dry_run_records_every_write(self):
        from services.graph import tool_loop

        for action in WRITES:
            args = {"action": action}
            assert names.tier("schedule", args) == "commitment", action
            assert tool_loop.is_effectful("schedule", args), action
        for read in ("calendar", "reminders", "list"):
            assert names.tier("schedule", {"action": read}) is None


# ------------------------------------------------------------------ events --

class TestEventAdd:
    async def test_owners_zone_and_an_hour_by_default(self, helper):
        helper.replies["event_add"] = event_reply()
        out = await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00")
        ((command, payload),) = helper.calls
        assert command == "event_add"
        assert payload == {"title": "Dentist", "timezone": "America/Chicago", "all_day": False,
                           "start": "2026-10-12T14:00:00Z", "end": "2026-10-12T15:00:00Z"}
        assert out.splitlines()[0] == "Added “Dentist” to Ellis's calendar (Home): Mon Oct 12, 9:00–10:00 am. id=cal-E1"

    async def test_a_date_alone_is_all_day(self, helper):
        helper.replies["event_add"] = event_reply(title="Biscuit's vet day", start="2026-10-12T05:00:00Z",
                                                  end="2026-10-13T04:59:59Z", all_day=True)
        out = await run(OWNER, action="event_add", what="Biscuit's vet day", start="2026-10-12")
        payload = helper.calls[0][1]
        assert payload["all_day"] is True and payload["date"] == "2026-10-12" and payload["end_date"] == "2026-10-12"
        assert "start" not in payload
        assert "Mon Oct 12 (all day)" in out

    async def test_a_multi_day_all_day_event(self, helper):
        helper.replies["event_add"] = event_reply(start="2026-10-12T05:00:00Z", end="2026-10-15T04:59:59Z",
                                                  all_day=True)
        out = await run(OWNER, action="event_add", what="Lake trip", start="2026-10-12", end="2026-10-14")
        assert helper.calls[0][1]["end_date"] == "2026-10-14"
        assert "Mon Oct 12 – Wed Oct 14 (all day)" in out

    async def test_an_all_day_end_is_its_last_day(self, helper):
        """``end`` for an all-day event is the inclusive last day (the
        docstring says so), and the reply's span comes from what was asked,
        not the helper's in-memory end — midnight of the last day, which
        read as one day short (review 2026-10-11)."""
        assert "end = last day" in S.schedule.description
        helper.replies["event_add"] = event_reply(start="2026-10-12T05:00:00Z", end="2026-10-14T05:00:00Z",
                                                  all_day=True)
        out = await run(OWNER, action="event_add", what="Lake trip", start="2026-10-12", end="2026-10-14")
        assert helper.calls[0][1]["end_date"] == "2026-10-14"
        assert "Mon Oct 12 – Wed Oct 14 (all day)" in out
        helper.replies["event_add"] = event_reply(start="2026-10-12T05:00:00Z", end="2026-10-12T05:00:00Z",
                                                  all_day=True)
        out = await run(OWNER, action="event_add", what="Vet day", start="2026-10-12")
        assert "Mon Oct 12 (all day)" in out and "–" not in out.split(".")[0].split(":", 1)[1]

    async def test_calendar_location_notes_and_invitees_go_along(self, helper):
        helper.replies["event_add"] = {**event_reply(), "attendees_saved": ["a@b.com"],
                                       "attendees_failed": [{"email": "c@d.com", "reason": "no invitees here"}]}
        out = await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00",
                        end="2026-10-12T09:30:00", calendar="Work", location="Main St", notes="bring the form",
                        attendees=["a@b.com", "c@d.com"])
        payload = helper.calls[0][1]
        assert payload["calendar"] == "Work" and payload["location"] == "Main St"
        assert payload["notes"] == "bring the form" and payload["attendees"] == ["a@b.com", "c@d.com"]
        assert payload["end"] == "2026-10-12T14:30:00Z"
        assert "Couldn't invite c@d.com (no invitees here) — want me to email them the details?" in out

    async def test_invitees_are_said_as_far_as_the_helper_can_see(self, helper):
        """A saved invitee is on the event Calendar kept; whether its server
        then sent the invitation the helper can't see, so the reply never
        says "Invited" (review 2026-10-11)."""
        helper.replies["event_add"] = {
            **event_reply(), "attendees_saved": ["a@b.com", "g@h.com"], "attendees_unverified": ["e@f.com"],
            "attendees_failed": [{"email": "c@d.com", "reason": "Calendar didn't keep the invitee"}]}
        out = await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00",
                        attendees=["a@b.com", "g@h.com", "e@f.com", "c@d.com"])
        assert "Invited" not in out
        assert ("Added a@b.com, g@h.com as invitees — Calendar's server sends the invitation and I can't see "
                "whether it did; want me to email them the details too?") in out
        assert ("Added e@f.com as an invitee but couldn't read the event back to check Calendar kept them — "
                "want me to email them the details?") in out
        assert "Couldn't invite c@d.com (Calendar didn't keep the invitee) — want me to email them the details?" in out

    async def test_bad_input_never_reaches_the_helper(self, helper):
        with pytest.raises(ToolFailure, match="what"):
            await run(OWNER, action="event_add", start="2026-10-12T09:00:00")
        with pytest.raises(ToolFailure, match="start"):
            await run(OWNER, action="event_add", what="Dentist")
        with pytest.raises(ToolFailure, match="after start"):
            await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00", end="2026-10-12T08:00:00")
        with pytest.raises(ToolFailure, match="ISO"):
            await run(OWNER, action="event_add", what="Dentist", start="next tuesday")
        with pytest.raises(ToolFailure, match="email"):
            await run(OWNER, action="event_add", what="Dentist", start="2026-10-12", attendees=["Parker"])
        assert helper.calls == []

    async def test_a_past_start_is_allowed(self, helper):
        helper.replies["event_add"] = event_reply()
        await run(OWNER, action="event_add", what="Logged walk", start="2020-01-01T09:00:00")
        assert helper.calls

    async def test_the_helpers_words_come_through(self, helper):
        helper.replies["event_add"] = ek.EventKitFailed("read_only", "“Holidays” is read-only — the ones I can "
                                                                     "write to: Home, Work")
        with pytest.raises(ToolFailure, match="Holidays.*read-only"):
            await run(OWNER, action="event_add", what="x", start="2026-10-12", calendar="Holidays")
        helper.replies["event_add"] = ek.EventKitDenied("event", "writeOnly")
        with pytest.raises(ToolFailure, match="Full Access"):
            await run(OWNER, action="event_add", what="x", start="2026-10-12", calendar="Work")
        helper.replies["event_add"] = ek.EventKitUnavailable("the EventKit helper isn't installed")
        with pytest.raises(ToolFailure, match="isn't installed"):
            await run(OWNER, action="event_add", what="x", start="2026-10-12")

    async def test_an_unknown_outcome_says_read_before_trying_again(self, helper):
        """A timeout or a crash after the save may have added it: saying it
        didn't happen invites a retry and a duplicate (review 2026-10-11)."""
        helper.replies["event_add"] = ek.EventKitOutcomeUnknown("the EventKit helper took longer than 20s")
        with pytest.raises(ToolFailure) as exc:
            await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00")
        text = str(exc.value)
        assert "may or may not have gone through" in text and "took longer than 20s" in text
        assert "schedule calendar" in text and "before trying again" in text
        assert "right now" not in text
        helper.replies["reminder_add"] = ek.EventKitOutcomeUnknown("the EventKit helper gave no answer (exit -9)")
        with pytest.raises(ToolFailure, match="may or may not have gone through.*schedule reminders"):
            await run(OWNER, action="reminder_add", what="Call the vet")

    async def test_a_helper_that_never_started_says_nothing_changed(self, helper):
        helper.replies["event_delete"] = ek.EventKitUnavailable("the EventKit helper wouldn't start (boom)")
        with pytest.raises(ToolFailure, match="I can't change Ellis's calendar right now .*wouldn't start"):
            await run(OWNER, action="event_delete", id="cal-ABCD")


class TestNoDoubleAdds:
    """The same add twice in ten minutes goes in once (send_message's
    dedupe, for Ellis's calendar: an event was duplicated on 2026-09-14)."""

    async def test_the_same_event_twice_is_added_once(self, helper):
        helper.replies["event_add"] = event_reply()
        first = await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00")
        again = await run(OWNER, action="event_add", what="  dentist ", start="2026-10-12T09:00:00")
        assert len(helper.calls) == 1
        assert first.startswith("Added “Dentist”")
        assert re.match(r"Already added “Dentist” to Ellis's calendar at \d{1,2}:\d\d [ap]m — not adding it "
                        r"twice\. id=cal-E1$", again), again

    async def test_another_time_or_calendar_is_another_event(self, helper):
        helper.replies["event_add"] = event_reply()
        await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00")
        await run(OWNER, action="event_add", what="Dentist", start="2026-10-13T09:00:00")
        await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00", calendar="Work")
        assert len(helper.calls) == 3

    async def test_after_an_unknown_outcome_a_retry_still_goes_through(self, helper):
        helper.replies["event_add"] = ek.EventKitOutcomeUnknown("the EventKit helper took longer than 20s")
        with pytest.raises(ToolFailure):
            await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00")
        helper.replies["event_add"] = event_reply()
        out = await run(OWNER, action="event_add", what="Dentist", start="2026-10-12T09:00:00")
        assert out.startswith("Added") and len(helper.calls) == 2

    async def test_the_same_reminder_twice_is_added_once(self, helper):
        helper.replies["reminder_add"] = {"ok": True, "reminder": {"id": "R1", "title": "Call the vet",
                                                                   "list": "Pets", "due": None}}
        await run(OWNER, action="reminder_add", what="Call the vet", calendar="Pets")
        again = await run(OWNER, action="reminder_add", what="Call the vet", calendar="pets")
        assert len(helper.calls) == 1
        assert re.match(r"Already added the reminder “Call the vet” at \d{1,2}:\d\d [ap]m — not adding it "
                        r"twice\. id=rem-R1$", again), again
        await run(OWNER, action="reminder_add", what="Call the vet", calendar="Pets", when="2026-10-12")
        assert len(helper.calls) == 2


class TestEventUpdateAndDelete:
    @pytest.fixture
    def hint(self, monkeypatch):
        from services import calendar_service as cs

        seen = []

        async def event_by_id(uuid, cd=None):
            seen.append((uuid, cd))
            return cs.CalEvent(id=f"cal-{uuid}-813456000", title="Dentist",
                               start=datetime(2026, 10, 12, 9, tzinfo=CHI), end=datetime(2026, 10, 12, 10, tzinfo=CHI),
                               all_day=False, calendar="Home")

        monkeypatch.setattr(cs, "event_by_id", event_by_id)
        return seen

    async def test_the_id_and_its_occurrence_parse_and_hint(self, helper, hint):
        helper.replies["event_update"] = {**event_reply(start="2026-10-12T15:00:00Z", end="2026-10-12T16:00:00Z"),
                                          "before": event_reply()["event"], "changed": ["time"]}
        out = await run(OWNER, action="event_update", id="cal-ABCD-1234-813456000", start="2026-10-12T10:00:00")
        assert hint == [("ABCD-1234", 813456000)]
        payload = helper.calls[0][1]
        assert payload["id"] == "ABCD-1234"
        assert payload["occurrence"] == "2026-10-12T14:00:00Z" and payload["hint_title"] == "Dentist"
        assert payload["start"] == "2026-10-12T15:00:00Z" and "end" not in payload  # the helper keeps the length
        assert payload["span"] == "this" and payload["timezone"] == "America/Chicago"
        assert out.startswith("Moved “Dentist” to Mon Oct 12, 10:00–11:00 am.")

    async def test_an_id_without_an_occurrence(self, helper, hint):
        helper.replies["event_update"] = {**event_reply(title="Dentist (new)"), "before": event_reply()["event"],
                                          "changed": ["title"]}
        out = await run(OWNER, action="event_update", id="cal-ABCD", what="Dentist (new)")
        assert hint == [("ABCD", None)]
        assert helper.calls[0][1]["title"] == "Dentist (new)"
        assert out.startswith("Changed “Dentist” (title)")

    async def test_a_uuid_whose_last_group_is_digits_is_not_an_occurrence(self, helper, hint):
        helper.replies["event_delete"] = event_reply()
        await run(OWNER, action="event_delete", id="cal-8F3A2B1C-0000-4000-8000-123456789012")
        assert hint == [("8F3A2B1C-0000-4000-8000-123456789012", None)]

    async def test_a_date_start_makes_it_all_day(self, helper, hint):
        helper.replies["event_update"] = {**event_reply(all_day=True, start="2026-10-13T05:00:00Z",
                                                        end="2026-10-14T04:59:59Z"),
                                          "before": event_reply()["event"], "changed": ["time"]}
        await run(OWNER, action="event_update", id="cal-ABCD-813456000", start="2026-10-13")
        payload = helper.calls[0][1]
        assert payload["all_day"] is True and payload["date"] == "2026-10-13" and "start" not in payload

    async def test_moving_to_days_names_the_days_asked_for(self, helper, hint):
        helper.replies["event_update"] = {**event_reply(all_day=True, start="2026-10-13T05:00:00Z",
                                                        end="2026-10-15T05:00:00Z"),
                                          "before": event_reply()["event"], "changed": ["time"]}
        out = await run(OWNER, action="event_update", id="cal-ABCD-813456000", start="2026-10-13", end="2026-10-15")
        assert out.startswith("Moved “Dentist” to Tue Oct 13 – Thu Oct 15 (all day).")

    async def test_nothing_to_change_is_refused(self, helper, hint):
        with pytest.raises(ToolFailure, match="what to change"):
            await run(OWNER, action="event_update", id="cal-ABCD-813456000")
        with pytest.raises(ToolFailure, match="id"):
            await run(OWNER, action="event_update", what="x")
        assert helper.calls == []

    async def test_delete(self, helper, hint):
        helper.replies["event_delete"] = event_reply()
        out = await run(OWNER, action="event_delete", id="cal-ABCD-813456000")
        payload = helper.calls[0][1]
        assert payload == {"id": "ABCD", "occurrence": "2026-10-12T14:00:00Z", "hint_title": "Dentist",
                           "span": "this", "timezone": "America/Chicago"}
        assert out == "Deleted “Dentist” (Mon Oct 12 9:00 am) from Ellis's calendar."

    async def test_without_a_readable_calendar_the_occurrence_comes_from_the_id(self, helper, monkeypatch):
        from services import calendar_service as cs

        async def unreadable(uuid, cd=None):
            raise cs.CalendarUnavailable("off")

        monkeypatch.setattr(cs, "event_by_id", unreadable)
        helper.replies["event_delete"] = event_reply()
        await run(OWNER, action="event_delete", id="cal-ABCD-813456000")
        payload = helper.calls[0][1]
        assert payload["occurrence"] == "2026-10-12T00:00:00Z" and "hint_title" not in payload


class TestRepeatingEvents:
    """``span=future`` reaches that repeat and every one after it ("cancel my
    weekly gym class", "move standup to 10 from now on"); the default is that
    one only, and the reply says it repeats (review 2026-10-11)."""

    def test_the_docstring_teaches_it(self):
        assert "span=future" in S.schedule.description

    async def test_future_moves_that_repeat_and_the_ones_after_it(self, helper, no_hints):
        standup = event_reply(title="Standup", start="2026-10-12T15:00:00Z", end="2026-10-12T15:15:00Z",
                              recurring=True)
        helper.replies["event_update"] = {**standup, "before": {"title": "Standup"}, "changed": ["time"]}
        out = await run(OWNER, action="event_update", id="cal-ABCD-813456000", start="2026-10-12T10:00:00",
                        span="future")
        assert helper.calls[0][1]["span"] == "future"
        assert out.startswith("Moved “Standup” to Mon Oct 12, 10:00–10:15 am, and every repeat after it.")

    async def test_future_deletes_that_repeat_and_the_ones_after_it(self, helper, no_hints):
        helper.replies["event_delete"] = event_reply(title="Gym", start="2026-10-12T23:00:00Z", recurring=True)
        out = await run(OWNER, action="event_delete", id="cal-ABCD-813456000", span=" Future ")
        assert helper.calls[0][1]["span"] == "future"
        assert out == "Deleted “Gym” (Mon Oct 12 6:00 pm) and every repeat after it from Ellis's calendar."

    async def test_by_default_only_that_one_and_the_reply_says_it_repeats(self, helper, no_hints):
        helper.replies["event_delete"] = event_reply(title="Gym", start="2026-10-12T23:00:00Z", recurring=True)
        out = await run(OWNER, action="event_delete", id="cal-ABCD-813456000")
        assert helper.calls[0][1]["span"] == "this"
        assert out == ("Deleted “Gym” (Mon Oct 12 6:00 pm) from Ellis's calendar. Only that one — it repeats; "
                       "span=future reaches the ones after it too.")
        helper.replies["event_update"] = {**event_reply(title="Gym", recurring=True), "before": {"title": "Gym"},
                                          "changed": ["location"]}
        out = await run(OWNER, action="event_update", id="cal-ABCD-813456000", location="New gym")
        assert out.startswith("Changed “Gym” (location). Only that one — it repeats; span=future reaches the ones "
                              "after it too.")

    async def test_a_one_off_says_nothing_about_repeats(self, helper, no_hints):
        helper.replies["event_delete"] = event_reply(recurring=False)
        out = await run(OWNER, action="event_delete", id="cal-ABCD-813456000", span="future")
        assert "repeat" not in out

    async def test_a_bad_span_never_reaches_the_helper(self, helper, no_hints):
        with pytest.raises(ToolFailure, match="span is this .* or future"):
            await run(OWNER, action="event_delete", id="cal-ABCD-813456000", span="all")
        assert helper.calls == []


# --------------------------------------------------------------- reminders --

class TestReminderWrites:
    async def test_a_timed_reminder_has_a_due_time(self, helper):
        helper.replies["reminder_add"] = {"ok": True, "reminder": {"id": "R1", "title": "Call the vet", "list": "Pets",
                                                                   "due": "2026-10-12T22:00:00Z"}}
        out = await run(OWNER, action="reminder_add", what="Call the vet", when="2026-10-12T17:00:00", calendar="Pets",
                        notes="ask about the dose")
        payload = helper.calls[0][1]
        assert payload == {"title": "Call the vet", "timezone": "America/Chicago", "list": "Pets",
                           "notes": "ask about the dose", "due": "2026-10-12T22:00:00Z"}
        assert out == "Added the reminder “Call the vet” to Pets, due Mon Oct 12 5:00 pm. id=rem-R1"

    async def test_a_date_alone_is_an_all_day_due_date(self, helper):
        helper.replies["reminder_add"] = {"ok": True, "reminder": {"id": "R2", "title": "Renew tags",
                                                                   "list": "Reminders", "due": "2026-10-12"}}
        out = await run(OWNER, action="reminder_add", what="Renew tags", when="2026-10-12")
        payload = helper.calls[0][1]
        assert payload["due_date"] == "2026-10-12" and "due" not in payload and "list" not in payload
        assert out == "Added the reminder “Renew tags” to Reminders, due Mon Oct 12. id=rem-R2"

    async def test_no_due_date(self, helper):
        helper.replies["reminder_add"] = {"ok": True, "reminder": {"id": "R3", "title": "Someday", "list": "Reminders",
                                                                   "due": None}}
        out = await run(OWNER, action="reminder_add", what="Someday")
        assert out == "Added the reminder “Someday” to Reminders. id=rem-R3"
        with pytest.raises(ToolFailure, match="what"):
            await run(OWNER, action="reminder_add")

    async def test_complete_and_delete_pass_every_hint(self, helper, monkeypatch):
        from services import reminders_service as rs

        async def by_id(ck):
            return rs.Reminder(id="CK1", title="Call the vet", notes="", list="Pets", due=None, all_day=False,
                               flagged=False, priority=0, hints={"ids": ["CK1", "DA1", "UUID1"],
                                                                 "title": "Call the vet", "list": "Pets"})

        monkeypatch.setattr(rs, "reminder_by_id", by_id)
        helper.replies["reminder_complete"] = {"ok": True, "reminder": {"title": "Call the vet", "list": "Pets"}}
        helper.replies["reminder_delete"] = {"ok": True, "reminder": {"title": "Call the vet", "list": "Pets"}}
        assert await run(OWNER, action="reminder_complete", id="rem-CK1") == "Checked off “Call the vet”."
        assert await run(OWNER, action="reminder_delete", id="rem-UUID1") == "Deleted the reminder “Call the vet”."
        assert helper.calls[0] == ("reminder_complete", {"ids": ["CK1", "DA1", "UUID1"], "title": "Call the vet",
                                                         "list": "Pets"})
        assert helper.calls[1][1]["ids"] == ["UUID1", "CK1", "DA1"]
        with pytest.raises(ToolFailure, match="id"):
            await run(OWNER, action="reminder_complete")


# ------------------------------------------------------------------- reads --

class TestReads:
    @pytest.fixture
    def frozen(self, monkeypatch):
        now = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)  # 10:00 am in Springfield
        monkeypatch.setattr(S, "_now", lambda: now)
        return now

    async def test_reminders_read_in_any_turn(self, frozen, monkeypatch):
        from services import reminders_service as rs

        seen = {}

        async def open_reminders(list_name=None, query=None, limit=60):
            seen.update(list_name=list_name, query=query)
            return [
                rs.Reminder(id="CK1", title="Call the vet", notes="", list="Pets",
                            due=datetime(2026, 10, 12, 17, tzinfo=CHI), all_day=False, flagged=True, priority=0),
                rs.Reminder(id="CK2", title="Renew tags", notes="", list="Pets",
                            due=datetime(2026, 10, 13, tzinfo=CHI), all_day=True, flagged=False, priority=0),
                rs.Reminder(id="CK3", title="Someday", notes="", list="Reminders", due=None, all_day=False,
                            flagged=False, priority=0),
                rs.Reminder(id="CK4", title="Vaccines", notes="", list="Pets",
                            due=datetime(2026, 10, 19, tzinfo=CHI), all_day=True, flagged=False, priority=0),
            ]

        monkeypatch.setattr(rs, "open_reminders", open_reminders)
        for policy in (OWNER, HUB, FRIEND, MIND, PULSE):
            out = await run(policy, action="reminders", calendar="Pets", query="v")
            assert "- Call the vet · due today 5:00 pm · flagged · [Pets] id=rem-CK1" in out
            assert "- Renew tags · due tomorrow (all day) · [Pets] id=rem-CK2" in out
            assert "- Someday · no due date · [Reminders] id=rem-CK3" in out
            assert "- Vaccines · due Mon Oct 19 (all day) · [Pets] id=rem-CK4" in out
        assert seen == {"list_name": "Pets", "query": "v"}

    async def test_reminders_unreadable_is_said(self, monkeypatch):
        from services import reminders_service as rs

        async def broken(**kw):
            raise rs.RemindersUnavailable("schema changed")

        monkeypatch.setattr(rs, "open_reminders", broken)
        with pytest.raises(ToolFailure, match="Ellis's Reminders can't be read right now.*schema changed"):
            await run(MIND, action="reminders")

    async def test_no_open_reminders(self, monkeypatch):
        from services import reminders_service as rs

        async def empty(**kw):
            return []

        async def lists():
            return ["Pets", "Reminders"]

        monkeypatch.setattr(rs, "open_reminders", empty)
        monkeypatch.setattr(rs, "list_names", lists)
        assert await run(OWNER, action="reminders", calendar="Pets") == "No open reminders in Pets."
        assert await run(OWNER, action="reminders", calendar="pet") == "No open reminders in pet."
        assert await run(OWNER, action="reminders") == "No open reminders."

    async def test_a_list_that_doesnt_exist_is_said(self, monkeypatch):
        """Never "No open reminders in Groceries" for a list Ellis doesn't have
        (a typo, or Groceries vs Shopping; review 2026-10-11)."""
        from services import reminders_service as rs

        async def empty(**kw):
            return []

        async def lists():
            return ["Pets", "Reminders", "Shopping"]

        monkeypatch.setattr(rs, "open_reminders", empty)
        monkeypatch.setattr(rs, "list_names", lists)
        with pytest.raises(ToolFailure) as exc:
            await run(OWNER, action="reminders", calendar="Groceries")
        assert str(exc.value) == "Ellis has no Reminders list called “Groceries” — their lists: Pets, Reminders, Shopping"

    async def test_the_calendar_window_starts_at_owners_midnight(self, monkeypatch):
        """03:00 UTC is 10 pm the evening before in Springfield: today's window
        starts at Ellis's midnight, not UTC's (it used to miss his evening)."""
        from services import calendar_service as cs

        monkeypatch.setattr(S, "_now", lambda: datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc))
        seen = {}

        async def events_between(start, end, query=None):
            seen.update(start=start, end=end)
            return []

        monkeypatch.setattr(cs, "events_between", events_between)
        await run(MIND, action="calendar")
        assert seen["start"] == datetime(2026, 10, 11, tzinfo=CHI)
        assert seen["end"] - seen["start"] >= timedelta(days=7)

    async def test_every_event_line_carries_its_id(self, monkeypatch):
        from services import calendar_service as cs

        async def events_between(start, end, query=None):
            return [cs.CalEvent(id="cal-U1-813456000", title="Dentist", start=datetime(2026, 10, 12, 9, tzinfo=CHI),
                                end=datetime(2026, 10, 12, 10, tzinfo=CHI), all_day=False, calendar="Home")]

        monkeypatch.setattr(cs, "events_between", events_between)
        out = await run(FRIEND, action="calendar")
        assert "Dentist" in out and "id=cal-U1-813456000" in out


# ------------------------------------------------------------ the shared bits --

def test_egress_names_what_leaves_for_icloud_and_invitees():
    from services.secret_scan import egress_args

    assert egress_args("schedule", {"action": "event_add"}) == ("what", "location", "notes", "attendees")
    assert egress_args("schedule", {"action": "event_update"}) == ("what", "location", "notes", "attendees")
    assert egress_args("schedule", {"action": "reminder_add"}) == ("what", "notes")
    assert egress_args("schedule", {"action": "event_delete"}) == ()
    assert egress_args("schedule", {"action": "calendar"}) == ()
    assert egress_args("schedule", {"action": "add"}) == ()


def test_the_work_rail_summary_names_each_write():
    from services.graph.streaming import _summarize_tool_args as summary

    assert summary("schedule", {"action": "event_add", "what": "Dentist"}) == "event_add: Dentist"
    # Never the raw id on the rail ("Changed cal-6F2A…-781234567 on your
    # calendar"): without a new title it reads "Changed an event".
    assert summary("schedule", {"action": "event_update", "id": "cal-U1", "start": "x"}) == "event_update"
    assert summary("schedule", {"action": "event_update", "id": "cal-U1", "what": "Dentist"}) == \
        "event_update: Dentist"
    assert summary("schedule", {"action": "reminder_add", "what": "Call the vet"}) == "reminder_add: Call the vet"
    assert summary("schedule", {"action": "reminders", "query": "vet"}) == "reminders: vet"
    assert summary("schedule", {"action": "reminder_complete", "id": "rem-1"}) == "reminder_complete: rem-1"


def test_the_manual_says_it_changes_them_when_owner_asks():
    from services.toolset.manual import TOOL_MANUAL

    assert ("- schedule — remind, run a task later, wake yourself (kind=wake); list, cancel; read Ellis's calendar and "
            "Reminders, and change them when they ask.") in TOOL_MANUAL


class TestTheJournal:
    def line(self, args, result=""):
        from services.thought_journal import describe_action

        return describe_action({"name": "schedule", "args": args, "ok": True, "result": result})

    def test_event_sentences(self):
        assert self.line({"action": "event_add", "what": "Dentist", "start": "2026-10-12T09:00:00"}) == \
            "Added “Dentist” to your calendar for Mon 9:00am"
        assert self.line({"action": "event_add", "what": "Vet day", "start": "2026-10-12"}) == \
            "Added “Vet day” to your calendar for Mon Oct 12"
        assert self.line({"action": "event_update", "id": "cal-U1", "start": "2026-10-12T10:00:00"},
                         "Moved “Dentist” to Mon Oct 12, 10:00–11:00 am.") == \
            "Changed “Dentist” on your calendar (now Mon 10:00am)"
        assert self.line({"action": "event_delete", "id": "cal-U1"},
                         "Deleted “Dentist” (Mon Oct 12 9:00 am) from Ellis's calendar.") == \
            "Deleted an event from your calendar: “Dentist”"

    def test_a_repeating_event_changed_from_here_on_says_so(self):
        assert self.line({"action": "event_delete", "id": "cal-U1", "span": "future"},
                         "Deleted “Gym” (Mon Oct 12 6:00 pm) and every repeat after it from Ellis's calendar.") == \
            "Deleted an event and its later repeats from your calendar: “Gym”"
        assert self.line({"action": "event_update", "id": "cal-U1", "start": "2026-10-12T10:00:00", "span": "future"},
                         "Moved “Standup” to Mon Oct 12, 10:00–10:15 am, and every repeat after it.") == \
            "Changed “Standup” and its later repeats on your calendar (now Mon 10:00am)"

    def test_reminder_sentences(self):
        assert self.line({"action": "reminder_add", "what": "Call the vet", "when": "2026-10-12T17:00:00"}) == \
            "Added a reminder: Call the vet (due Mon 5:00pm)"
        assert self.line({"action": "reminder_complete", "id": "rem-1"}, "Checked off “Call the vet”.") == \
            "Checked off a reminder: “Call the vet”"
        assert self.line({"action": "reminder_delete", "id": "rem-1"}) == "Deleted a reminder"


def test_there_is_a_write_for_each_and_reads_stay_reads():
    assert set(S.WRITE_ACTIONS) == set(WRITES)
    assert set(S.SCHEDULE_ACTIONS) == {"add", "list", "cancel", "calendar", "reminders", *WRITES}
    assert date(2026, 10, 12)  # keeps the import honest


class TestHintsFromCalendarsDatabase:
    """``calendar_service.event_by_id`` re-reads the event a ``cal-`` id
    names (read-only), so the helper gets its title and occurrence."""

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        from services import calendar_service as cs
        from tests.test_calendar_service import build_db

        path = tmp_path / "Calendar.sqlitedb"
        monkeypatch.setenv("EDWARD_CALENDAR_DB", str(path))
        monkeypatch.setenv("EDWARD_CALENDAR_SQLITE", "on")
        monkeypatch.setattr(cs, "_user_tz", lambda: CHI)
        monkeypatch.setattr(cs, "_now", lambda: datetime(2026, 10, 12, 8, tzinfo=CHI))
        build_db(path, [
            {"summary": "Dentist", "start": datetime(2026, 10, 12, 9, tzinfo=CHI),
             "end": datetime(2026, 10, 12, 10, tzinfo=CHI), "calendar": "Home"},
        ])
        return cs

    async def test_by_uuid_and_occurrence(self, db):
        ev = await db.event_by_id("uuid-Dentist")
        assert ev is not None and ev.title == "Dentist" and ev.start == datetime(2026, 10, 12, 9, tzinfo=CHI)
        cd = int(ev.id.rsplit("-", 1)[1])
        again = await db.event_by_id("uuid-Dentist", cd)
        assert again is not None and again.id == ev.id
        assert await db.event_by_id("uuid-Dentist", cd + 60) is None
        assert await db.event_by_id("uuid-nope") is None

    async def test_unreadable_raises(self, db, monkeypatch):
        monkeypatch.setenv("EDWARD_CALENDAR_SQLITE", "off")
        with pytest.raises(db.CalendarUnavailable):
            await db.event_by_id("uuid-Dentist")
