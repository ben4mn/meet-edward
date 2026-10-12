"""Ellis restating an instruction whose row lapsed stores it live again
(chunk 1 integration, 2026-09-27).

Batch A2's review, item 7: the decay pass can now retire an instruction
(the instruction horizon, and a contradiction's loser); when Ellis said it
again, conflict detection matched the retired row at >= 0.92 and only
reinforced it — the restatement was absorbed into a row no wake reads, and
Ellis's standing instruction was gone. Batch A1's live-only conflict
detection (``dd4011b``, and ``is_passive_relift`` in ``6bdc4d1``, which
keeps retired rows in view only for extraction from a turn where Ellis is not
speaking) was expected to close it; this pins that it does, through the
whole extraction path of Ellis's own chat and voice turns.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

INSTRUCTION = "Never text the family group chat on Ellis's behalf"


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


async def _seed_lapsed(lapsed_at):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=INSTRUCTION, memory_type="instruction", importance=0.8, origin="heard",
                          about="owner", temporal_nature="evolving", confidence=0.9,
                          embedding=get_embedding(INSTRUCTION), valid_until=lapsed_at, reinforcement_count=2,
                          tier="belief", source_ref=_ref(conversation_id="c-old", turn_source="chat",
                                                         profile="chat"))
        s.add(row)
        await s.commit()
        return row.id


async def _rows():
    from sqlalchemy import select

    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(MemoryModel))).scalars().all())


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

    async def _no_resolver(existing, memory, similarity):
        raise AssertionError(f"the resolver was consulted at {similarity:.2f} (against {existing.id})")

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "_spawn_background", lambda coro: coro.close())
    monkeypatch.setattr(memory_service, "resolve_memory_conflict", _no_resolver)


@pytest.mark.integration
@pytest.mark.parametrize("source", ["chat", "voice"])
async def test_owner_restating_a_lapsed_instruction_stores_it_live(clean_db, monkeypatch, source):
    from services.memory_service import extract_and_store_memories

    lapsed_at = datetime.utcnow() - timedelta(days=3)
    old = await _seed_lapsed(lapsed_at)
    _extractor_returns(monkeypatch, [{
        "content": INSTRUCTION, "memory_type": "instruction", "importance": 0.8,
        "temporal_nature": "evolving", "source_type": "user_stated", "confidence": 0.9,
        "origin": "heard", "about": "owner",
    }])
    with _Turn(source, "c-owner"):
        stored = await extract_and_store_memories(
            [{"role": "user", "content": "Reminder: never text the family group chat for me."},
             {"role": "assistant", "content": "Understood — I won't."}],
            "c-owner", extraction_profile="chat")

    rows = {r.id: r for r in await _rows()}
    now = datetime.utcnow()
    live = [r for r in rows.values() if r.valid_until is None or r.valid_until > now]
    assert len(live) == 1, "Ellis's restatement is a live row again"
    new = live[0]
    assert new.id != old and new.content == INSTRUCTION
    assert (new.memory_type, new.origin, new.about) == ("instruction", "heard", "owner")
    assert [m.id for m in stored] == [new.id]
    # The lapsed row is history: not revived, not reinforced.
    assert abs((rows[old].valid_until - lapsed_at).total_seconds()) < 1
    assert rows[old].reinforcement_count == 2


@pytest.mark.integration
async def test_the_live_restatement_is_what_the_next_wake_reads_as_his_instruction(clean_db, monkeypatch):
    """The point of storing it live: a wake's "Standing instructions from
    Ellis" lists it again."""
    from datetime import timezone

    from services import mind_service
    from services.memory_service import extract_and_store_memories

    await _seed_lapsed(datetime.utcnow() - timedelta(days=3))
    before = "\n".join(await mind_service._instructions_source(datetime.now(timezone.utc)))
    _extractor_returns(monkeypatch, [{
        "content": INSTRUCTION, "memory_type": "instruction", "importance": 0.8,
        "temporal_nature": "evolving", "source_type": "user_stated", "confidence": 0.9,
        "origin": "heard", "about": "owner",
    }])
    with _Turn("chat", "c-owner"):
        await extract_and_store_memories(
            [{"role": "user", "content": "Never text the family group chat for me."},
             {"role": "assistant", "content": "Got it."}],
            "c-owner", extraction_profile="chat")
    after = "\n".join(await mind_service._instructions_source(datetime.now(timezone.utc)))
    assert INSTRUCTION not in before
    assert INSTRUCTION in after
