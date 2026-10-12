"""
Track G1: the main chat path builds its model through ``build_chat_llm``,
never langchain's ``ChatAnthropic``.

Every runtime LLM call goes through the ``llm_utils`` builders: Claude
through ``ChatClaude`` (the ``anthropic`` SDK itself, never
``langchain_anthropic``), the Codex fallback through its own adapter. The
two holdouts at graph/nodes.py:72 and graph/streaming.py:74 migrated to
the ``build_chat_llm`` helper.
"""

from __future__ import annotations

import pytest


def test_llm_utils_has_build_chat_llm():
    """The chat helper must exist alongside ``build_background_llm``."""
    from services import llm_utils

    assert hasattr(llm_utils, "build_chat_llm"), (
        "llm_utils.build_chat_llm is missing — G1 requires a chat-tuned "
        "GPT/OpenAI helper parallel to build_background_llm"
    )


def test_no_chatanthropic_imports_in_graph():
    """The two offending files must no longer import ChatAnthropic."""
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    for relpath in (
        "services/graph/nodes.py",
        "services/graph/streaming.py",
    ):
        path = backend / relpath
        text = path.read_text()
        assert "langchain_anthropic" not in text, (
            f"{relpath} still imports from langchain_anthropic — G1 requires "
            "it removed"
        )
        assert "ChatAnthropic(" not in text, (
            f"{relpath} still instantiates ChatAnthropic — G1 requires "
            "migration to build_chat_llm"
        )


async def test_respond_node_does_not_call_chatanthropic(monkeypatch):
    """``respond_node`` in graph/nodes.py must route through the new
    helper, not instantiate ChatAnthropic.
    """
    # Sentinel that fails if anything tries to build ChatAnthropic.
    from langchain_anthropic import ChatAnthropic

    original_init = ChatAnthropic.__init__

    def _forbidden_init(self, *args, **kwargs):
        raise AssertionError(
            "ChatAnthropic instantiated during main chat — G1 policy violation"
        )

    monkeypatch.setattr(ChatAnthropic, "__init__", _forbidden_init)

    # Fake OpenAI-ish LLM.
    class _FakeLLM:
        async def ainvoke(self, messages):
            class _Resp:
                content = "hello"

            return _Resp()

    openai_calls = []

    def _build_chat_llm(*args, **kwargs):
        openai_calls.append((args, kwargs))
        return _FakeLLM()

    import services.llm_utils as llm_utils

    monkeypatch.setattr(llm_utils, "build_chat_llm", _build_chat_llm, raising=False)

    # Also patch any lingering direct imports in nodes.py: after
    # migration the module should import build_chat_llm from llm_utils.
    from services.graph import nodes

    if hasattr(nodes, "build_chat_llm"):
        monkeypatch.setattr(nodes, "build_chat_llm", _build_chat_llm)

    state = {
        "messages": [],
        "model": "gpt-5.4",
        "temperature": 0.7,
        "system_prompt": "sp",
        "retrieved_memories": [],
        "node_history": [],
    }

    result = await nodes.respond_node(state)

    # Reset init for anything else
    monkeypatch.setattr(ChatAnthropic, "__init__", original_init)

    assert result.get("error") in (None, ""), f"respond_node errored: {result!r}"
    assert len(openai_calls) >= 1, "build_chat_llm must be invoked by respond_node"


def test_streaming_build_llm_never_instantiates_chatanthropic():
    """``_build_llm`` in streaming.py goes through the builders whatever the
    provider — never langchain's ``ChatAnthropic``.
    """
    from services.graph import streaming

    source = open(streaming.__file__).read()
    # Either the provider-selector is gone, or it routes both branches
    # through GPT — any remaining "ChatAnthropic(" call violates G1.
    assert "ChatAnthropic(" not in source, (
        "graph/streaming.py still instantiates ChatAnthropic — G1 policy "
        "forbids Anthropic in the main chat path"
    )
