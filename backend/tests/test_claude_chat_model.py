"""ChatClaude — Edward's LangChain adapter over the Anthropic Messages API.

It has to fit the contract ``ChatCodexResponses`` set and the tool loops
read (``services/graph/streaming.py``):

* text deltas are ``AIMessageChunk.content``; thinking summaries are
  ``additional_kwargs["reasoning_delta"]`` (the work rail's ``reasoning``
  event); partial tool-call JSON is ``additional_kwargs["tool_args_delta"]``
  (the ``tool_args_delta`` event); complete tool calls arrive once, on the
  final chunk, as ``tool_call_chunks`` with the block index — the loop
  collects ``chunk.tool_calls`` from every chunk, so a partial call on a
  delta chunk would be executed;
* the raw Anthropic content blocks of a response ride
  ``additional_kwargs["claude_content"]`` and are replayed verbatim within
  the turn that produced them (thinking signatures included), never after
  it; ``strip_claude_thinking`` keeps checkpoints from growing with them;
* thinking summaries also ride ``additional_kwargs["reasoning_items"]`` in
  the Codex shape, so the trace, the journal and the screen read them;
* ``usage_metadata`` is LangChain-standard with cache reads and writes.

Every test drives a fake ``AsyncAnthropic`` (tests/fixtures/fake_anthropic);
nothing reaches anthropic.com.
"""

from __future__ import annotations

import json
import logging

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from tests.fixtures.fake_anthropic import (
    FakeAnthropic, FakeStreamResponse, connection_error, message, raw_block, redacted, status_error, text,
    thinking, tool_use,
)

pytestmark = pytest.mark.unit

STATIC = "You are Edward. " * 40


def _model(client, **kw):
    from services.claude_chat_model import ChatClaude

    kw.setdefault("model", "claude-opus-5-5")
    kw.setdefault("effort", "medium")
    return ChatClaude(client=client, **kw)


def _system(dynamic: str = "Current time: 7:34 pm") -> SystemMessage:
    from services.claude_chat_model import STATIC_DYNAMIC_SPLIT

    return SystemMessage(content=STATIC + STATIC_DYNAMIC_SPLIT + dynamic)


def _convert(messages, **kw):
    from services.claude_chat_model import to_anthropic_messages

    kw.setdefault("model", "claude-opus-5-5")
    return to_anthropic_messages(messages, **kw)


def _ai_with_blocks(blocks, tool_calls=None, text_content=""):
    from services.claude_chat_model import CLAUDE_CONTENT_KEY

    return AIMessage(content=text_content, tool_calls=tool_calls or [],
                     additional_kwargs={CLAUDE_CONTENT_KEY: blocks})


# =============================================================== conversion ==

