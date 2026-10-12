#!/bin/bash
# Tests for install-launchagents.sh: what it renders, and that --dry-run
# changes nothing. Nothing here loads an agent or calls launchctl bootstrap.
#
# Run: bash scripts/test_install_launchagents.sh

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }
contains() { case "$2" in *"$3"*) ok "$1";; *) nope "$1 (no '$3' in output)";; esac; }
lacks() { case "$2" in *"$3"*) nope "$1 ('$3' in output)";; *) ok "$1";; esac; }

TMP="$(mktemp -d)"
SLEEPERS=""
cleanup() {
    # shellcheck disable=SC2086
    [ -n "$SLEEPERS" ] && kill $SLEEPERS >/dev/null 2>&1
    rm -rf "$TMP"
}
trap cleanup EXIT
export EDWARD_ENV_FILE="$TMP/no-such.env"
export EDWARD_LAUNCHAGENTS_DIR="$TMP/agents"
unset EDWARD_LAUNCHD_PREFIX EDWARD_PG_BIN

# A launchctl that answers `print` with "not loaded" and records every other
# verb instead of running it: the script runs end to end below (--dry-run)
# with this first on PATH, and must never get as far as bootstrap.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/launchctl" <<STUB
#!/bin/bash
[ "\$1" = print ] && exit 113
echo "launchctl \$*" >> "$TMP/launchctl.log"
exit 1
STUB
chmod +x "$TMP/bin/launchctl"
: > "$TMP/launchctl.log"
STUB_PATH="$TMP/bin:$PATH"

# plist_value FILE KEY -> the value plutil reads for KEY (macOS only).
plist_value() { local v; v="$(plutil -extract "$2" raw -o - "$1" 2>/dev/null)" || return 1; printf '%s' "$v"; }

echo "render:"
for svc in backend frontend graph-worker healthcheck healthcheck-deep backup memory-report; do
    OUT="$(INSTALL_AGENTS_LIB_ONLY=1 bash -c 'f="$1" svc="$2"; . "$f"; render "$svc"' _ "$HERE/install-launchagents.sh" "$svc")"
    printf '%s\n' "$OUT" > "$TMP/$svc.plist"
    contains "$svc: label has the default prefix" "$OUT" "<string>com.meet-edward.$svc</string>"
    lacks "$svc: no private path" "$OUT" "/Users/me"
    if command -v plutil >/dev/null 2>&1; then
        plutil -lint "$TMP/$svc.plist" >/dev/null; check "$svc: lints" "$?" "0"
        check "$svc: label key" "$(plist_value "$TMP/$svc.plist" Label)" "com.meet-edward.$svc"
    fi
done

if command -v plutil >/dev/null 2>&1; then
    check "backend runs this checkout's start.sh" \
        "$(plist_value "$TMP/backend.plist" ProgramArguments.1)" "$REPO/backend/start.sh"
    check "backend is kept alive" "$(plist_value "$TMP/backend.plist" KeepAlive)" "true"
    check "backend throttles respawns to 10 s" "$(plist_value "$TMP/backend.plist" ThrottleInterval)" "10"
    check "backend logs to /tmp/edward-backend.log" \
        "$(plist_value "$TMP/backend.plist" StandardOutPath)" "/tmp/edward-backend.log"
    check "frontend runs run-launchd.sh" \
        "$(plist_value "$TMP/frontend.plist" ProgramArguments.1)" "$REPO/frontend/run-launchd.sh"
    check "frontend writes its own log (no StandardOutPath)" \
        "$(plist_value "$TMP/frontend.plist" StandardOutPath || echo none)" "none"
    check "graph worker writes its own log" \
        "$(plist_value "$TMP/graph-worker.plist" StandardOutPath || echo none)" "none"
    check "healthcheck every 5 min" "$(plist_value "$TMP/healthcheck.plist" StartInterval)" "300"
    check "deep check at 08:00" \
        "$(plist_value "$TMP/healthcheck-deep.plist" StartCalendarInterval.Hour):$(plist_value "$TMP/healthcheck-deep.plist" StartCalendarInterval.Minute)" "8:0"
    check "deep check passes --deep" "$(plist_value "$TMP/healthcheck-deep.plist" ProgramArguments.2)" "--deep"
    check "backup at 03:30" \
        "$(plist_value "$TMP/backup.plist" StartCalendarInterval.Hour):$(plist_value "$TMP/backup.plist" StartCalendarInterval.Minute)" "3:30"
    check "memory report at 09:05" \
        "$(plist_value "$TMP/memory-report.plist" StartCalendarInterval.Hour):$(plist_value "$TMP/memory-report.plist" StartCalendarInterval.Minute)" "9:5"
    check "memory report pushes its ledger row" "$(plist_value "$TMP/memory-report.plist" ProgramArguments.2)" "--push"
    check "agents know their prefix" \
        "$(plist_value "$TMP/backend.plist" EnvironmentVariables.EDWARD_LAUNCHD_PREFIX)" "com.meet-edward"
    contains "agents' PATH has Homebrew's bin" "$(plist_value "$TMP/backend.plist" EnvironmentVariables.PATH)" "/bin:"
