"""
Brave Search service for Edward.

Provides web search and page content fetching capabilities using the Brave Search API.
"""

import os
from typing import List, Optional
import httpx


# Configuration
BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def is_configured() -> bool:
    """Check if Brave Search API is configured."""
    return bool(BRAVE_API_KEY)


def get_status() -> dict:
    """
    Get Brave Search service status.

    Returns:
        Dict with status, status_message, and optional metadata
    """
    if not BRAVE_API_KEY:
        return {
            "status": "error",
            "status_message": "BRAVE_SEARCH_API_KEY not set",
        }

    return {
        "status": "connected",
        "status_message": "API key configured",
    }


async def search(
    query: str,
    count: int = 5,
    freshness: Optional[str] = None
) -> List[dict]:
    """
    Search the web using Brave Search API.

    Args:
        query: Search query string
        count: Number of results to return (1-20, default 5)
        freshness: Optional freshness filter (pd=past day, pw=past week, pm=past month)

    Returns:
        List of search results with title, url, and description

    Raises:
        Exception if API key not configured or request fails
    """
    if not BRAVE_API_KEY:
        raise Exception("Brave Search API key not configured")

    # Clamp count to valid range
    count = max(1, min(20, count))

    params = {
        "q": query,
        "count": count,
    }

    if freshness:
        params["freshness"] = freshness

    headers = {
        "X-Subscription-Token": BRAVE_API_KEY,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            BRAVE_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=10.0
        )
        response.raise_for_status()
        data = response.json()

    results = []
    web_results = data.get("web", {}).get("results", [])

    for result in web_results[:count]:
        results.append({
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "description": result.get("description", ""),
        })

    return results


async def fetch_page_content(url: str, max_chars: int = 4000) -> str:
    """
    Fetch and extract main content from a web page.

    Uses trafilatura for content extraction to get clean article text.

    Args:
        url: The URL to fetch content from
        max_chars: Maximum characters to return (default 4000)

    Returns:
        Extracted text content from the page

    Raises:
        Exception if fetch or extraction fails
    """
    try:
        import trafilatura
    except ImportError:
        raise Exception("trafilatura not installed. Run: pip install trafilatura")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
        )
        response.raise_for_status()
        html = response.text

    # Extract main content using trafilatura
    content = trafilatura.extract(
        html,
        include_links=False,
        include_images=False,
        include_tables=True,
        favor_recall=True,
    )

    if not content:
        return "Could not extract content from page."

    # Truncate if needed
    if len(content) > max_chars:
        content = content[:max_chars] + "...[truncated]"

    return content
