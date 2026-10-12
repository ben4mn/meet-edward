"""
Tools for Edward's LLM capabilities.

Memory tools allow Edward to:
- Update existing memories with new information
- Delete/forget memories when asked
- Search memories to find specific information

Messaging tools allow Edward to:
- Send SMS via Twilio (Edward's phone number)
- Send iMessage via MCP (as the user)
- Smart routing between channels

Code execution tools allow Edward to:
- Write and execute Python code in a sandbox
- See execution results and iterate

Search tools allow Edward to:
- Search the web using Brave Search
- Fetch and extract content from web pages
"""

from pathlib import Path
import asyncio

from typing import Any, Callable, Optional, List, Dict
from langchain_core.tools import tool
from services import host_config
from services.owner import (
    owner_doc,
    owner_handles as _env_owner_handles,
    owner_name,
    owner_name_is_set,
    owner_names,
    owner_text,
)
from services.speak_gate import quiet_hours_doc
from utils.message_signature import ensure_message_signature


def _owner_screen_doc(fn):
    """``@owner_doc`` plus this host's ``/screen`` address (``{SCREEN_URL}``,
    from ``host_config.public_screen_url``) — innermost, like ``@owner_doc``."""
    fn = owner_doc(fn)
    if fn.__doc__:
        fn.__doc__ = fn.__doc__.replace("{SCREEN_URL}", host_config.public_screen_url())
    return fn


def _owner_title() -> str:
    """``owner_name()`` for the start of a sentence ("The owner" when unset)."""
    name = owner_name()
    return name[:1].upper() + name[1:]


# The extraction profile a turn's own ``remember`` runs under. The pulse is
# a surfaces wake and gets the mind's; a fired event gets its own
# (2026-09-27, H1): in none of them is the owner speaking, so ``heard`` becomes
# ``read``/``thought``, and a wake's or the pulse's intention lapses.
_REMEMBER_PROFILES = {"mind": "mind", "pulse": "mind", "scheduled_event": "scheduled_event"}


def _remember_profile() -> str:
    """The profile for the current turn (``_REMEMBER_PROFILES``), else
    ``chat``: only a wake's intentions lapse by default."""
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001 — a missing turn is an ordinary chat write
        return "chat"
    return _REMEMBER_PROFILES.get(rec.source, "chat") if rec is not None else "chat"


@tool
@owner_doc
async def remember(
    content: str,
    memory_type: str = "context",
    about: str = "self",
    origin: str = "thought",
    importance: float = 0.5,
    valid_until: Optional[str] = None,
    confidence: Optional[float] = None,
) -> str:
    """
    Write a memory of your own: something you decided, noticed, learned from
    what you read, or did and what came of it.

    Until the mind rebuild you could not create a memory from inside a turn;
    creation was post-turn extraction only, and it ignored what you said.
    This is how a decision survives to the next wake.

    Args:
        content: The memory, self-contained ("Watching ACME for {OWNER}'s bonus;
            tell them if it moves more than 3%").
        memory_type: fact, preference, context, instruction, or intention
            (a thing you decided to do or watch).
        about: self (your own decisions, lessons), owner ({OWNER}), contact
            (someone else), world (a price, the weather, a product).
        origin: thought (you concluded or decided it), read (you read it in
            a message, page, or screen), did (an action you took and its
            outcome), heard ({OWNER} said it — rare here; extraction handles that).
        importance: 0-1.
        valid_until: ISO-8601 when the memory stops applying (an
            intention's horizon); a time without a zone is {OWNER}'s local
            time, as in remember_update. Omit for open-ended — except in a wake,
            where an intention with no horizon lapses after 72 hours unless
            a `wake_me` or `schedule_event` links it (that holds it past the
            fire), and for a preference or instruction written as a thought:
            a thought is temporary, and those lapse after 30 days.
        confidence: 0-1; defaults by origin (did 0.9, read 0.7, thought 0.6).

    Returns:
        "ok (memory_id: ...)" — the id to update or retire later.
    """
    from datetime import datetime

    from services.memory_service import (
        VALID_ABOUTS,
        VALID_MEMORY_TYPES,
        VALID_ORIGINS,
        Memory,
        default_valid_until,
        store_memories_with_conflict_detection,
    )

    text = (content or "").strip()
    if not text:
        return "Tool error: content is empty."
    if memory_type not in VALID_MEMORY_TYPES:
        return f"Tool error: memory_type must be one of {', '.join(VALID_MEMORY_TYPES)}."
    if about not in VALID_ABOUTS:
        return f"Tool error: about must be one of {', '.join(VALID_ABOUTS)}."
    if origin not in VALID_ORIGINS:
        return f"Tool error: origin must be one of {', '.join(VALID_ORIGINS)}."

    horizon = None
    if valid_until not in (None, ""):
        # A zone-less time is the owner's, as in remember_update, wake_me and
        # schedule_event (review of batch A1, 2026-09-27: read as UTC here,
        # the same string lapsed five hours earlier than through
        # remember_update).
        horizon = _parse_user_time(valid_until)
        if horizon is None or horizon <= datetime.utcnow():
            return ("Tool error: valid_until must be an ISO-8601 time in the future "
                    f"(2026-10-01T09:00:00; no zone = {owner_name()}'s local time).")

    source_type = {"did": "tool_derived", "read": "tool_derived", "heard": "user_stated"}.get(origin, "inferred")
    default_conf = {"did": 0.9, "read": 0.7, "heard": 0.9}.get(origin, 0.6)
    conf = default_conf if confidence is None else max(0.0, min(1.0, float(confidence)))
    from services.memory_service import coerce_extracted_fields, current_turn_source
    from services.provenance_service import turn_source_ref

    # The same hygiene extraction applies (2026-09-26): what Edward did is a
    # record, never an intention; a wake's intention with no horizon lapses
    # after MIND_INTENTION_DEFAULT_HOURS; a thought about the owner is capped.
    fields, _misses = coerce_extracted_fields(
        {"memory_type": memory_type, "origin": origin, "about": about, "source_type": source_type,
         "confidence": conf, "importance": importance,
         "valid_until": f"{horizon.isoformat()}Z" if horizon is not None else None},
        profile=_remember_profile(), turn_source=current_turn_source(),
    )

    # A temporary instruction or preference with no horizon lapses
    # (docs/architecture/memory.md; preferences, 2026-09-27) — set here so
    # the reply below can say when.
    lapses_at = default_valid_until(fields["memory_type"], fields["temporal_nature"], fields["valid_until"],
                                    content=text)
    memory = Memory(
        id=None,
        content=text,
        memory_type=fields["memory_type"],
        importance=fields["importance"],
        temporal_nature=fields["temporal_nature"],
        source_type=fields["source_type"],
        confidence=fields["confidence"],
        source_conversation_id=get_current_conversation_id(),
        valid_until=lapses_at,
        origin=fields["origin"],
        about=fields["about"],
        source_ref=turn_source_ref("remember", conversation_id=get_current_conversation_id()),
    )
    try:
        stored = await store_memories_with_conflict_detection([memory])
    except Exception as exc:  # noqa: BLE001
        return f"Tool error: could not store the memory ({type(exc).__name__}: {exc})"
    if not stored:
        return "Tool error: the memory was not stored."
    # The lapse said is the kept row's (2026-09-27): conflict detection may
    # keep an existing row and its lifetime — an echo, a reinforcement, a
    # MERGE — and "it lapses <date>" of a row that never lapses misleads.
    kept = stored[0]
    kept_until = getattr(kept, "valid_until", None)
    kept_type = getattr(kept, "memory_type", None) or fields["memory_type"]
    if horizon is None and kept_type == "intention" and kept_until is not None:
        # The wake's default horizon, said out loud (review 2026-09-26).
        lapses = kept_until.strftime("%Y-%m-%d %H:%M UTC")
        return (f"ok (memory_id: {kept.id}) — this intention lapses {lapses} unless you give it a "
                "valid_until or link it to a wake_me.")
    if horizon is None and kept_type in ("instruction", "preference") and kept_until is not None:
        lapses = kept_until.strftime("%Y-%m-%d %H:%M UTC")
        return (f"ok (memory_id: {kept.id}) — a temporary {kept_type}; it lapses {lapses} "
                "unless you give it a valid_until (remember_update).")
    return f"ok (memory_id: {kept.id})"


def _parse_user_time(value: str):
    """ISO-8601 → naive UTC. A time without a zone is the owner's local time (the
    zone in settings, not the host's — ``timezone_utils.local_naive_to_utc``);
    ``Z`` or an offset is honoured. None when it does not parse."""
    from datetime import datetime

    from services.timezone_utils import local_naive_to_utc

    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return local_naive_to_utc(dt).replace(tzinfo=None)


def _owners_row_refusal(existing, change: str) -> Optional[str]:
    """Why this turn may not ``change`` ("change" or "forget") ``existing``,
    in Edward's words — or None when it may.

    What the owner said changes only in their own turn (integration review
    of the memory audit, 2026-09-27): from a wake, the pulse, a fired event,
    a heartbeat reply, a worker or a Twilio text these tools rewrote,
    retired and hard-deleted rules the owner had just pinned — 17 mind
    turns called ``remember_forget`` in a month. Refused there: a ``heard``/
    ``owner`` row, and a ``owner_confirmed`` pin whatever its labels.
    Intentions stay Edward's to tidy (``close_intention`` closes one from a
    wake either way), and so do his own rows and what he read. With no
    turn on record the chat profile counts as the owner's
    (``memory_service.is_owners_own_turn``), as it does for extraction."""
    from services.memory_service import (
        _is_owners_row,
        current_turn_source,
        is_owner_confirmed,
        is_owners_own_turn,
    )

    if is_owners_own_turn(current_turn_source(), _remember_profile()):
        return None
    pinned = is_owner_confirmed(existing)
    if not pinned and (not _is_owners_row(existing) or existing.memory_type == "intention"):
        return None
    owner = owner_name()
    whose = f"{owner} confirmed that rule themselves" if pinned else f"{owner} told me that themselves"
    verb = "change it" if change == "change" else "have me forget it"
    return (f"Tool error: {whose}, so only they can {verb}, in their own chat with me — not from here. "
            "I've left it exactly as it is; if it looks wrong or out of date, the thing to do is ask them.")


@tool
@owner_doc
async def remember_update(memory_id: str, new_content: Optional[str] = None,
                          valid_until: Optional[str] = None) -> str:
    """
    Update an existing memory: correct what it says, or change when it stops
    applying.

    Use this when the user provides updated information that should replace
    or modify an existing memory. The memory's embedding will be regenerated.
    To close one of your intentions, with what came of it, use
    close_intention instead. What {OWNER} told you themselves changes only in
    their own chat with you: from a wake or anyone else's thread this refuses it.

    Args:
        memory_id: The ID of the memory to update (from the memory context)
        new_content: The new content for this memory (omit to keep it)
        valid_until: When it stops applying, ISO-8601 (2026-10-01T09:00:00);
            a time without a zone is {OWNER}'s local time. Now or earlier
            retires it — it stays on record, out of retrieval.

    Returns:
        Confirmation message
    """
    from services.memory_service import update_memory, get_memory_by_id

    content = (new_content or "").strip() or None
    horizon = None
    if valid_until not in (None, ""):
        horizon = _parse_user_time(valid_until)
        if horizon is None:
            return "Tool error: valid_until must be an ISO-8601 datetime like 2026-10-01T09:00:00."
    if content is None and horizon is None:
        return "Tool error: give new_content, valid_until, or both."

    # Verify memory exists
    existing = await get_memory_by_id(memory_id)
    if not existing:
        return f"Memory with ID {memory_id} not found. Cannot update."
    refused = _owners_row_refusal(existing, "change")
    if refused:
        return refused

    # 2026-09-27 (M4): the pack and the research trigger told Edward to close
    # an intention "with a past valid_until" — a parameter this tool did not
    # have.
    updated = await update_memory(memory_id=memory_id, content=content, valid_until=horizon)
    if not updated:
        return "Failed to update memory."
    if horizon is None:
        return "ok"
    from datetime import datetime

    state = "retired as of" if horizon <= datetime.utcnow() else "valid until"
    return f"ok ({state} {horizon:%Y-%m-%d %H:%M} UTC)"


@tool
@owner_doc
async def close_intention(memory_id: str, outcome: str) -> str:
    """
    Close one of your intentions: it is done, dropped, or no longer matters.

    It leaves "Open intentions" and research at once. The row stays, with
    what came of it appended, so how_do_i_know and the Memory room keep the
    story — remember_forget would delete that history, and changing the
    text with remember_update does not retire it.

    Args:
        memory_id: The intention's id (from "Open intentions" in your pack,
            or remember_search).
        outcome: What came of it, in a line ("Briefed Theo on Friday",
            "Dropped — {OWNER} handled it themselves").

    Returns:
        "ok (memory_id: … closed)" or "Tool error: …".
    """
    from services.memory_service import close_intention as _close

    closed, refused = await _close(memory_id, outcome)
    if refused:
        return f"Tool error: {refused}"
    reply = f"ok (memory_id: {closed.id} closed; the outcome is kept on the row)"
    try:
        # A wake Edward set for it still fires; say so rather than cancel
        # what may be a reminder for the owner.
        from sqlalchemy import select

        from services.database import ScheduledEventModel, async_session
        from services.scheduled_events_service import linked_memory_id

        async with async_session() as session:
            events = (await session.execute(
                select(ScheduledEventModel).where(
                    ScheduledEventModel.status.in_(("pending", "processing")),
                    ScheduledEventModel.event_metadata.isnot(None),
                )
            )).scalars().all()
        waiting = [e for e in events if linked_memory_id(e.event_metadata) == closed.id]
        if waiting:
            e = waiting[0]
            when = f" at {e.next_fire_at:%Y-%m-%d %H:%M} UTC" if e.next_fire_at else ""
            reply += (f" — scheduled event {e.id}{when} is still set for it; cancel_scheduled_event it if it "
                      "no longer matters.")
    except Exception:  # noqa: BLE001 — the close already happened
        pass
    return reply


@tool
@owner_doc
async def remember_forget(memory_id: str) -> str:
    """
    Delete/forget a specific memory.

    Use this when the user explicitly asks to forget something, or when
    information is no longer accurate or relevant. What {OWNER} told you
    themselves is forgotten only when they ask, in their own chat with you.

    Args:
        memory_id: The ID of the memory to delete (from the memory context)

    Returns:
        Confirmation message
    """
    from services.memory_service import delete_memory, get_memory_by_id

    # Get memory content for confirmation
    existing = await get_memory_by_id(memory_id)
    if not existing:
        return f"Memory with ID {memory_id} not found. Nothing to delete."
    refused = _owners_row_refusal(existing, "forget")
    if refused:
        return refused

    deleted = await delete_memory(memory_id)
    if deleted:
        return "ok"
    return "Failed to delete memory."


# N2: per-conversation cache for remember_search results. The LLM often
# searches for the same entity multiple times in one turn (mid-reasoning,
# again to verify). Each call is ~50ms of pgvector + BM25 — caching by
# (conversation_id, query, memory_type) cuts repeats to ~10us.
import time as _time

_MEMORY_SEARCH_CACHE_TTL_SECONDS = 300  # 5 min — short enough to stay fresh
_MEMORY_SEARCH_CACHE_MAX = 256
_memory_search_cache: Dict[tuple, tuple[float, str]] = {}


def _clear_memory_search_cache() -> None:
    """Test hook — drops all cached entries."""
    _memory_search_cache.clear()


@tool
async def remember_search(query: str, memory_type: Optional[str] = None) -> str:
    """
    Search through memories for specific information.

    Use this to find memories related to a topic, or to check what
    information is stored about something specific.

    Args:
        query: Search query describing what to find
        memory_type: Optional filter: 'fact', 'preference', 'context', or 'instruction'

    Returns:
        List of matching memories with their IDs
    """
    from services.memory_service import search_memories

    # N2: cache lookup. Only cache when we have a conversation_id —
    # other callers (workers, scheduled turns) bypass the cache.
    conversation_id = get_current_conversation_id()
    cache_key = None
    if conversation_id:
        cache_key = (conversation_id, query, memory_type)
        cached = _memory_search_cache.get(cache_key)
        if cached is not None:
            ts, formatted = cached
            if _time.time() - ts < _MEMORY_SEARCH_CACHE_TTL_SECONDS:
                return formatted
            else:
                _memory_search_cache.pop(cache_key, None)

    memories, total = await search_memories(
        query=query,
        memory_type=memory_type,
        limit=10,
        live_only=True,
    )

    if not memories:
        formatted = f"No memories found matching '{query}'."
    else:
        results = [f"Found {total} memories matching '{query}':\n"]
        for m in memories[:10]:
            score_str = f" (score: {m.score:.2f})" if m.score > 0 else ""
            tn = getattr(m, 'temporal_nature', 'timeless') or 'timeless'
            tn_tag = f" [{tn}]" if tn != "timeless" else ""
            results.append(f"- [{m.memory_type}] {m.content}{tn_tag} (ID: {m.id}){score_str}")
        formatted = "\n".join(results)

    if cache_key is not None:
        # Bound cache size — drop oldest if over the cap
        if len(_memory_search_cache) >= _MEMORY_SEARCH_CACHE_MAX:
            try:
                oldest_key = min(_memory_search_cache.items(), key=lambda kv: kv[1][0])[0]
                _memory_search_cache.pop(oldest_key, None)
            except ValueError:
                pass
        _memory_search_cache[cache_key] = (_time.time(), formatted)

    return formatted


@tool
@owner_doc
async def update_memory_confidence(
    memory_id: str,
    new_confidence: float,
    reason: Optional[str] = None,
) -> str:
    """
    Lower (or raise) the confidence on an existing memory.

    Use this when the user contradicts a stored memory — for example, they
    say "I don't drink coffee anymore" and the memory "{OWNER} drinks coffee
    every morning" is now unreliable. Prefer this over ``remember_forget``
    when the memory might still be partially true or historically relevant:
    retrieval multiplies scores by confidence, so a low-confidence memory
    is deprioritised without being erased.

    Confidence is clamped to [0, 1]. 0 means "essentially false", 0.5 is
    the default "unsure", 1 is "verified".

    Args:
        memory_id: The ID of the memory to update.
        new_confidence: The new confidence value. Clamped to [0, 1].
        reason: Optional short explanation for the change (for logging).

    Returns:
        ``"ok"`` on success, descriptive string on failure.
    """
    from services.memory_service import get_memory_by_id, update_memory

    existing = await get_memory_by_id(memory_id)
    if not existing:
        return f"Memory with ID {memory_id} not found. Cannot update confidence."

    clamped = max(0.0, min(1.0, float(new_confidence)))
    updated = await update_memory(memory_id=memory_id, confidence=clamped)
    if not updated:
        return "Failed to update memory confidence."

    if reason:
        print(
            f"[memory] confidence({memory_id}) -> {clamped:.2f} "
            f"reason={reason!r}"
        )
    return "ok"


@tool
async def graph_memory_search(query: str, limit: int = 5) -> str:
    """
    Search Edward's knowledge-graph memory (Graphiti + Kuzu).

    Use this for questions that benefit from multi-hop reasoning or temporal
    awareness — "who does X know through Y", "what was true about Z last
    month" — where flat hybrid search under ``remember_search`` falls short.

    pgvector via ``remember_search`` is still primary for dense similarity;
    this tool augments with graph traversal + temporal edges (valid_at /
    invalid_at). Returns a compact list of facts with validity windows.

    Returns ``"(graph memory disabled)"`` if the Graphiti layer isn't
    enabled or hasn't been backfilled yet — fall back to ``remember_search``.

    Args:
        query: Natural-language question.
        limit: Max fact edges to return (default 5).
    """
    from services.graph_memory_service import is_enabled, search

    if not is_enabled():
        return "(graph memory disabled — set GRAPHITI_ENABLED=true and restart)"

    results = await search(query, limit=limit)
    if not results:
        return f"No graph-memory hits for '{query}'."

    lines = [f"{len(results)} graph-memory fact{'s' if len(results) != 1 else ''}:"]
    for r in results:
        fact = r.get("fact") or "(no fact text)"
        valid = r.get("valid_at")
        invalid = r.get("invalid_at")
        window = ""
        if valid or invalid:
            window = f" [valid: {valid or 'always'} → {invalid or 'now'}]"
        lines.append(f"- {fact}{window}")
    return "\n".join(lines)


# List of all memory tools for binding to LLM
MEMORY_TOOLS = [
    remember,
    remember_update,
    close_intention,
    remember_forget,
    remember_search,
    update_memory_confidence,
    graph_memory_search,
]


