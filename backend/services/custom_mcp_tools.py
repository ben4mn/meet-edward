"""
LLM tools for Edward to self-service MCP servers.

Allows Edward to search for, install, manage, and remove MCP servers
autonomously at runtime.
"""

import os
import json
from typing import Optional
from langchain_core.tools import tool


@tool
async def search_mcp_servers(query: str) -> str:
    """Search for MCP (Model Context Protocol) servers that could extend your capabilities.

    Searches GitHub for published MCP server packages. Use this to find servers
    for specific integrations (e.g., "github", "slack", "weather", "filesystem").

    Args:
        query: What kind of MCP server to search for (e.g., "github", "slack", "database")

    Returns:
        JSON list of matching MCP servers with name, description, package info, and install command.
    """
    import httpx

    results = []

    # Search GitHub for MCP server repos
    github_token = os.getenv("GITHUB_TOKEN")
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        async with httpx.AsyncClient(timeout=15) as client:
            # Search for repos matching query + MCP
            search_query = f"{query} mcp server in:name,description,topics"
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": search_query, "sort": "stars", "per_page": 10},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                for repo in data.get("items", []):
                    # Try to detect package info from repo
                    name = repo["name"]
                    full_name = repo["full_name"]
                    description = repo.get("description", "")
                    stars = repo.get("stargazers_count", 0)
                    url = repo.get("html_url", "")
                    topics = repo.get("topics", [])

                    # Heuristic: detect runtime and package
                    runtime = "npx"
                    package_name = ""

                    # Check if it looks like an npm package
                    if any(t in topics for t in ["npm", "nodejs", "typescript", "javascript"]):
                        runtime = "npx"
                    elif any(t in topics for t in ["python", "pip", "pypi"]):
                        runtime = "uvx"

                    # Common MCP server naming patterns
                    if name.startswith("server-"):
                        package_name = f"@modelcontextprotocol/{name}"
                    elif "mcp" in name.lower():
                        # Try npm scope pattern
                        owner = full_name.split("/")[0]
                        package_name = f"@{owner}/{name}"

                    results.append({
                        "name": name,
                        "description": description[:200] if description else "",
                        "stars": stars,
                        "url": url,
                        "runtime": runtime,
                        "package_name": package_name or name,
                        "topics": topics[:5],
                    })

    except Exception as e:
        results.append({"error": f"GitHub search failed: {str(e)}"})

    # Also try searching the official MCP servers org
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            headers = {"Accept": "application/vnd.github.v3+json"}
            if github_token:
                headers["Authorization"] = f"token {github_token}"

            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": f"{query} org:modelcontextprotocol",
                    "sort": "stars",
                    "per_page": 5,
                },
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                existing_urls = {r.get("url") for r in results}
                for repo in data.get("items", []):
                    url = repo.get("html_url", "")
                    if url in existing_urls:
                        continue
                    name = repo["name"]
                    results.append({
                        "name": name,
                        "description": (repo.get("description", "") or "")[:200],
                        "stars": repo.get("stargazers_count", 0),
                        "url": url,
                        "runtime": "npx",
                        "package_name": f"@modelcontextprotocol/{name}",
                        "topics": repo.get("topics", [])[:5],
                    })
    except Exception:
        pass  # Best effort for official org search

    if not results:
        return json.dumps({"results": [], "message": f"No MCP servers found for '{query}'. Try a broader search term."})

    # Sort by stars
    results.sort(key=lambda x: x.get("stars", 0), reverse=True)

    return json.dumps({"results": results[:10], "total": len(results)})


