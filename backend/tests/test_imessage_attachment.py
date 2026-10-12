"""Sending a file over iMessage (2026-10-11, the integrations audit's
contacts track): ``imessage_service.send_attachment``.

``send_message(files=[...])`` already looped over
``imessage_service.send_attachment``, which did not exist, so every file
answered "attaching files isn't wired yet". Now:

- the file is **copied** (never moved) to a staging folder Messages can read
  (``~/Library/Messages/Attachments/Edward/<uuid>/<name>``;
  ``EDWARD_IMESSAGE_OUTBOX`` overrides it), and staged folders older than
  24 h are pruned;
- the AppleScript is ``send POSIX file "<staged>"`` to the chat (or the
  participant), routed exactly like a text (``applescript_targets``), one
  minute at most, never a second target after a timeout;
- **the read-back decides**: success only when a from-me message after the
  send, in that chat, carries an attachment with that file name and no
  error. osascript saying ok is not enough.

osascript is stubbed in every test; chat.db is a fake.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from services import imessage_sent, imessage_service as im
from tests.fixtures.fake_chatdb import FakeChatDB

pytestmark = pytest.mark.unit

GROUP = "chat100000000000000001"
FRIEND = "+12015550111"
APPLE_ID = "rowan.hale@example.com"


class Proc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, "", stderr


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
    db.add_chat(GROUP, display_name="Team", is_group=True, participants=[FRIEND])
    db.add_chat(FRIEND)
    db.add_message(GROUP, "earlier", sender=FRIEND)
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("EDWARD_IMESSAGE_OUTBOX", str(outbox))
    monkeypatch.setattr(im, "is_available", lambda: True)
    monkeypatch.setattr(im, "ATTACHMENT_READBACK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(im, "READBACK_INTERVAL_S", 0.01)
    recorded: list = []
    monkeypatch.setattr(im, "_record_outgoing_send", lambda chat, text, **f: recorded.append((chat, text, f)))
    imessage_sent.reset_for_tests()
    original = tmp_path / "home" / "chart.png"
    original.parent.mkdir()
    original.write_bytes(b"\x89PNG chart")
    scripts: list = []

    def osascript(lands: bool = True, returncode: int = 0, stderr: str = "", chat: str = GROUP):
        def run(script, timeout=30):
            scripts.append((script, timeout))
            if lands:
                rowid = db.add_message(chat, "￼", is_from_me=True)
                staged = script.split('POSIX file "', 1)[1].split('"', 1)[0]
                db.add_attachment(rowid, filename=staged, mime_type="image/png", is_outgoing=True)
            return Proc(returncode, stderr)

        monkeypatch.setattr(im, "_run_osascript", run)

    return {"db": db, "outbox": outbox, "original": original, "scripts": scripts, "osascript": osascript,
            "recorded": recorded}


class TestStaging:
    def test_a_copy_is_staged_and_the_original_stays(self, world):
        world["osascript"]()
        res = im.send_attachment(GROUP, str(world["original"]))
        staged = Path(res["staged"])
        assert staged.name == "chart.png" and staged.parent.parent == world["outbox"]
        assert staged.read_bytes() == b"\x89PNG chart"
        assert world["original"].exists(), "the original is never moved"

    def test_staged_folders_older_than_a_day_are_pruned(self, world):
        old = world["outbox"] / ("a" * 32)
        old.mkdir(parents=True)
        (old / "x.png").write_bytes(b"x")
        then = time.time() - 25 * 3600
        os.utime(old, (then, then))
        keep = world["outbox"] / "not-ours"
        keep.mkdir()
        os.utime(keep, (then, then))
        world["osascript"]()
        im.send_attachment(GROUP, str(world["original"]))
        assert not old.exists()
        assert keep.exists(), "only folders the outbox made are pruned"

    def test_a_staged_folder_a_message_still_points_at_is_kept(self, world):
        """If Messages keeps the staged path as the attachment's file (not
        verified on this macOS), deleting it would break that message in
        Ellis's history and ``look("attachment:<rowid>")``: a folder any
        chat.db attachment row names is kept, however old."""
        db, outbox = world["db"], world["outbox"]
        then = time.time() - 25 * 3600
        kept, gone = outbox / ("b" * 32), outbox / ("c" * 32)
        for folder in (kept, gone):
            folder.mkdir(parents=True)
            (folder / "old.png").write_bytes(b"x")
            os.utime(folder, (then, then))
        rowid = db.add_message(GROUP, "￼", is_from_me=True)
        db.add_attachment(rowid, filename=f"~/Library/Messages/Attachments/{outbox.name}/{'b' * 32}/old.png")
        world["osascript"]()
        im.send_attachment(GROUP, str(world["original"]))
        assert kept.exists(), "a message still points at it"
        assert not gone.exists()

    def test_without_chat_db_nothing_is_pruned(self, world, monkeypatch):
        old = world["outbox"] / ("d" * 32)
        old.mkdir(parents=True)
        then = time.time() - 25 * 3600
        os.utime(old, (then, then))
        monkeypatch.setattr(im, "_connect_chat_db", lambda: None)
        im._prune_outbox(world["outbox"])
        assert old.exists(), "chat.db can't say which folders are in use: keep them all"

    def test_a_missing_file_is_an_honest_failure(self, world):
        world["osascript"]()
        res = im.send_attachment(GROUP, str(world["original"].with_name("nope.png")))
        assert res["success"] is False and "no such file" in res["error"]
        assert world["scripts"] == []


class TestTheSend:
    def test_the_script_sends_the_staged_posix_file_to_the_group(self, world):
        world["osascript"]()
        res = im.send_attachment(GROUP, str(world["original"]))
        script, timeout = world["scripts"][0]
        assert f'POSIX file "{res["staged"]}"' in script
        assert f'chat id "any;+;{GROUP}"' in script
        assert timeout == 60

    def test_the_row_read_back_is_success(self, world):
        world["osascript"]()
        res = im.send_attachment(GROUP, str(world["original"]))
        assert res["success"] is True and res["confirmed"] is True
        assert res["route"] == "chat_id" and res["chat_identifier"] == GROUP
        assert imessage_sent.is_edward_rowid(res["message_rowid"])
        [(chat, text, fields)] = world["recorded"]
        assert chat == GROUP and text == "[attachment] chart.png"
        assert fields["message_rowid"] == res["message_rowid"]

    def test_osascript_ok_without_a_row_is_a_failure(self, world):
        world["osascript"](lands=False)
        res = im.send_attachment(GROUP, str(world["original"]))
        assert res["success"] is False and res["confirmed"] is False
        assert res["error"] == "Messages took the file but no message appeared in the chat"
        assert world["recorded"] == []

    def test_no_row_after_osascript_ok_arms_the_self_chat_breaker(self, world):
        """The file may still land after the read-back gives up: like an
        unconfirmed text, the chat is marked unconfirmed, so a late row in
        Ellis's self-chat is not read as Ellis writing (a new ping)."""
        world["osascript"](lands=False)
        res = im.send_attachment(FRIEND, str(world["original"]))
        assert res["success"] is False
        assert imessage_sent.unconfirmed_recently(FRIEND)
        assert imessage_sent.last_send(FRIEND) is not None

    def test_no_row_after_a_timeout_arms_the_breaker_too(self, world, monkeypatch):
        def run(script, timeout=30):
            raise subprocess.TimeoutExpired("osascript", timeout)

        monkeypatch.setattr(im, "_run_osascript", run)
        assert im.send_attachment(FRIEND, str(world["original"]))["success"] is False
        assert imessage_sent.unconfirmed_recently(FRIEND)

    def test_an_osascript_failure_sent_nothing_and_arms_nothing(self, world):
        world["osascript"](lands=False, returncode=1, stderr="execution error: Messages got an error (-1700)")
        res = im.send_attachment(FRIEND, str(world["original"]))
        assert res["success"] is False and "-1700" in res["error"]
        assert not imessage_sent.unconfirmed_recently(FRIEND)
        assert imessage_sent.last_send(FRIEND) is None

    def test_a_row_with_another_file_name_is_not_this_send(self, world, monkeypatch):
        def run(script, timeout=30):
            rowid = world["db"].add_message(GROUP, "￼", is_from_me=True)
            world["db"].add_attachment(rowid, filename="/tmp/other.png", is_outgoing=True)
            return Proc()

        monkeypatch.setattr(im, "_run_osascript", run)
        assert im.send_attachment(GROUP, str(world["original"]))["success"] is False

    def test_cannot_get_chat_moves_to_the_next_target(self, world, monkeypatch):
        seen: list = []

        def run(script, timeout=30):
            seen.append(script)
            if len(seen) == 1:
                return Proc(1, "execution error: Can't get chat id \"any;+;x\". (-1728)")
            rowid = world["db"].add_message(GROUP, "￼", is_from_me=True)
            staged = script.split('POSIX file "', 1)[1].split('"', 1)[0]
            world["db"].add_attachment(rowid, filename=staged, is_outgoing=True)
            return Proc()

        monkeypatch.setattr(im, "_run_osascript", run)
        res = im.send_attachment(GROUP, str(world["original"]))
        assert res["success"] is True and len(seen) == 2

    def test_a_timeout_never_tries_another_target(self, world, monkeypatch):
        seen: list = []

        def run(script, timeout=30):
            seen.append(script)
            raise subprocess.TimeoutExpired("osascript", timeout)

        monkeypatch.setattr(im, "_run_osascript", run)
        res = im.send_attachment(GROUP, str(world["original"]))
        assert res["success"] is False and len(seen) == 1
        assert "timed out" in res["error"]

    def test_a_one_to_one_goes_to_the_participant(self, world):
        world["osascript"](chat=FRIEND)
        res = im.send_attachment(FRIEND, str(world["original"]))
        assert res["success"] is True
        assert f'participant "{FRIEND}"' in world["scripts"][0][0]


