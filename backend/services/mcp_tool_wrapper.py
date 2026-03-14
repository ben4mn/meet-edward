"""
MCPToolWrapper: Wraps MCP server tools to match EdwardTool interface.

Used by mcp_client.py (Apple Services) and custom_mcp_service.py (custom servers).
Provides .name, .description, .args_schema, .ainvoke() — same as EdwardTool.
"""

import json
from typing import Any, Optional

from mcp import ClientSession
from pydantic import create_model


class _MCPArgsSchema:
    """Minimal args_schema that exposes model_json_schema() from raw JSON Schema."""

    def __init__(self, schema: dict):
        self._schema = schema

    @classmethod
    def model_json_schema(cls):
        # Class-level fallback — shouldn't be called
        return {"type": "object", "properties": {}}

    def to_json_schema(self) -> dict:
        return self._schema


class MCPToolWrapper:
    """Wraps an MCP tool to match EdwardTool interface."""

    def __init__(
        self,
        session: ClientSession,
        name: str,
        description: str,
        input_schema: Optional[dict] = None,
        original_name: Optional[str] = None,
    ):
        self._session = session
        self.name = name  # prefixed name exposed to the LLM
        self._original_name = original_name or name  # unprefixed name sent to the MCP server
        self.description = description
        self._input_schema = input_schema or {"type": "object", "properties": {}}

        # Build a Pydantic model from JSON Schema for compatibility
        self.args_schema = self._build_pydantic_schema()

    def _build_pydantic_schema(self):
        """Build a Pydantic model from MCP's input schema."""
        properties = self._input_schema.get("properties", {})
        required = set(self._input_schema.get("required", []))

        fields = {}
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type", "string")
            python_type = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }.get(prop_type, str)

            if prop_name in required:
                fields[prop_name] = (python_type, ...)
            else:
                default = prop_def.get("default")
                fields[prop_name] = (Optional[python_type], default)

        model_name = self.name.title().replace("_", "").replace("-", "") + "Input"
        try:
            return create_model(model_name, **fields)
        except Exception:
            # Fallback: empty schema if Pydantic model creation fails
            return create_model(model_name)

    async def ainvoke(self, args: dict) -> Any:
        """Execute the MCP tool via the session."""
        result = await self._session.call_tool(self._original_name, arguments=args)

        # Extract text from MCP result content blocks
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
        return f"MCPToolWrapper(name={self.name!r})"
