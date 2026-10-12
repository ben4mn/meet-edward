"""The backend suite runs on its own Postgres cluster, never production's.

On 2026-09-26 21:31–21:40 CDT a full run of this suite (``clean_db``
TRUNCATEs ~60 tables before each of ~4,800 tests) made the production
cluster's checkpointer fsync ~194K relation files: commits stalled and the
live backend's pool (10 + 5) ran out — 20 QueuePool tracebacks. The same
stall signature is in the Postgres log on 9/5, 9/7, 9/10, 9/11, 9/12 and
9/25. ``edward_test`` now lives on a second cluster on :5433 with fsync
off (``scripts/test-pg-cluster.sh``), and nothing here will use :5432.
"""

import os

import pytest

from services.sandbox_env import (
    DEFAULT_TEST_DATABASE_URL,
    PRODUCTION_PG_PORT,
    TEST_PG_PORT,
    db_port,
    is_test_db_url,
    refuse_production_cluster,
    safe_test_database_url,
    sandbox_env,
)


def test_the_test_cluster_is_not_the_production_port():
    assert TEST_PG_PORT == 5433
    assert PRODUCTION_PG_PORT == 5432
    assert db_port(DEFAULT_TEST_DATABASE_URL) == TEST_PG_PORT


def test_a_url_without_a_port_is_the_production_port():
    assert db_port("postgresql://x:y@localhost/edward_test") == 5432
    assert db_port("postgresql+asyncpg://edward:edward@localhost:5433/edward_test") == 5433


def test_a_test_database_on_the_production_cluster_is_not_a_test_url():
    assert not is_test_db_url("postgresql+asyncpg://edward:edward@localhost:5432/edward_test")
    assert not is_test_db_url("postgresql://x:y@localhost/other_test")
    assert is_test_db_url("postgresql+asyncpg://edward:edward@localhost:5433/edward_test")


def test_an_inherited_production_cluster_test_url_is_moved_to_the_test_cluster(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    url = safe_test_database_url("postgresql+asyncpg://edward:edward@localhost:5432/edward_test")
    assert db_port(url) == TEST_PG_PORT
    assert url.endswith("/edward_test")


def test_spawned_work_never_gets_the_production_cluster(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://edward:edward@localhost:5432/edward")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://edward:edward@localhost:5432/edward_test")
    env = sandbox_env()
    assert db_port(env["DATABASE_URL"]) == TEST_PG_PORT
    assert db_port(env["TEST_DATABASE_URL"]) == TEST_PG_PORT


def test_refuse_production_cluster_raises_for_5432():
    with pytest.raises(RuntimeError, match="5433"):
        refuse_production_cluster("postgresql+asyncpg://edward:edward@localhost:5432/edward_test")
    refuse_production_cluster("postgresql+asyncpg://edward:edward@localhost:5433/edward_test")


def test_this_process_runs_on_the_test_cluster():
    assert db_port(os.environ["DATABASE_URL"]) == TEST_PG_PORT
    assert db_port(os.environ["TEST_DATABASE_URL"]) == TEST_PG_PORT


def test_the_app_engine_points_at_the_test_cluster():
    from services import database

    assert db_port(str(database.DATABASE_URL)) == TEST_PG_PORT


@pytest.mark.asyncio
async def test_clean_db_builds_the_schema_once_per_session(clean_db):
    from tests import conftest

    assert conftest.INIT_DB_RUNS == 1


@pytest.mark.asyncio
async def test_a_second_clean_db_does_not_rebuild_the_schema(clean_db):
    from tests import conftest

    assert conftest.INIT_DB_RUNS == 1