class TestTheChatAsChatDbSpellsIt:
    """The read-back looks for the chat as chat.db spells it. A contact's
    number comes as the address book stores it ("(201) 555-0111") and an
    Apple ID in its own capitals, while chat.db holds "+12015550111" and
    the address in lower case: the file went out and was reported failed,
    inviting a second send from Ellis's number (review, 2026-10-11)."""

    @pytest.mark.parametrize("spelling", ["(201) 555-0111", "+1 (201) 555-0111", "201.555.0111"])
    def test_a_formatted_number_reads_back_from_its_chat(self, world, spelling):
        world["osascript"](chat=FRIEND)
        res = im.send_attachment(spelling, str(world["original"]))
        assert res["success"] is True, res
        assert imessage_sent.is_edward_rowid(res["message_rowid"])

    def test_an_apple_id_in_other_capitals_reads_back(self, world):
        world["db"].add_chat(APPLE_ID.lower())
        world["osascript"](chat=APPLE_ID.lower())
        res = im.send_attachment(APPLE_ID, str(world["original"]))
        assert res["success"] is True, res

    def test_a_number_never_reads_back_from_a_group_ending_in_its_digits(self, world):
        """A group's id ends in 5555550199; a 1:1 to (555) 555-0199 is not it."""
        trap = "chat1005555550199"
        world["db"].add_chat(trap, display_name="Trap", is_group=True, participants=[FRIEND])
        world["osascript"](chat=trap)
        res = im.send_attachment("(555) 555-0199", str(world["original"]))
        assert res["success"] is False

    def test_the_row_the_listener_claimed_is_the_send(self, world, monkeypatch):
        """The listener may reach the file's row first and claim it against
        the in-flight send (it keys on any spelling of the chat): the
        read-back takes that row, as the text read-back does."""
        def run(script, timeout=30):
            rowid = world["db"].add_message(FRIEND, "￼", is_from_me=True)
            staged = script.split('POSIX file "', 1)[1].split('"', 1)[0]
            world["db"].add_attachment(rowid, filename=staged, is_outgoing=True)
            assert imessage_sent.claim_inflight(FRIEND, im.OBJECT_REPLACEMENT_CHAR, rowid)
            return Proc()

        monkeypatch.setattr(im, "_run_osascript", run)
        monkeypatch.setattr(im, "_find_attachment_row", lambda *a, **k: None)
        res = im.send_attachment(FRIEND, str(world["original"]))
        assert res["success"] is True and res["message_rowid"] is not None

    def test_a_claimed_row_with_another_file_is_not_this_send(self, world, monkeypatch):
        """Ellis's own photo, claimed in flight, is not the file Edward sent."""
        def run(script, timeout=30):
            rowid = world["db"].add_message(FRIEND, "￼", is_from_me=True)
            world["db"].add_attachment(rowid, filename="/tmp/IMG_1.heic", is_outgoing=True)
            imessage_sent.claim_inflight(FRIEND, im.OBJECT_REPLACEMENT_CHAR, rowid)
            return Proc()

        monkeypatch.setattr(im, "_run_osascript", run)
        assert im.send_attachment(FRIEND, str(world["original"]))["success"] is False


