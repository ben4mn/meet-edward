#!/bin/bash
# Tests for restart.sh: which way it restarts (launchd or by hand), that it
# refuses a LaunchAgent that runs another checkout, what it stops, the order
# it starts things in and how it waits. Nothing here starts Edward, calls the
# real launchctl, kills anything but the test's own `sleep`s, or touches a
# port: launchctl, the listeners, curl and the stop are stubbed.
#
# Run: bash scripts/test_restart.sh

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

TMP="$(mktemp -d)"
SLEEPERS=""
cleanup() {
    # shellcheck disable=SC2086
    [ -n "$SLEEPERS" ] && { kill $SLEEPERS; wait $SLEEPERS; } >/dev/null 2>&1
    rm -rf "$TMP"
}
trap cleanup EXIT

export EDWARD_ENV_FILE="$TMP/no-such.env"
export EDWARD_LAUNCHAGENTS_DIR="$TMP/agents"
unset EDWARD_LAUNCHD_PREFIX
mkdir -p "$TMP/agents"

RESTART_LIB_ONLY=1 . "$REPO/restart.sh"
set +e   # restart.sh's own `set -e` must not end the test run

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }
contains() { case "$2" in *"$3"*) ok "$1";; *) nope "$1 (no '$3' in: $2)";; esac; }
lacks() { case "$2" in *"$3"*) nope "$1 ('$3' in: $2)";; *) ok "$1";; esac; }

check "the checkout is the repo restart.sh lives in" "$PROJECT_DIR" "$REPO"
check "default labels" "$BACKEND_LABEL $FRONTEND_LABEL" "com.meet-edward.backend com.meet-edward.frontend"

# ── Stubs ─────────────────────────────────────────────────────────────
# launchctl: `print` answers from $TMP/print.<label> (exit 113, "not
# loaded", when there is none); every other verb is recorded, never run.
LAUNCHCTL_LOG="$TMP/launchctl.log"; : > "$LAUNCHCTL_LOG"
launchctl() {
    if [ "$1" = "print" ]; then
        local label="${2##*/}"
        [ -f "$TMP/print.$label" ] || return 113
        cat "$TMP/print.$label"
        return 0
    fi
    echo "launchctl $*" >> "$LAUNCHCTL_LOG"
    return 0
}
STOPPED="$TMP/stopped"; : > "$STOPPED"
edward_stop_pids() { shift; echo "$*" >> "$STOPPED"; }
FAKE_LISTENERS=""
listeners() { local p; for p in $FAKE_LISTENERS; do echo "$p"; done; }
edward_listeners() { listeners "$@"; }
pgrep() { return 1; }
sleep() { :; }
curl() { return 7; }
# Never start anything, never move /tmp/edward-*.log: a test that needs a
# launch overrides these in its own subshell.
LAUNCHED="$TMP/launched"; : > "$LAUNCHED"
rotate_log() { echo "rotate $1" >> "$LAUNCHED"; }
launch_backend()  { echo "launch_backend" >> "$LAUNCHED"; return 1; }
launch_frontend() { echo "launch_frontend" >> "$LAUNCHED"; return 1; }

# print_for LABEL WORKDIR [ARG...] -> a `launchctl print` like macOS's for
# an agent running ARG... in WORKDIR (no working directory line when empty).
print_for() {
    local label="$1" wd="$2" a
    shift 2
    {
        echo "gui/$(id -u)/$label = {"
        echo "	active count = 1"
        echo "	path = $TMP/agents/$label.plist"
        echo "	type = LaunchAgent"
        echo "	state = running"
        echo ""
        echo "	program = /bin/bash"
        echo "	arguments = {"
        for a in "$@"; do echo "		$a"; done
        echo "	}"
        echo ""
        [ -n "$wd" ] && echo "	working directory = $wd"
        echo "	stdout path = /tmp/edward-backend.log"
        echo "	environment = {"
        echo "		PATH => /opt/homebrew/bin:/usr/bin:/bin"
        echo "	}"
        echo "	pid = ${PRINT_PID:-4242}"
        echo "}"
    } > "$TMP/print.$label"
}
unload_all() { rm -f "$TMP"/print.*; }

