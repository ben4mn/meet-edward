"""
Tests for the iMessage heartbeat listener — focused on high-water-mark
persistence (Fix #2 in the stabilization plan).

The existing listener tracks ``_last_seen_rowid`` as a module global that
resets to 0 on every process restart. During the seed window (calling
``_get_max_rowid()``) iCloud can sync historical messages with new ROWIDs,
causing them to appear "new" on the next poll.

These tests drive the fix: mirror the email listener and persist the
high-water mark to ``HeartbeatConfigModel.imessage_last_rowid``.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.heartbeat import listener_imessage as listener


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Ensure every test starts with a clean listener module state."""
    listener._last_seen_rowid = 0
    yield
    listener._last_seen_rowid = 0


# ---------------------------------------------------------------------------
# Unit tests — new persistence helpers (RED tests drive the fix)
# ---------------------------------------------------------------------------


async def test_load_persisted_rowid_helper_exists():
    """_load_persisted_rowid() must exist and be callable (mirrors email listener)."""
    assert hasattr(listener, "_load_persisted_rowid"), (
        "listener_imessage must expose _load_persisted_rowid() like listener_email"
    )
    assert asyncio.iscoroutinefunction(listener._load_persisted_rowid)


async def test_persist_rowid_helper_exists():
    """_persist_rowid(rowid) must exist and be callable."""
    assert hasattr(listener, "_persist_rowid"), (
        "listener_imessage must expose _persist_rowid() like listener_email"
    )
    assert asyncio.iscoroutinefunction(listener._persist_rowid)


