"""The thought journal — one short, readable conversation per wake that acts.

Why (design decision, 2026-09-25): Edward's thoughts that ended in actions
used to read as chats with himself — the owner could open the history and
follow his thinking into action. As features were layered in that got lost
and his thinking became confusing to read and scattered. Edward doesn't
need approval to act, but it must be obvious when he is choosing to.

The mind rebuild (2026-09-18) deleted every caller of
``thought_conversation_service.create_thought_conversation``: every wake
lands in the one ``edward-mind`` thread, whose "user" messages are 10–20K
character raw packs. This restores the old pattern without going back to
the templated senders: when a wake (any reason but the surfaces pulse)
does something outward or commits to something, it becomes its own
``edward_thought`` conversation, built from

* the **record** — what the tool executors saw the turn do
  (``TurnRecord.actions``: name, bounded args, result head, ok, held), so
  "Sent an iMessage to Parker" is only ever written when the send said so;
* **his own words** — the reasoning summaries his model responses carried
  ("What I noticed") and his closing sentences ("Why").

Tiers decide what counts: ``OUTWARD`` reaches a person or a surface the
owner reads (a message, a push, a note, a page, typing on a site),
``COMMITMENT`` binds his future (an event, a goal, an idea, a stand-down),
``INTERNAL`` is his own bookkeeping (memory, documents, ``wake_me``, the
wishlist, his surfaces). Only a successful outward or commitment action
makes an entry; internal ones ride along as one "Also:" line.

A wake allocates its entry's id before the turn (``begin_entry``, a
task-local), so a push or note it sends opens the entry rather than the
mind's raw thread; ``finish_entry`` makes sure that link resolves. A reply
inside an entry reads it (``entry_turn_context``, used by both chat loops).

Everything here is pure except the pending entry (``begin_entry`` …
``note_linked``), ``record_entry``, ``write_fallback_entry``,
``finish_entry``, ``attach_to_ledger`` and the readers the API and Today
use; the I/O never raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services.owner import owner_name, owner_text

logger = logging.getLogger(__name__)

ENTRY_KIND = "mind_action"
VERDICTS = ("right_call", "wrong_call")

# ---- tiers ------------------------------------------------------------------

MESSAGE_SEND_TOOLS = frozenset({"send_imessage", "send_message", "send_sms", "send_whatsapp"})
OUTWARD = frozenset(MESSAGE_SEND_TOOLS | {
    "send_push_notification", "leave_note", "create_hosted_page", "update_hosted_page", "browser_type",
})
# WhatsApp MCP sends (``whatsapp_send_message``, ``_send_file``, ``_send_audio_message``).
OUTWARD_PREFIXES = ("whatsapp_send",)
# apple-mcp multiplexes reads and sends through one tool per app.
OUTWARD_OPERATIONS: Dict[str, frozenset] = {
    "mail": frozenset({"send"}),
    "messages": frozenset({"send", "schedule"}),
}
COMMITMENT = frozenset({
    "schedule_event", "cancel_scheduled_event", "set_goal", "update_goal", "suggest_idea", "stand_down",
    # Code that runs on the host Mac (2026-09-27): a Codex
    # session with write access to files, and an MCP server that restarts
    # on every boot. Neither used to leave an entry.
    "spawn_cc_worker", "add_mcp_server",
})
SURFACE_TOOLS = frozenset({
    "update_widget", "update_widget_code", "clear_widget_code",
    "show_on_screen", "clear_screen", "compose_screen", "say_on_screen", "reset_screen_layout",
})
INTERNAL = frozenset({
    "remember", "remember_update", "remember_forget", "update_memory_confidence",
    # Retiring one of his own intentions with its outcome (2026-09-27):
    # bookkeeping like remember_update, never an entry alone.
    "close_intention",
    "save_document", "edit_document", "wake_me", "wish_for_feature", "update_wish",
}) | SURFACE_TOOLS

# The pulse has its own thread and its own decision rows; it arranges
# surfaces, it does not choose to act on people.
NEVER_JOURNAL_SOURCES = frozenset({"pulse"})

# Code that runs on the host Mac, from a turn that is not a wake
# (2026-09-27). Only ``mind_service.wake`` writes entries, and no wake
# profile binds these tools; the turns that can start Codex or install an
# MCP server on their own — a fired scheduled event, a heartbeat reply, a
# text to Edward's number, a worker — get the full registry, and in the
# default shadow mode one started Codex with write access and left no
# "Edward acted" anywhere. Such a turn now writes an entry of its own
# (``journal_unattended_turn``), with a ledger row of kind ``acted`` that
# Today lists and the owner can judge. A wake writes its own; a turn the
# owner is part of (chat, voice, the widget, Alexa —
# taint_service.OWNER_PRESENT_SOURCES) is theirs to see.
UNATTENDED_JOURNAL_TOOLS = frozenset({"spawn_cc_worker", "add_mcp_server"})
WAKE_SOURCES = frozenset({"mind"})
OWNER_PRESENT_SOURCES = frozenset({"chat", "voice", "widget", "alexa"})
ACTED_KIND = "acted"

# ---- how each tool says it worked ----------------------------------------------
#
# A send is only "sent" when the tool said so: ``send_imessage`` answers
# "Failed to send…", "'Parker' matches multiple contacts…", "Already sent…"
# or "Send-review rejected…" without any "Tool error:" prefix, and the
# journal must never write "Sent an iMessage to Parker" over one of those.
# The tools named here are read by their own success replies (pinned in
# tests/test_thought_journal.py); any other journal tool (the MCP sends)
# by a generic failure pattern.

# The taint gate's answer to a held call (``approvals_service``, the turn
# ledger and the mind's prompt render the same words from the same text).
HELD_PREFIX = owner_text("Waiting for {OWNER}'s approval")
_DELIVERED = ("iMessage sent", "SMS sent", "WhatsApp sent", "Sent via iMessage", "Sent via Gmail")
SUCCESS_PREFIXES: Dict[str, Tuple[str, ...]] = {
    "send_imessage": _DELIVERED,
    "send_message": _DELIVERED,
    "send_sms": _DELIVERED,
    "send_whatsapp": _DELIVERED,
    "send_push_notification": ("Push notification sent", "Interrupt started"),
    "leave_note": ("ok",),
    "wake_me": ("ok",),
    "set_goal": ("ok",),
    "update_goal": ("ok",),
    "suggest_idea": ("ok",),
    "remember": ("ok",),
    "remember_update": ("ok",),
    "remember_forget": ("ok",),
    "close_intention": ("ok",),
    "update_memory_confidence": ("ok",),
    "schedule_event": ("Event scheduled",),
    "cancel_scheduled_event": ("Event ",),
    "stand_down": ("Stood down",),
    "save_document": ("Document saved",),
    "edit_document": ("Document updated",),
    "create_hosted_page": ("Page published",),
    "update_hosted_page": ("Page updated",),
    "browser_type": ("Typed ",),
    "wish_for_feature": ("Noted on the wishlist", "Already on the list"),
    "update_wish": ("Updated:",),
    "update_widget": ("Widget updated",),
    "update_widget_code": ("Widget code updated",),
    "clear_widget_code": ("Custom widget code cleared",),
    "show_on_screen": ("Showing ",),
    # "Screen cleared on this Mac; …" (the Mac's own screen) or "Screen
    # cleared." (the household hub).
    "clear_screen": ("Screen cleared",),
    "compose_screen": ("Your screen now",),
    "say_on_screen": ("Said",),
    "reset_screen_layout": ("Screen reset",),
    # ``graph.tools.codex_session_reply``; a refusal is "Tool error:", a
    # failed session "Codex session failed …".
    "spawn_cc_worker": ("Codex session completed", "Codex session started"),
    # The server's row as JSON (``custom_mcp_tools.add_mcp_server``); a
    # refusal is ``{"error": …}``. Installed counts even when it did not
    # start: the row is enabled and retried on every boot.
    "add_mcp_server": ('{"id": ',),
}
_ALWAYS_FAILED = ("Tool error:", "Unknown tool:", "BLOCKED:", "[repeat call")
_GENERIC_FAILURE = re.compile(
    r"^\s*(tool error|unknown tool|blocked|failed|failure|error|could not|couldn't|cannot|can't|unable|"
    r"invalid|no such|not available|not found|already)\b", re.IGNORECASE)
_SUCCESS_FALSE = re.compile(r"""["']?success["']?\s*:\s*false""", re.IGNORECASE)

# ---- bounds ---------------------------------------------------------------------

TITLE_MAX = 64
MAX_NOTICED_CHARS = 600
MAX_WHY_CHARS = 1200
MAX_DID_LINES = 12
MAX_ALSO_LINES = 8
MAX_ALSO_CHARS = 600
MAX_NARRATIVE_CHARS = 3800  # create_thought_conversation keeps 4,000
MAX_PACK_EXCERPT_CHARS = 8000
MAX_CONTEXT_PACK_CHARS = 15000  # create_thought_conversation cuts the JSON at 16,000
LEDGER_DETAIL_MAX_CHARS = 600
JOURNAL_TIMEOUT_SECONDS = 10

REASON_LABELS = {
    "manual": "A look on request",
    "morning": "Morning look",
    "evening": "Evening look",
    "self": "A check-in I set",
    "messages": "New messages",
    "mention": "A mention",
    "reflect": "Sunday reflection",
    "reflect_lite": "Nightly reflection",
    "nightly": "Nightly reflection",
    "speak": "Something I noticed",
    "research": "Research",
    "prep": "Meeting prep",
    "free": "Free time",
    "surfaces": "Surfaces",
    # Turns that are not wakes (``journal_unattended_turn``).
    "scheduled_event": "A scheduled event",
    "heartbeat": "A message that came in",
    "twilio": "A text to my number",
    "worker": "A worker's task",
    "briefing": "A briefing",
    "background": "Background work",
}


# ================================================================== pure ====

def _operation(args: Any) -> str:
    return str((args or {}).get("operation") or "").strip().lower() if isinstance(args, dict) else ""


def _toolset_call(name: Any, args: Any) -> bool:
    """A call to one of the toolset's 14 tools (2026-10-11) — not apple-mcp's
    old ``messages`` (``operation``), which shares a name."""
    from services.toolset.names import TOOLSET_NAMES

    n = str(name or "")
    return n in TOOLSET_NAMES and not (n == "messages" and _operation(args) and not
                                       (isinstance(args, dict) and args.get("action")))


def action_tier(name: Any, args: Any = None) -> Optional[str]:
    """``outward`` | ``commitment`` | ``internal`` | ``None`` (not an action).
    The toolset's tiers are by ``(tool, action)`` (``toolset.names``; a
    surface is internal here, as the old surface tools were)."""
    n = str(name or "")
    if _toolset_call(n, args):
        from services.toolset.names import tier

        t = tier(n, args)
        return "internal" if t == "surface" else t
    from services.toolset.names import is_custom_mcp

    if is_custom_mcp(n):
        return "outward"  # a custom MCP tool: nothing can tell what it does
    if n in OUTWARD or n.startswith(OUTWARD_PREFIXES):
        return "outward"
    ops = OUTWARD_OPERATIONS.get(n)
    if ops is not None:
        return "outward" if _operation(args) in ops else None
    if n in COMMITMENT:
        return "commitment"
    if n in INTERNAL:
        return "internal"
    return None


def is_journal_tool(name: Any, args: Any = None) -> bool:
    return action_tier(name, args) is not None


def action_ok(name: Any, result: Any) -> Tuple[bool, bool]:
    """(worked?, held for the owner's approval?) from the tool's own reply. A held
    send did not fail and did not deliver: ``(True, True)``."""
    text = str(result if result is not None else "").strip()
    if text.startswith(HELD_PREFIX):
        return True, True
    if text.startswith(_ALWAYS_FAILED):
        return False, False
    prefixes = SUCCESS_PREFIXES.get(str(name or ""))
    if prefixes is not None:
        return text.startswith(prefixes), False
    if _GENERIC_FAILURE.match(text) or _SUCCESS_FALSE.search(text[:400]):
        return False, False
    return True, False


def action_facts(name: Any, result: Any) -> Dict[str, Any]:
    """What the journal needs from the *whole* reply, kept beside the cut
    one (``TurnRecord.note_action`` keeps 300 characters). Pure.

    ``add_mcp_server``: ``started`` — its JSON puts id, name, description,
    package_name, runtime and tool_prefix before ``status``, which falls
    past the cut once the description runs to ~90 characters, and the
    entry then read as a clean install (review 2026-09-27)."""
    if str(name or "") != "add_mcp_server":
        return {}
    try:
        parsed = json.loads(str(result or ""))
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict) or "status" not in parsed:
        return {}
    return {"started": parsed.get("status") != "error"}


