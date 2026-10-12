"""web_search and web_fetch (AREA 2, 2026-10-11; tools-spec §3.3–3.4).

Under Claude they are Anthropic's server tools in their basic versions —
``web_search_20250305`` everywhere, ``web_fetch_20250910`` on Opus and
Sonnet, a local web_fetch on Haiku 5.5 (the background route); never the
dynamic-filtering ``_20260209`` pair, whose direct calls Sonnet 5.5 wraps
in ``{"params": …}`` (2026-10-12) — and ``EDWARD_SERVER_WEB_TOOLS=off`` or the Codex
provider runs both locally: Brave for search, httpx + trafilatura for
fetch, with the three fixes (no Brave key needed to fetch, a 30K cap, no
brotli/zstd to choke on), real failures and an SSRF guard that also
covers redirects. No test reaches the network or a model."""

import httpx
import pytest
from langchain_core.messages import HumanMessage

from services import brave_search_service as brave
from services.toolset import web
from services.toolset._compat import ToolFailure

pytestmark = pytest.mark.unit


async def call(tool, **args):
    return await tool.ainvoke(args)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    from services import headless_browser as hb

    hb._dns_cache.clear()

    async def resolve(host):
        return ["93.184.216.34"]
    monkeypatch.setattr(hb, "_resolve", resolve)


# ---- the server tool per model ------------------------------------------------------

class TestServerSpec:
    def test_opus_and_sonnet_get_the_basic_server_search_and_fetch(self, monkeypatch):
        monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)
        monkeypatch.setattr(web, "_timezone", lambda: "America/Chicago")
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", None):
            s = web.server_tool_spec("web_search", model)
            assert s["type"] == "web_search_20250305" and s["name"] == "web_search", model
            assert s["max_uses"] == 8
            assert s["user_location"] == {"type": "approximate", "timezone": "America/Chicago", "country": "US"}
            f = web.server_tool_spec("web_fetch", model)
            assert f == {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10, "max_content_tokens": 20000}

    def test_no_model_is_sent_the_dynamic_web_tools(self, monkeypatch):
        """Ping 5 (2026-10-12): Sonnet 5.5 called the _20260209 tools
        directly with {"params": {"query": …}} and Anthropic answered
        invalid_tool_input — 0 of 9 well-formed on that request, 6 of 6
        with the basic versions."""
        monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1", None):
            for name in ("web_search", "web_fetch"):
                spec = web.server_tool_spec(name, model) or {}
                assert not str(spec.get("type", "")).endswith("_20260209"), (model, spec)

    def test_haiku_gets_basic_search_and_a_local_fetch(self, monkeypatch):
        monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)
        s = web.server_tool_spec("web_search", "claude-haiku-5-5")
        assert s["type"] == "web_search_20250305" and s["name"] == "web_search"
        assert web.server_tool_spec("web_fetch", "claude-haiku-5-5") is None

    def test_the_flag_turns_the_server_tools_off(self, monkeypatch):
        monkeypatch.setenv("EDWARD_SERVER_WEB_TOOLS", "off")
        assert web.server_tool_spec("web_search", "claude-opus-5-5") is None
        assert web.server_tool_spec("web_fetch", "claude-opus-5-5") is None

    def test_the_tools_name_their_resolver(self):
        for t in (web.web_search, web.web_fetch):
            assert t.metadata["anthropic_tool_resolver"] == "services.toolset.web:server_tool_spec"
            assert t.metadata["timeout_s"] == 45 and t.metadata["parallel_safe"] is True
            assert t.metadata["anthropic_tool"]["name"] == t.name


class TestChatClaudeBinding:
    """ChatClaude sends the server tool for a model that has it and the
    tool's own schema otherwise (the local implementation runs)."""

    def _bound(self, model, tools):
        from services.claude_chat_model import ChatClaude

        return ChatClaude(model=model, client=object()).bind_tools(tools).bound_tools

    def test_opus_binds_the_server_tools(self, monkeypatch):
        monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)
        bound = {t["name"]: t for t in self._bound("claude-opus-5-5", [web.web_search, web.web_fetch])}
        assert bound["web_search"]["type"] == "web_search_20250305"
        assert bound["web_fetch"]["type"] == "web_fetch_20250910"
        assert "input_schema" not in bound["web_search"]

    def test_haiku_binds_basic_search_and_a_custom_fetch(self, monkeypatch):
        monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)
        bound = {t["name"]: t for t in self._bound("claude-haiku-5-5", [web.web_search, web.web_fetch])}
        assert bound["web_search"]["type"] == "web_search_20250305"
        assert "input_schema" in bound["web_fetch"] and "type" not in bound["web_fetch"]

    def test_flag_off_binds_both_as_custom_tools(self, monkeypatch):
        monkeypatch.setenv("EDWARD_SERVER_WEB_TOOLS", "off")
        bound = {t["name"]: t for t in self._bound("claude-opus-5-5", [web.web_search, web.web_fetch])}
        assert "input_schema" in bound["web_search"] and "input_schema" in bound["web_fetch"]

    def test_a_raw_dynamic_dict_is_mapped_down_at_request_time(self):
        """Belt and braces: whoever binds the raw dict, Haiku never gets a
        version it rejects (the pulse and voice would 400 every turn), and
        no model gets the dynamic pair whose direct calls come back
        invalid_tool_input."""
        from services.claude_chat_model import ChatClaude

        llm = ChatClaude(model="claude-haiku-5-5", client=object()).bind_tools([
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10},
        ])
        kw = llm._request_kwargs([HumanMessage(content="hi")])
        types = {t.get("name"): t.get("type") for t in kw["tools"]}
        assert types == {"web_search": "web_search_20250305"}
        opus = ChatClaude(model="claude-opus-5-5", client=object()).bind_tools([
            {"type": "web_search_20260209", "name": "web_search"},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10}])
        types = {t.get("name"): t.get("type") for t in opus._request_kwargs([HumanMessage(content="hi")])["tools"]}
        assert types == {"web_search": "web_search_20250305", "web_fetch": "web_fetch_20250910"}


