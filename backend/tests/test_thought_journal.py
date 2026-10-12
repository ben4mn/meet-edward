"""The thought journal — one short, readable conversation per wake that acts.

Ellis (2026-09-25) wanted the thinking behind Edward's actions readable
again, like a chat with himself: no approval needed, but it must be obvious
when Edward chooses to act. The mind rebuild
(Sep 18) put every wake into one ``edward-mind`` thread whose "user"
messages are 10–20K-character packs. ``services/thought_journal.py`` builds
the entry from the record (what the tools did) and his own words (why).

These are the pure parts: tiers, the journal rule, one line per action,
the headline, the entry, and the "why I looked" line from the pack.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from services import thought_journal as tj

pytestmark = pytest.mark.unit


def act(name, args=None, result="ok", ok=True, held=False):
    a = {"name": name, "args": dict(args or {}), "result": result, "ok": ok}
    if held:
        a["held"] = True
    return a


NAMES = {"chat123": "Family"}


# ---------------------------------------------------------------- tiers --

@pytest.mark.parametrize("name,args,tier", [
    ("send_imessage", {}, "outward"),
    ("send_message", {}, "outward"),
    ("send_sms", {}, "outward"),
    ("send_whatsapp", {}, "outward"),
    ("whatsapp_send_message", {}, "outward"),
    ("whatsapp_send_file", {}, "outward"),
    ("mail", {"operation": "send"}, "outward"),
    ("messages", {"operation": "send"}, "outward"),
    ("messages", {"operation": "schedule"}, "outward"),
    ("send_push_notification", {}, "outward"),
    ("leave_note", {}, "outward"),
    ("create_hosted_page", {}, "outward"),
    ("update_hosted_page", {}, "outward"),
    ("browser_type", {}, "outward"),
    ("schedule_event", {}, "commitment"),
    ("cancel_scheduled_event", {}, "commitment"),
    ("set_goal", {}, "commitment"),
    ("update_goal", {}, "commitment"),
    ("suggest_idea", {}, "commitment"),
    ("stand_down", {}, "commitment"),
    ("remember", {}, "internal"),
    ("remember_update", {}, "internal"),
    ("remember_forget", {}, "internal"),
    ("update_memory_confidence", {}, "internal"),
    ("save_document", {}, "internal"),
    ("edit_document", {}, "internal"),
    ("wake_me", {}, "internal"),
    ("wish_for_feature", {}, "internal"),
    ("update_wish", {}, "internal"),
    ("update_widget", {}, "internal"),
    ("update_widget_code", {}, "internal"),
    ("clear_widget_code", {}, "internal"),
    ("show_on_screen", {}, "internal"),
    ("clear_screen", {}, "internal"),
    ("compose_screen", {}, "internal"),
    ("say_on_screen", {}, "internal"),
    ("reset_screen_layout", {}, "internal"),
    # Looking is not acting.
    ("mail", {"operation": "unread"}, None),
    ("messages", {"operation": "read"}, None),
    ("remember_search", {}, None),
    ("web_search", {}, None),
    ("read_imessage_thread", {}, None),
    ("whatsapp_list_chats", {}, None),
])
def test_action_tier(name, args, tier):
    assert tj.action_tier(name, args) == tier
    assert tj.is_journal_tool(name, args) is (tier is not None)


@pytest.mark.parametrize("name,result,ok,held", [
    ("send_imessage", "iMessage sent to group chat chat123", True, False),
    ("send_imessage", "iMessage sent to +15551234567 (resolved 'Parker' via Contacts)", True, False),
    ("send_imessage", "Failed to send iMessage: timeout", False, False),
    ("send_imessage", "'Parker' matches multiple contacts (Parker A, Parker B). Send with an exact phone number/email, "
                      "or reply using the chat_id from the message context.", False, False),
    ("send_imessage", "Already sent to chat123 at 3:02pm — not sending again.", False, False),
    ("send_imessage", "Send-review rejected this draft: filler — revise it", False, False),
    ("send_imessage", "Tool error: stand-down active for chat123 — Parker asked you to stay out", False, False),
    ("send_imessage", "Waiting for Ellis's approval (#12). Nothing was sent yet.", True, True),
    ("send_message", "SMS sent from Edward to +15551234567 (Status: queued)", True, False),
    ("send_message", "Sent via iMessage (Twilio failed): iMessage sent to Rowan", True, False),
    ("send_message", "iMessage is disabled. Enable it in Settings to use this feature.", False, False),
    ("send_sms", "SMS sent successfully to +15551234567 (Status: queued)", True, False),
    ("send_whatsapp", "WhatsApp sent successfully to +15551234567 (Status: queued)", True, False),
    ("send_push_notification", "Push notification sent to 2 device(s).", True, False),
    ("send_push_notification", "Interrupt started: the screen Ellis is at first, then his phone", True, False),
    ("send_push_notification", "No active push subscriptions. User has not enabled notifications.", False, False),
    ("send_push_notification", "Tool error: this push would carry high-risk content (self_harm)", False, False),
    ("leave_note", "ok", True, False),
    ("leave_note", "Tool error: a note needs a title and a body.", False, False),
    ("schedule_event", "Event scheduled (one-time). Next fire: Fri 09:00 AM. ID: e-1", True, False),
    ("schedule_event", "Invalid datetime format: soon. Use ISO 8601 format", False, False),
    ("cancel_scheduled_event", "Event e-1 cancelled.", True, False),
    ("cancel_scheduled_event", "Could not cancel event e-1. It may already be completed or cancelled.", False, False),
    ("stand_down", "Stood down in chat123. You will not message this chat again until …", True, False),
    ("stand_down", "Failed to record stand-down: db down", False, False),
    ("set_goal", "ok (goal_id: g-1) — \"Run a half marathon\" saved", True, False),
    ("set_goal", "Already a goal: Run a half marathon (goal_id: g-1). Use update_goal.", False, False),
    ("suggest_idea", "ok (idea n-1) — on Today for a week.", True, False),
    ("suggest_idea", "Already on Today: \"Plan Saturday's climb\". Nothing added.", False, False),
    ("remember", "ok (memory_id: m-1)", True, False),
    ("remember_update", "Memory with ID m-9 not found. Cannot update.", False, False),
    ("save_document", "Document saved: \"Edward: state\" (ID: d-1)", True, False),
    ("edit_document", "Document d-1 not found.", False, False),
    ("wake_me", "ok — you will wake at Fri 06:00 PM (event e-2)", True, False),
    ("wake_me", "Tool error: when must be in the future.", False, False),
    ("create_hosted_page", "Page published!\n\nURL: https://pages.example.com/x\nSlug: x\nExpiry: permanent", True, False),
    ("create_hosted_page", "Failed to create page: slug taken", False, False),
    ("browser_type", "Typed 'hello' and pressed Enter. Now on https://x.test", True, False),
    ("show_on_screen", "Showing text on the household screen until 3:10pm.", True, False),
    ("show_on_screen", "Could not show that: unknown kind 'web'.", False, False),
    ("clear_screen", "Screen cleared.", True, False),
    ("clear_screen", "Nothing was showing.", False, False),
    ("clear_screen", "Not changed: Ellis asked for what is on your screen, and it holds until 8:10 pm.", False, False),
    ("compose_screen", "Your screen now: clock center; you stand on the floor.", True, False),
    ("compose_screen", "Not changed: no usable pieces: weather is not a piece kind", False, False),
    ("say_on_screen", "Said aloud on your screen at 5:52 pm.", True, False),
    ("say_on_screen", "Not said: the line was empty.", False, False),
    ("reset_screen_layout", "Screen reset to the stock scene.", True, False),
    ("reset_screen_layout", "The screen was already on the stock scene.", False, False),
    ("whatsapp_send_message", '{"success": true, "message": "sent"}', True, False),
    ("whatsapp_send_message", '{"success": false, "message": "no such chat"}', False, False),
    ("mail", "Email sent to dana@x.test", True, False),
    ("mail", "Error: Mail.app is not running", False, False),
])
def test_action_ok_reads_each_tools_own_reply(name, result, ok, held):
    assert tj.action_ok(name, result) == (ok, held)


# ------------------------------------------------------- the journal rule --

def test_should_journal_needs_a_successful_outward_or_commitment_action():
    assert tj.should_journal([act("send_imessage", result="iMessage sent to x")], "mind") is True
    assert tj.should_journal([act("schedule_event")], "mind") is True
    assert tj.should_journal([act("leave_note")], "mind") is True
    # Internal work alone is not "choosing to act" on anyone.
    assert tj.should_journal([act("remember"), act("wake_me"), act("save_document")], "mind") is False
    assert tj.should_journal([], "mind") is False
    # A failed send alone is not an action.
    assert tj.should_journal([act("send_imessage", result="Failed", ok=False)], "mind") is False
    # A send held for Ellis's approval is still Edward choosing to act.
    assert tj.should_journal([act("send_imessage", result="Waiting for Ellis's approval (#3)", held=True)],
                             "mind") is True


def test_the_pulse_never_journals():
    assert tj.should_journal([act("send_push_notification"), act("update_widget")], "pulse") is False


def test_reached_someone_counts_delivered_sends_and_pushes_only():
    assert tj.reached_someone([act("send_imessage", result="iMessage sent to x")]) is True
    assert tj.reached_someone([act("send_push_notification", result="Push notification sent to 1 device(s).")])
    assert not tj.reached_someone([act("send_imessage", result="Waiting for Ellis's approval (#3)", held=True)])
    assert not tj.reached_someone([act("send_imessage", result="Failed", ok=False)])
    assert not tj.reached_someone([act("leave_note"), act("schedule_event")])


# ------------------------------------------------------ one line each --

FRI_9 = "2026-09-25T09:00:00"   # a Friday, in the user's zone
FRI_18 = "2026-09-25T18:00:00"


@pytest.mark.parametrize("a,line", [
    (act("send_imessage", {"contact": "Parker", "message": "running 10 min late\n\n- Edward 🤖", "chat_id": "chat123"},
         "iMessage sent to group chat chat123"),
     "Sent an iMessage to Parker (Family): “running 10 min late”"),
    (act("send_imessage", {"contact": "Parker", "message": "on my way"}, "iMessage sent to Parker"),
     "Sent an iMessage to Parker: “on my way”"),
    (act("send_imessage", {"contact": "x", "message": "hi", "chat_id": "iMessage;+;chat123"}, "iMessage sent"),
     "Sent an iMessage to x (Family): “hi”"),
    (act("send_message", {"recipient": "Rowan", "message": "Biscuit ate"},
         "SMS sent from Edward to +15551234567 (Status: queued)"),
     "Sent a text to Rowan: “Biscuit ate”"),
    (act("send_message", {"recipient": "Rowan", "message": "Biscuit ate"}, "iMessage sent to Rowan"),
     "Sent an iMessage to Rowan: “Biscuit ate”"),
    (act("send_sms", {"phone_number": "+15551234567", "message": "hello"}, "SMS sent successfully to +15551234567"),
     "Sent a text to +15551234567: “hello”"),
    (act("send_whatsapp", {"phone_number": "+15551234567", "message": "hello"}, "WhatsApp sent successfully"),
     "Sent a WhatsApp to +15551234567: “hello”"),
    (act("whatsapp_send_message", {"recipient": "Dana", "message": "see you"}, '{"success": true}'),
     "Sent a WhatsApp to Dana: “see you”"),
    (act("mail", {"operation": "send", "to": "dana@x.test", "subject": "Notes", "body": "…"}, "Email sent"),
     "Emailed dana@x.test: “Notes”"),
    (act("messages", {"operation": "send", "phoneNumber": "+15551234567", "message": "hi"}, "Message sent"),
     "Sent an iMessage to +15551234567: “hi”"),
    (act("send_push_notification", {"title": "Biscuit's 6pm dose is late"}, "Push notification sent to 1 device(s)."),
     "Pushed you: “Biscuit's 6pm dose is late”"),
    (act("send_push_notification", {"title": "Biscuit's dose", "body": "It's 7:10 and the pill is still there."},
         "Push notification sent to 1 device(s)."),
     "Pushed you: “Biscuit's dose” — It's 7:10 and the pill is still there."),
    (act("send_push_notification", {"title": "Smoke alarm", "urgency": "high"}, "Interrupt started: …"),
     "Pushed you, urgently: “Smoke alarm”"),
    (act("leave_note", {"title": "Before your 3pm with Dana", "body": "…"}),
     "Left a note on Today: “Before your 3pm with Dana”"),
    (act("leave_note", {"title": "Call the vet", "body": "…", "needs_you": True}),
     "Left a note on Today: “Call the vet” (needs you)"),
    (act("create_hosted_page", {"html": "<p>", "slug": "biscuit"},
         "Page published!\n\nURL: https://pages.example.com/biscuit\nSlug: biscuit"),
     "Published a page: https://pages.example.com/biscuit"),
    (act("update_hosted_page", {"slug": "biscuit", "html": "<p>"}, "Page updated!"),
     "Updated the page “biscuit”"),
    (act("browser_type", {"text": "jazz playlist", "submit": True}, "Typed 'jazz playlist'"),
     "Typed “jazz playlist” in Chrome"),
    (act("schedule_event", {"description": "call the vet", "scheduled_at": FRI_9}, "Event scheduled"),
     "Scheduled “call the vet” for Fri 9:00am"),
    (act("schedule_event", {"description": "water the plants", "scheduled_at": FRI_9,
                            "recurrence_pattern": "0 9 * * *"}, "Event scheduled"),
     "Scheduled “water the plants” (repeats 0 9 * * *), first Fri 9:00am"),
    (act("cancel_scheduled_event", {"event_id": "e-1234"}, "Event e-1234 cancelled."),
     "Cancelled a scheduled event (e-1234)"),
    (act("set_goal", {"title": "Run a half marathon", "why": "health"}),
     "Set a goal: Run a half marathon"),
    (act("update_goal", {"goal_id": "g-1", "progress": "ran 5k"},
         "ok Run a half marathon [active] (goal_id: g-1) — ran 5k"),
     "Logged progress on “Run a half marathon”: ran 5k"),
    (act("update_goal", {"goal_id": "g-1", "status": "done"}, "ok Run a half marathon [done] (goal_id: g-1)"),
     "Marked “Run a half marathon” done"),
    (act("suggest_idea", {"title": "Plan Saturday's climb", "why": "…", "prompt": "…"}),
     "Suggested an idea: Plan Saturday's climb"),
    (act("stand_down", {"chat_id": "chat123", "requested_by": "Parker"}, "Stood down in chat123."),
     "Stood down in Family (Parker asked)"),
    (act("stand_down", {"chat_id": "chat999"}, "Stood down in chat999."),
     "Stood down in chat999"),
    (act("remember", {"content": "Parker is flying to Denver Friday"}, "ok (memory_id: m-1)"),
     "Remembered: Parker is flying to Denver Friday"),
    (act("remember_update", {"memory_id": "m-1", "new_content": "Parker flies Saturday"}),
     "Updated a memory: Parker flies Saturday"),
    (act("remember_forget", {"memory_id": "m-1"}), "Forgot a memory (m-1)"),
    (act("update_memory_confidence", {"memory_id": "m-1", "new_confidence": 0.3, "reason": "Ellis said otherwise"}),
     "Changed my confidence in a memory to 0.3: Ellis said otherwise"),
    (act("save_document", {"title": "Edward: state", "content": "…"}, "Document saved: \"Edward: state\""),
     "Saved a document: “Edward: state”"),
    (act("edit_document", {"document_id": "d-1", "content": "…"}, "Document updated: \"Edward: state\" (ID: d-1)"),
     "Edited a document: “Edward: state”"),
    (act("wake_me", {"when": FRI_18, "why": "check whether Parker replied"}, "ok — you will wake at …"),
     "Will look again Fri 6:00pm: check whether Parker replied"),
    (act("wish_for_feature", {"title": "Read Ellis's email", "why": "…"}, "Noted on the wishlist: …"),
     "Wished for: Read Ellis's email"),
    (act("update_wish", {"wish_id": "w-1", "status": "done"}, "Updated: … [done]"),
     "Updated a wish (done)"),
    (act("update_widget", {"sections": [], "title": "Friday"}, "Widget updated with 2 section(s)."),
     "Changed the widget: “Friday”"),
    (act("update_widget_code", {"code": "…"}, "Widget code updated."), "Rewrote the widget's code"),
    (act("clear_widget_code", {}, "Custom widget code cleared."), "Cleared the widget's custom code"),
    (act("show_on_screen", {"kind": "text", "title": "Dinner at 7", "screen": "house"}, "Showing text …"),
     "Put a text on the household screen: “Dinner at 7”"),
    (act("show_on_screen", {"kind": "image", "screen": "mini"}, "Showing image …"),
     "Put an image on the Mac screen"),
    (act("clear_screen", {"screen": "mini"}, "Screen cleared on this Mac; it is back on your scene."),
     "Cleared the Mac screen"),
    (act("compose_screen", {"pieces": []}, "Your screen now: clock center; you stand on the floor."),
     "Set the scene on the Mac screen"),
    (act("say_on_screen", {"line": "Dinner."}, "Said on your screen at 5:52 pm."),
     "Said something on the Mac screen"),
    (act("reset_screen_layout", {}, "Screen reset to the stock scene."),
     "Reset the Mac screen to its stock scene"),
])
def test_describe_action_says_what_happened_from_the_record(a, line):
    assert tj.describe_action(a, names=NAMES) == line


def test_describe_action_marks_failures_and_held_sends():
    failed = act("send_imessage", {"contact": "Parker", "message": "hi"},
                 "Tool error: stand-down active for chat123 — Parker asked you to stay out of this chat at 2pm. "
                 "You cannot message it until someone there @edward-mentions you again", ok=False)
    line = tj.describe_action(failed)
    assert line.startswith("Sent an iMessage to Parker: “hi” — failed: stand-down active for chat123")
    assert len(line) < 200
    held = act("send_imessage", {"contact": "Parker", "message": "hi"}, "Waiting for Ellis's approval (#4)", held=True)
    assert tj.describe_action(held) == "Sent an iMessage to Parker: “hi” — held for approval"


def test_describe_action_bounds_long_text():
    a = act("send_imessage", {"contact": "Parker", "message": "word " * 200}, "iMessage sent to Parker")
    line = tj.describe_action(a)
    assert line.endswith("…”") and len(line) < 220


def test_describe_action_never_raises_on_odd_args():
    for a in (act("send_imessage", None), act("schedule_event", {"scheduled_at": "soon"}),
              act("wake_me", {"when": 42}), {"name": "leave_note"}, {}):
        assert isinstance(tj.describe_action(a), str)


def test_times_are_the_users_zone(monkeypatch):
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Los_Angeles")
    # A naive time is already the user's; an aware one is converted.
    assert tj.describe_action(act("wake_me", {"when": FRI_18, "why": "x"})) == "Will look again Fri 6:00pm: x"
    aware = act("wake_me", {"when": "2026-09-26T01:00:00+00:00", "why": "x"})
    assert tj.describe_action(aware) == "Will look again Fri 6:00pm: x"


# ------------------------------------------------------------- headline --

@pytest.mark.parametrize("a,reason,title", [
    (act("send_imessage", {"contact": "Parker", "message": "hi", "chat_id": "chat123"}, "iMessage sent"),
     "mention", "Replied to Parker in Family"),
    (act("send_imessage", {"contact": "Parker", "message": "hi"}, "iMessage sent"), "messages", "Replied to Parker"),
    (act("send_imessage", {"contact": "Parker", "message": "hi"}, "iMessage sent"), "morning", "Messaged Parker"),
    (act("send_push_notification", {"title": "Biscuit's dose is late"}, "Push notification sent to 1 device(s)."),
     "self", "Pushed you: Biscuit's dose is late"),
    (act("leave_note", {"title": "Before your 3pm", "body": "…"}), "prep", "Left a note: Before your 3pm"),
    (act("schedule_event", {"description": "call the vet", "scheduled_at": FRI_9}), "morning",
     "Scheduled: call the vet"),
    (act("stand_down", {"chat_id": "chat123"}), "mention", "Stood down in Family"),
    (act("set_goal", {"title": "Read more"}), "manual", "Set a goal: Read more"),
    (act("suggest_idea", {"title": "Plan the climb"}), "reflect", "Suggested: Plan the climb"),
])
def test_headline(a, reason, title):
    assert tj.headline(a, names=NAMES, reason=reason) == title


ONE_TO_ONE = {"chat123": "Family", "+15551234567": "Parker", "chat777": "Parker"}


@pytest.mark.parametrize("args,title,line", [
    # Parker's own 1:1 chat is "Parker": "Replied to Parker in Parker" (review 2026-09-26).
    ({"contact": "Parker", "chat_id": "iMessage;-;+15551234567", "message": "hi"}, "Replied to Parker",
     "Sent an iMessage to Parker"),
    ({"contact": "+15551234567", "chat_id": "+15551234567", "message": "hi"}, "Replied to Parker",
     "Sent an iMessage to Parker"),
    ({"chat_id": "iMessage;-;+15551234567", "message": "hi"}, "Replied to Parker", "Sent an iMessage to Parker"),
    ({"contact": "parker", "chat_id": "chat777", "message": "hi"}, "Replied to Parker", "Sent an iMessage to Parker"),
    # A group keeps its name.
    ({"contact": "Parker", "chat_id": "chat123", "message": "hi"}, "Replied to Parker in Family",
     "Sent an iMessage to Parker (Family)"),
])
def test_a_one_to_one_reply_is_titled_by_the_person_alone(args, title, line):
    a = act("send_imessage", args, "iMessage sent")
    assert tj.headline(a, names=ONE_TO_ONE, reason="mention") == title
    assert tj.describe_action(a, ONE_TO_ONE).startswith(line)


def test_headline_is_short():
    a = act("leave_note", {"title": "A very long note title that goes on and on about many things at once"})
    title = tj.headline(a)
    assert len(title) <= 64 and title.endswith("…")


# ----------------------------------------------------------- the entry --

WHEN = datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)


def test_compose_entry_reads_like_a_page():
    actions = [
        act("remember_search", {"query": "Parker"}),   # not an action; ignored if passed
        act("send_imessage", {"contact": "Parker", "message": "Ellis's in a meeting till 4 - Edward 🤖",
                              "chat_id": "chat123"}, "iMessage sent to group chat chat123"),
        act("remember", {"content": "Parker asked about Ellis at 3pm"}, "ok (memory_id: m-1)"),
        act("wake_me", {"when": FRI_18, "why": "see if Parker needs more"}, "ok — …"),
    ]
    title, narrative = tj.compose_entry(
        reason="mention", woke_for="Parker mentioned me in Family",
        reasoning=["**Reading the thread**\n\nParker asked whether Ellis is free; the calendar says a meeting till 4."],
        closing_note="Parker asked where Ellis was. His calendar says a meeting until 4, so I told Parker that and nothing more.",
        actions=actions, when=WHEN, names=NAMES,
    )
    assert title == "Replied to Parker in Family"
    assert narrative.startswith("**Why I looked** — A mention: Parker mentioned me in Family")
    assert "**What I noticed** — Parker asked whether Ellis is free; the calendar says a meeting till 4." in narrative
    assert "**Reading the thread**" not in narrative
    assert "**What I did**\n- Sent an iMessage to Parker (Family): “Ellis's in a meeting till 4”" in narrative
    assert "- Also: remembered: Parker asked about Ellis at 3pm; will look again Fri 6:00pm: see if Parker needs more" \
        in narrative
    assert narrative.rstrip().endswith("**Why** — Parker asked where Ellis was. His calendar says a meeting until 4, "
                                       "so I told Parker that and nothing more.")
    assert "remember_search" not in narrative
    # Sections are paragraphs, in this order.
    order = [narrative.index(h) for h in ("**Why I looked**", "**What I noticed**", "**What I did**", "**Why** —")]
    assert order == sorted(order)
    assert "\n\n**What I did**" in narrative


def test_compose_entry_omits_what_is_empty():
    title, narrative = tj.compose_entry(
        reason="self", woke_for="", reasoning=[], closing_note="",
        actions=[act("leave_note", {"title": "Vet on Monday", "body": "…"})], when=WHEN,
    )
    assert title == "Left a note: Vet on Monday"
    assert "**What I noticed**" not in narrative and "Also:" not in narrative
    assert "**Why** —" not in narrative
    assert narrative.startswith("**Why I looked** — A check-in I set\n")


def test_compose_entry_headlines_the_first_success():
    actions = [act("send_imessage", {"contact": "Parker", "message": "hi"}, "Failed to send iMessage: x", ok=False),
               act("send_push_notification", {"title": "Couldn't reach Parker"}, "Push notification sent to 1 device(s).")]
    title, narrative = tj.compose_entry(reason="messages", woke_for="1 message waiting on me", reasoning=[],
                                        closing_note="ok", actions=actions, when=WHEN)
    assert title == "Pushed you: Couldn't reach Parker"
    assert "— failed: Failed to send iMessage: x" in narrative


def test_compose_entry_bounds_reasoning_and_the_page():
    reasoning = ["thought " * 400, "thought " * 400]
    actions = [act("remember", {"content": f"fact {i}"}) for i in range(40)]
    actions += [act("leave_note", {"title": f"note {i}", "body": "b"}) for i in range(40)]
    _, narrative = tj.compose_entry(reason="reflect", woke_for="x", reasoning=reasoning, closing_note="y" * 5000,
                                    actions=actions, when=WHEN)
    noticed = narrative.split("**What I noticed** — ", 1)[1].split("\n", 1)[0]
    assert len(noticed) <= 620
    assert len(narrative) <= tj.MAX_NARRATIVE_CHARS
    assert "…and 28 more" in narrative


def test_reasoning_summary_dedupes_and_strips_headings():
    assert tj.reasoning_summary(["**A**\n\nfirst.", "**A**\n\nfirst.", "second."]) == "first. second."
    assert tj.reasoning_summary([]) == ""


# ------------------------------------------------------ why I looked --

def _pack(**kw):
    from services.mind_service import MindPack

    base = dict(now=WHEN, reason="messages", last_wake_at=None, attention={}, messages=[], calendar=[], board="",
                notes="", pushes="", verdicts=[], thoughts=[], actions=[], surfaces={}, escalations=[],
                state_document=None, extra=None)
    base.update(kw)
    return MindPack(**base)


def test_woke_for_line_names_who_is_waiting():
    p = _pack(reason="messages", escalations=[
        {"id": "e1", "sender": "Parker", "chat": "Family", "text": "anyone around?"},
        {"id": "e2", "sender": "Rowan", "chat": None, "text": "dinner?"},
        {"id": "e3", "sender": "Parker", "chat": "Family", "text": "@Edward is Ellis free at 3?"},
    ])
    assert tj.woke_for_line(p) == "3 messages waiting on me, from Parker and Rowan; Parker mentioned me in Family"


def test_woke_for_line_for_a_mention_and_a_check_in_and_a_prep():
    p = _pack(reason="mention", escalations=[{"id": "e1", "sender": "Parker", "chat": "Family",
                                              "text": "hey @edward what time is dinner"}])
    assert tj.woke_for_line(p) == "Parker mentioned me in Family"
    assert tj.woke_for_line(_pack(reason="self", extra="Check whether Parker replied about Friday.")) == \
        "Check whether Parker replied about Friday."
    prep = ('Meeting to prepare: "1:1 with Dana" at 3:00pm (in 90 min), Zoom. With: Dana. '
            'Note title: "Before 1:1 with Dana".')
    assert tj.woke_for_line(_pack(reason="prep", extra=prep)) == "“1:1 with Dana” at 3:00pm starts in 90 min"


def test_woke_for_line_for_a_tick_counts_the_day():
    from datetime import timedelta

    p = _pack(reason="morning", last_wake_at=WHEN - timedelta(hours=10), calendar=["09:00 standup", "15:00 Dana"],
              messages=[{"text": "a", "timestamp": "2026-09-25T13:00:00"}, {"text": "b", "timestamp": "2026-09-25T13:10:00"}])
    assert tj.woke_for_line(p) == "2 new messages since I last looked; 2 things on the calendar today"
    assert tj.woke_for_line(None) == ""


def test_free_time_has_its_own_label():
    assert tj.reason_label("free") == "Free time"
    assert tj.reason_label("prep") == "Meeting prep"


def test_woke_for_message_for_a_wake_without_a_pack():
    assert tj.woke_for_message("[PULSE] tick at 3pm\nmore") == "tick at 3pm"
    assert tj.woke_for_message(None) == "" and tj.woke_for_message("") == ""


def test_chat_names_from_the_pack():
    p = _pack(messages=[{"chat_identifier": "chat123", "chat_name": "Family", "text": "x"}],
              escalations=[{"id": "e1", "sender": "Parker", "chat": "Book club", "chat_identifier": "chat9"}])
    names = tj.chat_names(p)
    assert names["chat123"] == "Family"
    assert names["chat9"] == "Book club"
    assert tj.chat_names(None) == {}


# ------------------------------------------------------- ledger detail --

def test_detail_with_thought_keeps_the_id_and_the_bound():
    detail = json.dumps({"tools": ["remember"] * 5, "surfaces": [], "delivered": True})
    out = json.loads(tj.detail_with_thought(detail, "t-1"))
    assert out["thought_id"] == "t-1" and out["delivered"] is True and out["tools"] == ["remember"] * 5

    long = json.dumps({"tools": [f"tool_number_{i}" for i in range(60)], "surfaces": [], "delivered": False})
    text = tj.detail_with_thought(long, "t-2")
    assert len(text) <= 600
    parsed = json.loads(text)
    assert parsed["thought_id"] == "t-2" and parsed["tools"]

    assert json.loads(tj.detail_with_thought(None, "t-3")) == {"thought_id": "t-3"}
    assert json.loads(tj.detail_with_thought("{not json", "t-4")) == {"thought_id": "t-4"}


# ------------------------------------------------- close_intention --

def test_close_intention_is_his_own_bookkeeping_read_by_its_ok():
    """Batch A1's tool (2026-09-27, followups M4) sets an intention's
    valid_until to now and keeps its history; it answers a string starting
    "ok" on success, like ``remember_update``. Closing an intention is
    internal: it rides along in "Also:" and never makes an entry alone."""
    assert tj.action_tier("close_intention") == "internal"
    assert tj.is_journal_tool("close_intention")
    assert tj.SUCCESS_PREFIXES["close_intention"] == ("ok",)
    assert tj.action_ok("close_intention", "ok — closed m-1 (the briefing went out)") == (True, False)
    assert tj.action_ok("close_intention", "Tool error: m-9 is not an open intention") == (False, False)
    assert tj.action_ok("close_intention", "Memory m-9 not found.") == (False, False)
    closed = act("close_intention", {"memory_id": "m-1", "outcome": "the Parker briefing went out Monday"},
                 "ok — closed m-1")
    assert tj.describe_action(closed) == "Closed an intention: the Parker briefing went out Monday"
    assert tj.describe_action(act("close_intention", {"memory_id": "m-1"}, "ok")) == "Closed an intention (m-1)"
    assert tj.should_journal([closed], "mind") is False
    _, narrative = tj.compose_entry(reason="free", woke_for=None, reasoning=None, closing_note=None,
                                    actions=[act("leave_note", {"title": "Parker", "body": "…"}), closed])
    assert "- Also: closed an intention: the Parker briefing went out Monday" in narrative


def test_a_memory_update_that_only_moves_its_horizon_says_so():
    """Batch A1 (followups M4) lets ``remember_update`` take ``valid_until``
    alone and answers "ok (retired as of … UTC)" or "ok (valid until …
    UTC)". With no new text the line used to read "Updated a memory: " and
    stop — the line Ellis would have read in a research entry that closed its
    intention (2026-09-27 review)."""
    retired = act("remember_update", {"memory_id": "m-1", "valid_until": "2026-09-26T00:00:00"},
                  "ok (retired as of 2026-09-26 05:00 UTC)")
    assert tj.describe_action(retired) == "Retired a memory (m-1)"
    lapses = act("remember_update", {"memory_id": "m-2", "valid_until": "2026-10-01T09:00:00"},
                 "ok (valid until 2026-10-01 14:00 UTC)")
    assert tj.describe_action(lapses) == "Set a memory to lapse Thu 9:00am (m-2)"
    rewritten = act("remember_update", {"memory_id": "m-3", "new_content": "CLOSED — the rebate ended",
                                        "valid_until": "2026-09-26T00:00:00"},
                    "ok (retired as of 2026-09-26 05:00 UTC)")
    assert tj.describe_action(rewritten) == "Retired a memory: CLOSED — the rebate ended"
    refused = act("remember_update", {"memory_id": "m-4", "valid_until": "someday"},
                  "Tool error: valid_until must be an ISO-8601 datetime like 2026-10-01T09:00:00.", ok=False)
    assert tj.describe_action(refused).startswith("Set a memory to lapse someday (m-4) — failed: ")
    assert tj.describe_action(act("remember_update", {"memory_id": "m-5"}, "ok")) == "Updated a memory (m-5)"
    _, narrative = tj.compose_entry(reason="research", woke_for=None, reasoning=None, closing_note=None,
                                    actions=[act("leave_note", {"title": "Rebate", "body": "…"}), retired])
    assert "- Also: retired a memory (m-1)" in narrative
    assert "updated a memory: \n" not in narrative + "\n"


# ------------------------------------------------- the API's thought --

def test_the_entry_says_whether_it_reached_someone_not_just_whether_it_was_outward():
    """L3 (2026-09-27): the "reached out" chip read ``outward``, which a
    note on Today and a send held for Ellis's approval both set. ``reached``
    is ``reached_someone``: a message delivered to a person, or a push that
    reached Ellis."""
    def pack(*actions):
        return tj.entry_context_pack(ledger_id=1, reason="free", wake_thread="edward-free-time", turn_id="t",
                                     actions=list(actions), acted_at=None)

    note = pack(act("leave_note"))
    assert note["outward"] is True and note["reached"] is False
    held = pack(act("send_imessage", result="Waiting for Ellis's approval (#3)", held=True))
    assert held["outward"] is True and held["reached"] is False
    assert pack(act("send_imessage", result="iMessage sent to +15551234567"))["reached"] is True
    assert pack(act("leave_note"), act("send_push_notification", result="Push notification sent to 1 device(s)."))[
        "reached"] is True
    assert pack(act("schedule_event", result="Event scheduled"))["reached"] is False


def test_thought_info_carries_reached_and_older_entries_lack_it():
    base = {"kind": "mind_action", "ledger_id": 7, "reason": "free", "acted_at": "2026-09-27T14:30:00Z",
            "outward": True}
    assert tj.thought_info(json.dumps({**base, "reached": False}))["reached"] is False
    assert tj.thought_info(json.dumps({**base, "reached": True}))["reached"] is True
    assert "reached" not in tj.thought_info(json.dumps(base)), "an entry written before 2026-09-27"


def test_thought_info_parses_safely():
    pack = {"kind": "mind_action", "ledger_id": 7, "reason": "mention", "acted_at": "2026-09-25T14:30:00Z",
            "outward": True, "wake_thread": "edward-mind", "pack_excerpt": "…"}
    assert tj.thought_info(json.dumps(pack)) == {"kind": "mind_action", "ledger_id": 7, "reason": "mention",
                                                 "acted_at": "2026-09-25T14:30:00Z", "outward": True,
                                                 "verdict": None}
    assert tj.thought_info(json.dumps(pack), verdict="wrong_call")["verdict"] == "wrong_call"
    for bad in (None, "", "{", "[]", json.dumps({"kind": "wakeup"}), json.dumps({**pack, "ledger_id": None}),
                json.dumps({**pack, "ledger_id": "x"})):
        assert tj.thought_info(bad) is None
