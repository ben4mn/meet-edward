"""
WhatsApp Bridge client for Edward.

Connects to the custom Baileys bridge server via REST API and manages it
as a subprocess. Replaces MCP-based WhatsApp tool integration.

The bridge provides:
  - Real-time @edward mention detection via webhook push
  - REST API for sending messages, reading chats, listing contacts/groups
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx

MCP_WHATSAPP_ENABLED = os.getenv("MCP_WHATSAPP_ENABLED", "false").lower() == "true"
BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3100")
BRIDGE_PORT = os.getenv("WHATSAPP_BRIDGE_PORT", "3100")

_WATCHDOG_INTERVAL = 30       # seconds between health checks
_WATCHDOG_MAX_RETRIES = 3     # restart attempts before giving up until next cycle
_WATCHDOG_RETRY_DELAYS = [30, 60, 120]  # backoff between retries (seconds)

_client: Optional[httpx.AsyncClient] = None
_initialized = False
_bridge_healthy = False       # runtime health, updated by watchdog
_last_error: Optional[str] = None
_bridge_process: Optional[subprocess.Popen] = None
_watchdog_task: Optional[asyncio.Task] = None


def _bridge_dir() -> Path:
    """Path to the whatsapp-bridge directory."""
    return Path(__file__).resolve().parent.parent.parent / "whatsapp-bridge"


# ── Subprocess management ─────────────────────────────────────────────────────

async def _start_bridge_process() -> bool:
    """Start the Node.js bridge as a subprocess.

    Auto-installs npm dependencies if node_modules is missing.
    Polls /health until the bridge is ready (30s timeout).
    """
    global _bridge_process

    bridge = _bridge_dir()
    if not bridge.exists():
        print("[WhatsApp Bridge] Bridge directory not found, skipping")
        return False

    index_js = bridge / "index.js"
    if not index_js.exists():
        print("[WhatsApp Bridge] index.js not found, skipping")
        return False

    # Auto-install deps
    node_modules = bridge / "node_modules"
    if not node_modules.exists():
        print("[WhatsApp Bridge] Installing npm dependencies...")
        try:
            install = await asyncio.to_thread(
                subprocess.run,
                ["npm", "install"],
                cwd=str(bridge),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if install.returncode != 0:
                print(f"[WhatsApp Bridge] npm install failed: {install.stderr[:500]}")
                return False
            print("[WhatsApp Bridge] npm install complete")
        except Exception as e:
            print(f"[WhatsApp Bridge] npm install error: {e}")
            return False

    # Kill any existing process on the bridge port (stale from previous run)
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{BRIDGE_PORT}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != os.getpid():
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True, timeout=5)
                        print(f"[WhatsApp Bridge] Killed stale process on port {BRIDGE_PORT} (PID {pid})")
        else:
            subprocess.run(
                ["fuser", "-k", f"{BRIDGE_PORT}/tcp"],
                capture_output=True, timeout=5
            )
    except Exception:
        pass  # Best-effort cleanup

    # Spawn bridge process
    env = {**os.environ, "WHATSAPP_BRIDGE_PORT": BRIDGE_PORT}
    # On Windows, shell=False with node directly
    node_cmd = "node"
    if sys.platform == "win32":
        # Ensure node is found on Windows
        node_cmd = "node.exe"

    try:
        _bridge_process = subprocess.Popen(
            [node_cmd, "index.js"],
            cwd=str(bridge),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        print(f"[WhatsApp Bridge] Subprocess started (PID {_bridge_process.pid})")
    except Exception as e:
        print(f"[WhatsApp Bridge] Failed to start subprocess: {e}")
        return False

    # Start a background task to forward bridge stdout to Python console
    asyncio.create_task(_pipe_output())

    # Poll /health until ready
    temp_client = httpx.AsyncClient(base_url=BRIDGE_URL, timeout=5.0)
    try:
        for _ in range(30):
            await asyncio.sleep(1)
            # Check if process died
            if _bridge_process.poll() is not None:
                print(f"[WhatsApp Bridge] Subprocess exited with code {_bridge_process.returncode}")
                return False
            try:
                resp = await temp_client.get("/health")
                if resp.status_code == 200:
                    print("[WhatsApp Bridge] Subprocess ready")
                    return True
            except httpx.ConnectError:
                pass
            except Exception:
                pass
        print("[WhatsApp Bridge] Subprocess did not become ready within 30s")
        return False
    finally:
        await temp_client.aclose()


async def _pipe_output():
    """Forward bridge subprocess stdout to Python print (non-blocking)."""
    proc = _bridge_process
    if not proc or not proc.stdout:
        return
    try:
        while True:
            line = await asyncio.to_thread(proc.stdout.readline)
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                print(text)
    except Exception:
        pass


async def _stop_bridge_process():
    """Terminate the bridge subprocess."""
    global _bridge_process

    if _bridge_process is None:
        return

    try:
        _bridge_process.terminate()
        await asyncio.to_thread(_bridge_process.wait, timeout=10)
    except subprocess.TimeoutExpired:
        _bridge_process.kill()
    except Exception as e:
        print(f"[WhatsApp Bridge] Error stopping subprocess: {e}")
    finally:
        _bridge_process = None
        print("[WhatsApp Bridge] Subprocess stopped")


# ── Watchdog ──────────────────────────────────────────────────────────────────

async def _watchdog_loop():
    """Background task: poll /health every 30s, restart on failure, push-notify on state change."""
    global _bridge_healthy, _last_error, _initialized

    # Lazy import to avoid circular dependency (push_service → tools → bridge)
    from backend.services.push_service import send_push_notification

    async def _notify(title: str, body: str):
        try:
            await send_push_notification(title, body, tag="whatsapp-bridge")
        except Exception as e:
            print(f"[WhatsApp Bridge] Push notify failed: {e}")

    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL)

        if not MCP_WHATSAPP_ENABLED or not _initialized:
            continue

        # Health check
        alive = False
        try:
            resp = await _client.get("/health", timeout=5.0)
            alive = resp.status_code == 200
        except Exception:
            alive = False

        if alive:
            if not _bridge_healthy:
                # Recovered from a previous drop
                print("[WhatsApp Bridge] Watchdog: bridge recovered")
                _bridge_healthy = True
                _last_error = None
                await _notify("✅ WhatsApp bridge recovered", "WhatsApp bridge is back online.")
            continue

        # Bridge is down
        if _bridge_healthy:
            print("[WhatsApp Bridge] Watchdog: bridge is down, attempting restart")
            _bridge_healthy = False
            await _notify("⚠️ WhatsApp bridge dropped", "Attempting to restart automatically…")

        # Retry loop with backoff
        restarted = False
        for attempt, delay in enumerate(_WATCHDOG_RETRY_DELAYS, start=1):
            print(f"[WhatsApp Bridge] Watchdog restart attempt {attempt}/{_WATCHDOG_MAX_RETRIES}")
            started = await _start_bridge_process()
            if started:
                _bridge_healthy = True
                _last_error = None
                print("[WhatsApp Bridge] Watchdog: restart succeeded")
                await _notify("✅ WhatsApp bridge restarted", f"Reconnected after {attempt} attempt(s).")
                restarted = True
                break
            print(f"[WhatsApp Bridge] Watchdog: attempt {attempt} failed, waiting {delay}s")
            await asyncio.sleep(delay)

        if not restarted:
            _last_error = "Bridge crashed and could not be restarted automatically"
            print(f"[WhatsApp Bridge] Watchdog: all restart attempts failed")
            await _notify(
                "❌ WhatsApp bridge offline",
                "Could not restart automatically. Manual intervention needed.",
            )


# ── Initialization ────────────────────────────────────────────────────────────

async def initialize_bridge() -> bool:
    """Start the bridge subprocess and initialize the HTTP client."""
    global _client, _initialized, _last_error, _bridge_healthy, _watchdog_task

    if not MCP_WHATSAPP_ENABLED:
        _last_error = "Disabled via MCP_WHATSAPP_ENABLED=false"
        return False

    if _initialized:
        return True

    # Check if bridge is already running (e.g. started manually in another terminal)
    _client = httpx.AsyncClient(base_url=BRIDGE_URL, timeout=15.0)
    try:
        resp = await _client.get("/health")
        if resp.status_code == 200:
            print("[WhatsApp Bridge] Found existing bridge already running")
            _initialized = True
            _bridge_healthy = True
            _last_error = None
            _watchdog_task = asyncio.create_task(_watchdog_loop())
            return True
    except Exception:
        pass  # Not running yet, we'll start it

    # Start subprocess
    started = await _start_bridge_process()
    if not started:
        _last_error = "Bridge subprocess failed to start"
        await _client.aclose()
        _client = None
        return False

    # Check connection status
    try:
        resp = await _client.get("/status")
        data = resp.json()
        if data.get("connected"):
            _initialized = True
            _bridge_healthy = True
            _last_error = None
            user = data.get("user") or {}
            print(f"[WhatsApp Bridge] Connected as {user.get('name', 'unknown')} ({user.get('id', '')})")
        else:
            # Bridge is running but WhatsApp not yet connected (may need QR scan)
            _initialized = True
            _bridge_healthy = True
            _last_error = None
            print("[WhatsApp Bridge] Running but WhatsApp not yet connected (scan QR code)")
        _watchdog_task = asyncio.create_task(_watchdog_loop())
        return True
    except Exception as e:
        _last_error = str(e)
        print(f"[WhatsApp Bridge] Status check failed: {e}")
        return False


async def shutdown_bridge():
    """Shutdown the HTTP client and bridge subprocess."""
    global _client, _initialized, _bridge_healthy, _watchdog_task

    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None

    if _client:
        await _client.aclose()
        _client = None

    await _stop_bridge_process()
    _initialized = False
    _bridge_healthy = False
    print("[WhatsApp Bridge] Shutdown complete")


# ── Status ────────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Check if bridge is initialized (may still be awaiting QR scan)."""
    return _initialized


