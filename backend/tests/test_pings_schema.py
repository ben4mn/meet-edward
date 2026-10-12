"""Step 0 of AREA 3 (pings and media, 2026-10-11): the shared files.

``services/pings/hooks.py``, ``schema.py``, ``services/imessage_sent.py``,
``services/ping_service.py`` and ``services/media/{__init__,hooks,schema}.py``
are written once, verbatim from the spec's appendices, and the inbound,
outbound and media work all build on them. These tests pin what the others
rely on:

- the DDL is additive and idempotent, and the existing send-ledger rows
  become ``ok = true`` (the echo index trusts only ok rows);
- ``create_ping`` is idempotent by ``source_ref``, clips to column widths,
  and a failing insert costs only its savepoint, never the message, and
  never logs the message text (a database error's DETAIL line carries it);
- ``addressed_recently`` matches any spelling of a chat, ignores ``held``
  rows and holds whatever ``EDWARD_PINGS`` says; ``open_chats``;
- the in-memory send index (``imessage_sent``): the in-flight claim,
  ``resolve``, the 60 s unconfirmed window, ``load`` of ok rows only;
- the send ledger's time convention: aware UTC finds a row stamped now
  inside a 120 s window, and a 10-minute dedupe skips a 3-hour-old row;
- ``CURRENT_PING`` is one dict that a worker thread (``to_thread``) and a
  task (``create_task``) both write into.

Nothing here reaches chat.db, osascript or a model.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

# --------------------------------------------------------------- helpers ----


@pytest.fixture(autouse=True)
def _fresh_send_index():
    from services import imessage_sent

    imessage_sent.reset_for_tests()
    yield
    imessage_sent.reset_for_tests()


def _naive_utc(**delta) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)


async def _ping(session, ref: str, **kw):
    from services.pings import hooks

    fields = dict(channel="imessage", kind="mention", from_owner=False, third_party=True, sender_known=True,
                  asked_at=_naive_utc())
    fields.update(kw)
    return await hooks.create_ping(session, source_ref=ref, **fields)


async def _scalar(sql: str, **params):
    from services.database import async_session

    async with async_session() as session:
        return (await session.execute(text(sql), params)).scalar()


# ------------------------------------------------------------ the DDL ----

_NEW_SEND_COLUMNS = ("chat_guid", "route", "ok", "error", "message_rowid", "message_guid", "text_norm_hash",
                     "text_len", "ping_id", "purpose", "turn_source", "confirmed_at")


async def test_ddl_is_idempotent_and_marks_existing_sends_ok(clean_db):
    """Run init_db twice over a database in its pre-AREA-3 shape: the tables
    and indexes appear, and a send already in the ledger reads ok."""
    from services import database

    async with database.engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS pings, media_items"))
        for col in _NEW_SEND_COLUMNS:
            await conn.execute(text(f"ALTER TABLE imessage_outgoing_sends DROP COLUMN IF EXISTS {col}"))
        await conn.execute(text(
            "INSERT INTO imessage_outgoing_sends (chat_identifier, text_hash, sent_at) "
            "VALUES ('chat42', 'h', now())"))

    await database.init_db()
    await database.init_db()

    async with database.async_session() as session:
        tables = set((await session.execute(text(
            "SELECT tablename FROM pg_tables WHERE tablename IN ('pings', 'media_items')"))).scalars())
        cols = set((await session.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'imessage_outgoing_sends'")))
            .scalars())
        oks = (await session.execute(text("SELECT ok FROM imessage_outgoing_sends"))).scalars().all()
        indexes = set((await session.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename IN "
            "('pings', 'media_items', 'imessage_outgoing_sends')"))).scalars())
        sent_at_type = (await session.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'imessage_outgoing_sends' AND column_name = 'confirmed_at'"))).scalar()

    assert tables == {"pings", "media_items"}
    assert set(_NEW_SEND_COLUMNS) <= cols
    assert oks == [True]
    assert {"ux_pings_source_ref", "ix_pings_event", "ix_pings_status_due", "ix_pings_chat_asked",
            "ux_media_items_source_ref", "ix_media_items_event", "ix_media_items_chat_created",
            "ix_media_items_status", "ix_outgoing_sends_rowid", "ix_outgoing_sends_chat_time",
            "ix_outgoing_sends_ping"} <= indexes
    assert sent_at_type == "timestamp with time zone"


def test_send_ledger_model_carries_the_read_back_columns():
    """The ORM model and the ALTERs agree, so a fresh database built by
    ``create_all`` has the same columns as a migrated one."""
    from services.database import ImessageOutgoingSendModel

    cols = ImessageOutgoingSendModel.__table__.columns
    assert set(_NEW_SEND_COLUMNS) <= set(cols.keys())
    assert cols["ok"].nullable is False
    assert cols["confirmed_at"].type.timezone is True
    assert cols["message_rowid"].type.__class__.__name__ == "BigInteger"


def test_conftest_truncates_the_new_tables():
    import inspect

    from tests import conftest

    src = inspect.getsource(conftest._truncate_all)
    assert "pings" in src and "media_items" in src


# ------------------------------------------------------- create_ping ----


async def test_create_ping_is_idempotent_and_clips(clean_db):
    from services.database import async_session

    async with async_session() as session:
        first = await _ping(session, "imessage:101", sender="x" * 400, chat_name="c" * 400,
                            text="t" * 12_000, kind="reply_to_edward", from_owner=True, third_party=False)
        again = await _ping(session, "imessage:101", sender="other")
        await session.commit()

    assert first is not None and again == first
    async with async_session() as session:
        row = (await session.execute(text(
            "SELECT sender, chat_name, text, must_answer, status, attempts, next_attempt_at, detected_at "
            "FROM pings WHERE id = :id"), {"id": first})).mappings().one()
        count = (await session.execute(text("SELECT count(*) FROM pings"))).scalar()
    assert count == 1
    assert len(row["sender"]) == 255 and row["sender"] == "x" * 255
    assert len(row["chat_name"]) == 255
    assert len(row["text"]) == 10_000
    assert row["must_answer"] is True            # Ellis's own reply to Edward's bubble
    assert row["status"] == "open" and row["attempts"] == 0
    assert row["next_attempt_at"] is not None and row["detected_at"] is not None


async def test_a_friends_reply_to_edward_is_may_answer(clean_db):
    from services.database import async_session

    async with async_session() as session:
        pid = await _ping(session, "imessage:102", kind="reply_to_edward", from_owner=False)
        await session.commit()
    assert await _scalar("SELECT must_answer FROM pings WHERE id = :id", id=pid) is False


async def test_a_failing_insert_never_costs_the_message(clean_db, caplog):
    """A NOT NULL violation rolls back the savepoint only: the events written
    before and after it in the same transaction still commit, and the log
    carries the error's first line, never the row (or the message in it)."""
    from services.database import HeartbeatEventModel, async_session

    secret = "the message body that must never reach a log"
    caplog.set_level(logging.WARNING, logger="services.pings.hooks")
    async with async_session() as session:
        session.add(HeartbeatEventModel(id="ev-before", source="imessage", event_type="message_received",
                                        source_id="imessage:201", summary=secret))
        await session.flush()
        pid = await _ping(session, "imessage:201", channel=None, text=secret)
        session.add(HeartbeatEventModel(id="ev-after", source="imessage", event_type="message_received",
                                        source_id="imessage:202"))
        await session.commit()

    assert pid is None
    events = await _scalar("SELECT count(*) FROM heartbeat_events WHERE id IN ('ev-before', 'ev-after')")
    assert events == 2
    assert await _scalar("SELECT count(*) FROM pings") == 0
    assert "could not record the ping for imessage:201" in caplog.text
    assert secret not in caplog.text
    assert "Failing row" not in caplog.text


