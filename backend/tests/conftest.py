"""
Test fixtures for Edward backend.

Strategy:
- Most tests are unit tests that mock I/O (LLM calls, DB, chat.db).
- Integration tests marked with @pytest.mark.integration use a live test
  Postgres DB (edward_test on the test cluster, :5433 — see
  ``scripts/test-pg-cluster.sh``). Skipped automatically if the DB isn't
  reachable.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

# The owner in tests is a fictional person. Set before any service imports:
# model-facing text renders the owner's name once, at import
# (services/owner.py).
os.environ["EDWARD_OWNER_NAME"] = "Ellis"
os.environ["EDWARD_OWNER_FULL_NAME"] = "Ellis Reed"
os.environ.pop("EDWARD_OWNER_ALIASES", None)
os.environ.pop("EDWARD_HOUSEHOLD", None)

# Make `services.*` imports resolve when running pytest from the backend dir.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Route tests to a dedicated test DB. Tests that touch Postgres will skip if
# this URL can't be reached.
#
# NEVER trust an inherited DATABASE_URL. On 2026-09-07 an evolution cycle
# ran this suite from the backend's own environment (DATABASE_URL = the
# live ``edward`` database); a ``setdefault`` here left it alone and
# ``clean_db`` truncated memories, conversations, settings, scheduled
# events — nearly everything — on the production database. Only a URL
# whose database name ends in ``_test`` is ever used.
#
# And never on the production cluster: edward_test lives on its own
# Postgres cluster on :5433 with fsync off (``scripts/test-pg-cluster.sh``).
# A full run on :5432 stalled production's checkpointer and exhausted the
# live backend's pool (2026-09-26 21:31–21:40, and on six earlier days).
# A test database named on :5432 is refused outright rather than
# redirected, so a run that meant a particular database fails loudly.
from services.sandbox_env import (  # noqa: E402
    is_test_db_url,
    refuse_production_cluster,
    safe_test_database_url,
)

for _var in ("DATABASE_URL", "TEST_DATABASE_URL"):
    _inherited = os.environ.get(_var) or ""
    if _inherited.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
        refuse_production_cluster(_inherited)

TEST_DATABASE_URL = safe_test_database_url(os.environ.get("DATABASE_URL"))
refuse_production_cluster(TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["TEST_DATABASE_URL"] = TEST_DATABASE_URL

# Kuzu is single-writer and the graph-worker LaunchAgent owns the live
# backend/storage/graphiti.kuzu exclusively. Any test code that resolves
# the Graphiti DB path must land on a throwaway file, never the production
# one — a second process opening the live file can corrupt it.
_graphiti_test_dir = tempfile.mkdtemp(prefix="edward-test-graphiti-")
os.environ["GRAPHITI_DB_PATH"] = os.path.join(_graphiti_test_dir, "graphiti.kuzu")

# The Graphiti dual-write is off for the whole run. ``main.py``,
# ``services/voice_service.py`` and ``workers/graph_worker.py`` load the
# repo's production ``.env`` at import, and ``load_dotenv`` only fills
# variables that are unset: before this pin, the first test file to import
# ``main`` turned ``GRAPHITI_ENABLED=true`` on for every later test, so each
# ``store_memory`` queued a ``graph_ingest_queue`` row behind the test's back
# and ``graph_memory_service.search`` would ask the live worker on :8001
# (2026-09-25: test_forget_service passed alone, failed in the full suite).
# A test that wants it says so with ``monkeypatch.setenv``.
os.environ["GRAPHITI_ENABLED"] = "false"
os.environ["GRAPHITI_INGEST_ENABLED"] = "false"

# The same for the model provider (2026-10-11): with
# ``EDWARD_LLM_PROVIDER=anthropic`` in the live ``.env``, the first test to
# import ``main`` turned every later builder into ChatClaude and the surplus
# queue off, and the routing, mind and Codex-adapter tests failed by test
# order. The suite runs on Codex; a test that wants Claude says so with
# ``monkeypatch.setenv``. The live Anthropic key is blanked so ``load_dotenv``
# cannot bring it in (anthropic.com is refused at the transport regardless).
os.environ["EDWARD_LLM_PROVIDER"] = "codex"
os.environ["ANTHROPIC_API_KEY"] = ""
# Ellis's Gmail OAuth client file (``~/.config/edward``) is never read by a
# test: ``load_dotenv`` would bring its path in (2026-10-11).
os.environ["GMAIL_CLIENT_SECRETS_FILE"] = ""

# Ellis's Codex login is never read by a test (2026-09-27). The auth singleton
# used to load the live ``~/.codex/auth.json`` for any test that built
# ``ChatCodexResponses`` or asked the usage meter, and a refresh from there
# rotates the pair ``codex exec`` uses. ``codex_auth_service`` reads this at
# import and ``secret_scan`` per call; ``_fake_codex_login`` re-points both
# and writes the fake tokens for every test.
_codex_test_dir = tempfile.mkdtemp(prefix="edward-test-codex-")
os.environ["CODEX_AUTH_PATH"] = os.path.join(_codex_test_dir, "auth.json")


async def _engine_reachable(url: str) -> bool:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text

        eng = create_async_engine(url, pool_pre_ping=True)
        try:
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await eng.dispose()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def db_available() -> bool:
    """True if the test Postgres DB is reachable. Used to skip integration tests."""
    return await _engine_reachable(TEST_DATABASE_URL)


@pytest_asyncio.fixture
async def clean_db(db_available):
    """Truncate all tables before each integration test. Skips if DB unavailable.

    The schema is built once per session; each test gets truncated tables.
    The app-wide engine is disposed after the test so asyncpg doesn't hold
    connections across the pytest-asyncio per-test event loop boundary.
    """
    if not db_available:
        pytest.skip("Test database not reachable")

    from services import database
    from sqlalchemy import text

    # Belt and braces: whatever the engine was built from, refuse to
    # truncate anything that is not a test database.
    if not is_test_db_url(str(database.DATABASE_URL)):
        raise RuntimeError(
            f"clean_db refuses to truncate a non-test database: {database.DATABASE_URL}"
        )

    # A test that ran its own loop (asyncio.run, a sync helper) can leave
    # pooled connections bound to that closed loop; the next clean_db then
    # fails at setup with "attached to a different loop". Drop the pool
    # without closing those connections on this loop (2026-09-24; it was
    # the source of the order-dependent setup errors in the full suite).
    await database.engine.dispose(close=False)

    # Build the schema once per session (it is idempotent but slow: ~150
    # CREATE/ALTER IF NOT EXISTS per call, before each of ~4,800 tests). A
    # test that drops one of the app's tables gets it back through the
    # retry below.
    global INIT_DB_RUNS
    if INIT_DB_RUNS == 0:
        await database.init_db()
        INIT_DB_RUNS += 1

    try:
        await _truncate_all(database, text)
    except Exception:
        await database.engine.dispose(close=False)
        await database.init_db()
        INIT_DB_RUNS += 1
        await _truncate_all(database, text)
    yield
    # Dispose the engine so asyncpg releases connections before the per-test
    # event loop closes (avoids "Event loop is closed" during teardown).
    await database.engine.dispose()


INIT_DB_RUNS = 0


async def _truncate_all(database, text) -> None:
    """Truncate every table a test may have written."""
    async with database.engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE "
                "memories, heartbeat_events, heartbeat_config, llm_rate_gate_state, memory_connections, memory_flags, "
                "memory_enrichments, consolidation_cycles, consolidation_config, self_observations, self_observation_hits, turn_log, "
                "user_signals, ledger_events, noticings, codex_calls, llm_meter_samples, llm_calls, pending_approvals, wishlist_items, external_contacts, scheduled_events, contact_identity_aliases, "
                "imessage_outgoing_sends, imessage_chat_conversations, heartbeat_false_dismissal_signals, heartbeat_chat_backoffs, heartbeat_adaptive_dismissal_counters, heartbeat_contact_affinity, "
                "heartbeat_contact_triage_calibration, heartbeat_dismiss_calibration_profiles, heartbeat_contact_shorthand_profiles, heartbeat_contact_exemplars, heartbeat_contact_topic_affinity, heartbeat_sender_reply_after_dismiss, "
                "heartbeat_service_interaction_profiles, coordination_thread_state, coordination_cases, admin_obligation_state, live_conversation_sessions, conversation_momentum_records, "
                "action_episode_tracker, skills, skills_audit, chat_standdowns, push_subscriptions, documents, "
                "communication_preference_records, triage_decisions, presence_beacons, widget_tokens, widget_state, interrupt_deliveries, "
                "hub_canvas, push_log, edward_notes, heartbeat_feedback, triage_results, conversations, "
                "entities, entity_mentions, graph_ingest_queue, pings, media_items, "
                "settings RESTART IDENTITY CASCADE"
            )
        )


class FakeBackgroundLLM:
    """Drop-in replacement for ``build_background_llm()`` output.

    Scripts a sequence of responses. Used to simulate GPT-5.4 returning
    various JSON shapes (clean array, dict-wrapped, preambled, malformed).
    """

    def __init__(self, scripted_responses):
        self._responses = list(scripted_responses)
        self.calls = []  # Record every ainvoke call for assertions

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("FakeBackgroundLLM exhausted — no more scripted responses")
        content = self._responses.pop(0)

        class _Response:
            def __init__(self, c):
                self.content = c

        return _Response(content)


@pytest.fixture(autouse=True)
def _clear_dream_cache():
    """Reset the DreamContext shared query cache before every test so
    messages reused across tests don't accidentally pick up each other's
    LLM stub output."""
    try:
        from services.dream import context as _dream_ctx

        _dream_ctx._cache.clear()
        _dream_ctx._inflight.clear()
    except Exception:
        pass
    yield


@pytest.fixture
def fake_llm_factory(monkeypatch):
    """Patch ``build_background_llm`` to return a FakeBackgroundLLM.

    Usage:
        def test_foo(fake_llm_factory):
            llm = fake_llm_factory(['[{"content": "x", "memory_type": "fact"}]'])
            ...
    """
    instances = []

    def _factory(responses):
        fake = FakeBackgroundLLM(responses)
        instances.append(fake)

        def _build(*args, **kwargs):
            return fake

        monkeypatch.setattr("services.llm_utils.build_background_llm", _build)
        return fake

    return _factory


@pytest.fixture(autouse=True)
def _never_touch_chrome(monkeypatch):
    """screen_window_service drives Ellis's real Chrome over AppleScript. A
    tool test once launched Chrome and hung on the Automation prompt
    (2026-09-07); no test may reach osascript."""
    try:
        from services import screen_window_service as sw
    except Exception:  # noqa: BLE001
        return

    async def fake_ensure(*, url=None):
        return {"ok": True, "opened": False, "faked": True}

    monkeypatch.setattr(sw, "ensure_screen_tab", fake_ensure)

    async def fake_recover(*, url=None):
        return {"action": "none", "reason": "tests never touch Chrome"}

    monkeypatch.setattr(sw, "recover_hands", fake_recover)
    monkeypatch.setattr(sw, "ensure_in_background", lambda *a, **k: None)
    _never_look_or_launch(monkeypatch)
    # Ellis's real calendar database is readable from a terminal with Full
    # Disk Access; no test reads it (test_calendar_service points the
    # reader at a fixture and turns it back on).
    monkeypatch.setenv("EDWARD_CALENDAR_SQLITE", "off")
    monkeypatch.setenv("EDWARD_REMINDERS_SQLITE", "off")
    monkeypatch.setenv("EDWARD_EVENTKIT", os.path.join(tempfile.gettempdir(), "edward-tests-no-eventkit"))
    _never_reach_eventkit(monkeypatch)


def _never_reach_eventkit(monkeypatch):
    """Once build_eventkit.sh has run on the mini the helper's default path
    exists, so the env var above is only the first guard: ``_run`` itself
    refuses unless the test patched it or ``subprocess.run`` (review
    2026-10-11) — a forgotten stub must never write Ellis's calendar."""
    try:
        from services import eventkit_service as ek
    except Exception:  # noqa: BLE001
        return
    real = ek._run

    def _refuse_real_helper(command, payload=None, timeout_s=ek.RUN_TIMEOUT_S):
        if ek.available() and ek.subprocess.run is _REAL_SUBPROCESS_RUN:
            raise RuntimeError("tests never reach EventKit: patch eventkit_service._run")
        return real(command, payload, timeout_s=timeout_s)

    monkeypatch.setattr(ek, "_run", _refuse_real_helper)


