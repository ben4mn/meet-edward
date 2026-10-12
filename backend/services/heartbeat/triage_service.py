"""One heartbeat cycle: what arrived since the last look goes to the mind.

A cycle takes the pending events the listeners stored, does the clean-up
that is not a judgment (expired listening windows dropped, an SMS
compliance footer stripped from a message's text), and hands the batch to
the mind path (``mind_path.run_mind_cycle``): identity filters only, the
rest escalated to Edward's mind with a wake request. The mind decides what,
if anything, to do about it.

This module used to be the legacy three-layer triage engine (a rule chain,
a background-LLM classifier, then its own reply turns), with a dozen
learners and sub-engines around it. It sat dormant behind
``heartbeat_config.mind_enabled`` and was tuned to one person's message
traffic, so it is gone; the mind path is the only path. What stays is what
the rest of Edward still imports from here:

* ``run_triage_cycle`` — one cycle (``heartbeat_service`` runs it).
* the chat stand-down (``record_standdown``, ``_get_active_standdown``,
  ``_release_standdown``) — the send tools, the mind path and the ping
  runner use it.
* ``register_listening_window_for_chat`` — every iMessage send path calls it.
* ``_count_recent_sends`` — the send review's count of what Edward has
  already said in a chat.
* ``MENTION_PATTERN`` — what an ``@edward`` looks like.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from services.database import HeartbeatEventModel, async_session

logger = logging.getLogger(__name__)


MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)


# ===== Listening windows (conversation continuity) =====
#
# Every iMessage send registers a short window on its chat. The legacy
# rule chain read it to treat the next inbound message as a follow-up; the
# mind path does not, but registering still maps the chat to its stable
# conversation (``imessage_conversation_service``), and the send paths and
# their tests rely on the call.

LISTENING_WINDOW_DURATION = timedelta(minutes=5)


@dataclass
class ListeningWindow:
    conversation_id: str
    chat_identifier: str
    expires_at: datetime
    system_prompt: str
    model: str
    temperature: float


# Active listening windows keyed by chat_identifier
_active_listeners: dict[str, ListeningWindow] = {}


async def register_listening_window_for_chat(
    chat_identifier: str, *, conversation_id: Optional[str] = None
) -> bool:
    """Register (or extend) a listening window for ``chat_identifier``.

    Called from every Edward iMessage send path (the send tools, scheduled
    events, workers, manual tool use).

    Returns True if a window was registered. Idempotent — re-calling just
    extends the expiry.
    """
    try:
        from services.settings_service import get_settings

        if conversation_id is None:
            try:
                from services.imessage_conversation_service import (
                    get_or_create_for_chat,
                )

                conversation_id = await get_or_create_for_chat(
                    chat_identifier, chat_name=None, is_group=False
                )
            except Exception as e:
                logger.warning(
                    "register_listening_window: conv lookup failed for %s: %s",
                    chat_identifier,
                    e,
                )
                return False

        settings = await get_settings()
        _active_listeners[chat_identifier] = ListeningWindow(
            conversation_id=conversation_id,
            chat_identifier=chat_identifier,
            expires_at=datetime.utcnow() + LISTENING_WINDOW_DURATION,
            system_prompt=settings.system_prompt,
            model=settings.model,
            temperature=settings.temperature,
        )
        print(
            f"[Heartbeat] Listening window registered from send for "
            f"{chat_identifier} (conv {conversation_id[:8]}...)"
        )
        return True
    except Exception as e:
        logger.warning(
            "register_listening_window_for_chat(%s) failed: %s", chat_identifier, e
        )
        return False


def _cleanup_expired_listeners() -> int:
    """Remove expired listening windows. Returns count removed."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expired = [k for k, v in _active_listeners.items() if v.expires_at <= now]
    for k in expired:
        del _active_listeners[k]
    return len(expired)


# ===== Inbound normalization (not a judgment) =====
#
# Short-code senders append opt-out boilerplate ("Reply STOP to cancel",
# "Msg & data rates may apply"). The cleaned body is stored beside the
# original (``raw_data.normalized_body``, which the mind path reads first)
# so the mind sees what the message says, not its legal footer.

SMS_COMPLIANCE_FOOTER_PATTERNS = [
    (re.compile(r"\breply\s+stop(?:\s+to\s+(?:cancel|opt\s*out|unsubscribe|end|quit))?\b", re.IGNORECASE), "reply_stop"),
    (re.compile(r"\bstop\s+to\s+(?:cancel|opt\s*out|unsubscribe|end|quit)\b", re.IGNORECASE), "stop_to_opt_out"),
    (re.compile(r"\b(?:text|reply)\s+(?:help|info)\s+for\s+(?:help|info)\b", re.IGNORECASE), "help_disclosure"),
    (re.compile(r"\bmsg\s*&\s*data\s+rates\s+may\s+apply\b", re.IGNORECASE), "msg_data_rates"),
    (re.compile(r"\bmessage\s+and\s+data\s+rates\s+may\s+apply\b", re.IGNORECASE), "message_data_rates"),
    (re.compile(r"\b(?:terms|privacy)(?:\s*:|\s+at)?\s*(?:https?://\S+|www\.\S+)\b", re.IGNORECASE), "terms_privacy_link"),
    (re.compile(r"\b(?:sent|powered)\s+by\s+(?:short\s*code|sms)\b", re.IGNORECASE), "short_code_disclosure"),
]