def test_brief_error_keeps_the_first_line_only():
    from services.pings.hooks import brief_error

    class _Orig(Exception):
        pass

    class DBError(Exception):
        def __init__(self):
            super().__init__("wrapper")
            self.orig = _Orig('null value in column "channel"\nDETAIL:  Failing row contains (secret text).')

    out = brief_error(DBError())
    assert out == 'DBError: null value in column "channel"'
    assert "secret" not in out


# ------------------------------------------------- the pure helpers ----


@pytest.mark.parametrize("kind,from_owner,expected", [
    ("mention", False, True), ("mention", True, True), ("owner_direct", True, True),
    ("reply_to_edward", True, True), ("reply_to_edward", False, False),
    ("vocative", False, False), ("turn", True, False),
])
def test_must_answer(kind, from_owner, expected):
    from services.pings.hooks import must_answer

    assert must_answer(kind, from_owner) is expected


@pytest.mark.parametrize("spelling", ["chat123456", "any;+;chat123456", "iMessage;+;chat123456", "CHAT123456"])
def test_chat_key_one_spelling_per_group(spelling):
    from services.pings.hooks import chat_key

    assert chat_key(spelling) == "chat123456"


@pytest.mark.parametrize("spelling", ["+12015550101", "(201) 555-0101", "201.555.0101", "SMS;-;+12015550101",
                                      "2015550101"])
