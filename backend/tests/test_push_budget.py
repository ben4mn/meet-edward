"""Quiet hours held in code, and the push budget only when one is set
(proactivity-spec §4.1, 2026-10-11; Ellis's answers #2 the same morning).

Why: on the worst day before the rebuild Ellis's phone rang from four code
paths in two hours, and quiet hours were held only for the templated
senders. Ellis's answer: "push, with no limit" — there is no daily push count
by default (``EDWARD_PUSH_BUDGET`` unset), but quiet hours (22:00–07:00) hold
for Edward's own turns except what cannot wait, and the offer grammar and
one follow-up stay. ``EDWARD_PUSH_BUDGET=<n>`` still caps the counted pushes
of a local day when someone sets it; past it an urgent reach is a note with
the reason. Ellis's asks, ping-failure notices and care alerts are never
counted — a late dose at 06:45 must still ring.
"""

from datetime import datetime, timezone

import pytest

from services import reach_service
from tests.test_reach_service import GOOD, FakePush

pytestmark = pytest.mark.integration

DAY = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)        # 15:00 Springfield
NIGHT = datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc)      # 23:30 Springfield
DAWN = datetime(2026, 10, 11, 11, 45, tzinfo=timezone.utc)      # 06:45 Springfield


@pytest.fixture
def push(monkeypatch):
    from services import push_service, timezone_utils

    fake = FakePush()
    monkeypatch.setattr(push_service, "send_push_notification", fake)
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setattr(reach_service, "_now", lambda: DAY)
    monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    return fake


def _offer(i: int) -> str:
    return f"BA's 9:3{i} pm text says gate {i} changed. Want me to check it? (not done)"


async def _self_push(i: int) -> str:
    return await reach_service.tell_owner(_offer(i), title=f"Gate {i}", urgent=True, origin="self")


async def test_by_default_there_is_no_daily_push_cap(clean_db, push):
    for i in range(1, 6):
        assert (await _self_push(i)).startswith("Pushed"), i
    assert len(push.calls) == 5
    assert reach_service.push_budget() is None


@pytest.mark.parametrize("value", ["", "unlimited", "none", "off", "lots"])
def test_unset_or_unlimited_means_no_cap(monkeypatch, value):
    monkeypatch.setenv("EDWARD_PUSH_BUDGET", value)
    assert reach_service.push_budget() is None


async def test_the_third_counted_push_becomes_a_note_with_the_reason(clean_db, push, monkeypatch):
    monkeypatch.setenv("EDWARD_PUSH_BUDGET", "2")
    assert (await _self_push(1)).startswith("Pushed")
    assert (await _self_push(2)).startswith("Pushed")
    out = await _self_push(3)
    assert out.startswith("Left it as a note — 2 of 2 pushes used today.")
    assert len(push.calls) == 2


async def test_speak_and_fallback_count_too(clean_db, push, monkeypatch):
    monkeypatch.setenv("EDWARD_PUSH_BUDGET", "2")
    await reach_service.tell_owner(_offer(1), title="a", urgent=True, origin="fallback")
    with reach_service.speak_scope(noticing_id=None, channel="push", floor_push=False, who=("BA",),
                                   sensitive=False, dedupe_key="g2"):
        await reach_service.tell_owner(_offer(2), title="b", urgent=True)
    out = await _self_push(3)
    assert out.startswith("Left it as a note")


async def test_care_ping_and_owner_pushes_are_not_counted(clean_db, push):
    await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", urgent=True, origin="care", dedupe_key="c1")
    await reach_service.tell_owner("Biscuit's 6:00 am dose isn't logged", urgent=True, origin="care", dedupe_key="c2")
    await reach_service.tell_owner("I couldn't answer your ping in Trivia crew", urgent=True, origin="ping:owner")
    await reach_service.tell_owner("Quinn pinged me and I couldn't answer", urgent=True, origin="ping:other")
    await reach_service.tell_owner("call mom", urgent=True, origin="owner")
    assert await reach_service.autonomous_pushes_today() == 0
    assert (await _self_push(1)).startswith("Pushed")
    assert (await _self_push(2)).startswith("Pushed")
    assert await reach_service.autonomous_pushes_today() == 2


async def test_care_still_pushes_past_the_budget(clean_db, push, monkeypatch):
    monkeypatch.setenv("EDWARD_PUSH_BUDGET", "2")
    await _self_push(1)
    await _self_push(2)
    out = await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", urgent=True, origin="care")
    assert out.startswith("Pushed")


async def test_quiet_hours_turn_an_urgent_self_reach_into_a_note(clean_db, push, monkeypatch):
    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    out = await _self_push(1)
    assert out.startswith("Left it as a note") and "quiet hours" in out
    assert push.calls == []


async def test_owners_own_ping_notice_at_2330_still_pushes(clean_db, push, monkeypatch):
    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    out = await reach_service.tell_owner("I couldn't answer your ping in Trivia crew", urgent=True, origin="ping:owner")
    assert out.startswith("Pushed")


async def test_a_friends_ping_notice_at_2330_is_a_note(clean_db, push, monkeypatch):
    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    out = await reach_service.tell_owner("Quinn pinged me and I couldn't answer", urgent=True, origin="ping:other")
    assert out.startswith("Left it as a note") and push.calls == []


async def test_a_care_alert_at_0645_pushes(clean_db, push, monkeypatch):
    monkeypatch.setattr(reach_service, "_now", lambda: DAWN)
    out = await reach_service.tell_owner("Biscuit's 6:00 am dose isn't logged", urgent=True, origin="care")
    assert out.startswith("Pushed")


async def test_owner_asked_at_night_pushes(clean_db, push, monkeypatch):
    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    assert (await reach_service.tell_owner("wake me", urgent=True, origin="owner")).startswith("Pushed")


async def test_speak_at_night_trusts_the_gate(clean_db, push, monkeypatch):
    # The gate already applied quiet hours (R11): a scope of push at night is
    # a floor item closing before 07:00.
    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    with reach_service.speak_scope(noticing_id=None, channel="push", floor_push=True, who=("BA",),
                                   sensitive=False, dedupe_key="ba"):
        out = await reach_service.tell_owner(GOOD, title="UA958", urgent=True)
    assert out.startswith("Pushed")


async def test_an_explicit_budget_is_honoured_whatever_its_size(clean_db, push, monkeypatch):
    monkeypatch.setenv("EDWARD_PUSH_BUDGET", "10")
    for i in range(1, 6):
        assert (await _self_push(i)).startswith("Pushed")


async def test_a_zero_budget_stops_every_counted_push(clean_db, push, monkeypatch):
    monkeypatch.setenv("EDWARD_PUSH_BUDGET", "0")
    assert (await _self_push(1)).startswith("Left it as a note")
    assert (await reach_service.tell_owner("Biscuit's dose", urgent=True, origin="care")).startswith("Pushed")


async def test_an_unreadable_count_is_treated_as_spent(clean_db, push, monkeypatch):
    from services import push_ledger

    monkeypatch.setenv("EDWARD_PUSH_BUDGET", "2")

    async def unknown(*a, **k):
        return None

    monkeypatch.setattr(push_ledger, "count_sent_today", unknown)
    out = await _self_push(1)
    assert out.startswith("Left it as a note") and "couldn't count" in out
