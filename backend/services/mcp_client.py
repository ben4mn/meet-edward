"""
MCP (Model Context Protocol) client for messaging integrations.

Manages MCP server subprocesses for WhatsApp and Apple Services.
Uses langchain-mcp-adapters for the underlying client + transport, but
wraps each server in an MCPSessionHolder so the subprocess stays warm
across many tool calls (T1). Without the holder, every call cold-starts
a fresh subprocess — apple-mcp's bun runtime takes ~5s per cold start.
"""

import asyncio
import os
from contextvars import ContextVar
from typing import Optional, List, Any

from services.mcp_session_holder import MCPSessionHolder, mcp_call_timeout_seconds

# MCP configuration — WhatsApp
MCP_WHATSAPP_ENABLED = os.getenv("MCP_WHATSAPP_ENABLED", "false").lower() == "true"
MCP_WHATSAPP_SERVER_DIR = os.getenv("MCP_WHATSAPP_SERVER_DIR", "")
# The Go bridge the MCP server talks to (its own LaunchAgent). Paired means it
# listens here and its session store holds a linked device (2026-10-11: the
# MCP server listed its tools for months while the bridge sat unpaired).
# ADAPT: WhatsApp goes through the whatsapp-mcp project's Go bridge, run outside Edward.
#   How it works here: the bridge runs from its own LaunchAgent on 127.0.0.1:8080, logs to
#   /tmp/whatsapp-bridge.log (where its pairing QR appears) and is paired once by scanning that QR.
#   To adapt: install whatsapp-mcp, run its bridge (a LaunchAgent or by hand), scan the QR, then set
#   MCP_WHATSAPP_ENABLED=true, MCP_WHATSAPP_SERVER_DIR=<its whatsapp-mcp-server dir> and, if it logs
#   elsewhere, MCP_WHATSAPP_BRIDGE_LOG.
WHATSAPP_BRIDGE_ADDR = ("127.0.0.1", 8080)
WHATSAPP_BRIDGE_CONNECT_S = 0.3
WHATSAPP_STORE_FRESH_S = 48 * 3600
WHATSAPP_BRIDGE_LOG = os.getenv("MCP_WHATSAPP_BRIDGE_LOG") or "/tmp/whatsapp-bridge.log"

# MCP configuration — Apple Services (unified: Calendar, Reminders, Notes, Mail, Contacts, Maps, Messages)
MCP_APPLE_ENABLED = os.getenv("MCP_APPLE_ENABLED", "false").lower() == "true"
# ADAPT: apple-mcp is the jxnl/apple-mcp fork (its calendar code works), run from a local clone with bun.
#   How it works here: ``bun run index.ts`` in ~/apple-mcp over stdio. The original build carried an
#   optional local patch to utils/calendar.ts that removed noisy console.error lines. Calendar, Contacts
#   and Reminders are read from their own SQLite databases first; apple-mcp is only the fallback.
#   To adapt: install bun, clone jxnl/apple-mcp, ``bun install`` there, then set MCP_APPLE_ENABLED=true and,
#   if they live elsewhere, BUN_PATH (default ~/.bun/bin/bun) and MCP_APPLE_DIR (default ~/apple-mcp).
DEFAULT_BUN_PATH = "~/.bun/bin/bun"
DEFAULT_APPLE_MCP_DIR = "~/apple-mcp"


def apple_mcp_command() -> tuple[str, str]:
    """``(bun, apple-mcp checkout)``: ``BUN_PATH`` and ``MCP_APPLE_DIR``, read per call."""
    bun = os.path.expanduser((os.getenv("BUN_PATH") or "").strip() or DEFAULT_BUN_PATH)
    directory = os.path.expanduser((os.getenv("MCP_APPLE_DIR") or "").strip() or DEFAULT_APPLE_MCP_DIR)
    return bun, directory

