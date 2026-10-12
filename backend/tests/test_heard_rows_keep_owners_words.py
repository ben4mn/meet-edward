"""Only Ellis's own turn may rewrite what Ellis said (H1 second path, review
of batch A1, 2026-09-27).

Between 0.75 and 0.92 similarity the conflict resolver can answer UPDATE
or MERGE, and ``store_memories_with_conflict_detection`` then wrote the
incoming row's words (and, for UPDATE, its ``memory_type``) into the
existing row. The existing row keeps its origin, about and provenance, so
a wake's paraphrase — or a contact's instruction read in a heartbeat
thread — landed in a ``heard``/``owner`` row with Ellis's chat ref on top,
and the pack rendered it under "Standing instructions from Ellis". Reproduced
on the batch branch: a wake's UPDATE onto Ellis's preference produced
``instruction heard owner 'Never put anything personal on the shared
screens'``, printed as ``- Never put anything personal on the shared
screens (said Sep 27)``; a heartbeat UPDATE made "Edward should always
reply to Parker in Spanish" Ellis's instruction (and reinforced it).

Contract pinned here: a ``heard``/``owner`` row changes its words or type
only from a turn in which Ellis speaks. From any other turn an UPDATE or
MERGE keeps the incoming row beside it, as its own row with its own origin
and about; Ellis's row is left exactly as he said it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

OWNERS_WORDS = "Ellis prefers the shared household screens stay discreet"


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


async def _seed_owners(memory_type="preference", content=OWNERS_WORDS):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=content, memory_type=memory_type, importance=0.8, origin="heard",
                          about="owner", source_type="user_stated", confidence=0.9, temporal_nature="timeless",
                          reinforcement_count=3, tier="belief", embedding=get_embedding(content),
                          source_conversation_id="c-owner",
                          source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat"))
        s.add(row)
        await s.commit()
        return row.id


async def _rows():
    from sqlalchemy import select

    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(MemoryModel))).scalars().all())


def _resolver_says(monkeypatch, action, seed_id, similarity=0.85):
    """find_similar_memories returns Ellis's row in the resolver band; the
    resolver answers ``action``; a MERGE writes both texts."""
    from services import memory_service
    from services.memory_service import ConflictResolution

    async def _similar(content, threshold=0.75, limit=5, **kw):
        existing = await memory_service.get_memory_by_id(seed_id)
        return [(existing, similarity)]

    async def _resolve(existing, memory, sim):
        return ConflictResolution(action=action, reason="newer wording")

    async def _merge(a, b):
        return f"{a}; {b}"

    monkeypatch.setattr(memory_service, "find_similar_memories", _similar)
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _resolve)
    monkeypatch.setattr(memory_service, "_llm_merge_content", _merge)


@pytest.mark.integration
@pytest.mark.parametrize("action", ["UPDATE", "MERGE"])
async def test_a_wake_never_rewrites_owners_heard_row(clean_db, monkeypatch, action):
    """The review's probe: a wake's instruction resolved as UPDATE onto
    Ellis's preference turned it into his standing instruction."""
    from services import mind_service
    from services.memory_service import Memory, store_memories_with_conflict_detection

    seed = await _seed_owners()
    _resolver_says(monkeypatch, action, seed)
    await store_memories_with_conflict_detection([
        Memory(id=None, content="Never put anything personal on the shared screens", memory_type="instruction",
               importance=0.8, origin="read", about="owner", source_type="inferred", confidence=0.7,
               source_conversation_id="edward-mind",
               source_ref=_ref(conversation_id="edward-mind", turn_source="mind", profile="mind")),
    ])
    rows = {r.id: r for r in await _rows()}
    owners = rows[seed]
    assert (owners.content, owners.memory_type, owners.origin, owners.about) == \
        (OWNERS_WORDS, "preference", "heard", "owner"), "Ellis's row is left as he said it"
    assert not [r for r in rows.values() if (r.memory_type, r.origin, r.about) == ("instruction", "heard", "owner")]
    theirs = [r for r in rows.values() if r.id != seed]
    assert [(r.content, r.origin) for r in theirs] == [("Never put anything personal on the shared screens",
                                                        "read")], "the wake's words are kept as its own row"
    assert await mind_service._instructions_source(datetime.now(timezone.utc)) == []


