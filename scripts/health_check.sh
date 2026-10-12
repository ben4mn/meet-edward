#!/bin/bash
#
# Edward health check / watchdog.
#
# Checks Edward in dependency order and repairs what it can. Order matters:
# on 2026-08-12 Postgres died in a reboot and left a stale postmaster.pid
# naming PID 699; npm inherited that recycled PID minutes later, so Postgres
# refused to start for nine days. launchd dutifully respawned the backend the
# whole time -- and the backend died on DB connect every time. Restarting the
# app was never going to fix it, so this script always repairs the layer
# underneath before touching the layer above.
#
# Usage:
#   health_check.sh            # shallow watchdog (cheap, runs every 5 min)
#   health_check.sh --deep     # adds capacity/freshness checks (daily)
#   health_check.sh --dry-run  # report only, repair nothing
#
# scripts/install-launchagents.sh schedules both. Settings come from the
# environment or the checkout's .env: EDWARD_LAUNCHD_PREFIX (the labels it
# restarts), EDWARD_TUNNEL_LABEL + EDWARD_PUBLIC_BASE_URL (a tunnel, if
# any), EDWARD_BACKUP_DIR, DATABASE_URL, and the optional deep checks
# EDWARD_HEALTH_HUB_KIOSK / EDWARD_HEALTH_EMAIL. EDWARD_HEALTH_NOTIFY_CMD
# runs with (title, body) when something needs attention.
#
# Exit: 0 all healthy · 1 repaired · 2 still unhealthy (needs a human)

set -uo pipefail

# ── Config ────────────────────────────────────────────────────────────
# Everything host-specific comes from the environment, then the checkout's
# .env (launchd gives this script neither), then a default that fits a
# fresh install (setup.sh + scripts/install-launchagents.sh).
HEALTH_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HEALTH_HERE/lib/edward-env.sh"
PROJECT_DIR="$EDWARD_REPO"
BREW_PREFIX="$(edward_brew_prefix)"
PG_BIN="$(edward_env EDWARD_PG_BIN "$BREW_PREFIX/opt/postgresql@16/bin")"
PGDATA="$(edward_env EDWARD_PGDATA "$BREW_PREFIX/var/postgresql@16")"
PG_LOG="$(edward_env EDWARD_PG_LOG "$BREW_PREFIX/var/log/postgresql@16.log")"
PG_LABEL="homebrew.mxcl.postgresql@16"   # `brew services start postgresql@16`
BACKEND_LABEL="$(edward_label backend)"
FRONTEND_LABEL="$(edward_label frontend)"
WORKER_LABEL="$(edward_label graph-worker)"
# A tunnel in front of Edward (Cloudflare, ngrok …) is optional: with no
# EDWARD_TUNNEL_LABEL there is nothing to check or restart.
TUNNEL_LABEL="$(edward_env EDWARD_TUNNEL_LABEL "")"
# Checked through the tunnel, so only when one is configured.
PUBLIC_URL="$(edward_env EDWARD_PUBLIC_BASE_URL "")"
# The same default as backend/scripts/backup_memories.sh.
BACKUP_DIR="$(edward_expand_home "$(edward_env EDWARD_BACKUP_DIR "$HOME/edward-backups")")"
# Postgres credentials for the query and checkpoint checks, from the URL
# the backend uses (setup.sh creates edward/edward/edward on :5432).
DB_URL="$(edward_env DATABASE_URL "postgresql://edward:edward@localhost:5432/edward")"
_db_rest="${DB_URL#*://}"
_db_creds=""
case "$_db_rest" in *@*) _db_creds="${_db_rest%%@*}"; _db_rest="${_db_rest#*@}" ;; esac
DB_USER="${_db_creds%%:*}"; DB_USER="${DB_USER:-edward}"
DB_PASSWORD=""; case "$_db_creds" in *:*) DB_PASSWORD="${_db_creds#*:}" ;; esac
_db_hostport="${_db_rest%%/*}"
DB_HOST="${_db_hostport%%:*}"; DB_HOST="${DB_HOST:-localhost}"
DB_PORT="5432"; case "$_db_hostport" in *:*) DB_PORT="${_db_hostport##*:}" ;; esac
DB_NAME="edward"; case "$_db_rest" in */?*) DB_NAME="${_db_rest#*/}"; DB_NAME="${DB_NAME%%\?*}" ;; esac
unset _db_rest _db_creds _db_hostport
# Optional deep checks, off unless asked for (any non-empty value):
#   EDWARD_HEALTH_HUB_KIOSK -- a household hub kiosk polls /api/hub/* all day;
#                              report when it goes silent (hub_kiosk_verdict).
#   EDWARD_HEALTH_EMAIL     -- the backend's GET /api/health/email verdict
#                              (Mail.app and Gmail listeners; email_verdict).
HEALTH_HUB_KIOSK="$(edward_env EDWARD_HEALTH_HUB_KIOSK "")"
HEALTH_EMAIL="$(edward_env EDWARD_HEALTH_EMAIL "")"

STATE_DIR="$(edward_expand_home "$(edward_env EDWARD_HEALTH_STATE "$HOME/.edward-health")")"
LOG_FILE="$STATE_DIR/health.log"
STATUS_FILE="$STATE_DIR/status.json"

# A component may be restarted at most MAX_RESTARTS times per WINDOW seconds.
# Past that we stop and escalate rather than feed a crash loop.
MAX_RESTARTS=3
RESTART_WINDOW=3600

