"""
HTML Hosting service for Edward.

Provides ability to create, update, and delete hosted HTML pages on html.zyroi.com.
"""

import os
from typing import Optional
import httpx


# Configuration
HTML_HOSTING_API_KEY = os.getenv("HTML_HOSTING_API_KEY")
HTML_HOSTING_URL = os.getenv("HTML_HOSTING_URL", "https://html.zyroi.com")


def is_configured() -> bool:
    """Check if HTML Hosting API is configured."""
    return bool(HTML_HOSTING_API_KEY)


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

    return {
        "status": "connected",
        "status_message": "API key configured",
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
        Exception if API key not configured or request fails
    """
    if not HTML_HOSTING_API_KEY:
        raise Exception("HTML Hosting API key not configured")

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
        Exception if API key not configured or request fails
    """
    if not HTML_HOSTING_API_KEY:
        raise Exception("HTML Hosting API key not configured")

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
        Exception if API key not configured or request fails
    """
    if not HTML_HOSTING_API_KEY:
        raise Exception("HTML Hosting API key not configured")

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
        Exception if API key not configured or request fails
    """
    if not HTML_HOSTING_API_KEY:
        raise Exception("HTML Hosting API key not configured")

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
