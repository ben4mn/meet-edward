"""The mail listeners run again, and never flood (2026-10-11, integrations audit).

Both listeners had been dark since 2026-06-21: in ``_poll_once`` the
``stored = 0`` / ``ledger_events = []`` lines sat after a ``return``, so
every poll that found mail raised ``UnboundLocalError``. Fixed now, with
three rules so turning them on can't bury Ellis or the noticer:

* a message more than 24 h old is skipped, though the mark still moves past
  it — Mail.app was down 2026-09-14 → 10-11 and downloads old mail under new
  ROWIDs; it always applies, so first sight after an outage is covered too;
* the iCloud listener drains its backlog in one poll, page by page (the
  mark is saved after each page), so new mail isn't stuck behind old;
* each listener says when it last polled cleanly (``listener_state``), so
  the health report can tell a quiet inbox from a dead listener.

Gmail: an expired ``historyId`` re-seeds the mark and stores nothing (it
used to look like an empty inbox forever); the "watch my inbox" switch
starts and stops the listener (the PATCH used to ignore it), and so do
connecting and disconnecting. Nothing here reaches Google, Mail or a model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.fixtures.fake_mail import FakeMailStore, hours_ago

pytestmark = pytest.mark.unit


class _Session:
    added: list = []
    commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, *_a, **_k):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, obj):
        _Session.added.append(obj)

    async def commit(self):
        _Session.commits += 1


async def _identity(**_kw):
    return SimpleNamespace(person_key="alias:test", display_label="someone", provenance="raw_fallback",
                           alias_key=None)


@pytest.fixture
def icloud(tmp_path, monkeypatch):
    from services.heartbeat import listener_email as L

    store = FakeMailStore(tmp_path).point(monkeypatch)
    _Session.added, _Session.commits = [], 0
    marks: list = []

    async def persist(rowid):
        marks.append(rowid)

    monkeypatch.setattr(L, "async_session", _Session)
    monkeypatch.setattr(L, "resolve_contact_identity", _identity)
    monkeypatch.setattr(L, "append_identity_raw_data", lambda raw, _i: raw)
    monkeypatch.setattr(L, "_persist_rowid", persist)
    monkeypatch.setattr(L, "_last_rowid", 0)
    monkeypatch.setattr(L, "_inbox_rowids", [])
    monkeypatch.setattr(L, "_fetch_limit", 20)
    for name, value in (("_last_ok_at", None), ("_last_error", None), ("_last_stats", {})):
        monkeypatch.setattr(L, name, value, raising=False)
    store.marks = marks
    return store


# ------------------------------------------------------------- iCloud --

async def test_fresh_mail_is_stored(icloud):
    from services.heartbeat import listener_email as L

    a = icloud.add("parker@example.com", "Parker", "Dinner Friday?", received=hours_ago(0.5))
    b = icloud.add("shop@example.com", "Shop", "Your order shipped", received=hours_ago(2))
    await L._poll_once()
    assert sorted(e.source_id for e in _Session.added) == [f"email:{a}", f"email:{b}"]
    assert L._last_rowid == b and icloud.marks[-1] == b


async def test_old_mail_is_skipped_but_the_mark_moves_past_it(icloud):
    """Mail.app back after a month: last month's mail arrives under new ROWIDs."""
    from services.heartbeat import listener_email as L

    old = icloud.add("news@example.com", "News", "September digest", received=hours_ago(24 * 20))
    fresh = icloud.add("parker@example.com", "Parker", "Dinner Friday?", received=hours_ago(1))
    older = icloud.add("bank@example.com", "Bank", "Statement", received=hours_ago(25))
    await L._poll_once()
    assert [e.source_id for e in _Session.added] == [f"email:{fresh}"]
    assert L._last_rowid == older, "every row advances the mark, stored or not"
    state = L.listener_state()
    assert state["last_poll"]["stored"] == 1 and state["last_poll"]["skipped_stale"] == 2


