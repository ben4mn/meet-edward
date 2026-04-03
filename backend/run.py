"""Windows-compatible runner that forces SelectorEventLoop for psycopg."""
import os
import sys
import asyncio
import selectors
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

if __name__ == "__main__":
    config = uvicorn.Config(
        "main:app",
        host="0.0.0.0",
        port=8000,
        loop="none",
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
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
