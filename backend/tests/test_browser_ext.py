"""Edward's hands in Ellis's own Chrome (2026-09-07, Ellis: "he can do anything
on the machine I can do — I open a tab and log in for him, he takes over").
A small extension in Chrome keeps a WebSocket to the backend and relays
tabs + DevTools commands; the protocol and the routing decisions are pure."""

import asyncio
import json

import pytest

from services import browser_ext_service as bx

pytestmark = pytest.mark.unit


class TestProtocol:
    def test_call_frames_carry_an_id_method_and_params(self):
        msg = json.loads(bx.encode_call(7, "tabs.open", {"url": "https://a.b", "active": True}))
        assert msg == {"type": "call", "id": 7, "method": "tabs.open", "params": {"url": "https://a.b", "active": True}}

    def test_decode_tolerates_junk_and_classifies(self):
        assert bx.decode_message("not json") is None
        assert bx.decode_message(json.dumps({"type": "result", "id": 3, "ok": True, "result": {"x": 1}}))["id"] == 3
        assert bx.decode_message(json.dumps({"type": "event", "name": "screencastFrame", "tabId": 5, "params": {}}))["type"] == "event"
        assert bx.decode_message(json.dumps({"nope": 1})) is None

    def test_format_tabs_is_compact_and_marks_the_screen_and_active_tabs(self, monkeypatch):
        # Edward's own screen is on the configured public host (host_config).
        monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.com")
        monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
        tabs = [
            {"id": 12, "title": "Edward - AI Assistant", "url": "https://edward.example.com/screen", "active": False},
            {"id": 13, "title": "Gmail - Inbox (3)", "url": "https://mail.google.com/mail/u/0/#inbox", "active": True},
            {"id": 14, "title": "", "url": "chrome://newtab/", "active": False},
        ]
        out = bx.format_tabs(tabs, working_tab_id=13)
        assert "[13] Gmail - Inbox (3) — mail.google.com/mail/u/0/#inbox (active, yours)" in out
        assert "[12] Edward - AI Assistant — edward.example.com/screen (your screen)" in out
        assert "[14]" in out and "new tab" in out


class TestLink:
    async def test_call_round_trips_over_a_fake_socket_and_times_out_cleanly(self):
        sent = []

        class FakeWs:
            async def send_text(self, text):
                sent.append(json.loads(text))

        link = bx.ExtensionLink(FakeWs())
        task = asyncio.create_task(link.call("tabs.list", {}, timeout=5))
        await asyncio.sleep(0)
        assert sent and sent[0]["method"] == "tabs.list"
        link.handle_message(json.dumps({"type": "result", "id": sent[0]["id"], "ok": True, "result": {"tabs": []}}))
        assert await task == {"tabs": []}

        task = asyncio.create_task(link.call("tabs.list", {}, timeout=0.05))
        with pytest.raises(bx.ExtensionUnavailable):
            await task

    async def test_error_results_raise(self):
        sent = []

        class FakeWs:
            async def send_text(self, text):
                sent.append(json.loads(text))

        link = bx.ExtensionLink(FakeWs())
        task = asyncio.create_task(link.call("attach", {"tabId": 9}, timeout=5))
        await asyncio.sleep(0)
        link.handle_message(json.dumps({"type": "result", "id": sent[0]["id"], "ok": False, "error": "Cannot attach to chrome:// URL"}))
        with pytest.raises(RuntimeError, match="chrome://"):
            await task


class TestTools:
    def test_chrome_tools_are_always_available(self):
        from services.graph.tools import BROWSER_TOOLS
        names = {t.name for t in BROWSER_TOOLS}
        assert {"browser_tabs", "browser_open", "browser_use_tab", "browser_find", "browser_done"} <= names

    async def test_browser_tabs_explains_when_the_extension_is_not_connected(self, monkeypatch):
        from services.graph import tools
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: False)
        out = await tools.browser_tabs.ainvoke({})
        assert "extension" in out.lower() and "chrome" in out.lower() and "not connected" in out.lower()
