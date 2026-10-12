#!/bin/bash
# Build the production bundle when it is missing or older than the sources.
#
# The frontend used to run `next dev` in production: every cold open of
# the PWA pulled ~11 MB of unminified JavaScript, and the service worker
# cached dev chunks (whose names never change) for 30 days, so a phone
# could run last week's page against this week's runtime. `next build` +
# `next start` fixes both; this script keeps the bundle current without
# rebuilding on every launchd respawn.
#
# Build before the swap (2026-09-27): building here happens after launchd
# has killed the old server, so a deploy was ~1 min of 502s through the
# tunnel. deploy-build.sh builds into .next-staging while the old server
# keeps serving; on start, swap_staged_build moves a complete, newer staged
# build into .next (the one it replaces is kept as .next.prev) and the
# in-place build below is then skipped because the bundle is current. With
# no staged build nothing changes. Roll back with
#   bash frontend/rollback-build.sh
#   launchctl kickstart -k gui/$UID/<prefix>.frontend   (or ./restart.sh frontend)
# (rollback_to_previous; running it again undoes it). Never with a bare
# `mv .next.prev .next`: the restart would rebuild the bad code in place.
#
# Sourced by test_ensure_build.sh (and deploy-build.sh) with
# ENSURE_BUILD_LIB_ONLY=1.

set -uo pipefail

STAGING_DIR=".next-staging"
PREVIOUS_DIR=".next.prev"
# deploy-build.sh writes this into the staged build last, so a build that
# is still running or failed never looks complete.
STAGED_MARKER="EDWARD_STAGED"
# A build's own service worker, kept inside that build (see pwa_files).
PWA_DIR="EDWARD_PWA"

# frontend_label -> this checkout's frontend LaunchAgent label: the prefix
# from EDWARD_LAUNCHD_PREFIX (the environment, else the repo's .env), else
# com.meet-edward. Only printed, in the "restart it now" hints.
frontend_label() {
    local here lib
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    lib="$here/../scripts/lib/edward-env.sh"
    if [ -f "$lib" ]; then
        ( . "$lib" && edward_label frontend )
    else
        echo "${EDWARD_LAUNCHD_PREFIX:-com.meet-edward}.frontend"
    fi
}

# needs_build DIR -> 0 when a build is needed, 1 when the bundle is current.
# "Current" means .next/BUILD_ID exists and no source file is newer than it.
# node_modules and every .next* directory are ignored (npm install churn,
# the build's own outputs, a staged or previous build, the harness's).
needs_build() {
    local dir="$1" stamp="$1/.next/BUILD_ID" newer
    [ -f "$stamp" ] || return 0
    newer=$(find "$dir" \
        \( -path "$dir/node_modules" -o -path "$dir/.next*" -o -name ".DS_Store" \) -prune \
        -o -type f -newer "$stamp" -print -quit 2>/dev/null)
    [ -n "$newer" ] && return 0
    return 1
}

# staged_build_ready DIR -> 0 when DIR/.next-staging is a finished build:
# deploy-build.sh's marker naming the staged BUILD_ID, plus what
# `next start` reads. Only required-server-files.json names the staging
# directory, and next start takes nothing from it but an experimental flag,
# so a build moved to .next serves as it is (proven 2026-09-27: built into
# .next-staging, swapped, `next start` answered /login and its chunks).
staged_build_ready() {
    local s="$1/$STAGING_DIR" f
    [ -f "$s/$STAGED_MARKER" ] && [ -f "$s/BUILD_ID" ] || return 1
    [ "$(sed -n 's/^build_id=//p' "$s/$STAGED_MARKER")" = "$(cat "$s/BUILD_ID")" ] || return 1
    for f in build-manifest.json app-build-manifest.json prerender-manifest.json \
             routes-manifest.json required-server-files.json server static; do
        [ -e "$s/$f" ] || return 1
    done
    return 0
}

# pwa_files DIR -> the service-worker files next-pwa generates, one path a
# line: sw.js, the workbox runtime, a custom or fallback worker, their maps.
# Never sw-push.js, which is tracked.
#
# Why they travel with their build (2026-09-27): next-pwa wrote them into
# public/ on every build, staged or not (a staged build now writes them into
# its own EDWARD_PWA/, next.config.js), and `next start` reads public/
# from disk. The old server served the new worker, which precaches the new
# build's /_next/static/<BUILD_ID>/ manifests the old server cannot serve:
# Workbox fails the whole install, so a device with no worker cannot
# subscribe to push (the 2026-09-14 symptom). So deploy-build.sh puts
# public/ back after the build and keeps the new worker in the staged
# build's EDWARD_PWA/; the swap installs it and keeps the replaced one in
# .next.prev/EDWARD_PWA/, and a rollback puts that one back.
pwa_files() {
    [ -d "$1" ] || return 0
    find "$1" -maxdepth 1 -type f \( -name sw.js -o -name sw.js.map \
        -o -name 'workbox-*.js' -o -name 'workbox-*.js.map' \
        -o -name 'worker-*.js' -o -name 'worker-*.js.map' \
        -o -name 'fallback-*.js' -o -name 'fallback-*.js.map' \) 2>/dev/null
}

