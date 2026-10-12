"""Shopping / household lists for the hub.

A list on a wall panel behaves differently from a list in an app: nobody
tidies it. So the board prunes itself — ticked items sink, then disappear
after a while — and stays a queue rather than a stack, because the thing
you added first is the thing you've been meaning to buy longest.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import hub_list_service as svc


NOW = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)


def _item(item_id, text, done=False, created_min_ago=0, done_min_ago=None):
    return SimpleNamespace(
        id=item_id,
        list_name="Shopping",
        text=text,
        done=done,
        created_at=(NOW - timedelta(minutes=created_min_ago)).replace(tzinfo=None),
        done_at=(
            (NOW - timedelta(minutes=done_min_ago)).replace(tzinfo=None)
            if done_min_ago is not None
            else None
        ),
        created_by="owner",
        done_by="owner" if done else None,
    )


def test_open_items_are_a_queue_oldest_first():
    items = [
        _item("c", "cat litter", created_min_ago=5),
        _item("a", "coffee", created_min_ago=100),
        _item("b", "dog food", created_min_ago=50),
    ]
    assert [i.id for i in svc.visible_items(items, now=NOW)] == ["a", "b", "c"]


def test_ticked_items_sink_below_open_ones():
    items = [
        _item("done", "milk", done=True, created_min_ago=200, done_min_ago=5),
        _item("open", "eggs", created_min_ago=10),
    ]
    assert [i.id for i in svc.visible_items(items, now=NOW)] == ["open", "done"]


def test_recently_ticked_stays_visible_so_it_can_be_unticked():
    items = [_item("d", "milk", done=True, created_min_ago=200, done_min_ago=30)]
    assert [i.id for i in svc.visible_items(items, now=NOW)] == ["d"]


def test_long_done_items_drop_off_the_board():
    """Nobody clears a wall panel by hand, so it clears itself."""
    items = [_item("d", "milk", done=True, created_min_ago=2000, done_min_ago=13 * 60)]
    assert svc.visible_items(items, now=NOW) == []


def test_boundary_of_the_auto_clear():
    just_inside = _item("in", "milk", done=True, done_min_ago=svc.CLEAR_DONE_AFTER_MINUTES - 1)
    just_outside = _item("out", "milk", done=True, done_min_ago=svc.CLEAR_DONE_AFTER_MINUTES + 1)
    ids = [i.id for i in svc.visible_items([just_inside, just_outside], now=NOW)]
    assert ids == ["in"]


def test_done_item_with_no_timestamp_is_kept_not_silently_dropped():
    """Defensive: a NULL done_at from an older row must not vanish."""
    items = [_item("d", "milk", done=True, created_min_ago=5000, done_min_ago=None)]
    assert [i.id for i in svc.visible_items(items, now=NOW)] == ["d"]


def test_grouping_splits_by_list_name():
    shopping = _item("s", "eggs")
    household = _item("h", "change filter")
    household.list_name = "Household"

    grouped = svc.group_by_list([shopping, household], now=NOW)

    assert set(grouped) == set(svc.DEFAULT_LISTS)
    assert [i["text"] for i in grouped["Shopping"]] == ["eggs"]
    assert [i["text"] for i in grouped["Household"]] == ["change filter"]


def test_empty_lists_still_appear_so_the_ui_has_somewhere_to_add():
    grouped = svc.group_by_list([], now=NOW)
    assert set(grouped) == set(svc.DEFAULT_LISTS)
    assert all(v == [] for v in grouped.values())
