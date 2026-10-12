"""``care_watch`` — the late-dose alert, made model-free (2026-10-11).

The judgement check's ``late_dose`` scenario asked a model to notice that
Biscuit's pill had not been logged. That is too important to ride an LLM, a
flag or the spend cap. A timed hub item still unlogged at due + 45 min
pushes Ellis once — per pet and slot, so the three 6:00 pm doses are one
push — through ``reach_service.tell_owner`` with ``origin="care"`` (exempt
from the push budget and quiet hours: 06:45 is a real dose).

The hub's rules stand: a ``window`` item (the cats' wet food) never goes
overdue, so it never alerts; a ``missed`` dose (> 240 min) stops alerting.
Someone touching the hub in the last 15 minutes sees its own alert, so
Ellis is not pushed. A restart never re-alerts: each alert is a keyed
``care_alert`` ledger row. ``EDWARD_CARE_WATCH=off`` is its own kill switch.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import care_watch
from tests.area1_fakes import fake_module

CHI = ZoneInfo("America/Chicago")
DAY = "2026-10-11"


def _med(name, at="18:00", status="overdue", late=47, review=False, kind="med", item_id=None):
    return {"id": item_id or f"biscuit-{name.lower()}-pm", "name": name, "kind": kind, "at_time": at, "status": status,
            "minutes_late": late if status in ("overdue", "missed") else None, "needs_review": review}


def _board(*meds, pet="Biscuit", pet_id="biscuit"):
    return {"care_day": DAY, "window": "pm",
            "pets": [{"id": pet_id, "name": pet, "feedings": {}, "meds": list(meds)}], "alerts": []}


NOW = datetime(2026, 10, 11, 18, 47, tzinfo=CHI)


# ------------------------------------------------------------------ pure --

@pytest.mark.unit
def test_a_dose_45_minutes_late_alerts_once_with_the_clock_and_the_ask():
    alerts = care_watch.due_alerts(_board(_med("Carprofen")), NOW, set())
    assert len(alerts) == 1
    a = alerts[0]
    assert a.key == f"care:biscuit:{DAY}:18:00"
    assert a.title == "Biscuit's 6:00 pm dose"
    assert a.text == "Biscuit's 6:00 pm Carprofen isn't logged — 47 min late. Given? Log it on the hub."
    assert a.link == "/hub/pets"


@pytest.mark.unit
@pytest.mark.parametrize("med", [
    _med("Carprofen", status="due", late=None),             # inside the grace
    _med("Carprofen", status="overdue", late=40),           # overdue, not yet 45
    _med("Carprofen", status="missed", late=241),           # missed stops alerting (hub rule 2)
    _med("Carprofen", status="given", late=None),           # logged
    _med("Wet food", status="due", late=None, kind="feeding"),
])
def test_nothing_alerts_before_45_after_missed_or_once_logged(med):
    assert care_watch.due_alerts(_board(med), NOW, set()) == []


@pytest.mark.unit
def test_one_slot_is_one_alert_and_an_unconfirmed_schedule_says_so():
    board = _board(_med("Carprofen"), _med("Probiotic", review=True), _med("Melatonin", review=True),
                   _med("Cetirizine", at="06:00", status="missed", late=767))
    alerts = care_watch.due_alerts(board, NOW, set())
    assert len(alerts) == 1
    a = alerts[0]
    assert a.title == "Biscuit's 6:00 pm doses"
    assert a.text.startswith("Biscuit's 6:00 pm Carprofen, Probiotic and Melatonin aren't logged — 47 min late. "
                             "Given? Log them on the hub.")
    assert a.text.endswith("(schedule unconfirmed)")
    assert a.item_ids == ("biscuit-carprofen-pm", "biscuit-probiotic-pm", "biscuit-melatonin-pm")


@pytest.mark.unit
def test_an_alert_already_sent_is_not_sent_again():
    assert care_watch.due_alerts(_board(_med("Carprofen")), NOW, {f"care:biscuit:{DAY}:18:00"}) == []


@pytest.mark.unit
def test_the_morning_dose_reads_am():
    a = care_watch.due_alerts(_board(_med("Cetirizine", at="06:00", late=47)), NOW, set())[0]
    assert a.title == "Biscuit's 6:00 am dose" and "6:00 am Cetirizine" in a.text


# ------------------------------------------------------------ the check --

@pytest.fixture
def reach(monkeypatch):
    """1C's ``reach_service.tell_owner``, recorded."""
    calls = []

    async def tell_owner(message, title=None, urgent=False, link=None, *, origin=None, noticing_id=None,
                       dedupe_key=None):
        calls.append({"message": message, "title": title, "urgent": urgent, "link": link, "origin": origin,
                      "dedupe_key": dedupe_key})
        return "Pushed to Ellis's phone"

    mod = types.ModuleType("services.reach_service")
    mod.tell_owner = tell_owner
    fake_module(monkeypatch, "services.reach_service", mod)
    return calls


