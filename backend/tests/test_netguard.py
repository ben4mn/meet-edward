"""services/netguard.py — a link someone texts Edward is fetched only from the
public internet: never this Mac, the LAN, the tailnet or a link-local
metadata address, checked on every redirect (pings-media-spec Appendix A8).
No real network: DNS is patched and httpx runs on a MockTransport."""

import httpx
import pytest

from services import netguard


class TestPublicIp:
    @pytest.mark.parametrize("ip", ["8.8.8.8", "::ffff:8.8.8.8", "2606:4700::1111"])
    def test_public(self, ip):
        assert netguard.is_public_ip(ip)

    @pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "100.100.1.1",
                                    "::1", "::ffff:127.0.0.1", "fc00::1", "fe80::1", "64:ff9b::7f00:1",
                                    "0.0.0.0", "224.0.0.1", "not-an-ip"])
    def test_refused(self, ip):
        assert not netguard.is_public_ip(ip)


class TestShape:
    @pytest.mark.parametrize("url", ["http://localhost:5432/", "https://my-mac.local/", "ftp://example.com/x",
                                     "http://user:pw@example.com/", "https://intranet/", "javascript:alert(1)", ""])
    def test_blocked(self, url):
        with pytest.raises(netguard.BlockedURL):
            netguard.check_url_shape(url)

    def test_ok(self):
        assert netguard.check_url_shape("http://example.com/") == ("example.com", 80)
        assert netguard.check_url_shape("https://example.com:8443/a") == ("example.com", 8443)


@pytest.fixture
def dns(monkeypatch):
    table = {"example.com": ["93.184.216.34"], "cdn.example.com": ["93.184.216.35"],
             "evil.example": ["127.0.0.1"], "rebind.example": ["93.184.216.36", "10.0.0.5"]}

    async def fake_resolve(host, port):
        return table.get(host, [])

    monkeypatch.setattr(netguard, "resolve", fake_resolve)
    return table


@pytest.mark.asyncio
class TestAssertPublic:
    async def test_a_public_host(self, dns):
        await netguard.assert_public_url("https://example.com/a")

    async def test_a_host_that_resolves_private(self, dns):
        with pytest.raises(netguard.BlockedURL, match="private address"):
            await netguard.assert_public_url("https://evil.example/")

    async def test_any_private_address_blocks(self, dns):
        with pytest.raises(netguard.BlockedURL):
            await netguard.assert_public_url("https://rebind.example/")

    async def test_unresolvable(self, dns):
        with pytest.raises(netguard.BlockedURL):
            await netguard.assert_public_url("https://nowhere.example/")

    @pytest.mark.parametrize("url", ["http://2130706433/", "http://0x7f000001/", "http://127.0.0.1:8000/api"])
    async def test_numeric_tricks(self, url, monkeypatch):
        async def real_like(host, port):
            import ipaddress
            try:
                return [str(ipaddress.ip_address(int(host, 0)))]
            except ValueError:
                return [host]
        monkeypatch.setattr(netguard, "resolve", real_like)
        with pytest.raises(netguard.BlockedURL):
            await netguard.assert_public_url(url)


@pytest.mark.asyncio
class TestGuardedGet:
    async def test_a_page(self, dns):
        def handler(request):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<title>Hi</title>hello")

        got = await netguard.guarded_get("https://example.com/a", max_bytes=1000, timeout_s=5,
                                         transport=httpx.MockTransport(handler))
        assert got.status == 200 and got.content.endswith(b"hello") and not got.truncated
        assert got.final_url == "https://example.com/a" and got.headers["content-type"] == "text/html"

    async def test_a_redirect_to_a_private_host_is_blocked(self, dns):
        def handler(request):
            return httpx.Response(302, headers={"location": "http://evil.example/admin"})

        with pytest.raises(netguard.BlockedURL):
            await netguard.guarded_get("https://example.com/r", max_bytes=1000, timeout_s=5,
                                       transport=httpx.MockTransport(handler))

    async def test_a_public_redirect_is_followed(self, dns):
        def handler(request):
            if request.url.host == "example.com":
                return httpx.Response(301, headers={"location": "https://cdn.example.com/b"})
            return httpx.Response(200, content=b"ok")

        got = await netguard.guarded_get("https://example.com/r", max_bytes=1000, timeout_s=5,
                                         transport=httpx.MockTransport(handler))
        assert got.final_url == "https://cdn.example.com/b" and got.content == b"ok"

    async def test_too_many_redirects(self, dns):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://example.com/again"})

        with pytest.raises(netguard.BlockedURL, match="redirect"):
            await netguard.guarded_get("https://example.com/r", max_bytes=1000, timeout_s=5, max_redirects=2,
                                       transport=httpx.MockTransport(handler))

    async def test_the_body_is_capped(self, dns):
        def handler(request):
            return httpx.Response(200, content=b"x" * 5000)

        got = await netguard.guarded_get("https://example.com/big", max_bytes=1000, timeout_s=5,
                                         transport=httpx.MockTransport(handler))
        assert len(got.content) == 1000 and got.truncated

    async def test_the_first_url_is_checked_before_any_request(self, dns):
        seen = []

        def handler(request):
            seen.append(request.url)
            return httpx.Response(200)

        with pytest.raises(netguard.BlockedURL):
            await netguard.guarded_get("http://localhost:5432/", max_bytes=10, timeout_s=5,
                                       transport=httpx.MockTransport(handler))
        assert seen == []
