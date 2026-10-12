"""The toolset's names, in one place (docs/architecture/tool-loop.md).

* ``TOOL_ORDER`` — the 14 tools, in the order they are bound on every
  surface (the prompt-cache prefix is shared by every same-model turn).
* Tiers as ``(tool, action)`` pairs, for the thought journal, the event
  ledger and the mind: what reaches people (OUTWARD), what commits Edward
  to something later (COMMITMENT), what changes a surface (SURFACE) and
  bookkeeping (INTERNAL). An action tool's tier depends on its ``action``;
  a custom MCP tool's is always OUTWARD (``CUSTOM_MCP_TOOLS``).
* ``alias_call`` — a call to a retired name (``send_imessage``,
  ``leave_note``, ``wake_me`` …) that a not-yet-updated prompt still
  teaches is answered by the new tool with the arguments translated, for
  one release, instead of "Unknown tool".

Pure: no I/O (``CUSTOM_MCP_TOOLS`` is the one piece of state, filled by the
registry).
"""

from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet, Optional, Tuple

TOOL_ORDER: Tuple[str, ...] = (
    "bash",
    "str_replace_based_edit_tool",
    "web_search",
    "web_fetch",
    "browser",
    "chrome",
    "look",
    "messages",
    "send_message",
    "tell_owner",
    "memory",
    "schedule",
    "screen",
    "plan",
)
TOOLSET_NAMES: FrozenSet[str] = frozenset(TOOL_ORDER)

# Built by other agents (A: the computer; C: web, browsers, look). The
# registry imports them when present and stands in a stub otherwise.
EXTERNAL_TOOLS: Dict[str, str] = {
    "bash": "computer_tools",
    "str_replace_based_edit_tool": "computer_tools",
    "web_search": "web",
    "web_fetch": "web",
    "browser": "browsers",
    "chrome": "browsers",
    "look": "look",
}

# Tools whose first argument is ``action`` (their work-rail summary is
# "{action}: {detail}").
ACTION_TOOLS: FrozenSet[str] = frozenset({"browser", "chrome", "messages", "memory", "schedule", "screen"})

# The tools that can reach a person or a surface (a shim for older callers:
# subtracting these from a legacy list gives a quiet turn).
REACH_TOOLS: FrozenSet[str] = frozenset({"send_message", "tell_owner", "chrome", "screen"})

ANY = None  # every action of a tool

OUTWARD: FrozenSet[Tuple[str, Optional[str]]] = frozenset({
    ("send_message", ANY), ("tell_owner", ANY), ("chrome", "type"), ("chrome", "steps"),
})
COMMITMENT: FrozenSet[Tuple[str, Optional[str]]] = frozenset({
    ("schedule", "add"), ("schedule", "cancel"), ("messages", "stand_down"),
    # The owner's calendar and Reminders (2026-10-11): a dry run records them, a
    # wake that made one would write a journal entry.
    ("schedule", "event_add"), ("schedule", "event_update"), ("schedule", "event_delete"),
    ("schedule", "reminder_add"), ("schedule", "reminder_complete"), ("schedule", "reminder_delete"),
})
SURFACE: FrozenSet[Tuple[str, Optional[str]]] = frozenset({
    ("screen", "compose"), ("screen", "say"), ("screen", "show"), ("screen", "clear"), ("screen", "reset"),
    ("screen", "bring_up"), ("screen", "widget"), ("screen", "widget_code"), ("screen", "widget_clear"),
})
INTERNAL: FrozenSet[Tuple[str, Optional[str]]] = frozenset({
    ("memory", "remember"), ("memory", "update"), ("memory", "close"), ("memory", "doc_write"),
    ("memory", "doc_edit"), ("plan", ANY),
})
WIDGET_ACTIONS: FrozenSet[str] = frozenset({"widget", "widget_code", "widget_clear"})

