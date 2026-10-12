"""One "Which is right?" question on Today at a time (2026-09-27).

The memory audit found two identical "Which is right?" notes written in the
same minute: the daily triage walked the open flags oldest first, and
duplicate flags of one memory pair (one pair had 55) each became the same
question. Today shows every note sharing a title as one card — the newest
question, "Done" clears the rest — so a second live question is never seen,
yet its flag is marked asked and closed as stale a week later, unanswered.

Now a question is written only when no other "Which is right?" note is live
and still waiting on Ellis, and a pair is asked about once: a flag whose pair
was already asked waits with it. A question that cannot be written costs no
LLM call and leaves its flag unasked for a later run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update


class _Conflict:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        n = self.calls

        class _R:
            content = json.dumps({"classification": "real_conflict", "question": f"Which is current? ({n})"})
        return _R()


# Both rows are Ellis's own word (his chat turn, user_stated), so the pair is his
# call: only such a pair is asked, or reaches the model (integration review,
# 2026-09-27). Two default rows are a pair the label decides, left for
# consolidation.
OWNERS_WORD = dict(source_type="user_stated",
                 source_ref='{"kind": "extraction", "profile": "chat", "turn_source": "chat"}')


async def _memory(content: str) -> str:
    from services.database import MemoryModel, async_session

    async with async_session() as session:
        m = MemoryModel(content=content, memory_type="fact", importance=0.6, **OWNERS_WORD)
        session.add(m)
        await session.commit()
        return m.id


async def _flag(a: str, b: str, created_at: datetime | None = None) -> str:
    from services.database import MemoryFlagModel, async_session

    async with async_session() as session:
        f = MemoryFlagModel(memory_id=a, related_memory_id=b, flag_type="contradiction",
                            description="conflict", resolved=False)
        if created_at is not None:
            f.created_at = created_at
        session.add(f)
        await session.commit()
        return f.id


async def _flag_row(fid: str):
    from services.database import MemoryFlagModel, async_session

    async with async_session() as session:
        return (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == fid))).scalar_one()


async def _live_questions():
    from services.notes_service import recent_notes

    return [n for n in await recent_notes(hours=48, unexpired_only=True) if n.title == "Which is right?"]


def _patch(cts, monkeypatch) -> _Conflict:
    llm = _Conflict()

    async def no_push(**kw):
        raise AssertionError("a note-path triage never pushes")

    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: llm)
    monkeypatch.setattr(cts.push_service, "send_push_notification", no_push)
    return llm


async def _run(cts):
    return await cts.triage_unresolved(limit=10, priority="surplus", purpose="surplus:contradictions",
                                       ask_via="note", max_asks=3)


@pytest.mark.integration
async def test_duplicate_flags_of_one_pair_become_one_question(clean_db, monkeypatch):
    """The audit's two identical notes in one minute."""
    from services import contradiction_triage_service as cts

    a = await _memory("Edward speaks only as Edward")
    b = await _memory("Edward sends Ellis in-app reminders only")
    older = datetime.utcnow() - timedelta(days=3)
    first = await _flag(a, b, created_at=older)
    second = await _flag(b, a, created_at=older + timedelta(minutes=1))
    llm = _patch(cts, monkeypatch)

    await _run(cts)

    assert len(await _live_questions()) == 1, "one question for one pair"
    assert llm.calls == 1, "the duplicate costs no LLM call"
    asked = [fid for fid in (first, second) if cts.asked_at((await _flag_row(fid)).description)]
    assert len(asked) == 1


@pytest.mark.integration
async def test_a_second_question_waits_while_one_is_live(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts

    f1 = await _flag(await _memory("Ellis's dentist is Dr. Novak"), await _memory("Ellis's dentist is Dr. Park"))
    f2 = await _flag(await _memory("Ellis lives in Springfield"), await _memory("Ellis lives in Denver"))
    llm = _patch(cts, monkeypatch)

    results = await _run(cts)

    assert len(await _live_questions()) == 1, "Today shows one 'Which is right?' card; a second is never seen"
    assert llm.calls == 1
    assert sorted(r["action"] for r in results) == ["asked_user", "deferred"]
    unasked = [fid for fid in (f1, f2) if cts.asked_at((await _flag_row(fid)).description) is None]
    assert len(unasked) == 1, "the deferred flag is not marked asked, so a later run can ask it"

    # The next day's run, with the first question still live, asks nothing new.
    await _run(cts)
    assert len(await _live_questions()) == 1 and llm.calls == 1


@pytest.mark.integration
async def test_once_owner_answers_the_next_question_can_be_asked(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts
    from services.database import MemoryFlagModel, async_session

    await _flag(await _memory("Ellis's dentist is Dr. Novak"), await _memory("Ellis's dentist is Dr. Park"))
    await _flag(await _memory("Ellis lives in Springfield"), await _memory("Ellis lives in Denver"))
    _patch(cts, monkeypatch)
    await _run(cts)
    (live,) = await _live_questions()
    answered = live.url.split("flag=")[1].split("&")[0]
    async with async_session() as session:
        await session.execute(update(MemoryFlagModel).where(MemoryFlagModel.id == answered)
                              .values(resolved=True, resolved_at=datetime.utcnow()))
        await session.commit()

    results = await _run(cts)

    assert "asked_user" in [r["action"] for r in results]
    assert len(await _live_questions()) == 2, "the answered question no longer holds the slot"


@pytest.mark.integration
async def test_a_pair_already_asked_waits_after_its_note_expires(clean_db, monkeypatch):
    """The note lives a day; the flag waits a week for Ellis's answer. A
    duplicate flag of the same pair must not ask him again on day two."""
    from services import contradiction_triage_service as cts
    from services.database import EdwardNoteModel, async_session

    a = await _memory("Edward speaks only as Edward")
    b = await _memory("Edward sends Ellis in-app reminders only")
    await _flag(a, b)
    _patch(cts, monkeypatch)
    await _run(cts)
    async with async_session() as session:
        await session.execute(update(EdwardNoteModel).values(expires_at=datetime.utcnow() - timedelta(minutes=1)))
        await session.commit()
    dup = await _flag(b, a)

    results = await _run(cts)

    assert [r["action"] for r in results] == ["waiting_for_user"]
    assert cts.asked_at((await _flag_row(dup)).description) is None
    assert await _live_questions() == []


@pytest.mark.integration
async def test_a_question_asked_while_the_model_thinks_defers_this_one(clean_db, monkeypatch):
    """Two runs at once both pass the check before the model call; the one
    whose model answers second must not write a second question. Only the
    check after the model's answer catches that — a sequential run never
    reaches it, so nothing else here would notice it gone."""
    from services import contradiction_triage_service as cts

    other = await _flag(await _memory("Ellis lives in Springfield"), await _memory("Ellis lives in Denver"))
    mine = await _flag(await _memory("Ellis's dentist is Dr. Novak"), await _memory("Ellis's dentist is Dr. Park"))
    llm = _patch(cts, monkeypatch)
    answer = llm.ainvoke

    async def racing(messages):
        # The other run's question lands on Today while this model thinks.
        await cts._note_disambiguation({"question": "Springfield or Denver?"}, await _flag_row(other))
        await cts._record_asked(other, datetime.utcnow())
        return await answer(messages)

    llm.ainvoke = racing

    result = await cts.triage_flag(mine, priority="surplus", purpose="surplus:contradictions", ask_via="note")

    assert result["action"] == "deferred"
    assert len(await _live_questions()) == 1, "one question on Today"
    assert cts.asked_at((await _flag_row(mine)).description) is None, "a later run can still ask it"
