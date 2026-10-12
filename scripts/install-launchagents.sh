#!/bin/bash
# Install (or remove) the LaunchAgents that keep Edward running on this Mac.
#
#   bash scripts/install-launchagents.sh               # install and load
#   bash scripts/install-launchagents.sh --with-graph  # also the graph worker
#   bash scripts/install-launchagents.sh --dry-run     # print the plists, change nothing
#   bash scripts/install-launchagents.sh --uninstall   # unload and remove them
#
# The agents (labels <prefix>.<service>; the prefix is EDWARD_LAUNCHD_PREFIX
# from the environment or .env, default com.meet-edward):
#   backend           backend/start.sh, always on (KeepAlive, 10 s throttle);
#                     log /tmp/edward-backend.log
#   frontend          frontend/run-launchd.sh, always on; it rotates its own
#                     log, /tmp/edward-frontend.log
#   graph-worker      backend/start-graph-worker.sh, only with --with-graph
#                     (and GRAPHITI_ENABLED=true in .env); /tmp/edward-graph-worker.log
#   healthcheck       scripts/health_check.sh every 5 minutes
#   healthcheck-deep  scripts/health_check.sh --deep at 08:00
#                     (both log to /tmp/edward-healthcheck.log)
#   backup            the nightly backup at 03:30 (backend/scripts/backup_memories.sh,
#                     through run_backup_with_fda.py); /tmp/edward-backup.log
#   memory-report     backend/scripts/memory_health_report.py --push at 09:05;
#                     /tmp/edward-memory-report.log
#
# Paths come from where this checkout is, so run it from the checkout you
# serve. Running it again rewrites only what changed: an agent whose plist
# is the same stays loaded and running. A tunnel (cloudflared, ngrok …) is
# yours to install; name its label in EDWARD_TUNNEL_LABEL and the health
# check watches it.
#
# Options:
#   --with-graph       install the graph worker too (knowledge-graph memory)
#   --without-backup   leave the nightly backup out
#   --dry-run          print each plist and what would happen; touch nothing
#   --uninstall        unload and delete every agent with this prefix
#   -h, --help         this text
# Environment: EDWARD_LAUNCHD_PREFIX, EDWARD_FDA_PYTHON (the python that
# runs the backup, see below), EDWARD_LAUNCHAGENTS_DIR (default
# ~/Library/LaunchAgents).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
. "$HERE/lib/edward-env.sh"

WITH_GRAPH=false
WITH_BACKUP=true
DRY_RUN=false
UNINSTALL=false
[ -n "${INSTALL_AGENTS_LIB_ONLY:-}" ] && set --   # sourced by the tests: no options
for arg in "$@"; do
    case "$arg" in
        --with-graph) WITH_GRAPH=true ;;
        --without-backup) WITH_BACKUP=false ;;
        --dry-run) DRY_RUN=true ;;
        --uninstall) UNINSTALL=true ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 64 ;;
    esac
done

PREFIX="$(edward_launchd_prefix)"
DEST="$(edward_expand_home "${EDWARD_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}")"
DOMAIN="gui/$(id -u)"
BREW_PREFIX="$(edward_brew_prefix)"
# The system's own directories first, then Postgres 16's tools
# (postgresql@16 is keg-only, so Homebrew's bin has no psql or pg_config on
# a fresh install: edward_pg_bin), then Homebrew's. Never ~/.local/bin or
# ~/bin. These jobs run outside Edward's sandbox, and his shell can write
# the user's home and Homebrew's prefix (he may `brew install`): a program
# a job runs by bare name (the health check's curl and stat) must be the
# system's (review 2026-10-12; scripts/lib/edward-env.sh:
# edward_service_path).
AGENT_PATH="$(edward_service_path)"
VENV_PY="$REPO/backend/.venv/bin/python"
ALL_SERVICES="backend frontend graph-worker healthcheck healthcheck-deep backup memory-report"

say() { echo "[launchagents] $*"; }

