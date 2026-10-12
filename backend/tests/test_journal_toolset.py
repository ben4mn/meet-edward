"""The thought journal and the ledgers read the toolset's names (tools-spec
§8.3; critique #12, #33).

The tiers are by ``(tool, action)``: ``send_message``, ``tell_owner`` and
Chrome typing reach out; ``schedule`` add/cancel and a stand-down commit;
the screen and memory writes are bookkeeping. A shell command that installs
something or leaves a job running is code an unattended turn ran, and
earns an entry. The sentences reuse the old tools' (``names.legacy_view``).
"""

from __future__ import annotations

import types

import pytest

from services import event_ledger, thought_journal as tj
from services.graph.turn_trace import args_summary

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name,args,tier", [
    ("send_message", {"chat_id": "c", "message": "m"}, "outward"),
    ("tell_owner", {"message": "m"}, "outward"),
    ("chrome", {"action": "type", "text": "x"}, "outward"),
    ("chrome", {"action": "open", "url": "u"}, None),
    ("schedule", {"action": "add", "when": "w", "what": "x"}, "commitment"),
    ("schedule", {"action": "list"}, None),
    ("messages", {"action": "stand_down", "chat_id": "c"}, "commitment"),
    ("messages", {"action": "thread", "chat_id": "c"}, None),
    ("screen", {"action": "compose"}, "internal"),
    ("memory", {"action": "remember", "content": "x"}, "internal"),
    ("memory", {"action": "search", "query": "x"}, None),
    ("bash", {"command": "pip install yt-dlp"}, "commitment"),
    ("bash", {"command": "cat notes.md"}, None),
    # apple-mcp's old ``messages`` still reads by operation.
    ("messages", {"operation": "send", "phoneNumber": "+1"}, "outward"),
])
def test_tiers(name, args, tier):
    assert tj.action_tier(name, args) == tier


def test_reached_someone():
    assert tj.reached_someone([{"name": "send_message", "args": {"chat_id": "c"}, "ok": True}])
    assert tj.reached_someone([{"name": "tell_owner", "args": {"message": "m", "urgent": True}, "ok": True}])
    assert not tj.reached_someone([{"name": "tell_owner", "args": {"message": "m"}, "ok": True}])
    assert not tj.reached_someone([{"name": "send_message", "args": {"chat_id": "c"}, "ok": False}])


def test_sentences():
    say = lambda name, args, result="ok": tj.describe_action({"name": name, "args": args, "result": result, "ok": True})
    assert say("send_message", {"to": "Parker", "message": "On my way"}, "Sent via iMessage to Parker").startswith(
        "Sent an iMessage to Parker")
    assert say("tell_owner", {"title": "Biscuit", "message": "Pill late", "urgent": True}).startswith("Pushed you")
    assert say("tell_owner", {"title": "Parker", "message": "asked"}).startswith("Left a note on Today")
    assert say("schedule", {"action": "add", "kind": "wake", "when": "2026-10-12T09:00:00", "what": "check"}
               ).startswith("Will look again")
    assert say("schedule", {"action": "add", "what": "Text Rowan", "when": "2026-10-12T09:00:00"}
               ).startswith("Scheduled")
    assert say("messages", {"action": "stand_down", "chat_id": "chat1"}).startswith("Stood down in")
    assert say("memory", {"action": "remember", "content": "Ellis likes figs"}) == "Remembered: Ellis likes figs"
    assert say("screen", {"action": "compose", "pieces": []}) == "Set the scene on the Mac screen"
    assert say("bash", {"command": "pip install yt-dlp"}) == "Ran on the Mac: pip install yt-dlp"


def test_a_headline():
    a = {"name": "send_message", "args": {"chat_id": "chat123", "message": "hi"}, "result": "Sent", "ok": True}
    assert tj.headline(a, reason="mention").startswith("Replied in") or tj.headline(a, reason="mention").startswith(
        "Replied to")


def test_unattended_shell_code_earns_an_entry():
    acts = [{"name": "bash", "args": {"command": "nohup python poll.py > jobs/p.log 2>&1 &"}, "ok": True}]
    assert tj.should_journal_unattended(acts, "scheduled_event")
    assert not tj.should_journal_unattended(acts, "chat")
    assert not tj.should_journal_unattended([{"name": "bash", "args": {"command": "ls"}, "ok": True}],
                                            "scheduled_event")


def test_event_ledger_surfaces_and_delivery():
    assert event_ledger.surfaces_touched([{"n": "screen", "ok": True}]) == ["screen"]
    assert event_ledger.pulse_decision("set the evening scene", [{"n": "screen", "ok": True}])[0] == "changed"
    assert event_ledger.delivered([{"n": "tell_owner", "ok": True}])
    assert event_ledger.delivered([{"n": "send_message", "ok": True}])
    assert not event_ledger.delivered([{"n": "send_message", "ok": False}])


def test_the_trace_never_shows_typed_text():
    assert args_summary("chrome", {"action": "type", "text": "hunter2"}) == "type"
    assert "hunter2" not in str(args_summary("browser", {"action": "steps", "steps": [{"text": "hunter2"}]}))
    assert args_summary("send_message", {"chat_id": "chat1", "message": "secret plans"}) == "to: chat1"


def test_an_event_owner_asked_for_is_his():
    from services.scheduled_events_service import event_is_owners

    ev = lambda meta, conv="edward-mind": types.SimpleNamespace(created_by="edward", event_metadata=meta,
                                                                conversation_id=conv)
    assert event_is_owners(ev('{"requested_by_owner": true, "created_in": "ping_owner"}'))
    assert not event_is_owners(ev('{"requested_by_owner": false, "created_in": "chat"}', conv="c1"))
    assert event_is_owners(ev('{"created_in": "chat"}'))