def get_status() -> dict:
    """Get bridge status for skills service."""
    if not MCP_WHATSAPP_ENABLED:
        return {
            "status": "error",
            "status_message": "Set MCP_WHATSAPP_ENABLED=true in environment",
            "metadata": None,
        }
    if not _initialized:
        return {
            "status": "error",
            "status_message": _last_error or "Not initialized",
            "metadata": None,
        }
    if not _bridge_healthy:
        return {
            "status": "error",
            "status_message": _last_error or "Bridge process is down",
            "metadata": None,
        }
    return {
        "status": "connected",
        "status_message": "WhatsApp bridge running",
        "metadata": {"bridge_url": BRIDGE_URL},
    }


# ── REST API wrappers ─────────────────────────────────────────────────────────

async def send_message(chat_id: str, message: str) -> dict:
    """Send a WhatsApp message via the bridge."""
    resp = await _client.post("/send", json={"chat_id": chat_id, "message": message})
    resp.raise_for_status()
    return resp.json()


async def get_chat_messages(chat_id: str, limit: int = 15) -> list:
    """Get messages from a specific chat."""
    resp = await _client.get(f"/chats/{chat_id}/messages", params={"limit": limit})
    resp.raise_for_status()
    return resp.json()


async def get_contacts() -> list:
    """List all contacts."""
    resp = await _client.get("/contacts")
    resp.raise_for_status()
    return resp.json()


async def get_groups() -> list:
    """List all groups."""
    resp = await _client.get("/groups")
    resp.raise_for_status()
    return resp.json()


async def get_recent_chats(limit: int = 30) -> list:
    """List recent chats."""
    resp = await _client.get("/chats", params={"limit": limit})
    resp.raise_for_status()
    return resp.json()


async def resolve_lid(lid_jid: str) -> str:
    """Resolve an @lid JID to @s.whatsapp.net via the bridge.

    Returns the original JID if not an @lid or no mapping exists.
    """
    if not lid_jid or not lid_jid.endswith("@lid"):
        return lid_jid

    try:
        resp = await _client.get(f"/resolve-lid/{lid_jid}")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("was_resolved"):
                return data["resolved"]
    except Exception as e:
        print(f"[WhatsApp Bridge] LID resolution failed for {lid_jid}: {e}")

    return lid_jid
