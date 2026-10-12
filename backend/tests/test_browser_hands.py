"""Edward's hands in Ellis's Chrome (2026-09-07, second pass — Ellis: "now that
he has my Chrome, 'put X on your screen' is: open a tab, go full screen,
come back to /screen when done"). One coherent tool set, no headless
shell, no screencast tile. Pure decisions are tested here; the extension
socket and CDP are faked. Nothing here may reach osascript or Chrome."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import browser_ext_service as bx
from services import browser_hands as bh
from services import screen_service as ss

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


# ---- pure ------------------------------------------------------------------------

class TestPageRead:
    def test_format_numbers_the_clickables_and_caps_the_text(self):
        payload = {
            "url": "https://www.youtube.com/results?search_query=lofi+piano",
            "title": "lofi piano - YouTube",
            "text": "x" * 10_000,
            "scroll": {"y": 0, "height": 4000, "viewport": 900},
            "elements": [
                {"ref": 1, "tag": "input", "role": "searchbox", "text": "", "placeholder": "Search", "href": None},
                {"ref": 2, "tag": "a", "text": "Lofi Piano - Rainy Evening (Live)", "href": "https://www.youtube.com/watch?v=abc123DEF_-"},
                {"ref": 3, "tag": "button", "text": "Filters", "href": None},
                {"ref": 4, "tag": "input", "type": "checkbox", "text": "Remember me", "checked": True},
            ],
        }
        out = bh.format_page_read(payload)
        assert out.startswith("lofi piano - YouTube — https://www.youtube.com/results")
        assert "top of page" in out or "0%" in out
        assert "[1] searchbox" in out and "Search" in out
        assert "[2] link Lofi Piano - Rainy Evening (Live) → https://www.youtube.com/watch?v=abc123DEF_-" in out
        assert "[3] button Filters" in out
        assert "[4] checkbox Remember me (checked)" in out
        assert len(out) < bh.PAGE_TEXT_CHARS + 2_000

    def test_scroll_position_is_described(self):
        assert bh.scroll_phrase({"y": 0, "height": 4000, "viewport": 900}) == "top of page, 22% visible"
        assert bh.scroll_phrase({"y": 3100, "height": 4000, "viewport": 900}) == "bottom of page"
        assert bh.scroll_phrase({"y": 1000, "height": 4000, "viewport": 900}).startswith("scrolled 25%")
        assert bh.scroll_phrase(None) == ""

    def test_find_elements_matches_text_role_placeholder_and_href(self):
        els = [
            {"ref": 1, "tag": "a", "text": "Cancel subscription", "href": "https://x/cancel"},
            {"ref": 2, "tag": "input", "placeholder": "Search", "text": ""},
            {"ref": 3, "tag": "button", "text": "Sign in", "role": "button"},
        ]
        assert [e["ref"] for e in bh.find_elements(els, "cancel")] == [1]
        assert [e["ref"] for e in bh.find_elements(els, "search")] == [2]
        assert [e["ref"] for e in bh.find_elements(els, "BUTTON")] == [3]
        assert bh.find_elements(els, "") == []

    def test_scroll_deltas(self):
        assert bh.wheel_delta("down", 1) == (0, bh.SCROLL_STEP_PX)
        assert bh.wheel_delta("up", 2) == (0, -2 * bh.SCROLL_STEP_PX)
        assert bh.wheel_delta("right", 1) == (bh.SCROLL_STEP_PX, 0)
        with pytest.raises(ValueError):
            bh.wheel_delta("sideways", 1)

    def test_key_events_cover_the_keys_a_page_needs(self):
        enter = bh.key_params("Enter")
        assert enter["key"] == "Enter" and enter["windowsVirtualKeyCode"] == 13 and enter["text"] == "\r"
        esc = bh.key_params("escape")
        assert esc["key"] == "Escape" and esc["windowsVirtualKeyCode"] == 27
        assert bh.key_params("ArrowDown")["windowsVirtualKeyCode"] == 40
        assert bh.key_params("f")["text"] == "f"          # YouTube's fullscreen key
        assert bh.key_params(" ")["key"] == " "
        with pytest.raises(ValueError):
            bh.key_params("")


class TestSettle:
    def test_two_identical_complete_snapshots_mean_settled(self):
        a = {"ready": "complete", "url": "https://x/a", "height": 1000}
        assert bh.is_settled(a, dict(a)) is True
        assert bh.is_settled({**a, "ready": "loading"}, {**a, "ready": "loading"}) is False
        assert bh.is_settled(a, {**a, "height": 1400}) is False
        assert bh.is_settled(a, {**a, "url": "https://x/b"}) is False
        assert bh.is_settled(None, a) is False


class TestSteps:
    def test_plan_validates_each_step_and_caps_the_count(self):
        steps = bh.plan_steps([
            {"action": "open", "url": "https://www.youtube.com/results?search_query=lofi+piano"},
            {"action": "find", "query": "rainy"},
            {"action": "click", "ref": 2},
            {"action": "type", "text": "hello", "ref": 1, "submit": False},
            {"action": "key", "key": "Escape"},
            {"action": "scroll", "direction": "down", "amount": 2},
            {"action": "wait", "text": "Playing", "seconds": 5},
            {"action": "fullscreen", "on": True},
            {"action": "back"}, {"action": "read"}, {"action": "look"},
        ])
        assert [s["action"] for s in steps][:3] == ["open", "find", "click"]
        assert steps[2]["ref"] == 2 and steps[3]["submit"] is False and steps[5]["amount"] == 2
        with pytest.raises(ValueError, match="step 1"):
            bh.plan_steps([{"action": "dance"}])
        with pytest.raises(ValueError, match="step 2"):
            bh.plan_steps([{"action": "read"}, {"action": "click"}])       # click needs ref
        with pytest.raises(ValueError, match="step 1"):
            bh.plan_steps([{"action": "type"}])                            # type needs text
        with pytest.raises(ValueError, match="at most"):
            bh.plan_steps([{"action": "read"}] * (bh.MAX_STEPS + 1))
        with pytest.raises(ValueError):
            bh.plan_steps("read")

    async def test_run_steps_runs_in_order_and_stops_at_the_first_failure(self, monkeypatch):
        calls = []

        async def ok(name):
            async def fn(*a, **kw):
                calls.append((name, a, kw))
                return {"url": "https://x/" + name, "title": name}
            return fn

        monkeypatch.setattr(bh, "navigate", await ok("navigate"))
        monkeypatch.setattr(bh, "click", await ok("click"))
        monkeypatch.setattr(bh, "scroll", await ok("scroll"))

        async def boom(*a, **kw):
            raise RuntimeError("no element [9] on the page")
        monkeypatch.setattr(bh, "type_text", boom)

        async def read():
            return "PAGE TEXT"
        monkeypatch.setattr(bh, "read_page", read)

        out = await bh.run_steps([
            {"action": "open", "url": "https://x/a", "new_tab": False},
            {"action": "click", "ref": 2},
            {"action": "type", "text": "hi", "ref": 9},
            {"action": "scroll"},
            {"action": "read"},
        ])
        assert [c[0] for c in calls] == ["navigate", "click"]
        assert "1. open" in out and "2. click" in out
        assert "3. type" in out and "failed" in out and "no element [9]" in out
        assert "scroll" not in out.split("failed", 1)[1] or "not run" in out
        assert "PAGE TEXT" not in out  # the read after the failure never ran


class TestDescribe:
    def test_describe_hands_for_the_pulse(self):
        assert bh.describe_hands({"extension": {"connected": False}}) == "the Edward extension is not connected in Chrome"
        assert bh.describe_hands({"extension": {"connected": True}, "working_tab": None}) == "idle (no tab of yours open)"
        s = {"extension": {"connected": True}, "working_tab": 12,
             "tab": {"url": "https://www.youtube.com/watch?v=abc", "title": "Lofi Piano - Rainy Evening - YouTube"}, "fullscreen": True}
        assert bh.describe_hands(s) == "in Chrome tab 12 — Lofi Piano - Rainy Evening - YouTube (youtube.com/watch), full screen"


# ---- the extension link -----------------------------------------------------------

class TestExtensionLink:
    def test_no_headless_fallback_remains(self):
        assert not hasattr(bx, "pick_hands")
        import services
        assert not (Path(services.__file__).parent / "screen_driver.py").exists()

    async def test_hands_need_the_extension(self, monkeypatch):
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        bh.set_working_tab(None)
        with pytest.raises(bx.ExtensionUnavailable):
            await bh.read_page()

    async def test_hands_need_a_working_tab(self, monkeypatch):
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: True)
        bh.set_working_tab(None)
        with pytest.raises(bh.NoWorkingTab):
            await bh.click(1)

    async def test_a_detached_working_tab_is_let_go(self):
        bh.set_working_tab(7)
        bh._fullscreen = True
        await bh.on_event({"type": "event", "name": "detached", "tabId": 7, "params": {"reason": "target_closed"}})
        assert bh.working_tab() is None and bh.status()["fullscreen"] is False
        bh.set_working_tab(8)
        await bh.on_event({"type": "event", "name": "detached", "tabId": 9, "params": {}})
        assert bh.working_tab() == 8
        await bh.on_event({"type": "event", "name": "disconnected", "tabId": None, "params": {}})
        assert bh.working_tab() is None

    def test_extension_speaks_the_methods_the_backend_calls(self):
        js = (ROOT / "chrome-extension" / "background.js").read_text()
        for method in ("tabs.list", "tabs.open", "tabs.activate", "tabs.close", "tabs.navigate", "tabs.capture",
                       "windows.fullscreen", "attach", "detach", "cdp"):
            assert f'"{method}"' in js, method
        assert "captureVisibleTab" in js and "fullscreen" in js
        assert "screencastFrame" not in js
        manifest = (ROOT / "chrome-extension" / "manifest.json").read_text()
        assert '"version": "0.2.0"' in manifest


# ---- the screen without the browser tile ------------------------------------------

class TestScreenIsJustTilesAndOverlays:
    def test_no_web_kind_and_no_browser_tile(self):
        assert "web" not in ss.SHOW_KINDS
        assert "browser" not in ss.TILE_TYPES
        with pytest.raises(ValueError):
            ss.make_show_content(kind="web", source="https://x", now=NOW)
        for name in ("is_embeddable_web", "web_render", "replace_show_source"):
            assert not hasattr(ss, name), name

    def test_snapshot_never_injects_a_tile(self):
        snap = ss.build_snapshot(layout=None, layout_note=None, layout_updated_at=None, layout_updated_by=None,
                                 show=ss.make_show_content(kind="alert", body="hi", now=NOW), now=NOW)
        assert [t["type"] for t in snap["layout"]] == [t["type"] for t in ss.default_layout()]
        assert "render" not in snap["show"]

    def test_describe_show(self):
        assert ss.describe_show(None) == "its scene"
        assert ss.describe_show(ss.make_show_content(kind="alert", body="x", title="Vet", now=NOW)) == "an alert 'Vet'"


# ---- tools --------------------------------------------------------------------------

class TestTools:
    def test_the_tool_set_is_the_whole_web_story(self):
        from services.graph import tools as t
        browser = {x.name for x in t.BROWSER_TOOLS}
        assert browser == {"browser_tabs", "browser_open", "browser_use_tab", "browser_read", "browser_find",
                           "browser_click", "browser_type", "browser_key", "browser_scroll", "browser_back",
                           "browser_wait", "browser_fullscreen", "browser_steps", "browser_done"}
        screen = {x.name for x in t.SCREEN_TOOLS}
        assert screen == {"show_on_screen", "clear_screen", "compose_screen", "say_on_screen", "reset_screen_layout",
                          "look_at_screen", "bring_up_screen"}
        assert not hasattr(t, "set_screen_layout")  # the stage replaced the tile grid (2026-09-28)
        assert not hasattr(t, "BROWSER_AUTOMATION_TOOLS")
        assert not hasattr(t, "CANVAS_TOOLS")
        for old in ("browse_web", "browse_my_chrome", "my_chrome_click", "browser_extract", "browser_screenshot"):
            assert not hasattr(t, old), old

    def test_docstrings_tell_one_story(self):
        from services.graph import tools as t
        d = t.browser_open.description
        assert "Ellis's Chrome" in d and "fullscreen" in d
        assert "headless" not in d and "hidden browser" not in d and "tile" not in d.lower()
        assert "browser" not in t.show_on_screen.description.lower().split("kind")[0] or "browser_open" in t.show_on_screen.description
        assert "web" not in t.show_on_screen.description.split("kind:")[1].split("seconds")[0] if "kind:" in t.show_on_screen.description else True
        assert "browser_steps" in t.browser_click.description or "browser_read" in t.browser_click.description
        manual = t.get_web_tools_description()
        for name in ("web_search", "fetch_page_content", "browser_open", "browser_steps", "browser_fullscreen", "browser_done", "look_at_screen"):
            assert name in manual, name
        assert "Lightpanda" not in manual and "Tier" not in manual and "remote-debugging-port" not in manual

    def test_registry_binds_both_sets_always_and_drops_the_legacy_skill(self):
        from services import tool_registry as tr
        assert "browser_automation" not in tr.SKILL_TOOL_MAPPING
        assert "browser_automation" not in tr._SKILL_IDS
        src = Path(tr.__file__).read_text()
        assert "BROWSER_TOOLS" in src and "SCREEN_TOOLS" in src and "BROWSER_AUTOMATION_TOOLS" not in src
        assert "get_web_tools_description" in src and "get_search_tools_description" not in src
        from services import skills_service as sk
        assert "browser_automation" not in sk.SKILLS if hasattr(sk, "SKILLS") else True

    async def test_tools_explain_when_the_extension_is_not_connected(self, monkeypatch):
        from services.graph import tools as t
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: False)
        bh.set_working_tab(None)
        for tool, args in ((t.browser_tabs, {}), (t.browser_open, {"url": "https://x"}), (t.browser_read, {}),
                           (t.browser_click, {"ref": 1}), (t.browser_steps, {"steps": [{"action": "read"}]})):
            out = await tool.ainvoke(args)
            assert "extension" in out and "chrome://extensions" in out, tool.name

    async def test_tools_explain_when_no_tab_is_held(self, monkeypatch):
        from services.graph import tools as t
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: True)
        bh.set_working_tab(None)
        out = await t.browser_read.ainvoke({})
        assert "browser_open" in out and "browser_use_tab" in out

    async def test_show_on_screen_points_web_asks_at_the_browser(self, monkeypatch):
        from services.graph import tools as t
        out = await t.show_on_screen.ainvoke({"kind": "web", "source": "https://www.twitch.tv/examplestreamer", "screen": "mini"})
        assert "browser_open" in out and "fullscreen" in out.lower()

    async def test_browser_open_opens_in_chrome_and_can_go_fullscreen(self, monkeypatch):
        from services.graph import tools as t
        monkeypatch.setattr(bx, "connected", lambda: True)
        seen = {}

        async def fake_open(url, *, new_tab=True, fullscreen=False):
            seen.update(url=url, new_tab=new_tab, fullscreen=fullscreen)
            return {"tab_id": 12, "url": url, "title": "examplestreamer - Twitch", "fullscreen": fullscreen}
        monkeypatch.setattr(bh, "open_page", fake_open)

        async def fake_read():
            return "examplestreamer - Twitch — https://www.twitch.tv/examplestreamer\n\nLIVE"
        monkeypatch.setattr(bh, "read_page", fake_read)
        out = await t.browser_open.ainvoke({"url": "https://www.twitch.tv/examplestreamer", "fullscreen": True})
        assert seen == {"url": "https://www.twitch.tv/examplestreamer", "new_tab": True, "fullscreen": True}
        assert "tab 12" in out and "full screen" in out.lower() and "LIVE" in out

    async def test_browser_done_clears_up_and_returns_to_the_screen(self, monkeypatch):
        from services.graph import tools as t
        monkeypatch.setattr(bx, "connected", lambda: True)

        async def fake_release(*, close=False, back_to_screen=True):
            return {"released": 12, "closed": close, "screen_tab": 3, "left_fullscreen": True}
        monkeypatch.setattr(bh, "release", fake_release)
        out = await t.browser_done.ainvoke({"close_tab": True})
        assert "12" in out and "closed" in out and "screen" in out


# ---- routers ------------------------------------------------------------------------

class TestRoutes:
    def test_screen_router_lost_the_cast_and_driver_endpoints(self):
        from routers import screen as r
        paths = {getattr(x, "path", None) for x in r.router.routes}
        assert "/screen/frame" not in paths and "/screen/driver" not in paths and "/screen/browser" not in paths
        assert {"/screen", "/screen/show", "/screen/scene", "/screen/look", "/screen/window"} <= paths
        assert "/screen/layout" not in paths  # the tile grid's routes went with it (2026-09-28)

    def test_browser_router_has_status_and_operator_hands(self):
        from routers import browser_ext as r
        paths = {getattr(x, "path", None) for x in r.router.routes}
        assert {"/browser/ext", "/browser/status", "/browser/act"} <= paths


# ---- expression pulse ---------------------------------------------------------------

class TestPulse:
    def test_prompt_tells_the_new_story(self):
        from services import expression_service as es
        p = es.build_expression_system_prompt(chrome_hands=True, people=("Ellis",))
        for needle in ("browser_open", "browser_fullscreen", "browser_done", "look_at_screen", "compose_screen"):
            assert needle in p, needle
        for gone in ("browser tile", 'kind "web"', "hidden browser", "headless"):
            assert gone not in p, gone

    def test_without_hands_the_prompt_never_offers_chrome(self):
        from services import expression_service as es
        p = es.build_expression_system_prompt(chrome_hands=False, people=("Ellis",))
        for needle in ("look_at_screen", "compose_screen"):
            assert needle in p, needle
        for gone in ("browser_open", "browser_fullscreen", "browser_done", "bring_up_screen", "headless"):
            assert gone not in p, gone

    def test_trigger_says_where_his_hands_are(self):
        from services import expression_service as es
        sig = es.PulseSignals(now=NOW, attention={"owner_present": False, "active_surface": None, "in_imessage_thread": None},
                              recent_messages=[], board_summary="", current_widget=None, current_screen=None,
                              mini_screen={"layout_source": "default", "show": None},
                              browser={"extension": {"connected": True}, "working_tab": 4,
                                       "tab": {"url": "https://www.twitch.tv/examplestreamer", "title": "examplestreamer - Twitch"}, "fullscreen": True})
        t = es.build_pulse_trigger(sig)
        assert "This Mac's screen now: the stock scene" in t
        assert "Your hands in Chrome: in Chrome tab 4 — examplestreamer - Twitch (twitch.tv/examplestreamer), full screen" in t


class TestLookFallback:
    async def test_a_failed_screenshot_describes_the_scene_from_state(self, monkeypatch):
        """The stage (2026-09-28): the fallback names the scene's pieces, not the old tiles."""
        async def boom():
            raise RuntimeError("no display")

        async def snap(**_):
            return ss.build_snapshot(now=NOW)

        monkeypatch.setattr(bh, "screenshot_display", boom)
        monkeypatch.setattr(ss, "get_snapshot", snap)
        out = await bh.look("display")
        assert "Could not take a picture (no display)" in out
        assert "its scene" in out and "pieces: clock, calendar, pets" in out and "tiles" not in out


