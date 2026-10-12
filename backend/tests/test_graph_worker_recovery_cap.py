"""
Regression: graph_worker._recover_stuck_processing must NOT re-pend a
row that has already been retried too many times. Without an attempts
cap, a row whose ingest segfaults Kuzu loops forever:

  worker boots → picks row → Kuzu segfault → launchd respawns →
  recovery flips 'processing' back to 'pending' → same row again →
  segfault → ...

Real-world: 6+ respawns in 4 minutes today, every cycle re-loading the
350MB embedding model. CPU thrash and the queue can never drain past
the poison pill.

After the fix:
  - rows with attempts < MAX_ATTEMPTS → 'pending' (recoverable)
  - rows with attempts >= MAX_ATTEMPTS → 'failed' with a clear error_msg
    so an operator (or a re-queue script) can investigate later.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recover_stuck_processing_caps_retries(isolated_queue) -> None:
    from services.database import async_session, GraphIngestQueueModel
    from workers.graph_worker import _recover_stuck_processing, MAX_RECOVERY_ATTEMPTS

    fresh_id = str(uuid.uuid4())
    poisoned_id = str(uuid.uuid4())

    async with async_session() as s:
        s.add(GraphIngestQueueModel(
            id=fresh_id,
            memory_id=str(uuid.uuid4()),
            content="harmless content",
            status="processing",
            attempts=1,
            enqueued_at=datetime.utcnow(),
        ))
        s.add(GraphIngestQueueModel(
            id=poisoned_id,
            memory_id=str(uuid.uuid4()),
            content="content that segfaults Kuzu",
            status="processing",
            attempts=MAX_RECOVERY_ATTEMPTS,
            enqueued_at=datetime.utcnow(),
        ))
        await s.commit()

    recovered = await _recover_stuck_processing()
    assert recovered == 1, f"only the fresh row should be re-pended, got {recovered}"

    async with async_session() as s:
        rows = (await s.execute(select(GraphIngestQueueModel))).scalars().all()
        by_id = {r.id: r for r in rows}

        assert by_id[fresh_id].status == "pending", (
            f"fresh row (attempts={by_id[fresh_id].attempts}) should re-queue, "
            f"got status={by_id[fresh_id].status}"
        )

        poisoned = by_id[poisoned_id]
        assert poisoned.status == "failed", (
            f"poisoned row (attempts={poisoned.attempts} >= cap={MAX_RECOVERY_ATTEMPTS}) "
            f"must be quarantined to 'failed', got status={poisoned.status}"
        )
        assert poisoned.error_msg, "quarantined row must carry an error_msg explaining why"
        assert "attempts" in (poisoned.error_msg or "").lower(), (
            f"error_msg should mention the attempt cap. got: {poisoned.error_msg!r}"
        )
        assert poisoned.processed_at is not None, (
            "processed_at must be set when quarantining so the row sorts after live work"
        )