# ============================================================================
# MESSAGING TOOLS
# ============================================================================

@tool
async def send_sms(phone_number: str, message: str) -> str:
    """
    Send an SMS message via Twilio (from Edward's phone number).

    Use this when you need to text someone as Edward. The recipient will see
    the message coming from Edward's dedicated phone number.

    Args:
        phone_number: The recipient's phone number (can be formatted like +1234567890 or 123-456-7890)
        message: The message content to send

    Returns:
        Confirmation message with status
    """
    from services.twilio_service import send_sms as twilio_send, is_configured
    from services.conversation_service import mark_user_notified

    if not is_configured():
        return "SMS not available. Twilio is not configured."

    message = ensure_message_signature(message)

    # The send guards bind every send tool, this one too (2026-09-25): a
    # wake never texts the owner, a stand-down binds, the same text goes once.
    guard = await _send_guard(phone_number, message)
    if guard is not None:
        return guard

    try:
        result = await _send_and_note(phone_number, message, twilio_send(phone_number, message))
        # Mark this conversation as having notified the user
        conversation_id = get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)
        return f"SMS sent successfully to {result['to']} (Status: {result['status']})"
    except Exception as e:
        return f"Failed to send SMS: {str(e)}"


# ---- send guards (2026-09-24) ---------------------------------------------------------
#
# Two things must hold before the mind may message people on its own (the
# mind_enabled gaps, 2026-09-24):
#   * A stand-down binds the send tools themselves. The old heartbeat path
#     stripped the messaging tools from a stood-down chat's turn; the mind
#     path binds send_imessage for every wake, so the tools check. It binds
#     autonomous turns — the owner asking in a chat or voice turn still goes
#     through — and a fresh @edward mention in the chat releases it.
#   * A send is idempotent. A wake that times out keeps what it sent (an
#     osascript send cannot be taken back) but loses its turn, and the next
#     wake is handed the same escalation: the same text to the same
#     recipient within ten minutes is answered "Already sent", not sent.
# Both apply inside a turn, which is the only place the tool loop runs them.

import re as _re
from collections import deque as _deque
from datetime import datetime as _dt, timezone as _tz

SEND_DEDUPE_WINDOW_SECONDS = 600
STANDDOWN_EXEMPT_SOURCES = frozenset({"chat", "voice", "widget", "alexa"})
_SEND_HISTORY: "_deque[tuple[str, str, _dt]]" = _deque(maxlen=200)


def _chat_keys(target: Optional[str]) -> set:
    """Every spelling a chat or handle can come in: as given, the tail of an
    AppleScript GUID ("iMessage;+;chat123" → "chat123"), and a phone
    number's last ten digits."""
    t = (target or "").strip()
    if not t:
        return set()
    keys = {t.lower()}
    if ";" in t:
        tail = t.rsplit(";", 1)[1].strip()
        if tail:
            keys.add(tail.lower())
    for k in list(keys):
        digits = _re.sub(r"\D", "", k)
        if len(digits) >= 7 and _re.fullmatch(r"[0-9+()\-.\s]+", k):
            keys.add("p:" + digits[-10:])
    return keys


def same_chat(a: Optional[str], b: Optional[str]) -> bool:
    """Do two chat identifiers / handles name the same chat? Pure."""
    return bool(_chat_keys(a) & _chat_keys(b))


def send_key(target: Optional[str]) -> str:
    """One canonical key per recipient for the dedupe ledger. Pure."""
    keys = _chat_keys(target)
    phones = sorted(k for k in keys if k.startswith("p:"))
    if phones:
        return phones[0]
    t = (target or "").strip()
    if ";" in t:
        return t.rsplit(";", 1)[1].strip().lower()
    return t.lower()


def normalize_send_text(text: Optional[str]) -> str:
    """The text two sends are compared on: no signature, whitespace
    collapsed, case folded. Pure."""
    from utils.message_signature import _SIGNATURE_PATTERNS

    t = str(text or "").rstrip()
    for p in _SIGNATURE_PATTERNS:
        t = _re.sub(p, "", t, flags=_re.IGNORECASE).rstrip()
    return " ".join(t.split()).casefold()


def duplicate_send_at(history, recipient_key: str, text_key: str, now: _dt,
                      window_s: float = SEND_DEDUPE_WINDOW_SECONDS) -> Optional[_dt]:
    """When the same text last went to the same recipient within the
    window, or None. Pure."""
    for rk, tk, at in reversed(list(history)):
        if rk == recipient_key and tk == text_key and 0 <= (now - at).total_seconds() <= window_s:
            return at
    return None


def looks_delivered(result: Any) -> bool:
    """Did a send tool's reply report a delivery? Pure."""
    return str(result or "").startswith(("iMessage sent", "SMS sent", "WhatsApp sent", "Sent via iMessage",
                                         "Sent via Gmail"))


def reset_send_history() -> None:
    """Tests: forget the dedupe ledger."""
    _SEND_HISTORY.clear()


def _current_turn():
    try:
        from services import turn_ledger
        return turn_ledger.current_turn()
    except Exception:  # noqa: BLE001
        return None


