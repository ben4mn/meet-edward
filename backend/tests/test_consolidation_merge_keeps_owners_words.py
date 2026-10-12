"""Consolidation's merge step never rewrites or deletes what Ellis said
(pre-deploy review, 2026-09-27).

H1's guard in ``store_memories_with_conflict_detection`` keeps a wake's
paraphrase of a ``heard``/``owner`` row beside it (KEEP_BOTH) instead of
writing the wake's words into Ellis's row. One hop later the hourly cycle
undid it: step 8 merges any two cluster members at similarity >= 0.78 with
no origin/about check, keeps whichever id the model listed first with the
LLM's merged text, and hard-deletes the other. So either Ellis's row came to
hold the wake's words (rendered under "Standing instructions from Ellis"),
or Ellis's row was deleted and the paraphrase survived. One of Ellis's
instructions was a merge target three times on 2026-09-27 at 0.81–0.82,
above the merge threshold.

Contract pinned here: Ellis's row is never merged with a row that is not
his; both stay exactly as they are and no merge call is spent. Two that
are not his still merge.

Nor are two of his own rows merged (decision (a), 2026-09-27). They did
merge — every word was his — but the merge is still an unattended LLM step
that rewrites one of them and hard-deletes the other: the live near-
duplicates at 0.78 were mostly separate episodes (a lawn visit in July and
the September reschedule, "Parker said" and "Parker asked") that a merge
would have folded into one, and the pinned instructions Ellis confirmed
(``owner_confirmed``) are heard/owner rows too. Ellis restating something
reinforces his row at store time, in his own turn.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

OWNERS = "Ellis says do not cancel the lawn-mowing service"
PARAPHRASE = "Ellis says do not cancel the lawn-mowing service for now"


class _LLM:
    def __init__(self, cluster):
        self._cluster = cluster
        self.calls = 0
        self.merges = 0

    async def ainvoke(self, messages):
        self.calls += 1
        text = " ".join(str(getattr(m, "content", m)) for m in messages)

        class _R:
            def __init__(self, c):
                self.content = c

        if "Merge these two related memories" in text:
            self.merges += 1
            return _R("Ellis wants the lawn-mowing service kept for now; a wake noticed it again")
        if self.calls == 1:
            return _R(self._cluster)
        return _R('{"contradictions": []}')


def _use(monkeypatch, stub):
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)
    monkeypatch.setattr("services.memory_service.build_background_llm", lambda *a, **k: stub)


async def _insert(content, origin, about, memory_type="instruction", source_ref=None):
    from services import database, memory_service
    from services.database import MemoryModel

    mid = str(uuid.uuid4())
    async with database.async_session() as session:
        session.add(MemoryModel(
            id=mid, content=content, embedding=memory_service.get_embedding(content),
            memory_type=memory_type, importance=0.8, temporal_nature="timeless",
            source_type="user_stated" if origin == "heard" else "inferred", confidence=0.9,
            origin=origin, about=about, last_accessed=datetime.utcnow(), source_ref=source_ref,
        ))
        await session.commit()
    return mid


async def _filler():
    """A cycle with fewer than three candidates is skipped."""
    await _insert("Biscuit's walk is at six in the morning", "heard", "owner", "fact")


async def _config():
    from services import database
    from services.database import ConsolidationConfigModel

    async with database.async_session() as session:
        cfg = (await session.execute(
            select(ConsolidationConfigModel).where(ConsolidationConfigModel.id == "default"))).scalar_one_or_none()
        if cfg is None:
            cfg = ConsolidationConfigModel(id="default")
            session.add(cfg)
        cfg.max_cluster_size, cfg.max_pairs_per_cluster, cfg.max_llm_calls_per_cycle = 100, 100, 20
        await session.commit()


async def _row(mid):
    from services import database
    from services.database import MemoryModel

    async with database.async_session() as session:
        return (await session.execute(select(MemoryModel).where(MemoryModel.id == mid))).scalar_one_or_none()


def _cluster(*ids):
    listed = ", ".join(f'"{i}"' for i in ids)
    return f'{{"clusters": [{{"label": "lawn", "type": "same_topic", "memory_ids": [{listed}]}}]}}'


@pytest.mark.parametrize("owners_first", [True, False])
async def test_a_merge_never_rewrites_or_deletes_owners_row(clean_db, monkeypatch, owners_first):
    from services import memory_service
    from services.consolidation_service import _run_consolidation_cycle

    owners = await _insert(OWNERS, "heard", "owner")
    wake = await _insert(PARAPHRASE, "read", "owner")
    # The pair is above the merge threshold.
    similar = await memory_service.find_similar_memories(OWNERS, threshold=0.78, limit=5)
    assert wake in {m.id for m, _ in similar}

    await _filler()
    await _config()
    stub = _LLM(_cluster(*((owners, wake) if owners_first else (wake, owners))))
    _use(monkeypatch, stub)

    await _run_consolidation_cycle()

    kept = await _row(owners)
    assert kept is not None, "Ellis's row was deleted by a merge"
    assert kept.content == OWNERS and (kept.origin, kept.about) == ("heard", "owner")
    assert (await _row(wake)).content == PARAPHRASE
    assert stub.merges == 0, "no merge call is spent on a pair with Ellis's words"


async def test_other_duplicates_still_merge(clean_db, monkeypatch):
    from services.consolidation_service import _run_consolidation_cycle

    a = await _insert(OWNERS.replace("Ellis says", "Rowan says"), "read", "contact", "fact")
    b = await _insert(PARAPHRASE.replace("Ellis says", "Rowan says"), "read", "contact", "fact")
    await _filler()
    await _config()
    stub = _LLM(_cluster(a, b))
    _use(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.merges == 1
    assert (await _row(a)) is None or (await _row(b)) is None


@pytest.mark.parametrize("source_ref", [None, '{"kind": "owner_confirmed"}'])
async def test_two_of_owners_own_rows_are_never_merged(clean_db, monkeypatch, source_ref):
    from services.consolidation_service import _run_consolidation_cycle

    a = await _insert(OWNERS, "heard", "owner", source_ref=source_ref)
    b = await _insert(PARAPHRASE, "heard", "owner", source_ref=source_ref)
    await _filler()
    await _config()
    stub = _LLM(_cluster(a, b))
    _use(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.merges == 0, "no merge call is spent on two of Ellis's rows"
    assert (await _row(a)).content == OWNERS
    assert (await _row(b)).content == PARAPHRASE
