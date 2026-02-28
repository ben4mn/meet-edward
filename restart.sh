#!/bin/bash
# Restart Edward: gracefully stop old processes, restart frontend and backend.
#
# Usage:
#   ./restart.sh           # Restart both frontend and backend
#   ./restart.sh frontend  # Restart only the frontend
#   ./restart.sh backend   # Restart only the backend

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPONENT="${1:-all}"  # "all", "frontend", or "backend"
BACKEND_LOG="/tmp/edward-backend.log"
FRONTEND_LOG="/tmp/edward-frontend.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No color

info()  { echo -e "${GREEN}[restart]${NC} $1"; }
warn()  { echo -e "${YELLOW}[restart]${NC} $1"; }
error() { echo -e "${RED}[restart]${NC} $1"; }

# ── Stop backend ──────────────────────────────────────────────────────
stop_backend() {
    info "Stopping backend..."
    local pids
    pids=$(lsof -t -i :8000 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        for i in {1..10}; do
            if ! lsof -i :8000 >/dev/null 2>&1; then break; fi
            sleep 0.5
        done
        if lsof -i :8000 >/dev/null 2>&1; then
            warn "Backend didn't stop gracefully, force killing..."
            lsof -t -i :8000 2>/dev/null | xargs kill -9 2>/dev/null || true
            sleep 1
        fi
        info "Backend stopped"
    else
        info "Backend was not running"
    fi
}

# ── Start backend ─────────────────────────────────────────────────────
start_backend() {
    info "Starting backend..."
    cd "$PROJECT_DIR/backend"
    bash start.sh > "$BACKEND_LOG" 2>&1 &
    local backend_pid=$!

    for i in {1..30}; do
        if lsof -i :8000 >/dev/null 2>&1; then
            info "Backend is UP on port 8000 (PID group: $backend_pid)"
            return 0
        fi
        sleep 0.5
    done

    error "Backend failed to start within 15s. Check log: $BACKEND_LOG"
    tail -20 "$BACKEND_LOG"
    return 1
}

# ── Stop frontend ─────────────────────────────────────────────────────
stop_frontend() {
    info "Stopping frontend..."
    local pids
    pids=$(lsof -t -i :3000 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 2
        if lsof -i :3000 >/dev/null 2>&1; then
            lsof -t -i :3000 2>/dev/null | xargs kill -9 2>/dev/null || true
        fi
        info "Frontend stopped"
    else
        info "Frontend was not running"
    fi
}

# ── Start frontend ────────────────────────────────────────────────────
start_frontend() {
    info "Starting frontend..."
    cd "$PROJECT_DIR/frontend"
    npm run dev > "$FRONTEND_LOG" 2>&1 &

    for i in {1..20}; do
        if curl -s -o /dev/null -w '' http://localhost:3000 2>/dev/null; then
            info "Frontend is UP on port 3000"
            return 0
        fi
        sleep 1
    done

    error "Frontend didn't respond within 20s"
    return 1
}

# ── Main ──────────────────────────────────────────────────────────────
echo ""
info "=== Edward Restart ==="
echo ""

case "$COMPONENT" in
    all)
        stop_backend
        stop_frontend
        start_frontend
        start_backend
        ;;
    frontend)
        stop_frontend
        start_frontend
        ;;
    backend)
        stop_backend
        start_backend
        ;;
    *)
        error "Unknown component: $COMPONENT"
        echo "Usage: $0 [all|frontend|backend]"
        exit 1
        ;;
esac

echo ""
info "=== Done ==="
info "  Backend:  http://localhost:8000  (log: $BACKEND_LOG)"
info "  Frontend: http://localhost:3000  (log: $FRONTEND_LOG)"
echo ""