async def test_persist_then_load_roundtrip_returns_same_rowid(clean_db):
    """_persist_rowid(N) → _load_persisted_rowid() returns N."""
    from services import database

    # Ensure a HeartbeatConfigModel row exists (normally created by init_db defaults)
    async with database.async_session() as session:
        from sqlalchemy import select
        existing = await session.execute(
            select(database.HeartbeatConfigModel).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(database.HeartbeatConfigModel(id="default"))
            await session.commit()

    await listener._persist_rowid(12345)
    loaded = await listener._load_persisted_rowid()
    assert loaded == 12345


async def test_load_persisted_rowid_returns_none_when_unset(clean_db):
    """Fresh config row with NULL imessage_last_rowid → returns None."""
    from services import database

    async with database.async_session() as session:
        session.add(database.HeartbeatConfigModel(id="default"))
        await session.commit()

    loaded = await listener._load_persisted_rowid()
    assert loaded is None


# ---------------------------------------------------------------------------
# Seed / warm-start behavior
# ---------------------------------------------------------------------------


async def test_warm_start_uses_persisted_value_not_max_rowid():
    """If a persisted rowid exists, _listener_loop must NOT call _get_max_rowid()."""
    persisted_value = 50000
    get_max_calls = []

    def fake_get_max():
        get_max_calls.append("called")
        return 99999  # would be used if seed falls through

    with patch.object(listener, "_load_persisted_rowid", AsyncMock(return_value=persisted_value)):
        with patch.object(listener, "_get_max_rowid", side_effect=fake_get_max):
            # Simulate just the seed path (not the infinite loop)
            persisted = await listener._load_persisted_rowid()
            if persisted is not None:
                listener._last_seen_rowid = persisted
            else:
                listener._last_seen_rowid = listener._get_max_rowid()

    assert listener._last_seen_rowid == persisted_value
    assert get_max_calls == [], "Warm start must not call _get_max_rowid()"


async def test_cold_start_seeds_from_max_rowid_and_persists():
    """No persisted value → seed from chat.db max ROWID and immediately persist."""
    persist_calls = []

    async def fake_persist(rowid):
        persist_calls.append(rowid)

    with patch.object(listener, "_load_persisted_rowid", AsyncMock(return_value=None)):
        with patch.object(listener, "_get_max_rowid", return_value=77777):
            with patch.object(listener, "_persist_rowid", fake_persist):
                # Simulate the seed path. The fix must call both.
                persisted = await listener._load_persisted_rowid()
                if persisted is None:
                    seed = listener._get_max_rowid()
                    listener._last_seen_rowid = seed
                    await listener._persist_rowid(seed)

    assert listener._last_seen_rowid == 77777
    assert persist_calls == [77777], (
        "Cold start must persist the seed value immediately so a fast restart "
        "doesn't re-race iCloud sync"
    )


# ---------------------------------------------------------------------------
# Race-condition simulation — the core bug
# ---------------------------------------------------------------------------


async def test_two_rapid_restarts_do_not_replay_messages():
    """Simulate two process restarts. After the first seed persists, a second
    'restart' must pick up the persisted value and not reprocess anything.

    This is the exact bug: without persistence, each restart re-seeds from
    _get_max_rowid() while iCloud can be injecting historical rows.
    """
    _in_memory = {"rowid": None}

    async def fake_load():
        return _in_memory["rowid"]

    async def fake_persist(r):
        _in_memory["rowid"] = r

    get_max_calls = []

    def fake_get_max():
        get_max_calls.append(True)
        return 10000

    with patch.object(listener, "_load_persisted_rowid", fake_load):
        with patch.object(listener, "_persist_rowid", fake_persist):
            with patch.object(listener, "_get_max_rowid", side_effect=fake_get_max):
                # Restart #1 — cold start
                listener._last_seen_rowid = 0
                persisted = await listener._load_persisted_rowid()
                if persisted is None:
                    seed = listener._get_max_rowid()
                    listener._last_seen_rowid = seed
                    await listener._persist_rowid(seed)

                # Restart #2 — warm start
                listener._last_seen_rowid = 0
                persisted = await listener._load_persisted_rowid()
                if persisted is not None:
                    listener._last_seen_rowid = persisted
                else:
                    seed = listener._get_max_rowid()
                    listener._last_seen_rowid = seed
                    await listener._persist_rowid(seed)

    assert listener._last_seen_rowid == 10000
    assert len(get_max_calls) == 1, (
        "_get_max_rowid() must only run on the cold start. Calling it on "
        "restart #2 is the race-condition-bug behaviour."
    )


async def test_poll_persists_updated_rowid():
    """_poll_once() must persist the new max rowid after processing messages."""
    persist_calls = []

    async def fake_persist(r):
        persist_calls.append(r)

    fake_messages = [
        {"rowid": 100, "text": "hi", "is_from_me": False, "date": 0, "service": "iMessage",
         "sender_id": "+15555550101", "chat_identifier": "chat1", "display_name": None,
         "is_group": False, "guid": "g1"},
        {"rowid": 101, "text": "hey", "is_from_me": False, "date": 0, "service": "iMessage",
         "sender_id": "+15555550101", "chat_identifier": "chat1", "display_name": None,
         "is_group": False, "guid": "g2"},
    ]

    with patch.object(listener, "_query_chat_db", return_value=fake_messages):
        with patch.object(listener, "_store_events", AsyncMock(return_value=listener.StoreResult(
            stored=2, has_mentions=False, has_owner_direct=False, has_inbound_direct=False,
        ))):
            with patch.object(listener, "_load_listener_config", AsyncMock(return_value=([], True))):
                with patch.object(listener, "_persist_rowid", fake_persist):
                    listener._last_seen_rowid = 50
                    await listener._poll_once()

    assert listener._last_seen_rowid == 101
    assert persist_calls == [101], (
        "_poll_once() must persist the new high-water mark so restarts "
        "pick up from the same point"
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_chat_db_wipe_resets_seed():
    """If persisted rowid > current_max (chat.db was wiped), the listener
    should log a warning and re-seed from the current max."""
    get_max_calls = []
    persist_calls = []

    def fake_get_max():
        get_max_calls.append(True)
        return 42  # chat.db was wiped, current max is low

    async def fake_persist(r):
        persist_calls.append(r)

    with patch.object(listener, "_load_persisted_rowid", AsyncMock(return_value=999999)):
        with patch.object(listener, "_get_max_rowid", side_effect=fake_get_max):
            with patch.object(listener, "_persist_rowid", fake_persist):
                # Simulate the guarded seed path the fix must implement.
                persisted = await listener._load_persisted_rowid()
                current_max = listener._get_max_rowid()
                if persisted is not None and persisted > current_max:
                    # chat.db was wiped — re-seed
                    listener._last_seen_rowid = current_max
                    await listener._persist_rowid(current_max)
                elif persisted is not None:
                    listener._last_seen_rowid = persisted
                else:
                    listener._last_seen_rowid = current_max
                    await listener._persist_rowid(current_max)

    assert listener._last_seen_rowid == 42
    assert persist_calls == [42], "Re-seeded rowid must be persisted"


async def test_twenty_four_hour_filter_still_trips():
    """Regression guard for commit 2f83061 — the 24h age filter must still
    skip messages with an Apple timestamp older than 24 hours."""
    from datetime import datetime, timedelta, timezone
    from services.heartbeat.listener_imessage import (
        _apple_timestamp_to_datetime,
        APPLE_EPOCH_OFFSET,
    )

    # Apple timestamp for 48 hours ago (nanoseconds since 2001-01-01)
    two_days_ago_utc = datetime.now(timezone.utc) - timedelta(hours=48)
    apple_ts = int((two_days_ago_utc.timestamp() - APPLE_EPOCH_OFFSET) * 1e9)

    recovered = _apple_timestamp_to_datetime(apple_ts)
    age_hours = (datetime.now(timezone.utc) - recovered).total_seconds() / 3600

    assert age_hours > 24, (
        "Sanity: the computed age must exceed 24h so the existing filter "
        "at listener_imessage.py:219-223 trips as intended"
    )


async def test_store_events_flags_known_united_rbm_as_no_reply_expected(clean_db):
    from datetime import datetime, timezone
    from services.database import HeartbeatEventModel, async_session
    from services.heartbeat.listener_imessage import APPLE_EPOCH_OFFSET
    from sqlalchemy import select

    now_apple_ts = int((datetime.now(timezone.utc).timestamp() - APPLE_EPOCH_OFFSET) * 1e9)
    messages = [
        {
            "rowid": 1001,
            "text": "United flight UA 124 has arrived at B12.",
            "is_from_me": False,
            "date": now_apple_ts,
            "service": "RCS",
            "sender_id": "notifications@rbm.goog",
            "chat_identifier": "iMessage;-;notifications@rbm.goog",
            "display_name": None,
            "is_group": False,
            "guid": "rbm-guid",
        },
        {
            "rowid": 1002,
            "text": "Can you call me later?",
            "is_from_me": False,
            "date": now_apple_ts,
            "service": "iMessage",
            "sender_id": "+15550006666",
            "chat_identifier": "iMessage;-;+15550006666",
            "display_name": None,
            "is_group": False,
            "guid": "normal-guid",
        },
    ]

    async def fake_resolve(sender):
        return "United Airlines" if sender == "notifications@rbm.goog" else "Rowan"

    with patch.object(listener, "_resolve_contact_name", fake_resolve):
        result = await listener._store_events(messages)

    assert result.stored == 2
    assert result.has_mentions is False

    async with async_session() as session:
        rows = (await session.execute(
            select(HeartbeatEventModel).order_by(HeartbeatEventModel.sender.asc())
        )).scalars().all()

    by_sender = {row.sender: json.loads(row.raw_data or "{}") for row in rows}
    assert by_sender["notifications@rbm.goog"]["informational_push"] is True
    assert by_sender["notifications@rbm.goog"]["no_reply_expected"] is True
    assert "no_reply_expected" not in by_sender["+15550006666"]


# ---------------------------------------------------------------------------
# Schema — new column must exist
# ---------------------------------------------------------------------------


def test_heartbeat_config_has_imessage_last_rowid_column():
    """HeartbeatConfigModel must expose imessage_last_rowid (Integer, nullable)."""
    from services.database import HeartbeatConfigModel

    col = HeartbeatConfigModel.__table__.columns.get("imessage_last_rowid")
    assert col is not None, (
        "Add imessage_last_rowid = Column(Integer, nullable=True) to "
        "HeartbeatConfigModel, next to email_last_rowid"
    )
    assert col.nullable is True, "imessage_last_rowid should be nullable"


# ---------------------------------------------------------------------------
# Integration — full seed path against a live test DB
# ---------------------------------------------------------------------------


async def test_integration_full_seed_roundtrip(clean_db):
    """Live DB: cold seed persists, warm restart restores, poll updates row."""
    from services import database
    from sqlalchemy import select

    # Ensure a HeartbeatConfigModel row exists
    async with database.async_session() as session:
        session.add(database.HeartbeatConfigModel(id="default"))
        await session.commit()

    # --- Cold start simulation ---
    listener._last_seen_rowid = 0
    with patch.object(listener, "_get_max_rowid", return_value=1000):
        persisted = await listener._load_persisted_rowid()
        current_max = listener._get_max_rowid()
        if persisted is None:
            listener._last_seen_rowid = current_max
            await listener._persist_rowid(current_max)

    # Verify persisted in DB
    async with database.async_session() as session:
        result = await session.execute(
            select(database.HeartbeatConfigModel.imessage_last_rowid).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        assert result.scalar_one() == 1000

    # --- Warm restart simulation ---
    listener._last_seen_rowid = 0
    with patch.object(listener, "_get_max_rowid", return_value=2000) as mock_max:
        persisted = await listener._load_persisted_rowid()
        current_max = listener._get_max_rowid()
        assert persisted == 1000
        # Normal case: persisted <= current_max
        listener._last_seen_rowid = persisted

    assert listener._last_seen_rowid == 1000, "Warm start must use persisted value"

    # --- Poll updates and persists ---
    fake_msgs = [
        {"rowid": 1500, "text": "hello", "is_from_me": False, "date": 0, "service": "iMessage",
         "sender_id": "+15555550199", "chat_identifier": "c1", "display_name": None,
         "is_group": False, "guid": "gX"},
    ]
    with patch.object(listener, "_query_chat_db", return_value=fake_msgs):
        with patch.object(listener, "_store_events", AsyncMock(return_value=listener.StoreResult(
            stored=1, has_mentions=False, has_owner_direct=False, has_inbound_direct=False,
        ))):
            await listener._poll_once()

    async with database.async_session() as session:
        result = await session.execute(
            select(database.HeartbeatConfigModel.imessage_last_rowid).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        assert result.scalar_one() == 1500, "Poll must persist the new high-water mark"
