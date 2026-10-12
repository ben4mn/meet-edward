"""
Tests for the robust JSON payload parser (Fix #1 in the stabilization plan).

Background: the current parser used in ``memory_service.py`` and several
other background subsystems is ``re.search(r'\\[[\\s\\S]*?\\]', response_text)``.
The non-greedy match stops at the first ``]`` it encounters, which breaks on:

  1. Nested arrays inside objects: ``{"memories": [{"tags": ["a","b"]}]}``
  2. Dict-wrapped payloads: ``{"memories": [...]}``, ``{"items": [...]}``, etc.
  3. Preamble text before the array.
  4. Markdown code fences.

The new helper, ``services.json_parsing.extract_json_payload``, must handle
all of these robustly.
"""

import pytest

from services import json_parsing
from tests.fixtures import codex_responses as fx


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_array_parses_directly():
    result = json_parsing.extract_json_payload(fx.CLEAN_ARRAY)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["content"] == "Ellis lives in Seattle"


def test_empty_array_returns_empty_list():
    assert json_parsing.extract_json_payload(fx.EMPTY_ARRAY) == []


def test_whitespace_around_array_tolerated():
    assert json_parsing.extract_json_payload(fx.EMPTY_ARRAY_WITH_WHITESPACE) == []


# ---------------------------------------------------------------------------
# Dict-wrapped payloads (the core bug)
# ---------------------------------------------------------------------------


def test_dict_wrapped_memories_key_unwraps():
    result = json_parsing.extract_json_payload(fx.DICT_WRAPPED_MEMORIES)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["content"] == "Ellis prefers dark mode"


def test_dict_wrapped_items_key_unwraps():
    result = json_parsing.extract_json_payload(fx.DICT_WRAPPED_ITEMS)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["content"] == "Ellis prefers tea"


def test_dict_wrapped_results_key_unwraps():
    result = json_parsing.extract_json_payload(fx.DICT_WRAPPED_RESULTS)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["content"] == "Ellis has a cat named Mochi"


def test_nested_array_inside_object_preserved():
    """The exact bug: object with an array containing a nested array.

    Non-greedy regex produces truncated/invalid JSON. The new parser must
    correctly unwrap and preserve the nested tags array.
    """
    result = json_parsing.extract_json_payload(fx.NESTED_ARRAY_IN_OBJECT)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["tags"] == ["language", "code"]


# ---------------------------------------------------------------------------
# Wrapping variants
# ---------------------------------------------------------------------------


def test_preamble_text_before_array_stripped():
    result = json_parsing.extract_json_payload(fx.PREAMBLE_TEXT)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["content"] == "Ellis works on Edward"


def test_markdown_code_fence_json_stripped():
    result = json_parsing.extract_json_payload(fx.MARKDOWN_FENCED)
    assert isinstance(result, list)
    assert result[0]["content"] == "Ellis drinks coffee"


def test_markdown_code_fence_plain_stripped():
    result = json_parsing.extract_json_payload(fx.MARKDOWN_FENCED_PLAIN)
    assert isinstance(result, list)
    assert result[0]["content"] == "Ellis drinks tea"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_malformed_returns_empty_array_for_array_expected():
    assert json_parsing.extract_json_payload(fx.MALFORMED, expected="array") == []


def test_malformed_returns_empty_dict_for_object_expected():
    assert json_parsing.extract_json_payload(fx.MALFORMED, expected="object") == {}


def test_broken_json_returns_empty_array():
    assert json_parsing.extract_json_payload(fx.BROKEN_JSON, expected="array") == []


def test_malformed_logs_warning(caplog):
    """Failed parse must log at WARNING level, not silently return."""
    import logging

    with caplog.at_level(logging.WARNING):
        json_parsing.extract_json_payload(fx.MALFORMED, expected="array")

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, (
        "A failed parse must emit a WARNING log — silent returns were the "
        "original bug that let memory extraction rot for weeks"
    )
    # The log must include at least a snippet of the raw response so ops can
    # diagnose the parse failure without rerunning.
    assert any("json" in r.getMessage().lower() or "parse" in r.getMessage().lower()
               for r in warnings)


