"""R/G TDD red for C Phase 1 (temporal edges).

A memory's ``valid_until`` marks when a belief stopped being true. When
consolidation decides a memory has been superseded (e.g. "Ellis drinks coffee
every morning" vs. a later "Ellis quit coffee in April"), it should stamp
``valid_until`` on the older memory rather than deleting it. Retrieval
then filters out memories whose ``valid_until`` is in the past.

Contract:
  * ``MemoryModel`` has columns ``valid_from`` and ``valid_until`` (nullable
    DateTime).
  * ``retrieve_memories`` excludes memories where ``valid_until`` is set and
    in the past.
  * ``retrieve_memories`` still returns memories where ``valid_until`` is NULL
    (default — still valid) or in the future.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def test_memory_model_has_valid_from_column():
    from services.database import MemoryModel

    assert hasattr(MemoryModel, "valid_from"), (
        "MemoryModel must have a ``valid_from`` DateTime column so the "
        "consolidation pass can mark when a belief began."
    )


def test_memory_model_has_valid_until_column():
    from services.database import MemoryModel

    assert hasattr(MemoryModel, "valid_until"), (
        "MemoryModel must have a ``valid_until`` DateTime column so the "
        "consolidation pass can retire superseded beliefs."
    )


@pytest.mark.integration
async def test_expired_memory_excluded_from_retrieval(clean_db):
    """Memories with ``valid_until`` in the past are not returned by
    ``retrieve_memories``."""
    from services.memory_service import Memory, store_memory, retrieve_memories
    from services.database import async_session, MemoryModel
    from sqlalchemy import update

    # Two memories about the same concept, one retired.
    live = await store_memory(Memory(
        id=None,
        content="Ellis drinks oat milk in his morning coffee",
        memory_type="preference",
        importance=0.6,
        confidence=0.9,
    ))
    retired = await store_memory(Memory(
        id=None,
        content="Ellis drinks cream in his morning coffee",
        memory_type="preference",
        importance=0.6,
        confidence=0.9,
    ))

    # Retire the second memory as of 1 hour ago.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    async with async_session() as session:
        await session.execute(
            update(MemoryModel)
            .where(MemoryModel.id == retired.id)
            .values(valid_until=past.replace(tzinfo=None))
        )
        await session.commit()

    results = await retrieve_memories(
        query="What does Ellis put in his coffee?",
        limit=20,
        min_score=0.0,  # don't filter by score in this test
    )
    ids_returned = {m.id for m in results}

    assert live.id in ids_returned, (
        "Memory with no valid_until must still be returned. "
        f"Got: {ids_returned}"
    )
    assert retired.id not in ids_returned, (
        "Memory with valid_until in the past MUST be filtered out. "
        f"Got: {ids_returned}"
    )


@pytest.mark.integration
async def test_future_valid_until_does_not_exclude_memory(clean_db):
    """A memory with ``valid_until`` in the future should still be
    returned — it's still in its valid window."""
    from services.memory_service import Memory, store_memory, retrieve_memories
    from services.database import async_session, MemoryModel
    from sqlalchemy import update

    mem = await store_memory(Memory(
        id=None,
        content="Ellis is subscribed to the premium plan through next year",
        memory_type="fact",
        importance=0.7,
        confidence=0.9,
    ))

    future = datetime.now(timezone.utc) + timedelta(days=365)
    async with async_session() as session:
        await session.execute(
            update(MemoryModel)
            .where(MemoryModel.id == mem.id)
            .values(valid_until=future.replace(tzinfo=None))
        )
        await session.commit()

    results = await retrieve_memories(
        query="What subscription does Ellis have?",
        limit=20,
        min_score=0.0,
    )
    assert mem.id in {m.id for m in results}, (
        "Memory with valid_until in the future must still be returned."
    )