def _acts(actions: Any) -> List[Dict[str, Any]]:
    return [a for a in (actions or []) if isinstance(a, dict)]


def _tier(a: Dict[str, Any]) -> Optional[str]:
    return action_tier(a.get("name"), a.get("args"))


def should_journal(actions: Any, turn_source: Optional[str]) -> bool:
    """A wake earns an entry when it did something outward or committed to
    something, and it was not the surfaces pulse."""
    if (turn_source or "") in NEVER_JOURNAL_SOURCES:
        return False
    return any(a.get("ok") and _tier(a) in ("outward", "commitment") for a in _acts(actions))


def unattended_code_actions(actions: Any) -> List[Dict[str, Any]]:
    """The code a turn really ran: a Codex session, an MCP server, or a
    shell command that installs something or leaves a job running
    — that worked, not held for the owner, not refused."""
    from services.toolset.names import is_unattended_code

    return [a for a in _acts(actions)
            if (a.get("name") in UNATTENDED_JOURNAL_TOOLS or is_unattended_code(a.get("name"), a.get("args")))
            and a.get("ok") and not a.get("held")]


def should_journal_unattended(actions: Any, turn_source: Optional[str]) -> bool:
    """A turn that is not a wake, and that the owner is not part of, earns
    an entry when it ran code on the host Mac on its own. Pure."""
    source = turn_source or ""
    if source in NEVER_JOURNAL_SOURCES or source in WAKE_SOURCES or source in OWNER_PRESENT_SOURCES:
        return False
    return bool(unattended_code_actions(actions))


def _reaches_people(a: Dict[str, Any]) -> bool:
    name = str(a.get("name") or "")
    if _toolset_call(name, a.get("args")):
        from services.toolset.names import reaches_people

        return reaches_people(name, a.get("args"))
    if name in MESSAGE_SEND_TOOLS or name == "send_push_notification" or name.startswith(OUTWARD_PREFIXES):
        return True
    ops = OUTWARD_OPERATIONS.get(name)
    return ops is not None and _operation(a.get("args")) in ops


def reached_someone(actions: Any) -> bool:
    """Did a message reach a person, or a push reach the owner? Held sends did not."""
    return any(a.get("ok") and not a.get("held") and _reaches_people(a) for a in _acts(actions))


# ---- words ----------------------------------------------------------------------

