"""Codex emits JSONL lines that can exceed asyncio's default 64KB
readline buffer (a large diff in a file_change event, a tool output,
etc). When that happens, ``StreamReader.readline()`` raises
``ValueError: Separator is not found, and chunk exceed the limit``
and the evolve cycle dies with an opaque "Codex error" instead of
producing any work.

Pin the buffer-size constant so it's hard to regress.

Run: pytest backend/tests/test_codex_buffer_limit.py
"""

from __future__ import annotations


def test_codex_buffer_limit_is_large_enough_for_diffs():
    """A single diff line from Codex can easily run 200KB+. Default
    64KB is too small. We want at least 4MB headroom.
    """
    from services.codex_cli_service import CODEX_STDOUT_BUFFER_LIMIT

    assert CODEX_STDOUT_BUFFER_LIMIT >= 4 * 1024 * 1024, (
        f"CODEX_STDOUT_BUFFER_LIMIT={CODEX_STDOUT_BUFFER_LIMIT} too small. "
        "Codex JSONL lines with large diffs/tool outputs will overflow."
    )


def test_codex_buffer_limit_passed_to_subprocess():
    """The constant exists for a reason; make sure the subprocess call
    actually consumes it (otherwise it's decorative).
    """
    import inspect

    from services import codex_cli_service

    source = inspect.getsource(codex_cli_service)
    assert "CODEX_STDOUT_BUFFER_LIMIT" in source
    assert "limit=CODEX_STDOUT_BUFFER_LIMIT" in source, (
        "create_subprocess_exec must be called with limit=CODEX_STDOUT_BUFFER_LIMIT"
    )
