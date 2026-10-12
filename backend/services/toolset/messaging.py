"""``messages`` (read iMessage, look people up, stand down) and
``send_message`` — the only send (docs/architecture/tool-loop.md;
2026-10-11).

``send_message`` replaces ``send_imessage``, the old routed
``send_message``, ``send_sms``, ``send_whatsapp`` and the MCP sends that
bypassed the guard. It goes out from the owner's number, signed as Edward —
or, with a subject (or ``chat_id`` = a mail id from ``mail_search``), as an
email from the owner's Gmail (2026-10-11, ``toolset/mail.py``): the same
guards, another transport, never a second send path. The guards, in order:

1. Exactly one of ``chat_id`` / ``to``.
2. Never text the owner from a turn they are not part of (by name before
   the lookup, by their own handles after it) — the owner is reached with
   ``tell_owner``. A reply into the chat this turn was asked in (their
   self-chat ping), or into a self-chat where their own ping is still
   waiting, goes through.
3. ``to`` resolves to exactly one person (several → the list, with handles).
4. The turn may reach people at all (``TurnPolicy.may_reach_people``;
   fail-closed by default).
5. Stand-downs bind every turn the owner did not ask in.
6. The recipient rule: the owner asked, the chat is one this turn was asked
   in, the chat addressed Edward in 24 h, or a live conversation (his last
   send there answered by someone else within 2 h). Anyone else is the
   owner's call.
7. The same text to the same recipient within 10 minutes is sent once.
8. Files: autonomous turns attach only what Edward made in
   ``~/edward-home``; a text file carrying a secret is refused.
9. The egress secret scan runs at the executor boundary (``tool_loop``).
10. The transport, shielded (``graph.tools._send_and_note``); a failure
    raises (a failed send once read ``delivered: true``).

The send-review critic and the taint approval hold are gone from this path
(every ``would_gate`` row they produced was a legitimate reply).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

from langchain_core.tools import tool

from services.owner import owner_doc, owner_name, owner_names, owner_text
from services.toolset import policy as policy_mod
from services.toolset.result import ToolFailure

# ``messages`` actions; the mail ones live in ``toolset/mail.py`` (2026-10-11).
MESSAGES_ACTIONS = ("recent", "thread", "participants", "who", "stand_down", "mail_search", "mail_read")
TEXT_FILE_SCAN_MAX_BYTES = 1_000_000
_TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html", ".xml", ".yaml", ".yml",
                  ".env", ".ini", ".cfg", ".conf", ".sh", ".toml")

# "If you meant someone else named …" only makes sense with a name to mean.
TEXT_TO_OWNER_REFUSAL = owner_text(
    "Tool error: {OWNER} is reached with tell_owner, not by text (a note, or urgent for a push)."
    + (" If you meant someone else named {OWNER}, use their full name or number." if owner_names() else ""))
# ``{chat}`` is filled with ``.format`` at the refusal.
TEXT_TO_OWNER_WAITING_HINT = owner_text(" A message from {OWNER} is waiting for your answer: reply with "
                                        "send_message(chat_id=\"{chat}\").")


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _edward_home() -> Path:
    try:
        from services.computer.home import edward_home  # type: ignore[import-not-found]

        return Path(edward_home()).expanduser().resolve()
    except Exception:  # noqa: BLE001 — the computer module is missing or broken
        return Path(os.getenv("EDWARD_HOME") or "~/edward-home").expanduser().resolve()


def _why_quiet(policy: policy_mod.TurnPolicy) -> str:
    if policy.source in ("mind", "pulse", "background", "worker"):
        return f"a {policy.source} turn that wasn't given reach"
    return f"{policy.source} turn"


# --------------------------------------------------- never text the owner --

async def _owner_handles() -> List[str]:
    """Every handle that is the owner: their iMessage handles
    (``heartbeat_config.imessage_owner_handles``) and their email addresses
    (``mail_service.owner_addresses``: GMAIL_ACCOUNT, the connected Gmail, the
    addresses they send from in Mail.app) — ``graph/tools._owner_all_handles``,
    the same list the executor's check reads. Never raises. (``_owner_handles``
    in ``graph/tools`` is the iMessage-only list; this must be the full one.)"""
    return await _legacy()._owner_all_handles()


async def text_to_owner_refusal(policy: policy_mod.TurnPolicy, targets: List[Optional[str]], *,
                              check_handles: bool = True, email: bool = False) -> Optional[str]:
    """The refusal when a turn the owner is not part of addresses the owner
    by text, else None (keyed on the policy, not the turn source). A text
    may still go into the chat this turn was asked in, or their self-chat
    where their own ping waits; an email to the owner never goes from such a
    turn — those exceptions are for a reply in that chat (2026-10-11
    review)."""
    if policy.owner_asked:
        return None
    legacy = _legacy()
    targets = [str(t) for t in targets if str(t or "").strip()]
    if not targets:
        return None
    to_owner = [t for t in targets if legacy.names_owner(t)]
    if check_handles and len(to_owner) < len(targets):
        handles = await _owner_handles()
        to_owner = [t for t in targets if legacy.names_owner(t, handles)]
    if not to_owner:
        return None
    if not email and all(policy_mod.in_reply_to(policy, t) for t in to_owner):
        return None
    waiting = await legacy._chats_where_owner_is_waiting()
    if not email and waiting and all(any(legacy.same_handle(t, chat) for chat in waiting) for t in to_owner):
        return None
    if waiting:
        return TEXT_TO_OWNER_REFUSAL + TEXT_TO_OWNER_WAITING_HINT.format(chat=waiting[0])
    return TEXT_TO_OWNER_REFUSAL


# ------------------------------------------------------------ resolve --

async def _resolve_to(to: str) -> Tuple[str, str]:
    """``(handle, who)`` for a name, phone or email; raises unless exactly
    one person matches."""
    from services.imessage_service import looks_like_imessage_handle

    raw = to.strip()
    if looks_like_imessage_handle(raw):
        return raw, raw
    try:
        from services import contacts_service

        lookup = await asyncio.to_thread(contacts_service.lookup_contact, raw)
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"couldn't look '{raw}' up in Contacts ({type(exc).__name__}: {exc}) — use their "
                          "number or email, or reply with chat_id") from exc
    if isinstance(lookup, dict) and lookup.get("success") is False and not lookup.get("matches"):
        raise ToolFailure(f"couldn't look '{raw}' up in Contacts ({lookup.get('error') or 'unavailable'}) — use "
                          "their number or email, or reply with chat_id")
    matches = (lookup or {}).get("matches") or []
    usable = [m for m in matches if m.get("phones") or m.get("emails")]
    if not usable:
        raise ToolFailure(f"no contact matches '{raw}' — use a phone number or email, or reply with the chat_id "
                          "from the thread")
    if len(usable) > 1:
        listed = ", ".join(f"{m.get('name', '?')} ({(m.get('phones') or m.get('emails'))[0]})" for m in usable[:6])
        raise ToolFailure(f"'{raw}' matches {len(usable)} people: {listed}. Say which: send to=<their number>, or "
                          "reply with chat_id.")
    person = usable[0]
    handle = (person.get("phones") or person.get("emails"))[0]
    return handle, f"{person.get('name') or raw} ({handle})"


def _chat_label(chat_id: str) -> str:
    try:
        from services import imessage_service

        info_fn = getattr(imessage_service, "chat_info", None)
        info = info_fn(chat_id) if info_fn else None
        name = (info or {}).get("display_name") if isinstance(info, dict) else None
        if name:
            return f"the group {name}"
    except Exception:  # noqa: BLE001 — a label is decoration
        pass
    return chat_id


# -------------------------------------------------------------- files --

def _secret_in_file(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > TEXT_FILE_SCAN_MAX_BYTES:
            return None
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.suffix:
            return None
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    from services import secret_scan

    hits = secret_scan.find_secrets(text, secret_scan.known_secrets())
    if hits:
        return ", ".join(secret_scan.kinds_of(hits))
    return None


def _check_files(files: Optional[List[str]], policy: policy_mod.TurnPolicy) -> List[str]:
    out: List[str] = []
    home = _edward_home()
    for raw in files or []:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = home / p
        try:
            real = p.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ToolFailure(f"no such file: {raw}")
        if not real.is_file():
            raise ToolFailure(f"{raw} is not a file")
        try:
            from services.computer import rails  # type: ignore[import-not-found]

            # By identity, not spelling: a case variant, a link or a hard
            # link of a credential is the credential (review 2026-10-12).
            if hasattr(rails, "can_read") and not rails.can_read(str(real)):
                raise ToolFailure(f"{raw} isn't yours to read")
        except ImportError:
            pass
        if not policy.owner_asked and home not in real.parents:
            raise ToolFailure(f"in your own turns you can attach only what you made in ~/edward-home; {raw} "
                              f"is not there. If {owner_name()} should see it, tell_owner where it is.")
        kinds = _secret_in_file(real)
        if kinds:
            raise ToolFailure(f"{raw} contains what looks like a secret ({kinds}) — credentials never leave "
                              "this machine.")
        out.append(str(real))
    return out


def _file_label(path: str) -> str:
    """What a file send is deduped and recorded as: its name and size."""
    try:
        size = Path(path).stat().st_size
    except OSError:
        size = 0
    return f"[file] {Path(path).name} {size}"


# ----------------------------------------------------------- transport --

async def _transport_text(chat_id: Optional[str], handle: Optional[str], text: str) -> dict:
    from services import imessage_service as im

    try:
        im.note_main_loop(asyncio.get_running_loop())
    except Exception:  # noqa: BLE001
        pass
    if chat_id:
        send_to_chat = getattr(im, "send_to_chat", None)
        if send_to_chat is not None:
            return await asyncio.to_thread(send_to_chat, chat_id, text, purpose="tool")
        return await asyncio.to_thread(im.send_imessage_to_chat, chat_id, text)
    return await asyncio.to_thread(im.send_imessage, handle, text)


async def _after_send(target: str) -> None:
    """What a delivered send updates: the conversation's notified flag and
    the listening window (best effort)."""
    legacy = _legacy()
    conversation_id = legacy.get_current_conversation_id()
    try:
        from services.conversation_service import mark_user_notified

        if conversation_id:
            await mark_user_notified(conversation_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.heartbeat.triage_service import register_listening_window_for_chat

        await register_listening_window_for_chat(target, conversation_id=conversation_id)
    except Exception:  # noqa: BLE001
        pass


async def send_message_impl(message: str = "", chat_id: Optional[str] = None, to: Optional[str] = None,
                            files: Optional[List[str]] = None, subject: Optional[str] = None) -> str:
    from datetime import datetime, timezone

    from services import imessage_service
    from services.toolset import mail
    from utils.message_signature import ensure_message_signature

    legacy = _legacy()
    policy = policy_mod.current_policy()
    text = (message or "").strip()
    chat_id = (chat_id or "").strip() or None
    to = (to or "").strip() or None
    email = mail.is_email_send(subject, chat_id)
    if bool(chat_id) == bool(to):
        raise ToolFailure("give exactly one of chat_id (reply into a chat from the thread you were given) or to "
                          "(a name, phone or email)")
    if not text and not files:
        raise ToolFailure("the message is empty")

    # The owner by name: refused before any lookup.
    refused = await text_to_owner_refusal(policy, [chat_id or to], check_handles=False, email=email)
    if refused:
        raise ToolFailure(refused)

    reply_ctx = None
    if email:
        target, who, reply_ctx = await mail.resolve_email_target(chat_id, to)
        handle = None
    elif chat_id:
        target, who, handle = chat_id, _chat_label(chat_id), None
    else:
        handle, who = await _resolve_to(to)
        target = handle
    refused = await text_to_owner_refusal(policy, [target], email=email)
    if refused:
        raise ToolFailure(refused)
    # Past that check a send to the owner is an answer where they asked
    # (their self-chat ping): the recipient rule has nothing more to say
    # about it.
    to_owner = legacy.names_owner(target, await _owner_handles())

    if not policy.may_reach_people:
        raise ToolFailure(f"this turn can't message people ({_why_quiet(policy)}). If it matters, tell_owner.")

    if not policy.owner_asked:
        row = await legacy._active_standdown_for(target)
        if row is not None:
            asker = row.requested_by or "Someone in that chat"
            when = f" at {legacy._clock(row.requested_at)}" if getattr(row, "requested_at", None) else ""
            raise ToolFailure(f"stand-down active for {who} — {asker} asked you to stay out of this chat{when}. "
                              "You can't message it until someone there @edward-mentions you again or the "
                              "stand-down expires. If something there needs attention, tell_owner.")
        refusal = None if to_owner else await policy_mod.recipient_refusal(target, policy, who=who)
        if refusal:
            raise ToolFailure(refusal)

    if email:
        paths = _check_files(files, policy) if files else []
        return await mail.send_email(policy, address=target, who=who, text=text, subject=subject, paths=paths,
                                     reply_ctx=reply_ctx)

    paths = _check_files(files, policy) if files else []
    if paths and not hasattr(imessage_service, "send_attachment"):
        raise ToolFailure(f"attaching files isn't wired yet — send the text, and tell {owner_name()} where the "
                          "file is")

    if not imessage_service.is_available():
        raise ToolFailure("iMessage isn't available on this Mac (Messages is off for Edward)")

    # The text and each file are deduped and sent as one cancellation-safe
    # unit (``_send_and_note``): a re-ask after a cut or a failed file sends
    # only what didn't go, never the text or a file twice from the owner's number.
    signed = ensure_message_signature(text) if text else ""
    now = datetime.now(timezone.utc)

    def _went_at(label: str) -> Optional[datetime]:
        return legacy.duplicate_send_at(legacy._SEND_HISTORY, legacy.send_key(target),
                                        legacy.normalize_send_text(label), now)

    text_at = _went_at(signed) if signed else None
    file_labels = [_file_label(path) for path in paths]
    files_at = [_went_at(label) for label in file_labels]
    if (not signed or text_at is not None) and all(at is not None for at in files_at):
        at = max(a for a in (text_at, *files_at) if a is not None)
        return f"Already sent to {who} at {legacy._clock(at)} — not sending again."

    if policy.dry_run_outward:
        extra = f" with {len(paths)} file(s)" if paths else ""
        return f"[dry run] Would have sent via iMessage to {who}{extra}: {text[:300]}"

    if signed and text_at is None:
        result = await legacy._send_and_note(target, signed, _transport_text(chat_id, handle, signed),
                                             delivered=lambda r: bool((r or {}).get("success")))
        if not (result or {}).get("success"):
            raise ToolFailure(f"Messages couldn't send it to {who}: {(result or {}).get('error') or 'unknown error'}")
    sent_files = gone_files = 0
    for path, label, at in zip(paths, file_labels, files_at):
        if at is not None:
            gone_files += 1
            continue
        res = await legacy._send_and_note(
            target, label, asyncio.to_thread(imessage_service.send_attachment, chat_id or handle, path),
            delivered=lambda r: bool((r or {}).get("success")))
        if not (res or {}).get("success"):
            went = (["the text"] if signed else []) + (
                [f"{gone_files} file{'s' if gone_files != 1 else ''}"] if gone_files else [])
            done = f"{' and '.join(went)} went out, but " if went else ""
            raise ToolFailure(f"{done}Messages couldn't attach {Path(path).name}: "
                              f"{(res or {}).get('error') or 'unknown error'}")
        sent_files += 1
        gone_files += 1
    await _after_send(target)
    extra = f" (with {sent_files} file{'s' if sent_files != 1 else ''})" if sent_files else ""
    if text_at is not None or any(at is not None for at in files_at):
        extra += " — the rest had gone already"
    return f"Sent via iMessage to {who}{extra}"


@tool
@owner_doc
async def send_message(message: str, chat_id: Optional[str] = None, to: Optional[str] = None,
                       files: Optional[List[str]] = None, subject: Optional[str] = None) -> str:
    """Text or email someone — the only way to message anyone, signed as you.

    A text goes over iMessage from {OWNER}'s number: reply into a chat with chat_id (from the thread you were
    given) — preferred — or to = a name, phone or email that matches exactly one person.
    With subject it is an email from {OWNER}'s Gmail: to = an address (or a name with one), or chat_id = a mail id
    from mail_search to reply in its thread. You may write where you were asked, where you're already in the
    conversation, or where {OWNER} asked you to; anyone else is {OWNER}'s call (tell_owner what you'd send). Never
    text {OWNER} from your own turns: tell_owner reaches them. files: paths to attach (in your own turns, only what
    you made in ~/edward-home)."""
    return await send_message_impl(message=message, chat_id=chat_id, to=to, files=files, subject=subject)


# ------------------------------------------------------------ messages --

_FAILURE_HEADS = (
    "iMessage history unavailable", "Failed to get messages", "read_imessage_thread: failed",
    "read_imessage_thread: chat_identifier is required", "list_chat_participants: failed",
    "list_chat_participants: chat_identifier is required", "Contacts lookup not available",
    "Failed to search contacts", "Failed to record stand-down", "Tool error:",
)


def _checked(text: Any) -> str:
    out = str(text if text is not None else "")
    if out.lstrip().startswith(_FAILURE_HEADS):
        raise ToolFailure(out.removeprefix("Tool error:").strip())
    return out


def _looks_like_phone(value: str) -> bool:
    digits = "".join(ch for ch in value if ch.isdigit())
    return len(digits) >= 7 and all(ch in "0123456789+()-. " for ch in value.strip())


async def messages_impl(action: str, chat_id: Optional[str] = None, contact: Optional[str] = None,
                        hours: int = 24, limit: Optional[int] = None, before_rowid: Optional[int] = None,
                        query: Optional[str] = None, requested_by: Optional[str] = None, id: Optional[str] = None,
                        account: Optional[str] = None, mailbox: Optional[str] = None,
                        unread_only: bool = False) -> str:
    legacy = _legacy()
    act = (action or "").strip().lower()
    if act not in MESSAGES_ACTIONS:
        raise ToolFailure(f"action must be one of {', '.join(MESSAGES_ACTIONS)}")
    if act == "mail_search":
        from services.toolset import mail

        return await mail.mail_search(query, account, mailbox, unread_only, limit)
    if act == "mail_read":
        from services.toolset import mail

        mail_id = (id or chat_id or "").strip()
        if not mail_id:
            raise ToolFailure("mail_read needs id (a mail id from mail_search)")
        return await mail.mail_read(mail_id)
    if act == "recent":
        return _checked(await legacy.get_recent_messages.ainvoke({"contact": contact or query, "hours": hours or 24}))
    if act in ("thread", "participants", "stand_down") and not (chat_id or "").strip():
        raise ToolFailure(f"{act} needs chat_id")
    if act == "thread":
        args = {"chat_identifier": chat_id, "limit": limit or 40}
        if before_rowid is not None:
            args["before_rowid"] = before_rowid
        return _checked(await legacy.read_imessage_thread.ainvoke(args))
    if act == "participants":
        return _checked(await legacy.list_chat_participants.ainvoke({"chat_identifier": chat_id}))
    if act == "who":
        q = (query or contact or "").strip()
        if not q:
            raise ToolFailure("who needs query (a name or a phone number)")
        if _looks_like_phone(q):
            return _checked(await legacy.lookup_phone.ainvoke({"phone_number": q}))
        return _checked(await legacy.lookup_contact.ainvoke({"query": q}))
    # stand_down
    return _checked(await legacy.stand_down.ainvoke({"chat_id": chat_id, "requested_by": requested_by}))


@tool
@owner_doc
async def messages(action: str, chat_id: Optional[str] = None, contact: Optional[str] = None, hours: int = 24,
                   limit: Optional[int] = None, before_rowid: Optional[int] = None, query: Optional[str] = None,
                   requested_by: Optional[str] = None, id: Optional[str] = None, account: Optional[str] = None,
                   mailbox: Optional[str] = None, unread_only: bool = False) -> str:
    """Read {OWNER}'s iMessage and email, find out who someone is, or stand down in a chat.

    action:
      recent — recent messages, one header per chat with its chat_id (contact= a name or handle, hours=);
      thread — one chat (chat_id), newest `limit` lines; before_rowid= to scroll back;
      participants — who is in a chat (chat_id);
      who — who a name, number or email is (query);
      stand_down — someone in chat_id asked you to stay out: you won't message it until you're @mentioned there
        again (requested_by = who asked);
      mail_search — query matches sender or subject (Gmail search syntax works there); account icloud | gmail |
        both (default), mailbox (default all, e.g. inbox), unread_only, limit;
      mail_read — one email, or a Gmail thread, by id from mail_search.
    Attachments show as #<rowid>: look("attachment:<rowid>") to see one."""
    return await messages_impl(action=action, chat_id=chat_id, contact=contact, hours=hours, limit=limit,
                               before_rowid=before_rowid, query=query, requested_by=requested_by, id=id,
                               account=account, mailbox=mailbox, unread_only=unread_only)