def _never_look_or_launch(monkeypatch):
    """No test photographs the Mac's display or Ellis's Chrome tab (``look``
    reaches both since 2026-10-11), and none launches the headless browser
    unless EDWARD_TEST_BROWSER=1 asks for the integration run."""
    try:
        from services import browser_hands as bh
    except Exception:  # noqa: BLE001
        bh = None
    if bh is not None:
        async def no_display(*a, **k):
            raise RuntimeError("tests never capture the Mac's display (screencapture)")

        async def no_tab(*a, **k):
            raise RuntimeError("tests never capture Ellis's Chrome")

        monkeypatch.setattr(bh, "screenshot_display", no_display)
        monkeypatch.setattr(bh, "screenshot_tab", no_tab)
    if os.environ.get("EDWARD_TEST_BROWSER") == "1":
        return
    try:
        from services import headless_browser as hb
    except Exception:  # noqa: BLE001
        return

    async def no_launch(*a, **k):
        raise RuntimeError("tests never launch the headless browser (set EDWARD_TEST_BROWSER=1 for the integration run)")

    monkeypatch.setattr(hb, "_launch", no_launch)


# ── No test ever sends a real message (2026-10-11) ────────────────────────
#
# Under the toolset the taint approval hold is gone from the send path, so
# nothing between a test and Ellis's Messages but the patches each test makes.
# ``imessage_service`` calls ``osascript`` through ``subprocess`` directly;
# a ``Shortcuts`` shortcut can send from Ellis's account too. Any subprocess
# whose program is one of these raises here, whatever a test forgot to
# patch (critique #1). A test that fakes the transport patches it first and
# never reaches this.
_NEVER_RUN_IN_TESTS = frozenset({"osascript", "shortcuts"})


