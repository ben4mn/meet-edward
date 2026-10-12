"""Consolidation's merge never folds rows of different lifetimes
(integration review, 2026-09-27).

Step 8 keeps the first cluster member with the LLM's merged text and its
own ``temporal_nature`` and ``valid_until``, and hard-deletes the other. A
new temporary preference now lapses 30 days after it is written, so a
one-off ("Ellis wanted the spicy deluxe for Saturday's order") merged with a
lasting taste ("Ellis likes the spicy deluxe") left one row that lapsed with
the one-off — the lasting row was gone. The other way round, Saturday's
order became an open-ended "likes" row. Replayed with real embeddings, the
pair scored 0.886, over the 0.78 merge threshold. Only the heard/owner
guard stood in the way, and 1,198 live rows are read/owner.

Contract pinned here: a pair whose lifetimes differ — a different
``temporal_nature``, or a horizon (``valid_until``) on one side only — is
never merged and costs no merge call. Two of the same lifetime still merge.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_consolidation_merge_keeps_owners_words import OWNERS, PARAPHRASE, _cluster, _config, _filler, _LLM, \
    _row, _use

pytestmark = pytest.mark.integration

ONE_OFF = PARAPHRASE.replace("Ellis says", "Rowan says")
LASTING = OWNERS.replace("Ellis says", "Rowan says")


async def _insert(content, temporal_nature, valid_until=None):
    from services import database, memory_service
    from services.database import MemoryModel

    mid = str(uuid.uuid4())
    async with database.async_session() as session:
        session.add(MemoryModel(
            id=mid, content=content, embedding=memory_service.get_embedding(content),
            memory_type="preference", importance=0.8, temporal_nature=temporal_nature, valid_until=valid_until,
            source_type="inferred", confidence=0.9, origin="read", about="owner", last_accessed=datetime.utcnow(),
        ))
        await session.commit()
    return mid


def _horizon():
    return datetime.utcnow() + timedelta(days=30)


@pytest.mark.parametrize("one_off_first", [True, False])
async def test_a_one_off_and_a_lasting_row_are_never_merged(clean_db, monkeypatch, one_off_first):
    from services.consolidation_service import _run_consolidation_cycle

    one_off = await _insert(ONE_OFF, "temporary", _horizon())
    lasting = await _insert(LASTING, "evolving")
    await _filler()
    await _config()
    stub = _LLM(_cluster(*((one_off, lasting) if one_off_first else (lasting, one_off))))
    _use(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.merges == 0, "no merge call is spent on rows of different lifetimes"
    kept_lasting, kept_one_off = await _row(lasting), await _row(one_off)
    assert kept_lasting is not None and kept_lasting.content == LASTING
    assert kept_lasting.valid_until is None and kept_lasting.temporal_nature == "evolving"
    assert kept_one_off is not None and kept_one_off.content == ONE_OFF
    assert kept_one_off.temporal_nature == "temporary" and kept_one_off.valid_until is not None


async def test_a_horizon_on_one_side_only_is_a_different_lifetime(clean_db, monkeypatch):
    """Two temporary rows, one written before horizons (none) and one after."""
    from services.consolidation_service import _run_consolidation_cycle

    dated = await _insert(ONE_OFF, "temporary", _horizon())
    open_ended = await _insert(LASTING, "temporary")
    await _filler()
    await _config()
    stub = _LLM(_cluster(open_ended, dated))
    _use(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.merges == 0
    assert (await _row(dated)) is not None and (await _row(open_ended)) is not None


@pytest.mark.parametrize("nature,horizon", [("temporary", True), ("evolving", False)])
async def test_rows_of_one_lifetime_still_merge(clean_db, monkeypatch, nature, horizon):
    from services.consolidation_service import _run_consolidation_cycle

    a = await _insert(ONE_OFF, nature, _horizon() if horizon else None)
    b = await _insert(LASTING, nature, _horizon() if horizon else None)
    await _filler()
    await _config()
    stub = _LLM(_cluster(a, b))
    _use(monkeypatch, stub)

    await _run_consolidation_cycle()

    assert stub.merges == 1
    assert (await _row(a)) is None or (await _row(b)) is None