# Global client state — WhatsApp
_whatsapp_mcp_client = None
_whatsapp_mcp_tools: List[Any] = []
_whatsapp_initialized = False
_whatsapp_last_error: Optional[str] = None
_whatsapp_holder: Optional[MCPSessionHolder] = None

# Global client state — Apple Services (unified)
_apple_mcp_client = None
_apple_mcp_tools: List[Any] = []
_apple_initialized = False
_apple_last_error: Optional[str] = None
_apple_holder: Optional[MCPSessionHolder] = None


def _build_langchain_tools_from_holder(
    holder: MCPSessionHolder, name_prefix: str = ""
) -> List[Any]:
    """Wrap each tool exposed by the holder as a LangChain StructuredTool.

    args_schema is set to the raw inputSchema dict (not a Pydantic model)
    so codex_chat_model._convert_tools picks up the real argument names —
    see test_codex_tool_schema_from_dict. Each tool's coroutine routes
    through the holder, so all calls reuse the single open session.
    """
    from langchain_core.tools import StructuredTool

    tools = []
    for spec in holder.tools:
        tool_name = (name_prefix + spec["name"]) if name_prefix else spec["name"]

        def _make_runner(real_name: str):
            async def _run(**kwargs):
                return await _bounded_holder_call(holder, real_name, kwargs, _CALL_BOUND.get())
            return _run

        tool = StructuredTool.from_function(
            coroutine=_make_runner(spec["name"]),
            name=tool_name,
            description=spec.get("description", "") or "",
        )
        # Stash the raw inputSchema where _convert_tools and the chat
        # path will read it. Pydantic's auto-generated args_schema
        # would lose the property names.
        tool.args_schema = spec.get("inputSchema") or {"type": "object", "properties": {}}
        tools.append(tool)
    return tools


# ============================================================================
# BOUNDS (2026-09-24)
# ============================================================================
#
# Every MCP call is bounded: ``MCP_CALL_TIMEOUT_SECONDS`` (30 s) by default,
# in the caller *and* in the holder's worker, so one wedged apple-mcp call
# can no longer hold the worker — and every call queued behind it — until a
# restart (it froze every wake's pack through the calendar). A caller that
# needs a shorter bound (the calendar list, 10 s) uses ``call_tool_bounded``;
# the bound reaches the worker through a context variable.

_CALL_BOUND: "ContextVar[Optional[float]]" = ContextVar("mcp_call_bound", default=None)
# Grace on top of a holder's own bound: the holder raises first with the
# clearer message; this only catches a holder that never answers at all.
_WRAPPER_GRACE_SECONDS = 5.0


def _grace(bound: float) -> float:
    return min(_WRAPPER_GRACE_SECONDS, max(0.05, bound * 0.25))


async def _bounded_holder_call(holder: MCPSessionHolder, real_name: str, args: dict,
                               timeout: Optional[float]) -> str:
    """One MCP call through the holder, bounded twice: the holder bounds the
    call in the caller and in its worker; this bounds the holder itself.
    A timeout raises ``TimeoutError`` naming the server and the tool —
    the tool executor turns it into ``Tool error: …``; the calendar and the
    listeners already treat any exception as "unavailable"."""
    bound = float(timeout) if timeout else float(getattr(holder, "_call_timeout_seconds", 0) or mcp_call_timeout_seconds())
    try:
        return await asyncio.wait_for(holder.call_tool(real_name, args, timeout=bound), timeout=bound + _grace(bound))
    except asyncio.TimeoutError as exc:
        text = str(exc) or f"timeout after {bound:.0f}s — {getattr(holder, '_server_name', 'mcp')}.{real_name} hung"
        raise TimeoutError(text) from exc