def _program_of(args) -> str:
    if isinstance(args, (list, tuple)) and args:
        return os.path.basename(str(args[0]))
    if isinstance(args, (str, bytes)):
        text = args.decode() if isinstance(args, bytes) else args
        parts = text.split()
        return os.path.basename(parts[0]) if parts else ""
    return os.path.basename(str(args or ""))


@pytest.fixture(autouse=True)
def _never_run_osascript(monkeypatch):
    import subprocess

    real_popen = subprocess.Popen

    class _GuardedPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            if _program_of(args) in _NEVER_RUN_IN_TESTS:
                raise RuntimeError(f"tests never run {_program_of(args)} (it can message real people)")
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)


import subprocess as _subprocess  # noqa: E402

_REAL_SUBPROCESS_RUN = _subprocess.run


@pytest.fixture(autouse=True)
def _never_reach_messages(monkeypatch):
    """No test sends a real iMessage or reads Ellis's chat.db (AREA 3,
    2026-10-11). The transport's one way to osascript
    (``imessage_service._run_osascript``) refuses unless the test patched it
    or ``subprocess.run``; chat.db points at a file that doesn't exist until
    a test points it at its own (``FakeChatDB.point_listener``). The send
    path reads chat.db back after every send, so without this a test that
    faked osascript would still read Ellis's messages."""
    try:
        from services import imessage_service as imsg
        from services.heartbeat import listener_imessage
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(listener_imessage, "CHAT_DB_PATH",
                        os.path.join(tempfile.gettempdir(), "edward-tests-no-chat-db", "chat.db"))
    # Nor Ellis's address book (read first since 2026-10-11), nor Contacts.app:
    # with no store the lookups fall back to AppleScript, which first runs
    # ``open -ga Contacts`` — a no-op here unless the test faked subprocess.run.
    monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR",
                       os.path.join(tempfile.gettempdir(), "edward-tests-no-addressbook"))
    try:
        from services import contacts_service as _contacts
    except Exception:  # noqa: BLE001
        _contacts = None
    if _contacts is not None:
        _real_wake = _contacts._ensure_contacts_running

        def _no_real_wake():
            if _contacts.subprocess.run is not _REAL_SUBPROCESS_RUN:
                _real_wake()

        monkeypatch.setattr(_contacts, "_ensure_contacts_running", _no_real_wake)
    # Nor Twilio: the Settings rows ask the account (2026-10-11), and the live
    # .env's credentials reach a test that imported main first.
    try:
        from services import twilio_service as _twilio
    except Exception:  # noqa: BLE001
        _twilio = None
    if _twilio is not None and hasattr(_twilio, "_fetch_account"):
        async def _never_twilio(*_a, **_k):
            raise RuntimeError("tests never reach Twilio")

        monkeypatch.setattr(_twilio, "_fetch_account", _never_twilio)
        monkeypatch.setattr(_twilio, "_account_cache", None)
    real = getattr(imsg, "_run_osascript", None)
    if real is None:
        return

    def _refuse_real_osascript(script, timeout=30):
        if imsg.subprocess.run is _REAL_SUBPROCESS_RUN:
            raise RuntimeError("tests never reach osascript: patch imessage_service._run_osascript")
        return real(script, timeout=timeout)

    monkeypatch.setattr(imsg, "_run_osascript", _refuse_real_osascript)


