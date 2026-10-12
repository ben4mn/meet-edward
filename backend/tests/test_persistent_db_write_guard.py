"""Write-guard tests for query_persistent_db (persistent_db_service).

Incident 2026-07-05: user SQL from the LLM's query_persistent_db tool ran on
the app's full-privilege engine with `public` on the search_path, so it could
write any public table (all 21 rows in `skills` were mass-disabled this way).

Guard under test:
- User SQL executes on a low-privilege `edward_llm` engine that has no grants
  on `public` objects.
- If that role is unavailable (env password missing / connect failure), the
  service degrades to read-only: only SELECT/WITH/EXPLAIN statements run, on
  the old engine.
"""

import os

import pytest
import pytest_asyncio

from services import persistent_db_service as pdb


class _FakeResult:
    returns_rows = False
    rowcount = 1


class _FakeConn:
    def __init__(self, log):
        self._log = log

    async def execute(self, clause, *args, **kwargs):
        self._log.append(str(clause))
        return _FakeResult()


class _FakeBegin:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return _FakeConn(self._log)

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.executed = []

    def begin(self):
        return _FakeBegin(self.executed)


class _ExplodingEngine:
    def begin(self):
        raise AssertionError("full-privilege engine must not run user SQL")


class _FakeScalarResult:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _FakeSession:
    def __init__(self, record):
        self._record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return _FakeScalarResult(self._record)

    async def commit(self):
        pass


class _FakeDbRecord:
    schema_name = "edward_db_guardtest"
    last_accessed = None


@pytest.fixture(autouse=True)
def _reset_llm_engine():
    """Each test starts with a fresh (unresolved) llm engine global."""
    yield
    pdb._llm_engine = None


@pytest.mark.asyncio
async def test_user_sql_runs_on_llm_engine(monkeypatch):
    """execute_query must run user SQL on the low-privilege engine, never on
    the app's full-privilege engine."""
    fake_llm = _FakeEngine()

    async def fake_get_llm_engine():
        return fake_llm

    monkeypatch.setattr(pdb, "_get_llm_engine", fake_get_llm_engine)
    monkeypatch.setattr(pdb, "engine", _ExplodingEngine())
    monkeypatch.setattr(pdb, "async_session", lambda: _FakeSession(_FakeDbRecord()))

    result = await pdb.execute_query("guardtest", "INSERT INTO notes (t) VALUES ('x')")

    assert result["success"] is True
    assert any("INSERT INTO notes" in stmt for stmt in fake_llm.executed)


@pytest.mark.asyncio
async def test_degraded_mode_rejects_writes(monkeypatch):
    """Without the low-privilege role, non-read statements are refused before
    touching the database."""

    async def no_llm_engine():
        return None

    monkeypatch.setattr(pdb, "_get_llm_engine", no_llm_engine)
    monkeypatch.setattr(pdb, "engine", _ExplodingEngine())

    for sql in (
        "UPDATE skills SET enabled = false",
        "INSERT INTO skills (id) VALUES ('x')",
        "DELETE FROM skills",
        "CREATE TABLE t (id int)",
        "  update skills set enabled = false",
    ):
        result = await pdb.execute_query("guardtest", sql)
        assert result["success"] is False, sql
        assert "read-only" in (result["error"] or "").lower(), sql


@pytest.mark.asyncio
async def test_degraded_mode_allows_reads(monkeypatch):
    """SELECT/WITH/EXPLAIN pass the degraded-mode guard (they proceed to the
    db-record lookup, which reports 'not found' for our fake)."""

    async def no_llm_engine():
        return None

    monkeypatch.setattr(pdb, "_get_llm_engine", no_llm_engine)
    monkeypatch.setattr(pdb, "async_session", lambda: _FakeSession(None))

    for sql in ("SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x", "EXPLAIN SELECT 1"):
        result = await pdb.execute_query("guardtest", sql)
        # Guard passed; failure is only the missing db record.
        assert "not found" in (result["error"] or ""), sql


def test_llm_engine_url_swaps_credentials():
    """The low-privilege engine URL uses edward_llm + env password against the
    same host/database as the app engine."""
    url = pdb._llm_engine_url("sekret")
    assert url.username == "edward_llm"
    assert url.password == "sekret"
    assert url.database is not None


# ---------------------------------------------------------------------------
# Integration: real Postgres privileges
# ---------------------------------------------------------------------------

_LLM_PW = os.getenv("PERSISTENT_DB_LLM_PASSWORD")


@pytest_asyncio.fixture
async def _dispose_llm_engine():
    # clean_db doesn't cover persistent_databases; drop leftovers ourselves.
    try:
        await pdb.delete_database("guardtest")
    except Exception:
        pass
    yield
    try:
        await pdb.delete_database("guardtest")
    except Exception:
        pass
    if pdb._llm_engine is not None:
        await pdb._llm_engine.dispose()
        pdb._llm_engine = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llm_role_cannot_write_public_tables(clean_db, _dispose_llm_engine):
    if not _LLM_PW:
        pytest.skip("PERSISTENT_DB_LLM_PASSWORD not set")

    await pdb.create_database("guardtest")
    result = await pdb.execute_query("guardtest", "UPDATE skills SET enabled = false")

    assert result["success"] is False
    assert "permission denied" in (result["error"] or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llm_role_schema_local_crud_works(clean_db, _dispose_llm_engine):
    if not _LLM_PW:
        pytest.skip("PERSISTENT_DB_LLM_PASSWORD not set")

    await pdb.create_database("guardtest")

    r1 = await pdb.execute_query(
        "guardtest", "CREATE TABLE notes (id serial PRIMARY KEY, body text)"
    )
    assert r1["success"] is True, r1["error"]

    r2 = await pdb.execute_query("guardtest", "INSERT INTO notes (body) VALUES ('hi')")
    assert r2["success"] is True, r2["error"]

    r3 = await pdb.execute_query("guardtest", "SELECT body FROM notes")
    assert r3["success"] is True, r3["error"]
    assert "hi" in r3["output"]

    # public functions still resolve
    r4 = await pdb.execute_query("guardtest", "SELECT now()")
    assert r4["success"] is True, r4["error"]
