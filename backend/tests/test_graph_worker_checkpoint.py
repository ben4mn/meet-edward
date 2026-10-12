"""
Regression: after a successful add_episode, the worker must issue
``CHECKPOINT`` against Kuzu so the WAL stays bounded.

Real-world: ``graphiti.kuzu.wal`` reached 11 MB while the main
``graphiti.kuzu`` file hadn't been written in 28 days — every worker
restart replayed the entire WAL, and a partial replay corrupted enough
page state that the next ``NodeTable::update`` segfaulted on a bad row
offset (sign-extended ``-8`` dereferenced as a pointer → ``0x7fffffff8``
in the crash report). 53 Python "quit unexpectedly" dialogs in <1 week.

Kuzu only folds the WAL into the main file on graceful shutdown or an
explicit ``CHECKPOINT`` statement. The worker dies via SIGSEGV (not
graceful), so unless we checkpoint after each success the WAL grows
without bound until a replay-induced corruption produces the next crash.

Contract this test pins:
    For every queue row that ingests cleanly, the worker MUST invoke
    ``CHECKPOINT`` against the underlying Kuzu connection before
    marking the row done.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def isolated_queue(db_available):
    if not db_available:
        pytest.skip("Test database not reachable")

    from services import database
    from sqlalchemy import text

    await database.init_db()
    async with database.engine.begin() as conn:
        await conn.execute(text("TRUNCATE graph_ingest_queue RESTART IDENTITY"))
    yield
    await database.engine.dispose()


def _make_fake_graphiti() -> AsyncMock:
    """Graphiti stub whose ``driver.client.execute`` records every call
    so the test can assert CHECKPOINT was issued."""
    fake_client = AsyncMock()
    fake_client.execute = AsyncMock(return_value=None)
    fake_driver = AsyncMock()
    fake_driver.client = fake_client
    fake_graphiti = AsyncMock()
    fake_graphiti.add_episode = AsyncMock(return_value=None)
    fake_graphiti.driver = fake_driver
    return fake_graphiti


@pytest.mark.integration
@pytest.mark.asyncio
async def test_successful_ingest_checkpoints_kuzu(isolated_queue, monkeypatch) -> None:
    from services.database import async_session, GraphIngestQueueModel
    from workers import graph_worker

    row_id = str(uuid.uuid4())
    async with async_session() as s:
        s.add(GraphIngestQueueModel(
            id=row_id,
            memory_id=str(uuid.uuid4()),
            content="harmless content",
            status="pending",
            attempts=0,
            enqueued_at=datetime.utcnow(),
        ))
        await s.commit()

    fake_graphiti = _make_fake_graphiti()
    monkeypatch.setattr(graph_worker, "_GRAPHITI", fake_graphiti)

    processed = await graph_worker._process_one_pending()
    assert processed is True, "queue had a pending row; worker should have processed it"

    async with async_session() as s:
        row = (await s.execute(
            select(GraphIngestQueueModel).where(GraphIngestQueueModel.id == row_id)
        )).scalar_one()
    assert row.status == "done", f"row should be marked done on success, got {row.status!r}"

    execute_calls = fake_graphiti.driver.client.execute.call_args_list
    statements = [
        (call.args[0] if call.args else call.kwargs.get("query", ""))
        for call in execute_calls
    ]
    assert any("CHECKPOINT" in str(stmt).upper() for stmt in statements), (
        "Expected the worker to issue CHECKPOINT against Kuzu after a "
        "successful add_episode so the WAL stays bounded. Statements "
        f"observed on driver.client.execute: {statements!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_ingest_does_not_checkpoint(isolated_queue, monkeypatch) -> None:
    """If add_episode raises, we must NOT issue CHECKPOINT — a partial
    transaction may have left in-memory state inconsistent, and writing
    that to the main file is exactly the corruption we're avoiding."""
    from services.database import async_session, GraphIngestQueueModel
    from workers import graph_worker

    row_id = str(uuid.uuid4())
    async with async_session() as s:
        s.add(GraphIngestQueueModel(
            id=row_id,
            memory_id=str(uuid.uuid4()),
            content="will fail",
            status="pending",
            attempts=0,
            enqueued_at=datetime.utcnow(),
        ))
        await s.commit()

    fake_graphiti = _make_fake_graphiti()
    fake_graphiti.add_episode = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(graph_worker, "_GRAPHITI", fake_graphiti)

    processed = await graph_worker._process_one_pending()
    assert processed is True

    async with async_session() as s:
        row = (await s.execute(
            select(GraphIngestQueueModel).where(GraphIngestQueueModel.id == row_id)
        )).scalar_one()
    assert row.status == "failed"

    execute_calls = fake_graphiti.driver.client.execute.call_args_list
    statements = [
        (call.args[0] if call.args else call.kwargs.get("query", ""))
        for call in execute_calls
    ]
    assert not any("CHECKPOINT" in str(stmt).upper() for stmt in statements), (
        "CHECKPOINT must NOT be issued after a failed ingest — folding a "
        "partial / aborted transaction into the main file is what produces "
        f"the corrupt state we're trying to avoid. Statements: {statements!r}"
    )
