"""The heartbeat thinks in the mind.

A triage cycle does only what is not a judgment: drop what the owner wrote
themselves, keep one mention per chat, skip what the owner already answered
in this batch. Everything else is handed to the mind as an escalation (raw,
with its thread) and a ``messages`` wake is requested; a mention asks for
an immediate wake that must reply. (This replaced the legacy triage engine,
which is gone.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

MENTION_WAKE_DELAY_S = 0.0
MESSAGES_WAKE_DELAY_S = 60.0


@dataclass
class IdentityVerdict:
    keep: List[Any] = field(default_factory=list)
    mentions: List[Any] = field(default_factory=list)
    dismissed: List[Any] = field(default_factory=list)


def _text(event) -> str:
    raw = getattr(event, "raw_data", None)
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                for key in ("normalized_body", "text", "body"):
                    if payload.get(key):
                        return str(payload[key])
        except (TypeError, ValueError):
            pass
    return str(getattr(event, "summary", "") or "")


# Only an iMessage can ask Edward anything (2026-10-11, the integrations
# audit): an email's subject, a Gmail message or a shared calendar title is
# anyone's text, and an @edward there bought an immediate, paid mention wake
# that had to reply, for an event with no chat. Such a row is an ordinary
# event, kept for the noticer.
MENTION_SOURCES = frozenset({"imessage"})


def _can_ping(event) -> bool:
    return getattr(event, "source", None) in MENTION_SOURCES


def _is_mention(event, mention_re) -> bool:
    """On the whole message: the summary is cut at 200 characters, and an
    ask past that was not seen (2026-10-11). iMessage only."""
    return _can_ping(event) and bool(mention_re.search(_full_text(event)))


def _full_text(event) -> str:
    """The whole message (the summary is cut at 200 characters, which can
    cut off the signature of a long reply)."""
    raw = getattr(event, "raw_data", None)
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("full_text"):
                return str(payload["full_text"])
        except (TypeError, ValueError):
            pass
    return _text(event)


def _raw(event) -> dict:
    raw = getattr(event, "raw_data", None)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_edwards_echo(event) -> bool:
    """Edward's own send echoed back by iCloud — not the owner writing. The
    listener says so structurally (``raw_data.edward_echo``, 2026-10-11:
    the ROWID the transport read back, an in-flight send, the ledger); a
    from-me message that carries Edward's signature is his too (2026-09-24)."""
    if not getattr(event, "is_from_user", False):
        return False
    if _raw(event).get("edward_echo"):
        return True
    from services.mind_service import has_edward_signature

    return has_edward_signature(_full_text(event))


def _is_owner_direct(event) -> bool:
    return _can_ping(event) and bool(_raw(event).get("owner_direct"))


def _is_handled_ping(event) -> bool:
    """A row the ping runner owns (``raw_data.ping``). Such rows are stored
    ``triage_status='ping'`` and never reach a cycle; if one does, it is
    already handled."""
    return bool(_raw(event).get("ping"))


