"""The lane is never empty (the stage, round 2, 2026-09-28).

Ellis: "it should always show what he is thinking or dreaming". Between
turns the /screen lane drifts through Edward's own recent thoughts, so
``GET /api/screen/live`` carries ``recent``: the closing lines of his
wakes (the ``mind`` ledger rows; the surplus queue's free time and
research went with the queue on 2026-10-11 — ``test_screen_lane_private``),
from the last 12 hours, newest last, at most 12 — durable rows, so a
restart keeps them.
Never the pulse's decisions (they are about surfaces, not thoughts) and
nothing from Ellis's own chat. Cleaned like a live line, scrubbed fail
closed, boilerplate dropped; the read is cached ~30 s (the page polls
every 2 s) and never breaks the route.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import screen_thoughts

NOW = datetime(2026, 9, 28, 21, 30, tzinfo=timezone.utc)


def _row(kind: str, source: str, outcome: str, reason: str, minutes_ago: float) -> dict:
    # ledger_events.created_at is naive UTC (the session's TimeZone is pinned to UTC)
    return {"kind": kind, "source": source, "outcome": outcome, "reason": reason,
            "created_at": (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None)}


@pytest.fixture(autouse=True)
def _fresh():
    screen_thoughts.reset()
    yield
    screen_thoughts.reset()


# ------------------------------------------------------------------ pure --

@pytest.mark.unit
def test_his_wakes_become_lines_newest_last():
    rows = [
        _row("mind", "evening", "left_alone", "Nothing about Biscuit's evening dose needs Ellis yet.", 30),
        _row("mind", "self", "acted", "Sketched a plan for the garden beds in a document.", 90),
        _row("mind", "messages", "acted", "Answered Rowan about dinner; she wanted a time.", 5),
        _row("mind", "nightly", "left_alone", "Closed two stale intentions and wrote down what I learned.", 400),
    ]
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == [
        "Closed two stale intentions and wrote down what I learned.",
        "Sketched a plan for the garden beds in a document.",
        "Nothing about Biscuit's evening dose needs Ellis yet.",
        "Answered Rowan about dinner; she wanted a time.",
    ]
    assert [ln["about"] for ln in out] == ["nightly", "self", "evening", "messages"]
    assert [ln["kind"] for ln in out] == ["mind", "mind", "mind", "mind"]
    assert out[-1]["at"] == (NOW - timedelta(minutes=5)).isoformat()
    assert all(set(ln) == {"text", "at", "about", "kind"} for ln in out)
    assert all(datetime.fromisoformat(ln["at"]).tzinfo is not None for ln in out)


@pytest.mark.unit
def test_never_the_pulse_a_failure_or_a_job_that_is_not_his_thinking():
    rows = [
        _row("decision", "pulse", "changed", "Put the evening scene up.", 1),
        _row("mind", "surfaces", "acted", "Set the scene.", 2),
        _row("mind", "morning", "failed", "TimeoutError", 3),
        _row("mind", "morning", "skipped", "Codex quota exhausted", 4),
        _row("surplus", "free_time", "failed", "TimeoutError: ran past 600s", 5),
        _row("surplus", "free_time", "skipped", "the wake was skipped", 6),
        _row("surplus", "controller", "skipped", "waiting for the pace line", 7),
        _row("surplus", "dossiers", "done", "Parker refreshed from 12 memories and 40 messages", 8),
        _row("surplus", "summaries", "done", "Trip planning summarised from 80 messages", 9),
        _row("rate_gate", "x", "deferred", "over pace", 10),
        _row("mind", "self", "left_alone", "Still watching the parcel.", 11),
    ]
    assert [ln["text"] for ln in screen_thoughts.recent_lines(rows, now=NOW)] == ["Still watching the parcel."]


@pytest.mark.unit
def test_only_the_last_twelve_hours_and_at_most_twelve_lines():
    rows = [_row("mind", "self", "left_alone", f"Thought number {i}.", 10 + i * 20) for i in range(20)]
    rows.append(_row("mind", "self", "left_alone", "Too old to show.", 12 * 60 + 1))
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert len(out) == screen_thoughts.MAX_RECENT == 12
    assert out[-1]["text"] == "Thought number 0." and out[0]["text"] == "Thought number 11."
    assert "Too old to show." not in [ln["text"] for ln in out]


@pytest.mark.unit
@pytest.mark.parametrize("note", ["", "   ", "(no note)", "Nothing to do.", "nothing", "No note",
                                  "free_time done", "reflect_lite done", "Nothing new."])
def test_boilerplate_is_not_a_thought(note):
    rows = [_row("mind", "self", "left_alone", note, 3), _row("surplus", "free_time", "done", note, 4)]
    assert screen_thoughts.recent_lines(rows, now=NOW) == []


def _stopped_notes():
    # The real words a wake closes on when it stopped without an answer
    # (``tool_policy.honest_ending`` via the tool loop, shaped into a note the
    # way ``mind_service.wake`` shapes it): a failure report, not a thought.
    from services.graph.tool_policy import honest_ending

    return [
        honest_ending("the model did not answer within 300s",
                      [("remember_search", {}), ("remember_search", {}), ("read_document", {})]),
        honest_ending("it ran out of tool iterations (30)", [("browser_read", {})]),
        honest_ending("the model returned an empty answer", []),
        honest_ending("", []),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("outcome", ["left_alone", "acted"])
def test_a_wake_that_stopped_before_finishing_is_not_a_thought(outcome):
    notes = _stopped_notes()
    assert all(n.startswith("I stopped before finishing") for n in notes)
    rows = [_row("mind", "morning", outcome, " ".join(n.split()), 3 + i) for i, n in enumerate(notes)]
    rows += [_row("surplus", "free_time", "done", notes[0], 20)]
    rows.append(_row("mind", "evening", "left_alone", "The house is calm; nothing needs Ellis.", 1))
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == ["The house is calm; nothing needs Ellis."]


@pytest.mark.unit
def test_a_retry_marker_or_a_tool_error_is_not_a_thought():
    from services.mind_service import RETRY_MARK, retry_extra

    rows = [
        _row("mind", "morning", "left_alone", retry_extra(None, 1, "TimeoutError"), 3),
        _row("mind", "morning", "left_alone", f"{RETRY_MARK}2 of 3: the last attempt failed.)", 4),
        _row("mind", "self", "acted", "Tool error: browser_read timed out after 120s", 5),
        _row("surplus", "research", "done", "Tool error: blocked — the boundary check failed", 6),
        _row("mind", "self", "left_alone", "Tuesday's flight still works.", 1),
    ]
    assert [ln["text"] for ln in screen_thoughts.recent_lines(rows, now=NOW)] == ["Tuesday's flight still works."]


@pytest.mark.unit
def test_a_line_is_cleaned_like_a_live_line():
    long = "**Weighing the evening** " + "I checked the pets and the calendar and nothing is pressing. " * 8
    out = screen_thoughts.recent_lines([_row("mind", "evening", "left_alone", long, 3),
                                        _row("mind", "self", "acted", "Saved   the  `recipe`\n  for later.", 2)],
                                       now=NOW)
    assert out[0]["text"].startswith("Weighing the evening I checked") or out[0]["text"].startswith("I checked")
    assert len(out[0]["text"]) <= screen_thoughts.LINE_MAX_CHARS and "**" not in out[0]["text"]
    assert out[1]["text"] == "Saved the recipe for later."


@pytest.mark.unit
def test_the_same_wake_in_both_ledgers_is_one_line():
    # A free-time wake writes its mind row and the surplus job's row with the same note.
    note = "Rewrote my state document so it reads as a page."
    rows = [_row("mind", "free", "acted", note, 10.1), _row("surplus", "free_time", "done", note, 10)]
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == [note]
    assert out[0]["about"] == "free"


@pytest.mark.unit
def test_a_line_that_looks_like_a_secret_never_shows():
    rows = [_row("mind", "self", "acted", "Saved the key sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD for later.", 3),
            _row("mind", "self", "left_alone", "Nothing needs Ellis tonight, the house is calm.", 2)]
    out = screen_thoughts.recent_lines(rows, now=NOW)
    assert [ln["text"] for ln in out] == ["Nothing needs Ellis tonight, the house is calm."]
    assert "sk-proj" not in str(out)


@pytest.mark.unit
def test_odd_rows_are_skipped_not_fatal():
    rows = [None, {}, {"kind": "mind"}, _row("mind", "self", "acted", "A real one.", 1),
            {"kind": "mind", "source": "self", "outcome": "acted", "reason": "no time", "created_at": "garbage"}]
    assert [ln["text"] for ln in screen_thoughts.recent_lines(rows, now=NOW)] == ["A real one."]


# ------------------------------------------------------------ the reader --

async def _add(*rows):
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        for r in rows:
            s.add(LedgerEventModel(kind=r["kind"], source=r["source"], outcome=r["outcome"], reason=r["reason"],
                                   created_at=r["created_at"]))
        await s.commit()


@pytest.mark.integration
async def test_recent_reads_the_durable_rows(clean_db):
    await _add(_row("mind", "evening", "left_alone", "The house is quiet; nothing needs Ellis.", 20),
               _row("decision", "pulse", "changed", "Put up the evening scene.", 10),
               _row("surplus", "free_time", "done", "Drew the week as a little map.", 7),
               _row("mind", "nightly", "left_alone", "Drew the week as a little map.", 5))
    out = await screen_thoughts.recent(now=NOW)
    assert [ln["text"] for ln in out] == ["The house is quiet; nothing needs Ellis.", "Drew the week as a little map."]
    assert out[-1]["about"] == "nightly"


@pytest.mark.integration
async def test_recent_is_cached_for_half_a_minute(clean_db, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(screen_thoughts, "_monotonic", lambda: clock[0])
    await _add(_row("mind", "self", "acted", "First thought.", 5))
    assert [ln["text"] for ln in await screen_thoughts.recent(now=NOW)] == ["First thought."]
    await _add(_row("mind", "self", "acted", "Second thought.", 1))
    clock[0] += screen_thoughts.RECENT_TTL_S - 1
    assert [ln["text"] for ln in await screen_thoughts.recent(now=NOW)] == ["First thought."]
    clock[0] += 2
    assert [ln["text"] for ln in await screen_thoughts.recent(now=NOW)] == ["First thought.", "Second thought."]


@pytest.mark.unit
async def test_recent_is_empty_when_the_database_fails(monkeypatch):
    async def boom(since):
        raise RuntimeError("no database")

    monkeypatch.setattr(screen_thoughts, "_read_recent_rows", boom)
    assert await screen_thoughts.recent(now=NOW) == []


# ------------------------------------------------------------- the route --

@pytest.mark.unit
async def test_the_live_route_carries_recent(monkeypatch):
    from routers import screen as screen_router

    async def fake(now=None):
        return [{"text": "Drew the week as a little map.", "at": NOW.isoformat(), "about": "free", "kind": "surplus"}]

    monkeypatch.setattr(screen_thoughts, "recent", fake)
    out = await screen_router.get_live()
    assert set(out) == {"thinking", "folded", "now", "recent"}
    assert out["recent"][0]["about"] == "free"


@pytest.mark.unit
async def test_the_live_route_still_serves_thinking_when_recent_breaks(monkeypatch):
    from routers import screen as screen_router

    async def boom(now=None):
        raise RuntimeError("no")

    monkeypatch.setattr(screen_thoughts, "recent", boom)
    out = await screen_router.get_live()
    assert out["recent"] == [] and "thinking" in out and "folded" in out


@pytest.mark.unit
async def test_the_live_route_never_500s_with_both_broken(monkeypatch):
    from routers import screen as screen_router

    def boom(*a, **k):
        raise RuntimeError("no")

    async def aboom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(screen_thoughts, "snapshot", boom)
    monkeypatch.setattr(screen_thoughts, "recent", aboom)
    out = await screen_router.get_live()
    assert out["thinking"] is None and out["folded"] == [] and out["recent"] == []