class TestSystemPrompt:
    def test_the_static_part_is_one_cached_block_and_the_dynamic_part_follows_the_human(self):
        system, msgs = _convert([_system("Current time: 7:34 pm"), HumanMessage(content="hi")])
        assert system == [{"type": "text", "text": STATIC, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
        assert msgs[0]["role"] == "user"
        assert msgs[1] == {"role": "system", "content": "Current time: 7:34 pm"}
        assert len(msgs) == 2
        assert "7:34" not in json.dumps(system)

    def test_without_the_marker_the_whole_prompt_is_one_cached_block(self):
        system, msgs = _convert([SystemMessage(content="Just a prompt."), HumanMessage(content="hi")])
        assert system == [{"type": "text", "text": "Just a prompt.",
                           "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
        assert [m["role"] for m in msgs] == ["user"]

    def test_the_static_ttl_is_configurable_and_can_be_off(self):
        system, _ = _convert([SystemMessage(content="P"), HumanMessage(content="hi")], cache_ttl="5m")
        assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
        system, _ = _convert([SystemMessage(content="P"), HumanMessage(content="hi")], cache_ttl=None)
        assert "cache_control" not in system[0]

    def test_the_dynamic_context_sits_after_the_last_human_in_a_tool_loop(self):
        tc = {"id": "toolu_1", "name": "web_search", "args": {"q": "x"}, "type": "tool_call"}
        msgs = [
            _system("DYN"),
            HumanMessage(content="first"),
            AIMessage(content="first answer"),
            HumanMessage(content="second"),
            AIMessage(content="", tool_calls=[tc]),
            ToolMessage(content="result", tool_call_id="toolu_1"),
        ]
        _, out = _convert(msgs)
        roles = [m["role"] for m in out]
        assert roles == ["user", "assistant", "user", "system", "assistant", "user"]
        assert out[3]["content"] == "DYN"
        assert out[2]["content"][-1]["text"] == "second"
        # Valid placement: after a user message, followed by an assistant turn.
        assert out[2]["role"] == "user" and out[4]["role"] == "assistant"

    def test_models_without_mid_conversation_system_messages_get_a_second_system_block(self):
        system, msgs = _convert([_system("DYN"), HumanMessage(content="hi")], model="claude-opus-4-1")
        assert system[0]["text"] == STATIC
        assert system[1] == {"type": "text", "text": "DYN"}
        assert [m["role"] for m in msgs] == ["user"]

    def test_the_last_human_message_carries_the_tail_cache_breakpoint(self):
        _, out = _convert([_system(), HumanMessage(content="older"), AIMessage(content="ok"),
                           HumanMessage(content="newest")])
        marked = [(i, b) for i, m in enumerate(out) if isinstance(m["content"], list)
                  for b in m["content"] if "cache_control" in b]
        assert len(marked) == 1
        assert marked[0][1]["text"] == "newest"
        assert marked[0][1]["cache_control"] == {"type": "ephemeral"}


class TestAssistantTurns:
    def test_thinking_is_replayed_verbatim_only_in_the_current_turn(self):
        old_blocks = [{"type": "thinking", "thinking": "old plan", "signature": "sig-old"},
                      {"type": "text", "text": "Old answer."}]
        cur_tc = {"id": "toolu_9", "name": "web_search", "args": {"q": "now"}, "type": "tool_call"}
        cur_blocks = [{"type": "thinking", "thinking": "new plan", "signature": "sig-new"},
                      {"type": "text", "text": "Let me look."},
                      {"type": "tool_use", "id": "toolu_9", "name": "web_search", "input": {"q": "now"}}]
        msgs = [
            _system(),
            HumanMessage(content="before"),
            _ai_with_blocks(old_blocks, text_content="Old answer."),
            HumanMessage(content="now"),
            _ai_with_blocks(cur_blocks, tool_calls=[cur_tc], text_content="Let me look."),
            ToolMessage(content="found it", tool_call_id="toolu_9"),
        ]
        _, out = _convert(msgs)
        old = out[1]
        assert old["role"] == "assistant"
        assert old["content"] == [{"type": "text", "text": "Old answer."}]
        current = [m for m in out if m["role"] == "assistant"][-1]
        assert current["content"] == cur_blocks

    def test_older_turns_are_rebuilt_from_text_and_tool_calls_dropping_server_blocks(self):
        tc = {"id": "toolu_a", "name": "remember_search", "args": {"query": "tea"}, "type": "tool_call"}
        blocks = [{"type": "thinking", "thinking": "t", "signature": "s"},
                  {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "x"}},
                  {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []},
                  {"type": "text", "text": "Checking."},
                  {"type": "tool_use", "id": "toolu_a", "name": "remember_search", "input": {"query": "tea"}}]
        msgs = [_system(), HumanMessage(content="q"), _ai_with_blocks(blocks, tool_calls=[tc], text_content="Checking."),
                ToolMessage(content="Ellis likes tea", tool_call_id="toolu_a"), AIMessage(content="He likes tea."),
                HumanMessage(content="next")]
        _, out = _convert(msgs)
        assert out[1]["content"] == [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_a", "name": "remember_search", "input": {"query": "tea"}},
        ]

    def test_raw_blocks_whose_tool_uses_do_not_match_the_tool_calls_are_rebuilt(self):
        tc = {"id": "toolu_b", "name": "web_search", "args": {"q": "y"}, "type": "tool_call"}
        blocks = [{"type": "thinking", "thinking": "t", "signature": "s"},
                  {"type": "tool_use", "id": "toolu_OTHER", "name": "web_search", "input": {"q": "y"}}]
        msgs = [_system(), HumanMessage(content="q"), _ai_with_blocks(blocks, tool_calls=[tc]),
                ToolMessage(content="r", tool_call_id="toolu_b")]
        _, out = _convert(msgs)
        assert out[2]["content"] == [{"type": "tool_use", "id": "toolu_b", "name": "web_search", "input": {"q": "y"}}]

    def test_after_a_fallback_only_the_continuation_and_paired_server_blocks_are_echoed(self):
        tc = {"id": "toolu_new", "name": "web_search", "args": {"q": "z"}, "type": "tool_call"}
        blocks = [
            {"type": "thinking", "thinking": "declined path", "signature": "s1"},
            {"type": "text", "text": "Partial "},
            {"type": "tool_use", "id": "toolu_old", "name": "web_search", "input": {"q": "old"}},
            {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}},
            {"type": "text", "text": "continued."},
            {"type": "tool_use", "id": "toolu_new", "name": "web_search", "input": {"q": "z"}},
        ]
        msgs = [_system(), HumanMessage(content="q"), _ai_with_blocks(blocks, tool_calls=[tc]),
                ToolMessage(content="r", tool_call_id="toolu_new")]
        _, out = _convert(msgs)
        assert out[2]["content"] == [
            {"type": "text", "text": "Partial "},
            {"type": "text", "text": "continued."},
            {"type": "tool_use", "id": "toolu_new", "name": "web_search", "input": {"q": "z"}},
        ]

    def test_a_loop_note_never_moves_the_replay_boundary(self):
        """The loop's own notes (the closing call, the step-back, the reply
        check) are scaffolding, not Ellis speaking: the turn's thinking and
        server-tool blocks still replay, the note rides in the tool-result
        turn, and the tail breakpoint and per-turn context stay on Ellis's
        message (review 2026-10-11)."""
        from services.graph.history_window import scaffolding_note

        tc = {"id": "toolu_1", "name": "bash", "args": {"command": "ls"}, "type": "tool_call"}
        blocks = [{"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                  {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "x"}},
                  {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []},
                  {"type": "text", "text": "Found it; checking disk."},
                  {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"command": "ls"}}]
        msgs = [_system("DYN"), HumanMessage(content="research this"),
                _ai_with_blocks(blocks, tool_calls=[tc], text_content="Found it; checking disk."),
                ToolMessage(content="a b c", tool_call_id="toolu_1"),
                scaffolding_note("[SYSTEM] Stop here: this turn has spent its budget.")]
        _, out = _convert(msgs)
        assistant = [m for m in out if m["role"] == "assistant"][-1]
        assert assistant["content"] == blocks
        assert out[-1]["role"] == "user"
        assert [b["type"] for b in out[-1]["content"]] == ["tool_result", "text"]
        assert out[-1]["content"][-1]["text"].startswith("[SYSTEM] Stop here")
        assert out[0]["content"][-1]["text"] == "research this" and "cache_control" in out[0]["content"][-1]
        assert out[1] == {"role": "system", "content": "DYN"}
        assert not any("cache_control" in b for b in out[-1]["content"])

    def test_owners_own_next_message_still_closes_the_turn(self):
        tc = {"id": "toolu_2", "name": "bash", "args": {}, "type": "tool_call"}
        blocks = [{"type": "thinking", "thinking": "p", "signature": "s"},
                  {"type": "tool_use", "id": "toolu_2", "name": "bash", "input": {}}]
        msgs = [_system(), HumanMessage(content="q"), _ai_with_blocks(blocks, tool_calls=[tc]),
                ToolMessage(content="r", tool_call_id="toolu_2"), AIMessage(content="done"),
                HumanMessage(content="next")]
        _, out = _convert(msgs)
        assert out[1]["content"] == [{"type": "tool_use", "id": "toolu_2", "name": "bash", "input": {}}]

    def test_empty_assistant_messages_are_dropped_and_adjacent_user_turns_merge(self):
        _, out = _convert([_system(), HumanMessage(content="a"), AIMessage(content=""),
                           HumanMessage(content="[SYSTEM] say something")])
        assert [m["role"] for m in out] == ["user", "system"]
        assert [b["text"] for b in out[0]["content"]] == ["a", "[SYSTEM] say something"]


class TestToolResults:
    def test_consecutive_tool_results_go_in_one_user_message_and_errors_are_flagged(self):
        calls = [{"id": "toolu_1", "name": "a", "args": {}, "type": "tool_call"},
                 {"id": "toolu_2", "name": "b", "args": {}, "type": "tool_call"},
                 {"id": "toolu_3", "name": "c", "args": {}, "type": "tool_call"}]
        msgs = [_system(), HumanMessage(content="do three"), AIMessage(content="", tool_calls=calls),
                ToolMessage(content="ok", tool_call_id="toolu_1"),
                ToolMessage(content="Tool error: a timed out after 45s", tool_call_id="toolu_2"),
                ToolMessage(content="bad", tool_call_id="toolu_3", status="error")]
        _, out = _convert(msgs)
        results = out[-1]
        assert results["role"] == "user"
        assert [b["tool_use_id"] for b in results["content"]] == ["toolu_1", "toolu_2", "toolu_3"]
        assert [b.get("is_error", False) for b in results["content"]] == [False, True, True]
        assert all(b["type"] == "tool_result" for b in results["content"])

    def test_a_tool_use_with_no_result_gets_an_error_result_so_the_request_stays_valid(self):
        calls = [{"id": "toolu_1", "name": "a", "args": {}, "type": "tool_call"},
                 {"id": "toolu_2", "name": "b", "args": {}, "type": "tool_call"}]
        msgs = [_system(), HumanMessage(content="go"), AIMessage(content="", tool_calls=calls),
                ToolMessage(content="ok", tool_call_id="toolu_1"), HumanMessage(content="and?")]
        _, out = _convert(msgs)
        results = out[2]
        ids = [b["tool_use_id"] for b in results["content"] if b.get("type") == "tool_result"]
        assert ids == ["toolu_1", "toolu_2"]
        assert results["content"][1]["is_error"] is True

    def test_images_inside_tool_results(self):
        tc = {"id": "toolu_1", "name": "look", "args": {}, "type": "tool_call"}
        content = [{"type": "text", "text": "the screen"},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]
        msgs = [_system(), HumanMessage(content="look"), AIMessage(content="", tool_calls=[tc]),
                ToolMessage(content=content, tool_call_id="toolu_1")]
        _, out = _convert(msgs)
        block = out[-1]["content"][0]
        assert block["content"] == [
            {"type": "text", "text": "the screen"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        ]


class TestUserContent:
    def test_image_shapes_become_anthropic_image_blocks(self):
        human = HumanMessage(content=[
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "input_image", "image_url": "data:image/png;base64,BBBB"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/webp", "data": "CCCC"}},
            {"type": "image", "source_type": "base64", "mime_type": "image/gif", "data": "DDDD"},
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        ])
        _, out = _convert([_system(), human])
        blocks = out[0]["content"]
        assert blocks[0]["text"] == "what is this"
        assert blocks[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
        assert blocks[2] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "BBBB"}}
        assert blocks[3] == {"type": "image", "source": {"type": "base64", "media_type": "image/webp", "data": "CCCC"}}
        assert blocks[4] == {"type": "image", "source": {"type": "base64", "media_type": "image/gif", "data": "DDDD"}}
        assert blocks[5]["type"] == "image"
        assert blocks[5]["source"] == {"type": "url", "url": "https://example.com/cat.png"}

    def test_pdfs_become_document_blocks_without_extra_keys(self):
        human = HumanMessage(content=[
            {"type": "text", "text": "[Uploaded PDF: a.pdf]"},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "UERG",
                                            "filename": "a.pdf"}},
            {"type": "file", "source_type": "base64", "mime_type": "application/pdf", "data": "UERH"},
        ])
        _, out = _convert([_system(), human])
        docs = [b for b in out[0]["content"] if b["type"] == "document"]
        assert docs[0]["source"] == {"type": "base64", "media_type": "application/pdf", "data": "UERG"}
        assert docs[1]["source"] == {"type": "base64", "media_type": "application/pdf", "data": "UERH"}

    def test_an_unsupported_image_type_becomes_a_note(self):
        human = HumanMessage(content=[{"type": "image", "source": {"type": "base64", "media_type": "image/heic",
                                                                     "data": "ZZZ"}}])
        _, out = _convert([_system(), human])
        assert out[0]["content"][0]["type"] == "text"
        assert "image/heic" in out[0]["content"][0]["text"]


