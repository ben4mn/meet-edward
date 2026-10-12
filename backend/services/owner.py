"""Who Edward works for: the owner's name, handles and household.

Edward serves one person, the owner. Everything that names the owner reads
it here, from the environment (``.env``), so nothing about a particular
person is baked into the code:

* ``EDWARD_OWNER_NAME`` — what Edward calls the owner ("Sam"). Unset, the
  prompts say "the owner".
* ``EDWARD_OWNER_FULL_NAME`` and ``EDWARD_OWNER_ALIASES`` (comma-separated
  nicknames) — other names the owner goes by, for recognising them in
  messages and in what the model writes.
* ``EDWARD_OWNER_HANDLES`` — the owner's own phone numbers, emails and
  Apple ID (comma-separated): how Edward recognises the owner's iMessages
  and self-chat. The heartbeat config's ``imessage_owner_handles`` column
  adds to these.
* ``EDWARD_OWNER_EMAILS`` — the owner's own email addresses (``GMAIL_ACCOUNT``
  counts too): mail from these is the owner's own, and Edward never emails
  the owner.
* ``EDWARD_HOUSEHOLD`` — the people at the household hub, comma-separated,
  owner first ("Sam,Alex"). Unset, it is just the owner.

Model-facing text names the owner through ``owner_text``: a prompt or tool
description is written with the ``{OWNER}`` token and rendered once at
import, so the text stays byte-identical between calls (the prompt cache
depends on it). A tool whose description is its docstring takes
``@owner_doc`` (innermost, so the docstring is rendered before the tool
decorator reads it). Code that matches the owner's name in text uses
``owner_names()`` or ``owner_name_pattern()``, never a literal.

Values are read on every call (``.env`` is loaded before services import),
so tests and one-off scripts can set them with ``monkeypatch.setenv``;
text rendered at import keeps the value it was rendered with.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Tuple, TypeVar

OWNER_TOKEN = "{OWNER}"
DEFAULT_OWNER_NAME = "the owner"

F = TypeVar("F", bound=Callable)


def _env_list(name: str) -> Tuple[str, ...]:
    raw = os.getenv(name) or ""
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def owner_name() -> str:
    """What Edward calls the owner: ``EDWARD_OWNER_NAME``, else "the owner"."""
    return (os.getenv("EDWARD_OWNER_NAME") or "").strip() or DEFAULT_OWNER_NAME


def owner_name_is_set() -> bool:
    return bool((os.getenv("EDWARD_OWNER_NAME") or "").strip())


def owner_full_name() -> str:
    return (os.getenv("EDWARD_OWNER_FULL_NAME") or "").strip()


def owner_aliases() -> Tuple[str, ...]:
    return _env_list("EDWARD_OWNER_ALIASES")


def owner_names() -> frozenset:
    """Every name the owner goes by, lowercased (empty when none is set)."""
    names = set()
    if owner_name_is_set():
        names.add(owner_name().lower())
    if owner_full_name():
        names.add(owner_full_name().lower())
    names.update(a.lower() for a in owner_aliases())
    return frozenset(names)


def owner_name_pattern() -> str:
    """A regex alternation matching any of the owner's names (longest first).

    With no name configured it matches nothing (``(?!)``), so a check built
    on it never fires on a stranger's name.
    """
    names = sorted(owner_names(), key=len, reverse=True)
    if not names:
        return r"(?!)"
    return "(?:" + "|".join(re.escape(n) for n in names) + ")"


def owner_text(text: str) -> str:
    """Render the ``{OWNER}`` token in model-facing text."""
    if not text:
        return text
    return text.replace(OWNER_TOKEN, owner_name())


def owner_doc(fn: F) -> F:
    """Render ``{OWNER}`` in a function's docstring (a tool's description)."""
    if fn.__doc__:
        fn.__doc__ = owner_text(fn.__doc__)
    return fn


def owner_handles() -> Tuple[str, ...]:
    """The owner's own phone numbers, emails and Apple ID, from the env."""
    return _env_list("EDWARD_OWNER_HANDLES")


def owner_emails() -> Tuple[str, ...]:
    emails = list(_env_list("EDWARD_OWNER_EMAILS"))
    gmail = (os.getenv("GMAIL_ACCOUNT") or "").strip()
    if gmail and gmail.lower() not in {e.lower() for e in emails}:
        emails.append(gmail)
    return tuple(emails)


def household() -> Tuple[str, ...]:
    """The people at the household hub, owner first."""
    people = _env_list("EDWARD_HOUSEHOLD")
    if people:
        return people
    return (owner_name() if owner_name_is_set() else "Me",)
