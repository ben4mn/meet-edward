"""Links in a ping are read before the turn (pings-media-spec §3.6 P1b):
iMessage's own preview (LPLinkMetadata in chat.db, Appendix A9) first, then
the page through netguard — never a sign-in/confirm link, never a link from
a number Ellis doesn't know. No real network: guarded_get is faked."""

import plistlib

import pytest

from services import netguard
from services.media import linkmeta, links


def link_payload(url="https://example.com/a", title="Riverside Fest tickets", summary="Saturday resale", site="Example",
                 original="https://ex.co/a"):
    objects = ["$null",
               {"richLinkMetadata": plistlib.UID(2), "$class": plistlib.UID(10)},
               {"URL": plistlib.UID(3), "title": plistlib.UID(4), "summary": plistlib.UID(5),
                "siteName": plistlib.UID(6), "originalURL": plistlib.UID(7), "$class": plistlib.UID(10)},
               {"NS.relative": plistlib.UID(8), "$class": plistlib.UID(10)},
               title, summary, site,
               {"NS.relative": plistlib.UID(9), "$class": plistlib.UID(10)},
               url, original,
               {"$classname": "LPLinkMetadata", "$classes": ["LPLinkMetadata", "NSObject"]}]
    return plistlib.dumps({"$archiver": "NSKeyedArchiver", "$version": 100000, "$top": {"root": plistlib.UID(1)},
                           "$objects": objects}, fmt=plistlib.FMT_BINARY)


class TestLinkMeta:
    def test_a_url_balloon(self):
        meta = linkmeta.parse_link_payload(link_payload())
        assert meta == {"url": "https://example.com/a", "original_url": "https://ex.co/a", "title": "Riverside Fest tickets",
                        "summary": "Saturday resale", "site": "Example"}

    @pytest.mark.parametrize("blob", [None, b"", b"not a plist", plistlib.dumps({"a": 1})])
    def test_unreadable_is_none(self, blob):
        assert linkmeta.parse_link_payload(blob) is None


class TestUrls:
    def test_found_in_text_without_trailing_punctuation(self):
        text = "look at https://example.com/a, and (https://example.org/b). also https://example.com/a!"
        assert links.urls_in(text) == ["https://example.com/a", "https://example.org/b"]

    def test_at_most_three(self):
        text = " ".join(f"https://example.com/{i}" for i in range(6))
        assert len(links.urls_in(text)) == 3

    @pytest.mark.parametrize("url", ["https://site.com/verify?code=123", "https://x.com/login", "https://x.com/a?token=abc",
                                     "https://x.com/unsubscribe/1", "https://x.com/reset-password",
                                     "https://x.com/magic/link", "https://x.com/rsvp?id=1", "https://x.com/oauth/authorize"])
    def test_sign_in_and_confirm_links_are_never_opened(self, url):
        assert links.never_fetch(url)

    @pytest.mark.parametrize("url", ["https://tickets.example/riverside-fest", "https://example.com/a?q=tickets"])
    def test_ordinary_links_are_read(self, url):
        assert not links.never_fetch(url)


@pytest.fixture
def web(monkeypatch):
    pages, calls = {}, []

    async def fake_get(url, *, max_bytes, timeout_s, **kw):
        calls.append(url)
        got = pages.get(url)
        if isinstance(got, Exception):
            raise got
        if got is None:
            raise netguard.BlockedURL("example.invalid did not resolve")
        return got

    monkeypatch.setattr(netguard, "guarded_get", fake_get)
    monkeypatch.setattr(links, "_payload_for", lambda rowid: None)
    return pages, calls


def _page(url, html, ctype="text/html; charset=utf-8"):
    return netguard.Fetched(status=200, final_url=url, headers={"content-type": ctype},
                            content=html.encode("utf-8"), truncated=False)


ARTICLE = ("<html><head><title>Riverside Fest Saturday tickets</title></head><body><nav>Home | Login</nav>"
           "<article><h1>Riverside Fest Saturday</h1><p>Resale tickets for Saturday start at $142 and go up "
           "to $310 for GA+. Prices updated hourly from verified sellers.</p><p>The festival runs all weekend at "
           "Riverside Park in Springfield, with gates opening at 11 am each day.</p></article></body></html>")


@pytest.mark.asyncio
class TestReadLinks:
    async def test_a_page_is_read(self, web):
        pages, _ = web
        pages["https://example.com/fest"] = _page("https://example.com/fest", ARTICLE)
        [got] = await links.read_links("@edward cheapest? https://example.com/fest")
        assert got["url"] == "https://example.com/fest" and got["title"] == "Riverside Fest Saturday tickets"
        assert "$142" in got["text"] and "Login" not in got["text"]

    async def test_the_preview_comes_first_and_survives_a_failed_fetch(self, web, monkeypatch):
        monkeypatch.setattr(links, "_payload_for", lambda rowid: link_payload(url="https://example.com/a"))
        [got] = await links.read_links("https://example.com/a", message_rowid=42)
        assert got["title"] == "Riverside Fest tickets" and got["site"] == "Example" and got["summary"] == "Saturday resale"
        assert "error" not in got or got.get("text") is None

    async def test_a_preview_without_the_url_in_the_text(self, web, monkeypatch):
        monkeypatch.setattr(links, "_payload_for", lambda rowid: link_payload(url="https://example.com/z"))
        out = await links.read_links("", message_rowid=42)
        assert out[0]["url"] == "https://example.com/z"

    async def test_a_sign_in_link_is_not_opened(self, web):
        _, calls = web
        [got] = await links.read_links("confirm here https://site.com/verify?code=123456")
        assert calls == [] and "didn't open" in got["note"]

    async def test_an_unknown_sender_gets_no_fetch(self, web):
        pages, calls = web
        pages["https://example.com/fest"] = _page("https://example.com/fest", ARTICLE)
        [got] = await links.read_links("https://example.com/fest", sender_known=False)
        assert calls == [] and "didn't open" in got["note"]

    async def test_a_blocked_link_says_why(self, web):
        [got] = await links.read_links("http://localhost:5432/")
        assert got["error"].startswith("not a public") or "public" in got["error"]

    async def test_a_failure_names_only_its_class(self, web):
        pages, _ = web
        pages["https://example.com/slow"] = TimeoutError("connect to 10.0.0.1 timed out with token=abc")
        [got] = await links.read_links("https://example.com/slow")
        assert got["error"] == "couldn't load it (TimeoutError)"

    async def test_a_non_page_is_named(self, web):
        pages, _ = web
        pages["https://example.com/x.pdf"] = _page("https://example.com/x.pdf", "%PDF-1.4", ctype="application/pdf")
        [got] = await links.read_links("https://example.com/x.pdf")
        assert "application/pdf" in got["note"] and not got.get("text")

    async def test_the_page_text_is_capped(self, web):
        pages, _ = web
        long = "<html><body><article>" + "".join(f"<p>Sentence number {i} about tickets.</p>" for i in range(2000)) + "</article></body></html>"
        pages["https://example.com/long"] = _page("https://example.com/long", long)
        [got] = await links.read_links("https://example.com/long")
        assert len(got["text"]) <= links.TEXT_MAX + 1

    async def test_off_reads_nothing(self, web, monkeypatch):
        monkeypatch.setenv("EDWARD_MEDIA", "off")
        assert await links.read_links("https://example.com/fest") == []