class TestWindowEdges:
    def test_a_window_that_starts_with_an_assistant_gets_a_user_placeholder(self):
        from services.claude_chat_model import EARLIER_CONVERSATION_PLACEHOLDER

        _, out = _convert([_system(), AIMessage(content="earlier answer"), HumanMessage(content="hi")])
        assert out[0] == {"role": "user", "content": [{"type": "text", "text": EARLIER_CONVERSATION_PLACEHOLDER}]}
        assert out[1]["role"] == "assistant"

    def test_orphan_tool_results_are_dropped(self):
        from services.claude_chat_model import EARLIER_CONVERSATION_PLACEHOLDER

        msgs = [_system(), ToolMessage(content="stale", tool_call_id="toolu_gone"),
                AIMessage(content="Done."), HumanMessage(content="next")]
        _, out = _convert(msgs)
        assert "tool_result" not in json.dumps(out)
        assert out[0]["content"][0]["text"] == EARLIER_CONVERSATION_PLACEHOLDER
        assert [m["role"] for m in out] == ["user", "assistant", "user", "system"]

    def test_tool_ids_outside_the_api_pattern_are_mapped_consistently(self):
        tc = {"id": "call:weird/id", "name": "a", "args": {}, "type": "tool_call"}
        msgs = [_system(), HumanMessage(content="go"), AIMessage(content="", tool_calls=[tc]),
                ToolMessage(content="ok", tool_call_id="call:weird/id")]
        _, out = _convert(msgs)
        use_id = out[2]["content"][0]["id"]
        assert use_id == out[3]["content"][0]["tool_use_id"]
        assert all(ch.isalnum() or ch in "_-" for ch in use_id)


# ================================================================== tools ==

def _fake_tools():
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field

    @tool
    def zeta_tool(query: str) -> str:
        """Search zeta."""
        return query

    @tool
    def alpha_tool(n: int = 3) -> str:
        """Count alpha."""
        return str(n)

    class GetWeather(BaseModel):
        """Weather for a city."""

        city: str = Field(description="City name")

    openai_style = {"type": "function", "function": {"name": "mid_tool", "description": "Mid.",
                                                     "parameters": {"type": "object",
                                                                    "properties": {"x": {"type": "string"}}}}}
    server = {"type": "web_search_20260209", "name": "web_search"}
    return [zeta_tool, alpha_tool, GetWeather, openai_style, server]


