"""
Track G2: runtime guard against ``langchain_anthropic`` reappearing.

G1 removed the last ChatAnthropic imports from the main chat path. This
test is the regression bumper — any file under ``backend/services/`` or
``backend/routers/`` that re-introduces ``from langchain_anthropic`` or
``ChatAnthropic(...)`` fails this gate.
"""

from __future__ import annotations

import pathlib

import pytest


REPO_ROOT = pathlib.Path("/Users/me/edward")


def _py_files_under(*subdirs: str) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for sd in subdirs:
        base = REPO_ROOT / sd
        if not base.exists():
            continue
        out.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def test_no_langchain_anthropic_import_in_services_or_routers():
    """Grep guard: neither services nor routers may import from
    langchain_anthropic. This is checked as a string test because
    importing every module to inspect it would fan out a huge surface.
    """
    bad: list[str] = []
    for path in _py_files_under("backend/services", "backend/routers"):
        text = path.read_text()
        if "from langchain_anthropic" in text or "import langchain_anthropic" in text:
            bad.append(str(path.relative_to(REPO_ROOT)))
    assert not bad, (
        "G2 violation — the following files import langchain_anthropic. "
        "Use services.llm_utils.build_chat_llm / build_background_llm "
        f"instead:\n  " + "\n  ".join(bad)
    )


def test_no_chatanthropic_instantiation_in_services_or_routers():
    bad: list[str] = []
    for path in _py_files_under("backend/services", "backend/routers"):
        text = path.read_text()
        if "ChatAnthropic(" in text:
            bad.append(str(path.relative_to(REPO_ROOT)))
    assert not bad, (
        "G2 violation — the following files instantiate ChatAnthropic. "
        "Route through build_chat_llm instead:\n  " + "\n  ".join(bad)
    )


async def test_chat_turn_never_constructs_chatanthropic(monkeypatch):
    """Stronger guard: sabotage ``ChatAnthropic.__init__`` and run the
    chat respond_node. If anything tries to build one, the test fails.
    """
    from langchain_anthropic import ChatAnthropic
    from services.graph import nodes

    original_init = ChatAnthropic.__init__

    attempted: list[str] = []

    def _fail_init(self, *args, **kwargs):
        attempted.append(f"model={kwargs.get('model') or (args[0] if args else '?')}")
        raise AssertionError(
            "ChatAnthropic instantiated during chat turn — G2 policy violation"
        )

    monkeypatch.setattr(ChatAnthropic, "__init__", _fail_init)

    class _FakeLLM:
        async def ainvoke(self, messages):
            class _R:
                content = "ok"

            return _R()

    def _build_chat_llm(*args, **kwargs):
        return _FakeLLM()

    monkeypatch.setattr(nodes, "build_chat_llm", _build_chat_llm)

    state = {
        "messages": [],
        "model": "gpt-5.4",
        "temperature": 0.7,
        "system_prompt": "sp",
        "retrieved_memories": [],
        "node_history": [],
    }

    try:
        result = await nodes.respond_node(state)
    finally:
        monkeypatch.setattr(ChatAnthropic, "__init__", original_init)

    assert not attempted, f"ChatAnthropic was constructed: {attempted}"
    assert result.get("error") in (None, ""), f"respond_node errored: {result!r}"
