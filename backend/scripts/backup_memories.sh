#!/bin/bash
# Backup Edward's memory store (PostgreSQL) + graph DB, verify, and rotate.
#
# WHY: Edward's long-term memory (memories, memory_connections, documents,
# self_observations, conversations, the persistent_db schemas) lives only
# in one local PostgreSQL instance with no redundancy. A bad migration,
# disk failure, or an errant DELETE would be unrecoverable. This script is
# the safety net.
#
# WHAT IT DOES (all read-only against the live DB):
#   1. pg_dump the whole DB in compressed custom format (pg_restore-able,
#      selective-restore-able).
#   2. Validate the dump is readable (pg_restore -l) — a backup you can't
#      read is not a backup.
#   3. Best-effort copy of the kuzu graph DB file.
#   4. Rotate: keep the last KEEP_DAILY dumps, prune older.
#
# Run as the Postgres superuser role -- with Homebrew's Postgres that is
# your macOS user, the default here -- so it can also reach the edward_db_*
# persistent-database schemas (owned by that role, not edward).
#
# Settings (the environment first, then the checkout's .env, which launchd
# does not load for us):
#   EDWARD_BACKUP_DIR   where dumps go (default ~/edward-backups). A path
#                       under /Volumes/ is a removable drive: the run is
#                       refused while it is not mounted (see the guard).
#   EDWARD_BACKUP_USER  the role pg_dump connects as (default $PGUSER, else
#                       your macOS user); EDWARD_DB_NAME (default edward).
#   EDWARD_BACKUP_KEEP / EDWARD_BACKUP_KEEP_KUZU   how many to keep (14 / 3).
#
# Manual run:   bash backend/scripts/backup_memories.sh
# Scheduled:    <prefix>.backup at 03:30 (scripts/install-launchagents.sh)

set -euo pipefail

# Owner-only, everything this run writes: a dump is the whole database
# (memories, conversations, widget tokens, push subscriptions, OAuth
# tokens), and under the default umask it came out 0644 in a 0755 directory
# that every local account (all in group staff) could read. The directory
# and every dump are also chmod-ed below, for backups made before this.
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# shellcheck source=../../scripts/lib/edward-env.sh
. "$REPO/scripts/lib/edward-env.sh"

# 14 dumps plus 3 kuzu copies can reach tens of GB on a long-lived install;
# point EDWARD_BACKUP_DIR at a bigger (or removable) disk when the internal
# one cannot spare it.
BACKUP_DIR="$(edward_expand_home "$(edward_env EDWARD_BACKUP_DIR "$HOME/edward-backups")")"
DB_NAME="$(edward_env EDWARD_DB_NAME edward)"
DB_USER="$(edward_env EDWARD_BACKUP_USER "${PGUSER:-$(id -un)}")"
# The graph worker's default: GRAPHITI_DB_PATH, else
# <FILE_STORAGE_ROOT>/graphiti.kuzu with the root relative to backend/.
STORAGE_ROOT="$(edward_expand_home "$(edward_env FILE_STORAGE_ROOT ./storage)")"
case "$STORAGE_ROOT" in /*) ;; *) STORAGE_ROOT="$REPO/backend/${STORAGE_ROOT#./}" ;; esac
KUZU_DB="$(edward_expand_home "$(edward_env GRAPHITI_DB_PATH "$STORAGE_ROOT/graphiti.kuzu")")"
KEEP_DAILY="$(edward_env EDWARD_BACKUP_KEEP 14)"   # keep ~2 weeks of daily dumps
# The kuzu graph copy is ~5GB against a ~300MB dump, and the script itself
# treats it as rebuildable from Postgres. Fourteen copies is ~56GB, which
# filled this disk and took Postgres down on 2026-08-12, so it prunes harder.
KEEP_KUZU="$(edward_env EDWARD_BACKUP_KEEP_KUZU 3)"
PG_DUMP_BIN="${EDWARD_PG_DUMP_BIN:-pg_dump}"        # overridable for tests
PG_RESTORE_BIN="${EDWARD_PG_RESTORE_BIN:-pg_restore}"
# Written after a dump verifies; scripts/health_check.sh reads it. The deep
# health check runs as bash under launchd, which cannot list a removable
# volume, so without this it reported "no database backups" every morning
# while the backups were fine (2026-09-25 audit).
HEALTH_STATE="$(edward_expand_home "$(edward_env EDWARD_HEALTH_STATE "${HOME:-}/.edward-health")")"
BACKUP_STAMP_FILE="$(edward_expand_home "$(edward_env EDWARD_BACKUP_STAMP "$HEALTH_STATE/backup.ok")")"

BREW_PREFIX="$(edward_brew_prefix)"
# postgresql@16's own pg_dump first: a newer client on PATH is fine, an
# older one refuses a pg16 server.
export PATH="$BREW_PREFIX/opt/postgresql@16/bin:$BREW_PREFIX/bin:$BREW_PREFIX/sbin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
# The checkpoint GC below connects with the backend's own DATABASE_URL.
if [ -z "${DATABASE_URL:-}" ]; then
  _url="$(edward_env DATABASE_URL "")"
  [ -n "$_url" ] && export DATABASE_URL="$_url"
  unset _url
fi

# GUARD: if BACKUP_DIR is on a removable volume, refuse to run unless that
# volume is actually mounted. Without this, `mkdir -p` cheerfully creates
# /Volumes/<drive>/edward-backups on the *internal* disk the moment someone
# unplugs the drive — silently refilling the disk we just cleared, with
# backups that vanish the instant the real drive is remounted over them.
# Failing loudly is the whole point: a backup you think you have and don't
# is worse than a backup you know is missing.
case "$BACKUP_DIR" in
  /Volumes/*)
    VOLUME_ROOT="/$(echo "${BACKUP_DIR#/}" | cut -d/ -f1-2)"
    if ! /sbin/mount | grep -q " on ${VOLUME_ROOT} "; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] FATAL: backup volume ${VOLUME_ROOT} is not mounted — refusing to write to the internal disk" >&2
      exit 1
    fi
    # Mounted is not the same as writable. macOS gates removable volumes
    # behind TCC, and a LaunchAgent has no grant even when an interactive
    # shell does — which is exactly how this broke on 2026-08-23: the run
    # died on `tee` with a bare "Operation not permitted" and no clue why.
    # Probe first so the reason is in the log instead of a cryptic pipe error.
    if ! ( mkdir -p "$BACKUP_DIR" && touch "$BACKUP_DIR/.write-probe" ) 2>/dev/null; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] FATAL: ${VOLUME_ROOT} is mounted but not writable by this process." >&2
      echo "  macOS gates removable volumes per-process. Run the job through" >&2
      echo "  run_backup_with_fda.py under a python that holds Full Disk Access" >&2
      echo "  (System Settings > Privacy & Security > Full Disk Access)," >&2
      echo "  or set EDWARD_BACKUP_DIR to a local path." >&2
      exit 1
    fi
    rm -f "$BACKUP_DIR/.write-probe"
    ;;
esac

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="$BACKUP_DIR/edward-db-$STAMP.dump"
LOG="$BACKUP_DIR/backup.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# 700 on the directory, 600 on what is in it (older dumps and copies
# included). A filesystem without Unix permissions (an exFAT drive) may
# refuse: that is a warning, never a reason to skip tonight's backup.
lock_down() {
  local f
  for f in "$@"; do
    [ -e "$f" ] || continue
    chmod 600 "$f" 2>/dev/null || echo "warn: could not chmod 600 $f" >&2
  done
}
chmod 700 "$BACKUP_DIR" 2>/dev/null || echo "warn: could not chmod 700 $BACKUP_DIR" >&2
lock_down "$LOG" "$BACKUP_DIR"/edward-db-*.dump "$BACKUP_DIR"/graphiti-*.kuzu

log "=== backup start (db=$DB_NAME user=$DB_USER) ==="

# 1. Dump (custom format = compressed + selective restore)
if "$PG_DUMP_BIN" -U "$DB_USER" -d "$DB_NAME" -Fc -f "$DUMP"; then
  lock_down "$DUMP" "$LOG"
  SIZE="$(du -h "$DUMP" | cut -f1)"
  log "pg_dump ok: $DUMP ($SIZE)"
else
  log "FATAL: pg_dump failed — keeping previous backups, not rotating"
  exit 1
fi

# 2. Validate the dump is readable and contains the core memory table.
#    Capture the TOC instead of piping into `grep -q`: with pipefail, grep
#    exiting at first match SIGPIPEs pg_restore once the TOC outgrows the
#    pipe buffer, which used to fail verification of perfectly good dumps.
if TOC="$("$PG_RESTORE_BIN" -l "$DUMP" 2>/dev/null)" \
   && grep -q 'TABLE DATA public memories' <<<"$TOC"; then
  log "verify ok: dump is valid and contains the memories table"
else
  log "FATAL: dump failed verification — removing it, keeping previous backups"
  rm -f "$DUMP"
  exit 1
fi

# 2a. Stamp the verified dump for the health check (key=value lines,
#     written to a temp file and renamed so a reader never sees half of
#     it). Non-fatal: a stamp that cannot be written costs a false alarm
#     tomorrow morning, never tonight's backup.
write_backup_stamp() {
  local stamp="$1" dump="$2" now size tmp
  now="$(date +%s)"
  size="$(wc -c < "$dump" | tr -d ' ')"
  mkdir -p "$(dirname "$stamp")" || return 1
  tmp="$stamp.tmp.$$"
  printf 'verified_at=%s\nverified_at_iso=%s\ndump=%s\nsize_bytes=%s\n' \
    "$now" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$dump")" "$size" > "$tmp" \
    && mv -f "$tmp" "$stamp" || { rm -f "$tmp"; return 1; }
}
if write_backup_stamp "$BACKUP_STAMP_FILE" "$DUMP" 2>/dev/null; then
  log "backup stamp written: $BACKUP_STAMP_FILE"
else
  log "warn: could not write the backup stamp $BACKUP_STAMP_FILE (non-fatal; the health check will report stale)"
fi

# 2b. Garbage-collect superseded langgraph checkpoints — only after the dump
#     verified, so a bad GC is always recoverable from tonight's backup.
#     Non-fatal: a GC failure must not stop the backup from rotating.
GC_PYTHON="${EDWARD_GC_PYTHON:-$REPO/backend/.venv/bin/python}"
if [ -x "$GC_PYTHON" ]; then
  # Invoke by path: the LaunchAgent's cwd is not backend/, so `-m scripts.…`
  # fails with "No module named scripts" (caught on the first run, 2026-09-05).
  GC_SCRIPT="${EDWARD_GC_SCRIPT:-$REPO/backend/scripts/gc_checkpoints.py}"
  if GC_OUT="$("$GC_PYTHON" "$GC_SCRIPT" --apply --vacuum 2>&1)"; then
    log "checkpoint gc: $(echo "$GC_OUT" | tr '\n' ' ' | cut -c1-300)"
  else
    log "warn: checkpoint gc failed (non-fatal): $(echo "$GC_OUT" | tail -3 | tr '\n' ' ')"
  fi
fi

# 3. Best-effort kuzu graph copy (rebuildable from Postgres, so non-fatal).
#    kuzu uses a WAL; a live copy may be slightly stale but is still a
#    useful fallback. Postgres above is the source of truth.
if [ -f "$KUZU_DB" ]; then
  if cp "$KUZU_DB" "$BACKUP_DIR/graphiti-$STAMP.kuzu" 2>/dev/null; then
    lock_down "$BACKUP_DIR/graphiti-$STAMP.kuzu"
    log "kuzu graph copied (best-effort)"
  else
    log "warn: kuzu graph copy failed (non-fatal)"
  fi
fi

# 4. Rotate — dumps keep KEEP_DAILY, kuzu copies keep the shorter KEEP_KUZU.
log "rotating: keeping newest $KEEP_DAILY dumps, $KEEP_KUZU kuzu copies"
# `|| true` on the ls: with pipefail, zero matches would abort the script.
{ ls -1t "$BACKUP_DIR"/edward-db-*.dump 2>/dev/null || true; } | tail -n +"$((KEEP_DAILY+1))" | while read -r old; do
  log "  prune $old"
  rm -f "$old"
done
{ ls -1t "$BACKUP_DIR"/graphiti-*.kuzu 2>/dev/null || true; } | tail -n +"$((KEEP_KUZU+1))" | while read -r old; do
  log "  prune $old"
  rm -f "$old"
done

COUNT="$(ls -1 "$BACKUP_DIR"/edward-db-*.dump 2>/dev/null | wc -l | tr -d ' ')"
log "=== backup done: $COUNT dump(s) retained in $BACKUP_DIR ==="