@pytest.fixture
def board_at(monkeypatch):
    state = {"board": _board(_med("Carprofen")), "asked_at": []}

    async def get_board(now=None):
        state["asked_at"].append(now)
        return state["board"]

    async def nobody(now=None):
        return {"person": None, "since": None, "last_seen": None, "people": ["Ellis", "Rowan"]}

    monkeypatch.setattr("services.hub_service.get_board", get_board)
    monkeypatch.setattr("services.hub_service.get_hub_person", nobody)
    return state


@pytest.mark.integration
async def test_a_late_dose_pushes_once_as_care_and_a_second_check_does_not(clean_db, reach, board_at, monkeypatch):
    monkeypatch.delenv("EDWARD_CARE_WATCH", raising=False)
    out = await care_watch.check(now=NOW)
    assert out["alerts"] == 1
    assert reach == [{"message": "Biscuit's 6:00 pm Carprofen isn't logged — 47 min late. Given? Log it on the hub.",
                      "title": "Biscuit's 6:00 pm dose", "urgent": True, "link": "/hub/pets", "origin": "care",
                      "dedupe_key": f"care:biscuit:{DAY}:18:00"}]
    # The board is read in the household's zone, never UTC.
    assert board_at["asked_at"][0].utcoffset() == NOW.utcoffset()

    again = await care_watch.check(now=NOW + timedelta(minutes=5))
    assert again["alerts"] == 0 and len(reach) == 1, "a restart or the next sweep never re-alerts"

    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "care_alert"))).scalars().all()
    assert [(r.source, r.outcome, r.ref_id) for r in rows] == [("care_watch", "pushed", f"care:biscuit:{DAY}:18:00")]
    assert json.loads(rows[0].detail_json)["items"] == ["Carprofen"]


@pytest.mark.integration
async def test_quiet_hours_do_not_hold_a_real_dose(clean_db, reach, board_at):
    board_at["board"] = _board(_med("Cetirizine", at="06:00", late=47))
    out = await care_watch.check(now=datetime(2026, 10, 11, 6, 47, tzinfo=CHI))
    assert out["alerts"] == 1 and reach[0]["urgent"] is True and reach[0]["origin"] == "care"


@pytest.mark.integration
async def test_someone_at_the_hub_sees_its_own_alert(clean_db, reach, board_at, monkeypatch):
    async def rowan(now=None):
        seen = (NOW - timedelta(minutes=5)).astimezone(timezone.utc).isoformat()
        return {"person": "Rowan", "since": seen, "last_seen": seen, "people": ["Ellis", "Rowan"]}

    monkeypatch.setattr("services.hub_service.get_hub_person", rowan)
    out = await care_watch.check(now=NOW)
    assert out["alerts"] == 0 and reach == [] and "hub" in out["skipped"]


