"""
Gmail API service for Edward.

Provides OAuth flow, email search/read/delete/archive/label operations,
and incremental history polling for the heartbeat listener.

All Google API calls are synchronous — wrapped with asyncio.to_thread().
"""

import asyncio
import base64
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from services.database import async_session, GmailCredentialsModel
from services.owner import owner_text


# OAuth config
def client_credentials(environ=None) -> tuple:
    """(client id, client secret): the env vars, else Google's downloaded
    client JSON at ``GMAIL_CLIENT_SECRETS_FILE`` (its ``web`` or
    ``installed`` section) — so the secret never has to be pasted into
    ``.env`` by hand (2026-10-11). Missing or unreadable is unconfigured."""
    env = os.environ if environ is None else environ
    cid = (env.get("GMAIL_CLIENT_ID") or "").strip()
    secret = (env.get("GMAIL_CLIENT_SECRET") or "").strip()
    path = (env.get("GMAIL_CLIENT_SECRETS_FILE") or "").strip()
    if (cid and secret) or not path:
        return cid, secret
    try:
        with open(os.path.expanduser(path)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cid, secret
    section = (data.get("web") or data.get("installed") or {}) if isinstance(data, dict) else {}
    return cid or str(section.get("client_id") or ""), secret or str(section.get("client_secret") or "")


GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET = client_credentials()
GMAIL_REDIRECT_URI = os.getenv("GMAIL_REDIRECT_URI", "http://localhost:3000/api/gmail/oauth/callback")
# gmail.modify covers users.messages.send, threads.get and messages.get/list
# (Google's discovery doc for gmail v1, checked 2026-10-11): sending email
# needs no new scope and no re-consent.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailError(Exception):
    """A Gmail call that could not be made; the message is for the owner."""


class GmailHistoryExpired(GmailError):
    """The listener's ``historyId`` is too old (or invalid): re-seed."""


class GmailNotConnected(GmailError):
    """No stored credentials."""


class GmailAuthLapsed(GmailError):
    """The stored refresh token no longer works."""


NOT_CONNECTED = owner_text("Gmail isn't connected — {OWNER} connects it in Settings → Gmail.")
AUTH_LAPSED = owner_text("Gmail's sign-in has lapsed — {OWNER} reconnects it in Settings → Gmail.")

# The last token refresh's failure (None after a success), for the health
# report and for an honest "sign-in has lapsed" (2026-10-11).
_last_refresh_error: Optional[str] = None


def token_refresh_failed() -> Optional[str]:
    """Why the last token refresh failed, or None."""
    return _last_refresh_error


def _clear_refresh_error() -> None:
    """A reconnect or a disconnect: the old refresh failure no longer says
    anything (2026-10-11 review — it kept the report warning "lapsed")."""
    global _last_refresh_error
    _last_refresh_error = None


# =====================================================================
# Credential management
# =====================================================================

async def _load_credentials_row() -> Optional[GmailCredentialsModel]:
    """Load the stored Gmail credentials from DB."""
    async with async_session() as session:
        result = await session.execute(
            select(GmailCredentialsModel).where(GmailCredentialsModel.id == "default")
        )
        return result.scalar_one_or_none()


async def _save_credentials(creds, email_address: str = "") -> None:
    """Save/update Google credentials to DB."""
    async with async_session() as session:
        result = await session.execute(
            select(GmailCredentialsModel).where(GmailCredentialsModel.id == "default")
        )
        row = result.scalar_one_or_none()

        expiry = creds.expiry if hasattr(creds, "expiry") and creds.expiry else None

        if row:
            row.access_token = creds.token
            row.refresh_token = creds.refresh_token or row.refresh_token
            row.token_expiry = expiry
            if email_address:
                row.email_address = email_address
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, the column convention
        else:
            row = GmailCredentialsModel(
                id="default",
                access_token=creds.token,
                refresh_token=creds.refresh_token,
                token_expiry=expiry,
                email_address=email_address,
            )
            session.add(row)

        await session.commit()


def _build_credentials_from_row(row: GmailCredentialsModel):
    """Build a google.oauth2.credentials.Credentials from DB row."""
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=row.access_token,
        refresh_token=row.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        scopes=GMAIL_SCOPES,
    )