def test_chat_key_one_spelling_per_phone(spelling):
    from services.pings.hooks import chat_key

    assert chat_key(spelling) == "p:2015550101"


def test_chat_key_email_and_empty():
    from services.pings.hooks import chat_key

    assert chat_key("Owner@Example.com") == "owner@example.com"
    assert chat_key(None) == "" and chat_key("  ") == ""


@pytest.mark.parametrize("value,expected", [(None, True), ("on", True), ("off", False), ("0", False),
                                            ("false", False), ("No", False), ("1", True)])
def test_enabled_reads_edward_pings(monkeypatch, value, expected):
    from services.pings import hooks

    if value is None:
        monkeypatch.delenv("EDWARD_PINGS", raising=False)
    else:
        monkeypatch.setenv("EDWARD_PINGS", value)
    assert hooks.enabled() is expected


def test_utcnow_is_naive_utc():
    from services.pings.hooks import utcnow

    now = utcnow()
    assert now.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds()) < 5


# ------------------------------------------- addressed_recently, open ----


async def test_addressed_recently_matches_any_spelling_and_skips_held(clean_db, monkeypatch):
    from services import ping_service
    from services.database import async_session
    from services.pings import hooks

    async with async_session() as session:
        await _ping(session, "imessage:301", chat_identifier="chat123456", chat_guid="iMessage;+;chat123456",
                    is_group=True)
        await _ping(session, "imessage:302", chat_identifier="+12015550101", status="legacy")
        await _ping(session, "imessage:303", chat_identifier="+12015550199", status="held", sender_known=False)
        await _ping(session, "imessage:304", chat_identifier="chat999", asked_at=_naive_utc(hours=30))
        await session.commit()

    for spelling in ("chat123456", "any;+;chat123456", "iMessage;+;chat123456", "(201) 555-0101", "2015550101"):
        assert await hooks.addressed_recently(spelling) is True, spelling
    assert await hooks.addressed_recently("+12015550199") is False      # held: Edward never answered there
    assert await hooks.addressed_recently("chat999") is False           # 30 h ago, outside 24 h
    assert await hooks.addressed_recently("chat999", 48) is True
    assert await hooks.addressed_recently("chat-nobody") is False
    assert await hooks.addressed_recently("") is False

    monkeypatch.setenv("EDWARD_PINGS", "off")                           # rows are written either way
    assert await hooks.addressed_recently("any;+;chat123456") is True
    assert await ping_service.addressed_recently("chat123456") is True  # AREA 2's name for it
    assert ping_service.chat_key("any;+;chat123456") == "chat123456"


async def test_open_chats_lists_open_and_running_only(clean_db):
    from services.database import async_session
    from services.pings import hooks

    async with async_session() as session:
        await _ping(session, "imessage:401", chat_identifier="any;+;chatOPEN")
        await _ping(session, "imessage:402", chat_identifier="+12015550101", status="running")
        await _ping(session, "imessage:403", chat_identifier="chatDONE", status="answered")
        await _ping(session, "imessage:404", chat_identifier="chatLEGACY", status="legacy")
        await session.commit()

    assert await hooks.open_chats() == {"chatopen", "p:2015550101"}


# ------------------------------------------------------ imessage_sent ----


