#!/bin/bash
# Shared helpers for Edward's shell scripts: where the checkout is, where
# Homebrew is, and the few settings the scripts need from .env.
#
# Source it (never run it):
#   . "$(dirname "${BASH_SOURCE[0]}")/lib/edward-env.sh"
#
# Why scripts read .env themselves: launchd starts the health check, the
# backup and the frontend with an empty environment, so a value the owner
# put in .env (EDWARD_LAUNCHD_PREFIX, EDWARD_BACKUP_DIR, EDWARD_TUNNEL_LABEL
# …) would otherwise reach the backend (which loads .env) and not the
# scripts around it. .env is parsed, never sourced: a script that only
# needs a label must not execute whatever else the file holds.
#
# Precedence everywhere: the process environment, then .env, then the
# default the caller passes. EDWARD_ENV_FILE points at another file (tests
# point it at one that does not exist).
#
# Safe under `set -euo pipefail`; defines functions and EDWARD_REPO only.

# The checkout this file belongs to (scripts/lib/ -> the repo root).
EDWARD_REPO="${EDWARD_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# edward_env_file -> the .env this checkout reads.
edward_env_file() {
    printf '%s\n' "${EDWARD_ENV_FILE:-$EDWARD_REPO/.env}"
}

# edward_dotenv_get KEY [FILE] -> KEY's value in FILE (the last assignment
# wins, as when bash sources it), or nothing. Understands `KEY=value`,
# `export KEY=value`, single or double quotes and a trailing ` # comment`.
# Returns 1 when the key is not assigned there.
edward_dotenv_get() {
    local key="$1" file="${2:-$(edward_env_file)}" line val
    [ -r "$file" ] || return 1
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | tail -1 || true)"
    [ -n "$line" ] || return 1
    val="${line#*=}"
    case "$val" in
        \"*) val="${val#\"}"; val="${val%%\"*}" ;;
        \'*) val="${val#\'}"; val="${val%%\'*}" ;;
        *)
            val="${val%%[[:space:]]#*}"
            val="${val%"${val##*[![:space:]]}"}"
            ;;
    esac
    printf '%s\n' "$val"
}

# edward_expand_home PATH -> PATH with a leading ~ replaced by $HOME.
edward_expand_home() {
    local p="$1"
    case "$p" in
        "~") p="$HOME" ;;
        "~/"*) p="$HOME/${p#\~/}" ;;
    esac
    printf '%s\n' "$p"
}

# edward_env KEY [DEFAULT] -> the environment's KEY when set and non-empty,
# else .env's, else DEFAULT.
edward_env() {
    local key="$1" def="${2:-}" val=""
    val="${!key:-}"
    if [ -z "$val" ]; then
        val="$(edward_dotenv_get "$key" 2>/dev/null || true)"
    fi
    printf '%s\n' "${val:-$def}"
}

# edward_brew_prefix -> Homebrew's prefix: HOMEBREW_PREFIX, `brew --prefix`,
# else /opt/homebrew (Apple silicon) or /usr/local (Intel).
edward_brew_prefix() {
    if [ -n "${HOMEBREW_PREFIX:-}" ]; then
        printf '%s\n' "$HOMEBREW_PREFIX"
        return 0
    fi
    local p=""
    if command -v brew >/dev/null 2>&1; then
        p="$(brew --prefix 2>/dev/null || true)"
    fi
    if [ -z "$p" ]; then
        if [ -d /opt/homebrew ]; then p=/opt/homebrew; else p=/usr/local; fi
    fi
    printf '%s\n' "$p"
}

# edward_pg_bin -> Postgres 16's bin directory: EDWARD_PG_BIN (environment or
# .env), else Homebrew's postgresql@16 keg. The formula is keg-only, so a
# fresh `brew install postgresql@16` puts neither psql nor pg_config on PATH:
# backend/start.sh, start-graph-worker.sh and the LaunchAgents' PATH put this
# directory first themselves (psycopg finds libpq through pg_config when its
# bundled one, psycopg-binary, is missing).
edward_pg_bin() {
    local def
    def="$(edward_brew_prefix)/opt/postgresql@16/bin"
    edward_expand_home "$(edward_env EDWARD_PG_BIN "$def")"
}

