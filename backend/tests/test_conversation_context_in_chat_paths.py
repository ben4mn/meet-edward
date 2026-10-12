"""
Bug: Edward replied "No conversation context available" when asked
to use browser tools. Source: tools.py:browse_web (and friends) call
get_current_conversation_id() and bail with that error if it's None.

stream_with_memory_events sets the ContextVar at line 1197. But
chat_with_memory (used by orchestrator workers, scheduled events,
heartbeat auto-replies, error recovery loops) does NOT — so any
browser/code-execution/SQL/shell tool call from those paths fails
with the same error.

This test pins that BOTH chat entry points set the conversation-id
ContextVar before invoking the tool loop. Easy to verify by parsing
the source — the call must appear at the top of each function body.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


_STREAMING = Path(__file__).resolve().parent.parent / "services" / "graph" / "streaming.py"


def _function_bodies_call(source: str, fn_names: set[str], target_call_name: str) -> dict[str, bool]:
    """For each named async function, return whether its top-level body
    contains a call to ``target_call_name``."""
    tree = ast.parse(source)
    out: dict[str, bool] = {fn: False for fn in fn_names}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in fn_names:
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) \
                        and child.func.id == target_call_name:
                    out[node.name] = True
                    break
    return out


def _delegates_to(source: str, wrapper: str, impl: str) -> bool:
    """True when ``wrapper``'s body calls ``impl``."""
    return _function_bodies_call(source, {wrapper}, impl)[wrapper]


# Since the turn ledger (2026-09-12) both public entry points are thin
# wrappers around an ``_impl`` that runs the turn; the ContextVar is set
# there. Pin both halves: the wrapper delegates, and the impl sets it.
# (Updated 2026-09-24: the original test looked in the wrappers and had
# failed by inspection ever since.)
@pytest.mark.parametrize(
    "fn_name,impl_name",
    [("stream_with_memory_events", "_stream_with_memory_events_impl"),
     ("chat_with_memory", "_chat_with_memory_impl")],
)
def test_chat_entry_point_sets_conversation_context(fn_name, impl_name):
    """Both chat paths must set the conversation ContextVar before
    running the tool loop. Without it, any browser/code-exec tool
    invocation returns 'No conversation context available'."""
    source = _STREAMING.read_text()
    assert _delegates_to(source, fn_name, impl_name), f"{fn_name} must run its turn through {impl_name}"
    calls = _function_bodies_call(source, {impl_name}, "set_current_conversation_id")
    assert calls[impl_name], (
        f"{impl_name} must call set_current_conversation_id() so browser/"
        f"code-exec/SQL/shell tools can read the conversation context. "
        f"Without it those tools bail with 'No conversation context available' "
        f"and the user sees a refusal that looks like Edward giving up."
    )


@pytest.mark.parametrize("impl_name", ["_stream_with_memory_events_impl", "_chat_with_memory_impl"])
def test_context_is_set_before_the_tool_loop(impl_name):
    """The ContextVar must be set before any tool can run, i.e. before the
    loop's first ``while``."""
    import inspect

    from services.graph import streaming

    src = inspect.getsource(getattr(streaming, impl_name))
    assert src.index("set_current_conversation_id(conversation_id)") < src.index("tool_loop.run_tool_loop(")
