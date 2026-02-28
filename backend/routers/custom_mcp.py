"""
Custom MCP servers router for Edward.

Provides endpoints for viewing, enabling/disabling, updating, restarting,
and removing custom MCP servers that Edward has added at runtime.
"""

from typing import Optional, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.custom_mcp_service import (
    get_all_servers,
    set_server_enabled,
    update_server,
    restart_server,
    remove_server,
)

router = APIRouter()


class CustomMCPUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    args: Optional[list] = None
    env_vars: Optional[Dict[str, str]] = None
    description: Optional[str] = None


@router.get("/custom-mcp")
async def list_custom_servers():
    """List all custom MCP servers with status."""
    try:
        servers = await get_all_servers()
        return {"servers": servers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/custom-mcp/{server_id}")
async def update_custom_server(server_id: str, request: CustomMCPUpdateRequest):
    """Update a custom MCP server's configuration and/or enabled state."""
    try:
        result = None

        # Handle config updates (args, env_vars, description)
        has_config_update = (
            request.args is not None
            or request.env_vars is not None
            or request.description is not None
        )
        if has_config_update:
            result = await update_server(
                server_id=server_id,
                args=request.args,
                env_vars=request.env_vars,
                description=request.description,
            )
            if not result:
                raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

        # Handle enabled toggle separately (if also provided)
        if request.enabled is not None:
            result = await set_server_enabled(server_id, request.enabled)
            if not result:
                raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

        if result is None:
            raise HTTPException(status_code=400, detail="No update fields provided")

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom-mcp/{server_id}/restart")
async def restart_custom_server(server_id: str):
    """Restart a custom MCP server."""
    try:
        result = await restart_server(server_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/custom-mcp/{server_id}")
async def delete_custom_server(server_id: str):
    """Remove a custom MCP server."""
    try:
        success = await remove_server(server_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
        return {"status": "removed", "server_id": server_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
