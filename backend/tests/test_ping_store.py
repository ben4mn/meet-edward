"""The ping store: claim, mark, due, recovery reads, limits, the dedupe,
the mind's pack lines, the report and the prune (AREA 3, 2026-10-11).

``pings`` times are naive UTC; the send ledger's are timestamptz and are
always bound aware (on this Central-time Mac a naive cutoff missed a row
stamped now). Nothing here reaches chat.db, osascript or a model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from services import imessage_sent
from services.pings import hooks, store


@pytest.fixture(autouse=True)
def _fresh_index():
    imessage_sent.reset_for_tests()
    yield
    imessage_sent.reset_for_tests()


def _ago(**delta) -> datetime:
    return hooks.utcnow() - timedelta(**delta)


async def _ping(ref: str, **kw) -> int:
    from services.database import async_session

    fields = dict(channel="imessage", kind="mention", from_owner=False, third_party=True, sender_known=True,
                  asked_at=_ago(seconds=5), chat_identifier="chat123", chat_name="Trivia crew", is_group=True,
                  sender="+12015550101", sender_label="Parker", text="@edward what time is it")
    fields.update(kw)
    async with async_session() as session:
        pid = await hooks.create_ping(session, source_ref=ref, **fields)
        await session.commit()
    return pid


async def _send_row(*, chat="chat123", ping_id=None, purpose="reply", ok=True, sent_at=None, message="hello",
                    rowid=None):
    from services.database import async_session

    async with async_session() as session:
        await session.execute(text(
            "INSERT INTO imessage_outgoing_sends (chat_identifier, text_hash, sent_at, ok, text_norm_hash, ping_id, "
            "purpose, message_rowid) VALUES (:c, 'x', :at, :ok, :h, :p, :purpose, :rowid)"),
            {"c": chat, "at": sent_at or datetime.now(timezone.utc), "ok": ok, "h": imessage_sent.text_hash(message),
             "p": ping_id, "purpose": purpose, "rowid": rowid})
        await session.commit()


async def test_claim_moves_open_to_running_once(clean_db):
    pid = await _ping("imessage:1")
    row = await store.claim(pid)
    assert row["status"] == "running" and row["attempts"] == 1 and row["started_at"] is not None
    assert await store.claim(pid) is None


async def test_due_lists_open_rows_whose_time_has_come_oldest_first(clean_db):
    later = await _ping("imessage:2", asked_at=_ago(seconds=10))
    first = await _ping("imessage:1", asked_at=_ago(seconds=60))
    waiting = await _ping("imessage:3", asked_at=_ago(seconds=90))
    await store.mark(waiting, next_attempt_at=hooks.utcnow() + timedelta(seconds=30))
    done = await _ping("imessage:4", asked_at=_ago(seconds=120))
    await store.mark(done, status="answered")
    legacy = await _ping("imessage:5", status="legacy")

    ids = [r["id"] for r in await store.due(hooks.utcnow(), limit=10)]
    assert ids == [first, later]
    assert legacy not in ids


async def test_mark_sets_fields_and_ignores_unknown_ones(clean_db):
    pid = await _ping("imessage:1")
    await store.mark(pid, status="answered", latency_s=12.5, reply_route="chat_id", bogus="x")
    row = await store.get(pid)
    assert row["status"] == "answered" and row["latency_s"] == 12.5 and row["reply_route"] == "chat_id"


async def test_running_lists_running_rows(clean_db):
    pid = await _ping("imessage:1")
    await store.claim(pid)
    await _ping("imessage:2")
    assert [r["id"] for r in await store.running()] == [pid]


async def test_the_ledger_answer_for_a_ping_ignores_acks_and_failures(clean_db):
    pid = await _ping("imessage:1")
    await _send_row(ping_id=pid, purpose="ack")
    await _send_row(ping_id=pid, purpose="reply", ok=False)
    assert await store.answered_in_ledger(pid) is None
    await _send_row(ping_id=pid, purpose="tool", rowid=77)
    found = await store.answered_in_ledger(pid)
    assert found["message_rowid"] == 77


async def test_the_ledger_prefers_the_runners_own_delivery_for_recovery(clean_db):
    """A turn's "On it" (``tool``) and then the runner's own reply: recovery
    must see the runner's, which settled the ping."""
    pid = await _ping("imessage:1")
    await _send_row(ping_id=pid, purpose="tool", rowid=70, sent_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    await _send_row(ping_id=pid, purpose="fallback", rowid=71)
    found = await store.answered_in_ledger(pid)
    assert found["message_rowid"] == 71 and found["purpose"] == "fallback"


async def test_the_loop_counts_each_party_on_its_own(clean_db):
    for i in range(2):
        await _ping(f"imessage:f{i}", asked_at=_ago(minutes=1))
    for i in range(3):
        await _ping(f"imessage:b{i}", asked_at=_ago(minutes=1), from_owner=True, third_party=False)
    assert await store.pings_in_chat_since("chat123", _ago(minutes=5), third_party=True) == 2
    assert await store.pings_in_chat_since("chat123", _ago(minutes=5), third_party=False) == 3
    assert await store.pings_in_chat_since("chat123", _ago(minutes=5)) == 5


async def test_limit_counts(clean_db):
    for i in range(3):
        await _ping(f"imessage:{i}", asked_at=_ago(minutes=1))
    await _ping("imessage:old", asked_at=_ago(minutes=9))
    await _ping("imessage:other", chat_identifier="chat999", asked_at=_ago(minutes=1))
    assert await store.pings_in_chat_since("chat123", _ago(minutes=5)) == 3

    a = await _ping("imessage:t1", asked_at=_ago(minutes=30))
    b = await _ping("imessage:t2", asked_at=_ago(minutes=20), from_owner=True, third_party=False)
    await store.claim(a)
    await store.claim(b)
    assert await store.third_party_turns_in_chat_since("chat123", _ago(hours=1)) == 1


async def test_the_dedupe_window_is_aware_and_chat_spelling_free(clean_db):
    await _send_row(chat="any;+;chat123", message="Answer - Edward 🤖")
    since = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert await store.recent_ok_send("chat123", imessage_sent.text_hash("answer"), since) is not None
    assert await store.recent_ok_send("chat999", imessage_sent.text_hash("answer"), since) is None
    assert await store.recent_ok_send("chat123", imessage_sent.text_hash("other"), since) is None


async def test_the_dedupe_is_one_pings_own(clean_db):
    """Review 2026-10-11 (verified): one ping's timeout template made the next
    ping's, three minutes later, a "deduped" no-op — its asker got nothing."""
    await _send_row(ping_id=1, message="Sorry — this took longer - Edward 🤖")
    since = datetime.now(timezone.utc) - timedelta(minutes=10)
    h = imessage_sent.text_hash("Sorry — this took longer - Edward 🤖")
    assert await store.recent_ok_send("chat123", h, since, ping_id=1) is not None
    assert await store.recent_ok_send("chat123", h, since, ping_id=2) is None


async def test_the_dedupe_skips_a_three_hour_old_row_and_failed_rows(clean_db):
    await _send_row(message="Answer", sent_at=datetime.now(timezone.utc) - timedelta(hours=3))
    await _send_row(message="Answer", ok=False)
    since = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert await store.recent_ok_send("chat123", imessage_sent.text_hash("Answer"), since) is None


async def test_pack_lines(clean_db, monkeypatch):
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: timezone.utc)
    asked = datetime(2026, 10, 7, 19, 28, 0)
    pid = await _ping("imessage:1", from_owner=True, third_party=False, sender_label="Ellis", asked_at=asked,
                      text="@edward look at used tickets for Riverside Fest on Saturday, find the cheapest please")
    await store.mark(pid, status="answered", answered_at=asked + timedelta(seconds=61), latency_s=61.0)
    lines = await store.pack_lines(asked + timedelta(minutes=5), hours=6)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith('7:28 pm Ellis in Trivia crew "@edward look at used tickets')
    assert line.endswith("→ answered 7:29 pm (61 s)")


async def test_the_report(clean_db):
    now = hooks.utcnow()
    a = await _ping("imessage:1", from_owner=True, third_party=False, asked_at=now - timedelta(minutes=30))
    await store.mark(a, status="answered", latency_s=40.0, answered_at=now - timedelta(minutes=29))
    b = await _ping("imessage:2", kind="owner_direct", from_owner=True, third_party=False,
                    asked_at=now - timedelta(minutes=40), message_rowid=555)
    await store.mark(b, status="fallback", latency_s=300.0, answered_at=now - timedelta(minutes=35))
    c = await _ping("imessage:3", asked_at=now - timedelta(minutes=20))
    await store.claim(c)
    await _ping("imessage:4", asked_at=now - timedelta(minutes=2))
    await _ping("imessage:5", asked_at=now - timedelta(days=9))
    imessage_sent.add(555, None, "chat123", None)

    rep = await store.report(7)
    assert rep["days"] == 7 and rep["total"] == 4
    assert rep["by_status"]["answered"] == 1 and rep["by_status"]["fallback"] == 1
    assert rep["by_status"]["running"] == 1 and rep["by_status"]["open"] == 1
    assert set(rep["by_status"]) >= {"answered", "fallback", "declined", "failed", "held", "limited",
                                     "expired", "legacy", "open", "running"}
    assert rep["by_who"] == {"owner": 1, "self_chat": 1, "friend": 2}
    assert rep["answered_within_2m"] == 1
    assert rep["median_latency_s"] == 170
    assert [o["id"] for o in rep["open_now"]] == [c]
    assert rep["open_now"][0]["chat"] == "Trivia crew" and rep["open_now"][0]["age_s"] >= 1199
    assert rep["self_replies"] == 1
    assert len(rep["by_day"]) == 7
    assert sum(d["total"] for d in rep["by_day"]) == 4
    assert sum(d["answered"] for d in rep["by_day"]) == 1


async def test_prune_keeps_ninety_days(clean_db):
    old = await _ping("imessage:old", asked_at=_ago(days=91))
    fresh = await _ping("imessage:fresh")
    await _send_row(sent_at=datetime.now(timezone.utc) - timedelta(days=91))
    await _send_row()
    counts = await store.prune(days=90)
    assert counts == {"pings": 1, "sends": 1}
    assert await store.get(old) is None and await store.get(fresh) is not None
