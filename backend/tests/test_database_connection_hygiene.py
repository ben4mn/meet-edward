"""
Connection-hygiene guards on the SQLAlchemy engine and init_db migrations.

Context: a backend crash on 2026-05-22 left ~15 leaked Postgres sessions
holding locks on heartbeat_config for 4 days. Postgres never reaped the
dead clients (no TCP keepalive set), and a fresh backend's pool exhausted
behind the lock-blocked queries (no lock/statement_timeout on the DDL
transaction). These tests pin the two configuration knobs that prevent
that failure mode from recurring.
"""

import pytest
from sqlalchemy import text


def test_engine_connect_args_enable_tcp_keepalives():
    """Asserts the engine asks Postgres to enable per-session TCP keepalives
    so an orphaned client socket is detected within minutes, not days."""
    from services.database import CONNECT_ARGS

    server_settings = CONNECT_ARGS.get("server_settings", {})
    assert server_settings.get("tcp_keepalives_idle"), (
        "tcp_keepalives_idle missing — orphaned client sockets won't be reaped"
    )
    assert server_settings.get("tcp_keepalives_interval"), (
        "tcp_keepalives_interval missing"
    )
    assert server_settings.get("tcp_keepalives_count"), (
        "tcp_keepalives_count missing"
    )


@pytest.mark.asyncio
async def test_init_db_migrations_set_lock_and_statement_timeout(monkeypatch):
    """Asserts the migration transaction in init_db() opens with
    SET LOCAL lock_timeout + SET LOCAL statement_timeout so a hung
    ALTER TABLE fails fast instead of pinning the table for days."""
    from services import database

    captured: list[str] = []

    class _FakeResult:
        def scalar(self):
            return None

        def scalar_one_or_none(self):
            return None

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def first(self):
            return None

        def all(self):
            return []

        def keys(self):
            return []

        def mappings(self):
            return self

    class _FakeConn:
        async def execute(self, clause, *args, **kwargs):
            sql = str(getattr(clause, "text", clause))
            captured.append(sql)
            return _FakeResult()

        async def run_sync(self, fn, *args, **kwargs):
            return None

    class _FakeBeginCtx:
        async def __aenter__(self):
            return _FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeBeginCtx()

        def connect(self):
            return _FakeBeginCtx()

    monkeypatch.setattr(database, "engine", _FakeEngine())

    try:
        await database.init_db()
    except Exception:
        # init_db may rely on real schema queries we stubbed away; we only
        # care about the SQL ordering. Swallow downstream failures.
        pass

    joined = "\n".join(captured).lower()
    assert "set local lock_timeout" in joined, (
        "init_db migrations must SET LOCAL lock_timeout to fail fast on "
        "lock contention. Captured SQL was:\n" + joined[:2000]
    )
    assert "set local statement_timeout" in joined, (
        "init_db migrations must SET LOCAL statement_timeout to bound DDL "
        "duration. Captured SQL was:\n" + joined[:2000]
    )

    # And they must come before any ALTER TABLE.
    lower_stmts = [s.lower() for s in captured]
    first_alter = next(
        (i for i, s in enumerate(lower_stmts) if "alter table" in s), None
    )
    first_set = next(
        (i for i, s in enumerate(lower_stmts) if "set local lock_timeout" in s),
        None,
    )
    assert first_set is not None and (
        first_alter is None or first_set < first_alter
    ), "SET LOCAL lock_timeout must precede any ALTER TABLE in init_db"