DISK_MIN_GB=10          # free space floor
BACKUP_MAX_AGE_H=48     # a listed dump older than this is stale (no stamp)
# backup_memories.sh stamps this after it verifies a dump (2026-09-25): the
# deep check runs as bash under launchd, which cannot list the removable
# volume, and reported "no database backups" daily while they were fine.
# The backup runs nightly at 03:30 and this check at 08:00, so a stamp is
# normally ~5h old; one missed night makes it ~29h.
BACKUP_STAMP_FILE="$(edward_expand_home "$(edward_env EDWARD_BACKUP_STAMP "$STATE_DIR/backup.ok")")"
BACKUP_STAMP_MAX_AGE_H=26
PG_LOG_MAX_MB=200       # rotate the postgres log past this
APP_LOG_MAX_MB=100      # rotate the edward backend/frontend logs past this
CHECKPOINT_MAX_GB=2     # langgraph checkpoint tables past this means nightly GC stopped running
# The scheduler's poll loop stamps this after every successful poll (every
# 30 s); services/scheduler_service.py writes the same default path.
SCHEDULER_ALIVE_FILE="$(edward_expand_home "$(edward_env EDWARD_SCHEDULER_ALIVE_PATH "$HOME/.edward-health/scheduler.alive")")"
SCHEDULER_SILENT_MAX_MIN=10
BACKEND_LOG="${EDWARD_BACKEND_LOG:-/tmp/edward-backend.log}"
# QueuePool / 5xx watch (2026-09-27): the last BACKEND_ERROR_WINDOW_MIN
# minutes of the backend log, read from its tail (ten minutes is well under
# a megabyte; a burst of tracebacks is a few).
BACKEND_ERROR_WINDOW_MIN=10
SERVER_ERROR_BURST=5
HUB_SILENT_MAX_H=6      # deep check (EDWARD_HEALTH_HUB_KIOSK): the kiosk's /api/hub/ polls stopped this long ago
BACKEND_ERROR_TAIL_BYTES=8388608

DRY_RUN=false
DEEP=false
BACKEND_RESTARTED=false
BACKEND_OK=false   # gates the deep email check -- see check_email

# ── Library ───────────────────────────────────────────────────────────
# Portable stat: BSD (macOS) says `stat -f %m`, GNU says `stat -c %Y`. GNU
# stat reads `-f` as "file system" and prints junk rather than failing, so
# probe for GNU first. Linux support exists so the tests run anywhere.
_gnu_stat() { stat -c %Y / >/dev/null 2>&1; }
file_mtime() {  # epoch seconds of the last write
    if _gnu_stat; then stat -c %Y "$1" 2>/dev/null; else stat -f %m "$1" 2>/dev/null; fi
}
file_size() {   # bytes
    if _gnu_stat; then stat -c %s "$1" 2>/dev/null; else stat -f %z "$1" 2>/dev/null; fi
}
file_inode() {
    if _gnu_stat; then stat -c %i "$1" 2>/dev/null; else stat -f %i "$1" 2>/dev/null; fi
}

# scheduler_liveness <alive_file> <max_minutes> [now_epoch]
# Prints the minutes since the scheduler last stamped the file. Returns 0
# when that is within max_minutes, 1 when it is older (the scheduler is
# wedged or dead), 2 when there is no stamp at all (it never completed a
# poll). Pure apart from reading the file's mtime, so it is testable.
scheduler_liveness() {
    local f="$1" max="$2" now="${3:-$(date +%s)}" mtime age
    [ -f "$f" ] || return 2
    mtime="$(file_mtime "$f")"
    [ -n "$mtime" ] || return 2
    age=$(( (now - mtime) / 60 ))
    [ "$age" -lt 0 ] && age=0
    echo "$age"
    [ "$age" -le "$max" ]
}

# is_stale_postmaster_lock <pgdata> <postgres_is_up>
# True (0) when a lock file exists, Postgres is NOT up, and the PID it names
# is either gone or belongs to some other program that inherited the number.
is_stale_postmaster_lock() {
    local pgdata="$1" pg_up="$2"
    local lock="$pgdata/postmaster.pid"

    [ -f "$lock" ] || return 1          # nothing to clean
    [ "$pg_up" = "true" ] && return 1   # healthy -- never touch a live lock

    local lockpid
    lockpid="$(head -1 "$lock" 2>/dev/null | tr -dc '0-9')"
    [ -n "$lockpid" ] || return 0       # unreadable/corrupt -> stale

    kill -0 "$lockpid" 2>/dev/null || return 0   # PID gone -> stale

    # PID is alive: stale only if it is not actually a postgres process.
    local cmd
    cmd="$(ps -p "$lockpid" -o command= 2>/dev/null)"
    case "$cmd" in
        *postgres*) return 1 ;;   # a real postmaster owns it -- hands off
        *)          return 0 ;;   # recycled PID -- the Aug 12 failure
    esac
}

# cooldown_ok <state_dir> <component> <max> <window_seconds>
cooldown_ok() {
    local dir="$1" comp="$2" max="$3" window="$4"
    local f="$dir/restarts-$comp"
    [ -f "$f" ] || return 0

    local now recent=0 ts
    now="$(date +%s)"
    while read -r ts; do
        [ -n "$ts" ] || continue
        if [ $(( now - ts )) -lt "$window" ]; then recent=$(( recent + 1 )); fi
    done < "$f"

    [ "$recent" -lt "$max" ]
}

record_restart() {
    local dir="$1" comp="$2"
    mkdir -p "$dir"
    date +%s >> "$dir/restarts-$comp"
    # keep the file bounded
    tail -50 "$dir/restarts-$comp" > "$dir/.tmp-$comp" 2>/dev/null \
        && mv "$dir/.tmp-$comp" "$dir/restarts-$comp"
}

rotate_app_log() {
    # Copy-truncate one app log if it exceeds ${2} megabytes.
    #
    # Deliberately NOT the mv+recreate dance used for the postgres log: launchd
    # holds /tmp/edward-{backend,frontend}.log open through StandardOutPath, so
    # renaming the file leaves the running process appending to the orphaned
    # inode while the fresh log sits empty. Copy, then truncate in place, so the
    # open descriptor keeps pointing at the file we just emptied.
    #
    # Returns 0 if it rotated, 1 if it left the log alone, 2 if it would have
    # rotated but --dry-run is on. Keeps no side effects beyond the file itself
    # and does no logging, so it stays sourceable by the test harness.
    local target="$1" max_mb="$2" mb archive
    [ -f "$target" ] || return 1
    mb=$(( $(file_size "$target") / 1048576 ))
    [ "$mb" -gt "$max_mb" ] || return 1
    [ "${DRY_RUN:-false}" = true ] && return 2
    archive="$target.$(date +%Y%m%d-%H%M%S)"
    cp "$target" "$archive" || return 1
    : > "$target"
    gzip -f "$archive" 2>/dev/null &
    return 0
}