fi

echo "the agents' PATH:"
OUT="$(HOMEBREW_PREFIX=/opt/fakebrew INSTALL_AGENTS_LIB_ONLY=1 bash -c '. "$1"; render backend' _ "$HERE/install-launchagents.sh")"
# The system's own directories first (review 2026-10-12: these jobs run
# outside Edward's sandbox, and his shell can write the home and Homebrew's
# bin), then postgresql@16's bin (the keg is not linked), then Homebrew's.
contains "the system's directories, then postgresql@16's bin, then Homebrew's" "$OUT" \
    "<string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/fakebrew/opt/postgresql@16/bin:/opt/fakebrew/bin:/opt/fakebrew/sbin:"
lacks "never ~/.local/bin or ~/bin" "$OUT" ".local/bin"
contains "the jobs know Homebrew's prefix without running brew" "$OUT" "<key>HOMEBREW_PREFIX</key>"
printf 'EDWARD_PG_BIN=~/pg16/bin\n' > "$TMP/pg.env"
OUT="$(EDWARD_ENV_FILE="$TMP/pg.env" HOMEBREW_PREFIX=/opt/fakebrew INSTALL_AGENTS_LIB_ONLY=1 bash -c '. "$1"; render frontend' _ "$HERE/install-launchagents.sh")"
contains "EDWARD_PG_BIN from .env takes postgresql@16's place" "$OUT" "<string>/usr/bin:/bin:/usr/sbin:/sbin:$HOME/pg16/bin:/opt/fakebrew/bin:"

echo "stop_hand_started:"
# A `sleep` whose working directory is a scratch checkout's backend/, and one
# from another directory; the listeners on the port are stubbed to these two
# and pgrep to nothing, so nothing else on this Mac is ever looked at.
mkdir -p "$TMP/repo/backend" "$TMP/repo/frontend" "$TMP/other/backend"
(cd "$TMP/repo/backend" && exec /bin/sleep 300) & OURS=$!
(cd "$TMP/other/backend" && exec /bin/sleep 300) & THEIRS=$!
SLEEPERS="$OURS $THEIRS"
disown $SLEEPERS 2>/dev/null   # the test kills one: no "Terminated" job notice
hand_started() {  # hand_started DRY_RUN JOB_PID -> output, then the live sleepers
    local dry="$1"
    JOB="$2"   # sourcing the installer clears this function's arguments
    INSTALL_AGENTS_LIB_ONLY=1 . "$HERE/install-launchagents.sh"
    # The installer's own `set -euo pipefail` stays on: stop_hand_started
    # must survive it, as it runs in the real script.
    REPO="$TMP/repo"; DRY_RUN="$dry"
    launchctl() { [ "$1" = print ] || return 1; [ -n "$JOB" ] || return 113; printf '\tpid = %s\n' "$JOB"; }
    pgrep() { return 1; }
    edward_listeners() { local p; for p in $OURS $THEIRS; do kill -0 "$p" 2>/dev/null && echo "$p"; done; }
    stop_hand_started backend
    echo "alive:$(for p in $OURS $THEIRS; do kill -0 "$p" 2>/dev/null && printf ' %s' "$p"; done)"
}
OUT="$(hand_started true "")"
contains "dry run: says it would stop this checkout's backend" "$OUT" "would stop this checkout's backend, started by hand (PID $OURS)"
contains "dry run: stops nothing" "$OUT" "alive: $OURS $THEIRS"
OUT="$(hand_started false "$OURS")"
contains "launchd's own process is left to launchd" "$OUT" "alive: $OURS $THEIRS"
lacks "... and not called hand-started" "$OUT" "stopping this checkout's"
OUT="$(hand_started false "")"
contains "stops this checkout's backend started by hand" "$OUT" "stopping this checkout's backend, started by hand (PID $OURS)"
contains "... and only that one" "$OUT" "alive: $THEIRS"
lacks "... gone" "$OUT" "alive: $OURS"
contains "names the other checkout's process on the port instead" "$OUT" "port 8000 is held by PID $THEIRS, which does not run from this checkout"
lacks "the old, wrong hint is gone" "$OUT" "./restart.sh backend stops it"

echo "install_one stops a hand-started copy before bootstrap:"
ORDER="$(sed -n '/^install_one()/,/^}/p' "$HERE/install-launchagents.sh" | sed -n '/mkdir -p "\$DEST"/,$p' \
    | grep -o -e '^ *unload "\$label"' -e 'stop_hand_started "\$svc"' -e 'launchctl bootstrap' | sed 's/^ *//' | tr '\n' '|')"
