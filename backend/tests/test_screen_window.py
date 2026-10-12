"""services/screen_window_service.py — Edward's screen lives in Ellis's own
Chrome (2026-09-07, Ellis: "check if a browser is open with a tab on
edward.example.com/screen; if so make it full screen, else open a new tab — no
other Spaces, I'm on a mouse"). The AppleScript is built here; running it
is the I/O."""

import pytest

from services import host_config
from services import screen_window_service as sw

pytestmark = pytest.mark.unit


class TestUrls:
    def test_recognises_the_screen_tab_on_any_of_its_hosts(self, monkeypatch):
        # The hosts come from host_config: the public URL, localhost, this Mac's Bonjour name.
        monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.com")
        monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
        monkeypatch.setattr(host_config, "local_hostname", lambda: "my-mac.local")
        assert sw.is_screen_url("https://edward.example.com/screen")
        assert sw.is_screen_url("https://edward.example.com/screen?x=1")
        assert sw.is_screen_url("http://localhost:3000/screen")
        assert sw.is_screen_url("http://my-mac.local:3000/display")
        assert sw.is_screen_url("https://edward.example.com/fullscreen")
        assert not sw.is_screen_url("https://edward.example.com/chat")
        assert not sw.is_screen_url("https://www.youtube.com/screen")
        assert not sw.is_screen_url("")
        assert not sw.is_screen_url("http://someone-elses-mac.local:3000/screen")

    def test_with_nothing_configured_the_screen_is_on_localhost(self, monkeypatch):
        monkeypatch.delenv("EDWARD_PUBLIC_BASE_URL", raising=False)
        monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
        monkeypatch.setattr(host_config, "local_hostname", lambda: "my-mac.local")
        assert sw.is_screen_url("http://localhost:3000/screen")
        assert not sw.is_screen_url("https://edward.example.com/screen")


class TestScript:
    def test_script_finds_an_existing_tab_else_opens_one_and_fills_the_desktop(self):
        s = sw.ensure_script("https://edward.example.com/screen")
        assert 'tell application "Google Chrome"' in s
        assert "make new tab" in s and "https://edward.example.com/screen" in s
        assert "chrome://newtab/" in s  # a fresh launch's blank tab is reused, not left behind
        assert "bounds of window of desktop" in s          # size to the display, not a fullscreen Space
        assert "AXFullScreen" in s                          # and leave any fullscreen Space
        assert "activate" in s
        # A window on another Space is rebuilt here: Ellis cannot switch Spaces with a mouse.
        assert "count of windows of process" in s and "elsewhere" in s

    def test_result_line_is_parsed(self):
        assert sw.parse_result("found\n") == {"ok": True, "opened": False, "elsewhere": False, "moved": False}
        assert sw.parse_result("opened+here")["opened"] is True and sw.parse_result("opened+here")["elsewhere"] is False
        assert sw.parse_result("found+elsewhere")["elsewhere"] is True
        assert sw.parse_result("nope")["ok"] is False


class TestLaunch:
    def test_chrome_is_launched_with_autoplay_allowed(self):
        """Edward found and loaded a video but it sat on the play button:
        Chrome refuses audible autoplay on a page nobody has clicked. When
        Edward launches Chrome himself, autoplay is allowed."""
        argv = sw.launch_argv("https://edward.example.com/screen")
        assert argv[:3] == ["open", "-a", "Google Chrome"]
        assert "https://edward.example.com/screen" in argv
        assert "--args" in argv and "--autoplay-policy=no-user-gesture-required" in argv
        assert argv.index("--args") > argv.index("https://edward.example.com/screen")
        # branded Chrome ignores --load-extension; Ellis loads the extension once by hand
        assert not any(a.startswith("--load-extension=") for a in argv)
