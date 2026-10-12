"""
Policy test: no function in `services.memory_service` may declare a
`model` parameter with a `claude-*` default value.

Context: those params are vestigial — every function body in
`memory_service` actually constructs its LLM via `build_background_llm()`
(which routes to GPT via Codex). The stale claude-* defaults are dead
code that invites future regressions where someone wires the parameter
into the LLM builder and silently reintroduces Anthropic at runtime.

See `feedback_llm_policy.md`.
"""

import ast
from pathlib import Path

import pytest

MEMORY_SERVICE = Path(__file__).resolve().parent.parent / "services" / "memory_service.py"


def _find_claude_defaults(source: str) -> list[tuple[str, str, str]]:
    """Return [(function_name, arg_name, default_value)] for any claude-*
    default in module-level function / async function signatures.
    """
    tree = ast.parse(source)
    offenders: list[tuple[str, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        all_args = list(args.args) + list(getattr(args, "kwonlyargs", []))
        all_defaults = list(args.defaults) + list(getattr(args, "kw_defaults", []))
        # positional defaults right-align; kwonly_defaults align 1:1 (None for
        # args with no default). Flatten by pairing each arg with its default
        # if one exists.
        paired: list[tuple[ast.arg, ast.expr]] = []
        # positional
        pos_args = args.args
        pos_defaults = args.defaults
        if pos_defaults:
            for a, d in zip(pos_args[-len(pos_defaults):], pos_defaults):
                paired.append((a, d))
        # kwonly
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            if d is not None:
                paired.append((a, d))

        for arg, default in paired:
            if not isinstance(default, ast.Constant):
                continue
            val = default.value
            if isinstance(val, str) and val.startswith("claude-"):
                offenders.append((node.name, arg.arg, val))

    return offenders


def test_memory_service_has_no_claude_model_defaults():
    source = MEMORY_SERVICE.read_text()
    offenders = _find_claude_defaults(source)
    assert not offenders, (
        "memory_service.py has stale Claude-* parameter defaults. These "
        "should be removed or set to None — the function bodies already "
        "route through build_background_llm().\n\nOffenders:\n"
        + "\n".join(f"  {fn}({arg}={val!r})" for fn, arg, val in offenders)
    )