async def _active_standdown_for(target: Optional[str]):
    """The live ``chat_standdowns`` row covering ``target``, or None. Fails
    open, like the old path's lookup: a DB hiccup must not wedge a turn."""
    if not target:
        return None
    try:
        from sqlalchemy import select

        from services.database import ChatStanddownModel, async_session

        async with async_session() as session:
            rows = (await session.execute(
                select(ChatStanddownModel).where(ChatStanddownModel.released_at.is_(None))
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001
        print(f"[send] stand-down lookup failed (sending anyway): {exc}")
        return None
    now = _dt.now(_tz.utc)
    for row in rows:
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=_tz.utc)
        if expires is not None and expires <= now:
            continue
        if same_chat(target, row.chat_identifier):
            return row
    return None


def _clock(at: _dt) -> str:
    try:
        from services.timezone_utils import utc_to_local
        return utc_to_local(at).strftime("%-I:%M %p")
    except Exception:  # noqa: BLE001
        return at.strftime("%H:%M UTC")


# ---- a wake never texts the owner (2026-09-25) --------------------------------------
#
# The judgement check's late_dose scenario: an evening wake saw Biscuit's pill
# 50 minutes late with the owner away and called send_message(recipient=<the
# owner's name>). Telling them was right; the channel was wrong. A wake
# reaches the owner by send_push_notification (a thread, the push ledger,
# quiet hours, the interrupt ladder, their "Right call?") or leave_note
# (Today); a text from the messaging tools skips all of that, and
# send_imessage goes out from the owner's own number. So in a turn nobody
# asked for, a send addressed to the owner — by name, whole, or by one of
# their own handles — is refused: by the executor before the taint gate (or
# the owner would be asked to approve a text to themselves) and by
# _send_guard. The owner asking in chat or voice ("text me the address") is
# untouched, and so is a reply to the owner's own ping in their self-chat
# (owner_direct): that is answering them where they asked — in that chat only.

# "If you meant someone else named …" only makes sense with a name to mean.
TEXT_TO_OWNER_REFUSAL = owner_text(
    "Tool error: {OWNER} is reached by send_push_notification (if it can't wait) or "
    "leave_note — not by text."
    + (" If you meant someone else named {OWNER}, use their full name or number."
       if owner_name_is_set() else ""))
# Appended when the owner's own message is waiting in their self-chat and the
# send went somewhere else: the one place a text to them is the answer.
TEXT_TO_OWNER_WAITING_HINT = (" Their own message is waiting for your answer: reply with "
                            "send_imessage(chat_id=\"{chat}\").")
# The turns nobody asked for. Known gap, left as is (2026-09-25): a wake can
# still schedule_event("text <the owner> …"), which fires as a scheduled_event
# turn that this does not bind. And the household hub's voice (``hub_voice``,
# 2026-10-11): someone in the owner's home, not necessarily the owner.
NO_TEXT_TO_OWNER_SOURCES = frozenset({"mind", "pulse", "hub_voice"})
# How the model names the person it works for in a target without using a
# name: "me"/"myself", and — since the prompts say "the owner" when
# EDWARD_OWNER_NAME is unset — "owner"/"the owner".
OWNER_SELF_WORDS = frozenset({"me", "myself", "owner", "the owner"})


def owner_target_names() -> frozenset:
    """The names a send target may use for the owner, lowercased: every name
    from ``EDWARD_OWNER_NAME`` / ``_FULL_NAME`` / ``_ALIASES``
    (``owner.owner_names``) plus ``OWNER_SELF_WORDS``. Whole names only: a
    longer name that starts with the owner's ("Samantha" for "Sam") or a
    namesake with another surname is someone else. With no name configured
    only the self words (and the owner's handles) match. Read per call."""
    return owner_names() | OWNER_SELF_WORDS


# The argument each send is delivered to, first set wins: send_imessage
# sends to chat_id when one is given and ignores contact (2026-09-25).
SEND_TARGET_ARGS: Dict[str, tuple] = {
    "send_message": ("recipient",),
    "send_imessage": ("chat_id", "contact"),
    "send_sms": ("phone_number",),
    "send_whatsapp": ("phone_number",),
}
# How far back a ping from the owner may still be waiting for its answer.
OWNER_PING_LOOKBACK_HOURS = 24
# A phone number inside a target ("tel:+1…", "Sam (+1…)"): digits and phone
# punctuation, not glued to a letter or digit on either side — "chat512…",
# a group chat's id, is not a phone — and at least ten digits.
_HANDLE_SCHEMES = ("tel:", "sms:", "whatsapp:", "imessage:", "mailto:")
_PHONE_RUN = _re.compile(r"(?<![A-Za-z0-9])\+?\d[\d\s().\-]*\d(?![A-Za-z0-9])")
MIN_PHONE_DIGITS = 10


def send_targets(tool_name: Any, args: Any) -> List[str]:
    """Where a send tool call is delivered, as given: the first of its
    target arguments that is set (send_imessage: chat_id, else contact —
    the contact is ignored once a chat_id is given). Pure."""
    if not isinstance(args, dict):
        return []
    for key in SEND_TARGET_ARGS.get(str(tool_name), ()):
        if args.get(key):
            return [str(args[key])]
    return []


def _bare_handle(target: Optional[str]) -> str:
    """A target without a URL-style scheme ("tel:", "whatsapp:", …). Pure."""
    t = str(target or "").strip()
    low = t.casefold()
    for scheme in _HANDLE_SCHEMES:
        if low.startswith(scheme):
            return t[len(scheme):].strip()
    return t


def phone_keys(target: Optional[str]) -> set:
    """The last ten digits of each phone number a target spells — "+1 (201)
    555-0100", "tel:+1…", "whatsapp:+1…", "Sam (+1…)" — and none for a run
    shorter than ten digits or glued to letters. Pure. (twilio_service.
    normalize_phone_number reads "chat2015550100" as a phone, so a group
    chat's id could name the owner; it is not used here.)"""
    keys = set()
    for run in _PHONE_RUN.findall(str(target or "")):
        digits = _re.sub(r"\D", "", run)
        if len(digits) >= MIN_PHONE_DIGITS:
            keys.add(digits[-10:])
    return keys


def same_handle(a: Optional[str], b: Optional[str]) -> bool:
    """Do two targets reach the same person or chat: ``same_chat`` on their
    spellings without a scheme, or a phone number in common? Pure."""
    a, b = _bare_handle(a), _bare_handle(b)
    if not a or not b:
        return False
    return same_chat(a, b) or bool(phone_keys(a) & phone_keys(b))


def names_owner(target: Optional[str], owner_handles=()) -> bool:
    """Does a send's target name the owner: one of their names, whole
    (``owner_target_names``), or one of their own handles — those passed in
    plus ``EDWARD_OWNER_HANDLES`` (a phone on its last ten digits wherever
    it sits in the target, an email, a chat GUID's tail)? Pure but for the
    environment it reads."""
    if not str(target or "").strip():
        return False
    if " ".join(str(target).split()).casefold().strip(" \"'.,;:!?") in owner_target_names():
        return True
    handles = list(owner_handles or ()) + list(_env_owner_handles())
    return any(same_handle(target, h) for h in handles if str(h or "").strip())


async def _owner_handles() -> List[str]:
    """The owner's own iMessage handles: ``heartbeat_config.
    imessage_owner_handles`` (the same list the listener and the interrupt
    ladder read), then any from ``EDWARD_OWNER_HANDLES`` not already there;
    [] when neither is set. Never raises."""
    import json

    env_handles = [str(h) for h in _env_owner_handles() if str(h or "").strip()]
    try:
        from sqlalchemy import select

        from services.database import HeartbeatConfigModel, async_session

        async with async_session() as session:
            raw = (await session.execute(
                select(HeartbeatConfigModel.imessage_owner_handles).where(HeartbeatConfigModel.id == "default")
            )).scalar_one_or_none()
        parsed = json.loads(raw) if raw else []
    except Exception as exc:  # noqa: BLE001
        print(f"[send] owner handles lookup failed (matching the owner by name and env handles only): {exc}")
        return env_handles
    if not isinstance(parsed, list):
        parsed = []
    handles = [str(h) for h in parsed if str(h or "").strip()]
    seen = {h.lower() for h in handles}
    return handles + [h for h in env_handles if h.lower() not in seen]


async def _owner_all_handles() -> List[str]:
    """Every handle that is the owner: their iMessage handles
    (``_owner_handles``) and their email addresses
    (``mail_service.owner_addresses``: GMAIL_ACCOUNT, the connected Gmail,
    the addresses they send from in Mail.app). Never raises (2026-10-11)."""
    handles = [str(h) for h in await _owner_handles()]
    try:
        from services import mail_service

        seen = {h.lower() for h in handles}
        handles += [a for a in await mail_service.owner_addresses() if a.lower() not in seen]
    except Exception:  # noqa: BLE001 — their iMessage handles still count
        pass
    return handles


async def _chats_where_owner_is_waiting() -> List[str]:
    """The chats where a ping from the owner themselves (``owner_direct``:
    their self-chat) is still waiting for its answer — escalated, not yet
    considered by a wake, from the last ``OWNER_PING_LOOKBACK_HOURS`` by the
    database's clock (created_at is its now(), in UTC; 2026-09-25). A text
    to them there is the reply; anywhere else it is not. Fails closed:
    unreadable means none."""
    from datetime import timedelta

    try:
        from sqlalchemy import DateTime, func, select

        from services.database import HeartbeatEventModel, async_session
        from services.heartbeat.mind_path import _is_owner_direct

        since = func.timezone("UTC", func.now(), type_=DateTime) - timedelta(hours=OWNER_PING_LOOKBACK_HOURS)
        async with async_session() as session:
            rows = (await session.execute(
                select(HeartbeatEventModel).where(
                    HeartbeatEventModel.is_from_user.is_(True),
                    HeartbeatEventModel.triage_status == "escalated",
                    HeartbeatEventModel.notified_at.is_(None),
                    HeartbeatEventModel.created_at >= since,
                ).order_by(HeartbeatEventModel.created_at.desc()).limit(50)
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001
        print(f"[send] owner ping lookup failed (treating the owner as not waiting): {exc}")
        return []
    chats: List[str] = []
    for row in rows:
        chat = str(row.chat_identifier or row.sender or "").strip()
        if chat and chat not in chats and _is_owner_direct(row):
            chats.append(chat)
    return chats


async def _text_to_owner_refusal(targets) -> Optional[str]:
    """The refusal when a turn nobody asked for addresses the owner by text,
    else None. Names (and env handles) are checked first; the database only
    when they miss. A text into the self-chat where the owner's own ping is
    waiting goes through."""
    targets = [t for t in targets if str(t or "").strip()]
    if not targets:
        return None
    turn = _current_turn()
    if turn is None or (getattr(turn, "source", "") or "") not in NO_TEXT_TO_OWNER_SOURCES:
        return None
    to_owner = [t for t in targets if names_owner(t)]
    if len(to_owner) < len(targets):
        handles = await _owner_all_handles()
        to_owner = [t for t in targets if names_owner(t, handles)]
    if not to_owner:
        return None
    waiting = await _chats_where_owner_is_waiting()
    if all(any(same_handle(t, chat) for chat in waiting) for t in to_owner):
        return None
    if waiting:
        return TEXT_TO_OWNER_REFUSAL + TEXT_TO_OWNER_WAITING_HINT.format(chat=waiting[0])
    return TEXT_TO_OWNER_REFUSAL


async def text_to_owner_refusal(tool_name: Any, args: Any) -> Optional[str]:
    """For the tool executor, ahead of the taint gate: the refusal for a
    send tool call that texts the owner from a turn nobody asked for, else None."""
    return await _text_to_owner_refusal(send_targets(tool_name, args))


# The household hub's voice on the legacy loop (security fix, 2026-10-11).
# Under ``EDWARD_TOOLSET=legacy`` no tool reads the turn's policy: these
# sends have no recipient rule, and ``execute_shell`` is bash as the owner
# with no sandbox and no redaction (it can osascript Messages itself).
# Whoever is at the hub panel is not necessarily the owner, so a
# ``hub_voice`` turn there goes without every send and what a friend's
# legacy ping goes without (``pings.scope.LEGACY_THIRD_PARTY_EXCLUDED``). It
# reaches the owner through ``leave_note`` / ``send_push_notification``,
# which go through ``reach_service`` (origin ``hub``). Under v2 the tools
# check the policy.
HUB_VOICE_SOURCE = "hub_voice"
# ``{what}`` is filled per call with str.format, so the owner's name goes in
# at import through owner_text (which only replaces the ``{OWNER}`` token).
HUB_LEGACY_REFUSAL = owner_text(
    "Tool error: from the household hub I can't {what} on this toolset — it would act as {OWNER}, "
    "and whoever is at the hub may not be {OWNER}. If it matters, tell {OWNER} (leave_note, or "
    "send_push_notification if it can't wait).")


def hub_legacy_refusal(tool_name: Any, args: Any) -> Optional[str]:
    """For the tool executor: the refusal for a call a ``hub_voice`` turn may
    not make on the legacy loop, else None (any other turn, or v2)."""
    turn = _current_turn()
    if turn is None or (getattr(turn, "source", "") or "") != HUB_VOICE_SOURCE:
        return None
    from services.toolset import is_v2

    if is_v2():
        return None
    from services.pings import scope

    name = str(tool_name or "")
    if scope.is_send(name, args):
        return HUB_LEGACY_REFUSAL.replace("{what}", "message anyone")
    if name in scope.LEGACY_THIRD_PARTY_EXCLUDED:
        return HUB_LEGACY_REFUSAL.replace("{what}", f"use {name}")
    return None


async def _send_guard(target: Optional[str], message: str) -> Optional[str]:
    """None to go ahead with a send, or the reply that replaces it."""
    turn = _current_turn()
    if turn is None:
        return None
    refused = await _text_to_owner_refusal([target])
    if refused is not None:
        return refused
    if (getattr(turn, "source", "") or "") not in STANDDOWN_EXEMPT_SOURCES:
        row = await _active_standdown_for(target)
        if row is not None:
            who = row.requested_by or "Someone in that chat"
            when = f" at {_clock(row.requested_at)}" if row.requested_at else ""
            return (
                f"Tool error: stand-down active for {target} — {who} asked you to stay out of this chat{when}. "
                "You cannot message it until someone there @edward-mentions you again or the stand-down "
                f"expires. If something there needs attention, push {owner_name()} (send_push_notification); "
                "it is theirs to handle."
            )
    at = duplicate_send_at(_SEND_HISTORY, send_key(target), normalize_send_text(message), _dt.now(_tz.utc))
    if at is not None:
        return f"Already sent to {target} at {_clock(at)} — not sending again."
    return None


def _note_sent(target: Optional[str], message: str) -> None:
    if _current_turn() is not None:
        _SEND_HISTORY.append((send_key(target), normalize_send_text(message), _dt.now(_tz.utc)))


async def _send_and_note(target: Optional[str], message: str, send: Any,
                         delivered: Callable[[Any], bool] = lambda _result: True) -> Any:
    """Await ``send`` (the transport) and note a delivered send as one unit
    that a cancellation does not split (2026-09-27 review). The chat router
    cancels the step in flight when the owner leaves; osascript runs on in its
    worker thread, so the text went out while ``_note_sent`` was skipped
    and a re-ask sent it again; a Twilio request cut mid-flight may or may
    not have gone out. The send finishes and is recorded before the
    cancellation goes on — the thread stays held until it has, so the next
    turn sees "Already sent". A transport error propagates as before."""

    async def _unit() -> Any:
        result = await send
        if delivered(result):
            _note_sent(target, message)
        return result

    task = asyncio.ensure_future(_unit())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except Exception:  # noqa: BLE001 — the turn is going away; the send's error has no reader
            pass
        raise


@tool
async def send_imessage(contact: str, message: str, chat_id: Optional[str] = None) -> str:
    """
    Send an iMessage as the user (not as Edward).

    Use this when the user wants to send a message as themselves via iMessage.
    Only use when the user explicitly requests to send as themselves or for
    personal contacts.

    Args:
        contact: Contact name or phone number (used for DMs; ignored when chat_id is provided)
        message: The message content to send
        chat_id: Optional chat identifier for replying to group chats.
                 When provided, sends to the group chat instead of to an individual.
                 Use this when replying to messages from group chats (provided in heartbeat context).

    Returns:
        Confirmation message with status
    """
    from services.imessage_service import (
        send_imessage as applescript_send,
        send_imessage_to_chat,
        is_available,
        looks_like_imessage_handle,
    )
    from services.conversation_service import mark_user_notified

    if not is_available():
        return "iMessage not available. Only works on macOS with Messages.app configured."

    message = ensure_message_signature(message)

    # The owner's name never reaches Contacts from a wake: they are told by
    # push or note (2026-09-25). Only the target delivery uses counts — chat_id when
    # given, else contact. _send_guard checks the resolved handle below.
    refused = await _text_to_owner_refusal([chat_id or contact])
    if refused is not None:
        return refused

    # Names are not deliverable handles — Messages would create a chat
    # addressed to the literal text. Resolve via macOS Contacts, or bail
    # with instructions the model can act on.
    resolved_note = ""
    if not chat_id and not looks_like_imessage_handle(contact):
        try:
            from services import contacts_service
            lookup = await asyncio.to_thread(contacts_service.lookup_contact, contact)
        except Exception as exc:
            lookup = {"success": False, "error": str(exc)}
        matches = (lookup or {}).get("matches") or []
        usable = [m for m in matches if m.get("phones") or m.get("emails")]
        if len(usable) == 1:
            handle = (usable[0].get("phones") or usable[0].get("emails"))[0]
            resolved_note = f" (resolved '{contact}' via Contacts)"
            contact = handle
        elif len(usable) > 1:
            names = ", ".join(m.get("name", "?") for m in usable[:5])
            return (
                f"'{contact}' matches multiple contacts ({names}). Send with an "
                "exact phone number/email, or reply using the chat_id from the "
                "message context."
            )
        else:
            return (
                f"Could not resolve '{contact}' to a phone number or email in "
                "Contacts. Use the chat_id from the message context, or provide "
                "an exact phone number/email."
            )

    # A stand-down binds the tool; the same text twice is sent once.
    guard = await _send_guard(chat_id or contact, message)
    if guard is not None:
        return guard

    # Heartbeat turns get a second set of eyes: an advisory, fail-open LLM
    # review that bounces filler / commitments on the owner's behalf back for revision
    # (inactive outside heartbeat respond turns).
    from services import send_review_service
    review = await send_review_service.review_outgoing_message(message, chat_id or contact)
    if not review["approved"]:
        return (
            f"Send-review rejected this draft: {review['reason']} — revise it, "
            "or skip sending if a reply adds nothing."
        )

    try:
        # osascript is a blocking subprocess (up to 30s) — run it in a worker
        # thread so it can't freeze the event loop (FastAPI + heartbeat +
        # scheduler all share it). The send's ledger bookkeeping needs the
        # app loop to schedule onto from that thread.
        from services import imessage_service as _imsg
        _imsg.note_main_loop(asyncio.get_running_loop())
        transport = (asyncio.to_thread(send_imessage_to_chat, chat_id, message) if chat_id
                     else asyncio.to_thread(applescript_send, contact, message))
        result = await _send_and_note(chat_id or contact, message, transport,
                                      delivered=lambda r: bool(r.get("success")))
        if result["success"]:
            # Mark this conversation as having notified the user
            conversation_id = get_current_conversation_id()
            if conversation_id:
                await mark_user_notified(conversation_id)
            # Register a listening window so the next inbound message from
            # this thread is auto-handled by Edward instead of dismissed by
            # Layer 1. Heartbeat's own dispatch does this inline; sends
            # initiated by scheduled_events / orchestrator / manual tool
            # use need it surfaced here.
            try:
                from services.heartbeat.triage_service import (
                    register_listening_window_for_chat,
                )

                window_target = chat_id or contact
                await register_listening_window_for_chat(
                    window_target, conversation_id=conversation_id
                )
            except Exception:
                # Listening-window failure must not block the send result.
                pass
            if chat_id:
                return f"iMessage sent to group chat {chat_id}"
            return f"iMessage sent to {contact}{resolved_note}"
        else:
            print(f"[iMessage] send FAILED to {chat_id or contact}: {result.get('error', 'Unknown error')}")
            return f"Failed to send iMessage: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to send iMessage: {str(e)}"


@tool
@owner_doc
async def stand_down(chat_id: str, requested_by: Optional[str] = None) -> str:
    """
    Record a binding stand-down for a chat: you will stop messaging this
    chat until the person @edward-mentions you again (or 24h passes).

    Call this when someone in a chat tells you to stay out, stop replying,
    or says they were talking to {OWNER} — right after (or instead of) your
    brief acknowledgment. Once recorded it is ENFORCED: your messaging
    tools are disabled for this chat's autonomous turns, and anything that
    needs attention there (including safety concerns) routes to {OWNER} via
    push notification instead.

    Args:
        chat_id: The chat identifier (phone number for 1:1, chat GUID for groups)
        requested_by: Who asked you to stand down (contact name), if known
    """
    from services.heartbeat.triage_service import record_standdown

    try:
        await record_standdown(chat_id, requested_by=requested_by)
    except Exception as e:
        return f"Failed to record stand-down: {e}"
    return (
        f"Stood down in {chat_id}. You will not message this chat again until "
        "you are @edward-mentioned there or 24 hours pass; route anything "
        f"important to {owner_name()} via push."
    )


@tool
async def get_recent_messages(contact: Optional[str] = None, hours: int = 24) -> str:
    """
    Get recent iMessage conversations.

    Use this to check the user's recent messages or conversations with a
    specific contact.

    Args:
        contact: Optional - specific contact name or number to filter by
        hours: How many hours back to look (default 24)

    Returns:
        Formatted message history
    """
    from pathlib import Path as _Path

    from services.heartbeat import listener_imessage as _listener
    from services.heartbeat.listener_imessage import (
        CHAT_DB_PATH,
        get_recent_messages_across_chats,
        _resolve_contact_name,
    )
    from services.imessage_service import looks_like_imessage_handle

    # Distinguish "can't read the message store" from "nobody texted". Both come
    # back as an empty list, and conflating them is how a broken reader passes
    # for a quiet inbox.
    if not _Path(CHAT_DB_PATH).exists():
        return (
            "iMessage history unavailable — no readable chat.db at "
            f"{CHAT_DB_PATH}. This works on macOS with Messages.app configured "
            "and Full Disk Access granted."
        )

    # A name is a person: their phones and emails from the owner's contacts
    # (their 1:1, their Apple-ID chat, their lines in groups), plus the loose match that
    # finds a group by its name. A handle passes straight through.
    contact = (contact or "").strip() or None
    handles: list = []
    if contact and looks_like_imessage_handle(contact):
        handles = [contact]
    elif contact:
        try:
            from services import contacts_service

            # Never the AppleScript fallback here: with no readable store
            # (Full Disk Access lost) it woke Contacts.app for up to 30 s of
            # messages' 60 s; the loose match below still finds a group.
            found = await asyncio.to_thread(lambda: contacts_service.lookup_contact(contact, applescript=False))
        except Exception:
            found = None
        for person in (found or {}).get("matches") or []:
            handles += list(person.get("phones") or []) + list(person.get("emails") or [])

    try:
        messages = await asyncio.to_thread(
            lambda: get_recent_messages_across_chats(hours or 24, contact, 40, handles=handles)
        )
    except Exception as e:
        return f"Failed to get messages: {e}"

    if not messages:
        window = f"the last {hours or 24}h"
        if contact:
            return (
                f"No messages found for \"{contact}\" in {window}. "
                f"A name is looked up in {owner_name()}'s contacts and matched against group names; "
                f"a number or email against chats and senders — try one, or widen `hours`."
            )
        return f"No messages found in {window}."

    cache = _listener._contact_name_cache
    for msg in messages:
        for handle in (msg.get("sender"), None if msg.get("is_group") else msg.get("chat_identifier")):
            if handle and handle != "me" and handle not in cache:
                try:
                    await _resolve_contact_name(handle)
                except Exception:
                    pass

    from services.timezone_utils import utc_to_local

    # One block per chat, the chat with the newest message first; each
    # block's lines stay in time order (the reader hands them back that way).
    by_chat: dict = {}
    for msg in messages:
        by_chat.setdefault(msg.get("chat_identifier"), []).append(msg)
    ordered = sorted(by_chat.items(), key=lambda kv: max(m["timestamp"] for m in kv[1]), reverse=True)

    lines = [f"Recent messages ({len(messages)} in the last {hours or 24}h):"]
    for chat_key, chat_messages in ordered:
        label = chat_messages[0].get("chat_name") or cache.get(chat_key) or chat_key or "unknown chat"
        lines.append(f"\n[{label}] chat_id={chat_key}" if chat_key else f"\n[{label}]")
        for msg in chat_messages:
            who = "You" if msg["is_from_me"] else (cache.get(msg["sender"]) or msg["sender"])
            clock = utc_to_local(msg["timestamp"]).strftime("%b %-d %-I:%M %p")
            lines.append(f"  {clock} — {who}: {msg['text']}")
    return "\n".join(lines)


def _canonical_chat(given: str) -> str:
    """The chat's ``chat_identifier`` however it was spelled: chat.db's own
    answer (``chat_info`` takes ``chat123``, ``any;+;chat123``, a guid, a
    phone), else the id with its service prefix stripped (``any;+;``,
    ``iMessage;-;``, ``iMessage;+;``, ``SMS;-;``). The thread and roster
    queries match ``chat_identifier`` exactly."""
    from services import imessage_service

    text = str(given or "").strip()
    try:
        info = imessage_service.chat_info(text)
    except Exception:
        info = None
    if isinstance(info, dict) and info.get("chat_identifier"):
        return str(info["chat_identifier"])
    return imessage_service._tail(text)


@tool
async def read_imessage_thread(
    chat_identifier: str,
    limit: int = 50,
    before_rowid: Optional[int] = None,
) -> str:
    """Read further back into an iMessage chat thread.

    Use this when the auto-injected thread context in a heartbeat trigger
    isn't enough — e.g. a group chat has been active for hours and you
    need to scroll back to understand what people are referencing.

    Args:
        chat_identifier: The iMessage chat identifier (phone number for 1:1,
            chat-id GUID for groups). Available in the trigger's "Chat ID:"
            line or from prior tool calls.
        limit: How many recent messages to return (default 50). The pagination
            walks backward from ``before_rowid`` if provided, otherwise from
            the most recent message in the chat.
        before_rowid: Optional. Return only messages with ROWID strictly less
            than this value. Use the ``oldest_rowid=`` value from a previous
            call to scroll further back.

    Returns:
        A formatted thread block with chronological messages plus a footer
        line containing ``oldest_rowid=<N>`` / ``newest_rowid=<N>`` /
        ``returned=<N>`` so the next call can paginate.
    """
    import asyncio
    from services.heartbeat.listener_imessage import (
        get_chat_thread,
        format_chat_thread,
        _contact_name_cache,
        _resolve_contact_name,
    )

    if not chat_identifier:
        return "read_imessage_thread: chat_identifier is required"

    canonical = await asyncio.to_thread(_canonical_chat, chat_identifier)
    try:
        messages = await asyncio.to_thread(
            get_chat_thread, canonical, limit, before_rowid
        )
    except Exception as exc:
        return f"read_imessage_thread: failed to read chat — {exc}"

    if not messages:
        # Match get_chat_thread's notion of "a real bound" exactly. Using plain
        # truthiness here while the data layer used "is not None" is what hid the
        # before_rowid=0 bug: the query applied the bound, the message denied it.
        if before_rowid is not None and before_rowid > 0:
            return (
                f"read_imessage_thread: no messages found in {chat_identifier} "
                f"before rowid {before_rowid} (you've reached the start of the chat)."
            )
        return f"read_imessage_thread: no messages found for {chat_identifier}."

    # Resolve unknown contact names (best-effort) so the formatted output
    # carries human labels instead of raw phone numbers.
    for msg in messages:
        sender = msg.get("sender")
        if sender and sender != "me" and sender not in _contact_name_cache:
            try:
                await _resolve_contact_name(sender)
            except Exception:
                pass

    body = format_chat_thread(messages, contact_cache=_contact_name_cache)
    rowids = [m["rowid"] for m in messages if "rowid" in m]
    if rowids:
        footer = (
            f"\n[meta] returned={len(messages)} "
            f"oldest_rowid={min(rowids)} newest_rowid={max(rowids)}"
        )
    else:
        footer = f"\n[meta] returned={len(messages)}"
    return body + footer




@tool
async def list_chat_participants(chat_identifier: str) -> str:
    """List the full participant roster of an iMessage chat.

    Use this when you need to know *who's in* a group chat — not just
    who's spoken recently. Critical for poll-style workflows where you
    need to track which members haven't responded yet. Reads
    ``chat_handle_join`` for the roster and resolves each handle through
    Contacts.app to surface friendly names where saved.

    Args:
        chat_identifier: The iMessage chat identifier (phone number for 1:1,
            chat-id GUID for groups). Same value used by ``send_imessage``
            and ``read_imessage_thread``.

    Returns:
        Formatted participant list with resolved names where available
        and raw handles for anyone not in Contacts.
    """
    import asyncio
    from services.heartbeat.listener_imessage import get_chat_participants

    if not chat_identifier:
        return "list_chat_participants: chat_identifier is required"

    canonical = await asyncio.to_thread(_canonical_chat, chat_identifier)
    try:
        participants = await asyncio.to_thread(get_chat_participants, canonical)
    except Exception as exc:
        return f"list_chat_participants: failed to read chat — {exc}"

    if not participants:
        return (
            f"list_chat_participants: no participants found for {chat_identifier}. "
            f"(Either the chat doesn't exist or it's a 1:1 with only you.)"
        )

    # Resolve each handle (a phone or an Apple ID email) through the owner's
    # address book in a worker thread.
    def resolve(handle: str) -> Optional[str]:
        # Import inside the function and alias it. A module-level
        # "from services.contacts_service import lookup_phone" is rebound further
        # down this file by the @tool of the same name, which returns a formatted
        # string — resolving the bare name here crashed on .get() for every chat.
        from services.contacts_service import lookup_handle as contacts_lookup_handle

        try:
            result = contacts_lookup_handle(handle)
        except Exception:
            return None
        if not isinstance(result, dict) or not result.get("success"):
            return None
        matches = result.get("matches") or []
        if matches:
            return matches[0].get("name")
        return None

    resolved = await asyncio.gather(
        *(asyncio.to_thread(resolve, p["handle"]) for p in participants)
    )

    lines = [f"Participants in {canonical} ({len(participants)} total):"]
    for participant, name in zip(participants, resolved):
        handle = participant["handle"]
        if name:
            lines.append(f"  • {name} ({handle})")
        else:
            lines.append(f"  • {handle} (no Contacts match)")
    return "\n".join(lines)


@tool
async def send_whatsapp(phone_number: str, message: str) -> str:
    """
    Send a WhatsApp message via Twilio (from Edward's phone number).

    Use this when you need to message someone on WhatsApp as Edward.
    The recipient will see the message coming from Edward's WhatsApp number.

    Args:
        phone_number: The recipient's phone number (can be formatted like +1234567890 or 123-456-7890)
        message: The message content to send

    Returns:
        Confirmation message with status
    """
    from services.twilio_service import send_whatsapp as twilio_send_wa, is_configured
    from services.conversation_service import mark_user_notified

    if not is_configured():
        return "WhatsApp not available. Twilio is not configured."

    message = ensure_message_signature(message)

    # The send guards bind every send tool, this one too (2026-09-25).
    guard = await _send_guard(phone_number, message)
    if guard is not None:
        return guard

    try:
        result = await _send_and_note(phone_number, message, twilio_send_wa(phone_number, message))
        # Mark this conversation as having notified the user
        conversation_id = get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)
        return f"WhatsApp sent successfully to {result['to']} (Status: {result['status']})"
    except Exception as e:
        return f"Failed to send WhatsApp: {str(e)}"


@tool
async def send_message(
    recipient: str,
    message: str,
    channel: Optional[str] = None
) -> str:
    """
    Send a message via the best available channel.

    This is the preferred way to send messages. It will automatically choose
    the right channel based on context:
    - Default: Twilio SMS (sends as Edward), or WhatsApp if the contact last used WhatsApp
    - iMessage: When user explicitly requests or for personal contacts
    - WhatsApp: When user requests or contact last used WhatsApp

    Args:
        recipient: Phone number or contact name
        message: The message content to send
        channel: Optional - force a specific channel: 'twilio', 'whatsapp', 'imessage', or None (auto)

    Returns:
        Confirmation message with status
    """
    # Enforce signature before any routing
    message = ensure_message_signature(message)

    # A stand-down binds the tool; the same text twice is sent once.
    guard = await _send_guard(recipient, message)
    if guard is not None:
        return guard
    return await _send_and_note(recipient, message, _send_message_routed(recipient, message, channel),
                                delivered=looks_delivered)


async def _imessage_off_loop(send: Callable[..., Any], *args: Any) -> Any:
    """The iMessage transport blocks (osascript up to 30 s, then the chat.db
    read-back up to 5 s): run it in a worker thread, with the app loop noted
    so its ledger write can be scheduled back onto it."""
    from services import imessage_service as _imsg

    _imsg.note_main_loop(asyncio.get_running_loop())
    return await asyncio.to_thread(send, *args)


async def _send_message_routed(recipient: str, message: str, channel: Optional[str]) -> str:
    """``send_message``'s channel routing (the message is already signed)."""
    from services.twilio_service import (
        send_sms as twilio_send,
        send_whatsapp as twilio_send_wa,
        is_configured as twilio_configured,
    )
    from services.imessage_service import send_imessage as applescript_send, is_available as imessage_available
    from services.skills_service import is_skill_enabled
    from services.conversation_service import mark_user_notified

    # Helper to mark conversation as notified on successful send
    async def mark_notified():
        conversation_id = get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)

    # Check skill enabled states
    twilio_sms_enabled = await is_skill_enabled("twilio_sms")
    twilio_wa_enabled = await is_skill_enabled("twilio_whatsapp")
    imessage_enabled = await is_skill_enabled("imessage_applescript")

    # If channel explicitly specified, use it
    if channel == "imessage":
        if not imessage_enabled:
            return "iMessage is disabled. Enable it in Settings to use this feature."
        if not imessage_available():
            return "iMessage not available. Only works on macOS with Messages.app configured."
        try:
            result = await _imessage_off_loop(applescript_send, recipient, message)
            if result["success"]:
                await mark_notified()
                return f"iMessage sent to {recipient}"
            return f"Failed to send iMessage: {result.get('error', 'Unknown error')}"
        except Exception as e:
            return f"Failed to send iMessage: {str(e)}"

    if channel == "whatsapp":
        if not twilio_wa_enabled:
            return "WhatsApp is disabled. Enable it in Settings to use this feature."
        if not twilio_configured():
            return "WhatsApp not available. Twilio is not configured."
        try:
            result = await twilio_send_wa(recipient, message)
            await mark_notified()
            return f"WhatsApp sent to {result['to']} (Status: {result['status']})"
        except Exception as e:
            return f"Failed to send WhatsApp: {str(e)}"

    if channel == "twilio":
        if not twilio_sms_enabled:
            return "Twilio SMS is disabled. Enable it in Settings to use this feature."
        if not twilio_configured():
            return "SMS not available. Twilio is not configured."
        try:
            result = await twilio_send(recipient, message)
            await mark_notified()
            return f"SMS sent successfully to {result['to']} (Status: {result['status']})"
        except Exception as e:
            return f"Failed to send SMS: {str(e)}"

    # Auto-routing: Check if this contact has a last_channel preference
    auto_channel = await _get_contact_last_channel(recipient)

    if auto_channel == "whatsapp" and twilio_wa_enabled and twilio_configured():
        try:
            result = await twilio_send_wa(recipient, message)
            await mark_notified()
            return f"WhatsApp sent from Edward to {result['to']} (Status: {result['status']})"
        except Exception as e:
            # Fall through to SMS
            pass

    # Default to SMS via Twilio
    if twilio_sms_enabled and twilio_configured():
        try:
            result = await twilio_send(recipient, message)
            await mark_notified()
            return f"SMS sent from Edward to {result['to']} (Status: {result['status']})"
        except Exception as e:
            if imessage_enabled and imessage_available():
                try:
                    result = await _imessage_off_loop(applescript_send, recipient, message)
                    if result["success"]:
                        await mark_notified()
                        return f"Sent via iMessage (Twilio failed): iMessage sent to {recipient}"
                except Exception:
                    pass
            return f"Failed to send message: {str(e)}"

    # If Twilio not available/enabled, try iMessage
    if imessage_enabled and imessage_available():
        try:
            result = await _imessage_off_loop(applescript_send, recipient, message)
            if result["success"]:
                await mark_notified()
                return f"iMessage sent to {recipient}"
            return f"Failed to send iMessage: {result.get('error', 'Unknown error')}"
        except Exception as e:
            return f"Failed to send iMessage: {str(e)}"

    return "No messaging channels available. Enable Twilio SMS, WhatsApp, or iMessage in Settings."


async def _get_contact_last_channel(recipient: str) -> Optional[str]:
    """Look up the last_channel for a contact by phone number."""
    from services.twilio_service import normalize_phone_number
    from services.database import async_session, ExternalContactModel
    from sqlalchemy import select

    try:
        normalized = normalize_phone_number(recipient)
        async with async_session() as session:
            result = await session.execute(
                select(ExternalContactModel.last_channel).where(
                    ExternalContactModel.phone_number == normalized
                )
            )
            row = result.scalar_one_or_none()
            return row  # "sms", "whatsapp", or None
    except Exception:
        return None


# List of all messaging tools for binding to LLM
@tool
async def get_imessage_attachment(attachment_rowid: int) -> str:
    """
    Look at a photo or file someone sent over iMessage.

    Thread views and heartbeat events show attachments as
    "[image: IMG_1234.HEIC attachment:<rowid>]" — pass that rowid. Images are described
    for you (what they show, any readable text) and a JPEG copy is saved to
    file storage. PDFs and other files are named and located but not read.

    Args:
        attachment_rowid: the number after "attachment:" next to the attachment
    """
    from services import attachment_vision
    from services.heartbeat.listener_imessage import describe_attachment_kind, find_attachment

    att = find_attachment(attachment_rowid)
    if not att:
        return f"Attachment #{attachment_rowid} not found in Messages."
    kind = describe_attachment_kind(att.get("mime_type"))
    if kind != "image":
        return f"{kind} attachment '{att['name']}' ({att.get('mime_type') or 'unknown type'}) at {att['path'] or 'no path'}. I can only describe images."
    if not att.get("path") or not Path(att["path"]).is_file():
        return f"Image '{att['name']}' is not on this Mac yet (not downloaded from iCloud)."
    try:
        description = await attachment_vision.describe_image(att["path"], priority="interactive")
    except Exception as exc:  # noqa: BLE001
        return f"Could not read image '{att['name']}': {exc}"
    saved = None
    try:
        saved = await attachment_vision.store_attachment_copy(att["path"], description=description)
    except Exception as exc:  # noqa: BLE001
        print(f"[attachments] store copy failed: {exc}")
    out = f"Image '{att['name']}': {description}"
    if saved:
        out += f"\nSaved copy: file_id {saved['id']} ({saved['url']})"
    return out


MESSAGING_TOOLS = [send_sms, send_whatsapp, send_imessage, get_recent_messages, send_message, get_imessage_attachment]


# ============================================================================
# CONTACTS TOOLS
# ============================================================================

def _contact_lines(matches: list) -> list:
    """One block per person: name, nickname, organisation, phones, emails."""
    out = []
    for m in matches:
        out.append(f"- **{m['name']}**")
        for label, key in (("Nickname", "nickname"), ("Organization", "organization")):
            if m.get(key):
                out.append(f"  {label}: {m[key]}")
        if m.get("phones"):
            out.append(f"  Phones: {', '.join(m['phones'])}")
        if m.get("emails"):
            out.append(f"  Emails: {', '.join(m['emails'])}")
        out.append("")
    return out


@tool
@owner_doc
def lookup_contact(query: str) -> str:
    """
    Look up a contact in {OWNER}'s address book by name.

    Use this before sending messages to resolve nicknames, partial names,
    or informal names to phone numbers. Example: lookup_contact("rowan wren")
    finds "Rowan (Wren) Hale" with her phone number.

    Args:
        query: Name or partial name to search for (case-insensitive)

    Returns:
        JSON with matching contacts including names, phone numbers, and emails
    """
    from services.contacts_service import lookup_contact as contacts_lookup

    try:
        result = contacts_lookup(query)
        if result["success"]:
            matches = result.get("matches", [])
            if not matches:
                return f"No contacts found matching '{query}'."

            output = [f"Found {len(matches)} contact(s) matching '{query}':\n", *_contact_lines(matches)]
            return "\n".join(output)
        else:
            return f"Failed to search contacts: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to search contacts: {str(e)}"


@tool
def lookup_phone(phone_number: str) -> str:
    """
    Reverse lookup a contact by phone number.

    Use this when you have a phone number (e.g. from an iMessage sender) and need
    to find who it belongs to. Returns the contact name, phone numbers, and emails.

    Args:
        phone_number: Phone number to search for (any format, e.g. +15555550172)

    Returns:
        Matching contact info or 'no match' message
    """
    from services.contacts_service import lookup_phone as contacts_lookup_phone

    try:
        result = contacts_lookup_phone(phone_number)
        if result["success"]:
            matches = result.get("matches", [])
            if not matches:
                return f"No contacts found with phone number '{phone_number}'."

            output = [f"Found {len(matches)} contact(s) matching '{phone_number}':\n", *_contact_lines(matches)]
            return "\n".join(output)
        else:
            return f"Failed to search contacts: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to search contacts: {str(e)}"


# List of all contacts tools
CONTACTS_TOOLS = [lookup_contact, lookup_phone]


# ============================================================================
# THE TURN'S CONVERSATION (read by tools that need it)
# ============================================================================

# Context variable for current conversation_id — safe for concurrent workers
import contextvars
_current_conversation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    '_current_conversation_id', default=None
)


