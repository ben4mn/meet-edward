"""
Regression: when a LangChain BaseTool has args_schema set to a *dict*
(as langchain-mcp-adapters does for MCP tools), codex_chat_model
_convert_tools must read that dict, not call get_input_schema().

Failure signature pre-fix: every chat turn that touched the apple-mcp
'calendar' tool got "Tool error: Error: Invalid arguments for calendar
tool" because the LLM saw a fallback schema (anyOf: [string, object,
ToolCall]) with no documented arg names — so it guessed argument keys
that apple-mcp's strict validator rejected. Edward looped 4-5 times,
hit the consecutive-all-failed bail-out, and replied with garbage.

The real schema (operation/fromDate/toDate/...) lives on
tool.args_schema as a dict. Pull it from there.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool


CALENDAR_DICT_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "description": "Operation to perform",
            "enum": ["search", "open", "list", "create"],
        },
        "fromDate": {"type": "string", "description": "Start date ISO"},
        "toDate": {"type": "string", "description": "End date ISO"},
        "limit": {"type": "number", "description": "Max events"},
    },
    "required": ["operation"],
}


def _make_mcp_like_tool() -> StructuredTool:
    """Approximate what langchain-mcp-adapters returns: a StructuredTool
    whose args_schema is a raw dict (not a Pydantic model)."""

    def _stub(**_kwargs) -> str:
        return "stub"

    tool = StructuredTool.from_function(
        func=_stub,
        name="calendar",
        description="Search, create, and open calendar events in Apple Calendar app",
    )
    # Overwrite args_schema with a dict to mimic mcp-adapters' wrapping
    tool.args_schema = CALENDAR_DICT_SCHEMA
    return tool


def test_convert_tools_preserves_dict_args_schema() -> None:
    from services.codex_chat_model import _convert_tools

    tool = _make_mcp_like_tool()
    converted = _convert_tools([tool])

    assert len(converted) == 1
    entry = converted[0]
    assert entry["name"] == "calendar"
    params = entry["parameters"]
    assert params.get("type") == "object", f"expected object schema, got {params!r}"
    properties = params.get("properties", {})

    # The whole point: the LLM must see the real fields, not a generic
    # anyOf [string, object, ToolCall] fallback.
    for required_key in ("operation", "fromDate", "toDate"):
        assert required_key in properties, (
            f"missing '{required_key}' in converted schema. "
            f"Got properties: {sorted(properties)}. "
            "This is the bug that made apple-mcp 'calendar' calls fail."
        )

    assert "operation" in params.get("required", []), (
        "operation must remain marked required so the LLM doesn't omit it"
    )
