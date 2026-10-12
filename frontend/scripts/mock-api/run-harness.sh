#!/usr/bin/env bash
# Edward UX harness: the real frontend (next dev :3100) against a mock API (:8100).
#
#   bash scripts/mock-api/run-harness.sh          # (re)start both, wait until ready
#   bash scripts/mock-api/run-harness.sh stop     # stop both
#   bash scripts/mock-api/run-harness.sh status   # what is listening
#   bash scripts/mock-api/run-harness.sh reset    # undo every mutation in the mock (fresh fixtures)
#   bash scripts/mock-api/run-harness.sh mock     # restart only the mock API (after editing its code)
#
# Then open http://localhost:3100/today (no login: the mock says you are authenticated).
# Logs: /tmp/edward-ux-mock.log, /tmp/edward-ux-next.log
#
# Only ports 3100 and 8100 are ever touched. The production app (3000/8000),
# its LaunchAgents, Chrome and the live database are never involved.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND="$(cd "$HERE/../.." && pwd)"
# The backend's venv has FastAPI, uvicorn and Playwright; a worktree without
# one can point EDWARD_HARNESS_PYTHON at another checkout's venv python.
PY="${EDWARD_HARNESS_PYTHON:-$(cd "$FRONTEND/.." && pwd)/backend/.venv/bin/python}"
MOCK_PORT=8100
NEXT_PORT=3100
MOCK_LOG=/tmp/edward-ux-mock.log
NEXT_LOG=/tmp/edward-ux-next.log

listeners() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u; }

kill_port() {
  local port="$1" pids pid
  case "$port" in 3000|8000) echo "refusing to touch production port $port" >&2; return 1;; esac
  pids="$(listeners "$port")"
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    # next dev runs as a child of a parent node process; take the parent too
    # when it is ours (its command line names this worktree or the harness).
    local ppid; ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    kill "$pid" 2>/dev/null || true
    if [ -n "$ppid" ] && [ "$ppid" != "1" ] && ps -o command= -p "$ppid" 2>/dev/null | grep -q -e "next dev -p $port" -e "edward-ux"; then
      kill "$ppid" 2>/dev/null || true
    fi
  done
  for _ in $(seq 1 20); do [ -z "$(listeners "$port")" ] && return 0; sleep 0.25; done
  for pid in $(listeners "$port"); do kill -9 "$pid" 2>/dev/null || true; done
}

stop() {
  kill_port "$NEXT_PORT"
  kill_port "$MOCK_PORT"
  # stray `next dev -p 3100` parents from an earlier run
  pkill -f "next dev -p $NEXT_PORT" 2>/dev/null || true
  echo "harness stopped"
}

status() {
  echo "mock  :$MOCK_PORT -> $(listeners $MOCK_PORT | tr '\n' ' ')"
  echo "next  :$NEXT_PORT -> $(listeners $NEXT_PORT | tr '\n' ' ')"
}

wait_for() {  # url seconds label
  local url="$1" secs="$2" label="$3" i code
  for i in $(seq 1 "$secs"); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$url" || true)"
    if [ "$code" = "200" ]; then echo "$label ready ($url)"; return 0; fi
    sleep 1
  done
  echo "$label did not come up: $url (last HTTP $code) — see logs" >&2
  return 1
}

start_mock() {
  kill_port "$MOCK_PORT"
  cd "$FRONTEND"
  nohup "$PY" "$HERE/server.py" --port "$MOCK_PORT" >"$MOCK_LOG" 2>&1 &
  wait_for "http://127.0.0.1:$MOCK_PORT/__mock/health" 30 "mock API" || exit 1
}

# Refuse the checkout that is serving :3000 (production's `next start`):
# even with its own build dir, a dev server there shares node_modules caches,
# public/ and the source the owner's phone is running. Use a git worktree instead.
refuse_production_checkout() {
  local pid cwd
  pid="$(lsof -nP -iTCP:3000 -sTCP:LISTEN -t 2>/dev/null | head -1)"
  [ -n "$pid" ] || return 0
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  if [ -n "$cwd" ] && [ "$(cd "$cwd" && pwd -P)" = "$(cd "$FRONTEND" && pwd -P)" ]; then
    echo "run-harness: refusing — this checkout is serving :3000 (production). Run it from a git worktree." >&2
    exit 2
  fi
}

start() {
  refuse_production_checkout
  stop >/dev/null
  start_mock

  # Its own build dir and no next-pwa: `next dev` must never write the .next
  # that `next start` serves or regenerate public/sw.js (2026-09-26).
  NEXT_DIST_DIR=.next-harness EDWARD_HARNESS=1 \
  NEXT_PUBLIC_API_URL="http://localhost:$MOCK_PORT" NEXT_TELEMETRY_DISABLED=1 \
    nohup ./node_modules/.bin/next dev -p "$NEXT_PORT" >"$NEXT_LOG" 2>&1 &
  wait_for "http://127.0.0.1:$NEXT_PORT/login" 240 "next dev" || exit 1
  # Warm the pages testers hit first (dev compiles each route on first visit).
  for p in /today /chat /settings; do
    curl -s -o /dev/null --max-time 180 "http://127.0.0.1:$NEXT_PORT$p" || true
  done
  echo "harness up: http://localhost:$NEXT_PORT/today"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  mock) start_mock ;;
  reset) curl -s -X POST "http://127.0.0.1:$MOCK_PORT/__mock/reset"; echo ;;
  *) echo "usage: $0 [start|stop|status|reset|mock]" >&2; exit 2 ;;
esac