# ── The backup's python ──────────────────────────────────────────────
# ADAPT: the nightly backup runs under a python that may hold Full Disk Access.
#   How it works here: the backup agent runs backend/scripts/run_backup_with_fda.py with
#   the real path of the python3.12 the backend's venv is built on, so one grant to that
#   binary covers chat.db and a removable backup drive alike.
#   To adapt: grant that path Full Disk Access (printed at the end) when backups go to a
#   /Volumes drive; EDWARD_FDA_PYTHON picks another interpreter; re-run this script
#   after `brew upgrade python@3.12` moves the binary.
fda_python() {
    local py=""
    local fda
    fda="$(edward_env EDWARD_FDA_PYTHON)"
    if [ -n "$fda" ]; then
        printf '%s\n' "$fda"
        return 0
    fi
    if [ -x "$VENV_PY" ]; then
        py="$("$VENV_PY" -c 'import os, sys; print(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))' 2>/dev/null || true)"
    fi
    if [ -z "$py" ] && [ -x "$BREW_PREFIX/opt/python@3.12/bin/python3.12" ]; then
        py="$("$BREW_PREFIX/opt/python@3.12/bin/python3.12" -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
    fi
    printf '%s\n' "$py"
}

# ── Rendering ─────────────────────────────────────────────────────────
x() {  # XML-escape one value
    local s="$1"
    s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"
    printf '%s' "$s"
}

p_open() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Written by scripts/install-launchagents.sh from $(x "$REPO"); re-run it rather than editing this file. -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$(x "$1")</string>
EOF
}

p_args() {
    local a
    echo "    <key>ProgramArguments</key>"
    echo "    <array>"
    for a in "$@"; do echo "        <string>$(x "$a")</string>"; done
    echo "    </array>"
}

p_workdir() {
    echo "    <key>WorkingDirectory</key>"
    echo "    <string>$(x "$1")</string>"
}

# HOMEBREW_PREFIX rides along so the jobs' scripts never run `brew --prefix`
# (Homebrew's own code, which Edward's shell can change) to find it.
p_env() {
    cat <<EOF
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$(x "$AGENT_PATH")</string>
        <key>EDWARD_LAUNCHD_PREFIX</key>
        <string>$(x "$PREFIX")</string>
        <key>HOMEBREW_PREFIX</key>
        <string>$(x "$BREW_PREFIX")</string>
    </dict>
EOF
}

p_always_on() {
    cat <<EOF
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
EOF
}

p_every() {
    cat <<EOF
    <key>StartInterval</key>
    <integer>$1</integer>
    <key>RunAtLoad</key>
    <false/>
EOF
}

p_daily_at() {
    # If the Mac is asleep at that time, launchd runs it at the next wake.
    cat <<EOF
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>$1</integer>
        <key>Minute</key><integer>$2</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
EOF
}

p_log() {
    cat <<EOF
    <key>StandardOutPath</key>
    <string>$(x "$1")</string>
    <key>StandardErrorPath</key>
    <string>$(x "$1")</string>
EOF
}

p_close() {
    echo "</dict>"
    echo "</plist>"
}

# render SERVICE -> the plist for <prefix>.SERVICE on stdout.
render() {
    local svc="$1" label="$PREFIX.$1"
    p_open "$label"
    case "$svc" in
        backend)
            p_args /bin/bash "$REPO/backend/start.sh"
            p_workdir "$REPO/backend"
            p_env
            p_always_on
            p_log /tmp/edward-backend.log
            ;;
        frontend)
            # No StandardOutPath: run-launchd.sh moves the last log to .prev
            # and writes its own, so a crash's output survives the respawn.
            p_args /bin/bash "$REPO/frontend/run-launchd.sh"
            p_workdir "$REPO/frontend"
            p_env
            p_always_on
            ;;
        graph-worker)
            # No StandardOutPath either: start-graph-worker.sh rotates its log.
            p_args /bin/bash "$REPO/backend/start-graph-worker.sh"
            p_workdir "$REPO/backend"
            p_env
            p_always_on
            ;;
        healthcheck)
            p_args /bin/bash "$REPO/scripts/health_check.sh"
            p_env
            p_every 300
            p_log /tmp/edward-healthcheck.log
            ;;
        healthcheck-deep)
            p_args /bin/bash "$REPO/scripts/health_check.sh" --deep
            p_env
            p_daily_at 8 0
            p_log /tmp/edward-healthcheck.log
            ;;
        backup)
            local py
            py="$(fda_python)"
            if [ -n "$py" ]; then
                p_args "$py" "$REPO/backend/scripts/run_backup_with_fda.py"
            else
                p_args /bin/bash "$REPO/backend/scripts/backup_memories.sh"
            fi
            p_workdir "$REPO/backend"
            p_env
            p_daily_at 3 30
            p_log /tmp/edward-backup.log
            ;;
        memory-report)
            p_args "$VENV_PY" "$REPO/backend/scripts/memory_health_report.py" --push
            p_workdir "$REPO/backend"
            p_env
            p_daily_at 9 5
            p_log /tmp/edward-memory-report.log
            ;;
        *)
            echo "unknown service $svc" >&2
            return 1
            ;;
    esac
    p_close
}