def test_normalize_and_text_hash_ignore_signature_space_and_case():
    from services import imessage_sent

    a = "Saturday works.\n\n- Edward 🤖"
    b = "  saturday   WORKS. "
    assert imessage_sent.normalize(a) == "saturday works."
    assert imessage_sent.text_hash(a) == imessage_sent.text_hash(b)
    assert len(imessage_sent.text_hash(a)) == 64
    assert imessage_sent.text_hash("other") != imessage_sent.text_hash(a)


def test_inflight_claim_then_resolve_records_the_row():
    from services import imessage_sent

    t0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    token = imessage_sent.begin("any;+;chat123", "Tickets are $142\n\n- Edward 🤖", now=t0)

    # The listener sees the from-me row first, spelled its own way.
    assert imessage_sent.claim_inflight("chat123", "tickets are $142", 9001, now=t0 + timedelta(seconds=3))
    assert imessage_sent.claimed_rowid(token) == 9001
    assert imessage_sent.is_edward_rowid(9001)
    # A second copy of the row is not claimed twice.
    assert not imessage_sent.claim_inflight("chat123", "tickets are $142", 9002, now=t0 + timedelta(seconds=4))

    imessage_sent.resolve(token, guid="GUID-9001", now=t0 + timedelta(seconds=5))   # no rowid: the claim's
    assert imessage_sent.is_edward_rowid(9001)
    assert imessage_sent.last_send("iMessage;+;chat123") == t0 + timedelta(seconds=5)
    assert not imessage_sent.unconfirmed_recently("chat123", now=t0 + timedelta(seconds=6))
    assert imessage_sent.claimed_rowid(token) is None                                # resolved and gone


def test_claim_needs_same_chat_same_text_within_120s():
    from services import imessage_sent

    t0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    imessage_sent.begin("chat123", "hello there", now=t0)
    assert not imessage_sent.claim_inflight("chat999", "hello there", 1, now=t0)
    assert not imessage_sent.claim_inflight("chat123", "hello there!", 2, now=t0)
    assert not imessage_sent.claim_inflight("chat123", "hello there", 3, now=t0 + timedelta(seconds=121))
    assert not imessage_sent.claim_inflight("chat123", "", 4, now=t0)
    assert not imessage_sent.claim_inflight(None, "hello there", 5, now=t0)
    assert imessage_sent.claim_inflight("chat123", "Hello  there", 6, now=t0 + timedelta(seconds=119))


def test_resolve_with_no_row_opens_the_unconfirmed_window():
    from services import imessage_sent

    t0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    token = imessage_sent.begin("+12015550101", "hi", now=t0)
    imessage_sent.resolve(token, now=t0 + timedelta(seconds=5))
    assert imessage_sent.unconfirmed_recently("(201) 555-0101", now=t0 + timedelta(seconds=30))
    assert imessage_sent.unconfirmed_recently("2015550101", now=t0 + timedelta(seconds=65))
    assert not imessage_sent.unconfirmed_recently("2015550101", now=t0 + timedelta(seconds=66))
    assert not imessage_sent.unconfirmed_recently("+12015550199", now=t0 + timedelta(seconds=30))
    imessage_sent.resolve(token)                  # a second resolve is a no-op


def test_resolve_with_a_row_and_abandon():
    from services import imessage_sent

    t0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    token = imessage_sent.begin("chat123", "one", now=t0)
    imessage_sent.resolve(token, rowid=77, guid="G77", now=t0)
    assert imessage_sent.is_edward_rowid(77) and not imessage_sent.is_edward_rowid(78)
    assert not imessage_sent.is_edward_rowid(None)
    assert "G77" in imessage_sent.edward_guids()
    assert not imessage_sent.unconfirmed_recently("chat123", now=t0)

    failed = imessage_sent.begin("chat555", "two", now=t0)
    imessage_sent.abandon(failed)
    assert imessage_sent.last_send("chat555") is None
    assert not imessage_sent.claim_inflight("chat555", "two", 79, now=t0)