@pytest.fixture(autouse=True)
def _never_reach_mail(monkeypatch):
    """No test reads Ellis's mail or reaches Google (2026-10-11). Mail's
    Envelope Index points at a file that doesn't exist until a test points
    it at its own (``tests/fixtures/fake_mail.FakeMailStore``), and building
    a Gmail API client raises unless the test patched it: googleapiclient
    talks through httplib2, which the httpx host guard below never sees."""
    missing = os.path.join(tempfile.gettempdir(), "edward-tests-no-mail", "Envelope Index")
    try:
        from services import email_management_service
        from services.heartbeat import listener_email

        monkeypatch.setattr(email_management_service, "MAIL_DB_PATH", missing)
        monkeypatch.setattr(listener_email, "MAIL_DB_PATH", missing)
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import gmail_service
    except Exception:  # noqa: BLE001
        return

    def _no_google(*_a, **_k):
        raise RuntimeError("tests never reach Google: patch gmail_service._build_service or _get_service")

    monkeypatch.setattr(gmail_service, "_build_service", _no_google)
    try:
        from services import mail_service

        mail_service.reset_for_tests()
    except Exception:  # noqa: BLE001
        pass


# ── No real model, no real Codex login (2026-09-27) ───────────────────────
#
# Chunk 1's batch A2 found a consolidation test that stubbed
# ``services.llm_utils.build_background_llm`` while ``memory_service`` had
# imported the builder by name: the merge step built a real
# ``ChatCodexResponses`` and ~40 real requests went to Codex on Ellis's weekly
# quota, and nothing failed, because background paths catch every exception.
# So the refusal sits at the lowest seam every provider call goes through —
# httpx's network transports (``ChatCodexResponses``, the usage meter's
# ``wham/usage``, the OAuth refresh, and the OpenAI/Anthropic SDKs all send
# through them; ``MockTransport``, ``ASGITransport`` and the TestClient's
# transport are other classes and are untouched) — and every refused
# request is also recorded, so a test whose code swallowed the error still
# fails at teardown. Stub the LLM where the code under test looks it up; a
# test that truly must reach a provider says ``@pytest.mark.real_llm``.