async def test_a_backlog_drains_in_one_poll(icloud):
    from services.heartbeat import listener_email as L

    last = 0
    for i in range(120):
        last = icloud.add(f"n{i}@example.com", None, f"old {i}", received=hours_ago(24 * 30))
    fresh = icloud.add("parker@example.com", "Parker", "Dinner Friday?", received=hours_ago(0.2))
    await L._poll_once()
    assert [e.source_id for e in _Session.added] == [f"email:{fresh}"]
    assert L._last_rowid == fresh > last
    assert len(icloud.marks) >= 6, "the mark is saved after each page"
    assert icloud.marks == sorted(icloud.marks)


async def test_a_clean_poll_is_stamped_even_with_nothing_new(icloud):
    from services.heartbeat import listener_email as L

    assert L.listener_state()["last_ok_at"] is None
    await L._poll_once()
    state = L.listener_state()
    assert state["last_ok_at"] is not None and state["last_error"] is None


async def test_a_failing_poll_says_why(icloud, monkeypatch):
    from services.heartbeat import listener_email as L

    def broken(*_a):
        raise RuntimeError("database disk image is malformed")

    monkeypatch.setattr(L, "_query_mail_db", broken)
    await L._poll_once()
    state = L.listener_state()
    assert state["last_ok_at"] is None and "malformed" in state["last_error"]


async def test_the_poll_logs_what_it_stored_and_skipped(icloud, capsys):
    from services.heartbeat import listener_email as L

    icloud.add("news@example.com", "News", "old", received=hours_ago(48))
    last = icloud.add("parker@example.com", "Parker", "new", received=hours_ago(1))
    await L._poll_once()
    out = capsys.readouterr().out
    assert f"stored 1, skipped 1 older than 24 h (mark → {last})" in out


# -------------------------------------------------------------- Gmail --

@pytest.fixture
def gmail_listener(monkeypatch):
    from services import gmail_service
    from services.heartbeat import listener_gmail as G

    _Session.added, _Session.commits = [], 0
    monkeypatch.setattr(G, "async_session", _Session)
    monkeypatch.setattr(G, "resolve_contact_identity", _identity)
    monkeypatch.setattr(G, "append_identity_raw_data", lambda raw, _i: raw)
    monkeypatch.setattr(G, "_last_history_id", "100")
    for name, value in (("_last_ok_at", None), ("_last_error", None), ("_last_stats", {})):
        monkeypatch.setattr(G, name, value, raising=False)
    world = SimpleNamespace(history=[], metadata={}, current="200", saved=[], expired=False)

    async def history(_since):
        if world.expired:
            raise gmail_service.GmailHistoryExpired("historyId 100 is too old")
        return list(world.history)

    async def metadata(mid):
        return world.metadata.get(mid)

    async def current():
        return world.current

    async def save(hid):
        world.saved.append(hid)

    monkeypatch.setattr(gmail_service, "get_history", history)
    monkeypatch.setattr(gmail_service, "get_message_metadata", metadata)
    monkeypatch.setattr(gmail_service, "get_current_history_id", current)
    monkeypatch.setattr(gmail_service, "update_last_history_id", save)
    return world


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


async def test_gmail_skips_mail_older_than_a_day(gmail_listener):
    from services.heartbeat import listener_gmail as G

    gmail_listener.history = [{"message_id": "old"}, {"message_id": "new"}]
    gmail_listener.metadata = {
        "old": {"sender": "news@example.com", "subject": "digest", "snippet": "", "labels": ["INBOX"],
                "internal_date": _ms(hours_ago(30))},
        "new": {"sender": "parker@example.com", "subject": "dinner", "snippet": "", "labels": ["INBOX"],
                "internal_date": _ms(hours_ago(1))},
    }
    await G._poll_once()
    assert [e.source_id for e in _Session.added] == ["gmail:new"]
    assert G._last_history_id == "200" and gmail_listener.saved == ["200"], "the mark still advances"
    state = G.listener_state()
    assert state["last_ok_at"] is not None and state["last_poll"]["skipped_stale"] == 1


async def test_an_expired_history_id_reseeds_and_stores_nothing(gmail_listener):
    from services.heartbeat import listener_gmail as G

    gmail_listener.expired = True
    gmail_listener.current = "999"
    await G._poll_once()
    assert _Session.added == []
    assert G._last_history_id == "999" and gmail_listener.saved == ["999"]
    assert G.listener_state()["last_ok_at"] is not None