# Sourced for tests: stop here, don't run main.
# Is BACKUP_DIR on a /Volumes mount that isn't currently mounted?
#
# An unplugged drive and a broken backup job both present as "no dumps",
# but the remedies differ completely — plug the drive back in vs. go find
# out why pg_dump is failing. Reporting the wrong one wastes the outage.
# Takes `mount` output as an argument so it is testable without a drive.
backup_volume_missing() {
    local dir="$1" mount_output="$2" root
    case "$dir" in
        /Volumes/*)
            root="/$(echo "${dir#/}" | cut -d/ -f1-2)"
            # Trailing space matters: /Volumes/Backup must not match
            # /Volumes/Backup_BACKUP.
            if grep -q " on ${root} " <<<"$mount_output"; then
                return 1
            fi
            return 0
            ;;
    esac
    return 1
}

# backup_stamp_field <stamp_file> <key> -> the value of key=value, or nothing.
backup_stamp_field() {
    local f="$1" key="$2"
    [ -r "$f" ] || return 0
    sed -n "s/^${key}=//p" "$f" 2>/dev/null | head -1
}

# backup_verdict <stamp_file> <stamp_max_h> <backup_dir> <mount_output> <dir_max_h> [now_epoch]
# Prints one line and returns 0 (backups fine) or 1 (a problem). In order:
#   1. the backup volume is not mounted -> problem (the drive, not the job);
#   2. a readable stamp from backup_memories.sh decides: older than
#      stamp_max_h -> "backup stale", else fine -- the directory is never
#      listed, since bash under launchd cannot list the removable volume;
#   3. no stamp (or one without verified_at) -> list the directory as before.
# Pure apart from reading the stamp and listing the dir, so it is testable.
backup_verdict() {
    local stamp="$1" stamp_max="$2" dir="$3" mount_output="$4" dir_max="$5" now="${6:-$(date +%s)}"
    local at dump age_h newest
    if backup_volume_missing "$dir" "$mount_output"; then
        echo "backup drive not mounted ($dir) -- plug the backup drive back in"
        return 1
    fi
    at="$(backup_stamp_field "$stamp" verified_at | tr -dc '0-9')"
    if [ -n "$at" ]; then
        dump="$(backup_stamp_field "$stamp" dump)"
        age_h=$(( (now - at) / 3600 ))
        [ "$age_h" -lt 0 ] && age_h=0
        if [ "$age_h" -gt "$stamp_max" ]; then
            echo "backup stale: last verified backup ${age_h}h ago (${dump:-unknown dump}) -- backup job may be broken (see /tmp/edward-backup.log)"
            return 1
        fi
        echo "last verified backup ${age_h}h ago (${dump:-unknown dump})"
        return 0
    fi
    # Match the real nightly artifacts (edward-db-*.dump), not stray one-off
    # .sql files -- a 2026-07 pre-remediation dump was masking the outage.
    newest="$(ls -t "$dir"/edward-db-*.dump 2>/dev/null | head -1)"
    if [ -z "$newest" ]; then
        echo "no database backups found in $dir (and no stamp at $stamp)"
        return 1
    fi
    age_h=$(( (now - $(file_mtime "$newest")) / 3600 ))
    if [ "$age_h" -gt "$dir_max" ]; then
        echo "newest DB backup is ${age_h}h old ($(basename "$newest")) -- backup job may be broken"
        return 1
    fi
    echo "newest is ${age_h}h old ($(basename "$newest"))"
    return 0
}

# checkpoint_tables_too_big BYTES MAX_GB -> 0 if the three langgraph tables
# exceed the ceiling. Pure, so it is testable without a database.
checkpoint_tables_too_big() {
    local bytes="$1" max_gb="$2"
    [ -z "$bytes" ] && return 1
    [ "$bytes" -gt $(( max_gb * 1024 * 1024 * 1024 )) ]
}

# memory_pipeline_unhealthy ENTITY_TIMEOUTS ENTITY_TOTAL BUDGET_EXHAUSTED CYCLES
# -> 0 when more than half of entity-extraction calls timed out, or more than
# half of consolidation cycles exhausted their LLM budget, over the window
# counted. Too few samples (under 5) is not a verdict. Pure, so testable.
memory_pipeline_unhealthy() {
    local timeouts="${1:-0}" total="${2:-0}" exhausted="${3:-0}" cycles="${4:-0}"
    timeouts="${timeouts:-0}"; total="${total:-0}"; exhausted="${exhausted:-0}"; cycles="${cycles:-0}"
    if [ "$total" -ge 5 ] && [ $(( timeouts * 2 )) -gt "$total" ]; then return 0; fi
    if [ "$cycles" -ge 5 ] && [ $(( exhausted * 2 )) -gt "$cycles" ]; then return 0; fi
    return 1
}

# memory_pipeline_window_counts  (stdin: a log window)
# Prints "T N E C t n e c <restart>": entity timeouts / entity calls,
# consolidation budget exhaustions / cycles over the whole window, the same
# four since the last backend restart in it (the whole window when there is
# none), and that restart's time -- the stamped line just before uvicorn's
# "Started server process", i.e. when the old process stopped -- or nothing.
memory_pipeline_window_counts() {
    awk '
        /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ { t = substr($0, 1, 19) }
        /Started server process \[/ { restart = t; st = 0; sn = 0; se = 0; sc = 0; next }
        /\[ENTITY\] timed out/     { T++; N++; st++; sn++ }
        /\[ENTITY\] ok /           { N++; sn++ }
        /LLM budget exhausted/     { E++; se++ }
        /consolidation cycle complete/ { C++; sc++ }
        END { printf "%d %d %d %d %d %d %d %d %s\n", T, N, E, C, st, sn, se, sc, restart }
    '
}

# memory_pipeline_verdict T N E C t n e c <restart>
# Prints one line; returns 0 (fine) or 1 (a problem). The window is judged
# by memory_pipeline_unhealthy. When a restart falls inside it and every
# failing measure has had no failure since (2026-09-27 08:00: "entity
# timeouts 18/29" in 24h, all before the 23:50 deploy, 0/23 after), the line
# says so; it stops being a problem only once each failing measure has five
# clean samples since, so a restart minutes before the check hides nothing.
memory_pipeline_verdict() {
    local T="${1:-0}" N="${2:-0}" E="${3:-0}" C="${4:-0}" t="${5:-0}" n="${6:-0}" e="${7:-0}" c="${8:-0}" restart="${9:-}"
    local counts="entity timeouts ${T}/${N}, consolidation budget exhausted ${E}/${C} in 24h"
    local since="since then entity timeouts ${t}/${n}, budget exhausted ${e}/${c}"
    if ! memory_pipeline_unhealthy "$T" "$N" "$E" "$C"; then
        echo "$counts"
        return 0
    fi
    if [ -z "$restart" ]; then
        echo "$counts"
        return 1
    fi
    local entity_bad=false budget_bad=false predate=true settled=true
    if [ "$N" -ge 5 ] && [ $(( T * 2 )) -gt "$N" ]; then entity_bad=true; fi
    if [ "$C" -ge 5 ] && [ $(( E * 2 )) -gt "$C" ]; then budget_bad=true; fi
    if [ "$entity_bad" = true ]; then
        [ "$t" -eq 0 ] || predate=false
        [ "$n" -ge 5 ] || settled=false
    fi
    if [ "$budget_bad" = true ]; then
        [ "$e" -eq 0 ] || predate=false
        [ "$c" -ge 5 ] || settled=false
    fi
    if [ "$predate" != true ]; then
        echo "$counts ($since, the backend restart at ${restart})"
        return 1
    fi
    if [ "$settled" = true ]; then
        echo "$counts -- all before the backend restart at ${restart}; $since"
        return 0
    fi
    echo "$counts -- all before the backend restart at ${restart}, but too little since to call it fixed ($since)"
    return 1
}

# log_window <since_stamp>  (stdin: backend log) -> the lines at or after since.
# A line's time is its own "YYYY-MM-DDTHH:MM:SS" prefix (local time, as
# services/log_setup.py writes it), else that of the last stamped line above
# it: uvicorn's access lines and the MCP servers' output carry no stamp of
# their own. Lines above the first stamp have no known time and are dropped.
log_window() {
    awk -v s="$1" '
        /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ { t = substr($0, 1, 19) }
        t != "" && t >= s
    '
}

# backend_error_verdict <max_server_errors>  (stdin: a log window)
# Prints one line; returns 0 (fine) or 1 (a problem). Pure, so testable.
#   * Any "QueuePool limit" line: the database connection pool ran dry.
#     2026-09-26 21:31-21:40 a full test run on the production cluster
#     stalled Postgres's checkpointer and the backend logged 20 of these
#     while /api/auth/status still answered, so the watchdog saw nothing.
#     A stalled database is not fixed by restarting the app, so it is only
#     reported.
#   * max or more server errors: access lines answering 5xx, plus
#     "Exception in ASGI application" -- uvicorn answers an unhandled
#     exception 500 without writing an access line for it.
backend_error_verdict() {
    local max="$1" text pool answered unhandled errors
    text="$(cat)"
    pool="$(grep -c 'QueuePool limit' <<<"$text")"
    answered="$(grep -cE 'HTTP/[0-9.]+" 5[0-9][0-9]( |$)' <<<"$text")"
    unhandled="$(grep -c 'Exception in ASGI application' <<<"$text")"
    errors=$(( answered + unhandled ))
    if [ "$pool" -gt 0 ]; then
        echo "database pool ran dry: ${pool} 'QueuePool limit' lines and ${errors} server errors in the backend log -- Postgres is stalled or something holds every connection (a test run on the production cluster did this on 2026-09-26); a restart cannot fix it, see /tmp/edward-backend.log"
        return 1
    fi
    if [ "$errors" -ge "$max" ]; then
        echo "backend answered ${errors} server errors (${answered} 5xx, ${unhandled} unhandled exceptions) -- see /tmp/edward-backend.log"
        return 1
    fi
    echo "${errors} server errors, no pool exhaustion"
    return 0
}

# stamp_epoch <YYYY-MM-DDTHH:MM:SS> -> epoch seconds, reading it as local
# time (the backend log's stamps are local). BSD date on macOS, GNU
# elsewhere so the tests run anywhere.
stamp_epoch() {
    if date --version >/dev/null 2>&1; then
        date -d "$1" +%s 2>/dev/null
    else
        date -j -f %Y-%m-%dT%H:%M:%S "$1" +%s 2>/dev/null
    fi
}

# hub_kiosk_verdict <max_hours> [now_epoch]  (stdin: the backend log)
# Prints one line; returns 0 (fine, or not enough log to judge) or 1 (the
# hub is silent). A household hub kiosk polls /api/hub/* all day, so hours
# without one mean the kiosk device is off, off the network, or its browser
# is wedged -- on 2026-09-27 one had been silent for 36 h+ with nothing
# noticing. Only run when EDWARD_HEALTH_HUB_KIOSK is set (check_hub_kiosk). The log restarts with the backend and is copy-truncated at
# 100 MB, so "no request in the log" only counts once the log covers
# max_hours. Pure apart from `date`, so testable.
#
# Only what the kiosk alone asks for counts (HUB_KIOSK_REQUEST): the board,
# the canvas, who is there, the lists, its screensaver settings and the
# screensaver's shuffled album (photos?seed=). The phone asks /api/hub/
# too -- the Edward tab and the House room read /api/hub/health on every
# open, Today's "Log given" posts /api/hub/care, the Hub Photos panel lists,
# uploads and deletes /api/hub/photos -- and the first version counted any
# of it: replayed on the 2026-09-27 08:00 log, with the kiosk dark 36 h+, it
# said "last polled 0h ago" because the owner had opened the Edward tab at
# 07:13.
#
# ADAPT: assumes the hub is a dedicated kiosk that polls these routes.
#   How it works here: a wall tablet or Raspberry Pi runs /hub full screen;
#   its board, canvas, who, lists, settings and screensaver polls are the
#   only requests counted, so a phone opening the app never hides a dark kiosk.
#   To adapt: set EDWARD_HEALTH_HUB_KIOSK=1 to turn the check on; change
#   HUB_KIOSK_REQUEST if your kiosk polls other routes.
HUB_KIOSK_REQUEST='"[A-Z]+ /api/hub/((board|canvas|who|lists|settings)[ ?/]|photos[?]seed=)'
hub_kiosk_verdict() {
    local max="$1" now="${2:-$(date +%s)}" seen first last secs age_h
    seen="$(awk -v kiosk="$HUB_KIOSK_REQUEST" '
        /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ {
            t = substr($0, 1, 19); if (first == "") first = t
        }
        $0 ~ kiosk && t != "" { last = t }
        END { print first "|" last }
    ')"
    first="${seen%%|*}"; last="${seen#*|}"
    if [ -z "$first" ]; then
        echo "no timestamps in the backend log to judge the hub by"
        return 0
    fi
    if [ -n "$last" ]; then
        secs=$(( now - $(stamp_epoch "$last") ))
        [ "$secs" -lt 0 ] && secs=0
        age_h=$(( secs / 3600 ))
        if [ "$secs" -gt $(( max * 3600 )) ]; then
            echo "hub kiosk silent: its last poll was ${age_h}h ago (${last}) -- is the kiosk device on and on the network?"
            return 1
        fi
        echo "hub kiosk last polled ${age_h}h ago (${last})"
        return 0
    fi
    secs=$(( now - $(stamp_epoch "$first") ))
    age_h=$(( secs / 3600 ))
    if [ "$secs" -gt $(( max * 3600 )) ]; then
        echo "hub kiosk silent: no poll from it in the ${age_h}h the backend log covers (since ${first}) -- is the kiosk device on and on the network?"
        return 1
    fi
    echo "no hub kiosk poll yet, but the backend log only covers ${age_h}h (since ${first}) -- not judging"
    return 0
}

# proactivity_verdict [now_epoch]  (stdin: a log window, e.g. the last 6h)
# Prints one line; returns 0 (fine, or too little log to judge) or 1 (a
# problem). The */5 proactive_sweep (2026-10-11) runs the late-dose check
# (care_watch) and prints "[sweep] ok …" every pass; the noticer prints
# "[notice] pass <id> ok|FAILED|deferred". A dead noticer must not look like
# a quiet day, and a dead sweep means no late-dose alert:
#   * no "[sweep] ok" for 30 min in a log that covers 30 min -> a problem;
#   * at least 4 noticer passes and half or more FAILED -> a problem
#     (deferred at the spend cap is not a failure, only counted).
# Pure apart from `date`, so testable.
SWEEP_SILENT_MAX_MIN=30
proactivity_verdict() {
    local now="${1:-$(date +%s)}" seen first last sweeps ok failed deferred passes cover_min age_min
    seen="$(awk '
        /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ {
            t = substr($0, 1, 19); if (first == "") first = t
        }
        /\[sweep\] ok/ && t != ""      { s++; last = t }
        /\[notice\] pass [^ ]+ ok/     { ok++ }
        /\[notice\] pass [^ ]+ FAILED/ { failed++ }
        /\[notice\] pass [^ ]+ deferred/ { deferred++ }
        END { printf "%s|%s|%d|%d|%d|%d\n", first, last, s, ok, failed, deferred }
    ')"
    IFS='|' read -r first last sweeps ok failed deferred <<<"$seen"
    if [ -z "$first" ]; then
        echo "no timestamps in the backend log to judge proactivity by"
        return 0
    fi
    cover_min=$(( (now - $(stamp_epoch "$first")) / 60 ))
    passes=$(( ok + failed + deferred ))
    local notice="noticer ${passes} passes (${failed} failed, ${deferred} deferred)"
    if [ -z "$last" ]; then
        if [ "$cover_min" -ge "$SWEEP_SILENT_MAX_MIN" ]; then
            echo "no proactive_sweep in the ${cover_min}m the backend log covers -- the late-dose check (care_watch) is not running; look for 'proactive_sweep' and '[sweep]' in /tmp/edward-backend.log"
            return 1
        fi
        echo "no proactive_sweep yet, but the backend log only covers ${cover_min}m -- not judging"
        return 0
    fi
    age_min=$(( (now - $(stamp_epoch "$last")) / 60 ))
    [ "$age_min" -lt 0 ] && age_min=0
    if [ "$age_min" -gt "$SWEEP_SILENT_MAX_MIN" ]; then
        echo "proactive_sweep last ran ${age_min}m ago (${last}) -- the late-dose check (care_watch) is not running"
        return 1
    fi
    if [ "$passes" -ge 4 ] && [ $(( failed * 2 )) -ge "$passes" ]; then
        echo "noticer failing: ${failed} of ${passes} passes FAILED (see '[notice]' in /tmp/edward-backend.log); sweep ran ${sweeps} times"
        return 1
    fi
    echo "sweep ran ${sweeps} times (last ${age_min}m ago); ${notice}"
    return 0
}

# Mail (2026-10-11): the backend's GET /api/health/email -- public, a verdict
# and a summary only, because this script has no cookie and (no Full Disk
# Access) can't read Mail's files itself. Only run when EDWARD_HEALTH_EMAIL
# is set (an install that reads no mail has nothing to judge). The backend judges Mail.app
# running, the age of Mail's index, the mail listeners' last clean poll and
# Gmail's sign-in. An empty or unparseable answer, or "unknown", is not a
# verdict. Pure (reads stdin), so testable.
email_verdict() {
    local json verdict summary
    json="$(cat)"
    verdict="$(printf '%s' "$json" | sed -nE 's/.*"verdict"[[:space:]]*:[[:space:]]*"([a-z]+)".*/\1/p' | head -1)"
    summary="$(printf '%s' "$json" | sed -nE 's/.*"summary"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)".*/\1/p' | head -1)"
    summary="$(printf '%s' "$summary" | sed -e 's/\\"/"/g' -e 's/\\\\/\\/g')"
    case "$verdict" in
        ok)   echo "mail is flowing${summary:+ ($summary)}"; return 0 ;;
        warn) echo "${summary:-a warning without a summary}"; return 1 ;;
        *)    echo "not judging mail (no verdict from /api/health/email)"; return 0 ;;
    esac
}

