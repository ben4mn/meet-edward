"""
Twilio SMS service for Edward's messaging capabilities.

Handles both outbound SMS (Edward's phone number) and inbound webhook processing.
"""

import asyncio
import os
import time
from typing import Any, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx
from twilio.rest import Client
from twilio.request_validator import RequestValidator


# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_WEBHOOK_URL = os.getenv("TWILIO_WEBHOOK_URL")

# Lazy client initialization
_client: Optional[Client] = None
_validator: Optional[RequestValidator] = None


def _get_client() -> Client:
    """Get or create the Twilio client."""
    global _client
    if _client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise ValueError(
                "Twilio credentials not configured. "
                "Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN environment variables."
            )
        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _client


def _get_validator() -> RequestValidator:
    """Get or create the Twilio request validator."""
    global _validator
    if _validator is None:
        if not TWILIO_AUTH_TOKEN:
            raise ValueError("TWILIO_AUTH_TOKEN required for webhook validation.")
        _validator = RequestValidator(TWILIO_AUTH_TOKEN)
    return _validator


def is_configured() -> bool:
    """Check if Twilio is properly configured."""
    return all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER])


async def send_sms(to_number: str, message: str) -> dict:
    """
    Send an SMS message via Twilio.

    Args:
        to_number: The recipient's phone number (E.164 format preferred)
        message: The message content

    Returns:
        Dict with message SID and status

    Raises:
        ValueError: If Twilio is not configured
        Exception: If sending fails
    """
    if not is_configured():
        raise ValueError(
            "Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "and TWILIO_PHONE_NUMBER environment variables."
        )

    # Normalize phone number (basic cleanup)
    to_number = normalize_phone_number(to_number)

    client = _get_client()

    # Send the message
    twilio_message = client.messages.create(
        body=message,
        from_=TWILIO_PHONE_NUMBER,
        to=to_number
    )

    return {
        "sid": twilio_message.sid,
        "status": twilio_message.status,
        "to": twilio_message.to,
        "from": twilio_message.from_
    }


def validate_webhook_signature(
    url: str,
    params: dict,
    signature: str
) -> bool:
    """
    Validate an incoming Twilio webhook request.

    Args:
        url: The full webhook URL
        params: The request body parameters
        signature: The X-Twilio-Signature header value

    Returns:
        True if the signature is valid
    """
    if not signature:
        return False

    try:
        validator = _get_validator()
        return validator.validate(url, params, signature)
    except ValueError:
        return False


def _first_header(value: Optional[str]) -> str:
    """The first of a comma-separated proxy header (``https, http`` → ``https``)."""
    return (value or "").split(",", 1)[0].strip()


def received_webhook_url(request_url: str, headers: Mapping[str, str]) -> str:
    """The URL Twilio POSTed to, as this request was received: the path and
    query of ``request_url`` with the scheme and host a proxy or tunnel in
    front saw — ``X-Forwarded-Proto`` and ``X-Forwarded-Host`` (else
    ``Host``), so ``http://localhost:8000/api/webhook/twilio`` behind a
    tunnel checks as ``https://edward.example.com/api/webhook/twilio``."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    parts = urlsplit(request_url)
    scheme = _first_header(h.get("x-forwarded-proto")) or parts.scheme or "https"
    host = _first_header(h.get("x-forwarded-host")) or (h.get("host") or "").strip() or parts.netloc
    return urlunsplit((scheme, host, parts.path, parts.query, ""))


def check_webhook_signature(
    *,
    request_url: str,
    headers: Mapping[str, str],
    params: Mapping[str, Any],
    configured_url: Optional[str] = None,
) -> Tuple[bool, str]:
    """``(accepted, why)`` for an inbound Twilio webhook. Fails closed.

    A request is accepted only when its ``X-Twilio-Signature`` is valid for
    ``TWILIO_AUTH_TOKEN`` and one of the URLs Twilio may have signed: the
    configured webhook URL (``TWILIO_WEBHOOK_URL`` …) when there is one,
    then the URL as received (``received_webhook_url``). Without a token
    nothing can be checked, so nothing is accepted: an inbound webhook runs
    a model turn, and an unchecked one let anyone who found the URL start
    one (it used to be accepted whenever ``TWILIO_WEBHOOK_URL`` was unset).
    ``why`` is for the log; it never carries the token or the signature."""
    token = (TWILIO_AUTH_TOKEN or "").strip()
    if not token:
        return False, "TWILIO_AUTH_TOKEN isn't set, so the signature can't be checked"
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    signature = (h.get("x-twilio-signature") or "").strip()
    if not signature:
        return False, "the request has no X-Twilio-Signature header"
    candidates: List[str] = []
    for url in ((configured_url or "").strip(), received_webhook_url(request_url, h)):
        if url and url not in candidates:
            candidates.append(url)
    validator = RequestValidator(token)
    for url in candidates:
        try:
            if validator.validate(url, dict(params or {}), signature):
                return True, f"signed for {url}"
        except Exception:  # noqa: BLE001 — a malformed request is a mismatch
            continue
    return False, "the signature doesn't match " + " or ".join(candidates)


def normalize_phone_number(number: str) -> str:
    """
    Normalize a phone number to E.164 format.

    Args:
        number: The phone number to normalize

    Returns:
        Normalized phone number string
    """
    import phonenumbers

    # Remove common formatting characters
    cleaned = number.strip()

    try:
        # Try to parse as US number if no country code
        if not cleaned.startswith('+'):
            parsed = phonenumbers.parse(cleaned, "US")
        else:
            parsed = phonenumbers.parse(cleaned)

        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )
    except phonenumbers.NumberParseException:
        # Return original if parsing fails (let Twilio validate)
        return cleaned


async def send_whatsapp(to_number: str, message: str) -> dict:
    """
    Send a WhatsApp message via Twilio.

    Args:
        to_number: The recipient's phone number (E.164 format preferred)
        message: The message content

    Returns:
        Dict with message SID and status
    """
    if not is_configured():
        raise ValueError(
            "Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "and TWILIO_PHONE_NUMBER environment variables."
        )

    to_number = normalize_phone_number(to_number)

    client = _get_client()

    twilio_message = client.messages.create(
        body=message,
        from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
        to=f"whatsapp:{to_number}"
    )

    return {
        "sid": twilio_message.sid,
        "status": twilio_message.status,
        "to": to_number,
        "from": TWILIO_PHONE_NUMBER
    }


# ----------------------------------------------------- the account check --
#
# Three env vars said "connected" while the account behind them was
# inactive (2026-10-11, the integrations audit): the Settings rows now ask
# Twilio. One small GET, 3 s, the answer kept 10 minutes, one request in
# flight; the SID never goes in a message or a log line (an httpx error's
# text carries the URL, so only its type is logged).

ACCOUNT_STATUS_TTL_S = 600
ACCOUNT_STATUS_TIMEOUT_S = 3.0
_now = time.monotonic
_account_cache: Optional[tuple] = None          # (at, verdict)
_account_inflight: Optional["asyncio.Task"] = None


async def _fetch_account() -> httpx.Response:
    """``GET /2010-04-01/Accounts/<sid>.json`` with the account's own
    credentials (conftest refuses it in tests)."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}.json"
    async with httpx.AsyncClient(timeout=ACCOUNT_STATUS_TIMEOUT_S) as client:
        return await client.get(url, auth=(TWILIO_ACCOUNT_SID or "", TWILIO_AUTH_TOKEN or ""))