# ---- the local tools ---------------------------------------------------------------------

class TestLocalSearch:
    async def test_results_are_listed(self, monkeypatch):
        async def search(q, count=5, freshness=None):
            return [{"title": "Riverside Fest", "url": "https://riversidefest.example", "description": "Sept 18–20"}]
        monkeypatch.setattr(brave, "search", search)
        monkeypatch.setattr(brave, "is_configured", lambda: True)
        out = await call(web.web_search, query="riverside fest dates")
        assert "Riverside Fest" in out and "https://riversidefest.example" in out

    async def test_no_results_is_an_answer_not_an_error(self, monkeypatch):
        async def search(q, count=5, freshness=None):
            return []
        monkeypatch.setattr(brave, "search", search)
        monkeypatch.setattr(brave, "is_configured", lambda: True)
        assert "No results" in await call(web.web_search, query="zzzz")

    async def test_failures_raise(self, monkeypatch):
        monkeypatch.setattr(brave, "is_configured", lambda: False)
        with pytest.raises(ToolFailure, match="not configured"):
            await call(web.web_search, query="x")
        monkeypatch.setattr(brave, "is_configured", lambda: True)

        async def boom(q, count=5, freshness=None):
            raise httpx.ConnectError("no route")
        monkeypatch.setattr(brave, "search", boom)
        with pytest.raises(ToolFailure, match="ConnectError"):
            await call(web.web_search, query="x")


class TestLocalFetch:
    async def test_fetch_needs_no_brave_key(self, monkeypatch):
        monkeypatch.setattr(brave, "is_configured", lambda: False)

        async def fetch(url, max_chars=30000):
            return "The page text."
        monkeypatch.setattr(brave, "fetch_page", fetch)
        out = await call(web.web_fetch, url="https://example.com/a")
        assert "The page text." in out

    @pytest.mark.parametrize("url", ["file:///etc/hosts", "http://127.0.0.1:8000/api/memories",
                                     "http://localhost:3000", "http://169.254.169.254/latest/meta-data"])
    async def test_inside_and_non_web_urls_are_refused(self, url):
        with pytest.raises(ToolFailure):
            await call(web.web_fetch, url=url)


def _client_with(handler):
    def factory(**kw):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    return factory


HTML = ("<html><head><title>T</title></head><body><nav>menu</nav><article><h1>Heading</h1>"
        + "<p>" + ("Real article text. " * 40) + "</p></article></body></html>")


class TestBraveFetchPage:
    async def test_no_brotli_and_a_30k_cap(self, monkeypatch):
        seen = {}

        def handler(request):
            seen["enc"] = request.headers.get("accept-encoding")
            body = "<html><body><article><p>" + ("word " * 20000) + "</p></article></body></html>"
            return httpx.Response(200, html=body)
        monkeypatch.setattr(brave, "_client", _client_with(handler))
        text = await brave.fetch_page("https://example.com/long")
        assert seen["enc"] == "gzip, deflate"
        assert 29_000 <= len(text) <= 30_100 and text.endswith("[truncated]")

    async def test_extracts_the_article(self, monkeypatch):
        monkeypatch.setattr(brave, "_client", _client_with(lambda r: httpx.Response(200, html=HTML)))
        text = await brave.fetch_page("https://example.com/a")
        assert "Real article text." in text

    async def test_http_errors_raise_with_the_status(self, monkeypatch):
        monkeypatch.setattr(brave, "_client", _client_with(lambda r: httpx.Response(404, text="nope")))
        with pytest.raises(Exception, match="404"):
            await brave.fetch_page("https://example.com/missing")

    async def test_a_pdf_says_to_look(self, monkeypatch):
        monkeypatch.setattr(brave, "_client", _client_with(
            lambda r: httpx.Response(200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"})))
        with pytest.raises(Exception, match="look"):
            await brave.fetch_page("https://example.com/menu.pdf")

    async def test_a_redirect_inside_is_refused(self, monkeypatch):
        def handler(request):
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/api/memories"})
            raise AssertionError(f"followed the redirect to {request.url}")
        monkeypatch.setattr(brave, "_client", _client_with(handler))
        with pytest.raises(Exception, match="127.0.0.1"):
            await brave.fetch_page("https://example.com/r")

    async def test_nothing_readable_raises(self, monkeypatch):
        monkeypatch.setattr(brave, "_client", _client_with(
            lambda r: httpx.Response(200, html="<html><body><script>app()</script></body></html>")))
        with pytest.raises(Exception, match="browser"):
            await brave.fetch_page("https://example.com/spa")