class TestStaleShows:
    def test_a_stored_show_of_a_retired_kind_is_dropped(self):
        """The DB still held a kind="web" show from the morning when the kind
        was removed; the page cannot draw it, so the snapshot must not offer it."""
        stale = {"kind": "web", "source": "https://lawn-care.example.com", "shown_at": NOW.isoformat(), "expires_at": None}
        snap = ss.build_snapshot(layout=None, layout_note=None, layout_updated_at=None, layout_updated_by=None, show=stale, now=NOW)
        assert snap["show"] is None
        assert ss.show_is_usable(stale) is False
        assert ss.show_is_usable(ss.make_show_content(kind="text", body="x", now=NOW)) is True


class TestOpenIsForgiving:
    async def test_a_failed_fullscreen_does_not_fail_the_open(self, monkeypatch):
        """Live, 2026-09-07: windows.fullscreen was unknown to a stale
        extension and browser_open reported 'Opening the page failed' with
        the tab already open. The tab is the point; full screen is a note."""
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: True)
        monkeypatch.setattr(bh, "SETTLE_MIN_S", 0)

        async def fake_call(method, params=None, *, timeout=None):
            if method == "tabs.open":
                return {"tabId": 5}
            if method == "windows.fullscreen":
                raise RuntimeError("unknown method windows.fullscreen")
            if method == "cdp":
                return {"result": {"value": {"url": "https://x/a", "title": "A", "ready": "complete", "height": 10}}}
            raise AssertionError(method)
        monkeypatch.setattr(bx, "call", fake_call)
        bh.set_working_tab(None)
        info = await bh.open_page("https://x/a", fullscreen=True)
        assert info["tab_id"] == 5 and bh.working_tab() == 5
        assert info["fullscreen"] is False and "full screen" in info["note"].lower()

    def test_chrome_is_launched_without_the_debugger_infobar(self, monkeypatch):
        """The 'Edward started debugging this browser' bar was drawn over a
        full-screen video; --silent-debugger-extension-api hides it. The bar
        is the honest sign that Edward is driving, so hiding it is opt-in
        (EDWARD_CHROME_SILENT_DEBUGGER=on)."""
        from services import screen_window_service as sw
        monkeypatch.delenv("EDWARD_CHROME_SILENT_DEBUGGER", raising=False)
        assert "--silent-debugger-extension-api" not in sw.launch_argv("https://edward.example.com/screen")
        monkeypatch.setenv("EDWARD_CHROME_SILENT_DEBUGGER", "on")
        assert "--silent-debugger-extension-api" in sw.launch_argv("https://edward.example.com/screen")


