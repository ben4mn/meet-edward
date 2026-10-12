"""
Custom MCP Server service for Edward.

Manages lifecycle of MCP servers that Edward adds at runtime.
Servers run as subprocesses via npx/uvx and their tools become
available to Edward immediately.
"""

import asyncio
import json
import os
import shlex
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.database import async_session, CustomMCPServerModel
from sqlalchemy import select


@dataclass
class ServerInstance:
    """In-memory state for a running MCP server."""
    server_id: str
    name: str
    client: Any = None
    tools: List[Any] = field(default_factory=list)
    status: str = "stopped"  # stopped, starting, connected, error
    error: Optional[str] = None


# In-memory registry of running servers
_servers: Dict[str, ServerInstance] = {}

MCP_HANDSHAKE_TIMEOUT_SECONDS = 30
MCP_STDERR_TAIL_LINES = 20
MCP_STDERR_MAX_CHARS = 4000
MCP_RUNTIME_ROOT = Path(
    os.getenv(
        "EDWARD_CUSTOM_MCP_RUNTIME_DIR",
        Path(__file__).resolve().parents[1] / "data" / "mcp-runtime",
    )
)


# What a custom server inherits from the backend (Evolution v2 Phase 0,
# 2026-09-27). It used to be all of ``os.environ`` — the live
# DATABASE_URL, Twilio, VAPID, the JWT secret, the Cloudflare API tokens,
# every model key — handed to code chosen by a package name and restarted
# on every boot. A package runner needs PATH (to find node / uv), HOME
# (npm's and uv's own config and tool installs), the user and the shell,
# TMPDIR, the locale and TERM. No NODE_* or npm_config_* is passed: the
# backend's launchd environment sets none, npx starts from PATH and HOME,
# and the npm / uv / corepack caches are set explicitly below. A server
# that needs a key declares it in its own ``env_vars``.
INHERITED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
INHERITED_ENV_PREFIXES = ("LC_",)