async def call_tool_bounded(tool: Any, args: dict, *, timeout: float) -> Any:
    """Invoke an MCP-backed LangChain tool with a bound of ``timeout``
    seconds that reaches the holder's worker too. A tool that was not built
    from a holder (a fake in a test) is bounded from outside."""
    token = _CALL_BOUND.set(float(timeout))
    try:
        return await asyncio.wait_for(tool.ainvoke(args), timeout=float(timeout) + _grace(float(timeout)))
    except asyncio.TimeoutError as exc:
        text = str(exc) or f"timeout after {timeout:.0f}s — {getattr(tool, 'name', 'mcp tool')} hung"
        raise TimeoutError(text) from exc
    finally:
        _CALL_BOUND.reset(token)


# ============================================================================
# ORPHAN SWEEP
# ============================================================================
#
# Every backend restart through launchd (``kickstart -k``) SIGKILLs uvicorn;
# the stdio servers it spawned are re-parented to launchd and live on. On
# 2026-09-21 eight whatsapp-mcp chains from Aug 22 to Sep 20 were running
# beside the live one. Startup sweeps servers whose ancestry does not reach
# this process. The Go WhatsApp bridge has its own LaunchAgent and is not a
# server we spawn, so it is not in the marker list.

MCP_SERVER_MARKERS: tuple[str, ...] = ("whatsapp-mcp-server", "apple-mcp")


def parse_ps_output(text: str) -> List[tuple[int, int, str]]:
    """``ps -axo pid,ppid,command`` → ``[(pid, ppid, command)]``, header skipped."""
    rows: List[tuple[int, int, str]] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def orphaned_mcp_server_pids(rows: List[tuple[int, int, str]], own_pid: int) -> set[int]:
    """MCP server processes (by command-line marker) left behind by an
    earlier backend: their parent chain climbs through MCP server processes
    only and ends at launchd (pid 1), never reaching ``own_pid``.

    A server whose chain reaches any other process belongs to someone else
    and is spared: another app on this Mac (Claude Desktop, an editor) may
    run apple-mcp or whatsapp-mcp too, and a second Edward on the same Mac
    must not kill the first one's servers."""
    parent = {pid: ppid for pid, ppid, _ in rows}
    is_server = {pid for pid, _ppid, cmd in rows if any(m in cmd for m in MCP_SERVER_MARKERS)}

    def abandoned(pid: int) -> bool:
        seen = set()
        cur = parent.get(pid, 0)
        while cur and cur not in seen:
            if cur == own_pid:
                return False
            if cur == 1:
                return True
            if cur not in is_server:
                return False
            seen.add(cur)
            cur = parent.get(cur, 0)
        return False

    return {pid for pid in is_server if pid != own_pid and abandoned(pid)}


def sweep_orphaned_mcp_servers() -> List[int]:
    """Terminate MCP servers an earlier backend left behind. Returns the pids signalled."""
    import os
    import signal
    import subprocess

    try:
        out = subprocess.run(["ps", "-axo", "pid,ppid,command"], capture_output=True, text=True, timeout=10).stdout
    except Exception as exc:  # noqa: BLE001
        print(f"MCP orphan sweep skipped: ps failed ({exc})")
        return []
    orphans = sorted(orphaned_mcp_server_pids(parse_ps_output(out), own_pid=os.getpid()))
    killed: List[int] = []
    for pid in orphans:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"MCP orphan sweep: could not signal {pid}: {exc}")
    if killed:
        print(f"MCP orphan sweep: terminated {len(killed)} server process(es) left by an earlier backend: {killed}")
    return killed


# ============================================================================
# WHATSAPP MCP
# ============================================================================