@pytest.fixture
def guards(monkeypatch):
    from services.graph import tools as legacy
    from services.toolset import policy as P

    legacy.reset_send_history()

    async def none(*a, **k):
        return None

    async def owner_handles():
        return ["+12015550100"]

    async def nobody():
        return []

    monkeypatch.setattr(legacy, "_owner_handles", owner_handles)
    monkeypatch.setattr(legacy, "_chats_where_owner_is_waiting", nobody)
    monkeypatch.setattr(legacy, "_active_standdown_for", none)
    monkeypatch.setattr(P, "addressed_recently", lambda chat, hours: none())
    monkeypatch.setattr(P, "in_conversation", lambda chat: none())
    import services.conversation_service as cs

    monkeypatch.setattr(cs, "mark_user_notified", none)
    monkeypatch.setattr(im, "send_to_chat", lambda chat, text, **k: {"success": True})


class TestThroughSendMessage:
    """``send_message(files=[...])`` end to end with the real
    ``send_attachment`` and a fake transport underneath."""

    async def test_owners_turn_sends_the_file(self, world, guards):
        from services.toolset import messaging as M, policy as P

        world["osascript"]()
        owner = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
        with P.policy_scope(owner):
            out = await M.send_message_impl(message="the chart", chat_id=GROUP, files=[str(world["original"])])
        assert out.startswith("Sent via iMessage") and "with 1 file" in out
        assert any('POSIX file "' in s for s, _ in world["scripts"])

    @pytest.mark.parametrize("handle, chat", [("(201) 555-0111", FRIEND), (APPLE_ID, APPLE_ID.lower())])
    async def test_to_a_name_sends_the_file_to_the_number_the_address_book_holds(self, world, guards, monkeypatch,
                                                                                handle, chat):
        from services import contacts_service
        from services.toolset import messaging as M, policy as P

        if chat not in world["db"]._chats:
            world["db"].add_chat(chat)
        kind = "emails" if "@" in handle else "phones"
        monkeypatch.setattr(contacts_service, "lookup_contact", lambda q, **k: {
            "success": True, "matches": [{"name": "Rowan Hale", "phones": [], "emails": [], kind: [handle]}]})
        world["osascript"](chat=chat)
        owner = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
        with P.policy_scope(owner):
            out = await M.send_message_impl(message="the chart", to="Rowan", files=[str(world["original"])])
        assert out.startswith("Sent via iMessage to Rowan Hale") and "with 1 file" in out, out

    async def test_an_unconfirmed_file_is_a_failure_after_the_text(self, world, guards):
        from services.toolset import messaging as M, policy as P
        from services.toolset.result import ToolFailure

        world["osascript"](lands=False)
        owner = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
        with P.policy_scope(owner), pytest.raises(ToolFailure) as e:
            await M.send_message_impl(message="the chart", chat_id=GROUP, files=[str(world["original"])])
        assert "the text went out" in str(e.value) and "no message appeared" in str(e.value)

    async def test_an_autonomous_turn_attaches_only_from_edward_home(self, world, guards, monkeypatch, tmp_path):
        from services.toolset import messaging as M, policy as P
        from services.toolset.result import ToolFailure

        home = tmp_path / "edward-home"
        home.mkdir()
        mine = home / "plot.png"
        mine.write_bytes(b"\x89PNG plot")
        monkeypatch.setenv("EDWARD_HOME", str(home))
        world["osascript"]()
        wake = P.TurnPolicy(source="mind", may_reach_people=True, reply_to=(GROUP,))
        with P.policy_scope(wake), pytest.raises(ToolFailure, match="edward-home"):
            await M.send_message_impl(message="look", chat_id=GROUP, files=[str(world["original"])])
        assert world["scripts"] == []
        with P.policy_scope(wake):
            out = await M.send_message_impl(message="look", chat_id=GROUP, files=[str(mine)])
        assert "with 1 file" in out