async def _get_credentials():
    """Load credentials, auto-refresh if expired, save back."""
    row = await _load_credentials_row()
    if not row or not row.refresh_token:
        return None

    creds = _build_credentials_from_row(row)

    # Check if refresh needed
    global _last_refresh_error
    if not creds.valid:
        try:
            from google.auth.transport.requests import Request
            await asyncio.to_thread(creds.refresh, Request())
            await _save_credentials(creds)
        except Exception as e:
            _last_refresh_error = f"{type(e).__name__}: {e}"[:200]
            print(f"[Gmail] Token refresh failed: {e}")
            return None
        _last_refresh_error = None

    return creds


def _build_service(creds):
    """Build the Gmail API service resource."""
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


async def _get_service():
    """Get an authenticated Gmail API service."""
    creds = await _get_credentials()
    if not creds:
        return None
    return await asyncio.to_thread(_build_service, creds)


async def _require_service():
    """The service, or GmailNotConnected / GmailAuthLapsed with the sentence
    the owner needs."""
    service = await _get_service()
    if service is not None:
        return service
    if not await is_configured():
        raise GmailNotConnected(NOT_CONNECTED)
    raise GmailAuthLapsed(AUTH_LAPSED)


def _internal_date(msg: dict) -> Optional[int]:
    """Gmail's ``internalDate`` (ms since the epoch, a string) as an int."""
    try:
        return int(msg.get("internalDate"))
    except (TypeError, ValueError):
        return None


def _http_status(exc: Exception) -> Optional[int]:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None) or getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _google_words(exc: Exception) -> str:
    """What Google said about a failed call — the HttpError's reason, its
    details and the response body — never ``str(exc)``, which carries the
    request URI (for ``history.list`` it always says ``startHistoryId``)."""
    parts = [str(getattr(exc, "reason", "") or ""), str(getattr(exc, "error_details", "") or "")]
    content = getattr(exc, "content", b"")
    parts.append(content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or ""))
    return " ".join(p for p in parts if p)


def history_expired(exc: Exception) -> bool:
    """Is this ``history.list`` failure Gmail saying the start ``historyId``
    is too old or invalid? 404 always (Google's documented answer); 400 only
    when Google's own words name the history id. A 429, a 5xx or a 403 is a
    failing poll, never an expired mark (2026-10-11 review)."""
    status = _http_status(exc)
    if status == 404:
        return True
    if status == 400:
        return "historyid" in _google_words(exc).lower().replace("_", "")
    return False


def http_error_text(exc: Exception) -> str:
    """``"<status> <reason>"`` for a googleapiclient HttpError, else the
    exception in one line."""
    status = _http_status(exc)
    reason = getattr(exc, "reason", None) or getattr(getattr(exc, "resp", None), "reason", None)
    if status:
        return f"{status} {reason or ''}".strip()
    return f"{type(exc).__name__}: {exc}"[:200]


# =====================================================================
# Status
# =====================================================================

async def is_configured() -> bool:
    """Check if Gmail credentials exist and are valid."""
    row = await _load_credentials_row()
    return row is not None and row.refresh_token is not None


async def get_email_address() -> Optional[str]:
    """Get the connected Gmail address."""
    row = await _load_credentials_row()
    return row.email_address if row else None


async def get_status() -> dict:
    """Get Gmail connection status."""
    row = await _load_credentials_row()
    if not row or not row.refresh_token:
        return {"configured": False, "email_address": None, "token_valid": False}

    # Quick validity check
    creds = _build_credentials_from_row(row)
    return {
        "configured": True,
        "email_address": row.email_address,
        "token_valid": creds.valid if hasattr(creds, "valid") else True,
    }


# =====================================================================
# OAuth flow
# =====================================================================

# Connect flows the owner started and Google has not answered yet: state ->
# {"verifier", "redirect_uri", "created_at"} (2026-10-11 security review).
# The callback is public, so a code is exchanged only with a state minted
# here behind the owner's login, once, within ten minutes, and with that state's
# own PKCE verifier. In memory: the backend is one process, and a restart
# only means pressing Connect again.
_OAUTH_STATE_TTL_S = 600
_OAUTH_STATES_MAX = 16
_pending_oauth: dict[str, dict] = {}
_clock = time.monotonic

_START_AGAIN = "Start again from Settings → Gmail."


