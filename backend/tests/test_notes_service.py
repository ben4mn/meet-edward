"""Edward's notes — what the templated services have to say, as inputs.

Why (2026-09-12): the morning briefing, meeting prep, open-loop nudge and
weekly patterns each rang the phone on their own schedule. They are
inputs, not voices: a note lands on Today and in the next wake-up tick's
signal pack, and one judgment decides whether anything reaches the phone.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.notes_service import format_notes_for_pack, note_dict, ttl_hours_for

pytestmark = pytest.mark.unit


def _note(**kw):
    base = dict(
        id="n1", kind="meeting_prep", title="Meeting prep: Dentist",
        body="Bring the insurance card.", url="/today", needs_you=True,
        created_at=datetime(2026, 9, 12, 13, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_two_remaining_kinds_last_a_day():
    # Phase 7: the templated senders (meeting_prep, morning_briefing,
    # weekly_patterns, open_loops) are gone; the mind and whispers remain.
    assert ttl_hours_for("mind") == 24
    assert ttl_hours_for("whisper") == 24
    assert ttl_hours_for("anything_else") == 24


def test_pack_lines_carry_kind_age_title_and_body():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
    text = format_notes_for_pack([_note()], now=now)
    assert "meeting_prep" in text and "2h ago" in text
    assert "Dentist" in text and "insurance" in text
    assert "needs Ellis" in text


def test_pack_is_empty_string_when_nothing_to_say():
    assert format_notes_for_pack([], now=datetime.now(timezone.utc)) == ""


def test_note_dict_has_what_today_needs():
    d = note_dict(_note())
    assert d["kind"] == "meeting_prep" and d["needs_you"] is True
    assert d["created_at"].endswith("Z") and d["href"] == "/today"


@pytest.mark.integration
async def test_leave_note_then_recent_notes_round_trip(clean_db):
    from services.notes_service import leave_note, recent_notes

    nid = await leave_note(kind="open_loops", title="Open items", body="3 things", url="/today")
    assert nid
    rows = await recent_notes(hours=1)
    assert [r.id for r in rows] == [nid]
    assert rows[0].needs_you is False


@pytest.mark.integration
async def test_dismiss_stamps_dismissed_at_beside_the_expiry(clean_db):
    # 2026-10-11: "Done" is a dismissal, not an expiry — the noticing ladder
    # demotes a category only on explicit dismissals (proactivity-spec §2.8).
    from sqlalchemy import select

    from services.database import EdwardNoteModel, async_session
    from services.notes_service import dismiss_notes, leave_note

    a = await leave_note(kind="mind", title="A", body="a")
    b = await leave_note(kind="mind", title="B", body="b")
    untouched = await leave_note(kind="mind", title="C", body="c")
    assert await dismiss_notes(a, also=[b]) == 2
    async with async_session() as s:
        rows = {n.id: n for n in (await s.execute(select(EdwardNoteModel))).scalars().all()}
    assert rows[a].dismissed_at is not None and rows[b].dismissed_at is not None
    assert rows[a].expires_at <= rows[a].dismissed_at + timedelta(seconds=1)
    assert rows[untouched].dismissed_at is None


@pytest.mark.integration
async def test_a_second_dismiss_keeps_the_first_dismissal_time(clean_db):
    from sqlalchemy import select

    from services.database import EdwardNoteModel, async_session
    from services.notes_service import dismiss_notes, leave_note

    a = await leave_note(kind="mind", title="A", body="a")
    await dismiss_notes(a)
    async with async_session() as s:
        first = (await s.execute(select(EdwardNoteModel).where(EdwardNoteModel.id == a))).scalar_one().dismissed_at
    await dismiss_notes(a)
    async with async_session() as s:
        again = (await s.execute(select(EdwardNoteModel).where(EdwardNoteModel.id == a))).scalar_one().dismissed_at
    assert again == first
