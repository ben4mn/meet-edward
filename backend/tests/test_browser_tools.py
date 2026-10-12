"""The two browsers as two tools (AREA 2, 2026-10-11): ``browser`` is
Edward's own invisible one, ``chrome`` is Ellis's real Chrome through the
extension. Both run browser_hands' page actions; every failure raises
(the executor makes it ``Tool error: …``). Chrome's rails: typing outside a
search box and clicks that act as Ellis (send, post, delete, …) need Ellis to
be the one asking; a payment step or a card number is refused in every
turn. The extension, Chrome and the headless browser are all faked."""

import pytest

from services import browser_ext_service as bx
from services import browser_hands as bh
from services import headless_browser as hb
from services.toolset import _compat
from services.toolset import browsers as bt
from services.toolset._compat import ToolFailure

pytestmark = pytest.mark.unit


async def call(tool, **args):
    return await tool.ainvoke(args)


class FakeTarget:
    tab_id = "browser"

    def __init__(self, labels=None):
        self.labels = labels or {}

    async def send(self, method, params=None, *, timeout=None):
        return {}


@pytest.fixture
def headless(monkeypatch):
    """A fake headless browser: records what was asked of it."""
    calls = []
    target = FakeTarget()

    async def open_url(conv, url):
        calls.append(("open", conv, url))
        hb.check_url(url)
        if "127.0.0.1" in url:
            raise hb.BrowserRefused("127.0.0.1 is this Mac (loopback)")
        return {"tab_id": "browser", "url": url, "title": "Tickets"}

    async def target_for(conv):
        calls.append(("target", conv))
        return target

    async def guard_current(conv):
        calls.append(("guard", conv))

    async def download(conv, ref, **kw):
        calls.append(("download", conv, ref))
        return {"path": "/Users/x/edward-home/downloads/report.pdf", "name": "report.pdf", "bytes": 2048, "url": "u"}

    async def close_page(conv):
        calls.append(("close", conv))
        return True

    async def back(conv):
        calls.append(("back", conv))
        return {"tab_id": "browser", "url": "https://x/prev", "title": "Prev"}

    monkeypatch.setattr(hb, "open_url", open_url)
    monkeypatch.setattr(hb, "target_for", target_for)
    monkeypatch.setattr(hb, "guard_current", guard_current)
    monkeypatch.setattr(hb, "download", download)
    monkeypatch.setattr(hb, "close_page", close_page)
    monkeypatch.setattr(hb, "back", back)
    monkeypatch.setattr(hb, "has_page", lambda conv: True)
    monkeypatch.setattr(_compat, "conversation_id", lambda: "conv-1")

    async def read_page(*, target=None):
        assert target is not None, "the headless tool must pass its target"
        return "Tickets — https://x\n\n[1] button Buy"
    monkeypatch.setattr(bh, "read_page", read_page)

    async def click(ref, *, target=None):
        assert target is not None
        calls.append(("click", ref))
        return {"tab_id": "browser", "url": "https://x/next", "title": "Next"}
    monkeypatch.setattr(bh, "click", click)

    async def type_text(text, *, ref=None, submit=True, target=None):
        assert target is not None
        calls.append(("type", text, ref, submit))
        return {"tab_id": "browser", "url": "https://x/q", "title": "Q"}
    monkeypatch.setattr(bh, "type_text", type_text)
    return calls


class TestBrowser:
    async def test_open_reads_the_page_in_the_conversations_browser(self, headless):
        out = await call(bt.browser, action="open", url="https://tickets.example/show")
        assert ("open", "conv-1", "https://tickets.example/show") in headless
        assert "Tickets" in out and "[1] button Buy" in out

    async def test_open_needs_a_url(self, headless):
        with pytest.raises(ToolFailure, match="url"):
            await call(bt.browser, action="open")

    @pytest.mark.parametrize("url", ["file:///etc/hosts", "chrome://settings", "http://127.0.0.1:8000/api/memories"])
    async def test_refused_urls_raise(self, headless, url):
        with pytest.raises(ToolFailure):
            await call(bt.browser, action="open", url=url)

    async def test_click_and_type_run_on_the_headless_target_and_check_where_they_landed(self, headless):
        out = await call(bt.browser, action="click", ref=1)
        assert "Clicked [1]" in out and ("guard", "conv-1") in headless
        out = await call(bt.browser, action="type", text="concert tickets", ref=2)
        assert ("type", "concert tickets", 2, True) in headless and "Typed" in out

    async def test_click_needs_a_ref(self, headless):
        with pytest.raises(ToolFailure, match="ref"):
            await call(bt.browser, action="click")

    async def test_reading_with_no_page_open_says_open_first(self, headless, monkeypatch):
        monkeypatch.setattr(hb, "has_page", lambda conv: False)
        with pytest.raises(ToolFailure, match="open"):
            await call(bt.browser, action="read")

    async def test_download_and_close(self, headless):
        out = await call(bt.browser, action="download", ref=3)
        assert "report.pdf" in out and "2 KB" in out
        assert "Closed" in await call(bt.browser, action="close")

    async def test_unavailable_browser_is_an_honest_failure(self, headless, monkeypatch):
        async def down(conv, url):
            raise hb.HeadlessUnavailable("the headless browser is not installed")
        monkeypatch.setattr(hb, "open_url", down)
        with pytest.raises(ToolFailure, match="not installed"):
            await call(bt.browser, action="open", url="https://example.com")


@pytest.fixture
def chrome(monkeypatch):
    calls = []
    monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")   # off by default
    monkeypatch.setattr(bx, "connected", lambda: True)
    monkeypatch.setattr(bh, "working_tab", lambda: 7)
    labels = {1: "Search", 2: "Send", 3: "Place order", 4: "Paywall details", 5: "Next"}

    async def ref_label(ref, *, target=None):
        return labels.get(int(ref), "")
    monkeypatch.setattr(bt, "_ref_label", ref_label)

    async def current_url():
        return calls_url[0]
    calls_url = ["https://mail.google.com/mail/u/0/#inbox"]
    monkeypatch.setattr(bt, "_chrome_url", current_url)

    async def click(ref, *, target=None):
        calls.append(("click", ref))
        return {"tab_id": 7, "url": calls_url[0], "title": "T"}

    async def type_text(text, *, ref=None, submit=True, target=None):
        calls.append(("type", text))
        return {"tab_id": 7, "url": calls_url[0], "title": "T"}

    async def press_key(key, *, target=None):
        calls.append(("key", key))
        return {"tab_id": 7, "url": calls_url[0], "title": "T"}

    async def run_steps(steps):
        calls.append(("steps", steps))
        return "1. ok"

    async def open_page(url, *, new_tab=True, fullscreen=False):
        calls.append(("open", url))
        return {"tab_id": 7, "url": url, "title": "T", "fullscreen": fullscreen}

    async def read_page(*, target=None):
        return "T — page"

    monkeypatch.setattr(bh, "click", click)
    monkeypatch.setattr(bh, "type_text", type_text)
    monkeypatch.setattr(bh, "press_key", press_key)
    monkeypatch.setattr(bh, "run_steps", run_steps)
    monkeypatch.setattr(bh, "open_page", open_page)
    monkeypatch.setattr(bh, "read_page", read_page)
    return calls, calls_url


def as_owner(monkeypatch, yes: bool):
    monkeypatch.setattr(_compat, "owner_asked", lambda: yes)


class TestChrome:
    async def test_open_and_read_in_owners_chrome(self, chrome, monkeypatch):
        as_owner(monkeypatch, False)
        out = await call(bt.chrome, action="open", url="https://www.youtube.com/results?search_query=x")
        assert "tab 7" in out and "T — page" in out

    async def test_extension_down_is_a_failure_that_says_what_to_do(self, monkeypatch):
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: False)
        with pytest.raises(ToolFailure, match="extension is not connected"):
            await call(bt.chrome, action="tabs")

    async def test_hands_off_by_default_nothing_reaches_chrome(self, monkeypatch):
        monkeypatch.delenv("EDWARD_CHROME_HANDS", raising=False)
        monkeypatch.setattr(bx, "connected", lambda: True)
        with pytest.raises(ToolFailure, match="Chrome hands are off"):
            await call(bt.chrome, action="tabs")

    async def test_no_working_tab(self, chrome, monkeypatch):
        as_owner(monkeypatch, True)

        async def no_tab(*a, **k):
            raise bh.NoWorkingTab("you are not holding a tab in Chrome")
        monkeypatch.setattr(bh, "read_page", no_tab)
        with pytest.raises(ToolFailure, match="not holding a tab"):
            await call(bt.chrome, action="read")


