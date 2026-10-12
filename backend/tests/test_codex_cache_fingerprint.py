"""Prompt-cache diagnostics (2026-09-27, followups L6).

Only 4.7–5.7 % of Codex input tokens were cached (the mind 3 %, the pulse
14 %) although ``compose_system_prompt`` already puts the static parts
first. Something early in the prefix changes between calls — the tools'
order, the cache key, per-wake text in ``base`` — and nothing recorded
enough to say what. Every request now logs one ``[codex-cache]`` line:
short hashes of the first 1K / 4K / 16K characters of the instructions and
of the serialized tools, the ``prompt_cache_key``, and the cached share the
response reported, so two consecutive lines show where the prompts first
differ. No behaviour changes.
"""

from __future__ import annotations

import logging

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from tests.test_codex_stream_errors import (  # noqa: F401  (ccm is a fixture)
    _FakeStreamResponse, _client, _completed, _llm, _text, ccm,
)

pytestmark = pytest.mark.unit

TOOL_A = {"type": "function", "name": "remember", "description": "Remember something.",
          "parameters": {"type": "object", "properties": {"content": {"type": "string"}}}}
TOOL_B = {"type": "function", "name": "leave_note", "description": "Leave Ellis a note on Today.",
          "parameters": {"type": "object", "properties": {"title": {"type": "string"}}}}
BASE = "You are Edward. " * 1500          # 24K characters


def _body(instructions=BASE, tools=(TOOL_A, TOOL_B), key="edward-mind"):
    return {"model": "gpt-6-astra", "instructions": instructions, "tools": [dict(t) for t in tools],
            "prompt_cache_key": key, "input": [{"role": "user", "content": "the pack"}]}


def test_identical_prompts_fingerprint_the_same(ccm):
    a, b = ccm.prompt_fingerprint(_body()), ccm.prompt_fingerprint(_body())
    assert a == b
    assert set(a["instructions"]) == {"1k", "4k", "16k"} and set(a["tools"]) == {"1k", "4k", "16k"}
    assert all(len(h) == 8 for h in list(a["instructions"].values()) + list(a["tools"].values()))
    assert a["key"] == "edward-mind" and a["n_tools"] == 2 and a["instructions_chars"] == len(BASE)


def test_a_changed_or_reordered_tool_changes_the_tools_hashes_not_the_instructions(ccm):
    base = ccm.prompt_fingerprint(_body())
    changed = ccm.prompt_fingerprint(_body(tools=(TOOL_A, {**TOOL_B, "description": "Leave a note."})))
    reordered = ccm.prompt_fingerprint(_body(tools=(TOOL_B, TOOL_A)))
    assert changed["instructions"] == base["instructions"]
    assert changed["tools"] != base["tools"]
    assert reordered["tools"]["1k"] != base["tools"]["1k"], "the cache sees the order the tools are sent in"


def test_the_prefix_hashes_show_where_the_instructions_first_differ(ccm):
    changed = BASE[:5000] + "Now: Sunday 9:14 am. " + BASE[5000:]
    a, b = ccm.prompt_fingerprint(_body()), ccm.prompt_fingerprint(_body(instructions=changed))
    assert a["instructions"]["1k"] == b["instructions"]["1k"]
    assert a["instructions"]["4k"] == b["instructions"]["4k"]
    assert a["instructions"]["16k"] != b["instructions"]["16k"]
    assert a["tools"] == b["tools"]


def test_a_request_without_tools_or_key_still_fingerprints(ccm):
    fp = ccm.prompt_fingerprint({"instructions": "short"})
    assert fp["key"] is None and fp["n_tools"] == 0
    assert fp["instructions"]["1k"] == fp["instructions"]["16k"], "shorter than every prefix"


async def test_each_request_logs_one_line_with_its_fingerprint_and_cache_hit(ccm, monkeypatch, caplog):
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=[_text("Hi."), _completed({
        "input_tokens": 1200, "input_tokens_details": {"cached_tokens": 1024}, "output_tokens": 2})])])
    caplog.set_level(logging.INFO, logger=ccm.logger.name)
    await _llm(ccm)._agenerate([SystemMessage(content="You are Edward."), HumanMessage(content="hi")])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[codex-cache]")]
    assert len(lines) == 1, lines
    fp = ccm.prompt_fingerprint({"instructions": "You are Edward.", "tools": []})
    assert fp["instructions"]["1k"] in lines[0] and fp["tools"]["1k"] in lines[0]
    assert "in=1200" in lines[0] and "cached=1024" in lines[0]