@tool
async def add_mcp_server(
    name: str,
    package_name: str,
    runtime: str,
    args: Optional[str] = None,
    env_vars: Optional[str] = None,
    description: Optional[str] = None,
    source_url: Optional[str] = None,
) -> str:
    """Add and start a new MCP server to extend your capabilities.

    The server runs as a subprocess and its tools become immediately available to you.
    Use npx for Node.js/TypeScript servers and uvx for Python servers.

    Args:
        name: Short name for the server (e.g., "github", "slack"). Used as tool prefix.
        package_name: npm or PyPI package name (e.g., "@modelcontextprotocol/server-github")
        runtime: "npx" for Node.js packages, "uvx" for Python packages
        args: Optional JSON array of extra command-line arguments (e.g., '["--token", "abc"]')
        env_vars: Optional JSON object of environment variables (e.g., '{"GITHUB_TOKEN": "ghp_..."}')
        description: Optional description of what this server does
        source_url: Optional URL to the server's repository

    Returns:
        JSON with server info and list of available tools, or error message.
    """
    from services.custom_mcp_service import add_server

    # Parse optional JSON args
    parsed_args = None
    if args:
        try:
            parsed_args = json.loads(args)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON for args: {args}"})

    parsed_env = None
    if env_vars:
        try:
            parsed_env = json.loads(env_vars)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON for env_vars: {env_vars}"})

    try:
        result = await add_server(
            name=name,
            package_name=package_name,
            runtime=runtime,
            args=parsed_args,
            env_vars=parsed_env,
            description=description,
            source_url=source_url,
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"Failed to add server: {str(e)}"})


@tool
async def list_custom_servers() -> str:
    """List all custom MCP servers you've added, with their status and tools.

    Returns:
        JSON list of all custom servers with status, tool counts, and details.
    """
    from services.custom_mcp_service import get_all_servers

    servers = await get_all_servers()
    return json.dumps({"servers": servers, "total": len(servers)})


@tool
async def remove_mcp_server(server_id: str) -> str:
    """Remove a custom MCP server you previously added.

    This stops the server process and removes it from the database.
    Its tools will no longer be available.

    Args:
        server_id: The ID of the server to remove. Use list_custom_servers to find IDs.

    Returns:
        JSON confirmation or error message.
    """
    from services.custom_mcp_service import remove_server

    try:
        success = await remove_server(server_id)
        if success:
            return json.dumps({"status": "removed", "server_id": server_id})
        else:
            return json.dumps({"error": f"Server '{server_id}' not found"})
    except Exception as e:
        return json.dumps({"error": f"Failed to remove server: {str(e)}"})


@tool
async def update_mcp_server(
    server_id: str,
    env_vars: Optional[str] = None,
    args: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """Update configuration of an existing custom MCP server.

    Use this to add or change environment variables (like API keys), update command-line
    arguments, or change the description. If the server is currently running, it will be
    automatically restarted with the new configuration.

    Args:
        server_id: The ID of the server to update. Use list_custom_servers to find IDs.
        env_vars: Optional JSON object of environment variables to merge. Set a key to "" to remove it.
                  Example: '{"GITHUB_TOKEN": "ghp_abc123"}' or '{"OLD_KEY": ""}' to remove.
        args: Optional JSON array of command-line arguments (replaces existing args entirely).
              Example: '["--port", "8080"]'
        description: Optional new description for the server.

    Returns:
        JSON with updated server info, or error message.
    """
    from services.custom_mcp_service import update_server

    parsed_args = None
    if args:
        try:
            parsed_args = json.loads(args)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON for args: {args}"})

    parsed_env = None
    if env_vars:
        try:
            parsed_env = json.loads(env_vars)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON for env_vars: {env_vars}"})

    try:
        result = await update_server(
            server_id=server_id,
            args=parsed_args,
            env_vars=parsed_env,
            description=description,
        )
        if result is None:
            return json.dumps({"error": f"Server '{server_id}' not found"})
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Failed to update server: {str(e)}"})


@tool
async def restart_mcp_server(server_id: str) -> str:
    """Restart a custom MCP server.

    Stops the server if running, then starts it fresh from its saved configuration.
    Useful for error recovery or when a server is in a bad state.

    Args:
        server_id: The ID of the server to restart. Use list_custom_servers to find IDs.

    Returns:
        JSON with server info after restart, or error message.
    """
    from services.custom_mcp_service import restart_server

    try:
        result = await restart_server(server_id)
        if result is None:
            return json.dumps({"error": f"Server '{server_id}' not found"})
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Failed to restart server: {str(e)}"})


# Export as a single list for easy registration
CUSTOM_MCP_TOOLS = [search_mcp_servers, add_mcp_server, list_custom_servers, remove_mcp_server, update_mcp_server, restart_mcp_server]
