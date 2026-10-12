"""The chat.db poll loop must not block on a triage cycle.

Why (2026-09-02): `_poll_once` awaited `trigger_immediate_triage()` inline,
so a 347-second cycle stalled the 10s poll for its whole duration and
every message that arrived meanwhile waited. Now the listener *requests*
a cycle via an asyncio.Event; a single runner task in heartbeat_service
coalesces bursts and runs it.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from services.heartbeat import heartbeat_service, listener_imessage as listener
from services.heartbeat.listener_imessage import StoreResult, should_request_triage

pytestmark = pytest.mark.unit


class TestShouldRequestTriage:
    def test_mentions_bypass_the_debounce(self):
        r = StoreResult(stored=1, has_mentions=True, has_owner_direct=False, has_inbound_direct=True)
        assert should_request_triage(r, now=100.0, last=99.0, enabled=True) == "@edward mention"

    def test_owner_direct_bypasses_the_debounce(self):
        r = StoreResult(stored=1, has_mentions=False, has_owner_direct=True, has_inbound_direct=False)
        assert should_request_triage(r, now=100.0, last=99.0, enabled=True) == "owner direct ping"

    def test_ordinary_inbound_is_debounced(self):
        r = StoreResult(stored=1, has_mentions=False, has_owner_direct=False, has_inbound_direct=True)
        assert should_request_triage(r, now=100.0, last=99.0, enabled=True) is None
        assert should_request_triage(r, now=100.0 + listener.EVENT_TRIGGERED_TRIAGE_MIN_INTERVAL_SECONDS,
                                     last=100.0, enabled=True) == "inbound 1:1 message"

    def test_ordinary_inbound_respects_the_config_flag(self):
        r = StoreResult(stored=1, has_mentions=False, has_owner_direct=False, has_inbound_direct=True)
        assert should_request_triage(r, now=1000.0, last=0.0, enabled=False) is None

    def test_nothing_interesting_requests_nothing(self):
        r = StoreResult(stored=3, has_mentions=False, has_owner_direct=False, has_inbound_direct=False)
        assert should_request_triage(r, now=1000.0, last=0.0, enabled=True) is None


async def test_poll_once_does_not_await_triage_cycle():
    fake_messages = [{"rowid": 100, "text": "@edward hi", "is_from_me": False, "date": 0, "service": "iMessage",
                      "sender_id": "+15555550101", "chat_identifier": "chat1", "display_name": None,
                      "is_group": False, "guid": "g1"}]

    async def slow_cycle(_n):
        await asyncio.sleep(1.0)

    requested = []
    with patch.object(listener, "_query_chat_db", return_value=fake_messages), \
         patch.object(listener, "_store_events", AsyncMock(return_value=StoreResult(
             stored=1, has_mentions=True, has_owner_direct=False, has_inbound_direct=True))), \
         patch.object(listener, "_load_listener_config", AsyncMock(return_value=([], True))), \
         patch.object(listener, "_persist_rowid", AsyncMock()), \
         patch.object(heartbeat_service, "request_triage", lambda reason: requested.append(reason)):
        listener._last_seen_rowid = 50
        t = time.monotonic()
        await listener._poll_once()
        elapsed = time.monotonic() - t
    assert elapsed < 0.1, f"_poll_once blocked for {elapsed:.2f}s"
    assert requested == ["@edward mention"]


async def test_trigger_runner_coalesces_bursts_into_one_cycle():
    runs = []

    async def fake_cycle():
        runs.append(1)
        await asyncio.sleep(0.05)

    heartbeat_service._reset_trigger_for_tests()
    with patch.object(heartbeat_service, "trigger_immediate_triage", fake_cycle):
        task = asyncio.create_task(heartbeat_service._trigger_runner())
        try:
            for _ in range(5):
                heartbeat_service.request_triage("burst")
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    assert 1 <= len(runs) <= 2, f"5 requests in a burst became {len(runs)} cycles"