# save_pwa PUBLIC DEST -> PUBLIC's worker files copied into DEST (created,
# even when there are none), mtimes kept.
save_pwa() {
    local f
    mkdir -p "$2" || return 1
    while IFS= read -r f; do
        cp -p "$f" "$2/" || return 1
    done < <(pwa_files "$1")
}

# install_pwa SRC PUBLIC -> PUBLIC's worker files are exactly SRC's, with
# SRC's mtimes -- older than the build they came with, so needs_build does
# not see a source change.
install_pwa() {
    local f
    [ -d "$1" ] || return 1
    mkdir -p "$2" || return 1
    while IFS= read -r f; do rm -f "$f"; done < <(pwa_files "$2")
    while IFS= read -r f; do
        cp -p "$f" "$2/" || return 1
    done < <(pwa_files "$1")
}

# swap_staged_build DIR -> 0 swapped in, 1 nothing to swap (no staged build,
# or one still building -- left alone), 2 a staged build older than .next
# (someone built in place since) was discarded. Run only while no server
# is serving .next: two renames on one volume, microseconds apart.
swap_staged_build() {
    local dir="$1" staged="$1/$STAGING_DIR" live="$1/.next" prev="$1/$PREVIOUS_DIR" pub="$1/public"
    [ -d "$staged" ] || return 1
    if ! staged_build_ready "$dir"; then
        echo "[ensure-build] leaving $STAGING_DIR alone: no $STAGED_MARKER marker (a deploy build still running, or one that failed)"
        return 1
    fi
    if [ -f "$live/BUILD_ID" ] && ! [ "$staged/BUILD_ID" -nt "$live/BUILD_ID" ]; then
        echo "[ensure-build] discarding staged build $(cat "$staged/BUILD_ID"): .next ($(cat "$live/BUILD_ID")) is newer"
        rm -rf "$staged"
        return 2
    fi
    if [ -d "$live" ]; then
        # Only now is .next.prev replaced -- after a swap killed between the
        # two renames (.next gone), the old .next.prev is all there is.
        rm -rf "$prev"
        # The worker in public/ is the outgoing build's unless it already
        # carries its own (a build swapped in earlier, or rolled back to).
        if [ ! -d "$live/$PWA_DIR" ] && ! save_pwa "$pub" "$live/$PWA_DIR"; then
            echo "[ensure-build] could not keep the service worker with the outgoing build (a rollback would serve the new one)"
            rm -rf "$live/$PWA_DIR"
        fi
        mv "$live" "$prev" || { echo "[ensure-build] could not move .next aside; not swapping"; return 1; }
    fi
    if ! mv "$staged" "$live"; then
        echo "[ensure-build] could not move $STAGING_DIR into .next; putting the old build back"
        [ -d "$prev" ] && [ ! -e "$live" ] && mv "$prev" "$live"
        return 1
    fi
    if [ -d "$live/$PWA_DIR" ] && ! install_pwa "$live/$PWA_DIR" "$pub"; then
        echo "[ensure-build] could not install the new build's service worker into public/"
    fi
    # The webpack cache is hundreds of MB and a rollback does not need it.
    rm -rf "$prev/cache"
    echo "[ensure-build] $(date '+%F %T') swapped in staged build $(cat "$live/BUILD_ID")$([ -d "$prev" ] && echo " (previous: $(cat "$prev/BUILD_ID" 2>/dev/null) in $PREVIOUS_DIR)")"
    return 0
}