check "unload, then stop the hand-started copy, then bootstrap" "$ORDER" 'unload "$label"|stop_hand_started "$svc"|launchctl bootstrap|'

echo "the backup's interpreter:"
OUT="$(EDWARD_FDA_PYTHON=/opt/somewhere/python3.12 INSTALL_AGENTS_LIB_ONLY=1 bash -c 'f="$1"; . "$f"; render backup' _ "$HERE/install-launchagents.sh")"
contains "EDWARD_FDA_PYTHON runs run_backup_with_fda.py" "$OUT" "<string>/opt/somewhere/python3.12</string>"
contains "-> the FDA entry point" "$OUT" "backend/scripts/run_backup_with_fda.py"

echo "prefix and escaping:"
OUT="$(EDWARD_LAUNCHD_PREFIX='org.example.' INSTALL_AGENTS_LIB_ONLY=1 bash -c 'f="$1"; . "$f"; render backend' _ "$HERE/install-launchagents.sh")"
contains "EDWARD_LAUNCHD_PREFIX, trailing dot dropped" "$OUT" "<string>org.example.backend</string>"
printf 'EDWARD_LAUNCHD_PREFIX=org.dotenv   # from .env\n' > "$TMP/prefix.env"
OUT="$(EDWARD_ENV_FILE="$TMP/prefix.env" INSTALL_AGENTS_LIB_ONLY=1 bash -c 'f="$1"; . "$f"; render frontend' _ "$HERE/install-launchagents.sh")"
contains "the prefix from .env" "$OUT" "<string>org.dotenv.frontend</string>"
OUT="$(EDWARD_LAUNCHD_PREFIX='a&b' INSTALL_AGENTS_LIB_ONLY=1 bash -c 'f="$1"; . "$f"; render backend' _ "$HERE/install-launchagents.sh")"
contains "XML-escaped" "$OUT" "<string>a&amp;b.backend</string>"

echo "next steps and the first password's setup code:"
next_steps_with() {  # next_steps_with STATUS_BODY -> what a real install prints at the end
    STATUS_BODY="$1" INSTALL_AGENTS_LIB_ONLY=1 bash -c '
        . "$1"
        curl() { [ -n "$STATUS_BODY" ] || return 7; printf "%s" "$STATUS_BODY"; }
        next_steps /opt/py/python3.12
    ' _ "$HERE/install-launchagents.sh"
}
NOTE="First time? Your setup code is in backend/.setup_code (cat backend/.setup_code)"
OUT="$(next_steps_with '{"configured":false,"authenticated":false}')"
contains "no password yet: says where the code is" "$OUT" "$NOTE"
contains "... after the line about opening the app" "$OUT" "tail -f /tmp/edward-backend.log /tmp/edward-frontend.log
$NOTE
Logs:"
contains "... and the rest is there" "$OUT" "Full Disk Access for /opt/py/python3.12"
contains "the backend not answering yet (the first start): says it" "$(next_steps_with "")" "$NOTE"
OUT="$(next_steps_with '{"configured":true,"authenticated":false}')"
lacks "a password set: no setup-code line" "$OUT" "setup code"
contains "... the rest is unchanged" "$OUT" "Stop them:"
check "the end of a real install is next_steps, after the dry run's exit" \
    "$(grep -n -e '^\$DRY_RUN && exit 0' -e '^next_steps ' "$HERE/install-launchagents.sh" | cut -d: -f2 | tr '\n' '|')" \
    '$DRY_RUN && exit 0|next_steps "$(fda_python)"|'

echo "--dry-run:"
OUT="$(PATH="$STUB_PATH" bash "$HERE/install-launchagents.sh" --dry-run --with-graph 2>&1)"; RC=$?
check "dry run succeeds" "$RC" "0"
contains "dry run prints the backend plist" "$OUT" "would write $TMP/agents/com.meet-edward.backend.plist"
contains "dry run includes the graph worker when asked" "$OUT" "com.meet-edward.graph-worker"
check "dry run writes nothing" "$(ls -A "$TMP/agents" 2>/dev/null | wc -l | tr -d ' ')" "0"
OUT="$(PATH="$STUB_PATH" bash "$HERE/install-launchagents.sh" --dry-run --without-backup 2>&1)"
lacks "--without-backup leaves the backup out" "$OUT" "com.meet-edward.backup.plist"
lacks "no graph worker unless asked" "$OUT" "graph-worker.plist and"
PATH="$STUB_PATH" bash "$HERE/install-launchagents.sh" --nope >/dev/null 2>&1; check "unknown option -> usage error" "$?" "64"
check "a dry run never runs launchctl beyond print" "$(cat "$TMP/launchctl.log")" ""

echo
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
