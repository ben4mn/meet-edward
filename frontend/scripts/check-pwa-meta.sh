#!/usr/bin/env bash
# Assert PWA meta-tag invariants on a served URL.
#
# Usage:
#   ./check-pwa-meta.sh [URL]     # default http://localhost:3000/
#
# Exits non-zero if any invariant fails. Designed for R/G-TDD — expect it
# to fail before the layout.tsx meta cleanup lands, and pass after.

set -u
URL="${1:-http://localhost:3000/}"
HTML="$(curl -sSL --max-time 10 "$URL")" || {
  echo "FAIL  could not fetch $URL" >&2
  exit 2
}

fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*" >&2; fails=$((fails+1)); }

# 1. viewport-fit=cover present (needed for notched iPhone PWAs).
if echo "$HTML" | grep -qE 'name="viewport"[^>]*viewport-fit=cover'; then
  pass "viewport-fit=cover present"
else
  fail "viewport-fit=cover missing in viewport meta"
fi

# Count matches (not lines — Next ships one-line minified HTML).
count_meta() {
  # shellcheck disable=SC2086
  echo "$HTML" | grep -oE "$1" | wc -l | tr -d ' '
}

# 2. Exactly one <meta name="viewport"> tag.
vp_count=$(count_meta '<meta[^>]*name="viewport"')
if [ "$vp_count" = "1" ]; then
  pass "single viewport meta tag"
else
  fail "expected 1 viewport meta, got $vp_count"
fi

# 3. Exactly one <meta name="apple-mobile-web-app-capable">.
apc_count=$(count_meta '<meta[^>]*name="apple-mobile-web-app-capable"')
if [ "$apc_count" = "1" ]; then
  pass "single apple-mobile-web-app-capable meta"
else
  fail "expected 1 apple-mobile-web-app-capable meta, got $apc_count"
fi

# 4. Exactly one <meta name="apple-mobile-web-app-title">.
apt_count=$(count_meta '<meta[^>]*name="apple-mobile-web-app-title"')
if [ "$apt_count" = "1" ]; then
  pass "single apple-mobile-web-app-title meta"
else
  fail "expected 1 apple-mobile-web-app-title meta, got $apt_count"
fi

# 4b. Exactly one <meta name="apple-mobile-web-app-status-bar-style">.
apsb_count=$(count_meta '<meta[^>]*name="apple-mobile-web-app-status-bar-style"')
if [ "$apsb_count" = "1" ]; then
  pass "single apple-mobile-web-app-status-bar-style meta"
else
  fail "expected 1 apple-mobile-web-app-status-bar-style meta, got $apsb_count"
fi

# 5. og:image / twitter:image should NOT point at localhost on production
#    builds. For localhost dev servers this test is skipped.
case "$URL" in
  *localhost*|*127.0.0.1*)
    echo "SKIP  og:image origin check (dev URL)"
    ;;
  *)
    if echo "$HTML" | grep -E 'property="og:image"|name="twitter:image"' | grep -q localhost; then
      fail "og:image or twitter:image still points to localhost"
    else
      pass "og:image + twitter:image use production origin"
    fi
    ;;
esac

echo
if [ "$fails" -gt 0 ]; then
  echo "FAILED $fails check(s)" >&2
  exit 1
fi
echo "All checks passed"
