"""Nightly GC of superseded LangGraph checkpoints.

``langgraph-checkpoint-postgres`` never deletes anything: every turn adds a
checkpoint, blobs for the channels that changed, and pending writes. By
2026-09-05 the three tables held 2.3 GB, every one of the 19,917 write rows
belonged to a superseded checkpoint, and the 228 GB disk had 23 GB free on a
machine that already went down once from a full disk (2026-08-12). Edward
only ever reads the latest checkpoint per thread; nothing calls
``aget_state_history``.

Pure selection functions are tested directly. The DB tests seed real
checkpoints through ``AsyncPostgresSaver`` so the keep/drop logic is checked
against the library's own storage layout, not a hand-rolled imitation.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text


# ── pure selection ────────────────────────────────────────────────────


def _cid(n: int) -> str:
    # langgraph ids are uuid6 (time-ordered, lexically sortable); a zero-padded
    # counter has the same ordering property for these tests.
    return f"{n:032d}"


def test_keep_newest_per_thread():
    from scripts.gc_checkpoints import select_checkpoints_to_drop

    rows = [("t1", "", _cid(i)) for i in range(30)] + [("t2", "", _cid(i)) for i in range(5)]
    drop = select_checkpoints_to_drop(rows, keep=20)
    assert len(drop) == 10
    assert all(t == "t1" for t, _, _ in drop)
    assert {c for _, _, c in drop} == {_cid(i) for i in range(10)}


def test_never_drops_latest_even_with_keep_1():
    from scripts.gc_checkpoints import select_checkpoints_to_drop

    rows = [("t1", "", _cid(3)), ("t1", "", _cid(9)), ("t1", "", _cid(5))]
    drop = select_checkpoints_to_drop(rows, keep=1)
    assert ("t1", "", _cid(9)) not in drop
    assert len(drop) == 2


def test_keep_is_per_namespace():
    from scripts.gc_checkpoints import select_checkpoints_to_drop

    rows = [("t1", "", _cid(i)) for i in range(3)] + [("t1", "sub", _cid(i)) for i in range(3)]
    assert select_checkpoints_to_drop(rows, keep=3) == []


def test_orphan_blob_keys_survive_only_when_referenced():
    from scripts.gc_checkpoints import select_orphan_blob_keys

    surviving = [
        ("t1", "", {"messages": "00000002.0.1", "model": 7}),  # int version (v1 layout)
    ]
    blobs = [
        ("t1", "", "messages", "00000002.0.1"),  # referenced
        ("t1", "", "messages", "00000001.0.1"),  # superseded
        ("t1", "", "model", "7"),                # referenced, int vs str
        ("t2", "", "messages", "00000002.0.1"),  # other thread, no checkpoints left
    ]
    orphans = select_orphan_blob_keys(surviving, blobs)
    assert orphans == [("t1", "", "messages", "00000001.0.1"), ("t2", "", "messages", "00000002.0.1")]


def test_orphan_write_keys_are_those_without_a_checkpoint():
    from scripts.gc_checkpoints import select_orphan_write_keys

    surviving = {("t1", "", _cid(2))}
    writes = [("t1", "", _cid(2)), ("t1", "", _cid(1)), ("t9", "", _cid(0))]
    assert select_orphan_write_keys(surviving, writes) == [("t1", "", _cid(1)), ("t9", "", _cid(0))]


# ── against the real saver ────────────────────────────────────────────


async def _seed(saver, thread_id: str, turns: int):
    """Write ``turns`` chained checkpoints; ``messages`` changes every turn,
    ``model`` only on the first, so blobs are shared across checkpoints the
    way they are in production."""
    from langgraph.checkpoint.base import empty_checkpoint

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    for i in range(turns):
        cp = empty_checkpoint()
        cp["channel_values"] = {"messages": [f"m{j}" for j in range(i + 1)], "model": "gpt-6-astra"}
        cp["channel_versions"] = {"messages": f"{i + 1:032d}.0.1", "model": f"{1:032d}.0.1"}
        new_versions = {"messages": cp["channel_versions"]["messages"]}
        if i == 0:
            new_versions["model"] = cp["channel_versions"]["model"]
        config = await saver.aput(config, cp, {"step": i, "source": "loop"}, new_versions)
        await saver.aput_writes(config, [("messages", f"pending{i}")], task_id=f"task{i}")
    return config


@pytest.fixture
async def saver(clean_db):
    from psycopg import AsyncConnection
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        await AsyncPostgresSaver(conn).setup()
        saver = AsyncPostgresSaver(conn)
        from services.database import async_session
        async with async_session() as session:
            await session.execute(text("TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes"))
            await session.commit()
        yield saver


async def _counts():
    from services.database import async_session
    async with async_session() as session:
        out = {}
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            out[t] = (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()
        return out


async def test_dry_run_writes_nothing(saver):
    from scripts.gc_checkpoints import run_gc

    for t in ("a", "b", "c"):
        await _seed(saver, t, 30)
    before = await _counts()
    assert before["checkpoints"] == 90

    report = await run_gc(keep=20, apply=False)

    assert await _counts() == before
    assert report["checkpoints_to_drop"] == 30
    assert report["writes_to_drop"] == 30
    assert report["blobs_to_drop"] == 30  # one superseded ``messages`` blob per dropped checkpoint
    assert report["applied"] is False


async def test_apply_preserves_latest_state(saver):
    from scripts.gc_checkpoints import run_gc

    latest_before = {}
    for t in ("a", "b", "c"):
        cfg = await _seed(saver, t, 30)
        tup = await saver.aget_tuple({"configurable": {"thread_id": t, "checkpoint_ns": ""}})
        latest_before[t] = (tup.checkpoint["id"], tup.checkpoint["channel_values"], cfg)

    report = await run_gc(keep=20, apply=True)
    assert report["applied"] is True

    after = await _counts()
    assert after["checkpoints"] == 60
    assert after["writes"] if False else after["checkpoint_writes"] == 60
    for t, (cid, values, cfg) in latest_before.items():
        tup = await saver.aget_tuple({"configurable": {"thread_id": t, "checkpoint_ns": ""}})
        assert tup.checkpoint["id"] == cid
        assert tup.checkpoint["channel_values"] == values, "latest state must round-trip after GC"
        assert tup.pending_writes, "writes for the surviving checkpoint must be kept"
        # the shared ``model`` blob from turn 0 is still referenced and must survive
        assert tup.checkpoint["channel_values"]["model"] == "gpt-6-astra"
