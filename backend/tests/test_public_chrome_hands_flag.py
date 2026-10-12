"""EDWARD_CHROME_HANDS: Edward's hands in the owner's own Chrome are off by
default. Off, nothing serves the extension link, the ``chrome`` tool and
``look("chrome")`` answer a Tool error that says how to turn them on, and
nothing opens, raises, relaunches or quits Chrome. On, the link and the
screen-window plumbing work as before. EDWARD_CHROME_SILENT_DEBUGGER=on is
the only way Chrome is launched with ``--silent-debugger-extension-api``.

Nothing here reaches Chrome, osascript or a subprocess: the screen-window
module is loaded as a fresh copy (conftest replaces the real module's entry
points in every test) and its I/O seams are replaced with recorders."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from services import browser_ext_service as bx
from services import browser_hands as bh
from services.toolset._compat import ToolFailure

pytestmark = pytest.mark.unit

SW_PATH = Path(__file__).resolve().parents[1] / "services" / "screen_window_service.py"


@pytest.fixture
def hands_off(monkeypatch):
    monkeypatch.delenv("EDWARD_CHROME_HANDS", raising=False)


@pytest.fixture
def hands_on(monkeypatch):
    monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")


@pytest.fixture
def no_link(monkeypatch):
    monkeypatch.setattr(bx, "_link", None)
    monkeypatch.setattr(bx, "_disconnected_at", None)


class FakeWebSocket:
    def __init__(self, host="127.0.0.1"):
        self.client = type("C", (), {"host": host})()
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive_text(self):  # pragma: no cover - never reached while off
        raise AssertionError("an off link never reads")

    async def send_text(self, text):  # pragma: no cover
        raise AssertionError("an off link never sends")


def _fresh_screen_window(monkeypatch):
    """A private copy of screen_window_service whose Chrome-touching seams
    record instead of running (conftest patches the shared module's entry
    points to fakes, so the real gate is tested on this copy)."""
    spec = importlib.util.spec_from_file_location("_sw_under_test", SW_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    touched = []

    async def osascript(script, *, timeout=20):
        touched.append(("osascript", script[:40]))
        return 0, "found+here\n", ""

    async def run(*argv, timeout=10):
        touched.append(("run", argv))
        return 0

    async def ui_running():
        touched.append(("ps", "chrome"))
        return True

    async def renderers():
        touched.append(("ps", "renderers"))
        return 0

    monkeypatch.setattr(mod, "_osascript", osascript)
    monkeypatch.setattr(mod, "_run", run)
    monkeypatch.setattr(mod, "_chrome_ui_running", ui_running)
    monkeypatch.setattr(mod, "_chrome_renderers", renderers)
    return mod, touched


# ---- the switch ----------------------------------------------------------------------

def test_hands_are_off_unless_the_flag_says_on(monkeypatch):
    monkeypatch.delenv("EDWARD_CHROME_HANDS", raising=False)
    assert bh.enabled() is False and bx.hands_enabled() is False
    for value in ("off", "", "no", "false"):
        monkeypatch.setenv("EDWARD_CHROME_HANDS", value)
        assert bh.enabled() is False, value
    monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
    assert bh.enabled() is True


def test_start_is_a_no_op_while_off(hands_off, monkeypatch):
    monkeypatch.setattr(bh, "_started", False)
    monkeypatch.setattr(bx, "_listeners", [])
    bh.start()
    assert bh._started is False and bx._listeners == []


def test_start_listens_to_the_link_when_on(hands_on, monkeypatch):
    monkeypatch.setattr(bh, "_started", False)
    monkeypatch.setattr(bx, "_listeners", [])
    bh.start()
    assert bh._started is True and bx._listeners == [bh.on_event]


# ---- the link ------------------------------------------------------------------------

def test_an_off_link_refuses_every_call_even_with_a_socket(hands_off, monkeypatch):
    monkeypatch.setattr(bx, "_link", bx.ExtensionLink(FakeWebSocket()))
    assert bx.connected() is False
    with pytest.raises(bx.ChromeHandsOff, match="EDWARD_CHROME_HANDS=on"):
        asyncio.run(bx.call("tabs.open", {"url": "https://example.org"}))
    with pytest.raises(bx.ChromeHandsOff):
        asyncio.run(bx.tabs())
    st = bx.status()
    assert st["enabled"] is False and "EDWARD_CHROME_HANDS" in st["message"]
    # Off is not "down": the presence line must not claim his hands fell.
    assert "connected" not in st
    from services.wake_state import hands_view
    assert hands_view(st)["connected"] is None


def test_an_off_link_closes_the_socket_and_keeps_no_link(hands_off, no_link):
    ws = FakeWebSocket()
    asyncio.run(bx.serve(ws))
    assert ws.closed and ws.closed[0] == 4004
    assert bx._link is None


def test_the_router_refuses_the_extension_socket_while_off(hands_off, no_link):
    from routers import browser_ext

    ws = FakeWebSocket()
    asyncio.run(browser_ext.browser_extension_socket(ws))
    assert ws.accepted is False and ws.closed and ws.closed[0] == 4004


def test_no_recovery_is_scheduled_while_off(hands_off, monkeypatch):
    monkeypatch.setattr(bx, "_recovery_task", None)
    bx._schedule_recovery()
    assert bx._recovery_task is None


def test_the_hands_themselves_refuse_while_off(hands_off):
    with pytest.raises(bh.ChromeHandsOff):
        asyncio.run(bh.open_page("https://example.org"))
    with pytest.raises(bh.ChromeHandsOff):
        asyncio.run(bh.use_tab(3))
    with pytest.raises(bh.ChromeHandsOff):
        asyncio.run(bh.read_page())
    assert bh.status()["enabled"] is False
    assert bh.describe_hands(bh.status()).startswith("off")


# ---- the tools -----------------------------------------------------------------------

def test_the_chrome_tool_says_how_to_turn_the_hands_on(hands_off, monkeypatch):
    from services.toolset import browsers as bt

    calls = []

    async def never(*a, **k):  # pragma: no cover - must not run
        calls.append(a)
        raise AssertionError("an off chrome tool reached the hands")

    monkeypatch.setattr(bh, "open_page", never)
    monkeypatch.setattr(bx, "tabs", never)
    for args in ({"action": "tabs"}, {"action": "open", "url": "https://example.org"}, {"action": "done"}):
        with pytest.raises(ToolFailure) as err:
            asyncio.run(bt.chrome.ainvoke(args))
        assert str(err.value).startswith("Chrome hands are off")
        assert "EDWARD_CHROME_HANDS=on" in str(err.value) and "chrome-extension/" in str(err.value)
    assert calls == []


def test_the_chrome_tool_works_as_before_when_on(hands_on, monkeypatch):
    from services.toolset import browsers as bt

    monkeypatch.setattr(bx, "connected", lambda: True)

    async def tabs(*, refresh=True):
        return [{"id": 7, "title": "Docs", "url": "https://example.org/docs", "active": True}]

    monkeypatch.setattr(bx, "tabs", tabs)
    out = asyncio.run(bt.chrome.ainvoke({"action": "tabs"}))
    assert "[7] Docs" in out


def test_look_at_chrome_says_the_hands_are_off(hands_off):
    from services.toolset import look as lk

    with pytest.raises(ToolFailure, match="Chrome hands are off"):
        asyncio.run(lk._from_screen("chrome"))


# ---- the screen window ---------------------------------------------------------------

def test_nothing_touches_chrome_while_off(hands_off, monkeypatch):
    sw, touched = _fresh_screen_window(monkeypatch)
    res = asyncio.run(sw.ensure_screen_tab())
    assert res["ok"] is False and res.get("off") is True and "EDWARD_CHROME_HANDS" in res["error"]
    assert asyncio.run(sw.recover_hands())["action"] == "none"
    assert asyncio.run(sw._relaunch_on_this_space("http://localhost:3000/screen")) is False
    asyncio.run(sw._launch_chrome("http://localhost:3000/screen"))
    asyncio.run(sw._quit_chrome())

    async def in_loop():
        sw.ensure_in_background()
        await asyncio.sleep(0)

    asyncio.run(in_loop())
    assert touched == []


def test_the_screen_tab_is_brought_up_when_on(hands_on, monkeypatch):
    sw, touched = _fresh_screen_window(monkeypatch)
    res = asyncio.run(sw.ensure_screen_tab(url="http://localhost:3000/screen"))
    assert res["ok"] is True and res["opened"] is False
    assert any(kind == "osascript" for kind, _ in touched)


def test_the_debugging_bar_shows_unless_silenced(monkeypatch):
    sw, _ = _fresh_screen_window(monkeypatch)
    monkeypatch.delenv("EDWARD_CHROME_SILENT_DEBUGGER", raising=False)
    argv = sw.launch_argv("http://localhost:3000/screen")
    assert "--silent-debugger-extension-api" not in argv
    assert "--autoplay-policy=no-user-gesture-required" in argv
    monkeypatch.setenv("EDWARD_CHROME_SILENT_DEBUGGER", "on")
    assert "--silent-debugger-extension-api" in sw.launch_argv("http://localhost:3000/screen")


def test_the_screen_tab_is_recognised_on_the_configured_hosts(monkeypatch):
    sw, _ = _fresh_screen_window(monkeypatch)
    monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.org")
    monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
    assert sw.is_screen_url("https://edward.example.org/screen")
    assert sw.is_screen_url("https://www.edward.example.org/display")
    assert sw.is_screen_url("http://localhost:3000/screen")
    assert not sw.is_screen_url("https://example.com/screen")
    assert sw.public_screen_url() == "https://edward.example.org/screen"


# ---- the pulse -----------------------------------------------------------------------

def test_the_pulse_prompt_offers_chrome_only_with_the_hands():
    from services import expression_service as es

    off = es.build_expression_system_prompt(chrome_hands=False, people=("Sam", "Alex"))
    on = es.build_expression_system_prompt(chrome_hands=True, people=("Sam",))
    for needle in ("browser_open", "browser_fullscreen", "bring_up_screen"):
        assert needle not in off and needle in on, needle
    for p in (off, on):
        assert "compose_screen" in p and "say_on_screen" in p and 'show_on_screen(screen="mini")' in p
    assert "(Alex too)" in off and "too)" not in on.split("2. ")[1].split("\n")[0]


def test_the_pulse_signal_leaves_chrome_out_while_off(hands_off):
    from services import expression_service as es

    assert asyncio.run(es._browser_signal()) is None
