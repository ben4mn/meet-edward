#!/bin/bash
# Tests for ensure-build.sh's one decision: does the production bundle
# need rebuilding before `next start`?
#
# Run: bash frontend/test_ensure_build.sh

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }

ENSURE_BUILD_LIB_ONLY=1 . "$HERE/ensure-build.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/frontend"
mkdir -p "$D/app" "$D/components" "$D/node_modules/x" "$D/.next"
echo "x" > "$D/app/page.tsx"
echo "{}" > "$D/package.json"

echo "needs_build:"

needs_build "$D"; check "no BUILD_ID -> build" "$?" "0"

sleep 1; echo "abc" > "$D/.next/BUILD_ID"
needs_build "$D"; check "build newer than every source -> skip" "$?" "1"

sleep 1; echo "y" > "$D/app/page.tsx"
needs_build "$D"; check "edited source -> build" "$?" "0"

sleep 1; echo "def" > "$D/.next/BUILD_ID"
sleep 1; echo "z" > "$D/node_modules/x/index.js"
needs_build "$D"; check "node_modules churn is ignored" "$?" "1"

sleep 1; touch "$D/.next/cache-junk"
needs_build "$D"; check ".next itself is ignored" "$?" "1"

sleep 1; echo "{\"a\":1}" > "$D/package.json"
needs_build "$D"; check "package.json change -> build" "$?" "0"

sleep 1; echo "ghi" > "$D/.next/BUILD_ID"
sleep 1; echo "c" > "$D/components/New.tsx"
needs_build "$D"; check "new component file -> build" "$?" "0"

echo "ensure_build + the size report:"
# 2026-09-27: after a successful build the first-load report runs;
# a route over budget is a WARN line, never a failed build (a failed build
# puts production on `next dev`).
STUBS="$TMP/stubs"; mkdir -p "$STUBS"
cat > "$STUBS/npm" <<'EOF'
#!/bin/bash
# A stand-in for `npm run build`: writes a build whose /today is ~200 kB gzipped.
[ "${STUB_NPM_FAIL:-}" = 1 ] && { echo "stub build failed"; exit 1; }
dist="${NEXT_DIST_DIR:-.next}"
mkdir -p "$dist/static/chunks"
head -c 400000 /dev/urandom | base64 > "$dist/static/chunks/today.js"
printf '{"pages":{"/(app)/today/page":["static/chunks/today.js"]}}' > "$dist/app-build-manifest.json"
echo "stub-$(date +%s)" > "$dist/BUILD_ID"
echo "stub build ok"
EOF
chmod +x "$STUBS/npm"
B="$TMP/buildme"; mkdir -p "$B/app"; echo x > "$B/app/page.tsx"
OUT="$(PATH="$STUBS:$PATH" ensure_build "$B" 2>&1)"; RC=$?
check "a build over budget still succeeds" "$RC" "0"
check "the report warns about /today" "$(grep -c '\[bundle-budget\] WARN /today' <<<"$OUT")" "1"
OUT="$(PATH="$STUBS:$PATH" ensure_build "$B" 2>&1)"; RC=$?
check "a current bundle is not re-measured" "$(grep -c 'bundle-budget' <<<"$OUT")" "0"
sleep 1; echo y > "$B/app/page.tsx"
OUT="$(PATH="$STUBS:$PATH" BUNDLE_BUDGET_SCRIPT="$TMP/no-such-script.js" ensure_build "$B" 2>&1)"; RC=$?
check "a report that crashes never fails the build" "$RC" "0"
check "a crashed report says it was ignored" "$(grep -c 'report failed (ignored)' <<<"$OUT")" "1"
sleep 1; echo z > "$B/app/page.tsx"
OUT="$(PATH="$STUBS:$PATH" STUB_NPM_FAIL=1 ensure_build "$B" 2>&1)"; RC=$?
check "a failed build still fails" "$RC" "1"
check "no report after a failed build" "$(grep -c 'bundle-budget' <<<"$OUT")" "0"

