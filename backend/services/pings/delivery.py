"""The runner's own reply or fallback into the ping's chat (AREA 3,
2026-10-11; docs/architecture/pings.md).

The runner posts only when the turn didn't send: the turn's final text
(``purpose="reply"``), a template (``fallback``) or the rate-limit line
(``limit``). In order:

0. **No tool call written as text** (``services.tool_markup``; the outcome
   rules strip it first, this is the last line of defence): what is left
   of the text is posted, or the ``garbled`` template when nothing is.
1. **The egress secret scan** — the same check as the Twilio reply
   (``routers/webhooks.scrub_outbound_reply``), failing closed: a hit, or a
   broken scan, replaces the text, tells the owner, and writes an ``egress``
   ledger row with the kinds only.
2. **Sign** (``ensure_message_signature``).
3. **Dedupe**: an ok send of the same normalised text into this chat in the
   last 10 minutes (aware UTC) *for this same ping* means it is already
   there. Another ping's identical template is its own reply (review
   2026-10-11: one ping's timeout template was "deduped" against another's).
4. **Send** through the transport (``imessage_service.send_to_chat``).
5. **On a reported failure**: chat.db is read for the row (a timeout often
   sent anyway), again after 10 s, and only then is the send retried, once.

A chat.db ``error`` value on a row is never a reason to resend.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

DEDUPE_WINDOW = timedelta(minutes=10)
RECHECK_AFTER_S = 10.0
SECRET_REPLY = ("I found an answer but it has something in it that looks like a password or key, "
                "so I won't post it here.")


@dataclass(frozen=True)
class Delivery:
    ok: bool
    deduped: bool = False
    rowid: Optional[int] = None
    route: Optional[str] = None
    error: Optional[str] = None
    text: str = ""
    scrubbed: bool = False


async def _notify_owner(text: str, *, urgent: bool = False, link: Optional[str] = None,
                      key: Optional[str] = None, origin: Optional[str] = None) -> None:
    from services.pings import runner

    await runner.tell_owner(text, urgent=urgent, link=link, key=key, origin=origin)


def _scan(text: str) -> Optional[str]:
    """The kinds found ("" when the scan itself failed), or None when clean."""
    try:
        from services import secret_scan, taint_service

        if not taint_service.secret_scan_enabled():
            return None
        hits = secret_scan.find_secrets(text, secret_scan.known_secrets())
    except Exception as exc:  # noqa: BLE001 - a broken scan must not leak
        print(f"[pings] reply secret scan failed; withholding the reply: {type(exc).__name__}")
        return ""
    if not hits:
        return None
    return ", ".join(sorted({h.kind for h in hits}))


def _who(ping: Dict[str, Any]) -> str:
    """Whom the reply was for, as the owner is told it ("you" for their own ping)."""
    return "you" if ping.get("from_owner") else str(ping.get("sender_label") or ping.get("sender") or "someone")


async def _scrub(ping: Dict[str, Any], text: str) -> tuple[str, bool]:
    kinds = _scan(text)
    if kinds is None:
        return text, False
    try:
        from services import event_ledger

        event_ledger.record_event(kind="egress", source="ping_reply", outcome="blocked",
                                  reason=kinds or "scan_failed")
    except Exception:  # noqa: BLE001
        pass
    where = ping.get("chat_name") or ping.get("chat_identifier") or "a chat"
    what = f"something that looks like a credential ({kinds})" if kinds else "something my secret check couldn't clear"
    await _notify_owner(f"My answer to {_who(ping)} in {where} had {what}, so I didn't post it there.",
                      urgent=False, link=f"/chat?c=ping-{ping.get('id')}", key=f"secret:{ping.get('id')}",
                      origin="ping:owner" if ping.get("from_owner") else "ping:other")
    return SECRET_REPLY, True


def _without_tool_markup(ping: Dict[str, Any], text: str) -> str:
    """``text`` with any tool call written in it removed, or the honest
    ``garbled`` line when nothing else is left."""
    from services import tool_markup
    from services.pings import fallback

    cleaned = tool_markup.clean_reply(text)
    if not cleaned.found:
        return text
    print(f"[pings] ping {ping.get('id')}: a tool call written as text was stripped from the reply"
          + ("" if cleaned.meaningful else "; nothing was left, so the honest line goes instead"))
    return cleaned.text if cleaned.meaningful else fallback.fallback_text("garbled")


async def _send(chat: str, text: str, purpose: str, ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The transport in a worker thread, inside the ping's context when given
    (so the send is noted on it and the ledger row carries the ping id)."""
    from services import imessage_service
    from services.pings import hooks

    token = hooks.CURRENT_PING.set(ctx) if ctx is not None else None
    try:
        return await asyncio.to_thread(imessage_service.send_to_chat, chat, text, purpose=purpose)
    finally:
        if token is not None:
            hooks.CURRENT_PING.reset(token)


async def _found_in_chat_db(chat: str, since: datetime, text: str) -> Optional[Dict[str, Any]]:
    from services import imessage_service

    try:
        return await asyncio.to_thread(imessage_service.recent_from_me_since, chat, since, text)
    except Exception:  # noqa: BLE001
        return None


async def deliver(ping: Dict[str, Any], text: str, *, purpose: str,
                  ctx: Optional[Dict[str, Any]] = None) -> Delivery:
    """Post ``text`` into the ping's chat at most once. Never raises."""
    from services import imessage_sent
    from services.pings import store
    from utils.message_signature import ensure_message_signature

    chat = str(ping.get("chat_identifier") or ping.get("chat_guid") or "")
    if not chat:
        return Delivery(ok=False, error="the ping has no chat to answer in")
    body = _without_tool_markup(ping, str(text or ""))
    body, scrubbed = await _scrub(ping, body)
    signed = ensure_message_signature(body)

    try:
        pid = ping.get("id")
        dup = await store.recent_ok_send(chat, imessage_sent.text_hash(signed),
                                         datetime.now(timezone.utc) - DEDUPE_WINDOW,
                                         ping_id=int(pid) if pid is not None else None)
    except Exception as exc:  # noqa: BLE001 - a broken read must not double-post; it only skips the dedupe
        print(f"[pings] delivery dedupe read failed: {type(exc).__name__}")
        dup = None
    if dup is not None:
        return Delivery(ok=True, deduped=True, rowid=dup.get("message_rowid"), route=dup.get("route"), text=signed,
                        scrubbed=scrubbed)

    error: Optional[str] = None
    for attempt in range(2):
        since = datetime.now(timezone.utc)
        try:
            result = await _send(chat, signed, purpose, ctx)
        except Exception as exc:  # noqa: BLE001
            result = {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if result.get("success"):
            return Delivery(ok=True, rowid=result.get("message_rowid"), route=result.get("route"), text=signed,
                            scrubbed=scrubbed)
        error = str(result.get("error") or "the send failed")
        # Messages often sends anyway (a timeout): chat.db decides before any retry.
        found = await _found_in_chat_db(chat, since, signed)
        if found is None:
            await asyncio.sleep(RECHECK_AFTER_S)
            found = await _found_in_chat_db(chat, since, signed)
        if found is not None:
            return Delivery(ok=True, rowid=found.get("rowid"), route="found", text=signed, scrubbed=scrubbed)
        if attempt == 0:
            print(f"[pings] delivery to ping {ping.get('id')} failed, retrying once: {error[:160]}")
    return Delivery(ok=False, error=error, text=signed, scrubbed=scrubbed)
