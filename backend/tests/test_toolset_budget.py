"""The toolset's prompt budget (tools-spec §3.15, §7).

Tools plus the manual stay at or under ~7K tokens, static and cached:
today's static prompt is chat 31.8K / mind 15.4K / pulse 10.4K tokens.
Counted here: the JSON schemas of the custom tools (native and server
tools are counted on Anthropic's side) and ``TOOL_MANUAL``, at ≤ 22,000
characters (≈ 5.5K tokens). No description teaches a retired tool name.
"""

from __future__ import annotations

import json
import re

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from services.toolset import build_toolset, names
from services.toolset.manual import TOOL_MANUAL

pytestmark = pytest.mark.unit

BUDGET_CHARS = 22_000
ALL_ON = {"shell_interpreter": True, "brave_search": True, "imessage_applescript": True}


def _custom(tools):
    return [t for t in tools if not (t.metadata or {}).get("anthropic_tool")]


def test_custom_schemas_plus_manual_fit_the_budget():
    tools = build_toolset(ALL_ON)
    schema_chars = sum(len(json.dumps(convert_to_openai_tool(t))) for t in _custom(tools))
    total = schema_chars + len(TOOL_MANUAL)
    assert total <= BUDGET_CHARS, f"{total} chars ({schema_chars} schemas + {len(TOOL_MANUAL)} manual)"


def test_the_manual_is_about_1_2k_tokens():
    assert 3_000 <= len(TOOL_MANUAL) <= 7_000


def test_the_manual_says_how_to_work():
    text = TOOL_MANUAL.lower()
    for phrase in ("make a plan", "real result", "re-plan", "another route", "never give up silently"):
        assert phrase in text


def test_no_description_teaches_a_retired_name():
    # ``remember`` and ``stand_down`` are actions of the new tools as well.
    retired = sorted(names.RETIRED_NAMES - set(names.TOOL_ORDER) - {"remember", "stand_down"})
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, retired)) + r")\b")
    for t in build_toolset(ALL_ON):
        assert not pattern.search(t.description or ""), (t.name, pattern.search(t.description).group(0))
    assert not pattern.search(TOOL_MANUAL), pattern.search(TOOL_MANUAL).group(0)


def test_the_manual_names_every_tool():
    for name in names.TOOL_ORDER:
        assert f"- {name}" in TOOL_MANUAL or name in TOOL_MANUAL
