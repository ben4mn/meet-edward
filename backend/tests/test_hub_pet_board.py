"""Pet board logic for the household hub (`/hub`).

The hub is a Raspberry Pi touchscreen at the pet station. Its first screen
answers two questions from across the room:

  1. "Did anyone feed them?"  — four pets x two bowls, lit or dim.
  2. "Did Biscuit get her 6-and-6?" — a real timed drug protocol.

Those two questions have *different* urgency semantics, and conflating them
is the failure mode that kills the device:

  - Cat wet food is free-fed and given "twice daily, whenever". If a dim
    bowl turns amber/OVERDUE at 9am, Ellis and Rowan learn within a week
    that amber means nothing — and then they ignore amber when Biscuit's
    carprofen is genuinely late. Alert fatigue is the risk, so
    ``schedule_type="window"`` items must NEVER be overdue.
  - Biscuit's protocol is at 06:00/18:00 sharp. ``schedule_type="timed"``
    items go overdue after a grace period and are worth escalating.

Bowls reset at 3am local, not midnight, so a 1am feeding still belongs to
the night it happened rather than lighting the next morning's bowl.
"""

from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from services import hub_pet_service as svc


TZ = ZoneInfo("America/Chicago")


def _pet(pet_id, name, species="dog", sort_order=0):
    return SimpleNamespace(
        id=pet_id, name=name, species=species,
        photo_url=None, sort_order=sort_order,
    )


def _item(item_id, pet_id, kind, name, schedule_type, window,
          at_time=None, dose=None, bundle_with_id=None, sort_order=0,
          needs_review=False):
    return SimpleNamespace(
        id=item_id, pet_id=pet_id, kind=kind, name=name, dose=dose,
        schedule_type=schedule_type, window=window, at_time=at_time,
        bundle_with_id=bundle_with_id, sort_order=sort_order, active=True,
        needs_review=needs_review,
    )


def _event(item_id, given_at, given_by="owner", status="given"):
    """given_at is naive UTC, matching how the DB stores timestamps."""
    return SimpleNamespace(
        care_item_id=item_id, given_at=given_at,
        given_by=given_by, status=status, method="tap",
    )


def _utc(y, m, d, hh, mm):
    """A naive-UTC timestamp for the given Springfield wall-clock time."""
    local = datetime(y, m, d, hh, mm, tzinfo=TZ)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# The 3am care-day boundary
# ---------------------------------------------------------------------------

def test_care_day_rolls_over_at_3am_not_midnight():
    # 11pm and 1am are the same care day: the night of the 21st.
    assert svc.care_day_for(datetime(2026, 8, 21, 23, 0, tzinfo=TZ)) == date(2026, 8, 21)
    assert svc.care_day_for(datetime(2026, 8, 22, 1, 0, tzinfo=TZ)) == date(2026, 8, 21)
    assert svc.care_day_for(datetime(2026, 8, 22, 2, 59, tzinfo=TZ)) == date(2026, 8, 21)
    # 3am starts the new one.
    assert svc.care_day_for(datetime(2026, 8, 22, 3, 0, tzinfo=TZ)) == date(2026, 8, 22)
    assert svc.care_day_for(datetime(2026, 8, 22, 6, 12, tzinfo=TZ)) == date(2026, 8, 22)


def test_current_window_flips_at_noon():
    assert svc.current_window(datetime(2026, 8, 22, 6, 12, tzinfo=TZ)) == "am"
    assert svc.current_window(datetime(2026, 8, 22, 11, 59, tzinfo=TZ)) == "am"
    assert svc.current_window(datetime(2026, 8, 22, 12, 0, tzinfo=TZ)) == "pm"
    assert svc.current_window(datetime(2026, 8, 22, 18, 0, tzinfo=TZ)) == "pm"
    # Pre-3am belongs to the previous care day's PM window.
    assert svc.current_window(datetime(2026, 8, 22, 1, 0, tzinfo=TZ)) == "pm"


# ---------------------------------------------------------------------------
# Feeding: the eight-icon strip
# ---------------------------------------------------------------------------

def test_unfed_bowls_are_dim_with_no_attribution():
    pets = [_pet("pepper", "Pepper", "cat")]
    items = [
        _item("pepper-am", "pepper", "feeding", "Wet food", "window", "am"),
        _item("pepper-pm", "pepper", "feeding", "Wet food", "window", "pm"),
    ]
    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 6, 2, tzinfo=TZ))

    feedings = board["pets"][0]["feedings"]
    assert feedings["am"]["done"] is False
    assert feedings["am"]["by"] is None
    assert feedings["pm"]["done"] is False


def test_fed_bowl_lights_up_and_records_who_and_when():
    pets = [_pet("pepper", "Pepper", "cat")]
    items = [_item("pepper-am", "pepper", "feeding", "Wet food", "window", "am")]
    events = [_event("pepper-am", _utc(2026, 8, 22, 6, 40), given_by="rowan")]

    board = svc.build_board(pets, items, events, now=datetime(2026, 8, 22, 7, 0, tzinfo=TZ))

    am = board["pets"][0]["feedings"]["am"]
    assert am["done"] is True
    assert am["by"] == "rowan"
    assert am["at_local"] == "6:40a"