# Tools of the custom MCP servers the owner installed, by name → server
# (a security fix, 2026-10-11). Nothing here can tell what
# one does — post, pay, delete — so every call is OUTWARD: recorded in a dry
# run, journaled, egress-scanned whole, refused in a friend's ping. Only a
# tool the registry tagged (``metadata.custom_mcp``) and bound after the 14
# is counted (``note_custom_mcp``): a legacy or server tool's name keeps its
# tier.
CUSTOM_MCP_KEY = "custom_mcp"
CUSTOM_MCP_TOOLS: Dict[str, str] = {}

# A shell command that leaves something running or installed on the host
# Mac: an unattended turn that runs one writes a journal entry.
_BASH_COMMITMENT = re.compile(
    r"(\b(pip3?|uv|npm|npx|brew|gem|cargo)\s+(install|add|i)\b|\bnohup\b|(?<![&|])&\s*($|\n|;)|"
    r"\bcrontab\b|\blaunchctl\b|\bgit\s+push\b)",
    re.IGNORECASE)


def action_of(args: Any) -> str:
    if isinstance(args, dict):
        return str(args.get("action") or "").strip().lower()
    return ""


def _in(pairs: FrozenSet[Tuple[str, Optional[str]]], name: str, action: str) -> bool:
    return (name, ANY) in pairs or (name, action) in pairs


def note_custom_mcp(tool: Any) -> Optional[str]:
    """Count ``tool`` as a custom MCP tool when it is tagged
    ``metadata.custom_mcp`` (never one of the 14); its server, or None."""
    md = getattr(tool, "metadata", None)
    server = md.get(CUSTOM_MCP_KEY) if isinstance(md, dict) else None
    name = str(getattr(tool, "name", "") or "")
    if not server or not name or name in TOOLSET_NAMES:
        return None
    CUSTOM_MCP_TOOLS[name] = str(server)
    return str(server)


def custom_mcp_server(name: Any) -> Optional[str]:
    return CUSTOM_MCP_TOOLS.get(str(name or ""))


def is_custom_mcp(name: Any) -> bool:
    return str(name or "") in CUSTOM_MCP_TOOLS


def tier(name: Any, args: Any = None) -> Optional[str]:
    """``outward`` | ``commitment`` | ``surface`` | ``internal`` | ``None``
    for a toolset call; ``outward`` for a custom MCP tool; ``None`` for any
    other name this module does not own."""
    n = str(name or "")
    if n not in TOOLSET_NAMES:
        return "outward" if is_custom_mcp(n) else None
    act = action_of(args)
    if _in(OUTWARD, n, act):
        return "outward"
    if _in(COMMITMENT, n, act):
        return "commitment"
    if n == "bash" and isinstance(args, dict) and _BASH_COMMITMENT.search(str(args.get("command") or "")):
        return "commitment"
    if _in(SURFACE, n, act):
        return "surface"
    if _in(INTERNAL, n, act):
        return "internal"
    return None


def is_outward(name: Any, args: Any = None) -> bool:
    return tier(name, args) == "outward"


def reaches_people(name: Any, args: Any = None) -> bool:
    """A message to a person, or a push to the owner (a note is not a reach)."""
    n = str(name or "")
    if n == "send_message":
        return True
    if n == "tell_owner":
        return bool(isinstance(args, dict) and args.get("urgent") in (True, "true", "True", 1))
    return False


def surface_of(name: Any, args: Any = None) -> Optional[str]:
    """``widget`` | ``screen`` for a call that changes one, else None."""
    n = str(name or "")
    if n != "screen":
        return None
    act = action_of(args)
    if act in WIDGET_ACTIONS:
        return "widget"
    if ("screen", act) in SURFACE:
        return "screen"
    return None


def is_unattended_code(name: Any, args: Any = None) -> bool:
    """A call that leaves code installed or running on the host Mac."""
    return str(name or "") == "bash" and tier(name, args) == "commitment"


# ------------------------------------------------------------- aliases --

