"""He is encouraged to use his screen (the stage, round 2, 2026-09-28).

Ellis: "he should always feel encouraged to be doing something with it".

* The pulse's prompt says this Mac's screen is his — a place to think out
  loud, play and make things — and that change is welcome there; composing
  and saying never move a window, so they are fine while Ellis is at the
  desk; what raises Chrome or covers their work stays off then unless asked.
* The pulse trigger says how long the scene has been up (coarse, so it
  does not flip every poll) and carries one random spark — never part of
  the change key (``signal_parts``).
* Free time may use the stage (``compose_screen``, ``say_on_screen``) and
  nothing else that reaches out; a line said in a surplus turn is always
  quiet, enforced by the call's purpose, not only by the prompt.
* The mind's own prompt has one line saying the screen is his.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from services import expression_service as es
from services import screen_service as ss

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)  # 4:00 pm CDT


@pytest.fixture
def chicago(monkeypatch):
    from services import timezone_utils
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")


def _signals(**kw):
    base = dict(now=NOW, attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None},
                recent_messages=[], board_summary="", current_widget=None, current_screen=None)
    base.update(kw)
    return es.PulseSignals(**base)


def _scene_at(updated_at: datetime, by: str = "edward"):
    scene, _ = ss.normalize_scene([{"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left"}],
                                  why="you're out", now=updated_at)
    return ss.build_snapshot(scene=scene, scene_why="you're out", scene_updated_by=by,
                             scene_updated_at=updated_at.replace(tzinfo=None), now=updated_at)


class _First:
    """A deterministic rng: always the first choice."""

    def choice(self, seq):
        return seq[0]


# ------------------------------------------------------------ the prompt --

@pytest.mark.unit
def test_the_pulse_prompt_says_the_screen_is_his_and_change_is_welcome():
    for hands in (False, True):   # with or without his hands in Chrome (EDWARD_CHROME_HANDS)
        p = es.build_expression_system_prompt(chrome_hands=hands, people=("Ellis",))
        low = p.lower()
        assert "think out loud" in low and "play" in low and "make things" in low
        assert "change is welcome" in low
        assert "has been up a while" in low


@pytest.mark.unit
def test_at_the_desk_composing_and_saying_are_fine_but_nothing_raises_chrome():
    # What raises Chrome only exists with his hands in it (EDWARD_CHROME_HANDS=on).
    p = es.build_expression_system_prompt(chrome_hands=True, people=("Ellis",))
    desk = p[p.index("at the desk"):]
    assert "never move a window" in p
    for needle in ('show_on_screen(screen="mini")', "bring_up_screen", "browser_open", "browser_fullscreen"):
        assert needle in desk, needle
    assert "unless they asked" in p
    # the old "leave the screen alone" at the desk is gone
    assert "leave the screen alone" not in p


# ----------------------------------------------------------- the trigger --

@pytest.mark.unit
def test_the_trigger_says_how_long_the_scene_has_been_up(chicago):
    t = es.build_pulse_trigger(_signals(mini_screen=_scene_at(NOW - timedelta(hours=3, minutes=50))), rng=_First())
    assert "your scene has been up since 12:10 pm, 3 h" in t
    t = es.build_pulse_trigger(_signals(mini_screen=_scene_at(NOW - timedelta(minutes=20))), rng=_First())
    assert "your scene has been up since 3:40 pm, under an hour" in t
    t = es.build_pulse_trigger(_signals(mini_screen=_scene_at(NOW - timedelta(hours=26), by="owner")), rng=_First())
    assert "the scene Ellis asked for has been up since yesterday 2:00 pm, 26 h" in t
    t = es.build_pulse_trigger(_signals(mini_screen=_scene_at(NOW - timedelta(days=3, hours=1))), rng=_First())
    assert "your scene has been up since Friday 3:00 pm, 3 days" in t


@pytest.mark.unit
def test_the_stock_scene_with_no_time_says_no_age(chicago):
    t = es.build_pulse_trigger(_signals(mini_screen=ss.build_snapshot(now=NOW)), rng=_First())
    assert "This Mac's screen now: the stock scene" in t and "has been up" not in t


@pytest.mark.unit
def test_one_random_spark_per_pulse(chicago):
    assert 18 <= len(es.SCREEN_SPARKS) <= 30
    assert len(set(es.SCREEN_SPARKS)) == len(es.SCREEN_SPARKS)
    t = es.build_pulse_trigger(_signals(mini_screen=_scene_at(NOW - timedelta(hours=2))), rng=_First())
    assert f"One idea, if you want it (ignore freely): {es.SCREEN_SPARKS[0]}" in t
    assert t.count("One idea, if you want it") == 1
    seen = {es.screen_spark(random.Random(seed)) for seed in range(60)}
    assert len(seen) > 5 and seen <= set(es.SCREEN_SPARKS)


@pytest.mark.unit
def test_neither_the_spark_nor_the_age_is_part_of_the_change_key(chicago):
    snap = _scene_at(NOW - timedelta(minutes=10))
    early = _signals(mini_screen=snap)
    later = _signals(mini_screen=snap, now=NOW + timedelta(hours=5))
    assert es.signal_parts(early) == es.signal_parts(later)
    parts = " ".join(es.signal_parts(early).values())
    assert "One idea" not in parts and "has been up" not in parts
    a = es.build_pulse_trigger(early, rng=random.Random(1))
    b = es.build_pulse_trigger(early, rng=random.Random(2))
    strip = lambda t: [ln for ln in t.splitlines() if not ln.startswith("One idea")]  # noqa: E731
    assert strip(a) == strip(b)


@pytest.mark.unit
def test_the_trigger_stays_compact_with_the_spark(chicago):
    msgs = [{"sender": f"+1{i:03d}", "text": "x" * 140, "is_from_me": False, "chat_identifier": "c",
             "chat_name": None} for i in range(12)]
    t = es.build_pulse_trigger(_signals(mini_screen=_scene_at(NOW - timedelta(hours=2)), recent_messages=msgs))
    assert "One idea, if you want it (ignore freely): " in t
    assert len(t) < 6000


# ------------------------------------------------------------- free time --

@pytest.mark.integration
async def test_a_line_in_a_surplus_turn_is_always_quiet(clean_db):
    from services.call_purpose import purpose_scope

    day = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)  # noon in Springfield: not quiet hours
    with purpose_scope("surplus:free_time"):
        out = await ss.say("A thought about the garden.", aloud=True, now=day)
    assert out["line"]["aloud"] is False and out["quiet"] is True and out["quiet_why"] == "surplus"
    out = await ss.say("Out loud this time.", aloud=True, now=day)
    assert out["line"]["aloud"] is True and out["quiet"] is False and out["quiet_why"] is None


@pytest.mark.unit
def test_only_a_surplus_purpose_counts_as_a_surplus_turn():
    from services.call_purpose import purpose_scope

    assert ss.in_surplus_turn() is False
    with purpose_scope("surplus:free_time"):
        assert ss.in_surplus_turn() is True
    with purpose_scope("mind:morning"):
        assert ss.in_surplus_turn() is False


@pytest.mark.unit
async def test_the_tool_says_a_surplus_line_was_shown_not_spoken(monkeypatch):
    from services.graph import tools

    async def fake_say(text, *, aloud=False, by="edward", now=None):
        return {"line": {"id": "a1b2c3d4", "text": text, "at": NOW.isoformat(), "aloud": False},
                "quiet": True, "quiet_why": "surplus"}

    monkeypatch.setattr(ss, "say", fake_say)
    reply = await tools.say_on_screen.ainvoke({"line": "Hello", "aloud": True})
    assert reply.startswith("Said on your screen at ") and "quietly" in reply and "free time" in reply


# ------------------------------------------------------------- the mind --

@pytest.mark.unit
def test_the_mind_prompt_has_one_line_about_his_screen():
    from services import mind_service as ms

    lines = [ln for ln in ms.MIND_SYSTEM_PROMPT.splitlines() if "say_on_screen" in ln]
    assert len(lines) == 1 and "This Mac's screen" in lines[0] and "yours" in lines[0]
    assert len(lines[0]) < 260


@pytest.mark.unit
def test_the_compose_manual_welcomes_change_and_the_desk():
    from services.graph import tools

    doc = " ".join(tools.compose_screen.description.split())
    assert "leave the screen alone" not in doc
    assert "never moves a window" in doc and "at the desk" in doc
    assert "change is welcome" in doc.lower()
