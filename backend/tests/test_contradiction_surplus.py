"""Contradiction triage as a surplus job (2026-09-24).

``triage_unresolved`` had no caller. The surplus queue runs it daily at
``surplus`` priority with its calls labelled, and a real conflict is asked
as a note on Today (``ask_via="note"``) — never a push: the job may run at
3 a.m. The push path is unchanged for any other caller.
"""

from __future__ import annotations

import json

import pytest

# Both rows are Ellis's own word (his chat turn, user_stated), so the pair is his
# call: only such a pair is asked, or reaches the model (integration review,
# 2026-09-27). Two default rows are a pair the label decides, left for
# consolidation.
OWNERS_WORD = dict(source_type="user_stated",
                 source_ref='{"kind": "extraction", "profile": "chat", "turn_source": "chat"}')


async def _seed_flag():
    from services.database import MemoryFlagModel, MemoryModel, async_session

    async with async_session() as session:
        m1 = MemoryModel(content="Ellis's dentist is Dr. Hale", memory_type="fact", importance=0.6, **OWNERS_WORD)
        m2 = MemoryModel(content="Ellis's dentist is Dr. Park", memory_type="fact", importance=0.6, **OWNERS_WORD)
        session.add_all([m1, m2])
        await session.flush()
        flag = MemoryFlagModel(memory_id=m1.id, flag_type="contradiction", description="two dentists",
                               related_memory_id=m2.id, resolved=False)
        session.add(flag)
        await session.commit()
        return flag.id


class _FakeLLM:
    def __init__(self):
        self.purpose = ""

    async def ainvoke(self, messages):
        class _R:
            content = json.dumps({"classification": "real_conflict", "question": "Is your dentist Dr. Hale or Dr. Park?"})
        return _R()