class GmailOAuthRefused(Exception):
    """The callback is refused and nothing is stored; the message is for the owner.

    ``reason`` is a short code for the log, which never carries the
    addresses the message may name.
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _oauth_flow(redirect_uri: Optional[str], code_verifier: str, state: Optional[str] = None):
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GMAIL_CLIENT_ID,
                "client_secret": GMAIL_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GMAIL_SCOPES,
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri or GMAIL_REDIRECT_URI
    return flow


def _remember_state(state: str, verifier: str, redirect_uri: str) -> None:
    now = _clock()
    for old in [s for s, p in _pending_oauth.items() if now - p["created_at"] > _OAUTH_STATE_TTL_S]:
        del _pending_oauth[old]
    while len(_pending_oauth) >= _OAUTH_STATES_MAX:
        del _pending_oauth[next(iter(_pending_oauth))]  # the oldest
    _pending_oauth[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "created_at": now}


def _take_state(state: str) -> dict:
    """The pending flow for ``state``, removed (single use), or GmailOAuthRefused."""
    if not state:
        raise GmailOAuthRefused(
            "Google's answer came without a state, so I can't tell it came from my Connect "
            f"button. I saved nothing. {_START_AGAIN}",
            "no_state",
        )
    pending = _pending_oauth.pop(state, None)
    if pending is None:
        raise GmailOAuthRefused(
            "I don't recognise this sign-in: it wasn't started here, or it was already used. "
            f"I saved nothing. {_START_AGAIN}",
            "unknown_state",
        )
    if _clock() - pending["created_at"] > _OAUTH_STATE_TTL_S:
        raise GmailOAuthRefused(
            f"This sign-in took more than {_OAUTH_STATE_TTL_S // 60} minutes and expired. "
            f"I saved nothing. {_START_AGAIN}",
            "expired_state",
        )
    return pending


def discard_oauth_state(state: str) -> None:
    """Spend a state whose flow ended without a code (consent declined)."""
    _pending_oauth.pop(state or "", None)


async def _check_account(email_address: str) -> None:
    """Refuse (GmailOAuthRefused) a Google account that isn't the one to connect.

    ``GMAIL_ACCOUNT`` (optional, read per call) names the only account
    allowed; an account already connected is only ever replaced by itself
    — a different one needs a Disconnect first.
    """
    if not email_address:
        raise GmailOAuthRefused(
            f"Google didn't tell me which account that was, so I saved nothing. {_START_AGAIN}",
            "no_account",
        )
    wanted = os.getenv("GMAIL_ACCOUNT", "").strip()
    if wanted and email_address.lower() != wanted.lower():
        raise GmailOAuthRefused(
            f"You signed in as {email_address}, but I'm set up to connect only {wanted} "
            "(GMAIL_ACCOUNT). I saved nothing.",
            "not_gmail_account",
        )
    row = await _load_credentials_row()
    if row and row.refresh_token:
        connected = (row.email_address or "").strip()
        if connected.lower() != email_address.lower():
            raise GmailOAuthRefused(
                f"Gmail is already connected as {connected or 'another account'}. Disconnect it in "
                f"Settings → Gmail first, then connect {email_address}. I saved nothing.",
                "another_account_connected",
            )


async def get_oauth_url(redirect_uri: Optional[str] = None) -> str:
    """Generate the Google OAuth authorization URL with a fresh state and PKCE challenge."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 unreserved characters (RFC 7636: 43–128)
    redirect_uri = redirect_uri or GMAIL_REDIRECT_URI
    flow = _oauth_flow(redirect_uri, verifier, state)

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    _remember_state(state, verifier, redirect_uri)
    return auth_url


async def exchange_code(code: str, state: str) -> dict:
    """Exchange an authorization code for tokens and store them.

    Raises GmailOAuthRefused (nothing stored) for a missing, unknown,
    expired or reused ``state``, and for an account ``_check_account``
    refuses.
    """
    pending = _take_state(state)
    flow = _oauth_flow(pending["redirect_uri"], pending["verifier"], state)

    await asyncio.to_thread(flow.fetch_token, code=code)
    creds = flow.credentials

    # Get user's email address
    service = await asyncio.to_thread(_build_service, creds)
    profile = await asyncio.to_thread(
        lambda: service.users().getProfile(userId="me").execute()
    )
    email_address = (profile.get("emailAddress") or "").strip()

    await _check_account(email_address)
    await _save_credentials(creds, email_address)
    _clear_refresh_error()  # a fresh sign-in: no longer lapsed

    return {"email_address": email_address, "success": True}


