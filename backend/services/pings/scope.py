"""What a friend's ping may do (AREA 3, 2026-10-11; docs/architecture/pings.md).

**The default** (public release, 2026-10-12): a ping from someone who is not
the owner runs on AREA 2's tool loop with every tool but the ones that act
as the owner on the owner's own machine — the shell and the file editor
(``bash``, ``str_replace_based_edit_tool``), the owner's signed-in Chrome
(``chrome``, every action), the screens in the owner's home and on the
owner's desk (``screen``, every action), and ``look`` at the owner's display
or Chrome, at a file on the Mac or at one the owner uploaded. ``look`` at
what was sent in a chat (``attachment:<id>``, ``media:<id>``), at an http(s)
URL or at Edward's own headless browser stays, and so do the web, Edward's
own browser, memory, the chats, the reply and the stand-down. The refusal
is an honest ``Tool error:`` that says only the owner can turn it on. A
prompt injection in a friend's message (or in a page or file they point
Edward at) then cannot reach code execution as the owner's macOS user.

``EDWARD_FRIENDS_FULL_TOOLSET=on`` restores the reference host's design
decision of 2026-10-11: a friend's ping gets Edward's **full toolset** —
the shell, his browsers, his screens, memory, every chat — bounded by the
per-chat rate (``limits``), Edward's own spend share
(``llm_spend.own_work_scope``), the loop breaker and the egress secret scan.
The owner's own pings keep everything either way.

That decision came with its conditions: shell output is redacted before the
model sees it, never a second Google Chrome, never deleting the owner's
memories. AREA 2's sandbox keeps them (docs/architecture/computer.md). The
**legacy loop** (no ``chat_with_memory(policy=…)``: AREA 2 not merged, or its
import broken) keeps none of them — its ``execute_shell`` is bash as the
owner's macOS user with no redaction — so on that loop a friend's turn goes
without exactly the tools that would break them
(``LEGACY_THIRD_PARTY_EXCLUDED``, review 2026-10-11). The owner's own pings
keep everything on either loop.

One rail holds on every loop, because it is not about access: **Edward never
starts or joins someone else's conversation unasked.** A send from a
friend's ping — any send: the send tools, apple-mcp's ``messages`` and
``mail`` send operations (the taint gate's own list), typing into the
owner's Chrome outside a search box, or a task scheduled to run later with nobody's
ask behind it — goes into that ping's chat, or into a chat that has
addressed Edward in the last day (recipient rule (b), which keeps the
friend-to-friend relay); never to someone who asked him nothing. What Edward
says about the owner to the friend is judgment, carried by the prompt
(``prompt.THIRD_PARTY``), not a tool rule.

A custom MCP tool (a server the owner installed; ``toolset.names.is_custom_mcp``)
is refused outright in a friend's ping: it may post, mail or pay through its
own service, and where its call lands can't be checked against that rule.

``refusal(name, args)`` is called by the tool executor's boundary step for
every call in a turn (``graph.tool_loop._boundary_check``, AREA 2's executor;
``streaming`` re-exports it) while a friend's ping is current. It returns the reason without
the ``Tool error:`` prefix, or None. The refusals are model-facing: they
name the owner with the ``{OWNER}`` token, rendered once at import.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from services.owner import owner_text
from services.pings import hooks

logger = logging.getLogger(__name__)

# The legacy loop's ``allowed_tools`` for a friend's ping: None is every
# tool but ``LEGACY_THIRD_PARTY_EXCLUDED``.
LEGACY_THIRD_PARTY_TOOLS = None

# What a friend's turn on the legacy loop goes without: code that runs as
# the owner's macOS user with no sandbox and no output redaction (and can
# osascript, psql the live database or launch a second Chrome), the ways to
# start such code elsewhere (workers, Codex, a Claude session, an MCP
# install), deleting memories, a task that fires later with full hands, and
# acting in the owner's logged-in Chrome. Reading, searching, remembering,
# waking himself later, the reply and the stand-down all stay.
LEGACY_THIRD_PARTY_EXCLUDED: FrozenSet[str] = frozenset({
    "execute_shell", "execute_code", "execute_javascript", "execute_sql",
    "spawn_worker", "send_to_worker", "spawn_cc_worker", "start_claude_session",
    "add_mcp_server", "update_mcp_server", "restart_mcp_server", "remove_mcp_server",
    "remember_forget", "forget_about",
    "schedule_event",
    "browser_type", "browser_steps", "browser_click", "browser_key",
})

ADDRESSED_HOURS = 24
SEND_TOOLS = frozenset({"send_message", "send_imessage", "send_sms", "send_whatsapp"})
# apple-mcp multiplexes reads and sends through one tool per app; the taint
# gate's list is the source of truth, this copy only when it can't be read.
_SEND_OPERATIONS_FALLBACK: Dict[str, FrozenSet[str]] = {
    "mail": frozenset({"send"}),
    "messages": frozenset({"send", "schedule"}),
}
# Where each send tool is addressed, first set wins: AREA 2's send_message
# (chat_id | to), today's tools (recipient, chat_id/contact, phone_number)
# and apple-mcp's ``messages`` (phoneNumber) and ``mail`` (to).
TARGET_ARGS = ("chat_id", "to", "recipient", "contact", "phone_number", "phoneNumber")
# More people a mail reaches.
EXTRA_TARGET_ARGS = ("cc", "bcc")
# A task that runs later as a full turn with no ping behind it: the legacy
# tool, and the toolset's ``schedule(action="add")`` of anything but a wake
# (a task — its default kind — or a reminder; AREA 2 + 3 merge).
LATER_TOOLS = frozenset({"schedule_event"})
TOOLSET_SCHEDULE = "schedule"
TYPING_TOOLS = frozenset({"browser_type", "browser_steps"})

LATER_REFUSAL = owner_text(
    "a task scheduled from a friend's ask would run later with my full hands and nobody's ask behind it — "
    "that's {OWNER}'s call. If they want a reminder or a follow-up, say you'll check with {OWNER} and tell "
    "{OWNER} what and when; to look again yourself, use wake_me.")
LATER_REFUSAL_TOOLSET = owner_text(
    "a task or reminder scheduled from a friend's ask would run later with my full hands and nobody's ask "
    "behind it — that's {OWNER}'s call. If they want a reminder or a follow-up, say you'll check with {OWNER} "
    "and tell {OWNER} what and when; to look again yourself, schedule a wake (kind=wake).")
TYPING_REFUSAL = owner_text(
    "typing into {OWNER}'s Chrome for someone who isn't {OWNER} goes only into a search box — anything else "
    "there speaks as {OWNER}. Answer here instead.")
# ``{name}`` / ``{target}`` are filled per call with str.replace, so a tool
# name or a handle with braces in it can't break the sentence.
_CUSTOM_MCP_REFUSAL = owner_text(
    "{name} comes from a server {OWNER} installed, and I can't check who or what it reaches — for someone "
    "who isn't {OWNER}, that's {OWNER}'s call. Answer here, and if they want it done, tell {OWNER} what.")
_NOT_ASKED_REFUSAL = owner_text(
    "{target} hasn't asked you anything, and the person asking here isn't {OWNER} — starting a conversation "
    "with someone else is {OWNER}'s call. Answer here, and if they want something passed on, tell {OWNER} "
    "what and to whom.")


# ---- the friends' default (2026-10-12) ---------------------------------------------------

FRIENDS_FULL_TOOLSET_ENV = "EDWARD_FRIENDS_FULL_TOOLSET"
# Code that runs as the owner's macOS user: the toolset's computer and the
# legacy runners (the legacy loop drops them too; these catch a stray call).
COMPUTER_TOOLS = frozenset({"bash", "str_replace_based_edit_tool", "execute_shell", "execute_code",
                            "execute_javascript", "execute_sql"})
# The owner's signed-in Chrome: the toolset's ``chrome`` and the legacy hands
# (``browser_open``, ``browser_read`` … — Edward's own headless browser is
# ``browser``, no underscore, and stays).
CHROME_TOOLS = frozenset({"chrome"})
SCREEN_TOOLS = frozenset({"screen"})
# ``look`` targets a friend's ping may use: what was sent in a chat, a URL,
# Edward's own headless page.
_LOOK_OK_RE = re.compile(r"^(?:attachment:|media:|https?://)", re.I)
_LOOK_OWN_BROWSER = "browser"

COMPUTER_REFUSAL = owner_text(
    "my computer (the shell and the file editor) runs as {OWNER} on {OWNER}'s Mac, so it stays off when "
    "someone who isn't {OWNER} asks — only {OWNER} can turn that on. Answer from what you know, the web "
    "(web_search, web_fetch, your own browser) and the chats; if it needs your computer, tell {OWNER}.")
CHROME_REFUSAL = owner_text(
    "{OWNER}'s Chrome is signed in as {OWNER}, so I don't use it when someone who isn't {OWNER} asks — only "
    "{OWNER} can turn that on. Your own browser (browser) and web_fetch still work.")
SCREEN_REFUSAL = owner_text(
    "the screens are in {OWNER}'s home and on {OWNER}'s desk, so they stay as they are when someone who "
    "isn't {OWNER} asks — only {OWNER} can turn that on. Answer here.")
LOOK_REFUSAL = owner_text(
    "when someone who isn't {OWNER} asks, I look only at what was sent in a chat (attachment:<id>, "
    "media:<id>), a link (http/https) or my own browser (browser) — not {OWNER}'s screen, {OWNER}'s Chrome "
    "or files on {OWNER}'s Mac; only {OWNER} can turn that on.")


def friends_full_toolset() -> bool:
    """``EDWARD_FRIENDS_FULL_TOOLSET=on``: a friend's ping gets the full
    toolset (the reference host's 2026-10-11 decision). Read per call."""
    return (os.getenv(FRIENDS_FULL_TOOLSET_ENV) or "").strip().lower() in ("on", "1", "true", "yes")


def _as_toolset(name: str, args: Any) -> Tuple[str, Any]:
    """A retired name as the toolset call it maps to (``names.alias_call``),
    so ``execute_shell`` or ``look_at_screen`` meets the same rule."""
    try:
        from services.toolset.names import alias_call

        mapped = alias_call(name, args)
    except Exception:  # noqa: BLE001
        mapped = None
    return mapped if mapped is not None else (name, args)


def _look_allowed(args: Any) -> bool:
    target = str((args or {}).get("target") or "").strip() if isinstance(args, dict) else ""
    return bool(_LOOK_OK_RE.match(target)) or target.lower() == _LOOK_OWN_BROWSER


def friends_default_refusal(name: str, args: Any) -> Optional[str]:
    """Why a friend's ping may not make this call under the default, or
    None. Pure but for the env switch: callers check that the current ping
    is a friend's."""
    if friends_full_toolset():
        return None
    n, a = _as_toolset(str(name or ""), args)
    if n in COMPUTER_TOOLS:
        return COMPUTER_REFUSAL
    if n in CHROME_TOOLS or n.startswith("browser_"):
        return CHROME_REFUSAL
    if n in SCREEN_TOOLS:
        return SCREEN_REFUSAL
    if n == "look" and not _look_allowed(a):
        return LOOK_REFUSAL
    return None


def custom_mcp_refusal(name: str) -> str:
    return _CUSTOM_MCP_REFUSAL.replace("{name}", str(name))


def _is_custom_mcp(name: str) -> bool:
    try:
        from services.toolset.names import is_custom_mcp

        return is_custom_mcp(name)
    except Exception:  # noqa: BLE001
        return False


def _send_operations() -> Dict[str, FrozenSet[str]]:
    try:
        from services.taint_service import GATED_SEND_OPERATIONS

        return dict(GATED_SEND_OPERATIONS)
    except Exception:  # noqa: BLE001
        return dict(_SEND_OPERATIONS_FALLBACK)


def _operation(args: Any) -> str:
    return str((args or {}).get("operation") or "").strip().lower() if isinstance(args, dict) else ""


def is_send(name: str, args: Any = None) -> bool:
    """A call that reaches a person: a send tool, or apple-mcp's ``messages``
    / ``mail`` with a send operation. Pure but for the taint gate's list."""
    n = str(name or "")
    if n in SEND_TOOLS or n.startswith("whatsapp_send"):
        return True
    ops = _send_operations().get(n)
    return ops is not None and _operation(args) in ops


def _values(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if v and str(v).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()] if "@" in text else [text]


def send_targets(args: Any) -> List[str]:
    """Everyone a send call is addressed to, as given: the first target
    argument that is set (all of it, for a list of recipients) plus a mail's
    cc and bcc. Pure."""
    if not isinstance(args, dict):
        return []
    out: List[str] = []
    for key in TARGET_ARGS:
        if args.get(key):
            out += _values(args[key])
            break
    for key in EXTRA_TARGET_ARGS:
        if args.get(key):
            out += _values(args[key])
    return out


def send_target(args: Any) -> Optional[str]:
    """The first chat or person a send call is addressed to, as given. Pure."""
    targets = send_targets(args)
    return targets[0] if targets else None


def _leaves_a_task(name: str, args: Any) -> bool:
    """The toolset's ``schedule(action="add")`` of a task or a reminder (a
    wake is Edward looking again himself). Pure."""
    if name != TOOLSET_SCHEDULE or not isinstance(args, dict):
        return False
    if str(args.get("action") or "").strip().lower() != "add":
        return False
    return str(args.get("kind") or "task").strip().lower() != "wake"


def _types_off_search(name: str, args: Any) -> bool:
    """Typing in the owner's Chrome that lands anywhere but a search box (the
    taint gate's test; the site of a bare ``browser_type`` is unknown here)."""
    if name not in TYPING_TOOLS:
        return False
    try:
        from services.taint_service import is_gated_send

        return bool(is_gated_send(name, args, current_url=None))
    except Exception:  # noqa: BLE001 - unsure: it would speak as the owner
        return True


async def _addressed_recently(chat: str, hours: float = ADDRESSED_HOURS) -> bool:
    return await hooks.addressed_recently(chat, hours)


def _not_asked(target: str) -> str:
    return _NOT_ASKED_REFUSAL.replace("{target}", str(target))


async def refusal(name: str, args: Any) -> Optional[str]:
    """Why a friend's ping may not make this call, or None (it may)."""
    ctx = hooks.current_ping()
    if not ctx or not ctx.get("third_party"):
        return None
    n = str(name or "")
    default = friends_default_refusal(n, args)
    if default:
        return default
    if _is_custom_mcp(n):
        return custom_mcp_refusal(n)
    if n in LATER_TOOLS:
        return LATER_REFUSAL
    if _leaves_a_task(n, args):
        return LATER_REFUSAL_TOOLSET
    if _types_off_search(n, args):
        return TYPING_REFUSAL
    if not is_send(n, args):
        return None
    for target in send_targets(args):
        if hooks.same_chat(ctx, target):
            continue
        try:
            if await _addressed_recently(target, ADDRESSED_HOURS):
                continue
        except Exception as exc:  # noqa: BLE001 - when unsure, don't start a conversation
            logger.warning("pings: addressed_recently failed in scope: %s", hooks.brief_error(exc))
        return _not_asked(target)
    return None