def test_feeding_is_never_overdue_however_late_it_gets():
    """The whole point. A dim bowl at 11am is information, not an alarm."""
    pets = [_pet("whiskers", "Mr. Whiskers", "cat")]
    items = [_item("whiskers-am", "whiskers", "feeding", "Wet food", "window", "am")]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 11, 55, tzinfo=TZ))

    assert board["pets"][0]["feedings"]["am"]["overdue"] is False
    assert board["alerts"] == []


def test_yesterdays_feeding_does_not_light_todays_bowl():
    pets = [_pet("pepper", "Pepper", "cat")]
    items = [_item("pepper-am", "pepper", "feeding", "Wet food", "window", "am")]
    events = [_event("pepper-am", _utc(2026, 8, 21, 6, 30))]

    board = svc.build_board(pets, items, events, now=datetime(2026, 8, 22, 6, 2, tzinfo=TZ))

    assert board["pets"][0]["feedings"]["am"]["done"] is False


# ---------------------------------------------------------------------------
# Meds: Biscuit's 6-and-6
# ---------------------------------------------------------------------------

def test_timed_med_is_due_before_its_time_and_not_overdue():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0), dose="100mg")]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 5, 45, tzinfo=TZ))

    med = board["pets"][0]["meds"][0]
    assert med["status"] == "due"
    assert board["alerts"] == []


def test_timed_med_stays_due_inside_the_grace_window():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0), dose="100mg")]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 6, 20, tzinfo=TZ))

    assert board["pets"][0]["meds"][0]["status"] == "due"


def test_timed_med_goes_overdue_past_the_grace_window():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0), dose="100mg")]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 6, 45, tzinfo=TZ))

    med = board["pets"][0]["meds"][0]
    assert med["status"] == "overdue"
    assert board["alerts"] == [
        {"pet": "Biscuit", "item": "Carprofen", "minutes_late": 45},
    ]


def test_given_med_is_never_overdue_even_if_logged_late():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0), dose="100mg")]
    events = [_event("biscuit-carprofen-am", _utc(2026, 8, 22, 7, 30))]

    board = svc.build_board(pets, items, events, now=datetime(2026, 8, 22, 9, 0, tzinfo=TZ))

    med = board["pets"][0]["meds"][0]
    assert med["status"] == "given"
    assert med["by"] == "owner"
    assert board["alerts"] == []


def test_refused_med_is_flagged_but_does_not_read_as_given():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0))]
    events = [_event("biscuit-carprofen-am", _utc(2026, 8, 22, 6, 5), status="refused")]

    board = svc.build_board(pets, items, events, now=datetime(2026, 8, 22, 6, 50, tzinfo=TZ))

    assert board["pets"][0]["meds"][0]["status"] == "refused"


def test_prn_items_are_never_due_and_never_overdue():
    """Home-pharmacy meds are logged when used, and never nag."""
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-melatonin", "biscuit", "med", "Melatonin", "prn", None)]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 23, 0, tzinfo=TZ))

    assert board["pets"][0]["meds"] == []
    assert board["alerts"] == []


# ---------------------------------------------------------------------------
# Bundling: Pepper's PM bowl carries her probiotic powder
# ---------------------------------------------------------------------------

def test_bundled_supplement_rides_on_its_parent_feeding():
    pets = [_pet("pepper", "Pepper", "cat")]
    items = [
        _item("pepper-pm", "pepper", "feeding", "Wet food", "window", "pm"),
        _item("pepper-probiotic", "pepper", "supplement", "Probiotic powder", "window", "pm",
              dose="1 scoop", bundle_with_id="pepper-pm"),
    ]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 18, 0, tzinfo=TZ))

    pm = board["pets"][0]["feedings"]["pm"]
    assert pm["marker"] == "+P"
    assert pm["bundled_item_ids"] == ["pepper-probiotic"]
    # It must not also appear as a separate tap target.
    assert board["pets"][0]["meds"] == []


def test_a_short_last_word_is_the_bundled_badge():
    pets = [_pet("pepper", "Pepper", "cat")]
    items = [
        _item("pepper-pm", "pepper", "feeding", "Wet food", "window", "pm"),
        _item("pepper-e", "pepper", "supplement", "Vitamin E", "window", "pm",
              dose="1 pump", bundle_with_id="pepper-pm"),
    ]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 18, 0, tzinfo=TZ))

    assert board["pets"][0]["feedings"]["pm"]["marker"] == "+E"


# ---------------------------------------------------------------------------
# Board shape
# ---------------------------------------------------------------------------

def test_board_reports_care_day_and_current_window():
    board = svc.build_board([], [], [], now=datetime(2026, 8, 22, 6, 2, tzinfo=TZ))
    assert board["care_day"] == "2026-08-22"
    assert board["window"] == "am"


