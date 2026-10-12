"""The policy a fired event's turn runs under (review of AREA 1, 2026-10-11).

``scheduler_service.event_policy`` passes AREA 2's ``TurnPolicy``
explicitly, so it decides whose event it is — not AREA 2's default for
``scheduled_event``. Two findings:

* **Whose event.** It read only ``event_metadata.requested_by_owner``, which
  only AREA 2's ``schedule`` tool stamps. Every reminder Ellis made in the
  Events UI (``created_by="user"``) or in his chat before that stamp —
  every recurring reminder pending today — fired as Edward's own turn: the
  offer grammar on "Time to give Biscuit her meds", quiet hours turning a
  06:30 reminder into a note, a twice-daily reminder refused as a repeat,
  and his own iMessage reminder refused by never-text-Ellis. Whose it is is
  ``scheduled_events_service.event_is_owners`` — the rule the scheduler
  already used for spend.
* **The reach of the turn that made it.** The nightly (no reach, no push)
  or a speak wake the gate held to a note could ``schedule`` a task five
  minutes out, and the task fired with ``may_push=True,
  may_reach_people=True``: "never a push" and "speak can only lower" broken
  by one hop. ``create_event`` now stamps the making turn's reach
  (``event_metadata.reach``) when its policy was set by its caller and Ellis
  was not the one asking, and the event's turn never gets more.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.area1_fakes import install_turn_policy


def _event(created_by="edward", meta=None, conversation_id="conv-1"):
    return SimpleNamespace(id="evt-1", created_by=created_by, conversation_id=conversation_id,
                           event_metadata=json.dumps(meta) if meta is not None else None,
                           description="Give Biscuit her 6:30 am meds")


@pytest.mark.unit
@pytest.mark.parametrize("event,owners", [
    (_event(created_by="user"), True),                          # the Events UI (REST)
    (_event(meta={"created_in": "chat"}), True),                # "remind me at 6:30" in his chat
    (_event(meta={"created_in": "voice"}), True),
    (_event(), True),                                           # an old event outside Edward's threads
    (_event(meta={"requested_by_owner": True, "created_in": "ping"}), True),   # AREA 2's stamp
    (_event(meta={"created_in": "mind"}), False),               # the mind's own follow-up
    (_event(conversation_id="edward-mind"), False),
])
def test_whose_event_decides_owner_asked(monkeypatch, event, owners):
    install_turn_policy(monkeypatch)
    from services import scheduler_service

    policy = scheduler_service.event_policy(event)
    assert policy.owner_asked is owners
    assert policy.source == "scheduled_event"


@pytest.mark.unit
def test_an_unstamped_event_may_reach_people_and_push(monkeypatch):
    install_turn_policy(monkeypatch)
    from services import scheduler_service

    policy = scheduler_service.event_policy(_event(meta={"created_in": "mind"}))
    assert (policy.may_reach_people, policy.may_push) == (True, True)


@pytest.mark.unit
@pytest.mark.parametrize("reach,expected", [
    ({"may_reach_people": False, "may_push": False}, (False, False)),    # made in the nightly
    ({"may_reach_people": False, "may_push": True}, (False, True)),      # a speak wake allowed a push
    ({"may_reach_people": True, "may_push": False}, (True, False)),
    ("garbage", (True, True)),                                           # unreadable → today's behaviour
])
def test_an_event_never_gets_more_reach_than_the_turn_that_made_it(monkeypatch, reach, expected):
    install_turn_policy(monkeypatch)
    from services import scheduler_service

    policy = scheduler_service.event_policy(_event(meta={"created_in": "mind", "reach": reach}))
    assert (policy.may_reach_people, policy.may_push) == expected
    assert policy.owner_asked is False


@pytest.mark.unit
def test_owners_event_is_never_narrowed_by_a_stamp(monkeypatch):
    install_turn_policy(monkeypatch)
    from services import scheduler_service

    policy = scheduler_service.event_policy(
        _event(created_by="user", meta={"reach": {"may_reach_people": False, "may_push": False}}))
    assert (policy.owner_asked, policy.may_reach_people, policy.may_push) == (True, True, True)


@pytest.mark.integration
async def test_create_event_stamps_the_reach_of_a_policy_its_caller_set(clean_db, monkeypatch):
    TurnPolicy = install_turn_policy(monkeypatch)
    from services import scheduled_events_service as ses
    from services.toolset import policy as policy_mod

    at = datetime.now(timezone.utc) + timedelta(hours=4)
    nightly = TurnPolicy(source="mind", owner_asked=False, may_reach_people=False, may_push=False)
    with policy_mod.policy_scope(nightly):
        made_at_night = await ses.create_event(description="Tell Ellis urgently about the quote", scheduled_at=at,
                                               conversation_id="edward-nightly")
    owner = TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
    with policy_mod.policy_scope(owner):
        made_by_owner = await ses.create_event(description="Remind me about the vet", scheduled_at=at,
                                             conversation_id="conv-1")
    no_policy = await ses.create_event(description="No turn at all", scheduled_at=at, conversation_id="conv-2")

    assert json.loads(made_at_night.event_metadata)["reach"] == {"may_reach_people": False, "may_push": False}
    assert "reach" not in json.loads(made_by_owner.event_metadata or "{}")
    assert "reach" not in json.loads(no_policy.event_metadata or "{}")

    from services import scheduler_service

    fired = scheduler_service.event_policy(made_at_night)
    assert (fired.may_reach_people, fired.may_push) == (False, False)


@pytest.mark.integration
async def test_without_the_policy_module_nothing_is_stamped(clean_db, monkeypatch):
    from tests.area1_fakes import hide_turn_policy

    hide_turn_policy(monkeypatch)
    from services import scheduled_events_service as ses

    ev = await ses.create_event(description="Plain", scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                                conversation_id="conv-3")
    assert "reach" not in json.loads(ev.event_metadata or "{}")