async def revoke_and_disconnect() -> None:
    """Revoke tokens and delete credentials from DB."""
    creds = await _get_credentials()
    if creds:
        try:
            import httpx
            await httpx.AsyncClient().post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": creds.token},
            )
        except Exception as e:
            print(f"[Gmail] Token revocation failed (non-fatal): {e}")

    async with async_session() as session:
        result = await session.execute(
            select(GmailCredentialsModel).where(GmailCredentialsModel.id == "default")
        )
        row = result.scalar_one_or_none()
        if row:
            await session.delete(row)
            await session.commit()
    _clear_refresh_error()  # not connected is said as such, never as lapsed


# =====================================================================
# Core operations
# =====================================================================

def _parse_headers(headers: list[dict]) -> dict:
    """Parse Gmail API headers list into a dict."""
    return {h["name"].lower(): h["value"] for h in headers}


def _decode_body(payload: dict) -> str:
    """Extract text body from Gmail message payload (MIME tree)."""
    # Direct body
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart — prefer text/plain, fallback to text/html
    parts = payload.get("parts", [])
    text_part = None
    html_part = None

    for part in parts:
        mime = part.get("mimeType", "")
        if mime == "text/plain" and part.get("body", {}).get("data"):
            text_part = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        elif mime == "text/html" and part.get("body", {}).get("data"):
            html_part = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        elif mime.startswith("multipart/"):
            # Recurse into nested multipart
            nested = _decode_body(part)
            if nested:
                if text_part is None:
                    text_part = nested

    if text_part:
        return text_part
    if html_part:
        # Strip HTML tags for plain text fallback
        return re.sub(r"<[^>]+>", "", html_part).strip()
    return ""


def _extract_links(body: str) -> list[str]:
    """Extract URLs from email body text."""
    return re.findall(r"https?://[^\s<>\"')\]]+", body)


def _has_unsubscribe(headers: dict) -> bool:
    """Check if List-Unsubscribe header exists."""
    return "list-unsubscribe" in headers


async def search_gmail(
    query: str = "",
    max_results: int = 20,
    label_ids: Optional[list[str]] = None,
) -> list[dict]:
    """Search Gmail messages."""
    service = await _get_service()
    if not service:
        return [{"error": "Gmail not configured"}]

    def _search():
        params = {"userId": "me", "maxResults": min(max_results, 50)}
        if query:
            params["q"] = query
        if label_ids:
            params["labelIds"] = label_ids

        resp = service.users().messages().list(**params).execute()
        message_ids = [m["id"] for m in resp.get("messages", [])]
        if not message_ids:
            return []

        # Batch fetch metadata
        results = []
        for msg_id in message_ids:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date", "List-Unsubscribe"],
            ).execute()
            headers = _parse_headers(msg.get("payload", {}).get("headers", []))
            results.append({
                "message_id": msg["id"],
                "thread_id": msg.get("threadId"),
                "sender": headers.get("from", ""),
                "subject": headers.get("subject", "(no subject)"),
                "date": headers.get("date", ""),
                "internal_date": _internal_date(msg),
                "snippet": msg.get("snippet", ""),
                "labels": msg.get("labelIds", []),
                "has_unsubscribe": "list-unsubscribe" in headers,
            })
        return results

    try:
        return await asyncio.to_thread(_search)
    except Exception as e:
        return [{"error": f"Gmail search failed: {e}"}]


def _walk_parts(payload: dict):
    """Every leaf part of a Gmail message payload, depth first."""
    parts = payload.get("parts") or []
    if not parts:
        yield payload
        return
    for part in parts:
        yield from _walk_parts(part)


def _message_view(msg: dict) -> dict:
    """A full Gmail message as Edward reads it: headers, the text body
    (text/plain preferred, else the HTML as text), attachments, links."""
    from services.mail_service import html_links, html_to_text, merge_links

    payload = msg.get("payload", {}) or {}
    headers = _parse_headers(payload.get("headers", []))
    text_body = None
    html_body = None
    attachments = []
    for part in _walk_parts(payload):
        body = part.get("body", {}) or {}
        if part.get("filename"):
            attachments.append({"name": part["filename"], "size": int(body.get("size") or 0)})
            continue
        data = body.get("data")
        if not data:
            continue
        decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        mime = part.get("mimeType", "")
        if mime == "text/plain" and text_body is None:
            text_body = decoded
        elif mime == "text/html" and html_body is None:
            html_body = decoded
    body = (text_body or "").strip() or (html_to_text(html_body) if html_body else "")
    return {
        "message_id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "sender": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "internal_date": _internal_date(msg),
        "body": body,
        "links": merge_links(_extract_links(body), html_links(html_body)),
        "attachments": attachments,
        "labels": msg.get("labelIds", []),
        "message_id_header": headers.get("message-id", ""),
        "reply_to": headers.get("reply-to", ""),
        "references": headers.get("references", ""),
        "headers": {
            k: v for k, v in headers.items()
            if k in ("list-unsubscribe", "list-unsubscribe-post", "from", "to", "subject", "date")
        },
    }