async def initialize_whatsapp_mcp() -> bool:
    """
    Initialize the WhatsApp MCP client.

    Connects to the whatsapp-mcp Python server (which talks to the Go bridge).

    Returns:
        True if initialization succeeded, False otherwise.
    """
    global _whatsapp_mcp_client, _whatsapp_mcp_tools, _whatsapp_initialized, _whatsapp_last_error

    if not MCP_WHATSAPP_ENABLED:
        _whatsapp_last_error = "Disabled via MCP_WHATSAPP_ENABLED=false"
        return False

    if not MCP_WHATSAPP_SERVER_DIR:
        _whatsapp_last_error = "MCP_WHATSAPP_SERVER_DIR not set"
        return False

    if _whatsapp_initialized:
        return True

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        _whatsapp_mcp_client = MultiServerMCPClient({
            "whatsapp": {
                "command": "uv",
                "args": ["--directory", MCP_WHATSAPP_SERVER_DIR, "run", "main.py"],
                "transport": "stdio"
            }
        })

        global _whatsapp_holder
        _whatsapp_holder = MCPSessionHolder(_whatsapp_mcp_client, "whatsapp")
        await _whatsapp_holder.start()
        _whatsapp_mcp_tools = _build_langchain_tools_from_holder(
            _whatsapp_holder, name_prefix="whatsapp_"
        )
        _whatsapp_initialized = True
        _whatsapp_last_error = None

        print(f"WhatsApp MCP client initialized with {len(_whatsapp_mcp_tools)} tools (long-lived session)")
        for tool in _whatsapp_mcp_tools:
            desc = tool.description[:50] if tool.description else "No description"
            print(f"  - {tool.name}: {desc}...")

        return True

    except ImportError:
        _whatsapp_last_error = "langchain-mcp-adapters not installed"
        print("langchain-mcp-adapters not installed. WhatsApp MCP disabled.")
        return False
    except Exception as e:
        _whatsapp_last_error = str(e)
        print(f"Failed to initialize WhatsApp MCP client: {e}")
        return False


async def shutdown_whatsapp_mcp():
    """Shutdown the WhatsApp MCP client."""
    global _whatsapp_mcp_client, _whatsapp_mcp_tools, _whatsapp_initialized, _whatsapp_holder

    if _whatsapp_holder is not None:
        try:
            await _whatsapp_holder.stop()
        except Exception as e:
            print(f"Error stopping WhatsApp MCP session holder: {e}")
        finally:
            _whatsapp_holder = None

    if _whatsapp_mcp_client is not None:
        _whatsapp_mcp_client = None
    _whatsapp_mcp_tools = []
    _whatsapp_initialized = False


def is_whatsapp_available() -> bool:
    """Check if WhatsApp MCP is available."""
    return _whatsapp_initialized and len(_whatsapp_mcp_tools) > 0


def get_whatsapp_mcp_tools() -> List[Any]:
    """Get the WhatsApp MCP tools for binding to the LLM."""
    return _whatsapp_mcp_tools


def get_whatsapp_status() -> dict:
    """
    Get the current status of the WhatsApp MCP service.

    Returns:
        Dict with status info: status, status_message, metadata
    """
    if not MCP_WHATSAPP_ENABLED:
        return {
            "status": "error",
            "status_message": "Set MCP_WHATSAPP_ENABLED=true in environment",
            "metadata": None
        }

    if not MCP_WHATSAPP_SERVER_DIR:
        return {
            "status": "error",
            "status_message": "Set MCP_WHATSAPP_SERVER_DIR to whatsapp-mcp server path",
            "metadata": None
        }

    if not _whatsapp_initialized:
        if _whatsapp_last_error:
            return {
                "status": "error",
                "status_message": _whatsapp_last_error,
                "metadata": None
            }
        return {
            "status": "connecting",
            "status_message": "Not yet initialized",
            "metadata": None
        }

    if len(_whatsapp_mcp_tools) == 0:
        return {
            "status": "error",
            "status_message": "No tools available from WhatsApp MCP server",
            "metadata": None
        }

    problem = _whatsapp_bridge_problem()
    if problem:
        return {"status": "error", "status_message": problem, "metadata": None}

    return {
        "status": "connected",
        "status_message": f"{len(_whatsapp_mcp_tools)} tools available (bridge paired)",
        "metadata": {"tools_count": len(_whatsapp_mcp_tools)}
    }