[ -n "${HEALTH_CHECK_LIB_ONLY:-}" ] && return 0

# ── Reporting ─────────────────────────────────────────────────────────
mkdir -p "$STATE_DIR"
PROBLEMS=()   # unrepaired
REPAIRS=()    # repaired
FRONTEND_OK=true   # gates the tunnel check -- see check_tunnel

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
# detected() = a fault was observed; repair is about to be attempted. It does
# NOT mark the run unhealthy, otherwise every successful self-repair would page
# a human. problem() is reserved for faults that survived the repair attempt.
detected() { log "DETECTED $1"; }
problem() { PROBLEMS+=("$1"); log "PROBLEM  $1"; }
repaired() { REPAIRS+=("$1"); log "REPAIRED $1"; }

notify() {
    local title="$1" body="$2"
    osascript -e "display notification \"${body//\"/}\" with title \"${title//\"/}\"" >/dev/null 2>&1 || true
    local cmd
    cmd="$(edward_env EDWARD_HEALTH_NOTIFY_CMD "")"
    if [ -n "$cmd" ]; then
        "$(edward_expand_home "$cmd")" "$title" "$body" >/dev/null 2>&1 || true
    fi
}

kickstart() {
    local label="$1" comp="$2"
    if [ "$DRY_RUN" = true ]; then
        problem "$comp is down (dry-run: not restarting)"
        return 1
    fi
    if ! cooldown_ok "$STATE_DIR" "$comp" "$MAX_RESTARTS" "$RESTART_WINDOW"; then
        problem "$comp is crash-looping (>=$MAX_RESTARTS restarts in the last hour) -- not restarting again, needs a human"
        [ "$comp" = "frontend" ] && FRONTEND_OK=false
        return 1
    fi
    record_restart "$STATE_DIR" "$comp"
    log "restarting $comp ..."
    launchctl kickstart -k "gui/$(id -u)/$label" >/dev/null 2>&1
}

