"""Taint at the boundary: what a turn read decides what it may send alone.

Why (2026-09-24; docs/architecture/trust-boundary.md): Edward's backend has
the "lethal trifecta" — it reads untrusted text (inbound iMessages, web
pages, email bodies), it holds private data (chat.db, memories, the owner's
logged-in Chrome), and it can send (iMessage, SMS, WhatsApp, mail, hosted pages,
typing into forms). Meta's Muse answers this with eBPF taint tracking, a
host-side egress broker the agent cannot override and approvals shown as
system UI, never chat. This is that design scaled down to one process:

* **Egress secret scan** (``secret_scan``), on by default: anything leaving
  through an egress tool is scanned for credentials; a finding blocks the
  call with ``Tool error:`` whatever the mode, and a ``ledger_events`` row
  (kind ``egress``, outcome ``blocked``) records the kinds — never the
  secret. ``EDWARD_EGRESS_SECRET_SCAN=off`` is the emergency switch.
* **Taint per turn**, on the task-local turn record (``turn_ledger``):
  set at the start when the turn carries third-party text by construction
  (``construction_taint``), and after any tool that ingests untrusted
  content returns successfully (``ingest_taint``). Written to
  ``turn_log.tainted`` / ``taint_reason``.
* **The gate** (``gate_decision``): a send to a third party (a message,
  mail, a hosted page, typing anywhere but a search box — and installing
  an MCP server or starting a Codex session, which run code of the page's
  choosing), in a tainted
  turn that nobody is watching, is a *gated send*. ``EDWARD_TAINT_APPROVALS``
  decides what happens to it: ``off`` (the default) runs it and records
  nothing about the gate; ``shadow`` runs it as today and records ``taint``/``would_gate``; ``on`` does not run it — it becomes a
  pending approval the owner answers out of band (a push with Approve / Deny,
  ``approvals_service``); ``off`` does nothing.

Deterministic like ``push_safety_gate``: safety, not taste. The model sees
only the result string; nothing it writes can change a decision here.

**What counts as third-party text by construction** (from how the packs
are built, 2026-09-24):

* ``heartbeat`` — the trigger is an inbound message.
* ``mind`` — every wake's pack, whatever the reason (not only ``messages``
  and ``mention``), renders "Recent messages" verbatim from chat.db
  (``mind_service.render_pack`` / ``_messages_source``), plus escalations
  with their thread lines.
* ``pulse`` — the surfaces pack renders recent messages verbatim too
  (``expression_service.build_pulse_trigger``).
* ``twilio`` — the trigger is an SMS/WhatsApp from any number.
* the threads ``edward-mind`` and ``edward-surfaces`` — whatever the
  source (a scheduled event fires into ``edward-mind``), their history
  window is those packs.

A turn that is otherwise clean but tries a gated send also checks its
thread's recent turns (``HISTORY_TAINT_DAYS``): a web page read in an
earlier turn is still in the history an autonomous turn sends from.
Documents, memories, the calendar and the owner's notes are not treated as
untrusted — Edward or the owner wrote them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

from services import secret_scan
from services.owner import owner_text

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
MODE_ENV = "EDWARD_TAINT_APPROVALS"
SCAN_ENV = "EDWARD_EGRESS_SECRET_SCAN"
TAINT_REASON_MAX_CHARS = 80
HISTORY_TAINT_DAYS = 7
LEDGER_TIMEOUT_S = 5.0

# Where the owner is the one asking: the send is their request in this very
# turn (sends the owner asked for in this same turn stay automatic).
OWNER_PRESENT_SOURCES = frozenset({"chat", "voice", "widget", "alexa"})

TAINTED_SOURCES = frozenset({"heartbeat", "mind", "pulse", "twilio"})
TAINTED_THREADS = frozenset({"edward-mind", "edward-surfaces"})

# Tools that bring untrusted content into the turn when they succeed.
INGEST_TOOLS = frozenset({
    # the toolset (2026-10-11): the web, both browsers, what Edward looks at, and
    # the shell (``curl`` output is a page). ``messages`` is by action below.
    "web_fetch", "browser", "chrome", "look", "bash",
    # the web
    "fetch_page_content", "web_search", "webSearch",
    # the owner's Chrome: every call that returns page text, titles or a picture
    "browser_open", "browser_read", "browser_find", "browser_steps", "browser_tabs", "browser_use_tab",
    "browser_wait", "browser_click", "browser_back", "look_at_screen",
    # other people's messages and attachments
    "read_imessage_thread", "get_recent_messages", "get_imessage_attachment", "review_heartbeat",
    # mail
    "search_inbox", "read_email", "get_email_actions", "search_gmail", "read_gmail_message",
    "get_gmail_unsub_info",
    # files (downloads, attachments saved to storage)
    "read_storage_file",
    # workers bring back what they read
    "check_worker", "wait_for_workers",
})
# ...and every custom MCP tool (``toolset.names.is_custom_mcp``): its server's
# reply is someone else's text.
# apple-mcp multiplexes reads and sends through one tool per app.
INGEST_OPERATIONS: Dict[str, frozenset] = {
    "mail": frozenset({"unread", "search", "latest", "read"}),
    "messages": frozenset({"read", "unread"}),
}
# The toolset's action tools read other people's words by these actions:
# messages (iMessage, and email since 2026-10-11) and schedule (calendar
# events and reminders others wrote or invited the owner to). A ledger
# signal only, never a block (design decision, 2026-10-11: no new gates on
# reading).
INGEST_ACTIONS: Dict[str, frozenset] = {
    "messages": frozenset({"recent", "thread", "participants", "mail_search", "mail_read"}),
    "schedule": frozenset({"calendar", "reminders"}),
}

# Sends to someone other than the owner. Push, notes, the widget and the
# screens are the owner's (or the household's) and never gated; the secret scan still
# covers URLs and searches, which leave the machine but address no person.
GATED_SEND_TOOLS = frozenset({
    "send_imessage", "send_message", "send_sms", "send_whatsapp",
    "create_hosted_page", "update_hosted_page",
    "browser_type",
    # Not a send, but the same shape of harm: a page that says "install
    # this MCP server" would have code of its choosing running on the host Mac
    # (and restarted on every boot) with nobody watching.
    "add_mcp_server", "update_mcp_server",
    # Likewise starting Codex (2026-09-27, Evolution v2 Phase 0): a coding
    # session with write access to files, its task taken from whatever the
    # turn read. Read-only sessions too — the task still steers what runs.
    "spawn_cc_worker",
})
GATED_SEND_PREFIXES = ("whatsapp_send",)
GATED_SEND_OPERATIONS: Dict[str, frozenset] = {
    "mail": frozenset({"send"}),
    "messages": frozenset({"send", "schedule"}),
}
# Typing a query into a search box addresses no one: the pulse puts videos
# on the screen by typing into YouTube. Exact hosts only — mail.google.com
# and docs.google.com are not search boxes.
SEARCH_TYPING_HOSTS = frozenset({
    "www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com",
    "www.google.com", "google.com", "www.bing.com", "duckduckgo.com",
    "www.twitch.tv", "twitch.tv", "open.spotify.com",
})

_OWNER = owner_text("{OWNER}")  # in model-facing refusals

_ON = {"on", "enforce", "enforced", "1", "true", "yes"}
_OFF = {"off", "0", "false", "no", "disabled", "disable", "none"}


# ------------------------------------------------------------------ pure --

def parse_mode(raw: Optional[str]) -> str:
    """``on`` / ``off`` / ``shadow``. Unset is ``off`` since the toolset
    (2026-10-11): 20 of 20 ``would_gate`` rows were
    legitimate replies, and ``send_message``'s recipient rule replaced the
    hold. Anything unrecognised is ``shadow`` — record, never silently
    enforce or silently drop."""
    value = (raw or "").strip().lower()
    if not value:
        return "off"
    if value in _ON:
        return "on"
    if value in _OFF:
        return "off"
    return "shadow"


def taint_mode() -> str:
    return parse_mode(os.getenv(MODE_ENV))


def secret_scan_enabled() -> bool:
    return (os.getenv(SCAN_ENV) or "on").strip().lower() not in _OFF


def owner_present(source: Optional[str]) -> bool:
    return (source or "") in OWNER_PRESENT_SOURCES


def construction_taint(source: Optional[str], conversation_id: Optional[str]) -> Optional[str]:
    """The reason a turn starts tainted, or ``None``."""
    if (source or "") in TAINTED_SOURCES:
        return f"source:{source}"
    if (conversation_id or "") in TAINTED_THREADS:
        return f"thread:{conversation_id}"
    return None


def _operation(args: Any) -> str:
    return str((args or {}).get("operation") or "").strip().lower() if isinstance(args, dict) else ""


def _action(args: Any) -> str:
    return str((args or {}).get("action") or "").strip().lower() if isinstance(args, dict) else ""


def _succeeded(result: Any) -> bool:
    from services.graph import tool_policy

    return not tool_policy.is_failure_result(result)


def ingest_taint(tool_name: str, args: Any, result: Any) -> Optional[str]:
    """``read:<tool>`` when a successful call brought untrusted content in."""
    from services.toolset.names import is_custom_mcp

    name = str(tool_name or "")
    reads = name in INGEST_TOOLS or is_custom_mcp(name)  # a custom MCP server's reply is its own text
    if not reads and name.startswith("whatsapp_") and not name.startswith(GATED_SEND_PREFIXES):
        reads = True
    acts = INGEST_ACTIONS.get(name)
    if acts is not None and _action(args):
        reads = _action(args) in acts
    else:
        ops = INGEST_OPERATIONS.get(name)
        if ops is not None:
            reads = _operation(args) in ops
    if reads and _succeeded(result):
        return f"read:{name}"
    return None


def _host(url: Optional[str]) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def typed_hosts(tool_name: str, args: Any, current_url: Optional[str] = None) -> List[str]:
    """The site each piece of typing lands on (``""`` = unknown). A step
    that can navigate — a click, back, a key — loses track of the site."""
    if tool_name == "browser_type" or (tool_name == "chrome" and _action(args) == "type"):
        return [_host(current_url)]
    hosts: List[str] = []
    url: Optional[str] = current_url
    steps = secret_scan._steps((args or {}).get("steps") if isinstance(args, dict) else None)
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip().lower()
        if action == "open":
            url = str(step.get("url") or "")
        elif action in ("click", "back", "key"):
            url = None
        elif action == "type":
            hosts.append(_host(url))
    return hosts


def is_gated_send(tool_name: str, args: Any = None, current_url: Optional[str] = None) -> bool:
    """A call that sends something to a third party. Typing counts unless
    every keystroke lands on a search site (``SEARCH_TYPING_HOSTS``) — the
    pulse types "lofi piano" into YouTube, not a message to anyone."""
    name = str(tool_name or "")
    if name == "chrome":
        if _action(args) not in ("type", "steps"):
            return False
        hosts = typed_hosts(name, args, current_url)
        return bool(hosts) and not all(h in SEARCH_TYPING_HOSTS for h in hosts)
    if name in ("browser_type", "browser_steps"):
        hosts = typed_hosts(name, args, current_url)
        return bool(hosts) and not all(h in SEARCH_TYPING_HOSTS for h in hosts)
    if name in GATED_SEND_TOOLS or name.startswith(GATED_SEND_PREFIXES):
        return True
    ops = GATED_SEND_OPERATIONS.get(name)
    return ops is not None and _operation(args) in ops


def gate_decision(*, source: Optional[str], tool_name: str, args: Any, tainted: bool, mode: str,
                  current_url: Optional[str] = None) -> str:
    """``run`` | ``would_gate`` (shadow) | ``gate`` (on). ``current_url`` is
    Edward's working tab, for typing."""
    if mode == "off" or not tainted or owner_present(source) \
            or not is_gated_send(tool_name, args, current_url=current_url):
        return "run"
    return "gate" if mode == "on" else "would_gate"