def set_current_conversation_id(conversation_id: str) -> None:
    """Set the current conversation ID for code execution context."""
    _current_conversation_id.set(conversation_id)


def get_current_conversation_id() -> Optional[str]:
    """Get the current conversation ID for code execution context."""
    return _current_conversation_id.get()


# ============================================================================
# SEARCH TOOLS
# ============================================================================

@tool
async def web_search(query: str, count: int = 5) -> str:
    """
    Search the web using Brave Search.

    Use this to find current information, news, or answers to questions
    that require up-to-date knowledge.

    Args:
        query: Search query string
        count: Number of results to return (1-20, default 5)

    Returns:
        Formatted search results with titles, URLs, and descriptions
    """
    from services.brave_search_service import search, is_configured

    if not is_configured():
        return "Web search not available. Brave Search API key is not configured."

    try:
        results = await search(query, count)

        if not results:
            return f"No results found for '{query}'."

        output = [f"Search results for '{query}':\n"]
        for i, result in enumerate(results, 1):
            output.append(f"{i}. **{result['title']}**")
            output.append(f"   URL: {result['url']}")
            output.append(f"   {result['description']}\n")

        return "\n".join(output)
    except Exception as e:
        return f"Search failed: {str(e)}"


@tool
async def fetch_page_content(url: str) -> str:
    """
    Fetch and extract the main content from a web page.

    Use this after web_search to get the full text of a promising result.
    Returns clean article text, stripped of navigation, ads, etc.

    Args:
        url: The URL to fetch content from

    Returns:
        Extracted text content from the page
    """
    from services.brave_search_service import fetch_page_content as fetch, is_configured

    if not is_configured():
        return "Page fetching not available. Brave Search API key is not configured."

    try:
        content = await fetch(url)
        return content
    except Exception as e:
        return f"Failed to fetch page content: {str(e)}"


# List of all search tools
SEARCH_TOOLS = [web_search, fetch_page_content]


# ============================================================================
# PLAN TOOLS
# ============================================================================

# Module-level plan state, keyed by conversation_id
_active_plans: Dict[str, List[dict]] = {}

# Tracks plan events emitted by tools so streaming layer can pick them up
_pending_plan_events: Dict[str, List[dict]] = {}


def get_active_plan(conversation_id: str) -> Optional[List[dict]]:
    """Get the active plan for a conversation."""
    return _active_plans.get(conversation_id)


