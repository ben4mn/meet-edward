#!/bin/bash
# Restart Edward: stop this checkout's backend and frontend, start them again.
#
# Usage:
#   ./restart.sh           # Restart both frontend and backend
#   ./restart.sh frontend  # Restart only the frontend
#   ./restart.sh backend   # Restart only the backend
#
# When the LaunchAgents are installed (scripts/install-launchagents.sh) this
# is `launchctl kickstart -k` on <prefix>.backend / <prefix>.frontend, the
# prefix from EDWARD_LAUNCHD_PREFIX (environment or .env, default
# com.meet-edward). Without them it stops and starts the processes itself.
# Either way it only ever touches this checkout's Edward:
#   - by hand, it stops only processes whose working directory is this
#     checkout's backend/ or frontend/;
#   - with an agent loaded, it first checks that the agent runs this
#     checkout (the working directory and program arguments `launchctl
#     print` shows, and those of the plist it loaded) and refuses, exit 78,
#     when it runs another one: a second clone or a worktree keeps the
#     default prefix, and must never restart someone's live install. A copy
#     this checkout started by hand that holds the port is stopped first.
#
# Until the first password is set, the summary ends with where its one-time
# setup code is (backend/.setup_code).
#
# The first start is slow: the backend downloads its embedding model and
# sets up an empty database before it opens :8000, and the frontend runs its
# first `next build`. The backend is started first, so a long build never
# holds it up, and given 2 minutes to answer /health; when it is still
# running after that, this says "still starting" and exits 0 -- tail the log.
# Exit 1 means something did not start (its log tail is printed).

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$PROJECT_DIR/scripts/lib/edward-env.sh"
BACKEND_LOG="/tmp/edward-backend.log"
FRONTEND_LOG="/tmp/edward-frontend.log"
BACKEND_PORT="$(edward_port backend)"
FRONTEND_PORT="$(edward_port frontend)"
BACKEND_LABEL="$(edward_label backend)"
FRONTEND_LABEL="$(edward_label frontend)"
BACKEND_WAIT_S=120    # a first start: the model download + init_db on an empty database
FRONTEND_WAIT_S=240   # a first start: a cold `next build`
PROGRESS_EVERY_S=15

# Per service, set as the run goes (bash 3.2 has no associative arrays):
#   *_MODE   launchd | manual          (resolve_mode, before anything stops)
#   *_PID    the process started by hand (manual mode)
#   *_STATE  up | starting | failed    (start_services)
BACKEND_MODE="";  FRONTEND_MODE=""
BACKEND_PID="";   FRONTEND_PID=""
BACKEND_STATE=""; FRONTEND_STATE=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No color

info()  { echo -e "${GREEN}[restart]${NC} $1"; }
warn()  { echo -e "${YELLOW}[restart]${NC} $1"; }
error() { echo -e "${RED}[restart]${NC} $1"; }

# Move the previous log aside so a crash trace is preserved across restarts.
rotate_log() {
    local log="$1"
    [ -f "$log" ] && mv -f "$log" "${log}.prev"
    return 0
}

listeners() { edward_listeners "$1"; }

# ── Per-service variables ─────────────────────────────────────────────
title() { case "$1" in backend) echo Backend ;; frontend) echo Frontend ;; *) echo "$1" ;; esac; }
mode_of()  { if [ "$1" = backend ]; then echo "$BACKEND_MODE"; else echo "$FRONTEND_MODE"; fi; }
pid_of()   { if [ "$1" = backend ]; then echo "$BACKEND_PID"; else echo "$FRONTEND_PID"; fi; }
state_of() { if [ "$1" = backend ]; then echo "$BACKEND_STATE"; else echo "$FRONTEND_STATE"; fi; }
label_of() { if [ "$1" = backend ]; then echo "$BACKEND_LABEL"; else echo "$FRONTEND_LABEL"; fi; }
log_of()   { if [ "$1" = backend ]; then echo "$BACKEND_LOG"; else echo "$FRONTEND_LOG"; fi; }
set_state() { if [ "$1" = backend ]; then BACKEND_STATE="$2"; else FRONTEND_STATE="$2"; fi; }

