"""Today's reads and its two new buttons, against the test database.

The pure composer is pinned in ``tests/test_today_service.py``; this file
pins what ``get_today`` gathers for it (UX pass 2026-09-25) and the two
endpoints that make a card go away:

* ``POST /api/notes/{id}/dismiss`` — a note leaves Today now (expires).
* ``POST /api/today/heartbeat/{id}/done`` — a message card is stamped as
  seen (``notified_at``, batch ``owner:done``), the same mark the mind puts
  on what it considered, so neither Today nor the next wake lists it again.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       cookies={"edward_token": create_access_token()})


async def _hb(session, eid, chat, *, minutes_ago, status="escalated", **kw):
    from services.database import HeartbeatEventModel

    session.add(HeartbeatEventModel(
        id=eid, source="imessage", event_type="message_received", sender=kw.pop("sender", "x"),
        chat_identifier=chat, summary=kw.pop("summary", "hi"), triage_status=status,
        created_at=_utcnow_naive() - timedelta(minutes=minutes_ago), **kw))


# --- the reads ------------------------------------------------------------------

async def test_one_chatty_thread_cannot_hide_the_others(clean_db):
    """_heartbeat used to take ten rows before collapsing per thread."""
    from services.database import async_session
    from services.today_service import _heartbeat

    async with async_session() as s:
        for i in range(15):
            await _hb(s, f"busy-{i}", "chat-busy", minutes_ago=i + 1)
        await _hb(s, "quiet", "chat-quiet", minutes_ago=40)
        await s.commit()
    rows = await _heartbeat(datetime.now(timezone.utc))
    assert "quiet" in {r["id"] for r in rows}


async def test_scheduled_events_carry_their_type(clean_db):
    from services.database import ScheduledEventModel, async_session
    from services.today_service import _events, _fired_events

    now = _utcnow_naive()
    async with async_session() as s:
        s.add(ScheduledEventModel(id="tick", description="surplus_tick — spare quota", scheduled_at=now,
                                  next_fire_at=now + timedelta(minutes=10), recurrence_pattern="*/10 * * * *",
                                  status="pending", created_by="edward", last_fired_at=now - timedelta(minutes=5),
                                  event_metadata=json.dumps({"type": "surplus_tick"})))
        s.add(ScheduledEventModel(id="eve", description="evening", scheduled_at=now,
                                  next_fire_at=now + timedelta(hours=3), recurrence_pattern="0 18 * * *",
                                  status="pending", created_by="edward",
                                  event_metadata=json.dumps({"type": "wakeup_tick", "kind": "evening"})))
        s.add(ScheduledEventModel(id="vet", description="Remind Ellis to call the vet", scheduled_at=now,
                                  next_fire_at=now + timedelta(hours=1), status="pending", created_by="user",
                                  last_fired_at=now - timedelta(minutes=30)))
        await s.commit()
    start = datetime.now(timezone.utc) - timedelta(hours=18)
    fired = {r["id"]: r for r in await _fired_events(start)}
    assert fired["tick"]["type"] == "surplus_tick"
    assert fired["vet"]["type"] is None
    pending = {r["id"]: r for r in await _events()}
    assert pending["eve"]["type"] == "wakeup_tick" and pending["eve"]["kind"] == "evening"


async def test_triage_counts_carry_the_newest_message_time(clean_db):
    from services.database import async_session
    from services.today_service import _triage_counts

    async with async_session() as s:
        await _hb(s, "a", "c1", minutes_ago=50, status="dismissed")
        await _hb(s, "b", "c2", minutes_ago=7, status="noted")
        await s.commit()
    start = datetime.now(timezone.utc) - timedelta(hours=18)
    counts = await _triage_counts(start)
    assert counts["total"] == 2
    latest = datetime.fromisoformat(counts["latest_at"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - timedelta(minutes=7) - latest).total_seconds()) < 60


async def test_the_minds_wakes_come_from_its_ledger(clean_db):
    from services.database import LedgerEventModel, async_session
    from services.today_service import _mind_wakes

    now = _utcnow_naive()
    async with async_session() as s:
        s.add(LedgerEventModel(kind="mind", source="messages", outcome="acted", reason="Replied to Parker.",
                               created_at=now - timedelta(minutes=5)))
        s.add(LedgerEventModel(kind="mind", source="morning", outcome="left_alone", reason="Quiet.",
                               created_at=now - timedelta(hours=40)))
        s.add(LedgerEventModel(kind="pulse", source="cron", outcome="changed", created_at=now))
        await s.commit()
    rows = await _mind_wakes(datetime.now(timezone.utc) - timedelta(hours=18))
    assert [(r["source"], r["outcome"]) for r in rows] == [("messages", "acted")]
    # With nothing in the window the last wake is still known (presence).
    async with async_session() as s:
        from sqlalchemy import delete
        await s.execute(delete(LedgerEventModel).where(LedgerEventModel.source == "messages"))
        await s.commit()
    rows = await _mind_wakes(datetime.now(timezone.utc) - timedelta(hours=18))
    assert [r["source"] for r in rows] == ["morning"]


async def test_the_flags_live_notes_point_at_are_read_with_both_memories(clean_db):
    from services.database import MemoryFlagModel, MemoryModel, async_session
    from services.today_service import _note_flags

    async with async_session() as s:
        m1 = MemoryModel(content="Ellis's dentist is Dr. Novak", memory_type="fact", importance=0.6)
        m2 = MemoryModel(content="Ellis's dentist is Dr. Park", memory_type="fact", importance=0.6)
        s.add_all([m1, m2])
        await s.flush()
        open_flag = MemoryFlagModel(memory_id=m1.id, related_memory_id=m2.id, flag_type="contradiction",
                                    description="two dentists", resolved=False)
        done_flag = MemoryFlagModel(memory_id=m1.id, related_memory_id=m2.id, flag_type="contradiction",
                                    description="old", resolved=True)
        s.add_all([open_flag, done_flag])
        await s.commit()
        open_id, done_id = open_flag.id, done_flag.id
    notes = [{"id": "n1", "href": f"/settings/dreaming?flag={open_id}"},
             {"id": "n2", "href": f"/settings/memories?flag={done_id}"},
             {"id": "n3", "href": "/settings/dreaming?flag=gone"},
             {"id": "n4", "href": "/chat?c=edward-mind"}]
    flags = await _note_flags(notes)
    assert flags[open_id] == {"resolved": False, "memory_a": "Ellis's dentist is Dr. Novak",
                              "memory_b": "Ellis's dentist is Dr. Park"}
    assert flags[done_id]["resolved"] is True
    assert "gone" not in flags


async def test_get_today_carries_presence_overflow_and_a_compact_status(clean_db):
    from services.today_service import get_today

    feed = await get_today()
    assert set(feed["overflow"]) == {"note", "heartbeat"}
    assert isinstance(feed["did_since"], str) and feed["did_since"].endswith("Z")
    assert set(feed["presence"]) == {"listening", "last_look_at", "mind"}
    assert set(feed["status"]) <= {"running", "last_triage_at"}


# --- the buttons ----------------------------------------------------------------

async def test_done_on_a_note_expires_it_and_the_rest_of_its_card(clean_db):
    from services.notes_service import leave_note, recent_notes

    a = await leave_note(kind="mind", title="Which is right?", body="first?", needs_you=True)
    b = await leave_note(kind="mind", title="Which is right?", body="second?", needs_you=True)
    c = await leave_note(kind="mind", title="Unrelated", body="stays", needs_you=True)
    async with await _client() as client:
        r = await client.post(f"/api/notes/{a}/dismiss", json={"also": [b]})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "dismissed": 2}
        missing = await client.post("/api/notes/nope/dismiss")
        assert missing.status_code == 404
        listed = await client.get("/api/notes")
    live = {n.id for n in await recent_notes(hours=24, unexpired_only=True)}
    assert live == {c}
    assert [n["id"] for n in listed.json()["notes"]] == [c]


async def test_done_on_a_message_card_stamps_the_thread_seen(clean_db):
    from services.database import HeartbeatEventModel, async_session
    from services.today_service import OWNER_DONE_BATCH_ID, _heartbeat

    async with async_session() as s:
        await _hb(s, "m1", "chat-a", minutes_ago=9)
        await _hb(s, "m2", "chat-a", minutes_ago=3)
        await _hb(s, "late", "chat-a", minutes_ago=1)  # arrived after Today loaded
        await s.commit()
    async with await _client() as client:
        r = await client.post("/api/today/heartbeat/m2/done", json={"also": ["m1"]})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "done": 2}
        missing = await client.post("/api/today/heartbeat/nope/done")
        assert missing.status_code == 404
    async with async_session() as s:
        m2 = await s.get(HeartbeatEventModel, "m2")
        assert m2.notified_at is not None and m2.notification_batch_id == OWNER_DONE_BATCH_ID
    rows = await _heartbeat(datetime.now(timezone.utc))
    assert {r["id"] for r in rows} == {"late"}