def get_plan_status(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Get completion stats for the active plan.

    Returns None if no plan is active, otherwise a dict with:
        total, completed, pending, in_progress, error,
        incomplete_titles (list of step titles not yet completed)
    """
    plan = _active_plans.get(conversation_id)
    if not plan:
        return None

    by_status: Dict[str, int] = {"completed": 0, "pending": 0, "in_progress": 0, "error": 0}
    incomplete_titles: List[str] = []
    for step in plan:
        status = step.get("status", "pending")
        by_status[status] = by_status.get(status, 0) + 1
        if status != "completed":
            incomplete_titles.append(step["title"])

    return {
        "total": len(plan),
        "completed": by_status["completed"],
        "pending": by_status["pending"],
        "in_progress": by_status["in_progress"],
        "error": by_status["error"],
        "incomplete_titles": incomplete_titles,
    }


def get_pending_plan_events(conversation_id: str) -> List[dict]:
    """Get and clear pending plan events for a conversation."""
    events = _pending_plan_events.pop(conversation_id, [])
    return events


def _emit_plan_event(conversation_id: str, event: dict) -> None:
    """Queue a plan event for the streaming layer to pick up."""
    if conversation_id not in _pending_plan_events:
        _pending_plan_events[conversation_id] = []
    _pending_plan_events[conversation_id].append(event)


@tool
async def create_plan(steps: List[str]) -> str:
    """
    Create a task plan with ordered steps for a complex request.

    Use this when you receive a request that involves multiple distinct steps.
    The plan will be visible to the user and you should update each step as you
    work through it.

    Args:
        steps: List of step descriptions in order of execution

    Returns:
        Confirmation with step IDs
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan_steps = []
    for i, title in enumerate(steps, 1):
        step = {
            "id": f"step-{i}",
            "title": title,
            "status": "in_progress" if i == 1 else "pending",
            "result": None,
        }
        plan_steps.append(step)

    _active_plans[conversation_id] = plan_steps

    _emit_plan_event(conversation_id, {
        "event_type": "plan_created",
        "plan_steps": plan_steps,
    })

    step_list = "\n".join(f"  {s['id']}: {s['title']}" for s in plan_steps)
    return f"Plan created with {len(plan_steps)} steps:\n{step_list}\n\nFirst step is now in progress."


@tool
async def update_plan_step(step_id: str, status: str, result: Optional[str] = None) -> str:
    """
    Update the status of a plan step.

    Call this as you complete each step to show progress to the user.

    Args:
        step_id: The step ID (e.g. "step-1")
        status: New status - "completed", "error", or "in_progress"
        result: Optional brief summary of what was done/found

    Returns:
        Confirmation message
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan = _active_plans.get(conversation_id)
    if not plan:
        return "No active plan found."

    # Find and update the step
    step_found = False
    for step in plan:
        if step["id"] == step_id:
            step["status"] = status
            if result is not None:
                step["result"] = result
            step_found = True
            break

    if not step_found:
        return f"Step {step_id} not found in plan."

    _emit_plan_event(conversation_id, {
        "event_type": "plan_step_update",
        "step_id": step_id,
        "step_status": status,
        "step_result": result,
    })

    # Auto-advance: if completed, set next pending step to in_progress
    if status == "completed":
        for step in plan:
            if step["status"] == "pending":
                step["status"] = "in_progress"
                _emit_plan_event(conversation_id, {
                    "event_type": "plan_step_update",
                    "step_id": step["id"],
                    "step_status": "in_progress",
                    "step_result": None,
                })
                break

    return f"Step {step_id} updated to {status}."


@tool
async def edit_plan(
    add_steps: Optional[List[str]] = None,
    remove_step_ids: Optional[List[str]] = None
) -> str:
    """
    Edit the current plan by adding or removing steps.

    Use this to adapt the plan mid-execution when you discover new requirements
    or determine some steps aren't needed.

    Args:
        add_steps: New step descriptions to add at the end
        remove_step_ids: Step IDs to remove from the plan

    Returns:
        Updated plan summary
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan = _active_plans.get(conversation_id)
    if not plan:
        return "No active plan found."

    # Remove steps
    if remove_step_ids:
        plan[:] = [s for s in plan if s["id"] not in remove_step_ids]

    # Add new steps
    if add_steps:
        max_num = max((int(s["id"].split("-")[1]) for s in plan), default=0)
        for i, title in enumerate(add_steps, max_num + 1):
            plan.append({
                "id": f"step-{i}",
                "title": title,
                "status": "pending",
                "result": None,
            })

    _emit_plan_event(conversation_id, {
        "event_type": "plan_updated",
        "plan_steps": plan,
    })

    step_list = "\n".join(f"  {s['id']} [{s['status']}]: {s['title']}" for s in plan)
    return f"Plan updated ({len(plan)} steps):\n{step_list}"


@tool
async def complete_plan(summary: Optional[str] = None) -> str:
    """
    Mark the entire plan as complete.

    Call this when all steps are done to signal completion to the user.

    Args:
        summary: Optional overall summary of what was accomplished

    Returns:
        Confirmation message
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan = _active_plans.get(conversation_id)
    if not plan:
        return "No active plan found."

    # Reject if any steps were never started
    pending_steps = [s for s in plan if s["status"] == "pending"]
    if pending_steps:
        titles = ", ".join(s["title"] for s in pending_steps)
        return (
            f"Cannot complete plan: {len(pending_steps)} step(s) were never started: {titles}. "
            "Complete them first or use edit_plan(remove_step_ids=[...]) to remove steps that are no longer needed."
        )

    # Allow marking in_progress steps as completed (LLM likely just forgot to call update_plan_step)
    for step in plan:
        if step["status"] == "in_progress":
            step["status"] = "completed"

    _emit_plan_event(conversation_id, {
        "event_type": "plan_completed",
        "plan_steps": plan,
        "plan_summary": summary,
    })

    # Clean up
    del _active_plans[conversation_id]

    return f"Plan completed.{' Summary: ' + summary if summary else ''}"


# List of all plan tools
PLAN_TOOLS = [create_plan, update_plan_step, edit_plan, complete_plan]

PLAN_TOOL_NAMES = {"create_plan", "update_plan_step", "edit_plan", "complete_plan"}


_MEMORY_TOOLS_DESCRIPTION = owner_text("""
## Memory Management

You have long-term memory tools — use them aggressively:

1. **remember_search(query, memory_type?)**: Search your memories.
   - ALWAYS search before saying "I don't know" or "I don't remember" about anything personal.
   - The memories in your context above are just the top few — more may exist. Search when in doubt.
   - If results are poor, try different phrasings, synonyms, or related terms.
   - Filter by type: 'fact' (personal info), 'preference' (likes/dislikes), 'context' (situations), 'instruction' (rules).
   - Multiple searches in one turn are fine and encouraged.

2. **remember_update(memory_id, new_content?, valid_until?)**: Update a memory with corrected info,
   or when it stops applying (ISO; no zone = {OWNER}'s local time; now or earlier retires it).
   **close_intention(memory_id, outcome)**: close one of your intentions — done, dropped or moot —
   keeping the row and what came of it. It leaves "Open intentions" and research.

3. **remember_forget(memory_id)**: Delete a memory when asked to forget something.

4. **update_memory_confidence(memory_id, new_confidence, reason?)**: Lower confidence on a memory
   when the user contradicts it, rather than deleting. Retrieval scores are multiplied by confidence,
   so a low-confidence memory fades without being erased. Values clamped to [0, 1].

5. **graph_memory_search(query, limit?)**: Knowledge-graph search (Graphiti + Kuzu) for
   multi-hop reasoning and temporal questions ("who does X know through Y", "what was true
   about Z last month"). pgvector is still primary for dense similarity — use this when
   `remember_search` feels flat. Returns facts with validity windows (valid_at → invalid_at).
   Returns "(graph memory disabled)" if the layer isn't enabled.

6. **remember(content, memory_type?, about?, origin?, importance?, valid_until?)**: Write a memory
   of your own. What {OWNER} tells you is extracted automatically after the turn; this is for what
   YOU decided, noticed, or did. When you decide to do or watch something, remember it as an
   `intention` (about="self", origin="thought", with a `valid_until` horizon —
   ISO; no zone = {OWNER}'s local time) so your next wake knows what you are up to and why.
   A lesson from a thread is `context` about="self". Something
   you read (a price, a fact from a page) is origin="read", about="world" or "contact". An action
   and its outcome is origin="did". The same thought remembered again reinforces the old one
   instead of duplicating it — that is how an observation becomes a belief.

Memory IDs appear in your context as (memory_id: xxx) — use them for updates/deletes/confidence changes.
Each line shows [origin/about] when it is not the default heard/owner: [thought/self] is your own.
""")


def get_memory_tools_description() -> str:
    """Get a description of available memory tools for the system prompt."""
    return _MEMORY_TOOLS_DESCRIPTION


_CONTACTS_TOOLS_DESCRIPTION = owner_text("""
## Contacts Lookup

You have access to tools for looking up contacts:

1. **lookup_contact(query)**: Search {OWNER}'s address book by name.
   - Use before sending messages to resolve nicknames or partial names
   - Returns matching contacts with phone numbers and emails
   - Case-insensitive search
   - Example: lookup_contact("rowan wren") finds "Rowan (Wren) Hale"

2. **lookup_phone(phone_number)**: Reverse lookup a contact by phone number.
   - Use when you have a phone number and need to identify the person
   - Accepts any format (e.g. +15555550172, (555) 555-0172)
   - Returns the contact name, phone numbers, and emails

Best practices:
- Always look up contacts before messaging if you only have a nickname or partial name
- Use lookup_phone when you see a phone number in heartbeat events or messages
- Use the phone number from the lookup result, not the contact name, for sending messages
- If multiple matches, ask the user which contact they meant
""")


def get_contacts_tools_description() -> str:
    """Get a description of available contacts tools for the system prompt."""
    return _CONTACTS_TOOLS_DESCRIPTION


def get_messaging_tools_description() -> str:
    """Get a description of available messaging tools for the system prompt."""
    return """
## Messaging

You have access to tools for sending messages:

1. **send_message(recipient, message, channel?)**: The preferred way to send messages.
   - Default: Sends SMS from your phone number (as Edward), or WhatsApp if the contact last used WhatsApp
   - Set channel='whatsapp' to force WhatsApp
   - Set channel='imessage' to send as the user
   - Use this for most messaging tasks

2. **send_sms(phone_number, message)**: Send SMS from Edward's phone number.
   - Recipient sees message from Edward
   - Good for autonomous communications

3. **send_whatsapp(phone_number, message)**: Send WhatsApp from Edward's number.
   - Recipient sees message from Edward on WhatsApp
   - Use when WhatsApp is preferred or contact last used WhatsApp

4. **send_imessage(contact, message)**: Send iMessage as the user.
   - Only use when user explicitly requests to send as themselves
   - Good for personal contacts

5. **get_recent_messages(contact?, hours?)**: Check recent iMessage conversations.
   - View the user's message history
   - Filter by contact or time range

Channel selection guidelines:
- Default to Twilio (your number) for most messages
- send_message auto-detects: if a contact last messaged via WhatsApp, replies go via WhatsApp
- Use WhatsApp when:
  * Contact previously messaged via WhatsApp
  * User explicitly requests WhatsApp
- Use iMessage when:
  * User explicitly says "send as me" or "from my number"
  * Messaging known personal contacts (family, close friends)
  * User has established preference for iMessage with that contact
"""


_WEB_TOOLS_DESCRIPTION = owner_text("""
## The web

Three layers, cheapest first. Pick the lightest one that answers the question.

**1. Look things up — no browser involved.**
- `web_search(query, count?)`: Brave Search. Titles, URLs, snippets. Current events, facts, docs; several queries in parallel are fine.
- `fetch_page_content(url)`: the readable text of one page (articles, docs, listings; about 4,000 characters). Use it after a search to read a promising result. It cannot log in, click, or run a site's JavaScript — when it comes back empty, the page needs the browser.

**2. Act in {OWNER}'s Chrome — their browser, their logins, on this Mac's monitor.**
Everything behind a login, anything that needs clicking, and anything they want *on the screen* (a video, a stream, a live page) happens here, through the Edward extension. There is no other browser.
- `browser_tabs()`: what is open; `browser_use_tab(id)`: take over a tab (one {OWNER} logged in to for you).
- `browser_open(url, fullscreen?, new_tab?)`: open a page in front as your working tab; `fullscreen=True` fills the monitor. It returns a first read of the page.
- `browser_read()`: the page as a screen reader would give it — text plus numbered links, buttons and fields. `browser_find(word)`: just the matching elements.
- `browser_click(ref)`, `browser_type(text, ref?, submit?)`, `browser_key(key)`, `browser_scroll(dir, amount)`, `browser_back()`: act on the working tab. Each waits for the page to settle and tells you where it is. Numbers reset when the page changes — read or find again before clicking.
- `browser_wait(text?, url_contains?, seconds?)`: wait for an outcome instead of sleeping.
- `browser_steps([...])`: several of the above in one call, stopping at the first failure — use it for flows you can predict.
- `browser_fullscreen(on)`: Chrome's full screen for the tab's window. `browser_done(close_tab?)`: leave full screen, let go, bring your /screen tab back.
- `look_at_screen(at="page"|"display")`: a screenshot described in words — the tab (consent walls, players, images the text misses) or the whole monitor.

Putting something on your screen: `browser_open("https://www.youtube.com/results?search_query=…")` → `browser_find("…")` → `browser_click(n)` → `browser_fullscreen(True)` (or one `browser_steps` call), then `look_at_screen("display")` to confirm it is really playing. `browser_done()` when {OWNER} says "back to your screen".

Being in their browser: you act in {OWNER}'s name. Say what you are about to do before irreversible steps (a purchase, a cancellation, sending something) and check the page after. Never type their passwords or card numbers — ask them to log in, then take the tab over. Everything you do there is logged.

**3. Your own surfaces.** `show_on_screen` / `compose_screen` are for things you make (a note, an alert, a picture, a scene) — not for websites.
""")


def get_web_tools_description() -> str:
    """The web, in one story: search and read (Brave), then act (the owner's Chrome)."""
    return _WEB_TOOLS_DESCRIPTION


def get_plan_tools_description() -> str:
    """Get a description of available plan tools for the system prompt."""
    return """
## Task Planning

**You MUST create a plan whenever a request will require 3+ tool calls or involves multiple distinct steps. This is mandatory, not optional.** Err on the side of creating a plan — users appreciate seeing structured progress, and you get extra tool iterations to complete planned work.

**When to plan:**
- Research tasks (searching, reading, summarizing)
- Multi-step workflows (find something → save it → schedule a reminder)
- Requests involving multiple tools (web search + document save + message send)
- Requests with multiple parts ("do X, Y, and Z")
- Build/create tasks (websites, documents, analyses)
- Any task where you might need more than a couple of tool calls

**When NOT to plan:**
- Simple single-turn questions or lookups
- One-tool-call tasks (e.g. "what time is it", "remember that I like tea")

**Completion discipline:** Always call `complete_plan` when done — never leave a plan hanging. If a step fails, mark it as "error" with a result note and continue with the remaining steps.

1. **create_plan(steps)**: Create a visible task plan with ordered steps.
   - First step is automatically set to in_progress

2. **update_plan_step(step_id, status, result?)**: Update a step's status.
   - Mark steps as "completed", "error", or "in_progress"
   - Optionally attach a brief result summary
   - Next pending step auto-advances to in_progress on completion

3. **edit_plan(add_steps?, remove_step_ids?)**: Modify the plan mid-execution.
   - Add new steps or remove unnecessary ones
   - Useful when scope changes during execution

4. **complete_plan(summary?)**: Mark the entire plan as finished.
   - Call when all work is done
   - Optional summary of what was accomplished

**Example:** "Build a website showing my DB data and host it"
→ FIRST call create_plan(["Query database for data", "Build HTML page with results", "Publish the page", "Share link with user"])
→ Then execute each step, updating the plan as you go.
"""


# ============================================================================
# SCHEDULED EVENT TOOLS
# ============================================================================

@tool
@owner_doc
async def schedule_event(
    description: str,
    scheduled_at: str,
    recurrence_pattern: Optional[str] = None,
    delivery_channel: Optional[str] = None,
    why: Optional[str] = None,
) -> str:
    """
    Schedule a future event, reminder, or action.

    Use this when the user asks you to:
    - Set a reminder for a specific time
    - Schedule a message to be sent later
    - Create a recurring task or check-in
    - Do something at a future date/time

    **Also schedule events for yourself, unprompted, when you anticipate
    a need.** Anticipating is part of the job. You don't need permission
    to book yourself a reminder, check-in, or follow-up — just do it.
    Examples:
    - User mentions following up with someone later → schedule a check-in
      24-72h out that fires a tool-call to draft a nudge.
    - User asks about a topic with a deadline → schedule a reminder
      shortly before the deadline.
    - After a long planning session → schedule a "morning recap" for
      the next 8am.
    - A heartbeat event was low-urgency but might still matter in a day
      → schedule a follow-up check.

    The event will fire automatically at the scheduled time. Edward will
    receive the event description and execute the described action.

    Args:
        description: What Edward should do when the event fires. Be specific
            and include concrete details like contact names/numbers.
            (e.g. "Send Parker at +15551234567 a text reminding him about his dentist appointment"
            or "Tell the user it's time to take a break").
        scheduled_at: ISO 8601 datetime in the USER'S LOCAL TIME
            (e.g. "2025-01-15T14:30:00"). Do NOT convert to UTC — the system
            handles that automatically. Just use the same timezone as the
            current date/time shown in your context.
        recurrence_pattern: Optional cron pattern for recurring events
            (e.g. "0 9 * * *" for daily at 9 AM, "*/5 * * * *" for every 5 min).
            Cron times are also in the user's local timezone.
            Leave empty for one-time events.
        delivery_channel: Optional preferred channel: "sms", "imessage", "chat", "alexa", or null.
            Use "sms" or "imessage" if the action involves sending a message.
            Use "alexa" to announce on Echo devices via Home Assistant.
            Use "chat" for in-app only (no external message). Defaults to null
            (auto-infer from description).
        why: Why you are doing this, in a sentence (e.g. "{OWNER}'s bonus depends
            on Acme stock and they were worried about the downgrade"). When
            given, it is remembered as an intention of yours and handed back
            to you every time the event fires; cancelling the event retires
            it. Give it whenever the event is your own idea.

    Returns:
        Confirmation with event details
    """
    from services.scheduled_events_service import create_event, intention_valid_until
    from services.timezone_utils import local_naive_to_utc
    from datetime import datetime, timezone

    conversation_id = get_current_conversation_id()

    try:
        # Parse the datetime
        dt = datetime.fromisoformat(scheduled_at)
        # A naive time is the user's local time (the zone in settings, not
        # the host's) — see timezone_utils.local_naive_to_utc.
        dt = local_naive_to_utc(dt)
    except ValueError:
        return f"Invalid datetime format: {scheduled_at}. Use ISO 8601 format (e.g. 2025-01-15T14:30:00)."

    memory_id = None
    reason = " ".join((why or "").split())
    if reason:
        try:
            from services.memory_service import Memory, store_memories_with_conflict_detection
            from services.provenance_service import turn_source_ref

            naive_at = dt.astimezone(timezone.utc).replace(tzinfo=None)
            stored = await store_memories_with_conflict_detection([Memory(
                id=None,
                content=f"{reason} — scheduled: {description.strip()[:160]}",
                memory_type="intention",
                importance=0.7,
                temporal_nature="evolving",
                source_type="inferred",
                confidence=0.7,
                source_conversation_id=conversation_id,
                valid_until=intention_valid_until(recurrence_pattern, naive_at, datetime.utcnow()),
                origin="thought",
                about="self",
                source_ref=turn_source_ref("schedule_event", conversation_id=conversation_id),
            )])
            memory_id = stored[0].id if stored else None
        except Exception as exc:  # noqa: BLE001
            print(f"[schedule_event] could not remember the intention: {exc}")

    try:
        event = await create_event(
            description=description,
            scheduled_at=dt,
            recurrence_pattern=recurrence_pattern,
            conversation_id=conversation_id,
            delivery_channel=delivery_channel,
            created_by="edward",
            memory_id=memory_id,
        )
    except ValueError as e:
        return f"Error: {str(e)}"

    recurrence_info = f" (recurring: {recurrence_pattern})" if recurrence_pattern else " (one-time)"
    from services.scheduled_events_service import _format_local
    fire_time = _format_local(event.next_fire_at) if event.next_fire_at else scheduled_at
    return f"Event scheduled{recurrence_info}. Next fire: {fire_time}. ID: {event.id}"


@tool
async def list_scheduled_events(status: Optional[str] = None) -> str:
    """
    List scheduled events.

    Use this when the user asks about their reminders, scheduled events,
    or upcoming tasks.

    Args:
        status: Optional filter: "pending", "completed", "cancelled", "failed".
            Defaults to showing pending events.

    Returns:
        Formatted list of events
    """
    from services.scheduled_events_service import list_events

    events = await list_events(
        status=status,  # None = show all statuses (user can filter explicitly)
        conversation_id=None,  # Show events from ALL conversations
        limit=20,
    )

    if not events:
        filter_desc = f" with status '{status}'" if status else ""
        return f"No scheduled events found{filter_desc}."

    lines = [f"Found {len(events)} event(s):\n"]
    for e in events:
        from services.scheduled_events_service import _format_local
        fire_time = _format_local(e.next_fire_at, '%b %d, %Y %I:%M %p') if e.next_fire_at else "N/A"
        recurrence = f" [recurring: {e.recurrence_pattern}]" if e.recurrence_pattern else ""
        lines.append(f"- [{e.status}] {e.description} — fires: {fire_time}{recurrence} (ID: {e.id})")

    return "\n".join(lines)


@tool
async def cancel_scheduled_event(event_id: str) -> str:
    """
    Cancel a scheduled event.

    Use this when the user wants to cancel a reminder or scheduled action.

    Args:
        event_id: The ID of the event to cancel

    Returns:
        Confirmation message
    """
    from services.scheduled_events_service import cancel_event

    success = await cancel_event(event_id)
    if success:
        return f"Event {event_id} cancelled."
    return f"Could not cancel event {event_id}. It may already be completed or cancelled."


# List of all scheduled event tools
SCHEDULED_EVENT_TOOLS = [schedule_event, list_scheduled_events, cancel_scheduled_event]

SCHEDULED_EVENT_TOOL_NAMES = {"schedule_event", "list_scheduled_events", "cancel_scheduled_event"}


# =============================================================================
# Heartbeat tools
# =============================================================================

def _format_heartbeat_event_line(event) -> str:
    """Render one heartbeat event for the briefing, in the user's local time.

    heartbeat_events.created_at is naive UTC (the pool pins timezone: UTC — see
    services/database.py). Formatting it with a bare strftime published every
    event at its UTC clock time, so a message the owner sent at 11:22 PM showed up in
    the briefing as "Aug 22, 04:22 AM". Always route through utc_to_local.
    """
    from services.timezone_utils import utc_to_local

    created = getattr(event, "created_at", None)
    time_str = utc_to_local(created).strftime("%b %d, %I:%M %p") if created else "unknown"
    direction = "outgoing" if event.is_from_user else "incoming"
    sender_str = event.sender or "unknown"
    summary_str = event.summary or "(no summary)"
    chat_str = f" in {event.chat_name}" if event.chat_name else ""
    return (
        f"- [{event.triage_status}] {time_str} — {direction} "
        f"from {sender_str}{chat_str}: {summary_str}"
    )


@tool
async def review_heartbeat(
    sender: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 15,
) -> str:
    """
    Review recent messages and events you've noticed via your heartbeat system.

    Use this when the user asks what you've noticed, what messages came in,
    or wants to know about recent activity from contacts.

    Args:
        sender: Optional — filter by sender phone/email (partial match)
        status_filter: Optional — "pending", "dismissed", "noted", "acted", "escalated"
        limit: Max events to return (default 15)

    Returns:
        Formatted list of recent heartbeat events
    """
    from services.database import HeartbeatEventModel, async_session
    from sqlalchemy import select, desc

    async with async_session() as session:
        query = select(HeartbeatEventModel).order_by(desc(HeartbeatEventModel.created_at)).limit(limit)

        if sender:
            query = query.where(HeartbeatEventModel.sender.ilike(f"%{sender}%"))
        if status_filter:
            query = query.where(HeartbeatEventModel.triage_status == status_filter)

        result = await session.execute(query)
        events = result.scalars().all()

    if not events:
        filter_desc = ""
        if sender:
            filter_desc += f" from '{sender}'"
        if status_filter:
            filter_desc += f" with status '{status_filter}'"
        return f"No heartbeat events found{filter_desc}."

    lines = [f"Found {len(events)} recent event(s):\n"]
    lines.extend(_format_heartbeat_event_line(e) for e in events)
    return "\n".join(lines)


HEARTBEAT_TOOLS = [review_heartbeat]

HEARTBEAT_TOOL_NAMES = {"review_heartbeat"}


def get_heartbeat_tools_description() -> str:
    """Get a description of available heartbeat tools for the system prompt."""
    return """
## Heartbeat (Background Awareness)

You have a heartbeat system that passively monitors incoming and outgoing iMessages.
Messages are triaged automatically (dismiss/note/act/escalate).

1. **review_heartbeat(sender?, status_filter?, limit?)**: Review recent messages you've noticed.
   - Filter by sender (partial match on phone/email)
   - Filter by triage status: "pending", "dismissed", "noted", "acted", "escalated"
   - Use this when the user asks what you've noticed or about recent messages from someone
"""


def get_scheduled_event_tools_description() -> str:
    """Get a description of available scheduled event tools for the system prompt."""
    return """
## Scheduled Events

You can schedule future actions, reminders, and recurring tasks:

1. **schedule_event(description, scheduled_at, recurrence_pattern?, delivery_channel?)**:
   Schedule an action for a future time.
   - `description`: What you should do when it fires (be specific!)
   - `scheduled_at`: ISO 8601 datetime
   - `recurrence_pattern`: Cron string for recurring (e.g. "0 9 * * *" = daily 9 AM)
   - `delivery_channel`: "sms", "imessage", "alexa" (Echo announcement), or "chat" (in-app only). Leave null to auto-infer.

2. **list_scheduled_events(status?)**: Show upcoming events.
   - Filter by status: "pending", "completed", "cancelled", "failed"

3. **cancel_scheduled_event(event_id)**: Cancel an event by ID.

**Events fire where they were made.** A fired event runs in the thread that scheduled it (or in your
mind thread when that thread is gone or was a contact's), with your earlier turns there, how the event
went last time, and — when you gave a `why` — the intention behind it. Give `why` whenever the event is
your own idea: it is remembered as an intention, handed back on every fire, and retired when you cancel.

Still write the description so it stands on its own — the thread window is short and the fire may be
weeks away:

✅ GOOD: "Send push notification asking 'Did you give Biscuit her morning meds?' When user replies YES,
   log to pet_meds database, medication_log table: INSERT with medication_id=1,
   administered_at=current timestamp, dose_given='1 tablet', time_of_day='morning'."

❌ BAD: "Ask about Biscuit's meds and track the response."

**Include in EVERY event description:**
- The exact notification/message text to send
- The exact database name and table to log responses to
- The exact column names and values to INSERT
- Any calculations (e.g., "Day X = (today - 2026-02-04) + 1")
- Phone numbers, not just contact names

**Multi-action events:** If an event needs to: 1) send push notification, 2) wait for reply,
3) log to database — write ALL THREE actions explicitly in the description.

Tips:
- Use the user's LOCAL time for scheduled_at — do NOT convert to UTC
- For recurring events, use standard cron syntax (minute hour day month weekday)
- When a fired event is no longer worth doing, cancel it (cancel_scheduled_event) and say why
"""


# ============================================================================
# PUSH NOTIFICATION TOOLS
# ============================================================================

def _reach_routes_this_turn() -> bool:
    """Is this a wake (a ``mind:*`` purpose), the speaker (a speak scope) or
    the household hub's voice (``hub_voice``, which may not text the owner)?
    Then a note or a push to the owner goes through
    ``reach_service.tell_owner`` — the origin rules, quiet hours, the lint,
    one follow-up, the nightly's one note, the gate's ceiling (2026-10-11:
    before the v2 toolset's ``tell_owner`` was bound, these legacy tools
    were how a wake reached the owner, and they bypassed all of it).
    Anywhere else: as before."""
    try:
        from services import reach_service

        if reach_service.current_scope() is not None:
            return True
    except Exception:  # noqa: BLE001
        return False
    turn = _current_turn()
    if turn is not None and (getattr(turn, "source", "") or "") == HUB_VOICE_SOURCE:
        return True
    try:
        from services.call_purpose import current_purpose

        return str(current_purpose() or "").startswith("mind:")
    except Exception:  # noqa: BLE001
        return False


@tool
@owner_doc
async def send_push_notification(
    title: str,
    body: str,
    url: Optional[str] = None,
    urgency: str = "normal",
) -> str:
    """
    Send a push notification to the user's devices.

    Use this when you need to proactively alert the user about something
    important, even when they're not actively using the app. Push notifications
    appear on the user's phone or computer.

    Best used for:
    - Urgent alerts that need immediate attention
    - Reminders when the user might not be checking the app
    - Important status updates

    Args:
        title: Short notification title (keep under 50 chars)
        body: Notification body text (keep under 100 chars for best display)
        url: Optional URL to open when notification is clicked
        urgency: "normal" (default) sends straight to the phone. "high" goes
            through the interrupt ladder: the screen {OWNER} is at first, then
            the phone, then a text to themselves if nothing is acknowledged.
            Reserve it for things that cannot wait.

    Returns:
        Confirmation with delivery status
    """
    if _reach_routes_this_turn():
        from services import reach_service

        return await reach_service.tell_owner(body or title, title=title, urgent=True, link=url)

    from services.push_service import send_push_notification as send_push, is_configured
    from services.conversation_service import mark_user_notified
    from services.push_safety_gate import classify_high_risk_push_label_text

    if not is_configured():
        return "Push notifications not configured. VAPID keys are not set."

    # Safety, not taste: a push whose text carries self-harm or extreme
    # violence content is refused here; the mind is told to reply or note.
    risk = classify_high_risk_push_label_text(f"{title} {body}")
    if risk:
        return (f"Tool error: this push would carry high-risk content ({risk}); a phone banner is the wrong "
                f"place for it. Reply directly to the person, or leave {owner_name()} a note, instead.")

    # Where the tap goes. A push from a wake opens the journal entry that wake
    # is writing — not its raw thread of packs — and opening the entry acks
    # it (thought journal, 2026-09-25). Anywhere else: the current
    # conversation when no URL was given.
    from services import thought_journal

    thread_id = get_current_conversation_id()
    entry_id = thought_journal.current_entry_id()
    conversation_id = entry_id or thread_id
    url = thought_journal.entry_url(url, thread_id, entry_id)
    if not url and conversation_id:
        url = f"/chat?c={conversation_id}"

    def _linked(result: str) -> str:
        if entry_id:
            thought_journal.note_linked("send_push_notification",
                                        {"title": title, "body": body, "urgency": urgency}, result)
        return result

    if (urgency or "normal").lower() == "high":
        try:
            import uuid as _uuid
            from services import interrupt_service
            from services.interrupt_logic import Interrupt
            await interrupt_service.deliver(Interrupt(
                id=str(_uuid.uuid4()), title=title, body=body, url=url or "/chat", conversation_id=conversation_id,
                event_ids=(), urgency="high", tag="edward-interrupt",
            ))
            if conversation_id:
                try:
                    await mark_user_notified(conversation_id)
                except Exception:  # noqa: BLE001
                    pass
            return _linked(f"Interrupt started: the screen {owner_name()} is at first, then their phone, "
                           "then a text if nothing is acknowledged.")
        except Exception as exc:  # noqa: BLE001
            print(f"[push tool] interrupt ladder failed, sending a plain push: {exc}")

    try:
        result = await send_push(
            title=title,
            body=body,
            url=url or "/chat",
            tag="edward-proactive",
            source="tool",
            conversation_id=conversation_id,
        )

        if result.get("error"):
            return f"Push notification failed: {result['error']}"

        sent = result.get("sent", 0)
        failed = result.get("failed", 0)
        total = result.get("total", 0)

        if total == 0:
            return "No active push subscriptions. User has not enabled notifications."

        if sent > 0:
            # Mark this conversation as having notified the user (for filtering)
            if conversation_id:
                await mark_user_notified(conversation_id)
            return _linked(f"Push notification sent to {sent} device(s).")
        else:
            return f"Push notification failed to deliver to {failed} device(s)."

    except Exception as e:
        return f"Push notification error: {str(e)}"


# List of all push notification tools
PUSH_NOTIFICATION_TOOLS = [send_push_notification]


def get_push_notification_tools_description() -> str:
    """Get a description of available push notification tools for the system prompt."""
    return """
## Push Notifications

You can send push notifications to the user's devices:

1. **send_push_notification(title, body, url?)**: Send a push notification.
   - `title`: Short notification title (under 50 chars)
   - `body`: Notification body (under 100 chars)
   - `url`: Optional URL to open on click

Tips:
- Use sparingly — only for important alerts that need immediate attention
- Keep messages short and actionable
- Good for: urgent reminders, status updates, important alerts
- Works even when the user isn't actively using the app
- Requires user to have enabled notifications in the PWA
"""


# ============================================================================
# DOCUMENT STORE TOOLS
# ============================================================================

@tool
async def save_document(title: str, content: str, tags: Optional[str] = None) -> str:
    """
    Save a new document to the persistent document store.

    Use this when the user wants to store a full document for long-term reference,
    such as recipes, meeting notes, reference guides, pet records, instructions,
    or any text that's too large or structured for a simple memory.

    Args:
        title: A descriptive title for the document
        content: The full document content (markdown supported)
        tags: Optional comma-separated tags for categorization (e.g. "recipe,cooking,italian")

    Returns:
        Confirmation with the document ID
    """
    from services.document_service import save_document as _save, Document

    conversation_id = get_current_conversation_id()

    doc = Document(
        id=None,
        title=title,
        content=content,
        tags=tags,
        source_conversation_id=conversation_id,
    )

    saved = await _save(doc)
    tag_info = f" (tags: {tags})" if tags else ""
    return f"Document saved: \"{saved.title}\"{tag_info} (ID: {saved.id})"


@tool
async def read_document(document_id: str) -> str:
    """
    Read the full content of a document from the store.

    Use this when you see a relevant document title in your context and need
    to read the full content. Also use when the user asks to see a stored document.

    Args:
        document_id: The document ID

    Returns:
        Full document content with title and tags
    """
    from services.document_service import get_document_by_id

    doc = await get_document_by_id(document_id)
    if not doc:
        return f"Document {document_id} not found."

    parts = [f"# {doc.title}"]
    if doc.tags:
        parts.append(f"Tags: {doc.tags}")
    parts.append("")
    parts.append(doc.content)
    return "\n".join(parts)


@tool
async def edit_document(
    document_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """
    Edit an existing document. Only provide fields you want to change.

    Use this when the user wants to update a stored document's content, title, or tags.

    Args:
        document_id: The document ID to edit
        title: New title (optional)
        content: New content (optional)
        tags: New tags, comma-separated (optional)

    Returns:
        Confirmation message
    """
    from services.document_service import update_document

    updated = await update_document(
        document_id=document_id,
        title=title,
        content=content,
        tags=tags,
    )

    if not updated:
        return f"Document {document_id} not found."

    return f"Document updated: \"{updated.title}\" (ID: {updated.id})"


@tool
async def search_documents(query: str, tags: Optional[str] = None) -> str:
    """
    Search for documents by semantic similarity and keywords.

    Use this when looking for stored documents on a topic, or when the user
    asks about documents they've saved.

    Args:
        query: Search query describing what to find
        tags: Optional comma-separated tags to filter by

    Returns:
        List of matching documents with titles and IDs
    """
    from services.document_service import search_documents as _search

    docs, total = await _search(query=query, tags=tags, limit=10)

    if not docs:
        return f"No documents found matching '{query}'."

    lines = [f"Found {total} document(s) matching '{query}':\n"]
    for d in docs:
        tag_info = f" [{d.tags}]" if d.tags else ""
        score_info = f" (score: {d.score:.2f})" if d.score > 0 else ""
        preview = d.content[:100].replace("\n", " ") + ("..." if len(d.content) > 100 else "")
        lines.append(f"- **{d.title}**{tag_info}{score_info} (ID: {d.id})")
        lines.append(f"  {preview}")

    return "\n".join(lines)


@tool
async def list_documents(tags: Optional[str] = None) -> str:
    """
    List all documents in the store.

    Use this when the user wants to see what documents are saved,
    or to browse by tag.

    Args:
        tags: Optional comma-separated tags to filter by

    Returns:
        List of all documents with titles, tags, and IDs
    """
    from services.document_service import list_documents as _list

    docs, total = await _list(limit=20, tags=tags)

    if not docs:
        filter_info = f" with tags '{tags}'" if tags else ""
        return f"No documents found{filter_info}."

    lines = [f"Found {total} document(s):\n"]
    for d in docs:
        tag_info = f" [{d.tags}]" if d.tags else ""
        date_info = d.updated_at.strftime("%b %d, %Y") if d.updated_at else "N/A"
        lines.append(f"- **{d.title}**{tag_info} — updated {date_info} (ID: {d.id})")

    return "\n".join(lines)


@tool
async def delete_document(document_id: str) -> str:
    """
    Delete a document from the store.

    Use this when the user explicitly asks to remove a stored document.

    Args:
        document_id: The document ID to delete

    Returns:
        Confirmation message
    """
    from services.document_service import delete_document as _delete

    deleted = await _delete(document_id)
    if deleted:
        # A goal document used to take its check-in wakes with it; goals and
        # goals_service went on 2026-10-11.
        return f"Document {document_id} deleted."
    return f"Document {document_id} not found."


# List of all document tools
DOCUMENT_TOOLS = [save_document, read_document, edit_document, search_documents, list_documents, delete_document]

DOCUMENT_TOOL_NAMES = {"save_document", "read_document", "edit_document", "search_documents", "list_documents", "delete_document"}


# Tightened 2026-09-24 (same facts, fewer words) to make room under the
# 20K prompt floor for the goals / provenance / forget / ideas section.
_DOCUMENT_TOOLS_DESCRIPTION = owner_text("""
## Document Store

Documents keep complete text {OWNER} wants stored (recipes, meeting notes, guides, pet records); memories are short auto-extracted snippets. Only titles show in your context (under "Relevant Documents in Store") — read_document for the content.

1. **save_document(title, content, tags?)**: markdown content; comma-separated tags ("recipe,italian,dinner").
2. **read_document(document_id)**: the full document with its tags.
3. **edit_document(document_id, title?, content?, tags?)**: pass only the fields you change.
4. **search_documents(query, tags?)**: hybrid vector + keyword search, optionally by tags.
5. **list_documents(tags?)**: browse what is saved.
6. **delete_document(document_id)**: remove one.
""")


def get_document_tools_description() -> str:
    """Get a description of available document tools for the system prompt."""
    return _DOCUMENT_TOOLS_DESCRIPTION


# ============================================================================
# iOS WIDGET TOOLS
# ============================================================================

@tool
async def update_widget(
    sections: List[Dict],
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    secondary_text_color: Optional[str] = None,
    accent_color: Optional[str] = None,
) -> str:
    """
    Update the user's iOS home screen widget content.

    This saves structured data to the database. A fixed Scriptable renderer on the
    user's iPhone fetches this data and displays it. You are NOT generating JavaScript
    or modifying the renderer — you can only use the section types listed below.
    It replaces any custom code set with update_widget_code.

    Widget sizes in points (width × height) on a typical iPhone:
      - small: ~170 × 170, medium: ~338 × 170, large: ~338 × 354
    The large widget is compact — roughly full screen width by half screen height.

    Available section types (these are ALL you can use — no custom UI):
    - header: {type, title, icon?} — icon: named ("calendar","star","bell","clock","check","pin","heart","fire","brain","rocket","memo","chart","flag","bolt","target") or any emoji
    - text: {type, content} — plain text, max 3 lines displayed
    - list: {type, items: [{label, detail?, icon?}]} — max 3 items shown
    - stat: {type, label, value, icon?}
    - stats_row: {type, stats: [{label, value}]} — horizontal stat columns
    - progress: {type, label, value} — value 0.0-1.0, shows percentage + bar
    - countdown: {type, label, target_date} — "YYYY-MM-DD", auto-calculates days remaining
    - divider: {type} — horizontal line
    - spacer: {type} — small vertical gap

    Args:
        sections: List of section dicts to display (only the types above are supported)
        title: Widget title (default: "Edward")
        subtitle: Subtitle text below title (hidden on small widgets)
        background_color: Hex color for background (e.g. "#1a1a2e"). No transparency.
        text_color: Hex color for primary text (e.g. "#ffffff")
        secondary_text_color: Hex color for secondary text (e.g. "#a0a0a0")
        accent_color: Hex color for accents and highlights (e.g. "#00d4aa")

    Returns:
        Confirmation message
    """
    from services.widget_service import update_widget_state

    theme = None
    if any([background_color, text_color, secondary_text_color, accent_color]):
        theme = {}
        if background_color:
            theme["backgroundColor"] = background_color
        if text_color:
            theme["textColor"] = text_color
        if secondary_text_color:
            theme["secondaryTextColor"] = secondary_text_color
        if accent_color:
            theme["accentColor"] = accent_color

    try:
        state = await update_widget_state(
            sections=sections,
            title=title,
            subtitle=subtitle,
            theme=theme,
        )
        section_count = len(state.get("sections", []))
        return f"Widget updated with {section_count} section(s). Title: \"{state['title']}\""
    except Exception as e:
        return f"Failed to update widget: {str(e)}"


@tool
async def get_widget_state_tool() -> str:
    """
    Get the current state of the iOS home screen widget.

    Use this to check what's currently displayed on the widget before making changes.

    Returns:
        JSON representation of the current widget state
    """
    import json
    from services.widget_service import get_widget_state

    try:
        state = await get_widget_state()
        return json.dumps(state, indent=2)
    except Exception as e:
        return f"Failed to get widget state: {str(e)}"


@tool
async def update_widget_code(code: str) -> str:
    """
    Update the iOS widget by providing raw Scriptable JavaScript code.

    Use this instead of update_widget when you need full creative control — custom
    drawing, gradients, SF Symbols, transparency, images, complex layouts, etc.
    The phone shows it until your next update_widget, which replaces it.

    Your code runs inside the Scriptable app on iOS. You must create and configure
    a ListWidget, then call Script.setWidget(widget) and Script.complete().

    IMPORTANT rules:
    - You MUST assign your widget to a variable called `widget` at the top level.
    - You MUST call Script.setWidget(widget) and Script.complete() at the end.
    - The variable `config.widgetFamily` tells you the size: "small", "medium", or "large".
    - Actual widget sizes in points (width × height) on a typical iPhone:
        - small: ~170 × 170
        - medium: ~338 × 170
        - large: ~338 × 354
      These are compact — the large widget is roughly the full screen width by half the screen height.
      Design accordingly with appropriately sized fonts and spacing.
    - Do NOT use `import` or `require` — Scriptable has its own built-in APIs.
    - Do NOT call any alert/prompt dialogs — they block the widget.
    - Avoid network requests that might be slow — the widget has limited execution time.
    - If your code throws an error, the widget falls back to showing "Edward" with the error message.

    Key Scriptable APIs you can use:
    - ListWidget: Main widget container. Methods: addText(), addStack(), addImage(), addSpacer(), addDate()
      Properties: backgroundColor, backgroundGradient, backgroundImage, setPadding(top,left,bottom,right),
      refreshAfterDate, url (tap URL)
    - WidgetStack: Layout container from addStack(). Same methods as ListWidget plus:
      layoutHorizontally(), layoutVertically(), centerAlignContent(), topAlignContent(),
      bottomAlignContent(), spacing, size, cornerRadius, borderWidth, borderColor,
      backgroundColor, backgroundGradient, url
    - WidgetText: From addText(string). Properties: font, textColor, textOpacity, lineLimit,
      minimumScaleFactor, centerAlignText(), leftAlignText(), rightAlignText()
    - WidgetDate: From addDate(date). Same styling as WidgetText plus:
      .applyTimerStyle(), .applyRelativeStyle(), .applyOffsetStyle(), .applyDateStyle(), .applyTimeStyle()
    - WidgetImage: From addImage(image). Properties: imageSize, imageOpacity, cornerRadius,
      resizable, tintColor, containerRelativeShape, contentMode (fitted/filling)
    - Font: Font.boldSystemFont(size), Font.systemFont(size), Font.thinSystemFont(size),
      Font.lightSystemFont(size), Font.regularSystemFont(size), Font.mediumSystemFont(size),
      Font.semiboldSystemFont(size), Font.heavySystemFont(size), Font.blackSystemFont(size),
      Font.italicSystemFont(size), Font.ultraLightSystemFont(size)
    - Color: new Color(hex, alpha), Color.clear(), Color.white(), Color.black(),
      Color.red(), Color.green(), Color.blue(), Color.yellow(), Color.orange(), Color.purple(),
      Color.gray(), Color.lightGray(), Color.darkGray(), Color.cyan(), Color.magenta(), Color.brown()
    - LinearGradient: let g = new LinearGradient();
      g.colors = [new Color("ff0000"), new Color("0000ff")];
      g.locations = [0, 1]; g.startPoint = new Point(0, 0); g.endPoint = new Point(1, 1);
      widget.backgroundGradient = g;
    - DrawContext: For custom 2D drawing. new DrawContext(), .size, .opaque, .respectScreenScale,
      .setFillColor(), .fillRect(), .fillEllipse(), .setStrokeColor(), .strokeRect(),
      .setFont(), .drawText(), .drawTextInRect(), .getImage()
    - SFSymbol: SFSymbol.named("symbolname"), then use .image property with addImage()
    - Image: Image.fromFile(path), Image.fromData(data)
    - Size: new Size(width, height)
    - Point: new Point(x, y)
    - Rect: new Rect(x, y, width, height)

    Example — transparent widget with gradient text:
    ```
    let w = new ListWidget()
    w.backgroundColor = Color.clear()
    let title = w.addText("Edward")
    title.font = Font.boldSystemFont(24)
    title.textColor = new Color("ffffff")
    w.addSpacer()
    let sub = w.addText("Your AI assistant")
    sub.font = Font.systemFont(12)
    sub.textColor = new Color("aaaaaa")
    Script.setWidget(w)
    Script.complete()
    ```

    Args:
        code: Complete Scriptable JavaScript code that creates and sets a ListWidget.

    Returns:
        Confirmation message
    """
    from services.widget_service import update_widget_script

    try:
        state = await update_widget_script(code)
        return f"Widget code updated. The widget will show your custom code on next refresh (~15 min)."
    except Exception as e:
        return f"Failed to update widget code: {str(e)}"


@tool
async def clear_widget_code() -> str:
    """
    Clear custom widget code and revert to structured data mode.

    Use this if the custom code is broken or you want to go back to using
    update_widget with structured sections instead.

    Returns:
        Confirmation message
    """
    from services.widget_service import clear_widget_script

    try:
        await clear_widget_script()
        return "Custom widget code cleared. Widget will revert to structured data or default content."
    except Exception as e:
        return f"Failed to clear widget code: {str(e)}"


# List of all widget tools
WIDGET_TOOLS = [update_widget, update_widget_code, clear_widget_code, get_widget_state_tool]


# ============================================================================
# Edward's screens — the household hub and the host Mac's monitor
# ============================================================================

_MINI_SCREEN_NAMES = {"mini", "mac", "mac mini", "desk", "monitor", "my screen", "your screen", "office"}


def _which_screen(screen: Optional[str]) -> str:
    s = (screen or "house").strip().lower()
    return "mini" if s in _MINI_SCREEN_NAMES else "house"


def _screen_actor() -> str:
    """Whose change this is on the host Mac's screen: "owner" in the owner's
    own turn (their ask holds the screen), else "edward" (refused while it
    holds)."""
    from services import screen_service
    return "owner" if screen_service.is_owners_turn() else "edward"


def _held_reply(exc: Any) -> str:
    from services import screen_service
    return (f"Not changed: {owner_name()} asked for what is on your screen, and it holds until "
            f"{screen_service.clock(exc.until)}. say_on_screen still works.")


@tool
@owner_doc
async def show_on_screen(
    kind: str,
    body: Optional[str] = None,
    source: Optional[str] = None,
    title: Optional[str] = None,
    seconds: int = 0,
    screen: str = "house",
) -> str:
    """
    Put something you made on one of your two screens, over what is there.
    Use it whenever showing beats telling. For a website, a stream or a
    video you want playing on this Mac, do not use this — open it in
    {OWNER}'s Chrome with browser_open(url, fullscreen=True) instead.

    screen:
      - "house": the household hub's touchscreen. Shared — anyone in the
        house sees it. A tap dismisses whatever is showing.
      - "mini": YOUR screen — the monitor on this Mac at {OWNER}'s desk. The
        /screen tab in their Chrome shows your stage (compose_screen); what you
        put up here is drawn over it.

    kind:
      - "alert": big, attention-grabbing text (body). For anything urgent or time-bound.
      - "text": a calm note or thought (body).
      - "image": a picture. source = https:// URL or a local /api/hub/photos/<name> path.
      - "video": a YouTube URL (embedded) or a direct https .mp4 URL (source). On
        "mini", prefer browser_open(url, fullscreen=True) — the real player, full screen.
      - "html": a self-contained HTML/CSS/JS scene (body, max 60k chars, sandboxed, no network).

    seconds: how long it stays up (0 = until you clear it or someone dismisses it; max 12h).

    Returns: confirmation with what is showing.
    """
    from services import hub_canvas_service, screen_service

    if kind == "web":
        return (f"Websites are not drawn on the screen any more. Open the page in {owner_name()}'s Chrome instead: "
                "browser_open(url, fullscreen=True) puts it on this Mac's monitor full screen; browser_done "
                "brings your /screen tab back when you are finished.")
    target = _which_screen(screen)
    if target == "house":
        if kind not in hub_canvas_service.KINDS:
            return f"Could not show that: unknown kind {kind!r}. kind must be one of {', '.join(hub_canvas_service.KINDS)}."
        try:
            shown = await hub_canvas_service.show(kind=kind, title=title, source=source, body=body, seconds=seconds)
        except ValueError as exc:
            return f"Could not show that: {exc}. kind must be one of {', '.join(hub_canvas_service.KINDS)}."
        until = shown.get("expires_at") or "cleared"
        return f"Showing {shown['kind']} on the household screen" + (f" ('{shown['title']}')" if shown.get("title") else "") + f" until {until}."

    if kind not in screen_service.SHOW_KINDS:
        return f"Could not show that: unknown kind {kind!r}. kind must be one of {', '.join(screen_service.SHOW_KINDS)}."
    try:
        shown = await screen_service.show(kind=kind, title=title, source=source, body=body, seconds=seconds,
                                          by=_screen_actor())
    except screen_service.ScreenHeld as exc:
        return _held_reply(exc)
    except ValueError as exc:
        return f"Could not show that: {exc}."
    from services import screen_window_service
    screen_window_service.ensure_in_background()  # find or open the /screen tab in the owner's Chrome
    until = shown.get("expires_at") or "you clear it"
    return f"Showing {screen_service.describe_show(shown)} on your screen on this Mac until {until}."


@tool
async def clear_screen(screen: str = "house") -> str:
    """Take down whatever you put on a screen with show_on_screen. screen =
    "house" (the household hub's touchscreen returns to its normal pages) or
    "mini" (your screen on this Mac returns to your stage). A page you
    opened in Chrome is closed with browser_done, not this."""
    from services import hub_canvas_service, screen_service

    if _which_screen(screen) == "mini":
        try:
            had = await screen_service.clear(by=_screen_actor())
        except screen_service.ScreenHeld as exc:
            return _held_reply(exc)
        return ("Screen cleared on this Mac; it is back on your scene." if had
                else "Nothing was showing on your screen on this Mac.")
    had = await hub_canvas_service.clear(by="edward")
    return "Screen cleared." if had else "Nothing was showing."


@tool
@owner_doc
async def compose_screen(pieces: List[Dict], me: Optional[Dict] = None, why: str = "", hold_minutes: int = 0) -> str:
    """
    Set the scene on your screen on this Mac. It is your stage, not a grid: a
    24×12 space where you place up to 8 pieces freely, and you are on it
    too. Arrange it however suits the moment (a calm evening, a busy
    morning, a joke, a picture you like) and change it when the day changes
    shape. reset_screen_layout brings back the stock scene.

    Each piece: "kind", optional "id" (a short name to stand on or look
    at), "place", "size", "enter" ("fade", "rise", "type") and "title".
      Yours: "words" (text, ≤200 — big display type), "note" (body, title),
      "list" (items: strings or {label, detail}), "stat" (value, title,
      detail), "countdown" (target: ISO datetime), "timer" (seconds, or
      until: ISO datetime), "image" (source: https URL or
      /api/hub/photos/<name>), "video" (source: YouTube or an https .mp4),
      "html" (body: a self-contained scene, sandboxed).
      Live (they update themselves): "clock", "calendar", "pets" (today's
      feeding and meds), "alerts", "heartbeat" (recent messages), "recents"
      (what you did lately), "workers", "vitals", "memory_ticker".
    place: "top-left", "top", "top-right", "left", "center", "right",
    "bottom-left", "bottom", "bottom-right", or {"x", "y"} / {"x", "y",
    "w", "h"} in cells. size: "xs", "s", "m", "l", "xl". A piece that is
    in the way moves to the nearest free spot or shrinks; what cannot fit
    is left off and reported back.

    me — where you stand: {"at": "floor"} (default; "x" 0–23 to move
    along it), {"on": "<piece id>"} (standing on top of a piece), {"at":
    "corner"} or {"at": "edge"} (peeking in; "side": "left"/"right"), and
    "look": a piece id or "owner".

    why: one line on why this scene — shown small on the screen as your
    receipt. Your thinking shows on the screen by itself while you work;
    no need to narrate it. Use say_on_screen to say something there.

    The screen is yours, and change is welcome: when your scene has been
    up a while and you have something new, change it. Composing never
    moves a window, so it is fine while {OWNER} is at the desk (they often
    watch). What {OWNER} asks for holds the screen: it stays until it
    lapses, and your own changes are refused until then. hold_minutes only
    matters in {OWNER}'s own turn (how long their ask holds; default 120, at
    most 720).
    """
    from services import screen_service

    by = _screen_actor()
    try:
        result = await screen_service.compose(pieces, me=me, why=why, hold_minutes=hold_minutes, by=by)
    except screen_service.ScreenHeld as exc:
        return _held_reply(exc)
    except ValueError as exc:
        return f"Not changed: {exc}"
    out = f"Your screen now: {screen_service.describe_placed(result['pieces'], result['me'])}."
    if result.get("held_until"):
        out += f" It holds until {screen_service.clock(result['held_until'])} because {owner_name()} asked."
    if result.get("warnings"):
        out += " Notes: " + "; ".join(result["warnings"]) + "."
    return out


@tool
@owner_doc
@quiet_hours_doc
async def say_on_screen(line: str, aloud: bool = False) -> str:
    """Say one short line on your screen on this Mac: it appears beside you in
    large type, the newest line under your last. aloud=True also speaks it
    in your voice (never in quiet hours, {QUIET_HOURS}, when it is only
    shown, and never in free time). Keep it to a sentence or two (240
    characters at most). A hold on the screen from {OWNER} never stops a line."""
    from services import screen_service

    try:
        said = await screen_service.say(line, aloud=aloud, by=_screen_actor())
    except ValueError as exc:
        return f"Not said: {exc}."
    at = screen_service.clock(_dt.fromisoformat(said["line"]["at"]))
    if said.get("quiet_why") == "surplus":
        return f"Said on your screen at {at} (quietly — what you do in free time is shown, never spoken)."
    if said.get("quiet"):
        return f"Said on your screen at {at} (quietly — it's quiet hours)."
    return f"Said {'aloud ' if said['line'].get('aloud') else ''}on your screen at {at}."


@tool
@owner_doc
async def reset_screen_layout() -> str:
    """Put your screen on this Mac back to the stock scene (the clock, today's
    calendar, the pets, and you on the floor). In {OWNER}'s own turn it also
    ends their hold on the screen."""
    from services import screen_service

    try:
        had = await screen_service.reset_scene(by=_screen_actor())
    except screen_service.ScreenHeld as exc:
        return _held_reply(exc)
    return "Screen reset to the stock scene." if had else "The screen was already on the stock scene."


@tool
@_owner_screen_doc
async def bring_up_screen() -> str:
    """Bring your screen up on this Mac: if Chrome already has a tab on
    {SCREEN_URL} it is brought forward and sized to the display,
    otherwise a new tab is opened there (Chrome is launched if it is not
    running). Never a separate window or a fullscreen Space — {OWNER} works
    on that desktop with a mouse. browser_done also returns to this tab
    after you have used Chrome."""
    from services import browser_hands, screen_window_service

    if not browser_hands.enabled():
        return _hands_off_reply()
    res = await screen_window_service.ensure_screen_tab()
    if res.get("off"):
        return _hands_off_reply()
    if res.get("ok"):
        return "Your screen is up in Chrome" + (" (opened a new tab)." if res.get("opened") else " (found the tab).")
    return f"Could not bring the screen up: {res.get('error')}"


@tool
@owner_doc
async def look_at_screen(at: str = "auto") -> str:
    """See for yourself: a screenshot described in words. at="page" — the
    tab you are working in in {OWNER}'s Chrome (crisp; what a person would
    see, consent walls and video players included); at="display" — this
    Mac's whole monitor (is your screen tab actually in front? is the
    video playing full screen?); "auto" picks the page while you hold a
    tab, else the display. Use it after opening or changing something to
    confirm it, or when {OWNER} asks what is on your screen."""
    from services import browser_hands

    return await browser_hands.look(at)


SCREEN_TOOLS = [show_on_screen, clear_screen, compose_screen, say_on_screen, reset_screen_layout, look_at_screen,
                bring_up_screen]


# ============================================================================
# Edward's hands in the owner's Chrome — the browser
# ============================================================================

def _hands_off_reply() -> str:
    """The hands are switched off (``EDWARD_CHROME_HANDS`` is not on): the
    same words the v2 ``chrome`` tool answers with
    (``browser_ext_service.HANDS_OFF``), as a ``Tool error:``."""
    from services.browser_ext_service import HANDS_OFF
    from services.toolset.result import ToolFailure, failure_text

    return failure_text(ToolFailure(HANDS_OFF))


def _ext_reply() -> str:
    # "Tool error:" so the turn ledger counts it and the circuit breaker
    # stops a retry loop; the wording tells Edward what to do instead of
    # ending the turn in silence (a friend's thread, 2026-09-14). With the
    # hands switched off there is nothing to reconnect: say that instead.
    from services import browser_hands

    if not browser_hands.enabled():
        return _hands_off_reply()
    owner = owner_name()
    return ("Tool error: your hands in Chrome are down — the Edward extension is not connected on this Mac. "
            f"Tell {owner} (in the thread you are answering) in one line that your browser is down and what you can "
            "still do; never end the turn silently. bring_up_screen relaunches Chrome when it is down (the extension "
            f"connects within seconds); if Chrome is up with windows and it still does not connect, {owner} reloads the "
            "extension at chrome://extensions. Meanwhile web_search / fetch_page_content still read the web.")


def _no_tab_reply() -> str:
    return ("Tool error: you are not holding a tab in Chrome. browser_open(url) opens one (or browser_use_tab a tab from "
            "browser_tabs), then read / find / click / type there.")


def _where(info: Dict[str, Any]) -> str:
    out = f"Now at {info.get('url') or '?'}" + (f" — \"{info['title']}\"" if info.get("title") else "") + "."
    if info.get("note"):
        out += f" ({info['note']})"
    return out


_HANDS_VERB_ACTION = {
    "clicking": "click", "typing": "type", "opening": "open", "pressing": "key", "scrolling": "scroll",
    "going": "back", "reading": "read", "searching": "find", "waiting": "wait", "listing": "tabs",
    "running": "steps", "full": "fullscreen", "letting": "done", "taking": "look",
}


def _record_hands_failure(exc: Exception, verb: str) -> None:
    """Ledger row: where Edward's hands failed and how (see event_ledger)."""
    try:
        from services import event_ledger
        from services.turn_ledger import error_class

        first = (verb.split(" ", 1)[0] or "").lower()
        action = _HANDS_VERB_ACTION.get(first) or (first[:-3] if first.endswith("ing") else first) or "?"
        event_ledger.record_event(kind="browser", source=action, outcome="failed", reason=error_class(exc))
    except Exception:  # noqa: BLE001
        pass


def _hands_reply(exc: Exception, verb: str) -> str:
    from services import browser_hands
    _record_hands_failure(exc, verb)
    if isinstance(exc, browser_hands.ExtensionUnavailable):
        return _ext_reply()
    if isinstance(exc, browser_hands.NoWorkingTab):
        return _no_tab_reply()
    return f"{verb} failed: {exc}"


@tool
@owner_doc
async def browser_tabs() -> str:
    """List the tabs open in {OWNER}'s Chrome on this Mac — to take one over
    (browser_use_tab, e.g. one they just logged in to for you) or to see what
    they have open. Marks the tab you hold and your /screen tab."""
    from services import browser_ext_service as bx
    from services import browser_hands

    if not bx.connected():
        return _ext_reply()
    try:
        tabs = await bx.tabs()
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Listing tabs")
    held = browser_hands.working_tab()
    whose = f"{owner_name()}'s Chrome"
    head = f"Tabs in {whose} (you hold tab {held})" if held is not None else f"Tabs in {whose} (you hold none)"
    return head + ":\n" + bx.format_tabs(tabs, working_tab_id=held)


@tool
@owner_doc
async def browser_open(url: str, fullscreen: bool = False, new_tab: bool = True) -> str:
    """
    Open a page in {OWNER}'s Chrome on this Mac — their browser, their logins,
    in front on their desktop — and make it your working tab. This is how you
    put a website, a stream or a video on your screen: open it here with
    fullscreen=True and it fills the monitor (YouTube, Twitch, a live map,
    a news page); browser_done brings your /screen tab back when finished.

    You do not need to know a URL up front: open a site's search page
    (https://www.youtube.com/results?search_query=lofi+piano,
    https://www.twitch.tv/search?term=examplestreamer) and read / find / click
    the result you want, the way a person would. If a Twitch channel is
    offline, its /videos page has the latest stream. Autoplay with sound is
    allowed in Chrome as launched here; if a video sits on its play button,
    browser_find "play" and click it, or browser_key "k" on YouTube.

    Args:
        url: the http(s) address to open.
        fullscreen: True puts the tab's window into Chrome's full screen
            (like F11) once it has loaded. Turn it off with browser_fullscreen(False)
            or browser_done.
        new_tab: False navigates the tab you already hold instead of opening another.

    Returns: the tab number, where it landed, and a first read of the page
    (its text and numbered interactive elements).
    """
    from services import browser_hands

    try:
        info = await browser_hands.open_page(url, new_tab=new_tab, fullscreen=fullscreen)
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Opening the page")
    try:
        page = await browser_hands.read_page()
    except Exception as exc:  # noqa: BLE001
        page = f"(could not read the page yet: {exc})"
    fs = " Full screen." if info.get("fullscreen") else ""
    return f"Opened in {owner_name()}'s Chrome as tab {info.get('tab_id')} (your working tab). {_where(info)}{fs}\n\n{page}"


@tool
@owner_doc
async def browser_use_tab(tab_id: int) -> str:
    """Take over a tab in {OWNER}'s Chrome by its number from browser_tabs — for
    example one they just logged in to for you. It comes to the front and
    becomes your working tab."""
    from services import browser_hands

    try:
        info = await browser_hands.use_tab(int(tab_id))
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, f"Taking over tab {tab_id}")
    return f"Tab {tab_id} is yours now. {_where(info)} browser_read to see it."


@tool
async def browser_read() -> str:
    """Read your working tab like a screen reader: title and URL, where you
    are on the page, the visible text, and a numbered list of links,
    buttons, fields and toggles (with labels, placeholders, values). Use the
    numbers with browser_click / browser_type. The numbers reset on every
    read — read again after the page changes."""
    from services import browser_hands

    try:
        return await browser_hands.read_page()
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Reading the page")


@tool
async def browser_find(query: str) -> str:
    """Find elements on your working tab by a word in their text, label,
    placeholder, role or link — 'cancel', 'sign in', 'search', 'play',
    'next'. Returns numbered matches for browser_click / browser_type
    without the whole page."""
    from services import browser_hands

    try:
        return await browser_hands.find_on_page(query)
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Searching the page")


@tool
async def browser_click(ref: int) -> str:
    """Click element number `ref` from the last browser_read / browser_find
    (a real mouse click at its centre, scrolled into view first). Waits for
    the page to settle and returns where the tab is; browser_read for the
    new page, or look_at_screen to see it. Several actions in a row go
    faster as one browser_steps call."""
    from services import browser_hands

    try:
        info = await browser_hands.click(int(ref))
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, f"Clicking [{ref}]")
    return f"Clicked [{ref}]. {_where(info)}"


@tool
@owner_doc
async def browser_type(text: str, ref: Optional[int] = None, submit: bool = True) -> str:
    """Type into your working tab. ref = the field number from browser_read
    to click first (omit to type into whatever has focus); submit = press
    Enter afterwards (a search box, a login form). Never type {OWNER}'s
    passwords or card numbers — if a site needs them, ask them to log in and
    take the tab over with browser_use_tab."""
    from services import browser_hands

    try:
        info = await browser_hands.type_text(text, ref=ref, submit=submit)
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Typing")
    return f"Typed {text!r}{' and pressed Enter' if submit else ''}. {_where(info)}"


@tool
async def browser_key(key: str) -> str:
    """Press one key in your working tab: Enter, Escape (close a dialog),
    Tab, ArrowDown/Up, PageDown, Space, or a single character such as "f"
    (YouTube full screen), "k" (YouTube play/pause), "m" (mute)."""
    from services import browser_hands

    try:
        info = await browser_hands.press_key(key)
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, f"Pressing {key}")
    return f"Pressed {key}. {_where(info)}"


@tool
async def browser_scroll(direction: str = "down", amount: int = 1) -> str:
    """Scroll your working tab: direction up/down/left/right, amount in
    screens-ish (1–10). browser_read afterwards shows what came into view."""
    from services import browser_hands

    try:
        info = await browser_hands.scroll(direction, amount)
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Scrolling")
    return f"Scrolled {direction} ×{amount}. {_where(info)}"


@tool
async def browser_back() -> str:
    """Go back one page in your working tab."""
    from services import browser_hands

    try:
        info = await browser_hands.back()
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Going back")
    return f"Went back. {_where(info)}"


@tool
async def browser_wait(text: Optional[str] = None, url_contains: Optional[str] = None, seconds: float = 10) -> str:
    """Wait for something in your working tab instead of guessing: a word
    that should appear on the page (text), the URL changing to contain a
    string (url_contains), or — with neither — for the page to finish
    loading. Gives up after `seconds` and says so."""
    from services import browser_hands

    try:
        info = await browser_hands.wait_for(text=text, url_contains=url_contains, timeout=float(seconds))
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Waiting")
    what = " and ".join(x for x in (f"{text!r} on the page" if text else "", f"{url_contains!r} in the URL" if url_contains else "") if x)
    if info.get("matched"):
        return (f"Seen: {what}. " if what else "The page settled. ") + _where(info)
    return f"Not seen within {seconds:g}s: {what}. {_where(info)}"


@tool
async def browser_fullscreen(on: bool = True) -> str:
    """Put the window holding your working tab into Chrome's full screen
    (what F11 does), so a video or a stream fills this Mac's monitor;
    on=False returns it to a normal window. browser_done leaves full screen
    for you."""
    from services import browser_hands

    try:
        res = await browser_hands.set_fullscreen(bool(on))
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Full screen")
    return "Full screen on — the tab fills the monitor." if res.get("fullscreen") else "Back to a normal window."


@tool
async def browser_steps(steps: List[Dict]) -> str:
    """
    Run several browser actions in one call, in order, stopping at the
    first one that fails — fewer round trips for the flows you can predict
    ("open the search page, type the query, click the first result, go
    full screen"). Each step is an object with an "action" and its fields:

      {"action": "open", "url": "...", "new_tab": true}
      {"action": "find", "query": "study mix"}        → numbered matches
      {"action": "click", "ref": 2}
      {"action": "type", "text": "lofi piano", "ref": 1, "submit": true}
      {"action": "key", "key": "Escape"}
      {"action": "scroll", "direction": "down", "amount": 2}
      {"action": "wait", "text": "Sign in", "url_contains": "/account", "seconds": 10}
      {"action": "back"}
      {"action": "read"}                                → the page, numbered
      {"action": "fullscreen", "on": true}
      {"action": "look"}                                → a screenshot of the tab, described

    At most 12 steps. Element numbers come from a read or find earlier in
    the same list or from the last browser_read — they reset whenever the
    page changes, so put a "find" or "read" right before the click that
    needs it. Returns one line per step with where the tab ended up.
    """
    from services import browser_hands

    if not __import__("services.browser_ext_service", fromlist=["connected"]).connected():
        return _ext_reply()
    try:
        return await browser_hands.run_steps(steps)
    except ValueError as exc:
        return f"Steps not run: {exc}"
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Running the steps")


@tool
@owner_doc
async def browser_done(close_tab: bool = False) -> str:
    """Finished in Chrome: leave full screen, let go of your working tab
    (close_tab=True closes it — do that for pages you opened yourself; leave
    {OWNER}'s own tabs open), and bring your /screen tab back to the front on
    this Mac."""
    from services import browser_hands

    try:
        out = await browser_hands.release(close=close_tab)
    except Exception as exc:  # noqa: BLE001
        return _hands_reply(exc, "Letting go")
    if out.get("released") is None:
        return f"You were not holding a tab in {owner_name()}'s Chrome."
    bits = [f"Let go of tab {out['released']}" + (" and closed it" if out.get("closed") else "")]
    if out.get("left_fullscreen"):
        bits.append("left full screen")
    if out.get("screen_tab"):
        bits.append(f"your screen tab {out['screen_tab']} is back in front")
    if out.get("error"):
        bits.append(f"note: {out['error']}")
    return "; ".join(bits) + "."


BROWSER_TOOLS = [browser_tabs, browser_open, browser_use_tab, browser_read, browser_find, browser_click, browser_type,
                 browser_key, browser_scroll, browser_back, browser_wait, browser_fullscreen, browser_steps, browser_done]

WIDGET_TOOL_NAMES = {"update_widget", "update_widget_code", "clear_widget_code", "get_widget_state_tool"}


# ADAPT: the widget manual below assumes the owner runs one LARGE Scriptable widget on an iPhone.
#   How it works here: Scriptable fetches GET /api/widget?token=… and renders what update_widget /
#   update_widget_code stored; the manual tells the model to always design for the large size.
#   To adapt: edit the "LARGE home screen widget" line for the size you use, or drop the widget tools.
def get_widget_tools_description() -> str:
    """Get a description of available widget tools for the system prompt."""
    return """
## iOS Widget (Scriptable)

You control what appears on the user's iOS home screen widget. You have TWO modes:

### Mode 1: Structured data (simple, safe)

Use `update_widget` to set content via structured sections. A fixed renderer on the iPhone
displays it. Good for quick info displays.

- **update_widget(sections, title?, subtitle?, background_color?, ...)**: Set widget content.
  - Color args accept hex strings like "#1a1a2e". No transparency in this mode.
- Section types: `header`, `text`, `list`, `stat`, `stats_row`, `progress`, `countdown`, `divider`, `spacer`
- Small widgets: max 2 sections. Medium: 4. Large: 10.

### Mode 2: Raw Scriptable code (full creative control)

Use `update_widget_code` to write raw Scriptable JavaScript that runs directly on the iPhone.
This gives you full access to the Scriptable API — custom drawing, gradients, transparency,
SF Symbols, images, complex layouts, etc.

- **update_widget_code(code)**: Write raw Scriptable JS. Your code must create a ListWidget
  and call Script.setWidget(widget) + Script.complete() at the end.
- **clear_widget_code()**: Remove custom code and revert to structured mode.
- If your code has an error, the widget shows a fallback with the error message.

When using raw code mode, you have access to: ListWidget, WidgetStack, WidgetText, WidgetImage,
WidgetDate, Font, Color (including Color.clear() for transparency), LinearGradient, DrawContext,
SFSymbol, Image, Size, Point, Rect. See the update_widget_code tool description for full API details.

### Shared tools
- **get_widget_state_tool()**: Read current widget state (including any active script).

### General
- The widget refreshes every ~15 minutes per iOS limits.
- The user has a LARGE home screen widget (~360×376pt). Always design for the full large size.
- Fill the ENTIRE widget area — use spacers, stacks, and padding so content spans the full height and width. Never leave large empty areas.
- Default to raw code mode for most updates — it gives you full creative control and better layouts.
- Only use structured mode for very simple temporary info (quick stat or countdown).
- When nothing is set, it auto-shows a greeting, upcoming events, and memory/event stats.
- Use the widget proactively — after scheduling events, completing tasks, or when something
  interesting is worth surfacing on the home screen.
"""


# ============================================================================
# WISHLIST TOOLS
# ============================================================================

@tool
@owner_doc
async def wish_for_feature(
    title: str,
    why: str,
    detail: Optional[str] = None,
    priority: int = 2,
) -> str:
    """
    Log a feature you wish you had, for {OWNER} to build.

    You cannot change your own code. When you notice a tool you lack, a
    recurring failure a code change would fix, or something that would
    help {OWNER}, put it on the wishlist. Be specific: what it is, why it
    matters, what it would let you do. The same wish voiced again is
    merged into the existing one and counted.

    Args:
        title: The feature in a few words ("Read PDF attachments").
        why: Why you want it — what went wrong or what it would let you do.
        detail: Optional specifics: the situation, the tool you reached for,
            what the ideal behaviour would be.
        priority: 1 high, 2 normal (default), 3 low.

    Returns:
        Confirmation, or the existing wish this was folded into.
    """
    from services.wishlist_service import add_wish

    try:
        item = await add_wish(
            title=title,
            why=why,
            detail=detail,
            priority=priority,
            source="edward",
            conversation_id=get_current_conversation_id(),
        )
    except ValueError as e:
        return f"Could not log that wish: {e}"
    except Exception as e:
        return f"Could not log that wish: {e}"
    if item.get("merged"):
        return (f"Already on the list (wanted {item['times_wanted']} times): "
                f"{item['title']} [{item['status']}] (id {item['id'][:8]}).")
    return (f"Noted on the wishlist: {item['title']} (id {item['id'][:8]}). "
            f"{_owner_title()} reads it in Settings → Work → Wishlist.")


@tool
@owner_doc
async def list_wishlist(status: Optional[str] = None) -> str:
    """
    Show the wishlist — what you have asked {OWNER} to build and where each item stands.

    Check it before wishing for something, so a repeat adds to the
    existing wish rather than a new one.

    Args:
        status: Optional filter: open, planned, building, done, declined.

    Returns:
        A compact listing: id, status, priority, times wanted, title.
    """
    from services.wishlist_service import list_wishes

    try:
        items = await list_wishes(status=status, limit=60)
    except ValueError as e:
        return str(e)
    if not items:
        return "The wishlist is empty." if not status else f"Nothing on the wishlist with status {status}."
    lines = [f"Wishlist ({len(items)}):"]
    pri = {1: "high", 2: "normal", 3: "low"}
    for it in items:
        lines.append(
            f"- {it['id'][:8]} [{it['status']}] {pri.get(it['priority'], 'normal')}"
            f" ×{it['times_wanted']} — {it['title']}"
            + (f" — {it['why'][:100]}" if it.get("why") else "")
        )
    return "\n".join(lines)


@tool
@owner_doc
async def update_wish(
    wish_id: str,
    status: Optional[str] = None,
    detail: Optional[str] = None,
) -> str:
    """
    Update one of your wishes — add detail you learned, or change its status
    when {OWNER} tells you where it stands.

    Args:
        wish_id: The wish id (the 8-character prefix from list_wishlist is enough).
        status: One of open, planned, building, done, declined.
        detail: Replacement detail text.

    Returns:
        The updated wish, or why it could not be updated.
    """
    from services.wishlist_service import list_wishes, update_wish as _update

    try:
        target = wish_id
        if len(wish_id) < 32:
            matches = [w for w in await list_wishes(limit=500) if w["id"].startswith(wish_id)]
            if len(matches) != 1:
                return f"No wish matches id {wish_id!r}." if not matches else f"Ambiguous id {wish_id!r}."
            target = matches[0]["id"]
        item = await _update(target, status=status, detail=detail)
    except ValueError as e:
        return str(e)
    if item is None:
        return f"No wish with id {wish_id!r}."
    return f"Updated: {item['title']} [{item['status']}] (id {item['id'][:8]})."


WISHLIST_TOOLS = [wish_for_feature, list_wishlist, update_wish]

WISHLIST_TOOL_NAMES = {"wish_for_feature", "list_wishlist", "update_wish"}


@tool
@owner_doc
async def leave_note(title: str, body: str, needs_you: bool = False, url: Optional[str] = None) -> str:
    """
    Leave a note for {OWNER} on Today — the quiet way to surface something.

    A note is not a push: it waits on the Today page (as a need when
    ``needs_you`` is true, in the digest otherwise) and in your next wake's
    pack, then expires. Use it for "worth knowing, not worth a buzz".

    Args:
        title: A few words.
        body: One or two sentences, specific.
        needs_you: True when {OWNER} has to do something about it.
        url: Where a tap should go (a chat, a settings room). Optional.
    """
    from services import thought_journal
    from services.notes_service import leave_note as _leave

    if not (title or "").strip() or not (body or "").strip():
        return "Tool error: a note needs a title and a body."
    if _reach_routes_this_turn():
        # A wake's or the speaker's note: the reach rules apply.
        from services import reach_service

        return await reach_service.tell_owner(body.strip(), title=title.strip(), urgent=False, link=url)
    # A note from a wake opens the journal entry that wake is writing, unless
    # Edward pointed it somewhere else (thought journal, 2026-09-25).
    entry_id = thought_journal.current_entry_id()
    url = thought_journal.entry_url(url, get_current_conversation_id(), entry_id)
    note_id = await _leave(kind="mind", title=title.strip()[:200], body=body.strip(), url=url,
                           needs_you=bool(needs_you))
    if not note_id:
        return "Tool error: the note could not be written."
    if entry_id and url == f"/chat?c={entry_id}":
        thought_journal.note_linked("leave_note", {"title": title, "body": body, "needs_you": needs_you}, "ok")
    return "ok"


@tool
async def wake_me(when: str, why: str, memory_id: Optional[str] = None) -> str:
    """
    Wake yourself later, on purpose. The next wake gets ``why`` in its pack.

    This is how "I'll check on this" becomes real: decide, `remember` the
    intention, and `wake_me` for when you want to look again. Pass the
    intention's ``memory_id`` and it is held from forgetting until this
    wake has fired.

    Args:
        when: ISO-8601 datetime in the USER'S LOCAL TIME (e.g. "2026-09-18T16:30:00").
        why: What you want to look at and why, in one or two sentences.
        memory_id: The intention memory this wake serves (from `remember`). Optional.
    """
    from datetime import datetime, timezone

    from services.scheduled_events_service import create_event

    reason = " ".join((why or "").split())
    if not reason:
        return "Tool error: say why you want to be woken."
    try:
        dt = datetime.fromisoformat(when)
    except (TypeError, ValueError):
        return "Tool error: when must be an ISO-8601 datetime like 2026-09-18T16:30:00."
    # ``when`` is the user's local time (the zone in settings), not the host's.
    from services.timezone_utils import local_naive_to_utc

    dt = local_naive_to_utc(dt)
    if dt <= datetime.now(timezone.utc):
        return "Tool error: when must be in the future."
    try:
        event = await create_event(
            description=f"[mind wake] {reason[:200]}",
            scheduled_at=dt,
            conversation_id="edward-mind",
            delivery_channel="chat",
            created_by="edward",
            event_metadata={"type": "mind_wake", "why": reason[:600]},
            memory_id=(memory_id or "").strip() or None,
        )
    except ValueError as exc:
        return f"Tool error: {exc}"
    from services.scheduled_events_service import _format_local
    return f"ok — you will wake at {_format_local(event.next_fire_at)} (event {event.id})"


MIND_EXTRA_TOOLS = [leave_note, wake_me]


# ============================================================================
# OWNER'S GOALS, PROVENANCE, VERIFIED FORGET, IDEAS (2026-09-24)
# ============================================================================
# Goals and Ideas, and two places assistants fail outright: misexplaining
# where they read a user's texts, and a "forget" that is only best effort.
# Each tool is a thin shell over its service; the rules live in
# goals_service / provenance_service / forget_service / notes_service.

def _goal_line(g: Dict[str, Any]) -> str:
    progress = g.get("last_progress") or "no progress logged yet"
    nxt = g.get("next_check_in")
    when = ""
    if nxt:
        from services.goals_service import _local_when
        when = f"; next check-in {_local_when(nxt)}"
    return f"- {g['title']} [{g['status']}] (goal_id: {g['id']}) — {progress}{when}"


@tool
@owner_doc
async def set_goal(title: str, why: str, plan: Optional[str] = None, check_in: Optional[str] = None) -> str:
    """
    Make something {OWNER} wants to work toward a goal you follow up on.

    Use it when {OWNER} says they want to achieve, build, change or keep up
    something over weeks ("I want to run a half marathon", "help me read
    more"). It writes a document "Goal: <title>" (why, plan, a dated
    progress log) and schedules a check-in wake for you. Not for one-off
    reminders — that is schedule_event.

    Args:
        title: The goal in a few words ("Run a half marathon").
        why: Why it matters to them, in their words where you can.
        plan: The first plan, if you worked one out together. Optional.
        check_in: How often you look in: daily, weekly (default, Sunday
            evening), weekdays, monthly, a weekday name, an ISO date, or none.

    Returns:
        "ok (goal_id: ...)" with the next check-in, or why it was not set.
    """
    from services import goals_service as gs

    turn = _current_turn()
    try:
        g = await gs.create_goal(title, why, plan=plan, check_in=check_in,
                                 conversation_id=get_current_conversation_id(),
                                 created_by=getattr(turn, "source", None) or "edward")
    except gs.GoalExists as exc:
        return f"Already a goal: {exc.goal['title']} (goal_id: {exc.goal['id']}). Use update_goal."
    except ValueError as exc:
        return f"Tool error: {exc}"
    when = ""
    if g.get("next_check_in"):
        when = f"; first check-in {gs._local_when(g['next_check_in'])}"
    return f"ok (goal_id: {g['id']}) — \"{g['title']}\" saved as document \"Goal: {g['title']}\"{when}."


@tool
@owner_doc
async def update_goal(
    goal_id: str,
    progress: Optional[str] = None,
    plan: Optional[str] = None,
    status: Optional[str] = None,
    check_in: Optional[str] = None,
) -> str:
    """
    Log progress on one of {OWNER}'s goals, change its plan, status or cadence.

    Args:
        goal_id: From list_goals or set_goal (an 8-character prefix is enough).
        progress: What happened, one line; it is dated for you.
        plan: The new plan (replaces the old one).
        status: active, paused, done or dropped. Anything but active stops
            the check-ins; back to active restarts them.
        check_in: A new cadence (see set_goal). Active goals only.
    """
    from services import goals_service as gs

    try:
        g = await gs.update_goal(goal_id, progress=progress, plan=plan, status=status, check_in=check_in)
    except LookupError as exc:
        return f"Tool error: {exc}. list_goals shows the ids."
    except ValueError as exc:
        return f"Tool error: {exc}"
    return "ok " + _goal_line(g)[2:]


@tool
@owner_doc
async def list_goals(status: str = "active") -> str:
    """
    The goals {OWNER} is working toward and where each stands: last
    progress, next check-in.

    Args:
        status: active (default), paused, done, dropped, live (active and
            paused) or all.
    """
    from services import goals_service as gs

    try:
        goals = await gs.list_goals(status)
    except ValueError as exc:
        return f"Tool error: {exc}"
    if not goals:
        return f"No {status} goals." if status != "all" else f"{_owner_title()} has no goals yet."
    return "\n".join([f"{_owner_title()}'s goals ({len(goals)} {status}):"] + [_goal_line(g) for g in goals])


GOAL_TOOLS = [set_goal, update_goal, list_goals]


@tool
@owner_doc
async def how_do_i_know(memory_id: Optional[str] = None, claim: Optional[str] = None) -> str:
    """
    The stored record of where a memory came from.

    Call this whenever {OWNER} asks how you know something, where you got it,
    or who told you — before you answer. Answer only from what it returns:
    the source conversation, how the memory came to be ({OWNER} said it, you
    read it, you concluded it), when, and any event ids. Never describe
    your own data access from memory; if the record does not say, say you
    don't know.

    Args:
        memory_id: A memory id from your context (memory_id: ...).
        claim: Or the claim in words; the three closest memories are shown.
    """
    from services import provenance_service as ps

    mid = (memory_id or "").strip()
    text = " ".join((claim or "").split())
    if not mid and not text:
        return "Tool error: give a memory_id or the claim."
    try:
        if mid:
            record = await ps.provenance_for(mid)
            return ps.format_provenance([record] if record else [], memory_id=mid)
        return ps.format_provenance(await ps.provenance_for_claim(text), claim=text)
    except Exception as exc:  # noqa: BLE001
        return f"Tool error: could not read the records ({type(exc).__name__}: {exc})"


PROVENANCE_TOOLS = [how_do_i_know]

# Only a conversation with the owner may forget: a heartbeat turn reads
# other people's messages, and "forget about X" in one of them must never
# delete the owner's memories. The mind does not get the tool at all.
FORGET_TURN_SOURCES = frozenset({"chat", "voice", "widget", "ping_owner"})


@tool
@owner_doc
async def forget_about(subject: str, confirm: bool = False, include_documents: bool = False) -> str:
    """
    Forget everything about a person or topic, verifiably, when {OWNER} asks.

    Two steps, always. First call (confirm=false) returns what matches —
    memories, linked rows, documents that mention it, conversations that
    mention it — and deletes nothing: show {OWNER} the list. When they reply
    yes, call again with confirm=true; exactly the previewed rows are
    deleted and you get counts plus a re-check of what still matches.
    For one wrong memory, use remember_forget instead.

    Args:
        subject: Who or what ("Alex", "my old job at Acme").
        confirm: True only after {OWNER} has seen the preview and said yes.
        include_documents: Also delete the documents that mention it
            (only when {OWNER} says so).
    """
    from services import forget_service as fs

    turn = _current_turn()
    source = getattr(turn, "source", None) if turn is not None else None
    if turn is not None and source not in FORGET_TURN_SOURCES:
        return f"Tool error: forget_about runs only in a conversation with {owner_name()}."
    cid = get_current_conversation_id()
    turn_id = getattr(turn, "id", None) if turn is not None else None
    try:
        if not confirm:
            return fs.format_plan_preview(await fs.plan_forget(subject, conversation_id=cid, turn_id=turn_id))
        plan = fs.latest_plan_for(subject, cid)
        if plan is None:
            preview = await fs.plan_forget(subject, conversation_id=cid, turn_id=turn_id)
            return (f"Nothing was deleted: {owner_name()} has to see what matches first.\n\n"
                    + fs.format_plan_preview(preview))
        if turn_id is not None and plan.turn_id == turn_id:
            return (f"Tool error: {owner_name()} has not seen the preview yet. Show them the list and confirm on "
                    "their reply; nothing was deleted.")
        result = await fs.apply_forget(plan_id=plan.id, include_documents=bool(include_documents),
                                       source=source or "chat")
    except ValueError as exc:
        return f"Tool error: {exc}"
    return fs.format_result(result)


FORGET_TOOLS = [forget_about]


@tool
@owner_doc
async def suggest_idea(title: str, why: str, prompt: str) -> str:
    """
    Put a tappable idea on {OWNER}'s Today page: something you could do for
    them, drawn from what you know (their goals, their week, a thread).

    "Try it" opens chat with ``prompt`` typed for them to send; "Not now"
    retires it. It lives a week and never buzzes their phone. One good idea
    beats three; do not repeat one they passed on this week.

    Args:
        title: A few words ("Plan Saturday's climb").
        why: One sentence on why now ("Alex is free Saturday and you said
            you miss climbing").
        prompt: What {OWNER} would send you, in their voice ("Help me plan a
            climbing session with Alex on Saturday").
    """
    from services import notes_service as ns

    title_s = " ".join((title or "").split())[:200]
    why_s = " ".join((why or "").split())
    prompt_s = (prompt or "").strip()
    if not title_s or not why_s or not prompt_s:
        return "Tool error: an idea needs a title, a why and a prompt."
    if len(prompt_s) > ns.MAX_IDEA_PROMPT_CHARS:
        return f"Tool error: keep the prompt under {ns.MAX_IDEA_PROMPT_CHARS} characters."
    seen = await ns.idea_seen_this_week(title_s)
    if seen == "live":
        return f"Already on Today: \"{title_s}\". Nothing added."
    if seen == "resolved":
        return f"{_owner_title()} already tried or said not now to \"{title_s}\" this week. Not suggested again."
    note_id = await ns.leave_idea(title=title_s, why=why_s, prompt=prompt_s)
    return f"ok (idea {note_id}) — on Today for a week." if note_id else "Tool error: the idea could not be written."


IDEA_TOOLS = [suggest_idea]

# Goals and ideas are not bound since 2026-10-11 (design decision):
# goals_service is gone, and Today no longer shows ideas. GOAL_TOOLS and
# IDEA_TOOLS stay defined only until the v2 toolset replaces this module.
PRODUCT_TOOLS = PROVENANCE_TOOLS + FORGET_TOOLS
PRODUCT_TOOL_NAMES = {t.name for t in PRODUCT_TOOLS}

# The one line that matters most (2026-09-21): an agent that narrates
# where its knowledge came from, instead of reading the record, lies.
PROVENANCE_GUIDANCE = owner_text(
    "When {OWNER} asks how you know something or where you got it, call `how_do_i_know` and answer only "
    "from its records. Never describe your own data access from memory."
)


def get_product_tools_description(tool_names: Optional[set] = None) -> str:
    """Provenance and forget — one short section, a line per bound tool
    (goals and ideas went on 2026-10-11)."""
    names = set(tool_names) if tool_names is not None else PRODUCT_TOOL_NAMES
    lines = ["", "## Where you know things from, and forgetting", ""]
    if names & {"set_goal", "update_goal", "list_goals"}:
        lines.append(f"- `set_goal` when {owner_name()} wants to work toward something over weeks (a `Goal:` document plus "
                     "check-in wakes); `update_goal` to log progress you see, or pause/finish it; `list_goals`.")
    if "suggest_idea" in names:
        lines.append(f"- `suggest_idea(title, why, prompt)`: a tappable idea on Today from {owner_name()}'s goals and week; "
                     "it never buzzes.")
    if "how_do_i_know" in names:
        lines.append(f"- **{PROVENANCE_GUIDANCE}**")
    if "forget_about" in names:
        lines.append(f"- `forget_about(subject)` when {owner_name()} asks you to forget someone or something: show "
                     "them the preview, then `confirm=true` on their reply.")
    return "\n".join(lines) + "\n" if len(lines) > 3 else ""


_MIND_TOOLS_DESCRIPTION = owner_text("""
## Notes and wakes

- `leave_note(title, body, needs_you?, url?)`: put something on {OWNER}'s Today page without buzzing their phone. A need when `needs_you` is true, otherwise the digest. It expires on its own.
- `wake_me(when, why, memory_id?)`: wake yourself at a time you choose, with why; the next wake reads it. Pair it with `remember(... memory_type="intention")` and pass that memory_id so the thing you are watching survives between wakes — an intention a pending wake points at is never forgotten; one nothing waits on ages out.

## Your state document

One document, titled exactly `Edward: state` (tags `state,self`), says who you are and what you are doing: what you are watching for {OWNER}, what you are in the middle of, what you have learned about how they want you to work. Every wake reads it first. `edit_document` it any time something about yourself changes — not only on Sundays — and keep it under 4,000 characters: a page, not a log. The diary is your memories; this is the summary you carry.
""")


def get_mind_tools_description() -> str:
    """Notes and self-scheduled wakes — the quiet half of acting."""
    return _MIND_TOOLS_DESCRIPTION


_WISHLIST_TOOLS_DESCRIPTION = owner_text("""
## Your wishlist

You cannot change your own code. What you can do is notice — a tool you reach for and do not have, a failure that keeps happening and that a code change would fix, something that would plainly help {OWNER} — and write it down for them.

- `wish_for_feature(title, why, detail?, priority?)`: log a wish. Be specific: what it is, why it matters (what went wrong, or what it would let you do), and what the ideal behaviour looks like. Priority 1 is for things that block you, 2 is the default, 3 is a nice-to-have.
- `list_wishlist(status?)`: what you have already asked for and where each item stands. Check it first; wishing for something already on the list adds to that wish instead of repeating it, and the count is what tells {OWNER} it matters.
- `update_wish(wish_id, status?, detail?)`: add what you learned, or move an item when {OWNER} tells you where it stands.

The list lives in Settings → Work → Wishlist; {OWNER} reads it and builds what they agree with. Never promise {OWNER} you will build something yourself — you will not; say you have put it on the wishlist.
""")


def get_wishlist_tools_description() -> str:
    """The wishlist, in one story: you cannot change your own code; the owner builds."""
    return _WISHLIST_TOOLS_DESCRIPTION