echo "staged builds (build before the swap):"
# 2026-09-27: run-launchd.sh built after the old server was killed, so
# every deploy was ~1 min of 502s. deploy-build.sh now builds into
# .next-staging while the old server keeps serving; on start ensure-build.sh
# swaps a complete, newer staged build into .next (the old one kept as
# .next.prev) instead of building in place.

# make_build DIR ID -> a fake build with everything staged_build_ready checks.
make_build() {
    local d="$1" id="$2"
    mkdir -p "$d/server" "$d/static/chunks" "$d/cache/webpack"
    for f in build-manifest.json app-build-manifest.json prerender-manifest.json routes-manifest.json required-server-files.json; do
        echo '{}' > "$d/$f"
    done
    echo "junk" > "$d/cache/webpack/pack"
    echo "$id" > "$d/BUILD_ID"
}
stage() {  # stage DIR ID -> a complete staged build, as deploy-build.sh leaves it
    make_build "$1/.next-staging" "$2"
    printf 'build_id=%s\nstaged_at=%s\n' "$2" "$(date +%s)" > "$1/.next-staging/EDWARD_STAGED"
}

S1="$TMP/swap1"; mkdir -p "$S1/app"; echo x > "$S1/app/page.tsx"
make_build "$S1/.next" OLD
swap_staged_build "$S1" >/dev/null; check "no staged build -> nothing swapped" "$?" "1"
check "no staged build -> .next untouched" "$(cat "$S1/.next/BUILD_ID")" "OLD"

sleep 1; make_build "$S1/.next-staging" HALF   # a deploy build still running
swap_staged_build "$S1" >/dev/null; check "staged build without its marker -> not swapped" "$?" "1"
check "incomplete staged build -> .next untouched" "$(cat "$S1/.next/BUILD_ID")" "OLD"
check "incomplete staged build -> left alone (it may still be building)" "$(cat "$S1/.next-staging/BUILD_ID")" "HALF"
printf 'build_id=OTHER\n' > "$S1/.next-staging/EDWARD_STAGED"
staged_build_ready "$S1"; check "marker naming another build -> not ready" "$?" "1"
rm -rf "$S1/.next-staging"

sleep 1; stage "$S1" NEW
rm "$S1/.next-staging/routes-manifest.json"
staged_build_ready "$S1"; check "staged build missing a manifest -> not ready" "$?" "1"
echo '{}' > "$S1/.next-staging/routes-manifest.json"
staged_build_ready "$S1"; check "complete staged build -> ready" "$?" "0"

needs_build "$S1"; check "a staged build is not a source change" "$?" "1"
OUT="$(swap_staged_build "$S1")"; RC=$?
check "complete, newer staged build -> swapped" "$RC" "0"
check "swap -> .next is the staged build" "$(cat "$S1/.next/BUILD_ID")" "NEW"
check "swap -> the old build is kept as .next.prev" "$(cat "$S1/.next.prev/BUILD_ID")" "OLD"
check "swap -> .next-staging is gone" "$([ -e "$S1/.next-staging" ] && echo there || echo gone)" "gone"
check "swap -> .next.prev keeps no webpack cache" "$([ -e "$S1/.next.prev/cache" ] && echo there || echo gone)" "gone"
check "swap -> says what it swapped in" "$(grep -c 'NEW' <<<"$OUT")" "1"
needs_build "$S1"; check "after the swap the bundle is current" "$?" "1"

sleep 1; stage "$S1" NEWER
swap_staged_build "$S1" >/dev/null
check "a second swap keeps only the build it replaced" "$(cat "$S1/.next.prev/BUILD_ID")" "NEW"

stage "$S1" STALE
sleep 1; echo "INPLACE" > "$S1/.next/BUILD_ID"   # someone built in place after staging
swap_staged_build "$S1" >/dev/null; check "staged build older than .next -> discarded (rc 2)" "$?" "2"
check "stale staged build -> .next untouched" "$(cat "$S1/.next/BUILD_ID")" "INPLACE"
check "stale staged build -> removed" "$([ -e "$S1/.next-staging" ] && echo there || echo gone)" "gone"