# ── Which way: launchd or by hand ─────────────────────────────────────
# When a LaunchAgent is loaded, the manual stop/start path races with
# launchd's KeepAlive=true respawn — launchd brings up its own copy
# within ThrottleInterval (10s), beating us to the port. So a loaded agent
# is restarted with `launchctl kickstart -k`, an atomic restart of
# launchd's own process -- but only when it is this checkout's agent.

# refuse_foreign_agent SERVICE LABEL -> explains, on stderr, why a loaded
# agent that runs another checkout is left alone.
refuse_foreign_agent() {
    local svc="$1" label="$2" paths
    paths="$(edward_agent_paths "$label" | grep '^/' | grep -v -e '^/bin/' -e '^/usr/bin/' | sort -u | tr '\n' ' ')"
    {
        error "$label is loaded, but it does not run this checkout."
        if [ -n "$paths" ]; then
            error "  It runs: $paths"
        else
            error "  I cannot tell which checkout it runs: \`launchctl print gui/$(id -u)/$label\`"
            error "  shows no working directory or program, and its plist is not readable."
        fi
        error "  This checkout: $PROJECT_DIR"
        error "Not restarting it: ./restart.sh only ever restarts its own checkout's Edward."
        error "  - To restart that install, run the restart.sh in its own checkout."
        error "  - To work on this checkout beside it, use the tests and the UX harness"
        error "    (bash frontend/scripts/mock-api/run-harness.sh, ports 3100 and 8100)."
    } >&2
}

# mode_for SERVICE -> "launchd" or "manual"; returns 78 (after explaining)
# when the service's agent is loaded from another checkout, or from one it
# cannot tell.
mode_for() {
    local svc="$1" label
    label="$(label_of "$svc")"
    if ! edward_launchd_loaded "$label"; then
        echo manual
        return 0
    fi
    if edward_agent_serves "$label" "$PROJECT_DIR/$svc"; then
        echo launchd
        return 0
    fi
    refuse_foreign_agent "$svc" "$label"
    return 78
}

# resolve_mode COMPONENT -> sets BACKEND_MODE / FRONTEND_MODE for what
# COMPONENT restarts, before anything is stopped; exits 78 on a foreign agent.
resolve_mode() {
    local rc=0
    case "$1" in
        all|backend) BACKEND_MODE="$(mode_for backend)" || rc=$? ;;
    esac
    [ "$rc" -eq 0 ] || exit "$rc"
    case "$1" in
        all|frontend) FRONTEND_MODE="$(mode_for frontend)" || rc=$? ;;
    esac
    [ "$rc" -eq 0 ] || exit "$rc"
    return 0
}

# ── Stop ──────────────────────────────────────────────────────────────
stop_service() {
    local svc="$1" mode label port pids foreign
    mode="$(mode_of "$svc")"; label="$(label_of "$svc")"; port="$(edward_port "$svc")"
    if [ "$mode" = "launchd" ]; then
        # kickstart -k replaces launchd's own process. A copy started by
        # hand from this checkout that holds the port would keep it, and
        # launchd's copy would fail to bind every 10 s behind it: stop it.
        # shellcheck disable=SC2046
        pids="$(edward_not_launched_by "$label" $(edward_pids_under "$PROJECT_DIR/$svc" $(listeners "$port")))"
        if [ -n "$pids" ]; then
            warn "A $svc started by hand from this checkout holds port $port (PID $(echo $pids)); stopping it so launchd's copy can bind"
            # shellcheck disable=SC2086
            edward_stop_pids "$svc" $pids
        fi
        # shellcheck disable=SC2046
        foreign="$(edward_pids_not_under "$PROJECT_DIR/$svc" $(listeners "$port"))"
        if [ -n "$foreign" ]; then
            warn "Port $port is held by a process outside this checkout (PID $(echo $foreign)); launchd's $svc cannot bind until it stops (lsof -nP -iTCP:$port -sTCP:LISTEN)"
        fi
        info "$(title "$svc") is launchd-managed ($label); deferring to kickstart"
        return 0
    fi
    info "Stopping $svc..."
    # The listener on its port, plus leftovers that lost the port but kept
    # running (an OOM-killed parent leaving its child, a crashed reload, a
    # next-server whose parent died): only those whose working directory is
    # this checkout's backend/ or frontend/ (scripts/lib/edward-env.sh).
    pids="$(edward_checkout_pids "$svc" "$PROJECT_DIR")"
    if [ -n "$pids" ]; then
        # shellcheck disable=SC2086
        edward_stop_pids "$svc" $pids
        info "$(title "$svc") stopped"
    else
        info "$(title "$svc") was not running"
    fi
    # shellcheck disable=SC2046
    foreign="$(edward_pids_not_under "$PROJECT_DIR/$svc" $(listeners "$port"))"
    if [ -n "$foreign" ]; then
        warn "Port $port is held by a process outside this checkout (PID $(echo $foreign)); not touching it"
    fi
}

