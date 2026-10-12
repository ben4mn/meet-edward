#!/bin/bash
# Build the next production bundle beside the one being served.
#
# Why (2026-09-27): a deploy used to kickstart the frontend and let
# run-launchd.sh build afterwards, with the old server already killed --
# about a minute of 502s through the tunnel (`connection refused` on :3000)
# and longer when the build was cold. This builds into .next-staging while `next start` keeps serving
# .next; the restart then only swaps directories (ensure-build.sh's
# swap_staged_build) and starts in seconds.
#
# Deploy:
#   git pull
#   bash frontend/deploy-build.sh           # the site stays up meanwhile
#   launchctl kickstart -k gui/$UID/<prefix>.frontend   (or ./restart.sh frontend)
# A failed build leaves no staged build, so the restart (if you make one)
# builds in place as before -- and falls back to `next dev` if that fails.
#
# The service worker (2026-09-27): next-pwa used to write public/sw.js and
# workbox-*.js at the client compile, whatever the distDir -- before type
# checking and lint can still fail the build. The old server reads public/
# from disk, and the new worker precaches the new build's manifests, which
# the old server 404s, so its install fails; a device with no worker could
# not subscribe to push (the 2026-09-14 symptom), and a failed build left
# it that way for good. next.config.js now points next-pwa's dest at
# .next-staging/EDWARD_PWA/ when NEXT_DIST_DIR is .next-staging, so public/
# is never written during the build, and the swap installs that worker
# (ensure-build.sh, pwa_files). public/'s worker files are still
# snapshotted first and put back when the build ends, whatever happened,
# and a worker that did land in public/ (an older next.config.js) still
# moves into .next-staging/EDWARD_PWA/.
#
# `next build` adds "<distDir>/types/**/*.ts" to tsconfig.json (reformatting
# it) when that include is missing. The file is put back byte for byte with
# its old mtime -- it is tracked in git, and a newer tsconfig.json would make
# ensure-build.sh rebuild in place right after the swap.
#
# For the build itself tsconfig.json includes .next-staging/types and no
# other build's types (staged_tsconfig). next build type-checks every file
# the tsconfig includes, and ".next/types/**/*.ts" is in it: an in-place
# build deletes .next first, but a staged one left the served build's route
# types in the program, and each imports its page by relative path -- so a
# commit that deleted or renamed a route (5419dd9 deleted
# app/(app)/settings/[slug]/layout.tsx) failed the staged build with TS2307
# while an in-place build of the same code passed (2026-09-27).
#
# Usage: bash deploy-build.sh [frontend_dir]   (default: this script's dir)
# Exit: 0 staged, 1 failed or another deploy build is running.
# Tests: bash frontend/test_ensure_build.sh

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENSURE_BUILD_LIB_ONLY=1 . "$HERE/ensure-build.sh"

DIR="$(cd "${1:-$HERE}" && pwd)"
STAGED="$DIR/$STAGING_DIR"
LOCK="$DIR/$STAGING_DIR.lock"
LOCK_STALE_MIN=60   # a deploy build takes minutes; an hour-old lock is a killed run's

if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +"$LOCK_STALE_MIN" 2>/dev/null)" ]; then
    echo "[deploy-build] breaking a lock older than ${LOCK_STALE_MIN} min ($LOCK)"
    rm -rf "$LOCK"
fi
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "[deploy-build] a deploy build is already running ($LOCK); remove it if none is"
    exit 1
fi
TS="$DIR/tsconfig.json"
[ -f "$TS" ] && cp -p "$TS" "$LOCK/tsconfig.json"
PUB="$DIR/public"
if ! save_pwa "$PUB" "$LOCK/pwa"; then
    echo "[deploy-build] could not snapshot the service worker in public/; not building"
    rm -rf "$LOCK"
    exit 1
fi