S2="$TMP/swap2"; mkdir -p "$S2"
stage "$S2" FIRST
swap_staged_build "$S2" >/dev/null; check "no .next yet -> the staged build is promoted" "$(cat "$S2/.next/BUILD_ID")" "FIRST"
check "no .next yet -> no .next.prev invented" "$([ -e "$S2/.next.prev" ] && echo there || echo none)" "none"

# Killed between the two renames: .next is gone, .next.prev and a complete
# staged build remain. The next start promotes it and keeps .next.prev.
S3="$TMP/swap3"; mkdir -p "$S3"
make_build "$S3/.next.prev" PREV
sleep 1; stage "$S3" STAGED
swap_staged_build "$S3" >/dev/null
check "half-done swap -> staged build promoted" "$(cat "$S3/.next/BUILD_ID")" "STAGED"
check "half-done swap -> .next.prev kept" "$(cat "$S3/.next.prev/BUILD_ID")" "PREV"

echo "ensure_build with a staged build:"
S4="$TMP/swap4"; mkdir -p "$S4/app"; echo x > "$S4/app/page.tsx"
sleep 1; make_build "$S4/.next" OLD
sleep 1; stage "$S4" NEW
NPM_CALLS="$TMP/npm-calls"; : > "$NPM_CALLS"
cat > "$STUBS/npm-recording" <<EOF
#!/bin/bash
echo "npm \$*" >> "$NPM_CALLS"
exec "$STUBS/npm" "\$@"
EOF
chmod +x "$STUBS/npm-recording"
REC="$TMP/rec"; mkdir -p "$REC"; ln -sf "$STUBS/npm-recording" "$REC/npm"
OUT="$(PATH="$REC:$PATH" ensure_build "$S4" 2>&1)"; RC=$?
check "start with a staged build -> ok" "$RC" "0"
check "start with a staged build -> serves it" "$(cat "$S4/.next/BUILD_ID")" "NEW"
check "start with a staged build -> no in-place build" "$(wc -l < "$NPM_CALLS" | tr -d ' ')" "0"

echo "deploy-build.sh:"
cat > "$STUBS/npm-next" <<'EOF'
#!/bin/bash
# `npm run build` as next build 14.1 behaves, in the order that matters:
#  1. an in-place build deletes its distDir first (build/index.js:690); a
#     staged one writes only .next-staging, which deploy-build.sh cleared
#  2. the client compile: next-pwa (dest 'public', whatever the distDir)
#     deletes public/sw.js, workbox-*.js and their maps and writes this
#     build's -- before type checking or lint can fail the build
#  3. route types for each app/<route>/page.tsx into <distDir>/types
#  4. tsconfig.json gains "<distDir>/types/**/*.ts" when that include is
#     missing (writeConfigurationDefaults, which never removes one)
#  5. the type check over every file tsconfig.json includes: a route type
#     whose page is gone fails with TS2307 (runTypeCheck has no filter);
#     STUB_NPM_FAIL=1 fails here too, as a lint error does
#  6. the manifests, then BUILD_ID last
dist="${NEXT_DIST_DIR:-.next}"
[ "$dist" = .next ] && rm -rf .next
mkdir -p public
rm -f public/sw.js public/sw.js.map public/workbox-*.js public/workbox-*.js.map
echo "sw precaching /_next/static/build-$$/_buildManifest.js" > public/sw.js
echo "workbox runtime $$" > "public/workbox-$$.js"
for page in app/*/page.tsx; do
    [ -f "$page" ] || continue
    r="$(basename "$(dirname "$page")")"
    mkdir -p "$dist/types/app/$r"
    echo "import * as entry from '../../../../app/$r/page.js'" > "$dist/types/app/$r/page.ts"
done
if [ -f tsconfig.json ] && ! grep -qF "\"$dist/types/**/*.ts\"" tsconfig.json; then
    sed "s|\\(\"include\": \\[.*\\)\\]|\\1, \"$dist/types/**/*.ts\"]|" tsconfig.json > tsconfig.json.tmp
    mv tsconfig.json.tmp tsconfig.json
