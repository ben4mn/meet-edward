"""The owner's email in the toolset (2026-10-11): ``messages(mail_search |
mail_read)`` and the email half of ``send_message``. No ``@tool`` here —
``messaging.py`` owns the two tools and dispatches.

Reads have no gate (design decision, 2026-10-11: few restrictions on what
Edward reads): every surface and every turn may search and read iCloud mail
(Mail.app on the host Mac — its Envelope Index and ``.emlx`` files) and
Gmail (the API). What another person wrote is untrusted content, marked as
a taint ledger signal (``taint_service.INGEST_ACTIONS``), never blocked.

Mail ids: ``icloud:<Envelope Index ROWID>``, ``gmail:<message id>``,
``gmail:thread:<thread id>``.
"""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.owner import owner_name, owner_text
from services.toolset.result import ToolFailure

ACCOUNTS = ("icloud", "gmail", "both")
SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 50
BODY_CAP = 8_000
THREAD_MESSAGE_CAP = 4_000
THREAD_TOTAL_CAP = 16_000
MAX_LINKS = 10

ID_HELP = "id is a mail id from mail_search: icloud:<n>, gmail:<id> or gmail:thread:<id>"
REPLY_FROM = owner_text("it goes from {OWNER}'s Gmail")
_KEY_VALUE = re.compile(r"^([a-z_]+):(.*)$", re.IGNORECASE)


# ------------------------------------------------------------- pieces --

def _icloud_terms(query: Optional[str]) -> Tuple[str, bool, Optional[str]]:
    """Gmail's search syntax cut down for Mail's index: ``from:``/``to:``/
    ``subject:`` X become X, ``is:unread`` sets unread-only, ``in:inbox``
    the inbox, any other ``key:value`` is dropped. ``(terms, unread_only,
    mailbox)``."""
    try:
        tokens = shlex.split(query or "")
    except ValueError:
        tokens = (query or "").split()
    words: List[str] = []
    unread = False
    mailbox: Optional[str] = None
    for tok in tokens:
        m = _KEY_VALUE.match(tok)
        if not m:
            words.append(tok)
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key in ("from", "to", "subject") and value:
            words.append(value)
        elif key == "is" and value.lower() == "unread":
            unread = True
        elif key == "in" and value.lower() == "inbox":
            mailbox = "inbox"
    return " ".join(words), unread, mailbox


def _gmail_query(query: str, mailbox: Optional[str], unread_only: bool) -> str:
    q = (query or "").strip()
    if unread_only:
        q += " is:unread"
    if mailbox:
        box = mailbox.strip()
        if box.lower() == "inbox":
            q += " in:inbox"
        else:
            q += f' label:"{box}"' if " " in box else f" label:{box}"
    return q.strip()


