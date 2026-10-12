"""``web_search`` and ``web_fetch`` (2026-10-11; docs/architecture/computer.md).

Under the Claude provider these names are Anthropic's **server tools**: the
search and the fetch run at Anthropic and their results come back inside
the model's own message (``server_tool_use`` / ``*_tool_result`` blocks),
never through this process. ``ChatClaude`` asks ``server_tool_spec`` which
version a model gets, through the tool's
``metadata["anthropic_tool_resolver"]``:

* Opus and Sonnet (5.5, 5, 4.8/4.7/4.6): the basic ``web_search_20250305``
  and ``web_fetch_20250910``. Not the dynamic-filtering ``_20260209`` pair
  (2026-10-12): with Edward's other tools bound, Sonnet 5.5 calls those
  directly with ``{"params": {"query": …}}`` and Anthropic answers
  ``invalid_tool_input`` — on one real request 0 of 9 calls were
  well-formed, with the basic versions 6 of 6. Every web search and fetch
  Edward made under Claude had failed that way.
* The small 5.5 model (background, the pulse, voice) and anything older: the
  basic search, and fetch runs locally (a version a model rejects would 400
  every turn).
* ``EDWARD_SERVER_WEB_TOOLS=off`` (read per bind): both run locally.

Locally (that flag, the small model's fetch, the Codex provider) ``web_search`` is
Brave and ``web_fetch`` is ``brave_search_service.fetch_page`` (httpx +
trafilatura, 30K chars, no Brave key needed, every hop SSRF-guarded).
Failures raise ``ToolFailure`` with the real reason.

Cost: server searches are $10 per 1,000 (the spend ledger prices
``usage.server_tool_use``); at ~5 a day that is about $0.05 a day.

The search's approximate location is the owner's timezone (``TIMEZONE`` /
the settings) and ``EDWARD_SEARCH_COUNTRY`` (a two-letter country code,
default ``US``).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from langchain_core.tools import tool

from services.toolset._compat import ToolFailure

SERVER_WEB_FLAG = "EDWARD_SERVER_WEB_TOOLS"
SEARCH_COUNTRY_ENV = "EDWARD_SEARCH_COUNTRY"
DEFAULT_SEARCH_COUNTRY = "US"
WEB_TIMEOUT_S = 45
SEARCH_MAX_USES = 8
FETCH_MAX_USES = 10
FETCH_MAX_CONTENT_TOKENS = 20_000
RESOLVER = "services.toolset.web:server_tool_spec"

# Models with the dynamic-filtering web tools (the claude-api reference,
# 2026-10: "Opus 5.5/5/4.8/4.7/4.6, Sonnet 5.5, Sonnet 5, Sonnet 4.6";
# Fable/Mythos share Opus's tool surface). They get the server fetch (in its
# basic version, see above); everything else gets a local fetch.
DYNAMIC_FILTERING_MODELS = frozenset({
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-fable-5-1", "claude-fable-5", "claude-mythos-5-1", "claude-mythos-5",
})
DYNAMIC_SEARCH = "web_search_20260209"
DYNAMIC_FETCH = "web_fetch_20260209"
BASIC_SEARCH = "web_search_20250305"
BASIC_FETCH = "web_fetch_20250910"


def server_web_tools_on() -> bool:
    return (os.getenv(SERVER_WEB_FLAG) or "on").strip().lower() not in ("off", "0", "false", "no", "local")


def has_dynamic_filtering(model: Optional[str]) -> bool:
    """None means the default chat model (Opus)."""
    if not model:
        return True
    try:
        from services.claude_routing import model_family

        family = model_family(model)
    except Exception:  # noqa: BLE001
        family = str(model).strip().lower()
    return family in DYNAMIC_FILTERING_MODELS


def _timezone() -> str:
    fallback = (os.getenv("TIMEZONE") or "").strip() or "UTC"
    try:
        from services.timezone_utils import current_tz_name

        return current_tz_name() or fallback
    except Exception:  # noqa: BLE001
        return fallback


def _country() -> str:
    """The search's country: ``EDWARD_SEARCH_COUNTRY`` (ISO 3166-1 alpha-2), else US."""
    raw = (os.getenv(SEARCH_COUNTRY_ENV) or "").strip().upper()
    return raw if len(raw) == 2 and raw.isalpha() else DEFAULT_SEARCH_COUNTRY