class TestAFileSendIsOneUnit:
    """A file goes through the same cancellation-safe send as a text
    (``_send_and_note``): a step cut while Messages is sending finishes that
    file and records it, and a re-ask sends only what didn't go — never the
    text or a file twice from Ellis's number (review, 2026-10-11)."""

    @pytest.fixture
    def turn(self, monkeypatch, tmp_path):
        from services.graph import tools as legacy

        monkeypatch.setattr(legacy, "_current_turn", lambda: object())
        texts: list = []

        def send_to_chat(chat, text, **k):
            texts.append(text)
            return {"success": True}

        monkeypatch.setattr(im, "send_to_chat", send_to_chat)
        second = tmp_path / "home" / "plot.png"
        second.write_bytes(b"\x89PNG plot")
        return {"texts": texts, "second": second}

    @staticmethod
    def _owner():
        from services.toolset import policy as P

        return P.policy_scope(P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True))

    async def test_a_file_alone_is_not_sent_twice(self, world, guards, turn):
        from services.toolset import messaging as M

        world["osascript"]()
        with self._owner():
            first = await M.send_message_impl(message="", chat_id=GROUP, files=[str(world["original"])])
            again = await M.send_message_impl(message="", chat_id=GROUP, files=[str(world["original"])])
        assert "with 1 file" in first
        assert again.startswith("Already sent to") and again.endswith("— not sending again."), again
        assert len(world["scripts"]) == 1

    async def test_a_re_ask_after_a_failed_file_sends_only_what_did_not_go(self, world, guards, turn, monkeypatch):
        from services.toolset import messaging as M
        from services.toolset.result import ToolFailure

        failing = {"plot.png": True}

        def run(script, timeout=30):
            world["scripts"].append((script, timeout))
            staged = script.split('POSIX file "', 1)[1].split('"', 1)[0]
            if failing.get(Path(staged).name):
                return Proc(1, "execution error: Messages got an error (-1700)")
            rowid = world["db"].add_message(GROUP, "￼", is_from_me=True)
            world["db"].add_attachment(rowid, filename=staged, is_outgoing=True)
            return Proc()

        monkeypatch.setattr(im, "_run_osascript", run)
        files = [str(world["original"]), str(turn["second"])]
        with self._owner(), pytest.raises(ToolFailure, match="couldn't attach plot.png"):
            await M.send_message_impl(message="the charts", chat_id=GROUP, files=files)
        assert len(turn["texts"]) == 1 and len(world["scripts"]) == 2
        failing["plot.png"] = False
        with self._owner():
            out = await M.send_message_impl(message="the charts", chat_id=GROUP, files=files)
        assert len(turn["texts"]) == 1, "the text already went"
        sent = [s for s, _ in world["scripts"][2:]]
        assert len(sent) == 1 and "plot.png" in sent[0], "only the file that didn't go"
        assert "with 1 file" in out, out

    async def test_a_file_send_the_step_is_cancelled_under_finishes_and_is_recorded(self, world, guards, turn,
                                                                                   monkeypatch):
        import asyncio
        import threading

        from services.toolset import messaging as M

        entered, release = threading.Event(), threading.Event()

        def run(script, timeout=30):
            world["scripts"].append((script, timeout))
            entered.set()
            release.wait(5)
            staged = script.split('POSIX file "', 1)[1].split('"', 1)[0]
            rowid = world["db"].add_message(GROUP, "￼", is_from_me=True)
            world["db"].add_attachment(rowid, filename=staged, is_outgoing=True)
            return Proc()

        monkeypatch.setattr(im, "_run_osascript", run)
        with self._owner():
            task = asyncio.ensure_future(M.send_message_impl(message="", chat_id=GROUP,
                                                             files=[str(world["original"])]))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.05)
            assert not task.done(), "the send in flight holds the step until it has finished"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            again = await M.send_message_impl(message="", chat_id=GROUP, files=[str(world["original"])])
        assert again.startswith("Already sent to"), again
        assert len(world["scripts"]) == 1


