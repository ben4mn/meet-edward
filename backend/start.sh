#!/bin/bash
# Start Edward backend locally on macOS
#
# This is the recommended way to run the backend. Running natively (not in
# Docker) is required for iMessage, scheduled events, and any other
# feature that needs macOS system access.

cd "$(dirname "$0")"

# Activate virtual environment
if [ ! -d .venv ]; then
    echo "Error: .venv not found. Create it first:"
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
source .venv/bin/activate

# Install/upgrade dependencies if requirements.txt is newer than the marker
MARKER=".venv/.deps_installed"
if [ ! -f "$MARKER" ] || [ requirements.txt -nt "$MARKER" ]; then
    echo "Installing/upgrading dependencies..."
    pip install -r requirements.txt -q
    touch "$MARKER"
fi

# Load environment variables from .env
set -a
source ../.env 2>/dev/null || source .env 2>/dev/null || true
set +a

# Set database URL for local postgres
export DATABASE_URL=postgresql://edward:edward@localhost:5432/edward
export MCP_IMESSAGE_ENABLED=true

echo "Starting Edward backend..."
echo "  - Database: localhost:5432"
echo "  - MCP iMessage: enabled"
echo "  - Scheduler: enabled (polls every 30s)"
echo "  - API: http://localhost:8000"
echo ""

# Start uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