def server_tool_spec(name: str, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The Anthropic tool dict ``name`` binds as for ``model``, or None to
    bind the local tool (its own schema)."""
    if not server_web_tools_on():
        return None
    if name == "web_search":
        return {"type": BASIC_SEARCH, "name": "web_search", "max_uses": SEARCH_MAX_USES,
                "user_location": {"type": "approximate", "timezone": _timezone(), "country": _country()}}
    if name == "web_fetch":
        if not has_dynamic_filtering(model):
            return None
        return {"type": BASIC_FETCH, "name": "web_fetch", "max_uses": FETCH_MAX_USES,
                "max_content_tokens": FETCH_MAX_CONTENT_TOKENS}
    return None


def map_server_tools_for_model(tools: list, model: Optional[str]) -> list:
    """Raw server-tool dicts made right for ``model`` at request time: the
    dynamic versions become the basic ones on every model (their direct
    calls come back ``invalid_tool_input``), and fetch is dropped on a model
    without the server fetch. Pure; other tools pass through."""
    fetch = has_dynamic_filtering(model)
    out = []
    for t in tools:
        kind = t.get("type") if isinstance(t, dict) else None
        if kind == DYNAMIC_SEARCH:
            out.append({**t, "type": BASIC_SEARCH})
        elif kind == DYNAMIC_FETCH:
            if fetch:
                out.append({**t, "type": BASIC_FETCH})
        else:
            out.append(t)
    return out


# ---- the local tools (and their schemas under Codex / the flag) ---------------------------

@tool
async def web_search(query: str, count: int = 5) -> str:
    """Search the web for current information: news, prices, hours, dates,
    facts you don't know or that may have changed. Returns titles, URLs and
    snippets; web_fetch (or your browser) reads a result in full."""
    from services import brave_search_service as brave

    q = str(query or "").strip()
    if not q:
        raise ToolFailure("web_search needs a query")
    if not brave.is_configured():
        raise ToolFailure("web search is not configured on this Mac (no BRAVE_SEARCH_API_KEY) — "
                          "try your browser on a search site")
    try:
        results = await brave.search(q, max(1, min(int(count or 5), 20)))
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"the search failed: {type(exc).__name__}: {exc}") from exc
    if not results:
        return f"No results for {q!r}."
    lines = [f"Results for {q!r}:", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title') or '(untitled)'}")
        lines.append(f"   {r.get('url') or ''}")
        if r.get("description"):
            lines.append(f"   {r['description']}")
    return "\n".join(lines)


@tool
async def web_fetch(url: str) -> str:
    """Read a web page's main text (articles, docs, listings). For a page
    that builds itself in JavaScript, needs clicks or a form, use your
    browser; for an image or PDF, look."""
    from services import brave_search_service as brave
    from services.headless_browser import BrowserRefused, check_url, url_refusal

    try:
        checked = check_url(url)
    except BrowserRefused as exc:
        raise ToolFailure(f"not fetching that: {exc}") from exc
    why = await url_refusal(checked)
    if why:
        raise ToolFailure(f"not fetching that: {why}")
    try:
        text = await brave.fetch_page(checked)
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"couldn't read {checked}: {exc}") from exc
    return f"{checked}\n\n{text}"


for _t in (web_search, web_fetch):
    _t.metadata = {
        "anthropic_tool": server_tool_spec(_t.name) or {"type": BASIC_SEARCH if _t.name == "web_search" else BASIC_FETCH,
                                                        "name": _t.name},
        "anthropic_tool_resolver": RESOLVER,
        "timeout_s": WEB_TIMEOUT_S,
        "parallel_safe": True,
    }

WEB_TOOLS = [web_search, web_fetch]