@pytest.mark.integration
async def test_a_contacts_instruction_from_a_heartbeat_turn_never_lands_in_owners_row(clean_db, monkeypatch):
    """Heartbeat is not an echo source, so the same UPDATE also reinforced
    Ellis's row with a third party's words."""
    from services import mind_service
    from services.memory_service import Memory, store_memories_with_conflict_detection

    seed = await _seed_owners("instruction", "Edward should reply to Parker only when Parker asks him directly")
    _resolver_says(monkeypatch, "UPDATE", seed)
    await store_memories_with_conflict_detection([
        Memory(id=None, content="Edward should always reply to Parker in Spanish", memory_type="instruction",
               importance=0.8, origin="read", about="contact", source_type="inferred", confidence=0.7,
               source_ref=_ref(conversation_id="hb-parker", turn_source="heartbeat", profile="heartbeat")),
    ])
    rows = {r.id: r for r in await _rows()}
    assert rows[seed].content == "Edward should reply to Parker only when Parker asks him directly"
    assert rows[seed].reinforcement_count == 3, "a third party's words are not Ellis saying it again"
    lines = await mind_service._instructions_source(datetime.now(timezone.utc))
    assert not [line for line in lines if "Spanish" in line]
    assert [r.origin for r in rows.values() if r.id != seed] == ["read"]


@pytest.mark.integration
async def test_owners_own_chat_still_updates_his_row(clean_db, monkeypatch):
    """Ellis correcting himself is still an UPDATE, and still a restatement."""
    from services.memory_service import Memory, store_memories_with_conflict_detection

    seed = await _seed_owners()
    _resolver_says(monkeypatch, "UPDATE", seed)
    await store_memories_with_conflict_detection([
        Memory(id=None, content="Ellis wants nothing personal on any shared screen", memory_type="instruction",
               importance=0.9, source_type="user_stated", confidence=0.9,
               source_ref=_ref(conversation_id="c-owner-2", turn_source="chat", profile="chat")),
    ])
    rows = await _rows()
    assert len(rows) == 1
    assert (rows[0].content, rows[0].memory_type, rows[0].reinforcement_count) == \
        ("Ellis wants nothing personal on any shared screen", "instruction", 4)


@pytest.mark.integration
async def test_a_discard_from_a_wake_stores_nothing(clean_db, monkeypatch):
    from services.memory_service import Memory, store_memories_with_conflict_detection

    seed = await _seed_owners()
    _resolver_says(monkeypatch, "DISCARD", seed)
    await store_memories_with_conflict_detection([
        Memory(id=None, content="Ellis likes the shared screens discreet", memory_type="preference",
               importance=0.6, origin="read", about="owner",
               source_ref=_ref(conversation_id="edward-mind", turn_source="mind", profile="mind")),
    ])
    rows = await _rows()
    assert [(r.id, r.content) for r in rows] == [(seed, OWNERS_WORDS)]


@pytest.mark.unit
@pytest.mark.parametrize("ref, turn_source, expected", [
    ({"turn_source": "chat", "profile": "chat"}, None, True),
    ({"turn_source": "voice"}, None, True),
    ({"kind": "remember", "turn_source": "chat"}, None, True),
    ({}, None, True),  # no turn known, chat profile: the pre-ledger node
    ({"turn_source": "mind", "profile": "mind"}, None, False),
    ({"profile": "mind"}, None, False),
    ({"profile": "scheduled_event"}, None, False),
    ({"turn_source": "heartbeat", "profile": "heartbeat"}, None, False),
    ({"profile": "heartbeat"}, None, False),
    ({"kind": "remember"}, "pulse", False),
    ({"turn_source": "twilio"}, None, False),
    ({"turn_source": "worker"}, None, False),
])
def test_who_may_rewrite_what_owner_said(ref, turn_source, expected):
    from services.memory_service import is_owners_word

    assert is_owners_word(_ref(**ref) if ref else None, turn_source) is expected