class TestOperatorActions:
    async def test_close_closes_any_tab_without_making_it_the_working_tab(self, monkeypatch):
        """Cleaning up stray tabs (chrome://extensions cannot be attached, so
        use+done cannot reach them — and done closed the wrong tab)."""
        calls = []

        async def fake_call(method, params=None, *, timeout=None):
            calls.append((method, params))
            return {"ok": True}
        monkeypatch.setattr(bx, "connected", lambda: True)
        monkeypatch.setattr(bx, "call", fake_call)
        bh.set_working_tab(4)
        out = await bh.act("close", tab_id=9)
        assert calls == [("tabs.close", {"tabId": 9})] and out == {"closed": 9}
        assert bh.working_tab() == 4
        out = await bh.act("close", tab_id=4)
        assert bh.working_tab() is None and out == {"closed": 4}


# ---- one set of actions, two targets (AREA 2, 2026-10-11) ----------------------------

class _FakeTarget:
    """A CdpTarget that is neither Chrome nor the extension: a scripted page."""

    tab_id = "browser"

    def __init__(self):
        self.calls = []

    async def send(self, method, params=None, *, timeout=None):
        self.calls.append((method, params or {}))
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            if "data-edward-ref=" in expr and "getBoundingClientRect" in expr:
                return {"result": {"value": {"x": 10, "y": 20}}}
            if "innerWidth" in expr and "innerHeight" in expr and "w:" in expr:
                return {"result": {"value": {"w": 1280, "h": 900}}}
            if "elements" in expr or "querySelectorAll" in expr:
                return {"result": {"value": {"url": "https://x/a", "title": "A", "text": "hello",
                                             "elements": [{"ref": 1, "tag": "button", "text": "Go"}]}}}
            return {"result": {"value": {"url": "https://x/a", "title": "A", "ready": "complete", "height": 10}}}
        return {}


