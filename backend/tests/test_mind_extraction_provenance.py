"""A wake's pack is not Ellis talking (H1, 2026-09-27 follow-ups).

A mind wake's pack is the human message of the turn, so post-turn
extraction read it as Ellis speaking. ``MIND_EXTRACTION_ADDENDUM`` told the
extractor to keep "anything Ellis said in the pack" as ``origin: heard``, and
a missing origin defaulted to ``heard``. The pack's "Standing instructions
from Ellis" section is built from exactly those rows (``heard``/``owner``
instructions), so a conclusion Edward drew in a wake became a standing
instruction under Ellis's name — one that "outranks everything below, a
mention included" — and every later wake re-extracted it from the pack.

Production evidence: a memory ("Ellis prefers shared household and Mac
mini screens to remain discreet…"), type instruction, heard/owner,
confidence 0.9, source_ref profile ``mind`` in ``edward-mind``; two more
rows of the same shape.

Contract pinned here:
  * under the ``mind`` profile an extracted ``heard`` becomes ``read`` —
    or ``thought`` when it is about Edward himself; a missing origin never
    defaults to ``heard``;
  * so no wake ever stores a ``heard``/``owner`` instruction;
  * a ``user_stated`` source demoted with it becomes ``inferred`` (a
    user-stated timeless row is never auto-retired, so the label would
    make Edward's own conclusion immortal);
  * the addendum tells the extractor not to re-extract the pack's own
    sections (standing instructions, intentions, thoughts, what he did,
    memory lines).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def _item(**over):
    base = {
        "content": "Ellis prefers shared household and Mac mini screens to remain discreet",
        "memory_type": "instruction",
        "importance": 0.8,
        "temporal_nature": "timeless",
        "source_type": "user_stated",
        "confidence": 0.9,
        "origin": "heard",
        "about": "owner",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# coerce_extracted_fields under the mind profile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_heard_instruction_from_a_wake_is_not_owners():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_item(), now=NOW, profile="mind")
    assert (fields["origin"], fields["about"]) != ("heard", "owner")
    assert fields["origin"] == "read"
    assert "origin" in misses, "the extractor said heard in a wake — that is a miss worth counting"


@pytest.mark.unit
def test_a_demoted_row_is_no_longer_user_stated():
    """A user-stated timeless row is never auto-retired (memory_decay_service
    ``is_protected``); Edward's own reading of a pack must not inherit that."""
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(_item(), now=NOW, profile="mind")
    assert fields["source_type"] == "inferred"


@pytest.mark.unit
def test_heard_about_himself_in_a_wake_is_a_thought():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(
        _item(content="I should keep the screens discreet", memory_type="context", about="self"),
        now=NOW, profile="mind",
    )
    assert fields["origin"] == "thought"


@pytest.mark.unit
@pytest.mark.parametrize("about,expected", [("owner", "read"), ("contact", "read"), ("world", "read"),
                                            ("self", "thought")])
def test_a_missing_origin_in_a_wake_never_defaults_to_heard(about, expected):
    from services.memory_service import coerce_extracted_fields

    item = _item(memory_type="context", about=about)
    item.pop("origin")
    fields, misses = coerce_extracted_fields(item, now=NOW, profile="mind")
    assert fields["origin"] == expected
    assert "origin" in misses


@pytest.mark.unit
def test_other_origins_in_a_wake_are_untouched():
    from services.memory_service import coerce_extracted_fields

    for origin in ("read", "thought", "did"):
        fields, misses = coerce_extracted_fields(
            _item(memory_type="context", origin=origin, about="self", source_type="inferred"),
            now=NOW, profile="mind",
        )
        assert fields["origin"] == origin
        assert "origin" not in misses


@pytest.mark.unit
def test_chat_still_defaults_a_missing_origin_to_heard():
    """Ellis's own chat is where ``heard`` belongs; the default there is unchanged."""
    from services.memory_service import coerce_extracted_fields

    item = _item(memory_type="fact")
    item.pop("origin")
    fields, _ = coerce_extracted_fields(item, now=NOW, profile="chat")
    assert fields["origin"] == "heard"


# ---------------------------------------------------------------------------
# The addendum
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_addendum_no_longer_keeps_what_owner_said_as_heard():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    for line in MIND_EXTRACTION_ADDENDUM.splitlines():
        if '"origin": "heard"' in line:
            assert "never" in line.lower() or "not" in line.lower(), line


@pytest.mark.unit
def test_the_addendum_says_not_to_re_extract_the_packs_own_sections():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    text = MIND_EXTRACTION_ADDENDUM
    for section in ("Standing instructions", "Open intentions", "Recent thoughts", "What you did lately",
                    "memory_id"):
        assert section in text, section
    assert "already stored" in text.lower()