@pytest.mark.integration
async def test_a_surplus_triage_asks_by_note_and_never_pushes(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts
    from services.notes_service import recent_notes

    flag_id = await _seed_flag()
    fake = _FakeLLM()
    built = []

    def build(**kw):
        built.append(kw)
        return fake

    async def no_push(**kw):
        raise AssertionError("a surplus triage must never push")

    monkeypatch.setattr(cts, "build_background_llm", build)
    monkeypatch.setattr(cts.push_service, "send_push_notification", no_push)

    results = await cts.triage_unresolved(limit=10, priority="surplus", purpose="surplus:contradictions",
                                          ask_via="note")
    assert [r["action"] for r in results] == ["asked_user"]
    assert built[0]["priority"] == "surplus"
    assert fake.purpose == "surplus:contradictions"
    notes = await recent_notes(hours=1)
    assert len(notes) == 1
    assert "Dr. Hale or Dr. Park" in notes[0].body and notes[0].needs_you is True
    assert flag_id in (notes[0].url or "")
    # asked once: the next run waits for Ellis
    again = await cts.triage_unresolved(limit=10, priority="surplus", ask_via="note")
    assert [r["action"] for r in again] == ["waiting_for_user"]


@pytest.mark.integration
async def test_the_default_caller_still_pushes_within_quiet_hours(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts

    await _seed_flag()
    pushed = []

    async def push(**kw):
        pushed.append(kw)

    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: _FakeLLM())
    monkeypatch.setattr(cts.push_service, "send_push_notification", push)
    results = await cts.triage_unresolved(limit=5)
    assert [r["action"] for r in results] == ["asked_user"]
    assert pushed and pushed[0]["respect_quiet_hours"] is True


@pytest.mark.integration
async def test_a_pair_with_none_of_owners_words_is_kept_without_a_question(clean_db, monkeypatch):
    """Decision (b), 2026-09-27: a side written while Ellis was not speaking,
    and neither side his own word — Edward keeps both rows as they are and
    closes the flag. No model call, no note, no memory touched."""
    from sqlalchemy import select

    from services import contradiction_triage_service as cts
    from services.database import MemoryFlagModel, MemoryModel, async_session
    from services.notes_service import recent_notes

    wake_ref = json.dumps({"kind": "extraction", "profile": "mind", "turn_source": "mind"})
    async with async_session() as session:
        m1 = MemoryModel(content="Edward congratulated Parker at Ellis's request", memory_type="fact", importance=0.6,
                         origin="did", about="self", source_ref=wake_ref)
        m2 = MemoryModel(content="Edward stayed out of Parker's thread", memory_type="fact", importance=0.6,
                         origin="did", about="self", source_type="user_stated")
        session.add_all([m1, m2])
        await session.flush()
        flag = MemoryFlagModel(memory_id=m1.id, flag_type="contradiction", description="congratulated vs stayed out",
                               related_memory_id=m2.id, resolved=False)
        # the same pair flagged again by a later cycle (before one flag per pair)
        dup = MemoryFlagModel(memory_id=m2.id, flag_type="contradiction", description="stayed out vs congratulated",
                              related_memory_id=m1.id, resolved=False)
        session.add_all([flag, dup])
        await session.commit()
        flag_id, dup_id, ids = flag.id, dup.id, (m1.id, m2.id)

    def no_llm(**kw):
        raise AssertionError("keeping both needs no model call")

    monkeypatch.setattr(cts, "build_background_llm", no_llm)

    results = await cts.triage_unresolved(limit=10, priority="surplus", purpose="surplus:contradictions",
                                          ask_via="note")

    assert [r["action"] for r in results] == ["kept_both", "kept_both"], "the pair's verdict closes both flags"
    assert await recent_notes(hours=1) == []
    async with async_session() as session:
        flags = {f.id: f for f in (await session.execute(select(MemoryFlagModel))).scalars().all()}
        mems = {m.id: m for m in (await session.execute(select(MemoryModel))).scalars().all()}
    assert all(flags[i].resolved is True and "[kept both]" in flags[i].description for i in (flag_id, dup_id))
    assert all(mems[i].valid_until is None for i in ids)
    assert mems[ids[0]].content == "Edward congratulated Parker at Ellis's request"


@pytest.mark.integration
async def test_owners_own_word_against_a_wake_row_is_still_asked(clean_db, monkeypatch):
    """Ellis's own word is a row from his chat turn. It was a pin until the
    integration review (2026-09-27): a pin is settled, never asked about
    again (tests/test_contradiction_settled_rows.py)."""
    from services import contradiction_triage_service as cts
    from services.database import MemoryFlagModel, MemoryModel, async_session

    wake_ref = json.dumps({"kind": "extraction", "profile": "mind", "turn_source": "mind"})
    async with async_session() as session:
        m1 = MemoryModel(content="Ellis's dentist is Dr. Hale", memory_type="fact", importance=0.6,
                         source_ref=json.dumps({"kind": "extraction", "profile": "chat", "turn_source": "chat"}))
        m2 = MemoryModel(content="Ellis's dentist is Dr. Park", memory_type="fact", importance=0.6,
                         origin="read", source_ref=wake_ref)
        session.add_all([m1, m2])
        await session.flush()
        session.add(MemoryFlagModel(memory_id=m1.id, flag_type="contradiction", description="two dentists",
                                    related_memory_id=m2.id, resolved=False))
        await session.commit()
    monkeypatch.setattr(cts, "build_background_llm", lambda **kw: _FakeLLM())

    results = await cts.triage_unresolved(limit=10, priority="surplus", ask_via="note")

    assert [r["action"] for r in results] == ["asked_user"]


# The triage asks, or spends a model call, only on a pair that is Ellis's call
# (integration review, 2026-09-27). It short-circuited only "keep both", so a
# pair a label decides — none of Ellis's words, one side outranking the other —
# went to the model and became a "Which is right?" note. On the live store the
# first such pair was a dated episode against an alerts rule, both
# read rows from edward-mind; the next consolidation cycle then settled the
# pair and left the note pointing at a closed flag.

@pytest.mark.integration
async def test_a_pair_a_label_decides_is_left_for_consolidation_without_a_question(clean_db, monkeypatch):
    from sqlalchemy import select

    from services import contradiction_triage_service as cts
    from services.database import MemoryFlagModel, MemoryModel, async_session
    from services.notes_service import recent_notes

    async with async_session() as session:
        m1 = MemoryModel(content="Ellis's dentist is Dr. Hale", memory_type="fact", importance=0.6,
                         origin="read", source_type="inferred")
        m2 = MemoryModel(content="Ellis's dentist is Dr. Park", memory_type="fact", importance=0.6,
                         origin="read", source_type="user_stated")
        session.add_all([m1, m2])
        await session.flush()
        flag = MemoryFlagModel(memory_id=m1.id, flag_type="contradiction", description="two dentists",
                               related_memory_id=m2.id, resolved=False)
        session.add(flag)
        await session.commit()
        flag_id, ids = flag.id, (m1.id, m2.id)

    def no_llm(**kw):
        raise AssertionError("a pair the label decides costs no model call")

    monkeypatch.setattr(cts, "build_background_llm", no_llm)

    results = await cts.triage_unresolved(limit=10, priority="surplus", purpose="surplus:contradictions",
                                          ask_via="note")

    assert [r["action"] for r in results] == ["left_for_consolidation"]
    assert await recent_notes(hours=1) == []
    async with async_session() as session:
        f = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id))).scalar_one()
        mems = {m.id: m for m in (await session.execute(select(MemoryModel))).scalars().all()}
    assert f.resolved is False, "consolidation settles it; the triage leaves it as it is"
    assert cts.asked_at(f.description) is None
    assert all(mems[i].valid_until is None for i in ids)


@pytest.mark.integration
async def test_a_flag_with_one_side_is_closed_without_a_question(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts
    from services.database import MemoryFlagModel, MemoryModel, async_session
    from services.notes_service import recent_notes

    async with async_session() as session:
        m1 = MemoryModel(content="Ellis's dentist is Dr. Hale", memory_type="fact", importance=0.6)
        session.add(m1)
        await session.flush()
        session.add(MemoryFlagModel(memory_id=m1.id, flag_type="contradiction", description="alone",
                                    resolved=False))
        await session.commit()

    def no_llm(**kw):
        raise AssertionError("nothing to ask about")

    monkeypatch.setattr(cts, "build_background_llm", no_llm)

    results = await cts.triage_unresolved(limit=10, priority="surplus", ask_via="note")

    assert [r["action"] for r in results] == ["missing_resolved"]
    assert await recent_notes(hours=1) == []