def _size(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _who(name: Optional[str], address: Optional[str]) -> str:
    name, address = (name or "").strip(), (address or "").strip()
    if name and address and name.lower() != address.lower():
        return f"{name} <{address}>"
    return address or name or "unknown sender"


def _cap(body: str, limit: int) -> str:
    body = body or ""
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + f"\n[cut: {len(body) - limit} more characters]"


def _links(body: str, found: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    for url in list(found or []) + re.findall(r"https?://[^\s<>\"')\]]+", body or ""):
        url = url.rstrip(".,;:")
        if url not in out:
            out.append(url)
        if len(out) >= MAX_LINKS:
            break
    return out


def _from_ms(ms: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _from_epoch(sec: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(sec), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _when(dt: Optional[datetime]) -> str:
    from services.mail_service import when_text

    return when_text(dt)


def parse_mail_id(mail_id: Optional[str]) -> Tuple[str, str]:
    """``("icloud", "48211")`` | ``("gmail", "<id>")`` | ``("gmail_thread",
    "<id>")``; ToolFailure for anything else."""
    raw = (mail_id or "").strip()
    low = raw.lower()
    if low.startswith("icloud:") and raw[7:].strip().isdigit():
        return "icloud", raw[7:].strip()
    if low.startswith("gmail:thread:") and raw[13:].strip():
        return "gmail_thread", raw[13:].strip()
    if low.startswith("gmail:") and raw[6:].strip():
        return "gmail", raw[6:].strip()
    raise ToolFailure(ID_HELP)


# --------------------------------------------------------- mail_search --

async def _icloud_side(terms: str, mailbox: Optional[str], unread_only: bool, limit: int) -> Dict[str, Any]:
    """``{"rows", "note", "error"}`` — rows are ``(datetime, line)``."""
    from services import email_management_service as ems
    from services import mail_service

    status = await mail_service.icloud_status()
    if not status.get("index_readable"):
        return {"rows": [], "error": "Mail's index isn't readable on this Mac (Full Disk Access?), so iCloud mail "
                                     "can't be searched"}
    try:
        found = await ems.search_mail(terms, mailbox, unread_only, limit)
    except LookupError as exc:
        # A mailbox Mail.app doesn't have (a Gmail label, "sent"): a note when
        # Gmail is searched too, a failure when only iCloud is.
        return {"rows": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"rows": [], "error": f"Mail's index couldn't be searched ({type(exc).__name__})"}
    rows = []
    for r in found:
        dt = _from_epoch(r.get("date_received"))
        parts = [_when(dt), _who(r.get("sender_name"), r.get("sender")), r.get("subject") or "(no subject)"]
        if not r.get("read"):
            parts.append("unread")
        parts.append(f"iCloud/{ems.mailbox_name(r.get('mailbox_url'))}")
        parts.append(f"id=icloud:{r['rowid']}")
        rows.append((dt, "- " + " · ".join(parts)))
    return {"rows": rows, "note": mail_service.staleness_note(status)}


async def _gmail_side(query: str, mailbox: Optional[str], unread_only: bool, limit: int) -> Dict[str, Any]:
    """``{"rows", "address", "error"}``; ``error`` is a sentence for the owner."""
    from services import gmail_service

    if not await gmail_service.is_configured():
        return {"rows": [], "error": gmail_service.NOT_CONNECTED}
    found = await gmail_service.search_gmail(_gmail_query(query, mailbox, unread_only), max_results=limit)
    if found and isinstance(found[0], dict) and found[0].get("error"):
        if gmail_service.token_refresh_failed() or "not configured" in str(found[0]["error"]):
            return {"rows": [], "error": gmail_service.AUTH_LAPSED}
        return {"rows": [], "error": f"Gmail's search failed ({found[0]['error']})"}
    address = await gmail_service.get_email_address()
    rows = []
    for r in found:
        dt = _from_ms(r.get("internal_date"))
        labels = r.get("labels") or []
        parts = [_when(dt), r.get("sender") or "unknown sender", r.get("subject") or "(no subject)"]
        if "UNREAD" in labels:
            parts.append("unread")
        parts.append("Gmail/INBOX" if "INBOX" in labels else "Gmail")
        line = "- " + " · ".join(parts) + f" · id=gmail:{r.get('message_id')}"
        if r.get("thread_id"):
            line += f" (thread gmail:thread:{r['thread_id']})"
        rows.append((dt, line))
    return {"rows": rows, "address": address}


async def mail_search(query: Optional[str], account: Optional[str], mailbox: Optional[str], unread_only: bool,
                      limit: Optional[int]) -> str:
    acct = (account or "both").strip().lower()
    if acct not in ACCOUNTS:
        raise ToolFailure("account must be icloud, gmail or both")
    q = (query or "").strip()
    try:
        n = min(int(limit or SEARCH_DEFAULT_LIMIT), SEARCH_MAX_LIMIT)
    except (TypeError, ValueError):
        n = SEARCH_DEFAULT_LIMIT
    n = max(1, n)
    unread = bool(unread_only)

    sides: List[str] = []
    rows: List[Tuple[Optional[datetime], str]] = []
    notes: List[str] = []
    errors: List[str] = []

    if acct in ("icloud", "both"):
        terms, op_unread, op_box = _icloud_terms(q)
        box = mailbox or op_box
        ic = await _icloud_side(terms, box, unread or op_unread, n)
        if ic.get("error"):
            if acct == "icloud":
                raise ToolFailure(ic["error"])
            errors.append(ic["error"])
            notes.append(f"(iCloud: {ic['error']}.)")
        else:
            sides.append(f"iCloud ({box or 'all mailboxes'})")
            rows.extend(ic["rows"])
            if ic.get("note"):
                notes.append(ic["note"])

    if acct in ("gmail", "both"):
        gm = await _gmail_side(q, mailbox, unread, n)
        if gm.get("error"):
            if acct == "gmail":
                raise ToolFailure(gm["error"])
            errors.append(gm["error"])
            notes.append(f"({gm['error']})")
        else:
            sides.append(f"Gmail ({gm.get('address') or 'connected'})")
            rows.extend(gm["rows"])

    if not sides:
        raise ToolFailure(" ".join(e.rstrip(".") + "." for e in errors))

    floor = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: r[0] or floor, reverse=True)
    rows = rows[:n]
    where = " + ".join(sides)
    if not rows:
        head = (f'No email matching "{q}" — {where}.' if q else f"No email — {where}.")
        return "\n".join([head] + notes)
    head = (f'Email matching "{q}" — {where}, newest first:' if q else f"Newest email — {where}:")
    return "\n".join([head] + [line for _, line in rows] + notes)


# ----------------------------------------------------------- mail_read --

def _block(view: Dict[str, Any], *, mail_id: str, account_line: str, reply_id: str, body_cap: int,
           reply_hint: bool = True) -> str:
    lines = [f"From: {view.get('from') or 'unknown sender'}"]
    if view.get("to"):
        lines.append(f"To: {view['to']}")
    if view.get("cc"):
        lines.append(f"Cc: {view['cc']}")
    lines.append(f"Date: {view.get('when') or '?'}")
    lines.append(f"Subject: {view.get('subject') or '(no subject)'}")
    lines.append(f"{account_line} · id={mail_id}")
    if reply_hint:
        lines.append(f'Reply: send_message(chat_id="{reply_id}", message=…) — {REPLY_FROM}')
    atts = view.get("attachments") or []
    if atts:
        lines.append("Attachments: " + ", ".join(f"{a.get('name')} ({_size(a.get('size'))})" for a in atts))
    body = view.get("body")
    lines.append("---")
    lines.append(_cap(body, body_cap) if body else "(no text)")
    links = _links(body or "", view.get("links"))
    if links:
        lines.append("")
        lines.append("Links:")
        lines.extend(f"- {u}" for u in links)
    return "\n".join(lines)


async def _read_icloud(rowid: int, mail_id: str) -> str:
    from services import email_management_service as ems

    got = await ems.read_email_body(rowid)
    if got.get("error"):
        raise ToolFailure(f"{got['error']} — {ID_HELP}" if "no message" in got["error"] else got["error"])
    view = {
        "from": got.get("sender_header") or _who(got.get("sender_name"), got.get("sender")),
        "to": got.get("to"), "cc": got.get("cc"), "subject": got.get("subject"),
        "when": _when(_from_epoch(got.get("date_received"))),
        "attachments": got.get("attachments"), "body": got.get("body"), "links": got.get("links"),
    }
    account_line = f"Account: iCloud/{ems.mailbox_name(got.get('mailbox_url'))}"
    text = _block(view, mail_id=mail_id, account_line=account_line, reply_id=mail_id, body_cap=BODY_CAP)
    if not got.get("downloaded"):
        text = text.replace("---\n(no text)", "---\nMail.app hasn't downloaded this message's body yet.")
    return text


def _gmail_view(v: Dict[str, Any]) -> Dict[str, Any]:
    return {"from": v.get("sender"), "to": v.get("to"), "cc": v.get("cc"), "subject": v.get("subject"),
            "when": _when(_from_ms(v.get("internal_date"))), "attachments": v.get("attachments"),
            "body": v.get("body"), "links": v.get("links")}


async def _gmail_unavailable() -> ToolFailure:
    from services import gmail_service

    if not await gmail_service.is_configured():
        return ToolFailure(gmail_service.NOT_CONNECTED)
    return ToolFailure(gmail_service.AUTH_LAPSED)


async def _read_gmail(message_id: str, mail_id: str) -> str:
    from services import gmail_service

    got = await gmail_service.read_gmail(message_id)
    if got.get("error"):
        if "not configured" in got["error"]:
            raise await _gmail_unavailable()
        if got.get("not_found"):
            raise ToolFailure(f"Gmail has no message {mail_id} — {ID_HELP}")
        raise ToolFailure(got["error"])
    thread = f" (thread gmail:thread:{got['thread_id']})" if got.get("thread_id") else ""
    return _block(_gmail_view(got), mail_id=mail_id, account_line=f"Account: Gmail{thread}", reply_id=mail_id,
                  body_cap=BODY_CAP)


async def _read_gmail_thread(thread_id: str, mail_id: str) -> str:
    from services import gmail_service

    got = await gmail_service.read_gmail_thread(thread_id)
    if got.get("error"):
        if "not configured" in got["error"]:
            raise await _gmail_unavailable()
        if got.get("not_found"):
            raise ToolFailure(f"Gmail has no thread {mail_id} — {ID_HELP}")
        raise ToolFailure(got["error"])
    msgs = got.get("messages") or []
    if not msgs:
        raise ToolFailure(f"Gmail's thread {mail_id} is empty")
    # The newest are kept: walk back from the newest, each body capped, until
    # the thread's budget is spent; older bodies are cut whole.
    budget = THREAD_TOTAL_CAP
    blocks: List[str] = []
    for v in reversed(msgs):
        view = _gmail_view(v)
        body = _cap(view.get("body") or "", THREAD_MESSAGE_CAP)
        if len(body) > budget:
            view["body"] = None
            block = _block(view, mail_id=f"gmail:{v.get('message_id')}", account_line="Gmail",
                           reply_id=mail_id, body_cap=THREAD_MESSAGE_CAP, reply_hint=False)
            block = block.replace("---\n(no text)", f"---\n[cut to fit the thread: {len(v.get('body') or '')} "
                                                    f"characters — mail_read gmail:{v.get('message_id')}]")
            budget = 0
        else:
            budget -= len(body)
            block = _block(view, mail_id=f"gmail:{v.get('message_id')}", account_line="Gmail",
                           reply_id=mail_id, body_cap=THREAD_MESSAGE_CAP, reply_hint=False)
        blocks.append(block)
    blocks.reverse()
    head = (f"Thread {mail_id} — {len(msgs)} message{'s' if len(msgs) != 1 else ''}, oldest first. "
            f'Reply: send_message(chat_id="{mail_id}", message=…) answers the newest.')
    return head + "\n\n" + "\n\n===\n\n".join(blocks)


async def mail_read(mail_id: Optional[str]) -> str:
    kind, ident = parse_mail_id(mail_id)
    canonical = {"icloud": f"icloud:{ident}", "gmail": f"gmail:{ident}",
                 "gmail_thread": f"gmail:thread:{ident}"}[kind]
    if kind == "icloud":
        return await _read_icloud(int(ident), canonical)
    if kind == "gmail":
        return await _read_gmail(ident, canonical)
    return await _read_gmail_thread(ident, canonical)


# ----------------------------------------------------------- sending one --
#
# ``send_message`` with a subject, or with ``chat_id`` = a mail id (a reply),
# is an email from the owner's Gmail. It shares every guard with a text
# (``messaging.send_message_impl``) and differs only at the transport.

ATTACHMENTS_MAX_BYTES = 20 * 1024 * 1024  # Gmail takes 25 MB once encoded
GMAIL_NEEDED = owner_text("I can only send email from {OWNER}'s Gmail, and it isn't connected — {OWNER} "
                          "connects it in Settings → Gmail.")
ATTACHMENTS_TOO_BIG_HINT = owner_text("send fewer, or put them somewhere {OWNER} can share a link")
_MAIL_ID_PREFIXES = ("gmail:", "icloud:")


def is_email_send(subject: Optional[str], chat_id: Optional[str]) -> bool:
    """An email when there is a subject, or ``chat_id`` is a mail id."""
    if str(subject or "").strip():
        return True
    return str(chat_id or "").strip().lower().startswith(_MAIL_ID_PREFIXES)


def _re_subject(subject: Optional[str]) -> str:
    s = (subject or "").strip()
    if not s:
        return "Re:"
    return s if s.lower().startswith("re:") else f"Re: {s}"


def _address_of(header: Optional[str]) -> Tuple[str, str]:
    """``(name, address)`` from a From / Reply-To header."""
    from email.utils import getaddresses

    pairs = [p for p in getaddresses([header or ""]) if "@" in (p[1] or "")]
    if not pairs:
        return "", ""
    return pairs[0][0].strip(), pairs[0][1].strip()


def _reply_ctx(headers: Dict[str, Any], *, thread_id: Optional[str]) -> Dict[str, Any]:
    mid = (headers.get("message_id_header") or "").strip()
    refs = (headers.get("references") or "").strip()
    references = " ".join(x for x in (refs, mid) if x) or None
    return {"thread_id": thread_id, "in_reply_to": mid or None, "references": references,
            "subject": _re_subject(headers.get("subject"))}


_OWNERS_LABELS = ("SENT", "DRAFT")


def _addresses(*headers: Optional[str]) -> List[Tuple[str, str]]:
    """Every ``(name, address)`` in some address headers, in order."""
    from email.utils import getaddresses

    return [(n.strip(), a.strip()) for n, a in getaddresses([h for h in headers if h]) if "@" in (a or "")]


def _is_owners(h: Dict[str, Any], owners: set) -> bool:
    """The owner's own message: Gmail labels it SENT or DRAFT, or it is
    from one of their addresses (``mail_service.owner_addresses``)."""
    if any(label in (h.get("labels") or []) for label in _OWNERS_LABELS):
        return True
    _name, address = _address_of(h.get("from"))
    return bool(address) and address.lower() in owners


def _recipient(h: Dict[str, Any], owners: set, *, own: bool) -> Tuple[str, str]:
    """Whom a reply to this message goes to: its Reply-To or From — or, when
    it is the owner's own, the first of its To/Cc who isn't the owner
    (2026-10-11 review: a reply to the owner's own email went to the owner).
    A note the owner sent themselves falls back to the owner, and the
    never-the-owner guard decides."""
    if own:
        for name, address in _addresses(h.get("to"), h.get("cc")):
            if address.lower() not in owners:
                return name, address
    return _address_of(h.get("reply_to") or h.get("from"))


async def _reply_target(kind: str, ident: str, mail_id: str) -> Tuple[str, str, Dict[str, Any]]:
    """A reply answers the other side: a message's Reply-To or From, or for
    the owner's own message whom they wrote to; a thread answers its newest
    message that isn't the owner's, threaded under the newest sent one
    (drafts never count)."""
    from services import gmail_service, mail_service

    owners = {a.lower() for a in await mail_service.owner_addresses()}
    if kind in ("gmail", "gmail_thread"):
        try:
            if kind == "gmail":
                newest = await gmail_service.get_message_headers(ident)
                name, address = _recipient(newest, owners, own=_is_owners(newest, owners))
            else:
                msgs = await gmail_service.thread_headers(ident)
                sent = [m for m in msgs if "DRAFT" not in (m.get("labels") or [])] or msgs
                newest = sent[-1]
                others = [m for m in sent if not _is_owners(m, owners)]
                if others:
                    name, address = _recipient(others[-1], owners, own=False)
                else:
                    name, address = _recipient(newest, owners, own=True)
        except gmail_service.GmailError as exc:
            raise ToolFailure(str(exc)) from exc
        if not address:
            raise ToolFailure(f"{mail_id} has no address to reply to")
        return address, _who(name, address), _reply_ctx(newest, thread_id=newest.get("thread_id"))

    from services import email_management_service as ems

    got = await ems.read_email_body(int(ident))
    if got.get("error"):
        raise ToolFailure(f"{got['error']} — {ID_HELP}")
    h = {"from": got.get("sender_header") or got.get("sender") or "", "reply_to": got.get("reply_to"),
         "to": got.get("to"), "cc": got.get("cc")}
    own = ems.mailbox_name(got.get("mailbox_url")).lower().startswith("sent") or _is_owners(h, owners)
    name, address = _recipient(h, owners, own=own)
    if not address:
        name, address = (got.get("sender_name") or ""), (got.get("sender") or "")
    if "@" not in address:
        raise ToolFailure(f"{mail_id} has no address to reply to")
    headers = {"message_id_header": got.get("message_id"), "references": got.get("references"),
               "subject": got.get("subject")}
    return address, _who(name, address), _reply_ctx(headers, thread_id=None)


async def _resolve_name_to_address(name: str) -> Tuple[str, str]:
    import asyncio

    try:
        from services import contacts_service

        lookup = await asyncio.to_thread(contacts_service.lookup_contact, name)
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"couldn't look '{name}' up in Contacts ({type(exc).__name__}: {exc}) — use their "
                          "email address") from exc
    matches = (lookup or {}).get("matches") or []
    with_email = [m for m in matches if m.get("emails")]
    if not with_email:
        if matches:
            raise ToolFailure(f"'{name}' has no email address in Contacts — use their address")
        raise ToolFailure(f"no contact matches '{name}' — use their email address")
    if len(with_email) > 1:
        listed = ", ".join(f"{m.get('name', '?')} ({m['emails'][0]})" for m in with_email[:6])
        raise ToolFailure(f"'{name}' matches {len(with_email)} people: {listed}. Say which: to=<their address>.")
    person = with_email[0]
    emails = [str(e) for e in person["emails"] if str(e or "").strip()]
    if len(emails) > 1:
        raise ToolFailure(f"{person.get('name') or name} has {len(emails)} email addresses: {', '.join(emails[:6])}. "
                          "Say which: to=<the address>.")
    return emails[0], f"{person.get('name') or name} ({emails[0]})"


async def resolve_email_target(chat_id: Optional[str], to: Optional[str]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """``(address, who, reply_ctx)``: a reply (``chat_id`` = a mail id) goes
    to its Reply-To or From, in its thread; ``to`` is an address, or a name
    with exactly one person and one address in Contacts."""
    if chat_id:
        if not str(chat_id).strip().lower().startswith(_MAIL_ID_PREFIXES):
            raise ToolFailure("an email reply takes chat_id = a mail id from mail_search (gmail:… or icloud:…); "
                              "a new email takes to = an address")
        kind, ident = parse_mail_id(chat_id)
        return await _reply_target(kind, ident, str(chat_id).strip())
    raw = str(to or "").strip()
    from services.imessage_service import looks_like_imessage_handle

    if "@" in raw and looks_like_imessage_handle(raw):
        return raw, raw, None
    address, who = await _resolve_name_to_address(raw)
    return address, who, None


async def send_email(policy: Any, *, address: str, who: str, text: str, subject: Optional[str],
                     paths: List[str], reply_ctx: Optional[Dict[str, Any]]) -> str:
    """The transport half of an email ``send_message``: every guard before
    it has run. Gmail connected, attachments within 20 MB, signed, the
    10-minute dedupe, the dry run, then the send — shielded, and "Sent"
    only when Gmail says SENT."""
    import os
    from datetime import datetime, timezone

    from services import gmail_service
    from services.graph import tools as legacy
    from utils.message_signature import ensure_message_signature

    if not await gmail_service.is_configured():
        raise ToolFailure(GMAIL_NEEDED)
    total = sum(os.path.getsize(p) for p in paths)
    if total > ATTACHMENTS_MAX_BYTES:
        raise ToolFailure(f"those files come to {total / (1024 * 1024):.1f} MB; an email takes at most 20 MB of "
                          f"attachments — {ATTACHMENTS_TOO_BIG_HINT}")
    ctx = reply_ctx or {}
    subj = (subject or "").strip() or ctx.get("subject") or ""
    signed = ensure_message_signature(text) if text else ""
    key = legacy.send_key("mail:" + address)
    dedupe_text = signed or " ".join([subj] + [os.path.basename(p) for p in paths])
    at = legacy.duplicate_send_at(legacy._SEND_HISTORY, key, legacy.normalize_send_text(dedupe_text),
                                  datetime.now(timezone.utc))
    if at is not None:
        return f"Already sent to {who} at {legacy._clock(at)} — not sending again."
    if getattr(policy, "dry_run_outward", False):
        extra = f" with {len(paths)} file(s)" if paths else ""
        return f"[dry run] Would have emailed {who}: “{subj}”{extra} {text[:300]}".rstrip()

    result = await legacy._send_and_note(
        "mail:" + address, dedupe_text,
        gmail_service.send_gmail(address, subj, signed, attachments=tuple(paths), thread_id=ctx.get("thread_id"),
                                 in_reply_to=ctx.get("in_reply_to"), references=ctx.get("references")),
        # An unconfirmed send went out too: noted, so a retry is "Already sent".
        delivered=lambda r: bool((r or {}).get("delivered") or (r or {}).get("unconfirmed")))
    if (result or {}).get("unconfirmed"):
        why = str(result.get("error") or "").split("): ", 1)[-1] or "the confirmation failed"
        raise ToolFailure(f"Gmail took it (id {result.get('id')}) but didn't confirm SENT — don't resend; check Sent "
                          f"({why})")
    if not (result or {}).get("delivered"):
        raise ToolFailure(f"Gmail couldn't send it: {(result or {}).get('error') or 'unknown error'}")
    try:
        from services.conversation_service import mark_user_notified

        conversation_id = legacy.get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)
    except Exception:  # noqa: BLE001
        pass
    sender = (result or {}).get("from") or f"{owner_name()}'s Gmail"
    files = f", {len(paths)} file{'s' if len(paths) != 1 else ''}" if paths else ""
    return f"Sent via Gmail to {who}: “{subj}” (from {sender}{files})"
