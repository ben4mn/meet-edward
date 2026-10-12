"""Edward's own invisible browser (AREA 2, 2026-10-11).

Why: a ping asking "find me tickets on StubHub" failed because the agent
answering had no browser at all, and ``web_fetch`` cannot read pages that
build themselves in JavaScript. The owner's Chrome (``browser_hands``) is
in front of the owner with their logins; this one is Edward's, headless,
with its own profile in ``~/edward-home/browser/profile`` and none of the
owner's sessions.

The rules that make it safe to hand to every turn:

* **Playwright's bundled headless shell, never Google Chrome.** The binary is
  found under ``~/Library/Caches/ms-playwright`` and asserted to be there
  before launch; no ``channel`` is ever named. A second Google Chrome
  instance hijacks ``open -a`` and ``tell application "Google Chrome"``
  (docs/architecture/computer.md), so the single-Chrome rule lives here as well as in the
  extension link.
* **http and https only.** ``file:``, ``chrome:``, ``view-source:``,
  ``devtools:``, ``blob:``, ``data:`` and ``javascript:`` are refused before
  anything opens. This process runs with Full Disk Access; a ``file://``
  page would read what nothing else may.
* **Never this Mac's own services (SSRF).** Every request the page makes
  passes ``_route_guard``: the host is resolved and private, loopback,
  link-local, CGNAT, multicast and reserved addresses are aborted.
  localhost serves Postgres, the backend and the extension socket.
  Playwright does not route the hops of a redirect, so after a navigation
  the final URL is checked again and a page that landed inside is never
  read (``open_url``).
* **Bounded.** One working page per conversation, at most ``MAX_PAGES``;
  the whole browser closes after ``IDLE_CLOSE_S`` idle; ``shutdown()`` from
  the lifespan. Tests never launch it (conftest) unless
  ``EDWARD_TEST_BROWSER=1``.

The page actions (read, find, click, type, key, scroll, wait, back) are
``browser_hands``' own, run against a ``HeadlessTarget`` — the same CDP
calls the owner's Chrome gets through the extension. This module adds what only a
headless page has: open (``page.goto``), download, screenshot, and
``read_url`` for the media package's link enrichment.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

NAV_TIMEOUT_S = 20.0
ACTION_TIMEOUT_S = 45.0
CDP_TIMEOUT_S = 15.0
IDLE_CLOSE_S = 600.0
MAX_PAGES = 3
DNS_CACHE_S = 60.0
VIEWPORT = {"width": 1280, "height": 900}
SCREENSHOT_QUALITY = 70
ALLOWED_SCHEMES = ("http", "https")
# Names that are this Mac or its LAN without asking DNS.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BrowserRefused(ValueError):
    """The URL is not one Edward's browser opens (scheme or address)."""


class HeadlessUnavailable(RuntimeError):
    """The headless shell is missing, refused, or failed to start."""


# ---- pure: URLs and addresses ---------------------------------------------------------

def check_url(url: str) -> str:
    """The URL to open, or ``BrowserRefused``. A bare host gets https."""
    raw = str(url or "").strip()
    if not raw:
        raise BrowserRefused("no URL given")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw) or re.match(r"^[^:/]+:\d+(/|$)", raw):
        raw = "https://" + raw
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BrowserRefused(f"{scheme or 'that'}: URLs don't open in your browser — only http and https")
    if not parts.hostname:
        raise BrowserRefused(f"{raw!r} has no host")
    return raw


def blocked_address(ip: str) -> Optional[str]:
    """Why an address is off limits (private, loopback, …), or None."""
    try:
        addr = ipaddress.ip_address(str(ip).strip("[]").split("%", 1)[0])
    except ValueError:
        return f"{ip} is not an address"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return f"{addr} is this Mac (loopback)"
    if addr.is_link_local:
        return f"{addr} is link-local"
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
        return f"{addr} is a carrier-grade NAT address"
    if addr.is_multicast:
        return f"{addr} is multicast"
    if addr.is_unspecified:
        return f"{addr} is unspecified"
    if addr.is_private:
        return f"{addr} is a private address"
    if addr.is_reserved:
        return f"{addr} is reserved"
    return None


_dns_cache: Dict[str, Tuple[float, List[str]]] = {}


