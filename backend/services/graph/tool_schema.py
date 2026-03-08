"""
Tool schema conversion for Anthropic API.

Converts EdwardTool and MCPToolWrapper objects into the tool schema format
expected by anthropic.messages.create(tools=[...]).
"""

from typing import Any


def tool_to_anthropic_schema(tool: Any) -> dict:
    """Convert a tool object to Anthropic's tool schema format.

    Works with both EdwardTool (.args_schema is a Pydantic model) and
    MCPToolWrapper (.args_schema may be a Pydantic model or have _input_schema).
    """
    schema = _extract_json_schema(tool)

    # Clean the schema: keep only what Anthropic expects
    clean_schema = {
        "type": schema.get("type", "object"),
        "properties": schema.get("properties", {}),
    }
    if "required" in schema:
        clean_schema["required"] = schema["required"]

    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": clean_schema,
    }


def tools_to_anthropic_schemas(tools: list) -> list[dict]:
    """Convert a list of tools to Anthropic tool schema format."""
    return [tool_to_anthropic_schema(t) for t in tools]


def _extract_json_schema(tool: Any) -> dict:
    """Extract JSON Schema from a tool's args_schema."""
    args_schema = getattr(tool, "args_schema", None)

    if args_schema is None:
        return {"type": "object", "properties": {}}

    # MCPToolWrapper with raw JSON Schema via _input_schema
    if hasattr(tool, "_input_schema"):
        return tool._input_schema

    # Pydantic v2 model class
    if hasattr(args_schema, "model_json_schema"):
        schema = args_schema.model_json_schema()
        # Strip Pydantic-added fields that Anthropic doesn't want
        schema.pop("title", None)
        schema.pop("$defs", None)
        schema.pop("definitions", None)
        # Inline any $ref definitions (simple case)
        _inline_refs(schema)
        return schema

    # Pydantic v1 model class
    if hasattr(args_schema, "schema"):
        schema = args_schema.schema()
        schema.pop("title", None)
        schema.pop("definitions", None)
        return schema

    return {"type": "object", "properties": {}}


def _inline_refs(schema: dict) -> None:
    """Inline simple $ref references in properties (from Pydantic v2 $defs)."""
    defs = schema.pop("$defs", None)
    if not defs:
        return

    properties = schema.get("properties", {})
    for prop_name, prop_def in list(properties.items()):
        if "$ref" in prop_def:
            ref_name = prop_def["$ref"].split("/")[-1]
            if ref_name in defs:
                properties[prop_name] = defs[ref_name]
        # Handle anyOf (Optional types in Pydantic v2)
        if "anyOf" in prop_def:
            non_null = [t for t in prop_def["anyOf"] if t != {"type": "null"}]
            if len(non_null) == 1:
                ref = non_null[0]
                if "$ref" in ref:
                    ref_name = ref["$ref"].split("/")[-1]
                    if ref_name in defs:
                        properties[prop_name] = defs[ref_name]