# ---------------------------------------------------------------------------
# 2026-09-27: an UPDATE carries the new words' lifetime (memory audit,
# preferences)
# ---------------------------------------------------------------------------
#
# Between 0.75 and 0.92 similarity the conflict resolver can answer UPDATE,
# and ``store_memories_with_conflict_detection`` wrote the incoming row's
# content and ``memory_type`` into the existing row — but neither its
# ``temporal_nature`` nor its ``valid_until``. "Rowan wanted salmon
# nigiri tonight" written over "Rowan likes salmon nigiri" kept
# the old row's open-ended, evolving life; the reverse kept a lasting
# taste on a one-off's 30-day horizon.

ROWAN_TASTE = "Rowan likes salmon nigiri"


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


async def _seed_rowan(**over):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    fields = dict(content=ROWAN_TASTE, memory_type="preference", importance=0.6, origin="read",
                  about="contact", source_type="inferred", confidence=0.8, temporal_nature="evolving",
                  embedding=get_embedding(ROWAN_TASTE), source_conversation_id="hb-rowan",
                  source_ref=_ref(conversation_id="hb-rowan", turn_source="heartbeat", profile="heartbeat"))
    fields.update(over)
    async with async_session() as s:
        row = MemoryModel(**fields)
        s.add(row)
        await s.commit()
        return row.id


def _resolver_updates(monkeypatch, seed_id):
    from services import memory_service
    from services.memory_service import ConflictResolution

    async def _similar(content, threshold=0.75, limit=5, **kw):
        return [(await memory_service.get_memory_by_id(seed_id), 0.85)]

    async def _resolve(existing, memory, sim):
        return ConflictResolution(action="UPDATE", reason="newer")

    monkeypatch.setattr(memory_service, "find_similar_memories", _similar)
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _resolve)


async def _update_with(monkeypatch, seed_id, content, **over):
    from services.database import MemoryModel, async_session
    from services.memory_service import Memory, store_memories_with_conflict_detection

    _resolver_updates(monkeypatch, seed_id)
    fields = dict(id=None, content=content, memory_type="preference", importance=0.5, origin="read",
                  about="contact", source_type="inferred", confidence=0.8,
                  source_ref=_ref(conversation_id="hb-rowan", turn_source="heartbeat", profile="heartbeat"))
    fields.update(over)
    await store_memories_with_conflict_detection([Memory(**fields)])
    async with async_session() as s:
        return await s.get(MemoryModel, seed_id)


@pytest.mark.integration
async def test_an_update_to_a_one_off_takes_the_one_offs_horizon(clean_db, monkeypatch):
    seed = await _seed_rowan()
    before = datetime.utcnow()
    row = await _update_with(monkeypatch, seed, "Rowan wanted salmon nigiri for dinner tonight",
                             temporal_nature="temporary")
    assert row.content == "Rowan wanted salmon nigiri for dinner tonight"
    assert row.temporal_nature == "temporary"
    assert row.valid_until is not None, "temporary new words do not inherit an open-ended life"
    assert timedelta(days=29, hours=23) < row.valid_until - before <= timedelta(days=30, minutes=1)


@pytest.mark.integration
async def test_an_update_keeps_the_window_the_new_words_name(clean_db, monkeypatch):
    seed = await _seed_rowan()
    window = datetime.utcnow().replace(microsecond=0) + timedelta(hours=20)
    row = await _update_with(monkeypatch, seed, "Rowan wants salmon nigiri until her diet starts",
                             temporal_nature="temporary", valid_until=window)
    assert (row.temporal_nature, row.valid_until) == ("temporary", window)


@pytest.mark.integration
async def test_an_update_to_a_lasting_taste_drops_the_one_offs_horizon(clean_db, monkeypatch):
    soon = datetime.utcnow().replace(microsecond=0) + timedelta(days=10)
    seed = await _seed_rowan(content="Rowan wanted salmon nigiri for dinner tonight",
                               temporal_nature="temporary", valid_until=soon)
    row = await _update_with(monkeypatch, seed, ROWAN_TASTE, temporal_nature="evolving")
    assert row.content == ROWAN_TASTE
    assert (row.temporal_nature, row.valid_until) == ("evolving", None)


