"""Links in a ping, read before the turn (2026-10-11; docs/architecture/media.md).

``read_links(text, message_rowid=…, sender_known=…)`` gives the ping turn
what the links in the message say, so "@edward which of these is cheapest?"
doesn't start with three fetches:

1. **iMessage's own preview first** (tier 1): ``message.payload_data`` holds
   the ``LPLinkMetadata`` the sender's phone fetched — title, summary, site —
   free, offline, and right even for pages that block bots or need a login
   (``linkmeta``). The preview picture is the message's
   ``.pluginPayloadAttachment``, which arrives with the attachments.
2. **Then the page itself**, only through ``netguard.guarded_get`` (public
   addresses only, checked on every redirect), at most 2 MB in 8 s, its main
   text extracted with trafilatura and capped at 4,000 characters.

Never fetched: a link whose path or query looks like signing in or
confirming something (``token``, ``code``, ``verify``, ``unsubscribe``,
``login``, ``rsvp``, …) — opening it could act as the owner — and any link
from a number the owner doesn't know. The turn can still open them deliberately.

The page text is someone else's words: the trigger fences it as
``<untrusted source="page …">``. Errors say only their class.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.owner import owner_text

MAX_LINKS = 3
FETCH_MAX_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 8.0
TEXT_MAX = 4_000

_URL_RE = re.compile(r"https?://[^\s<>\"' ]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}'\"…"
_NEVER_RE = re.compile(r"token|code|verify|confirm|unsubscribe|reset|magic|login|auth|approve|rsvp|otp",
                       re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# What the ping turn reads about a link it was not given (model-facing).
UNKNOWN_SENDER_NOTE = owner_text("I didn't open it — it came from a number {OWNER} doesn't know")
SIGN_IN_NOTE = owner_text("I didn't open it — it looks like a sign-in or confirmation link, "
                          "and opening it could act as {OWNER}")


def urls_in(text: Optional[str], limit: int = MAX_LINKS) -> List[str]:
    """http(s) URLs in ``text``, trailing punctuation off, first ``limit``, no repeats. Pure."""
    out: List[str] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0)
        while url and url[-1] in _TRAILING:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        if url and url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


def never_fetch(url: str) -> bool:
    """A sign-in, confirm or one-time link: opening it could act as the owner. Pure."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return bool(_NEVER_RE.search(f"{parts.path}?{parts.query}"))


def _payload_for(rowid: int) -> Optional[bytes]:
    """``message.payload_data`` of one chat.db row (query_only); None when absent."""
    try:
        from services.heartbeat.listener_imessage import CHAT_DB_PATH
    except Exception:  # noqa: BLE001
        return None
    if not Path(CHAT_DB_PATH).exists():
        return None
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("SELECT payload_data FROM message WHERE ROWID = ?", (int(rowid),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return bytes(row[0]) if row and row[0] else None


def _title(html: str) -> Optional[str]:
    m = _TITLE_RE.search(html or "")
    if not m:
        return None
    return " ".join(html_lib.unescape(m.group(1)).split())[:200] or None


def page_text(html: str) -> str:
    """The main text of a page (trafilatura when installed, else tag-stripped), capped. Pure."""
    text = ""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=False) or ""
    except Exception:  # noqa: BLE001
        text = ""
    if not text:
        stripped = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html or "")
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", stripped))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX] + "…"


async def _fetch(url: str) -> Dict[str, Any]:
    from services import netguard
    try:
        got = await netguard.guarded_get(url, max_bytes=FETCH_MAX_BYTES, timeout_s=FETCH_TIMEOUT_S)
    except netguard.BlockedURL as exc:
        return {"error": f"not a public web page I can open: {exc}"}
    except Exception as exc:  # noqa: BLE001 - only the class: a message can carry a token
        return {"error": f"couldn't load it ({type(exc).__name__})"}
    if got.status >= 400:
        return {"error": f"the site answered {got.status}"}
    ctype = (got.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and "html" not in ctype and not ctype.startswith("text/"):
        return {"note": f"it's a {ctype} file, not a page — look at it to see it", "final_url": got.final_url}
    html = got.content.decode("utf-8", "replace")
    text = await asyncio.to_thread(page_text, html) if "html" in ctype or "<html" in html[:500].lower() else \
        html[:TEXT_MAX]
    out: Dict[str, Any] = {"text": text, "final_url": got.final_url}
    title = _title(html)
    if title:
        out["title"] = title
    if got.truncated:
        out["note"] = "only the first 2 MB was read"
    return out


async def read_links(text: Optional[str], *, message_rowid: Optional[int] = None, sender_known: bool = True,
                     fetch: bool = True) -> List[Dict[str, Any]]:
    """What the links in a ping say: dicts with ``url`` and any of ``title``,
    ``site``, ``summary``, ``text``, ``note``, ``error`` (render's ``links``).
    [] when media is off. Never raises."""
    from services.media.hooks import enabled
    if not enabled():
        return []
    previews: Dict[str, Dict[str, Any]] = {}
    if message_rowid is not None:
        try:
            from services.media.linkmeta import parse_link_payload
            meta = parse_link_payload(await asyncio.to_thread(_payload_for, int(message_rowid)))
        except Exception:  # noqa: BLE001
            meta = None
        if meta:
            url = meta.get("url") or meta.get("original_url")
            previews[url] = {k: v for k, v in meta.items() if v}
    urls = urls_in(text)
    for url in previews:
        if url and url not in urls and len(urls) < MAX_LINKS:
            urls.append(url)
    out: List[Dict[str, Any]] = []
    for url in urls:
        item: Dict[str, Any] = {"url": url}
        preview = previews.get(url) or next((p for p in previews.values() if p.get("original_url") == url), None)
        if preview:
            item.update({k: v for k, v in preview.items() if k in ("title", "summary", "site")})
        if not fetch:
            pass
        elif not sender_known:
            item["note"] = UNKNOWN_SENDER_NOTE
        elif never_fetch(url):
            item["note"] = SIGN_IN_NOTE
        else:
            got = await _fetch(url)
            if preview and got.get("error"):
                got = {"note": f"the page itself didn't load ({got['error']}); this is its preview"}
            for key, value in got.items():
                if key == "title" and item.get("title"):
                    continue
                item[key] = value
        out.append(item)
    return out
