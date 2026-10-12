"""Guard: a test run never turns on the Graphiti dual-write (2026-09-25).

``main.py``, ``services/voice_service.py`` and ``workers/graph_worker.py``
call ``load_dotenv(<repo>/.env)`` at import time. About twenty test files
import ``main``, so from the first of them on, every later test in the
process ran with the production ``GRAPHITI_ENABLED=true``: each
``store_memory`` fire-and-forgot a ``graph_ingest_queue`` row (and
``graph_memory_service.search`` would ask the live graph worker on
:8001). ``test_forget_service`` passed alone and failed in the full suite
with 5 queue rows instead of 1 — four of them written by the dual-write
after its own wipe.

conftest pins the graph flags off before anything can import ``main``;
``load_dotenv`` never overrides a variable that is already set. A test
that wants the dual-write turns it on with ``monkeypatch.setenv``.

It also truncates ``entities``, ``entity_mentions`` and
``graph_ingest_queue`` between tests: they are memory-derived data, not
configuration, and have no foreign key to ``memories`` for its CASCADE to
follow.
"""

from __future__ import annotations

import asyncio
import os

import pytest


@pytest.mark.unit
def test_the_repo_env_cannot_turn_on_the_graph_dual_write():
    import main  # noqa: F401 — loads the repo .env, as ~20 test files do
    import workers.graph_worker  # noqa: F401 — so does the worker
    from services import graph_memory_service

    assert not graph_memory_service.is_enabled(), (
        "GRAPHITI_ENABLED leaked into the test process from the repo .env"
    )
    assert os.environ.get("GRAPHITI_INGEST_ENABLED", "false").lower() not in ("1", "true", "yes", "on")


@pytest.mark.integration
async def test_a_memory_stored_after_main_is_imported_is_not_queued_for_the_graph(clean_db):
    from sqlalchemy import func, select

    import main  # noqa: F401
    from services.database import GraphIngestQueueModel, async_session
    from services.memory_service import Memory, store_memory

    stored = await store_memory(Memory(id=None, content="Alex loves bouldering", memory_type="fact",
                                       importance=0.5))
    for _ in range(10):  # the dual-write is a fire-and-forget task
        await asyncio.sleep(0.01)
    async with async_session() as s:
        n = (await s.execute(select(func.count()).select_from(GraphIngestQueueModel)
                             .where(GraphIngestQueueModel.memory_id == stored.id))).scalar()
    assert n == 0


@pytest.mark.integration
async def test_clean_db_hands_every_test_empty_graph_and_entity_tables(clean_db):
    from sqlalchemy import text

    from services.database import async_session

    async with async_session() as s:
        for table in ("entities", "entity_mentions", "graph_ingest_queue"):
            n = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            assert n == 0, f"{table} kept {n} rows from an earlier test"