def _whatsapp_linked_devices(session_store) -> Optional[int]:
    """How many devices the bridge's session store (whatsmeow's
    ``whatsmeow_device``, read-only) holds, or None when it can't be read."""
    import sqlite3

    if not session_store.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{session_store}?mode=ro", uri=True, timeout=1)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM whatsmeow_device").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _whatsapp_bridge_problem() -> Optional[str]:
    """What is wrong with the bridge, said as it is, or None when it is
    paired: listening on ``WHATSAPP_BRIDGE_ADDR`` within 0.3 s, and a linked
    device in its session store (``store/whatsapp.db`` beside the MCP
    server's directory). Only when the session store can't be read does the
    message store stand in — changed within 48 h reads as paired, and an
    older one is said as quiet, not as unpaired (an account can be quiet).
    Blocking: async callers run it in a thread."""
    import socket
    import time
    from pathlib import Path

    host, port = WHATSAPP_BRIDGE_ADDR[0], WHATSAPP_BRIDGE_ADDR[1]
    try:
        socket.create_connection((host, port), timeout=WHATSAPP_BRIDGE_CONNECT_S).close()
    except OSError:
        return f"the bridge isn't running (nothing listening on {host}:{port}) — see {WHATSAPP_BRIDGE_LOG}, or turn this off"
    store = Path(MCP_WHATSAPP_SERVER_DIR).expanduser().parent / "whatsapp-bridge" / "store"
    devices = _whatsapp_linked_devices(store / "whatsapp.db")
    if devices == 0:
        return f"not paired — the bridge has no linked device; scan the QR in {WHATSAPP_BRIDGE_LOG} or turn this off"
    if devices:
        return None
    stamps = []
    for path in (store / "messages.db", store / "messages.db-wal"):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    if not stamps:
        return f"can't tell whether the bridge is paired — it has no session or message store; see {WHATSAPP_BRIDGE_LOG}"
    if time.time() - max(stamps) > WHATSAPP_STORE_FRESH_S:
        return (f"quiet for 48 h (paired?) — the bridge's session store isn't readable and no message has arrived "
                f"since; see {WHATSAPP_BRIDGE_LOG}")
    return None


# ============================================================================
# APPLE SERVICES MCP (unified: Calendar, Reminders, Notes, Mail, Contacts, Maps, Messages)
# ============================================================================

async def initialize_apple_mcp() -> bool:
    """
    Initialize the unified Apple Services MCP client.

    Uses the 'apple-mcp' package which provides access to:
    - Calendar
    - Reminders
    - Notes
    - Mail (Apple Mail.app)
    - Contacts
    - Maps

    Returns:
        True if initialization succeeded, False otherwise.
    """
    global _apple_mcp_client, _apple_mcp_tools, _apple_initialized, _apple_last_error

    if not MCP_APPLE_ENABLED:
        _apple_last_error = "Disabled via MCP_APPLE_ENABLED=false"
        return False

    if _apple_initialized:
        return True

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        # The jxnl/apple-mcp fork with working calendar code (requires bun)
        bun_path, apple_mcp_dir = apple_mcp_command()
        if not os.path.isfile(bun_path):
            _apple_last_error = f"bun not found at {bun_path} — install bun or set BUN_PATH"
            print(f"Apple Services MCP disabled: {_apple_last_error}")
            return False
        if not os.path.isfile(os.path.join(apple_mcp_dir, "index.ts")):
            _apple_last_error = f"apple-mcp not found at {apple_mcp_dir} — clone jxnl/apple-mcp there or set MCP_APPLE_DIR"
            print(f"Apple Services MCP disabled: {_apple_last_error}")
            return False
        _apple_mcp_client = MultiServerMCPClient({
            "apple": {
                "command": bun_path,
                "args": ["run", "index.ts"],
                "transport": "stdio",
                "cwd": apple_mcp_dir
            }
        })

        global _apple_holder
        _apple_holder = MCPSessionHolder(_apple_mcp_client, "apple")
        await _apple_holder.start()
        _apple_mcp_tools = _build_langchain_tools_from_holder(_apple_holder)
        _apple_initialized = True
        _apple_last_error = None

        print(f"Apple Services MCP client initialized with {len(_apple_mcp_tools)} tools (long-lived session)")
        for tool in _apple_mcp_tools:
            desc = tool.description[:50] if tool.description else "No description"
            print(f"  - {tool.name}: {desc}...")

        return True

    except ImportError:
        _apple_last_error = "langchain-mcp-adapters not installed"
        print("langchain-mcp-adapters not installed. Apple Services MCP disabled.")
        return False
    except Exception as e:
        _apple_last_error = str(e)
        print(f"Failed to initialize Apple Services MCP client: {e}")
        return False