@pytest.mark.integration
async def test_a_hub_touched_twenty_minutes_ago_does_not_hold_it(clean_db, reach, board_at, monkeypatch):
    async def earlier(now=None):
        seen = (NOW - timedelta(minutes=20)).astimezone(timezone.utc).isoformat()
        return {"person": "Rowan", "since": seen, "last_seen": seen, "people": ["Ellis", "Rowan"]}

    monkeypatch.setattr("services.hub_service.get_hub_person", earlier)
    assert (await care_watch.check(now=NOW))["alerts"] == 1


@pytest.mark.integration
async def test_it_ignores_the_proactivity_flag_and_the_spend_cap(clean_db, reach, board_at, monkeypatch):
    from services import expression_service, llm_spend

    def no_quota_read():
        raise AssertionError("care_watch never asks about the model quota: it makes no model call")

    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    monkeypatch.setattr(llm_spend, "quota_state_cached", no_quota_read)
    monkeypatch.setattr(expression_service, "quota_state", no_quota_read)
    assert (await care_watch.check(now=NOW))["alerts"] == 1


@pytest.mark.integration
async def test_its_own_kill_switch(clean_db, reach, board_at, monkeypatch):
    monkeypatch.setenv("EDWARD_CARE_WATCH", "off")
    out = await care_watch.check(now=NOW)
    assert out == {"enabled": False} and reach == []


@pytest.fixture
def transport(monkeypatch):
    """The real ``reach_service`` over a push transport that fails until
    told otherwise (review 2026-10-11: the old test faked ``tell_owner`` to
    raise, which the real one never does — it leaves a note and returns
    "Left it as a note — the push failed", which care_watch counted as a
    push, so the dose was never alerted again)."""
    state = {"fail": "WebPushException: 503", "pushes": []}

    async def send_push_notification(title, body, url=None, **kw):
        state["pushes"].append({"title": title, "body": body, "url": url, **kw})
        if state["fail"]:
            return {"sent": 0, "failed": 1, "total": 1, "error": state["fail"]}
        return {"sent": 1, "failed": 0, "total": 1, "ledger_id": f"push-{len(state['pushes'])}"}

    monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    monkeypatch.setattr("services.push_service.is_configured", lambda: True)
    monkeypatch.setattr("services.push_service.send_push_notification", send_push_notification)
    return state


async def _notes():
    from sqlalchemy import select

    from services.database import EdwardNoteModel, async_session

    async with async_session() as s:
        return (await s.execute(select(EdwardNoteModel))).scalars().all()


async def _care_rows():
    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "care_alert")
                                .order_by(LedgerEventModel.id))).scalars().all()
    return [r.outcome for r in rows]


@pytest.mark.integration
async def test_a_push_that_failed_is_tried_again_until_it_lands(clean_db, board_at, transport, monkeypatch):
    """The real reach_service: the push fails (503), so Ellis gets a note and
    the dose is *not* counted as pushed — the next sweep tries the push
    again (no second note), and once it lands it is never sent again."""
    from services import reach_service

    monkeypatch.setattr(reach_service, "_now", lambda: NOW.astimezone(timezone.utc))
    first = await care_watch.check(now=NOW)
    assert first["alerts"] == 0 and first["failed"] == 1
    assert len(transport["pushes"]) == 1 and len(await _notes()) == 1, "the push failed: Ellis has a note"

    second = await care_watch.check(now=NOW + timedelta(minutes=5))
    assert len(transport["pushes"]) == 2, "the next sweep tries the push again"
    assert second["alerts"] == 0 and len(await _notes()) == 1, "still failing: no second note"

    transport["fail"] = None
    third = await care_watch.check(now=NOW + timedelta(minutes=10))
    assert third["alerts"] == 1 and len(transport["pushes"]) == 3
    assert transport["pushes"][-1]["source"] == "reach:care"

    fourth = await care_watch.check(now=NOW + timedelta(minutes=15))
    assert fourth["alerts"] == 0 and len(transport["pushes"]) == 3, "pushed once: never again"
    assert await _care_rows() == ["downgraded", "failed", "pushed"]