async def test_get_history_raises_when_the_history_id_expired(monkeypatch):
    from services import gmail_service
    from tests.fixtures.fake_mail import FakeGmail, FakeHttpError

    g = FakeGmail().install(monkeypatch)
    g.history_error = FakeHttpError(404, "Requested entity was not found.")
    with pytest.raises(gmail_service.GmailHistoryExpired):
        await gmail_service.get_history("100")


HISTORY_URI = ("https://gmail.googleapis.com/gmail/v1/users/me/history?startHistoryId=123"
               "&historyTypes=messageAdded&labelId=INBOX&alt=json")


def _http_error(status: int, reason: str, message: str, uri: str = HISTORY_URI):
    """A real googleapiclient HttpError: its str() carries the request URI,
    and for history.list that URI always says ``startHistoryId``."""
    import json

    import httplib2
    from googleapiclient.errors import HttpError

    content = json.dumps({"error": {"code": status, "message": message, "status": reason}}).encode()
    return HttpError(httplib2.Response({"status": status, "reason": reason}), content, uri=uri)


@pytest.mark.parametrize("status,reason,message", [
    (503, "Service Unavailable", "The service is currently unavailable."),
    (429, "Too Many Requests", "Quota exceeded for quota metric 'Queries'."),
    (403, "Forbidden", "Request had insufficient authentication scopes."),
    (500, "Internal Server Error", "Backend Error"),
    (400, "Bad Request", "Invalid label: INBOXX"),
])
async def test_a_gmail_failure_is_not_an_expired_history(monkeypatch, status, reason, message):
    """2026-10-11 review: str(HttpError) carries the URI, which always says
    startHistoryId — reading str(e) made every 5xx/429/403 look expired."""
    from services import gmail_service
    from tests.fixtures.fake_mail import FakeGmail

    g = FakeGmail().install(monkeypatch)
    g.history_error = _http_error(status, reason, message)
    assert "startHistoryId" in str(g.history_error)
    with pytest.raises(gmail_service.GmailError) as e:
        await gmail_service.get_history("123")
    assert not isinstance(e.value, gmail_service.GmailHistoryExpired)
    assert str(status) in str(e.value)


@pytest.mark.parametrize("status,reason,message", [
    (404, "Not Found", "Requested entity was not found."),
    (400, "Bad Request", "Invalid startHistoryId"),
    (400, "Bad Request", "Invalid value at 'start_history_id' (TYPE_UINT64), \"abc\""),
])
async def test_an_expired_or_invalid_history_id_is_expired(monkeypatch, status, reason, message):
    from services import gmail_service
    from tests.fixtures.fake_mail import FakeGmail

    g = FakeGmail().install(monkeypatch)
    g.history_error = _http_error(status, reason, message)
    with pytest.raises(gmail_service.GmailHistoryExpired):
        await gmail_service.get_history("123")


async def test_a_gmail_outage_reseeds_nothing_and_says_why(monkeypatch):
    """The listener over the real get_history: a 503 keeps the mark, saves
    nothing, and is not a clean poll."""
    from services import gmail_service
    from services.heartbeat import listener_gmail as G
    from tests.fixtures.fake_mail import FakeGmail

    g = FakeGmail().install(monkeypatch)
    g.history_error = _http_error(503, "Service Unavailable", "The service is currently unavailable.")
    g.history_id = "999"
    saved: list = []

    async def save(hid):
        saved.append(hid)

    monkeypatch.setattr(gmail_service, "update_last_history_id", save)
    monkeypatch.setattr(G, "_last_history_id", "100")
    for name, value in (("_last_ok_at", None), ("_last_error", None), ("_last_stats", {})):
        monkeypatch.setattr(G, name, value, raising=False)
    await G._poll_once()
    state = G.listener_state()
    assert G._last_history_id == "100" and saved == [], "an outage is not an expired history: nothing re-seeded"
    assert state["last_ok_at"] is None and "503" in (state["last_error"] or "")


async def test_an_expired_history_that_cannot_reseed_is_not_a_clean_poll(gmail_listener):
    from services.heartbeat import listener_gmail as G

    gmail_listener.expired = True
    gmail_listener.current = None
    await G._poll_once()
    state = G.listener_state()
    assert G._last_history_id == "100" and gmail_listener.saved == []
    assert state["last_ok_at"] is None and state["last_error"]