# The carry is scoped (review of the preferences change, 2026-09-27): it
# first cleared any horizon the new words did not name, whatever the type.
# Ellis's 24-hour stand-down, rewritten by his own next message with no window,
# became open-ended — and his instruction is never lapsed by decay; a wake's
# intention updated from a chat turn lost its 72 hours (or the horizon a
# linked wake_me stretched it to) and sat in "Open intentions" again; a
# caller that names no nature (the dataclass default, "timeless") made a
# two-week note timeless. Only a preference's words set its lifetime either
# way; any other type keeps a window it has, and takes one only when it had
# none.


@pytest.mark.integration
async def test_owners_windowed_stand_down_keeps_its_window_when_his_next_words_name_none(clean_db, monkeypatch):
    window = datetime.utcnow().replace(microsecond=0) + timedelta(hours=24)
    seed = await _seed_rowan(content="Edward should stay out of Parker's thread for 24 hours",
                               memory_type="instruction", origin="heard", about="owner",
                               source_type="user_stated", temporal_nature="temporary", valid_until=window,
                               source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat"))
    row = await _update_with(monkeypatch, seed, "Edward should keep out of Parker's thread for now",
                             memory_type="instruction", origin="heard", about="owner",
                             source_type="user_stated", temporal_nature="temporary",
                             source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat"))
    assert row.content == "Edward should keep out of Parker's thread for now", "Ellis's own turn may rewrite it"
    assert (row.temporal_nature, row.valid_until) == ("temporary", window)


@pytest.mark.integration
@pytest.mark.parametrize("hours", [72, 6 * 24])
async def test_a_wake_intention_updated_from_a_chat_turn_keeps_its_horizon(clean_db, monkeypatch, hours):
    """72 h is the wake's default; six days is what a linked wake_me five
    days out stretches it to (``scheduled_events_service.extended_horizon``)."""
    horizon = datetime.utcnow().replace(microsecond=0) + timedelta(hours=hours)
    seed = await _seed_rowan(content="I decided to watch ACME for Ellis's bonus", memory_type="intention",
                               origin="thought", about="self", temporal_nature="evolving",
                               valid_until=horizon,
                               source_ref=_ref(conversation_id="edward-mind", turn_source="mind", profile="mind"))
    row = await _update_with(monkeypatch, seed, "I decided to watch ACME and tell Ellis if it moves 3%",
                             memory_type="intention", origin="thought", about="self",
                             temporal_nature="evolving",
                             source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat"))
    assert row.content == "I decided to watch ACME and tell Ellis if it moves 3%"
    assert (row.temporal_nature, row.valid_until) == ("evolving", horizon)


@pytest.mark.integration
async def test_a_note_with_no_nature_does_not_make_a_two_week_note_timeless(clean_db, monkeypatch):
    """The email NOTE in ``triage_service`` builds ``Memory(...)`` with no
    ``temporal_nature``: the dataclass default is "timeless"."""
    until = datetime.utcnow().replace(microsecond=0) + timedelta(days=14)
    seed = await _seed_rowan(content="Rowan said the plumber comes Tuesday", memory_type="context",
                               temporal_nature="temporary", valid_until=until)
    row = await _update_with(monkeypatch, seed, "Rowan said the plumber now comes Wednesday",
                             memory_type="context")
    assert row.content == "Rowan said the plumber now comes Wednesday"
    assert (row.temporal_nature, row.valid_until) == ("temporary", until)


@pytest.mark.integration
async def test_temporary_words_over_an_open_ended_row_still_bring_their_horizon(clean_db, monkeypatch):
    seed = await _seed_rowan(content="I am watching ACME for Ellis's bonus", memory_type="intention",
                               origin="thought", about="self", temporal_nature="evolving")
    horizon = datetime.utcnow().replace(microsecond=0) + timedelta(hours=72)
    row = await _update_with(monkeypatch, seed, "I decided to watch ACME until Friday's close",
                             memory_type="intention", origin="thought", about="self",
                             temporal_nature="evolving", valid_until=horizon,
                             source_ref=_ref(conversation_id="edward-mind", turn_source="mind", profile="mind"))
    assert row.valid_until == horizon, "new words with a horizon never inherit an open-ended life"
