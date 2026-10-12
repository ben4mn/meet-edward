"""``send_message`` — the only send (tools-spec §3.9; critique #2, #15).

Every guard in order: exactly one recipient, a name that resolves to one
person, never text Ellis from a turn he is not part of, the turn may reach
people at all, stand-downs, the recipient rule, the 10-minute dedupe,
files, then the transport — whose failure raises (R8: a failed send used
to read ``delivered: true``). Nothing here reaches Messages: the transport
and the lookups are patched in every test, and conftest refuses osascript.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.graph import tools as legacy
from services.toolset import messaging as M
from services.toolset import policy as P
from services.toolset.result import ToolFailure

pytestmark = pytest.mark.unit

OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
WAKE = P.TurnPolicy(source="mind", may_reach_people=True, may_push=True)
QUIET_WAKE = P.TurnPolicy(source="mind")
FRIEND_PING = P.TurnPolicy(source="ping", may_reach_people=True, reply_to=("chat777",), third_party=True)
OWNERS_PHONE = "+12015550100"


class Transport:
    def __init__(self, ok=True, error=None):
        self.sent = []
        self.ok, self.error = ok, error

    def to_chat(self, chat, text, **kw):
        self.sent.append(("chat", chat, text))
        return {"success": self.ok, "error": self.error}

    def to_handle(self, handle, text):
        self.sent.append(("handle", handle, text))
        return {"success": self.ok, "error": self.error}


@pytest.fixture
def world(monkeypatch):
    """No real Messages, Contacts or database: every lookup is scripted."""
    from services import contacts_service, imessage_service

    legacy.reset_send_history()
    t = Transport()
    monkeypatch.setattr(imessage_service, "is_available", lambda: True)
    monkeypatch.setattr(imessage_service, "send_imessage_to_chat", t.to_chat)
    monkeypatch.setattr(imessage_service, "send_imessage", t.to_handle)
    if hasattr(imessage_service, "send_to_chat"):
        monkeypatch.setattr(imessage_service, "send_to_chat", t.to_chat)
    contacts = {
        "parker": [{"name": "Parker Smith", "phones": ["+12015550111"], "emails": []}],
        "matt": [{"name": "Matt A", "phones": ["+12015550122"], "emails": []},
                 {"name": "Matt B", "phones": ["+12015550133"], "emails": []}],
        "ellis reed": [{"name": "Ellis Reed", "phones": [OWNERS_PHONE], "emails": []}],
    }
    monkeypatch.setattr(contacts_service, "lookup_contact",
                        lambda q: {"success": True, "matches": contacts.get(q.strip().lower(), [])})

    async def owner_handles():
        return [OWNERS_PHONE]

    async def nobody_waiting():
        return []

    async def no_standdown(target):
        return None

    monkeypatch.setattr(legacy, "_owner_handles", owner_handles)
    monkeypatch.setattr(legacy, "_chats_where_owner_is_waiting", nobody_waiting)
    monkeypatch.setattr(legacy, "_active_standdown_for", no_standdown)
    t.addressed = set()
    t.conversations = set()

    async def addressed(chat, hours):
        return chat in t.addressed

    async def conversation(chat):
        return chat in t.conversations

    monkeypatch.setattr(P, "addressed_recently", addressed)
    monkeypatch.setattr(P, "in_conversation", conversation)

    async def nothing(*a, **k):
        return None

    import services.conversation_service as cs
    monkeypatch.setattr(cs, "mark_user_notified", nothing)
    return t


async def send(policy, **kw):
    with P.policy_scope(policy):
        return await M.send_message_impl(**kw)


class TestOneRecipient:
    async def test_neither_or_both_is_refused(self, world):
        with pytest.raises(ToolFailure, match="exactly one"):
            await send(OWNER, message="hi")
        with pytest.raises(ToolFailure, match="exactly one"):
            await send(OWNER, message="hi", chat_id="chat1", to="Parker")
        assert world.sent == []

    async def test_a_name_resolves_to_one_handle(self, world):
        out = await send(OWNER, message="hi", to="Parker")
        assert out.startswith("Sent via iMessage to Parker Smith")
        assert world.sent[0][:2] == ("handle", "+12015550111")

    async def test_several_matches_are_listed_with_their_handles(self, world):
        with pytest.raises(ToolFailure) as e:
            await send(OWNER, message="hi", to="Matt")
        assert "Matt A (+12015550122)" in str(e.value) and "Matt B (+12015550133)" in str(e.value)
        assert world.sent == []

    async def test_no_match_is_refused(self, world):
        with pytest.raises(ToolFailure, match="no contact matches"):
            await send(OWNER, message="hi", to="Zelda")

    async def test_a_handle_needs_no_lookup(self, world):
        await send(OWNER, message="hi", to="+1 201 555 0144")
        assert world.sent[0][1] == "+1 201 555 0144"

    async def test_a_reply_goes_into_the_chat(self, world):
        out = await send(OWNER, message="on it", chat_id="chat123")
        assert out.startswith("Sent via iMessage to chat123")
        kind, chat, text = world.sent[0]
        assert (kind, chat) == ("chat", "chat123")
        assert text.startswith("on it")  # signed by ensure_message_signature

    async def test_the_message_is_signed(self, world):
        from utils.message_signature import ensure_message_signature

        await send(OWNER, message="on it", chat_id="chat123")
        assert world.sent[0][2] == ensure_message_signature("on it")


class TestNeverTextOwner:
    @pytest.mark.parametrize("policy", [WAKE, P.TurnPolicy(source="pulse", may_reach_people=True),
                                        FRIEND_PING, P.TurnPolicy(source="worker", may_reach_people=True),
                                        P.TurnPolicy(source="scheduled_event", may_reach_people=True)])
    async def test_a_turn_owner_is_not_part_of_cannot_text_him(self, world, policy):
        for kw in ({"to": "Ellis"}, {"to": "Ellis Reed"}, {"to": OWNERS_PHONE}, {"chat_id": OWNERS_PHONE}):
            with pytest.raises(ToolFailure, match="tell_owner"):
                await send(policy, message="hi", **kw)
        assert world.sent == []

    async def test_owner_asking_may_text_himself(self, world):
        await send(OWNER, message="the address", to="Ellis Reed")
        assert world.sent

    async def test_a_reply_into_owners_own_ping_goes_through(self, world):
        """Ellis's self-chat ping answered by a wake: replying there is
        answering him where he asked (pings-spec §4.2 #10)."""
        policy = P.TurnPolicy(source="mind", may_reach_people=True, reply_to=(OWNERS_PHONE,))
        await send(policy, message="done", chat_id=OWNERS_PHONE)
        assert world.sent

    async def test_a_waiting_self_chat_ping_lets_the_reply_through(self, world, monkeypatch):
        async def waiting():
            return [OWNERS_PHONE]

        monkeypatch.setattr(legacy, "_chats_where_owner_is_waiting", waiting)
        await send(WAKE, message="done", chat_id=OWNERS_PHONE)
        assert world.sent

    async def test_someone_else_called_owner_by_full_name(self, world):
        """'Ellis' alone is Ellis; a different Ellis by number is someone else."""
        world.addressed.add("+12015550999")
        await send(WAKE, message="hi", to="+12015550999")
        assert world.sent


class TestTheTurnMayReachPeople:
    async def test_a_quiet_wake_cannot_message_anyone(self, world):
        world.addressed.add("chat123")
        with pytest.raises(ToolFailure, match="can't message people"):
            await send(QUIET_WAKE, message="hi", chat_id="chat123")
        assert world.sent == []

    async def test_the_fail_closed_default(self, world):
        """No policy set and no turn open: nothing goes out."""
        with pytest.raises(ToolFailure, match="can't message people"):
            await M.send_message_impl(message="hi", chat_id="chat123")


class TestTheRecipientRule:
    async def test_owner_asked(self, world):
        await send(OWNER, message="hi", to="Parker")
        assert world.sent

    async def test_the_chat_this_turn_was_asked_in(self, world):
        await send(FRIEND_PING, message="here you go", chat_id="chat777")
        assert world.sent

    async def test_a_chat_that_addressed_him(self, world):
        world.addressed.add("chat5")
        await send(WAKE, message="hi", chat_id="chat5")
        assert world.sent

    async def test_a_live_conversation(self, world):
        world.conversations.add("chat6")
        await send(WAKE, message="hi", chat_id="chat6")
        assert world.sent

    async def test_anyone_else_is_owners_call(self, world):
        with pytest.raises(ToolFailure, match="Ellis's call"):
            await send(WAKE, message="hi", to="Parker")
        with pytest.raises(ToolFailure, match="Ellis's call"):
            await send(FRIEND_PING, message="hi", chat_id="chat-somewhere-else")
        assert world.sent == []


class TestStandDowns:
    async def test_a_stand_down_binds_autonomous_turns(self, world, monkeypatch):
        world.addressed.add("chat9")

        class Row:
            requested_by = "Rowan"
            requested_at = datetime.now(timezone.utc)

        async def standdown(target):
            return Row()

        monkeypatch.setattr(legacy, "_active_standdown_for", standdown)
        with pytest.raises(ToolFailure, match="stand-down"):
            await send(WAKE, message="hi", chat_id="chat9")
        assert world.sent == []
        await send(OWNER, message="hi", chat_id="chat9")  # Ellis asking lifts it
        assert world.sent


class TestTheHubsVoice:
    """The household hub's panel is someone in Ellis's home, not necessarily
    Ellis (``hub_voice``, 2026-10-11): the app's live voice is Ellis and may
    message anyone; the hub's goes through every rail an autonomous turn
    does. Both use the default policy the voice socket's turns get."""

    async def test_an_unaddressed_recipient_is_refused_at_the_hub(self, world):
        with pytest.raises(ToolFailure, match="Ellis's call"):
            await send(P.default_policy("hub_voice"), message="hi", to="Parker")
        with pytest.raises(ToolFailure, match="Ellis's call"):
            await send(P.default_policy("hub_voice"), message="hi", chat_id="chat-somewhere")
        assert world.sent == []

    async def test_the_same_send_is_allowed_from_owners_own_voice(self, world):
        await send(P.default_policy("voice"), message="hi", to="Parker")
        assert world.sent and world.sent[0][1] == "+12015550111"

    async def test_a_chat_that_addressed_him_may_be_answered_from_the_hub(self, world):
        world.addressed.add("chat5")
        await send(P.default_policy("hub_voice"), message="hi", chat_id="chat5")
        assert world.sent

    async def test_the_hub_cannot_text_owner(self, world):
        with pytest.raises(ToolFailure, match="tell_owner"):
            await send(P.default_policy("hub_voice"), message="hi", to="Ellis Reed")
        assert world.sent == []

    async def test_a_stand_down_binds_the_hub(self, world, monkeypatch):
        world.addressed.add("chat9")

        class Row:
            requested_by = "Rowan"
            requested_at = datetime.now(timezone.utc)

        async def standdown(target):
            return Row()

        monkeypatch.setattr(legacy, "_active_standdown_for", standdown)
        with pytest.raises(ToolFailure, match="stand-down"):
            await send(P.default_policy("hub_voice"), message="hi", chat_id="chat9")
        assert world.sent == []


class TestDedupeAndTransport:
    async def test_the_same_text_twice_is_sent_once(self, world, monkeypatch):
        from services import turn_ledger

        rec = turn_ledger.begin_turn(conversation_id="c", source="chat", model="m", provider="p", message="x")
        try:
            await send(OWNER, message="hello", chat_id="chat1")
            out = await send(OWNER, message="hello", chat_id="chat1")
        finally:
            turn_ledger.end_turn(rec, "completed")
        assert out.startswith("Already sent to")
        assert len(world.sent) == 1

    async def test_a_failed_transport_raises(self, world):
        world.ok, world.error = False, "Can't get chat"
        with pytest.raises(ToolFailure, match="Can't get chat"):
            await send(OWNER, message="hi", chat_id="chat1")

    async def test_success_reads_delivered(self, world):
        out = await send(OWNER, message="hi", chat_id="chat1")
        assert legacy.looks_delivered(out)

    async def test_messages_off(self, world, monkeypatch):
        from services import imessage_service

        monkeypatch.setattr(imessage_service, "is_available", lambda: False)
        with pytest.raises(ToolFailure, match="iMessage"):
            await send(OWNER, message="hi", chat_id="chat1")

    async def test_a_dry_run_records_and_sends_nothing(self, world):
        policy = P.TurnPolicy(source="mind", may_reach_people=True, reply_to=("chat1",), dry_run_outward=True)
        out = await send(policy, message="hi", chat_id="chat1")
        assert out.startswith("[dry run]")
        assert world.sent == []


class TestFiles:
    async def test_autonomous_turns_attach_only_what_edward_made(self, world, tmp_path, monkeypatch):
        home = tmp_path / "edward-home"
        home.mkdir()
        mine = home / "chart.png"
        mine.write_bytes(b"\x89PNG....")
        owners = tmp_path / "Documents" / "taxes.pdf"
        owners.parent.mkdir()
        owners.write_bytes(b"%PDF-1.4")
        monkeypatch.setenv("EDWARD_HOME", str(home))
        sent_files = []

        def send_attachment(target, path):
            sent_files.append((target, path))
            return {"success": True}

        from services import imessage_service

        monkeypatch.setattr(imessage_service, "send_attachment", send_attachment, raising=False)
        world.addressed.add("chat1")
        with pytest.raises(ToolFailure, match="edward-home"):
            await send(WAKE, message="here", chat_id="chat1", files=[str(owners)])
        assert world.sent == [] and sent_files == []
        await send(WAKE, message="here", chat_id="chat1", files=[str(mine)])
        assert sent_files == [("chat1", str(mine.resolve()))]
        await send(OWNER, message="here", chat_id="chat1", files=[str(owners)])
        assert sent_files[-1] == ("chat1", str(owners.resolve()))

    async def test_a_text_file_carrying_a_secret_is_refused(self, world, tmp_path, monkeypatch):
        home = tmp_path / "edward-home"
        home.mkdir()
        leaky = home / "notes.txt"
        leaky.write_text("token: ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
        monkeypatch.setenv("EDWARD_HOME", str(home))
        from services import imessage_service

        monkeypatch.setattr(imessage_service, "send_attachment", lambda t, p: {"success": True}, raising=False)
        with pytest.raises(ToolFailure, match="secret"):
            await send(OWNER, message="here", chat_id="chat1", files=[str(leaky)])
        assert world.sent == []

    async def test_no_spelling_of_a_credential_is_attached(self, world, tmp_path, monkeypatch):
        """Review 2026-10-12: the files rail is the computer's read rail,
        compared by identity — a case variant of the session key (macOS
        volumes fold case) is the session key, even in the owner's turn."""
        import os

        repo = tmp_path / "edward"
        (repo / "backend").mkdir(parents=True)
        key = repo / "backend" / ".jwt_secret"
        key.write_text("k" * 64)
        monkeypatch.setenv("EDWARD_LIVE_REPO", str(repo))
        monkeypatch.setenv("EDWARD_HOME", str(tmp_path / "edward-home"))
        (tmp_path / "edward-home").mkdir()
        sent_files = []
        from services import imessage_service

        monkeypatch.setattr(imessage_service, "send_attachment",
                            lambda t, p: sent_files.append(p) or {"success": True}, raising=False)
        spellings = [str(key)]
        try:
            if os.pathconf(str(tmp_path), 11) == 0:  # a case-insensitive volume
                spellings += [str(key).upper(), str(repo) + "/Backend/.JWT_SECRET"]
        except (OSError, ValueError):
            pass
        for spelling in spellings:
            with pytest.raises(ToolFailure, match="isn't yours to read"):
                await send(OWNER, message="here", chat_id="chat1", files=[spelling])
        assert sent_files == [] and world.sent == []

    async def test_attachments_not_wired_yet(self, world, tmp_path, monkeypatch):
        from services import imessage_service

        monkeypatch.delattr(imessage_service, "send_attachment", raising=False)
        f = tmp_path / "x.png"
        f.write_bytes(b"x")
        with pytest.raises(ToolFailure, match="isn't wired"):
            await send(OWNER, message="here", chat_id="chat1", files=[str(f)])
        assert world.sent == []


class TestRuleCOnTheDatabase:
    """Rule (c) narrowed (critique #15): Edward's latest send answered by
    someone other than Ellis within 2 h; Ellis's own line does not count."""

    def test_pure(self):
        t0 = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)
        assert P.conversation_is_live([t0], [t0 + timedelta(minutes=30)])
        assert not P.conversation_is_live([t0], [t0 + timedelta(hours=3)])
        assert not P.conversation_is_live([t0], [t0 - timedelta(minutes=5)])
        assert not P.conversation_is_live([], [t0])

    async def test_on_the_tables(self, clean_db):
        from services.database import HeartbeatEventModel, ImessageOutgoingSendModel, async_session

        now = datetime.now(timezone.utc)
        sent = now - timedelta(hours=5)
        async with async_session() as s:
            s.add(ImessageOutgoingSendModel(chat_identifier="chat42", text_hash="h", sent_at=sent))
            s.add(ImessageOutgoingSendModel(chat_identifier="chat43", text_hash="h", sent_at=sent))
            # A friend answered in chat42 within the hour; only Ellis wrote in chat43.
            s.add(HeartbeatEventModel(source="imessage", event_type="message_received", sender="+12015550111",
                                      chat_identifier="chat42", is_from_user=False,
                                      created_at=(sent + timedelta(minutes=40)).replace(tzinfo=None)))
            s.add(HeartbeatEventModel(source="imessage", event_type="message_sent", sender="me",
                                      chat_identifier="chat43", is_from_user=True,
                                      created_at=(sent + timedelta(minutes=10)).replace(tzinfo=None)))
            await s.commit()
        assert await P.in_conversation("chat42") is True
        assert await P.in_conversation("iMessage;+;chat42") is True
        assert await P.in_conversation("chat43") is False
        assert await P.in_conversation("chat44") is False
