"""``messages(recent | thread | participants)`` read the way Edward uses them
(2026-10-11, the integrations audit's contacts track).

- ``recent`` printed a ``[label]`` header whenever the chat changed in time
  order, so a busy hour repeated chats and never said how to reply: now one
  block per chat, newest chat first, its lines in order, and every header
  carries ``chat_id=<chat_identifier>`` (what ``send_message(chat_id=)`` and
  ``thread`` take).
- ``recent(contact="Rowan")`` matched the literal text against chat ids and
  handles, so a name found nothing: a name now resolves through Ellis's
  contacts to her phones and emails (her 1:1, her Apple-ID chat, her lines in
  groups); a group's name still matches; a handle passes straight through.
- ``thread`` and ``participants`` queried ``chat_identifier = ?`` exactly, so
  the guid spellings Edward is shown (``any;+;chat…``, ``iMessage;-;+1…``)
  found nothing: the id is made canonical first, through chat.db, else by
  stripping the service prefix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.heartbeat import listener_imessage as listener
from tests.fixtures.fake_chatdb import FakeChatDB

ROWAN = "+15555550171"
ROWAN_ID = "rowan.hale@example.com"
ROSS = "+12015550101"
GROUP = "chat100000000000000001"
NAMES = {ROWAN: "Rowan Hale", ROWAN_ID.lower(): "Rowan Hale", ROSS: "Ross Dunn"}


def _ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


@pytest.fixture
def chats(tmp_path, monkeypatch):
    db = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    db.add_chat(ROWAN)
    db.add_chat(ROWAN_ID)
    db.add_chat(GROUP, display_name="Team Weekend", is_group=True, participants=[ROWAN, ROSS, ROWAN_ID])
    db.add_chat(ROSS)
    db.add_message(ROSS, "ross early", sender=ROSS, date=_ago(5))
    db.add_message(ROWAN, "rowan first", sender=ROWAN, date=_ago(3))
    db.add_message(GROUP, "ross in the group", sender=ROSS, date=_ago(2.5))
    db.add_message(GROUP, "rowan in the group", sender=ROWAN, date=_ago(2))
    db.add_message(ROWAN_ID, "from her apple id", sender=ROWAN_ID, date=_ago(1.5))
    db.add_message(GROUP, "me in the group", is_from_me=True, date=_ago(1))
    db.add_message(ROWAN, "rowan latest", sender=ROWAN, date=_ago(0.5))

    from services import contacts_service

    monkeypatch.setattr(listener, "_contact_name_cache", {})

    def lookup_handle(handle):
        name = NAMES.get(str(handle).lower()) or NAMES.get(str(handle))
        return {"success": True, "matches": [{"name": name, "phones": [], "emails": []}] if name else []}

    monkeypatch.setattr(contacts_service, "lookup_handle", lookup_handle)
    return db


def _lookup_contact(monkeypatch, people):
    from services import contacts_service

    asked: list = []

    def lookup_contact(query, **_):
        asked.append(query)
        return {"success": True, "matches": people}

    monkeypatch.setattr(contacts_service, "lookup_contact", lookup_contact)
    return asked


async def _recent(**args) -> str:
    from services.graph.tools import get_recent_messages

    return await get_recent_messages.ainvoke(args)


class TestRecent:
    async def test_one_block_per_chat_newest_first_with_its_chat_id(self, chats):
        out = await _recent()
        headers = [line for line in out.splitlines() if line.startswith("[")]
        assert headers == [
            f"[Rowan Hale] chat_id={ROWAN}",
            f"[Team Weekend] chat_id={GROUP}",
            f"[Rowan Hale] chat_id={ROWAN_ID}",
            f"[Ross Dunn] chat_id={ROSS}",
        ], out
        rowan_block = out.split(f"chat_id={ROWAN}\n", 1)[1].split("\n\n", 1)[0]
        assert rowan_block.index("rowan first") < rowan_block.index("rowan latest")
        group_block = out.split(f"chat_id={GROUP}\n", 1)[1].split("\n\n", 1)[0]
        assert [w in group_block for w in ("ross in the group", "rowan in the group", "me in the group")] == [True] * 3
        assert group_block.index("ross in the group") < group_block.index("me in the group")

    async def test_a_name_reads_her_chats_and_her_lines_in_groups(self, chats, monkeypatch):
        asked = _lookup_contact(monkeypatch, [{"name": "Rowan Hale", "phones": ["(555) 555-0171"],
                                              "emails": ["rowan.hale@example.com"]}])
        out = await _recent(contact="Rowan")
        assert asked == ["Rowan"]
        for line in ("rowan first", "rowan latest", "from her apple id", "rowan in the group"):
            assert line in out, out
        for line in ("ross early", "ross in the group", "me in the group"):
            assert line not in out, out

    async def test_a_group_name_still_matches(self, chats, monkeypatch):
        _lookup_contact(monkeypatch, [])
        out = await _recent(contact="Weekend")
        assert "ross in the group" in out and "me in the group" in out
        assert "rowan latest" not in out

    async def test_a_handle_passes_straight_through(self, chats, monkeypatch):
        from services import contacts_service

        def never(_q, **_):
            raise AssertionError("a handle is not looked up by name")

        monkeypatch.setattr(contacts_service, "lookup_contact", never)
        out = await _recent(contact="(555) 555-0171")
        assert "rowan first" in out and "rowan in the group" in out
        assert "ross early" not in out
        out = await _recent(contact="rowan.hale@example.com")
        assert "from her apple id" in out and "rowan first" not in out

    async def test_an_unknown_name_says_nothing_matched(self, chats, monkeypatch):
        _lookup_contact(monkeypatch, [])
        out = await _recent(contact="Nobody Atall")
        assert out.startswith("No messages found") and "Nobody Atall" in out


class TestNoAddressBook:
    """With no readable AddressBook store (Full Disk Access lost after a
    python@3.12 upgrade), ``recent(contact=<name>)`` used the AppleScript
    fallback — ``open -ga Contacts`` and an osascript of up to 30 s inside
    the 60 s ``messages`` deadline. It now uses the loose match alone."""

    async def test_a_name_never_wakes_contacts_app(self, chats, monkeypatch):
        from services import contacts_service

        calls: list = []

        def refuse(args, *a, **k):
            calls.append(args)
            raise AssertionError(f"no subprocess for recent: {args}")

        monkeypatch.setattr(contacts_service.subprocess, "run", refuse)
        monkeypatch.setattr(contacts_service.subprocess, "Popen", refuse)
        out = await _recent(contact="Weekend")
        assert "ross in the group" in out
        out = await _recent(contact="Rowan")
        assert out.startswith("No messages found") or "chat_id=" in out
        assert calls == []


async def test_who_shows_a_nickname_and_an_organisation(monkeypatch):
    from services import contacts_service
    from services.toolset import messaging as M

    monkeypatch.setattr(contacts_service, "lookup_contact", lambda q, **k: {"success": True, "matches": [
        {"name": "Tom Rivera", "phones": ["+12015550101"], "emails": [], "nickname": "TJ",
         "organization": "Acme Plumbing"}]})
    out = await M.messages_impl("who", query="tj")
    assert "Tom Rivera" in out and "Nickname: TJ" in out and "Organization: Acme Plumbing" in out, out
    monkeypatch.setattr(contacts_service, "lookup_phone", lambda q, **k: {"success": True, "matches": [
        {"name": "Tom Rivera", "phones": ["+12015550101"], "emails": [], "organization": "Acme Plumbing"}]})
    out = await M.messages_impl("who", query="+12015550101")
    assert "Organization: Acme Plumbing" in out, out


class TestThreadAndParticipantsSpellings:
    SPELLINGS = (GROUP, f"any;+;{GROUP}", f"iMessage;+;{GROUP}")

    @pytest.mark.parametrize("given", SPELLINGS)
    async def test_thread_accepts_every_spelling(self, chats, given):
        from services.graph.tools import read_imessage_thread

        out = await read_imessage_thread.ainvoke({"chat_identifier": given, "limit": 10})
        assert "rowan in the group" in out and "ross in the group" in out, out

    @pytest.mark.parametrize("given", (ROWAN, f"iMessage;-;{ROWAN}", f"SMS;-;{ROWAN}", f"any;-;{ROWAN}"))
    async def test_thread_accepts_a_one_to_one_spelling(self, chats, given):
        from services.graph.tools import read_imessage_thread

        out = await read_imessage_thread.ainvoke({"chat_identifier": given, "limit": 10})
        assert "rowan latest" in out, out

    @pytest.mark.parametrize("given", SPELLINGS)
    async def test_participants_accept_every_spelling_and_name_an_apple_id(self, chats, given):
        from services.graph.tools import list_chat_participants

        out = await list_chat_participants.ainvoke({"chat_identifier": given})
        assert "(3 total)" in out, out
        assert f"Rowan Hale ({ROWAN})" in out
        assert f"Rowan Hale ({ROWAN_ID})" in out
        assert f"Ross Dunn ({ROSS})" in out

    async def test_without_chat_db_knowing_it_the_prefix_is_stripped(self, chats, monkeypatch):
        from services import imessage_service
        from services.graph.tools import list_chat_participants, read_imessage_thread

        monkeypatch.setattr(imessage_service, "chat_info", lambda _given: None)
        out = await read_imessage_thread.ainvoke({"chat_identifier": f"any;+;{GROUP}", "limit": 10})
        assert "rowan in the group" in out, out
        out = await list_chat_participants.ainvoke({"chat_identifier": f"iMessage;+;{GROUP}"})
        assert "(3 total)" in out, out