# ── launchctl ─────────────────────────────────────────────────────────
unload() {
    local label="$1" i
    edward_launchd_loaded "$label" || return 0
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    for i in $(seq 1 20); do
        edward_launchd_loaded "$label" || return 0
        sleep 0.5
    done
    say "warning: $label is still loaded after bootout"
}

# stop_hand_started SERVICE -> stops the backend or frontend this checkout
# runs outside launchd (./restart.sh before the agents were installed, a
# start.sh in a terminal). It would hold the port launchd's copy needs, and
# once the agent is loaded ./restart.sh restarts through launchd and leaves
# it be. Found the way restart.sh finds them (edward_checkout_pids: working
# directory in this checkout); another checkout's processes are never
# touched, and one of those holding the port is named instead.
stop_hand_started() {
    local svc="$1" label="$PREFIX.$1" port pids foreign
    port="$(edward_port "$svc")"
    # shellcheck disable=SC2046
    pids="$(edward_not_launched_by "$label" $(edward_checkout_pids "$svc" "$REPO"))"
    if [ -n "$pids" ]; then
        if $DRY_RUN; then
            say "would stop this checkout's $svc, started by hand (PID $(echo $pids)), so launchd's copy can take port $port"
        else
            say "stopping this checkout's $svc, started by hand (PID $(echo $pids)), so launchd's copy can take port $port"
            # shellcheck disable=SC2086
            edward_stop_pids "$svc" $pids
        fi
    fi
    $DRY_RUN && return 0
    # shellcheck disable=SC2046
    foreign="$(edward_pids_not_under "$REPO/$svc" $(edward_listeners "$port"))"
    if [ -n "$foreign" ]; then
        say "note: port $port is held by PID $(echo $foreign), which does not run from this checkout (another Edward, or another app). launchd's $svc retries every 10 s until the port is free; see it with: lsof -nP -iTCP:$port -sTCP:LISTEN"
    fi
    return 0
}

install_one() {
    local svc="$1" label="$PREFIX.$1" path="$DEST/$PREFIX.$1.plist" tmp
    tmp="$(mktemp "${TMPDIR:-/tmp}/edward-agent.XXXXXX")"
    render "$svc" > "$tmp"
    if command -v plutil >/dev/null 2>&1 && ! plutil -lint "$tmp" >/dev/null; then
        rm -f "$tmp"
        say "ERROR: the plist for $label does not lint; nothing installed for it"
        return 1
    fi
    if $DRY_RUN; then
        if [ -f "$path" ] && cmp -s "$tmp" "$path"; then
            say "would leave $label as it is ($path unchanged)"
        else
            case "$svc" in backend|frontend) stop_hand_started "$svc" ;; esac
            say "would write $path and (re)load $label:"
            cat "$tmp"
        fi
        rm -f "$tmp"
        return 0
    fi
    if [ -f "$path" ] && cmp -s "$tmp" "$path" && edward_launchd_loaded "$label"; then
        rm -f "$tmp"
        say "$label unchanged and loaded"
        return 0
    fi
    mkdir -p "$DEST"
    unload "$label"
    case "$svc" in backend|frontend) stop_hand_started "$svc" ;; esac
    mv -f "$tmp" "$path"
    chmod 644 "$path"
    launchctl bootstrap "$DOMAIN" "$path"
    say "loaded $label ($path)"
}

