"""
Custom MCP Server service for Edward.

Manages lifecycle of MCP servers that Edward adds at runtime.
Servers run as subprocesses via npx/uvx and their tools become
available to Edward immediately.
"""

import os
import sys
import json
import uuid
import asyncio
import threading
import concurrent.futures
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from services.database import async_session, CustomMCPServerModel
from sqlalchemy import select


def _needs_proactor_thread() -> bool:
    """
    On Windows with SelectorEventLoop, asyncio cannot create subprocess pipes.
    anyio (used by the MCP SDK) raises NotImplementedError in this case.
    Detect this so we can launch MCP subprocesses in a dedicated ProactorEventLoop thread.
    """
    return sys.platform == "win32"


class _ProactorMCPThread:
    """
    Runs a single MCP server subprocess on a dedicated ProactorEventLoop thread.

    Required on Windows because the main backend uses SelectorEventLoop (forced
    by run.py for psycopg compatibility), and SelectorEventLoop cannot create
    subprocess pipes. All async session operations are dispatched to this thread
    via run_coroutine_threadsafe so they stay on the correct event loop.
    """

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stdio_ctx = None
        self._session_ctx = None
        self.session = None

    def start(self):
        """Start the background thread and its ProactorEventLoop."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run_loop(self):
        self._loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run(self, coro):
        """Submit a coroutine to the ProactorEventLoop and block until done."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    async def connect(self, server_params):
        """Open the stdio transport and MCP session (runs on ProactorEventLoop)."""
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        self._stdio_ctx = stdio_client(server_params)
        read_stream, write_stream = await self._stdio_ctx.__aenter__()
        self._session_ctx = ClientSession(read_stream, write_stream)
        self.session = await self._session_ctx.__aenter__()
        await self.session.initialize()

    async def list_tools(self):
        return await self.session.list_tools()

    async def call_tool(self, name: str, arguments: dict):
        return await self.session.call_tool(name, arguments=arguments)

    async def shutdown(self):
        """Tear down session and stdio transport."""
        try:
            if self._session_ctx:
                await self._session_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if self._stdio_ctx:
                await self._stdio_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        self.session = None

    def stop(self):
        """Shut down the session and stop the ProactorEventLoop thread."""
        if self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self.shutdown(), self._loop)
                future.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)


class _ProactorMCPToolWrapper:
    """
    Wraps an MCP tool whose session lives on a ProactorEventLoop thread.
    Dispatches ainvoke() calls to that thread via run_coroutine_threadsafe.
    Matches the MCPToolWrapper interface (.name, .description, .args_schema, .ainvoke).
    """

    def __init__(self, mcp_thread: _ProactorMCPThread, name: str, original_name: str, description: str, input_schema: dict):
        from services.mcp_tool_wrapper import MCPToolWrapper
        self._thread = mcp_thread
        self.name = name  # prefixed name exposed to the LLM
        self._original_name = original_name  # unprefixed name sent to the MCP server
        self.description = description
        # Reuse MCPToolWrapper's Pydantic schema builder with a dummy session
        _dummy = MCPToolWrapper.__new__(MCPToolWrapper)
        _dummy.name = name
        _dummy._input_schema = input_schema or {"type": "object", "properties": {}}
        self.args_schema = _dummy._build_pydantic_schema()

    async def ainvoke(self, args: dict) -> Any:
        """Dispatch the tool call to the ProactorEventLoop thread."""
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._thread.call_tool(self._original_name, args),
            self._thread._loop,
        )
        # Await the concurrent.futures.Future from the main SelectorEventLoop
        result = await loop.run_in_executor(None, future.result, 30)

        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif hasattr(block, "data"):
                parts.append(f"[binary data: {len(block.data)} bytes]")
            else:
                parts.append(str(block))
        return "\n".join(parts) if parts else ""

    def __repr__(self):
        return f"_ProactorMCPToolWrapper(name={self.name!r})"


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