class TestTools:
    def test_tools_are_converted_and_sorted_by_name_with_server_tools_passed_through(self):
        from services.claude_chat_model import convert_tools

        out = convert_tools(_fake_tools())
        assert [t["name"] for t in out] == ["GetWeather", "alpha_tool", "mid_tool", "web_search", "zeta_tool"]
        by_name = {t["name"]: t for t in out}
        assert by_name["web_search"] == {"type": "web_search_20260209", "name": "web_search"}
        assert by_name["zeta_tool"]["input_schema"]["type"] == "object"
        assert "query" in by_name["zeta_tool"]["input_schema"]["properties"]
        assert by_name["zeta_tool"]["description"].startswith("Search zeta")
        assert by_name["mid_tool"]["input_schema"]["properties"] == {"x": {"type": "string"}}
        assert by_name["GetWeather"]["input_schema"]["properties"]["city"]["type"] == "string"

    def test_a_root_level_union_is_flattened_to_an_object(self):
        from services.claude_chat_model import convert_tools

        mcp = {"name": "calendar", "description": "Cal.", "input_schema": {
            "type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "anyOf": [{"required": ["a"]}, {"required": ["b"]}]}}
        out = convert_tools([mcp])
        schema = out[0]["input_schema"]
        assert schema["type"] == "object"
        assert "anyOf" not in schema and set(schema["properties"]) == {"a", "b"}

    async def test_bind_tools_sends_them_and_keeps_the_model_settings(self):
        client = FakeAnthropic([message([text("ok")])])
        llm = _model(client, purpose="mind:morning", priority="interactive").bind_tools(_fake_tools())
        assert llm.purpose == "mind:morning"
        await llm.ainvoke([_system(), HumanMessage(content="hi")])
        sent = client.calls[0]
        assert [t["name"] for t in sent["tools"]][:2] == ["GetWeather", "alpha_tool"]


class TestToolChoice:
    async def _sent(self, model, choice, parallel=False):
        client = FakeAnthropic([message([text("ok")], model=model)])
        llm = _model(client, model=model, parallel_tool_calls=parallel).bind_tools(_fake_tools()[:2], tool_choice=choice)
        await llm.ainvoke([_system(), HumanMessage(content="hi")])
        return client.calls[0].get("tool_choice")

    @pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5"])
    @pytest.mark.parametrize("choice", ["required", "any", "zeta_tool", {"type": "function", "name": "zeta_tool"}])
    async def test_forced_choices_become_auto_on_opus_and_sonnet_5_5(self, model, choice, caplog):
        caplog.set_level(logging.INFO)
        sent = await self._sent(model, choice)
        assert sent["type"] == "auto"

    async def test_none_is_kept(self):
        assert (await self._sent("claude-opus-5-5", "none")) == {"type": "none"}

    async def test_haiku_may_be_forced(self):
        assert (await self._sent("claude-haiku-5-5", "zeta_tool"))["type"] == "tool"
        assert (await self._sent("claude-haiku-5-5", "zeta_tool"))["name"] == "zeta_tool"
        assert (await self._sent("claude-haiku-5-5", "required"))["type"] == "any"

    async def test_one_call_per_response_unless_parallel_is_allowed(self):
        sent = await self._sent("claude-opus-5-5", None)
        assert sent == {"type": "auto", "disable_parallel_tool_use": True}
        sent = await self._sent("claude-opus-5-5", None, parallel=True)
        assert sent is None or "disable_parallel_tool_use" not in sent


# ================================================================ request ==

class TestRequestShape:
    async def test_thinking_effort_cache_and_beta_headers(self):
        client = FakeAnthropic([message([text("ok")])])
        await _model(client, effort="low").ainvoke([_system(), HumanMessage(content="hi")])
        sent = client.calls[0]
        assert sent["model"] == "claude-opus-5-5"
        assert sent["stream"] is True
        assert sent["max_tokens"] >= 16000
        body = sent["extra_body"]
        assert body["thinking"] == {"type": "adaptive", "display": "summarized",
                                    "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
        assert body["output_config"] == {"effort": "low"}
        assert body["cache_control"] == {"type": "ephemeral"}
        betas = sent["extra_headers"]["anthropic-beta"].split(",")
        assert "thinking-binding-controls-2026-08-01" in betas
        for forbidden in ("temperature", "top_p", "top_k"):
            assert forbidden not in sent and forbidden not in body

    async def test_display_can_be_omitted(self):
        client = FakeAnthropic([message([text("ok")])])
        await _model(client, thinking_display="omitted").ainvoke([HumanMessage(content="hi")])
        assert client.calls[0]["extra_body"]["thinking"]["display"] == "omitted"

    @pytest.mark.parametrize("model,expected", [("claude-opus-5-5", True), ("claude-sonnet-5-5", True),
                                                ("claude-haiku-5-5", False)])
    async def test_server_side_fallbacks_only_for_opus_and_sonnet_5_5(self, model, expected):
        client = FakeAnthropic([message([text("ok")], model=model)])
        await _model(client, model=model).ainvoke([HumanMessage(content="hi")])
        sent = client.calls[0]
        betas = sent["extra_headers"]["anthropic-beta"].split(",")
        assert (sent["extra_body"].get("fallbacks") == "default") is expected
        assert ("server-side-fallback-2026-07-01" in betas) is expected

    async def test_a_1h_tail_is_never_sent_behind_a_5m_static_block(self):
        client = FakeAnthropic([message([text("ok")])])
        await _model(client, cache_ttl="5m", tail_cache_ttl="1h").ainvoke([_system(), HumanMessage(content="hi")])
        sent = client.calls[0]
        assert sent["system"][0]["cache_control"]["ttl"] == "5m"
        assert sent["extra_body"]["cache_control"] == {"type": "ephemeral"}

    async def test_no_prefill_a_trailing_assistant_message_is_not_sent_last(self):
        client = FakeAnthropic([message([text("ok")])])
        await _model(client).ainvoke([HumanMessage(content="hi"), AIMessage(content="Sure, here")])
        sent = client.calls[0]["messages"]
        assert sent[-1]["role"] != "assistant"


# =============================================================== responses ==

class TestResponses:
    async def test_text_thinking_usage_and_metadata(self):
        usage = {"input_tokens": 50, "output_tokens": 30, "cache_read_input_tokens": 900,
                 "cache_creation_input_tokens": 200}
        client = FakeAnthropic([message([thinking("Weighing the options.", "sig-1"), text("Hello Ellis.")],
                                        usage=usage, msg_id="msg_9")])
        msg = await _model(client).ainvoke([_system(), HumanMessage(content="hi")])
        from services.claude_chat_model import CLAUDE_CONTENT_KEY

        assert msg.content == "Hello Ellis."
        assert msg.tool_calls == []
        blocks = msg.additional_kwargs[CLAUDE_CONTENT_KEY]
        assert blocks[0] == {"type": "thinking", "thinking": "Weighing the options.", "signature": "sig-1"}
        assert blocks[1] == {"type": "text", "text": "Hello Ellis."}
        items = msg.additional_kwargs["reasoning_items"]
        assert items[0]["summary"] == [{"type": "summary_text", "text": "Weighing the options."}]
        um = msg.usage_metadata
        assert um["input_tokens"] == 50 + 900 + 200
        assert um["output_tokens"] == 30
        assert um["total_tokens"] == 50 + 900 + 200 + 30
        assert um["input_token_details"] == {"cache_read": 900, "cache_creation": 200}
        meta = msg.response_metadata
        assert meta["stop_reason"] == "end_turn"
        assert meta["model"] == "claude-opus-5-5"
        assert meta["id"] == "msg_9"
        assert meta["request_id"] == "req_test"

    async def test_reasoning_texts_reach_the_journal_and_the_trace(self):
        from services.graph.turn_trace import _reasoning_texts
        from services.turn_ledger import reasoning_texts

        client = FakeAnthropic([message([thinking("Checking the calendar."), text("Done.")])])
        msg = await _model(client).ainvoke([HumanMessage(content="hi")])
        assert reasoning_texts(msg) == ["Checking the calendar."]
        assert _reasoning_texts(msg) == ["Checking the calendar."]

    async def test_tool_use_becomes_tool_calls(self):
        from services.claude_chat_model import CLAUDE_CONTENT_KEY

        client = FakeAnthropic([message([thinking("Need the web."), text("Let me look."),
                                         tool_use("toolu_1", "web_search", {"query": "flights"})],
                                        stop_reason="tool_use")])
        msg = await _model(client).ainvoke([HumanMessage(content="flights?")])
        assert msg.content == "Let me look."
        assert msg.tool_calls == [{"name": "web_search", "args": {"query": "flights"}, "id": "toolu_1",
                                   "type": "tool_call"}]
        assert msg.additional_kwargs[CLAUDE_CONTENT_KEY][2] == {
            "type": "tool_use", "id": "toolu_1", "name": "web_search", "input": {"query": "flights"}}
        assert msg.response_metadata["stop_reason"] == "tool_use"

    async def test_unparseable_tool_input_is_marked_not_run_blind(self):
        from services.codex_chat_model import INVALID_JSON_KEY

        client = FakeAnthropic([message([tool_use("toolu_1", "save_document", None, raw_json='{"title": "x", ')],
                                        stop_reason="tool_use")])
        msg = await _model(client).ainvoke([HumanMessage(content="save")])
        assert INVALID_JSON_KEY in msg.tool_calls[0]["args"]

    async def test_a_refusal_is_an_honest_answer_never_an_exception(self):
        from services.claude_chat_model import REFUSAL_NOTE

        client = FakeAnthropic([message([text("Partial "), tool_use("toolu_1", "web_search", {"q": "x"})],
                                        stop_reason="refusal",
                                        stop_details={"type": "refusal", "category": "cyber", "explanation": None})])
        msg = await _model(client).ainvoke([HumanMessage(content="?")])
        assert msg.content == REFUSAL_NOTE
        assert msg.tool_calls == []
        assert msg.response_metadata["stop_reason"] == "refusal"
        assert msg.response_metadata["stop_details"]["category"] == "cyber"

    async def test_max_tokens_returns_the_partial_answer_without_tool_calls(self):
        client = FakeAnthropic([message([text("Half an ans"), tool_use("toolu_1", "web_search", {"q": "x"})],
                                        stop_reason="max_tokens")])
        msg = await _model(client).ainvoke([HumanMessage(content="?")])
        assert msg.content == "Half an ans"
        assert msg.tool_calls == []
        assert msg.additional_kwargs["incomplete_reason"] == "max_tokens"

    async def test_pause_turn_is_resumed_with_the_partial_turn_appended(self):
        srv = tool_use("srvtoolu_1", "web_search", {"query": "news"}, server=True)
        first = message([text("Searching. "), srv], stop_reason="pause_turn", msg_id="m1",
                        usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0})
        result = raw_block({"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []})
        second = message([result, text("Found it.")], stop_reason="end_turn", msg_id="m2",
                         usage={"input_tokens": 20, "output_tokens": 7, "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0})
        client = FakeAnthropic([first, second])
        msg = await _model(client).ainvoke([_system(), HumanMessage(content="news?")])
        assert len(client.calls) == 2
        resumed = client.calls[1]["messages"]
        assert resumed[-1]["role"] == "assistant"
        assert resumed[-1]["content"][-1]["type"] == "server_tool_use"
        assert resumed[:-1] == client.calls[0]["messages"]
        assert msg.content == "Searching. Found it."
        from services.claude_chat_model import CLAUDE_CONTENT_KEY

        types = [b["type"] for b in msg.additional_kwargs[CLAUDE_CONTENT_KEY]]
        assert types == ["text", "server_tool_use", "web_search_tool_result", "text"]
        assert msg.usage_metadata["input_tokens"] == 30
        assert msg.usage_metadata["output_tokens"] == 12
        assert msg.response_metadata["stop_reason"] == "end_turn"

    async def test_pause_turn_is_resumed_at_most_three_times(self):
        srv = lambda i: tool_use(f"srvtoolu_{i}", "web_search", {"query": "x"}, server=True)  # noqa: E731
        client = FakeAnthropic([message([srv(i)], stop_reason="pause_turn", msg_id=f"m{i}") for i in range(6)])
        msg = await _model(client).ainvoke([HumanMessage(content="?")])
        assert len(client.calls) == 4
        assert msg.response_metadata["stop_reason"] == "pause_turn"

    async def test_tool_uses_before_a_fallback_are_not_called(self):
        blocks = [tool_use("toolu_old", "web_search", {"q": "a"}),
                  raw_block({"type": "fallback", "from": {"model": "claude-opus-5-5"},
                             "to": {"model": "claude-opus-5"}}),
                  tool_use("toolu_new", "web_search", {"q": "b"})]
        client = FakeAnthropic([message(blocks, stop_reason="tool_use")])
        msg = await _model(client).ainvoke([HumanMessage(content="?")])
        assert [tc["id"] for tc in msg.tool_calls] == ["toolu_new"]


# ================================================================ streaming ==

class TestStreaming:
    async def _chunks(self, events_list, **kw):
        client = FakeAnthropic(events_list)
        llm = _model(client, **kw).bind_tools(_fake_tools()[:2])
        chunks = [c async for c in llm.astream([_system(), HumanMessage(content="go")])]
        return chunks, client

    async def test_text_reasoning_and_tool_args_stream_and_the_call_arrives_once_at_the_end(self):
        events = message([thinking("Plan it."), text("On it.", chunks=2),
                          tool_use("toolu_7", "zeta_tool", {"query": "abc"})], stop_reason="tool_use")
        chunks, _ = await self._chunks([events])
        assert all(isinstance(c, AIMessageChunk) for c in chunks)
        texts = [c.content for c in chunks if isinstance(c.content, str) and c.content]
        assert "".join(texts) == "On it."
        reasoning = [c.additional_kwargs["reasoning_delta"] for c in chunks
                     if c.additional_kwargs.get("reasoning_delta")]
        assert "".join(reasoning) == "Plan it."
        args = [c.additional_kwargs["tool_args_delta"] for c in chunks if c.additional_kwargs.get("tool_args_delta")]
        assert "".join(a["delta"] for a in args) == json.dumps({"query": "abc"})
        assert {a["id"] for a in args} == {"toolu_7"} and {a["name"] for a in args} == {"zeta_tool"}
        with_calls = [i for i, c in enumerate(chunks) if c.tool_calls]
        assert len(with_calls) == 1
        last_delta = max(i for i, c in enumerate(chunks) if c.content or c.additional_kwargs.get("reasoning_delta")
                         or c.additional_kwargs.get("tool_args_delta"))
        assert with_calls[0] > last_delta
        final = chunks[with_calls[0]]
        assert final.tool_calls == [{"name": "zeta_tool", "args": {"query": "abc"}, "id": "toolu_7",
                                     "type": "tool_call"}]
        assert final.tool_call_chunks[0]["index"] == 2
        assert final.usage_metadata["input_tokens"] == 100
        assert final.additional_kwargs["claude_content"][0]["signature"] == "sig-abc"
        assert final.response_metadata["stop_reason"] == "tool_use"

    async def test_summed_chunks_are_one_coherent_message(self):
        events = message([thinking("Plan."), text("Answer."), tool_use("toolu_1", "zeta_tool", {"query": "q"})],
                         stop_reason="tool_use")
        chunks, _ = await self._chunks([events])
        total = chunks[0]
        for c in chunks[1:]:
            total = total + c
        assert total.content == "Answer."
        assert [tc["id"] for tc in total.tool_calls] == ["toolu_1"]

    async def test_a_refusal_mid_stream_appends_the_note_and_drops_the_calls(self):
        from services.claude_chat_model import REFUSAL_NOTE

        events = message([text("Partial"), tool_use("toolu_1", "zeta_tool", {"query": "q"})], stop_reason="refusal")
        chunks, _ = await self._chunks([events])
        assert REFUSAL_NOTE in "".join(c.content for c in chunks if isinstance(c.content, str))
        assert not any(c.tool_calls for c in chunks)

    async def test_the_streaming_loop_keeps_the_raw_blocks_on_its_final_message(self):
        from services.graph.streaming import _stream_llm_with_events

        events = message([thinking("Plan."), tool_use("toolu_1", "zeta_tool", {"query": "q"})],
                         stop_reason="tool_use")
        client = FakeAnthropic([events])
        llm = _model(client).bind_tools(_fake_tools()[:2])
        out = [e async for e in _stream_llm_with_events(llm, [_system(), HumanMessage(content="go")])]
        final = out[-1]["message"]
        assert final.additional_kwargs["claude_content"][0]["type"] == "thinking"
        assert final.additional_kwargs["reasoning_items"][0]["summary"][0]["text"] == "Plan."
        assert [tc["id"] for tc in final.tool_calls] == ["toolu_1"]
        assert final.usage_metadata["input_tokens"] == 100
        kinds = [e["type"] for e in out]
        assert "reasoning_delta" in kinds and "tool_args_delta" in kinds


# ================================================================== errors ==

class TestErrors:
    @pytest.mark.parametrize("status,err_type,retryable", [
        (529, "overloaded_error", True), (500, "api_error", True), (429, "rate_limit_error", True),
        (400, "invalid_request_error", False), (401, "authentication_error", False),
    ])
    async def test_status_errors_become_one_provider_neutral_error(self, status, err_type, retryable):
        from services.llm_errors import LLMProviderError
        from services.mind_service import is_transient_error

        client = FakeAnthropic([status_error(status, err_type)] * 2)
        with pytest.raises(LLMProviderError) as info:
            await _model(client, retry_backoff=(0, 0)).ainvoke([HumanMessage(content="hi")])
        exc = info.value
        assert exc.retryable is retryable
        assert exc.http_status == status
        assert exc.provider == "anthropic"
        assert is_transient_error(exc) is retryable
        assert isinstance(exc, ValueError)

    async def test_connection_errors_are_transient(self):
        from services.llm_errors import LLMProviderError
        from services.mind_service import is_transient_error

        client = FakeAnthropic([connection_error(), connection_error()])
        with pytest.raises(LLMProviderError) as info:
            await _model(client, retry_backoff=(0, 0)).ainvoke([HumanMessage(content="hi")])
        assert is_transient_error(info.value)

    async def test_a_transient_failure_is_retried_once_before_anything_is_committed(self):
        client = FakeAnthropic([message([], error={"type": "overloaded_error", "message": "Overloaded"}),
                                message([text("second try")])])
        msg = await _model(client, retry_backoff=(0, 0)).ainvoke([HumanMessage(content="hi")])
        assert msg.content == "second try"
        assert len(client.calls) == 2

    async def test_a_stream_that_ends_early_is_a_transient_error(self):
        from services.llm_errors import LLMProviderError

        cut = message([text("half")], end=False)
        cut = [e for e in cut if e["type"] != "message_delta"]
        client = FakeAnthropic([cut, list(cut)])
        with pytest.raises(LLMProviderError) as info:
            await _model(client, retry_backoff=(0, 0)).ainvoke([HumanMessage(content="hi")])
        assert info.value.retryable and info.value.code == "stream_truncated"

    async def test_streaming_does_not_retry_after_text_reached_the_consumer(self):
        from services.llm_errors import LLMProviderError

        bad = message([text("Hello there")], error={"type": "overloaded_error", "message": "Overloaded"})
        client = FakeAnthropic([bad, message([text("again")])])
        llm = _model(client, retry_backoff=(0, 0))
        seen = []
        with pytest.raises(LLMProviderError):
            async for c in llm.astream([HumanMessage(content="hi")]):
                seen.append(c)
        assert len(client.calls) == 1

    async def test_a_model_that_rejects_mid_conversation_system_messages_is_retried_with_top_level(self, monkeypatch):
        import services.claude_chat_model as ccm

        monkeypatch.setattr(ccm, "_LEARNED_NO_MID_SYSTEM", set())
        rejected = status_error(400, "invalid_request_error", "role 'system' is not supported on this model")
        client = FakeAnthropic([rejected, message([text("ok")])])
        msg = await _model(client).ainvoke([_system("DYN"), HumanMessage(content="hi")])
        assert msg.content == "ok"
        second = client.calls[1]
        assert all(m["role"] != "system" for m in second["messages"])
        assert second["system"][-1]["text"] == "DYN"

    async def test_llm_call_timeout_still_bounds_the_call(self):
        import asyncio

        from services.graph import tool_deadlines

        class Slow(FakeAnthropic):
            pass

        client = Slow([message([text("late")])])
        orig = client.messages.create

        def create(**kw):
            ctx = orig(**kw)

            class _Ctx:
                async def __aenter__(self_inner):
                    await asyncio.sleep(5)
                    return await ctx.__aenter__()

                async def __aexit__(self_inner, *a):
                    return False
            return _Ctx()

        client.messages.create = create
        with pytest.raises(tool_deadlines.LLMCallTimeout):
            await tool_deadlines.invoke_with_deadline(_model(client).ainvoke([HumanMessage(content="hi")]),
                                                      timeout=0.05)


# ============================================================== checkpoint ==

class TestCheckpoint:
    def test_strip_claude_thinking_keeps_only_the_current_turns_blocks(self):
        from services.claude_chat_model import CLAUDE_CONTENT_KEY, strip_claude_thinking

        blocks = lambda tag: [{"type": "thinking", "thinking": tag, "signature": "s" * 500}]  # noqa: E731
        old = AIMessage(content="old", additional_kwargs={CLAUDE_CONTENT_KEY: blocks("old"), "trace": {"x": 1}})
        cur = AIMessage(content="cur", additional_kwargs={CLAUDE_CONTENT_KEY: blocks("cur")})
        msgs = [SystemMessage(content="s"), HumanMessage(content="a"), old, HumanMessage(content="b"), cur]
        out = strip_claude_thinking(msgs)
        assert CLAUDE_CONTENT_KEY not in out[2].additional_kwargs
        assert out[2].additional_kwargs["trace"] == {"x": 1}
        assert out[4].additional_kwargs[CLAUDE_CONTENT_KEY][0]["thinking"] == "cur"
        assert old.additional_kwargs[CLAUDE_CONTENT_KEY]  # not mutated

    def test_the_checkpoint_path_strips_both_providers(self):
        from services.claude_chat_model import CLAUDE_CONTENT_KEY
        from services.graph.streaming import _checkpoint_messages

        old = AIMessage(content="old", additional_kwargs={
            CLAUDE_CONTENT_KEY: [{"type": "thinking", "thinking": "t", "signature": "s"}],
            "reasoning_items": [{"type": "reasoning", "encrypted_content": "e", "summary": []}]})
        out = _checkpoint_messages([HumanMessage(content="a"), old, HumanMessage(content="b"),
                                    AIMessage(content="new")])
        assert CLAUDE_CONTENT_KEY not in out[1].additional_kwargs
        assert "reasoning_items" not in out[1].additional_kwargs


# ================================================================= pricing ==

class TestCost:
    def test_cost_estimate_uses_cache_prices_and_haikus_long_prompt_tier(self):
        from services.claude_chat_model import estimate_cost_usd

        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
                 "cache_creation_input_tokens": 1_000_000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1_000_000}}
        assert estimate_cost_usd("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 0.20 + 8)
        small = {"input_tokens": 50_000, "output_tokens": 1_000}
        assert estimate_cost_usd("claude-haiku-5-5", small) == pytest.approx(0.05 * 0.10 + 0.001 * 0.50)
        big = {"input_tokens": 150_000, "output_tokens": 1_000}
        assert estimate_cost_usd("claude-haiku-5-5", big) == pytest.approx(0.15 * 0.50 + 0.001 * 2.50)
        assert estimate_cost_usd("claude-unknown", small) is None


class TestFallbackSpend:
    """``fallbacks: "default"`` rides every Opus 5.5 request. When the
    server-side fallback serves a response, top-level ``usage`` covers only
    the attempt that produced the message; ``usage.iterations`` itemizes
    every attempt, and a mid-stream decline bills at normal rates. The
    fallback model (Opus 5, sticky for about an hour) bills at its own
    rates. Until 2026-10-11 the declined attempt's tokens never reached the
    ledger, the cache line priced the fallback's tokens at Opus 5.5 rates,
    and the metadata had no cost at all (Opus 5 had no price)."""

    DECLINED = {"type": "message", "input_tokens": 2_000, "cache_read_input_tokens": 10_000,
                "cache_creation_input_tokens": 0, "output_tokens": 300}
    SERVED = {"type": "fallback_message", "input_tokens": 2_100, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 10_000, "output_tokens": 500}

    def _usage(self):
        top = {k: v for k, v in self.SERVED.items() if k != "type"}
        return dict(top, iterations=[dict(self.DECLINED), dict(self.SERVED)])

    def _expected_cost(self):
        d, f = self.DECLINED, self.SERVED
        opus_5_5 = (d["input_tokens"] * 4 + d["cache_read_input_tokens"] * 0.20 + d["output_tokens"] * 20) / 1e6
        opus_5 = (f["input_tokens"] * 5 + f["cache_creation_input_tokens"] * 6.25 + f["output_tokens"] * 25) / 1e6
        return opus_5_5 + opus_5

    def test_the_fallback_models_have_prices(self):
        from services.claude_chat_model import estimate_cost_usd

        mtok = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
                "cache_creation_input_tokens": 1_000_000,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1_000_000}}
        assert estimate_cost_usd("claude-opus-5", mtok) == pytest.approx(5 + 25 + 0.50 + 10)
        assert estimate_cost_usd("claude-opus-4-8", mtok) == pytest.approx(5 + 25 + 0.50 + 10)

    def test_every_attempt_is_priced_at_the_model_that_ran_it(self):
        from services.claude_chat_model import billed_usage, estimate_cost_usd

        usage = self._usage()
        cost = estimate_cost_usd("claude-opus-5", usage, requested_model="claude-opus-5-5")
        assert cost == pytest.approx(self._expected_cost())
        billed = billed_usage(usage)
        assert billed["input_tokens"] == 2_000 + 2_100
        assert billed["cache_read_input_tokens"] == 10_000
        assert billed["cache_creation_input_tokens"] == 10_000
        assert billed["output_tokens"] == 300 + 500

    def test_without_iterations_the_top_level_is_the_whole_bill(self):
        from services.claude_chat_model import billed_usage, estimate_cost_usd

        usage = {"input_tokens": 100, "output_tokens": 10}
        assert billed_usage(usage) == usage
        assert estimate_cost_usd("claude-opus-5-5", usage) == pytest.approx((100 * 4 + 10 * 20) / 1e6)

    def _capture(self, monkeypatch, caplog):
        """The ``llm_calls`` rows the requests write — the only ledger a
        Claude call reaches (``self.codex_rows`` must stay empty)."""
        from services import codex_call_ledger, llm_spend

        rows = []

        async def _insert(row):
            rows.append(dict(row))
            return True

        self.codex_rows = []
        monkeypatch.setattr(llm_spend, "_insert", _insert)
        monkeypatch.setattr(llm_spend, "WRITES_ENABLED", True)
        monkeypatch.setattr(codex_call_ledger, "schedule_call", lambda **row: self.codex_rows.append(row))
        caplog.set_level(logging.INFO, logger="services.claude_chat_model")
        return rows

    @staticmethod
    def _cache_line(caplog) -> str:
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[claude-cache] purpose")]
        assert len(lines) == 1, lines
        return lines[0]

    async def test_a_mid_stream_fallback_reaches_the_ledger_the_log_and_the_metadata(self, monkeypatch, caplog):
        from services import llm_spend

        rows = self._capture(monkeypatch, caplog)
        blocks = [text("Here is the part"), raw_block({"type": "fallback", "from": {"model": "claude-opus-5-5"},
                                                       "to": {"model": "claude-opus-5"}}),
                  text("Here is the answer.")]
        client = FakeAnthropic([message(blocks, model="claude-opus-5", usage=self._usage())])
        msg = await _model(client).ainvoke([HumanMessage(content="hi")])
        await llm_spend.drain()

        um = msg.usage_metadata
        assert um["input_tokens"] == 2_000 + 2_100 + 10_000 + 10_000
        assert um["output_tokens"] == 800
        assert um["input_token_details"] == {"cache_read": 10_000, "cache_creation": 10_000}
        meta = msg.response_metadata
        assert meta["model"] == "claude-opus-5"
        assert meta["requested_model"] == "claude-opus-5-5"
        assert meta["estimated_cost_usd"] == pytest.approx(self._expected_cost(), abs=1e-6)

        [row] = rows
        assert row["model"] == "claude-opus-5"           # the served model (record_claude_call's served_model)
        assert row["input_tokens"] == 2_000 + 2_100      # llm_calls keeps the uncached remainder
        assert row["cache_read_tokens"] == 10_000
        assert row["cache_write_tokens"] == 10_000
        assert row["output_tokens"] == 800
        assert row["cost_usd"] == pytest.approx(self._expected_cost(), abs=1e-6)
        line = self._cache_line(caplog)
        assert "model=claude-opus-5 " in line and "requested=claude-opus-5-5" in line
        assert "out=800" in line and "in=4100" in line
        assert f"usd={self._expected_cost():.4f}" in line
        assert self.codex_rows == []

    async def test_a_sticky_fallback_without_iterations_is_priced_at_the_served_model(self, monkeypatch, caplog):
        from services import llm_spend

        rows = self._capture(monkeypatch, caplog)
        usage = {"input_tokens": 1_000, "output_tokens": 100, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        client = FakeAnthropic([message([text("Hi.")], model="claude-opus-5", usage=usage)])
        msg = await _model(client).ainvoke([HumanMessage(content="hi")])
        await llm_spend.drain()
        opus_5 = (1_000 * 5 + 100 * 25) / 1e6
        assert msg.response_metadata["estimated_cost_usd"] == pytest.approx(opus_5)
        assert rows[0]["model"] == "claude-opus-5"
        assert rows[0]["cost_usd"] == pytest.approx(opus_5)
        assert f"usd={opus_5:.4f}" in self._cache_line(caplog)
        assert self.codex_rows == []

    async def test_a_normal_answer_keeps_the_requested_model(self, monkeypatch, caplog):
        from services import llm_spend

        rows = self._capture(monkeypatch, caplog)
        client = FakeAnthropic([message([text("Hi.")])])
        msg = await _model(client).ainvoke([HumanMessage(content="hi")])
        await llm_spend.drain()
        assert rows[0]["model"] == "claude-opus-5-5"
        assert "requested_model" not in msg.response_metadata
        line = self._cache_line(caplog)
        assert "model=claude-opus-5-5 " in line and "requested=" not in line
        assert self.codex_rows == []


class TestNoToolsBound:
    async def test_a_conversation_with_tool_blocks_and_no_tools_is_sent_as_text(self):
        """Safety net: the API refuses tool_use / tool_result blocks in a
        request that defines no tools."""
        tc = {"id": "toolu_1", "name": "web_search", "args": {"q": "tide"}, "type": "tool_call"}
        blocks = [{"type": "thinking", "thinking": "t", "signature": "s"},
                  {"type": "tool_use", "id": "toolu_1", "name": "web_search", "input": {"q": "tide"}}]
        client = FakeAnthropic([message([text("Summary.")])])
        await _model(client).ainvoke([_system(), HumanMessage(content="tide?"), _ai_with_blocks(blocks, [tc]),
                                      ToolMessage(content="6pm", tool_call_id="toolu_1")])
        sent = client.calls[0]
        dumped = json.dumps(sent["messages"])
        assert "tool_use" not in dumped and "tool_result" not in dumped and "thinking" not in dumped
        assert "web_search" in dumped and "6pm" in dumped
        assert "tools" not in sent


class TestBlockStartText:
    async def test_text_that_arrives_in_the_block_start_still_streams(self):
        events = message([raw_block({"type": "text", "text": "Hello "}), text("there.")])
        client = FakeAnthropic([events])
        chunks = [c async for c in _model(client).astream([HumanMessage(content="hi")])]
        assert "".join(c.content for c in chunks if isinstance(c.content, str)) == "Hello there."


class TestSseFraming:
    """SSE lines end at \\r and \\n only. JSON may carry U+2028, U+2029 and
    U+0085 unescaped inside a string, and httpx's ``aiter_lines`` splits on
    them too (``str.splitlines``): the cut ``data:`` line no longer parsed and
    its delta was dropped without a word — a piece of the answer, a tool's
    arguments, a thinking block that then no longer matched its signature
    (review 2026-10-11). The adapter splits the raw bytes, as the SDK's own
    decoder does."""

    ODD = "one\u2028two\u2029three\u0085four"

    async def test_text_with_unicode_line_separators_streams_whole(self):
        client = FakeAnthropic([message([text(self.ODD, chunks=1)])])
        chunks = [c async for c in _model(client).astream([HumanMessage(content="hi")])]
        assert "".join(c.content for c in chunks if isinstance(c.content, str)) == self.ODD

    async def test_the_final_message_and_the_thinking_block_keep_them(self):
        from services.claude_chat_model import CLAUDE_CONTENT_KEY

        client = FakeAnthropic([message([thinking("Weigh\u2028it.", "sig-1"), text(self.ODD)])])
        msg = await _model(client).ainvoke([HumanMessage(content="hi")])
        assert msg.content == self.ODD
        assert msg.additional_kwargs[CLAUDE_CONTENT_KEY][0]["thinking"] == "Weigh\u2028it."

    async def test_tool_arguments_with_them_still_parse(self):
        args = {"query": "caf\u00e9\u2028menu\u0085today"}
        client = FakeAnthropic([message([tool_use("toolu_1", "web_search", args)], stop_reason="tool_use")])
        msg = await _model(client).ainvoke([HumanMessage(content="?")])
        assert [tc["args"] for tc in msg.tool_calls] == [args]

    @pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
    @pytest.mark.parametrize("chunk_size", [1, 2, 5, 4096])
    async def test_any_line_ending_and_any_chunking(self, newline, chunk_size):
        events = message([thinking("Plan \u00e9\u2028.", "sig-1"), text("Hello \u00e9t\u00e9 \U0001F600 Ellis.")])
        client = FakeAnthropic([FakeStreamResponse(events, chunk_size=chunk_size, newline=newline)])
        msg = await _model(client).ainvoke([HumanMessage(content="hi")])
        assert msg.content == "Hello \u00e9t\u00e9 \U0001F600 Ellis."
        assert msg.response_metadata["stop_reason"] == "end_turn"


class TestSync:
    def test_invoke_works_without_a_running_loop(self):
        client = FakeAnthropic([message([text("sync ok")])])
        assert _model(client).invoke([HumanMessage(content="hi")]).content == "sync ok"

    async def test_invoke_works_inside_a_running_loop(self):
        client = FakeAnthropic([message([text("threaded ok")])])
        assert _model(client).invoke([HumanMessage(content="hi")]).content == "threaded ok"
