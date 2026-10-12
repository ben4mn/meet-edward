"""The stage lane after the proactivity rebuild (2026-10-11).

* Free time, research and the nightly ``reflect_lite`` job were the surplus
  queue's; the queue is gone, so ``surplus`` ledger rows are no longer read
  for the lane (an old row in the last 12 hours is simply not shown).
* A ``speak`` wake is about something Edward noticed in Ellis's messages. Its
  claim must never reach the Mac mini screen, which anyone in the room
  can read: its reasoning lines are not shown while it runs, and its
  closing — live or from the ledger — is one fixed line.
* The ``nightly`` reflection's closing shows like any wake's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import screen_thoughts

NOW = datetime(2026, 10, 11, 21, 30, tzinfo=timezone.utc)


def _row(kind: str, source: str, outcome: str, reason: str, minutes_ago: float) -> dict:
    return {"kind": kind, "source": source, "outcome": outcome, "reason": reason,
            "created_at": (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None)}


@pytest.fixture(autouse=True)
def _fresh():
    screen_thoughts.reset()
    yield
    screen_thoughts.reset()


@pytest.mark.unit
def test_surplus_rows_are_not_read_any_more():
    rows = [_row("surplus", "free_time", "done", "Sketched a plan for the garden beds.", 30),
            _row("surplus", "research", "done", "Looked into the flight change.", 40),
            _row("mind", "self", "left_alone", "Still watching the parcel.", 5)]
    assert [ln["text"] for ln in screen_thoughts.recent_lines(rows, now=NOW)] == ["Still watching the parcel."]
    assert not hasattr(screen_thoughts, "_SURPLUS_THOUGHTS")


@pytest.mark.unit
def test_a_speak_closing_is_one_fixed_line():
    rows = [_row("mind", "speak", "acted", "Told Ellis BA's check-in closes at 10 pm and he is en route.", 3)]
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == [screen_thoughts.SPEAK_LINE]
    assert out[0]["about"] == "speak"
    assert "BA" not in str(out) and "check-in" not in str(out)


@pytest.mark.unit
def test_speak_closings_collapse_to_one_line_and_a_dropped_one_still_says_nothing():
    rows = [_row("mind", "speak", "acted", "Left Ellis a note about Quinn's referral link.", 9),
            _row("mind", "speak", "left_alone", "DROP: Ellis already replied to Quinn at 3:10 pm.", 4)]
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == [screen_thoughts.SPEAK_LINE]
    assert "Quinn" not in str(out)


@pytest.mark.unit
def test_the_nightly_closing_shows_like_a_reflection():
    rows = [_row("mind", "nightly", "left_alone", "A quiet day: two pings answered, nothing left open.", 60)]
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == ["A quiet day: two pings answered, nothing left open."]
    assert out[0]["about"] == "nightly"


def _rec(turn: str, source: str = "mind", outcome: str = "completed"):
    return SimpleNamespace(id=turn, source=source, outcome=outcome)


@pytest.mark.unit
def test_a_live_speak_turn_shows_its_tools_but_never_its_thinking(monkeypatch):
    monkeypatch.setattr(screen_thoughts, "_about", lambda source: "speak")
    rec = _rec("t-speak")
    screen_thoughts.begin(rec, now=NOW)
    screen_thoughts.note_reasoning(rec, ["**Checking BA's text** The check-in closes at 10 pm; Ellis is en route."],
                                   now=NOW)
    screen_thoughts.tool_started(rec, "tell_owner", now=NOW)
    screen_thoughts.tool_finished(rec, "tell_owner", True, now=NOW)
    snap = screen_thoughts.snapshot(now=NOW)
    thinking = snap["thinking"]
    assert thinking["about"] == "speak"
    assert "BA" not in str(thinking) and "check-in" not in str(thinking)
    assert [r["tool"] for r in thinking["receipts"]] == ["tell_owner"]
    screen_thoughts.end(rec, now=NOW)
    folded = screen_thoughts.snapshot(now=NOW)["folded"]
    assert folded[-1]["text"] == screen_thoughts.SPEAK_LINE


@pytest.mark.unit
def test_other_live_turns_still_show_their_thinking(monkeypatch):
    monkeypatch.setattr(screen_thoughts, "_about", lambda source: "nightly")
    rec = _rec("t-night")
    screen_thoughts.begin(rec, now=NOW)
    screen_thoughts.note_reasoning(rec, ["Reading the day's verdicts."], now=NOW)
    assert screen_thoughts.snapshot(now=NOW)["thinking"]["lines"][0]["text"] == "Reading the day's verdicts."
