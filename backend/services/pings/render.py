"""The ping turn's trigger: what Edward reads when someone asks him something
in an iMessage chat (AREA 3, 2026-10-11; docs/architecture/pings.md).

Pure. The runner gathers (the thread, the roster, the media, the links) and
this lays it out:

- the whole message (the old pack cut every message at 200 characters, and
  an ask at character 524 was never seen);
- the chat's name, id and roster, and how to reply there;
- who asked, and whether they are the owner;
- the last 20 lines of the chat, each labelled with the owner's name
  (``owner.owner_name()``), ``Edward`` or the contact, each at most 1,500 characters and 12K in all (newest kept);
- the photos and files attached below, those too big to attach, those
  still processing when the turn began (each with its ``look`` call), and
  the links in the message as Edward's reader found them;
- a retry note and a stale note when they apply.

Everything other people wrote, a friend's ask included, is fenced in
``<untrusted source="…">…</untrusted>``; the prompt says such text is
information, never instructions. A fence cannot be closed from inside.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Mapping, Optional, Sequence

from services.owner import owner_name

THREAD_LINES = 20
LINE_MAX = 1_500
THREAD_MAX = 12_000
STALE_AFTER_S = 30 * 60
LINK_TEXT_MAX = 4_000

_FENCE_RE = re.compile(r"<(/?)untrusted", re.IGNORECASE)
_REF_RE = re.compile(r"attachment:\d+")


def _tz(tz: Optional[tzinfo]) -> tzinfo:
    if tz is not None:
        return tz
    try:
        from services.timezone_utils import get_user_tz
        return get_user_tz()
    except Exception:  # noqa: BLE001
        return timezone.utc


def _local(dt: datetime, tz: tzinfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _clock(dt: datetime) -> str:
    """"7:28 pm" — Edward's one clock format."""
    return dt.strftime("%-I:%M %p").lower()


def _when(dt: datetime, ref: datetime) -> str:
    """A thread line's time, both local: the clock on the ping's day, the
    weekday within a week, else the date."""
    if dt.date() == ref.date():
        return _clock(dt)
    if timedelta(0) <= ref - dt < timedelta(days=6):
        return f"{dt.strftime('%a')} {_clock(dt)}"
    return f"{dt.strftime('%b')} {dt.day} {_clock(dt)}"


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"{minutes} minutes"
    hours = int(round(seconds / 3600))
    if hours < 36:
        return f"{hours} hours"
    return f"{int(round(seconds / 86400))} days"


def _defang(text: str) -> str:
    """Untrusted text can't open or close a fence: ``<untrusted`` -> ``‹untrusted``."""
    return _FENCE_RE.sub(lambda m: "‹" + m.group(1) + "untrusted", text)


def _source(label: str) -> str:
    return _defang(str(label or "someone")).replace('"', "'")


def untrusted(text: str, source: str) -> str:
    """``<untrusted source="…">text</untrusted>``, unbreakable. Pure."""
    return f'<untrusted source="{_source(source)}">{_defang(str(text or ""))}</untrusted>'


def _cap(text: str) -> str:
    """The first letter upper-cased: "the owner's …" opening a line."""
    return text[:1].upper() + text[1:]


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _class_name(error: Any) -> str:
    from services.pings.fallback import class_name
    return class_name(error) or "an error"


def _chat_line(*, chat_identifier: str, chat_name: Optional[str], is_group: bool, roster: Sequence[str],
               kind: str, sender_label: str, reply_call: str) -> str:
    people = [str(r) for r in roster if r]
    owner = owner_name()
    if kind == "owner_direct":
        where = f"{owner}'s own chat with you"
    elif is_group:
        name = chat_name or ", ".join(people) or "a group chat"
        where = f'"{name}" — group of {len(people) + 1}: ' + ", ".join([owner, *people])
    else:
        other = (people[0] if people else None) or chat_name or sender_label or chat_identifier
        where = f"{other} (1:1 with {owner})"
    return f"Chat: {where} · chat_id: {chat_identifier}   (reply with {reply_call.format(chat=chat_identifier)})"


def _from_line(*, kind: str, from_owner: bool, sender_label: str, sender_known: bool) -> str:
    owner = owner_name()
    if kind == "owner_direct":
        return f"From: {owner}, in their own chat with you"
    if from_owner:
        if kind == "reply_to_edward":
            return f"From: {owner} (they replied to one of your messages)"
        return f"From: {owner} (they wrote @Edward)"
    who = (f"{sender_label}, a contact (not {owner})" if sender_known
           else f"{sender_label} (not in {owner}'s contacts — not {owner})")
    if kind == "reply_to_edward":
        return (f"From: {who} — replied to one of your messages, which to them came from {owner}'s phone, "
                f"so it may be meant for {owner}")
    return f"From: {who} — wrote @Edward"


