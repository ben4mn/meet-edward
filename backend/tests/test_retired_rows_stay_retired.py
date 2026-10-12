"""An extractor re-lifting a retired row does not bring it back (review of
batch A1, 2026-09-27).

Conflict detection was limited to live rows (``live_only=True``) so that a
decision taken again after ``close_intention`` — or a fact Ellis restated
after decay retired it — would be stored again. But the echo guard only
runs once a live match exists, so when a wake's own extraction re-lifts
the text of a row that is no longer live — its pack's "Open intentions"
line, gathered before the close, or an earlier pack still in the
10-message extraction window — nothing matches and a brand-new live row
is stored. Reproduced on the batch branch: close intention X in a mind
turn, extract X's text again, and a second row with the same words and a
72-hour horizon appears in the next pack's "Open intentions" and in the
research candidates. The same path brings back a contradiction loser Ellis
retired (``POST /api/consolidation/flags/{id}/resolve``) when a later
extraction re-reads its source message.

Contract pinned here:
  * a passive re-lift — post-turn extraction in a turn where Ellis is not
    speaking (a wake, the pulse, a fired event, a heartbeat thread) — still
    meets retired rows: a >= 0.92 match is already known, so nothing is
    stored and the retired row is not touched; in the resolver band the
    resolver decides, and only KEEP_BOTH (related but distinct) stores it;
  * Ellis's own turns and Edward's deliberate writes (``remember``) meet live
    rows only, so a restated fact or a new decision is stored again — but
    in Ellis's turn only what Ellis said (``heard``) counts as restated: an
    intention, a thought or a record lifted from Edward's own earlier
    answer, or from a journal entry's narrative, in the 10-message window
    is a re-lift like a wake's (pre-deploy review, 2026-09-27: one more
    message from Ellis reopened an intention a wake had closed, open-ended);
  * ``close_intention`` keeps the intention's embedding: its content gains
    the outcome, but a re-lift of what the intention said still finds it
    (with the real model the closed text scored 0.70–0.82 against the
    original, below the echo line).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

TEXT = "Brief Theo before Monday's planning meeting"


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


async def _seed(content=TEXT, valid_until=None, memory_type="intention", origin="thought", about="self"):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=content, memory_type=memory_type, importance=0.7, origin=origin, about=about,
                          temporal_nature="evolving", confidence=0.8, embedding=get_embedding(content),
                          valid_until=valid_until, reinforcement_count=1, tier="observation",
                          source_ref=_ref(kind="remember", conversation_id="edward-mind", turn_source="mind"))
        s.add(row)
        await s.commit()
        return row.id


async def _rows():
    from sqlalchemy import select

    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(MemoryModel))).scalars().all())


def _live(rows):
    now = datetime.utcnow()
    return [r for r in rows if r.valid_until is None or r.valid_until > now]


class _Turn:
    """Run the body inside a turn of this source (what post-turn extraction
    and the tools read through ``turn_ledger.current_turn``)."""

    def __init__(self, source, conversation_id="edward-mind"):
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


def _extractor_returns(monkeypatch, items, resolver=None):
    from services import memory_service

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = json.dumps(items)

            return R()

    async def _no_resolver(existing, memory, similarity):
        raise AssertionError(f"the resolver was consulted at {similarity:.2f}")

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "_spawn_background", lambda coro: coro.close())
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", resolver or _no_resolver)


def _intention_item(content=TEXT):
    return {"content": content, "memory_type": "intention", "importance": 0.7, "temporal_nature": "evolving",
            "source_type": "inferred", "confidence": 0.8, "origin": "thought", "about": "self"}


@pytest.mark.integration
async def test_a_wake_re_lifting_the_intention_it_just_closed_does_not_reopen_it(clean_db, monkeypatch):
    """The review's scenario: close X in a mind turn, then the same wake's
    extraction lifts X from its pack."""
    from services import mind_service
    from services.graph.tools import close_intention
    from services.memory_service import extract_and_store_memories

    mid = await _seed()
    with _Turn("mind"):
        out = await close_intention.ainvoke({"memory_id": mid, "outcome": "Briefed Theo on Friday"})
        assert out.startswith("ok"), out
        _extractor_returns(monkeypatch, [_intention_item()])
        pack = f"Open intentions (1):\n  - [2026-09-29] {TEXT} (memory_id: {mid})"
        stored = await extract_and_store_memories(
            [{"role": "user", "content": pack}, {"role": "assistant", "content": "Closed the Theo one."}],
            "edward-mind", extraction_profile="mind")
    rows = await _rows()
    assert _live(rows) == [], "nothing reopened"
    assert [r.id for r in rows] == [mid]
    assert stored == []
    thoughts, _ = await mind_service._self_memories(datetime.now(timezone.utc))
    assert thoughts == []


@pytest.mark.integration
@pytest.mark.parametrize("source, profile, conversation", [
    ("mind", "mind", "edward-mind"),
    ("pulse", "chat", "edward-surfaces"),
    ("scheduled_event", "scheduled_event", "c-reminder"),
    ("heartbeat", "heartbeat", "hb-parker"),
])
async def test_a_re_lift_of_a_retired_row_is_already_known(clean_db, monkeypatch, source, profile, conversation):
    """A row retired by its horizon, by decay, or as the loser of a
    contradiction Ellis settled: its words re-read by an extractor store
    nothing and leave it as it was."""
    from services.memory_service import Memory, store_memories_with_conflict_detection

    _extractor_returns(monkeypatch, [])
    retired_at = datetime.utcnow() - timedelta(hours=1)
    mid = await _seed(valid_until=retired_at)
    stored = await store_memories_with_conflict_detection([
        Memory(id=None, content=TEXT, memory_type="intention", importance=0.7, origin="thought", about="self",
               valid_until=datetime.utcnow() + timedelta(hours=72),
               source_ref=_ref(conversation_id=conversation, turn_source=source, profile=profile)),
    ])
    rows = await _rows()
    assert [r.id for r in rows] == [mid] and stored == []
    row = rows[0]
    assert abs((row.valid_until - retired_at).total_seconds()) < 1, "it stays retired"
    assert row.reinforcement_count == 1 and row.last_used_at is None, "and untouched"


@pytest.mark.integration
@pytest.mark.parametrize("action, stored_again", [
    ("UPDATE", False), ("MERGE", False), ("DISCARD", False), ("KEEP_BOTH", True),
])
async def test_in_the_resolver_band_a_retired_row_is_known_unless_distinct(clean_db, monkeypatch, action,
                                                                            stored_again):
    """A reworded re-lift lands between 0.75 and 0.92: the resolver says
    whether it is the same thing (then it is known, and the retired row is
    not rewritten) or a related but distinct one (then it is stored)."""
    from services import memory_service
    from services.memory_service import ConflictResolution, Memory, store_memories_with_conflict_detection

    mid = await _seed(valid_until=datetime.utcnow() - timedelta(hours=1))
    seen = []

    async def _similar(content, threshold=0.75, limit=5, live_only=False):
        retired = await memory_service.get_memory_by_id(mid)
        return [] if live_only else [(retired, 0.85)]

    async def _resolve(existing, memory, similarity):
        seen.append(existing.id)
        return ConflictResolution(action=action, reason="probe", merged_content="merged")

    _extractor_returns(monkeypatch, [], resolver=_resolve)
    monkeypatch.setattr(memory_service, "find_similar_memories", _similar)
    await store_memories_with_conflict_detection([
        Memory(id=None, content="Brief Theo before the Monday planning sync", memory_type="intention",
               importance=0.7, origin="thought", about="self",
               source_ref=_ref(conversation_id="edward-mind", turn_source="mind", profile="mind")),
    ])
    rows = {r.id: r for r in await _rows()}
    assert seen == [mid]
    assert rows[mid].content == TEXT, "a retired row is never rewritten"
    assert (len(rows) == 2) is stored_again


@pytest.mark.integration
async def test_owners_chat_restating_a_retired_fact_stores_it_again(clean_db, monkeypatch):
    from services.memory_service import Memory, store_memories_with_conflict_detection

    _extractor_returns(monkeypatch, [])
    content = "Ellis's dentist is Dr. Alvarez on Oak Street"
    mid = await _seed(content=content, valid_until=datetime.utcnow() - timedelta(days=2), memory_type="fact",
                      origin="heard", about="owner")
    await store_memories_with_conflict_detection([
        Memory(id=None, content=content, memory_type="fact", importance=0.6,
               source_ref=_ref(conversation_id="c-owner", turn_source="chat", profile="chat")),
    ])
    live = _live(await _rows())
    assert len(live) == 1 and live[0].id != mid


@pytest.mark.integration
async def test_a_wake_deciding_it_again_with_remember_is_a_new_open_intention(clean_db, monkeypatch):
    """A deliberate write is a decision, not a re-lift."""
    from services.graph.tools import close_intention, remember

    _extractor_returns(monkeypatch, [])
    mid = await _seed()
    with _Turn("mind"):
        await close_intention.ainvoke({"memory_id": mid, "outcome": "Dropped — Theo moved the meeting"})
        out = await remember.ainvoke({"content": TEXT, "memory_type": "intention", "about": "self",
                                      "origin": "thought"})
    assert out.startswith("ok"), out
    live = _live(await _rows())
    assert len(live) == 1 and live[0].id != mid


@pytest.mark.integration
async def test_closing_keeps_what_the_intention_said_findable(clean_db):
    from services.graph.tools import close_intention
    from services.memory_service import find_similar_memories

    mid = await _seed()
    await close_intention.ainvoke({"memory_id": mid, "outcome": "Briefed Theo on Friday"})
    assert [m.id for m, s in await find_similar_memories(TEXT, threshold=0.92)] == [mid]



# Ellis's turn, Edward's words (pre-deploy review, 2026-09-27). The chat
# extractor reads the last 10 messages, Edward's own earlier answers
# included (and, in a journal entry, the seeded narrative), and keeps what
# the assistant decided as intention/thought/self. With every chat turn
# counted as Ellis's word, conflict detection met live rows only: a closed
# intention lifted again from "I'll keep an eye on Storm's reply" matched
# nothing live and came back open-ended, into every pack's "Open
# intentions" and the research candidates.

STORM = "Keep an eye on Storm's reply about the invoice"


@pytest.mark.integration
@pytest.mark.parametrize("source", ["chat", "voice"])
async def test_owners_turn_re_lifting_edwards_closed_intention_does_not_reopen_it(clean_db, monkeypatch, source):
    from services.graph.tools import close_intention
    from services.memory_service import extract_and_store_memories

    mid = await _seed(content=STORM)
    with _Turn("mind"):
        out = await close_intention.ainvoke({"memory_id": mid, "outcome": "Storm replied; nothing to do"})
        assert out.startswith("ok"), out
    _extractor_returns(monkeypatch, [_intention_item(STORM)])
    with _Turn(source, "c-owner"):
        stored = await extract_and_store_memories(
            [{"role": "user", "content": "Can you check on the invoice?"},
             {"role": "assistant", "content": "I'll keep an eye on Storm's reply about the invoice."},
             {"role": "user", "content": "Thanks. Also, what's for dinner?"},
             {"role": "assistant", "content": "Pasta, from the plan."}],
            "c-owner", extraction_profile="chat")
    rows = await _rows()
    assert stored == [] and [r.id for r in rows] == [mid]
    assert _live(rows) == [], "the closed intention stays closed"


@pytest.mark.integration
@pytest.mark.parametrize("origin, about", [("thought", "self"), ("did", "self"), ("read", "contact")])
async def test_in_owners_turn_only_his_own_words_count_as_said_again(clean_db, monkeypatch, origin, about):
    from services.memory_service import Memory, store_memories_with_conflict_detection

    _extractor_returns(monkeypatch, [])
    content = "Texted Parker that the lawn crew comes Thursday"
    mid = await _seed(content=content, valid_until=datetime.utcnow() - timedelta(days=1), memory_type="context",
                      origin=origin, about=about)
    stored = await store_memories_with_conflict_detection([
        Memory(id=None, content=content, memory_type="context", importance=0.6, origin=origin, about=about,
               source_ref=_ref(conversation_id="c-entry", turn_source="chat", profile="chat")),
    ])
    rows = await _rows()
    assert stored == [] and [r.id for r in rows] == [mid], f"{origin}/{about} re-lifted"