fi
if [ -f tsconfig.json ]; then
    for inc in $(grep -oE '"[^"]+/types/\*\*/\*\.ts"' tsconfig.json | tr -d '"'); do
        for f in $(find "${inc%/\*\*/\*.ts}" -name '*.ts' 2>/dev/null); do
            rel="$(sed -n "s/.* from '\(.*\)\.js'.*/\1/p" "$f")"
            [ -n "$rel" ] || continue
            if [ ! -f "$(dirname "$f")/$rel.tsx" ] && [ ! -f "$(dirname "$f")/$rel.ts" ]; then
                echo "$f Type error: Cannot find module '$rel.js'"; exit 1
            fi
        done
    done
fi
[ "${STUB_NPM_FAIL:-}" = 1 ] && { echo "stub build failed"; exit 1; }
mkdir -p "$dist/server" "$dist/static/chunks" "$dist/cache"
for f in build-manifest.json prerender-manifest.json routes-manifest.json required-server-files.json; do echo '{}' > "$dist/$f"; done
echo 'console.log(1)' > "$dist/static/chunks/today.js"
printf '{"pages":{"/(app)/today/page":["static/chunks/today.js"]}}' > "$dist/app-build-manifest.json"
sleep 1
echo "built-$$" > "$dist/BUILD_ID"
EOF
chmod +x "$STUBS/npm-next"
NX="$TMP/nx"; mkdir -p "$NX"; ln -sf "$STUBS/npm-next" "$NX/npm"
D5="$TMP/deploy"; mkdir -p "$D5/app"; echo x > "$D5/app/page.tsx"
echo '{"include": ["next-env.d.ts"]}' > "$D5/tsconfig.json"
touch -t 202609010000 "$D5/tsconfig.json" "$D5/app/page.tsx"
TS_BEFORE="$(cat "$D5/tsconfig.json")"; TS_MTIME="$(stat -f %m "$D5/tsconfig.json" 2>/dev/null || stat -c %Y "$D5/tsconfig.json")"
make_build "$D5/.next" SERVING
OUT="$(PATH="$NX:$PATH" bash "$HERE/deploy-build.sh" "$D5" 2>&1)"; RC=$?
check "deploy build -> ok" "$RC" "0"
check "deploy build -> the served .next is untouched" "$(cat "$D5/.next/BUILD_ID")" "SERVING"
staged_build_ready "$D5"; check "deploy build -> a complete staged build" "$?" "0"
check "deploy build -> tsconfig.json put back" "$(cat "$D5/tsconfig.json")" "$TS_BEFORE"
check "deploy build -> tsconfig.json keeps its mtime" \
    "$(stat -f %m "$D5/tsconfig.json" 2>/dev/null || stat -c %Y "$D5/tsconfig.json")" "$TS_MTIME"
check "deploy build -> runs the size report" "$(grep -c '\[bundle-budget\] /today' <<<"$OUT")" "1"
check "deploy build -> says how to swap it in" "$(grep -c 'kickstart' <<<"$OUT")" "1"
check "deploy build -> leaves no lock" "$([ -e "$D5/.next-staging.lock" ] && echo there || echo gone)" "gone"
OUT="$(PATH="$NX:$PATH" ensure_build "$D5" 2>&1)"; RC=$?
check "then start -> swapped, no rebuild" "$RC:$(grep -c 'running next build' <<<"$OUT")" "0:0"
check "then start -> serves the staged build" "$(grep -c '^built-' "$D5/.next/BUILD_ID")" "1"
check "then start -> the served build is .next.prev" "$(cat "$D5/.next.prev/BUILD_ID")" "SERVING"

OUT="$(PATH="$NX:$PATH" STUB_NPM_FAIL=1 bash "$HERE/deploy-build.sh" "$D5" 2>&1)"; RC=$?
check "failed deploy build -> rc 1" "$RC" "1"
check "failed deploy build -> no staged build left" "$([ -e "$D5/.next-staging" ] && echo there || echo gone)" "gone"
check "failed deploy build -> the served .next untouched" "$(grep -c '^built-' "$D5/.next/BUILD_ID")" "1"

mkdir "$D5/.next-staging.lock"
OUT="$(PATH="$NX:$PATH" bash "$HERE/deploy-build.sh" "$D5" 2>&1)"; RC=$?
check "a deploy build already running -> refused" "$RC" "1"
check "refused -> says why" "$(grep -c 'already running' <<<"$OUT")" "1"
check "refused -> the other run's lock is left alone" "$([ -d "$D5/.next-staging.lock" ] && echo kept)" "kept"
rmdir "$D5/.next-staging.lock"

