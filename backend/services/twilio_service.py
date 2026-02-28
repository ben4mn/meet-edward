"""
Twilio SMS service for Edward's messaging capabilities.

Handles both outbound SMS (Edward's phone number) and inbound webhook processing.
"""

import os
from typing import Optional
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