# ── Which way ─────────────────────────────────────────────────────────
echo "mode_for:"
unload_all
check "no agent loaded -> by hand" "$(mode_for backend 2>/dev/null)" "manual"

print_for "$BACKEND_LABEL" "$REPO/backend" /bin/bash "$REPO/backend/start.sh"
check "this checkout's agent -> launchd" "$(mode_for backend 2>/dev/null)" "launchd"

print_for "$BACKEND_LABEL" "" /bin/bash "$REPO/backend/start.sh"
check "program arguments alone are enough" "$(mode_for backend 2>/dev/null)" "launchd"

print_for "$FRONTEND_LABEL" "$REPO/frontend" /bin/bash "$REPO/frontend/run-launchd.sh"
check "the frontend's agent -> launchd" "$(mode_for frontend 2>/dev/null)" "launchd"

mkdir -p "$TMP/other/backend"; : > "$TMP/other/backend/start.sh"
print_for "$BACKEND_LABEL" "$TMP/other/backend" /bin/bash "$TMP/other/backend/start.sh"
ERR="$(mode_for backend 2>&1 >/dev/null)"; RC=$?
check "another checkout's agent -> 78" "$RC" "78"
contains "says it does not run this checkout" "$ERR" "does not run this checkout"
contains "names the checkout it runs" "$ERR" "$TMP/other/backend"
contains "names this checkout" "$ERR" "$REPO"
lacks "does not list /bin/bash as a checkout" "$ERR" "/bin/bash "

print_for "$BACKEND_LABEL" "$REPO/.claude/worktrees/x/backend" /bin/bash "$REPO/.claude/worktrees/x/backend/start.sh"
mode_for backend >/dev/null 2>&1; check "a checkout nested inside this one is not this one" "$?" "78"

print_for "$BACKEND_LABEL" "$REPO/frontend" /bin/bash "$REPO/frontend/run-launchd.sh"
mode_for backend >/dev/null 2>&1; check "the frontend's directory is not the backend's" "$?" "78"

ln -s "$REPO" "$TMP/link"
print_for "$BACKEND_LABEL" "$TMP/link/backend" /bin/bash "$TMP/link/backend/start.sh"
check "a symlinked path to this checkout is this checkout" "$(mode_for backend 2>/dev/null)" "launchd"

# Nothing parseable in `launchctl print`: the agent's plist decides.
printf 'gui/%s/%s = {\n\tstate = running\n}\n' "$(id -u)" "$BACKEND_LABEL" > "$TMP/print.$BACKEND_LABEL"
ERR="$(mode_for backend 2>&1 >/dev/null)"; RC=$?
check "cannot tell, no plist -> 78" "$RC" "78"
contains "says it cannot tell" "$ERR" "cannot tell"
if command -v plutil >/dev/null 2>&1; then
    INSTALL_AGENTS_LIB_ONLY=1 bash -c '. "$1"; render backend' _ "$HERE/install-launchagents.sh" \
        > "$TMP/agents/$BACKEND_LABEL.plist"
    check "cannot tell from launchctl, the installer's plist for this checkout -> launchd" \
        "$(mode_for backend 2>/dev/null)" "launchd"
    sed "s#$REPO#$TMP/other#g" "$TMP/agents/$BACKEND_LABEL.plist" > "$TMP/p" && mv "$TMP/p" "$TMP/agents/$BACKEND_LABEL.plist"
    mode_for backend >/dev/null 2>&1; check "another checkout's plist -> 78" "$?" "78"
    rm -f "$TMP/agents/$BACKEND_LABEL.plist"
    # The plist launchctl says it loaded wins over the agents directory.
    mkdir -p "$TMP/elsewhere"
    INSTALL_AGENTS_LIB_ONLY=1 bash -c '. "$1"; render backend' _ "$HERE/install-launchagents.sh" \
        > "$TMP/elsewhere/loaded.plist"
    printf 'gui/%s/%s = {\n\tpath = %s\n\tstate = running\n\tprogram = /bin/bash\n}\n' \
        "$(id -u)" "$BACKEND_LABEL" "$TMP/elsewhere/loaded.plist" > "$TMP/print.$BACKEND_LABEL"
    check "launchctl's own plist path is read when its arguments are not shown" \
        "$(mode_for backend 2>/dev/null)" "launchd"
