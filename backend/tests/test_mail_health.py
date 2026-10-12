"""Mail's health, honestly (2026-10-11, integrations audit).

iCloud mail reaches Edward only while Mail.app runs on the Mac (it was
down 2026-09-14 → 10-11 and nothing said so), and the mail listeners were
dark for months behind a quiet inbox. ``mail_service.email_block()`` says
which: Mail.app running (``pgrep -x Mail``), how old the newest mail in
Mail's index is, whether each listener polled cleanly in the last 30 min,
Gmail connected and signed in. ``/api/health/report`` carries it as
``email``; the watchdog has no cookie, so ``GET /api/health/email`` is
public and answers only a verdict and a summary with no address and no
subject in it. Nothing here runs pgrep for real or reads Ellis's mail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.fake_mail import FakeGmail, FakeMailStore, hours_ago

pytestmark = pytest.mark.unit


@pytest.fixture
def mail(tmp_path, monkeypatch):
    """A fresh store, Mail.app running, both listeners polling cleanly,
    Gmail connected and watched."""
    from services import mail_service
    from services.heartbeat import listener_email, listener_gmail

    store = FakeMailStore(tmp_path).point(monkeypatch)
    store.add("parker@example.com", "Parker", "Dinner Friday?", received=hours_ago(1))
    store.running = True
    monkeypatch.setattr(mail_service, "_mail_app_running", lambda: store.running)
    now = datetime.now(timezone.utc)
    store.icloud_listener = {"status": "running", "last_ok_at": (now - timedelta(minutes=3)).isoformat(),
                             "started_at": (now - timedelta(hours=2)).isoformat(), "last_error": None,
                             "last_poll": {"stored": 0}}
    store.gmail_listener = dict(store.icloud_listener)
    monkeypatch.setattr(listener_email, "listener_state", lambda: dict(store.icloud_listener))
    monkeypatch.setattr(listener_gmail, "listener_state", lambda: dict(store.gmail_listener))
    store.tracks = {"email_enabled": True, "gmail_enabled": True}

    async def tracks():
        return dict(store.tracks)

    monkeypatch.setattr(mail_service, "_listener_tracks", tracks)
    store.gmail = FakeGmail("owner@gmail.example").install(monkeypatch)
    from services import gmail_service

    monkeypatch.setattr(gmail_service, "_last_refresh_error", None)
    return store


async def test_all_well_is_ok(mail):
    from services import mail_service

    block = await mail_service.email_block()
    assert block["verdict"] == "ok", block["warnings"]
    assert block["icloud"]["mail_app_running"] is True and block["icloud"]["index_readable"] is True
    assert block["icloud"]["index_age_hours"] < 2
    assert block["gmail"]["connected"] is True and block["gmail"]["account"] == "owner@gmail.example"


async def test_mail_app_not_running_warns(mail):
    from services import mail_service

    mail.running = False
    block = await mail_service.email_block()
    assert block["verdict"] == "warn"
    assert any("Mail.app isn't running" in w for w in block["warnings"])


async def test_an_index_more_than_a_day_old_warns(mail, tmp_path, monkeypatch):
    from services import mail_service

    stale = FakeMailStore(tmp_path / "stale").point(monkeypatch)
    stale.add("parker@example.com", "Parker", "old", received=hours_ago(30))
    block = await mail_service.email_block()
    assert block["verdict"] == "warn"
    assert any("newest mail in Mail's index" in w for w in block["warnings"])


async def test_an_unreadable_index_warns(mail, monkeypatch, tmp_path):
    from services import email_management_service, mail_service
    from services.heartbeat import listener_email

    monkeypatch.setattr(email_management_service, "MAIL_DB_PATH", str(tmp_path / "nope" / "Envelope Index"))
    monkeypatch.setattr(listener_email, "MAIL_DB_PATH", str(tmp_path / "nope" / "Envelope Index"))
    mail_service.reset_for_tests()
    block = await mail_service.email_block()
    assert block["icloud"]["index_readable"] is False
    assert any("index isn't readable" in w for w in block["warnings"])


async def test_a_silent_icloud_listener_warns(mail):
    from services import mail_service

    mail.icloud_listener["last_ok_at"] = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    block = await mail_service.email_block()
    assert any("iCloud mail listener" in w for w in block["warnings"])
    mail.icloud_listener.update(status="error", last_ok_at=None)
    block = await mail_service.email_block()
    assert block["verdict"] == "warn"


async def test_a_just_started_listener_is_not_judged_yet(mail):
    from services import mail_service

    mail.icloud_listener.update(last_ok_at=None, started_at=datetime.now(timezone.utc).isoformat())
    block = await mail_service.email_block()
    assert block["verdict"] == "ok", block["warnings"]


async def test_a_listener_that_is_switched_off_is_not_a_warning(mail):
    from services import mail_service

    mail.tracks = {"email_enabled": False, "gmail_enabled": False}
    mail.icloud_listener.update(status="stopped", last_ok_at=None)
    mail.gmail_listener.update(status="stopped", last_ok_at=None)
    block = await mail_service.email_block()
    assert block["verdict"] == "ok", block["warnings"]


async def test_gmail_not_connected_is_information_not_a_warning(mail, monkeypatch):
    from services import mail_service

    FakeGmail().install(monkeypatch, connected=False)
    block = await mail_service.email_block()
    assert block["verdict"] == "ok"
    assert block["gmail"]["connected"] is False
    assert any("Gmail isn't connected" in i for i in block["info"])


async def test_a_lapsed_gmail_sign_in_warns(mail, monkeypatch):
    from services import gmail_service, mail_service

    monkeypatch.setattr(gmail_service, "_last_refresh_error", "RefreshError: invalid_grant")
    block = await mail_service.email_block()
    assert block["verdict"] == "warn"
    assert any("Gmail's sign-in has lapsed" in w for w in block["warnings"])


async def test_a_silent_gmail_listener_warns(mail):
    from services import mail_service

    mail.gmail_listener["last_ok_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    block = await mail_service.email_block()
    assert any("Gmail listener" in w for w in block["warnings"])


def test_pgrep_decides_whether_mail_app_runs(monkeypatch):
    import subprocess

    from services import mail_service

    seen = []

    def run(args, **kw):
        seen.append((list(args), kw.get("timeout")))
        return subprocess.CompletedProcess(args, 0 if seen[-1][0][-1] == "Mail" else 1, b"", b"")

    monkeypatch.setattr(mail_service.subprocess, "run", run)
    assert mail_service._mail_app_running() is True
    assert seen == [(["pgrep", "-x", "Mail"], 2)]

    def broken(*_a, **_k):
        raise subprocess.TimeoutExpired("pgrep", 2)

    monkeypatch.setattr(mail_service.subprocess, "run", broken)
    assert mail_service._mail_app_running() is False


# ------------------------------------------------------------ the route --

async def test_the_public_route_says_only_a_verdict_and_a_summary(mail):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import health_report

    mail.running = False
    app = FastAPI()
    app.include_router(health_report.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/health/email")
    body = r.json()
    assert r.status_code == 200 and set(body) == {"verdict", "summary"}
    assert body["verdict"] == "warn" and "Mail.app isn't running" in body["summary"]
    for private in ("parker@example.com", "Dinner", "owner@gmail.example"):
        assert private not in r.text


def test_the_route_is_public_for_the_watchdog():
    from middleware.auth import PUBLIC_ROUTES

    assert "/api/health/email" in PUBLIC_ROUTES
    assert "/api/health/report" not in PUBLIC_ROUTES, "the full report stays behind the cookie"


@pytest.mark.integration
async def test_the_health_report_carries_the_email_block(clean_db, monkeypatch):
    from services import health_report_service as hr, mail_service

    async def block():
        return {"verdict": "warn", "warnings": ["Mail.app isn't running on this Mac"]}

    monkeypatch.setattr(mail_service, "email_block", block)
    rep = await hr.build_report(days=1)
    assert rep["email"]["verdict"] == "warn"


async def test_the_email_section_never_sinks_the_report(monkeypatch):
    from services import health_report_service as hr, mail_service

    async def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(mail_service, "email_block", broken)
    out = await hr.email_section()
    assert out["verdict"] == "unknown" and "boom" in out["error"]


# --------------------------------------------------------- Ellis's addresses --

async def test_owners_addresses_come_from_every_place_he_is(mail, monkeypatch):
    from services import mail_service
    from services.graph import tools as legacy

    sent = "imap://11111111-2222-3333-4444-555555555555/Sent%20Messages"
    for _ in range(3):
        mail.add("owner@icloud.example", "Ellis Reed", "re: dinner", mailbox=sent, write_file=False)
    mail.add("ellis.reed@me.example", "Ellis Reed", "fwd", mailbox=sent, write_file=False)
    monkeypatch.setenv("GMAIL_ACCOUNT", "Ellis.Work@gmail.example")

    async def handles():
        return ["+12015550100", "owner@apple-id.example"]

    monkeypatch.setattr(legacy, "_owner_handles", handles)
    mail_service.reset_for_tests()
    got = {a.lower() for a in await mail_service.owner_addresses()}
    assert {"ellis.work@gmail.example", "owner@gmail.example", "owner@icloud.example", "ellis.reed@me.example",
            "owner@apple-id.example"} <= got
    assert "parker@example.com" not in got, "an inbox sender is not Ellis"
    assert "+12015550100" not in got


async def test_owners_addresses_never_raise(monkeypatch, tmp_path):
    from services import email_management_service, gmail_service, mail_service

    async def broken():
        raise RuntimeError("db down")

    async def no_handles():
        raise RuntimeError("db down")

    from services.graph import tools as legacy

    monkeypatch.setattr(gmail_service, "get_email_address", broken)
    monkeypatch.setattr(legacy, "_owner_handles", no_handles)
    monkeypatch.setattr(email_management_service, "MAIL_DB_PATH", str(tmp_path / "missing"))
    monkeypatch.delenv("GMAIL_ACCOUNT", raising=False)
    mail_service.reset_for_tests()
    assert await mail_service.owner_addresses() == []


# ------------------------------------------------------------- html → text --

def test_html_becomes_readable_text():
    from services.mail_service import html_to_text

    html = ("<html><head><style>p{color:red}</style><script>alert(1)</script></head><body>"
            "<h1>Your order</h1><p>Hi Ellis,</p><p>It&#39;s shipped &amp; on its way.</p>"
            "<div>Track it<br>here</div><ul><li>One</li><li>Two</li></ul>"
            "<p></p><p></p><p></p><p></p><p>Thanks</p></body></html>")
    text = html_to_text(html)
    assert "color:red" not in text and "alert" not in text
    assert "It's shipped & on its way." in text
    assert "Track it\nhere" in text
    assert "\n\n\n\n" not in text, "at most two blank lines"
    assert text.startswith("Your order")


# ------------------------------------------------------------- the clock --

def test_an_email_from_another_year_says_the_year():
    """2026-10-11 review: "Sep 6 8:43 pm" for a message 400 days old read
    like one from last month."""
    import re

    from services import mail_service
    from services.timezone_utils import utc_to_local

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=400)
    text = mail_service.when_text(old)
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [ap]m", text), text
    assert str(utc_to_local(old).year) in text
    recent = now - timedelta(minutes=5)
    if utc_to_local(recent).year == utc_to_local(now).year:
        assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2} \d{1,2}:\d{2} [ap]m", mail_service.when_text(recent))


# ------------------------------------------------ Gmail's sign-in marker --

async def test_reconnecting_gmail_clears_the_lapsed_warning(mail, monkeypatch):
    """2026-10-11 review: the lapsed marker cleared only on a later token
    refresh, so the report and the watchdog kept warning after Ellis
    reconnected."""
    from services import gmail_service, mail_service

    monkeypatch.setattr(gmail_service, "_last_refresh_error", "RefreshError: invalid_grant")
    assert any("sign-in has lapsed" in w for w in (await mail_service.email_block())["warnings"])

    class Flow:
        credentials = object()

        def fetch_token(self, code):
            assert code == "4/again"

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(gmail_service, "_take_state", lambda state: {"redirect_uri": "r", "verifier": "v"})
    monkeypatch.setattr(gmail_service, "_oauth_flow", lambda *_a, **_k: Flow())
    monkeypatch.setattr(gmail_service, "_build_service", lambda creds: mail.gmail)
    monkeypatch.setattr(gmail_service, "_check_account", nothing)
    monkeypatch.setattr(gmail_service, "_save_credentials", nothing)
    await gmail_service.exchange_code("4/again", "state")
    assert gmail_service.token_refresh_failed() is None
    block = await mail_service.email_block()
    assert block["verdict"] == "ok", block["warnings"]


async def test_disconnecting_gmail_clears_the_lapsed_marker(monkeypatch):
    from types import SimpleNamespace

    from services import gmail_service

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, *_a, **_k):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def no_credentials():
        return None

    monkeypatch.setattr(gmail_service, "_last_refresh_error", "RefreshError: invalid_grant")
    monkeypatch.setattr(gmail_service, "_get_credentials", no_credentials)
    monkeypatch.setattr(gmail_service, "async_session", Session)
    await gmail_service.revoke_and_disconnect()
    assert gmail_service.token_refresh_failed() is None


async def test_a_burst_of_public_hits_does_the_work_once(mail, monkeypatch):
    """2026-10-11 review: the public route is reachable through the tunnel
    and each hit ran pgrep, opened Mail's index and queried Postgres. The
    summary is kept 60 s, and hits that arrive together share one check."""
    import asyncio

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import health_report
    from services import mail_service

    calls = []
    real = mail_service.email_block

    async def counted():
        calls.append(1)
        await asyncio.sleep(0.01)
        return await real()

    clock = [1000.0]
    monkeypatch.setattr(mail_service, "email_block", counted)
    monkeypatch.setattr(mail_service, "_clock", lambda: clock[0])
    app = FastAPI()
    app.include_router(health_report.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        together = await asyncio.gather(*[c.get("/api/health/email") for _ in range(5)])
        for _ in range(5):
            r = await c.get("/api/health/email")
        assert len(calls) == 1
        assert {x.json()["verdict"] for x in together} == {"ok"} and r.json()["verdict"] == "ok"
        clock[0] += 61
        mail.running = False
        r = await c.get("/api/health/email")
    assert len(calls) == 2 and r.json()["verdict"] == "warn"