# edward_service_path -> the PATH Edward's unsandboxed jobs run with (the
# LaunchAgents, backend/start.sh, backend/start-graph-worker.sh,
# frontend/run-launchd.sh): the system's own directories first, then
# Postgres 16's tools (edward_pg_bin), then Homebrew's bin and sbin (and
# /usr/local/bin). Never ~/.local/bin or ~/bin.
#
# Why (review 2026-10-12): Edward's sandboxed shell runs as this macOS user,
# so it can write the user's home and Homebrew's prefix (he may `brew
# install` by design). A program these jobs run by bare name -- curl, stat,
# sips, osascript -- must resolve to the system's read-only copy, never to
# one planted earlier on PATH. postgresql@16's keg and its opt/ link are
# write-protected from the shell (backend/services/computer/rails.py); what
# still resolves from Homebrew (node, npm, cloudflared) is in the threat
# model (docs/architecture/computer.md).
edward_service_path() {
    local out d brew
    brew="$(edward_brew_prefix)"
    out="/usr/bin:/bin:/usr/sbin:/sbin"
    for d in "$(edward_pg_bin)" "$brew/bin" "$brew/sbin" /usr/local/bin; do
        [ -n "$d" ] || continue
        case ":$out:" in *":$d:"*) ;; *) out="$out:$d" ;; esac
    done
    printf '%s\n' "$out"
}

# edward_backend_env KEY [DEFAULT] -> edward_env KEY, with backend/.env as a
# last resort when the repo has no .env (the old place backend/start.sh
# also read). Only the few settings the start scripts need are read this
# way: the rest of .env never enters a process's start-up environment.
edward_backend_env() {
    local key="$1" def="${2:-}" val
    val="$(edward_env "$key")"
    if [ -z "$val" ] && [ -z "${EDWARD_ENV_FILE:-}" ] && [ ! -r "$EDWARD_REPO/.env" ]; then
        val="$(edward_dotenv_get "$key" "$EDWARD_REPO/backend/.env" 2>/dev/null || true)"
    fi
    printf '%s\n' "${val:-$def}"
}

# edward_export_home_paths [FILE] -> exports, expanded, each KEY=~/... in
# FILE (default this checkout's .env) that the environment does not already
# set. .env used to be sourced by bash, which expanded a leading ~; the
# backend now reads .env itself (python-dotenv, which does not), so the
# start scripts hand it these paths ready-made. Only values that begin with
# ~ are exported: they are paths, never secrets.
edward_export_home_paths() {
    local file="${1:-$(edward_env_file)}" key val
    [ -r "$file" ] || return 0
    while IFS= read -r key; do
        [ -n "$key" ] || continue
        [ -n "${!key:-}" ] && continue
        val="$(edward_dotenv_get "$key" "$file" 2>/dev/null || true)"
        case "$val" in
            "~"|"~/"*) export "$key=$(edward_expand_home "$val")" ;;
        esac
    done <<EOF
$(grep -E '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=~' "$file" 2>/dev/null \
    | sed -E 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/' | sort -u)
EOF
    return 0
}

# edward_launchd_prefix -> EDWARD_LAUNCHD_PREFIX (default com.meet-edward),
# without a trailing dot. backend/services/host_config.py reads the same.
edward_launchd_prefix() {
    local p
    p="$(edward_env EDWARD_LAUNCHD_PREFIX com.meet-edward)"
    p="${p%.}"
    printf '%s\n' "${p:-com.meet-edward}"
}

# edward_label SERVICE -> "<prefix>.SERVICE" (backend, frontend, graph-worker,
# healthcheck, healthcheck-deep, backup, memory-report).
edward_label() {
    printf '%s.%s\n' "$(edward_launchd_prefix)" "$1"
}

# edward_launchd_loaded LABEL -> 0 when that LaunchAgent is loaded for this user.
edward_launchd_loaded() {
    launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1
}