async def read_gmail(message_id: str) -> dict:
    """Read full content of a Gmail message (``_message_view``), or
    ``{"error": …}``; ``not_found`` when Gmail has no such message."""
    service = await _get_service()
    if not service:
        return {"error": "Gmail not configured"}

    def _read():
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full",
        ).execute()
        return _message_view(msg)

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:
        return {"error": f"Gmail read failed: {http_error_text(e)}", "not_found": _http_status(e) == 404}


async def read_gmail_thread(thread_id: str) -> dict:
    """A whole Gmail thread (``threads.get``, format full): ``{"thread_id",
    "messages": [view, …]}`` oldest first, or ``{"error": …}``."""
    service = await _get_service()
    if not service:
        return {"error": "Gmail not configured"}

    def _read():
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        views = [_message_view(m) for m in thread.get("messages", [])]
        views.sort(key=lambda v: v.get("internal_date") or 0)
        return {"thread_id": thread.get("id") or thread_id, "messages": views}

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:
        return {"error": f"Gmail read failed: {http_error_text(e)}", "not_found": _http_status(e) == 404}


_REPLY_HEADERS = ["Message-ID", "Reply-To", "From", "To", "Cc", "Subject", "References"]


def _reply_headers(msg: dict, thread_id: Optional[str] = None) -> dict:
    """What a reply needs from one message: its ids, labels (SENT and DRAFT
    say it is the owner's own) and the address headers."""
    headers = _parse_headers(msg.get("payload", {}).get("headers", []))
    return {
        "message_id": msg.get("id"),
        "thread_id": msg.get("threadId") or thread_id,
        "labels": list(msg.get("labelIds") or []),
        "internal_date": _internal_date(msg),
        "message_id_header": headers.get("message-id", ""),
        "reply_to": headers.get("reply-to", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "references": headers.get("references", ""),
    }


async def get_message_headers(message_id: str) -> dict:
    """What a reply needs from one message (``_reply_headers``). Raises
    GmailError (GmailNotConnected / GmailAuthLapsed included) when it can't
    be read."""
    service = await _require_service()

    def _get():
        msg = service.users().messages().get(
            userId="me", id=message_id, format="metadata", metadataHeaders=_REPLY_HEADERS,
        ).execute()
        return _reply_headers(msg)

    try:
        return await asyncio.to_thread(_get)
    except Exception as e:  # noqa: BLE001
        raise GmailError(f"Gmail couldn't read gmail:{message_id} ({http_error_text(e)})") from e


async def thread_headers(thread_id: str) -> list[dict]:
    """Every message of a thread, oldest first, as ``_reply_headers`` gives
    it — the reply chooses whom to answer (``toolset.mail._reply_target``).
    Raises GmailError."""
    service = await _require_service()

    def _get():
        thread = service.users().threads().get(userId="me", id=thread_id, format="metadata",
                                               metadataHeaders=_REPLY_HEADERS).execute()
        msgs = sorted(thread.get("messages", []), key=lambda m: _internal_date(m) or 0)
        if not msgs:
            raise GmailError(f"Gmail thread {thread_id} is empty")
        return [_reply_headers(m, thread_id) for m in msgs]

    try:
        return await asyncio.to_thread(_get)
    except GmailError:
        raise
    except Exception as e:  # noqa: BLE001
        raise GmailError(f"Gmail couldn't read gmail:thread:{thread_id} ({http_error_text(e)})") from e


async def send_gmail(to: str, subject: str, body: str, *, attachments=(), thread_id: Optional[str] = None,
                     in_reply_to: Optional[str] = None, references: Optional[str] = None) -> dict:
    """Send one email from the connected account (``users.messages.send``,
    covered by gmail.modify). ``{"delivered", "unconfirmed", "id",
    "thread_id", "labels", "from", "error"}``: delivered only when Gmail's
    answer has an id and the SENT label (one ``format=minimal`` get when the
    answer leaves the labels out) — never "sent" on a guess; ``unconfirmed``
    when Gmail took it (an id) but that get failed: it went out, so the
    caller must not resend. From is left to Gmail."""
    import mimetypes
    from email.message import EmailMessage

    try:
        service = await _require_service()
    except GmailError as e:
        return {"delivered": False, "unconfirmed": False, "id": None, "thread_id": None, "labels": [], "from": None,
                "error": str(e)}

    def _send():
        from googleapiclient.http import MediaInMemoryUpload

        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject or ""
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        msg.set_content(body or "", charset="utf-8")
        for path in attachments or ():
            ctype, _ = mimetypes.guess_type(str(path))
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            with open(path, "rb") as fh:
                data = fh.read()
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                               filename=os.path.basename(str(path)))
        media = MediaInMemoryUpload(msg.as_bytes(), mimetype="message/rfc822", resumable=False)
        resp = service.users().messages().send(
            userId="me", body={"threadId": thread_id} if thread_id else {}, media_body=media,
        ).execute()
        labels = resp.get("labelIds")
        if labels is None and resp.get("id"):
            # Gmail has taken it: a failure from here on is "unconfirmed",
            # never "not sent" — a retry would send it twice (2026-10-11).
            try:
                got = service.users().messages().get(userId="me", id=resp["id"], format="minimal").execute()
            except Exception as e:  # noqa: BLE001
                return {"delivered": False, "unconfirmed": True, "id": resp["id"], "thread_id": resp.get("threadId"),
                        "labels": [], "from": None,
                        "error": f"sent but unconfirmed (id {resp['id']}): {http_error_text(e)}"}
            labels = got.get("labelIds")
        labels = list(labels or [])
        delivered = bool(resp.get("id")) and "SENT" in labels
        return {"delivered": delivered, "unconfirmed": False, "id": resp.get("id"), "thread_id": resp.get("threadId"),
                "labels": labels, "from": None, "error": None if delivered else "Gmail didn't mark it sent"}

    try:
        out = await asyncio.to_thread(_send)
    except Exception as e:  # noqa: BLE001 — an HttpError, a file that vanished
        return {"delivered": False, "unconfirmed": False, "id": None, "thread_id": None, "labels": [], "from": None,
                "error": http_error_text(e)}
    try:
        out["from"] = await get_email_address()
    except Exception:  # noqa: BLE001
        pass
    return out


