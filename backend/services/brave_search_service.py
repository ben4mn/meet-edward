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


FETCH_MAX_CHARS = 30_000
FETCH_TIMEOUT_S = 15.0
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


def _client(**kw) -> httpx.AsyncClient:
    """The fetch's HTTP client (tests swap in a MockTransport)."""
    return httpx.AsyncClient(**kw)


async def _guard_request(request: httpx.Request) -> None:
    """Every hop, redirects included: never this Mac or the LAN (SSRF)."""
    from services.headless_browser import url_refusal

    why = await url_refusal(str(request.url))
    if why:
        raise PermissionError(f"not fetching {request.url.host}: {why}")


async def fetch_page(url: str, max_chars: int = FETCH_MAX_CHARS) -> str:
    """A web page's main text (trafilatura), at most ``max_chars``.

    Raises on every failure — a refused or unreachable URL, an HTTP error,
    a page with no readable text — with a message that says what to try
    instead. No Brave key is needed (2026-10-11: ``fetch_page_content``
    used to refuse without one). ``Accept-Encoding`` names only gzip and
    deflate: with brotli/zstd decoders installed httpx advertised them and
    a broken stream failed in ``decompressobj``.
    """
    from services.headless_browser import check_url

    checked = check_url(url)
    try:
        import trafilatura
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("trafilatura is not installed") from exc

    async with _client(timeout=FETCH_TIMEOUT_S, follow_redirects=True, max_redirects=8,
                       event_hooks={"request": [_guard_request]}) as client:
        response = await client.get(checked, headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        })
        if response.status_code >= 400:
            raise RuntimeError(f"{response.url} answered HTTP {response.status_code}")
        ctype = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype and not (ctype.startswith("text/") or "html" in ctype or "xml" in ctype or "json" in ctype):
            raise RuntimeError(f"{response.url} is {ctype}, not a page — look(url) shows images and PDFs")
        body = response.text

    if ctype in ("text/plain", "application/json") or ctype.endswith("+json"):
        content = body.strip()
    else:
        content = trafilatura.extract(body, include_links=False, include_images=False,
                                      include_tables=True, favor_recall=True) or ""
    if not content.strip():
        raise RuntimeError("no readable text on that page (it may build itself in JavaScript) — "
                           "browser(open, url) reads it like a person would")
    if len(content) > max_chars:
        content = content[:max_chars] + "...[truncated]"
    return content


async def fetch_page_content(url: str, max_chars: int = 4000) -> str:
    """The legacy tool's fetch (``EDWARD_TOOLSET=legacy``): ``fetch_page``."""
    return await fetch_page(url, max_chars=max_chars)