async def _start_server_process(server: CustomMCPServerModel) -> ServerInstance:
    """Start an MCP server subprocess and connect to it."""
    from mcp import StdioServerParameters
    from services.mcp_tool_wrapper import MCPToolWrapper

    instance = ServerInstance(
        server_id=server.id,
        name=server.name,
        status="starting",
    )

    try:
        args_list = json.loads(server.args) if server.args else []
        env_vars = json.loads(server.env_vars) if server.env_vars else {}

        # Build command based on runtime
        if server.runtime == "npx":
            command = "npx"
            args = ["-y", server.package_name] + args_list
        elif server.runtime == "uvx":
            command = "uvx"
            args = [server.package_name] + args_list
        elif server.runtime == "binary":
            # Direct binary execution — package_name is the command (must be on PATH or full path)
            command = server.package_name
            args = args_list
        else:
            raise ValueError(f"Unknown runtime: {server.runtime}")

        # Merge env vars with current environment
        full_env = {**os.environ, **env_vars} if env_vars else dict(os.environ)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=full_env,
        )

        if _needs_proactor_thread():
            # On Windows, SelectorEventLoop cannot create subprocess pipes.
            # Run the MCP subprocess on a dedicated ProactorEventLoop thread.
            mcp_thread = _ProactorMCPThread()
            mcp_thread.start()
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: mcp_thread.run(mcp_thread.connect(server_params)))
                tools_result = await loop.run_in_executor(None, lambda: mcp_thread.run(mcp_thread.list_tools()))
            except Exception:
                mcp_thread.stop()
                raise

            wrapped_tools = []
            prefix = server.tool_prefix
            for t in tools_result.tools:
                original_name = t.name
                tool_name = original_name if original_name.startswith(f"{prefix}_") else f"{prefix}_{original_name}"
                wrapped_tools.append(_ProactorMCPToolWrapper(
                    mcp_thread=mcp_thread,
                    name=tool_name,
                    original_name=original_name,
                    description=t.description or "",
                    input_schema=t.inputSchema if hasattr(t, 'inputSchema') else {},
                ))

            instance.client = {"mcp_thread": mcp_thread}
        else:
            # macOS/Linux: SelectorEventLoop supports subprocesses fine.
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            stdio_ctx = stdio_client(server_params)
            read_stream, write_stream = await stdio_ctx.__aenter__()
            try:
                session_ctx = ClientSession(read_stream, write_stream)
                mcp_session = await session_ctx.__aenter__()
                await mcp_session.initialize()

                tools_result = await mcp_session.list_tools()
                wrapped_tools = []
                prefix = server.tool_prefix
                for t in tools_result.tools:
                    original_name = t.name
                    tool_name = original_name if original_name.startswith(f"{prefix}_") else f"{prefix}_{original_name}"
                    wrapped_tools.append(MCPToolWrapper(
                        session=mcp_session,
                        name=tool_name,
                        original_name=original_name,
                        description=t.description or "",
                        input_schema=t.inputSchema if hasattr(t, 'inputSchema') else {},
                    ))
            except Exception:
                try:
                    await stdio_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                raise

            instance.client = {"stdio_ctx": stdio_ctx, "session_ctx": session_ctx, "session": mcp_session}

        instance.tools = wrapped_tools
        instance.status = "connected"
        instance.error = None

        # Update tool names in DB
        tool_names = [t.name for t in wrapped_tools]
        async with async_session() as session:
            db_server = await session.get(CustomMCPServerModel, server.id)
            if db_server:
                db_server.tool_names = json.dumps(tool_names)
                await session.commit()

        print(f"Custom MCP server '{server.name}' started with {len(wrapped_tools)} tools")
        for tool in wrapped_tools:
            desc = tool.description[:60] if tool.description else "No description"
            print(f"  - {tool.name}: {desc}...")

        return instance

    except Exception as e:
        instance.status = "error"
        instance.error = str(e)
        print(f"Failed to start custom MCP server '{server.name}': {e}")
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
    if runtime not in ("npx", "uvx", "binary"):
        raise ValueError(f"runtime must be 'npx', 'uvx', or 'binary', got '{runtime}'")

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


def _cleanup_instance(instance: ServerInstance):
    """Clean up a server instance, stopping its subprocess if needed."""
    if instance.client:
        mcp_thread = instance.client.get("mcp_thread")
        if mcp_thread is not None:
            try:
                mcp_thread.stop()
            except Exception:
                pass
    instance.status = "stopped"
    instance.tools = []
    instance.client = None


async def remove_server(server_id: str) -> bool:
    """Stop and remove a custom MCP server."""
    # Stop if running
    if server_id in _servers:
        instance = _servers[server_id]
        _cleanup_instance(instance)
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
    _cleanup_instance(instance)
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
                _cleanup_instance(_servers[server_id])
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
        _cleanup_instance(instance)
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