async def delete_gmail(message_id: str) -> dict:
    """Move a Gmail message to Trash."""
    service = await _get_service()
    if not service:
        return {"error": "Gmail not configured"}

    def _trash():
        service.users().messages().trash(userId="me", id=message_id).execute()
        return {"success": True, "message_id": message_id, "action": "trashed"}

    try:
        result = await asyncio.to_thread(_trash)
        # Audit trail
        await _log_gmail_action(message_id, "delete")
        return result
    except Exception as e:
        return {"error": f"Gmail delete failed: {e}"}


async def archive_gmail(message_id: str) -> dict:
    """Archive a Gmail message (remove INBOX label)."""
    service = await _get_service()
    if not service:
        return {"error": "Gmail not configured"}

    def _archive():
        service.users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return {"success": True, "message_id": message_id, "action": "archived"}

    try:
        result = await asyncio.to_thread(_archive)
        await _log_gmail_action(message_id, "archive")
        return result
    except Exception as e:
        return {"error": f"Gmail archive failed: {e}"}


async def label_gmail(
    message_id: str,
    add_labels: Optional[list[str]] = None,
    remove_labels: Optional[list[str]] = None,
) -> dict:
    """Modify labels on a Gmail message."""
    service = await _get_service()
    if not service:
        return {"error": "Gmail not configured"}

    def _modify():
        body = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels
        service.users().messages().modify(
            userId="me", id=message_id, body=body,
        ).execute()
        return {"success": True, "message_id": message_id, "added": add_labels, "removed": remove_labels}

    try:
        return await asyncio.to_thread(_modify)
    except Exception as e:
        return {"error": f"Gmail label failed: {e}"}


