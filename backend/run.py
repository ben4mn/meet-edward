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
        # Create SelectorEventLoop explicitly and run the server on it.
        # uvicorn.run() and asyncio.run() both create ProactorEventLoop
        # on Windows which psycopg cannot use.
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
    else:
        server.run()
