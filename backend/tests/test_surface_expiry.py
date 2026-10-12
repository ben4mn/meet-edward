"""Ambient surfaces expire in code (proactivity rebuild, G23, 2026-10-11).

With the pulse change-only, nothing re-reads a surface every hour just in
case, so a stale item must go on its own — with no model call:

* a widget section may carry ``"until": "<ISO>"``; ``live_sections`` drops it
  once past, in ``get_widget_state`` (so ``GET /api/widget`` never serves
  it) and in the pulse's widget signal (so the ``*/15`` check sees the
  widget change and recomposes). A naive time is read in Ellis's zone;
* a show on the household screen or the Mac mini screen that Ellis did not
  ask for and that names no ``seconds`` expires after two hours, so a
  failed clear can never leave a kitchen alert up. What Ellis asks for (his
  turn, his policy, the hub's own panel) keeps its "until cleared".
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from services import widget_service as ws
from tests.area1_fakes import fake_module

NOW = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)
CHI = ZoneInfo("America/Chicago")


# ------------------------------------------------------------- widget --

@pytest.mark.unit
def test_live_sections_drop_what_is_past_its_until():
    sections = [
        {"type": "header", "title": "Tonight"},
        {"type": "text", "content": "Dog-waste visit at 3:40", "until": (NOW - timedelta(minutes=1)).isoformat()},
        {"type": "text", "content": "Dinner at 7", "until": (NOW + timedelta(hours=1)).isoformat()},
        {"type": "text", "content": "odd until", "until": "soon"},
        "not a section",
    ]
    kept = ws.live_sections(sections, NOW)
    assert [s.get("title") or s.get("content") for s in kept if isinstance(s, dict)] == [
        "Tonight", "Dinner at 7", "odd until"]
    assert "not a section" in kept, "anything else is left as it was"


@pytest.mark.unit
def test_a_naive_until_is_owner_local_time():
    # 2:30 pm in Springfield is 19:30 UTC — before NOW (20:00 UTC).
    gone = {"type": "text", "content": "x", "until": "2026-10-11T14:30:00"}
    kept = {"type": "text", "content": "y", "until": "2026-10-11T15:30:00"}
    assert ws.live_sections([gone, kept], NOW, tz=CHI) == [kept]


@pytest.mark.unit
def test_live_sections_tolerate_no_list():
    assert ws.live_sections(None, NOW) == []
    assert ws.live_sections("x", NOW) == []


async def _store_sections(sections):
    from services.database import WidgetStateModel, async_session

    async with async_session() as s:
        s.add(WidgetStateModel(id="default", title="Edward", subtitle="Tonight", sections=json.dumps(sections),
                               updated_by="edward"))
        await s.commit()


@pytest.mark.integration
async def test_the_widget_never_serves_an_expired_section(clean_db):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    await _store_sections([{"type": "text", "content": "stale", "until": past},
                           {"type": "text", "content": "fresh", "until": future}])
    state = await ws.get_widget_state()
    assert [s["content"] for s in state["sections"]] == ["fresh"]


@pytest.mark.integration
async def test_the_pulse_sees_an_expired_section_as_a_change(clean_db):
    from services import expression_service as es

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await _store_sections([{"type": "text", "content": "stale", "until": past}])
    widget = await es._widget_signal()
    assert widget["sections"] == []


# ------------------------------------------------------------ screens --

@pytest.fixture
def nobodys_turn(monkeypatch):
    from services import turn_ledger

    monkeypatch.setattr(turn_ledger, "current_turn", lambda: None)
    fake_module(monkeypatch, "services.toolset.policy", None)  # AREA 2 not merged


@pytest.mark.integration
async def test_an_autonomous_house_show_with_no_end_expires_in_two_hours(clean_db, nobodys_turn):
    from services import hub_canvas_service as canvas

    shown = await canvas.show(kind="text", title="Biscuit", body="6 pm dose not logged")
    exp = datetime.fromisoformat(shown["expires_at"])
    shown_at = datetime.fromisoformat(shown["shown_at"])
    assert exp - shown_at == timedelta(seconds=canvas.AUTONOMOUS_SHOW_SECONDS) == timedelta(hours=2)


@pytest.mark.integration
async def test_a_show_with_its_own_seconds_keeps_them(clean_db, nobodys_turn):
    from services import hub_canvas_service as canvas

    shown = await canvas.show(kind="text", body="x", seconds=600)
    assert datetime.fromisoformat(shown["expires_at"]) - datetime.fromisoformat(shown["shown_at"]) == \
        timedelta(seconds=600)


@pytest.mark.integration
async def test_the_hubs_own_panel_keeps_until_cleared(clean_db, nobodys_turn):
    from services import hub_canvas_service as canvas

    shown = await canvas.show(kind="text", body="Back at 6", shown_by="user")
    assert shown["expires_at"] is None


@pytest.mark.integration
async def test_owners_own_turn_keeps_until_cleared(clean_db, monkeypatch):
    from services import hub_canvas_service as canvas
    from services import turn_ledger

    fake_module(monkeypatch, "services.toolset.policy", None)
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source="chat", id="t"))
    assert (await canvas.show(kind="text", body="Movie night"))["expires_at"] is None


@pytest.mark.integration
async def test_a_policy_that_says_owner_asked_keeps_until_cleared(clean_db, monkeypatch):
    from services import hub_canvas_service as canvas
    from services import turn_ledger

    mod = types.ModuleType("services.toolset.policy")
    mod.current_policy = lambda: SimpleNamespace(owner_asked=True)
    fake_module(monkeypatch, "services.toolset.policy", mod)
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source="ping", id="t"))
    assert (await canvas.show(kind="text", body="Ellis asked from a ping"))["expires_at"] is None

    mod.current_policy = lambda: SimpleNamespace(owner_asked=False)
    assert (await canvas.show(kind="text", body="Edward's own"))["expires_at"] is not None


async def _clear_screen_row():
    from sqlalchemy import delete

    from services.database import ScreenStateModel, async_session

    async with async_session() as session:
        await session.execute(delete(ScreenStateModel))
        await session.commit()


@pytest.fixture
async def fresh_screen(clean_db):
    """``screen_state`` is kept across tests (``KEEP_ACROSS_TESTS``): Ellis's
    two-hour hold from the last line below would refuse Edward's show on
    the next run against the same database."""
    await _clear_screen_row()
    yield
    await _clear_screen_row()


@pytest.mark.integration
async def test_an_autonomous_mini_show_with_no_end_expires_in_two_hours(fresh_screen, nobodys_turn):
    from services import screen_service

    now = datetime.now(timezone.utc)
    shown = await screen_service.show(kind="text", body="Thinking about the garden", now=now, by="edward")
    assert datetime.fromisoformat(shown["expires_at"]) - now == timedelta(hours=2)
    asked = await screen_service.show(kind="text", body="Ellis's own", now=now, by="owner")
    assert asked["expires_at"] is None