echo "deploy-build.sh after a commit deletes a route:"
# 2026-09-27: tsconfig.json includes ".next/types/**/*.ts", and next build
# type-checks everything the tsconfig includes. An in-place build deletes
# .next first; a staged build does not, so the served build's route types
# were checked too -- and one whose page a commit deleted (5419dd9 deleted
# app/(app)/settings/[slug]/layout.tsx) failed with TS2307: "BUILD FAILED;
# nothing staged" for a build that works in place.
D7="$TMP/routes"; mkdir -p "$D7/app/kept" "$D7/app/gone"
echo x > "$D7/app/kept/page.tsx"; echo x > "$D7/app/gone/page.tsx"
printf '{\n  "include": ["next-env.d.ts", "**/*.ts", ".next/types/**/*.ts", ".next-harness/types/**/*.ts"]\n}\n' \
    > "$D7/tsconfig.json"
touch -t 202609010000 "$D7/tsconfig.json"
OUT="$(PATH="$NX:$PATH" ensure_build "$D7" 2>&1)"; RC=$?
check "in-place build with both routes -> ok" "$RC" "0"
check "the served build holds the route type of the page about to go" \
    "$([ -f "$D7/.next/types/app/gone/page.ts" ] && echo yes)" "yes"
rm -rf "$D7/app/gone"
TS_BEFORE="$(cat "$D7/tsconfig.json")"
OUT="$(PATH="$NX:$PATH" bash "$HERE/deploy-build.sh" "$D7" 2>&1)"; RC=$?
check "a deploy build after a route was deleted -> staged" "$RC" "0"
check "the staged build never type-checks the served build's route types" \
    "$(grep -c 'Cannot find module' <<<"$OUT")" "0"
check "route deleted -> tsconfig.json put back byte for byte" "$(cat "$D7/tsconfig.json")" "$TS_BEFORE"
check "route deleted -> the served .next untouched" \
    "$([ -f "$D7/.next/types/app/gone/page.ts" ] && echo yes)" "yes"
OUT="$(PATH="$NX:$PATH" ensure_build "$D7" 2>&1)"; RC=$?
check "route deleted -> the staged build is swapped in, no rebuild" \
    "$RC:$(grep -c 'running next build' <<<"$OUT")" "0:0"

echo "deploy-build.sh and the service worker in public/:"
# 2026-09-27: next-pwa writes public/sw.js and workbox-*.js on every build,
# staged or not, at the client compile -- before type checking and lint. The
# old server reads public/ from disk, so it served the new worker, which
# precaches the new build's /_next/static/<BUILD_ID>/ manifests it cannot
# serve: its install fails, and a device with no worker cannot subscribe to
# push (the 2026-09-14 symptom) -- for good when the build then failed.
D8="$TMP/pwa"; mkdir -p "$D8/app/kept" "$D8/public"; echo x > "$D8/app/kept/page.tsx"
echo '{"include": ["next-env.d.ts"]}' > "$D8/tsconfig.json"
echo "push handler (tracked)" > "$D8/public/sw-push.js"
echo "sw precaching /_next/static/SERVING/_buildManifest.js" > "$D8/public/sw.js"
echo "workbox runtime SERVING" > "$D8/public/workbox-aaaa.js"
touch -t 202609010000 "$D8/tsconfig.json" "$D8/app/kept/page.tsx" "$D8/public/"*
make_build "$D8/.next" SERVING
SW_SERVING="$(cat "$D8/public/sw.js")"
OUT="$(PATH="$NX:$PATH" STUB_NPM_FAIL=1 bash "$HERE/deploy-build.sh" "$D8" 2>&1)"; RC=$?
check "a deploy build failing after next-pwa wrote public/ -> rc 1" "$RC" "1"
check "failed -> public/sw.js back byte for byte" "$(cat "$D8/public/sw.js")" "$SW_SERVING"
check "failed -> the served build's workbox back" "$(cat "$D8/public/workbox-aaaa.js" 2>/dev/null)" "workbox runtime SERVING"
check "failed -> no workbox of the failed build left" "$(ls "$D8/public" | grep -c '^workbox-')" "1"
needs_build "$D8"; check "failed -> public/ keeps its old mtimes (no rebuild on the next start)" "$?" "1"
check "failed -> sw-push.js untouched" "$(cat "$D8/public/sw-push.js")" "push handler (tracked)"

