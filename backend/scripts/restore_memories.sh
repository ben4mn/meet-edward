#!/bin/bash
# Restore Edward's memory store from a backup dump.
#
# Safety-first: by default this restores into a NEW verification database
# (edward_restore_test) and prints a row-count comparison — it does NOT
# touch the live `edward` DB. Only with --force-live does it replace the
# live database, and even then it snapshots the current state first.
#
# Usage:
#   bash backend/scripts/restore_memories.sh                 # newest dump -> verify DB
#   bash backend/scripts/restore_memories.sh <dump-file>     # specific dump -> verify DB
#   bash backend/scripts/restore_memories.sh <dump> --force-live   # REPLACE live (asks first)
#
# Where the dumps are: the directory backup_memories.sh writes,
# EDWARD_BACKUP_DIR (the environment, then the checkout's .env) or
# ~/edward-backups. A /Volumes path that is not mounted is refused: the
# --force-live snapshot would otherwise be written to the internal disk
# under /Volumes and vanish when the drive is plugged back in.
#
# Which server: every command here is plain libpq (pg_restore, psql,
# createdb, dropdb, pg_dump), so PGHOST and PGPORT pick the cluster. Without
# them the verify path restores onto the main cluster (:5432). For a
# restore test, use the test cluster instead (scripts/test-pg-cluster.sh):
#   PGHOST=localhost PGPORT=5433 bash backend/scripts/restore_memories.sh <dump>
# (the role defaults to $PGUSER, else your macOS user -- the superuser of
# both Homebrew clusters; EDWARD_BACKUP_USER picks another).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../scripts/lib/edward-env.sh
. "$HERE/../../scripts/lib/edward-env.sh"

# The same default as backup_memories.sh: restore reads where backup writes
# (2026-09-27 -- this once looked in a directory the backup had stopped
# writing a month before).
BACKUP_DIR="$(edward_expand_home "$(edward_env EDWARD_BACKUP_DIR "$HOME/edward-backups")")"
DB_USER="$(edward_env EDWARD_BACKUP_USER "${PGUSER:-$(id -un)}")"
LIVE_DB="$(edward_env EDWARD_DB_NAME edward)"
# Overridable for tests (tests/test_restore_script.py), like backup_memories.sh.
PG_RESTORE_BIN="${EDWARD_PG_RESTORE_BIN:-pg_restore}"
PSQL_BIN="${EDWARD_PSQL_BIN:-psql}"
CREATEDB_BIN="${EDWARD_CREATEDB_BIN:-createdb}"
DROPDB_BIN="${EDWARD_DROPDB_BIN:-dropdb}"
PG_DUMP_BIN="${EDWARD_PG_DUMP_BIN:-pg_dump}"
BREW_PREFIX="$(edward_brew_prefix)"
export PATH="$BREW_PREFIX/opt/postgresql@16/bin:$BREW_PREFIX/bin:$BREW_PREFIX/sbin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
BACKEND_LABEL="$(edward_label backend)"

case "$BACKUP_DIR" in
  /Volumes/*)
    VOLUME_ROOT="/$(echo "${BACKUP_DIR#/}" | cut -d/ -f1-2)"
    if ! /sbin/mount | grep -q " on ${VOLUME_ROOT} "; then
      echo "FATAL: backup volume ${VOLUME_ROOT} is not mounted -- plug the drive in (or set EDWARD_BACKUP_DIR)" >&2
      exit 1
    fi
    ;;
esac

DUMP="${1:-}"
FORCE_LIVE=0
[ "${2:-}" = "--force-live" ] && FORCE_LIVE=1
[ "${1:-}" = "--force-live" ] && { FORCE_LIVE=1; DUMP=""; }

if [ -z "$DUMP" ]; then
  DUMP="$(ls -1t "$BACKUP_DIR"/edward-db-*.dump 2>/dev/null | head -1 || true)"
fi
[ -n "$DUMP" ] || { echo "No dump found in $BACKUP_DIR"; exit 1; }
[ -f "$DUMP" ] || { echo "No dump found ($DUMP)"; exit 1; }
echo "Using dump: $DUMP"

"$PG_RESTORE_BIN" -l "$DUMP" >/dev/null 2>&1 || { echo "FATAL: dump is not readable"; exit 1; }

if [ "$FORCE_LIVE" = "0" ]; then
  # Safe path: restore into a throwaway DB and report.
  VDB="edward_restore_test"
  echo ">> Restoring into verification DB '$VDB' on ${PGHOST:-the local socket}:${PGPORT:-5432} (live '$LIVE_DB' untouched)"
  "$DROPDB_BIN" -U "$DB_USER" --if-exists "$VDB"
  "$CREATEDB_BIN" -U "$DB_USER" "$VDB"
  "$PG_RESTORE_BIN" -U "$DB_USER" -d "$VDB" --no-owner --no-acl "$DUMP" 2>/dev/null || true
  echo ">> Row counts in restored copy:"
  for t in memories memory_connections documents self_observations conversations; do
    N="$("$PSQL_BIN" -U "$DB_USER" -d "$VDB" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo '?')"
    printf "   %-22s %s\n" "$t" "$N"
  done
  echo ">> Verification DB '$VDB' left in place for inspection."
  echo ">> Drop it with: dropdb -U $DB_USER $VDB"
  echo ">> To actually replace the live DB, re-run with --force-live"
  exit 0
fi

# Destructive path — replace live DB. Snapshot current state first.
echo "!!! This will REPLACE the live '$LIVE_DB' database."
read -r -p "Type 'REPLACE' to continue: " CONFIRM
[ "$CONFIRM" = "REPLACE" ] || { echo "Aborted."; exit 1; }

SNAP="$BACKUP_DIR/pre-restore-snapshot-$(date +%Y%m%d-%H%M%S).dump"
echo ">> Snapshotting current live DB to $SNAP first"
"$PG_DUMP_BIN" -U "$DB_USER" -d "$LIVE_DB" -Fc -f "$SNAP"

echo ">> Stop services that hold the DB open, then press Enter"
echo "   (launchctl bootout gui/\$UID/$BACKEND_LABEL  and  ...graph-worker)"
read -r _

echo ">> Restoring into live '$LIVE_DB' (clean + recreate objects)"
"$PG_RESTORE_BIN" -U "$DB_USER" -d "$LIVE_DB" --clean --if-exists --no-owner --no-acl "$DUMP"
echo ">> Done. Start the services again: bash scripts/install-launchagents.sh (loads any agent that is not loaded) or ./restart.sh backend"
echo ">> Pre-restore snapshot kept at: $SNAP"
