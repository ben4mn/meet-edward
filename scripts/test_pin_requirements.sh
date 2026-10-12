#!/bin/bash
# Tests for scripts/pin_requirements.sh — the freeze filter decides what
# the constraints file pins, so it is the piece worth testing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }

PIN_REQUIREMENTS_LIB_ONLY=1 . "$HERE/pin_requirements.sh"

FREEZE='anthropic==0.40.0
httpx==0.27.2
-e git+https://github.com/example/thing.git@abc#egg=thing
# Editable Git install with no remote (edward==0.1)
graphiti-core @ file:///Users/me/src/graphiti
langgraph-checkpoint-postgres==2.0.21
psycopg==3.2.9
weird-package'

OUT="$(printf '%s\n' "$FREEZE" | filter_freeze 2>/dev/null)"
check "keeps exact pins, in order" "$OUT" "anthropic==0.40.0
httpx==0.27.2
langgraph-checkpoint-postgres==2.0.21
psycopg==3.2.9"

ERR="$(printf '%s\n' "$FREEZE" | filter_freeze 2>&1 >/dev/null)"
check "says why it dropped the editable" "$(grep -c 'skipped (editable)' <<<"$ERR")" "1"
check "says why it dropped the local path" "$(grep -c 'skipped (direct reference)' <<<"$ERR")" "1"
check "says why it dropped the unpinned" "$(grep -c 'skipped (unpinned)' <<<"$ERR")" "1"

check "no trailing newline on the last line still counts" \
    "$(printf 'fastapi==0.109.2' | filter_freeze 2>/dev/null)" "fastapi==0.109.2"
check "CRLF from a copied freeze is tolerated" \
    "$(printf 'starlette==0.36.3\r\n' | filter_freeze 2>/dev/null)" "starlette==0.36.3"

# psycopg-binary carries libpq on a fresh Mac (postgresql@16 is keg-only):
# a regenerated file must keep it, at psycopg's own version.
check "psycopg with its binary at the same version -> fine" \
    "$(printf 'psycopg==3.3.3\npsycopg-binary==3.3.3\n' | psycopg_pair_problem)" ""
check "no psycopg at all -> fine" "$(printf 'fastapi==0.109.2\n' | psycopg_pair_problem)" ""
case "$(printf 'psycopg==3.3.3\npsycopg-pool==3.3.0\n' | psycopg_pair_problem)" in
    *"without psycopg-binary"*) ok "psycopg without its binary -> refused" ;;
    *) nope "psycopg without its binary -> refused" ;;
esac
case "$(printf 'psycopg==3.3.6\npsycopg-binary==3.3.3\n' | psycopg_pair_problem)" in
    *"does not match"*) ok "a mismatched pair -> refused" ;;
    *) nope "a mismatched pair -> refused" ;;
esac
check "the committed constraints.txt passes" \
    "$(psycopg_pair_problem < "$HERE/../backend/constraints.txt")" ""

# The script must refuse to run without a venv rather than write an empty file.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
EDWARD_VENV_PYTHON="$TMP/nope" bash "$HERE/pin_requirements.sh" "$TMP/c.txt" >/dev/null 2>&1
check "no venv -> exit 1" "$?" "1"
check "no venv -> no file" "$([ -e "$TMP/c.txt" ] && echo yes || echo no)" "no"

echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