class TestChromeRails:
    async def test_typing_outside_a_search_box_needs_owner(self, chrome, monkeypatch):
        calls, url = chrome
        as_owner(monkeypatch, False)
        with pytest.raises(ToolFailure, match="acts as Ellis"):
            await call(bt.chrome, action="type", text="see you at 7", ref=1)
        assert not any(c[0] == "type" for c in calls)
        as_owner(monkeypatch, True)
        await call(bt.chrome, action="type", text="see you at 7", ref=1)
        assert ("type", "see you at 7") in calls

    async def test_typing_into_a_search_site_is_fine_unasked(self, chrome, monkeypatch):
        calls, url = chrome
        url[0] = "https://www.youtube.com/"
        as_owner(monkeypatch, False)
        await call(bt.chrome, action="type", text="lofi piano", ref=1)
        assert ("type", "lofi piano") in calls

    async def test_clicks_that_act_as_owner_need_owner(self, chrome, monkeypatch):
        calls, url = chrome
        as_owner(monkeypatch, False)
        with pytest.raises(ToolFailure, match="Send"):
            await call(bt.chrome, action="click", ref=2)
        await call(bt.chrome, action="click", ref=5)          # "Next" is fine
        assert ("click", 5) in calls and ("click", 2) not in calls
        as_owner(monkeypatch, True)
        await call(bt.chrome, action="click", ref=2)
        assert ("click", 2) in calls

    async def test_payment_steps_are_refused_in_every_turn(self, chrome, monkeypatch):
        calls, url = chrome
        as_owner(monkeypatch, True)
        with pytest.raises(ToolFailure, match="payment"):
            await call(bt.chrome, action="click", ref=3)
        await call(bt.chrome, action="click", ref=4)          # "Paywall" is not a payment
        assert ("click", 4) in calls

    async def test_a_card_number_is_never_typed(self, chrome, monkeypatch):
        calls, url = chrome
        url[0] = "https://www.google.com/"
        as_owner(monkeypatch, True)
        with pytest.raises(ToolFailure, match="card"):
            await call(bt.chrome, action="type", text="4111 1111 1111 1111", ref=1)
        await call(bt.chrome, action="type", text="flight 4111 status", ref=1)
        assert ("type", "flight 4111 status") in calls

    async def test_enter_outside_a_search_site_needs_owner(self, chrome, monkeypatch):
        calls, url = chrome
        as_owner(monkeypatch, False)
        with pytest.raises(ToolFailure, match="acts as Ellis"):
            await call(bt.chrome, action="key", key="Enter")
        await call(bt.chrome, action="key", key="Escape")
        assert ("key", "Escape") in calls

    async def test_steps_are_inspected(self, chrome, monkeypatch):
        calls, url = chrome
        as_owner(monkeypatch, False)
        with pytest.raises(ToolFailure, match="acts as Ellis"):
            await call(bt.chrome, action="steps", steps=[{"action": "type", "text": "hello there", "ref": 1}])
        with pytest.raises(ToolFailure, match="Send"):
            await call(bt.chrome, action="steps", steps=[{"action": "click", "ref": 2}])
        with pytest.raises(ToolFailure, match="payment"):
            as_owner(monkeypatch, True)
            await call(bt.chrome, action="steps", steps=[{"action": "click", "ref": 3}])
        assert not any(c[0] == "steps" for c in calls)

    async def test_typing_after_a_click_in_steps_is_judged_on_an_unknown_page(self, chrome, monkeypatch):
        """Review 2026-10-11: a click can navigate, so typing after it was
        judged against the search page the steps opened — a forum or a
        contact form looked like google.com."""
        calls, url = chrome
        url[0] = "https://www.google.com/"
        as_owner(monkeypatch, False)
        search = {"action": "open", "url": "https://www.google.com/search?q=edward"}
        for nav in ({"action": "click", "ref": 5}, {"action": "back"}, {"action": "key", "key": "Enter"}):
            with pytest.raises(ToolFailure, match="can't tell"):
                await call(bt.chrome, action="steps", steps=[search, nav, {"action": "type", "text": "hi", "ref": 1}])
        assert not any(c[0] == "steps" for c in calls)
        # Searching is still fine: type into the search page, then Enter.
        await call(bt.chrome, action="steps", steps=[search, {"action": "type", "text": "q", "ref": 1},
                                                     {"action": "key", "key": "Enter"}])
        # Ellis asking lifts it.
        as_owner(monkeypatch, True)
        await call(bt.chrome, action="steps", steps=[search, {"action": "click", "ref": 5},
                                                     {"action": "type", "text": "hi", "ref": 1}])
        assert sum(1 for c in calls if c[0] == "steps") == 2


class TestPure:
    def test_luhn(self):
        assert bt.looks_like_card("4111-1111-1111-1111")
        assert bt.looks_like_card("my card is 5500 0000 0000 0004 thanks")
        assert not bt.looks_like_card("4111 1111 1111 1112")
        assert not bt.looks_like_card("call 312 555 0100")

    def test_payment_words_are_word_bounded(self):
        for label in ("Place order", "Buy now", "Pay $42.10", "Checkout", "Proceed to checkout", "Confirm payment", "Add card"):
            assert bt.is_payment_label(label), label
        for label in ("Paywall", "payload", "Repay schedule", "Display", "Next"):
            assert not bt.is_payment_label(label), label

    def test_acting_words(self):
        for label in ("Send", "Post", "Reply all", "Delete", "Accept invite", "Unsubscribe", "Sign out"):
            assert bt.is_acting_label(label), label
        for label in ("Search", "Next", "Sender details", "Postal code", "Deleted items"):
            assert not bt.is_acting_label(label), label


class TestStepsNeverLook:
    async def test_a_look_step_is_sent_to_the_look_tool(self, chrome, monkeypatch):
        """browser_hands' own look step activates Ellis's tab and asks a second
        model for words; the look tool returns the picture without touching
        his tabs."""
        calls, url = chrome
        as_owner(monkeypatch, True)
        with pytest.raises(ToolFailure, match=r"look\('chrome'\)"):
            await call(bt.chrome, action="steps", steps=[{"action": "read"}, {"action": "look"}])
        assert not any(c[0] == "steps" for c in calls)