stop_backend()  { stop_service backend; }
stop_frontend() { stop_service frontend; }

# ── Start, without waiting ────────────────────────────────────────────
# Both return 1 (after saying why) when they cannot start; called in a
# `||` context, so every failure is checked by hand.
launch_backend() {
    if [ "$BACKEND_MODE" = "launchd" ]; then
        info "Kickstarting backend via launchd ($BACKEND_LABEL)..."
        rotate_log "$BACKEND_LOG"
        launchctl kickstart -k "gui/$(id -u)/$BACKEND_LABEL" \
            || { error "launchctl kickstart $BACKEND_LABEL failed"; return 1; }
        return 0
    fi
    if [ -n "$(listeners "$BACKEND_PORT")" ]; then
        error "Port $BACKEND_PORT is still in use; not starting the backend"
        return 1
    fi
    info "Starting backend..."
    rotate_log "$BACKEND_LOG"
    ( cd "$PROJECT_DIR/backend" && exec bash start.sh ) > "$BACKEND_LOG" 2>&1 &
    BACKEND_PID=$!
    return 0
}

launch_frontend() {
    if [ "$FRONTEND_MODE" = "launchd" ]; then
        info "Kickstarting frontend via launchd ($FRONTEND_LABEL)..."
        rotate_log "$FRONTEND_LOG"
        # run-launchd.sh runs `next build` first when sources changed — a
        # couple of minutes — then `next start`.
        launchctl kickstart -k "gui/$(id -u)/$FRONTEND_LABEL" \
            || { error "launchctl kickstart $FRONTEND_LABEL failed"; return 1; }
        return 0
    fi
    if [ -n "$(listeners "$FRONTEND_PORT")" ]; then
        error "Port $FRONTEND_PORT is still in use; not starting the frontend"
        return 1
    fi
    info "Starting frontend..."
    rotate_log "$FRONTEND_LOG"
    ( cd "$PROJECT_DIR/frontend" && bash ./ensure-build.sh && exec npm run start -- -p 3000 ) > "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!
    return 0
}

# ── Wait ──────────────────────────────────────────────────────────────
# The backend opens its port only once its whole startup has run, and then
# /health answers 200.
backend_answers() {
    curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$BACKEND_PORT/health" 2>/dev/null
}

# Any HTTP answer will do (the app redirects to /login).
frontend_answers() {
    curl -s -o /dev/null --max-time 5 "http://localhost:$FRONTEND_PORT" 2>/dev/null
}