async def shutdown_apple_mcp():
    """Shutdown the Apple Services MCP client."""
    global _apple_mcp_client, _apple_mcp_tools, _apple_initialized, _apple_holder

    if _apple_holder is not None:
        try:
            await _apple_holder.stop()
        except Exception as e:
            print(f"Error stopping Apple MCP session holder: {e}")
        finally:
            _apple_holder = None

    if _apple_mcp_client is not None:
        _apple_mcp_client = None
    _apple_mcp_tools = []
    _apple_initialized = False


def is_apple_available() -> bool:
    """Check if Apple Services MCP is available."""
    return _apple_initialized and len(_apple_mcp_tools) > 0


def get_apple_mcp_tools() -> List[Any]:
    """Get the Apple Services MCP tools for binding to the LLM."""
    return _apple_mcp_tools


def get_apple_status() -> dict:
    """Get the current status of the Apple Services MCP."""
    if not MCP_APPLE_ENABLED:
        return {
            "status": "error",
            "status_message": "Set MCP_APPLE_ENABLED=true in environment",
            "metadata": None
        }

    if not _apple_initialized:
        if _apple_last_error:
            return {
                "status": "error",
                "status_message": _apple_last_error,
                "metadata": None
            }
        return {
            "status": "connecting",
            "status_message": "Not yet initialized",
            "metadata": None
        }

    if len(_apple_mcp_tools) == 0:
        return {
            "status": "error",
            "status_message": "No tools available from Apple Services MCP server",
            "metadata": None
        }

    return {
        "status": "connected",
        "status_message": f"{len(_apple_mcp_tools)} tools available",
        "metadata": {"tools_count": len(_apple_mcp_tools)}
    }


# Compatibility aliases for old function names
async def initialize_calendar_mcp() -> bool:
    return await initialize_apple_mcp()

async def initialize_notes_mcp() -> bool:
    return await initialize_apple_mcp()

async def initialize_reminders_mcp() -> bool:
    return await initialize_apple_mcp()

async def initialize_mail_mcp() -> bool:
    return await initialize_apple_mcp()

async def shutdown_calendar_mcp():
    pass  # Handled by shutdown_apple_mcp

async def shutdown_notes_mcp():
    pass  # Handled by shutdown_apple_mcp

async def shutdown_reminders_mcp():
    pass  # Handled by shutdown_apple_mcp

async def shutdown_mail_mcp():
    pass  # Handled by shutdown_apple_mcp

def get_calendar_status() -> dict:
    return get_apple_status()

def get_notes_status() -> dict:
    return get_apple_status()

def get_reminders_status() -> dict:
    return get_apple_status()

def get_mail_status() -> dict:
    return get_apple_status()

def is_calendar_available() -> bool:
    return is_apple_available()

def is_notes_available() -> bool:
    return is_apple_available()

def is_reminders_available() -> bool:
    return is_apple_available()

def is_mail_available() -> bool:
    return is_apple_available()

def get_calendar_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()

def get_notes_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()

def get_reminders_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()

def get_mail_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()
