"""Watermark must advance only after a successful store.

Before: ``_poll_once`` advanced the in-memory ``_last_seen_rowid`` BEFORE
calling ``_store_events``. If the store raised (transient DB error), the
batch was above the watermark on the next poll and never re-fetched — those
messages were silently lost for the life of the process (days).

Re-processing after a failed store is safe: ``_store_events`` dedups by
``source_id``.
"""

import time
from unittest.mock import AsyncMock

import pytest

from services.heartbeat import listener_imessage as listener


@pytest.fixture(autouse=True)
def _reset_module_state():
    listener._last_seen_rowid = 100
    listener._last_event_triggered_triage = 0.0
    yield
    listener._last_seen_rowid = 0
    listener._last_event_triggered_triage = 0.0


def _msg(rowid):
    return {
        "rowid": rowid,
        "text": "hello",
        "attributed_body": None,
        "is_from_me": False,
        "date": int((time.time() - listener.APPLE_EPOCH_OFFSET) * 1e9),
        "service": "iMessage",
        "sender_id": "+15559998888",
        "chat_identifier": "+15559998888",
        "display_name": None,
        "style": 45,
        "guid": "g",
        "is_group": False,
    }


async def test_store_failure_leaves_watermark_unchanged(monkeypatch):
    monkeypatch.setattr(listener, "_query_chat_db", lambda rowid: [_msg(101), _msg(102)])
    monkeypatch.setattr(
        listener, "_load_listener_config", AsyncMock(return_value=([], True))
    )
    monkeypatch.setattr(
        listener, "_store_events", AsyncMock(side_effect=RuntimeError("db down"))
    )
    persist = AsyncMock()
    monkeypatch.setattr(listener, "_persist_rowid", persist)

    with pytest.raises(RuntimeError):
        await listener._poll_once()

    assert listener._last_seen_rowid == 100, (
        "watermark must not advance past a failed store — the batch would be lost"
    )
    assert persist.await_count == 0


async def test_store_success_advances_watermark(monkeypatch):
    monkeypatch.setattr(listener, "_query_chat_db", lambda rowid: [_msg(101), _msg(102)])
    monkeypatch.setattr(
        listener, "_load_listener_config", AsyncMock(return_value=([], True))
    )
    monkeypatch.setattr(
        listener,
        "_store_events",
        AsyncMock(return_value=listener.StoreResult(
            stored=2, has_mentions=False,
            has_owner_direct=False, has_inbound_direct=False,
        )),
    )
    persist = AsyncMock()
    monkeypatch.setattr(listener, "_persist_rowid", persist)
    import services.heartbeat.heartbeat_service as hb

    monkeypatch.setattr(hb, "trigger_immediate_triage", AsyncMock())

    await listener._poll_once()

    assert listener._last_seen_rowid == 102
    persist.assert_awaited_once_with(102)
