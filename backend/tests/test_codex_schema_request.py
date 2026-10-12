"""D9 (muse-and-beyond plan, 2026-09-24): tool schemas keep what the model
needs, and the request says how the tools may be called.

The sanitiser stripped ``enum`` and ``default`` at every depth and kept
only the first ``anyOf`` branch, so the model guessed values — and every
wrong guess was a new argument set, so the loop's breaker never fired.
Pydantic's Optional fields also lost their ``description`` (``anyOf`` came
first in the dict and returned early). ``parallel_tool_calls`` was never
sent although browser actions depend on order, and no ``prompt_cache_key``
was sent, so Codex could not route a conversation to its warm cache.

The only real constraint is at the root: the parameters schema must be a
plain object, and ``enum`` / ``not`` there were rejected. Nested, enums and
unions are ordinary JSON Schema.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from services.codex_chat_model import _sanitize_schema, _convert_tools

pytestmark = pytest.mark.unit

_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"


def _obj(**props):
    return {"type": "object", "properties": props}


# ------------------------------------------------------------- schema --

def test_nested_enum_survives_but_the_root_enum_does_not():
    out = _sanitize_schema({
        "type": "object",
        "enum": [{"a": 1}],
        "properties": {
            "at": {"type": "string", "enum": ["page", "display"]},
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["click", "type", "key"]},
            }}},
        },
    })
    assert "enum" not in out
    assert out["properties"]["at"]["enum"] == ["page", "display"]
    assert out["properties"]["steps"]["items"]["properties"]["action"]["enum"] == ["click", "type", "key"]


def test_nested_const_survives():
    out = _sanitize_schema(_obj(kind={"type": "string", "const": "mind"}))
    assert out["properties"]["kind"]["const"] == "mind"


def test_default_is_folded_into_the_description():
    out = _sanitize_schema(_obj(
        limit={"type": "integer", "default": 10, "description": "How many rows."},
        mode={"type": "string", "default": "auto"},
        flag={"type": "boolean", "default": False, "description": "Also send it."},
        maybe={"type": "string", "default": None, "description": "Optional."},
    ))
    props = out["properties"]
    assert props["limit"]["description"] == "How many rows. Default: 10."
    assert props["mode"]["description"] == 'Default: "auto".'
    assert props["flag"]["description"] == "Also send it. Default: false."
    assert props["maybe"]["description"] == "Optional.", "a null default says nothing"
    assert all("default" not in p for p in props.values())


def test_pydantic_optional_keeps_its_description_and_null():
    out = _sanitize_schema(_obj(before_rowid={
        "anyOf": [{"type": "integer"}, {"type": "null"}],
        "default": None,
        "description": "Only messages older than this row id.",
        "title": "Before Rowid",
    }))
    prop = out["properties"]["before_rowid"]
    assert prop["type"] == ["integer", "null"]
    assert prop["description"] == "Only messages older than this row id."
    assert "title" not in prop


def test_nullable_enum_accepts_null():
    out = _sanitize_schema(_obj(urgency={
        "anyOf": [{"type": "string", "enum": ["normal", "high"]}, {"type": "null"}], "default": None,
    }))
    prop = out["properties"]["urgency"]
    assert prop["type"] == ["string", "null"]
    assert prop["enum"] == ["normal", "high", None]


def test_a_union_of_simple_types_becomes_a_type_list():
    out = _sanitize_schema(_obj(
        when={"anyOf": [{"type": "string"}, {"type": "integer"}]},
        value={"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}], "description": "x"},
    ))
    assert out["properties"]["when"]["type"] == ["string", "integer"]
    assert out["properties"]["value"]["type"] == ["string", "number", "null"]
    assert out["properties"]["value"]["description"] == "x"


def test_a_union_of_complex_branches_is_kept_as_anyof_not_its_first_branch():
    out = _sanitize_schema(_obj(target={
        "oneOf": [
            {"type": "string", "description": "a URL"},
            {"type": "object", "properties": {"tab_id": {"type": "integer"}}, "required": ["tab_id"]},
        ],
        "description": "What to open.",
    }))
    prop = out["properties"]["target"]
    assert "oneOf" not in prop
    assert [b.get("type") for b in prop["anyOf"]] == ["string", "object"]
    assert prop["anyOf"][1]["properties"]["tab_id"]["type"] == "integer"
    assert prop["description"] == "What to open."


def test_explicit_multi_type_lists_are_kept_whole():
    out = _sanitize_schema(_obj(v={"type": ["string", "integer", "null"]}))
    assert out["properties"]["v"]["type"] == ["string", "integer", "null"]


def test_a_constraint_only_union_keeps_the_object_it_constrains():
    """MCP servers write "one of these is required" as a root anyOf of
    ``required`` lists; flattening to the first branch threw away every
    property."""
    out = _sanitize_schema({
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "anyOf": [{"required": ["id"]}, {"required": ["name"]}],
    })
    assert set(out["properties"]) == {"id", "name"}
    assert out["type"] == "object"


def test_root_union_still_collapses_to_an_object_schema():
    tools = _convert_tools([{"type": "function", "name": "t", "description": "",
                             "parameters": {"anyOf": [_obj(a={"type": "string"}), _obj(b={"type": "integer"})]}}])
    params = tools[0]["parameters"]
    assert params["type"] == "object"
    assert "anyOf" not in params


def test_real_tool_schemas_still_convert():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from services.graph.tools import read_imessage_thread

    params = convert_to_openai_tool(read_imessage_thread)["function"]["parameters"]
    sanitized = _sanitize_schema(params)
    assert sanitized["type"] == "object"
    assert "null" in sanitized["properties"]["before_rowid"]["type"]


def _refs(node):
    """Every ``$ref`` left anywhere in a schema."""
    if isinstance(node, dict):
        out = [node["$ref"]] if "$ref" in node else []
        for v in node.values():
            out += _refs(v)
        return out
    if isinstance(node, list):
        return [r for v in node for r in _refs(v)]
    return []


def test_local_refs_are_inlined_before_defs_are_dropped():
    """Pydantic writes a nested model as ``$defs`` + ``$ref``; the sanitizer
    drops ``$defs``, so the ``$ref`` must be resolved first — a dangling
    ``#/$defs/X`` with an empty object is what the model got for plan's
    items (review 2026-10-11)."""
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}},
        "required": ["items"],
        "$defs": {"Item": {"type": "object", "title": "Item",
                           "properties": {"text": {"type": "string"},
                                          "status": {"enum": ["pending", "done"], "type": "string"}},
                           "required": ["text"]}},
    }
    out = _sanitize_schema(schema)
    assert _refs(out) == []
    item = out["properties"]["items"]["items"]
    assert item["type"] == "object" and set(item["properties"]) == {"text", "status"}
    assert item["required"] == ["text"]
    assert item["properties"]["status"]["enum"] == ["pending", "done"]


def test_a_recursive_or_unresolvable_ref_never_dangles():
    schema = {"type": "object", "properties": {
        "node": {"$ref": "#/$defs/Node"}, "lost": {"$ref": "#/$defs/Nowhere", "description": "gone"}},
        "$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}}}
    out = _sanitize_schema(schema)
    assert _refs(out) == []
    assert out["properties"]["node"]["type"] == "object"
    assert out["properties"]["lost"]["type"] == "object" and out["properties"]["lost"]["description"] == "gone"


def test_the_toolset_converts_with_no_ref():
    """Every tool bound on every turn — plan's ``List[PlanItem]`` included."""
    from services.toolset import build_toolset

    converted = _convert_tools(build_toolset())
    assert _refs(converted) == []
    plan = next(t for t in converted if t["name"] == "plan")
    item = plan["parameters"]["properties"]["items"]["items"]
    assert "text" in item["properties"] and "status" in item["properties"]