fi

echo "a refusal stops before anything is touched:"
unload_all; : > "$LAUNCHCTL_LOG"; : > "$STOPPED"
print_for "$FRONTEND_LABEL" "$TMP/other/frontend" /bin/bash "$TMP/other/frontend/run-launchd.sh"
OUT="$( (main all) 2>&1 )"; RC=$?
check "all, with the frontend agent another checkout's -> exit 78" "$RC" "78"
check "nothing kickstarted" "$(cat "$LAUNCHCTL_LOG")" ""
check "nothing stopped (not even this checkout's backend)" "$(cat "$STOPPED")" ""
lacks "never says Stopping" "$OUT" "Stopping"
check "nothing launched" "$(cat "$LAUNCHED")" ""
OUT="$(
    stop_backend() { echo "stop_backend $BACKEND_MODE"; }
    start_services() { echo "start_services $*"; set_state backend up; }
    main backend 2>&1
)"; RC=$?
check "backend alone: its agent is not loaded, so it restarts by hand" "$RC" "0"
contains "backend alone: stopped by hand" "$OUT" "stop_backend manual"
contains "backend alone: then started" "$OUT" "start_services backend"

# ── Stop ──────────────────────────────────────────────────────────────
echo "stop_service:"
unload_all; : > "$STOPPED"
# A process whose working directory is this checkout's backend/, and one
# from another directory: what edward_pids_under tells apart.
(cd "$REPO/backend" && exec /bin/sleep 300) & OURS=$!
(cd "$TMP/other/backend" && exec /bin/sleep 300) & THEIRS=$!
SLEEPERS="$OURS $THEIRS"
FAKE_LISTENERS="$OURS $THEIRS"
BACKEND_MODE=manual
OUT="$(stop_service backend 2>&1)"
check "by hand: stops only this checkout's" "$(cat "$STOPPED")" "$OURS"
contains "by hand: names the other process on the port, and only it" "$OUT" "outside this checkout (PID $THEIRS)"

: > "$STOPPED"
BACKEND_MODE=launchd
PRINT_PID=999999 print_for "$BACKEND_LABEL" "$REPO/backend" /bin/bash "$REPO/backend/start.sh"
OUT="$(stop_service backend 2>&1)"
check "launchd: stops this checkout's copy started by hand" "$(cat "$STOPPED")" "$OURS"
contains "launchd: says why" "$OUT" "started by hand"
contains "launchd: names another checkout's process holding the port" "$OUT" "held by a process outside this checkout (PID $THEIRS)"
: > "$STOPPED"
PRINT_PID="$OURS" print_for "$BACKEND_LABEL" "$REPO/backend" /bin/bash "$REPO/backend/start.sh"
OUT="$(stop_service backend 2>&1)"
check "launchd: leaves launchd's own process to kickstart" "$(cat "$STOPPED")" ""
contains "launchd: defers to kickstart" "$OUT" "deferring to kickstart"
PRINT_PID="$$" print_for "$BACKEND_LABEL" "$REPO/backend" /bin/bash "$REPO/backend/start.sh"
(cd "$REPO/backend" && exec /bin/sleep 300) & CHILD=$!
SLEEPERS="$SLEEPERS $CHILD"
FAKE_LISTENERS="$CHILD"
: > "$STOPPED"
stop_service backend >/dev/null 2>&1
check "launchd: a child of launchd's process is launchd's too" "$(cat "$STOPPED")" ""
FAKE_LISTENERS=""

# ── Start order ───────────────────────────────────────────────────────
echo "start_services:"
CALLS="$TMP/calls"
record() { echo "$1" >> "$CALLS"; }
(
    : > "$CALLS"
    launch_backend()  { record launch_backend; }
    launch_frontend() { record launch_frontend; }
    wait_backend()    { record wait_backend; set_state backend up; }
    wait_frontend()   { record wait_frontend; set_state frontend up; }
    start_services backend frontend
    echo "rc=$?" >> "$CALLS"
)
check "the backend is launched before the frontend's build, then both are waited for" \
    "$(tr '\n' ' ' < "$CALLS")" "launch_backend launch_frontend wait_backend wait_frontend rc=0 "
