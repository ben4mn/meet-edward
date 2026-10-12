"""Edward's hands must come back on their own when Chrome dies quietly.

2026-09-14: Chrome on the Mac relaunched itself window-less after a
background update, a short session at 14:02 closed its profile, and the
process lingered with zero renderers. The extension's service worker was
unloaded, so it could never reconnect; the backend saw "a Chrome process"
and never relaunched; Edward told Parker's thread nothing and went quiet.
"""

from __future__ import annotations

import pytest

from services import screen_window_service as sws
from services import browser_ext_service as ext


class TestChromeDecision:
    def test_a_running_chrome_with_no_renderers_and_a_dead_link_is_a_zombie(self):
        assert sws.chrome_needs_relaunch(process_running=True, renderers=0, link_connected=False, link_down_s=120) is True

    def test_a_fresh_disconnect_is_given_time_to_reconnect(self):
        assert sws.chrome_needs_relaunch(process_running=True, renderers=0, link_connected=False, link_down_s=20) is False

    def test_a_chrome_with_windows_is_left_alone_even_if_the_link_is_down(self):
        # Ellis may be using it; the extension may just need its reload arrow.
        assert sws.chrome_needs_relaunch(process_running=True, renderers=3, link_connected=False, link_down_s=600) is False

    def test_no_process_means_launch_not_relaunch(self):
        assert sws.chrome_needs_relaunch(process_running=False, renderers=0, link_connected=False, link_down_s=0) is False

    def test_connected_link_never_relaunches(self):
        assert sws.chrome_needs_relaunch(process_running=True, renderers=0, link_connected=True, link_down_s=0) is False


class TestExtensionStatus:
    def test_status_reports_how_long_the_link_has_been_down(self, monkeypatch):
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(ext, "_link", None)
        monkeypatch.setattr(ext, "_disconnected_at", 1000.0)
        monkeypatch.setattr(ext.time, "time", lambda: 1130.0)
        s = ext.status()
        assert s["connected"] is False and s["down_for_s"] == 130 and s["disconnected_at"] == 1000.0

    def test_status_before_any_connection(self, monkeypatch):
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(ext, "_link", None)
        monkeypatch.setattr(ext, "_disconnected_at", None)
        assert ext.status()["down_for_s"] is None

    def test_hands_switched_off_is_not_a_link_that_is_down(self, monkeypatch):
        # Off by default: no "connected" key, so no surface claims his hands fell.
        monkeypatch.delenv("EDWARD_CHROME_HANDS", raising=False)
        monkeypatch.setattr(ext, "_link", None)
        monkeypatch.setattr(ext, "_disconnected_at", 1000.0)
        s = ext.status()
        assert s["enabled"] is False and "connected" not in s and "down_for_s" not in s


class TestToolReplies:
    def test_extension_down_reply_is_a_counted_failure_that_tells_owner(self, monkeypatch):
        from services.graph.tools import _ext_reply
        from services.turn_ledger import classify_tool_result

        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        text = _ext_reply()
        assert text.startswith("Tool error:")
        assert classify_tool_result(text) is not None
        assert "tell ellis" in text.lower()
        assert "silently" in text.lower() or "silent" in text.lower()

    def test_hands_off_reply_is_a_counted_failure_too(self, monkeypatch):
        from services.graph.tools import _ext_reply
        from services.turn_ledger import classify_tool_result

        monkeypatch.delenv("EDWARD_CHROME_HANDS", raising=False)
        text = _ext_reply()
        assert text.startswith("Tool error:") and "EDWARD_CHROME_HANDS" in text
        assert classify_tool_result(text) is not None

    def test_no_tab_reply_is_a_counted_failure(self):
        from services.graph.tools import _no_tab_reply
        from services.turn_ledger import classify_tool_result

        assert classify_tool_result(_no_tab_reply()) is not None