# ------------------------------------------------------------ request --

TOOL = {"type": "function", "name": "calendar", "description": "", "parameters": _obj(op={"type": "string"})}


def _body(llm):
    return llm._build_request([HumanMessage(content="x")])[1]


def test_parallel_tool_calls_is_false_by_default_when_tools_are_bound():
    from services.codex_chat_model import ChatCodexResponses

    body = _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN).bind_tools([TOOL]))
    assert body["parallel_tool_calls"] is False
    assert body["tools"][0]["name"] == "calendar"


def test_parallel_tool_calls_is_not_sent_without_tools():
    from services.codex_chat_model import ChatCodexResponses

    assert "parallel_tool_calls" not in _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN))


def test_parallel_tool_calls_is_overridable():
    from services.codex_chat_model import ChatCodexResponses

    on_model = ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN, parallel_tool_calls=True).bind_tools([TOOL])
    assert _body(on_model)["parallel_tool_calls"] is True
    on_bind = ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN).bind_tools([TOOL], parallel_tool_calls=True)
    assert _body(on_bind)["parallel_tool_calls"] is True
    # a later bind keeps the choice
    assert _body(on_model.bind_tools([TOOL]))["parallel_tool_calls"] is True


def test_the_request_body_is_pinned():
    from services.codex_chat_model import ChatCodexResponses

    body = _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN,
                                    reasoning_effort="high").bind_tools([TOOL], tool_choice="auto"))
    assert set(body) == {"model", "instructions", "store", "stream", "input", "include", "reasoning",
                         "tools", "tool_choice", "parallel_tool_calls"}
    assert body["store"] is False and body["stream"] is True
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["reasoning"]["summary"] == "auto"


def test_prompt_cache_key_is_the_turns_conversation():
    from services import turn_ledger
    from services.codex_chat_model import ChatCodexResponses

    rec = turn_ledger.TurnRecord.start(conversation_id="edward-mind", source="mind", model="m", provider="openai")
    token = turn_ledger._current.set(rec)
    try:
        body = _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN))
    finally:
        turn_ledger._current.reset(token)
    assert body["prompt_cache_key"] == "edward-mind"


def test_prompt_cache_key_falls_back_to_the_tool_context_then_to_nothing():
    from services.codex_chat_model import ChatCodexResponses
    from services.graph import tools as graph_tools

    assert "prompt_cache_key" not in _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN))
    token = graph_tools._current_conversation_id.set("conv-42")
    try:
        body = _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN))
    finally:
        graph_tools._current_conversation_id.reset(token)
    assert body["prompt_cache_key"] == "conv-42"


def test_background_calls_share_a_cache_key_by_job():
    """A background job's prompts share their system prompt across every
    conversation, so they are keyed by the job, not by the thread."""
    from services import turn_ledger
    from services.codex_chat_model import ChatCodexResponses

    rec = turn_ledger.TurnRecord.start(conversation_id="conv-7", source="chat", model="m", provider="openai")
    token = turn_ledger._current.set(rec)
    try:
        body = _body(ChatCodexResponses(model="gpt-5.6-terra", api_key=FAKE_TOKEN, priority="background",
                                        purpose="memory_service.extract_memories"))
        chat = _body(ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN))
    finally:
        turn_ledger._current.reset(token)
    assert body["prompt_cache_key"] == "memory_service.extract_memories"
    assert chat["prompt_cache_key"] == "conv-7"