async def test_load_reads_ok_rows_only(clean_db):
    from services import imessage_sent
    from services.database import async_session

    now = imessage_sent.aware_utcnow()
    async with async_session() as session:
        for rowid, guid, chat, ok, age in ((501, "G501", "chat1", True, timedelta(minutes=5)),
                                           (502, "G502", "chat1", False, timedelta(minutes=4)),
                                           (503, "G503", "+12015550101", True, timedelta(days=2)),
                                           (504, "G504", "chat2", True, timedelta(days=40))):
            await session.execute(text(
                "INSERT INTO imessage_outgoing_sends (chat_identifier, text_hash, sent_at, ok, message_rowid, "
                "message_guid) VALUES (:chat, 'h', :at, :ok, :rowid, :guid)"),
                {"chat": chat, "at": now - age, "ok": ok, "rowid": rowid, "guid": guid})
        await session.commit()

    assert await imessage_sent.load(days=30) == 2
    assert imessage_sent.is_edward_rowid(501) and imessage_sent.is_edward_rowid(503)
    assert not imessage_sent.is_edward_rowid(502)      # a failed attempt is not Edward's row
    assert not imessage_sent.is_edward_rowid(504)      # older than the window
    assert imessage_sent.edward_guids() == frozenset({"G501", "G503"})
    last = imessage_sent.last_send("chat1")
    assert last is not None and last.tzinfo is not None
    assert abs((last - (now - timedelta(minutes=5))).total_seconds()) < 1


async def test_load_returns_zero_when_the_database_fails(monkeypatch):
    from services import database, imessage_sent

    def _boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(database, "async_session", _boom)
    assert await imessage_sent.load() == 0


# ------------------------------------------- the send ledger's clock ----


async def test_aware_windows_find_a_fresh_row_and_skip_an_old_one(clean_db):
    """The listener's 120 s hash window and the delivery's 10-minute dedupe
    bind aware UTC against the timestamptz ``sent_at``."""
    from services import imessage_sent
    from services.database import async_session

    h = imessage_sent.text_hash("Saturday works")
    now = imessage_sent.aware_utcnow()
    async with async_session() as session:
        await session.execute(text(
            "INSERT INTO imessage_outgoing_sends (chat_identifier, text_hash, text_norm_hash, sent_at, ok) VALUES "
            "('chat1', 'x', :h, :now, true), ('chat2', 'x', :h, :old, true), ('chat3', 'x', :h, :now, false)"),
            {"h": h, "now": now, "old": now - timedelta(hours=3)})
        await session.commit()

    window = await _scalar(
        "SELECT count(*) FROM imessage_outgoing_sends WHERE text_norm_hash = :h AND ok IS TRUE "
        "AND sent_at >= :cutoff", h=h, cutoff=imessage_sent.aware_utcnow() - timedelta(seconds=120))
    assert window == 1                                  # chat1 only: chat3 failed, chat2 is old
    dedupe_old = await _scalar(
        "SELECT count(*) FROM imessage_outgoing_sends WHERE chat_identifier = 'chat2' AND text_norm_hash = :h "
        "AND ok IS TRUE AND sent_at >= :cutoff", h=h, cutoff=imessage_sent.aware_utcnow() - timedelta(minutes=10))
    assert dedupe_old == 0


# ------------------------------------------------------- CURRENT_PING ----


async def test_current_ping_is_shared_with_threads_and_tasks():
    from services.pings import hooks

    ctx = hooks.new_context(ping_id=7, chat_identifier="chat123", chat_guid="iMessage;+;chat123", is_group=True,
                            conversation_id="ping-7", must_answer=True, third_party=False)
    token = hooks.CURRENT_PING.set(ctx)
    try:
        def transport():
            counted = hooks.note_send_start("any;+;chat123")
            assert counted is ctx and ctx["inflight"] == 1
            assert hooks.note_send_start("chat999") is None          # another chat: not this ping's
            hooks.note_send_end(counted, ok=True, purpose="tool", rowid=9001, route="chat_id")
            hooks.note_send_end(None, ok=True)                       # no context: a no-op

        async def tool_loop():
            hooks.note_tool("messages", {"action": "stand_down", "chat_id": "chat123"}, ok=True)
            hooks.note_tool("tell_owner", "not a dict", ok=False)
            hooks.note_stop(outcome="answered", error_class=None, retryable=False)

        await asyncio.to_thread(transport)
        await asyncio.create_task(tool_loop())
    finally:
        hooks.CURRENT_PING.reset(token)

    assert hooks.current_ping() is None
    assert ctx["inflight"] == 0
    assert [(s["ok"], s["rowid"], s["route"]) for s in ctx["sends"]] == [(True, 9001, "chat_id")]
    assert ctx["sends"][0]["at"].tzinfo is None
    assert ctx["tools"] == [
        {"name": "messages", "action": "stand_down", "chat": "chat123", "urgent": False, "ok": True},
        {"name": "tell_owner", "action": None, "chat": None, "urgent": False, "ok": False},
    ]
    assert ctx["stop"] == {"outcome": "answered", "retryable": False}   # None values are dropped
    assert ctx["media"] == {"images": 0, "bytes": 0}


