"""Edward's own sends coming back are recognised structurally (pings-media-spec §2.3).

Edward sends from Ellis's Apple ID, so each send comes back to the listener
as an is_from_me row — the same shape as Ellis typing on his phone. R10:
text-based echo detection failed and Edward answered himself. Now the
listener checks, first match wins: the chat.db ROWID the transport read
back (``rowid``), an unresolved send to this chat with the same text
(``inflight``), an ok ledger row with the same normalised hash in the last
120 s (``hash``, aware UTC), then the signature. An echo is stored
``dismissed`` with ``raw_data.edward_echo`` and is never a ping.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.listener_env import OWNER, ListenerEnv, raw

pytestmark = pytest.mark.integration

PARKER = "+12015550101"
GROUP = "chat556677"


@pytest.fixture
async def env(clean_db, tmp_path, monkeypatch):
    monkeypatch.delenv("EDWARD_PINGS", raising=False)
    e = await ListenerEnv.create(tmp_path, monkeypatch, contacts={PARKER: "Parker"})
    e.db.add_chat(GROUP, display_name="Trivia crew", is_group=True, participants=[PARKER])
    e.db.add_chat(OWNER)
    return e


async def _ledger(chat, text, *, ok=True, when=None, norm=True):
    from services import database, imessage_sent

    async with database.async_session() as session:
        session.add(database.ImessageOutgoingSendModel(
            chat_identifier=chat, text_hash=hashlib.sha256(text[:200].encode()).hexdigest(),
            text_norm_hash=imessage_sent.text_hash(text) if norm else None, ok=ok,
            sent_at=when or datetime.now(timezone.utc)))
        await session.commit()


async def test_a_rowid_the_transport_read_back_is_an_echo(env):
    from services import imessage_sent

    rowid = env.db.add_message(GROUP, "@edward said the 7pm set is sold out", is_from_me=True)
    imessage_sent.add(rowid, "G1", GROUP, imessage_sent.aware_utcnow())
    await env.poll()
    event = await env.event(rowid)
    assert raw(event)["edward_echo"] == "rowid" and event.triage_status == "dismissed"
    assert await env.pings() == [] and env.triage == [] and env.kicks == []


async def test_an_inflight_send_is_claimed_by_the_listener(env):
    from services import imessage_sent

    token = imessage_sent.begin(GROUP, "On it @edward style — checking now")
    rowid = env.db.add_message(GROUP, "On it @edward style — checking now", is_from_me=True)
    await env.poll()
    assert raw(await env.event(rowid))["edward_echo"] == "inflight"
    assert imessage_sent.claimed_rowid(token) == rowid
    assert imessage_sent.is_edward_rowid(rowid)
    assert await env.pings() == []


async def test_an_ok_ledger_row_with_the_same_normalised_text_is_an_echo(env):
    await _ledger(GROUP, "Checking @edward prices now\n\n- Edward 🤖")
    rowid = env.db.add_message(GROUP, "checking  @edward prices now", is_from_me=True)
    await env.poll()
    assert raw(await env.event(rowid))["edward_echo"] == "hash"
    assert await env.pings() == []


async def test_the_hash_check_trusts_only_ok_rows(env):
    await _ledger(GROUP, "@edward retry later", ok=False)
    rowid = env.db.add_message(GROUP, "@edward retry later", is_from_me=True)
    await env.poll()
    assert "edward_echo" not in raw(await env.event(rowid))
    [ping] = await env.pings()
    assert ping["from_owner"] is True


async def test_the_hash_window_is_aware_and_120_seconds(env):
    await _ledger(GROUP, "@edward old news", when=datetime.now(timezone.utc) - timedelta(hours=3))
    rowid = env.db.add_message(GROUP, "@edward old news", is_from_me=True)
    await env.poll()
    assert "edward_echo" not in raw(await env.event(rowid))
    assert len(await env.pings()) == 1


async def test_the_hash_must_be_for_the_same_chat(env):
    await _ledger("chat-other", "@edward ok")
    rowid = env.db.add_message(GROUP, "@edward ok", is_from_me=True)
    await env.poll()
    assert "edward_echo" not in raw(await env.event(rowid))


async def test_a_legacy_ledger_row_without_a_norm_hash_still_matches(env):
    await _ledger(GROUP, "@edward noted, thanks", norm=False)
    rowid = env.db.add_message(GROUP, "@edward noted, thanks", is_from_me=True)
    await env.poll()
    assert raw(await env.event(rowid))["edward_echo"] == "hash"


async def test_a_signed_from_me_row_is_an_echo(env):
    rowid = env.db.add_message(GROUP, "Here's the list @edward made.\n\n- Edward 🤖", is_from_me=True)
    await env.poll()
    assert raw(await env.event(rowid))["edward_echo"] == "signature"
    assert await env.pings() == []


async def test_a_signed_caption_with_an_attachment_is_an_echo(env):
    """The listener appends attachment placeholders after the caption; the
    echo checks read the caption, or the placeholder hides the signature."""
    rowid = env.db.add_message(GROUP, "Here's the map @edward found\n\n- Edward 🤖\ufffc", is_from_me=True)
    env.db.add_attachment(rowid, filename="/tmp/a3a-map.png", mime_type="image/png", is_outgoing=True)
    await env.poll()
    event = await env.event(rowid)
    assert raw(event)["edward_echo"] == "signature" and "attachment:" in raw(event)["full_text"]
    assert await env.pings() == []


async def test_a_friends_signed_looking_message_is_not_an_echo(env):
    """Only from-me rows can be echoes."""
    rowid = env.db.add_message(GROUP, "@edward thanks - Edward", sender=PARKER)
    await env.poll()
    assert "edward_echo" not in raw(await env.event(rowid))
    assert len(await env.pings()) == 1


async def test_owners_message_in_the_same_chat_5_seconds_later_is_not_an_echo(env):
    from services import imessage_sent

    echo = env.db.add_message(OWNER, "Booked the vet for 9.", is_from_me=True)
    imessage_sent.add(echo, "G-ECHO", OWNER, imessage_sent.aware_utcnow())
    owner = env.db.add_message(OWNER, "great, and the groomer?", is_from_me=True,
                             date=datetime.now(timezone.utc) + timedelta(seconds=5))
    await env.poll()
    assert raw(await env.event(echo))["edward_echo"] == "rowid"
    assert "edward_echo" not in raw(await env.event(owner))
    [ping] = await env.pings()
    assert ping["message_rowid"] == owner and ping["kind"] == "owner_direct"


async def test_the_self_chat_round_trip_does_not_answer_itself(env):
    """End to end on the listener's side: Ellis asks in his self-chat, Edward's
    reply (unsigned, the hardest case) is sent through the transport's
    bookkeeping and comes back as a from-me row, and is not a ping; Ellis's
    follow-up is."""
    from services import imessage_sent

    ask = env.db.add_message(OWNER, "what's the weather tomorrow", is_from_me=True)
    await env.poll()
    reply = "Sunny, 74 and dry."
    token = imessage_sent.begin(OWNER, reply)
    echo = env.db.add_message(OWNER, reply, is_from_me=True)
    await env.poll()
    imessage_sent.resolve(token, rowid=imessage_sent.claimed_rowid(token))
    follow = env.db.add_message(OWNER, "and Sunday?", is_from_me=True)
    await env.poll()

    kinds = {p["message_rowid"]: p["kind"] for p in await env.pings()}
    assert kinds == {ask: "owner_direct", follow: "owner_direct"}
    assert raw(await env.event(echo))["edward_echo"] == "inflight"
    assert not imessage_sent.unconfirmed_recently(OWNER), "the read-back found the row"


async def test_the_transport_round_trip_when_it_is_merged(env, monkeypatch):
    """With Agent B's transport merged: the runner's self-chat reply, written
    into the fake chat.db by a fake ``_run_osascript``, comes back and is not a ping."""
    import subprocess

    from services import imessage_service

    if not hasattr(imessage_service, "send_to_chat") or not hasattr(imessage_service, "_run_osascript"):
        pytest.skip("the transport (Agent B) is not merged on this branch")

    sent = []

    def fake_osascript(script, timeout=30, **_k):
        sent.append(script)
        env.db.add_message(OWNER, "Sunny, 74 and dry.\n\n- Edward 🤖", is_from_me=True)
        return subprocess.CompletedProcess(args=["osascript"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(imessage_service, "_run_osascript", fake_osascript)
    if hasattr(imessage_service, "is_available"):
        monkeypatch.setattr(imessage_service, "is_available", lambda: True)
    if hasattr(imessage_service, "CHAT_DB_PATH"):
        monkeypatch.setattr(imessage_service, "CHAT_DB_PATH", str(env.db.path))

    import asyncio

    result = await asyncio.to_thread(imessage_service.send_to_chat, OWNER, "Sunny, 74 and dry.\n\n- Edward 🤖",
                                     purpose="reply")
    assert sent and result.get("success")
    await env.poll()
    assert await env.pings() == []
    [event] = await env.events()
    assert raw(event).get("edward_echo") in ("rowid", "inflight", "hash", "signature")


def test_readers_label_edwards_lines(tmp_path, monkeypatch):
    """get_chat_thread and get_recent_messages_across_chats say which lines
    are Edward's: a ROWID the transport read back, or from-me and signed."""
    from services import imessage_sent
    from services.heartbeat import listener_imessage as listener
    from tests.fixtures.fake_chatdb import FakeChatDB

    imessage_sent.reset_for_tests()
    db = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    db.add_chat(GROUP, display_name="Trivia crew", is_group=True, participants=[PARKER])
    known = db.add_message(GROUP, "Sunny, 74 and dry.", is_from_me=True)
    imessage_sent.add(known, "G", GROUP, imessage_sent.aware_utcnow())
    signed = db.add_message(GROUP, "Booked.\n\n- Edward 🤖", is_from_me=True)
    owner = db.add_message(GROUP, "thanks!", is_from_me=True)
    parker = db.add_message(GROUP, "nice - Edward", sender=PARKER)
    try:
        thread = {m["rowid"]: m["by_edward"] for m in listener.get_chat_thread(GROUP, limit=10)}
        assert thread == {known: True, signed: True, owner: False, parker: False}
        recent = {m["rowid"]: m["by_edward"] for m in listener.get_recent_messages_across_chats(hours=1, limit=10)}
        assert recent == thread
    finally:
        imessage_sent.reset_for_tests()


async def test_startup_loads_the_echo_index_and_notes_the_loop(tmp_path, monkeypatch):
    import asyncio

    from services import imessage_sent, imessage_service
    from services.heartbeat import listener_imessage as listener
    from tests.fixtures.fake_chatdb import FakeChatDB

    FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    calls = []

    async def fake_load(days=30):
        calls.append(("load", days))
        return 3

    async def idle_loop():
        await asyncio.sleep(3600)

    monkeypatch.setattr(imessage_sent, "load", fake_load)
    monkeypatch.setattr(imessage_service, "note_main_loop", lambda loop: calls.append(("loop", loop)),
                        raising=False)
    monkeypatch.setattr(listener, "_listener_loop", idle_loop)
    monkeypatch.setattr(listener, "_listener_task", None)
    await listener.start_imessage_listener()
    try:
        assert ("load", 30) in calls
        assert ("loop", asyncio.get_running_loop()) in calls
        assert calls.index(("loop", asyncio.get_running_loop())) < calls.index(("load", 30))
    finally:
        await listener.stop_imessage_listener()