def _pick(args: Dict[str, Any], **mapping: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for new, old in mapping.items():
        if old in args and args[old] not in (None, ""):
            out[new] = args[old]
    return out


def _send_imessage(a: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    out: Dict[str, Any] = {"message": a.get("message", "")}
    if a.get("chat_id"):
        out["chat_id"] = a["chat_id"]
    elif a.get("contact"):
        out["to"] = a["contact"]
    return "send_message", out


def _send_message_old(a: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    out = {k: v for k, v in a.items() if k not in ("recipient", "channel")}
    if a.get("recipient") and not a.get("to") and not a.get("chat_id"):
        out["to"] = a["recipient"]
    return "send_message", out


def _push(a: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    out: Dict[str, Any] = {"message": a.get("body") or a.get("title") or ""}
    out.update(_pick(a, title="title", link="url"))
    out["urgent"] = True
    return "tell_owner", out


def _note(a: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    out: Dict[str, Any] = {"message": a.get("body") or a.get("title") or ""}
    out.update(_pick(a, title="title", link="url"))
    return "tell_owner", out


def _action(tool: str, action: str, **mapping: str):
    def convert(a: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        return tool, {"action": action, **_pick(a, **mapping)}
    return convert


def _look_at_screen(a: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    at = str(a.get("at") or "auto").lower()
    return "look", {"target": "chrome" if at == "page" else "screen"}


_ALIASES = {
    "send_imessage": _send_imessage,
    "send_push_notification": _push,
    "leave_note": _note,
    "wake_me": lambda a: ("schedule", {"action": "add", "kind": "wake",
                                       **_pick(a, when="when", what="why", memory_id="memory_id")}),
    "schedule_event": lambda a: ("schedule", {"action": "add", "kind": "task",
                                              **_pick(a, when="scheduled_at", what="description",
                                                      repeat="recurrence_pattern", why="why")}),
    "cancel_scheduled_event": _action("schedule", "cancel", id="event_id"),
    "list_scheduled_events": _action("schedule", "list", status="status"),
    "stand_down": _action("messages", "stand_down", chat_id="chat_id", requested_by="requested_by"),
    "get_recent_messages": _action("messages", "recent", contact="contact", hours="hours"),
    "review_heartbeat": _action("messages", "recent", contact="sender"),
    "read_imessage_thread": _action("messages", "thread", chat_id="chat_identifier", limit="limit",
                                    before_rowid="before_rowid"),
    "list_chat_participants": _action("messages", "participants", chat_id="chat_identifier"),
    "lookup_contact": _action("messages", "who", query="query"),
    "lookup_phone": _action("messages", "who", query="phone_number"),
    "remember": _action("memory", "remember", content="content", type="memory_type", about="about",
                        origin="origin", until="valid_until", confidence="confidence"),
    "remember_update": _action("memory", "update", id="memory_id", content="new_content", until="valid_until"),
    "update_memory_confidence": _action("memory", "update", id="memory_id", confidence="new_confidence"),
    "remember_search": _action("memory", "search", query="query", type="memory_type"),
    "search_documents": lambda a: ("memory", {"action": "search", "kind": "documents",
                                              **_pick(a, query="query", tags="tags")}),
    "close_intention": _action("memory", "close", id="memory_id", outcome="outcome"),
    "how_do_i_know": _action("memory", "why", id="memory_id", claim="claim"),
    "save_document": _action("memory", "doc_write", title="title", content="content", tags="tags"),
    "edit_document": _action("memory", "doc_write", id="document_id", title="title", content="content",
                             tags="tags"),
    "read_document": _action("memory", "doc_read", id="document_id"),
    "compose_screen": _action("screen", "compose", pieces="pieces", me="me", why="why",
                              hold_minutes="hold_minutes"),
    "say_on_screen": _action("screen", "say", line="line", aloud="aloud"),
    "show_on_screen": _action("screen", "show", kind="kind", body="body", source="source", title="title",
                              seconds="seconds", screen="screen"),
    "clear_screen": _action("screen", "clear", screen="screen"),
    "reset_screen_layout": _action("screen", "reset"),
    "bring_up_screen": _action("screen", "bring_up"),
    "update_widget": _action("screen", "widget", sections="sections", title="title", subtitle="subtitle"),
    "update_widget_code": _action("screen", "widget_code", code="code"),
    "clear_widget_code": _action("screen", "widget_clear"),
    "get_widget_state_tool": _action("screen", "state"),
    "fetch_page_content": lambda a: ("web_fetch", _pick(a, url="url")),
    "look_at_screen": _look_at_screen,
    "execute_shell": lambda a: ("bash", _pick(a, command="command")),
}


def alias_call(name: Any, args: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    """``(new name, new args)`` for a call to a retired name, else None.
    ``send_message`` with the old ``recipient`` argument is translated in
    place."""
    n = str(name or "")
    a = args if isinstance(args, dict) else {}
    if n == "send_message" and "recipient" in a and "to" not in a and "chat_id" not in a:
        return _send_message_old(a)
    convert = _ALIASES.get(n)
    if convert is None:
        return None
    return convert(a)


RETIRED_NAMES: FrozenSet[str] = frozenset(_ALIASES)


# ------------------------------------------------------ the legacy view --

def legacy_view(name: Any, args: Any) -> Tuple[str, Dict[str, Any]]:
    """A toolset call as the old tool it replaced — ``send_message`` into a
    chat as ``send_imessage``, ``tell_owner`` as a push or a note,
    ``schedule(add)`` as ``schedule_event`` / ``wake_me`` … — so the thought
    journal's sentences ("Replied to Parker in Family", "Pushed you: …")
    describe the new tools without a second copy of each. Anything else
    comes back unchanged. Pure."""
    n = str(name or "")
    a = dict(args) if isinstance(args, dict) else {}
    act = action_of(a)

    def pick(**mapping: str) -> Dict[str, Any]:
        return {old: a[new] for old, new in mapping.items() if a.get(new) not in (None, "")}

    if n == "send_message" and (a.get("subject") or str(a.get("chat_id") or "").startswith(("gmail:", "icloud:"))):
        return "mail", {"to": a.get("to") or "a reply", "subject": a.get("subject")}
    if n == "send_message" and ("chat_id" in a or "to" in a):
        return "send_imessage", pick(chat_id="chat_id", contact="to", message="message")
    if n == "tell_owner":
        out = {"title": a.get("title") or "", "body": a.get("message") or "", "url": a.get("link")}
        if a.get("urgent") in (True, "true", "True", 1):
            return "send_push_notification", {**out, "urgency": "normal"}
        return "leave_note", out
    if n == "schedule":
        if act == "add" and str(a.get("kind") or "").lower() == "wake":
            return "wake_me", pick(when="when", why="what", memory_id="memory_id")
        if act == "add":
            return "schedule_event", pick(description="what", scheduled_at="when", recurrence_pattern="repeat")
        if act == "cancel":
            return "cancel_scheduled_event", pick(event_id="id")
    if n == "messages" and act == "stand_down":
        return "stand_down", pick(chat_id="chat_id", requested_by="requested_by")
    if n == "memory":
        if act == "remember":
            return "remember", pick(content="content")
        if act == "update":
            if a.get("confidence") not in (None, "") and not a.get("content") and not a.get("until"):
                return "update_memory_confidence", pick(memory_id="id", new_confidence="confidence")
            return "remember_update", pick(memory_id="id", new_content="content", valid_until="until")
        if act == "close":
            return "close_intention", pick(memory_id="id", outcome="outcome")
        if act in ("doc_write", "doc_edit"):
            if a.get("id"):
                return "edit_document", pick(document_id="id", title="title")
            return "save_document", pick(title="title")
    if n == "screen":
        mapping = {"compose": "compose_screen", "say": "say_on_screen", "show": "show_on_screen",
                   "clear": "clear_screen", "reset": "reset_screen_layout", "widget": "update_widget",
                   "widget_code": "update_widget_code", "widget_clear": "clear_widget_code"}
        if act in mapping:
            return mapping[act], pick(kind="kind", title="title", screen="screen")
    if n == "chrome" and act in ("type", "steps"):
        return ("browser_type", pick(text="text")) if act == "type" else ("browser_steps", pick(steps="steps"))
    return n, a