(
    : > "$CALLS"
    launch_backend()  { record launch_backend; return 1; }
    launch_frontend() { record launch_frontend; }
    wait_backend()    { record wait_backend; }
    wait_frontend()   { record wait_frontend; set_state frontend up; }
    start_services backend frontend >/dev/null
    echo "rc=$? backend=$BACKEND_STATE frontend=$FRONTEND_STATE" >> "$CALLS"
)
check "a backend that cannot start never keeps the frontend from starting" \
    "$(tr '\n' ' ' < "$CALLS")" "launch_backend launch_frontend wait_frontend rc=1 backend=failed frontend=up "
(
    : > "$CALLS"
    resolve_mode() { record "resolve_mode $1"; BACKEND_MODE=manual; FRONTEND_MODE=manual; }
    stop_backend()   { record stop_backend; }
    stop_frontend()  { record stop_frontend; }
    start_services() { record "start_services $*"; set_state backend up; set_state frontend up; }
    main all >/dev/null
    echo "rc=$?" >> "$CALLS"
)
check "main all: decides first, stops both, then starts the backend first" \
    "$(tr '\n' ' ' < "$CALLS")" "resolve_mode all stop_backend stop_frontend start_services backend frontend rc=0 "

echo "main under restart.sh's own set -e:"
# A child bash that keeps `set -e` on: by hand, nothing running, both
# launched (stubs) and answering. Then the frontend's agent belongs to
# another checkout: 78, and nothing launched.
SETE='
    RESTART_LIB_ONLY=1 . "$1"
    launchctl() { [ "$1" = print ] && [ -f "$2.print" ] && { cat "$2.print"; return 0; }; return 113; }
    edward_listeners() { :; }; pgrep() { return 1; }; sleep() { :; }
    curl() { return 7; }   # the summary asks the backend about its password: never a real one
    rotate_log() { :; }
    launch_backend()  { BACKEND_PID=$$; echo launched-backend; }
    launch_frontend() { FRONTEND_PID=$$; echo launched-frontend; }
    backend_answers() { :; }; frontend_answers() { :; }
    main "$2"
'
OUT="$(cd "$TMP" && /bin/bash -c "$SETE" _ "$REPO/restart.sh" all 2>&1)"; RC=$?
check "set -e, all by hand: exit 0" "$RC" "0"
contains "set -e, all by hand: the backend is launched first" "$(tr '\n' ' ' <<<"$OUT")" "launched-backend launched-frontend"
contains "set -e, all by hand: both UP" "$OUT" "Frontend is UP"
contains "set -e, all by hand: the summary" "$OUT" "=== Done ==="
mkdir -p "$TMP/gui/$(id -u)"
printf '\tworking directory = %s\n' "$TMP/other/frontend" > "$TMP/gui/$(id -u)/$FRONTEND_LABEL.print"
OUT="$(cd "$TMP" && /bin/bash -c "$SETE" _ "$REPO/restart.sh" all 2>&1)"; RC=$?
check "set -e, the frontend's agent another checkout's: exit 78" "$RC" "78"
lacks "set -e, refused: nothing launched" "$OUT" "launched-"
rm -rf "$TMP/gui"

echo "the launcher scripts:"
check "restart.sh's own help path still works" "$(/bin/bash "$REPO/restart.sh" nope 2>&1 | grep -c 'Usage:')" "1"

