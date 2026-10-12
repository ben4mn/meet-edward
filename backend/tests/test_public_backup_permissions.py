"""Nightly backups are owner-only (backend/scripts/backup_memories.sh).

A dump is the whole database — memories, conversations, widget tokens,
push subscriptions, OAuth tokens — and under the default umask it came out
0644 in a 0755 directory that every local account could read. The script
now runs under ``umask 077`` and chmods the directory to 700 and every dump,
copy and its log to 600, older ones included.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_memories.sh"


def _exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def run(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    pg_dump = _exe(stubs / "pg_dump", '#!/bin/bash\nout=""\n'
                   'while [ $# -gt 0 ]; do if [ "$1" = "-f" ]; then out="$2"; shift; fi; shift; done\n'
                   'echo FAKE-PGDMP > "$out"\n')
    pg_restore = _exe(stubs / "pg_restore", '#!/bin/bash\ngrep -q FAKE-PGDMP "$2" || exit 1\n'
                      'echo "1; 1259 16385 TABLE DATA public memories me"\n')
    kuzu = tmp_path / "graph.kuzu"
    kuzu.write_text("graph")
    kuzu.chmod(0o644)
    backups = tmp_path / "backups"

    def _run():
        env = {k: v for k, v in os.environ.items()
               if k not in ("EDWARD_BACKUP_KEEP_KUZU", "EDWARD_BACKUP_STAMP", "EDWARD_BACKUP_USER", "EDWARD_DB_NAME")}
        env.update({
            "EDWARD_BACKUP_DIR": str(backups),
            "EDWARD_PG_DUMP_BIN": str(pg_dump),
            "EDWARD_PG_RESTORE_BIN": str(pg_restore),
            "GRAPHITI_DB_PATH": str(kuzu),
            "EDWARD_GC_PYTHON": str(tmp_path / "no-such-python"),
            "EDWARD_HEALTH_STATE": str(tmp_path / "health-state"),
            "EDWARD_ENV_FILE": str(tmp_path / "no-such.env"),
        })
        result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return backups

    return _run


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_directory_and_everything_written_are_owner_only(run):
    backups = run()
    assert _mode(backups) == 0o700
    dumps = list(backups.glob("edward-db-*.dump"))
    copies = list(backups.glob("graphiti-*.kuzu"))
    assert dumps and copies
    for path in dumps + copies + [backups / "backup.log"]:
        assert _mode(path) == 0o600, path


def test_older_backups_are_locked_down_too(run, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o755)
    backups.chmod(0o755)
    old = backups / "edward-db-20260101-033000.dump"
    old.write_text("FAKE-PGDMP")
    old.chmod(0o644)
    log = backups / "backup.log"
    log.write_text("old lines\n")
    log.chmod(0o644)
    run()
    assert _mode(backups) == 0o700
    assert _mode(old) == 0o600 and _mode(log) == 0o600


def test_the_health_stamp_is_owner_only(run, tmp_path):
    run()
    stamp = tmp_path / "health-state" / "backup.ok"
    assert stamp.exists() and _mode(stamp) & 0o077 == 0


def test_the_script_sets_a_private_umask_before_writing_anything():
    text = SCRIPT.read_text()
    assert "umask 077" in text
    assert text.index("umask 077") < text.index('mkdir -p "$BACKUP_DIR"')