LLM_PROVIDER_HOSTS = ("chatgpt.com", "openai.com", "anthropic.com")


def is_llm_provider_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in LLM_PROVIDER_HOSTS)


class RealLLMRequestRefused(RuntimeError):
    """A test tried to send a request to a real LLM provider."""


class _LLMNetworkGuard:
    def __init__(self):
        self.attempts: list = []

    def refuse(self, request) -> RealLLMRequestRefused:
        url = str(request.url)
        self.attempts.append({"method": request.method, "url": url})
        return RealLLMRequestRefused(
            f"test tried to reach a real model: {request.method} {url}. Stub the LLM "
            "where the code under test looks it up (a module that did "
            "`from services.llm_utils import build_background_llm` keeps the real "
            "one when only llm_utils is patched), or mark the test "
            "@pytest.mark.real_llm if it truly must."
        )


# The refusal is installed once for the whole session, not per test: a
# fire-and-forget task a test leaves behind (post-turn extraction, search
# tags) can run after that test's monkeypatch has been undone. Between
# tests a request is refused and kept here; ``pytest_sessionfinish`` fails
# the run over it.
_LLM_GUARD = {"current": None, "allow": False}
_ORPHAN_LLM_ATTEMPTS = _LLMNetworkGuard()


def _llm_guard_refuses(req):
    if _LLM_GUARD["allow"] or not is_llm_provider_host(req.url.host):
        return None
    return (_LLM_GUARD["current"] or _ORPHAN_LLM_ATTEMPTS).refuse(req)