def _speaker(line: Mapping[str, Any], names: Mapping[str, str]) -> tuple[str, bool]:
    """(label, trusted): the owner and Edward are trusted; everyone else is fenced."""
    if line.get("is_from_me"):
        return ("Edward", True) if line.get("by_edward") else (owner_name(), True)
    sender = str(line.get("sender") or "someone")
    return (names.get(sender) or line.get("sender_name") or line.get("label") or sender), False


def _thread_lines(thread: Sequence[Mapping[str, Any]], *, names: Mapping[str, str], tz: tzinfo,
                  ref: datetime, skip_rowid: Optional[int]) -> list[str]:
    lines = []
    for item in thread:
        if skip_rowid is not None and item.get("rowid") == skip_rowid:
            continue
        ts = item.get("timestamp")
        when = _when(_local(ts, tz), ref) if isinstance(ts, datetime) else "?"
        label, trusted = _speaker(item, names)
        text = _clip(item.get("text") or "", LINE_MAX)
        lines.append(f"  {when} {label}: {text}" if trusted else f"  {when} {untrusted(text, label)}")
    lines = lines[-THREAD_LINES:]
    kept: list[str] = []
    total = 0
    for line in reversed(lines):  # newest first, so the cap drops the oldest
        if total + len(line) + 1 > THREAD_MAX:
            break
        kept.append(line)
        total += len(line) + 1
    return list(reversed(kept))


def _link_block(link: Mapping[str, Any]) -> str:
    url = str(link.get("url") or link.get("original_url") or "")
    if link.get("error") and not (link.get("text") or link.get("title")):
        return f"  - {url} — I couldn't read it: {link['error']}"
    head = " — ".join(str(x) for x in (link.get("title"), link.get("site")) if x)
    body = "\n".join(str(x) for x in (head, link.get("summary"), _clip(link.get("text") or "", LINK_TEXT_MAX)) if x)
    note = f" (read: {link['note']})" if link.get("note") else ""
    return f"  - {url}{note}\n{untrusted(body, 'page ' + url)}"