async def test_metadata_carries_the_internal_date(monkeypatch):
    from services import gmail_service
    from tests.fixtures.fake_mail import FakeGmail

    g = FakeGmail().install(monkeypatch)
    when = hours_ago(3)
    g.add("m1", internal_date=when)
    meta = await gmail_service.get_message_metadata("m1")
    assert meta["internal_date"] == int(when.timestamp() * 1000)
    found = await gmail_service.search_gmail("dinner")
    assert found[0]["internal_date"] == int(when.timestamp() * 1000)


# ------------------------------------------------ starting the Gmail listener --

@pytest.fixture
def gmail_starts(monkeypatch):
    from services import gmail_service
    from services.heartbeat import listener_gmail as G

    calls: list = []

    async def start(config):
        calls.append(("start", getattr(config, "gmail_enabled", None)))

    async def stop():
        calls.append(("stop",))

    async def configured():
        return True

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(G, "start_gmail_listener", start)
    monkeypatch.setattr(G, "stop_gmail_listener", stop)
    monkeypatch.setattr(gmail_service, "is_configured", configured)
    # A fresh config row reads every other track as switched on: those
    # listeners stay where they are. (There is no calendar listener since
    # 2026-10-11, edward/cap-calendar.)
    from services.heartbeat import listener_email, listener_imessage

    for module, name in ((listener_imessage, "start_imessage_listener"), (listener_imessage, "stop_imessage_listener"),
                         (listener_email, "start_email_listener"), (listener_email, "stop_email_listener")):
        monkeypatch.setattr(module, name, nothing)
    return calls


@pytest.mark.integration
async def test_the_watch_my_inbox_switch_starts_and_stops_the_listener(clean_db, gmail_starts):
    from routers.heartbeat import update_config
    from services.heartbeat.models import HeartbeatConfigUpdate

    out = await update_config(HeartbeatConfigUpdate(gmail_enabled=True, gmail_poll_seconds=300))
    assert gmail_starts == [("start", True)]
    from sqlalchemy import select

    from services.database import HeartbeatConfigModel, async_session

    async with async_session() as s:
        row = (await s.execute(select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default"))
               ).scalar_one()
    assert row.gmail_enabled is True and row.gmail_poll_seconds == 300, "the PATCH used to ignore both"
    await update_config(HeartbeatConfigUpdate(gmail_enabled=False))
    assert gmail_starts[-1] == ("stop",)
    assert out is not None


@pytest.mark.integration
async def test_ensure_started_starts_only_when_watching_and_connected(clean_db, gmail_starts, monkeypatch):
    from services import gmail_service
    from services.heartbeat import listener_gmail as G
    from routers.heartbeat import update_config
    from services.heartbeat.models import HeartbeatConfigUpdate

    await G.ensure_started()
    assert gmail_starts == [], "not watching: nothing starts"
    await update_config(HeartbeatConfigUpdate(gmail_enabled=True))
    gmail_starts.clear()
    await G.ensure_started()
    assert gmail_starts == [("start", True)]

    async def not_configured():
        return False

    monkeypatch.setattr(gmail_service, "is_configured", not_configured)
    gmail_starts.clear()
    await G.ensure_started()
    assert gmail_starts == []


async def test_connecting_starts_and_disconnecting_stops_the_listener(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import gmail as gmail_router
    from services import gmail_service, tool_registry
    from services.heartbeat import listener_gmail as G

    calls: list = []

    async def ensure():
        calls.append("ensure")

    async def stop():
        calls.append("stop")

    async def exchange(code, state):
        return {"email_address": "owner@gmail.example", "success": True}

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(G, "ensure_started", ensure)
    monkeypatch.setattr(G, "stop_gmail_listener", stop)
    monkeypatch.setattr(gmail_service, "exchange_code", exchange)
    monkeypatch.setattr(gmail_service, "revoke_and_disconnect", nothing)
    monkeypatch.setattr(tool_registry, "refresh_registry", nothing)
    app = FastAPI()
    app.include_router(gmail_router.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/gmail/oauth/callback", params={"code": "4/x", "state": "s"})
        assert r.status_code in (302, 307)
        assert calls == ["ensure"]
        await c.post("/api/gmail/disconnect")
        assert calls == ["ensure", "stop"]