async def _resolve(host: str) -> List[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def host_refusal(host: str) -> Optional[str]:
    """Why a host is off limits, or None. Literals and local names are
    judged without DNS; anything else must resolve, and every address it
    resolves to must be public (a DNS-rebinding name with one inside
    address is refused)."""
    h = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not h:
        return "no host"
    if h == "localhost" or h.endswith(_LOCAL_SUFFIXES):
        return f"{h} is this Mac or the local network"
    try:
        ipaddress.ip_address(h.split("%", 1)[0])
        return blocked_address(h)
    except ValueError:
        pass
    cached = _dns_cache.get(h)
    if cached and time.monotonic() - cached[0] < DNS_CACHE_S:
        addrs = cached[1]
    else:
        try:
            addrs = await _resolve(h)
        except (OSError, UnicodeError) as exc:
            return f"{h} does not resolve ({exc})"
        _dns_cache[h] = (time.monotonic(), addrs)
    if not addrs:
        return f"{h} does not resolve"
    for a in addrs:
        why = blocked_address(a)
        if why:
            return f"{h} resolves inside ({why})"
    return None


async def url_refusal(url: str) -> Optional[str]:
    """Why a URL is off limits for the browser, or None."""
    try:
        checked = check_url(url)
    except BrowserRefused as exc:
        return str(exc)
    return await host_refusal(urlsplit(checked).hostname or "")


async def _route_guard(route: Any, request: Any = None) -> None:
    """Playwright's handler for every request the context makes."""
    url = str(route.request.url)
    scheme = url.split(":", 1)[0].lower()
    if scheme in ("data", "blob", "about"):
        # In-page content (inline images, the blank page) never leaves the browser.
        await route.continue_()
        return
    why = await url_refusal(url)
    if why:
        print(f"[browser] headless request refused: {why}")
        await route.abort("blockedbyclient")
        return
    await route.continue_()


# ---- pure: the executable --------------------------------------------------------------

def playwright_cache_dir() -> Path:
    raw = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if raw and raw not in ("0",):
        return Path(raw).expanduser()
    return Path.home() / "Library" / "Caches" / "ms-playwright"


def find_headless_shell(cache_dir: Optional[Path] = None) -> Path:
    """The newest ``chrome-headless-shell`` Playwright installed."""
    cache = Path(cache_dir) if cache_dir is not None else playwright_cache_dir()
    found = sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
    if not found:
        raise HeadlessUnavailable(
            f"the headless browser is not installed (no chrome-headless-shell under {cache}); "
            "`playwright install chromium-headless-shell` installs it")
    return found[-1]


def assert_playwright_executable(path: str, *, cache_dir: Optional[Path] = None) -> Path:
    """Refuse anything that is not Playwright's own binary — above all
    Google Chrome (one Chrome on this Mac, the owner's)."""
    cache = (Path(cache_dir) if cache_dir is not None else playwright_cache_dir()).resolve()
    real = Path(os.path.realpath(path))
    if "Google Chrome" in str(path) or "Google Chrome" in str(real) or str(real).startswith("/Applications/"):
        raise HeadlessUnavailable(f"refusing {path}: Edward's browser is never Google Chrome")
    try:
        real.relative_to(cache)
    except ValueError:
        raise HeadlessUnavailable(f"refusing {path}: not Playwright's bundled browser (under {cache})") from None
    return real


# ---- pure: downloads -------------------------------------------------------------------

def safe_download_name(name: str) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/")).strip().lstrip(".")
    base = re.sub(r"[\x00-\x1f/:]", "_", base)[:180]
    return base or "download"


def unique_path(directory: Path, name: str) -> Path:
    dest = Path(directory) / name
    if not dest.exists():
        return dest
    stem, suffix = os.path.splitext(name)
    for i in range(1, 1000):
        cand = Path(directory) / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    return Path(directory) / f"{stem}-{int(time.time())}{suffix}"


def edward_home() -> Path:
    """Edward's home (``services.computer`` owns it once merged)."""
    try:
        from services.computer.home import edward_home as _home  # type: ignore

        return Path(_home())
    except Exception:  # noqa: BLE001 — AREA 2 agent A's module may not be merged yet
        return Path(os.getenv("EDWARD_HOME") or (Path.home() / "edward-home")).expanduser()


def downloads_dir() -> Path:
    return edward_home() / "downloads"


def profile_dir() -> Path:
    return edward_home() / "browser" / "profile"


# ---- the browser ------------------------------------------------------------------------

class HeadlessTarget:
    """A Playwright page as a ``browser_hands.CdpTarget``: the same CDP
    calls the extension relays for the owner's Chrome."""

    tab_id = "browser"

    def __init__(self, page: Any, session: Any):
        self.page = page
        self.session = session

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, *,
                   timeout: float = CDP_TIMEOUT_S) -> Dict[str, Any]:
        res = await asyncio.wait_for(self.session.send(method, params or {}), timeout)
        return res if isinstance(res, dict) else {}