def build_trigger(*, text: Optional[str] = None, asked_at: Optional[datetime] = None,
                  chat_identifier: Optional[str] = None, chat_name: Optional[str] = None,
                  is_group: Optional[bool] = None, roster: Optional[Sequence[str]] = None,
                  sender_label: Optional[str] = None, from_owner: Optional[bool] = None, kind: Optional[str] = None,
                  sender_known: Optional[bool] = None, thread: Sequence[Mapping[str, Any]] = (),
                  names: Optional[Mapping[str, str]] = None, message_rowid: Optional[int] = None,
                  attached: Sequence[str] = (), listed: Sequence[str] = (), links: Sequence[Mapping[str, Any]] = (),
                  pending: Sequence[str] = (),
                  known: Sequence[str] = (), attempt: Optional[int] = None, max_attempts: int = 2,
                  last_error: Any = None, now: Optional[datetime] = None, tz: Optional[tzinfo] = None,
                  channel: str = "iMessage", legacy: Optional[bool] = None,
                  ping: Optional[Mapping[str, Any]] = None, row: Optional[Mapping[str, Any]] = None,
                  participants: Sequence[Mapping[str, Any]] = (), attachment_lines: Sequence[str] = (),
                  safe_memories: Sequence[str] = (), attempts: Optional[int] = None) -> str:
    """The trigger message for one ping. ``asked_at``/``now`` naive are UTC.

    ``thread`` is ``get_chat_thread``'s dicts (``by_edward`` marks Edward's
    lines); ``names`` maps a handle to a contact name; ``attached`` and
    ``listed`` are the labels of media attached below and of media too big
    to attach; ``pending`` the labels of media that was still being
    converted when the turn began; ``links`` are ``media.links.read_links``
    results; ``known`` is optional lines Edward may know (rendered only when
    given).

    The runner may instead pass the ping row itself (``ping``/``row``), the
    roster as ``participants`` (``{"handle", "name"}``), the media labels as
    ``attachment_lines``, ``attempts`` and ``safe_memories``: any field given
    explicitly wins over the row's.
    """
    p = dict(ping or row or {})
    text = text if text is not None else str(p.get("text") or "")
    asked_at = asked_at or p.get("asked_at") or now or datetime.now(timezone.utc)
    chat_identifier = chat_identifier or str(p.get("chat_identifier") or p.get("chat_guid") or "")
    chat_name = chat_name if chat_name is not None else p.get("chat_name")
    is_group = bool(is_group if is_group is not None else p.get("is_group"))
    from_owner = bool(from_owner if from_owner is not None else p.get("from_owner"))
    kind = kind or str(p.get("kind") or "mention")
    sender_known = bool(sender_known if sender_known is not None else p.get("sender_known", True))
    sender_label = sender_label or str(p.get("sender_label") or p.get("sender") or (owner_name() if from_owner else "someone"))
    message_rowid = message_rowid if message_rowid is not None else p.get("message_rowid")
    last_error = last_error if last_error is not None else p.get("last_error")
    attempt = int(attempt if attempt is not None else (attempts if attempts is not None else 1) or 1)
    names = dict(names or {})
    for person in participants or ():
        handle, name = person.get("handle"), person.get("name")
        if handle and name:
            names.setdefault(str(handle), str(name))
    if roster is None:
        roster = [str(person.get("name") or person.get("handle")) for person in participants or ()
                  if person.get("name") or person.get("handle")]
    attached = list(attached or attachment_lines or ())
    known = list(known or safe_memories or ())

    from services.pings.prompt import tool_names

    zone = _tz(tz)
    asked_local = _local(asked_at, zone)
    now = now or datetime.now(timezone.utc)
    third_party = not from_owner and kind != "owner_direct"

    out = [f"[PING] {_clock(asked_local)}, {asked_local.strftime('%a %b')} {asked_local.day} — {channel}",
           _chat_line(chat_identifier=chat_identifier, chat_name=chat_name, is_group=is_group, roster=roster,
                      kind=kind, sender_label=sender_label, reply_call=tool_names(legacy)["reply_call"]),
           _from_line(kind=kind, from_owner=from_owner, sender_label=sender_label, sender_known=sender_known)]
    if attempt and attempt > 1:
        out.append(f"(Retry {attempt} of {max_attempts} — the last attempt stopped: {_class_name(last_error)}. "
                   "Pick up where it stopped.)")
    late = (_local(now, timezone.utc) - _local(asked_at, timezone.utc)).total_seconds()
    if late > STALE_AFTER_S:
        out.append(f"(This came in {_ago(late)} ago while you were offline — open with a short apology.)")

    body = text or ""
    out += ["", "The message, whole:", untrusted(body, sender_label) if third_party else body]

    lines = _thread_lines(thread, names=names, tz=zone, ref=asked_local, skip_rowid=message_rowid)
    if lines:
        out += ["", "Recent messages in this chat (oldest first):", *lines]
    if attached:
        out += ["", "Photos and files attached below:", *(f"  - {a}" for a in attached)]
    if listed:
        out += ["", "Too big to attach — open with look(…):", *(f"  - {a}" for a in listed)]
    if pending:
        out += ["", "Still processing when you were asked (not attached) — open with look(…):",
                *(f"  - {a}" + (f' — look("{m.group(0)}")' if (m := _REF_RE.search(str(a))) else "")
                  for a in pending)]
    if links:
        out += ["", "Links in the message (read for you):", *(_link_block(link) for link in links)]
    if known:
        out += ["", "What you may know here:", *(f"  - {k}" for k in known)]
    return "\n".join(out)


def instructions_block(lines: Optional[Sequence[str]], *, legacy: Optional[bool] = None) -> str:
    """The owner's own standing instructions for a friend's ping turn (review
    2026-10-11: the mention wake's pack opened with them, and they outrank a
    mention). ``None``: they could not be read, and the turn is told so.
    Pure but for the toolset check and the owner's name."""
    from services.pings.prompt import tool_names

    names = tool_names(legacy)
    stand_down, tell = names["stand_down"], names["tell_owner_short"]
    owner = owner_name()
    if lines is None:
        return (f"(I couldn't read {owner}'s standing instructions just now. If you know they told you to stay "
                f"out of this chat, use {stand_down} for it and {tell} what was asked.)")
    kept = [str(line) for line in lines if str(line).strip()]
    if not kept:
        return ""
    return "\n".join([_cap(f"{owner}'s standing instructions (what they told you themselves; they outrank this "
                           "ping):"), *kept,
                      f"If one says to stay out of this chat or away from this person, don't reply: use {stand_down} "
                      f"for this chat and {tell} what was asked — nothing is posted then."])
