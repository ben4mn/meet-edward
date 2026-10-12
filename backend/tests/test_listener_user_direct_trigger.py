"""Immediate triage for direct pings (2026-07-07 heartbeat latency work).

Before this change only a literal ``@edward`` triggered an immediate triage
cycle — an ordinary direct ping waited for the scheduled cycle (up to
``triage_interval_seconds`` = 15 minutes). Now:

- A message the owner sends in their own self-chat (``imessage_owner_handles``)
  is a direct command channel: it triggers immediate triage AND is treated as
  an implicit mention instead of being dismissed as outbound (the mind path's
  ``identity_filter`` keeps an ``owner_direct`` event; the legacy pre-filter
  that once did this is gone).
- Any inbound 1:1 message triggers a debounced immediate cycle (gated by
  ``imessage_event_triggered_triage``) so ordinary texts don't wait 15 min.
"""

import time
from unittest.mock import AsyncMock, Mock

import pytest

from services.heartbeat import listener_imessage as listener


OWNER = "+15550001111"


@pytest.fixture(autouse=True)
def _reset_module_state():
    listener._last_seen_rowid = 0
    listener._last_event_triggered_triage = 0.0
    yield
    listener._last_seen_rowid = 0
    listener._last_event_triggered_triage = 0.0


def _msg(rowid=1, text="ping", is_from_me=True, chat_identifier=OWNER,
         is_group=False, sender_id=None):
    return {
        "rowid": rowid,
        "text": text,
        "attributed_body": None,
        "is_from_me": is_from_me,
        "date": int((time.time() - listener.APPLE_EPOCH_OFFSET) * 1e9),
        "service": "iMessage",
        "sender_id": sender_id,
        "chat_identifier": chat_identifier,
        "display_name": None,
        "style": 43 if is_group else 45,
        "guid": "g",
        "is_group": is_group,
    }


# ---------------------------------------------------------------------------
# _is_owner_direct — pure helper
# ---------------------------------------------------------------------------


def test_owner_self_chat_message_is_owner_direct():
    assert listener._is_owner_direct(_msg(), [OWNER]) is True


def test_group_chat_is_not_owner_direct():
    assert listener._is_owner_direct(_msg(is_group=True), [OWNER]) is False


def test_inbound_message_is_not_owner_direct():
    msg = _msg(is_from_me=False, sender_id="+15559998888",
               chat_identifier="+15559998888")
    assert listener._is_owner_direct(msg, [OWNER]) is False


def test_non_owner_chat_is_not_owner_direct():
    assert listener._is_owner_direct(_msg(chat_identifier="+15557775555"), [OWNER]) is False


def test_no_owner_handles_configured():
    assert listener._is_owner_direct(_msg(), []) is False
    assert listener._is_owner_direct(_msg(), None) is False


# ---------------------------------------------------------------------------
# _poll_once triggering
# ---------------------------------------------------------------------------


def _patch_poll(monkeypatch, store_result, messages=None):
    monkeypatch.setattr(
        listener, "_query_chat_db", lambda rowid: messages if messages is not None else [_msg()]
    )
    monkeypatch.setattr(listener, "_store_events", AsyncMock(return_value=store_result))
    monkeypatch.setattr(listener, "_persist_rowid", AsyncMock())
    monkeypatch.setattr(
        listener, "_load_listener_config", AsyncMock(return_value=([], True))
    )
    import services.heartbeat.heartbeat_service as hb

    # 2026-09-02: the listener no longer awaits the cycle inline (a long
    # cycle stalled the poll loop). It *requests* one; the runner in
    # heartbeat_service coalesces and runs it. Same triggering contract.
    trigger = Mock()
    monkeypatch.setattr(hb, "request_triage", trigger)
    return trigger


async def test_owner_direct_triggers_immediate_triage(monkeypatch):
    trigger = _patch_poll(
        monkeypatch,
        listener.StoreResult(stored=1, has_mentions=False,
                             has_owner_direct=True, has_inbound_direct=False),
    )
    await listener._poll_once()
    assert trigger.call_count == 1


async def test_inbound_direct_triggers_debounced_triage(monkeypatch):
    trigger = _patch_poll(
        monkeypatch,
        listener.StoreResult(stored=1, has_mentions=False,
                             has_owner_direct=False, has_inbound_direct=True),
    )
    await listener._poll_once()
    assert trigger.call_count == 1

    # Immediately again: debounced (min interval not elapsed)
    await listener._poll_once()
    assert trigger.call_count == 1

    # After the debounce interval: fires again
    listener._last_event_triggered_triage = (
        time.monotonic() - listener.EVENT_TRIGGERED_TRIAGE_MIN_INTERVAL_SECONDS - 1
    )
    await listener._poll_once()
    assert trigger.call_count == 2


async def test_mention_still_triggers(monkeypatch):
    trigger = _patch_poll(
        monkeypatch,
        listener.StoreResult(stored=1, has_mentions=True,
                             has_owner_direct=False, has_inbound_direct=False),
    )
    await listener._poll_once()
    assert trigger.call_count == 1


async def test_plain_outbound_does_not_trigger(monkeypatch):
    trigger = _patch_poll(
        monkeypatch,
        listener.StoreResult(stored=1, has_mentions=False,
                             has_owner_direct=False, has_inbound_direct=False),
    )
    await listener._poll_once()
    assert trigger.call_count == 0
