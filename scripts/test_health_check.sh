#!/bin/bash
# Tests for health_check.sh pure logic.
#
# The two pieces worth testing are the ones that decide whether to take a
# destructive action:
#   1. is_stale_postmaster_lock — deletes a lock file if it says yes
#   2. cooldown_ok              — restarts a service if it says yes
# Everything else in health_check.sh is a probe with an obvious remedy.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }

# Source the script under test without running main().
HEALTH_CHECK_LIB_ONLY=1 . "$HERE/health_check.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "is_stale_postmaster_lock:"

# No lock file at all -> nothing stale to clean.
PGDATA_DIR="$TMP/empty"; mkdir -p "$PGDATA_DIR"
is_stale_postmaster_lock "$PGDATA_DIR" "false"; check "no lock file -> not stale" "$?" "1"

# Postgres is healthy -> never touch the lock, whatever it says.
PGDATA_DIR="$TMP/healthy"; mkdir -p "$PGDATA_DIR"; echo "999999" > "$PGDATA_DIR/postmaster.pid"
is_stale_postmaster_lock "$PGDATA_DIR" "true"; check "postgres up -> not stale" "$?" "1"

# Lock names a PID that no longer exists -> stale (ordinary crash).
PGDATA_DIR="$TMP/deadpid"; mkdir -p "$PGDATA_DIR"; echo "999999" > "$PGDATA_DIR/postmaster.pid"
is_stale_postmaster_lock "$PGDATA_DIR" "false"; check "dead pid -> stale" "$?" "0"

# Lock names a live PID that is NOT postgres -> stale. This is the Aug 12
# failure: postgres died in a reboot, npm inherited its recycled PID 699,
# and postgres refused to start for nine days.
PGDATA_DIR="$TMP/recycled"; mkdir -p "$PGDATA_DIR"
sleep 300 & DECOY=$!
echo "$DECOY" > "$PGDATA_DIR/postmaster.pid"
is_stale_postmaster_lock "$PGDATA_DIR" "false"; check "recycled pid (live non-postgres) -> stale" "$?" "0"
kill $DECOY 2>/dev/null

# Lock names a live PID that IS postgres -> not stale, hands off.
PGDATA_DIR="$TMP/realpg"; mkdir -p "$PGDATA_DIR"
REALPG="$(pgrep -f "postgres.*postgresql@16" | head -1)"
if [ -n "$REALPG" ]; then
  echo "$REALPG" > "$PGDATA_DIR/postmaster.pid"
  is_stale_postmaster_lock "$PGDATA_DIR" "false"; check "live postgres pid -> not stale" "$?" "1"
else
  echo "  skip - live postgres pid (no postgres running to borrow a pid from)"
fi

echo "cooldown_ok:"
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"

# First restart of a component is always allowed.
cooldown_ok "$STATE_DIR" backend 3 3600; check "first attempt allowed" "$?" "0"

# Under the cap -> still allowed.
record_restart "$STATE_DIR" backend
cooldown_ok "$STATE_DIR" backend 3 3600; check "second attempt allowed" "$?" "0"
record_restart "$STATE_DIR" backend
cooldown_ok "$STATE_DIR" backend 3 3600; check "third attempt allowed" "$?" "0"

# At the cap -> refused, so a crash-looping service is not restarted forever.
record_restart "$STATE_DIR" backend
cooldown_ok "$STATE_DIR" backend 3 3600; check "fourth attempt refused (cap hit)" "$?" "1"

# Cap is per component, so a wedged backend never blocks the frontend.
cooldown_ok "$STATE_DIR" frontend 3 3600; check "other component unaffected" "$?" "0"

# Attempts outside the window age out and stop counting.
cooldown_ok "$STATE_DIR" backend 3 0; check "expired window resets cap" "$?" "0"

echo
echo "rotate_app_log:"

# /tmp/edward-backend.log reached 538MB unrotated (2026-08-22), which made it
# effectively unusable for debugging. Unlike the postgres log, this one is held
# open by launchd via StandardOutPath, so it MUST be copy-truncated: mv leaves
# the running process writing to the renamed inode and the "new" log stays empty
# forever. That distinction is the whole reason this has its own function.

LOGDIR="$TMP/logs"; mkdir -p "$LOGDIR"

# Small log -> left alone.
SMALL="$LOGDIR/small.log"; printf 'tiny' > "$SMALL"
rotate_app_log "$SMALL" 1; check "under cap -> not rotated" "$?" "1"
check "under cap -> content untouched" "$(cat "$SMALL")" "tiny"

# Missing log -> not an error, nothing to do.
rotate_app_log "$LOGDIR/nope.log" 1; check "missing log -> no-op" "$?" "1"

