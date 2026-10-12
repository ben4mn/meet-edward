"""``browser`` and ``chrome``: Edward's two browsers as two tools (2026-10-11).

``browser`` is his own invisible one (``services/headless_browser``):
JavaScript pages, forms and downloads that ``web_fetch`` can't read, with
none of the owner's logins. ``chrome`` is the owner's own Chrome on this Mac
through the Edward extension (``services/browser_hands``): their sessions,
in front of them. Both run browser_hands' page actions — read, find, click,
type, key, scroll, wait — against a CDP target, so the two read and click
alike. ``chrome`` needs ``EDWARD_CHROME_HANDS=on`` (off by default); off,
every action is a ``Tool error`` that says how to turn it on.

Every failure raises ``ToolFailure`` with the real reason; the executor
makes it ``Tool error: …`` plus ``is_error``.

Chrome's rails (in this module, checked before the action runs):

* **Typing outside a search box acts as the owner** — a message typed into
  Gmail or Slack goes out as them. ``type`` (and Enter, and ``steps`` that
  type) outside ``taint_service.SEARCH_TYPING_HOSTS`` and Edward's own
  /screen runs only when the owner is the one asking in this turn.
* **Clicks that act as the owner** — an element labelled send, post, reply,
  delete, accept, unsubscribe, sign out, … — likewise need the owner
  asking. The label is read from the page at click time.
* **Payments** — "place order", "buy now", "pay", "checkout", "confirm
  payment", "add card", or typing a number that passes the Luhn check —
  are refused in every turn: the owner does those themselves.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

from langchain_core.tools import tool

from services.owner import owner_doc, owner_name, owner_text
from services.toolset import _compat
from services.toolset._compat import ToolFailure

BROWSER_TIMEOUT_S = 45
CHROME_TIMEOUT_S = 90

# ---- pure: the rails ---------------------------------------------------------------------

_PAYMENT_RE = re.compile(
    r"\b(place\s+(?:your\s+)?order|buy\s+now|buy\s+it\s+now|pay|pay\s+now|payment|checkout|check\s+out"
    r"|purchase|confirm\s+(?:order|payment|purchase)|submit\s+order|complete\s+(?:order|purchase)"
    r"|add\s+(?:a\s+)?(?:new\s+)?card)\b",
    re.IGNORECASE,
)
_ACTING_RE = re.compile(
    r"\b(send|post|reply|reply\s+all|publish|delete|remove|accept|decline|confirm|submit|unsubscribe"
    r"|sign\s*out|log\s*out|transfer|share|invite|rsvp|archive|forward|follow|unfollow|block)\b",
    re.IGNORECASE,
)
_CARD_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_ENTER_KEYS = ("enter", "return")


def is_payment_label(label: Optional[str]) -> bool:
    return bool(label) and bool(_PAYMENT_RE.search(str(label)))


def is_acting_label(label: Optional[str]) -> bool:
    return bool(label) and bool(_ACTING_RE.search(str(label)))


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def looks_like_card(text: Optional[str]) -> bool:
    """A 13–19 digit run (spaces or dashes allowed) that passes Luhn."""
    for m in _CARD_RUN.finditer(str(text or "")):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def _host(url: Optional[str]) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


def typing_is_searching(url: Optional[str]) -> bool:
    """Typing here can't speak for the owner: a search site or Edward's /screen."""
    from services.taint_service import SEARCH_TYPING_HOSTS

    try:
        from services.screen_window_service import is_screen_url
    except Exception:  # noqa: BLE001
        def is_screen_url(u):  # type: ignore[no-redef]
            return False
    return _host(url) in SEARCH_TYPING_HOSTS or is_screen_url(url)


def _typing_refusal(url: Optional[str], what: str = "typing") -> str:
    host = _host(url) or "that page"
    owner = owner_name()
    return (f"{what} into {host} in {owner}'s Chrome acts as {owner} — only when they ask in this turn. "
            "tell_owner what you'd type and where, or use your own browser")


def _acting_refusal(label: str) -> str:
    owner = owner_name()
    return (f"clicking “{label.strip()[:60]}” in {owner}'s Chrome acts as {owner} — only when they ask in this "
            "turn. tell_owner what you'd do")


def _payment_refusal(label: str, url: Optional[str]) -> str:
    where = f" at {url}" if url else ""
    return (f"“{label.strip()[:60]}” is a payment step — {owner_name()} does those themselves; "
            f"tell them it's ready{where}")


_CARD_REFUSAL = owner_text("that looks like a card number — {OWNER} types those themselves; "
                           "tell them where it's needed")


# ---- seams (patched in tests) ---------------------------------------------------------------

_JS_LABEL = ("(() => { const el = document.querySelector('[data-edward-ref=\"%d\"]'); if (!el) return '';"
             " return String(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title')"
             " || el.value || el.getAttribute('placeholder') || '').trim().replace(/\\s+/g, ' ').slice(0, 120); })()")


async def _ref_label(ref: int, *, target: Any = None) -> str:
    """What element ``ref`` says on the page right now (the rails read it)."""
    from services import browser_hands as bh

    try:
        value = await bh._evaluate(bh._target(target), _JS_LABEL % int(ref))
    except Exception:  # noqa: BLE001 — no label is judged as no label
        return ""
    return str(value or "")


async def _chrome_url() -> str:
    from services import browser_hands as bh

    try:
        return str((await bh._info(bh._target())).get("url") or "")
    except Exception:  # noqa: BLE001
        return ""


async def chrome_refusal(action: str, *, ref: Optional[int] = None, text: Optional[str] = None,
                         key: Optional[str] = None, steps: Any = None) -> Optional[str]:
    """Why this Chrome action may not run in this turn, or None."""
    owner = _compat.owner_asked()
    a = str(action or "").lower()
    if a == "steps":
        last_url: Optional[str] = None
        unknown = False  # a click, Enter or back may have navigated: the page is unknown until the next open
        for raw in (steps if isinstance(steps, list) else []):
            if not isinstance(raw, dict):
                continue
            sa = str(raw.get("action") or "").lower()
            if sa == "look":
                # browser_hands' look step activates the owner's tab and asks a second
                # model for words; the look tool returns the picture untouched.
                return "steps can't look — run the steps, then look('chrome') to see the tab"
            if sa == "open":
                last_url, unknown = str(raw.get("url") or ""), False
                continue
            why = await _single_refusal(sa, owner, ref=raw.get("ref"), text=raw.get("text"), key=raw.get("key"),
                                        url=last_url, url_unknown=unknown)
            if why:
                return why
            if sa in _NAVIGATING_STEPS:
                unknown = True
        return None
    return await _single_refusal(a, owner, ref=ref, text=text, key=key)


# Steps after which the tab may be on another page (review 2026-10-11: typing
# after a click was judged against the search page the steps had opened).
_NAVIGATING_STEPS = frozenset({"click", "key", "back"})


def _unknown_page_refusal(what: str) -> str:
    return (f"can't tell which page that {what} would land on — a click, Enter or back earlier in these steps "
            "may have moved the tab. Run those steps first, then do this in a call of its own")


async def _single_refusal(a: str, owner: bool, *, ref: Any = None, text: Any = None, key: Any = None,
                          url: Optional[str] = None, url_unknown: bool = False) -> Optional[str]:
    if a == "type" and text is not None and looks_like_card(str(text)):
        return _CARD_REFUSAL
    if a in ("click", "type") and ref is not None:
        try:
            label = await _ref_label(int(ref))
        except (TypeError, ValueError):
            label = ""
        if is_payment_label(label):
            return _payment_refusal(label, url or await _chrome_url())
        if a == "click" and not owner and is_acting_label(label):
            return _acting_refusal(label)
    if owner:
        return None
    if a == "type":
        if url_unknown:
            return _unknown_page_refusal("typing")
        here = url if url is not None else await _chrome_url()
        if not typing_is_searching(here):
            return _typing_refusal(here)
    if a == "key" and str(key or "").strip().lower() in _ENTER_KEYS:
        if url_unknown:
            return _unknown_page_refusal("pressing Enter")
        here = url if url is not None else await _chrome_url()
        if not typing_is_searching(here):
            return _typing_refusal(here, "pressing Enter")
    return None


# ---- helpers -------------------------------------------------------------------------------

def _need(action: str, **values: Any) -> None:
    missing = [k for k, v in values.items() if v is None or (isinstance(v, str) and not v.strip())]
    if missing:
        raise ToolFailure(f"{action} needs {' and '.join(missing)}")


def _where(info: Dict[str, Any]) -> str:
    out = f"Now at {info.get('url') or '?'}" + (f" — \"{info['title']}\"" if info.get("title") else "") + "."
    if info.get("note"):
        out += f" ({info['note']})"
    return out


def _kb(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB" if n < 1024 * 1024 else f"{n / (1024 * 1024):.1f} MB"


_EXT_DOWN = owner_text(
    "your hands in Chrome are down — the Edward extension is not connected on this Mac. "
    "Say so in the thread you are answering and what you can still do (your own browser and web_fetch "
    "still work). screen(bring_up) relaunches Chrome when it is down; if Chrome is up and the extension "
    "still does not connect, {OWNER} reloads it at chrome://extensions.")
_NO_TAB = owner_text("you are not holding a tab in {OWNER}'s Chrome — chrome(open, url) opens one, "
                     "or chrome(use, tab_id) takes one from chrome(tabs)")


def _hands_off() -> str:
    from services.browser_ext_service import HANDS_OFF

    return HANDS_OFF


def _chrome_failure(exc: BaseException, action: str) -> ToolFailure:
    from services import browser_hands as bh

    if isinstance(exc, ToolFailure):
        return exc
    _compat.record_failure("browser", action, exc)
    if isinstance(exc, bh.ChromeHandsOff):
        return ToolFailure(_hands_off())
    if isinstance(exc, bh.ExtensionUnavailable):
        return ToolFailure(_EXT_DOWN)
    if isinstance(exc, bh.NoWorkingTab):
        return ToolFailure(_NO_TAB)
    return ToolFailure(f"chrome {action} failed: {type(exc).__name__}: {exc}")


def _browser_failure(exc: BaseException, action: str) -> ToolFailure:
    from services import headless_browser as hb

    if isinstance(exc, ToolFailure):
        return exc
    _compat.record_failure("browser", f"headless:{action}", exc)
    if isinstance(exc, hb.BrowserRefused):
        return ToolFailure(f"not opening that: {exc}")
    if isinstance(exc, hb.HeadlessUnavailable):
        return ToolFailure(f"your browser isn't available: {exc}")
    return ToolFailure(f"browser {action} failed: {type(exc).__name__}: {exc}")


# ---- browser (his own) ---------------------------------------------------------------------

BrowserAction = Literal["open", "read", "find", "click", "type", "key", "scroll", "back", "wait", "download", "close"]


@tool
@owner_doc
async def browser(
    action: BrowserAction,
    url: Optional[str] = None,
    ref: Optional[int] = None,
    text: Optional[str] = None,
    submit: bool = True,
    key: Optional[str] = None,
    direction: Optional[str] = None,
    amount: Optional[int] = None,
    seconds: Optional[float] = None,
    query: Optional[str] = None,
) -> str:
    """Your own invisible browser. Use it for pages web_fetch can't read
    (JavaScript, blocked fetches, forms, downloads). None of {OWNER}'s logins.

    open(url) loads a page and reads it; read shows the text and numbered
    links, buttons and fields; find(query) lists matching elements;
    click(ref) / type(text, ref?, submit=true) / key(key) act on those
    numbers (they reset when the page changes — read again); scroll(direction,
    amount); back; wait(text?, seconds?); download(ref) clicks and saves to
    ~/edward-home/downloads; close ends your page. look("browser") shows it
    as a picture. Only http(s); never this Mac's own services."""
    from services import browser_hands as bh
    from services import headless_browser as hb

    conv = _compat.conversation_id()
    a = str(action or "").strip().lower()
    try:
        if a == "open":
            _need("browser(open)", url=url)
            info = await hb.open_url(conv, str(url))
            target = await hb.target_for(conv)
            try:
                page = await bh.read_page(target=target)
            except Exception as exc:  # noqa: BLE001
                page = f"(could not read the page yet: {exc})"
            return f"Opened in your browser. {_where(info)}\n\n{page}"
        if a == "close":
            return "Closed your browser page." if await hb.close_page(conv) else "Your browser had no page open."
        if not hb.has_page(conv):
            raise ToolFailure("your browser has no page open — browser(open, url) first")
        if a == "back":
            return f"Went back. {_where(await hb.back(conv))}"
        target = await hb.target_for(conv)
        if a == "read":
            return await bh.read_page(target=target)
        if a == "find":
            _need("browser(find)", query=query)
            return await bh.find_on_page(str(query), target=target)
        if a == "click":
            _need("browser(click)", ref=ref)
            info = await bh.click(int(ref), target=target)
            await hb.guard_current(conv)
            return f"Clicked [{ref}]. {_where(info)}"
        if a == "type":
            _need("browser(type)", text=text)
            info = await bh.type_text(str(text), ref=ref, submit=bool(submit), target=target)
            await hb.guard_current(conv)
            return f"Typed {str(text)!r}{' and pressed Enter' if submit else ''}. {_where(info)}"
        if a == "key":
            _need("browser(key)", key=key)
            info = await bh.press_key(str(key), target=target)
            await hb.guard_current(conv)
            return f"Pressed {key}. {_where(info)}"
        if a == "scroll":
            d, n = str(direction or "down"), int(amount or 1)
            info = await bh.scroll(d, n, target=target)
            return f"Scrolled {d} ×{n}. {_where(info)}"
        if a == "wait":
            secs = float(seconds or 10)
            info = await bh.wait_for(text=text, timeout=secs, target=target)
            if info.get("matched"):
                return (f"Seen: {text!r}. " if text else "The page settled. ") + _where(info)
            raise ToolFailure(f"not seen within {secs:g}s: {text!r}. {_where(info)}")
        if a == "download":
            _need("browser(download)", ref=ref)
            got = await hb.download(conv, int(ref))
            return f"Downloaded {got['name']} ({_kb(int(got.get('bytes') or 0))}) to {got['path']}."
    except ToolFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _browser_failure(exc, a) from exc
    raise ToolFailure(f"browser has no action {action!r}")


