"""Who is spending: a task-local purpose label for Codex calls.

The spend ledger (``codex_calls.purpose``) names the job behind each call.
A model built by ``build_background_llm`` is stamped with its caller; a
chat-path turn (a wake, a research turn) is labelled by walking the stack,
which names the tool loop rather than the job. A surplus job (2026-09-24,
``services/surplus_service.py``) runs inside ``purpose_scope("surplus:<job>")``
so every call it causes — the turn's own, and the extraction it spawns,
which inherits the context — can be attributed to it and its spend shown
per job in ``GET /api/health/spend``.

``current_purpose()`` is what the ledger writer reads (the Codex adapter's
``_call_context``, wired by the coordinator); a model built inside a scope
can also be stamped directly with ``stamp(llm)``.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator, Optional

_purpose: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("edward_call_purpose", default=None)

MAX_PURPOSE_CHARS = 60  # codex_calls.purpose is String(60)


def current_purpose() -> Optional[str]:
    """The innermost scope's label, or None outside any scope."""
    return _purpose.get()


@contextmanager
def purpose_scope(label: str) -> Iterator[str]:
    """Label every Codex call made inside (and in tasks created inside)."""
    value = str(label or "")[:MAX_PURPOSE_CHARS] or None
    token = _purpose.set(value)
    try:
        yield value or ""
    finally:
        _purpose.reset(token)


def stamp(llm: Any, label: Optional[str] = None) -> Any:
    """Set ``llm.purpose`` to ``label`` (else the current scope's). The
    ledger row and the rate gate's deferral row both read that field."""
    value = label or current_purpose()
    if value:
        try:
            llm.purpose = str(value)[:MAX_PURPOSE_CHARS]
        except Exception:  # noqa: BLE001 — a fake or a frozen model: nothing to stamp
            pass
    return llm