OUT="$(PATH="$NX:$PATH" bash "$HERE/deploy-build.sh" "$D8" 2>&1)"; RC=$?
check "a deploy build with a worker in public/ -> staged" "$RC" "0"
check "staged -> public/sw.js is still the served build's" "$(cat "$D8/public/sw.js")" "$SW_SERVING"
check "staged -> only the served build's workbox in public/" "$(ls "$D8/public" | grep '^workbox-')" "workbox-aaaa.js"
check "staged -> the new worker travels with the staged build" \
    "$(grep -c '_buildManifest' "$D8/.next-staging/EDWARD_PWA/sw.js" 2>/dev/null)" "1"
NEW_SW="$(cat "$D8/.next-staging/EDWARD_PWA/sw.js" 2>/dev/null)"
needs_build "$D8"; check "staged -> no source change for the old server" "$?" "1"
OUT="$(PATH="$NX:$PATH" ensure_build "$D8" 2>&1)"; RC=$?
check "swap -> no rebuild" "$RC:$(grep -c 'running next build' <<<"$OUT")" "0:0"
check "swap -> public/sw.js is the new build's" "$(cat "$D8/public/sw.js")" "$NEW_SW"
check "swap -> the old workbox is gone from public/" \
    "$(ls "$D8/public" | grep '^workbox-' | grep -vc aaaa)" "1"
check "swap -> .next.prev keeps its own worker" "$(cat "$D8/.next.prev/EDWARD_PWA/sw.js" 2>/dev/null)" "$SW_SERVING"
check "swap -> .next.prev keeps its own workbox" \
    "$(cat "$D8/.next.prev/EDWARD_PWA/workbox-aaaa.js" 2>/dev/null)" "workbox runtime SERVING"
needs_build "$D8"; check "swap -> the installed worker is no source change" "$?" "1"
check "swap -> sw-push.js untouched" "$(cat "$D8/public/sw-push.js")" "push handler (tracked)"

echo "rolling back to .next.prev (rollback-build.sh):"
# 2026-09-27: the documented rollback was `mv .next .next.bad && mv
# .next.prev .next`, then a kickstart. A rename keeps .next.prev/BUILD_ID's
# mtime, older than the git pull that brought the bad code (and than the
# public/sw.js the deploy build wrote), so needs_build said "rebuild": the
# kickstart rebuilt the bad sources in place -- a minute of 502s -- served
# them again, and next build's clean destroyed the kept build. Even a
# rollback that stuck left the bad build's worker in public/.
D9="$TMP/rollback"; mkdir -p "$D9/app/kept"
echo v1 > "$D9/app/kept/page.tsx"; echo '{"include": ["next-env.d.ts"]}' > "$D9/tsconfig.json"
touch -t 202609010000 "$D9/tsconfig.json" "$D9/app/kept/page.tsx"
OUT="$(PATH="$NX:$PATH" ensure_build "$D9" 2>&1)"          # the good build, in place
GOOD_ID="$(cat "$D9/.next/BUILD_ID")"; GOOD_SW="$(cat "$D9/public/sw.js")"
GOOD_WB="$(ls "$D9/public" | grep '^workbox-')"
sleep 1; echo v2-bad > "$D9/app/kept/page.tsx"                # git pull
OUT="$(PATH="$NX:$PATH" bash "$HERE/deploy-build.sh" "$D9" 2>&1)"
OUT="$(PATH="$NX:$PATH" ensure_build "$D9" 2>&1)"            # the kickstart swaps it in
BAD_ID="$(cat "$D9/.next/BUILD_ID")"; BAD_SW="$(cat "$D9/public/sw.js")"
check "rollback setup -> the bad build is served, the good one kept" \
    "$([ "$BAD_ID" != "$GOOD_ID" ] && [ "$(cat "$D9/.next.prev/BUILD_ID")" = "$GOOD_ID" ] && echo yes)" "yes"
