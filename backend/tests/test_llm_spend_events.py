"""Whose turn a scheduled event is, for the Claude spend cap (review
2026-10-11).

Ordinary scheduled events run as ``turn_source="scheduled_event"`` turns
with no purpose scope, so the cap counted every one as Ellis's and let it run
past the $10 cap into the $3 he keeps — including follow-ups the mind
scheduled for itself (``schedule_event`` is a mind tool). Spend reached
$10.20 by evening, wakes stopped, two of the mind's follow-ups fired as
full Opus tool loops and spent the reserve, and Ellis's next message was
refused with "past my $10 daily cap and the $3 I keep for you".

Now an event records the source of the turn that made it
(``event_metadata.created_in``), and an event that was not Ellis's runs
inside ``llm_spend.own_work_scope()`` — Edward's own work, stopped at the
cap. Ellis's own events (made through the REST routes, or in his chat,
voice, widget or Alexa turn) keep his reserve; an unstamped event made
before the stamp counts as his unless it lives in one of Edward's own
threads.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services import llm_spend as ls


@contextmanager
def _turn(source):
    from services import turn_ledger

    rec = None if source is None else SimpleNamespace(source=source, id="turn-1", conversation_id="conv-1")
    token = turn_ledger._current.set(rec)
    try:
        yield
    finally:
        turn_ledger._current.reset(token)


def _event(created_by="edward", meta=None, conversation_id="conv-1"):
    return SimpleNamespace(id="evt-1", created_by=created_by, conversation_id=conversation_id,
                           event_metadata=json.dumps(meta) if meta is not None else None,
                           description="Check in on the plumber")


# --------------------------------------------------------------- pure --

@pytest.mark.unit
@pytest.mark.parametrize("event,expected", [
    (_event(created_by="user"), True),                                 # the REST routes (Ellis's UI)
    (_event(created_by="owner"), True),                                  # a goal Ellis set
    (_event(meta={"created_in": "chat"}), True),                       # "remind me at 9" in his chat
    (_event(meta={"created_in": "voice"}), True),
    (_event(meta={"created_in": "alexa"}), True),
    (_event(meta={"created_in": "mind"}), False),                      # the mind's own follow-up
    (_event(meta={"created_in": "pulse"}), False),
    (_event(meta={"created_in": "scheduled_event"}), False),           # an event that made another
    (_event(meta={"created_in": "heartbeat"}), False),
    (_event(conversation_id="edward-mind"), False),                    # unstamped, in Edward's thread
    (_event(conversation_id="edward-surfaces"), False),
    (_event(), True),                                                  # unstamped elsewhere: as before
    (_event(meta={"memory_id": "m-1"}, conversation_id="conv-9"), True),
])
def test_whose_event_it_is(event, expected):
    from services.scheduled_events_service import event_is_owners

    assert event_is_owners(event) is expected


# ------------------------------------------------------- the creator --

@pytest.mark.integration
async def test_an_event_records_the_turn_that_made_it(clean_db):
    from datetime import datetime, timedelta, timezone
    from services.scheduled_events_service import create_event

    at = datetime.now(timezone.utc) + timedelta(days=1)
    with _turn("mind"):
        made_by_mind = await create_event(description="Follow up on the quote", scheduled_at=at,
                                          conversation_id="edward-mind")
    with _turn("chat"):
        made_in_chat = await create_event(description="Remind Ellis about the vet", scheduled_at=at,
                                          conversation_id="conv-1", memory_id=None)
    outside = await create_event(description="No turn at all", scheduled_at=at, conversation_id="conv-2")
    with _turn("chat"):
        kept = await create_event(description="Explicit stamp", scheduled_at=at, conversation_id="conv-3",
                                  event_metadata={"created_in": "mind"})
    assert json.loads(made_by_mind.event_metadata)["created_in"] == "mind"
    assert json.loads(made_in_chat.event_metadata)["created_in"] == "chat"
    assert outside.event_metadata is None
    assert json.loads(kept.event_metadata)["created_in"] == "mind"


# ------------------------------------------------------ the scheduler --

@pytest.fixture
def ran(monkeypatch):
    """What ``_execute_event`` ran an ordinary event under."""
    from services import scheduler_service

    seen = []

    async def _thread(event):
        return "conv-1"

    async def _turn_body(event, conversation_id):
        seen.append(ls.in_own_work())

    monkeypatch.setattr(scheduler_service, "_event_conversation_id", _thread)
    monkeypatch.setattr(scheduler_service, "_execute_event_turn", _turn_body)
    return seen


@pytest.mark.unit
async def test_edwards_own_event_runs_as_his_own_work(ran):
    from services.scheduler_service import _execute_event

    await _execute_event(_event(meta={"created_in": "mind"}))
    assert ran == [True]
    assert not ls.in_own_work()                  # the scope ends with the event


@pytest.mark.unit
async def test_owners_event_keeps_his_reserve(ran):
    from services.scheduler_service import _execute_event

    await _execute_event(_event(created_by="user"))
    await _execute_event(_event(meta={"created_in": "chat"}))
    assert ran == [False, False]
