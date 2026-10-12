#!/bin/bash
# Wrapper for the <prefix>.frontend launchd job (scripts/install-launchagents.sh;
# the prefix is EDWARD_LAUNCHD_PREFIX, default com.meet-edward).
# Rotates the previous log to .prev, then serves the production build
# (`next build` when sources changed, then `next start -p 3000`, by
# absolute path) with stdout/stderr
# redirected to a fresh log. Required because launchd's StandardOutPath
# truncates the log before exec'ing the program — without this, a crash that
# triggers a KeepAlive respawn would lose the very output that explains it.
#
# If the build fails (a bad merge, say) the dev server is started instead so
# the app stays reachable — slow, but up — and the log says why.
#
# A deploy should build first (2026-09-27): `bash deploy-build.sh` builds
# into .next-staging while this job's server keeps serving, and on the
# restart ensure-build.sh swaps that build into .next (keeping the old one
# as .next.prev) instead of building here with the site down. Without a
# staged build it builds in place, as before. `bash rollback-build.sh` and a
# restart serve .next.prev again (never a bare mv: see ensure-build.sh).

set -e

LOG="${EDWARD_FRONTEND_LOG:-/tmp/edward-frontend.log}"   # a test points it elsewhere
[ -f "$LOG" ] && mv -f "$LOG" "${LOG}.prev"
exec > "$LOG" 2>&1

cd "$(dirname "$0")"
HERE="$PWD"

# PATH (scripts/lib/edward-env.sh: edward_service_path): the system's own
# directories first, then Homebrew's (where node and npm live). Never
# ~/.local/bin or ~/bin, nothing user-writable ahead of /usr/bin: this job
# runs outside Edward's sandbox (review 2026-10-12).
. ../scripts/lib/edward-env.sh
export PATH="$(edward_service_path)"

# node runs Next by absolute path (this checkout's node_modules, which
# Edward's shell can't write); npm only builds (ensure-build.sh).
NODE="$(command -v node || true)"
BREW="$(edward_brew_prefix)"
for candidate in "$BREW/bin/node" /opt/homebrew/bin/node /usr/local/bin/node; do
    [ -n "$NODE" ] && break
    [ -x "$candidate" ] && NODE="$candidate"
done
if [ -z "$NODE" ]; then
    echo "[run-launchd] node not found on PATH ($PATH); install Node (brew install node)"
    exit 1
fi
export PATH="$PATH:$(dirname "$NODE")"
NEXT="$HERE/node_modules/next/dist/bin/next"
if [ ! -f "$NEXT" ]; then
    echo "[run-launchd] $NEXT is missing; run npm install in $HERE (./setup.sh does)"
    exit 1
fi

# The port is pinned, so Edward's shell's deny on it (rails.APP_PORTS) can't
# drift from where the frontend listens.
PORT_ARG="$(edward_port frontend)"
if bash ./ensure-build.sh; then
    exec "$NODE" "$NEXT" start -p "$PORT_ARG"
fi
echo "[run-launchd] production build failed; falling back to next dev so the app stays up"
exec "$NODE" "$NEXT" dev -p "$PORT_ARG"