_pw: Any = None
_context: Any = None
_pages: "OrderedDict[str, Tuple[Any, HeadlessTarget]]" = OrderedDict()
_last_used = 0.0
_reaper: Optional["asyncio.Task[Any]"] = None
_lock: Optional[asyncio.Lock] = None
_lock_loop: Any = None


def _the_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


def running() -> bool:
    return _context is not None


def status() -> Dict[str, Any]:
    return {"running": running(), "pages": len(_pages),
            "idle_s": round(time.monotonic() - _last_used) if _last_used else None}


async def _launch() -> Any:
    """Start Playwright and the persistent context (headless shell only)."""
    global _pw, _context
    from playwright.async_api import async_playwright

    exe = assert_playwright_executable(str(find_headless_shell()))
    profile = profile_dir()
    downloads = downloads_dir()
    profile.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=True, executable_path=str(exe),
            accept_downloads=True, downloads_path=str(downloads), viewport=dict(VIEWPORT),
        )
        await ctx.route("**/*", _route_guard)
    except Exception:
        await pw.stop()
        raise
    _pw, _context = pw, ctx
    print(f"[browser] headless browser up ({exe.parent.parent.name})")
    return ctx


async def _ensure_context() -> Any:
    global _reaper
    if _context is not None:
        return _context
    async with _the_lock():
        if _context is None:
            try:
                await _launch()
            except HeadlessUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                raise HeadlessUnavailable(f"the headless browser did not start: {exc}") from exc
        if _reaper is None or _reaper.done():
            _reaper = asyncio.get_running_loop().create_task(_reap_when_idle())
    return _context


async def _reap_when_idle() -> None:
    while _context is not None:
        await asyncio.sleep(30)
        if _last_used and time.monotonic() - _last_used > IDLE_CLOSE_S:
            print("[browser] headless browser idle; closing it")
            await shutdown()
            return


def _touch() -> None:
    global _last_used
    _last_used = time.monotonic()


async def page_for(conversation_id: Optional[str]) -> Tuple[Any, HeadlessTarget]:
    """The conversation's working page (made on first use), LRU-capped."""
    key = str(conversation_id or "default")
    ctx = await _ensure_context()
    _touch()
    held = _pages.get(key)
    if held is not None and not held[0].is_closed():
        _pages.move_to_end(key)
        return held
    _pages.pop(key, None)
    while len(_pages) >= MAX_PAGES:
        _, (old_page, _t) = _pages.popitem(last=False)
        try:
            await old_page.close()
        except Exception:  # noqa: BLE001
            pass
    spare = [p for p in ctx.pages if not p.is_closed() and all(p is not v[0] for v in _pages.values())]
    page = spare[0] if spare else await ctx.new_page()
    session = await ctx.new_cdp_session(page)
    target = HeadlessTarget(page, session)
    _pages[key] = (page, target)

    def _adopt(popup: Any) -> None:
        # A link that opens a new window: the conversation follows it.
        async def _swap() -> None:
            try:
                s = await ctx.new_cdp_session(popup)
                _pages[key] = (popup, HeadlessTarget(popup, s))
            except Exception:  # noqa: BLE001
                pass
        asyncio.get_running_loop().create_task(_swap())

    page.on("popup", _adopt)
    return page, target


def has_page(conversation_id: Optional[str]) -> bool:
    held = _pages.get(str(conversation_id or "default"))
    return held is not None and not held[0].is_closed()


async def target_for(conversation_id: Optional[str]) -> HeadlessTarget:
    return (await page_for(conversation_id))[1]


async def _landed_inside(page: Any) -> Optional[str]:
    """Where a navigation ended, checked again (redirect hops are not routed)."""
    return await url_refusal(page.url) if page.url not in ("about:blank", "") else None


async def open_url(conversation_id: Optional[str], url: str) -> Dict[str, Any]:
    """Open ``url`` in the conversation's page and wait for it to settle."""
    from services import browser_hands as bh

    checked = check_url(url)
    why = await url_refusal(checked)
    if why:
        raise BrowserRefused(why)
    page, target = await page_for(conversation_id)
    note = None
    try:
        await page.goto(checked, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_S * 1000)
    except Exception as exc:  # noqa: BLE001 — a slow page still shows what it has
        if "Timeout" not in type(exc).__name__ and "timeout" not in str(exc).lower():
            raise
        note = f"the page was still loading after {NAV_TIMEOUT_S:g}s"
    inside = await _landed_inside(page)
    if inside:
        await page.goto("about:blank")
        raise BrowserRefused(f"that page redirected inside ({inside}); not reading it")
    info = await bh._settle(target)
    if note and not info.get("note"):
        info["note"] = note
    _touch()
    return info