@pytest.mark.unit
def test_the_addendum_says_a_wake_writes_no_instruction_for_owner():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    line = next(l for l in MIND_EXTRACTION_ADDENDUM.splitlines() if '"instruction"' in l)
    assert "never" in line.lower()


# ---------------------------------------------------------------------------
# The extraction pipeline: a pack carrying a standing instruction
# ---------------------------------------------------------------------------

PACK = """[WAKE · messages] Sat 2026-09-27 09:20 UTC.
Standing instructions from Ellis — they outrank everything below, a mention included:
  - Ellis prefers shared household and Mac mini screens to remain discreet (said Sep 23)
Recent messages (newest last):
  - Rowan: are you home?
Open intentions (1):
  - [2026-09-29] Brief Parker before Monday (2d) (memory_id: int-0001)
"""

# What the extractor returned on 2026-09-27 from a pack like this one.
_PAYLOAD = (
    '[{"content": "Ellis prefers shared household and Mac mini screens to remain discreet", '
    '"memory_type": "instruction", "importance": 0.8, "temporal_nature": "timeless", '
    '"source_type": "user_stated", "confidence": 0.9, "origin": "heard", "about": "owner"},'
    '{"content": "Rowan asked whether Ellis is home", "memory_type": "context", "importance": 0.4, '
    '"temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, "about": "contact"}]'
)


def _stub_pipeline(monkeypatch, payload=_PAYLOAD):
    from services import memory_service

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = payload

            return R()

    stored = []

    async def _store(memories):
        stored.extend(memories)
        return memories

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    monkeypatch.setattr("services.entity_service.extract_entities", _noop)
    return stored


async def test_a_wake_extraction_stores_no_standing_instruction_for_owner(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": PACK},
                  {"role": "assistant", "content": "Nothing to do; left it alone."}],
        conversation_id="edward-mind",
        extraction_profile="mind",
    )
    assert stored, "the stub returned two rows"
    assert not [m for m in stored if (m.origin, m.about) == ("heard", "owner")]
    assert not [m for m in stored if m.origin == "heard"]


# ---------------------------------------------------------------------------
# A wake re-reading its pack is not a restatement
# ---------------------------------------------------------------------------
#
# Reinforcement at >= 0.92 similarity had no source check, so every wake
# that re-extracted the pack's own sections reinforced them: one such row
# went from 22 to 30 reinforcements overnight (free and morning wakes),
# another reached knowledge at 22. The pulse's and a scheduled event's own words are
# the same kind of echo. From those turns a near-duplicate refreshes
# ``last_used_at`` (it came up again) and changes nothing else.


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


async def _seed_instruction(reinforcement_count=22, tier="belief"):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    content = "Ellis prefers shared household and Mac mini screens to remain discreet"
    async with async_session() as s:
        row = MemoryModel(content=content, memory_type="instruction", importance=0.8, origin="heard",
                          about="owner", source_type="user_stated", confidence=0.9, temporal_nature="timeless",
                          reinforcement_count=reinforcement_count, tier=tier, embedding=get_embedding(content),
                          source_ref=_ref(conversation_id="edward-mind", profile="mind"))
        s.add(row)
        await s.commit()
        return row.id, content


async def _row(memory_id):
    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return await s.get(MemoryModel, memory_id)


@pytest.mark.integration
@pytest.mark.parametrize("ref", [
    {"conversation_id": "edward-mind", "profile": "mind", "turn_source": "mind"},
    {"kind": "remember", "conversation_id": "edward-surfaces", "turn_source": "pulse"},
    {"conversation_id": "c-reminder", "turn_source": "scheduled_event", "profile": "chat"},
    {"conversation_id": "c-reminder", "profile": "scheduled_event"},
])
async def test_a_repeat_from_an_autonomous_turn_does_not_reinforce(clean_db, ref):
    from services.memory_service import Memory, store_memories_with_conflict_detection

    memory_id, content = await _seed_instruction()
    before = datetime.utcnow()
    stored = await store_memories_with_conflict_detection([
        Memory(id=None, content=content, memory_type="instruction", importance=0.9, origin="read",
               about="owner", source_ref=_ref(**ref)),
    ])
    row = await _row(memory_id)
    assert row.reinforcement_count == 22
    assert row.tier == "belief"
    assert row.last_used_at is not None and row.last_used_at >= before.replace(microsecond=0)
    assert [m.id for m in stored] == [memory_id], "the caller still learns which row it matched"


