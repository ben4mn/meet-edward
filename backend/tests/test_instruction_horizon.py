"""A temporary instruction lapses (memory audit §2.1/§7.4, 2026-09-27).

Decay never retires an ``instruction`` (``memory_decay_service.PROTECTED_TYPES``:
a standing directive dropped silently changes Edward's behaviour), so an
instruction the extractor itself called ``temporary`` but gave no
``valid_until`` lived forever: 38 such rows were live at the audit, mostly
one-off reminder guidance from July and August ("For Luc Martin's
August 24 message…").

When a new row is stored, a ``temporary`` instruction without a horizon
gets ``now + TEMPORARY_INSTRUCTION_DEFAULT_DAYS`` (30). A rule Ellis means
for good is ``timeless`` or ``evolving`` and is untouched; so is one that
names its own window, and rows already stored.

Ellis's own word never lapses (pre-deploy review, 2026-09-27): the decay
pass exempts it (``memory_decay_service.owners_own_word``: confirmed by him,
or heard in his own chat or voice turn), but store_memory stamped the 30
days on every temporary instruction whoever said it, and decay never looks
at a row that already has a horizon. "For now, don't text Parker about the
lawn", said in chat, left the pack's standing instructions a month later
with no notice. store_memory now asks the same predicate.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest


@pytest.mark.unit
def test_the_default_is_thirty_days():
    from services.memory_service import TEMPORARY_INSTRUCTION_DEFAULT_DAYS

    assert TEMPORARY_INSTRUCTION_DEFAULT_DAYS == 30


def _mem(**over):
    from services.memory_service import Memory

    base = dict(id=None, content="For Marc-Andre's message, only remind Ellis in the app",
                memory_type="instruction", importance=0.8, temporal_nature="temporary",
                source_type="inferred", confidence=0.8)
    base.update(over)
    return Memory(**base)


async def _stored_valid_until(memory):
    from services.database import MemoryModel, async_session
    from services.memory_service import store_memory

    stored = await store_memory(memory)
    async with async_session() as s:
        return (await s.get(MemoryModel, stored.id)).valid_until


@pytest.mark.integration
async def test_a_temporary_instruction_without_a_horizon_gets_one(clean_db):
    before = datetime.utcnow()
    until = await _stored_valid_until(_mem())
    assert until is not None
    assert timedelta(days=29, hours=23) < until - before <= timedelta(days=30, minutes=1)


@pytest.mark.integration
@pytest.mark.parametrize("over", [
    {"temporal_nature": "timeless"},
    {"temporal_nature": "evolving"},
    {"memory_type": "context"},
    {"memory_type": "fact"},
])
async def test_everything_else_keeps_no_horizon(clean_db, over):
    assert await _stored_valid_until(_mem(**over)) is None


@pytest.mark.integration
async def test_a_named_window_is_kept(clean_db):
    window = datetime.utcnow().replace(microsecond=0) + timedelta(hours=24)
    assert await _stored_valid_until(_mem(valid_until=window)) == window


async def test_the_remember_tool_says_when_it_lapses(monkeypatch):
    from services import memory_service
    from services.graph.tools import remember

    async def _store(memories):
        for m in memories:
            m.id = "m-1"
        return memories

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    out = await remember.ainvoke({"content": "Stay out of the flight chat this week", "memory_type": "instruction",
                                  "about": "self", "origin": "thought"})
    # instruction defaults to timeless unless it is his own thought — then temporary
    assert out.startswith("ok (memory_id: m-1)")
    assert "lapses" in out



# ---- Ellis's own word never lapses ----------------------------------------

def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


@pytest.mark.unit
def test_the_default_leaves_owners_own_word_alone():
    from services.memory_service import default_valid_until

    assert default_valid_until("instruction", "temporary", None, owners_own=True) is None
    assert default_valid_until("instruction", "temporary", None, owners_own=False) is not None


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


FOR_NOW = "For now, do not text Parker about the lawn"


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
@pytest.mark.parametrize("source", ["chat", "voice"])
async def test_owners_temporary_instruction_from_his_own_turn_never_lapses(clean_db, monkeypatch, source):
    from services.database import MemoryModel, async_session
    from services.memory_decay_service import should_retire
    from services.memory_service import extract_and_store_memories

    _extractor_returns(monkeypatch, [{
        "content": FOR_NOW, "memory_type": "instruction", "importance": 0.8,
        "temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9,
        "origin": "heard", "about": "owner",
    }])
    with _Turn(source, "c-owner"):
        stored = await extract_and_store_memories(
            [{"role": "user", "content": "For now, don't text Parker about the lawn."},
             {"role": "assistant", "content": "Okay, I'll leave it."}],
            "c-owner", extraction_profile="chat")
    assert len(stored) == 1
    async with async_session() as s:
        row = await s.get(MemoryModel, stored[0].id)
    assert (row.memory_type, row.temporal_nature, row.origin, row.about) == \
        ("instruction", "temporary", "heard", "owner")
    assert row.valid_until is None, "Ellis's own word never lapses"
    # And the decay pass agrees: a year on, still his.
    assert not should_retire(row, now=datetime.utcnow() + timedelta(days=400))


@pytest.mark.integration
async def test_a_temporary_instruction_in_owners_own_conversation_never_lapses(clean_db):
    from services.database import ConversationModel, async_session

    async with async_session() as s:
        s.add(ConversationModel(id="c-user", source="user"))
        await s.commit()
    # No turn recorded (the pre-ledger path): his conversation says whose it is.
    assert await _stored_valid_until(_mem(origin="heard", about="owner", source_conversation_id="c-user")) is None


@pytest.mark.integration
async def test_a_temporary_instruction_owner_confirmed_never_lapses(clean_db):
    assert await _stored_valid_until(_mem(origin="heard", about="owner",
                                          source_ref=_ref(kind="owner_confirmed"))) is None


@pytest.mark.integration
@pytest.mark.parametrize("over", [
    {"origin": "heard", "source_ref": None},  # nothing says it was Ellis
    {"origin": "thought", "source_ref": None},
    {"origin": "heard", "source_ref": None, "source_conversation_id": "edward-mind"},
])
async def test_what_is_not_owners_own_word_still_lapses(clean_db, over):
    from services.database import ConversationModel, async_session

    async with async_session() as s:
        s.add(ConversationModel(id="edward-mind", source="edward_thought"))
        await s.commit()
    assert await _stored_valid_until(_mem(**over)) is not None


# ---- sensitive content keeps the decay pass's hold -----------------------
#
# Integration review, 2026-09-27: the preference branch already skipped the
# write-time horizon for sensitive text (pets, health, family, money —
# ``memory_decay_service.is_sensitive``), because a row stored with a
# horizon is never looked at by decay again. The instruction branch did
# not: "Give Biscuit her ear medication at 6 am and 6 pm, per the pet sitter"
# (a read/contact row decay holds for Ellis) got 30 days at store time, and an
# UPDATE onto it gave it the same. Now the same exemption: decay decides —
# it holds what Ellis said or Edward read, and lapses Edward's own one-off.

BISCUIT = "Give Biscuit her ear medication at 6 am and 6 pm, per the pet sitter"


@pytest.mark.unit
def test_a_sensitive_temporary_instruction_gets_no_default_horizon():
    from services.memory_service import default_valid_until

    assert default_valid_until("instruction", "temporary", None, content=BISCUIT) is None
    assert default_valid_until("instruction", "temporary", None, content=FOR_NOW) is not None


@pytest.mark.integration
@pytest.mark.parametrize("origin,about", [("read", "contact"), ("thought", "self")])
async def test_a_sensitive_temporary_instruction_is_stored_without_one(clean_db, origin, about):
    assert await _stored_valid_until(_mem(content=BISCUIT, origin=origin, about=about)) is None


@pytest.mark.unit
async def test_an_update_with_sensitive_words_adds_no_horizon():
    """Updated deliberately (verification of the integration fixes,
    2026-09-27): still no horizon, but the row now takes the one-off's
    nature — it had kept ``timeless`` under the new one-off words (see
    below)."""
    from services.memory_service import Memory, _update_lifetime

    existing = Memory(id="m-biscuit", content=BISCUIT, memory_type="instruction", importance=0.8,
                      temporal_nature="timeless", origin="read", about="contact")
    incoming = _mem(content="Give Biscuit her ear medication at 6 am and 6 pm until the bottle runs out",
                    origin="thought", about="self")
    with _Turn("mind", "edward-mind"):
        assert await _update_lifetime(existing, incoming) == {"temporal_nature": "temporary"}


# ---- a sensitive one-off written over a lasting row stays a one-off -------
#
# Verification of the integration fixes (2026-09-27). With no write-time
# horizon for sensitive text, ``_update_lifetime`` wrote nothing for an
# instruction (it only wrote a lifetime along with a horizon), so an
# UPDATE put "For this week, give Biscuit her ear drops at 6 am and 6 pm"
# into Edward's own lasting "Check on Biscuit's ear drops in the evening" and
# the row kept ``evolving``: not held as sensitive (a thought), not a
# one-off to decay — an instruction held for good. The base branch gave
# the same probe ``temporary`` and +30 days. The one-off's nature is
# written with no horizon, and decay lapses Edward's own row on its own
# 30-day horizon while it still holds what Ellis said or Edward read.

BISCUIT_LASTING = "Check on Biscuit's ear drops in the evening"
BISCUIT_THIS_WEEK = "For this week, give Biscuit her ear drops at 6 am and 6 pm"


@pytest.mark.unit
async def test_a_sensitive_one_off_over_a_lasting_row_takes_the_one_offs_nature():
    from services.memory_service import Memory, _update_lifetime

    for nature in ("evolving", "timeless"):
        existing = Memory(id="m-biscuit", content=BISCUIT_LASTING, memory_type="instruction", importance=0.7,
                          temporal_nature=nature, origin="thought", about="self")
        incoming = _mem(content=BISCUIT_THIS_WEEK, origin="thought", about="self")
        with _Turn("mind", "edward-mind"):
            assert await _update_lifetime(existing, incoming) == {"temporal_nature": "temporary"}


@pytest.mark.integration
async def test_edwards_own_sensitive_one_off_update_lapses_through_decay(clean_db, monkeypatch):
    """The verifier's probe, end to end: resolver stubbed to UPDATE, from a
    mind turn, then the decay pass's own predicate 31 days on."""
    from services import memory_service
    from services.database import MemoryModel, async_session
    from services.memory_decay_service import INSTRUCTION_HORIZON_SINCE, retire_reason
    from services.memory_service import ConflictResolution, get_embedding, store_memories_with_conflict_detection

    created = INSTRUCTION_HORIZON_SINCE.replace(tzinfo=None) + timedelta(days=1)
    async with async_session() as s:
        row = MemoryModel(content=BISCUIT_LASTING, memory_type="instruction", importance=0.7, origin="thought",
                          about="self", source_type="inferred", confidence=0.7, temporal_nature="evolving",
                          valid_until=None, reinforcement_count=1, tier="observation", created_at=created,
                          embedding=get_embedding(BISCUIT_LASTING),
                          source_ref=_ref(kind="remember", conversation_id="edward-mind", turn_source="mind"))
        s.add(row)
        await s.commit()
        seed = row.id

    async def _similar(content, threshold=0.75, limit=5, **kw):
        return [(await memory_service.get_memory_by_id(seed), 0.85)]

    async def _resolve(existing, memory, sim):
        return ConflictResolution(action="UPDATE", reason="newer plan")

    monkeypatch.setattr(memory_service, "find_similar_memories", _similar)
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _resolve)
    with _Turn("mind", "edward-mind"):
        await store_memories_with_conflict_detection([
            _mem(content=BISCUIT_THIS_WEEK, origin="thought", about="self", source_conversation_id="edward-mind",
                 source_ref=_ref(kind="remember", conversation_id="edward-mind", turn_source="mind",
                                 profile="mind")),
        ])
    async with async_session() as s:
        updated = await s.get(MemoryModel, seed)
    assert (updated.content, updated.temporal_nature, updated.valid_until) == \
        (BISCUIT_THIS_WEEK, "temporary", None), "the one-off's words bring its nature, not a horizon"
    assert retire_reason(updated, now=created + timedelta(days=31)) == "instruction_horizon"
    assert retire_reason(updated, now=created + timedelta(days=20)) is None


async def test_the_remember_tool_names_no_lapse_for_a_sensitive_instruction(monkeypatch):
    from services import memory_service
    from services.graph.tools import remember

    stored = []

    async def _store(memories):
        for m in memories:
            m.id = "m-1"
        stored.extend(memories)
        return memories

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    out = await remember.ainvoke({"content": BISCUIT, "memory_type": "instruction", "about": "self",
                                  "origin": "thought"})
    [m] = stored
    assert (m.temporal_nature, m.valid_until) == ("temporary", None)
    assert out == "ok (memory_id: m-1)"