@pytest.fixture(scope="session", autouse=True)
def _refuse_llm_providers_for_the_session():
    import httpx

    real_async = httpx.AsyncHTTPTransport.handle_async_request
    real_sync = httpx.HTTPTransport.handle_request

    async def guarded_async(self, req):
        refused = _llm_guard_refuses(req)
        if refused is not None:
            raise refused
        return await real_async(self, req)

    def guarded_sync(self, req):
        refused = _llm_guard_refuses(req)
        if refused is not None:
            raise refused
        return real_sync(self, req)

    httpx.AsyncHTTPTransport.handle_async_request = guarded_async
    httpx.HTTPTransport.handle_request = guarded_sync
    try:
        yield
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = real_async
        httpx.HTTPTransport.handle_request = real_sync


def pytest_sessionfinish(session, exitstatus):
    if _ORPHAN_LLM_ATTEMPTS.attempts:
        shown = ", ".join(f"{a['method']} {a['url']}" for a in _ORPHAN_LLM_ATTEMPTS.attempts[:3])
        print(
            f"\nREAL_LLM_REFUSED between tests: {len(_ORPHAN_LLM_ATTEMPTS.attempts)} "
            f"request(s) from a task a test left running: {shown}"
        )
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def llm_network_guard(request):
    """Refuse every request to an LLM provider during this test; fail the
    test at teardown if one was attempted, even when the code under test
    swallowed the refusal. ``@pytest.mark.real_llm`` lets it through."""
    guard = _LLMNetworkGuard()
    allow = request.node.get_closest_marker("real_llm") is not None
    _LLM_GUARD.update(current=guard, allow=allow)
    try:
        yield guard
    finally:
        _LLM_GUARD.update(current=None, allow=False)
    if guard.attempts:
        shown = ", ".join(f"{a['method']} {a['url']}" for a in guard.attempts[:3])
        more = f" (+{len(guard.attempts) - 3} more)" if len(guard.attempts) > 3 else ""
        pytest.fail(
            f"REAL_LLM_REFUSED: {len(guard.attempts)} request(s) to a real model: "
            f"{shown}{more}. Stub the LLM where the code under test looks it up, "
            "or mark the test @pytest.mark.real_llm.",
            pytrace=False,
        )


def _fake_jwt(claims: dict) -> str:
    import base64
    import json as _json

    def seg(obj) -> str:
        return base64.urlsafe_b64encode(_json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'none', 'typ': 'JWT'})}.{seg(claims)}.fake-signature"


FAKE_CODEX_AUTH = {
    "auth_mode": "chatgpt",
    "OPENAI_API_KEY": None,
    "tokens": {
        # JWT-shaped with the account claim, so ChatCodexResponses gets as
        # far as the (guarded) transport, exactly as it would on the mini.
        "access_token": _fake_jwt({
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-test-fake"},
            "exp": 4102444800,  # 2100-01-01
        }),
        "refresh_token": "rt-test-fake-not-a-real-refresh-token",
        "id_token": _fake_jwt({"email": "test@example.invalid"}),
        "account_id": "acct-test-fake",
    },
    "last_refresh": "2026-09-27T00:00:00Z",
}


@pytest.fixture(autouse=True)
def _fake_codex_login(monkeypatch):
    """A throwaway ``auth.json`` with fake tokens, and a fresh auth
    singleton, for every test (see ``CODEX_AUTH_PATH`` at the top)."""
    import json as _json

    path = os.path.join(_codex_test_dir, "auth.json")
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(FAKE_CODEX_AUTH, fh)
    monkeypatch.setenv("CODEX_AUTH_PATH", path)
    # The auth manager prefers an API key; the fake login decides alone.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        from services import codex_auth_service
    except Exception:  # noqa: BLE001
        yield
        return
    monkeypatch.setattr(codex_auth_service, "CODEX_AUTH_PATH", path)
    codex_auth_service._manager = None
    yield
    codex_auth_service._manager = None


