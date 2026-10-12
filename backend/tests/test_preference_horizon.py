"""A temporary preference lapses (memory audit, preferences, 2026-09-27).

About 20% of the 332 live preferences were one-offs — "Ellis's food
preference in this conversation was a chicken sandwich", "September 26–27:
low-interruption weekend", Rowan asking for strawberry ice cream "that
night" — and they took 38% of preference retrievals. 67 of the 68 had no
``valid_until``: the default horizon covered instructions only, and the
decay curve gave a used, belief-tier row a half-life of 500–900 days.

When a new row is stored, a ``temporary`` preference without a horizon
gets ``now + TEMPORARY_PREFERENCE_DEFAULT_DAYS`` (30) — whoever said it:
a one-off is a one-off even in Ellis's own words; Ellis stating the taste as
lasting, in his own turn, takes the horizon off. Sensitive content (pets, health, family, money —
``memory_decay_service.is_sensitive``) gets none, so it keeps the decay
pass's hold for Ellis. A taste that lasts is ``timeless`` or ``evolving`` and
never gets one; neither does a preference that names its own window.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

ONE_OFF = "Ellis wanted a chicken sandwich for dinner on September 26, 2026"


@pytest.mark.unit
def test_the_default_is_thirty_days():
    from services.memory_service import TEMPORARY_PREFERENCE_DEFAULT_DAYS

    assert TEMPORARY_PREFERENCE_DEFAULT_DAYS == 30


@pytest.mark.unit
def test_default_valid_until_gives_a_temporary_preference_thirty_days():
    from services.memory_service import default_valid_until

    now = datetime(2026, 9, 27, 12, 0)
    assert default_valid_until("preference", "temporary", None, now=now, content=ONE_OFF) == \
        now + timedelta(days=30)


@pytest.mark.unit
def test_a_one_off_is_a_one_off_even_in_owners_own_words():
    from services.memory_service import default_valid_until

    now = datetime(2026, 9, 27, 12, 0)
    assert default_valid_until("preference", "temporary", None, now=now, owners_own=True, content=ONE_OFF) == \
        now + timedelta(days=30)


@pytest.mark.unit
@pytest.mark.parametrize("nature", ["timeless", "evolving"])
def test_a_lasting_preference_gets_no_horizon(nature):
    from services.memory_service import default_valid_until

    assert default_valid_until("preference", nature, None, content="Ellis dislikes bananas") is None


@pytest.mark.unit
@pytest.mark.parametrize("content", [
    "Rowan wants soup tonight because her throat is sore and she has insomnia",
    "Ellis wants Biscuit's evening pill hidden in cheese this week",
    "Ellis prefers to cover Rowan's rent this month by Zelle",
])
def test_a_sensitive_temporary_preference_keeps_the_decay_hold(content):
    """A write-time horizon would bypass the decay pass's hold for Ellis
    (``memory_decay_service.is_protected``): once a row has a
    ``valid_until`` decay never looks at it again."""
    from services.memory_service import default_valid_until

    assert default_valid_until("preference", "temporary", None, content=content) is None


@pytest.mark.unit
def test_a_named_window_is_kept():
    from services.memory_service import default_valid_until

    window = datetime(2026, 9, 28, 16, 0)
    assert default_valid_until("preference", "temporary", window, content=ONE_OFF) == window


def _mem(**over):
    from services.memory_service import Memory

    base = dict(id=None, content=ONE_OFF, memory_type="preference", importance=0.5,
                temporal_nature="temporary", source_type="user_stated", confidence=0.9)
    base.update(over)
    return Memory(**base)


async def _stored_valid_until(memory):
    from services.database import MemoryModel, async_session
    from services.memory_service import store_memory

    stored = await store_memory(memory)
    async with async_session() as s:
        return (await s.get(MemoryModel, stored.id)).valid_until


@pytest.mark.integration
async def test_a_temporary_preference_without_a_horizon_gets_one(clean_db):
    before = datetime.utcnow()
    until = await _stored_valid_until(_mem())
    assert until is not None
    assert timedelta(days=29, hours=23) < until - before <= timedelta(days=30, minutes=1)


@pytest.mark.integration
async def test_owners_own_temporary_preference_gets_one_too(clean_db):
    from services.database import ConversationModel, async_session

    async with async_session() as s:
        s.add(ConversationModel(id="c-user", source="user"))
        await s.commit()
    until = await _stored_valid_until(_mem(origin="heard", about="owner", source_conversation_id="c-user"))
    assert until is not None, "a one-off is a one-off even in Ellis's own words"


@pytest.mark.integration
@pytest.mark.parametrize("over", [
    {"temporal_nature": "timeless", "content": "Ellis dislikes bananas"},
    {"temporal_nature": "evolving", "content": "Ellis's go-to Chick-fil-A order is the spicy deluxe"},
    {"content": "Ellis wants Biscuit's evening pill hidden in cheese this week"},
])
async def test_a_lasting_or_sensitive_preference_keeps_no_horizon(clean_db, over):
    assert await _stored_valid_until(_mem(**over)) is None


class _Turn:
    def __init__(self, source, conversation_id):
        self.source, self.conversation_id = source, conversation_id

    def __enter__(self):
        from services import turn_ledger

        rec = turn_ledger.TurnRecord.start(conversation_id=self.conversation_id, source=self.source,
                                           model=None, provider=None)
        self.token = turn_ledger._current.set(rec)
        return rec

    def __exit__(self, *exc):
        from services import turn_ledger

        turn_ledger._current.reset(self.token)


def _extractor_returns(monkeypatch, items):
    from services import memory_service

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = json.dumps(items)
            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "_spawn_background", lambda coro: coro.close())


@pytest.mark.integration
async def test_extraction_gives_owners_temporary_preference_a_horizon(clean_db, monkeypatch):
    from services.database import MemoryModel, async_session
    from services.memory_service import extract_and_store_memories

    _extractor_returns(monkeypatch, [{
        "content": ONE_OFF, "memory_type": "preference", "importance": 0.5,
        "temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9,
        "origin": "heard", "about": "owner",
    }])
    before = datetime.utcnow()
    with _Turn("chat", "c-owner"):
        stored = await extract_and_store_memories(
            [{"role": "user", "content": "I just want a chicken sandwich tonight."},
             {"role": "assistant", "content": "Chicken sandwich it is."}],
            "c-owner", extraction_profile="chat")
    assert len(stored) == 1
    async with async_session() as s:
        row = await s.get(MemoryModel, stored[0].id)
    assert (row.memory_type, row.temporal_nature, row.origin) == ("preference", "temporary", "heard")
    assert row.valid_until is not None
    assert timedelta(days=29, hours=23) < row.valid_until - before <= timedelta(days=30, minutes=1)


# ---- the remember tool ----------------------------------------------------

def _capture_store(monkeypatch):
    from services import memory_service

    stored = []

    async def _store(memories):
        for m in memories:
            m.id = "m-1"
        stored.extend(memories)
        return memories

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    return stored


async def test_the_remember_tool_says_when_a_temporary_preference_lapses(monkeypatch):
    """His own thought is temporary by default (Phase 9), so a preference he
    remembers as a thought is a one-off unless he names its window."""
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    out = await remember.ainvoke({"content": "Rowan wanted strawberry ice cream tonight",
                                  "memory_type": "preference", "about": "contact", "origin": "thought"})
    [m] = stored
    assert m.temporal_nature == "temporary"
    assert m.valid_until is not None
    assert out.startswith("ok (memory_id: m-1)") and "lapses" in out


async def test_the_remember_tool_leaves_a_sensitive_preference_to_decay(monkeypatch):
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    out = await remember.ainvoke({"content": "Rowan wants soup tonight because her throat is sore and she "
                                             "has insomnia",
                                  "memory_type": "preference", "about": "contact", "origin": "thought"})
    [m] = stored
    assert m.valid_until is None
    assert out == "ok (memory_id: m-1)"


async def test_a_preference_owner_said_is_not_stored_timeless_by_the_tool(monkeypatch):
    """``remember`` has no ``temporal_nature`` argument: a preference heard
    from Ellis took the type's default, ``timeless`` — and user_stated plus
    timeless is the one pair decay holds forever."""
    from services.graph.tools import remember

    stored = _capture_store(monkeypatch)
    await remember.ainvoke({"content": "Ellis likes window seats on long flights", "memory_type": "preference",
                            "about": "owner", "origin": "heard"})
    [m] = stored
    assert (m.source_type, m.temporal_nature) == ("user_stated", "evolving")
    assert m.valid_until is None


# ---- Ellis restating a lasting taste inside a one-off's window --------------
#
# ``remember`` writes a preference as a thought by default, and a thought is
# temporary: "Ellis loves the spicy deluxe", remembered mid-turn, got 30 days.
# Ellis's own statement, extracted after the turn, met that row at >= 0.92
# (a reinforcement) or was merged into it — both keep the existing row's
# lifetime, so the lasting taste lapsed with the one-off's horizon anyway.
# In Ellis's own turn his lasting word takes the horizon off; from anyone
# else it does not.

TASTE = "Ellis loves the Chick-fil-A spicy deluxe sandwich"


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


async def _seed_one_off_taste():
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    until = datetime.utcnow().replace(microsecond=0) + timedelta(days=30)
    async with async_session() as s:
        row = MemoryModel(content=TASTE, memory_type="preference", importance=0.5, origin="thought",
                          about="owner", source_type="inferred", confidence=0.6, temporal_nature="temporary",
                          valid_until=until, embedding=get_embedding(TASTE), source_conversation_id="c-owner",
                          source_ref=_ref(kind="remember", conversation_id="c-owner"))
        s.add(row)
        await s.commit()
        return row.id, until


async def _restate(monkeypatch, seed_id, score, ref, action=None, **over):
    from services import memory_service
    from services.database import MemoryModel, async_session
    from services.memory_service import ConflictResolution, Memory, store_memories_with_conflict_detection

    async def _similar(content, threshold=0.75, limit=5, **kw):
        return [(await memory_service.get_memory_by_id(seed_id), score)]

    async def _resolve(existing, memory, sim):
        return ConflictResolution(action=action, reason="same taste")

    async def _merge(a, b, model=None):
        return f"{a}; {b}"

    monkeypatch.setattr(memory_service, "find_similar_memories", _similar)
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _resolve)
    monkeypatch.setattr(memory_service, "_llm_merge_content", _merge)
    fields = dict(id=None, content="Ellis loves the spicy deluxe from Chick-fil-A", memory_type="preference",
                  importance=0.6, temporal_nature="evolving", source_type="user_stated", confidence=0.9,
                  origin="heard", about="owner", source_conversation_id="c-owner", source_ref=ref)
    fields.update(over)
    await store_memories_with_conflict_detection([Memory(**fields)])
    async with async_session() as s:
        return await s.get(MemoryModel, seed_id)


OWNERS_TURN = dict(conversation_id="c-owner", turn_source="chat", profile="chat")


@pytest.mark.integration
@pytest.mark.parametrize("score,action", [(0.95, None), (0.85, "MERGE")])
async def test_owner_restating_a_lasting_taste_takes_the_one_offs_horizon_off(clean_db, monkeypatch, score, action):
    seed, _until = await _seed_one_off_taste()
    row = await _restate(monkeypatch, seed, score, _ref(**OWNERS_TURN), action=action)
    assert row.reinforcement_count == 1, "it is still a restatement"
    assert (row.temporal_nature, row.valid_until) == ("evolving", None)


@pytest.mark.integration
@pytest.mark.parametrize("score,action", [(0.95, None), (0.85, "MERGE")])
async def test_a_restatement_from_anyone_else_keeps_the_horizon(clean_db, monkeypatch, score, action):
    """Updated deliberately (integration review, 2026-09-27): the one-off
    keeps its horizon as before, and the lasting statement is no longer
    folded into it — it is kept as its own row (see below)."""
    seed, until = await _seed_one_off_taste()
    row = await _restate(monkeypatch, seed, score,
                         _ref(conversation_id="hb-parker", turn_source="heartbeat", profile="heartbeat"),
                         action=action, origin="read")
    assert (row.content, row.temporal_nature, row.valid_until) == (TASTE, "temporary", until)
    assert [(r.temporal_nature, r.valid_until) for r in await _others(seed)] == [("evolving", None)]


@pytest.mark.integration
async def test_edwards_own_inference_in_owners_turn_keeps_the_horizon(clean_db, monkeypatch):
    """Extraction in Ellis's turn also lifts Edward's own earlier answers:
    only what Ellis said (``heard``) is his word. Updated deliberately
    (integration review, 2026-09-27): the lasting inference is kept as its
    own row rather than reinforcing the one-off."""
    seed, until = await _seed_one_off_taste()
    row = await _restate(monkeypatch, seed, 0.95, _ref(**OWNERS_TURN), origin="thought", source_type="inferred")
    assert (row.temporal_nature, row.valid_until, row.reinforcement_count) == ("temporary", until, 0)
    assert [(r.temporal_nature, r.valid_until) for r in await _others(seed)] == [("evolving", None)]


@pytest.mark.integration
async def test_owner_restating_a_one_off_keeps_its_horizon(clean_db, monkeypatch):
    seed, until = await _seed_one_off_taste()
    row = await _restate(monkeypatch, seed, 0.95, _ref(**OWNERS_TURN), temporal_nature="temporary")
    assert (row.temporal_nature, row.valid_until) == ("temporary", until)


# ---- a lasting statement and a one-off are not folded together -----------
#
# Integration review, 2026-09-27. Only Ellis, in his own turn, can say that a
# one-off is really a lasting taste (``_owners_lasting_word``). From anyone
# else a >= 0.92 reinforcement or a MERGE kept the existing row's lifetime:
# a heartbeat-read "Ellis likes oat milk in his latte" (0.925 with the real
# model) reinforced "Ellis wants oat milk in his latte this week" and lapsed
# with it; merged the other way, "for Saturday's order" landed in an
# open-ended "likes" row. When the two lifetimes differ — one lasting (not
# ``temporary``, no horizon), the other a one-off — both are kept.
# Intentions are left to their own hygiene (a wake's 72-hour horizon is
# plumbing, not what the plan says).

LASTING = "Ellis likes the spicy deluxe from Chick-fil-A"
SATURDAY = "Ellis wanted the spicy deluxe from Chick-fil-A for Saturday's order"
HEARTBEAT = dict(conversation_id="hb-parker", turn_source="heartbeat", profile="heartbeat")


async def _others(seed_id):
    from sqlalchemy import select

    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(MemoryModel).where(MemoryModel.id != seed_id))).scalars().all())


async def _seed_lasting(content=LASTING, memory_type="preference", nature="evolving", origin="read",
                        about="owner"):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=content, memory_type=memory_type, importance=0.6, origin=origin, about=about,
                          source_type="inferred", confidence=0.7, temporal_nature=nature, valid_until=None,
                          reinforcement_count=2, tier="belief", embedding=get_embedding(content),
                          source_ref=_ref(**HEARTBEAT))
        s.add(row)
        await s.commit()
        return row.id


@pytest.mark.integration
@pytest.mark.parametrize("score,action", [(0.95, None), (0.85, "MERGE")])
async def test_a_one_off_from_anyone_else_is_kept_beside_a_lasting_taste(clean_db, monkeypatch, score, action):
    seed = await _seed_lasting()
    before = datetime.utcnow()
    row = await _restate(monkeypatch, seed, score, _ref(**HEARTBEAT), action=action, content=SATURDAY,
                         temporal_nature="temporary", origin="read", source_type="inferred")
    assert (row.content, row.temporal_nature, row.valid_until, row.reinforcement_count) == \
        (LASTING, "evolving", None, 2), "the lasting taste is untouched: no episode in it, no restatement"
    [one_off] = await _others(seed)
    assert (one_off.content, one_off.temporal_nature) == (SATURDAY, "temporary")
    assert one_off.valid_until is not None and one_off.valid_until - before > timedelta(days=29)


@pytest.mark.integration
@pytest.mark.parametrize("score,action", [(0.95, None), (0.85, "MERGE")])
async def test_the_same_lifetime_still_reinforces_and_merges(clean_db, monkeypatch, score, action):
    """Two lasting statements — ``timeless`` and ``evolving`` alike — are
    one taste said twice."""
    seed = await _seed_lasting(nature="timeless")
    row = await _restate(monkeypatch, seed, score, _ref(**HEARTBEAT), action=action,
                         content="Ellis loves the spicy deluxe at Chick-fil-A", origin="read", source_type="inferred")
    assert row.reinforcement_count == 3
    assert await _others(seed) == []


@pytest.mark.integration
async def test_intentions_merge_whatever_their_horizons(clean_db, monkeypatch):
    seed = await _seed_lasting(content="Watch whether Parker confirms Friday's lawn visit", memory_type="intention",
                               nature="evolving", origin="thought", about="self")
    row = await _restate(monkeypatch, seed, 0.85, _ref(conversation_id="c-owner", turn_source="chat",
                                                       kind="remember"),
                         action="MERGE", content="Watch whether Parker confirms the lawn visit",
                         memory_type="intention", temporal_nature="evolving", origin="thought", about="self",
                         valid_until=datetime.utcnow().replace(microsecond=0) + timedelta(hours=72))
    assert row.valid_until is None
    assert await _others(seed) == []


# ---- what remember says is what was stored ---------------------------------
#
# ``remember``'s reply named the horizon it computed before the store. When
# conflict detection kept an existing row instead — a >= 0.92 echo or
# reinforcement, or a MERGE, which keep the existing lifetime — Edward was
# told "a temporary preference; it lapses <date>" of a row that never lapses.


def _store_returns(monkeypatch, existing):
    from services import memory_service

    async def _store(memories, *args, **kwargs):
        return [existing]

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)


def _existing(**over):
    from services.memory_service import Memory

    base = dict(id="m-old", content="Rowan loves strawberry ice cream", memory_type="preference",
                importance=0.6, temporal_nature="evolving", origin="read", about="contact")
    base.update(over)
    return Memory(**base)


async def test_remember_says_nothing_lapses_when_the_kept_row_has_no_horizon(monkeypatch):
    from services.graph.tools import remember

    _store_returns(monkeypatch, _existing())
    out = await remember.ainvoke({"content": "Rowan wanted strawberry ice cream tonight",
                                  "memory_type": "preference", "about": "contact", "origin": "thought"})
    assert out == "ok (memory_id: m-old)"


async def test_remember_names_the_kept_rows_own_horizon(monkeypatch):
    from services.graph.tools import remember

    kept = datetime(2099, 10, 18, 0, 0)
    _store_returns(monkeypatch, _existing(temporal_nature="temporary", valid_until=kept))
    out = await remember.ainvoke({"content": "Rowan wanted strawberry ice cream tonight",
                                  "memory_type": "preference", "about": "contact", "origin": "thought"})
    assert out.startswith("ok (memory_id: m-old)") and "2099-10-18 00:00 UTC" in out


async def test_remember_says_nothing_lapses_when_a_wake_intention_met_an_open_ended_one(monkeypatch):
    from services import turn_ledger
    from services.graph.tools import remember

    _store_returns(monkeypatch, _existing(content="Watch whether Parker confirms Friday", memory_type="intention",
                                          origin="thought", about="self"))
    rec = turn_ledger.TurnRecord.start(conversation_id="edward-mind", source="mind", model="m", provider="openai")
    token = turn_ledger._current.set(rec)
    try:
        out = await remember.ainvoke({"content": "Watch whether Parker confirms Friday", "memory_type": "intention"})
    finally:
        turn_ledger._current.reset(token)
    assert out == "ok (memory_id: m-old)"