# edward_pids_under DIR PID... -> the PIDs whose working directory is DIR
# or below it. How restart.sh tells this checkout's uvicorn and next-server
# apart from another checkout's (or another project's) on the same Mac.
edward_pids_under() {
    local dir pid cwd
    # lsof reports the physical path, so compare against it.
    dir="$(cd "$1" 2>/dev/null && pwd -P)" || return 0
    shift
    for pid in "$@"; do
        [ -n "$pid" ] || continue
        cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
        case "$cwd" in
            "$dir"|"$dir"/*) printf '%s\n' "$pid" ;;
        esac
    done
}

# edward_pids_not_under DIR PID... -> the PIDs whose working directory is
# not DIR or below it: another checkout's, or another app's.
edward_pids_not_under() {
    local dir="$1" ours pid
    shift
    ours=" $(edward_pids_under "$dir" "$@" | tr '\n' ' ') "
    for pid in "$@"; do
        [ -n "$pid" ] || continue
        case "$ours" in *" $pid "*) ;; *) printf '%s\n' "$pid" ;; esac
    done
}

# ── This checkout's servers ───────────────────────────────────────────
# restart.sh and scripts/install-launchagents.sh both find, and stop, the
# backend and frontend this checkout runs with these, so the two can never
# disagree about what "this checkout's backend" is.

# edward_port SERVICE -> the port backend (8000) or frontend (3000) serves on.
edward_port() {
    case "$1" in
        backend) printf '8000\n' ;;
        frontend) printf '3000\n' ;;
        *) return 1 ;;
    esac
}

# edward_listeners PORT -> the PIDs listening on PORT (not clients of it).
edward_listeners() {
    lsof -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

# edward_checkout_pids SERVICE [REPO] -> this checkout's backend or frontend
# processes, one per line: the listener on its port plus leftovers that lost
# the port but kept running (an OOM-killed parent leaving its child, a
# crashed reload) -- the backend's `uvicorn main:app … --port 8000`, the
# frontend's next / next-server, whose command line names no path. Only
# those whose working directory is REPO/backend or REPO/frontend (REPO
# defaults to this checkout), never every next-server on the Mac.
edward_checkout_pids() {
    local svc="$1" repo="${2:-$EDWARD_REPO}" port pattern
    port="$(edward_port "$svc")" || return 1
    case "$svc" in
        backend) pattern="uvicorn main:app.*--port $port" ;;
        frontend) pattern="next" ;;
    esac
    # shellcheck disable=SC2046
    edward_pids_under "$repo/$svc" $(edward_listeners "$port") $(pgrep -f "$pattern" 2>/dev/null || true) | sort -u
}

# edward_stop_pids NAME PID... -> TERM, wait up to 5 s, then KILL what is left.
edward_stop_pids() {
    local name="$1" i p alive
    shift
    [ $# -gt 0 ] || return 0
    kill "$@" 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8 9 10; do
        alive=""
        for p in "$@"; do kill -0 "$p" 2>/dev/null && alive="$alive $p"; done
        [ -z "$alive" ] && return 0
        sleep 0.5
    done
    echo "$name didn't stop gracefully, force killing:$alive" >&2
    # shellcheck disable=SC2086
    kill -9 $alive 2>/dev/null || true
    sleep 1
}

# edward_descends_from PID ANCESTOR -> 0 when ANCESTOR is PID or one of its
# parents.
edward_descends_from() {
    local pid="$1" anc="$2" n=0
    [ -n "$pid" ] && [ -n "$anc" ] || return 1
    while [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null && [ $n -lt 64 ]; do
        [ "$pid" = "$anc" ] && return 0
        pid="$({ ps -o ppid= -p "$pid" 2>/dev/null || true; } | tr -d ' ')"
        n=$((n + 1))
    done
    return 1
}

# edward_launchd_pid LABEL -> the PID launchd runs for LABEL now, or nothing.
edward_launchd_pid() {
    { launchctl print "gui/$(id -u)/$1" 2>/dev/null || true; } \
        | awk '!found && /^[[:space:]]*pid = [0-9]+[[:space:]]*$/ { sub(/^[[:space:]]*pid = /, ""); sub(/[[:space:]]+$/, ""); print; found = 1 }'
}

# edward_not_launched_by LABEL PID... -> the PIDs that LABEL's running job
# did not start (all of them when the job runs nothing): a server started by
# hand from a terminal, which launchd's own copy can never displace.
edward_not_launched_by() {
    local label="$1" job pid
    shift
    job="$(edward_launchd_pid "$label")"
    for pid in "$@"; do
        [ -n "$pid" ] || continue
        if [ -n "$job" ] && edward_descends_from "$pid" "$job"; then
            continue
        fi
        printf '%s\n' "$pid"
    done
}

# ── Which checkout a loaded LaunchAgent serves ────────────────────────
# The label is per user, not per checkout: a second clone or worktree whose
# .env keeps the default prefix resolves to the same labels as the install
# that serves. restart.sh asks these before it kickstarts anything, so a
# second clone never restarts someone's live Edward.

# edward_physical PATH -> PATH with its symlinks resolved (a file's directory
# resolved), or PATH unchanged when it does not exist.
edward_physical() {
    local p="$1" d
    if [ -d "$p" ]; then
        d="$(cd "$p" 2>/dev/null && pwd -P)" && { printf '%s\n' "$d"; return 0; }
    elif [ -e "$p" ]; then
        d="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)" && { printf '%s/%s\n' "$d" "$(basename "$p")"; return 0; }
    fi
    printf '%s\n' "$p"
}

# edward_agent_paths LABEL -> the loaded agent's working directory, program
# and program arguments, one per line: what `launchctl print` shows launchd
# runs, then what the plist it loaded (its `path = `; else LABEL's plist in
# EDWARD_LAUNCHAGENTS_DIR, default ~/Library/LaunchAgents) says.
edward_agent_paths() {
    local label="$1" printed plist i v
    printed="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null || true)"
    printf '%s\n' "$printed" | awk '
        /^[[:space:]]*working directory = / { sub(/^[[:space:]]*working directory = /, ""); print; next }
        /^[[:space:]]*program = / { sub(/^[[:space:]]*program = /, ""); print; next }
        /^[[:space:]]*arguments = \{/ { inargs = 1; next }
        inargs && /^[[:space:]]*\}/ { inargs = 0; next }
        inargs { sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, ""); print }
    '
    plist="$(printf '%s\n' "$printed" | awk '!found && /^[[:space:]]*path = \// { sub(/^[[:space:]]*path = /, ""); print; found = 1 }')"
    [ -n "$plist" ] || plist="$(edward_expand_home "${EDWARD_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}")/$label.plist"
    [ -r "$plist" ] && command -v plutil >/dev/null 2>&1 || return 0
    v="$(plutil -extract WorkingDirectory raw -o - "$plist" 2>/dev/null)" && printf '%s\n' "$v"
    v="$(plutil -extract Program raw -o - "$plist" 2>/dev/null)" && printf '%s\n' "$v"
    i=0
    while [ $i -lt 64 ] && v="$(plutil -extract "ProgramArguments.$i" raw -o - "$plist" 2>/dev/null)"; do
        printf '%s\n' "$v"
        i=$((i + 1))
    done
    return 0
}

# edward_agent_serves LABEL DIR -> 0 when the loaded agent LABEL runs from DIR
# (this checkout's backend/ or frontend/): its working directory is DIR, or
# one of its program arguments is a file directly in DIR (backend/start.sh,
# frontend/run-launchd.sh). Exact, so an install nested inside another
# checkout (a worktree under .claude/worktrees, say) is never taken for it.
edward_agent_serves() {
    local label="$1" dir p
    dir="$(edward_physical "$2")"
    while IFS= read -r p; do
        case "$p" in /*) ;; *) continue ;; esac
        p="$(edward_physical "$p")"
        [ "$p" = "$dir" ] && return 0
        [ -f "$p" ] && [ "$(dirname "$p")" = "$dir" ] && return 0
    done <<EOF
$(edward_agent_paths "$label")
EOF
    return 1
}

# ── The first password ────────────────────────────────────────────────
# While no password is set, the backend keeps a one-time setup code in
# backend/.setup_code (mode 0600; backend/services/setup_code.py) and the
# login page asks for it: only someone at the Mac can read it. setup.sh,
# restart.sh and scripts/install-launchagents.sh say where it is (never
# what it is) with this one line.
EDWARD_SETUP_CODE_NOTE="First time? Your setup code is in backend/.setup_code (cat backend/.setup_code)"

# edward_password_state [PORT] -> "unset" or "set" as the backend on PORT
# (default 8000) answers GET /api/auth/status, "unknown" when it does not
# answer (not started yet, still starting).
edward_password_state() {
    local port="${1:-$(edward_port backend)}" body
    body="$(curl -fsS --max-time 3 "http://127.0.0.1:$port/api/auth/status" 2>/dev/null)" || body=""
    case "$body" in
        *'"configured":false'*|*'"configured": false'*) printf 'unset\n' ;;
        *'"configured":true'*|*'"configured": true'*) printf 'set\n' ;;
        *) printf 'unknown\n' ;;
    esac
}

# edward_setup_code_note [PORT] -> EDWARD_SETUP_CODE_NOTE unless the backend
# says a password is already set: a backend that has not answered yet is
# most likely a first start, which is when the code is needed.
edward_setup_code_note() {
    [ "$(edward_password_state "$@")" = set ] && return 0
    printf '%s\n' "$EDWARD_SETUP_CODE_NOTE"
}