# Oversized log -> rotated.
BIG="$LOGDIR/big.log"
# bs in bytes: BSD dd takes 1m, GNU dd wants 1M; both take 1048576.
mkfile -n 3m "$BIG" 2>/dev/null || dd if=/dev/zero of="$BIG" bs=1048576 count=3 2>/dev/null
echo "recent line" >> "$BIG"
rotate_app_log "$BIG" 1; check "over cap -> rotated" "$?" "0"
check "over cap -> original truncated in place" "$(file_size "$BIG")" "0"
# gzip runs in the background, so both "big.log.<ts>" and "big.log.<ts>.gz" can
# be present mid-compress. Assert an archive exists, not exactly how many.
check "over cap -> archive created" "$([ -n "$(ls "$LOGDIR"/big.log.* 2>/dev/null)" ] && echo yes)" "yes"

# Dry-run must be distinguishable from a real rotation, or --dry-run reports
# "REPAIRED rotated edward-backend.log" for work it did not do.
DRYLOG="$LOGDIR/dry.log"
dd if=/dev/zero of="$DRYLOG" bs=1048576 count=3 2>/dev/null
DRY_RUN=true rotate_app_log "$DRYLOG" 1 >/dev/null; check "dry-run -> distinct rc 2" "$?" "2"
check "dry-run -> log NOT truncated" "$([ "$(file_size "$DRYLOG")" -gt 0 ] && echo yes)" "yes"
DRY_RUN=false

# The inode must survive, or launchd's open file handle is orphaned.
BIG2="$LOGDIR/inode.log"
dd if=/dev/zero of="$BIG2" bs=1048576 count=3 2>/dev/null
INODE_BEFORE="$(file_inode "$BIG2")"
rotate_app_log "$BIG2" 1 >/dev/null
check "rotation preserves inode (copy-truncate, not mv)" "$(file_inode "$BIG2")" "$INODE_BEFORE"

echo

echo "backup_volume_missing:"

# Backups moved to the removable backup drive on 2026-08-22. An unplugged
# drive and a broken backup job look identical from the outside (no dumps)
# but need different remedies, so the check has to tell them apart.
MOUNTED_SAMPLE='/dev/disk3s1 on /System/Volumes/Data (apfs, local, journaled)
/dev/disk4s1 on /Volumes/Backup (exfat, local, nodev, nosuid, noowners)'
UNMOUNTED_SAMPLE='/dev/disk3s1 on /System/Volumes/Data (apfs, local, journaled)'

backup_volume_missing "/Volumes/Backup/edward-backups" "$MOUNTED_SAMPLE"
check "mounted drive -> not missing" "$?" "1"

backup_volume_missing "/Volumes/Backup/edward-backups" "$UNMOUNTED_SAMPLE"
check "unplugged drive -> missing" "$?" "0"

# A local path is never "missing" in this sense -- if it's empty that's a
# broken backup job, not an absent volume.
backup_volume_missing "/Users/me/edward-backups" "$UNMOUNTED_SAMPLE"
check "local path -> not a volume problem" "$?" "1"

# Must not match a different volume that happens to share a prefix.
PREFIX_SAMPLE='/dev/disk9s1 on /Volumes/Backup_BACKUP (exfat, local)'
backup_volume_missing "/Volumes/Backup/edward-backups" "$PREFIX_SAMPLE"
check "similarly-named volume does not count as mounted" "$?" "0"

echo "checkpoint_tables_too_big:"
checkpoint_tables_too_big "1000000000" 2; check "1 GB under a 2 GB ceiling -> fine" "$?" "1"
checkpoint_tables_too_big "2500000000" 2; check "2.5 GB over a 2 GB ceiling -> too big" "$?" "0"
checkpoint_tables_too_big "" 2;           check "no reading (psql failed) -> not flagged" "$?" "1"

echo "memory_pipeline_unhealthy:"
# args: entity_timeouts entity_total budget_exhausted cycles
memory_pipeline_unhealthy 27 29 8 24;  check "entity timeouts 27/29 -> unhealthy" "$?" "0"
memory_pipeline_unhealthy 2 29 8 24;   check "entity timeouts 2/29, budget 8/24 -> healthy" "$?" "1"
memory_pipeline_unhealthy 0 29 13 24;  check "budget exhausted 13/24 -> unhealthy" "$?" "0"
memory_pipeline_unhealthy 3 3 0 0;     check "too few calls to judge (3) -> not flagged" "$?" "1"
memory_pipeline_unhealthy "" "" "" ""; check "no readings -> not flagged" "$?" "1"