async def get_gmail_headers(message_id: str) -> dict:
    """Get unsubscribe-related headers from a Gmail message."""
    service = await _get_service()
    if not service:
        return {"error": "Gmail not configured"}

    def _headers():
        msg = service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post", "From", "Subject"],
        ).execute()
        headers = _parse_headers(msg.get("payload", {}).get("headers", []))
        return {
            "message_id": message_id,
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "list_unsubscribe": headers.get("list-unsubscribe", ""),
            "list_unsubscribe_post": headers.get("list-unsubscribe-post", ""),
        }

    try:
        return await asyncio.to_thread(_headers)
    except Exception as e:
        return {"error": f"Gmail headers failed: {e}"}


# =====================================================================
# History API (for heartbeat polling)
# =====================================================================

async def get_current_history_id() -> Optional[str]:
    """Get the current historyId from Gmail profile."""
    service = await _get_service()
    if not service:
        return None

    def _get_profile():
        return service.users().getProfile(userId="me").execute()

    try:
        profile = await asyncio.to_thread(_get_profile)
        return str(profile.get("historyId", ""))
    except Exception as e:
        print(f"[Gmail] Failed to get history ID: {e}")
        return None


async def get_history(start_history_id: str) -> list[dict]:
    """New messages added to INBOX since ``start_history_id``.

    Raises GmailHistoryExpired when Gmail no longer has that history
    (``history_expired``: a 404, or a 400 naming the history id) — the
    caller re-seeds — and GmailError for anything else (a 429, a 5xx, a
    403), so a failing poll is never read as an empty inbox or an expired
    mark."""
    service = await _require_service()

    def _fetch_history():
        results = []
        page_token = None

        while True:
            params = {
                "userId": "me",
                "startHistoryId": start_history_id,
                "historyTypes": ["messageAdded"],
                "labelId": "INBOX",
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                resp = service.users().history().list(**params).execute()
            except Exception as e:  # noqa: BLE001 — sorted below, by status and Google's words
                if history_expired(e):
                    raise GmailHistoryExpired(f"history {start_history_id} expired ({http_error_text(e)})") from e
                raise GmailError(f"Gmail history failed: {http_error_text(e)}") from e

            for record in resp.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg = added.get("message", {})
                    if "INBOX" in msg.get("labelIds", []):
                        results.append({
                            "message_id": msg["id"],
                            "thread_id": msg.get("threadId"),
                            "labels": msg.get("labelIds", []),
                        })

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return results

    return await asyncio.to_thread(_fetch_history)


async def get_message_metadata(message_id: str) -> Optional[dict]:
    """Get sender, subject, snippet, labels for a single message."""
    service = await _get_service()
    if not service:
        return None

    def _get():
        msg = service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["From", "Subject", "Date", "List-Unsubscribe"],
        ).execute()
        headers = _parse_headers(msg.get("payload", {}).get("headers", []))
        return {
            "message_id": msg["id"],
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "internal_date": _internal_date(msg),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
            "has_unsubscribe": "list-unsubscribe" in headers,
        }

    try:
        return await asyncio.to_thread(_get)
    except Exception as e:
        print(f"[Gmail] Metadata fetch failed for {message_id}: {e}")
        return None


# =====================================================================
# Audit trail
# =====================================================================

async def _log_gmail_action(message_id: str, action: str, sender: str = "", subject: str = "") -> None:
    """Log a Gmail action to the email_actions audit trail."""
    try:
        from services.email_management_service import _extract_domain

        async with async_session() as session:
            from services.database import EmailActionModel
            row = EmailActionModel(
                email_rowid=None,
                sender=sender,
                sender_domain=_extract_domain(sender),
                subject=subject[:500] if subject else f"gmail:{message_id}",
                action=action,
                action_source="llm",
                status="completed",
            )
            session.add(row)
            await session.commit()
    except Exception as e:
        print(f"[Gmail] Audit log failed: {e}")


# =====================================================================
# History ID persistence
# =====================================================================

async def get_last_history_id() -> Optional[str]:
    """Get the stored last_history_id."""
    row = await _load_credentials_row()
    return row.last_history_id if row else None


async def update_last_history_id(history_id: str) -> None:
    """Update the stored last_history_id."""
    async with async_session() as session:
        result = await session.execute(
            select(GmailCredentialsModel).where(GmailCredentialsModel.id == "default")
        )
        row = result.scalar_one_or_none()
        if row:
            row.last_history_id = history_id
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, the column convention
            await session.commit()