@pytest.fixture(autouse=True)
def _quiet_codex_call_ledger(monkeypatch):
    """``ChatCodexResponses`` schedules a ``codex_calls`` row per HTTP
    request and the rate gate a ``llm_meter_samples`` row per reading it
    adopts — fire-and-forget DB writes. In a unit test that is a write on
    a loop about to close (and a pooled connection the next test's loop
    cannot use), so they are off by default; tests that pin the ledger set
    ``codex_call_ledger.WRITES_ENABLED`` back on (2026-09-24)."""
    try:
        from services import codex_call_ledger
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(codex_call_ledger, "WRITES_ENABLED", False)


# The pay-per-token switch and its limits (2026-10-10) are read from the
# environment per call, and ``main.py`` loads the repo's production ``.env``
# at import: without this a live ``EDWARD_LLM_PROVIDER=anthropic`` would put
# every later test on the Claude spend cap instead of the Codex meter.
_SPEND_ENV = (
    "EDWARD_LLM_PROVIDER", "EDWARD_DAILY_SPEND_CAP_USD", "EDWARD_BACKGROUND_SPEND_SHARE",
    "EDWARD_INTERACTIVE_RESERVE_USD", "EDWARD_CREDITS_START_USD", "EDWARD_CREDITS_START_DATE",
)


@pytest.fixture(autouse=True)
def _quiet_llm_spend(monkeypatch, request):
    """Each test starts on the Codex provider with the default limits, an
    empty in-memory day (as after a restart) and no fire-and-forget
    ``llm_calls`` writes; a test that pins the ledger sets
    ``llm_spend.WRITES_ENABLED`` back on, or sets the switch itself.

    ``ChatClaude`` asks the spend cap before every request (2026-10-11), and
    a cold day reads ``llm_calls``: a test without ``clean_db`` reads $0
    instead, so a unit test never depends on rows another test left in the
    test database (a test's own ``_db_spent_between`` patch still wins).

    The provider is pinned to ``codex``, never removed: ``load_dotenv``
    fills only unset variables, so a test that imports ``main`` (or
    ``voice_service``, ``graph_worker``) would otherwise bring the live
    ``.env``'s ``EDWARD_LLM_PROVIDER=anthropic`` in mid-test — the pin at
    the top of this file, kept per test (merge 2026-10-11)."""
    for name in _SPEND_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "codex")
    try:
        from services import llm_spend
    except Exception:  # noqa: BLE001
        yield
        return
    monkeypatch.setattr(llm_spend, "WRITES_ENABLED", False)
    if "clean_db" not in request.fixturenames:
        async def _nothing_spent(start, end):
            return 0.0

        monkeypatch.setattr(llm_spend, "_db_spent_between", _nothing_spent)
    llm_spend.reset_cache()
    yield
    llm_spend.reset_cache()


@pytest.fixture(autouse=True)
def _fake_embeddings_when_offline(monkeypatch):
    """Deterministic bag-of-words embeddings when ``EDWARD_FAKE_EMBEDDINGS=1``.

    The real all-MiniLM-L6-v2 download is blocked in some sandboxes (a
    proxy 403 from huggingface.co, 2026-09-18). With this flag set, every
    token is hashed into one of 384 buckets and the vector is normalised:
    identical texts embed identically (so reinforcement at >= 0.92 still
    fires) and texts that share words score high. Unset on the Mac mini,
    where the real model runs, so nothing changes there.
    """
    if os.environ.get("EDWARD_FAKE_EMBEDDINGS") != "1":
        return
    import hashlib
    import math
    import re as _re

    def _embed(text: str):
        vec = [0.0] * 384
        for tok in _re.findall(r"[a-z0-9']+", (text or "").lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % 384] += 1.0
            vec[(h >> 16) % 384] += 0.5
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    try:
        from services import memory_service

        monkeypatch.setattr(memory_service, "get_embedding", _embed)
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import document_service

        if hasattr(document_service, "get_embedding"):
            monkeypatch.setattr(document_service, "get_embedding", _embed)
    except Exception:  # noqa: BLE001
        pass