async def _check_account() -> dict:
    global _account_cache
    try:
        resp = await _fetch_account()
    except Exception as exc:  # noqa: BLE001 - a timeout, a refused connection, DNS
        print(f"[Twilio] account check failed ({type(exc).__name__})")
        verdict = {"status": "error", "status_message": "couldn't reach Twilio"}
    else:
        if resp.status_code in (401, 403):
            verdict = {"status": "error",
                       "status_message": "Twilio rejected the credentials — the account looks inactive"}
        elif resp.status_code != 200:
            verdict = {"status": "error", "status_message": f"couldn't reach Twilio (HTTP {resp.status_code})"}
        else:
            try:
                state = str((resp.json() or {}).get("status") or "unknown")
            except ValueError:
                state = "unknown"
            verdict = ({"status": "connected", "status_message": "Twilio account active"} if state == "active"
                       else {"status": "error", "status_message": f"Twilio account inactive ({state})"})
    _account_cache = (_now(), verdict)
    return verdict


async def account_status() -> dict:
    """``{"status": "connected"|"error", "status_message"}`` for the Twilio
    account: active, inactive (suspended/closed), credentials rejected, or
    unreachable. Cached ``ACCOUNT_STATUS_TTL_S``; concurrent callers share
    one request."""
    global _account_inflight
    if not is_configured():
        return {"status": "error", "status_message": "Twilio isn't configured"}
    cached = _account_cache
    if cached is not None and _now() - cached[0] < ACCOUNT_STATUS_TTL_S:
        return dict(cached[1])
    loop = asyncio.get_running_loop()
    task = _account_inflight
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_check_account())
        _account_inflight = task
    return dict(await asyncio.shield(task))


def get_phone_number() -> Optional[str]:
    """Get Edward's Twilio phone number."""
    return TWILIO_PHONE_NUMBER


def get_whatsapp_status() -> dict:
    """
    Get the current status of the Twilio WhatsApp service.

    Returns:
        Dict with status info: status, status_message, metadata
    """
    if not is_configured():
        missing = []
        if not TWILIO_ACCOUNT_SID:
            missing.append("TWILIO_ACCOUNT_SID")
        if not TWILIO_AUTH_TOKEN:
            missing.append("TWILIO_AUTH_TOKEN")
        if not TWILIO_PHONE_NUMBER:
            missing.append("TWILIO_PHONE_NUMBER")
        return {
            "status": "error",
            "status_message": f"Missing: {', '.join(missing)}",
            "metadata": None
        }

    try:
        _get_client()
        phone = TWILIO_PHONE_NUMBER
        if phone and len(phone) == 12 and phone.startswith("+1"):
            phone = f"+1 ({phone[2:5]}) {phone[5:8]}-{phone[8:]}"
        return {
            "status": "connected",
            "status_message": f"WhatsApp via {phone}",
            "metadata": {"phone_number": TWILIO_PHONE_NUMBER}
        }
    except Exception as e:
        return {
            "status": "error",
            "status_message": str(e),
            "metadata": None
        }


def get_status() -> dict:
    """
    Get the current status of the Twilio SMS service.

    Returns:
        Dict with status info: status, status_message, metadata
    """
    if not is_configured():
        missing = []
        if not TWILIO_ACCOUNT_SID:
            missing.append("TWILIO_ACCOUNT_SID")
        if not TWILIO_AUTH_TOKEN:
            missing.append("TWILIO_AUTH_TOKEN")
        if not TWILIO_PHONE_NUMBER:
            missing.append("TWILIO_PHONE_NUMBER")
        return {
            "status": "error",
            "status_message": f"Missing: {', '.join(missing)}",
            "metadata": None
        }

    # Try to verify the client works
    try:
        client = _get_client()
        # Format phone number for display
        phone = TWILIO_PHONE_NUMBER
        if phone and len(phone) == 12 and phone.startswith("+1"):
            phone = f"+1 ({phone[2:5]}) {phone[5:8]}-{phone[8:]}"
        return {
            "status": "connected",
            "status_message": phone,
            "metadata": {"phone_number": TWILIO_PHONE_NUMBER}
        }
    except Exception as e:
        return {
            "status": "error",
            "status_message": str(e),
            "metadata": None
        }