echo "scheduler_liveness:"
# 2026-09-24: the scheduler's poll loop stamps a file after every poll; the
# old check read the backend log's mtime, which the /screen pollers keep
# fresh whether or not the scheduler is alive.
ALIVE="$TMP/scheduler.alive"
scheduler_liveness "$ALIVE" 10 >/dev/null; check "no stamp -> never polled (rc 2)" "$?" "2"
date -u +%Y-%m-%dT%H:%M:%SZ > "$ALIVE"
STAMP="$(file_mtime "$ALIVE")"
check "file_mtime reads an epoch" "$([ -n "$STAMP" ] && [ "$STAMP" -gt 1700000000 ] && echo yes)" "yes"
AGE="$(scheduler_liveness "$ALIVE" 10 "$(( STAMP + 120 ))")"; RC=$?
check "stamped 2m ago -> alive" "$RC" "0"
check "stamped 2m ago -> age 2" "$AGE" "2"
AGE="$(scheduler_liveness "$ALIVE" 10 "$(( STAMP + 600 ))")"; RC=$?
check "exactly at the limit -> still alive" "$RC" "0"
AGE="$(scheduler_liveness "$ALIVE" 10 "$(( STAMP + 11 * 60 ))")"; RC=$?
check "silent 11m -> wedged (rc 1)" "$RC" "1"
check "silent 11m -> age 11" "$AGE" "11"
AGE="$(scheduler_liveness "$ALIVE" 10 "$(( STAMP - 60 ))")"; RC=$?
check "clock skew (stamp in the future) -> alive, age 0" "$RC:$AGE" "0:0"

echo "backup_verdict:"
# 2026-09-25: the deep check said "no database backups" every morning while
# the nightly backup was fine -- under launchd, bash has no permission to
# list the removable volume, so `ls` saw nothing. The backup job (which runs
# under the Full-Disk-Access python) now stamps ~/.edward-health/backup.ok
# after it verifies a dump; the check reads the stamp and never lists the
# drive when there is one. The mount check stays first.
# args: stamp_file stamp_max_h backup_dir mount_output dir_max_h [now_epoch]
NOW="$(date +%s)"
STAMP_OK="$TMP/backup.ok"
printf 'verified_at=%s\nverified_at_iso=x\ndump=edward-db-20260926-033000.dump\nsize_bytes=314572800\n' \
    "$(( NOW - 5 * 3600 ))" > "$STAMP_OK"
UNREADABLE_DIR="$TMP/unlistable-backups"   # never created: `ls` finds nothing, as under launchd
MSG="$(backup_verdict "$STAMP_OK" 26 "$UNREADABLE_DIR" "$MOUNTED_SAMPLE" 48 "$NOW")"; RC=$?
check "fresh stamp + unreadable dir -> no problem" "$RC" "0"
check "fresh stamp -> says how old and which dump" \
    "$(grep -q '5h' <<<"$MSG" && grep -q 'edward-db-20260926-033000.dump' <<<"$MSG" && echo yes)" "yes"
check "fresh stamp -> never 'no database backups'" "$(grep -q 'no database backups' <<<"$MSG" && echo said || echo quiet)" "quiet"

STAMP_OLD="$TMP/backup-old.ok"
printf 'verified_at=%s\ndump=edward-db-20260924-033000.dump\nsize_bytes=1\n' "$(( NOW - 30 * 3600 ))" > "$STAMP_OLD"
MSG="$(backup_verdict "$STAMP_OLD" 26 "$UNREADABLE_DIR" "$MOUNTED_SAMPLE" 48 "$NOW")"; RC=$?
check "30h-old stamp -> problem" "$RC" "1"
check "30h-old stamp -> 'backup stale'" "$(grep -q 'backup stale' <<<"$MSG" && echo yes)" "yes"
check "30h-old stamp -> says 30h" "$(grep -q '30h' <<<"$MSG" && echo yes)" "yes"

MSG="$(backup_verdict "$STAMP_OK" 26 "/Volumes/Backup/edward-backups" "$UNMOUNTED_SAMPLE" 48 "$NOW")"; RC=$?
check "unplugged drive beats a fresh stamp -> problem" "$RC" "1"
check "unplugged drive -> 'not mounted'" "$(grep -q 'not mounted' <<<"$MSG" && echo yes)" "yes"

STAMP_FUTURE="$TMP/backup-future.ok"
printf 'verified_at=%s\ndump=d.dump\n' "$(( NOW + 600 ))" > "$STAMP_FUTURE"
backup_verdict "$STAMP_FUTURE" 26 "$UNREADABLE_DIR" "$MOUNTED_SAMPLE" 48 "$NOW" >/dev/null
check "clock skew (stamp in the future) -> fine" "$?" "0"

# No stamp (or a corrupt one): fall back to listing the directory, as before.
MSG="$(backup_verdict "$TMP/no-such-stamp" 26 "$UNREADABLE_DIR" "$MOUNTED_SAMPLE" 48 "$NOW")"; RC=$?
check "no stamp + nothing listable -> problem" "$RC" "1"
check "no stamp + nothing listable -> 'no database backups'" \
    "$(grep -q 'no database backups' <<<"$MSG" && echo yes)" "yes"
