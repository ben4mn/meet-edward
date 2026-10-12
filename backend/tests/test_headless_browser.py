"""Edward's own invisible browser (AREA 2, 2026-10-11): Playwright's bundled
headless shell, never Google Chrome; http(s) only; never the Mac's own
services (SSRF). Pure guards run always. The integration case launches a
real headless shell and only runs with EDWARD_TEST_BROWSER=1 — it never
touches the network (the page is set in place) or Ellis's Chrome."""

import os
from pathlib import Path

import pytest

from services import headless_browser as hb

pytestmark = pytest.mark.unit


# ---- schemes ------------------------------------------------------------------------

class TestSchemes:
    @pytest.mark.parametrize("url", [
        "file:///etc/hosts",
        "file:///Users/me/edward/.env",
        "chrome://settings",
        "view-source:https://example.com",
        "devtools://devtools/bundled/inspector.html",
        "blob:https://example.com/123",
        "data:text/html,<h1>x</h1>",
        "javascript:alert(1)",
        "about:config",
        "ftp://example.com/x",
        "",
        "https://",
    ])
    def test_only_http_and_https(self, url):
        with pytest.raises(hb.BrowserRefused):
            hb.check_url(url)

    def test_a_bare_host_gets_https(self):
        assert hb.check_url("example.com/a?b=1") == "https://example.com/a?b=1"
        assert hb.check_url("  http://example.com  ") == "http://example.com"


# ---- addresses ----------------------------------------------------------------------

class TestAddresses:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1", "127.8.9.10", "10.0.0.5", "192.168.1.20", "172.16.0.1", "169.254.169.254",
        "0.0.0.0", "100.64.0.1", "224.0.0.1", "255.255.255.255", "240.0.0.1",
        "::1", "fe80::1", "fc00::1", "::", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "ff02::1",
    ])
    def test_private_loopback_link_local_and_reserved_are_refused(self, ip):
        assert hb.blocked_address(ip)

    @pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
    def test_public_addresses_pass(self, ip):
        assert hb.blocked_address(ip) is None

    async def test_localhost_names_and_literals_are_refused_without_dns(self, monkeypatch):
        async def no_dns(host):
            raise AssertionError(f"resolved {host}")
        monkeypatch.setattr(hb, "_resolve", no_dns)
        for host in ("localhost", "LOCALHOST", "foo.localhost", "127.0.0.1", "[::1]", "::1", "my-mac.local"):
            assert await hb.host_refusal(host), host

    async def test_a_name_that_resolves_inside_is_refused(self, monkeypatch):
        hb._dns_cache.clear()

        async def resolve(host):
            return {"rebind.example": ["93.184.216.34", "127.0.0.1"], "fine.example": ["93.184.216.34"]}[host]
        monkeypatch.setattr(hb, "_resolve", resolve)
        assert "127.0.0.1" in (await hb.host_refusal("rebind.example"))
        assert await hb.host_refusal("fine.example") is None

    async def test_a_name_that_does_not_resolve_is_refused(self, monkeypatch):
        hb._dns_cache.clear()

        async def resolve(host):
            raise OSError("nodename nor servname provided")
        monkeypatch.setattr(hb, "_resolve", resolve)
        assert await hb.host_refusal("nowhere.invalid")

    async def test_url_refusal_combines_scheme_and_host(self, monkeypatch):
        hb._dns_cache.clear()

        async def resolve(host):
            return ["93.184.216.34"]
        monkeypatch.setattr(hb, "_resolve", resolve)
        assert await hb.url_refusal("https://example.com/x") is None
        assert await hb.url_refusal("file:///etc/passwd")
        assert await hb.url_refusal("http://127.0.0.1:8000/api/health/report")
        assert await hb.url_refusal("http://localhost:3000/today")


class TestRouteGuard:
    """Every request the page makes goes through the guard: a page on the
    web can't make the browser fetch the backend, Postgres or the
    extension socket on this Mac."""

    class _Route:
        def __init__(self, url):
            self.request = type("R", (), {"url": url})()
            self.done = None

        async def abort(self, code=None):
            self.done = ("abort", code)

        async def continue_(self):
            self.done = ("continue", None)

    async def test_private_requests_are_aborted_public_ones_continue(self, monkeypatch):
        hb._dns_cache.clear()

        async def resolve(host):
            return ["93.184.216.34"]
        monkeypatch.setattr(hb, "_resolve", resolve)
        bad = self._Route("http://127.0.0.1:8000/api/memories")
        await hb._route_guard(bad)
        assert bad.done[0] == "abort"
        good = self._Route("https://example.com/app.js")
        await hb._route_guard(good)
        assert good.done == ("continue", None)
        weird = self._Route("chrome-extension://abc/x.js")
        await hb._route_guard(weird)
        assert weird.done[0] == "abort"