async def back(conversation_id: Optional[str]) -> Dict[str, Any]:
    from services import browser_hands as bh

    page, target = await page_for(conversation_id)
    await page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_S * 1000)
    inside = await _landed_inside(page)
    if inside:
        await page.goto("about:blank")
        raise BrowserRefused(f"going back landed inside ({inside}); not reading it")
    return await bh._settle(target)


async def guard_current(conversation_id: Optional[str]) -> None:
    """Refuse to act on a page that ended up inside (a click that redirected)."""
    page, _ = await page_for(conversation_id)
    inside = await _landed_inside(page)
    if inside:
        await page.goto("about:blank")
        raise BrowserRefused(f"the page went inside ({inside}); not reading it")


async def download(conversation_id: Optional[str], ref: int, *, timeout: float = ACTION_TIMEOUT_S) -> Dict[str, Any]:
    """Click element ``ref`` and keep what it downloads in ~/edward-home/downloads."""
    from services import browser_hands as bh

    page, target = await page_for(conversation_id)
    async with page.expect_download(timeout=timeout * 1000) as info:
        await bh._click_ref(target, int(ref))
    dl = await info.value
    dest_dir = downloads_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest_dir, safe_download_name(dl.suggested_filename))
    await dl.save_as(str(dest))
    failure = await dl.failure()
    if failure:
        raise RuntimeError(f"the download failed: {failure}")
    _touch()
    return {"path": str(dest), "name": dest.name, "bytes": dest.stat().st_size if dest.exists() else 0,
            "url": dl.url}


async def screenshot(conversation_id: Optional[str]) -> bytes:
    """JPEG of the conversation's page (for ``look("browser")``)."""
    if conversation_id is not None and str(conversation_id) not in _pages:
        raise RuntimeError("your browser has no page open — browser(open, url) first")
    page, _ = await page_for(conversation_id)
    _touch()
    return await page.screenshot(type="jpeg", quality=SCREENSHOT_QUALITY)


async def close_page(conversation_id: Optional[str]) -> bool:
    held = _pages.pop(str(conversation_id or "default"), None)
    if held is None:
        return False
    try:
        await held[0].close()
    except Exception:  # noqa: BLE001
        pass
    return True


@dataclass
class PageRead:
    final_url: str
    title: str
    text: str
    screenshot_jpeg: Optional[bytes] = None
    error: Optional[str] = None


async def read_url(url: str, *, screenshot: bool = False, timeout: float = NAV_TIMEOUT_S) -> PageRead:
    """One page read in a throwaway tab, for the media package's link enrichment.
    Never raises: a refusal or a failure is ``error``."""
    from services import browser_hands as bh

    try:
        checked = check_url(url)
        why = await url_refusal(checked)
        if why:
            return PageRead(final_url=checked, title="", text="", error=why)
        ctx = await _ensure_context()
        _touch()
        page = await ctx.new_page()
        try:
            await page.goto(checked, wait_until="domcontentloaded", timeout=timeout * 1000)
            inside = await _landed_inside(page)
            if inside:
                return PageRead(final_url=page.url, title="", text="", error=f"redirected inside ({inside})")
            session = await ctx.new_cdp_session(page)
            target = HeadlessTarget(page, session)
            await bh._settle(target, timeout=min(8.0, timeout))
            payload = await bh._elements(target)
            shot = await page.screenshot(type="jpeg", quality=SCREENSHOT_QUALITY) if screenshot else None
            return PageRead(final_url=page.url, title=str(payload.get("title") or await page.title()),
                            text=str(payload.get("text") or ""), screenshot_jpeg=shot)
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        return PageRead(final_url=str(url), title="", text="", error=f"{type(exc).__name__}: {exc}")


async def shutdown() -> None:
    """Close every page, the context and Playwright (lifespan, idle reaper)."""
    global _pw, _context, _reaper
    ctx, pw = _context, _pw
    _context, _pw = None, None
    _pages.clear()
    task, _reaper = _reaper, None
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
    if ctx is not None:
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
    if pw is not None:
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass
