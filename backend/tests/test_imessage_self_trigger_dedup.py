"""
Tests for the iMessage self-trigger dedup ledger.

Bug: when Edward sends a reply via AppleScript, iCloud echoes the message
back into chat.db with is_from_me=True. The listener's old in-memory deque
(`_recent_edward_sends`) was unreliable (bounded to 20 entries, lost on
restart, brittle text-equality) — when it missed, Edward's own reply
containing the literal `@edward` string would fall through to the mention
check and self-trigger another triage cycle, causing a duplicate reply.

Fix: persist every outgoing send to `imessage_outgoing_sends` (chat_identifier,
text_hash, sent_at). The listener consults the ledger within a 120s window
before treating an is_from_me=True message as a user-device-initiated
mention. This preserves the critical user-from-iPhone path (no ledger entry
→ normal mention detection) while closing the self-trigger gap.

2026-10-11 (AREA 3): the deque is gone; echoes are recognised structurally
(``tests/test_imessage_echo.py``). A mention that is not an echo is now a
ping: with EDWARD_PINGS on it is recorded for the ping runner (``pings``),
with it off it asks for a triage cycle as before (``has_mentions``).
"""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.heartbeat import listener_imessage as listener
from services.heartbeat.listener_imessage import APPLE_EPOCH_OFFSET


def _apple_now() -> int:
    """Current moment as an Apple nanosecond timestamp — keeps the 24h age filter happy."""
    return int((datetime.now(timezone.utc).timestamp() - APPLE_EPOCH_OFFSET) * 1e9)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text[:200].encode("utf-8")).hexdigest()


async def _ensure_heartbeat_config_row():
    """Each test that touches the listener expects a HeartbeatConfig row for the
    persisted rowid helpers. Creating it is idempotent."""
    from services import database
    from sqlalchemy import select

    async with database.async_session() as session:
        existing = await session.execute(
            select(database.HeartbeatConfigModel).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(database.HeartbeatConfigModel(id="default"))
            await session.commit()


async def _insert_ledger(chat_identifier: str, text: str, when: datetime | None = None):
    from services import database

    async with database.async_session() as session:
        row = database.ImessageOutgoingSendModel(
            chat_identifier=chat_identifier,
            text_hash=_text_hash(text),
            sent_at=when or datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()


def _msg(rowid: int, text: str, *, is_from_me: bool, chat_identifier: str = "+15551230001"):
    return {
        "rowid": rowid,
        "text": text,
        "is_from_me": is_from_me,
        "date": _apple_now(),
        "service": "iMessage",
        "sender_id": chat_identifier,
        "chat_identifier": chat_identifier,
        "display_name": None,
        "is_group": False,
        "guid": f"g{rowid}",
    }


# ---------------------------------------------------------------------------
# New behavior — ledger-based dedup
# ---------------------------------------------------------------------------


async def test_self_send_with_matching_ledger_entry_does_not_trigger(clean_db, _no_contacts):
    """Edward's own @edward-containing reply, with a fresh ledger entry, must NOT trigger a new triage.

    RED: today the listener only checks the in-memory _recent_edward_sends
    deque. With the deque empty and `@edward` in the text, `has_mentions`
    flips to True. The fix is to consult the persistent ledger.
    """
    await _ensure_heartbeat_config_row()

    text = "Got it — I'll follow up on what @edward noted earlier."
    await _insert_ledger("+15551230099", text)

    result = await listener._store_events(
        [_msg(1001, text, is_from_me=True, chat_identifier="+15551230099")]
    )

    assert result.stored == 1, "Edward's echo is still stored, just not a mention"
    assert result.has_mentions is False, (
        "Ledger entry within 120s must suppress mention detection for "
        "is_from_me=True messages (Edward's own echo)."
    )


@pytest.fixture
def _no_contacts(monkeypatch):
    from unittest.mock import AsyncMock

    from services import imessage_sent

    imessage_sent.reset_for_tests()
    monkeypatch.setattr(listener, "_resolve_contact_name", AsyncMock(return_value=None))
    from services import contacts_service

    for name in ("lookup_phone", "lookup_handle"):
        monkeypatch.setattr(contacts_service, name, lambda *_a, **_k: {"success": True, "matches": []})
    monkeypatch.setattr(listener, "_schedule_attachment_description", lambda *a, **k: None)
    from services.pings import hooks as pings_hooks

    monkeypatch.setattr(pings_hooks, "kick", lambda: None)


async def _ping_rows():
    from sqlalchemy import text as sql

    from services import database

    async with database.async_session() as session:
        return [dict(r) for r in (await session.execute(sql("SELECT * FROM pings"))).mappings().all()]


@pytest.mark.parametrize("pings", ["on", "off"])
async def test_user_device_mention_without_ledger_still_triggers(clean_db, _no_contacts, monkeypatch, pings):
    """User sending @edward from another Apple-ID device (iPhone/laptop) must still trigger.

    is_from_me=True is not sufficient to skip — iCloud syncs user-typed
    mentions with is_from_me=True on the Mac Mini. Only a ledger match
    should suppress. This test guards against a regression toward the
    naive short-circuit.
    """
    monkeypatch.setenv("EDWARD_PINGS", pings)
    await _ensure_heartbeat_config_row()

    result = await listener._store_events(
        [_msg(1002, "@edward can you check the calendar?", is_from_me=True)]
    )

    [row] = await _ping_rows()
    assert row["from_owner"] is True and row["kind"] == "mention"
    if pings == "on":
        assert result.pings == 1 and row["status"] == "open", "the user-from-iPhone path is a ping"
    else:
        assert result.has_mentions is True and row["status"] == "legacy", (
            "Without a ledger entry, an is_from_me=True @edward message must "
            "still trigger triage — this is the user-from-iPhone path."
        )


async def test_ledger_entry_older_than_window_does_not_suppress(clean_db, _no_contacts, monkeypatch):
    """Stale ledger entries (>120s old) must not block mention detection."""
    monkeypatch.setenv("EDWARD_PINGS", "off")
    await _ensure_heartbeat_config_row()

    text = "@edward stale reminder"
    await _insert_ledger(
        "+15551230001",
        text,
        when=datetime.now(timezone.utc) - timedelta(seconds=300),
    )

    result = await listener._store_events(
        [_msg(1003, text, is_from_me=True)]
    )

    assert result.has_mentions is True, (
        "Ledger entries older than the 120s window must be ignored so "
        "retroactive self-sends don't mask real user mentions."
    )


async def test_incoming_mention_from_other_user_still_triggers(clean_db, _no_contacts, monkeypatch):
    """Regression guard: is_from_me=False with @edward always triggers (unchanged)."""
    monkeypatch.setenv("EDWARD_PINGS", "off")
    await _ensure_heartbeat_config_row()

    result = await listener._store_events(
        [_msg(1004, "hey @edward how are you", is_from_me=False)]
    )

    assert result.has_mentions is True
    assert result.stored == 1


async def test_incoming_mention_from_other_user_is_a_ping(clean_db, _no_contacts, monkeypatch):
    monkeypatch.delenv("EDWARD_PINGS", raising=False)
    await _ensure_heartbeat_config_row()

    result = await listener._store_events(
        [_msg(1005, "hey @edward how are you", is_from_me=False)]
    )

    assert result.pings == 1 and result.has_mentions is False
    [row] = await _ping_rows()
    assert row["third_party"] is True and row["sender_known"] is False


# ---------------------------------------------------------------------------
# send_imessage must populate the ledger
# ---------------------------------------------------------------------------


async def test_send_imessage_inserts_ledger_row_on_success(clean_db):
    """Successful AppleScript send must record the outgoing message in the ledger.

    Mocks subprocess.run to simulate a successful send without actually
    driving osascript. The ledger row is what the listener later consults.
    """
    from unittest.mock import MagicMock
    from services import imessage_service

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""

    with patch.object(imessage_service.subprocess, "run", return_value=fake_result):
        with patch.object(imessage_service, "is_available", return_value=True):
            result = imessage_service.send_imessage("+15551230042", "hello from edward")

    assert result["success"] is True

    # Give any async insert a chance to land (if implementation uses a task)
    # then verify the row exists.
    import asyncio
    await asyncio.sleep(0.1)

    from services import database
    from sqlalchemy import select

    async with database.async_session() as session:
        rows = await session.execute(
            select(database.ImessageOutgoingSendModel).where(
                database.ImessageOutgoingSendModel.text_hash == _text_hash("hello from edward")
            )
        )
        all_rows = rows.scalars().all()

    assert len(all_rows) == 1, (
        "send_imessage must insert exactly one ledger row for a successful send"
    )
    assert all_rows[0].chat_identifier == "+15551230042"


async def test_send_imessage_to_chat_inserts_ledger_row_on_success(clean_db):
    """Group-chat send path must also populate the ledger."""
    from unittest.mock import MagicMock
    from services import imessage_service

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""

    with patch.object(imessage_service.subprocess, "run", return_value=fake_result):
        with patch.object(imessage_service, "is_available", return_value=True):
            result = imessage_service.send_imessage_to_chat(
                "chat123456789", "group reply from edward"
            )

    assert result["success"] is True

    import asyncio
    await asyncio.sleep(0.1)

    from services import database
    from sqlalchemy import select

    async with database.async_session() as session:
        rows = await session.execute(
            select(database.ImessageOutgoingSendModel).where(
                database.ImessageOutgoingSendModel.text_hash
                == _text_hash("group reply from edward")
            )
        )
        all_rows = rows.scalars().all()

    assert len(all_rows) == 1
    # Routing may have normalized the chat_identifier; core check is row existed.
