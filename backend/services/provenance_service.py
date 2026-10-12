"""Provenance — "how do you know that?" answered from records, never narration.

Why (2026-09-24): another assistant, asked how it knew something
from a user's texts, said it only saw "the incoming notification stream".
It had synced their whole chat.db. It described its own plumbing from
nothing, and that one false sentence cost more trust than any capability
had earned. Edward already keeps ``origin``/``about`` and the source
conversation on every memory; ``memories.source_ref`` adds a small JSON of
where the row came from, and ``how_do_i_know`` returns only what the rows
say, ending with the rule that the answer comes from them.

``source_ref`` (≤ ``MAX_SOURCE_REF_CHARS``), keys present only when known:

* ``kind`` — ``remember`` (Edward wrote it in a turn), ``extraction``
  (post-turn extraction), or whatever a caller names;
* ``conversation_id``, ``turn_id``, ``turn_source`` — the turn it came from
  (``turn_ledger.current_turn()``);
* ``profile`` — the extraction profile (``chat``, ``heartbeat``, ``mind``);
* anything a caller put in ``source_context(...)`` around the turn —
  heartbeat event ids, a chat identifier, chat.db rowids, a URL, a wake
  reason;
* ``also`` — later sources that reinforced the same memory (the first
  source stays on top; at most ``MAX_ALSO``).

Folded engine rows (Phase 8a) keep ``source_conversation_id="fold:<table>:<id>"``
and are named as such.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from services.owner import owner_text

MAX_SOURCE_REF_CHARS = 1000
MAX_LIST_ITEMS = 10
MAX_ALSO = 5
MAX_VALUE_CHARS = 200
CLAIM_MATCHES = 3

PROVENANCE_RULE = (
    "Answer only from these records. If they don't show where it came from, say you don't know."
)

_ORIGIN_WORDS = {
    "heard": owner_text("{OWNER} said it"),
    "read": "you read it (a message, email, page or screen)",
    "thought": "you concluded or decided it",
    "did": "something you did, and how it went",
}
_ABOUT_WORDS = {
    "owner": owner_text("about {OWNER}"),
    "contact": "about someone else",
    "self": "about yourself",
    "world": "about the world",
}

_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("edward_source_context", default={})


# ---------------------------------------------------------------- pure --

def _clean_value(v: Any) -> Any:
    if isinstance(v, (list, tuple, set)):
        return [_clean_value(x) for x in list(v)[:MAX_LIST_ITEMS] if x not in (None, "")]
    if isinstance(v, dict):
        return {str(k): _clean_value(x) for k, x in list(v.items())[:MAX_LIST_ITEMS] if x not in (None, "", [], {})}
    if isinstance(v, str):
        return v[:MAX_VALUE_CHARS]
    return v


def build_source_ref(
    kind: str,
    *,
    conversation_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    turn_source: Optional[str] = None,
    profile: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The dict stored (as JSON) in ``memories.source_ref``. Empty values
    are left out; lists are capped."""
    ref: Dict[str, Any] = {"kind": kind, "conversation_id": conversation_id, "turn_id": turn_id,
                           "turn_source": turn_source, "profile": profile}
    for k, v in (extra or {}).items():
        if k not in ref or ref[k] is None:
            ref[k] = v
    return {k: _clean_value(v) for k, v in ref.items() if v not in (None, "", [], {})}