# ---- the rules Ellis pinned, and what a chat turn may write into his rows ----
#
# Integration review of the memory audit (2026-09-27). The UPDATE branch
# now writes the incoming words' lifetime too (``_update_lifetime``), and
# the guard above asked only whose *turn* it was. Two ways through:
#   1. Ellis's own "Until Friday, don't reply to @edward mentions…" resolved
#      as UPDATE over the pin f3eb03c0 (the real background model chose
#      UPDATE both times at 0.80): the pin took the exception's words and
#      Friday's ``valid_until`` — still ``owner_confirmed`` on top, gone from
#      his standing instructions on Saturday, and nothing restores it.
#   2. Edward's own ``thought`` instruction (``remember``, or extraction of
#      his answer) in Ellis's chat turn passed the turn-only guard: the pin
#      read ``heard owner temporary <+30 days> … owner_confirmed``.
# Contract: a ``owner_confirmed`` row is never rewritten by conflict
# detection — the exception is kept beside the rule and lapses on its own;
# a ``heard``/``owner`` row is rewritten only by what Ellis himself said
# (``heard``) in his own turn; and ``_update_lifetime`` never gives one of
# Ellis's rows a horizon or a new nature on anyone else's word.

PIN = ("Reply to @edward mentions, and in chats I've invited you into, join in when you have "
       "something useful to add")
FRIDAY_EXCEPTION = ("Until Friday, don't reply to @edward mentions or join chats I've invited you into; "
                    "tell me instead")


def _pin_ref():
    return json.dumps({"kind": "owner_confirmed", "audit": "2026-09-27", "pin": "p_invited"})


async def _seed_pin():
    """Shaped like production's five pins: instruction/heard/owner,
    timeless, knowledge, no thread, ``owner_confirmed``, open-ended."""
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=PIN, memory_type="instruction", importance=0.9, origin="heard", about="owner",
                          source_type="user_stated", confidence=0.95, temporal_nature="timeless",
                          reinforcement_count=6, tier="knowledge", embedding=get_embedding(PIN),
                          source_conversation_id=None, valid_until=None, source_ref=_pin_ref())
        s.add(row)
        await s.commit()
        return row.id


class _Turn:
    def __init__(self, source, conversation_id="c-owner"):
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


def _friday():
    return datetime.utcnow().replace(microsecond=0) + timedelta(days=3)


