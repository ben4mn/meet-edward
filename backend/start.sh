#!/bin/bash
# Start Edward backend locally on macOS
#
# This is the recommended way to run the backend. Running natively (not in
# Docker) is required for iMessage, the calendar, scheduled events and any
# other feature that needs macOS system access. setup.sh builds the venv;
# scripts/install-launchagents.sh runs this under launchd.

cd "$(dirname "$0")"

# The virtual environment (setup.sh builds it). Its programs are run by
# absolute path below, never found on PATH.
if [ ! -d .venv ]; then
    echo "Error: .venv not found. Create it first:"
    echo "  ./setup.sh   (from the repo root), or by hand:"
    echo "  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -c constraints.txt"
    exit 1
fi
VENV="$PWD/.venv"
export VIRTUAL_ENV="$VENV"
unset PYTHONHOME

# PATH (scripts/lib/edward-env.sh: edward_service_path): the venv's own bin
# (inside this checkout, which Edward's shell can't write), then the
# system's own directories, then Postgres 16's tools and Homebrew's.
# Never ~/.local/bin or ~/bin, and nothing user-writable ahead of /usr/bin:
# this process runs outside Edward's sandbox, and anything it runs by bare
# name (sips, osascript, open …) must not be a program his shell planted
# (review 2026-10-12). postgresql@16 is keg-only, so a fresh Homebrew
# install puts neither psql nor pg_config on PATH; psycopg uses the libpq
# psycopg-binary bundles, and without that wheel finds Homebrew's libpq
# through pg_config. EDWARD_PG_BIN (environment or .env) names another
# Postgres 16 (edward_pg_bin).
. ../scripts/lib/edward-env.sh
export PATH="$VENV/bin:$(edward_service_path)"

# Install/upgrade dependencies if requirements.txt (or constraints.txt) is
# newer than the marker. constraints.txt pins a known-good set
# (scripts/pin_requirements.sh, 2026-09-24): unpinned, a venv rebuild pulled
# a checkpoint library that cannot read older threads ("text = bytea").
MARKER=".venv/.deps_installed"
if [ ! -f "$MARKER" ] || [ requirements.txt -nt "$MARKER" ] \
        || { [ -f constraints.txt ] && [ constraints.txt -nt "$MARKER" ]; }; then
    echo "Installing/upgrading dependencies..."
    if [ -f constraints.txt ]; then
        "$VENV/bin/python" -m pip install -r requirements.txt -c constraints.txt -q && touch "$MARKER"
    else
        "$VENV/bin/python" -m pip install -r requirements.txt -q && touch "$MARKER"
    fi
fi

# .env is not exported into this process (review 2026-10-12). A value in
# the environment a process starts with can be read by any other process of
# the same user (sysctl KERN_PROCARGS2), Edward's sandboxed shell included,
# so the API keys stay out of it: main.py loads .env itself
# (python-dotenv, the environment winning) once it is running. This script
# reads only the settings it needs (edward_backend_env), and hands the
# backend the ~ paths .env holds already expanded, as sourcing it used to
# (edward_export_home_paths).
edward_export_home_paths
if [ ! -r ../.env ] && [ -r .env ]; then
    echo "Warning: backend/.env is not read by the backend; move it to $(cd .. && pwd)/.env"
fi

# The database: shown here, chosen by the backend (DATABASE_URL from the
# environment or .env, else the role and database setup.sh creates).
DB_SHOWN="$(edward_backend_env DATABASE_URL postgresql://edward:edward@localhost:5432/edward)"

BACKEND_HOST="$(edward_backend_env EDWARD_BACKEND_HOST 0.0.0.0)"

echo "Starting Edward backend..."
echo "  - Database: ${DB_SHOWN##*@}"
echo "  - Scheduler: enabled (polls every 30s)"
echo "  - API: http://localhost:8000 (listening on $BACKEND_HOST)"
echo ""

# Env vars that tame macOS fork/multiprocessing hazards for
# sentence-transformers + tokenizers. Without these, a child worker
# spawned after the main process has initialized Objective-C runtime
# can silently hang, wedging the parent's event loop.
# They are read when the process starts, so they come from here (the
# environment, then .env, then the default), not from main.py's load.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY="$(edward_backend_env OBJC_DISABLE_INITIALIZE_FORK_SAFETY YES)"
export TOKENIZERS_PARALLELISM="$(edward_backend_env TOKENIZERS_PARALLELISM false)"

# Start uvicorn
# NO --reload: uvicorn's reload mode forks a watcher + worker process
# and the bash parent can lose track of which child holds the port.
# When combined with launchd's KeepAlive=true on this service, orphaned
# workers kept port 8000 while launchd respawned fresh ones that failed
# to bind — each failed attempt spawned a new Python.app and flashed the
# Dock. Removing --reload makes uvicorn a single process, which launchd
# tracks cleanly. For live-code dev, run `restart.sh` or
# `launchctl kickstart -k gui/$UID/<prefix>.backend` (EDWARD_LAUNCHD_PREFIX,
# default com.meet-edward).
#
# --host: 0.0.0.0 (every interface) by default, so devices on the LAN (a
# household hub, a phone at home) can reach the API; EDWARD_BACKEND_HOST=
# 127.0.0.1 keeps it to this Mac, behind the frontend or a tunnel.
#
# --log-config: uvicorn's own logging config plus services/access_log.py's
# filter, which redacts secret query parameters (?token=, ?key=, ?code= …)
# before a request line reaches the world-readable /tmp/edward-backend.log
# (2026-09-27: the widget token was in it several times a minute), and a
# timestamp on access lines like every other line. Keep it after --port:
# restart.sh finds stragglers with `uvicorn main:app.*--port 8000`.
# By absolute path: the venv's own uvicorn, never one found on PATH.
exec "$VENV"/bin/uvicorn main:app --host "$BACKEND_HOST" --port 8000 --log-config log_config.json