uninstall_all() {
    local svc label path
    for svc in $ALL_SERVICES; do
        label="$PREFIX.$svc"; path="$DEST/$label.plist"
        if $DRY_RUN; then
            { edward_launchd_loaded "$label" || [ -f "$path" ]; } && say "would unload $label and delete $path"
            continue
        fi
        unload "$label"
        if [ -f "$path" ]; then
            rm -f "$path"
            say "removed $label"
        fi
    done
    say "done. Edward is no longer started by launchd (./restart.sh still runs it by hand)."
}

# next_steps FDA_PY -> what to do now, printed after a real install. Until
# the first password is set (or while the backend has not answered yet) it
# says where the one-time setup code is.
next_steps() {
    local fda_py="$1" setup_note
    setup_note="$(edward_setup_code_note)"
    cat <<EOF

Edward's LaunchAgents are installed, and launchd is starting the backend and the
frontend now. The first start takes a few minutes (the backend downloads its embedding
model and sets up the database; the frontend runs its first build); then open
http://localhost:3000. Watch it with: tail -f /tmp/edward-backend.log /tmp/edward-frontend.log
EOF
    [ -n "$setup_note" ] && printf '%s\n' "$setup_note"
    cat <<EOF
Logs: /tmp/edward-backend.log, /tmp/edward-frontend.log, /tmp/edward-healthcheck.log
(and ~/.edward-health/health.log), /tmp/edward-backup.log.
Restart with:  $REPO/restart.sh [backend|frontend]
  (from now on it restarts these agents: launchctl kickstart -k $DOMAIN/$PREFIX.backend)
Stop them:     bash $REPO/scripts/install-launchagents.sh --uninstall

macOS permissions (System Settings > Privacy & Security), granted once:
  - Full Disk Access for ${fda_py:-the python3.12 your backend venv uses}
      Edward reads iMessage (~/Library/Messages/chat.db) and Mail through it; the
      backup needs it only when EDWARD_BACKUP_DIR is on a removable drive.
  - Automation: macOS asks the first time Edward sends a message or opens a page
      in Chrome; answer Allow.
  - Calendars / Reminders: backend/scripts/eventkit_request_access.sh asks once.
After \`brew upgrade python@3.12\` the python's path changes: grant it again and
re-run this script.
EOF
}

# Sourced by scripts/test_install_launchagents.sh: stop here, change nothing.
if [ -n "${INSTALL_AGENTS_LIB_ONLY:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

# ── Main ──────────────────────────────────────────────────────────────
if [ "$(uname -s)" != "Darwin" ]; then
    echo "LaunchAgents are macOS only." >&2
    exit 1
fi

if $UNINSTALL; then
    uninstall_all
    exit 0
fi

if [ ! -x "$VENV_PY" ]; then
    say "warning: $VENV_PY is missing -- run ./setup.sh first, or the backend agent will fail until it exists"
fi
[ -f "$REPO/.env" ] || say "warning: no .env in $REPO -- run ./setup.sh (or copy .env.example) first"

SERVICES="backend frontend healthcheck healthcheck-deep memory-report"
$WITH_BACKUP && SERVICES="$SERVICES backup"
$WITH_GRAPH && SERVICES="$SERVICES graph-worker"

say "prefix $PREFIX, checkout $REPO, agents in $DEST"
for svc in $SERVICES; do
    install_one "$svc"
done

# Agents installed before but not asked for this time are unloaded, so the
# set on disk is always the set asked for.
for svc in $ALL_SERVICES; do
    case " $SERVICES " in *" $svc "*) continue ;; esac
    path="$DEST/$PREFIX.$svc.plist"
    if [ -f "$path" ] || edward_launchd_loaded "$PREFIX.$svc"; then
        if $DRY_RUN; then
            say "would unload and remove $PREFIX.$svc (not asked for)"
        else
            unload "$PREFIX.$svc"
            rm -f "$path"
            say "removed $PREFIX.$svc (not asked for this time)"
        fi
    fi
done

$DRY_RUN && exit 0

next_steps "$(fda_python)"