browser.metadata = {"timeout_s": BROWSER_TIMEOUT_S, "action_arg": "action"}


# ---- chrome (the owner's) ------------------------------------------------------------------

ChromeAction = Literal["tabs", "open", "use", "read", "find", "click", "type", "key", "scroll", "back", "wait",
                       "fullscreen", "steps", "done"]


@tool
@owner_doc
async def chrome(
    action: ChromeAction,
    url: Optional[str] = None,
    tab_id: Optional[int] = None,
    new_tab: bool = True,
    fullscreen: Optional[bool] = None,
    ref: Optional[int] = None,
    text: Optional[str] = None,
    submit: bool = True,
    key: Optional[str] = None,
    direction: Optional[str] = None,
    amount: Optional[int] = None,
    seconds: Optional[float] = None,
    query: Optional[str] = None,
    url_contains: Optional[str] = None,
    steps: Optional[List[Dict[str, Any]]] = None,
    close_tab: bool = False,
) -> str:
    """Your hands in {OWNER}'s own Chrome on this Mac, with their logins; it
    is in front of them. Use it only when you need their session or to put
    something on their monitor; otherwise use your own browser. done when
    finished.

    tabs lists their tabs; open(url, fullscreen?, new_tab?) opens one as your
    working tab (fullscreen=true fills the monitor — videos, streams); use(tab_id)
    takes over one they opened; read / find(query) / click(ref) / type(text, ref?,
    submit=true) / key(key) / scroll / back / wait(text?, url_contains?, seconds?)
    work like your browser's; fullscreen(fullscreen=false) leaves full screen;
    steps runs up to 12 {action: …} objects in order; done(close_tab?) lets go
    and brings your /screen tab back. Typing outside a search box, and clicks
    like send or delete, only when {OWNER} asked in this turn; never a payment."""
    from services import browser_ext_service as bx
    from services import browser_hands as bh

    a = str(action or "").strip().lower()
    if not bh.enabled():
        # Off by default: nothing reaches the owner's Chrome.
        raise ToolFailure(_hands_off())
    try:
        if a != "done" and not bx.connected():
            raise bh.ExtensionUnavailable("the Edward extension is not connected in Chrome")
        why = await chrome_refusal(a, ref=ref, text=text, key=key, steps=steps)
        if why:
            raise ToolFailure(why)
        if a == "tabs":
            tabs = await bx.tabs()
            held = bh.working_tab()
            head = (f"Tabs in {owner_name()}'s Chrome (you hold tab {held})" if held is not None
                    else f"Tabs in {owner_name()}'s Chrome (you hold none)")
            return head + ":\n" + bx.format_tabs(tabs, working_tab_id=held)
        if a == "open":
            _need("chrome(open)", url=url)
            info = await bh.open_page(str(url), new_tab=bool(new_tab), fullscreen=bool(fullscreen))
            try:
                page = await bh.read_page()
            except Exception as exc:  # noqa: BLE001
                page = f"(could not read the page yet: {exc})"
            fs = " Full screen." if info.get("fullscreen") else ""
            return (f"Opened in {owner_name()}'s Chrome as tab {info.get('tab_id')} (your working tab). "
                    f"{_where(info)}{fs}\n\n{page}")
        if a == "use":
            _need("chrome(use)", tab_id=tab_id)
            info = await bh.use_tab(int(tab_id))
            return f"Tab {tab_id} is yours now. {_where(info)} read to see it."
        if a == "read":
            return await bh.read_page()
        if a == "find":
            _need("chrome(find)", query=query)
            return await bh.find_on_page(str(query))
        if a == "click":
            _need("chrome(click)", ref=ref)
            return f"Clicked [{ref}]. {_where(await bh.click(int(ref)))}"
        if a == "type":
            _need("chrome(type)", text=text)
            info = await bh.type_text(str(text), ref=ref, submit=bool(submit))
            return f"Typed {str(text)!r}{' and pressed Enter' if submit else ''}. {_where(info)}"
        if a == "key":
            _need("chrome(key)", key=key)
            return f"Pressed {key}. {_where(await bh.press_key(str(key)))}"
        if a == "scroll":
            d, n = str(direction or "down"), int(amount or 1)
            return f"Scrolled {d} ×{n}. {_where(await bh.scroll(d, n))}"
        if a == "back":
            return f"Went back. {_where(await bh.back())}"
        if a == "wait":
            secs = float(seconds or 10)
            info = await bh.wait_for(text=text, url_contains=url_contains, timeout=secs)
            what = " and ".join(x for x in (f"{text!r} on the page" if text else "",
                                            f"{url_contains!r} in the URL" if url_contains else "") if x)
            if info.get("matched"):
                return (f"Seen: {what}. " if what else "The page settled. ") + _where(info)
            raise ToolFailure(f"not seen within {secs:g}s: {what}. {_where(info)}")
        if a == "fullscreen":
            res = await bh.set_fullscreen(True if fullscreen is None else bool(fullscreen))
            return "Full screen on — the tab fills the monitor." if res.get("fullscreen") else "Back to a normal window."
        if a == "steps":
            if not steps:
                raise ToolFailure("chrome(steps) needs steps: a list of {action: …} objects")
            try:
                out = await bh.run_steps(steps)
            except ValueError as exc:
                raise ToolFailure(f"steps not run: {exc}") from exc
            if " failed: " in out:
                raise ToolFailure(out)
            return out
        if a == "done":
            out = await bh.release(close=bool(close_tab))
            if out.get("released") is None:
                return f"You were not holding a tab in {owner_name()}'s Chrome."
            bits = [f"Let go of tab {out['released']}" + (" and closed it" if out.get("closed") else "")]
            if out.get("left_fullscreen"):
                bits.append("left full screen")
            if out.get("screen_tab"):
                bits.append(f"your screen tab {out['screen_tab']} is back in front")
            if out.get("error"):
                bits.append(f"note: {out['error']}")
            return "; ".join(bits) + "."
    except ToolFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _chrome_failure(exc, a) from exc
    raise ToolFailure(f"chrome has no action {action!r}")


chrome.metadata = {"timeout_s": CHROME_TIMEOUT_S, "action_arg": "action"}

BROWSER_TOOLS = [browser, chrome]