CORRUPT="$TMP/backup-corrupt.ok"; printf 'garbage\n' > "$CORRUPT"
LISTABLE="$TMP/listable-backups"; mkdir -p "$LISTABLE"; : > "$LISTABLE/edward-db-20260926-033000.dump"
backup_verdict "$CORRUPT" 26 "$LISTABLE" "$MOUNTED_SAMPLE" 48 >/dev/null
check "corrupt stamp + a fresh dump in a listable dir -> fine" "$?" "0"

echo "backup_stamp_field:"
check "reads a field" "$(backup_stamp_field "$STAMP_OK" dump)" "edward-db-20260926-033000.dump"
check "missing field -> empty" "$(backup_stamp_field "$STAMP_OK" nope)" ""
check "missing file -> empty" "$(backup_stamp_field "$TMP/no-such-stamp" dump)" ""

echo "log_window:"
# uvicorn's access lines and the MCP servers' output carry no timestamp;
# a bare line takes the time of the last stamped line above it.
WLOG="$TMP/window.log"
cat > "$WLOG" <<'EOF'
bare line before any stamp
2026-09-26T21:00:00 old stamped line
INFO:     1.2.3.4:0 - "GET /api/old HTTP/1.1" 200 OK
2026-09-26T21:25:00 new stamped line
INFO:     1.2.3.4:0 - "GET /api/new HTTP/1.1" 200 OK
EOF
WIN="$(log_window "2026-09-26T21:20:00" < "$WLOG")"
check "stamped line inside the window kept" "$(grep -c 'new stamped line' <<<"$WIN")" "1"
check "bare line after a recent stamp kept" "$(grep -c '/api/new' <<<"$WIN")" "1"
check "stamped line before the window dropped" "$(grep -c 'old stamped line' <<<"$WIN")" "0"
check "bare line after an old stamp dropped" "$(grep -c '/api/old' <<<"$WIN")" "0"
check "bare line before any stamp dropped" "$(grep -c 'before any stamp' <<<"$WIN")" "0"

echo "backend_error_verdict:"
# 2026-09-26 21:31-21:40: a full test run on the production cluster stalled
# Postgres's checkpointer and the backend logged 20 "QueuePool limit"
# tracebacks. The watchdog saw a healthy backend (auth/status answered).
# args: max_server_errors; stdin: the window
POOL_LOG="$TMP/pool.log"
cat > "$POOL_LOG" <<'EOF'
2026-09-26T21:31:17 Scheduler: 2 due event(s) found
INFO:     203.0.113.17:0 - "GET /api/screen HTTP/1.1" 200 OK
2026-09-26T21:31:18 Exception in ASGI application
2026-09-26T21:31:18 sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 5 reached, connection timed out, timeout 30.00
INFO:     203.0.113.17:0 - "GET /api/display/signals HTTP/1.1" 200 OK
EOF
MSG="$(log_window "2026-09-26T21:25:00" < "$POOL_LOG" | backend_error_verdict 5)"; RC=$?
check "QueuePool line in the window -> problem" "$RC" "1"
check "QueuePool -> says the pool ran dry" "$(grep -qi 'pool' <<<"$MSG" && echo yes)" "yes"
check "QueuePool -> says a restart cannot fix it" "$(grep -qi 'restart' <<<"$MSG" && echo yes)" "yes"
log_window "2026-09-26T21:40:00" < "$POOL_LOG" | backend_error_verdict 5 >/dev/null
check "QueuePool line outside the window -> fine" "$?" "0"

QUIET_LOG="$TMP/quiet.log"
cat > "$QUIET_LOG" <<'EOF'
2026-09-27T11:53:12 Memory extraction: processing 7 messages
INFO:     203.0.113.17:0 - "GET /api/display/signals HTTP/1.1" 200 OK
INFO:     203.0.113.17:0 - "GET /api/screen?at=500 HTTP/1.1" 200 OK
EOF
backend_error_verdict 5 < "$QUIET_LOG" >/dev/null
check "ordinary traffic -> fine" "$?" "0"

BURST_LOG="$TMP/burst.log"
{
    echo "2026-09-27T11:50:00 something"
    for i in 1 2 3; do echo 'INFO:     1.2.3.4:0 - "GET /api/today HTTP/1.1" 500 Internal Server Error'; done
    echo 'INFO:     1.2.3.4:0 - "POST /api/chat HTTP/1.1" 503 Service Unavailable'
} > "$BURST_LOG"
backend_error_verdict 5 < "$BURST_LOG" >/dev/null
check "4 server errors under a burst of 5 -> fine" "$?" "0"
echo "2026-09-27T11:50:30 Exception in ASGI application" >> "$BURST_LOG"
MSG="$(backend_error_verdict 5 < "$BURST_LOG")"; RC=$?
check "5 server errors (an unhandled exception counts) -> problem" "$RC" "1"
check "burst -> says how many" "$(grep -q '5 server errors' <<<"$MSG" && echo yes)" "yes"
backend_error_verdict 5 < /dev/null >/dev/null
check "empty window -> fine" "$?" "0"

echo "hub_kiosk_verdict:"
# A hub kiosk polls /api/hub/*; one went quiet for 36 h+ before 2026-09-27
# and nothing noticed. args: max_hours now_epoch; stdin: the log.
T0="$(stamp_epoch 2026-09-27T00:00:00)"
check "stamp_epoch reads a log stamp" "$([ -n "$T0" ] && [ "$T0" -gt 1700000000 ] && echo yes)" "yes"
check "stamp_epoch is local time, one hour apart" \
    "$(( $(stamp_epoch 2026-09-27T01:00:00) - T0 ))" "3600"
HUB_LOG="$TMP/hub.log"
cat > "$HUB_LOG" <<'EOF'
2026-09-27T00:00:00 Starting
INFO:     192.168.1.40:0 - "GET /api/hub/board HTTP/1.1" 200 OK
2026-09-27T02:00:00 later line
INFO:     203.0.113.17:0 - "GET /api/screen HTTP/1.1" 200 OK
EOF
MSG="$(hub_kiosk_verdict 6 "$(( T0 + 3 * 3600 ))" < "$HUB_LOG")"; RC=$?
check "hub asked 3h ago -> fine" "$RC" "0"
MSG="$(hub_kiosk_verdict 6 "$(( T0 + 7 * 3600 ))" < "$HUB_LOG")"; RC=$?
check "hub last asked 7h ago -> problem" "$RC" "1"
check "silent hub -> says 7h" "$(grep -q '7h' <<<"$MSG" && echo yes)" "yes"
check "silent hub -> names the kiosk" "$(grep -q 'kiosk device' <<<"$MSG" && echo yes)" "yes"

NOHUB_LOG="$TMP/nohub.log"
cat > "$NOHUB_LOG" <<'EOF'
2026-09-27T00:00:00 Starting
INFO:     203.0.113.17:0 - "GET /api/screen HTTP/1.1" 200 OK
2026-09-27T09:00:00 later line
EOF
MSG="$(hub_kiosk_verdict 6 "$(( T0 + 9 * 3600 ))" < "$NOHUB_LOG")"; RC=$?
check "no hub request in a log covering 9h -> problem" "$RC" "1"
check "no hub request -> says what the log covers" "$(grep -q '9h' <<<"$MSG" && echo yes)" "yes"
MSG="$(hub_kiosk_verdict 6 "$(( T0 + 2 * 3600 ))" < "$NOHUB_LOG")"; RC=$?
check "no hub request, but the log covers only 2h (restart/rotation) -> not judged" "$RC" "0"
hub_kiosk_verdict 6 "$T0" < /dev/null >/dev/null
check "empty log -> not judged" "$?" "0"
# /screen's own polls must not count as the kiosk.
check "a /api/screen poll is not a hub request" \
    "$(hub_kiosk_verdict 6 "$(( T0 + 9 * 3600 ))" < "$NOHUB_LOG" >/dev/null; echo $?)" "1"
# The phone asks /api/hub/ too: the Edward tab and the House room read
# /api/hub/health on every open, Today's "Log given" posts /api/hub/care,
# and the Hub Photos panel lists, uploads and deletes /api/hub/photos. On
# 2026-09-27 08:00 the kiosk had been dark 36 h+ and every /api/hub/ line in
# the log was the phone's GET /api/hub/health -- the check said "last
# polled 0h ago". Only what the kiosk alone asks for counts.
PHONE_LOG="$TMP/phone.log"
cat > "$PHONE_LOG" <<'EOF'
2026-09-27T00:00:00 Starting
INFO:     192.168.1.40:0 - "GET /api/hub/board HTTP/1.1" 200 OK
2026-09-27T08:00:00 later line
INFO:     2001:db8:2e06:a674::9e8f:674:0 - "GET /api/hub/health HTTP/1.1" 200 OK
2026-09-27T08:05:00 later line
INFO:     2001:db8:2e06:a674::9e8f:674:0 - "POST /api/hub/care/12/log HTTP/1.1" 200 OK
INFO:     2001:db8:2e06:a674::9e8f:674:0 - "GET /api/hub/photos HTTP/1.1" 200 OK
INFO:     2001:db8:2e06:a674::9e8f:674:0 - "POST /api/hub/photos HTTP/1.1" 200 OK
INFO:     2001:db8:2e06:a674::9e8f:674:0 - "DELETE /api/hub/photos/cat.jpg HTTP/1.1" 200 OK
INFO:     203.0.113.17:0 - "GET /api/hub/photos/cat.jpg HTTP/1.1" 200 OK
EOF
MSG="$(hub_kiosk_verdict 6 "$(( T0 + 9 * 3600 ))" < "$PHONE_LOG")"; RC=$?
check "the phone's hub calls 1h ago, the kiosk's board 9h ago -> problem" "$RC" "1"
check "phone calls -> the silence dates from the kiosk's board poll" \
    "$(grep -q '2026-09-27T00:00:00' <<<"$MSG" && echo yes)" "yes"
# Each thing only the kiosk asks for does count.
for line in \
    '"GET /api/hub/board HTTP/1.1" 200' \
    '"GET /api/hub/canvas HTTP/1.1" 200' \
    '"DELETE /api/hub/canvas?by=tap HTTP/1.1" 200' \
    '"POST /api/hub/who HTTP/1.1" 200' \
    '"GET /api/hub/lists HTTP/1.1" 200' \
    '"PATCH /api/hub/lists/items/4 HTTP/1.1" 200' \
    '"GET /api/hub/settings HTTP/1.1" 200' \
    '"GET /api/hub/photos?seed=81723 HTTP/1.1" 200'; do
    printf '2026-09-27T00:00:00 Starting\n2026-09-27T08:00:00 later\nINFO:     192.168.1.40:0 - %s OK\n' "$line" \
        > "$TMP/kiosk-one.log"
    check "the kiosk's $line counts" \
        "$(hub_kiosk_verdict 6 "$(( T0 + 9 * 3600 ))" < "$TMP/kiosk-one.log" >/dev/null; echo $?)" "0"
done

echo "memory_pipeline_window_counts:"
# 2026-09-27 08:00 the deep check flagged "entity timeouts 18/29, budget
# exhausted 17/29 in 24h" -- every one from before the 23:50 deploy; since
# the restart it was 0/23 and 0/9. stdin: the window; prints
# "T N E C t n e c <restart stamp>" (the lowercase ones since the last restart).
MP_LOG="$TMP/pipeline.log"
{
    for i in 1 2 3 4 5 6; do echo "2026-09-26T07:0${i}:00 [ENTITY] timed out after 30s"; done
    echo "2026-09-26T07:07:00 [ENTITY] ok 3 entities in 7.6s"
    for i in 1 2 3 4 5; do
        echo "2026-09-26T0${i}:35:31 INFO services.consolidation_service consolidation LLM budget exhausted, skipping remaining clusters"
        echo "2026-09-26T0${i}:35:37 INFO services.consolidation_service consolidation cycle complete"
    done
    echo "2026-09-26T23:50:01 Orchestrator stopped"
    echo "Starting Edward backend..."
    echo "INFO:     Started server process [23531]"
    echo "2026-09-26T23:50:20 [startup] timezone seeded: America/Chicago"
    for i in 1 2 3 4 5; do echo "2026-09-27T01:0${i}:00 [ENTITY] ok 3 entities in 7.6s"; done
    echo "2026-09-27T02:35:37 INFO services.consolidation_service consolidation cycle complete"
} > "$MP_LOG"
check "counts split at the last restart" \
    "$(memory_pipeline_window_counts < "$MP_LOG")" "6 12 5 6 0 5 0 1 2026-09-26T23:50:01"
check "no restart in the window -> no split" \
    "$(head -7 "$MP_LOG" | memory_pipeline_window_counts)" "6 7 0 0 6 7 0 0 "

echo "memory_pipeline_verdict:"
# args: T N E C t n e c restart_stamp
MSG="$(memory_pipeline_verdict 2 29 8 24 2 29 8 24 "")"; RC=$?
check "healthy window -> fine" "$RC" "0"
MSG="$(memory_pipeline_verdict 18 29 0 9 0 23 0 9 2026-09-26T23:50:01)"; RC=$?
check "entity timeouts all before the restart, 23 clean calls since -> fine" "$RC" "0"
check "-> says the problems predate the restart" \
    "$(grep -q 'before the backend restart at 2026-09-26T23:50' <<<"$MSG" && echo yes)" "yes"
check "-> still shows the window's counts" "$(grep -q '18/29' <<<"$MSG" && echo yes)" "yes"
MSG="$(memory_pipeline_verdict 18 29 0 0 0 2 0 0 2026-09-26T23:50:01)"; RC=$?
check "all before the restart but only 2 calls since -> still a problem" "$RC" "1"
check "-> notes the restart anyway" \
    "$(grep -q 'before the backend restart at 2026-09-26T23:50' <<<"$MSG" && echo yes)" "yes"
MSG="$(memory_pipeline_verdict 18 29 0 0 3 10 0 0 2026-09-26T23:50:01)"; RC=$?
check "timeouts continue after the restart -> problem" "$RC" "1"
check "-> no 'before the restart' claim" \
    "$(grep -q 'before the backend restart' <<<"$MSG" && echo said || echo quiet)" "quiet"
MSG="$(memory_pipeline_verdict 0 29 17 29 0 20 0 2 2026-09-26T23:50:01)"; RC=$?
check "budget problems predate the restart but only 2 cycles since -> problem" "$RC" "1"
MSG="$(memory_pipeline_verdict 18 29 0 0 18 29 0 0 "")"; RC=$?
check "unhealthy, no restart in the window -> problem" "$RC" "1"
check "-> plain counts" "$(grep -q 'entity timeouts 18/29' <<<"$MSG" && echo yes)" "yes"

echo "proactivity_verdict:"
# A 6h log window: the */5 proactive_sweep's "[sweep] ok" lines and the
# noticer's "[notice] pass" lines (2026-10-11). Times are local stamps.
T0="$(stamp_epoch 2026-10-11T00:00:00)"
SWEEP_LOG="$TMP/sweep.log"
{
    for m in 00 05 10 15 20 25 30 35 40 45 50 55; do
        echo "2026-10-11T01:${m}:00 [sweep] ok care=0 due=- outcomes=-"
    done
    echo "2026-10-11T01:31:00 [notice] pass p1 ok events=3 noticed=1 invalid=0 ms=900"
    echo "2026-10-11T01:41:00 [notice] pass p2 ok events=2 noticed=0 invalid=0 ms=700"
} > "$SWEEP_LOG"
MSG="$(proactivity_verdict "$(( T0 + 2 * 3600 ))" < "$SWEEP_LOG")"; RC=$?
check "sweeps every 5 min, noticer fine -> fine" "$RC" "0"
check "-> says how many and how recent" "$(grep -q 'sweep ran 12 times' <<<"$MSG" && echo yes)" "yes"
MSG="$(proactivity_verdict "$(( T0 + 3 * 3600 ))" < "$SWEEP_LOG")"; RC=$?
check "last sweep over 30 min ago -> problem" "$RC" "1"
check "-> names the late-dose check" "$(grep -q 'late-dose' <<<"$MSG" && echo yes)" "yes"
NOSWEEP_LOG="$TMP/nosweep.log"
printf '2026-10-11T00:00:00 Scheduler started\n2026-10-11T01:00:00 [expression] pulse skipped (cron): nothing changed\n' > "$NOSWEEP_LOG"
MSG="$(proactivity_verdict "$(( T0 + 2 * 3600 ))" < "$NOSWEEP_LOG")"; RC=$?
check "no sweep in a log covering 2h -> problem" "$RC" "1"
MSG="$(proactivity_verdict "$(( T0 + 10 * 60 ))" < "$NOSWEEP_LOG")"; RC=$?
check "no sweep yet, log covers 10 min -> not judging" "$RC" "0"
check "no timestamps -> not judging" "$(proactivity_verdict "$T0" < /dev/null >/dev/null; echo $?)" "0"
FAILING_LOG="$TMP/notice-failing.log"
{
    cat "$SWEEP_LOG"
    for i in 3 4 5; do echo "2026-10-11T01:5${i}:00 [notice] pass p${i} FAILED (TimeoutError)"; done
} > "$FAILING_LOG"
MSG="$(proactivity_verdict "$(( T0 + 2 * 3600 ))" < "$FAILING_LOG")"; RC=$?
check "noticer failing half its passes or more -> problem" "$RC" "1"
check "-> counts the failures" "$(grep -q '3 of 5' <<<"$MSG" && echo yes)" "yes"
DEFER_LOG="$TMP/notice-deferred.log"
{
    cat "$SWEEP_LOG"
    for i in 3 4 5 6; do echo "2026-10-11T01:5${i}:00 [notice] pass p${i} deferred (cap)"; done
} > "$DEFER_LOG"
MSG="$(proactivity_verdict "$(( T0 + 2 * 3600 ))" < "$DEFER_LOG")"; RC=$?
check "deferred at the cap is not a failure" "$RC" "0"
check "-> but is counted" "$(grep -q '4 deferred' <<<"$MSG" && echo yes)" "yes"

echo "email_verdict:"
# The backend's GET /api/health/email: a verdict and a summary only.
MSG="$(printf '%s' '{"verdict":"ok","summary":""}' | email_verdict)"; RC=$?
check "mail ok -> fine" "$RC" "0"
MSG="$(printf '%s' '{"verdict":"warn","summary":"Mail.app isn'"'"'t running on this Mac, so new iCloud mail isn'"'"'t arriving; the iCloud mail listener is error"}' | email_verdict)"; RC=$?
check "Mail.app down -> problem" "$RC" "1"
check "-> says what is wrong" "$(grep -q "Mail.app isn't running" <<<"$MSG" && echo yes)" "yes"
check "-> every warning, not only the first" "$(grep -q 'listener is error' <<<"$MSG" && echo yes)" "yes"
MSG="$(printf '' | email_verdict)"; RC=$?
check "no answer from the backend -> not judging" "$RC" "0"
check "-> says so" "$(grep -q 'not judging' <<<"$MSG" && echo yes)" "yes"
MSG="$(printf '%s' '<html>502 Bad Gateway</html>' | email_verdict)"; RC=$?
check "malformed answer -> not judging" "$RC" "0"
MSG="$(printf '%s' '{"verdict":"unknown","summary":"the email check failed (RuntimeError)"}' | email_verdict)"; RC=$?
check "the check itself failed -> not judging" "$RC" "0"