def inherited_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The allowlisted part of ``base`` (default: ``os.environ``).

    It stops a server inheriting the backend's secrets by accident; it
    cannot stop one reading them (review 2026-09-27). A custom server runs
    unsandboxed as the owner's macOS user, so it can still read the live ``.env``,
    ``~/.codex/auth.json`` and ``backend/.jwt_secret`` by path — installing
    one is taint-gated and journaled for that reason."""
    source = os.environ if base is None else base
    return {
        name: value for name, value in source.items()
        if name in INHERITED_ENV or name.startswith(INHERITED_ENV_PREFIXES)
    }


@dataclass
class ServerLaunchSpec:
    """Resolved process launch configuration for an MCP server."""
    connection: Dict[str, Any]
    full_cmd: str


class MCPStartupError(RuntimeError):
    """Raised when an MCP server fails before its tools can be loaded."""


def _ensure_runtime_paths() -> Dict[str, Path]:
    """Create writable runtime directories for external package managers."""
    paths = {
        "root": MCP_RUNTIME_ROOT,
        "uv_cache": MCP_RUNTIME_ROOT / "uv-cache",
        "npm_cache": MCP_RUNTIME_ROOT / "npm-cache",
        "xdg_cache": MCP_RUNTIME_ROOT / "xdg-cache",
        "corepack_home": MCP_RUNTIME_ROOT / "corepack",
        "logs": MCP_RUNTIME_ROOT / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _parse_args_json(raw_args: Optional[str]) -> List[str]:
    """Parse stored JSON args into a CLI-safe string list."""
    if not raw_args:
        return []

    parsed = json.loads(raw_args)
    if not isinstance(parsed, list):
        raise ValueError("Server args must be a JSON array")
    return [str(arg) for arg in parsed]


def _parse_env_json(raw_env: Optional[str]) -> Dict[str, str]:
    """Parse stored JSON env vars into a string dictionary."""
    if not raw_env:
        return {}

    parsed = json.loads(raw_env)
    if not isinstance(parsed, dict):
        raise ValueError("Server env_vars must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def _flatten_exceptions(exc: BaseException) -> List[BaseException]:
    """Return the leaf exceptions from an ExceptionGroup tree."""
    if isinstance(exc, BaseExceptionGroup):
        flattened: List[BaseException] = []
        for sub_exc in exc.exceptions:
            flattened.extend(_flatten_exceptions(sub_exc))
        return flattened
    return [exc]


def _summarize_exception(exc: BaseException) -> tuple[str, str]:
    """Build concise primary and combined summaries for nested exceptions."""
    flattened = _flatten_exceptions(exc)
    summaries: List[str] = []
    seen: set[str] = set()

    for leaf in flattened:
        message = str(leaf).strip() or repr(leaf)
        summary = f"{type(leaf).__name__}: {message}"
        if summary not in seen:
            summaries.append(summary)
            seen.add(summary)

    if not summaries:
        summary = f"{type(exc).__name__}: {exc}"
        return summary, summary

    return summaries[0], "; ".join(summaries)


def _read_stderr_tail(stderr_path: Path) -> str:
    """Read and truncate captured stderr for actionable startup diagnostics."""
    try:
        content = stderr_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""

    snippet = "\n".join(lines[-MCP_STDERR_TAIL_LINES:])
    if len(snippet) > MCP_STDERR_MAX_CHARS:
        snippet = "..." + snippet[-MCP_STDERR_MAX_CHARS:]
    return snippet


def _format_startup_failure(
    server_name: str,
    full_cmd: str,
    exc: BaseException,
    stderr_output: str,
    *,
    timed_out: bool = False,
) -> str:
    """Render a precise startup error instead of an opaque TaskGroup wrapper."""
    primary_error, all_errors = _summarize_exception(exc)
    connection_closed = "Connection closed" in all_errors or "ClosedResourceError" in all_errors

    if timed_out:
        message = (
            f"Server '{server_name}' did not complete MCP handshake within "
            f"{MCP_HANDSHAKE_TIMEOUT_SECONDS}s. Command: {full_cmd}."
        )
    elif stderr_output and connection_closed:
        message = (
            f"Server '{server_name}' exited before completing MCP initialization. "
            f"Command: {full_cmd}."
        )
    else:
        message = f"Server '{server_name}' failed during MCP startup. Command: {full_cmd}."
        if all_errors:
            message += f" Error: {primary_error}."

    if stderr_output:
        message += f" Stderr:\n{stderr_output}"
    elif all_errors and all_errors != primary_error:
        message += f" Details: {all_errors}"

    return message


def _build_server_launch_spec(server: CustomMCPServerModel) -> ServerLaunchSpec:
    """Resolve runtime, args, and writable cache locations for an MCP server."""
    args_list = _parse_args_json(server.args)
    env_vars = _parse_env_json(server.env_vars)
    runtime_paths = _ensure_runtime_paths()

    env = {**inherited_env(), **env_vars}
    env.setdefault("MCP_RUNTIME_ROOT", str(runtime_paths["root"]))
    env.setdefault("XDG_CACHE_HOME", str(runtime_paths["xdg_cache"]))

    if server.runtime == "npx":
        runtime_label = "npx"
        args = ["-y", server.package_name, *args_list]
        env.setdefault("NPM_CONFIG_CACHE", str(runtime_paths["npm_cache"]))
        env.setdefault("npm_config_cache", str(runtime_paths["npm_cache"]))
        env.setdefault("COREPACK_HOME", str(runtime_paths["corepack_home"]))
    elif server.runtime == "uvx":
        runtime_label = "uvx"
        args = [server.package_name, *args_list]
        env.setdefault("UV_CACHE_DIR", str(runtime_paths["uv_cache"]))
    else:
        raise ValueError(f"Unknown runtime: {server.runtime}")

    resolved_command = shutil.which(runtime_label)
    if not resolved_command:
        raise FileNotFoundError(
            f"Required runtime '{runtime_label}' is not installed or not on PATH."
        )

    connection = {
        "command": resolved_command,
        "args": args,
        "transport": "stdio",
        "env": env,
    }
    full_cmd = shlex.join([resolved_command, *args])
    return ServerLaunchSpec(connection=connection, full_cmd=full_cmd)


async def _load_tools_via_stdio(
    *,
    server_name: str,
    connection: Dict[str, Any],
    full_cmd: str,
) -> List[Any]:
    """Perform MCP initialize/list_tools with stderr capture and actionable errors."""
    from langchain_mcp_adapters.tools import _list_all_tools, convert_mcp_tool_to_langchain_tool
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    stderr_path = _ensure_runtime_paths()["logs"] / f"{server_name}-{uuid.uuid4().hex}.stderr.log"
    server_params = StdioServerParameters(
        command=connection["command"],
        args=connection["args"],
        env=connection.get("env"),
        cwd=connection.get("cwd"),
        encoding=connection.get("encoding", "utf-8"),
        encoding_error_handler=connection.get("encoding_error_handler", "strict"),
    )
    session_kwargs = connection.get("session_kwargs") or {}

    async def _probe_tool_definitions(errlog) -> List[Any]:
        async with (
            stdio_client(server_params, errlog=errlog) as (read, write),
            ClientSession(read, write, **session_kwargs) as session,
        ):
            await session.initialize()
            return await _list_all_tools(session)

    try:
        with open(stderr_path, "w+", encoding="utf-8") as errlog:
            try:
                tool_definitions = await asyncio.wait_for(
                    _probe_tool_definitions(errlog),
                    timeout=MCP_HANDSHAKE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                errlog.flush()
                raise MCPStartupError(
                    _format_startup_failure(
                        server_name,
                        full_cmd,
                        exc,
                        _read_stderr_tail(stderr_path),
                        timed_out=True,
                    )
                ) from exc
            except Exception as exc:
                errlog.flush()
                raise MCPStartupError(
                    _format_startup_failure(
                        server_name,
                        full_cmd,
                        exc,
                        _read_stderr_tail(stderr_path),
                    )
                ) from exc
    finally:
        try:
            stderr_path.unlink()
        except FileNotFoundError:
            pass

    return [
        convert_mcp_tool_to_langchain_tool(
            None,
            tool_definition,
            connection=connection,
            server_name=server_name,
        )
        for tool_definition in tool_definitions
    ]


async def _persist_tool_names(server_id: str, tool_names: List[str]) -> None:
    """Persist discovered tool names for the server status UI."""
    async with async_session() as session:
        db_server = await session.get(CustomMCPServerModel, server_id)
        if db_server:
            db_server.tool_names = json.dumps(tool_names)
            await session.commit()


async def _start_server_process(server: CustomMCPServerModel) -> ServerInstance:
    """Start an MCP server subprocess and connect to it."""
    instance = ServerInstance(
        server_id=server.id,
        name=server.name,
        status="starting",
    )

    try:
        launch_spec = _build_server_launch_spec(server)
        env_vars = _parse_env_json(server.env_vars)

        full_cmd = launch_spec.full_cmd
        print(f"[Custom MCP] Starting '{server.name}': {full_cmd}")
        if env_vars:
            print(f"[Custom MCP] Extra env vars: {list(env_vars.keys())}")

        raw_tools = await _load_tools_via_stdio(
            server_name=server.name,
            connection=launch_spec.connection,
            full_cmd=full_cmd,
        )

        # Prefix tool names to avoid collisions
        prefix = server.tool_prefix
        for tool in raw_tools:
            if not tool.name.startswith(f"{prefix}_"):
                tool.name = f"{prefix}_{tool.name}"

        instance.client = launch_spec.connection
        instance.tools = raw_tools
        instance.status = "connected"
        instance.error = None

        # Update tool names in DB
        tool_names = [t.name for t in raw_tools]
        await _persist_tool_names(server.id, tool_names)

        print(f"[Custom MCP] '{server.name}' connected with {len(raw_tools)} tools")
        for tool in raw_tools:
            desc = tool.description[:60] if tool.description else "No description"
            print(f"  - {tool.name}: {desc}...")

        return instance

    except Exception as e:
        if isinstance(e, MCPStartupError):
            error_type = type(e).__name__
            error_msg = str(e)
        else:
            primary_error, all_errors = _summarize_exception(e)
            error_type = primary_error.split(":", 1)[0]
            error_msg = all_errors or primary_error
        instance.status = "error"
        instance.error = f"[{error_type}] {error_msg}"
        print(f"[Custom MCP] Failed to start '{server.name}' [{error_type}]: {error_msg}")
        return instance


async def add_server(
    name: str,
    package_name: str,
    runtime: str,
    args: Optional[List[str]] = None,
    env_vars: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
    source_url: Optional[str] = None,
) -> dict:
    """
    Add and start a new custom MCP server.

    Returns dict with server info and tool list.
    """
    # Validate runtime
    if runtime not in ("npx", "uvx"):
        raise ValueError(f"runtime must be 'npx' or 'uvx', got '{runtime}'")

    # Generate tool prefix from name (lowercase, underscores)
    tool_prefix = name.lower().replace("-", "_").replace(" ", "_")

    # Check for duplicate name
    async with async_session() as session:
        existing = await session.execute(
            select(CustomMCPServerModel).where(CustomMCPServerModel.name == name)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Server '{name}' already exists")

    # Create DB record
    server_id = str(uuid.uuid4())
    server = CustomMCPServerModel(
        id=server_id,
        name=name,
        description=description,
        package_name=package_name,
        runtime=runtime,
        args=json.dumps(args) if args else None,
        env_vars=json.dumps(env_vars) if env_vars else None,
        tool_prefix=tool_prefix,
        enabled=True,
        source_url=source_url,
    )

    async with async_session() as session:
        session.add(server)
        await session.commit()
        await session.refresh(server)

    # Start the server
    instance = await _start_server_process(server)
    _servers[server_id] = instance

    # Refresh tool registry
    from services.tool_registry import refresh_registry
    await refresh_registry()

    return {
        "id": server_id,
        "name": name,
        "description": description,
        "package_name": package_name,
        "runtime": runtime,
        "tool_prefix": tool_prefix,
        "status": instance.status,
        "error": instance.error,
        "tools": [t.name for t in instance.tools],
        "tool_count": len(instance.tools),
    }


async def remove_server(server_id: str) -> bool:
    """Stop and remove a custom MCP server."""
    # Stop if running
    if server_id in _servers:
        instance = _servers[server_id]
        instance.status = "stopped"
        instance.tools = []
        instance.client = None
        del _servers[server_id]

    # Delete from DB
    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return False
        await session.delete(server)
        await session.commit()

    # Refresh tool registry
    from services.tool_registry import refresh_registry
    await refresh_registry()

    return True


async def update_server(
    server_id: str,
    args: Optional[List[str]] = None,
    env_vars: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
) -> Optional[dict]:
    """
    Update a custom MCP server's configuration.

    env_vars merges with existing (set a key to "" to remove it).
    args replaces entirely.
    If the server is running, it is automatically restarted.
    """
    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return None

        if description is not None:
            server.description = description

        if args is not None:
            server.args = json.dumps(args)

        if env_vars is not None:
            # Merge with existing env vars
            existing = json.loads(server.env_vars) if server.env_vars else {}
            for key, value in env_vars.items():
                if value == "":
                    existing.pop(key, None)
                else:
                    existing[key] = value
            server.env_vars = json.dumps(existing) if existing else None

        await session.commit()

    # If server is running, restart it with new config
    was_running = server_id in _servers and _servers[server_id].status == "connected"
    if was_running:
        await stop_server(server_id)

        # Re-read fresh DB data for restart
        async with async_session() as session:
            server = await session.get(CustomMCPServerModel, server_id)
            if server and server.enabled:
                instance = await _start_server_process(server)
                _servers[server_id] = instance

        from services.tool_registry import refresh_registry
        await refresh_registry()

    # Return current state
    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return None
        return _server_to_dict(server)


async def restart_server(server_id: str) -> Optional[dict]:
    """
    Restart a custom MCP server.

    Stops the server if running, then starts it fresh from DB config.
    Useful for error recovery or picking up config changes.
    """
    # Stop if running
    if server_id in _servers:
        _servers[server_id].status = "stopped"
        _servers[server_id].tools = []
        _servers[server_id].client = None
        del _servers[server_id]

    # Re-read from DB and start
    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return None

        if not server.enabled:
            return _server_to_dict(server)

        instance = await _start_server_process(server)
        _servers[server_id] = instance

    from services.tool_registry import refresh_registry
    await refresh_registry()

    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return None
        return _server_to_dict(server)


async def start_server(server_id: str) -> Optional[ServerInstance]:
    """Start a stopped server."""
    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return None

        instance = await _start_server_process(server)
        _servers[server_id] = instance

        # Refresh tool registry
        from services.tool_registry import refresh_registry
        await refresh_registry()

        return instance


async def stop_server(server_id: str) -> bool:
    """Stop a running server."""
    if server_id not in _servers:
        return False

    instance = _servers[server_id]
    instance.status = "stopped"
    instance.tools = []
    instance.client = None
    del _servers[server_id]

    # Refresh tool registry
    from services.tool_registry import refresh_registry
    await refresh_registry()

    return True


async def set_server_enabled(server_id: str, enabled: bool) -> Optional[dict]:
    """Enable or disable a server. Starts/stops the subprocess accordingly."""
    async with async_session() as session:
        server = await session.get(CustomMCPServerModel, server_id)
        if not server:
            return None

        server.enabled = enabled
        await session.commit()

        if enabled:
            instance = await _start_server_process(server)
            _servers[server_id] = instance
        else:
            if server_id in _servers:
                _servers[server_id].status = "stopped"
                _servers[server_id].tools = []
                _servers[server_id].client = None
                del _servers[server_id]

        # Refresh tool registry
        from services.tool_registry import refresh_registry
        await refresh_registry()

        return _server_to_dict(server)


async def get_all_servers() -> List[dict]:
    """Get all custom servers with live status."""
    async with async_session() as session:
        result = await session.execute(
            select(CustomMCPServerModel).order_by(CustomMCPServerModel.added_at.desc())
        )
        servers = result.scalars().all()

    return [_server_to_dict(s) for s in servers]


def get_server_tools(server_id: str) -> List[Any]:
    """Get tools for a specific running server."""
    instance = _servers.get(server_id)
    if not instance:
        return []
    return instance.tools


def get_all_custom_tools() -> List[Any]:
    """Get all tools from all running custom servers."""
    tools = []
    for instance in _servers.values():
        if instance.status == "connected":
            tools.extend(instance.tools)
    return tools


def get_all_custom_tools_by_server() -> List[Tuple[str, Any]]:
    """``(server name, tool)`` for every tool of every running custom server
    (the registry tags each with its server)."""
    return [(instance.name, tool) for instance in _servers.values() if instance.status == "connected"
            for tool in instance.tools]


async def initialize_custom_servers() -> None:
    """Start all enabled custom servers from the database. Called at startup."""
    async with async_session() as session:
        result = await session.execute(
            select(CustomMCPServerModel).where(CustomMCPServerModel.enabled == True)
        )
        servers = result.scalars().all()

    if not servers:
        print("No custom MCP servers to initialize")
        return

    print(f"Initializing {len(servers)} custom MCP server(s)...")
    for server in servers:
        try:
            instance = await _start_server_process(server)
            _servers[server.id] = instance
        except Exception as e:
            print(f"Failed to initialize custom MCP server '{server.name}': {e}")


async def shutdown_custom_servers() -> None:
    """Stop all running custom servers. Called at shutdown."""
    for server_id in list(_servers.keys()):
        instance = _servers[server_id]
        print(f"Stopping custom MCP server '{instance.name}'...")
        instance.status = "stopped"
        instance.tools = []
        instance.client = None
    _servers.clear()


def _server_to_dict(server: CustomMCPServerModel) -> dict:
    """Convert a DB server model to a response dict with live status."""
    instance = _servers.get(server.id)
    tool_names = json.loads(server.tool_names) if server.tool_names else []

    # Parse args and env var keys for visibility
    args_list = json.loads(server.args) if server.args else []
    env_dict = json.loads(server.env_vars) if server.env_vars else {}
    env_var_keys = list(env_dict.keys())

    return {
        "id": server.id,
        "name": server.name,
        "description": server.description,
        "package_name": server.package_name,
        "runtime": server.runtime,
        "tool_prefix": server.tool_prefix,
        "enabled": server.enabled,
        "status": instance.status if instance else ("stopped" if server.enabled else "disabled"),
        "error": instance.error if instance else None,
        "tool_names": tool_names,
        "tool_count": len(instance.tools) if instance else len(tool_names),
        "args": args_list,
        "env_var_keys": env_var_keys,
        "source_url": server.source_url,
        "added_at": server.added_at.isoformat() if server.added_at else None,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None,
    }