def test_hooks_are_no_ops_outside_a_ping():
    from services.pings import hooks

    assert hooks.current_ping() is None
    assert hooks.note_send_start("chat123") is None
    hooks.note_tool("send_message", {"chat_id": "chat123"}, ok=True)
    hooks.note_stop(outcome="answered")
    assert hooks.same_chat(None, "chat123") is False
    ctx = hooks.new_context(ping_id=1, chat_identifier="+12015550101", chat_guid=None, is_group=False,
                            conversation_id="ping-1", must_answer=True, third_party=True)
    assert hooks.same_chat(ctx, "SMS;-;(201) 555-0101") and not hooks.same_chat(ctx, "")


# ------------------------------------- the runner and media, lazily ----


@pytest.fixture
def _no_runner_store_or_viewer(monkeypatch):
    """As before Agents B and C merge: the lazy imports find nothing."""
    for name in ("services.pings.runner", "services.pings.store", "services.media.viewer",
                 "services.media.present", "services.media.store", "services.media.pipeline",
                 "services.media.metrics"):
        monkeypatch.setitem(sys.modules, name, None)


async def test_hooks_degrade_when_the_runner_is_missing(_no_runner_store_or_viewer):
    from services.pings import hooks

    hooks.kick()
    assert hooks.chats_in_flight() == set()
    await hooks.start()
    await hooks.stop()
    assert await hooks.pack_lines() == []
    assert await hooks.report(7) == {"error": "pings not installed"}


async def test_media_degrades_when_the_viewer_is_missing(_no_runner_store_or_viewer, monkeypatch):
    from services import media
    from services.media import hooks as media_hooks

    seen = await media.view("attachment:449321")
    assert seen.kind == "file" and seen.images == [] and seen.pdf is None
    assert seen.error.startswith("unsupported:")
    assert await media_hooks.turn_attachments([{"rowid": 1, "path": "/x.jpg"}]) == []
    assert await media_hooks.register_imessage(object(), event_id="e1", msg={}) == []
    media_hooks.enqueue([1, 2])
    await media_hooks.start()
    await media_hooks.stop()
    assert await media_hooks.report(7) == {"enabled": True, "store": "P1"}


def test_media_description_reaches_the_placeholder(monkeypatch, _no_runner_store_or_viewer):
    from collections import OrderedDict

    from services.media import hooks as media_hooks

    monkeypatch.setattr(media_hooks, "_descriptions", OrderedDict())   # the cache is process-wide
    monkeypatch.setenv("EDWARD_MEDIA", "on")
    default = "[image: IMG_3533.heic attachment:449321]"
    assert media_hooks.placeholder({"rowid": 449321}, default) == default
    media_hooks.note_description("attachment:449321", "  a screenshot of a Ticketmaster order  ")
    assert media_hooks.describe_ref("attachment:449321") == "a screenshot of a Ticketmaster order"
    assert media_hooks.placeholder({"rowid": 449321}, default) == (
        '[image: IMG_3533.heic attachment:449321 - "a screenshot of a Ticketmaster order"]')
    assert media_hooks.placeholder({}, default) == default
    monkeypatch.setenv("EDWARD_MEDIA", "off")
    assert media_hooks.describe_ref("attachment:449321") is None
    assert media_hooks.placeholder({"rowid": 449321}, default) == default
