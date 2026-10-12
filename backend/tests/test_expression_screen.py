"""The expression pulse knows about this Mac's screen (2026-09-07); the
stage (2026-09-28): his scene, Ellis's hold, his last line, Ellis at the desk."""

from datetime import datetime, timedelta, timezone

import pytest

from services import expression_service as es
from services import screen_service as ss

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)


def _signals(**kw):
    base = dict(
        now=NOW, attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None},
        recent_messages=[], board_summary="", current_widget=None, current_screen=None,
    )
    base.update(kw)
    return es.PulseSignals(**base)


def test_prompt_names_the_mini_screen_and_its_tools():
    # With Edward's hands in Chrome (EDWARD_CHROME_HANDS=on); the stage is on this Mac.
    p = es.build_expression_system_prompt(chrome_hands=True, people=("Ellis",))
    for needle in ("compose_screen", "say_on_screen", "reset_screen_layout", "look_at_screen", 'screen="mini"',
                   "browser_open"):
        assert needle in p
    assert "`set_screen_layout`" not in p and "tile" not in p.lower()
    assert "this mac" in p.lower() and "mac mini" not in p.lower()
    # his thinking shows by itself; Ellis's asks hold; what raises Chrome stays off while Ellis is at the desk
    assert "thinking" in p and "hold" in p and "at the desk" in p


@pytest.fixture
def chicago(monkeypatch):
    from services import timezone_utils
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")


def _his_scene(**kw):
    scene, _ = ss.normalize_scene([
        {"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left"},
        {"kind": "image", "source": "https://x.y/a.jpg", "place": "top-right", "size": "xs"},
        {"kind": "note", "id": "thought", "title": "A thought", "body": "x", "place": "bottom-left", "size": "s"},
        {"kind": "list", "title": "Later today", "items": ["tea"], "place": "right", "size": "xs"},
    ], me={"on": "thought"}, why="you're out", now=NOW)
    return ss.build_snapshot(scene=scene, scene_why="you're out", scene_updated_by="edward", now=NOW, **kw)


def test_trigger_reports_what_the_mini_screen_shows(chicago):
    t = es.build_pulse_trigger(_signals(mini_screen=ss.build_snapshot(now=NOW)))
    assert "This Mac's screen now: the stock scene" in t
    t = es.build_pulse_trigger(_signals(mini_screen=_his_scene()))
    assert ("This Mac's screen now: your scene (words 'A quiet Monday…', image, note 'A thought', list 'Later today'; "
            "you stand on 'thought'; why: you're out)") in t


def test_the_description_names_a_show_a_hold_a_line_and_owner_at_the_desk(chicago):
    note = ss.make_show_content(kind="alert", body="Biscuit", title="Dose", now=datetime(2026, 9, 7, 22, 2, tzinfo=timezone.utc))
    snap = _his_scene(show=note, asked_at=NOW.replace(tzinfo=None),
                      asked_until=datetime(2026, 9, 8, 1, 10),  # 8:10 pm CDT
                      lines=[{"id": "a1b2c3d4", "text": "Biscuit's evening meds are due at 6 and she has not had them",
                              "at": "2026-09-07T22:52:00+00:00", "aloud": True}],
                      owner_at_mini=True)
    d = es.describe_mini_screen(snap)
    assert d.startswith("your scene (")
    assert ", with an alert 'Dose' over it since 5:02 pm" in d
    assert "; Ellis's ask holds it until 8:10 pm" in d
    assert "; you last said 'Biscuit's evening meds are…' at 5:52 pm" in d   # cut at a word
    assert d.endswith("; Ellis is at this Mac right now")


def test_the_pulse_still_hears_of_a_hold_after_a_tap_took_owners_show_down(chicago):
    # The page claims no ask once nothing Ellis asked for is on it, but the
    # hold stands (a tap does not end it), so the pulse must still be told.
    snap = _his_scene(asked_at=NOW.replace(tzinfo=None), asked_until=datetime(2026, 9, 8, 1, 10))
    assert snap["asked"] is None
    assert "; Ellis's ask holds it until 8:10 pm" in es.describe_mini_screen(snap)


def test_the_description_is_a_stable_change_key(chicago):
    """It is the pulse's change key: the same stored screen reads the same on every poll,
    even after a line has aged off the page."""
    line = [{"id": "a1b2c3d4", "text": "Hi", "at": NOW.isoformat(), "aloud": False}]
    a = es.describe_mini_screen(_his_scene(lines=line))
    scene, _ = ss.normalize_scene([
        {"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left"},
        {"kind": "image", "source": "https://x.y/a.jpg", "place": "top-right", "size": "xs"},
        {"kind": "note", "id": "thought", "title": "A thought", "body": "x", "place": "bottom-left", "size": "s"},
        {"kind": "list", "title": "Later today", "items": ["tea"], "place": "right", "size": "xs"},
    ], me={"on": "thought"}, why="you're out", now=NOW)
    b = es.describe_mini_screen(ss.build_snapshot(scene=scene, scene_why="you're out", scene_updated_by="edward",
                                                  now=NOW + timedelta(minutes=30), lines=line))
    assert a == b and "you last said 'Hi'" in a


def test_trigger_survives_no_mini_screen_signal():
    t = es.build_pulse_trigger(_signals())
    assert "This Mac's screen now:" in t
