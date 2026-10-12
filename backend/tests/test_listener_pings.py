"""The iMessage listener records pings (pings-media-spec §2.2).

For each new chat.db row the listener stores the heartbeat event as before
and, for a ping, writes a ``pings`` row in the same transaction under a
SAVEPOINT. A ping event never enters triage (``triage_status='ping'``), so
it never silences friends in the same batch (R2) and is answered by the
reply path, not a wake. A failure of the ping row costs only the ping row:
the message is stored and goes the old way (the mind's mention wake).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.listener_env import OWNER, ListenerEnv, raw

pytestmark = pytest.mark.integration

PARKER = "+12015550101"
ROWAN = "+12015550102"
STRANGER = "+12015550199"
GROUP = "chat556677"


@pytest.fixture
async def env(clean_db, tmp_path, monkeypatch):
    monkeypatch.delenv("EDWARD_PINGS", raising=False)
    e = await ListenerEnv.create(tmp_path, monkeypatch, contacts={PARKER: "Parker", ROWAN: "Rowan"})
    e.db.add_chat(GROUP, display_name="Trivia crew", is_group=True, participants=[PARKER, ROWAN])
    e.db.add_chat(PARKER)
    e.db.add_chat(OWNER)
    return e


async def test_a_friends_mention_is_a_ping_row_in_the_same_commit(env):
    rowid = env.db.add_message(GROUP, "@edward which of these used tickets look real?", sender=PARKER,
                               guid="MSG-GUID-1")
    await env.poll()

    event = await env.event(rowid)
    assert event is not None and event.id, "an explicit event id (R7)"
    assert event.triage_status == "ping", "a ping never enters triage, escalations or Today"
    [ping] = await env.pings()
    assert raw(event)["ping"] == {"kind": "mention", "ping_id": ping["id"]}
    assert ping["source_ref"] == f"imessage:{rowid}"
    assert ping["status"] == "open" and ping["kind"] == "mention" and ping["must_answer"] is True
    assert ping["from_owner"] is False and ping["third_party"] is True and ping["sender_known"] is True
    assert ping["heartbeat_event_id"] == event.id
    assert ping["message_rowid"] == rowid and ping["message_guid"] == "MSG-GUID-1"
    assert ping["chat_identifier"] == GROUP and ping["chat_guid"] == f"iMessage;+;{GROUP}"
    assert ping["chat_name"] == "Trivia crew" and ping["is_group"] is True
    assert ping["sender"] == PARKER and ping["sender_label"] == "Parker"
    assert ping["text"] == "@edward which of these used tickets look real?"
    msg_dt = datetime.fromtimestamp(env.db.message(rowid)["date"] / 1e9 + 978307200, tz=timezone.utc)
    assert abs((ping["asked_at"] - msg_dt.replace(tzinfo=None)).total_seconds()) < 1, "naive UTC message date"


async def test_kick_after_the_commit_and_no_triage_request(env, monkeypatch):
    order = []
    real = env.listener.async_session

    def tracking_session():
        s = real()
        orig = s.commit

        async def commit():
            await orig()
            order.append("commit")

        s.commit = commit
        return s

    monkeypatch.setattr(env.listener, "async_session", tracking_session)
    from services.pings import hooks as pings_hooks

    monkeypatch.setattr(pings_hooks, "kick", lambda: order.append("kick"))
    env.db.add_message(GROUP, "@Edward when's the 7pm set?", sender=PARKER)
    await env.poll()

    assert "kick" in order and order.index("kick") > order.index("commit")
    assert order.count("kick") == 1
    assert env.triage == [], "a ping is the reply path's, not a triage cycle's"


async def test_owners_ping_does_not_silence_a_friend_in_the_same_poll(env):
    """R2: Ellis's own @edward used to put the chat in replied_chats, and
    Parker's message in the same batch was dismissed as 'Ellis is already there'."""
    owner = env.db.add_message(GROUP, "@edward look at used tickets for weekend two", is_from_me=True)
    parker = env.db.add_message(GROUP, "also is anyone driving?", sender=PARKER)
    await env.poll()

    assert (await env.event(owner)).triage_status == "ping"
    assert (await env.event(parker)).triage_status == "pending"
    [ping] = await env.pings()
    assert ping["from_owner"] is True and ping["third_party"] is False and ping["sender_label"] == "Ellis"