class TestActionsTakeATarget:
    """The headless browser runs browser_hands' own actions against a
    Playwright page: the actions take a ``target`` and never need the
    extension or a working tab when one is given."""

    async def test_read_click_type_key_scroll_back_wait_on_a_given_target(self, monkeypatch):
        monkeypatch.setattr(bx, "connected", lambda: False)   # the extension is down: irrelevant here
        monkeypatch.setattr(bh, "SETTLE_MIN_S", 0)
        bh.set_working_tab(None)
        t = _FakeTarget()
        assert "[1] button Go" in await bh.read_page(target=t)
        assert "[1] button Go" in await bh.find_on_page("go", target=t)
        assert (await bh.click(1, target=t))["tab_id"] == "browser"
        await bh.type_text("abc", ref=1, submit=True, target=t)
        await bh.press_key("Escape", target=t)
        await bh.scroll("down", 1, target=t)
        await bh.back(target=t)
        assert (await bh.wait_for(target=t, timeout=1))["matched"] is True
        methods = [m for m, _ in t.calls]
        assert "Input.dispatchMouseEvent" in methods and "Input.insertText" in methods
        assert "Input.dispatchKeyEvent" in methods

    async def test_screenshot_of_a_target_never_activates_a_tab(self, monkeypatch):
        """look('chrome') must not switch Ellis's active tab (critique #17):
        the picture comes from CDP Page.captureScreenshot on the target."""
        import base64

        activated = []

        async def fake_call(method, params=None, *, timeout=None):
            activated.append(method)
            return {}
        monkeypatch.setattr(bx, "call", fake_call)

        class Shooter(_FakeTarget):
            async def send(self, method, params=None, *, timeout=None):
                if method == "Page.captureScreenshot":
                    self.calls.append((method, params))
                    return {"data": base64.b64encode(b"\xff\xd8jpeg").decode()}
                return await super().send(method, params, timeout=timeout)

        t = Shooter()
        data, url = await bh.screenshot_target(t)
        assert data == b"\xff\xd8jpeg" and url == "https://x/a"
        assert ("Page.captureScreenshot", {"format": "jpeg", "quality": 70}) in t.calls
        assert "tabs.activate" not in activated