# rollback_to_previous DIR -> 0 .next and .next.prev swapped (so running it
# again undoes it), 1 refused with a reason and nothing changed. Restart
# the frontend straight after: the running server still holds the build
# it started with.
#
# Why not `mv .next .next.bad && mv .next.prev .next` (the first version of
# this header, 2026-09-27): a rename keeps .next.prev/BUILD_ID's mtime,
# older than the git pull that brought the bad code, so needs_build said
# "rebuild" and the restart rebuilt the bad sources in place -- a minute of
# 502s -- and served them again, with next build's clean taking the kept
# build with it. So the restored BUILD_ID is touched: the rollback holds
# until the next source change, which rebuilds as any change does. It also
# left the bad build's worker in public/; the restored build's own goes
# back (pwa_files).
rollback_to_previous() {
    local dir="$1" live="$1/.next" prev="$1/$PREVIOUS_DIR" hold="$1/.next.rollback" pub="$1/public"
    if [ ! -f "$prev/BUILD_ID" ]; then
        echo "[rollback] no previous build in $PREVIOUS_DIR -- nothing to roll back to"
        return 1
    fi
    if [ ! -f "$live/BUILD_ID" ]; then
        echo "[rollback] no build in .next to swap with $PREVIOUS_DIR -- start the frontend and let ensure-build.sh decide"
        return 1
    fi
    if [ -d "$dir/$STAGING_DIR.lock" ]; then
        echo "[rollback] a deploy build is running ($STAGING_DIR.lock): it puts public/'s service worker back when it ends -- roll back after it"
        return 1
    fi
    if staged_build_ready "$dir"; then
        echo "[rollback] a staged build ($(cat "$dir/$STAGING_DIR/BUILD_ID")) is waiting and the restart would serve it -- remove $STAGING_DIR first to roll back instead"
        return 1
    fi
    if [ -e "$hold" ]; then
        echo "[rollback] $hold is in the way (an interrupted rollback?) -- see which build it holds before removing it"
        return 1
    fi
    # The worker in public/ is the outgoing build's unless it carries its own.
    if [ ! -d "$live/$PWA_DIR" ] && ! save_pwa "$pub" "$live/$PWA_DIR"; then
        rm -rf "$live/$PWA_DIR"
    fi
    mv "$live" "$hold" || { echo "[rollback] could not move .next aside; nothing changed"; return 1; }
    if ! mv "$prev" "$live"; then
        mv "$hold" "$live"
        echo "[rollback] could not move $PREVIOUS_DIR into .next; nothing changed"
        return 1
    fi
    mv "$hold" "$prev" || echo "[rollback] the build rolled back is in $hold (could not name it $PREVIOUS_DIR)"
    if [ -d "$live/$PWA_DIR" ]; then
        install_pwa "$live/$PWA_DIR" "$pub" \
            || echo "[rollback] could not put build $(cat "$live/BUILD_ID")'s service worker back in public/"
    else
        echo "[rollback] build $(cat "$live/BUILD_ID") was kept without its service worker, so public/ keeps the newer one: a device with no worker cannot install one until the next deploy"
    fi
    touch "$live/BUILD_ID"
    echo "[rollback] $(date '+%F %T') .next is build $(cat "$live/BUILD_ID") again; $(cat "$prev/BUILD_ID" 2>/dev/null) is in $PREVIOUS_DIR (run this again to undo). Serve it now:"
    echo "  launchctl kickstart -k gui/$(id -u)/$(frontend_label)"
    return 0
}

# bundle_report DIST -> scripts/bundle-budget.js's first-load report for
# the key routes: a WARN line for a route over its budget (2026-09-27: /today
# had reached its 160 kB with nothing watching). Always returns 0 -- a size
# regression is a line in the log, never a failed build, which would put
# production on `next dev`.
ENSURE_BUILD_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bundle_report() {
    local dist="$1"
    node "${BUNDLE_BUDGET_SCRIPT:-$ENSURE_BUILD_HERE/scripts/bundle-budget.js}" "$dist" 2>&1 \
        || echo "[bundle-budget] report failed (ignored)"
    return 0
}

# frontend_build_env -> exports what `next build` inlines from the repo's
# .env, which Next itself never reads (it reads frontend/.env*):
# NEXT_PUBLIC_BASE_URL (the app's public origin for link previews; default
# EDWARD_PUBLIC_BASE_URL) and NEXT_PUBLIC_API_URL (where /api is proxied;
# default http://localhost:8000). Values already in the environment win.
# A changed value takes effect at the next build (a source change, or
# deploy-build.sh).
frontend_build_env() {
    local here lib v
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    lib="$here/../scripts/lib/edward-env.sh"
    [ -f "$lib" ] || return 0
    v="$( . "$lib" && edward_env NEXT_PUBLIC_BASE_URL "$(edward_env EDWARD_PUBLIC_BASE_URL "")" )"
    [ -n "$v" ] && export NEXT_PUBLIC_BASE_URL="$v"
    v="$( . "$lib" && edward_env NEXT_PUBLIC_API_URL "" )"
    [ -n "$v" ] && export NEXT_PUBLIC_API_URL="$v"
    return 0
}

ensure_build() {
    local dir="$1"
    frontend_build_env
    swap_staged_build "$dir"
    if needs_build "$dir"; then
        echo "[ensure-build] $(date '+%F %T') sources newer than bundle — running next build"
        ( cd "$dir" && npm run build ) || { echo "[ensure-build] BUILD FAILED"; return 1; }
        echo "[ensure-build] $(date '+%F %T') build complete"
        bundle_report "$dir/.next"
    else
        echo "[ensure-build] bundle is current (BUILD_ID $(cat "$dir/.next/BUILD_ID"))"
    fi
}

if [ -n "${ENSURE_BUILD_LIB_ONLY:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

ensure_build "$(cd "$(dirname "$0")" && pwd)"
