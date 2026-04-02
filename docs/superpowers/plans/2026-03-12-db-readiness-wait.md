# PostgreSQL Readiness Wait Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TCP port poll to `backend/run.py` so the backend waits for PostgreSQL to be reachable before starting uvicorn, preventing startup crashes on Windows reboot when Docker Desktop is still initializing.

**Architecture:** A single synchronous function `_wait_for_postgres_from_url()` is added to `run.py` and called inside the existing `if sys.platform == "win32":` branch before the event loop starts. No other files change.

**Tech Stack:** Python stdlib only — `socket`, `urllib.parse`, `time`, `sys`

---

## Chunk 1: Implement and commit

**Files:**
- Modify: `backend/run.py`

### Task 1: Add the wait function to `run.py`

- [ ] **Step 1: Read the current `run.py`**

Open `backend/run.py` and note the existing structure:
```python
# Load .env ...
# Set default DATABASE_URL ...
import uvicorn

if __name__ == "__main__":
    config = uvicorn.Config(...)
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
    else:
        server.run()
```

- [ ] **Step 2: Add stdlib imports**

At the top of `run.py`, add `socket`, `time`, and `urllib.parse` to the imports. These are all stdlib — no pip install needed.

The existing imports are:
```python
import os
import sys
import asyncio
import selectors
from pathlib import Path
```

Add after `selectors`:
```python
import socket
import time
import urllib.parse
```

- [ ] **Step 3: Add the `_wait_for_postgres_from_url` function**

Add this function after the imports block, before the `if __name__ == "__main__":` block:

```python
def _wait_for_postgres_from_url(url: str, timeout_s: int = 300, interval_s: int = 2) -> None:
    """Block until PostgreSQL is reachable on its TCP port, or exit after timeout.

    Extracts host/port from DATABASE_URL. Falls back to localhost:5432 if the
    URL has no explicit host (e.g. Unix socket URLs on macOS — but this function
    is only called on Windows where Docker always uses TCP).
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    print(f"[Startup] Waiting for PostgreSQL at {host}:{port} (timeout {timeout_s}s)…")
    start = time.monotonic()

    while True:
        elapsed = int(time.monotonic() - start)
        if elapsed >= timeout_s:
            print(
                f"[Startup] PostgreSQL not reachable at {host}:{port} after {timeout_s}s. "
                "Is Docker running? Exiting."
            )
            sys.exit(1)

        try:
            with socket.create_connection((host, port), timeout=1):
                pass
            print(f"[Startup] PostgreSQL ready at {host}:{port} ({elapsed}s elapsed)")
            return
        except OSError:
            print(f"[Startup] Waiting for PostgreSQL at {host}:{port}… ({elapsed}s elapsed)")
            time.sleep(interval_s)
```

- [ ] **Step 4: Call the function inside the `win32` branch**

Modify the `if sys.platform == "win32":` block to call `_wait_for_postgres_from_url` **before** creating the event loop:

Before:
```python
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
```

After:
```python
    if sys.platform == "win32":
        _wait_for_postgres_from_url(os.environ["DATABASE_URL"])  # block until DB ready before starting
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
```

- [ ] **Step 5: Verify the final `run.py` looks correct**

The complete file should look like this:

```python
"""Windows-compatible runner that forces SelectorEventLoop for psycopg."""
import os
import sys
import asyncio
import selectors
import socket
import time
import urllib.parse
from pathlib import Path

# Load .env (check parent dir first, then current dir)
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()  # tries .env in cwd

# Set default DATABASE_URL if not specified
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://edward:edward@localhost:5432/edward"

import uvicorn


def _wait_for_postgres_from_url(url: str, timeout_s: int = 300, interval_s: int = 2) -> None:
    """Block until PostgreSQL is reachable on its TCP port, or exit after timeout.

    Extracts host/port from DATABASE_URL. Falls back to localhost:5432 if the
    URL has no explicit host (e.g. Unix socket URLs on macOS — but this function
    is only called on Windows where Docker always uses TCP).
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    print(f"[Startup] Waiting for PostgreSQL at {host}:{port} (timeout {timeout_s}s)…")
    start = time.monotonic()

    while True:
        elapsed = int(time.monotonic() - start)
        if elapsed >= timeout_s:
            print(
                f"[Startup] PostgreSQL not reachable at {host}:{port} after {timeout_s}s. "
                "Is Docker running? Exiting."
            )
            sys.exit(1)

        try:
            with socket.create_connection((host, port), timeout=1):
                pass
            print(f"[Startup] PostgreSQL ready at {host}:{port} ({elapsed}s elapsed)")
            return
        except OSError:
            print(f"[Startup] Waiting for PostgreSQL at {host}:{port}… ({elapsed}s elapsed)")
            time.sleep(interval_s)


if __name__ == "__main__":
    config = uvicorn.Config(
        "main:app",
        host="0.0.0.0",
        port=8000,
        loop="none",
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        _wait_for_postgres_from_url(os.environ["DATABASE_URL"])  # block until DB ready before starting
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
    else:
        server.run()
```

- [ ] **Step 6: Manual smoke test**

With Docker PostgreSQL running normally, restart the backend:
```powershell
cd backend
.venv\Scripts\Activate.ps1
python run.py
```

Expected output in the first few lines:
```
[Startup] Waiting for PostgreSQL at localhost:5432 (timeout 300s)…
[Startup] PostgreSQL ready at localhost:5432 (0s elapsed)
INFO:     Started server process ...
```

The "ready" message should appear within 1-2 seconds and uvicorn should start normally.

- [ ] **Step 7: Commit**

```bash
git add backend/run.py
git commit -m "fix: wait for PostgreSQL before starting uvicorn on Windows reboot"
```