SMS_COMPLIANCE_FOOTER_MAX_LINES = 5


def _raw_data_dict(event: HeartbeatEventModel) -> dict:
    if not event.raw_data:
        return {}
    try:
        payload = json.loads(event.raw_data)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _raw_event_message_text(event: HeartbeatEventModel) -> str:
    """Original inbound text before local normalization."""
    if event.raw_data:
        try:
            payload = json.loads(event.raw_data)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            full_text = payload.get("full_text")
            if isinstance(full_text, str) and full_text.strip():
                return full_text.strip()
    return (event.summary or "").strip()


def _split_sms_compliance_footer(text: str) -> tuple[str, list[str], list[str]]:
    """Strip common SMS opt-out/compliance boilerplate from the tail only."""
    original = text or ""
    if not original.strip():
        return "", [], []

    lines = original.splitlines()
    stripped_lines = list(lines)
    footer_lines: list[str] = []
    matched_terms: list[str] = []

    while stripped_lines and len(footer_lines) < SMS_COMPLIANCE_FOOTER_MAX_LINES:
        candidate = stripped_lines[-1].strip()
        if not candidate:
            footer_lines.insert(0, stripped_lines.pop())
            continue

        line_terms = [
            label
            for pattern, label in SMS_COMPLIANCE_FOOTER_PATTERNS
            if pattern.search(candidate)
        ]
        compact_short_code_disclosure = (
            len(candidate) <= 120
            and re.search(r"\b(?:stop|help)\b", candidate, re.IGNORECASE)
            and re.search(r"\b(?:msg|message|data|rates|terms|privacy)\b", candidate, re.IGNORECASE)
        )
        if not line_terms and not compact_short_code_disclosure:
            break
        if compact_short_code_disclosure:
            line_terms.append("short_code_disclosure")
        matched_terms.extend(line_terms)
        footer_lines.insert(0, stripped_lines.pop())

    cleaned = "\n".join(stripped_lines).strip()
    if not footer_lines:
        inline_match = None
        for pattern, label in SMS_COMPLIANCE_FOOTER_PATTERNS:
            matches = list(pattern.finditer(original))
            if matches:
                match = matches[-1]
                tail = original[match.start():].strip()
                if len(tail) <= 180 and match.start() >= max(0, len(original) - 220):
                    inline_match = (match.start(), tail, label)
                    break
        if inline_match:
            start, tail, label = inline_match
            cleaned = original[:start].rstrip(" \n\t.;-")
            footer_lines = [tail]
            matched_terms = [label]

    return cleaned, [line.strip() for line in footer_lines if line.strip()], list(dict.fromkeys(matched_terms))


def _set_event_raw_data(event: HeartbeatEventModel, payload: dict) -> None:
    event.raw_data = json.dumps(payload, default=str)


def _normalize_inbound_message(event: HeartbeatEventModel) -> Optional[dict]:
    """Persist the cleaned body + footer metadata before the mind reads it."""
    if event.source not in {"imessage", "sms", "whatsapp"}:
        return None
    if event.is_from_user:
        return None

    original_text = _raw_event_message_text(event)
    cleaned, footer_lines, matched_terms = _split_sms_compliance_footer(original_text)
    if not footer_lines:
        return None

    payload = _raw_data_dict(event)
    payload.setdefault("full_text", original_text)
    payload["normalized_body"] = cleaned
    payload["normalization"] = {
        **(payload.get("normalization") if isinstance(payload.get("normalization"), dict) else {}),
        "sms_footer_stripped": True,
        "sms_compliance_footer_detected": True,
        "stripped_footer_lines": footer_lines,
        "reason_codes": ["sms_footer_stripped", "sms_compliance_footer_detected"],
        "matched_terms": matched_terms,
    }
    _set_event_raw_data(event, payload)
    if cleaned:
        event.summary = cleaned

    metadata = {
        "event_id": event.id,
        "sender": event.sender,
        "chat_identifier": event.chat_identifier,
        "reason_codes": ["sms_footer_stripped", "sms_compliance_footer_detected"],
        "matched_terms": matched_terms,
        "stripped_footer_line_count": len(footer_lines),
        "cleaned_body_empty": not bool(cleaned.strip()),
    }
    logger.info(
        "heartbeat_sms_message_normalized %s",
        json.dumps(metadata, default=str, sort_keys=True),
    )
    return payload["normalization"]


# ===== Edward's own footprint in a chat =====
#
# Presence awareness (2026-07-08): the send review gets real data about how
# much Edward has already said in a chat — the count of ledger sends in the
# trailing window — instead of guessing. No cap; the number informs judgment.
RECENT_SENDS_WINDOW_MINUTES = 60