# staged_tsconfig FILE DIST -> FILE's include with every "<.next*>/types/**/*.ts"
# entry replaced by DIST's own, so the staged build type-checks its route
# types and no other build's. The old mtime is kept, so a server respawned
# mid-build does not see a source newer than its bundle. Not JSON (comments)
# -> left as it is, with a warning: the build then runs as before.
staged_tsconfig() {
    local file="$1" dist="$2"
    [ -f "$file" ] || return 0
    if ! node -e '
        const fs = require("fs");
        const [file, dist] = process.argv.slice(1);
        const ts = JSON.parse(fs.readFileSync(file, "utf8"));
        const other = /^\.next[^/]*\/types\/\*\*\/\*\.ts$/;
        ts.include = (ts.include || []).filter((p) => !other.test(p)).concat(`${dist}/types/**/*.ts`);
        fs.writeFileSync(file, JSON.stringify(ts, null, 2) + "\n");
    ' "$file" "$dist"; then
        echo "[deploy-build] could not point tsconfig.json at $dist/types; building with it as it is"
        return 0
    fi
    [ -f "$LOCK/tsconfig.json" ] && touch -r "$LOCK/tsconfig.json" "$file"
    return 0
}

restore_tsconfig() {
    if [ -f "$LOCK/tsconfig.json" ] && ! cmp -s "$LOCK/tsconfig.json" "$TS"; then
        cp -p "$LOCK/tsconfig.json" "$TS" && echo "[deploy-build] put tsconfig.json back as it was"
    fi
}
# The served build's worker back in public/, byte for byte with its mtimes,
# and nothing of this build's left beside it.
restore_pwa() {
    [ -d "$LOCK/pwa" ] || return 0
    install_pwa "$LOCK/pwa" "$PUB" || echo "[deploy-build] could not put the served build's service worker back in public/ -- see $LOCK/pwa"
}
cleanup() {
    restore_tsconfig
    restore_pwa
    # An interrupted or failed run never leaves half a build behind.
    [ -f "$STAGED/$STAGED_MARKER" ] || rm -rf "$STAGED"
    rm -rf "$LOCK"
}
trap cleanup EXIT
trap 'exit 1' INT TERM

rm -rf "$STAGED"
frontend_build_env
staged_tsconfig "$TS" "$STAGING_DIR"
echo "[deploy-build] $(date '+%F %T') building into $STAGING_DIR while the current build keeps serving"
if ! ( cd "$DIR" && NEXT_DIST_DIR="$STAGING_DIR" npm run build ); then
    echo "[deploy-build] BUILD FAILED; nothing staged, the current build keeps serving"
    rm -rf "$STAGED"
    exit 1
fi
restore_tsconfig
# This build's worker goes with it (unless next-pwa already wrote it there),
# and public/ gets the served build's back.
if [ -z "$(pwa_files "$STAGED/$PWA_DIR")" ] && ! save_pwa "$PUB" "$STAGED/$PWA_DIR"; then
    echo "[deploy-build] could not keep the new service worker with the staged build; nothing staged"
    rm -rf "$STAGED"
    exit 1
fi
restore_pwa

if [ ! -f "$STAGED/BUILD_ID" ]; then
    echo "[deploy-build] the build finished without $STAGING_DIR/BUILD_ID; nothing staged"
    rm -rf "$STAGED"
    exit 1
fi
# Last, so an unfinished build never looks complete to ensure-build.sh.
printf 'build_id=%s\nstaged_at=%s\n' "$(cat "$STAGED/BUILD_ID")" "$(date +%s)" > "$STAGED/$STAGED_MARKER"
if ! staged_build_ready "$DIR"; then
    echo "[deploy-build] the staged build is missing files next start needs; removing it"
    rm -rf "$STAGED"
    exit 1
fi

bundle_report "$STAGED"
echo "[deploy-build] $(date '+%F %T') staged build $(cat "$STAGED/BUILD_ID"). Swap it in with:"
echo "  launchctl kickstart -k gui/$(id -u)/$(frontend_label)"
exit 0
