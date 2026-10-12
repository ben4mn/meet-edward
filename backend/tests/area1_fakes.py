"""Stand-ins for the other areas' modules, for AREA 1's tests (2026-10-11).

AREA 2's ``services.toolset.policy.TurnPolicy`` and AREA 3's
``services.pings.hooks`` merge on their own branches. ``mind_service``
imports both lazily and feature-detects them, so these tests must run with
or without them: each helper uses the real module when it is importable
and installs a faithful stand-in (the shape the specs give verbatim)
otherwise. ``monkeypatch`` undoes either.
"""

from __future__ import annotations

import contextvars
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional


def fake_module(monkeypatch, name: str, module: Any) -> None:
    """Install ``module`` as ``name`` for one test, or ``None`` so that
    importing it raises ``ImportError``. Both ``sys.modules`` and the parent
    package's attribute are set: ``from services import noticing_service``
    reads the attribute first, so once the real module has been imported
    anywhere in the run a ``sys.modules`` entry alone is never seen (the
    integration of 1A, 1B and 1C, 2026-10-11). ``monkeypatch`` undoes both."""
    monkeypatch.setitem(sys.modules, name, module)
    parent_name, _, child = name.rpartition(".")
    parent = sys.modules.get(parent_name) if parent_name else None
    if parent is None:
        return
    if module is None:
        monkeypatch.delattr(parent, child, raising=False)
    else:
        monkeypatch.setattr(parent, child, module, raising=False)


def install_turn_policy(monkeypatch):
    """The ``TurnPolicy`` class (tools-spec §5.2): the real one, else a stand-in."""
    try:
        from services.toolset.policy import TurnPolicy

        return TurnPolicy
    except ImportError:
        pass

    @dataclass(frozen=True)
    class TurnPolicy:  # tools-spec §5.2, verbatim fields
        source: str
        owner_asked: bool
        may_reach_people: bool
        may_push: bool
        reply_to: tuple = ()
        max_turn_usd: Optional[float] = None
        deadline_s: Optional[float] = None
        # The caller's final text answers the reply check (AREA 2's field).
        final_text_is_reply: Optional[bool] = None

    current: "contextvars.ContextVar[Optional[TurnPolicy]]" = contextvars.ContextVar(
        "edward_turn_policy_fake", default=None)

    @contextmanager
    def policy_scope(policy):
        token = current.set(policy)
        try:
            yield policy
        finally:
            current.reset(token)

    pkg = types.ModuleType("services.toolset")
    pkg.__path__ = []  # a package, so ``services.toolset.policy`` resolves
    mod = types.ModuleType("services.toolset.policy")
    mod.TurnPolicy = TurnPolicy
    # The current turn's policy (AREA 2's names): set by ``policy_scope``.
    mod.policy_is_set = lambda: current.get() is not None
    mod.current_policy = lambda: current.get() or TurnPolicy(source="background", owner_asked=False,
                                                             may_reach_people=False, may_push=False)
    mod.policy_scope = policy_scope
    fake_module(monkeypatch, "services.toolset", pkg)
    fake_module(monkeypatch, "services.toolset.policy", mod)
    return TurnPolicy


def hide_turn_policy(monkeypatch) -> None:
    """As if AREA 2 had not merged: the import fails."""
    fake_module(monkeypatch, "services.toolset.policy", None)


def _chat_key(chat: Optional[str]) -> str:
    """pings-spec Appendix A2 ``chat_key``, verbatim."""
    t = str(chat or "").strip()
    if ";" in t:
        t = t.rsplit(";", 1)[1].strip()
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 10 and all(ch in "0123456789+()-. " for ch in t):
        return "p:" + digits[-10:]
    return t.lower()


def install_pings_hooks(monkeypatch, *, in_flight: Iterable[str] = (), lines: Iterable[str] = (),
                        addressed: Iterable[str] = ()):
    """``services.pings.hooks``: the real module with its readers faked, else
    a stand-in with the same names (pings-spec Appendix A2)."""
    in_flight_keys = {_chat_key(c) for c in in_flight}
    pack = list(lines)
    addressed_keys = {_chat_key(c) for c in addressed}
    calls: List[Any] = []

    async def pack_lines(now=None, hours=6):
        calls.append(("pack_lines", hours))
        return list(pack)

    async def addressed_recently(chat_id, hours=24):
        return _chat_key(chat_id) in addressed_keys

    async def open_chats():
        return set()

    try:
        from services.pings import hooks as real
    except ImportError:
        real = None
    if real is not None:
        monkeypatch.setattr(real, "chats_in_flight", lambda: set(in_flight_keys))
        monkeypatch.setattr(real, "pack_lines", pack_lines)
        monkeypatch.setattr(real, "addressed_recently", addressed_recently)
        monkeypatch.setattr(real, "open_chats", open_chats)
        real.test_calls = calls  # type: ignore[attr-defined]
        return real

    pkg = types.ModuleType("services.pings")
    pkg.__path__ = []
    mod = types.ModuleType("services.pings.hooks")
    mod.CURRENT_PING = contextvars.ContextVar("current_ping_fake", default=None)
    mod.chat_key = _chat_key
    mod.chats_in_flight = lambda: set(in_flight_keys)
    mod.pack_lines = pack_lines
    mod.addressed_recently = addressed_recently
    mod.open_chats = open_chats
    mod.current_ping = lambda: mod.CURRENT_PING.get()
    mod.test_calls = calls
    pkg.hooks = mod
    fake_module(monkeypatch, "services.pings", pkg)
    fake_module(monkeypatch, "services.pings.hooks", mod)
    return mod


def hide_pings(monkeypatch) -> None:
    fake_module(monkeypatch, "services.pings", None)
    fake_module(monkeypatch, "services.pings.hooks", None)