# still_running SERVICE -> 0 while the process this run started by hand, or
# launchd's job for it, is alive.
still_running() {
    local svc="$1" pid
    if [ "$(mode_of "$svc")" = "launchd" ]; then
        [ -n "$(edward_launchd_pid "$(label_of "$svc")")" ]
    else
        pid="$(pid_of "$svc")"
        [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
    fi
}

# wait_for SERVICE LIMIT_S HINT -> waits until SERVICE answers. 0 when it
# does (state up) or when it is still running after LIMIT_S (state
# starting); 1 when it is not running (state failed): a process started by
# hand that exited, or a launchd job with no process at the end.
wait_for() {
    local svc="$1" limit="$2" hint="$3" log i=0
    log="$(log_of "$svc")"
    while ! "${svc}_answers"; do
        if [ "$(mode_of "$svc")" != "launchd" ] && ! still_running "$svc"; then
            set_state "$svc" failed
            error "$(title "$svc") exited before it answered. Check log: $log"
            tail -20 "$log" 2>/dev/null || true
            return 1
        fi
        if [ "$i" -ge "$limit" ]; then
            if still_running "$svc"; then
                set_state "$svc" starting
                warn "$(title "$svc") is still starting after ${limit}s ($hint) — tail -f $log"
                tail -5 "$log" 2>/dev/null | sed 's/^/    /' || true
                return 0
            fi
            set_state "$svc" failed
            error "$(title "$svc") is not running after ${limit}s (launchd keeps retrying). Check log: $log"
            tail -20 "$log" 2>/dev/null || true
            return 1
        fi
        if [ "$i" -gt 0 ] && [ $((i % PROGRESS_EVERY_S)) -eq 0 ]; then
            info "  still waiting for the $svc (${i}s; $hint — tail $log)"
        fi
        sleep 1
        i=$((i + 1))
    done
    set_state "$svc" up
    if [ "$(mode_of "$svc")" = "launchd" ]; then
        info "$(title "$svc") is UP on port $(edward_port "$svc") (launchd-managed)"
    else
        info "$(title "$svc") is UP on port $(edward_port "$svc") (PID $(pid_of "$svc"))"
    fi
    return 0
}

wait_backend() {
    wait_for backend "$BACKEND_WAIT_S" "the first start downloads the embedding model and sets up the database"
}

wait_frontend() {
    wait_for frontend "$FRONTEND_WAIT_S" "a build may be running"
}

# start_services SERVICE... -> launches every one first, then waits for each,
# so the backend's first start overlaps the frontend's build and one that
# cannot start never keeps the other from starting. 1 when any failed.
start_services() {
    local svc rc=0
    for svc in "$@"; do
        "launch_$svc" || { set_state "$svc" failed; rc=1; }
    done
    for svc in "$@"; do
        [ "$(state_of "$svc")" = failed ] && continue
        "wait_$svc" || rc=1
    done
    return $rc
}

# ── Main ──────────────────────────────────────────────────────────────
# setup_code_line -> the one line saying where the first password's setup
# code is (scripts/lib/edward-env.sh), when the backend says no password is
# set yet, or is still on its (first) start and cannot say. Nothing once a
# password is set, or when the backend did not start.
setup_code_line() {
    local backend password
    backend="$(state_of backend)"
    [ "$backend" = failed ] && return 0
    password="$(edward_password_state "$BACKEND_PORT")"
    case "$password" in
        unset) ;;
        unknown) [ "$backend" = starting ] || return 0 ;;
        *) return 0 ;;
    esac
    info "$EDWARD_SETUP_CODE_NOTE"
}

summary() {
    local svc state url
    echo ""
    info "=== Done ==="
    for svc in backend frontend; do
        state="$(state_of "$svc")"
        url="http://localhost:$(edward_port "$svc")"
        case "$state" in
            up)       info  "  $(title "$svc"):  $url  (log: $(log_of "$svc"))" ;;
            starting) warn  "  $(title "$svc"):  still starting — tail -f $(log_of "$svc")" ;;
            failed)   error "  $(title "$svc"):  did not start — see $(log_of "$svc")" ;;
        esac
    done
    setup_code_line
    echo ""
}

main() {
    local component="${1:-all}" rc=0  # "all", "frontend", or "backend"
    case "$component" in
        all|frontend|backend) ;;
        *)
            error "Unknown component: $component"
            echo "Usage: $0 [all|frontend|backend]"
            exit 1
            ;;
    esac

    echo ""
    info "=== Edward Restart ==="
    echo ""

    resolve_mode "$component"

    case "$component" in
        all)
            stop_backend
            stop_frontend
            # The backend first: its first start (model download, init_db)
            # overlaps the frontend's build.
            start_services backend frontend || rc=$?
            ;;
        frontend)
            stop_frontend
            start_services frontend || rc=$?
            ;;
        backend)
            stop_backend
            start_services backend || rc=$?
            ;;
    esac

    summary
    return "$rc"
}

# Sourced by scripts/test_restart.sh: the functions above, nothing run.
if [ -n "${RESTART_LIB_ONLY:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

main "$@"
