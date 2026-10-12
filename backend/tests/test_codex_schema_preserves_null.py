"""
Regression: _sanitize_schema stripped nullability off every Optional param,
so the model could never say "not specified" and invented 0 / "" / -1 instead.

Found 2026-08-22. Two P0 symptoms traced to this one line:

  * read_imessage_thread got before_rowid=0 -> "AND m.ROWID < 0" -> zero rows
    -> "no messages found for +1720...", for EVERY chat.
  * search_inbox got category=0 (then -1 on retry) -> wrong model_category
    filter -> "No matching emails found" while the Netflix sign-in code Ellis
    asked for sat in the index the whole time.

Pydantic v2 renders Optional[int] = None as
    {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}
and the anyOf branch of _sanitize_schema flattened that to {"type": "integer"}.

OpenAI strict schemas DO support nullable via a type array
(https://platform.openai.com/docs/guides/structured-outputs) — ["integer", "null"]
is the documented spelling — so we keep the null instead of discarding it.
"""

from __future__ import annotations

import pytest

from services.codex_chat_model import _sanitize_schema


def _types(schema: dict) -> object:
    return schema.get("type")


class TestOptionalStaysNullable:
    def test_anyof_int_or_null_keeps_null(self):
        out = _sanitize_schema(
            {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}
        )
        assert _types(out) == ["integer", "null"]

    def test_anyof_string_or_null_keeps_null(self):
        out = _sanitize_schema({"anyOf": [{"type": "string"}, {"type": "null"}]})
        assert _types(out) == ["string", "null"]

    def test_oneof_is_handled_the_same_way(self):
        out = _sanitize_schema({"oneOf": [{"type": "boolean"}, {"type": "null"}]})
        assert _types(out) == ["boolean", "null"]

    def test_explicit_type_list_with_null_is_preserved(self):
        # The other spelling of the same idea — must not collapse either.
        out = _sanitize_schema({"type": ["integer", "null"]})
        assert _types(out) == ["integer", "null"]

    def test_non_nullable_anyof_is_left_alone(self):
        # No null member -> nothing to preserve, still collapses to one type.
        out = _sanitize_schema({"anyOf": [{"type": "integer"}, {"type": "string"}]})
        assert _types(out) == "integer"

    def test_plain_type_is_untouched(self):
        assert _types(_sanitize_schema({"type": "string"})) == "string"

    def test_nested_property_keeps_null(self):
        out = _sanitize_schema(
            {
                "type": "object",
                "properties": {
                    "before_rowid": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "default": None,
                    },
                    "chat_identifier": {"type": "string"},
                },
            }
        )
        assert _types(out["properties"]["before_rowid"]) == ["integer", "null"]
        assert _types(out["properties"]["chat_identifier"]) == "string"


class TestRealToolSchemas:
    """End-to-end: the two tools whose failures started this investigation."""

    def test_read_imessage_thread_before_rowid_is_nullable(self):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from services.graph.tools import read_imessage_thread

        params = convert_to_openai_tool(read_imessage_thread)["function"]["parameters"]
        sanitized = _sanitize_schema(params)
        assert "null" in _types(sanitized["properties"]["before_rowid"])

    def test_review_heartbeat_sender_is_nullable(self):
        """search_inbox (whose category=0 started this) was deleted on
        2026-10-11; another real Optional argument keeps the end-to-end
        check."""
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from services.graph.tools import review_heartbeat

        params = convert_to_openai_tool(review_heartbeat)["function"]["parameters"]
        sanitized = _sanitize_schema(params)
        assert "null" in _types(sanitized["properties"]["sender"])