@pytest.mark.integration
async def test_a_repeat_inside_a_wake_turn_without_a_source_ref_does_not_reinforce(clean_db):
    from services import turn_ledger
    from services.memory_service import Memory, store_memories_with_conflict_detection

    memory_id, content = await _seed_instruction()
    rec = turn_ledger.TurnRecord.start(conversation_id="edward-mind", source="mind", model=None, provider=None)
    token = turn_ledger._current.set(rec)
    try:
        await store_memories_with_conflict_detection([
            Memory(id=None, content=content, memory_type="instruction", importance=0.9),
        ])
    finally:
        turn_ledger._current.reset(token)
    assert (await _row(memory_id)).reinforcement_count == 22


@pytest.mark.integration
async def test_owners_own_chat_still_reinforces(clean_db):
    from services.memory_service import Memory, store_memories_with_conflict_detection

    memory_id, content = await _seed_instruction()
    await store_memories_with_conflict_detection([
        Memory(id=None, content=content, memory_type="instruction", importance=0.9,
               source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat")),
    ])
    assert (await _row(memory_id)).reinforcement_count == 23


@pytest.mark.integration
async def test_a_wake_extraction_twice_leaves_reinforcement_unchanged(clean_db, monkeypatch):
    """The follow-up's own scenario: the pack carries the standing
    instruction, the wake's extraction runs, and runs again next wake."""
    from services import memory_service

    memory_id, _ = await _seed_instruction()

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = _PAYLOAD

            return R()

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr("services.entity_service.extract_entities", _noop)
    for _ in range(2):
        await memory_service.extract_and_store_memories(
            messages=[{"role": "user", "content": PACK}, {"role": "assistant", "content": "Left it alone."}],
            conversation_id="edward-mind",
            extraction_profile="mind",
        )
    row = await _row(memory_id)
    assert row.reinforcement_count == 22
    assert row.tier == "belief"


async def test_an_llm_update_from_a_wake_does_not_reinforce_either(monkeypatch):
    """Between 0.75 and 0.92 the conflict resolver can UPDATE or MERGE; both
    used to add a reinforcement. From a wake the content of his own row may
    change, the count may not. (A row that is Ellis's — heard/owner — is not
    rewritten from a wake at all: tests/test_heard_rows_keep_owners_words.py.)"""
    from services import memory_service
    from services.memory_service import ConflictResolution, Memory

    existing = Memory(id="m-1", content="I keep the shared screens discreet", memory_type="instruction",
                      importance=0.8, reinforcement_count=5, tier="belief", origin="thought", about="self")
    calls = []

    async def _similar(content, threshold=0.75, limit=5, **kw):
        return [(existing, 0.85)]

    async def _resolve(a, b, sim):
        return ConflictResolution(action="UPDATE", reason="newer wording")

    async def _update(memory_id, **kw):
        calls.append(kw)
        return existing

    monkeypatch.setattr(memory_service, "find_similar_memories", _similar)
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _resolve)
    monkeypatch.setattr(memory_service, "update_memory", _update)
    await memory_service.store_memories_with_conflict_detection([
        Memory(id=None, content="Ellis wants the shared screens kept discreet", memory_type="instruction",
               importance=0.8, source_ref=_ref(conversation_id="edward-mind", profile="mind")),
    ])
    assert calls and calls[0].get("content") == "Ellis wants the shared screens kept discreet"
    assert "reinforcement_count" not in calls[0] and "tier" not in calls[0]


@pytest.mark.integration
async def test_reinforcing_a_row_without_provenance_does_not_adopt_the_new_source(clean_db):
    """merge_source_ref(None, new) returned ``new``, so the first source to
    repeat a pre-provenance row became where it came from."""
    import json

    from services.database import MemoryModel, async_session
    from services.memory_service import Memory, get_embedding, store_memories_with_conflict_detection

    content = "Ellis decided not to cancel the lawn-mowing service for now"
    async with async_session() as s:
        row = MemoryModel(content=content, memory_type="instruction", importance=0.8, origin="heard",
                          about="owner", embedding=get_embedding(content), source_conversation_id="c-owner-old")
        s.add(row)
        await s.commit()
        mid = row.id
    await store_memories_with_conflict_detection([
        Memory(id=None, content=content, memory_type="instruction", importance=0.8,
               source_ref=_ref(conversation_id="c-other", turn_source="chat", profile="chat")),
    ])
    ref = json.loads((await _row(mid)).source_ref)
    assert ref["kind"] == "unrecorded" and ref["conversation_id"] == "c-owner-old"
    assert ref["also"][0]["conversation_id"] == "c-other"