def identity_filter(events: Sequence[Any], *, mention_re) -> IdentityVerdict:
    """Facts about who wrote a message, never judgments about it.

    * ``is_from_user`` → dismissed, unless it is the owner pinging Edward: in
      their self-chat (``raw_data.owner_direct``) or with an ``@edward``
      anywhere (2026-10-11: their own @edward used to be dropped here). Both
      are mentions.
    * Edward's own send echoed back (``raw_data.edward_echo``, or from-me
      and signed) → dismissed, never a ping, and never taken for the owner
      being in that chat.
    * a row the ping runner already owns (``raw_data.ping``) → dismissed.
    * a chat the owner replied in during this batch → its inbound messages
      are dismissed (they are already there). The owner pinging Edward is
      not the owner replying (2026-10-11): their mention or self-chat line
      never silences a friend in the same batch.
    * one mention per chat survives; the rest of that chat's mentions are
      dismissed as duplicates.
    * only an iMessage is ever a mention (``MENTION_SOURCES``): an email,
      a Gmail message or a calendar row with ``@edward`` in it is kept as an
      ordinary event.

    With EDWARD_PINGS on, pings never reach this filter; it serves the
    flag-off and degraded paths.
    """
    verdict = IdentityVerdict()
    replied_chats: Set[str] = {
        getattr(e, "chat_identifier", None) for e in events
        if getattr(e, "is_from_user", False) and getattr(e, "chat_identifier", None) and not _is_owner_direct(e)
        and not _is_edwards_echo(e) and not _is_handled_ping(e) and not _is_mention(e, mention_re)
    }
    mentioned_chats: Set[str] = set()
    for event in events:
        chat = getattr(event, "chat_identifier", None)
        if _is_handled_ping(event):
            verdict.dismissed.append(event)
            continue
        if getattr(event, "is_from_user", False):
            if _is_edwards_echo(event):
                verdict.dismissed.append(event)
            elif _is_owner_direct(event):
                verdict.mentions.append(event)
            elif _is_mention(event, mention_re):
                key = chat or getattr(event, "id", "")
                if key in mentioned_chats:
                    verdict.dismissed.append(event)
                else:
                    mentioned_chats.add(key)
                    verdict.mentions.append(event)
            else:
                verdict.dismissed.append(event)
            continue
        if chat and chat in replied_chats:
            verdict.dismissed.append(event)
            continue
        if _is_mention(event, mention_re):
            key = chat or getattr(event, "id", "")
            if key in mentioned_chats:
                verdict.dismissed.append(event)
                continue
            mentioned_chats.add(key)
            verdict.mentions.append(event)
            continue
        verdict.keep.append(event)
    return verdict


async def _release_standdowns_for(mentions: Sequence[Any]) -> None:
    chats = {getattr(e, "chat_identifier", None) for e in mentions if not _is_owner_direct(e)}
    chats.discard(None)
    if not chats:
        return
    try:
        from services.heartbeat.triage_service import _get_active_standdown, _release_standdown
    except Exception as exc:  # noqa: BLE001
        print(f"[Heartbeat] mind cycle: stand-down helpers unavailable: {exc}")
        return
    for chat in chats:
        try:
            if await _get_active_standdown(chat) is not None:
                await _release_standdown(chat)
        except Exception as exc:  # noqa: BLE001
            print(f"[Heartbeat] mind cycle: could not release the stand-down on {chat}: {exc}")


async def run_mind_cycle(pending_events: Sequence[Any], *, session, cycle_number: int, mention_re) -> dict:
    """Apply the identity filter, hand the rest to the mind, request wakes."""
    from services import mind_service

    verdict = identity_filter(pending_events, mention_re=mention_re)
    now = datetime.utcnow()
    for event in verdict.dismissed:
        event.triage_status = "dismissed"
    for event in verdict.keep + verdict.mentions:
        event.triage_status = "escalated"
        if hasattr(event, "acted_at"):
            event.acted_at = None
    await session.commit()

    summary = {
        "cycle": cycle_number,
        "dismissed": len(verdict.dismissed),
        "escalated": len(verdict.keep),
        "mentions": len(verdict.mentions),
        "wake": None,
    }
    # A fresh @edward mention is a re-invitation into a chat that told Edward
    # to stand down — the old path released it the same way. The send
    # tools refuse a stood-down chat in autonomous turns (2026-09-24), so
    # without this the mention wake could not answer.
    await _release_standdowns_for(verdict.mentions)
    if verdict.mentions:
        # The mentions' own ids ride along (2026-09-26): a retry or a
        # follow-up whose mention is gone (Done on Today, answered, given
        # up) must not be forced to reply into that thread.
        mind_service.request_wake("mention", delay_s=MENTION_WAKE_DELAY_S, required_tools=["send_imessage"],
                                  mention_ids=[str(e.id) for e in verdict.mentions if getattr(e, "id", None)] or None)
        summary["wake"] = "mention"
    elif verdict.keep:
        mind_service.request_wake("messages", delay_s=MESSAGES_WAKE_DELAY_S)
        summary["wake"] = "messages"
    print(f"[Heartbeat] mind cycle #{cycle_number}: {summary['dismissed']} dismissed by identity, "
          f"{summary['escalated']} escalated, {summary['mentions']} mention(s); wake={summary['wake']}")
    return summary
