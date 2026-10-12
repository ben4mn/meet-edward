"""The tier backfill never merges Ellis's row, a retired row, or an id the
model made up (review of decision (a), 2026-09-27).

``POST /api/memories/backfill-tiers`` runs ``backfill_memory_tiers``: rows at
>= 0.92 similarity are folded into the older one and hard-deleted with no
LLM call, and the model's MERGE groups rewrite the kept row with its text
and hard-delete the rest. Consolidation's merge step skips any pair with a
heard/owner row (decision (a)); this path had no such check, and none for
``valid_until`` either — a retired row could absorb a live one, or have its
text merged into one. It also deleted whatever ids the model returned,
inside the group or not.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

_T0 = datetime.utcnow() - timedelta(days=30)


async def _row(content: str, *, origin: str = "read", about: str = "contact", valid_until=None,
               age_rank: int = 0) -> str:
    """A memory inserted as it is (``store_memory`` would reinforce an
    identical row instead). ``age_rank`` orders ``created_at``: the backfill
    walks oldest first and folds newer rows into older ones."""
    from services import database, memory_service
    from services.database import MemoryModel

    mid = str(uuid.uuid4())
    async with database.async_session() as session:
        session.add(MemoryModel(id=mid, content=content, embedding=memory_service.get_embedding(content),
                                memory_type="fact", importance=0.6, temporal_nature="timeless",
                                source_type="user_stated", confidence=0.9, origin=origin, about=about,
                                valid_until=valid_until, created_at=_T0 + timedelta(minutes=age_rank),
                                last_accessed=_T0))
        await session.commit()
    return mid


async def _content(mid: str):
    from services import database
    from services.database import MemoryModel

    async with database.async_session() as session:
        return (await session.execute(select(MemoryModel.content).where(MemoryModel.id == mid))).scalar_one_or_none()


class _MergeLLM:
    """Answers every classification with one MERGE decision per group, or
    the decision given."""

    def __init__(self, decision=None):
        self.decision = decision
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        prompt = str(messages[-1].content)
        if self.decision is not None:
            groups = [self.decision]
        else:
            groups = []
            for g in json.loads(prompt.split("Memory groups:", 1)[1]):
                groups.append({"action": "MERGE", "keep_id": g[0]["id"],
                               "absorb_ids": [m["id"] for m in g[1:]], "merged_content": "MERGED TEXT"})

        class _R:
            content = json.dumps({"groups": groups})

        return _R()


def _use(monkeypatch, llm) -> None:
    monkeypatch.setattr("services.memory_service.build_background_llm", lambda *a, **k: llm)
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: llm)


async def test_owners_row_is_never_folded_into_another(clean_db, monkeypatch):
    from services.memory_service import backfill_memory_tiers

    _use(monkeypatch, _MergeLLM())
    other = await _row("Ellis's gym is on Pearl Street", age_rank=0)
    owners = await _row("Ellis's gym is on Pearl Street", origin="heard", about="owner", age_rank=1)

    await backfill_memory_tiers()

    assert await _content(owners) == "Ellis's gym is on Pearl Street"
    assert await _content(other) is not None


async def test_two_of_owners_own_rows_are_not_merged(clean_db, monkeypatch):
    from services.memory_service import backfill_memory_tiers

    _use(monkeypatch, _MergeLLM())
    first = await _row("Ellis walks Biscuit at six", origin="heard", about="owner", age_rank=0)
    second = await _row("Ellis walks Biscuit at six", origin="heard", about="owner", age_rank=1)

    await backfill_memory_tiers()

    assert await _content(first) is not None and await _content(second) is not None


async def test_the_model_never_merges_owners_row(clean_db, monkeypatch):
    """Similar, not identical (~0.85 with the real model and the fake
    embeddings alike): the pair goes to the model, which would merge it."""
    from services.memory_service import backfill_memory_tiers

    llm = _MergeLLM()
    _use(monkeypatch, llm)
    other = await _row("Parker works at the office downtown", age_rank=0)
    owners = await _row("Parker works at the office downtown with Theo", origin="heard", about="owner", age_rank=1)

    await backfill_memory_tiers()

    assert await _content(owners) == "Parker works at the office downtown with Theo"
    assert await _content(other) == "Parker works at the office downtown"


async def test_a_retired_row_takes_no_part(clean_db, monkeypatch):
    from services.memory_service import backfill_memory_tiers

    _use(monkeypatch, _MergeLLM())
    gone = datetime.utcnow() - timedelta(days=1)
    retired_first = await _row("Rowan's appointment is Tuesday", valid_until=gone, age_rank=0)
    live = await _row("Rowan's appointment is Tuesday", age_rank=1)
    live_first = await _row("Rowan's surgery went well", age_rank=2)
    retired_second = await _row("Rowan's surgery went well", valid_until=gone, age_rank=3)

    await backfill_memory_tiers()

    assert await _content(live) is not None, "a retired row never absorbs a live one"
    assert await _content(retired_first) is not None
    assert await _content(retired_second) is not None, "nor is it folded into one"
    assert await _content(live_first) == "Rowan's surgery went well"


async def test_the_model_can_only_merge_rows_in_the_group(clean_db, monkeypatch):
    from services.memory_service import backfill_memory_tiers

    a = await _row("Parker works at the office downtown", age_rank=0)
    b = await _row("Parker works at the office downtown with Theo", age_rank=1)
    outside = await _row("Biscuit's vet is on Pearl Street", age_rank=2)
    llm = _MergeLLM({"action": "MERGE", "keep_id": a, "absorb_ids": [b, outside],
                     "merged_content": "MERGED TEXT"})
    _use(monkeypatch, llm)

    await backfill_memory_tiers()

    assert llm.calls >= 1
    assert await _content(outside) == "Biscuit's vet is on Pearl Street", "an id outside the group is never deleted"
    assert await _content(b) is not None and await _content(a) == "Parker works at the office downtown"
