"""
HTML Hosting service for Edward.

Provides ability to create, update, and delete hosted HTML pages on a page
host you run or subscribe to: ``HTML_HOSTING_URL`` (its base URL) and
``HTML_HOSTING_API_KEY``. There is no default host — with either unset the
service reports "not configured" and every call refuses.

ADAPT: the calls speak a small upload API (``POST /api/v2/upload``,
``POST /api/v2/upload/<slug>``, ``DELETE /api/v2/file/<slug>``,
``GET /api/v2/check-slug/<slug>``, key in ``X-API-Key``); point
``HTML_HOSTING_URL`` at a host that speaks it, or change these calls.
"""

import os
from typing import Optional
import httpx


# Configuration
HTML_HOSTING_API_KEY = os.getenv("HTML_HOSTING_API_KEY")
HTML_HOSTING_URL = (os.getenv("HTML_HOSTING_URL") or "").strip().rstrip("/")

NOT_CONFIGURED = "HTML hosting is not configured (set HTML_HOSTING_URL and HTML_HOSTING_API_KEY)"


def is_configured() -> bool:
    """Check if HTML Hosting API is configured: a host and a key."""
    return bool(HTML_HOSTING_API_KEY) and bool(HTML_HOSTING_URL)


def _require_configured() -> None:
    if not is_configured():
        raise Exception(NOT_CONFIGURED)


# Result of the last real call to the API: (ok, message). None until something
# has actually tried. "A key is present" is not evidence the key works — a
# revoked or mistyped key used to show green in settings with no way to tell.
_verification: Optional[tuple] = None


def reset_verification() -> None:
    """Forget the cached probe result (used by tests and on key change)."""
    global _verification
    _verification = None


def record_verification(ok: bool, message: str) -> None:
    """Record the outcome of a real API call so get_status can report it."""
    global _verification
    _verification = (bool(ok), message)


def interpret_probe(status_code: Optional[int], error: Optional[str]) -> tuple:
    """Map a probe outcome to (ok, human-readable message)."""
    if error is not None:
        return False, f"API unreachable: {error}"
    if status_code == 200:
        return True, "API reachable, key accepted"
    if status_code == 401:
        return False, "API key required — no key was sent"
    if status_code == 403:
        return False, "Invalid API key — regenerate it on your page host"
    return False, f"Unexpected response from API (HTTP {status_code})"


async def check_connection() -> bool:
    """Probe the API with the configured key and cache the verdict."""
    if not HTML_HOSTING_API_KEY:
        record_verification(False, "HTML_HOSTING_API_KEY not set")
        return False
    if not HTML_HOSTING_URL:
        record_verification(False, "HTML_HOSTING_URL not set — not configured")
        return False

    status_code: Optional[int] = None
    error: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{HTML_HOSTING_URL}/api/v2/check-slug/edward-healthcheck",
                headers={"X-API-Key": HTML_HOSTING_API_KEY},
            )
            status_code = response.status_code
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__

    ok, message = interpret_probe(status_code, error)
    record_verification(ok, message)
    return ok


def get_status() -> dict:
    """
    Get HTML Hosting service status.

    Returns:
        Dict with status, status_message, and optional metadata
    """
    if not HTML_HOSTING_API_KEY:
        return {
            "status": "error",
            "status_message": "HTML_HOSTING_API_KEY not set",
        }
    if not HTML_HOSTING_URL:
        return {
            "status": "error",
            "status_message": "Not configured — set HTML_HOSTING_URL to your page host",
        }

    if _verification is None:
        return {
            # "connecting", not "connected": SkillStatus has no "configured"
            # member and get_all_skills() coerces this straight into the enum.
            "status": "connecting",
            "status_message": "API key set — not yet verified against the API",
        }

    ok, message = _verification
    return {
        "status": "connected" if ok else "error",
        "status_message": message,
    }


async def create_page(
    html: str,
    slug: Optional[str] = None,
    description: Optional[str] = None,
    duration: Optional[str] = None,
) -> dict:
    """
    Create a new hosted HTML page.

    Args:
        html: The HTML content for the page
        slug: Optional custom slug for the URL
        description: Optional description of the page
        duration: Optional expiration duration ("1day", "30days", "6months", "permanent")

    Returns:
        Parsed JSON response with id, slug, url, size, expiresAt, etc.

    Raises:
        Exception if hosting is not configured (URL and key) or the request fails
    """
    _require_configured()

    body = {"html": html}
    if slug is not None:
        body["slug"] = slug
    if description is not None:
        body["description"] = description
    if duration is not None:
        body["duration"] = duration

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{HTML_HOSTING_URL}/api/v2/upload",
            json=body,
            headers={
                "X-API-Key": HTML_HOSTING_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def update_page(
    slug: str,
    html: str,
    description: Optional[str] = None,
    duration: Optional[str] = None,
) -> dict:
    """
    Update an existing hosted HTML page.

    Args:
        slug: The slug of the page to update
        html: The new HTML content for the page
        description: Optional updated description
        duration: Optional updated expiration duration

    Returns:
        Parsed JSON response with updated page details

    Raises:
        Exception if hosting is not configured (URL and key) or the request fails
    """
    _require_configured()

    body = {"html": html}
    if description is not None:
        body["description"] = description
    if duration is not None:
        body["duration"] = duration

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{HTML_HOSTING_URL}/api/v2/upload/{slug}",
            json=body,
            headers={
                "X-API-Key": HTML_HOSTING_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def delete_page(slug: str) -> dict:
    """
    Delete a hosted HTML page.

    Args:
        slug: The slug of the page to delete

    Returns:
        Parsed JSON response with success confirmation

    Raises:
        Exception if hosting is not configured (URL and key) or the request fails
    """
    _require_configured()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{HTML_HOSTING_URL}/api/v2/file/{slug}",
            headers={
                "X-API-Key": HTML_HOSTING_API_KEY,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def check_slug(slug: str) -> dict:
    """
    Check if a slug is available for use.

    Args:
        slug: The slug to check availability for

    Returns:
        Dict with available (bool) and reason (str or null)

    Raises:
        Exception if hosting is not configured (URL and key) or the request fails
    """
    _require_configured()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{HTML_HOSTING_URL}/api/v2/check-slug/{slug}",
            headers={
                "X-API-Key": HTML_HOSTING_API_KEY,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()