OUT="$(bash "$HERE/rollback-build.sh" "$D9" 2>&1)"; RC=$?
check "rollback -> ok" "$RC" "0"
check "rollback -> .next is the previous build" "$(cat "$D9/.next/BUILD_ID" 2>/dev/null)" "$GOOD_ID"
check "rollback -> the build rolled back is kept as .next.prev" "$(cat "$D9/.next.prev/BUILD_ID" 2>/dev/null)" "$BAD_ID"
check "rollback -> public/sw.js is the previous build's" "$(cat "$D9/public/sw.js")" "$GOOD_SW"
check "rollback -> only the previous build's workbox in public/" "$(ls "$D9/public" | grep '^workbox-')" "$GOOD_WB"
check "rollback -> says to restart the frontend" "$(grep -c 'kickstart' <<<"$OUT")" "1"
needs_build "$D9"; check "rollback -> the restored build counts as current" "$?" "1"
: > "$NPM_CALLS"
OUT="$(PATH="$REC:$PATH" ensure_build "$D9" 2>&1)"; RC=$?
check "restart after a rollback -> no rebuild" "$RC:$(wc -l < "$NPM_CALLS" | tr -d ' ')" "0:0"
check "restart after a rollback -> serves the previous build" "$(cat "$D9/.next/BUILD_ID")" "$GOOD_ID"
OUT="$(bash "$HERE/rollback-build.sh" "$D9" 2>&1)"; RC=$?
check "a second rollback undoes the first" "$RC:$(cat "$D9/.next/BUILD_ID")" "0:$BAD_ID"
check "undone -> public/sw.js is the bad build's again" "$(cat "$D9/public/sw.js")" "$BAD_SW"
sleep 1; echo v3-fix > "$D9/app/kept/page.tsx"
needs_build "$D9"; check "after a rollback the next source change rebuilds as usual" "$?" "0"

R1="$TMP/rollback-none"; mkdir -p "$R1"; make_build "$R1/.next" ONLY
OUT="$(bash "$HERE/rollback-build.sh" "$R1" 2>&1)"; RC=$?
check "no .next.prev -> refused" "$RC" "1"
check "no .next.prev -> .next untouched" "$(cat "$R1/.next/BUILD_ID")" "ONLY"
mkdir "$D9/.next-staging.lock"
OUT="$(bash "$HERE/rollback-build.sh" "$D9" 2>&1)"; RC=$?
check "a deploy build running -> refused (it puts public/'s worker back when it ends)" \
    "$RC:$(cat "$D9/.next/BUILD_ID")" "1:$BAD_ID"
rmdir "$D9/.next-staging.lock"
stage "$D9" WAITING
OUT="$(bash "$HERE/rollback-build.sh" "$D9" 2>&1)"; RC=$?
check "a staged build waiting -> refused (the restart would serve it)" "$RC:$(cat "$D9/.next/BUILD_ID")" "1:$BAD_ID"
rm -rf "$D9/.next-staging"
R2="$TMP/rollback-noworker"; mkdir -p "$R2/public"
make_build "$R2/.next.prev" OLDER; sleep 1; make_build "$R2/.next" NEWER
echo "sw of NEWER" > "$R2/public/sw.js"; touch -t 202609010000 "$R2/public/sw.js"
OUT="$(bash "$HERE/rollback-build.sh" "$R2" 2>&1)"; RC=$?
check "a .next.prev kept without its worker -> still rolled back" "$RC:$(cat "$R2/.next/BUILD_ID")" "0:OLDER"
check "no saved worker -> public/ left as it is, and says so" \
    "$(cat "$R2/public/sw.js"):$(grep -c 'service worker' <<<"$OUT")" "sw of NEWER:1"
check "the rollback's leftovers are never committed" \
    "$(cd "$HERE" && git check-ignore .next.prev/x .next.rollback/x .next.bad/x .next-staging/x | wc -l | tr -d ' ')" "4"

echo
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
