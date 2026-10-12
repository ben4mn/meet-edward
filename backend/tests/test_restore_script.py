"""Tests for backend/scripts/restore_memories.sh.

Regression (2026-09-27): the restore script looked for dumps in a
directory the backup had stopped writing a month before, so the one
command a memory audit leans on for a restore test found nothing (or a
month-old dump). It now reads the directory backup_memories.sh writes
(EDWARD_BACKUP_DIR, else ~/edward-backups), refuses a /Volumes path that
is not mounted (its --force-live snapshot would otherwise land on the
internal disk under /Volumes), and says that the verify path follows
PGHOST/PGPORT, so a restore test can run on the test cluster (:5433)
instead of the main one.

Every database binary is a stub: nothing here reaches Postgres.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
RESTORE = SCRIPTS / "restore_memories.sh"
BACKUP = SCRIPTS / "backup_memories.sh"


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def stubs(tmp_path):
    """Recording stand-ins for pg_restore, psql, createdb, dropdb and pg_dump."""
    calls = tmp_path / "calls.log"
    calls.write_text("")
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    record = f'echo "$(basename "$0") $* PGHOST=${{PGHOST:-}} PGPORT=${{PGPORT:-}}" >> "{calls}"\n'
    bins = {
        "pg_restore": _write_executable(
            stub_dir / "pg_restore",
            "#!/bin/bash\n" + record
            + 'if [ "$1" = "-l" ]; then grep -q FAKE-PGDMP "$2" 2>/dev/null || exit 1; fi\nexit 0\n',
        ),
        "psql": _write_executable(stub_dir / "psql", "#!/bin/bash\n" + record + "echo 42\n"),
        "createdb": _write_executable(stub_dir / "createdb", "#!/bin/bash\n" + record),
        "dropdb": _write_executable(stub_dir / "dropdb", "#!/bin/bash\n" + record),
        "pg_dump": _write_executable(stub_dir / "pg_dump", "#!/bin/bash\n" + record + "exit 1\n"),
    }
    return {"calls": calls, **bins}


def _env(stubs, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "PGHOST", "PGPORT", "PGUSER")}
    for key in ("EDWARD_BACKUP_DIR", "EDWARD_BACKUP_USER", "EDWARD_DB_NAME"):
        env.pop(key, None)
    env.update(
        {
            # Never the checkout's own .env: only what the test sets counts.
            "EDWARD_ENV_FILE": "/nonexistent/edward-test.env",
            "EDWARD_PG_RESTORE_BIN": str(stubs["pg_restore"]),
            "EDWARD_PSQL_BIN": str(stubs["psql"]),
            "EDWARD_CREATEDB_BIN": str(stubs["createdb"]),
            "EDWARD_DROPDB_BIN": str(stubs["dropdb"]),
            "EDWARD_PG_DUMP_BIN": str(stubs["pg_dump"]),
        }
    )
    env.update(extra)
    return env


def _run(env, *args):
    return subprocess.run(
        ["bash", str(RESTORE), *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_restore_reads_where_the_backup_writes():
    def default_dir(script: Path) -> str:
        m = re.search(r'^BACKUP_DIR="(.*)"$', script.read_text(), re.M)
        assert m, f"no BACKUP_DIR line in {script.name}"
        return m.group(1)

    assert default_dir(RESTORE) == default_dir(BACKUP)
    assert '"$HOME/edward-backups"' in default_dir(RESTORE)
    assert "EDWARD_BACKUP_DIR" in default_dir(RESTORE)


def test_default_backup_dir_is_edward_backups_in_home(stubs, tmp_path):
    # A fresh install keeps its backups in ~/edward-backups; with none there
    # yet the script says so and restores nothing.
    home = tmp_path / "home"
    home.mkdir()
    result = _run(_env(stubs, HOME=str(home)))
    out = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"No dump found in {home}/edward-backups" in out, out
    assert stubs["calls"].read_text() == ""

    backups = home / "edward-backups"
    backups.mkdir()
    dump = backups / "edward-db-20260927-033002.dump"
    dump.write_text("FAKE-PGDMP\n")
    result = _run(_env(stubs, HOME=str(home), PGHOST="localhost", PGPORT="5433"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Using dump: {dump}" in result.stdout
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    calls = stubs["calls"].read_text().splitlines()
    assert any(l.startswith(f"createdb -U {me} edward_restore_test") for l in calls), calls


def test_backup_dir_from_dotenv(stubs, tmp_path):
    backups = tmp_path / "dotenv-backups"
    backups.mkdir()
    (backups / "edward-db-20260927-033002.dump").write_text("FAKE-PGDMP\n")
    dotenv = tmp_path / "edward.env"
    dotenv.write_text(f'EDWARD_BACKUP_DIR="{backups}"\nEDWARD_BACKUP_USER=dotenv_role\n')
    result = _run(_env(stubs, EDWARD_ENV_FILE=str(dotenv), PGHOST="localhost", PGPORT="5433"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Using dump: {backups}/" in result.stdout
    assert any(l.startswith("createdb -U dotenv_role ") for l in stubs["calls"].read_text().splitlines())


def test_refuses_a_volume_that_is_not_mounted(stubs, tmp_path):
    missing = "/Volumes/EdwardNoSuchDrive-7f3a/edward-backups"
    result = _run(_env(stubs, EDWARD_BACKUP_DIR=missing))
    assert result.returncode != 0
    assert "not mounted" in (result.stdout + result.stderr)
    assert stubs["calls"].read_text() == ""
    assert not Path(missing).exists()


def test_verify_path_follows_pghost_and_pgport(stubs, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    dump = backups / "edward-db-20260927-033002.dump"
    dump.write_text("FAKE-PGDMP\n")
    result = _run(
        _env(stubs, EDWARD_BACKUP_DIR=str(backups), PGHOST="localhost", PGPORT="5433",
             EDWARD_BACKUP_USER="edward"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Using dump: {dump}" in result.stdout
    calls = stubs["calls"].read_text().splitlines()
    assert calls, "no database command ran"
    for line in calls:
        assert line.endswith("PGHOST=localhost PGPORT=5433"), line
        assert "--clean" not in line
        # The verify path only ever names the throwaway database.
        assert not re.search(r"-d edward(\s|$)", line), line
    assert any(l.startswith("pg_restore -U edward -d edward_restore_test") for l in calls), calls
    assert any(l.startswith("createdb -U edward edward_restore_test") for l in calls), calls
    assert "memories" in result.stdout and "42" in result.stdout


def test_usage_says_how_to_restore_onto_the_test_cluster():
    header = RESTORE.read_text().split("set -euo pipefail")[0]
    assert "PGHOST" in header and "PGPORT" in header
    assert "PGPORT=5433" in header
    assert "EDWARD_BACKUP_DIR" in header
