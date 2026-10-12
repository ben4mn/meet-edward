#!/bin/bash
# Build Edward's Swift media helper (2026-10-11; docs/architecture/media.md).
#
#   bash backend/scripts/build_mediatool.sh [output path]
#
# Default output: ~/edward-media/bin/edward-mediatool (what services/media/mediatool.py
# looks for; EDWARD_MEDIATOOL overrides it). Uses only the Xcode command-line
# tools' swiftc: no Homebrew, nothing else installed. Builds to a temp file,
# runs the self-test, then moves it into place, so a failed build never
# replaces a working helper.
#
# swiftc targets the host's architecture (arm64 on Apple silicon, where this
# was built and tested); on an Intel Mac it builds x86_64 the same way. The
# source path is relative to this checkout, so any clone builds it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/tools/media/edward_mediatool.swift"
OUT="${1:-$HOME/edward-media/bin/edward-mediatool}"
mkdir -p "$(dirname "$OUT")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
swiftc -O -swift-version 5 -parse-as-library -o "$TMP/edward-mediatool" "$SRC"
"$TMP/edward-mediatool" selftest "$TMP/selftest" | grep -q '"ok":true' || { echo "self-test failed" >&2; exit 1; }
mv -f "$TMP/edward-mediatool" "$OUT"
echo "built $OUT"
