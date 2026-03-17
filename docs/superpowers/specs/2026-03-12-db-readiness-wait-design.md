# Design: PostgreSQL Readiness Wait on Startup

**Date:** 2026-03-12
**Status:** Approved
**Scope:** `backend/run.py` only

## Problem

On Windows reboot, Task Scheduler starts the backend immediately after login. Docker Desktop takes 1-2 minutes to appear, and the PostgreSQL container needs another 10-20s after that. The backend's `init_db()` fires before PostgreSQL is ready, crashes the lifespan, and WhatsApp bridge (and everything else) never initializes. User has to manually restart the backend.

## Solution

Add a synchronous TCP port poll to `run.py` that blocks before uvicorn starts, waiting until PostgreSQL is reachable.

## Design

### Single function: `_wait_for_postgres_from_url(url, timeout_s, interval_s)`

Added to `run.py`, called inside the `if sys.platform == "win32":` branch, before `loop.run_until_complete(server.serve())`.

**Parameters:**
- `url` — the full `DATABASE_URL` string; `host` and `port` are extracted internally via `urllib.parse.urlparse`, defaulting to `localhost` and `5432` if absent
- `timeout_s = 300` — 5 minutes total wait
- `interval_s = 2` — poll every 2 seconds

**Behavior:**
1. Parse `host` and `port` out of `url` using `urllib.parse.urlparse`; fall back to `localhost:5432` if not present
2. Loop: attempt `socket.create_connection((host, port), timeout=1)`
3. On success: print `[Startup] PostgreSQL ready at {host}:{port}` and return
4. On failure: print `[Startup] Waiting for PostgreSQL at {host}:{port}… ({elapsed}s elapsed)` and sleep `interval_s`
5. If `timeout_s` exceeded: print `[Startup] PostgreSQL not reachable at {host}:{port} after {timeout_s}s. Is Docker running? Exiting.` then `sys.exit(1)`

**Note:** TCP port open does not guarantee PostgreSQL has finished initializing (WAL replay can take 1-3s after the port binds). In practice the 2s poll interval absorbs this window. If `init_db()` still fails after the wait, it raises normally and the lifespan crash is the fallback.

### Placement: inside `win32` branch only

The call is placed inside `if sys.platform == "win32":` — the problem is Windows-specific (Task Scheduler + Docker Desktop). On macOS, `DATABASE_URL` may use a Unix socket path with no TCP port, so the poll is skipped entirely on non-Windows platforms.

### No other files change

`init_db()`, `lifespan()`, `main.py`, and all services remain identical. They simply run after the DB is confirmed reachable.

### Call site in `run.py`

```python
if sys.platform == "win32":
    _wait_for_postgres_from_url(DATABASE_URL)  # block until DB ready
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    # ... rest of existing win32 startup
```

## Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Timeout | 300s (5 min) | Docker Desktop ~1-2min + PostgreSQL init ~10-20s + headroom |
| Interval | 2s | Low CPU overhead, responsive enough |
| Socket timeout | 1s | Fast failure per attempt |

## Error handling

- `sys.exit(1)` on timeout — loud failure captured by Task Scheduler logs
- URL parse failure falls back to `localhost:5432` defaults rather than crashing

## Files changed

| File | Change |
|------|--------|
| `backend/run.py` | Add `_wait_for_postgres_from_url()` function + call before uvicorn |