def _s(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _clip(text: Any, limit: int) -> str:
    """One line, at most ``limit`` characters, cut on a word boundary."""
    text = _s(text)
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.—-") + "…"


def _unsigned(text: Any) -> str:
    """A message without Edward's signature ("- Edward 🤖"); every send
    tool signs, so the quote would otherwise end with it every time."""
    raw = str(text or "").rstrip()
    try:
        from utils.message_signature import _SIGNATURE_PATTERNS

        for pattern in _SIGNATURE_PATTERNS:
            raw = re.sub(pattern, "", raw, flags=re.IGNORECASE).rstrip()
    except Exception:  # noqa: BLE001
        pass
    return raw


def _quote(text: Any, limit: int = 140) -> str:
    return f"“{_clip(_unsigned(text), limit)}”"


def _lower_first(text: str) -> str:
    if not text or text[:2].isupper():
        return text
    return text[0].lower() + text[1:]


def _error_head(result: Any) -> str:
    text = str(result or "").strip()
    first = next((line for line in text.splitlines() if line.strip()), "")
    if first.startswith("Tool error:"):
        first = first[len("Tool error:"):]
    return _clip(first, 100) or "no reply"


def _clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def _local_when(value: Any) -> Optional[str]:
    """"Fri 9:00am" in the user's zone. A naive time is already the user's
    (every tool that takes a time says so); an aware one is converted."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        try:
            from services.timezone_utils import utc_to_local

            dt = utc_to_local(dt)
        except Exception:  # noqa: BLE001
            pass
    return f"{dt.strftime('%a')} {_clock(dt)}"


def _chat_label(chat_id: Any, names: Mapping[str, str]) -> Optional[str]:
    """A chat's human name from the pack, by id or by the tail of an
    AppleScript GUID ("iMessage;+;chat123" → "chat123")."""
    cid = _s(chat_id)
    if not cid:
        return None
    for key in (cid, cid.split(";")[-1]):
        if key in names and names[key]:
            return str(names[key])
    return None


def _looks_like_chat_id(text: str) -> bool:
    return ";" in text or bool(re.match(r"^chat\d", text))


_HANDLE_RE = re.compile(r"^(\+?\d[\d\s().-]{6,}|[^@\s]+@[^@\s]+)$")


def _is_one_to_one(chat_id: str) -> bool:
    """A person's own chat, not a group: an AppleScript 1:1 GUID
    ("iMessage;-;+1555…") or a bare handle (a phone number or an email),
    where a group is "iMessage;+;chat…" / "chat…". Pure."""
    cid = _s(chat_id)
    if ";-;" in cid:
        return True
    if ";+;" in cid:
        return False
    tail = cid.split(";")[-1]
    return bool(_HANDLE_RE.match(tail)) and not re.match(r"^chat\d", tail)


def _imessage_who(args: Dict[str, Any], names: Mapping[str, str]) -> Tuple[str, Optional[str], Optional[str]]:
    """(who, contact, chat name) for an iMessage send. A person's own chat
    has no separate chat name: "Replied to Parker", never "… in Parker"
    (review 2026-09-26)."""
    contact = _s(args.get("contact"))
    chat_id = _s(args.get("chat_id"))
    if contact and _looks_like_chat_id(contact):
        contact = ""
    if chat_id:
        chat = _chat_label(chat_id, names)
        if _is_one_to_one(chat_id) or (chat and contact and contact.lower() == chat.lower()):
            who = chat or names.get(contact) or contact or chat_id.split(";")[-1]
            return who, who, None
        if chat and contact and contact.lower() != chat.lower():
            return f"{contact} ({chat})", contact, chat
        if chat:
            return chat, None, chat
        if contact:
            return f"{contact} (group chat)", contact, None
        return "a group chat", None, None
    who = names.get(contact) or contact or "someone"
    return who, who, None


def _send_noun(result: str, channel: Any = None) -> str:
    head = (result or "")[:60]
    ch = _s(channel).lower()
    if "iMessage" in head or ch == "imessage":
        return "an iMessage"
    if head.startswith("WhatsApp") or ch == "whatsapp":
        return "a WhatsApp"
    if head.startswith("SMS") or ch in ("twilio", "sms"):
        return "a text"
    return "a message"


def _screen_name(screen: Any) -> str:
    s = _s(screen).lower()
    mini = {"mini", "mac", "mac mini", "desk", "monitor", "my screen", "your screen", "office"}
    return "Mac screen" if s in mini else "household screen"


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _goal_from_result(result: str) -> Tuple[Optional[str], Optional[str]]:
    m = re.match(r"^ok (.+?) \[(\w+)\]", result or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def _as_legacy(a: Dict[str, Any]) -> Dict[str, Any]:
    """A toolset action told in the old tools' words (``names.legacy_view``)."""
    if not _toolset_call(a.get("name"), a.get("args")):
        return a
    from services.toolset.names import legacy_view

    name, args = legacy_view(a.get("name"), a.get("args"))
    return {**a, "name": name, "args": args}


def _base_line(a: Dict[str, Any], names: Mapping[str, str]) -> str:
    if str(a.get("name") or "") == "bash":
        cmd = (a.get("args") or {}).get("command") if isinstance(a.get("args"), dict) else None
        return f"Ran on the Mac: {_clip(cmd, 140) or 'a command'}"
    a = _as_legacy(a)
    name = str(a.get("name") or "?")
    args = a.get("args") if isinstance(a.get("args"), dict) else {}
    result = str(a.get("result") or "")
    msg = args.get("message")
    said = f": {_quote(msg)}" if _s(msg) else ""

    if name == "send_imessage":
        who, _, _ = _imessage_who(args, names)
        return f"Sent an iMessage to {who}{said}"
    if name == "send_message":
        who = _s(args.get("recipient")) or "someone"
        return f"Sent {_send_noun(result, args.get('channel'))} to {names.get(who, who)}{said}"
    if name == "send_sms":
        return f"Sent a text to {_s(args.get('phone_number')) or 'someone'}{said}"
    if name == "send_whatsapp":
        return f"Sent a WhatsApp to {_s(args.get('phone_number')) or 'someone'}{said}"
    if name.startswith(OUTWARD_PREFIXES):
        who = _s(args.get("recipient") or args.get("chat_jid") or args.get("phone_number")) or "someone"
        if name.endswith("_file"):
            return f"Sent a file on WhatsApp to {who}"
        if name.endswith("_audio_message"):
            return f"Sent a voice note on WhatsApp to {who}"
        return f"Sent a WhatsApp to {who}{said}"
    if name == "mail":
        subject = args.get("subject")
        return f"Emailed {_s(args.get('to')) or 'someone'}" + (f": {_quote(subject, 100)}" if _s(subject) else "")
    if name == "messages":
        who = _s(args.get("phoneNumber") or args.get("recipient")) or "someone"
        verb = "Scheduled an iMessage to" if _operation(args) == "schedule" else "Sent an iMessage to"
        return f"{verb} {who}{said}"
    if name == "send_push_notification":
        urgent = _s(args.get("urgency")).lower() == "high"
        line = f"Pushed you{', urgently' if urgent else ''}: {_quote(args.get('title'), 100)}"
        body = _clip(args.get("body"), 120)
        return f"{line} — {body}" if body else line
    if name == "leave_note":
        needs = " (needs you)" if args.get("needs_you") in (True, "true", "True", 1) else ""
        return f"Left a note on Today: {_quote(args.get('title'), 100)}{needs}"
    if name == "create_hosted_page":
        m = re.search(r"URL:\s*(\S+)", result)
        if m:
            return f"Published a page: {m.group(1)}"
        slug = _s(args.get("slug"))
        return f"Published a page “{slug}”" if slug else "Published a page"
    if name == "update_hosted_page":
        return f"Updated the page “{_s(args.get('slug'))}”"
    if name == "browser_type":
        return f"Typed {_quote(args.get('text'), 100)} in Chrome"

    if name == "schedule_event":
        desc = _quote(args.get("description"), 100)
        when = _local_when(args.get("scheduled_at"))
        repeat = _s(args.get("recurrence_pattern"))
        if repeat:
            return f"Scheduled {desc} (repeats {repeat})" + (f", first {when}" if when else "")
        return f"Scheduled {desc}" + (f" for {when}" if when else "")
    if name == "cancel_scheduled_event":
        return f"Cancelled a scheduled event ({_s(args.get('event_id')) or '?'})"
    if name == "set_goal":
        return f"Set a goal: {_clip(args.get('title'), 100)}"
    if name == "update_goal":
        title, _ = _goal_from_result(result)
        label = f"“{title}”" if title else "a goal"
        if _s(args.get("progress")):
            return f"Logged progress on {label}: {_clip(args.get('progress'), 120)}"
        if _s(args.get("status")):
            return f"Marked {label} {_s(args.get('status'))}"
        if _s(args.get("plan")):
            return f"Changed the plan for {label}"
        if _s(args.get("check_in")):
            return f"Changed the check-ins for {label} to {_s(args.get('check_in'))}"
        return f"Updated {label}"
    if name == "suggest_idea":
        return f"Suggested an idea: {_clip(args.get('title'), 100)}"
    if name == "stand_down":
        chat = _chat_label(args.get("chat_id"), names) or _s(args.get("chat_id")) or "a chat"
        asked = _s(args.get("requested_by"))
        return f"Stood down in {chat}" + (f" ({asked} asked)" if asked else "")
    if name == "spawn_cc_worker":
        kind = "a read-only Codex session" if args.get("read_only") in (True, "true", "True", 1) \
            else "a Codex session"
        return f"Started {kind}: {_clip(args.get('task'), 140)}"
    if name == "add_mcp_server":
        package = _s(args.get("package_name"))
        line = f"Installed the MCP server {_s(args.get('name')) or '?'}" + (f" ({package})" if package else "")
        started = a.get("started")
        if started is None:  # an action recorded without the fact: read what is left of the reply
            started = '"status": "error"' not in result
        return line + ("" if started else " — it did not start")

    if name == "remember":
        return f"Remembered: {_clip(args.get('content'), 140)}"
    if name == "remember_update":
        # 2026-09-27: valid_until may come alone,
        # and the reply says which way it went ("ok (retired as of …)" /
        # "ok (valid until …)"). With no new text this line used to stop at
        # "Updated a memory: ".
        content = _clip(args.get("new_content"), 140)
        mid = _s(args.get("memory_id")) or "?"
        if "retired as of" in result:
            return f"Retired a memory: {content}" if content else f"Retired a memory ({mid})"
        if content:
            return f"Updated a memory: {content}"
        horizon = args.get("valid_until")
        if _s(horizon):
            return f"Set a memory to lapse {_local_when(horizon) or _clip(horizon, 40)} ({mid})"
        return f"Updated a memory ({mid})"
    if name == "remember_forget":
        return f"Forgot a memory ({_s(args.get('memory_id')) or '?'})"
    if name == "close_intention":
        outcome = _clip(args.get("outcome"), 140)
        return f"Closed an intention: {outcome}" if outcome else \
            f"Closed an intention ({_s(args.get('memory_id')) or '?'})"
    if name == "update_memory_confidence":
        why = _clip(args.get("reason"), 100)
        return f"Changed my confidence in a memory to {_s(args.get('new_confidence')) or '?'}" + (f": {why}" if why else "")
    if name == "save_document":
        return f"Saved a document: {_quote(args.get('title'), 100)}"
    if name == "edit_document":
        title = _s(args.get("title"))
        if not title:
            m = re.match(r'^Document updated: "(.+?)"', result)
            title = m.group(1) if m else ""
        return f"Edited a document: “{_clip(title, 100)}”" if title else \
            f"Edited a document ({_s(args.get('document_id')) or '?'})"
    if name == "wake_me":
        when = _local_when(args.get("when")) or _clip(args.get("when"), 40) or "later"
        return f"Will look again {when}: {_clip(args.get('why'), 140)}"
    if name == "wish_for_feature":
        return f"Wished for: {_clip(args.get('title'), 100)}"
    if name == "update_wish":
        status = _s(args.get("status"))
        return f"Updated a wish ({status})" if status else "Updated a wish"
    if name == "update_widget":
        title = _s(args.get("title"))
        return f"Changed the widget: “{_clip(title, 80)}”" if title else "Changed the widget"
    if name == "update_widget_code":
        return "Rewrote the widget's code"
    if name == "clear_widget_code":
        return "Cleared the widget's custom code"
    if name == "show_on_screen":
        kind = _s(args.get("kind")) or "something"
        title = _s(args.get("title"))
        return f"Put {_article(kind)} {kind} on the {_screen_name(args.get('screen'))}" + \
            (f": “{_clip(title, 80)}”" if title else "")
    if name == "clear_screen":
        return f"Cleared the {_screen_name(args.get('screen'))}"
    if name == "compose_screen":
        return "Set the scene on the Mac screen"
    if name == "say_on_screen":
        return "Said something on the Mac screen"
    if name == "reset_screen_layout":
        return "Reset the Mac screen to its stock scene"
    if name == "schedule":
        line = _calendar_line(args, result)
        if line:
            return line
    return f"Used {name}"


def _calendar_when(value: Any) -> Optional[str]:
    """A date alone ("2026-10-12") is a day, "Mon Oct 12"; a time is
    ``_local_when``'s "Mon 9:00am"."""
    s = _s(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.fromisoformat(s).strftime("%a %b %-d")
        except ValueError:
            return None
    return _local_when(s) if s else None


def _calendar_line(args: Dict[str, Any], result: str) -> Optional[str]:
    """``schedule``'s writes to the owner's calendar and Reminders
    (2026-10-11), told to the owner ("your calendar"); the title of a change or a delete comes
    from the reply, which re-read it."""
    act = _s(args.get("action")).lower()
    what = _quote(args.get("what"), 100) if _s(args.get("what")) else ""
    titled = re.match(r"^(?:Moved|Changed|Deleted|Checked off) (“.+?”)", result or "")
    title = titled.group(1) if titled else ""
    later = " and its later repeats" if "every repeat after it" in (result or "") else ""
    if act == "event_add":
        when = _calendar_when(args.get("start"))
        return f"Added {what or 'an event'} to your calendar" + (f" for {when}" if when else "")
    if act == "event_update":
        when = _calendar_when(args.get("start"))
        return f"Changed {title or what or 'an event'}{later} on your calendar" + (f" (now {when})" if when else "")
    if act == "event_delete":
        return f"Deleted an event{later} from your calendar" + (f": {title}" if title else "")
    if act == "reminder_add":
        when = _calendar_when(args.get("when"))
        return f"Added a reminder: {_clip(args.get('what'), 120) or '?'}" + (f" (due {when})" if when else "")
    if act == "reminder_complete":
        return "Checked off a reminder" + (f": {title}" if title else "")
    if act == "reminder_delete":
        return "Deleted a reminder"
    return None


def describe_action(a: Any, names: Optional[Mapping[str, str]] = None) -> str:
    """One plain line for one action, from its record: what it was, to whom,
    what it said — plus "— failed: …" or "— held for approval". Pure; never
    raises."""
    if not isinstance(a, dict):
        return "Did something I could not describe"
    try:
        line = _base_line(a, names or {})
    except Exception:  # noqa: BLE001
        line = f"Used {a.get('name') or 'a tool'}"
    if not a.get("ok", True):
        return f"{line} — failed: {_error_head(a.get('result'))}"
    if a.get("held"):
        return f"{line} — held for approval"
    return line


def headline(a: Any, names: Optional[Mapping[str, str]] = None, reason: Optional[str] = None) -> str:
    """The entry's title: the action as a short headline, at most 64
    characters ("Replied to Parker in Family", "Pushed you: Biscuit's dose")."""
    if not isinstance(a, dict):
        return "Acted on my own"
    names = names or {}
    try:
        title = _headline(a, names, reason)
    except Exception:  # noqa: BLE001
        title = describe_action(a, names)
    if a.get("held"):
        title = f"{title} (held)"
    return _clip(title, TITLE_MAX)


def _headline(a: Dict[str, Any], names: Mapping[str, str], reason: Optional[str]) -> str:
    if str(a.get("name") or "") != "bash":
        a = _as_legacy(a)
    name = str(a.get("name") or "")
    args = a.get("args") if isinstance(a.get("args"), dict) else {}
    replied = reason in ("messages", "mention")
    verb = "Replied to" if replied else "Messaged"
    if name == "send_imessage":
        _, contact, chat = _imessage_who(args, names)
        if contact and chat:
            return f"{verb} {contact} in {chat}"
        if chat:
            return f"{'Replied in' if replied else 'Messaged'} {chat}"
        return f"{verb} {contact or 'someone'}"
    if name == "send_message":
        who = _s(args.get("recipient")) or "someone"
        return f"{verb} {names.get(who, who)}"
    if name in ("send_sms", "send_whatsapp"):
        return f"{verb} {_s(args.get('phone_number')) or 'someone'}"
    if name.startswith(OUTWARD_PREFIXES):
        return f"{verb} {_s(args.get('recipient') or args.get('chat_jid') or args.get('phone_number')) or 'someone'} on WhatsApp"
    if name == "messages":
        return f"{verb} {_s(args.get('phoneNumber') or args.get('recipient')) or 'someone'}"
    if name == "mail":
        return f"Emailed {_s(args.get('to')) or 'someone'}"
    if name == "send_push_notification":
        return f"Pushed you: {_s(args.get('title')) or 'a heads-up'}"
    if name == "leave_note":
        return f"Left a note: {_s(args.get('title')) or 'for Today'}"
    if name == "create_hosted_page":
        return "Published a page"
    if name == "update_hosted_page":
        return "Updated a page"
    if name == "browser_type":
        return "Typed in Chrome"
    if name == "schedule_event":
        return f"Scheduled: {_s(args.get('description')) or 'something'}"
    if name == "cancel_scheduled_event":
        return "Cancelled a scheduled event"
    if name == "set_goal":
        return f"Set a goal: {_s(args.get('title'))}"
    if name == "update_goal":
        title, _ = _goal_from_result(str(a.get("result") or ""))
        return f"Updated a goal: {title}" if title else "Updated a goal"
    if name == "suggest_idea":
        return f"Suggested: {_s(args.get('title'))}"
    if name == "stand_down":
        return f"Stood down in {_chat_label(args.get('chat_id'), names) or _s(args.get('chat_id')) or 'a chat'}"
    return _base_line(a, names)


def reason_label(reason: Optional[str]) -> str:
    return REASON_LABELS.get(reason or "", (reason or "A wake").replace("_", " ").capitalize())


_HEADING = re.compile(r"^\s*\*\*[^*\n]+\*\*\s*(?:\n+|$)")


def reasoning_summary(texts: Any, limit: int = MAX_NOTICED_CHARS) -> str:
    """His reasoning summaries as one short paragraph: the bold step
    headings Codex puts on each summary dropped, repeats dropped, cut on a
    word boundary."""
    seen = set()
    parts: List[str] = []
    for raw in texts or []:
        text = str(raw or "")
        while True:
            stripped = _HEADING.sub("", text, count=1)
            if stripped == text:
                break
            text = stripped
        text = _s(text)
        if text and text not in seen:
            seen.add(text)
            parts.append(text)
    return _clip(" ".join(parts), limit)


def _also_line(internal: List[Dict[str, Any]], names: Mapping[str, str]) -> str:
    lines = [_lower_first(describe_action(a, names)) for a in internal[:MAX_ALSO_LINES]]
    more = len(internal) - MAX_ALSO_LINES
    text = "; ".join(lines) + (f"; and {more} more" if more > 0 else "")
    return text if len(text) <= MAX_ALSO_CHARS else text[: MAX_ALSO_CHARS - 1].rstrip() + "…"


def compose_entry(*, reason: Optional[str], woke_for: Optional[str], reasoning: Any, closing_note: Optional[str],
                  actions: Any, when: Optional[datetime] = None,
                  names: Optional[Mapping[str, str]] = None) -> Tuple[str, str]:
    """(title, narrative) for one wake that acted. Pure.

    The narrative is plain Markdown, one paragraph per heading::

        **Why I looked** — <reason label>: <woke_for>
        **What I noticed** — <his reasoning summary>        (when there is one)
        **What I did**
        - <one line per outward or commitment action, in order>
        - Also: <his internal work, one line>              (when there is any)
        **Why** — <his closing sentences>                    (when he wrote any)

    ``when`` is when he acted; the lines carry their own times.
    """
    names = names or {}
    acts = [a for a in _acts(actions) if _tier(a)]
    lead = [a for a in acts if _tier(a) in ("outward", "commitment")]
    internal = [a for a in acts if _tier(a) == "internal"]
    first = next((a for a in lead if a.get("ok")), lead[0] if lead else None)
    title = headline(first, names, reason) if first is not None else "Acted on my own"

    why_looked = f"**Why I looked** — {reason_label(reason)}"
    if _s(woke_for):
        why_looked += f": {_s(woke_for)}"
    sections = [why_looked]
    noticed = reasoning_summary(reasoning)
    if noticed:
        sections.append(f"**What I noticed** — {noticed}")
    did = ["**What I did**"]
    for a in lead[:MAX_DID_LINES]:
        did.append(f"- {describe_action(a, names)}")
    if len(lead) > MAX_DID_LINES:
        did.append(f"- …and {len(lead) - MAX_DID_LINES} more")
    if internal:
        did.append(f"- Also: {_also_line(internal, names)}")
    if len(did) > 1:
        sections.append("\n".join(did))
    closing = _clip(closing_note, MAX_WHY_CHARS)
    if closing:
        sections.append(f"**Why** — {closing}")
    narrative = "\n\n".join(sections)
    if len(narrative) > MAX_NARRATIVE_CHARS:
        narrative = narrative[: MAX_NARRATIVE_CHARS - 1].rstrip() + "…"
    return title, narrative


# ---- why I looked, from the pack --------------------------------------------------

_PREP = re.compile(r'Meeting to prepare: "(.+?)" at (\S+) \(in (\d+) min\)')


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+", _s(text), maxsplit=1)[0]


def _and(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) > 3:
        return ", ".join(names[:3]) + " and others"
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _mention_line(e: Dict[str, Any]) -> str:
    sender = _s(e.get("sender")) or "someone"
    chat = _s(e.get("chat"))
    return f"{sender} mentioned me" + (f" in {chat}" if chat and chat != sender else "")


def _mention(escalations: List[Dict[str, Any]]) -> Optional[str]:
    for e in reversed(escalations):
        if "@edward" in str(e.get("text") or "").lower():
            return _mention_line(e)
    return None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def woke_for_line(pack: Any) -> str:
    """A short, factual line on why this wake happened, from the pack:
    who is waiting, the check-in he set, the meeting he is preparing, the
    day's count. Pure; empty when the pack says nothing specific."""
    if pack is None:
        return ""
    reason = getattr(pack, "reason", "") or ""
    extra = _s(getattr(pack, "extra", None))
    if reason == "self":
        return _clip(extra, 200)
    if reason == "prep":
        m = _PREP.search(extra)
        if m:
            return f"“{m.group(1)}” at {m.group(2)} starts in {m.group(3)} min"
        return _clip(_first_sentence(extra), 160)

    parts: List[str] = []
    if extra and reason not in ("mention", "messages"):
        parts.append(_clip(_first_sentence(extra), 160))
    esc = [e for e in (getattr(pack, "escalations", None) or []) if isinstance(e, dict)]
    if reason == "mention" and esc:
        parts.append(_mention(esc) or _mention_line(esc[-1]))
        if len(esc) > 1:
            parts.append(f"{_plural(len(esc) - 1, 'more message')} waiting on me")
    elif esc:
        senders: List[str] = []
        for e in esc:
            who = _s(e.get("sender")) or "someone"
            if who not in senders:
                senders.append(who)
        parts.append(f"{_plural(len(esc), 'message')} waiting on me, from {_and(senders)}")
        mention = _mention(esc)
        if mention:
            parts.append(mention)
    # Only a look that is about the day counts what arrived since the last
    # one: free time is his own choice and a prep wake is about one meeting.
    if not esc and reason not in ("mention", "messages", "reflect", "reflect_lite", "research", "free", "prep",
                                  "nightly", "speak"):
        messages = getattr(pack, "messages", None) or []
        last = getattr(pack, "last_wake_at", None)
        if last is not None:
            try:
                from services.mind_service import new_messages_since

                n = new_messages_since(messages, last)
            except Exception:  # noqa: BLE001
                n = 0
            if n:
                parts.append(f"{_plural(n, 'new message')} since I last looked")
        elif messages:
            parts.append(f"{_plural(len(messages), 'recent message')}")
    calendar = getattr(pack, "calendar", None) or []
    if reason in ("morning", "evening") and calendar:
        parts.append(f"{_plural(len(calendar), 'thing')} on the calendar today")
    return "; ".join(p for p in parts if p)


_TRIGGER_TAG = re.compile(r"^\[[A-Z][A-Z_ ]*\]\s*")
_OPEN_QUESTION = re.compile(r'^"(.+)"\s*\(memory_id:')


def woke_for_message(message: Any) -> str:
    """Why a wake that brought its own trigger (no pack) happened: the
    open question a research turn was given, else the trigger's first
    line without its ``[TAG]``. Pure."""
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    for line in lines[:4]:
        m = _OPEN_QUESTION.match(line)
        if m:
            return f"a question I left open: “{_clip(m.group(1), 160)}”"
    return _clip(_TRIGGER_TAG.sub("", lines[0]), 160) if lines else ""


def chat_names(pack: Any) -> Dict[str, str]:
    """chat identifier → the chat's human name, from the pack's messages and
    escalations, so "chat123" reads "Family"."""
    names: Dict[str, str] = {}
    if pack is None:
        return names
    for m in getattr(pack, "messages", None) or []:
        if isinstance(m, dict) and m.get("chat_identifier") and m.get("chat_name"):
            names.setdefault(str(m["chat_identifier"]), str(m["chat_name"]))
    for e in getattr(pack, "escalations", None) or []:
        if not isinstance(e, dict):
            continue
        cid, chat = e.get("chat_identifier"), e.get("chat")
        if cid and chat and str(chat) != str(cid):
            names[str(cid)] = str(chat)
    return names


# ---- the entry's context pack, the ledger's detail, the API's field ----------------

def _iso_z(dt: Optional[datetime]) -> str:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def entry_context_pack(*, ledger_id: Optional[int], reason: Optional[str], wake_thread: Optional[str],
                       turn_id: Optional[str], actions: Any, acted_at: Optional[datetime],
                       pack_excerpt: Optional[str] = None,
                       names: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """What the entry's conversation carries (``conversations.context_pack``):
    enough for the API to mark it as his action and for a reply to recover
    what he was looking at. Bounded to fit the conversation's 16K column
    cut, which would otherwise leave JSON that no longer parses."""
    lead = [a for a in _acts(actions) if _tier(a) in ("outward", "commitment")]
    pack: Dict[str, Any] = {
        "kind": ENTRY_KIND,
        "ledger_id": ledger_id,
        "reason": reason,
        "wake_thread": wake_thread,
        "turn_id": turn_id,
        "outward": any(a.get("ok") and _tier(a) == "outward" for a in lead),
        # What the "reached out" chip means (2026-09-27):
        # ``outward`` is also set by a note on Today and by a send held for
        # the owner's approval, neither of which reached anyone.
        "reached": reached_someone(lead),
        "acted_at": _iso_z(acted_at),
        "actions": [describe_action(a, names) for a in lead[:MAX_DID_LINES]],
        "pack_excerpt": str(pack_excerpt or "")[:MAX_PACK_EXCERPT_CHARS],
    }
    while len(json.dumps(pack, default=str)) > MAX_CONTEXT_PACK_CHARS and pack["pack_excerpt"]:
        pack["pack_excerpt"] = pack["pack_excerpt"][: len(pack["pack_excerpt"]) // 2]
    return pack


def detail_with_thought(detail_json: Optional[str], thought_id: str, limit: int = LEDGER_DETAIL_MAX_CHARS) -> str:
    """The ledger row's detail with ``thought_id`` added, still within the
    600-character cap as JSON that parses (``bounded_detail`` would cut it
    mid-string): the tools list is shortened first, then other keys go."""
    try:
        parsed = json.loads(detail_json) if detail_json else {}
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    out: Dict[str, Any] = {"thought_id": thought_id}
    out.update({k: v for k, v in parsed.items() if k != "thought_id"})

    def dump(o: Dict[str, Any]) -> str:
        return json.dumps(o, separators=(",", ":"), default=str)

    text = dump(out)
    while len(text) > limit:
        tools = out.get("tools")
        if isinstance(tools, list) and tools:
            out["tools"] = tools[: len(tools) // 2]
        else:
            droppable = [k for k in out if k != "thought_id"]
            if not droppable:
                break
            out.pop(droppable[-1])
        text = dump(out)
    return text


# ---- a reply inside an entry ---------------------------------------------------------
#
# Why (2026-09-25): the owner reads an entry and replies "why?". Neither chat loop
# read ``initial_message`` or ``context_pack`` (only the graph's unused
# ``respond_node`` did), so Edward answered without the entry he was asked
# about. ``entry_turn_context`` is what a turn in a thought conversation
# needs: the entry to seed as the thread's first assistant message (only
# while the thread is empty — it is checkpointed with that turn, so it is
# there from then on) and a system-prompt section with what the wake saw.

MAX_ENTRY_PROMPT_CHARS = 8000
MAX_LEGACY_PACK_PROMPT_CHARS = 4000  # what respond_node gave the older thoughts


# ``create_thought_conversation`` writes the pack with json's default
# separators; the compact form counts too. ``conversation_service`` matches
# the same strings in SQL (``_visible_filter``).
ENTRY_PACK_MARKERS = (f'"kind": "{ENTRY_KIND}"', f'"kind":"{ENTRY_KIND}"')


def is_entry_pack(context_pack: Any) -> bool:
    """Does a conversation's ``context_pack`` mark a journal entry?"""
    return isinstance(context_pack, str) and any(m in context_pack for m in ENTRY_PACK_MARKERS)


def _entry_pack(context_pack: Any) -> Optional[Dict[str, Any]]:
    if not is_entry_pack(context_pack):
        return None
    try:
        pack = json.loads(context_pack)
    except (TypeError, ValueError):
        return None
    return pack if isinstance(pack, dict) and pack.get("kind") == ENTRY_KIND else None


def _msg_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content if isinstance(content, str) else str(content or "")


def _is_ai(message: Any) -> bool:
    return getattr(message, "type", None) == "ai"


def _is_human(message: Any) -> bool:
    return getattr(message, "type", None) == "human"


def entry_prompt_block(pack: Mapping[str, Any], narrative: Optional[str] = None,
                       limit: int = MAX_ENTRY_PROMPT_CHARS) -> str:
    """The system-prompt section for a turn inside a journal entry: when and
    why the wake ran, what the record says it did, the wake's thread, and
    the start of what it was looking at. ``narrative`` is the entry itself,
    given only when the thread no longer holds it. At most ``limit``
    characters; the pack excerpt gives way first. Pure."""
    reason = str(pack.get("reason") or "")
    when = _local_when(pack.get("acted_at"))
    head = [
        "\n\n## This conversation is one of your journal entries",
        f"You wrote this entry on your own after a wake — {reason_label(reason)}"
        + (f" (reason `{reason}`)" if reason else "") + (f", {when}" if when else "")
        + f" — to say what you did and why. You are answering {owner_name()}'s reply to it, usually a "
          "question about why. Answer from what you saw and did then; when the record below does not say, "
          "say that plainly rather than guess.",
    ]
    thread = _s(pack.get("wake_thread"))
    if thread:
        turn = _s(pack.get("turn_id"))
        head.append(f"The wake ran in your thread `{thread}`" + (f" (turn {turn})" if turn else "") + ".")
    actions = [str(a) for a in (pack.get("actions") or []) if _s(a)]
    if actions:
        head.append("What you did, from the record of that wake:\n" + "\n".join(f"- {a}" for a in actions))
    if _s(narrative):
        head.append("What you wrote then:\n" + str(narrative).strip())
    text = "\n".join(head)
    excerpt = str(pack.get("pack_excerpt") or "").strip()
    if excerpt:
        opener = "\nWhat you were looking at (the start of that wake's pack):\n<<<\n"
        room = limit - len(text) - len(opener) - len("\n>>>")
        if room > 200:
            if len(excerpt) > room:
                excerpt = excerpt[: room - 1].rstrip() + "…"
            text += opener + excerpt + "\n>>>"
    return text[:limit]


def _legacy_pack_block(context_pack: str) -> str:
    """What ``graph/nodes.respond_node`` gave an older thought conversation
    on its first reply: the raw signal pack, pretty-printed and cut. A pack
    that does not parse (cut mid-JSON) is left out."""
    try:
        pack_str = json.dumps(json.loads(context_pack), indent=2, default=str)[:MAX_LEGACY_PACK_PROMPT_CHARS]
    except (TypeError, ValueError):
        return ""
    return ("\n\n## What I was thinking about (signal pack from when I pinged you):\n"
            "```json\n" + pack_str + "\n```\n"
            f"Use these specifics when {owner_name()} asks about them.")


def entry_turn_context(*, initial_message: Optional[str], context_pack: Optional[str],
                       history: Sequence[Any]) -> Tuple[Optional[str], str]:
    """(opening, prompt section) for a turn in a conversation that opened
    with Edward's own words. Pure.

    ``opening`` is ``initial_message`` when the thread holds no messages
    yet: the loop puts it first in the history, so the model reads it and
    the checkpoint keeps it. The section is, for a journal entry, its
    context on every turn (an entry's thread is short, and "why?" is often
    asked twice); for an older thought with a raw pack, the pack on the
    first reply only, as ``respond_node`` did."""
    opening_text = (initial_message or "").strip()
    history = list(history or [])
    opening = opening_text if opening_text and not history else None
    held = opening is not None or (
        bool(opening_text) and any(_is_ai(m) and _msg_text(m).strip() == opening_text for m in history))
    pack = _entry_pack(context_pack)
    if pack is not None:
        return opening, entry_prompt_block(pack, narrative=None if held else opening_text or None)
    if isinstance(context_pack, str) and context_pack.strip() and not any(_is_human(m) for m in history):
        return opening, _legacy_pack_block(context_pack)
    return opening, ""


def _ledger_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def thought_info(context_pack_json: Any, verdict: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The API's ``thought`` field for a journal entry's conversation, or
    ``None`` for anything else — including a pack that does not parse or
    names no ledger row (no verdict could be given on it)."""
    if not context_pack_json or not isinstance(context_pack_json, str):
        return None
    try:
        pack = json.loads(context_pack_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(pack, dict) or pack.get("kind") != ENTRY_KIND:
        return None
    lid = _ledger_id(pack.get("ledger_id"))
    if lid is None:
        return None
    info = {
        "kind": ENTRY_KIND,
        "ledger_id": lid,
        "reason": str(pack.get("reason") or ""),
        "acted_at": str(pack.get("acted_at") or ""),
        "outward": bool(pack.get("outward")),
        "verdict": verdict if verdict in VERDICTS else None,
    }
    # ``reached``: a message delivered to a person or a push that reached
    # the owner (``reached_someone``). Entries written before 2026-09-27 do not
    # know, and say nothing rather than guess from ``outward``.
    if isinstance(pack.get("reached"), bool):
        info["reached"] = pack["reached"]
    return info


# ---- the entry a wake is writing ---------------------------------------------------
#
# Why (2026-09-25): a push a wake sent linked to the conversation it ran in
# — ``/chat?c=edward-mind``, the mind's raw thread of 10–20K character
# packs — and a note linked nowhere, so tapping "Parker asked about 3pm"
# opened a wall of pack instead of the entry that explains it. A wake now
# allocates its entry's id before the turn (``begin_entry``); the push and
# note tools link to it (``entry_url``) and use it for acks and the unread
# dot; the wake writes the entry under that id afterwards
# (``record_entry(conversation_id=…)``). The link is out before the entry
# exists, so ``record_entry`` falls back to a minimal entry when its write
# fails, ``finish_entry`` writes one when the journal never ran (a
# timeout), and the conversations API answers a placeholder while the wake
# is still running (``is_open_entry``): the link never 404s.

class PendingEntry:
    """One wake's entry: its id and the outward acts that handed out its
    link. Mutable on purpose — tools run in child tasks, which reach this
    same object through their copied context, so what they note is seen by
    the wake that finishes it."""

    __slots__ = ("id", "linked", "closed", "begun_at")

    def __init__(self, entry_id: str):
        self.id = entry_id
        self.linked: List[Dict[str, Any]] = []
        self.closed = False
        self.begun_at = time.monotonic()


_pending_entry: ContextVar[Optional[PendingEntry]] = ContextVar("thought_journal_pending_entry", default=None)
# id → the entry, while its wake runs: what the conversations API reads to
# answer a placeholder for a link that is out before its entry is written.
_OPEN_ENTRIES: Dict[str, PendingEntry] = {}
OPEN_ENTRY_MAX_AGE_S = 3600.0  # a wake that never finished its entry (a crash) is forgotten
FINISH_TIMEOUT_SECONDS = 10


def _prune_open_entries(now: Optional[float] = None) -> None:
    now = time.monotonic() if now is None else now
    for eid in [k for k, e in _OPEN_ENTRIES.items() if e.closed or now - e.begun_at > OPEN_ENTRY_MAX_AGE_S]:
        _OPEN_ENTRIES.pop(eid, None)


def begin_entry() -> str:
    """Allocate this wake's entry id and make it the task's pending entry.
    Called by ``mind_service.wake`` before the turn, for every wake but the
    pulse; the tools the turn runs read it through ``current_entry_id``."""
    _prune_open_entries()
    entry = PendingEntry(str(uuid.uuid4()))
    _pending_entry.set(entry)
    _OPEN_ENTRIES[entry.id] = entry
    return entry.id


def clear_entry() -> None:
    """No entry for this turn (the surfaces pulse): the tools link as they
    always did. Only this context forgets it — a wake whose context the
    turn inherited still owns its entry."""
    _pending_entry.set(None)
    return None


def current_entry_id() -> Optional[str]:
    """The id of the entry this wake will write, or ``None`` outside a wake."""
    entry = _pending_entry.get()
    return entry.id if entry is not None and not entry.closed else None


def is_open_entry(conversation_id: Any) -> bool:
    """Is this the id of an entry whose wake is still running?"""
    entry = _OPEN_ENTRIES.get(str(conversation_id or ""))
    return entry is not None and not entry.closed and time.monotonic() - entry.begun_at <= OPEN_ENTRY_MAX_AGE_S


def entry_url(url: Optional[str], thread_id: Optional[str], entry_id: Optional[str]) -> Optional[str]:
    """Where a push or a note from this turn should open. Outside a wake,
    ``url`` unchanged. In a wake, the entry — when no url was given, or
    when it points at the wake's own raw thread (which means "this
    thought") — and anywhere else he chose, unchanged. Pure."""
    if not entry_id:
        return url
    u = (url or "").strip()
    if not u or (thread_id and u == f"/chat?c={thread_id}"):
        return f"/chat?c={entry_id}"
    return url


def note_linked(name: str, args: Optional[Mapping[str, Any]], result: Any) -> None:
    """A tool handed out this wake's entry link (a push delivered, a note
    left): remember the act, so an entry is written even if the journal
    cannot write the full one."""
    entry = _pending_entry.get()
    if entry is None or entry.closed:
        return
    entry.linked.append({"name": str(name), "args": dict(args or {}), "result": str(result or "")[:300],
                         "ok": True, "held": False})


def is_someone_elses(conversation: Any) -> bool:
    """A row under an entry's id that is not a thought: a conversation the
    owner started there before the entry was written (a reply to the placeholder,
    a voice turn). The entry never takes it over. Pure."""
    return conversation is not None and getattr(conversation, "source", None) != "edward_thought"


async def _own_id(conversation_id: str, reason: Optional[str]) -> str:
    """``conversation_id``, or a fresh id when someone else's conversation
    holds it (2026-09-25: the insert used to collide, the error was
    swallowed, and the owner's chat was taken for the entry — the narrative
    was never written, the ledger row and the unread dot pointed at their
    chat). The link already out then opens their conversation; Today and the
    Thoughts panel open the entry through its ledger row."""
    from services.conversation_service import get_conversation

    if not is_someone_elses(await get_conversation(conversation_id)):
        return conversation_id
    fresh = str(uuid.uuid4())
    print(f"[journal] {conversation_id} is already another conversation; "
          f"the entry for the {reason} wake is written as {fresh}")
    return fresh


def fallback_entry(*, reason: Optional[str], closing_note: Optional[str], actions: Any) -> Tuple[str, str]:
    """(title, narrative) for the minimal entry written when the full one
    could not be: the acts that are known and his closing words. Built
    without ``compose_entry`` (whose failure may be why it is needed)."""
    lead = [a for a in _acts(actions) if a.get("ok")]
    closing = _clip(closing_note, MAX_WHY_CHARS)
    title = headline(lead[0], {}, reason) if lead else (_clip(_first_sentence(closing), TITLE_MAX)
                                                        or "Edward's thought")
    parts = [f"**Why I looked** — {reason_label(reason)}"]
    if lead:
        parts.append("**What I did**\n" + "\n".join(f"- {describe_action(a)}" for a in lead[:MAX_DID_LINES]))
    parts.append(f"**Why** — {closing}" if closing else
                 "**Why** — (The full entry for this wake could not be written.)")
    return title or "Edward's thought", "\n\n".join(parts)[:MAX_NARRATIVE_CHARS]


# ================================================================ I/O ======

async def record_entry(*, reason: Optional[str], woke_for: Optional[str], reasoning: Any,
                       closing_note: Optional[str], actions: Any, when: Optional[datetime] = None,
                       ledger_id: Optional[int] = None, wake_thread: Optional[str] = None,
                       turn_id: Optional[str] = None, pack_excerpt: Optional[str] = None,
                       names: Optional[Mapping[str, str]] = None,
                       conversation_id: Optional[str] = None) -> Optional[str]:
    """Write one journal entry: a fresh ``edward_thought`` conversation whose
    opening message is the narrative. When a message reached someone or a
    push reached the owner, the conversation carries the unread dot
    (``notified_user``). Returns its id, or ``None``; never raises.

    ``conversation_id`` is the id ``begin_entry`` handed out: a push or a
    note may already link to it, so when the full entry cannot be written a
    minimal one is written under it instead (``fallback_entry``). When a
    conversation that is not a thought already holds it, the entry is
    written under a fresh id (``_own_id``)."""
    try:
        from services.conversation_service import get_conversation, mark_user_notified
        from services.thought_conversation_service import create_thought_conversation

        if conversation_id:
            conversation_id = await _own_id(conversation_id, reason)
        when = when or datetime.now(timezone.utc)
        names = await chat_names_for(actions, names)
        title, narrative = compose_entry(reason=reason, woke_for=woke_for, reasoning=reasoning,
                                         closing_note=closing_note, actions=actions, when=when, names=names)
        pack = entry_context_pack(ledger_id=ledger_id, reason=reason, wake_thread=wake_thread, turn_id=turn_id,
                                  actions=actions, acted_at=when, pack_excerpt=pack_excerpt, names=names)
        written = await create_thought_conversation(title, narrative, context_pack=pack,
                                                    conversation_id=conversation_id)
        # create_thought_conversation returns an id even when the write
        # failed; an entry that does not exist is no entry.
        if await get_conversation(written) is None:
            print(f"[journal] entry for the {reason} wake was not written")
            return await _fallback_if_linked(conversation_id, reason=reason, closing_note=closing_note,
                                             actions=actions, ledger_id=ledger_id, wake_thread=wake_thread)
        if reached_someone(actions):
            await mark_user_notified(written)
        print(f"[journal] {reason}: {title} → {written}")
        return written
    except Exception as exc:  # noqa: BLE001
        print(f"[journal] could not write the entry for the {reason} wake: {exc}")
        return await _fallback_if_linked(conversation_id, reason=reason, closing_note=closing_note,
                                         actions=actions, ledger_id=ledger_id, wake_thread=wake_thread)


_unattended_tasks: set = set()


def schedule_unattended_entry(rec: Any) -> None:
    """``turn_ledger.end_turn``'s hook: when the finished turn earns an
    unattended entry, write it in the background. Never raises."""
    try:
        if not should_journal_unattended(getattr(rec, "actions", None), getattr(rec, "source", None)):
            return
        task = asyncio.get_running_loop().create_task(journal_unattended_turn(rec))
    except Exception:  # noqa: BLE001 — no loop (a sync test), nothing to write on
        return
    _unattended_tasks.add(task)
    task.add_done_callback(_unattended_tasks.discard)


async def settle_unattended() -> None:
    """Wait for the unattended entries being written (tests, shutdown)."""
    await asyncio.gather(*list(_unattended_tasks), return_exceptions=True)


async def journal_unattended_turn(rec: Any) -> Optional[str]:
    """One entry for a turn that ran code on its own (see
    ``UNATTENDED_JOURNAL_TOOLS``): a ``ledger_events`` row of kind
    ``acted`` (source = the turn's source, ref = the turn) and the entry,
    linked both ways, so Today's "What I did" and "Right call?" work as
    they do for a wake. Returns the entry's id; never raises."""
    source = str(getattr(rec, "source", "") or "background")
    actions = list(getattr(rec, "actions", None) or [])
    code = unattended_code_actions(actions)
    if not code:
        return None
    try:
        from services.event_ledger import record_event_now

        ledger_id = await asyncio.wait_for(record_event_now(
            kind=ACTED_KIND, source=source, outcome="acted", reason=describe_action(code[0]),
            ref_id=getattr(rec, "id", None),
            detail={"tools": [a.get("name") for a in code], "conversation_id": getattr(rec, "conversation_id", None)},
        ), timeout=5.0)

        async def _write() -> Optional[str]:
            written = await record_entry(
                reason=source, woke_for=woke_for_message(getattr(rec, "_new_text", "")),
                reasoning=list(getattr(rec, "reasoning", None) or []), closing_note=None, actions=actions,
                ledger_id=ledger_id, wake_thread=getattr(rec, "conversation_id", None),
                turn_id=getattr(rec, "id", None))
            if written and ledger_id is not None:
                await attach_to_ledger(ledger_id, written)
            return written

        return await asyncio.wait_for(_write(), timeout=JOURNAL_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        print(f"[journal] entry for the {source} turn that ran code failed: {exc}")
        return None


async def _fallback_if_linked(conversation_id: Optional[str], **kw) -> Optional[str]:
    if not conversation_id:
        return None
    return await write_fallback_entry(conversation_id, **kw)


async def write_fallback_entry(conversation_id: str, *, reason: Optional[str], closing_note: Optional[str],
                               actions: Any, ledger_id: Optional[int] = None,
                               wake_thread: Optional[str] = None) -> Optional[str]:
    """The minimal entry under ``conversation_id``, so a link already out
    resolves: title from the first known act, narrative = the acts and his
    closing words, the entry's context pack (so it is listed and judged
    like any other). An entry already there is kept; someone else's
    conversation there is not taken over (a fresh id). Never raises."""
    try:
        from services.conversation_service import get_conversation, mark_user_notified
        from services.thought_conversation_service import create_thought_conversation

        conversation_id = await _own_id(conversation_id, reason)
        if await get_conversation(conversation_id) is not None:
            return conversation_id
        title, narrative = fallback_entry(reason=reason, closing_note=closing_note, actions=actions)
        pack = entry_context_pack(ledger_id=ledger_id, reason=reason, wake_thread=wake_thread, turn_id=None,
                                  actions=actions, acted_at=datetime.now(timezone.utc))
        await create_thought_conversation(title, narrative, context_pack=pack, conversation_id=conversation_id)
        if await get_conversation(conversation_id) is None:
            print(f"[journal] the fallback entry for the {reason} wake was not written either")
            return None
        if reached_someone(actions):
            await mark_user_notified(conversation_id)
        print(f"[journal] {reason}: fallback entry {title} → {conversation_id}")
        return conversation_id
    except Exception as exc:  # noqa: BLE001
        print(f"[journal] could not write the fallback entry for the {reason} wake: {exc}")
        return None


async def finish_entry(thought_id: Optional[str], *, reason: Optional[str], closing_note: Optional[str],
                       ledger_id: Optional[int] = None, wake_thread: Optional[str] = None) -> Optional[str]:
    """Close this wake's pending entry after its journal step, whatever that
    step did. When the journal wrote nothing (it timed out, failed, or the
    record kept no act) but a push or note already carries the entry's link,
    a minimal entry is written under that id and linked from the ledger row.
    Returns the entry's id, or ``None``; never raises; bounded by
    ``FINISH_TIMEOUT_SECONDS``."""
    entry = _pending_entry.get()
    if entry is None or entry.closed:
        return thought_id
    entry.closed = True
    _OPEN_ENTRIES.pop(entry.id, None)
    if thought_id and (thought_id == entry.id or not entry.linked):
        return thought_id
    if not entry.linked:
        return None

    async def _ensure() -> Optional[str]:
        if thought_id:
            # The journal wrote its entry under a fresh id because the owner's
            # conversation holds this one: the link already opens something,
            # and a fallback here would be a second entry.
            from services.conversation_service import get_conversation

            if await get_conversation(entry.id) is not None:
                return None
        written = await write_fallback_entry(entry.id, reason=reason, closing_note=closing_note,
                                             actions=entry.linked, ledger_id=ledger_id, wake_thread=wake_thread)
        if written and ledger_id is not None and not thought_id:
            await attach_to_ledger(ledger_id, written)
        return written

    try:
        written = await asyncio.wait_for(_ensure(), timeout=FINISH_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        print(f"[journal] could not settle entry {entry.id} for the {reason} wake: {exc}")
        written = None
    return thought_id or written


async def attach_to_ledger(ledger_id: Optional[int], thought_id: str) -> bool:
    """Put the entry's id on the wake's ledger row (``detail.thought_id``),
    so the mind's log and Today can link to it. Never raises."""
    if ledger_id is None or not thought_id:
        return False
    try:
        from sqlalchemy import select

        from services.database import LedgerEventModel, async_session

        async with async_session() as s:
            row = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.id == int(ledger_id))))\
                .scalar_one_or_none()
            if row is None:
                return False
            row.detail_json = detail_with_thought(row.detail_json, thought_id)
            await s.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[journal] could not link entry {thought_id} to ledger row {ledger_id}: {exc}")
        return False


def _chat_ids(actions: Any) -> List[str]:
    ids: List[str] = []
    for a in _acts(actions):
        args = a.get("args") if isinstance(a.get("args"), dict) else {}
        cid = _s(args.get("chat_id"))
        if cid:
            ids.extend([cid, cid.split(";")[-1]])
    return sorted(set(ids))


async def chat_names_for(actions: Any, known: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """``known`` plus the names of the chats the actions went to that the
    pack did not name, from ``heartbeat_events`` (the newest row per chat
    that carries a name). Never raises."""
    names: Dict[str, str] = dict(known or {})
    missing = [cid for cid in _chat_ids(actions) if cid not in names]
    if not missing:
        return names
    try:
        from sqlalchemy import desc, select

        from services.database import HeartbeatEventModel, async_session

        async with async_session() as s:
            rows = (await s.execute(
                select(HeartbeatEventModel.chat_identifier, HeartbeatEventModel.chat_name)
                .where(HeartbeatEventModel.chat_identifier.in_(missing), HeartbeatEventModel.chat_name.isnot(None))
                .order_by(desc(HeartbeatEventModel.created_at)).limit(200)
            )).all()
        for cid, name in rows:
            if cid and name and str(cid) not in names:
                names[str(cid)] = str(name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("journal: chat names unavailable: %s", exc)
    return names


def _detail(detail_json: Optional[str]) -> Dict[str, Any]:
    try:
        parsed = json.loads(detail_json) if detail_json else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def recent_action_entries(*, hours: int = 24, now: Optional[datetime] = None,
                                limit: int = 50) -> List[Dict[str, Any]]:
    """The mind's wakes — and the turns that ran code on their own
    (``ACTED_KIND``) — in the last ``hours`` that wrote an entry, newest
    first: ``{ledger_id, thought_id, title, first_line, at}`` (``at`` naive
    UTC, as stored). One query for the rows, one for their conversations."""
    from sqlalchemy import desc, select

    from services.database import ConversationModel, LedgerEventModel, async_session

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        rows = (await s.execute(
            select(LedgerEventModel).where(LedgerEventModel.kind.in_(("mind", ACTED_KIND)),
                                           LedgerEventModel.created_at >= since)
            .order_by(desc(LedgerEventModel.created_at)).limit(400)
        )).scalars().all()
        linked = [(r, _detail(r.detail_json).get("thought_id")) for r in rows]
        linked = [(r, tid) for r, tid in linked if isinstance(tid, str) and tid][:limit]
        if not linked:
            return []
        convs = {c.id: c for c in (await s.execute(
            select(ConversationModel).where(ConversationModel.id.in_([tid for _, tid in linked]))
        )).scalars().all()}
    out: List[Dict[str, Any]] = []
    for r, tid in linked:
        conv = convs.get(tid)
        if conv is None:
            continue
        pack = _detail(conv.context_pack)
        lines = pack.get("actions") if isinstance(pack.get("actions"), list) else []
        out.append({"ledger_id": r.id, "thought_id": tid, "title": conv.title,
                    "first_line": str(lines[0]) if lines else "", "at": r.created_at})
    return out


async def verdicts_for(ledger_ids: Iterable[int]) -> Dict[int, str]:
    """The owner's latest ``mind_verdict`` per ledger row, in one query."""
    ids = sorted({int(i) for i in ledger_ids if i is not None})
    if not ids:
        return {}
    from sqlalchemy import select

    from services.database import UserSignalModel, async_session

    async with async_session() as s:
        rows = (await s.execute(
            select(UserSignalModel).where(UserSignalModel.kind == "mind_verdict",
                                          UserSignalModel.ref_id.in_([str(i) for i in ids]))
            .order_by(UserSignalModel.created_at.asc(), UserSignalModel.id.asc())
        )).scalars().all()
    out: Dict[int, str] = {}
    for r in rows:  # oldest first, so the latest verdict wins
        verdict = _detail(r.detail_json).get("verdict")
        lid = _ledger_id(r.ref_id)
        if lid is not None and verdict in VERDICTS:
            out[lid] = verdict
    return out


def _is_entry(conversation: Any) -> bool:
    return (getattr(conversation, "source", None) == "edward_thought"
            and is_entry_pack(getattr(conversation, "context_pack", None)))


async def thought_fields(conversations: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """conversation id → its ``thought`` field, for every journal entry in
    ``conversations``, with the owner's verdicts read in one batch. A failure
    reading verdicts leaves them ``None``; it never breaks a listing."""
    infos: Dict[str, Dict[str, Any]] = {}
    for c in conversations or []:
        try:
            if _is_entry(c):
                info = thought_info(c.context_pack)
                if info is not None:
                    infos[str(c.id)] = info
        except Exception:  # noqa: BLE001
            continue
    if not infos:
        return infos
    try:
        verdicts = await verdicts_for(i["ledger_id"] for i in infos.values())
    except Exception as exc:  # noqa: BLE001
        logger.warning("journal: verdicts unavailable: %s", exc)
        verdicts = {}
    for info in infos.values():
        info["verdict"] = verdicts.get(info["ledger_id"])
    return infos
