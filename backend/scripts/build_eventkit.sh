#!/bin/bash
# Build Edward's EventKit helper (2026-10-11; docs/architecture/computer.md).
#
#   bash backend/scripts/build_eventkit.sh [output path]
#
# Default output: ~/edward-eventkit/bin/edward-eventkit (what
# services/eventkit_service.py looks for; EDWARD_EVENTKIT overrides it, read
# per call). Uses only the Xcode command-line tools' swiftc and codesign: no
# Homebrew, nothing else installed (a brew install can move python@3.12 off
# its Full Disk Access path). Embeds Info.plist — EventKit refuses a process
# whose main bundle lacks the usage strings — signs ad hoc, builds to a temp
# file and runs the self-test (which never touches the owner's calendar or
# Reminders) before moving it into place, so a failed build never replaces a
# working helper. The deploy runs it once, like build_mediatool.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/tools/eventkit/edward_eventkit.swift"
PLIST="$HERE/tools/eventkit/Info.plist"
OUT="${1:-${EDWARD_EVENTKIT:-$HOME/edward-eventkit/bin/edward-eventkit}}"
mkdir -p "$(dirname "$OUT")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
swiftc -O -swift-version 5 -parse-as-library -target "$(uname -m)-apple-macos14.0" -framework EventKit \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PLIST" \
  -o "$TMP/edward-eventkit" "$SRC"
codesign --force --sign - --identifier com.meet-edward.eventkit "$TMP/edward-eventkit"
"$TMP/edward-eventkit" selftest | grep -q '"ok":true' || { echo "self-test failed" >&2; exit 1; }
mv -f "$TMP/edward-eventkit" "$OUT"
echo "built $OUT"
