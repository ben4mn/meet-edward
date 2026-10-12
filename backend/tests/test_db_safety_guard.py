"""Tests must never truncate the live database.

On 2026-09-07 an evolution cycle ran the backend test suite with the
backend's own environment — ``DATABASE_URL`` pointing at the live
``edward`` database — and ``conftest.clean_db`` truncated essentially
every table (memories, conversations, settings, scheduled events, …).
The fixture only redirected to ``edward_test`` when *no* URL was set.

Two guards, either of which would have prevented it:
  1. conftest forces a test database unless the inherited URL already
     names one (``…_test``).
  2. anything Edward spawns to work on code (``codex exec`` for the
     orchestrator's CC tasks) gets ``sandbox_env()``, whose DATABASE_URL
     is the test database. (The evolution service that ran that suite is
     gone since 2026-09-12; the guard stays.) Since 2026-09-27 that
     environment is an allowlist — no secrets at all
     (tests/test_codex_env_allowlist.py).

The tests that expect ``edward_test`` by name clear ``TEST_DATABASE_URL``:
``scripts/with-test-db-lock.sh`` exports another ``*_test`` database there
for parallel runs, and an explicit test database wins by design.
"""

from services.sandbox_env import is_test_db_url, safe_test_database_url, sandbox_env


def test_live_db_is_not_a_test_db():
    assert not is_test_db_url("postgresql+asyncpg://edward:edward@localhost:5432/edward")
    assert not is_test_db_url("postgresql://edward:edward@localhost/edward?sslmode=disable")


def test_test_db_names_qualify():
    # On the test cluster (:5433); a *_test name on :5432 is refused
    # (tests/test_conftest_isolation.py).
    assert is_test_db_url("postgresql+asyncpg://edward:edward@localhost:5433/edward_test")
    assert is_test_db_url("postgresql://x:y@h:5433/other_test?opt=1")


def test_inherited_live_url_is_replaced(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    live = "postgresql+asyncpg://edward:edward@localhost:5432/edward"
    assert safe_test_database_url(live).endswith("/edward_test")


def test_inherited_test_url_is_kept():
    url = "postgresql+asyncpg://edward:edward@localhost:5433/ci_test"
    assert safe_test_database_url(url) == url


def test_unset_url_falls_back_to_default_test_db(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert safe_test_database_url(None).endswith("/edward_test")


def test_spawned_subprocess_env_never_carries_the_live_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://edward:edward@localhost:5432/edward")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    env = sandbox_env()
    assert env["DATABASE_URL"].endswith("/edward_test")
    assert env["TEST_DATABASE_URL"].endswith("/edward_test")
    # Only an allowlist is inherited (PATH, HOME for Codex's auth, the
    # locale); API keys and other secrets are not (2026-09-27).
    assert env["PATH"]


def test_spawned_subprocess_env_keeps_an_explicit_test_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://edward:edward@localhost:5432/edward")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://edward:edward@localhost:5433/ci_test")
    env = sandbox_env()
    assert env["DATABASE_URL"].endswith("/ci_test")


def test_conftest_redirected_this_process_to_a_test_db():
    """Whatever DATABASE_URL the runner inherited, by the time tests run
    the process is pointed at a *_test database."""
    import os
    assert is_test_db_url(os.environ.get("DATABASE_URL")), os.environ.get("DATABASE_URL")
    assert is_test_db_url(os.environ.get("TEST_DATABASE_URL"))
