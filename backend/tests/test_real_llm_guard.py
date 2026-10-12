"""No test reaches the real model or reads Ellis's Codex login (2026-09-27).

Two batches of the chunk-1 stabilize work found tests that reached the real
world: one stubbed ``services.llm_utils.build_background_llm`` while the code
under test had imported the builder by name (``memory_service``), so its
consolidation cycle sent ~40 real requests to Codex on Ellis's weekly quota;
and every test that built ``ChatCodexResponses`` or asked the usage meter did
so through the auth singleton, which loaded the live ``~/.codex/auth.json`` —
a refresh there would have rotated the tokens ``codex exec`` uses.

``conftest`` now (a) refuses any request to an LLM provider at httpx's
transport — the seam ``ChatCodexResponses``, ``codex_usage_service``,
``codex_auth_service`` and the OpenAI SDK all go through — and fails the test
at teardown even when the code under test swallowed the error, unless the
test is marked ``real_llm``; and (b) points the Codex login at a throwaway
file with fake tokens and resets the auth singleton for every test.
"""

import os
from pathlib import Path

import httpcore
import httpx
import pytest

REAL_AUTH = (Path.home() / ".codex" / "auth.json").resolve()


class _PoolReached(Exception):
    """Raised by the fake connection pool: the request got past httpx's
    transport, i.e. it would have opened a socket."""


@pytest.fixture
def fake_pool(monkeypatch):
    """Stand in for httpcore's pool (below the transport) so a request the
    guard fails to stop is recorded here instead of leaving the machine."""
    reached = []

    def _url(request) -> str:
        u = request.url  # httpcore's URL: bytes parts
        port = f":{u.port}" if u.port else ""
        return f"{u.scheme.decode()}://{u.host.decode()}{port}{u.target.decode()}"

    async def fake_async(self, request):
        reached.append(_url(request))
        raise _PoolReached(_url(request))

    def fake_sync(self, request):
        reached.append(_url(request))
        raise _PoolReached(_url(request))

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", fake_async)
    monkeypatch.setattr(httpcore.ConnectionPool, "handle_request", fake_sync)
    return reached


@pytest.mark.parametrize(
    "url",
    [
        "https://chatgpt.com/backend-api/codex/responses",
        "https://chatgpt.com/backend-api/wham/usage",
        "https://auth.openai.com/oauth/token",
        "https://api.openai.com/v1/responses",
    ],
)
async def test_a_request_to_the_model_never_leaves_the_machine(url, fake_pool, llm_network_guard):
    with pytest.raises(Exception) as exc:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={})
    assert fake_pool == [], "the request got past the transport"
    assert "real_llm" in str(exc.value)
    assert [a["url"] for a in llm_network_guard.attempts] == [url]
    llm_network_guard.attempts.clear()


def test_a_sync_request_to_the_model_is_refused_too(fake_pool, llm_network_guard):
    with pytest.raises(Exception):
        httpx.post("https://api.openai.com/v1/chat/completions", json={})
    assert fake_pool == []
    assert len(llm_network_guard.attempts) == 1
    llm_network_guard.attempts.clear()


async def test_a_swallowed_attempt_is_still_recorded_for_teardown(fake_pool, llm_network_guard):
    """Background paths catch every exception and log it (extraction,
    consolidation, reflection); the guard's record is what fails the test
    at teardown when the refusal never surfaces."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post("https://chatgpt.com/backend-api/codex/responses", json={})
    except Exception:  # noqa: BLE001 — what the code under test does
        pass
    assert len(llm_network_guard.attempts) == 1
    llm_network_guard.attempts.clear()


async def test_chat_codex_responses_is_stopped_at_the_seam(fake_pool, llm_network_guard):
    """The real builder, the real model class, a fake login: the call gets
    as far as the transport and no further."""
    from langchain_core.messages import HumanMessage

    from services.llm_utils import _build_gpt_llm

    llm = _build_gpt_llm("gpt-5.6-terra", 0, 64, priority="triage")
    with pytest.raises(Exception):
        await llm.ainvoke([HumanMessage(content="hello")])
    assert fake_pool == []
    assert llm_network_guard.attempts, "ChatCodexResponses never reached the guarded seam"
    assert all("chatgpt.com" in a["url"] for a in llm_network_guard.attempts)
    llm_network_guard.attempts.clear()


async def test_other_hosts_are_not_the_guards_business(fake_pool, llm_network_guard):
    with pytest.raises(_PoolReached):
        async with httpx.AsyncClient() as client:
            await client.get("http://127.0.0.1:9/health")
    assert fake_pool == ["http://127.0.0.1:9/health"]
    assert llm_network_guard.attempts == []


@pytest.mark.real_llm
async def test_a_test_marked_real_llm_is_let_through(fake_pool):
    with pytest.raises(_PoolReached):
        async with httpx.AsyncClient() as client:
            await client.post("https://chatgpt.com/backend-api/codex/responses", json={})
    assert fake_pool == ["https://chatgpt.com/backend-api/codex/responses"]


def test_the_codex_login_is_a_throwaway_file():
    from services import codex_auth_service, secret_scan

    for path in (codex_auth_service.CODEX_AUTH_PATH, os.environ.get("CODEX_AUTH_PATH"),
                 secret_scan._codex_auth_path()):
        assert path
        assert Path(path).resolve() != REAL_AUTH
    assert Path(codex_auth_service.CODEX_AUTH_PATH).exists()


def test_the_auth_singleton_starts_fresh_from_the_fake_login():
    from services import codex_auth_service

    assert codex_auth_service._manager is None
    mgr = codex_auth_service.get_codex_auth_manager()
    assert mgr._auth_source == "codex_oauth"
    # Booleans, never the values: a failure here must not print a real token.
    jwt_shaped = (mgr.get_api_key() or "").startswith("eyJ")  # like the real one
    fake_refresh = "fake" in (mgr._refresh_token or "")
    assert jwt_shaped, "the access token is not the fake JWT"
    assert fake_refresh, "the refresh token is not the fake one"
    # A test that dirties the singleton does not hand it to the next test.
    mgr._access_token = "dirty"


def test_the_next_test_does_not_inherit_a_dirty_singleton():
    from services import codex_auth_service

    assert codex_auth_service._manager is None
    clean = codex_auth_service.get_codex_auth_manager().get_api_key() != "dirty"
    assert clean, "the previous test's singleton came back"


def test_the_secret_scan_knows_the_fake_tokens_not_owners():
    from services import secret_scan

    values = secret_scan.known_secrets()
    fake = "fake" in values.get("codex_refresh_token", "")
    assert fake, "the secret scan read a Codex login other than the fake one"
