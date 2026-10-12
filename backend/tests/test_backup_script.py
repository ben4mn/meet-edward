"""Tests for backend/scripts/backup_memories.sh.

Regression: with `set -euo pipefail`, the verify step
`pg_restore -l "$DUMP" | grep -q ...` kills pg_restore with SIGPIPE once the
dump TOC grows past the pipe buffer (grep -q exits at first match), which made
the script delete every *valid* nightly dump from Jun 23 2026 onward.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_memories.sh"


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def stubs(tmp_path):
    """Stub pg_dump/pg_restore mimicking the real post-evolution DB.

    The fake TOC has the memories line ~300 lines in, followed by enough
    output (>64KB pipe buffer) that a `grep -q` reader exiting early
    guarantees the writer receives SIGPIPE.
    """
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    pg_dump = _write_executable(
        stub_dir / "pg_dump",
        '#!/bin/bash\n'
        'out=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-f" ]; then out="$2"; shift; fi; shift; done\n'
        '[ -n "$out" ] || exit 2\n'
        'echo "FAKE-PGDMP" > "$out"\n',
    )
    pg_restore = _write_executable(
        stub_dir / "pg_restore",
        '#!/bin/bash\n'
        '[ "$1" = "-l" ] || exit 2\n'
        '[ -f "$2" ] || exit 1\n'
        'grep -q FAKE-PGDMP "$2" || { echo "pg_restore: error: did not find magic string" >&2; exit 1; }\n'
        'for i in $(seq 1 5000); do\n'
        '  if [ "$i" -eq 300 ]; then\n'
        '    echo "300; 1259 16385 TABLE DATA public memories me"\n'
        '  else\n'
        '    echo "$i; 1259 $((16000 + i)) TABLE DATA public evolution_table_$i me padding-padding-padding-padding"\n'
        '  fi\n'
        'done\n',
    )
    return {"pg_dump": pg_dump, "pg_restore": pg_restore}


def _run_backup(tmp_path, stubs, extra_env=None):
    backup_dir = tmp_path / "backups"
    env = os.environ.copy()
    env.update(
        {
            "EDWARD_BACKUP_DIR": str(backup_dir),
            "EDWARD_PG_DUMP_BIN": str(stubs["pg_dump"]),
            "EDWARD_PG_RESTORE_BIN": str(stubs["pg_restore"]),
            "GRAPHITI_DB_PATH": str(tmp_path / "no-such-graph.kuzu"),
            "EDWARD_BACKUP_KEEP": "14",
            # Never the real checkpoint GC: its default is the live venv's
            # python running gc_checkpoints.py --apply against the default
            # (live) database.
            "EDWARD_GC_PYTHON": str(tmp_path / "no-such-python"),
            # The success stamp goes to a scratch state dir, never ~/.edward-health.
            "EDWARD_HEALTH_STATE": str(tmp_path / "health-state"),
            # Never the checkout's own .env: only what this test sets counts.
            "EDWARD_ENV_FILE": str(tmp_path / "no-such.env"),
        }
    )
    for key in ("EDWARD_BACKUP_KEEP_KUZU", "EDWARD_BACKUP_STAMP", "EDWARD_BACKUP_USER", "EDWARD_DB_NAME"):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, backup_dir


def test_valid_dump_with_large_toc_survives_verification(tmp_path, stubs):
    """A readable dump containing the memories table must be kept (exit 0)."""
    result, backup_dir = _run_backup(tmp_path, stubs)
    dumps = list(backup_dir.glob("edward-db-*.dump"))
    assert result.returncode == 0, (
        f"backup script failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert dumps, "valid dump was deleted by verification"
    log = (backup_dir / "backup.log").read_text()
    assert "verify ok" in log


def test_unreadable_dump_is_still_rejected(tmp_path, stubs):
    """Verification must still catch genuinely bad dumps."""
    # pg_dump stub that writes garbage the pg_restore stub rejects
    _write_executable(
        stubs["pg_dump"],
        '#!/bin/bash\n'
        'out=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-f" ]; then out="$2"; shift; fi; shift; done\n'
        '[ -n "$out" ] || exit 2\n'
        'echo "GARBAGE" > "$out"\n',
    )
    result, backup_dir = _run_backup(tmp_path, stubs)
    assert result.returncode == 1
    assert not list(backup_dir.glob("edward-db-*.dump")), (
        "bad dump should have been removed"
    )


# ── Kuzu retention ────────────────────────────────────────────────────
# Regression: KEEP_DAILY governed both the ~300MB Postgres dumps and the
# ~5GB kuzu graph copies. Fourteen kuzu copies is ~56GB, which filled the
# host's disk and took Postgres down on 2026-08-12. The graph is
# rebuildable from Postgres (the script says so itself), so it gets its own
# shorter retention while the cheap, precious dumps still keep two weeks.

def _seed_old_backups(backup_dir, n=14):
    """Create n dated dump+kuzu pairs, oldest first."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(n):
        stamp = f"202607{i + 1:02d}-030000"
        dump = backup_dir / f"edward-db-{stamp}.dump"
        kuzu = backup_dir / f"graphiti-{stamp}.kuzu"
        dump.write_text("FAKE-PGDMP\n")
        kuzu.write_text("fake-graph\n")
        # stagger mtimes so `ls -1t` ordering is deterministic
        os.utime(dump, (1_700_000_000 + i * 86400,) * 2)
        os.utime(kuzu, (1_700_000_000 + i * 86400,) * 2)
        made.append((dump, kuzu))
    return made