def dump_source_ref(ref: Optional[Dict[str, Any]]) -> Optional[str]:
    """JSON, bounded. Drops ``also`` entries, then list tails, until it fits."""
    if not ref:
        return None
    ref = dict(ref)
    text = json.dumps(ref, separators=(", ", ": "), default=str)
    while len(text) > MAX_SOURCE_REF_CHARS and ref.get("also"):
        ref["also"] = ref["also"][:-1] or None
        if ref["also"] is None:
            ref.pop("also")
        text = json.dumps(ref, separators=(", ", ": "), default=str)
    while len(text) > MAX_SOURCE_REF_CHARS:
        longest = max((k for k, v in ref.items() if isinstance(v, (list, str)) and k != "kind"),
                      key=lambda k: len(json.dumps(ref[k], default=str)), default=None)
        if longest is None:
            break
        v = ref[longest]
        ref[longest] = v[:-1] if isinstance(v, list) and len(v) > 1 else (v[: len(v) // 2] if isinstance(v, str) else None)
        if ref[longest] in (None, "", []):
            ref.pop(longest)
        text = json.dumps(ref, separators=(", ", ": "), default=str)
    return text[:MAX_SOURCE_REF_CHARS]


def parse_source_ref(text: Optional[str]) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def merge_source_ref(existing: Optional[str], new: Optional[str]) -> Optional[str]:
    """A reinforced memory keeps where it first came from; the new source
    joins ``also`` (newest last, at most ``MAX_ALSO``).

    A row with no readable provenance (stored before 2026-09-24) keeps
    ``unrecorded`` on top (2026-09-27): returning ``new`` made the first
    source to repeat it its origin — a row from the owner's chat came to
    read as a wake's, and a wake's ref decides whose instruction a row is."""
    if not new:
        return existing
    incoming = parse_source_ref(new)
    if not incoming:
        return existing
    base = parse_source_ref(existing)
    if not base:
        base = build_source_ref("unrecorded")
    incoming.pop("also", None)
    also = [a for a in (base.get("also") or []) if isinstance(a, dict)]
    if incoming not in also:
        also.append(incoming)
    base["also"] = also[-MAX_ALSO:]
    return dump_source_ref(base)


def describe_origin(origin: Optional[str], about: Optional[str]) -> str:
    o = _ORIGIN_WORDS.get(origin or "heard", origin or "?")
    a = _ABOUT_WORDS.get(about or "owner", about or "?")
    return f"{o}, {a}"


def fold_source(source_conversation_id: Optional[str]) -> Optional[Tuple[str, str]]:
    """``fold:<table>:<id>`` → (table, id): a row folded from a retired
    engine table in Phase 8a."""
    s = source_conversation_id or ""
    if not s.startswith("fold:"):
        return None
    parts = s.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


def _fmt_time(value: Any) -> str:
    """A stored time (UTC; naive is UTC) as the owner's clock, named: "Wed
    2026-10-07 6:58 pm CDT". The old cut at 20 characters dropped the
    " UTC", so an evening find read as a time five hours later — the next
    day's after 7 pm in US Central time (judgement check, 2026-10-11)."""
    if not value:
        return "not recorded"
    try:
        from services.timezone_utils import utc_to_local

        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        local = utc_to_local(dt)
    except (TypeError, ValueError):
        return str(value)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    return f"{local:%a %Y-%m-%d} {clock} {local:%Z}".strip()


def format_record(i: int, r: Dict[str, Any]) -> List[str]:
    m = r.get("memory") or {}
    conf = m.get("confidence")
    conf_s = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "?"
    lines = [
        f"{i}. memory {m.get('id')} — \"{m.get('content')}\"",
        f"   origin/about: {m.get('origin') or 'heard'}/{m.get('about') or 'owner'} "
        f"({describe_origin(m.get('origin'), m.get('about'))}) · source_type: {m.get('source_type') or '?'} "
        f"· confidence {conf_s} · tier {m.get('tier') or 'observation'} "
        f"(reinforced {int(m.get('reinforcement_count') or 0)}×)",
        f"   stored: {_fmt_time(m.get('created_at'))}",
    ]
    fold = r.get("fold")
    conv = r.get("conversation")
    scid = m.get("source_conversation_id")
    if fold:
        lines.append(f"   source: folded from the retired `{fold[0]}` table (row {fold[1]}) when the engine "
                     "layer was dropped; the original conversation is not recorded")
    elif conv:
        lines.append(f"   source conversation: \"{conv.get('title') or 'Untitled'}\" (id {conv.get('id')}, "
                     f"source {conv.get('source') or 'user'}, channel {conv.get('channel') or 'text'})")
    elif scid:
        lines.append(f"   source conversation: id {scid} (the thread no longer exists)")
    else:
        lines.append("   source conversation: not recorded")
    chat = r.get("chat")
    if chat:
        lines.append(f"   that conversation is the {'group ' if chat.get('is_group') else ''}iMessage chat "
                     f"{chat.get('chat_identifier')}" + (f" (\"{chat['chat_name']}\")" if chat.get("chat_name") else ""))
    contact = r.get("contact")
    if contact:
        lines.append(f"   that conversation is the {contact.get('platform') or 'sms'} thread with "
                     f"{contact.get('phone_number')}" + (f" ({contact['contact_name']})" if contact.get("contact_name") else ""))
    ref = r.get("source_ref") or {}
    if ref:
        lines.append(f"   source_ref: {json.dumps(ref, separators=(', ', ': '), default=str)}")
    else:
        lines.append("   source_ref: not recorded (stored before 2026-09-24, or by a path that does not record one)")
    return lines


def format_provenance(records: List[Dict[str, Any]], *, claim: Optional[str] = None,
                      memory_id: Optional[str] = None) -> str:
    """The tool's answer: each record's stored fields, then the rule."""
    if not records:
        what = f"the claim \"{claim}\"" if claim else f"id {memory_id}" if memory_id else "that"
        return f"No memory matches {what}. Nothing on record says where it came from.\n\n{PROVENANCE_RULE}"
    head = (f"Records for \"{claim}\" ({len(records)} closest memor{'y' if len(records) == 1 else 'ies'}):"
            if claim else "Record:")
    lines = [head, ""]
    for i, r in enumerate(records, 1):
        lines.extend(format_record(i, r))
        lines.append("")
    lines.append(PROVENANCE_RULE)
    return "\n".join(lines)


def summary_line(r: Dict[str, Any]) -> str:
    """One human line for the Memory room ("Where this came from")."""
    m = r.get("memory") or {}
    parts = [describe_origin(m.get("origin"), m.get("about")).split(",")[0]]
    fold = r.get("fold")
    conv = r.get("conversation")
    ref = r.get("source_ref") or {}
    if fold:
        parts.append(f"folded from the old {fold[0]} table")
    elif conv:
        parts.append(f"in “{conv.get('title') or 'Untitled'}”")
    elif m.get("source_conversation_id"):
        parts.append("in a conversation that no longer exists")
    if r.get("chat"):
        parts.append(f"(iMessage {r['chat'].get('chat_name') or r['chat'].get('chat_identifier')})")
    if ref.get("kind") == "remember":
        parts.append("· Edward wrote it himself")
    elif ref.get("kind") == "extraction":
        parts.append(f"· picked up after a {ref.get('turn_source') or ref.get('profile') or 'chat'} turn")
    if not conv and not fold and not m.get("source_conversation_id") and not ref:
        return "Not recorded — this memory predates provenance, or came in by a path that does not record it."
    return " ".join(p for p in parts if p)


# ------------------------------------------------------ task-local context --

def current_source_context() -> Dict[str, Any]:
    return dict(_context.get())


@contextlib.contextmanager
def source_context(**fields: Any) -> Iterator[Dict[str, Any]]:
    """Wrap a turn so the memories it writes record where the input came
    from (``event_ids``, ``chat_identifier``, ``rowids``, ``url``,
    ``wake_reason`` …). Task-local; nested contexts merge."""
    merged = {**_context.get(), **{k: v for k, v in fields.items() if v not in (None, "", [], {})}}
    token = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


def heartbeat_source_fields(event: Any) -> Dict[str, Any]:
    """What a heartbeat turn should wrap itself in so the memories it
    extracts point at the message that caused them: the event id, the
    chat, and the chat.db rowid (``source_id`` is ``imessage:<rowid>``).
    Pure; missing fields are left out."""
    fields: Dict[str, Any] = {}
    eid = getattr(event, "id", None)
    if eid:
        fields["event_ids"] = [str(eid)]
    chat = getattr(event, "chat_identifier", None)
    if chat:
        fields["chat_identifier"] = str(chat)
    src = str(getattr(event, "source", "") or "")
    if src:
        fields["event_source"] = src
    source_id = str(getattr(event, "source_id", "") or "")
    if source_id.startswith("imessage:") and source_id[len("imessage:"):].isdigit():
        fields["rowids"] = [int(source_id[len("imessage:"):])]
    elif source_id:
        fields["source_ids"] = [source_id]
    return fields


def wake_source_fields(reason: str, escalations: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What a mind wake should wrap itself in: its reason and the
    heartbeat events it was served. Pure."""
    fields: Dict[str, Any] = {"wake_reason": reason}
    ids = [str(e.get("id")) for e in (escalations or []) if e.get("id")]
    if ids:
        fields["event_ids"] = ids[:MAX_LIST_ITEMS]
    return fields


def turn_source_ref(kind: str, *, conversation_id: Optional[str] = None,
                    profile: Optional[str] = None) -> Optional[str]:
    """The ``source_ref`` JSON for a memory written now: the current turn
    (if any), the caller's ``source_context``, and what was passed in."""
    turn_id = turn_source = None
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
        if rec is not None:
            turn_id, turn_source = rec.id, rec.source
            conversation_id = conversation_id or rec.conversation_id
    except Exception:  # noqa: BLE001 — provenance never breaks a write
        pass
    return dump_source_ref(build_source_ref(kind, conversation_id=conversation_id, turn_id=turn_id,
                                            turn_source=turn_source, profile=profile,
                                            extra=current_source_context()))


# ------------------------------------------------------------------ I/O --

def _memory_dict(m: Any) -> Dict[str, Any]:
    created = getattr(m, "created_at", None)
    return {
        "id": m.id,
        "content": m.content,
        "memory_type": m.memory_type,
        "origin": getattr(m, "origin", "heard") or "heard",
        "about": getattr(m, "about", "owner") or "owner",
        "source_type": getattr(m, "source_type", "inferred") or "inferred",
        "confidence": getattr(m, "confidence", None),
        "tier": getattr(m, "tier", "observation") or "observation",
        "reinforcement_count": getattr(m, "reinforcement_count", 0) or 0,
        "source_conversation_id": getattr(m, "source_conversation_id", None),
        "created_at": (created.isoformat() + "Z") if created is not None and created.tzinfo is None
        else (created.isoformat() if created is not None else None),
    }


async def _record_for(m: Any) -> Dict[str, Any]:
    from sqlalchemy import select

    from services.database import (
        ConversationModel,
        ExternalContactModel,
        IMessageChatConversationModel,
        async_session,
    )

    scid = getattr(m, "source_conversation_id", None)
    fold = fold_source(scid)
    conv = chat = contact = None
    if scid and not fold:
        async with async_session() as session:
            c = (await session.execute(select(ConversationModel).where(ConversationModel.id == scid))).scalar_one_or_none()
            if c is not None:
                conv = {"id": c.id, "title": c.title, "source": c.source, "channel": c.channel}
            ch = (await session.execute(
                select(IMessageChatConversationModel).where(IMessageChatConversationModel.conversation_id == scid)
            )).scalars().first()
            if ch is not None:
                chat = {"chat_identifier": ch.chat_identifier, "chat_name": ch.chat_name, "is_group": bool(ch.is_group)}
            ec = (await session.execute(
                select(ExternalContactModel).where(ExternalContactModel.conversation_id == scid)
            )).scalars().first()
            if ec is not None:
                contact = {"phone_number": ec.phone_number, "contact_name": ec.contact_name, "platform": ec.platform}
    return {
        "memory": _memory_dict(m),
        "conversation": conv,
        "chat": chat,
        "contact": contact,
        "fold": fold,
        "source_ref": parse_source_ref(getattr(m, "source_ref", None)),
    }


async def provenance_for(memory_id: str) -> Optional[Dict[str, Any]]:
    """Everything stored about where one memory came from, or None."""
    from services.memory_service import get_memory_by_id

    m = await get_memory_by_id(memory_id)
    if m is None:
        return None
    record = await _record_for(m)
    record["summary"] = summary_line(record)
    return record


async def provenance_for_claim(claim: str, limit: int = CLAIM_MATCHES) -> List[Dict[str, Any]]:
    """The records of the ``limit`` memories closest to a claim (hybrid search)."""
    from services.memory_service import get_memory_by_id, search_memories

    hits, _ = await search_memories(query=claim, limit=limit)
    out = []
    for h in hits[:limit]:
        m = await get_memory_by_id(h.id)
        if m is not None:
            out.append(await _record_for(m))
    return out