# launchd_pid LABEL -> the PID launchd reports for LABEL, "-" when loaded
# but not running, nothing when not loaded.
launchd_pid() {
    launchctl list 2>/dev/null | awk -v l="$1" '$3==l {print $1}'
}

http_ok() {  # url -- accepts 2xx/3xx (Edward redirects unauthenticated to /login)
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' -m "${2:-15}" "$1" 2>/dev/null)"
    [[ "$code" =~ ^[23] ]]
}

# ── Tier 0: PostgreSQL ────────────────────────────────────────────────
pg_up() { "$PG_BIN/pg_isready" -q -h "$DB_HOST" -p "$DB_PORT" 2>/dev/null; }

check_postgres() {
    if pg_up; then return 0; fi

    detected "PostgreSQL is not accepting connections"

    if is_stale_postmaster_lock "$PGDATA" "false"; then
        local lockpid holder
        lockpid="$(head -1 "$PGDATA/postmaster.pid" 2>/dev/null | tr -dc '0-9')"
        holder="$(ps -p "${lockpid:-0}" -o command= 2>/dev/null | head -c 60)"
        log "stale postmaster.pid: names PID ${lockpid:-?} -> ${holder:-<dead>}"
        if [ "$DRY_RUN" = true ]; then
            log "DRY-RUN  would clear stale lock and start postgres"
            return 1
        fi
        mv -f "$PGDATA/postmaster.pid" "$STATE_DIR/postmaster.pid.stale-$(date +%Y%m%d%H%M%S)" 2>/dev/null
        repaired "cleared stale postmaster.pid (PID ${lockpid:-?} was ${holder:-dead})"
    fi

    kickstart "$PG_LABEL" postgres || return 1

    local i
    for i in $(seq 1 30); do pg_up && break; sleep 1; done

    if pg_up; then
        repaired "PostgreSQL restarted and accepting connections"
        return 0
    fi
    problem "PostgreSQL still down after restart -- check $PG_BIN/../var/log"
    return 1
}

