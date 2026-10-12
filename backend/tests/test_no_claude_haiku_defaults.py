"""
Pin the LLM policy: no ``claude-haiku-*`` model string literals in the
dreaming services.

``build_background_llm()`` routes all background LLM calls to GPT via
Codex OAuth / OpenAI API. The ``model: str = "claude-haiku-..."`` default
parameters in ``reflection_service``, ``deep_retrieval_service``, and
``consolidation_service`` were vestigial — they get shadowed at call time
and never reach the real client, but leaving them on the signatures
misleads readers and invites regressions.

This test fails if any of the three files reintroduces a ``claude-haiku``
string literal.
"""

from __future__ import annotations

from pathlib import Path


FORBIDDEN = "claude-haiku"

FILES = [
    "services/reflection_service.py",
    "services/deep_retrieval_service.py",
    "services/consolidation_service.py",
]


def test_dreaming_services_have_no_claude_haiku_strings():
    backend = Path(__file__).resolve().parent.parent
    offending = []
    for rel in FILES:
        path = backend / rel
        text = path.read_text()
        if FORBIDDEN in text:
            offending.append(rel)

    assert not offending, (
        f"Dreaming services must not contain any '{FORBIDDEN}' literals — "
        f"all LLM calls route through build_background_llm() which returns "
        f"GPT. Offending files: {offending}"
    )