def test_pets_keep_their_column_order():
    pets = [
        _pet("whiskers", "Mr. Whiskers", "cat", sort_order=3),
        _pet("biscuit", "Biscuit", "dog", sort_order=0),
        _pet("pepper", "Pepper", "cat", sort_order=2),
        _pet("maple", "Maple", "dog", sort_order=1),
    ]
    board = svc.build_board(pets, [], [], now=datetime(2026, 8, 22, 6, 2, tzinfo=TZ))

    assert [p["name"] for p in board["pets"]] == [
        "Biscuit", "Maple", "Pepper", "Mr. Whiskers",
    ]


# ---------------------------------------------------------------------------
# One tap logs the bundle
# ---------------------------------------------------------------------------

def test_tapping_a_plain_item_logs_only_itself():
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am")]
    assert svc.items_to_log("biscuit-carprofen-am", items) == ["biscuit-carprofen-am"]


def test_tapping_pepper_pm_bowl_also_logs_her_probiotic():
    """The bowl and the powder in it are one physical act."""
    items = [
        _item("pepper-pm", "pepper", "feeding", "Wet food", "window", "pm"),
        _item("pepper-probiotic", "pepper", "supplement", "Probiotic powder", "window", "pm",
              bundle_with_id="pepper-pm"),
    ]
    assert svc.items_to_log("pepper-pm", items) == ["pepper-pm", "pepper-probiotic"]


def test_tapping_a_bundled_child_directly_logs_only_the_child():
    """Long-press on the +P badge records the powder alone, e.g. she ate
    the food yesterday but refused it today."""
    items = [
        _item("pepper-pm", "pepper", "feeding", "Wet food", "window", "pm"),
        _item("pepper-probiotic", "pepper", "supplement", "Probiotic powder", "window", "pm",
              bundle_with_id="pepper-pm"),
    ]
    assert svc.items_to_log("pepper-probiotic", items) == ["pepper-probiotic"]


def test_inactive_children_are_not_logged():
    parent = _item("pepper-pm", "pepper", "feeding", "Wet food", "window", "pm")
    child = _item("pepper-probiotic", "pepper", "supplement", "Probiotic powder", "window", "pm",
                  bundle_with_id="pepper-pm")
    child.active = False
    assert svc.items_to_log("pepper-pm", [parent, child]) == ["pepper-pm"]


def test_unknown_item_logs_nothing():
    assert svc.items_to_log("nope", []) == []


# ---------------------------------------------------------------------------
# Unconfirmed detail must look unconfirmed
# ---------------------------------------------------------------------------

def test_item_seeded_from_incomplete_info_is_flagged_for_review():
    """Biscuit's drug names/strengths came off a pet-sitter note. Until Ellis
    confirms them, the hub shows a badge rather than quietly implying the
    log is authoritative."""
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-melatonin-am", "biscuit", "med", "Melatonin", "timed", "am",
                   at_time=time(6, 0), dose="1 capsule", needs_review=True)]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 5, 45, tzinfo=TZ))

    assert board["pets"][0]["meds"][0]["needs_review"] is True


def test_confirmed_item_is_not_flagged():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0), dose="100mg")]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 5, 45, tzinfo=TZ))

    assert board["pets"][0]["meds"][0]["needs_review"] is False


# ---------------------------------------------------------------------------
# Overdue vs missed
# ---------------------------------------------------------------------------

def test_recently_late_dose_is_overdue_and_alerts():
    """Actionable: it's 6:45, the 6:00 dose is 45 minutes late, go give it."""
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0))]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 6, 45, tzinfo=TZ))

    assert board["pets"][0]["meds"][0]["status"] == "overdue"
    assert len(board["alerts"]) == 1


def test_long_past_dose_is_missed_and_stops_alerting():
    """By 6pm, the 6am dose is not something you fix by dosing now — and
    whether to catch up is a call for Ellis and his vet, not a red badge.
    The hub records it as missed for the adherence view and shuts up."""
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0))]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 18, 0, tzinfo=TZ))

    assert board["pets"][0]["meds"][0]["status"] == "missed"
    assert board["alerts"] == []


def test_missed_dose_still_appears_for_the_adherence_record():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0))]

    board = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 18, 0, tzinfo=TZ))

    assert len(board["pets"][0]["meds"]) == 1


def test_boundary_between_overdue_and_missed():
    pets = [_pet("biscuit", "Biscuit")]
    items = [_item("biscuit-carprofen-am", "biscuit", "med", "Carprofen", "timed", "am",
                   at_time=time(6, 0))]

    just_inside = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 9, 59, tzinfo=TZ))
    just_outside = svc.build_board(pets, items, [], now=datetime(2026, 8, 22, 10, 1, tzinfo=TZ))

    assert just_inside["pets"][0]["meds"][0]["status"] == "overdue"
    assert just_outside["pets"][0]["meds"][0]["status"] == "missed"