# ---- the executable: Playwright's shell, never Google Chrome ---------------------------

class TestExecutable:
    def test_google_chrome_is_refused(self, tmp_path):
        cache = tmp_path / "ms-playwright"
        cache.mkdir()
        for bad in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
                    "/opt/homebrew/bin/chromium",
                    str(cache / ".." / "elsewhere" / "chrome-headless-shell")):
            with pytest.raises(hb.HeadlessUnavailable):
                hb.assert_playwright_executable(bad, cache_dir=cache)

    def test_a_shell_under_the_playwright_cache_is_fine(self, tmp_path):
        cache = tmp_path / "ms-playwright"
        exe = cache / "chromium_headless_shell-1208" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        assert hb.assert_playwright_executable(str(exe), cache_dir=cache) == exe.resolve()
        assert hb.find_headless_shell(cache) == exe

    def test_no_shell_installed_says_so(self, tmp_path):
        with pytest.raises(hb.HeadlessUnavailable, match="not installed"):
            hb.find_headless_shell(tmp_path)

    def test_launch_never_names_a_channel(self):
        src = Path(hb.__file__).read_text()
        assert "channel=" not in src


class TestDownloadsNames:
    def test_names_are_kept_inside_downloads(self, tmp_path):
        assert hb.safe_download_name("../../.ssh/id_rsa") == "id_rsa"
        assert hb.safe_download_name("") == "download"
        assert hb.safe_download_name("report 2026.pdf") == "report 2026.pdf"
        d = tmp_path
        (d / "a.pdf").write_text("x")
        assert hb.unique_path(d, "a.pdf").name == "a (1).pdf"


class TestNeverInTests:
    async def test_the_suite_cannot_launch_a_browser_by_accident(self):
        """conftest refuses the launch unless EDWARD_TEST_BROWSER=1."""
        if os.environ.get("EDWARD_TEST_BROWSER") == "1":
            pytest.skip("the integration run allows the launch")
        with pytest.raises(Exception, match="EDWARD_TEST_BROWSER"):
            await hb._launch()


# ---- integration (opt-in) ----------------------------------------------------------

browser_run = pytest.mark.skipif(os.environ.get("EDWARD_TEST_BROWSER") != "1",
                                 reason="launches a real headless shell; set EDWARD_TEST_BROWSER=1")


@browser_run
class TestHeadlessIntegration:
    async def test_read_click_type_and_screenshot_on_a_page_set_in_place(self, tmp_path, monkeypatch):
        from services import browser_hands as bh

        monkeypatch.setenv("EDWARD_HOME", str(tmp_path))
        try:
            page, target = await hb.page_for("test-conv")
            await page.set_content(
                "<html><head><title>Edward test</title></head><body><main>"
                "<h1>Hello</h1><input id=q placeholder='Search'>"
                "<button onclick=\"document.title='clicked:'+document.getElementById('q').value\">Go</button>"
                "</main></body></html>")
            text = await bh.read_page(target=target)
            assert "Edward test" in text and "Hello" in text
            assert "[1] input" in text and "[2] button Go" in text
            await bh.type_text("abc", ref=1, submit=False, target=target)
            await bh.click(2, target=target)
            assert (await page.title()) == "clicked:abc"
            shot = await hb.screenshot("test-conv")
            assert shot[:2] == b"\xff\xd8"
            with pytest.raises(hb.BrowserRefused):
                await hb.open_url("test-conv", "file:///etc/hosts")
        finally:
            await hb.shutdown()


class TestLifespan:
    def test_the_backend_closes_its_headless_browser_on_shutdown(self):
        """A Playwright headless shell left running after a kickstart would
        be an orphan Chromium on an 8 GB Mac; the lifespan's shutdown closes it."""
        src = (Path(hb.__file__).resolve().parents[1] / "main.py").read_text()
        tail = src.split("yield", 1)[1]
        assert "headless_browser" in tail and "await headless_browser.shutdown()" in tail