def test_kuzu_retention_is_shorter_than_dump_retention(tmp_path, stubs):
    """Kuzu copies prune to EDWARD_BACKUP_KEEP_KUZU, dumps to EDWARD_BACKUP_KEEP."""
    backup_dir = tmp_path / "backups"
    _seed_old_backups(backup_dir, n=14)

    result, backup_dir = _run_backup(
        tmp_path,
        stubs,
        extra_env={"EDWARD_BACKUP_KEEP": "14", "EDWARD_BACKUP_KEEP_KUZU": "3"},
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    kuzu = list(backup_dir.glob("graphiti-*.kuzu"))
    dumps = list(backup_dir.glob("edward-db-*.dump"))

    assert len(kuzu) <= 3, (
        f"kuzu retention ignored: kept {len(kuzu)} copies, expected <= 3. "
        "Fourteen ~5GB copies is what filled the disk on Aug 12."
    )
    # The run adds one fresh dump to the 14 seeded, so 14 survive rotation.
    assert len(dumps) == 14, f"dump retention changed: kept {len(dumps)}, expected 14"


def test_kuzu_retention_defaults_to_three(tmp_path, stubs):
    """Unset EDWARD_BACKUP_KEEP_KUZU must not silently fall back to 14."""
    backup_dir = tmp_path / "backups"
    _seed_old_backups(backup_dir, n=14)

    env = {"EDWARD_BACKUP_KEEP": "14"}
    result, backup_dir = _run_backup(tmp_path, stubs, extra_env=env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    kuzu = list(backup_dir.glob("graphiti-*.kuzu"))
    assert len(kuzu) <= 3, f"default kuzu retention is {len(kuzu)}, expected <= 3"


# ── The success stamp ─────────────────────────────────────────────────
# 2026-09-25 audit: the deep health check reported "no database backups"
# every morning while the nightly backups were fine. It runs as bash under
# launchd, which has no permission to list a removable backup volume;
# the backup itself runs under the Full-Disk-Access python. So the backup
# now leaves a stamp the health check can always read:
# ~/.edward-health/backup.ok (EDWARD_HEALTH_STATE / EDWARD_BACKUP_STAMP),
# written only after the dump verified.


def _stamp_fields(path: Path) -> dict:
    fields = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    return fields


def test_a_verified_backup_writes_the_stamp(tmp_path, stubs):
    import time

    before = int(time.time())
    result, backup_dir = _run_backup(tmp_path, stubs)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    stamp = tmp_path / "health-state" / "backup.ok"
    assert stamp.exists(), "a verified backup must leave ~/.edward-health/backup.ok"
    fields = _stamp_fields(stamp)
    (dump,) = backup_dir.glob("edward-db-*.dump")
    assert fields["dump"] == dump.name
    assert int(fields["size_bytes"]) == dump.stat().st_size
    assert before - 1 <= int(fields["verified_at"]) <= int(time.time()) + 1
    assert fields["verified_at_iso"].endswith("Z")
    assert "backup stamp written" in (backup_dir / "backup.log").read_text()


def test_the_stamp_path_can_be_overridden(tmp_path, stubs):
    target = tmp_path / "elsewhere" / "ok.stamp"
    result, _ = _run_backup(tmp_path, stubs, extra_env={"EDWARD_BACKUP_STAMP": str(target)})
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert target.exists()
    assert not (tmp_path / "health-state" / "backup.ok").exists()


def test_a_failed_verification_leaves_the_old_stamp_alone(tmp_path, stubs):
    state = tmp_path / "health-state"
    state.mkdir()
    old = "verified_at=1700000000\nverified_at_iso=2023-11-14T22:13:20Z\ndump=edward-db-old.dump\nsize_bytes=1\n"
    (state / "backup.ok").write_text(old)
    _write_executable(
        stubs["pg_dump"],
        '#!/bin/bash\n'
        'out=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-f" ]; then out="$2"; shift; fi; shift; done\n'
        'echo "GARBAGE" > "$out"\n',
    )
    result, _ = _run_backup(tmp_path, stubs)
    assert result.returncode == 1
    assert (state / "backup.ok").read_text() == old, "only a verified dump may refresh the stamp"


def test_an_unwritable_stamp_does_not_fail_the_backup(tmp_path, stubs):
    blocker = tmp_path / "health-state"
    blocker.write_text("a file where the state dir should be")
    result, backup_dir = _run_backup(tmp_path, stubs)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert list(backup_dir.glob("edward-db-*.dump"))
    assert "could not write the backup stamp" in (backup_dir / "backup.log").read_text()


def test_the_health_check_reads_the_stamp_the_backup_writes(tmp_path, stubs):
    """The two scripts agree on the stamp: the deep check calls a fresh one
    fine even when it cannot list the backup directory."""
    result, _ = _run_backup(tmp_path, stubs)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    stamp = tmp_path / "health-state" / "backup.ok"
    health = Path(__file__).resolve().parents[2] / "scripts" / "health_check.sh"
    probe = subprocess.run(
        ["bash", "-c",
         'HEALTH_CHECK_LIB_ONLY=1 . "$1"; backup_verdict "$2" 26 "$3" "" 48',
         "_", str(health), str(stamp), str(tmp_path / "unlistable")],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "EDWARD_HEALTH_STATE": str(tmp_path / "health-state")},
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "last verified backup 0h ago (edward-db-" in probe.stdout


# ── Defaults for a fresh install ──────────────────────────────────────
# The public build backs up to ~/edward-backups (no removable drive
# assumed), as the role Homebrew's Postgres makes its superuser (the macOS
# user), and finds the kuzu graph and the checkpoint GC inside this
# checkout. .env counts when the environment does not set a value, because
# launchd starts the job without .env.


def _env_for_defaults(tmp_path, stubs, **extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("EDWARD_") and k not in ("PGUSER", "GRAPHITI_DB_PATH", "FILE_STORAGE_ROOT")
    }
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "EDWARD_PG_DUMP_BIN": str(stubs["pg_dump"]),
            "EDWARD_PG_RESTORE_BIN": str(stubs["pg_restore"]),
            "EDWARD_GC_PYTHON": str(tmp_path / "no-such-python"),
            "EDWARD_ENV_FILE": str(tmp_path / "no-such.env"),
        }
    )
    env.update(extra)
    return env, home