async def test_owners_at_edward_in_a_friends_one_to_one_is_his_ping(env):
    rowid = env.db.add_message(PARKER, "@Edward what time does Parker land?", is_from_me=True)
    await env.poll()
    [ping] = await env.pings()
    assert ping["message_rowid"] == rowid and ping["kind"] == "mention" and ping["from_owner"] is True
    assert ping["chat_identifier"] == PARKER and ping["is_group"] is False


async def test_owners_self_chat_is_an_owner_direct_ping(env):
    rowid = env.db.add_message(OWNER, "remind me to call the vet tomorrow", is_from_me=True)
    await env.poll()
    event = await env.event(rowid)
    [ping] = await env.pings()
    assert ping["kind"] == "owner_direct" and ping["must_answer"] is True and ping["from_owner"] is True
    assert event.triage_status == "ping" and raw(event)["owner_direct"] is True


async def test_inline_replies_to_edwards_bubble(env):
    from services import imessage_sent

    imessage_sent.add(900, "EDWARD-BUBBLE", GROUP, imessage_sent.aware_utcnow())
    owner = env.db.add_message(GROUP, "and the other pair?", is_from_me=True, thread_originator_guid="EDWARD-BUBBLE")
    parker = env.db.add_message(GROUP, "lol thanks", sender=PARKER, thread_originator_guid="EDWARD-BUBBLE")
    other = env.db.add_message(GROUP, "same", sender=ROWAN, thread_originator_guid="SOMEONE-ELSE")
    await env.poll()

    pings = {p["message_rowid"]: p for p in await env.pings()}
    assert pings[owner]["kind"] == "reply_to_edward" and pings[owner]["must_answer"] is True
    assert pings[parker]["kind"] == "reply_to_edward" and pings[parker]["must_answer"] is False
    assert other not in pings and (await env.event(other)).triage_status == "pending"
    assert raw(await env.event(parker))["thread_originator_guid"] == "EDWARD-BUBBLE"


async def test_an_unknown_senders_mention_is_recorded_as_unknown(env):
    env.db.add_chat("chat999", display_name="Party", is_group=True, participants=[PARKER, STRANGER])
    env.db.add_message("chat999", "@edward who's bringing ice", sender=STRANGER)
    await env.poll()
    [ping] = await env.pings()
    assert ping["sender_known"] is False and ping["third_party"] is True and ping["sender"] == STRANGER


async def test_pings_off_writes_legacy_rows_and_keeps_the_old_path(env, monkeypatch):
    monkeypatch.setenv("EDWARD_PINGS", "off")
    rowid = env.db.add_message(GROUP, "@edward which set is first?", sender=PARKER)
    result = await env.store()

    [ping] = await env.pings()
    assert ping["status"] == "legacy"
    event = await env.event(rowid)
    assert event.triage_status == "pending" and "ping" not in raw(event)
    assert result.has_mentions is True and result.pings == 0
    assert env.kicks == []
    from services.pings import hooks as pings_hooks

    assert await pings_hooks.addressed_recently(GROUP, 24) is True


async def test_a_failing_ping_insert_still_stores_the_message(env):
    """create_ping hits a real IntegrityError (a trigger raising
    unique_violation): the savepoint rolls back, the event commits, and the
    message takes the old path."""
    from sqlalchemy import text

    from services import database

    async with database.async_session() as session:
        await session.execute(text(
            "CREATE OR REPLACE FUNCTION _a3a_fail_ping() RETURNS trigger AS $$ BEGIN "
            "RAISE EXCEPTION 'boom' USING ERRCODE = 'unique_violation'; END $$ LANGUAGE plpgsql"))
        await session.execute(text("DROP TRIGGER IF EXISTS _a3a_fail_ping ON pings"))
        await session.execute(text(
            "CREATE TRIGGER _a3a_fail_ping BEFORE INSERT ON pings FOR EACH ROW EXECUTE FUNCTION _a3a_fail_ping()"))
        await session.commit()
    try:
        before = env.listener._last_seen_rowid
        first = env.db.add_message(GROUP, "@edward hello?", sender=PARKER)
        second = env.db.add_message(GROUP, "anyone?", sender=ROWAN)
        await env.poll()
        assert env.listener._last_seen_rowid == second > before, "the watermark advances"
        e1, e2 = await env.event(first), await env.event(second)
        assert e1 is not None and e2 is not None
        assert e1.triage_status == "pending" and "ping" not in raw(e1)
        assert await env.pings() == []
        assert "@edward mention" in env.triage, "the mention goes the old way"
    finally:
        async with database.async_session() as session:
            await session.execute(text("DROP TRIGGER IF EXISTS _a3a_fail_ping ON pings"))
            await session.execute(text("DROP FUNCTION IF EXISTS _a3a_fail_ping()"))
            await session.commit()


