"""R/G TDD red for A3 — remove stale "Haiku" prose + function names from
services. Runtime is already GPT-only (enforced by test_no_anthropic_runtime).
This test guards against future drift where someone writes a new docstring
that says "uses Claude Haiku" or imports a stale model string.

Scope:
  * No ``[\"']claude-haiku`` literal in services/ source.
  * No docstring or comment in services/ says "Haiku" or "Claude Haiku".
  * No ``_haiku_classify`` function anywhere in services/.
  * CLAUDE.md names Haiku only as what it is since the Claude provider
    became production's (2026-10-11): the ``background`` route's model.

Out of scope (deliberately):
  * DB columns ``haiku_input_tokens`` / ``haiku_output_tokens`` and their
    matching Pydantic fields — renaming those is an API-breaking change
    that needs a migration and frontend update, tracked separately.
"""

from __future__ import annotations

import pathlib
import re


# The checkout this test runs in (it was the live checkout's path, so a
# worktree's run checked production's files, not its own).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Files exempt from the prose scrub — they name "Haiku"/"ChatAnthropic"
# explicitly as anti-pattern warnings in policy-enforcer code. Keeping those
# warnings is what enforces the policy.
PROSE_EXEMPT = {
    "backend/services/llm_utils.py",
    "backend/services/dream/context.py",
    "backend/services/orchestrator_service.py",  # historical migration comment
    # Pricing-table model-id matchers ("haiku-4-5" etc.) for costing
    # historical Anthropic usage rows — data, not prose claiming Haiku use.
    "backend/services/display_service.py",
}

# The Claude provider (2026-10-10, behind EDWARD_LLM_PROVIDER=anthropic, off
# by default) names the Claude models it routes to — its table is data, and
# these two files are the only place a claude-haiku id may live. Anywhere
# else in services/ the guard still holds.
CLAUDE_PROVIDER_FILES = {
    "backend/services/claude_chat_model.py",
    "backend/services/claude_routing.py",
    # The Claude price table (the spend ledger's, 2026-10-10): list prices
    # per model id, Haiku's included.
    "backend/services/llm_pricing.py",
}


def _py_files_under(*subdirs: str) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for sd in subdirs:
        base = REPO_ROOT / sd
        if not base.exists():
            continue
        out.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_no_claude_haiku_model_literal_in_services():
    bad: list[str] = []
    for path in _py_files_under("backend/services"):
        if _rel(path) in CLAUDE_PROVIDER_FILES:
            continue
        text = path.read_text()
        if re.search(r"""[\"']claude-haiku""", text):
            bad.append(_rel(path))
    assert not bad, (
        "Stale claude-haiku model-string literal found in services/:\n  "
        + "\n  ".join(bad)
    )


def test_no_haiku_word_in_service_prose():
    """Docstrings, comments, log/print strings in services/ should not
    reference Haiku. Variable names like ``haiku_calls`` are out of scope
    (internal metric names, renamed separately)."""
    bad: list[str] = []
    pattern = re.compile(r"\b[Hh]aiku\b")
    for path in _py_files_under("backend/services"):
        if _rel(path) in PROSE_EXEMPT or _rel(path) in CLAUDE_PROVIDER_FILES:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            # Look only at comment/docstring/string-literal lines; skip lines
            # that are pure variable declarations or attribute access where
            # the word is part of an identifier (handled by the \b boundary
            # — identifier_haiku matches because _ isn't a word boundary in
            # Python but we specifically want to flag space-surrounded uses).
            # The \b regex already excludes ``haiku_calls`` because the _
            # is a word char. So any match here is bona fide prose.
            if pattern.search(stripped):
                bad.append(f"{_rel(path)}:{lineno}: {stripped[:100]}")
    assert not bad, (
        "Stale 'Haiku' prose in services/ — rewrite to say 'GPT' or "
        "'background LLM':\n  " + "\n  ".join(bad[:40])
    )


def test_no_haiku_classify_function_in_services():
    bad: list[str] = []
    for path in _py_files_under("backend/services"):
        text = path.read_text()
        if "_haiku_classify" in text:
            bad.append(_rel(path))
    assert not bad, (
        "Function ``_haiku_classify`` should be renamed (e.g. "
        "``_classify_events``). Found in:\n  " + "\n  ".join(bad)
    )


def _haiku_claims_off_route(text: str) -> list[str]:
    """Each place ``text`` names Haiku that is not the background route:
    the 200 characters before it must name the ``background`` route."""
    bad = []
    for m in re.finditer(r"\bClaude Haiku\b|\bHaiku\b", text):
        before = text[max(0, m.start() - 200):m.start()]
        if "background" not in before:
            line = text.count("\n", 0, m.start()) + 1
            bad.append(f"CLAUDE.md:{line}: …{text[max(0, m.start() - 60):m.end() + 20]}…")
    return bad


def test_claude_md_names_haiku_only_as_the_background_route():
    """Until 2026-10-11 the runtime was GPT-only and CLAUDE.md could not say
    Haiku at all. The Claude provider is production's now (Ellis, 2026-10-11),
    and the route table puts Haiku 5.5 on the background route alone (the
    benchmark's set R): CLAUDE.md may name it there and nowhere else, and
    the table must agree."""
    from services.claude_routing import DEFAULT_ROUTES

    assert DEFAULT_ROUTES["background"].model.startswith("claude-haiku")
    assert [p for p, r in DEFAULT_ROUTES.items() if r.model.startswith("claude-haiku")] == ["background"]
    path = REPO_ROOT / "CLAUDE.md"
    if not path.exists():
        return
    bad = _haiku_claims_off_route(path.read_text())
    assert not bad, "CLAUDE.md names Haiku off the background route:\n  " + "\n  ".join(bad[:30])


def test_the_check_catches_a_haiku_claim_off_the_background_route():
    assert _haiku_claims_off_route("`pulse` on Haiku 5.5 at medium")
    assert _haiku_claims_off_route("uses Claude Haiku for triage")
    assert not _haiku_claims_off_route("`background` (extraction, entities) on Haiku 5.5 at high")