def _recording_pg_dump(stubs, tmp_path):
    seen = tmp_path / "pg_dump.args"
    _write_executable(
        stubs["pg_dump"],
        '#!/bin/bash\n'
        f'echo "$*" > "{seen}"\n'
        'out=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-f" ]; then out="$2"; shift; fi; shift; done\n'
        'echo "FAKE-PGDMP" > "$out"\n',
    )
    return seen


def test_default_backup_dir_is_edward_backups_in_home(tmp_path, stubs):
    env, home = _env_for_defaults(tmp_path, stubs)
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((home / "edward-backups").glob("edward-db-*.dump"))
    assert (home / ".edward-health" / "backup.ok").exists()


def test_default_role_is_pguser_else_the_macos_user(tmp_path, stubs):
    seen = _recording_pg_dump(stubs, tmp_path)
    env, _ = _env_for_defaults(tmp_path, stubs, PGUSER="pg_owner_role")
    assert subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, timeout=60).returncode == 0
    assert "-U pg_owner_role -d edward" in seen.read_text()

    env, _ = _env_for_defaults(tmp_path, stubs)
    env.pop("PGUSER", None)
    assert subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, timeout=60).returncode == 0
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    assert f"-U {me} -d edward" in seen.read_text()


def test_dotenv_settings_count_when_the_environment_has_none(tmp_path, stubs):
    seen = _recording_pg_dump(stubs, tmp_path)
    target = tmp_path / "from-dotenv"
    dotenv = tmp_path / "edward.env"
    dotenv.write_text(
        f"EDWARD_BACKUP_DIR={target}   # where the dumps go\n"
        "EDWARD_BACKUP_USER='dotenv_role'\n"
    )
    env, _ = _env_for_defaults(tmp_path, stubs, EDWARD_ENV_FILE=str(dotenv))
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(target.glob("edward-db-*.dump"))
    assert "-U dotenv_role " in seen.read_text()

    # The environment still wins over .env.
    other = tmp_path / "from-env"
    env["EDWARD_BACKUP_DIR"] = str(other)
    assert subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, timeout=60).returncode == 0
    assert list(other.glob("edward-db-*.dump"))


def test_kuzu_graph_is_found_in_this_checkouts_storage(tmp_path, stubs):
    # FILE_STORAGE_ROOT relative to backend/, as the graph worker reads it.
    storage = tmp_path / "storage-root"
    storage.mkdir()
    (storage / "graphiti.kuzu").write_text("graph")
    env, home = _env_for_defaults(tmp_path, stubs, FILE_STORAGE_ROOT=str(storage))
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((home / "edward-backups").glob("graphiti-*.kuzu"))


def test_no_private_paths_in_the_script():
    text = SCRIPT.read_text()
    assert "/Users/" not in text
    assert "/opt/homebrew" not in text