echo "config (environment, then .env, then defaults):"
# launchd gives the watchdog no environment, so it reads the checkout's
# .env itself; the process environment still wins. EDWARD_ENV_FILE points
# the reader at a scratch file here.
CFG_ENV="$TMP/cfg.env"
cat > "$CFG_ENV" <<'EOF'
EDWARD_LAUNCHD_PREFIX=org.example.edward
EDWARD_TUNNEL_LABEL=com.cloudflare.example   # the tunnel's agent
EDWARD_PUBLIC_BASE_URL=https://edward.example.com
EDWARD_BACKUP_DIR=~/somewhere/backups
DATABASE_URL=postgresql+asyncpg://sam:s3cret@127.0.0.1:6543/edward_sam
EDWARD_HEALTH_HUB_KIOSK=1
EOF
cfg() {  # cfg <env assignments…> -- <expression>: the expression after sourcing the script
    local assigns=() expr
    while [ "$1" != "--" ]; do assigns+=("$1"); shift; done
    shift; expr="$1"
    env -u EDWARD_LAUNCHD_PREFIX -u EDWARD_TUNNEL_LABEL -u EDWARD_PUBLIC_BASE_URL \
        -u EDWARD_BACKUP_DIR -u DATABASE_URL -u EDWARD_HEALTH_HUB_KIOSK -u EDWARD_HEALTH_EMAIL \
        HOME="$TMP/home" "${assigns[@]}" HEALTH_CHECK_LIB_ONLY=1 \
        bash -c '. "$1"; eval "echo \"$2\""' _ "$HERE/health_check.sh" "$expr"
}
check "labels from .env's prefix" \
    "$(cfg EDWARD_ENV_FILE="$CFG_ENV" -- '$BACKEND_LABEL $FRONTEND_LABEL $WORKER_LABEL')" \
    "org.example.edward.backend org.example.edward.frontend org.example.edward.graph-worker"
check "the environment beats .env" \
    "$(cfg EDWARD_ENV_FILE="$CFG_ENV" EDWARD_LAUNCHD_PREFIX=com.other -- '$BACKEND_LABEL')" "com.other.backend"
check "tunnel label, comment stripped" "$(cfg EDWARD_ENV_FILE="$CFG_ENV" -- '$TUNNEL_LABEL')" "com.cloudflare.example"
check "public URL" "$(cfg EDWARD_ENV_FILE="$CFG_ENV" -- '$PUBLIC_URL')" "https://edward.example.com"
check "backup dir, ~ expanded" "$(cfg EDWARD_ENV_FILE="$CFG_ENV" -- '$BACKUP_DIR')" "$TMP/home/somewhere/backups"
check "database URL parsed" "$(cfg EDWARD_ENV_FILE="$CFG_ENV" -- '$DB_USER $DB_PASSWORD $DB_HOST $DB_PORT $DB_NAME')" \
    "sam s3cret 127.0.0.1 6543 edward_sam"
check "hub kiosk check on" "$(cfg EDWARD_ENV_FILE="$CFG_ENV" -- '${HEALTH_HUB_KIOSK:-off}')" "1"
NO_ENV="$TMP/no-such.env"
check "defaults: com.meet-edward labels" "$(cfg EDWARD_ENV_FILE="$NO_ENV" -- '$BACKEND_LABEL')" "com.meet-edward.backend"
check "defaults: no tunnel" "$(cfg EDWARD_ENV_FILE="$NO_ENV" -- '[${TUNNEL_LABEL}]')" "[]"
check "defaults: backups in ~/edward-backups" "$(cfg EDWARD_ENV_FILE="$NO_ENV" -- '$BACKUP_DIR')" "$TMP/home/edward-backups"
check "defaults: setup.sh's database" "$(cfg EDWARD_ENV_FILE="$NO_ENV" -- '$DB_USER $DB_PASSWORD $DB_HOST $DB_PORT $DB_NAME')" \
    "edward edward localhost 5432 edward"
check "defaults: optional deep checks off" "$(cfg EDWARD_ENV_FILE="$NO_ENV" -- '[${HEALTH_HUB_KIOSK}${HEALTH_EMAIL}]')" "[]"
check "defaults: Postgres under the Homebrew prefix" \
    "$(cfg EDWARD_ENV_FILE="$NO_ENV" HOMEBREW_PREFIX=/usr/local -- '$PG_BIN $PGDATA')" \
    "/usr/local/opt/postgresql@16/bin /usr/local/var/postgresql@16"
check "the repo is this checkout" "$(cfg EDWARD_ENV_FILE="$NO_ENV" -- '$PROJECT_DIR')" "$(cd "$HERE/.." && pwd)"

echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]