async def test_create_ping_raising_outright_still_stores_the_message(env, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from services.pings import hooks as pings_hooks

    async def boom(*_a, **_k):
        raise IntegrityError("INSERT INTO pings", {}, Exception("duplicate"))

    monkeypatch.setattr(pings_hooks, "create_ping", boom)
    rowid = env.db.add_message(GROUP, "@edward hello?", sender=PARKER)
    await env.poll()
    event = await env.event(rowid)
    assert event is not None and event.triage_status == "pending"
    assert env.listener._last_seen_rowid == rowid


async def test_three_failed_polls_store_the_batch_degraded(env, monkeypatch, capsys):
    calls = {"n": 0}

    def poisoned(*_a, **_k):
        calls["n"] += 1
        raise ValueError("a row this code cannot classify")

    monkeypatch.setattr(env.listener, "_classify", poisoned)
    rowid = env.db.add_message(GROUP, "@edward hello?", sender=PARKER)
    att_msg = env.db.add_message(GROUP, None, sender=ROWAN)
    env.db.add_attachment(att_msg, filename="/tmp/a3a-none.heic", mime_type="image/heic")

    for _ in range(2):
        with pytest.raises(ValueError):
            await env.poll()
        assert env.listener._last_seen_rowid == 0
    await env.poll()

    out = capsys.readouterr().out
    assert f"[pings] poison batch up to rowid {att_msg}: stored without ping registration after 3 failures" in out
    assert "ValueError" in out
    assert env.listener._last_seen_rowid == att_msg
    event = await env.event(rowid)
    assert event.triage_status == "pending" and "ping" not in raw(event)
    assert await env.pings() == []
    assert "@edward mention" in env.triage, "the mention wake answers it"
    await env.settle()
    assert env.described == [], "no describer in the degraded store"


async def test_the_poison_count_restarts_after_a_good_store(env, monkeypatch):
    env.db.add_message(GROUP, "first", sender=PARKER)
    real = env.listener._classify
    fail = {"on": True}

    def flaky(*a, **k):
        if fail["on"]:
            raise ValueError("flaky")
        return real(*a, **k)

    monkeypatch.setattr(env.listener, "_classify", flaky)
    for _ in range(2):
        with pytest.raises(ValueError):
            await env.poll()
    fail["on"] = False
    await env.poll()
    assert env.listener._store_failures == {}


async def test_the_describer_gets_the_real_event_id_after_the_commit(env, monkeypatch):
    """R7: the describer was scheduled with ``event.id`` before the flush, so
    it got None and no photo was ever described."""
    from services import database

    seen = []

    async def describe(event_id, attachments):
        async with database.async_session() as session:
            seen.append((event_id, await session.get(database.HeartbeatEventModel, event_id), attachments))

    monkeypatch.setattr(env.listener, "_describe_event_attachments", describe)
    rowid = env.db.add_message(GROUP, "look", sender=PARKER)
    att = env.db.add_attachment(rowid, filename="/tmp/a3a-IMG_1.heic", mime_type="image/heic")
    await env.poll()
    await env.settle()

    [(event_id, stored, attachments)] = seen
    assert event_id and stored is not None, "the event is committed and found by that id"
    assert attachments[0]["rowid"] == att


async def test_raw_data_carries_guids_and_up_to_4000_characters(env):
    long_text = "word " * 1000
    rowid = env.db.add_message(GROUP, long_text, sender=PARKER, guid="LONG-1", thread_originator_guid="T-1")
    await env.poll()
    data = raw(await env.event(rowid))
    assert data["message_guid"] == "LONG-1" and data["thread_originator_guid"] == "T-1"
    assert len(data["full_text"]) == 4000


async def test_a_mention_deep_in_a_long_body_only_message_is_a_ping(env):
    """R9 + R4 end to end: an attributedBody-only message over 128 bytes with
    the ask at character 524 is decoded whole and recorded whole."""
    head = ("so we were going back and forth about the weekend and nobody can decide "
            * 10)[:523] + " "
    text = head + "@edward can you check the used ticket prices?"
    rowid = env.db.add_message(GROUP, text, sender=PARKER, body_only=True)
    await env.poll()
    [ping] = await env.pings()
    assert ping["message_rowid"] == rowid and ping["text"] == text


async def test_tapbacks_are_never_ingested_or_pings(env):
    target = env.db.add_message(GROUP, "@edward look at this", sender=PARKER, guid="T-GUID")
    await env.poll()
    tapback = env.db.add_message(GROUP, "Loved “@edward look at this”", sender=ROWAN,
                                 associated_message_type=2000, thread_originator_guid="T-GUID")
    owner_tap = env.db.add_message(OWNER, "Emphasized “ok”", is_from_me=True, associated_message_type=2004)
    await env.poll()
    assert await env.event(tapback) is None and await env.event(owner_tap) is None
    assert [p["message_rowid"] for p in await env.pings()] == [target]


async def test_the_self_chat_breaker(env, capsys):
    """An Edward send into the self-chat whose row was never found: for 60 s a
    self-chat line without @edward is not a ping."""
    from services import imessage_sent

    token = imessage_sent.begin(OWNER, "Booked the vet for 9.")
    imessage_sent.resolve(token)  # no row found -> unconfirmed
    quiet = env.db.add_message(OWNER, "Booked the vet for nine", is_from_me=True)
    loud = env.db.add_message(OWNER, "@edward and the groomer?", is_from_me=True)
    await env.poll()
    pings = {p["message_rowid"]: p for p in await env.pings()}
    assert quiet not in pings and pings[loud]["kind"] == "mention"
    assert raw(await env.event(quiet))["owner_direct"] is False
    assert "[pings] breaker" in capsys.readouterr().out


async def test_an_ordinary_inbound_one_to_one_still_requests_triage(env):
    env.db.add_message(PARKER, "are you around later?", sender=PARKER)
    await env.poll()
    assert env.triage == ["inbound 1:1 message"]
    assert await env.pings() == []


async def test_a_friends_one_to_one_mention_is_not_counted_as_inbound_direct(env):
    env.db.add_message(PARKER, "@edward what's Ellis's flight?", sender=PARKER)
    result = await env.store()
    assert result.pings == 1 and result.has_inbound_direct is False and result.has_mentions is False


async def test_the_old_reasons_still_come_from_non_ping_rows(env, monkeypatch):
    """Flag off, Ellis's own self-chat line is an owner-direct request again."""
    monkeypatch.setenv("EDWARD_PINGS", "off")
    env.db.add_message(OWNER, "what's on today", is_from_me=True)
    result = await env.store()
    assert result.has_owner_direct is True and result.pings == 0


async def test_dedup_by_source_id_and_idempotent_ping(env):
    env.db.add_message(GROUP, "@edward hi", sender=PARKER)
    await env.store()
    env.listener._last_seen_rowid = 0
    await env.store()
    assert len(await env.events()) == 1 and len(await env.pings()) == 1


# ---------------------------------------------------------------------------
# Media registration through the step-0 hook (inert until P1b's store lands)
# ---------------------------------------------------------------------------


class _FakeMedia:
    def __init__(self, fail=False):
        self.registered = []
        self.enqueued = []
        self.fail = fail

    async def register_imessage(self, session, *, event_id, msg):
        from sqlalchemy import text

        found = (await session.execute(text("SELECT count(*) FROM heartbeat_events WHERE id = :id"),
                                       {"id": event_id})).scalar_one()
        self.registered.append((event_id, msg["rowid"], found))
        if self.fail:
            await session.execute(text("SELECT 1/0"))
        return [100 + len(self.registered)]

    def enqueue(self, ids, *, priority="background"):
        self.enqueued.append((list(ids), priority))


@pytest.fixture
def fake_media(monkeypatch):
    from services.media import hooks as media_hooks

    fake = _FakeMedia()
    monkeypatch.delenv("EDWARD_MEDIA", raising=False)
    monkeypatch.setattr(media_hooks, "_mod", lambda name: fake if name in ("store", "pipeline") else None)
    return fake


async def test_media_is_registered_in_the_transaction_and_enqueued_after_the_commit(env, fake_media):
    plain = env.db.add_message(GROUP, "no media here", sender=PARKER)
    photo = env.db.add_message(GROUP, "look", sender=PARKER)
    env.db.add_attachment(photo, filename="/tmp/a3a-IMG_2.heic", mime_type="image/heic")
    echo = env.db.add_message(GROUP, "sent you this\n\n- Edward 🤖", is_from_me=True)
    env.db.add_attachment(echo, filename="/tmp/a3a-out.png", mime_type="image/png", is_outgoing=True)
    await env.poll()

    event = await env.event(photo)
    assert [(e, r) for e, r, _ in fake_media.registered] == [(event.id, photo)]
    assert fake_media.registered[0][2] == 1, "the event is flushed before the media hook"
    assert fake_media.enqueued == [([101], "background")]
    assert (await env.event(plain)) is not None


async def test_a_failing_media_register_still_stores_the_message(env, monkeypatch, fake_media):
    fake_media.fail = True
    photo = env.db.add_message(GROUP, "@edward what is this?", sender=PARKER)
    env.db.add_attachment(photo, filename="/tmp/a3a-IMG_3.heic", mime_type="image/heic")
    await env.poll()
    event = await env.event(photo)
    assert event is not None and event.triage_status == "ping", "the savepoint kept the rest of the transaction"
    assert fake_media.enqueued == []
    assert len(await env.pings()) == 1


async def test_media_off_or_degraded_registers_nothing(env, monkeypatch, fake_media):
    monkeypatch.setenv("EDWARD_MEDIA", "off")
    from services.media import hooks as media_hooks

    monkeypatch.setattr(media_hooks, "_mod", lambda name: None if not media_hooks.enabled() else fake_media)
    photo = env.db.add_message(GROUP, "look", sender=PARKER)
    env.db.add_attachment(photo, filename="/tmp/a3a-IMG_4.heic", mime_type="image/heic")
    await env.poll()
    assert fake_media.registered == []
    monkeypatch.delenv("EDWARD_MEDIA")
    second = env.db.add_message(GROUP, "again", sender=PARKER)
    env.db.add_attachment(second, filename="/tmp/a3a-IMG_5.heic", mime_type="image/heic")
    await env.store(degraded=True)
    assert fake_media.registered == []


async def test_a_link_in_a_ping_carries_its_preview_metadata(env):
    from tests.fixtures.fake_chatdb import URL_BALLOON, link_payload

    rowid = env.db.add_message(GROUP, "@edward are these legit? https://tix.example/fest", sender=PARKER,
                               balloon_bundle_id=URL_BALLOON,
                               payload_data=link_payload(url="https://tix.example/fest", title="Riverside Fest resale",
                                                         site="Tix"))
    att = env.db.add_attachment(rowid, filename="/tmp/a3a-L.pluginPayloadAttachment", mime_type=None)
    await env.poll()
    data = raw(await env.event(rowid))
    assert data["link"]["title"] == "Riverside Fest resale" and data["link"]["url"] == "https://tix.example/fest"
    [ping] = await env.pings()
    assert ping["text"] == f'@edward are these legit? https://tix.example/fest [link: "Riverside Fest resale" (Tix) attachment:{att}]'
