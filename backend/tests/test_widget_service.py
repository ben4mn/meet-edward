"""The widget's fallback content (2026-09-27, followups L13).

With no custom widget state, ``_generate_default_content`` fills the iPhone
widget from the scheduled events. It printed ``next_fire_at`` — naive UTC —
as if it were Ellis's local time ("Sep 27, 10:00 PM" for a 5 pm reminder),
greeted him by the host's clock, and listed Edward's own housekeeping (the
surfaces pulse, the surplus tick, the mind's wakes and ticks) as
"Upcoming", which also made "Pending" count them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import timezone_utils
from services import widget_service as ws


@pytest.fixture
def pacific():
    """Ellis in Los Angeles, never the host's zone: the Mac mini runs on
    America/Chicago, and a user zone equal to it cannot tell Ellis's clock
    from the host's — a host-clock regression (the bare ``astimezone()``
    behind the 2026-09-21 storm) passed every test here (2026-09-27
    review)."""
    before = timezone_utils.current_tz_name()
    timezone_utils.set_user_tz("America/Los_Angeles")
    yield
    timezone_utils.set_user_tz(before)


def _event(description, next_fire_at, meta=None):
    return SimpleNamespace(description=description, next_fire_at=next_fire_at,
                           event_metadata=json.dumps(meta) if meta is not None else None)


THREE_PM_PDT = datetime(2026, 9, 27, 22, 0)         # naive UTC, as the column holds it; 5 pm in Springfield


@pytest.mark.unit
def test_upcoming_is_owners_events_in_his_zone_never_edwards_housekeeping(pacific):
    events = [
        _event("Surfaces pulse", THREE_PM_PDT - timedelta(hours=3), {"type": "expression_pulse"}),
        _event("Surplus tick", THREE_PM_PDT - timedelta(hours=2), {"type": "surplus_tick"}),
        _event("Look at the vet thread", THREE_PM_PDT - timedelta(hours=1), {"type": "mind_wake", "why": "vet"}),
        _event("Evening wake", THREE_PM_PDT + timedelta(hours=1), {"type": "wakeup_tick", "kind": "evening"}),
        _event("Widget refresh", THREE_PM_PDT, {"type": "widget_refresh"}),
        _event("Remind Ellis to call the vet", THREE_PM_PDT),
        _event("Text Sam about Saturday", THREE_PM_PDT + timedelta(days=1, hours=2), {"memory_id": "m-1"}),
    ]
    items = ws.upcoming_items(events)
    assert [i["label"] for i in items] == ["Remind Ellis to call the vet", "Text Sam about Saturday"]
    assert items[0]["detail"] == "Sep 27, 3:00 pm"
    assert items[1]["detail"] == "Sep 28, 5:00 pm"


@pytest.mark.unit
def test_upcoming_keeps_three_soonest_and_reads_bad_metadata_as_owners(pacific):
    events = [_event(f"Reminder {n}", THREE_PM_PDT + timedelta(hours=n)) for n in (4, 1, 3, 2)]
    events.append(SimpleNamespace(description="Odd row", next_fire_at=THREE_PM_PDT, event_metadata="{not json"))
    assert [i["label"] for i in ws.upcoming_items(events)] == ["Odd row", "Reminder 1", "Reminder 2"]


@pytest.mark.unit
@pytest.mark.parametrize("now,greeting", [
    # Each hour sits where Los Angeles, Springfield and UTC disagree.
    (datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc), "Good morning, Ellis"),    # 11 am PDT, 1 pm CDT
    (datetime(2026, 9, 27, 23, 0, tzinfo=timezone.utc), "Good afternoon, Ellis"),  # 4 pm PDT, 6 pm CDT
    (datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc), "Good evening, Ellis"),     # 10 pm PDT, midnight CDT
])
def test_the_greeting_follows_owners_clock_not_the_hosts(pacific, now, greeting):
    assert ws.greeting_for(now) == greeting


@pytest.mark.integration
async def test_the_fallback_card_lists_and_counts_only_owners_events(clean_db, pacific):
    from services.database import ScheduledEventModel, async_session

    soon = datetime(2030, 1, 15, 22, 0)   # naive UTC: 2:00 pm PST, 4:00 pm CST
    async with async_session() as s:
        s.add(ScheduledEventModel(description="Surfaces pulse", scheduled_at=soon, next_fire_at=soon,
                                  recurrence_pattern="*/15 * * * *", event_metadata='{"type": "expression_pulse"}'))
        s.add(ScheduledEventModel(description="Surplus tick", scheduled_at=soon, next_fire_at=soon,
                                  recurrence_pattern="*/10 * * * *", event_metadata='{"type": "surplus_tick"}'))
        s.add(ScheduledEventModel(description="Remind Ellis to call the vet", scheduled_at=soon, next_fire_at=soon))
        await s.commit()
    content = await ws._generate_default_content()
    lists = [sec for sec in content["sections"] if sec.get("type") == "list"]
    assert [i["label"] for i in lists[0]["items"]] == ["Remind Ellis to call the vet"]
    # A literal, never recomputed with the utc_to_local the code calls.
    assert lists[0]["items"][0]["detail"] == "Jan 15, 2:00 pm"
    stats = next(sec for sec in content["sections"] if sec.get("type") == "stats_row")["stats"]
    assert {"label": "Pending", "value": "1"} in stats


# ---- Edward's latest write wins (2026-09-27, the widget audit) -----------------
#
# The Scriptable client runs ``data.script`` in preference to ``sections``,
# and ``update_widget_state`` never cleared ``script``: a script Edward set
# once with ``update_widget_code`` hid every structured update after it,
# while each of those writes (``updated_by`` edward, a fresh ``updated_at``)
# renewed the refresh hold — the iPhone showed Friday's pet board all weekend.


async def _row():
    from sqlalchemy import select

    from services.database import WidgetStateModel, async_session

    async with async_session() as s:
        return (await s.execute(select(WidgetStateModel).where(WidgetStateModel.id == "default"))).scalar_one()


@pytest.mark.integration
async def test_a_structured_update_replaces_the_script(clean_db):
    await ws.update_widget_script("// FRIDAY · PET BOARD")
    await ws.update_widget_state(sections=[{"type": "text", "content": "Sunday, all quiet"}], title="Edward")
    assert (await _row()).script is None
    state = await ws.get_widget_state()
    assert "script" not in state, "the phone would still run Friday's script"
    assert state["sections"] == [{"type": "text", "content": "Sunday, all quiet"}]


@pytest.mark.integration
async def test_a_script_written_after_a_structured_update_still_shows(clean_db):
    await ws.update_widget_state(sections=[{"type": "text", "content": "Sunday"}])
    await ws.update_widget_script("// Edward's own drawing")
    assert (await ws.get_widget_state())["script"] == "// Edward's own drawing"


@pytest.mark.unit
def test_the_tools_say_the_latest_write_wins():
    from services.graph.tools import update_widget, update_widget_code

    assert "replaces any custom code" in update_widget.description
    assert "until your next update_widget" in update_widget_code.description


# ---- no live token in the repo (2026-09-27) -------------------------------------
#
# scriptable/edward-widget.js carried the live widget token (in git since
# Feb 7); the token authenticates GET /api/widget and POST /api/widget/chat.
# The scripts ship a placeholder, as edward-chat.js always did; the phone's
# copy or its widget parameter ("url|token") carries the real one. Failures
# name files only, never a value.

import re
from pathlib import Path

SCRIPTABLE = Path(__file__).resolve().parents[2] / "scriptable"
PLACEHOLDER = "YOUR_TOKEN_HERE"


@pytest.mark.unit
def test_no_scriptable_script_carries_a_token():
    scripts = sorted(SCRIPTABLE.glob("*.js"))
    assert scripts
    hard_coded = [p.name for p in scripts
                  if any(v != PLACEHOLDER for v in re.findall(r'const TOKEN = "([^"]*)"', p.read_text()))]
    assert hard_coded == []


@pytest.mark.unit
def test_the_widget_takes_its_token_from_the_placeholder_or_the_parameter():
    src = (SCRIPTABLE / "edward-widget.js").read_text()
    # Booleans, so a failure never prints the source (and a value in it).
    ships_placeholder = f'const TOKEN = "{PLACEHOLDER}";' in src
    parameter_overrides = all(s in src for s in ('param.includes("|")', "url = parts[0];", "token = parts[1];"))
    fetches_with_it = "/api/widget?token=${token}" in src
    assert ships_placeholder
    assert parameter_overrides
    assert fetches_with_it
