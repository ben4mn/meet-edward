"""A contradiction between two things Ellis said stays open until Ellis decides.

All 870 flags on 2026-09-05 were marked resolved. ``resolve_contradictions``
picked a winner by source_type rank, then reinforcement_count, and retired
the loser; if either side was missing it marked the flag resolved anyway.
Ellis never saw one. The pet-medication contradiction ("no medication is
scheduled for any pet" vs "Biscuit needs steroids every morning") was settled by
rank, not truth.

Rules now:
  * ranks differ (user_stated vs inferred)  -> auto-resolve, as before
  * both user_stated                         -> no pick by rank:
      - a side is Ellis's own word             -> stays open, marked for Ellis
      - neither side is                      -> keep_both (decision (b),
                                                2026-09-27): nothing retired,
                                                so the pet-medication mistake
                                                cannot repeat
  * one side missing or lapsed               -> moot: closed, nothing retired
                                               (2026-09-27, followups M5;
                                               tests/test_consolidation_flags.py)
  * other ties (inferred vs inferred)        -> auto by reinforcement, as before
  * a side written while Ellis was not speaking -> no verdict by label
    (a wake, a scheduled event, a heartbeat   (2026-09-27: the label is the
    thread, a worker — by its source_ref,      turn's, not who said it):
    or by its thread's source; a row the
    audit moved off heard)
      - a side is Ellis's own word               -> stays open, marked for Ellis
      - neither side is                        -> keep_both: the flag closes
                                                  "[kept both]", no memory is
                                                  touched (decision (b))
  * the loser by label is sensitive or        -> never retired: open when a
    knowledge tier                               side is Ellis's own word, else
                                                 keep_both (integration review,
                                                 2026-09-27)
  * a side is a pin Ellis confirmed              -> keep_both, never asked
                                                 (tests/test_contradiction_
                                                 settled_rows.py)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select


class _M:
    def __init__(self, id, source_type, reinforcement_count=0):
        self.id = id
        self.source_type = source_type
        self.reinforcement_count = reinforcement_count


def test_rank_mismatch_auto_resolves_for_the_higher_rank():
    from services.consolidation_service import decide_contradiction

    verdict = decide_contradiction(_M("a", "inferred"), _M("b", "user_stated"))
    assert verdict == ("auto", "b", "a")


def test_a_user_stated_tie_with_owners_own_word_stays_open():
    """Ellis's own word here is a row from his chat turn. It was a pin
    (``owner_confirmed``) until the integration review (2026-09-27): a pin is
    settled now, and kept against any row without a question
    (``tests/test_contradiction_settled_rows.py``)."""
    from services.consolidation_service import decide_contradiction

    owners = _M("a", "user_stated", 5)
    owners.source_ref = '{"kind": "extraction", "profile": "chat", "turn_source": "chat"}'
    verdict = decide_contradiction(owners, _M("b", "user_stated", 1))
    assert verdict[0] == "open"
    assert "user-stated" in verdict[1]


def test_a_user_stated_tie_with_none_of_owners_words_is_kept():
    """Two legacy user_stated rows, neither Ellis's own word — mostly the
    heartbeat reads and reminder echoes the label was stamped on. Of the 47
    pairs waiting for Ellis on 2026-09-27, 42 were such ties and none held
    his own words."""
    from services.consolidation_service import decide_contradiction

    verdict = decide_contradiction(_M("a", "user_stated", 5), _M("b", "user_stated", 1))
    assert verdict[0] == "keep_both"


def test_inferred_tie_still_auto_resolves_by_reinforcement():
    from services.consolidation_service import decide_contradiction

    assert decide_contradiction(_M("a", "inferred", 1), _M("b", "inferred", 4)) == ("auto", "b", "a")


def test_missing_side_is_moot():
    from services.consolidation_service import decide_contradiction

    verdict = decide_contradiction(_M("a", "user_stated"), None)
    assert verdict[0] == "moot" and "missing" in verdict[1]


async def _seed(content: str, source_type: str, valid_until=None, conversation=None) -> str:
    from services.memory_service import Memory, store_memory

    m = await store_memory(Memory(id=None, content=content, memory_type="fact", importance=0.8,
                                  source_type=source_type, confidence=0.9, valid_until=valid_until,
                                  source_conversation_id=conversation))
    return m.id


async def _flag(a: str, b: str | None) -> str:
    from services.database import async_session, MemoryFlagModel
    import uuid

    fid = str(uuid.uuid4())
    async with async_session() as session:
        session.add(MemoryFlagModel(id=fid, memory_id=a, flag_type="contradiction",
                                    description="One says no meds, the other says steroids daily", related_memory_id=b))
        await session.commit()
    return fid


async def _flag_row(fid: str):
    from services.database import async_session, MemoryFlagModel
    async with async_session() as session:
        return (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == fid))).scalar_one()


async def _valid_until(mid: str):
    from services.database import async_session, MemoryModel
    async with async_session() as session:
        return (await session.execute(select(MemoryModel.valid_until).where(MemoryModel.id == mid))).scalar_one()


async def test_user_stated_vs_user_stated_stays_open_in_db(clean_db):
    from services.consolidation_service import resolve_contradictions
    from services.conversation_service import create_conversation

    await create_conversation("conv-owner", title="Ellis", source="user")
    a = await _seed("No medication is scheduled for any pet", "user_stated")
    b = await _seed("Biscuit needs a steroid every morning", "user_stated", conversation="conv-owner")
    fid = await _flag(a, b)

    resolved = await resolve_contradictions()
    await resolve_contradictions()  # a second cycle must not stack the note

    assert resolved == 0
    row = await _flag_row(fid)
    assert row.resolved is False
    assert row.description.count("[needs Ellis]") == 1
    assert await _valid_until(a) is None and await _valid_until(b) is None


async def test_a_user_stated_tie_with_none_of_owners_words_is_kept_in_db(clean_db):
    from services.consolidation_service import resolve_contradictions

    a = await _seed("No medication is scheduled for any pet", "user_stated")
    b = await _seed("Biscuit needs a steroid every morning", "user_stated")
    fid = await _flag(a, b)

    assert await resolve_contradictions() == 1
    row = await _flag_row(fid)
    assert row.resolved is True and "[kept both]" in row.description
    assert await _valid_until(a) is None and await _valid_until(b) is None, "nothing is retired"


async def test_rank_mismatch_still_auto_resolves_in_db(clean_db):
    from services.consolidation_service import resolve_contradictions

    a = await _seed("Ellis's dog is named Cricket", "inferred")
    b = await _seed("Ellis's dog is named Biscuit", "user_stated")
    fid = await _flag(a, b)

    assert await resolve_contradictions() == 1
    row = await _flag_row(fid)
    assert row.resolved is True and row.resolved_at is not None
    assert await _valid_until(a) is not None, "the inferred side is retired"
    assert await _valid_until(b) is None


async def test_missing_side_closes_the_flag_and_keeps_the_memory(clean_db):
    from services.consolidation_service import resolve_contradictions

    a = await _seed("Ellis drinks oat milk", "user_stated")
    fid = await _flag(a, "does-not-exist")

    assert await resolve_contradictions() == 1
    row = await _flag_row(fid)
    assert row.resolved is True
    assert await _valid_until(a) is None


# A side written in a turn where Ellis was not speaking (pre-deploy review,
# 2026-09-27). Extraction under the mind and scheduled_event profiles now
# turns every user_stated label into inferred (and an instruction from any
# turn that is not Ellis's own too), so rank said nothing about who was right:
# a wake that read Ellis's newer text ("go ahead and answer Theo") lost by rank
# to the older heard row it was kept beside, and to ~1,100 legacy heartbeat
# reads still labelled user_stated, and was retired within the hour with no
# question to Ellis. Two such rows, which no longer reinforce, were settled
# by an arbitrary pick. Such a pair is Ellis's call when a side is his own
# word; otherwise Edward keeps both rows exactly as they are and closes the
# flag (decision (b), 2026-09-27): of 106 new pairs in ten days, 64 had a
# side written while Ellis was not speaking and none was his own word, so
# asking him meant a daily question about nothing he said.

import json


class _R(_M):
    def __init__(self, id, source_type, source_ref=None, origin="heard", about="owner", reinforcement_count=0):
        super().__init__(id, source_type, reinforcement_count)
        self.source_ref = json.dumps(source_ref) if source_ref else None
        self.origin = origin
        self.about = about


WAKE_REF = {"kind": "extraction", "profile": "mind", "turn_source": "mind", "conversation_id": "edward-mind"}


def test_a_wake_read_row_against_a_legacy_user_stated_read_is_kept_not_retired():
    from services.consolidation_service import decide_contradiction

    legacy = _R("old", "user_stated", origin="read")  # an August heartbeat read, no provenance
    wake = _R("new", "inferred", source_ref=WAKE_REF, origin="read")
    for pair in ((legacy, wake), (wake, legacy)):
        verdict = decide_contradiction(*pair)
        assert verdict[0] == "keep_both", verdict


def test_a_heard_row_from_a_reminder_turn_against_a_wake_is_kept():
    """heard/owner is only a label: this one was written in a scheduled
    event's turn, so it is not Ellis's own word."""
    from services.consolidation_service import decide_contradiction

    echo = _R("theo", "user_stated", source_ref={"kind": "extraction", "turn_source": "scheduled_event",
                                                 "profile": "scheduled_event"})
    wake = _R("go-ahead", "inferred", source_ref=WAKE_REF, origin="read")
    assert decide_contradiction(echo, wake)[0] == "keep_both"


def test_owners_own_word_against_a_wake_is_asked():
    from services.consolidation_service import decide_contradiction

    wake = _R("go-ahead", "inferred", source_ref=WAKE_REF, origin="read")
    chat = _R("chat", "user_stated", source_ref={"kind": "extraction", "profile": "chat", "turn_source": "chat"})
    assert decide_contradiction(chat, wake)[0] == "open"
    assert decide_contradiction(wake, chat)[0] == "open"
    # A pin was asked about too, until the integration review (2026-09-27):
    # Ellis confirmed it at Checkpoint 1, so it is settled — kept, never put to
    # him again (tests/test_contradiction_settled_rows.py).
    confirmed = _R("pinned", "user_stated", source_ref={"kind": "owner_confirmed"})
    assert decide_contradiction(confirmed, wake)[0] == "keep_both"


def test_his_own_chat_counts_by_the_conversation_source():
    """A legacy heard row with no provenance is Ellis's own word when its
    conversation is one of his (``conversations.source == "user"``)."""
    from services.consolidation_service import decide_contradiction

    legacy = _R("said", "user_stated")
    legacy.source_conversation_id = "conv-owner"
    wake = _R("wake", "inferred", source_ref=WAKE_REF, origin="read")
    assert decide_contradiction(legacy, wake)[0] == "keep_both"
    assert decide_contradiction(legacy, wake, conversation_sources={"conv-owner": "user"})[0] == "open"
    assert decide_contradiction(legacy, wake, conversation_sources={"conv-owner": "scheduled_event"})[0] == "keep_both"


def test_two_wake_rows_are_kept_not_picked_arbitrarily():
    from services.consolidation_service import decide_contradiction

    a = _R("a", "inferred", source_ref=WAKE_REF, origin="read")
    b = _R("b", "inferred", source_ref=dict(WAKE_REF, profile="scheduled_event", turn_source="scheduled_event"),
           origin="thought", about="self")
    assert decide_contradiction(a, b)[0] == "keep_both"


def test_a_row_from_any_turn_that_is_not_owners_is_asked():
    from services.consolidation_service import decide_contradiction

    chat = _R("chat", "user_stated", source_ref={"kind": "extraction", "profile": "chat", "turn_source": "chat"})
    for turn in ("heartbeat", "worker", "twilio", "pulse"):
        other = _R(turn, "inferred", source_ref={"kind": "extraction", "turn_source": turn}, origin="read")
        assert decide_contradiction(chat, other)[0] == "open", turn


def test_rank_still_decides_between_owners_own_turns_and_unrecorded_rows():
    from services.consolidation_service import decide_contradiction

    chat = _R("chat", "user_stated", source_ref={"kind": "extraction", "profile": "chat", "turn_source": "chat"})
    voice = _R("voice", "inferred", source_ref={"kind": "extraction", "turn_source": "voice"})
    assert decide_contradiction(chat, voice) == ("auto", "chat", "voice")
    legacy = _R("legacy", "inferred")
    assert decide_contradiction(legacy, chat) == ("auto", "chat", "legacy")


async def test_a_wake_row_flagged_against_a_legacy_read_is_kept_in_db(clean_db):
    from services.consolidation_service import resolve_contradictions
    from services.memory_service import Memory, store_memory

    old = (await store_memory(Memory(id=None, content="Rowan's appointment is at 3pm", memory_type="fact",
                                     importance=0.8, source_type="user_stated", confidence=0.9,
                                     origin="read", about="contact"))).id
    new = (await store_memory(Memory(id=None, content="Rowan's appointment moved to 4pm", memory_type="fact",
                                     importance=0.8, source_type="inferred", confidence=0.8,
                                     origin="read", about="contact", source_ref=json.dumps(WAKE_REF)))).id
    fid = await _flag(old, new)
    await _mark_needs_owner(fid)  # an earlier cycle left it for Ellis

    assert await resolve_contradictions() == 1
    row = await _flag_row(fid)
    assert row.resolved is True and row.resolved_at is not None
    assert "[kept both]" in row.description
    assert "[needs Ellis]" not in row.description, "nothing is left for Ellis"
    assert await _valid_until(old) is None and await _valid_until(new) is None
    assert await resolve_contradictions() == 0, "a kept flag is closed once"


async def test_owners_own_chat_against_a_wake_row_stays_open_in_db(clean_db):
    from services.consolidation_service import resolve_contradictions
    from services.conversation_service import create_conversation
    from services.memory_service import Memory, store_memory

    await create_conversation("conv-owner", title="Ellis", source="user")
    said = (await store_memory(Memory(id=None, content="Do not answer Theo for me", memory_type="instruction",
                                      importance=0.9, source_type="user_stated", confidence=0.9,
                                      origin="heard", about="owner", source_conversation_id="conv-owner"))).id
    wake = (await store_memory(Memory(id=None, content="Edward may answer Theo", memory_type="instruction",
                                      importance=0.8, source_type="inferred", confidence=0.8,
                                      origin="read", about="owner", source_ref=json.dumps(WAKE_REF)))).id
    fid = await _flag(said, wake)

    assert await resolve_contradictions() == 0
    row = await _flag_row(fid)
    assert row.resolved is False and row.description.count("[needs Ellis]") == 1
    assert "[kept both]" not in row.description
    assert await _valid_until(said) is None and await _valid_until(wake) is None


async def _mark_needs_owner(fid: str) -> None:
    from services.consolidation_service import _mark_flag_for_owner

    await _mark_flag_for_owner(fid, "a side was written while Ellis was not speaking")


# One pass decides every flag on the rows as they are when that flag is
# decided (review, 2026-09-27). Reading every flag's rows before deciding any
# let a row an earlier flag had just retired still win or lose a later one:
# two flags on one pair in swapped order (the old per-cycle duplicates)
# retired both sides, and a row already retired could retire a third.

async def test_duplicate_flags_on_one_pair_in_swapped_order_retire_one_side(clean_db):
    from services.consolidation_service import resolve_contradictions

    a = await _seed("The garage code is 4412", "inferred")
    b = await _seed("The garage code is 5521", "inferred")
    await _flag(a, b)
    await _flag(b, a)  # the same pair flagged again by a later cycle

    await resolve_contradictions()

    va, vb = await _valid_until(a), await _valid_until(b)
    assert (va is None) != (vb is None), f"exactly one side is retired; got a={va} b={vb}"


async def test_a_side_retired_earlier_in_the_pass_makes_the_next_flag_moot(clean_db):
    from services.consolidation_service import resolve_contradictions

    a = await _seed("Ellis's dentist is Dr. Novak", "inferred")
    b = await _seed("Ellis's dentist is Dr. Park", "user_stated")
    c = await _seed("Ellis's dentist moved to Denver", "inferred")
    await _flag(a, b)  # b outranks a: a is retired
    await _flag(a, c)  # a rank tie that would keep a and retire c

    await resolve_contradictions()

    assert await _valid_until(a) is not None
    assert await _valid_until(b) is None
    assert await _valid_until(c) is None, "c never loses to a row the same pass retired"
