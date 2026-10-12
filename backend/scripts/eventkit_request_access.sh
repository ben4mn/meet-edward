#!/bin/bash
# Ask macOS, once, to let Edward change the owner's calendar and Reminders
# (2026-10-11; docs/architecture/computer.md "Reminders and EventKit").
#
#   bash backend/scripts/eventkit_request_access.sh [events] [reminders] [--dry-run]
#
# Run it AT THE MAC itself (not over SSH): macOS shows a dialog for each ("python3.12 would
# like full access to your calendar / to access your reminders") — click
# Allow (Full Access). Nothing in Edward ever asks by itself: a dialog nobody
# answers hangs a turn for two minutes.
#
# Why launchd: macOS grants EventKit access to the *responsible* process. Run
# from Terminal, the helper's `request` would grant Terminal. The backend's
# python3.12 (a gui/$UID LaunchAgent) is what spawns the helper, so this asks
# through a one-shot launchd job under that same python, then removes the
# job. Measured 2026-10-11: Calendar was write-only and Reminders never asked
# for python3.12. When a dialog doesn't appear (an earlier "Don't Allow"),
# turn python3.12 on in System Settings → Privacy & Security → Calendars
# (Full Access) / Reminders instead.
#
# EDWARD_PYTHON (default this checkout's backend/.venv/bin/python — the one
# the backend runs under) and EDWARD_EVENTKIT (default
# ~/edward-eventkit/bin/edward-eventkit) override the paths;
# EDWARD_LAUNCHD_PREFIX (default com.meet-edward) names the one-shot job.
# --dry-run prints the job and runs nothing.
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
PY="${EDWARD_PYTHON:-$BACKEND/.venv/bin/python}"
HELPER="${EDWARD_EVENTKIT:-$HOME/edward-eventkit/bin/edward-eventkit}"
DRY=0
WHAT=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    events|reminders) WHAT+=("$arg") ;;
    *) echo "usage: $0 [events] [reminders] [--dry-run]" >&2; exit 2 ;;
  esac
done
[ ${#WHAT[@]} -eq 0 ] && WHAT=(events reminders)
. "$BACKEND/../scripts/lib/edward-env.sh"
PREFIX="$(edward_launchd_prefix)"
LABEL="${PREFIX%.}.eventkit-request"
DOMAIN="gui/$(id -u)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/request.py" <<'PYEOF'
import subprocess, sys
helper = sys.argv[1]
for what in sys.argv[2:]:
    r = subprocess.run([helper, "request", what], capture_output=True, timeout=600)
    print(what, r.returncode, r.stdout.decode().strip(), flush=True)
r = subprocess.run([helper, "access"], capture_output=True, timeout=30)
print("access", r.stdout.decode().strip(), flush=True)
PYEOF

ARGS=""
for w in "${WHAT[@]}"; do ARGS="$ARGS<string>$w</string>"; done
cat > "$TMP/job.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>$PY</string><string>-I</string><string>$TMP/request.py</string><string>$HELPER</string>$ARGS</array>
<key>RunAtLoad</key><true/>
<key>StandardOutPath</key><string>$TMP/out</string>
<key>StandardErrorPath</key><string>$TMP/err</string>
</dict></plist>
EOF

if [ "$DRY" = 1 ]; then
  cat "$TMP/job.plist"
  echo "launchctl bootstrap $DOMAIN $TMP/job.plist"
  echo "launchctl bootout $DOMAIN/$LABEL"
  exit 0
fi

[ -x "$HELPER" ] || { echo "no helper at $HELPER — run backend/scripts/build_eventkit.sh first" >&2; exit 1; }
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TMP/job.plist"
echo "Asking macOS now — answer the dialog(s) on this Mac's screen…"
for _ in $(seq 1 600); do
  grep -q '^access' "$TMP/out" 2>/dev/null && break
  sleep 1
done
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
cat "$TMP/out" 2>/dev/null || true
cat "$TMP/err" >&2 2>/dev/null || true