check_postgres_query() {
    pg_up || return 1
    if ! PGPASSWORD="$DB_PASSWORD" "$PG_BIN/psql" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc 'select 1' >/dev/null 2>&1; then
        problem "PostgreSQL is listening but the '$DB_NAME' database is not queryable"
        return 1
    fi
}

# ── Tier 1: Backend ───────────────────────────────────────────────────
check_backend() {
    if http_ok "http://localhost:8000/api/auth/status"; then return 0; fi
    detected "backend is not responding on :8000"
    kickstart "$BACKEND_LABEL" backend || return 1
    local i
    BACKEND_RESTARTED=true
    for i in $(seq 1 45); do
        sleep 2
        if http_ok "http://localhost:8000/api/auth/status" 5; then
            repaired "backend restarted and responding"
            return 0
        fi
    done
    problem "backend still down after restart -- see /tmp/edward-backend.log"
    return 1
}

# ── Tier 2: Frontend ──────────────────────────────────────────────────
check_frontend() {
    if http_ok "http://localhost:3000/"; then return 0; fi
    detected "frontend is not responding on :3000"
    kickstart "$FRONTEND_LABEL" frontend || return 1
    # The frontend serves a production build; run-launchd.sh rebuilds it
    # first when sources changed, which takes a couple of minutes.
    local i
    for i in $(seq 1 120); do
        sleep 2
        if http_ok "http://localhost:3000/" 5; then
            repaired "frontend restarted and responding"
            return 0
        fi
    done
    problem "frontend still down after restart -- see /tmp/edward-frontend.log"
    FRONTEND_OK=false
    return 1
}

# ── Tier 3: Tunnel / public reachability ──────────────────────────────
# Only when EDWARD_TUNNEL_LABEL names the LaunchAgent of a tunnel in front
# of Edward (cloudflared, ngrok …). The public URL is EDWARD_PUBLIC_BASE_URL;
# without one, only the tunnel's process is checked.
check_tunnel() {
    if [ -z "$TUNNEL_LABEL" ]; then
        return 0
    fi
    if [ "$FRONTEND_OK" != true ]; then
        log "skipping tunnel check -- frontend is down, an unreachable public URL is its symptom"
        return 0
    fi
    local pid
    pid="$(launchd_pid "$TUNNEL_LABEL")"
    if [ -z "$pid" ] || [ "$pid" = "-" ]; then
        detected "tunnel ($TUNNEL_LABEL) is not running"
        kickstart "$TUNNEL_LABEL" tunnel || return 1
        sleep 10
    fi
    case "$PUBLIC_URL" in
        ""|http://localhost*|http://127.0.0.1*) return 0 ;;
    esac
    if http_ok "$PUBLIC_URL/" 25; then return 0; fi
    detected "public URL $PUBLIC_URL is not reachable"
    kickstart "$TUNNEL_LABEL" tunnel || return 1
    sleep 15
    if http_ok "$PUBLIC_URL/" 25; then
        repaired "tunnel restarted, public URL reachable"
        return 0
    fi
    problem "public URL still unreachable after tunnel restart"
    return 1
}