@pytest.mark.integration
@pytest.mark.parametrize("action", ["UPDATE", "MERGE"])
@pytest.mark.parametrize("memory_type", ["instruction", "preference"])
async def test_owners_own_exception_is_kept_beside_his_pin(clean_db, monkeypatch, action, memory_type):
    from services import mind_service
    from services.memory_service import Memory, store_memories_with_conflict_detection

    pin = await _seed_pin()
    _resolver_says(monkeypatch, action, pin)
    until = _friday()
    with _Turn("chat"):
        await store_memories_with_conflict_detection([
            Memory(id=None, content=FRIDAY_EXCEPTION, memory_type=memory_type, importance=0.9,
                   temporal_nature="temporary", source_type="user_stated", confidence=0.9, origin="heard",
                   about="owner", valid_until=until, source_conversation_id="c-owner",
                   source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat")),
        ])
    rows = {r.id: r for r in await _rows()}
    kept = rows[pin]
    assert (kept.content, kept.memory_type, kept.temporal_nature, kept.valid_until, kept.origin, kept.about) == \
        (PIN, "instruction", "timeless", None, "heard", "owner"), "the pin is exactly as Ellis confirmed it"
    assert json.loads(kept.source_ref)["kind"] == "owner_confirmed"
    [exception] = [r for r in rows.values() if r.id != pin]
    assert (exception.content, exception.valid_until) == (FRIDAY_EXCEPTION, until), \
        "the exception is its own row and lapses on its own"
    lines = await mind_service._instructions_source(datetime.now(timezone.utc))
    assert [line for line in lines if "join in when you have something useful" in line], \
        "the pin is still one of his standing instructions"


@pytest.mark.integration
@pytest.mark.parametrize("action", ["UPDATE", "MERGE"])
@pytest.mark.parametrize("seed", ["pin", "heard"])
async def test_edwards_own_thought_in_owners_turn_never_rewrites_his_row(clean_db, monkeypatch, action, seed):
    """The finding's probe: ``remember`` (origin thought, about self) of an
    instruction in Ellis's chat turn, resolved as UPDATE over his row."""
    from services.memory_service import Memory, store_memories_with_conflict_detection

    if seed == "pin":
        seed_id, words = await _seed_pin(), PIN
    else:
        words = "Edward should keep replies short, with no filler"
        seed_id = await _seed_owners("instruction", words)
    _resolver_says(monkeypatch, action, seed_id)
    thought = "Keep replies to Ellis short for the rest of this week"
    with _Turn("chat"):
        await store_memories_with_conflict_detection([
            Memory(id=None, content=thought, memory_type="instruction", importance=0.6,
                   temporal_nature="temporary", source_type="inferred", confidence=0.6, origin="thought",
                   about="self", valid_until=_friday(), source_conversation_id="c-owner",
                   source_ref=_ref(kind="remember", conversation_id="c-owner", turn_source="chat")),
        ])
    rows = {r.id: r for r in await _rows()}
    owners = rows[seed_id]
    assert (owners.content, owners.temporal_nature, owners.valid_until, owners.origin, owners.about) == \
        (words, "timeless", None, "heard", "owner"), "only Ellis's own word rewrites his row"
    assert [(r.content, r.origin) for r in rows.values() if r.id != seed_id] == [(thought, "thought")]


@pytest.mark.unit
async def test_update_lifetime_never_changes_owners_row_on_anyone_elses_word():
    """Belt and braces for the guard: ``_update_lifetime`` itself returns
    nothing for Ellis's row (heard/owner or ``owner_confirmed``) unless the new
    words are his own, heard in his own turn."""
    from services.memory_service import Memory, _update_lifetime

    def _existing(**over):
        base = dict(id="m-owner", content=PIN, memory_type="instruction", importance=0.9,
                    temporal_nature="timeless", origin="heard", about="owner", source_ref=_pin_ref())
        base.update(over)
        return Memory(**base)

    thought = Memory(id=None, content="Keep replies short this week", memory_type="instruction", importance=0.6,
                     temporal_nature="temporary", origin="thought", about="self",
                     source_ref=_ref(kind="remember", conversation_id="c-owner", turn_source="chat"))
    read = Memory(id=None, content="Parker asks for replies in Spanish", memory_type="preference", importance=0.6,
                  temporal_nature="temporary", origin="read", about="contact",
                  source_ref=_ref(conversation_id="hb-parker", turn_source="heartbeat", profile="heartbeat"))
    for existing in (_existing(), _existing(source_ref=_ref(conversation_id="c-owner", turn_source="chat"))):
        with _Turn("chat"):
            assert await _update_lifetime(existing, thought) == {}
        with _Turn("heartbeat", "hb-parker"):
            assert await _update_lifetime(existing, read) == {}
    # A row that is not Ellis's still takes the new words' horizon.
    theirs = _existing(origin="read", about="contact", source_ref=None)
    with _Turn("chat"):
        assert (await _update_lifetime(theirs, thought))["valid_until"] is not None


# ---- Ellis's own one-off beside a lasting rule of his ------------------------
#
# Verification of the integration fixes (2026-09-27). ``_lifetimes_apart``
# let any of Ellis's own heard words through, so above 0.92 — where no
# resolver runs and the pin guard never looks — his "until Friday"
# exception simply reinforced the pin (real all-MiniLM cosine for the
# probe pair: 0.920): one row, the pin, count 6 -> 7, and the stand-down
# stored nowhere. Merged into a lasting heard/owner row, his one-off's
# words became permanent. And his lasting word reinforcing one of
# Edward's one-offs lapsed with it. The only exemption is the one
# ``_owners_lasting_word`` itself carries out: Ellis, in his own turn, saying
# a one-off taste lasts. Every other pair of lifetimes is kept apart.

MENTIONS_PIN = "Edward should reply to @edward mentions in group chats"
MENTIONS_FRIDAY = "Edward should not reply to @edward mentions in group chats until Friday"


def _owners_one_off(content=MENTIONS_FRIDAY, until=None):
    from services.memory_service import Memory

    return Memory(id=None, content=content, memory_type="instruction", importance=0.9,
                  temporal_nature="temporary", source_type="user_stated", confidence=0.9, origin="heard",
                  about="owner", valid_until=until, source_conversation_id="c-owner",
                  source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat"))


@pytest.mark.integration
@pytest.mark.parametrize("similarity", [0.92, 0.95])
async def test_owners_own_exception_above_the_resolver_band_is_kept_beside_his_pin(clean_db, monkeypatch,
                                                                                 similarity):
    from services import mind_service
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding, store_memories_with_conflict_detection

    async with async_session() as s:
        row = MemoryModel(content=MENTIONS_PIN, memory_type="instruction", importance=0.9, origin="heard",
                          about="owner", source_type="user_stated", confidence=0.95, temporal_nature="timeless",
                          reinforcement_count=3, tier="knowledge", embedding=get_embedding(MENTIONS_PIN),
                          source_conversation_id=None, valid_until=None, source_ref=_pin_ref())
        s.add(row)
        await s.commit()
        pin = row.id
    _resolver_says(monkeypatch, "UPDATE", pin, similarity=similarity)
    until = _friday()
    with _Turn("chat"):
        await store_memories_with_conflict_detection([_owners_one_off(until=until)])
    rows = {r.id: r for r in await _rows()}
    kept = rows[pin]
    assert (kept.content, kept.temporal_nature, kept.valid_until, kept.reinforcement_count) == \
        (MENTIONS_PIN, "timeless", None, 3), "the pin is not reinforced by its own exception"
    [exception] = [r for r in rows.values() if r.id != pin]
    assert (exception.content, exception.temporal_nature, exception.valid_until) == \
        (MENTIONS_FRIDAY, "temporary", until), "the stand-down is stored, and lapses on Friday"
    lines = await mind_service._instructions_source(datetime.now(timezone.utc))
    assert [line for line in lines if "until Friday" in line], "Edward sees the stand-down"


@pytest.mark.integration
@pytest.mark.parametrize("similarity,action", [(0.95, None), (0.85, "MERGE")])
async def test_owners_own_one_off_is_not_folded_into_his_lasting_row(clean_db, monkeypatch, similarity, action):
    """The related gap: merged (or reinforced) into his lasting heard/owner
    row, the one-off's words took the row's open-ended life."""
    from services.memory_service import store_memories_with_conflict_detection

    seed = await _seed_owners("instruction", MENTIONS_PIN)
    _resolver_says(monkeypatch, action, seed, similarity=similarity)
    until = _friday()
    with _Turn("chat"):
        await store_memories_with_conflict_detection([_owners_one_off(until=until)])
    rows = {r.id: r for r in await _rows()}
    assert (rows[seed].content, rows[seed].temporal_nature, rows[seed].valid_until,
            rows[seed].reinforcement_count) == (MENTIONS_PIN, "timeless", None, 3)
    assert [(r.content, r.valid_until) for r in rows.values() if r.id != seed] == [(MENTIONS_FRIDAY, until)]


@pytest.mark.integration
async def test_owners_lasting_rule_is_not_folded_into_edwards_one_off(clean_db, monkeypatch):
    """Ellis saying a rule for good does not reinforce Edward's week-long
    note of it (which lapses in 30 days): only a taste is carried over
    by ``_owners_lasting_word``; a rule is stored as his own row."""
    from services.database import MemoryModel, async_session
    from services.memory_service import Memory, get_embedding, store_memories_with_conflict_detection

    note = "Do not text Parker about the lawn this week"
    until = datetime.utcnow().replace(microsecond=0) + timedelta(days=30)
    async with async_session() as s:
        row = MemoryModel(content=note, memory_type="instruction", importance=0.6, origin="thought", about="self",
                          source_type="inferred", confidence=0.6, temporal_nature="temporary", valid_until=until,
                          reinforcement_count=0, tier="observation", embedding=get_embedding(note),
                          source_conversation_id="c-owner", source_ref=_ref(kind="remember", conversation_id="c-owner"))
        s.add(row)
        await s.commit()
        seed = row.id
    _resolver_says(monkeypatch, "UPDATE", seed, similarity=0.95)
    rule = "Never text Parker about the lawn"
    with _Turn("chat"):
        await store_memories_with_conflict_detection([
            Memory(id=None, content=rule, memory_type="instruction", importance=0.9, temporal_nature="timeless",
                   source_type="user_stated", confidence=0.9, origin="heard", about="owner",
                   source_conversation_id="c-owner",
                   source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat")),
        ])
    rows = {r.id: r for r in await _rows()}
    assert (rows[seed].valid_until, rows[seed].reinforcement_count) == (until, 0)
    assert [(r.content, r.temporal_nature, r.valid_until, r.origin) for r in rows.values() if r.id != seed] == \
        [(rule, "timeless", None, "heard")]