class TestEchoAndConfirmation:
    async def test_the_listener_claims_an_attachment_row_in_flight_as_edwards(self):
        """The transport begins the send with U+FFFC (what chat.db holds for
        a file-only message). The listener compares a from-me row's caption,
        which a file-only row doesn't have — its text is the placeholder —
        so without this a row the listener reached before the read-back read
        as Ellis's own message (in his self-chat: a new ping)."""
        from services.heartbeat import listener_imessage as listener

        imessage_sent.reset_for_tests()
        imessage_sent.begin(GROUP, im.OBJECT_REPLACEMENT_CHAR)
        row = {"is_from_me": True, "rowid": 4242, "chat_identifier": GROUP, "caption": "",
               "text": "[image: chart.png attachment:77]",
               "attachments": [{"rowid": 77, "name": "chart.png", "mime_type": "image/png", "path": ""}]}
        assert await listener._echo_reason(None, row) == "inflight"
        assert imessage_sent.is_edward_rowid(4242)

    async def test_owners_own_photo_with_nothing_in_flight_stays_his(self):
        from services.heartbeat import listener_imessage as listener

        imessage_sent.reset_for_tests()

        class NoLedger:
            def begin_nested(self):
                raise RuntimeError("no ledger in this test")

        row = {"is_from_me": True, "rowid": 4243, "chat_identifier": GROUP, "caption": "",
               "text": "[image: IMG_1.heic attachment:78]",
               "attachments": [{"rowid": 78, "name": "IMG_1.heic", "mime_type": "image/heic", "path": ""}]}
        assert await listener._echo_reason(NoLedger(), row) is None

    def test_without_a_readable_chat_db_no_file_is_sent(self, world, monkeypatch):
        """A file send that can't be read back can't be confirmed, and an
        unconfirmed send reads as failed (so it may be tried again): it is
        refused before anything goes out."""
        world["osascript"]()
        monkeypatch.setattr(im, "_max_message_rowid", lambda: None)
        res = im.send_attachment(GROUP, str(world["original"]))
        assert res["success"] is False and "chat.db" in res["error"]
        assert world["scripts"] == []