# ── Tier 4: graph worker ──────────────────────────────────────────────
# Optional (scripts/install-launchagents.sh --with-graph): checked only when
# its LaunchAgent is loaded and GRAPHITI_ENABLED is on -- with the flag off
# the worker exits by design (backend/start-graph-worker.sh).
check_worker() {
    edward_launchd_loaded "$WORKER_LABEL" || return 0
    case "$(edward_env GRAPHITI_ENABLED false)" in
        1|true|yes|on) ;;
        *) return 0 ;;
    esac
    local pid
    pid="$(launchd_pid "$WORKER_LABEL")"
    if [ -z "$pid" ] || [ "$pid" = "-" ]; then
        detected "graph-worker is not running"
        kickstart "$WORKER_LABEL" worker || return 1
        sleep 5
        pid="$(launchd_pid "$WORKER_LABEL")"
        if [ -n "$pid" ] && [ "$pid" != "-" ]; then
            repaired "graph-worker restarted"
        else
            problem "graph-worker still not running after restart"
        fi
    fi
}

# ── Deep checks (daily) ───────────────────────────────────────────────
check_disk() {
    local free_gb
    free_gb="$(df -g / | awk 'NR==2 {print $4}')"
    if [ "${free_gb:-999}" -lt "$DISK_MIN_GB" ]; then
        problem "low disk: ${free_gb}GB free (floor ${DISK_MIN_GB}GB)"
    else
        log "disk ok: ${free_gb}GB free"
    fi
}

check_backups() {
    # The backup job talks to Postgres, so it fails silently whenever Postgres
    # is down -- exactly what happened for the nine days after Aug 12.
    # The decision is backup_verdict (stamp first, directory as fallback).
    local verdict
    if verdict="$(backup_verdict "$BACKUP_STAMP_FILE" "$BACKUP_STAMP_MAX_AGE_H" "$BACKUP_DIR" \
                  "$(/sbin/mount)" "$BACKUP_MAX_AGE_H")"; then
        log "backups ok: $verdict"
    else
        problem "$verdict"
        return 1
    fi
}

check_pg_log_size() {
    local log="$PG_LOG" mb
    [ -f "$log" ] || return 0
    mb=$(( $(file_size "$log") / 1048576 ))
    if [ "$mb" -gt "$PG_LOG_MAX_MB" ]; then
        if [ "$DRY_RUN" = true ]; then log "DRY-RUN  would rotate postgres log (${mb}MB)"; return 0; fi
        mv "$log" "$log.$(date +%Y%m%d)" && : > "$log"
        gzip -f "$log.$(date +%Y%m%d)" 2>/dev/null &
        repaired "rotated postgres log (was ${mb}MB)"
    else
        log "postgres log ok: ${mb}MB"
    fi
}

check_app_log_size() {
    local f rc mb
    for f in /tmp/edward-backend.log /tmp/edward-frontend.log; do
        [ -f "$f" ] || continue
        mb=$(( $(file_size "$f") / 1048576 ))
        rotate_app_log "$f" "$APP_LOG_MAX_MB"; rc=$?
        case "$rc" in
            0) repaired "rotated $(basename "$f") (was ${mb}MB)" ;;
            2) log "DRY-RUN  would rotate $(basename "$f") (${mb}MB)" ;;
            *) log "$(basename "$f") ok: ${mb}MB" ;;
        esac
    done
    # Keep only the most recent archives so rotation cannot fill the disk itself.
    ls -t /tmp/edward-*.log.*.gz 2>/dev/null | tail -n +6 | while read -r old; do
        rm -f "$old"
    done
}

check_checkpoint_bloat() {
    # scripts/gc_checkpoints.py runs nightly from the backup job. If the
    # tables are growing anyway, the job is not running (2026-09-05: 2.3 GB,
    # every write row on a superseded checkpoint, 23 GB free on the disk).
    pg_up || return 1
    local bytes
    bytes="$(PGPASSWORD="$DB_PASSWORD" "$PG_BIN/psql" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
        "select coalesce(sum(pg_total_relation_size(relid)),0)::bigint from pg_stat_user_tables where relname in ('checkpoints','checkpoint_blobs','checkpoint_writes')" 2>/dev/null | tr -dc '0-9')"
    if checkpoint_tables_too_big "$bytes" "$CHECKPOINT_MAX_GB"; then
        problem "langgraph checkpoint tables are $(( bytes / 1024 / 1024 ))MB (ceiling ${CHECKPOINT_MAX_GB}GB) -- is gc_checkpoints running from the backup job?"
    else
        log "checkpoint tables ok: $(( ${bytes:-0} / 1024 / 1024 ))MB"
    fi
}

check_memory_pipeline() {
    # Counts over the last 24h of the backend log. Entity extraction timed
    # out 27 times in 29 and 8 of 24 consolidation cycles ran out of budget
    # before the 2026-09-05 remediation; nobody noticed for weeks. The
    # window keeps bare lines by the stamp above them (log_window) and is
    # split at the last backend restart, so problems a deploy already ended
    # say so (memory_pipeline_verdict).
    [ -f "$BACKEND_LOG" ] || return 0
    local verdict
    # Log prefixes are local time, so the window boundary must be too.
    # shellcheck disable=SC2046  # the nine counts are meant to split
    if verdict="$(memory_pipeline_verdict $(log_window "$(stamp_minutes_ago 1440)" < "$BACKEND_LOG" \
                  | memory_pipeline_window_counts))"; then
        log "memory pipeline ok: $verdict"
    else
        problem "memory pipeline: $verdict"
    fi
}

check_email() {
    # Mail.app, Mail's index, the mail listeners and Gmail's sign-in, as the
    # backend sees them (email_verdict). Reported, never repaired: nothing
    # here starts Mail.app or touches Mail's files. curl is the only access.
    local verdict
    if verdict="$(curl -s -m 10 "http://localhost:8000/api/health/email" | email_verdict)"; then
        log "email ok: $verdict"
    else
        problem "email: $verdict"
    fi
}

check_proactivity() {
    # The */5 proactive_sweep (care_watch, the noticing upkeep) and the
    # noticer over the last 6h of the backend log (proactivity_verdict).
    # Reported, never repaired: a missing sweep row comes back with a
    # backend restart (the boot seeds it), which is a person's call.
    [ -f "$BACKEND_LOG" ] || return 0
    local verdict
    if verdict="$(log_window "$(stamp_minutes_ago 360)" < "$BACKEND_LOG" | proactivity_verdict)"; then
        log "proactivity ok: $verdict"
    else
        problem "proactivity: $verdict"
    fi
}