# ---------------------------------------------------------------------------
# Non-greedy regex bug — direct reproduction
# ---------------------------------------------------------------------------


def test_nested_array_was_broken_by_old_regex():
    """Sanity check: confirms the old non-greedy regex truncates the
    nested-array fixture. This ensures the fixture actually exercises the bug.

    The regex ``\\[[\\s\\S]*?\\]`` (non-greedy) stops at the FIRST ``]`` it
    sees — in this fixture that's the ``]`` inside ``[Python, Rust, TypeScript]``
    inside the ``content`` string. The result is incomplete JSON that
    ``json.loads`` rejects, and the old code path returned ``[]`` silently.
    """
    import json
    import re

    m = re.search(r"\[[\s\S]*?\]", fx.NESTED_ARRAY_IN_OBJECT)
    assert m is not None
    truncated = m.group()

    # The truncation happens somewhere inside the first object — the regex
    # stops at the first ']' which is inside the content string.
    assert truncated.endswith("]")
    assert len(truncated) < len(fx.NESTED_ARRAY_IN_OBJECT), "Must be a truncation"

    # And the truncated chunk is not valid JSON — which is why the old code
    # caught JSONDecodeError and returned [] silently.
    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated)


# ---------------------------------------------------------------------------
# Object-expected mode
# ---------------------------------------------------------------------------


def test_object_expected_returns_dict():
    payload = '{"key": "value", "nested": {"inner": 1}}'
    result = json_parsing.extract_json_payload(payload, expected="object")
    assert result == {"key": "value", "nested": {"inner": 1}}


def test_object_expected_with_preamble():
    payload = 'Here you go:\n\n{"status": "ok", "count": 5}\n\nCheers.'
    result = json_parsing.extract_json_payload(payload, expected="object")
    assert result == {"status": "ok", "count": 5}


# ---------------------------------------------------------------------------
# Alternate wrapper keys
# ---------------------------------------------------------------------------


def test_clusters_key_unwraps():
    payload = '{"clusters": [{"id": 1}, {"id": 2}]}'
    result = json_parsing.extract_json_payload(payload, expected="array")
    assert result == [{"id": 1}, {"id": 2}]


def test_queries_key_unwraps():
    payload = '{"queries": ["what did owner eat", "where does owner work"]}'
    result = json_parsing.extract_json_payload(payload, expected="array")
    assert result == ["what did owner eat", "where does owner work"]


def test_tags_key_unwraps():
    payload = '{"tags": ["work", "travel", "food"]}'
    result = json_parsing.extract_json_payload(payload, expected="array")
    assert result == ["work", "travel", "food"]


# ---------------------------------------------------------------------------
# Robustness — edge cases that must not crash
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty():
    assert json_parsing.extract_json_payload("", expected="array") == []
    assert json_parsing.extract_json_payload("", expected="object") == {}


def test_none_input_returns_empty():
    assert json_parsing.extract_json_payload(None, expected="array") == []
    assert json_parsing.extract_json_payload(None, expected="object") == {}


def test_valid_object_when_array_expected_and_no_recognized_key():
    """If expected=array and the parsed value is a dict without a known
    wrapper key (memories/items/results/...), return []."""
    payload = '{"unknown": [1, 2, 3]}'
    assert json_parsing.extract_json_payload(payload, expected="array") == []


def test_dict_with_list_value_under_recognized_key_unwraps_even_after_preamble():
    payload = 'Sure! Here:\n\n{"memories": [{"content": "x", "memory_type": "fact"}]}'
    result = json_parsing.extract_json_payload(payload, expected="array")
    assert result == [{"content": "x", "memory_type": "fact"}]