def merge_reasons(existing: Optional[str], reason: Optional[str]) -> Optional[str]:
    """``source:mind; read:web_search`` — each cause once, the first ones
    kept when the column would overflow."""
    if not reason:
        return existing
    parts = [p for p in (existing or "").split("; ") if p]
    if reason in parts:
        return existing
    candidate = "; ".join(parts + [reason])
    if len(candidate) > TAINT_REASON_MAX_CHARS:
        return existing if existing else reason[:TAINT_REASON_MAX_CHARS]
    return candidate


def blocked_message(arg: str, kinds: Sequence[str]) -> str:
    return (f"Tool error: blocked — the {arg} contains what looks like a secret ({', '.join(kinds)}). "
            "Credentials must never leave this machine.")


# ----------------------------------------------------------- the hooks --

def _turn():
    try:
        from services.turn_ledger import current_turn

        return current_turn()
    except Exception:  # noqa: BLE001
        return None


def _conversation_id(rec: Any) -> Optional[str]:
    conv = getattr(rec, "conversation_id", None)
    if conv:
        return conv
    try:
        from services.graph.tools import get_current_conversation_id

        return get_current_conversation_id()
    except Exception:  # noqa: BLE001
        return None


async def _ledger(**kwargs) -> None:
    try:
        from services.event_ledger import record_event_now

        await asyncio.wait_for(record_event_now(**kwargs), timeout=LEDGER_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — a ledger row never blocks a turn
        logger.debug("taint ledger write failed: %s", exc)


async def egress_refusal(tool_name: str, args: Any) -> Optional[str]:
    """The secret scan. ``Tool error: blocked …`` when this call would send
    a credential, else ``None``. Runs in every mode, and on approved calls
    too (``approvals_service``)."""
    if not secret_scan.is_egress_tool(tool_name, args) or not secret_scan_enabled():
        return None
    try:
        hits = secret_scan.scan_egress(tool_name, args, secret_scan.known_secrets())
    except Exception as exc:  # noqa: BLE001 — fail closed: nothing leaves unscanned
        logger.exception("secret scan failed for %s", tool_name)
        return f"Tool error: blocked — the secret check failed ({type(exc).__name__}); nothing was sent."
    if not hits:
        return None
    kinds = secret_scan.kinds_of(hits)
    arg = hits[0][0].split("[", 1)[0].split(".", 1)[0]
    rec = _turn()
    print(f"[egress] blocked {tool_name}: {arg} carried {', '.join(kinds)}")
    await _ledger(kind="egress", source=tool_name, outcome="blocked", reason=", ".join(kinds),
                  ref_id=getattr(rec, "id", None),
                  detail={"arg": arg, "kinds": kinds, "turn_source": getattr(rec, "source", None),
                          "conversation_id": _conversation_id(rec)})
    return blocked_message(arg, kinds)


def _working_tab_url() -> Optional[str]:
    """Where Edward's hands are in the owner's Chrome (in memory; no I/O)."""
    try:
        from services import browser_hands

        return ((browser_hands.status() or {}).get("tab") or {}).get("url")
    except Exception:  # noqa: BLE001
        return None


async def _history_taint(conversation_id: Optional[str], turn_id: Optional[str]) -> Optional[str]:
    """``history`` when an earlier turn in this thread was tainted recently."""
    if not conversation_id:
        return None
    try:
        from sqlalchemy import select
        from services.database import async_session, TurnLogModel

        since = datetime.utcnow() - timedelta(days=HISTORY_TAINT_DAYS)
        async with async_session() as session:
            q = (select(TurnLogModel.id).where(TurnLogModel.conversation_id == conversation_id)
                 .where(TurnLogModel.tainted.is_(True)).where(TurnLogModel.started_at >= since))
            if turn_id:
                q = q.where(TurnLogModel.id != turn_id)
            row = (await asyncio.wait_for(session.execute(q.limit(1)), timeout=LEDGER_TIMEOUT_S)).first()
        return "history" if row is not None else None
    except Exception as exc:  # noqa: BLE001 — the weakest signal; never blocks
        logger.debug("history taint lookup failed: %s", exc)
        return None


async def gate_refusal(tool_name: str, args: Any) -> Optional[str]:
    """The taint gate. ``None`` to run the call; otherwise the tool result
    that answers it (``on`` mode: the approval is pending)."""
    mode = taint_mode()
    current_url = _working_tab_url() if str(tool_name).startswith("browser_") or tool_name == "chrome" else None
    if mode == "off" or not is_gated_send(tool_name, args, current_url=current_url):
        return None
    rec = _turn()
    source = getattr(rec, "source", None)
    if owner_present(source):
        return None
    tainted = bool(getattr(rec, "tainted", False))
    if not tainted and rec is not None:
        reason = await _history_taint(_conversation_id(rec), getattr(rec, "id", None))
        if reason:
            rec.note_taint(reason)
            tainted = True
    decision = gate_decision(source=source, tool_name=tool_name, args=args, tainted=tainted, mode=mode,
                             current_url=current_url)
    if decision == "run":
        return None
    taint_reason = getattr(rec, "taint_reason", None) or ""
    conversation_id = _conversation_id(rec)
    detail = {"turn_source": source, "conversation_id": conversation_id, "taint": taint_reason}
    if decision == "would_gate":
        print(f"[taint] would gate {tool_name} ({source}; {taint_reason})")
        await _ledger(kind="taint", source=tool_name, outcome="would_gate",
                      reason=f"{tool_name}: {taint_reason}", ref_id=getattr(rec, "id", None), detail=detail)
        return None
    from services import approvals_service

    print(f"[taint] gated {tool_name} ({source}; {taint_reason}) — asking the owner")
    return await approvals_service.request(
        tool_name=tool_name, args=args, conversation_id=conversation_id,
        turn_id=getattr(rec, "id", None), taint_reason=taint_reason, turn_source=source,
    )


async def check_tool_call(tool_name: str, args: Any) -> Optional[str]:
    """Both checks, in order, for the tool executor: a result string that
    answers the call without running it, or ``None`` to run it."""
    refused = await egress_refusal(tool_name, args)
    if refused is not None:
        return refused
    try:
        return await gate_refusal(tool_name, args)
    except Exception as exc:  # noqa: BLE001
        if taint_mode() == "on":
            logger.exception("taint gate failed for %s", tool_name)
            return (f"Tool error: could not ask {_OWNER} to approve {tool_name} ({type(exc).__name__}); "
                    "nothing was sent.")
        logger.debug("taint gate (shadow) failed: %s", exc)
        return None


def note_tool_result(tool_name: str, args: Any, result: Any) -> None:
    """After a call ran: taint the turn when it brought untrusted content in."""
    try:
        reason = ingest_taint(tool_name, args, result)
        if reason:
            rec = _turn()
            if rec is not None:
                rec.note_taint(reason)
    except Exception as exc:  # noqa: BLE001
        logger.debug("taint note failed: %s", exc)


# --------------------------------------------------------------- report --

def _head(reason: str) -> str:
    return reason.strip()[:60]


def taint_report(turns: Iterable[Dict[str, Any]] = (), events: Iterable[Dict[str, Any]] = (),
                 approvals: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """What the boundary saw: tainted turns by reason and source, the sends
    shadow mode would have gated (by tool), secrets blocked on the way out
    (by kind and tool), and approvals by status. Pure."""
    turns = list(turns)
    tainted = [t for t in turns if t.get("tainted")]
    by_reason: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for t in tainted:
        by_source[t.get("source") or "?"] = by_source.get(t.get("source") or "?", 0) + 1
        for part in str(t.get("taint_reason") or "").split("; "):
            if part:
                by_reason[_head(part)] = by_reason.get(_head(part), 0) + 1

    def count_by(rows: List[Dict[str, Any]], key) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in rows:
            for k in key(r):
                out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    events = list(events)
    would = [e for e in events if e.get("kind") == "taint" and e.get("outcome") == "would_gate"]
    blocked = [e for e in events if e.get("kind") == "egress" and e.get("outcome") == "blocked"]
    approvals = list(approvals)
    by_status: Dict[str, int] = {}
    for a in approvals:
        by_status[a.get("status") or "?"] = by_status.get(a.get("status") or "?", 0) + 1
    return {
        "mode": taint_mode(),
        "secret_scan": "on" if secret_scan_enabled() else "off",
        "turns": {"total": len(turns), "tainted": len(tainted),
                  "by_reason": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
                  "by_source": dict(sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])))},
        "would_gate": {"total": len(would), "by_tool": count_by(would, lambda e: [e.get("source") or "?"])},
        "egress_blocked": {"total": len(blocked),
                           "by_kind": count_by(blocked, lambda e: [k.strip() for k in str(e.get("reason") or "?").split(",") if k.strip()]),
                           "by_tool": count_by(blocked, lambda e: [e.get("source") or "?"])},
        "approvals": {"total": len(approvals), "by_status": dict(sorted(by_status.items()))},
    }