# A local "YYYY-MM-DDTHH:MM:SS" stamp N minutes ago, in the log's format.
stamp_minutes_ago() {
    date -v-"$1"M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -d "$1 minutes ago" +%Y-%m-%dT%H:%M:%S
}

check_backend_errors() {
    # Reported, never repaired: the pool runs dry when Postgres stalls, and
    # restarting the backend onto a stalled database changes nothing.
    [ -f "$BACKEND_LOG" ] || return 0
    local verdict
    if verdict="$(tail -c "$BACKEND_ERROR_TAIL_BYTES" "$BACKEND_LOG" \
                  | log_window "$(stamp_minutes_ago "$BACKEND_ERROR_WINDOW_MIN")" \
                  | backend_error_verdict "$SERVER_ERROR_BURST")"; then
        log "backend errors ok: $verdict (${BACKEND_ERROR_WINDOW_MIN}m)"
    else
        problem "$verdict (last ${BACKEND_ERROR_WINDOW_MIN}m)"
    fi
}

check_hub_kiosk() {
    # Reported, never repaired: the kiosk is a device on the LAN, nothing on
    # the host Mac can turn it back on. Only the kiosk's own polls count (see
    # hub_kiosk_verdict), so the phone opening the Edward tab no longer hides
    # a dark Pi; a /hub page left open in a browser elsewhere still would --
    # a silence detector, not a roll call.
    [ -f "$BACKEND_LOG" ] || return 0
    local verdict
    if verdict="$(hub_kiosk_verdict "$HUB_SILENT_MAX_H" < "$BACKEND_LOG")"; then
        log "hub ok: $verdict"
    else
        problem "$verdict"
    fi
}

check_scheduler() {
    # Primary (2026-09-24): the scheduler's own stamp. Its poll loop writes
    # $SCHEDULER_ALIVE_FILE after every successful poll, so silence means
    # no reminder, wake, tick or pulse is firing. The old check read only
    # the backend log's mtime, which the /screen pollers and the heartbeat
    # keep fresh whether the scheduler lives or not -- and a scheduler that
    # failed at boot was print-only (main.py). Reported, not repaired.
    if [ "$BACKEND_RESTARTED" = true ]; then
        log "skipping scheduler liveness -- the backend was just restarted"
        return 0
    fi
    local age_min rc
    age_min="$(scheduler_liveness "$SCHEDULER_ALIVE_FILE" "$SCHEDULER_SILENT_MAX_MIN")"; rc=$?
    case "$rc" in
        0) log "scheduler polling (${age_min}m since its last poll)" ;;
        1) problem "scheduler has not polled for ${age_min}m -- reminders, wakes and the pulse are not firing; see 'Scheduler' lines in /tmp/edward-backend.log" ;;
        *) problem "no scheduler liveness stamp at $SCHEDULER_ALIVE_FILE -- the scheduler has not completed a poll (boot failing?); see /tmp/edward-backend.log" ;;
    esac
    # Secondary: the whole log going silent means the event loop is wedged.
    if [ -f /tmp/edward-backend.log ]; then
        age_min=$(( ( $(date +%s) - $(file_mtime /tmp/edward-backend.log) ) / 60 ))
        if [ "$age_min" -gt 60 ]; then
            problem "backend log has been silent for ${age_min}m -- scheduler/heartbeat may be wedged"
        else
            log "backend log active (${age_min}m since last write)"
        fi
    fi
}

# ── Main ──────────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --deep)    DEEP=true ;;
        --dry-run) DRY_RUN=true ;;
        -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d'; exit 0 ;;
    esac
done

log "=== health check start (deep=$DEEP dry-run=$DRY_RUN) ==="

# Dependency order: repair the floor before the storeys above it.
if check_postgres; then
    check_postgres_query
    # The scheduler's liveness is a shallow check (every 5 min): a daily
    # look let a dead scheduler go unnoticed for a day. Only asked of a
    # backend that answers -- a dead backend is its own problem.
    if check_backend; then
        BACKEND_OK=true
        check_scheduler
        check_backend_errors
    fi
    check_frontend
    check_tunnel
    check_worker
else
    log "skipping app checks -- Postgres is down, restarting the app cannot help"
fi

if [ "$DEEP" = true ]; then
    check_disk
    check_backups
    check_pg_log_size
    check_app_log_size
    check_checkpoint_bloat
    check_memory_pipeline
    check_proactivity
    [ -n "$HEALTH_HUB_KIOSK" ] && check_hub_kiosk
    # Asked only of a backend that answered: a dead backend is its own problem.
    [ -n "$HEALTH_EMAIL" ] && [ "$BACKEND_OK" = true ] && check_email
fi

# ── Summary ───────────────────────────────────────────────────────────
STATUS="healthy"; EXIT=0
if [ "${#PROBLEMS[@]}" -gt 0 ]; then STATUS="unhealthy"; EXIT=2
elif [ "${#REPAIRS[@]}" -gt 0 ]; then STATUS="repaired"; EXIT=1
fi

{
    printf '{"status":"%s","checked_at":"%s","repairs":[' "$STATUS" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for i in "${!REPAIRS[@]}"; do [ "$i" -gt 0 ] && printf ','; printf '"%s"' "${REPAIRS[$i]//\"/}"; done
    printf '],"problems":['
    for i in "${!PROBLEMS[@]}"; do [ "$i" -gt 0 ] && printf ','; printf '"%s"' "${PROBLEMS[$i]//\"/}"; done
    printf ']}\n'
} > "$STATUS_FILE"

log "=== $STATUS (repaired=${#REPAIRS[@]} problems=${#PROBLEMS[@]}) ==="

# Notify only when something actually happened -- a silent watchdog is the goal.
if [ "${#PROBLEMS[@]}" -gt 0 ]; then
    notify "Edward: needs attention" "${PROBLEMS[0]}"
elif [ "${#REPAIRS[@]}" -gt 0 ]; then
    notify "Edward: self-repaired" "${REPAIRS[0]}"
fi

# Keep the log bounded.
if [ -f "$LOG_FILE" ] && [ "$(file_size "$LOG_FILE")" -gt 5242880 ]; then
    tail -2000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

exit $EXIT