async def _count_recent_sends(chat_identifier: Optional[str]) -> int:
    """Edward's sends to this chat in the trailing window, from the
    imessage_outgoing_sends ledger. Fails open to 0 — presence context is
    advisory and must never block a send."""
    if not chat_identifier:
        return 0
    try:
        from sqlalchemy import func as sa_func

        from services.database import ImessageOutgoingSendModel

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=RECENT_SENDS_WINDOW_MINUTES)
        async with async_session() as session:
            result = await session.execute(
                select(sa_func.count(ImessageOutgoingSendModel.id))
                .where(ImessageOutgoingSendModel.chat_identifier == chat_identifier)
                .where(ImessageOutgoingSendModel.sent_at >= cutoff)
            )
            return int(result.scalar() or 0)
    except Exception as exc:
        print(f"[Heartbeat] recent-send count failed: {exc}")
        return 0


# ===== Binding chat stand-down =====
#
# (2026-07-09) When someone in a chat tells Edward to stay out, that consent
# revocation is recorded (by Edward himself, via the stand_down tool) and
# ENFORCED: the send tools refuse that chat in turns the owner is not part
# of, and anything that needs attention there goes to the owner instead.
# Released by a fresh @edward mention (a re-invitation) or expiry.
STANDDOWN_DURATION_HOURS = 24


async def record_standdown(chat_identifier: str, requested_by: Optional[str] = None) -> None:
    """Record (or refresh) a stand-down for a chat."""
    from services.database import ChatStanddownModel

    now = datetime.now(timezone.utc)
    async with async_session() as session:
        row = await session.get(ChatStanddownModel, chat_identifier)
        if row is None:
            row = ChatStanddownModel(chat_identifier=chat_identifier)
            session.add(row)
        row.requested_by = requested_by
        row.requested_at = now
        row.expires_at = now + timedelta(hours=STANDDOWN_DURATION_HOURS)
        row.released_at = None
        await session.commit()
    print(f"[Heartbeat] STAND-DOWN recorded for {chat_identifier} (by {requested_by or 'unknown'})")


async def _get_active_standdown(chat_identifier: Optional[str]):
    """Active stand-down row for a chat, or None. Fails open to None — a DB
    hiccup must not wedge a cycle (worst case Edward is merely un-muted)."""
    if not chat_identifier:
        return None
    try:
        from services.database import ChatStanddownModel

        async with async_session() as session:
            row = await session.get(ChatStanddownModel, chat_identifier)
        if row is None or row.released_at is not None:
            return None
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires is not None and expires <= datetime.now(timezone.utc):
            return None
        return row
    except Exception as exc:
        print(f"[Heartbeat] standdown lookup failed: {exc}")
        return None


async def _release_standdown(chat_identifier: str) -> None:
    from services.database import ChatStanddownModel

    async with async_session() as session:
        row = await session.get(ChatStanddownModel, chat_identifier)
        if row is not None:
            row.released_at = datetime.now(timezone.utc)
            await session.commit()
    print(f"[Heartbeat] stand-down released for {chat_identifier} (re-invited)")


# ===== The cycle =====

PENDING_BATCH_LIMIT = 100


async def run_triage_cycle(cycle_number: int) -> None:
    """Run one cycle: fetch pending events, normalize them, hand them to the mind.

    The mind path is the only path since the legacy engine left, so
    ``heartbeat_config.mind_enabled`` no longer chooses between two engines
    (the column stays for the API and the Listening panel).
    """
    # Clean up expired listening windows
    expired = _cleanup_expired_listeners()
    if expired:
        print(f"[Heartbeat] Cleaned up {expired} expired listening window(s)")

    # Skills guardrail: alert on a burst of skill disables (2026-07-05 incident)
    try:
        from services.skills_service import check_mass_disable_burst
        await check_mass_disable_burst()
    except Exception as exc:
        print(f"[Heartbeat] skills mass-disable check failed: {exc}")

    async with async_session() as session:
        result = await session.execute(
            select(HeartbeatEventModel)
            .where(HeartbeatEventModel.triage_status == "pending")
            .order_by(HeartbeatEventModel.created_at.asc())
            .limit(PENDING_BATCH_LIMIT)
        )
        pending_events = list(result.scalars().all())

        if not pending_events:
            return

        print(f"[Heartbeat] Triage cycle #{cycle_number}: {len(pending_events)} pending events")

        normalization_count = 0
        for event in pending_events:
            if _normalize_inbound_message(event):
                normalization_count += 1
        if normalization_count:
            logger.info(
                "heartbeat_sms_message_normalization_cycle %s",
                json.dumps({
                    "cycle_number": cycle_number,
                    "normalized_events": normalization_count,
                    "reason_codes": ["sms_footer_stripped", "sms_compliance_footer_detected"],
                }, default=str, sort_keys=True),
            )

        # Identity filters only, then the mind (it commits the batch).
        from services.heartbeat.mind_path import run_mind_cycle
        await run_mind_cycle(pending_events, session=session, cycle_number=cycle_number, mention_re=MENTION_PATTERN)