async def build_taint_report(days: int = 7) -> Dict[str, Any]:
    """``taint_report`` over the last ``days`` — the coordinator wires it
    into ``GET /api/health/report`` as ``trust``."""
    from sqlalchemy import select
    from services.database import async_session, LedgerEventModel, PendingApprovalModel, TurnLogModel

    since = datetime.utcnow() - timedelta(days=days)
    async with async_session() as session:
        turns = (await session.execute(
            select(TurnLogModel.source, TurnLogModel.tainted, TurnLogModel.taint_reason)
            .where(TurnLogModel.started_at >= since))).all()
        events = (await session.execute(
            select(LedgerEventModel.kind, LedgerEventModel.source, LedgerEventModel.outcome, LedgerEventModel.reason)
            .where(LedgerEventModel.created_at >= since)
            .where(LedgerEventModel.kind.in_(("taint", "egress", "approval"))))).all()
        approvals = (await session.execute(
            select(PendingApprovalModel.status, PendingApprovalModel.tool_name)
            .where(PendingApprovalModel.created_at >= since))).all()
    report = taint_report(
        turns=[{"source": s, "tainted": t, "taint_reason": r} for s, t, r in turns],
        events=[{"kind": k, "source": s, "outcome": o, "reason": r} for k, s, o, r in events],
        approvals=[{"status": s, "tool_name": n} for s, n in approvals],
    )
    report["days"] = days
    return report