# ── Waiting ───────────────────────────────────────────────────────────
echo "wait_for:"
BACKEND_WAIT_S=3
(
    BACKEND_MODE=manual
    backend_answers() { return 1; }
    /bin/sleep 300 & BACKEND_PID=$!
    OUT="$(wait_for backend "$BACKEND_WAIT_S" "a hint" 2>&1; echo "rc=$?")"
    kill "$BACKEND_PID" 2>/dev/null
    echo "$OUT" > "$TMP/out"
)
OUT="$(cat "$TMP/out")"
contains "slow but running: says it is still starting" "$OUT" "still starting"
contains "slow but running: points at the log" "$OUT" "tail -f /tmp/edward-backend.log"
contains "slow but running: not a failure" "$OUT" "rc=0"
lacks "slow but running: never 'failed'" "$OUT" "failed"
(
    BACKEND_MODE=manual
    backend_answers() { return 1; }
    /usr/bin/true & BACKEND_PID=$!
    wait "$BACKEND_PID" 2>/dev/null
    wait_for backend "$BACKEND_WAIT_S" "a hint" > "$TMP/out" 2>&1
    echo "rc=$? state=$BACKEND_STATE" >> "$TMP/out"
)
OUT="$(cat "$TMP/out")"
contains "exited: a failure" "$OUT" "rc=1 state=failed"
contains "exited: says so" "$OUT" "exited before it answered"
(
    BACKEND_MODE=manual; BACKEND_PID=123
    backend_answers() { return 0; }
    wait_for backend "$BACKEND_WAIT_S" "a hint" > "$TMP/out" 2>&1
    echo "rc=$? state=$BACKEND_STATE" >> "$TMP/out"
)
OUT="$(cat "$TMP/out")"
contains "answers: up" "$OUT" "rc=0 state=up"
contains "answers: says UP" "$OUT" "Backend is UP on port 8000"
(
    BACKEND_MODE=launchd
    backend_answers() { return 1; }
    PRINT_PID=4242 print_for "$BACKEND_LABEL" "$REPO/backend" /bin/bash "$REPO/backend/start.sh"
    wait_for backend "$BACKEND_WAIT_S" "a hint" > "$TMP/out" 2>&1
    echo "rc=$? state=$BACKEND_STATE" >> "$TMP/out"
)
contains "launchd, its process running: still starting, exit 0" "$(cat "$TMP/out")" "rc=0 state=starting"
(
    BACKEND_MODE=launchd
    backend_answers() { return 1; }
    printf 'gui/%s/%s = {\n\tstate = waiting\n}\n' "$(id -u)" "$BACKEND_LABEL" > "$TMP/print.$BACKEND_LABEL"
    wait_for backend "$BACKEND_WAIT_S" "a hint" > "$TMP/out" 2>&1
    echo "rc=$? state=$BACKEND_STATE" >> "$TMP/out"
)
contains "launchd, no process at the end: a failure" "$(cat "$TMP/out")" "rc=1 state=failed"
check "the backend is given two minutes" "$(RESTART_LIB_ONLY=1 bash -c '. "$1"; echo $BACKEND_WAIT_S' _ "$REPO/restart.sh")" "120"

echo "the first password's setup code:"
STATUS_BODY=""
summary_with() {  # summary_with BACKEND_STATE FRONTEND_STATE STATUS_BODY -> summary's output
    (
        BACKEND_STATE="$1"; FRONTEND_STATE="$2"; STATUS_BODY="$3"
        curl() { [ -n "$STATUS_BODY" ] || return 7; printf '%s' "$STATUS_BODY"; }
        summary 2>&1
    )
}
UNSET='{"configured":false,"authenticated":false,"setup_code_required":true}'
SET='{"configured":true,"authenticated":false,"setup_code_required":false}'
contains "backend up, no password yet: says where the code is" "$(summary_with up up "$UNSET")" "$EDWARD_SETUP_CODE_NOTE"
lacks "backend up, a password set: nothing" "$(summary_with up up "$SET")" "setup code"
contains "backend still starting (no answer): says it" "$(summary_with starting up "")" "$EDWARD_SETUP_CODE_NOTE"
lacks "backend up but silent: nothing to go on" "$(summary_with up up "")" "setup code"
lacks "backend failed: no setup line on top of the failure" "$(summary_with failed up "$UNSET")" "setup code"
contains "frontend alone, no password yet: says it too" "$(summary_with "" up "$UNSET")" "$EDWARD_SETUP_CODE_NOTE"
check "the line is the shared one" "$EDWARD_SETUP_CODE_NOTE" \
    "First time? Your setup code is in backend/.setup_code (cat backend/.setup_code)"

echo "never touched for real:"
check "no launchctl verb but print was ever run" "$(cat "$LAUNCHCTL_LOG")" ""
check "nothing was launched, no log was moved" "$(cat "$LAUNCHED")" ""

echo
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