@pytest.mark.integration
async def test_no_device_is_not_a_push_either(clean_db, board_at, transport, monkeypatch):
    from services import reach_service

    monkeypatch.setattr(reach_service, "_now", lambda: NOW.astimezone(timezone.utc))
    transport["fail"] = None

    async def no_device(title, body, url=None, **kw):
        transport["pushes"].append({"title": title})
        return {"sent": 0, "failed": 0, "total": 0}

    monkeypatch.setattr("services.push_service.send_push_notification", no_device)
    out = await care_watch.check(now=NOW)
    assert out["alerts"] == 0 and await _care_rows() == ["downgraded"]
    again = await care_watch.check(now=NOW + timedelta(minutes=5))
    assert again["alerts"] == 0 and len(transport["pushes"]) == 2


@pytest.mark.integration
async def test_a_raising_transport_is_tried_again_on_the_next_sweep(clean_db, board_at, monkeypatch):
    calls = []

    async def tell_owner(message, title=None, urgent=False, link=None, **kw):
        calls.append(message)
        if len(calls) == 1:
            raise RuntimeError("push service down")
        return "Pushed to Ellis's phone."

    mod = types.ModuleType("services.reach_service")
    mod.tell_owner = tell_owner
    fake_module(monkeypatch, "services.reach_service", mod)
    first = await care_watch.check(now=NOW)
    assert first["alerts"] == 0 and first["failed"] == 1
    second = await care_watch.check(now=NOW + timedelta(minutes=5))
    assert second["alerts"] == 1 and len(calls) == 2


@pytest.mark.integration
async def test_without_reach_service_it_pushes_directly(clean_db, board_at, monkeypatch):
    sent = []

    async def send_push_notification(title, body, url=None, **kw):
        sent.append({"title": title, "body": body, "url": url, **kw})
        return {"sent": 1, "failed": 0, "total": 1}

    fake_module(monkeypatch, "services.reach_service", None)    # import raises ImportError
    monkeypatch.setattr("services.push_service.send_push_notification", send_push_notification)
    assert (await care_watch.check(now=NOW))["alerts"] == 1
    assert sent[0]["source"] == "reach:care" and sent[0]["respect_quiet_hours"] is False
    assert sent[0]["url"] == "/hub/pets" and sent[0]["title"] == "Biscuit's 6:00 pm dose"


@pytest.fixture
async def example_board(clean_db, monkeypatch):
    """The real board, planted from ``hub_seed.example.json`` into an empty
    pet board (clean_db does not truncate pets, care items or care events),
    read on Central time."""
    from sqlalchemy import delete

    from services import timezone_utils
    from services.database import CareEventModel, CareItemModel, PetModel, async_session

    async def wipe():
        async with async_session() as session:
            for model in (CareEventModel, CareItemModel, PetModel):
                await session.execute(delete(model))
            await session.commit()

    await wipe()
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setenv("EDWARD_HUB_SEED", "hub_seed.example.json")
    yield
    await wipe()


@pytest.mark.integration
async def test_the_seeded_hub_alerts_biscuits_evening_slot_once(example_board, reach, monkeypatch):
    """End to end over the real board: the example's 6-and-6 protocol for
    Biscuit, nothing logged, at 6:47 pm — the morning slot (with its
    unconfirmed chew) is long missed and stays quiet, the cats' windowed
    bowls never alert, and the evening dose is one alert."""
    async def nobody(now=None):
        return {"person": None, "since": None, "last_seen": None, "people": []}

    monkeypatch.setattr("services.hub_service.get_hub_person", nobody)
    out = await care_watch.check(now=NOW)
    assert out["alerts"] == 1 and len(reach) == 1
    assert reach[0]["title"] == "Biscuit's 6:00 pm dose"
    assert reach[0]["message"] == "Biscuit's 6:00 pm Carprofen isn't logged — 47 min late. Given? Log it on the hub."
    assert reach[0]["dedupe_key"] == f"care:biscuit:{DAY}:18:00"
    assert "(schedule unconfirmed)" not in reach[0]["message"]
    assert care_watch.last_check()["at"] is not None
