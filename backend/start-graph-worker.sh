#!/bin/bash
# Start Edward's out-of-process Graphiti worker.
#
# Runs as its own LaunchAgent (<prefix>.graph-worker, the prefix from
# EDWARD_LAUNCHD_PREFIX, default com.meet-edward) with an independent
# Python interpreter. Owns Kuzu exclusively; the main backend only talks
# to it via HTTP / the graph_ingest_queue table. A segfault here respawns
# just this process, not the backend.
#
# ADAPT: the knowledge graph is optional and off by default.
#   How it works here: scripts/install-launchagents.sh installs a KeepAlive
#   <prefix>.graph-worker agent that runs this script; with GRAPHITI_ENABLED
#   unset it sleeps 30 s and exits, so launchd backs off instead of looping.
#   To adapt: set GRAPHITI_ENABLED=true in .env to turn it on (graphiti-core
#   and kuzu are in requirements.txt); leave the agent out to skip it.

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Error: .venv not found in $(pwd)"
    exit 1
fi
VENV="$PWD/.venv"
export VIRTUAL_ENV="$VENV"
unset PYTHONHOME

# PATH: the venv's own bin, the system's directories, then Postgres 16's
# tools and Homebrew's; never ~/.local/bin or ~/bin (see backend/start.sh
# and scripts/lib/edward-env.sh: edward_service_path).
. ../scripts/lib/edward-env.sh
export PATH="$VENV/bin:$(edward_service_path)"

# .env is not exported into this process (see backend/start.sh): the
# worker loads it itself (workers/graph_worker.py), the environment
# winning. Read here: only what this script decides on, and the ~ paths
# .env holds, expanded as sourcing it used to.
edward_export_home_paths

# The worker is the only one who touches Kuzu, so its fork-safety envs
# matter here too.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY="$(edward_backend_env OBJC_DISABLE_INITIALIZE_FORK_SAFETY YES)"
export TOKENIZERS_PARALLELISM="$(edward_backend_env TOKENIZERS_PARALLELISM false)"

# If GRAPHITI_ENABLED is off, there's nothing for the worker to do —
# exit cleanly so launchd's ThrottleInterval backs off instead of
# spinning on a crash loop.
if [ "$(edward_backend_env GRAPHITI_ENABLED false)" != "true" ]; then
    echo "[graph-worker] GRAPHITI_ENABLED != true — exiting cleanly"
    sleep 30  # Prevents launchd from tight-looping when the flag is off
    exit 0
fi

# Rotate previous log to .prev so a crash trace survives the next launchd
# respawn. The agent's plist must drop StandardOutPath/StandardErrorPath
# for this to take effect (scripts/install-launchagents.sh writes it so).
LOG="${EDWARD_GRAPH_WORKER_LOG:-/tmp/edward-graph-worker.log}"   # a test points it elsewhere
[ -f "$LOG" ] && mv -f "$LOG" "${LOG}.prev"

echo "[graph-worker] starting on http://127.0.0.1:8001 ..."
exec "$VENV"/bin/uvicorn workers.graph_worker:app \
    --host 127.0.0.1 \
    --port 8001 \
    > "$LOG" 2>&1
