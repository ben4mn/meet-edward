"""
Custom MCP Server service for Edward.

Manages lifecycle of MCP servers that Edward adds at runtime.
Servers run as subprocesses via npx/uvx and their tools become
available to Edward immediately.
"""

import os
import json
import uuid
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

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


async def _start_server_process(server: CustomMCPServerModel) -> ServerInstance:
    """Start an MCP server subprocess and connect to it."""
    instance = ServerInstance(
        server_id=server.id,
        name=server.name,
        status="starting",
    )

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        args_list = json.loads(server.args) if server.args else []
        env_vars = json.loads(server.env_vars) if server.env_vars else {}

        # Build command based on runtime
        if server.runtime == "npx":
            command = "npx"
            args = ["-y", server.package_name] + args_list
        elif server.runtime == "uvx":
            command = "uvx"
            args = [server.package_name] + args_list
        else:
            raise ValueError(f"Unknown runtime: {server.runtime}")

        # Merge env vars with current environment
        full_env = {**os.environ, **env_vars} if env_vars else None

        server_config = {
            server.name: {
                "command": command,
                "args": args,
                "transport": "stdio",
            }
        }
        if full_env:
            server_config[server.name]["env"] = full_env

        client = MultiServerMCPClient(server_config)
        raw_tools = await client.get_tools()

        # Prefix tool names to avoid collisions
        prefix = server.tool_prefix
        for tool in raw_tools:
            if not tool.name.startswith(f"{prefix}_"):
                tool.name = f"{prefix}_{tool.name}"

        instance.client = client
        instance.tools = raw_tools
        instance.status = "connected"
        instance.error = None

        # Update tool names in DB
        tool_names = [t.name for t in raw_tools]
        async with async_session() as session:
            db_server = await session.get(CustomMCPServerModel, server.id)
            if db_server:
                db_server.tool_names = json.dumps(tool_names)
                await session.commit()

        print(f"Custom MCP server '{server.name}' started with {len(raw_tools)} tools")
        for tool in raw_tools:
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
