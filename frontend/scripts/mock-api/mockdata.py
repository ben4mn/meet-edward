"""Synthetic fixtures for the Edward UX harness.

Everything here is invented — a fictional household, not anyone's real life:
the owner Ellis, their partner Rowan, the pets Biscuit, Maple, Pepper and
Mr. Whiskers, friends Parker and Jordan, the "Family" group, the places,
restaurants, medicines and trips. Every message, number and address is made
up: phone numbers use the 555-01xx fiction range, emails use example.com,
and Biscuit's med list is a generic example, not veterinary advice.

Timestamps are relative to the moment the mock starts (``START``), so the
feed always reads "2h ago", "in 3h" and so on. ``build_state()`` returns one
mutable dict that the server keeps in memory; mutations (log a dose, answer
an approval, delete a memory) change it until the mock restarts.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

UTC = timezone.utc
TZ_NAME = "America/Chicago"
START = datetime.now(UTC)


def ago(**kw) -> datetime:
    return START - timedelta(**kw)


def ahead(**kw) -> datetime:
    return START + timedelta(**kw)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def next_local(weekday: int, hour: int) -> datetime:
    """The next <weekday> (Mon=0 … Sun=6) at <hour>:00 in Ellis's zone, as UTC."""
    from zoneinfo import ZoneInfo

    local = START.astimezone(ZoneInfo(TZ_NAME))
    days = (weekday - local.weekday()) % 7
    cand = (local + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if cand <= local:
        cand += timedelta(days=7)
    return cand.astimezone(UTC)


def naive_iso(dt: Optional[datetime]) -> Optional[str]:
    """The DB-row style some endpoints return (no zone, UTC)."""
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# Conversations and their messages
# ---------------------------------------------------------------------------

def _m(role: str, content: str, **extra) -> Dict[str, Any]:
    msg = {"role": role, "content": content, "attachments": None, "is_trigger": False, "trigger_type": None}
    msg.update(extra)
    return msg


def _u(content: str, **extra) -> Dict[str, Any]:
    return _m("user", content, **extra)


def _a(content: str, **extra) -> Dict[str, Any]:
    return _m("assistant", content, **extra)


def _trace(reasoning, tools, memory_hits=None) -> Dict[str, Any]:
    """A stored turn's compact trace, shaped like services/graph/turn_trace.py:
    tools are (name, args_summary, status, duration_ms, result_preview)."""
    return {"reasoning": reasoning,
            "tools": [{"name": n, "args_summary": a, "status": st, "duration_ms": ms, "result_preview": r}
                      for n, a, st, ms, r in tools],
            "memory_hits": memory_hits}


def _trigger(content: str, kind: str) -> Dict[str, Any]:
    return _m("user", content, is_trigger=True, trigger_type=kind)


CABIN = [
    _u("Can you help me plan the cabin weekend at Pine Lake? Rowan and I are thinking the 17th–19th, dogs are coming."),
    _a(
        "Love it. Here's a first pass — tell me what to cut.\n\n"
        "**Friday the 17th**\n"
        "- Leave by 3:30 to beat highway traffic (it's ~1h 40m with a stop)\n"
        "- Check-in is 4pm; the cabin has a fenced yard, so Biscuit and Maple can run\n"
        "- Dinner: grill at the cabin — I can build a grocery list\n\n"
        "**Saturday the 18th**\n"
        "- Morning: the 7‑mile Shore Path (dogs allowed on leash)\n"
        "- Afternoon: kayak rental at the south dock, 1–4pm\n"
        "- Dinner: The Dockside has patio seating that allows dogs\n\n"
        "**Sunday the 19th**\n"
        "- Slow breakfast, check-out at 11\n\n"
        "One thing to sort: **Pepper and Mr. Whiskers** stay home — want me to ask Jordan about feeding them Saturday?"
    ),
    _u("Yes ask Jordan later. What will it cost roughly?"),
    _a(
        "Rough numbers, based on what you've told me before:\n\n"
        "| Item | Estimate | Notes |\n"
        "|---|---:|---|\n"
        "| Cabin (2 nights) | $412 | pet fee included |\n"
        "| Gas | $38 | 2 × 95 mi |\n"
        "| Groceries | $120 | Friday + Saturday breakfast |\n"
        "| Kayaks | $70 | tandem, 3 hours |\n"
        "| Saturday dinner | $95 | |\n"
        "| **Total** | **$735** | |\n\n"
        "That's about $70 under what you spent on the lake trip in June."
    , trace=_trace(
        "Ellis wants a rough total. The cabin quote with the pet fee is in the booking email; gas from the distance; "
        "groceries and dinner from what the June lake trip cost.",
        [("remember_search", '"June lake trip spending"', "ok", 212,
          "3 memories: June lake trip $805 total; groceries ~$110; dinner at the pizza place $88"),
         ("search_documents", '"Pine Lake cabin booking"', "ok", 164,
          "1 document: Cabin booking — Pine Lake (id 88): 2 nights, $412 incl. pet fee"),
         ("fetch_page_content", '"https://southdockrentals.example/prices"', "error", 45012,
          "Tool error: timed out after 45 s"),
         ("web_search", '"Pine Lake kayak rental tandem 3 hours price"', "ok", 1490,
          "1. South Dock Rentals — tandem $70 / 3 h  2. Pine Lake Canoe & Kayak — $25/h")],
        memory_hits=6)),
    _u("Can you split that between me and Rowan, 60/40, and show the math?"),
    _a(
        "Sure — here's the split with the working:\n\n"
        "```python\n"
        "total = 412 + 38 + 120 + 70 + 95\n"
        "owner, rowan = round(total * 0.6, 2), round(total * 0.4, 2)\n"
        "print(total, owner, rowan)\n"
        "```\n\n"
        "```\n735 441.0 294.0\n```\n\n"
        "- **You:** $441.00\n"
        "- **Rowan:** $294.00\n\n"
        "Want me to save this as a document so it's with the trip notes?"
    , trace=_trace(None, [("execute_code", '"total = 412 + 38 + 120 + 70 + 95"', "ok", 388, "735 441.0 294.0")],
                   memory_hits=2)),
    _u("Save it, and add a packing list for the dogs."),
    _a(
        "Saved as **Cabin weekend — Pine Lake** in your documents.\n\n"
        "Dog packing list:\n\n"
        "1. Biscuit's AM + PM meds (Carprofen, joint chew, fish oil) — *two days' worth plus one spare*\n"
        "2. Collapsible bowls ×2\n"
        "3. The long lead for the Shore Path\n"
        "4. Towels (the lake will happen)\n"
        "5. Maple's cooling vest if it's over 75°F\n\n"
        "> Heads up: Biscuit's 18:00 dose lands right when you'd be on the road Friday. "
        "I'll remind you at 5:45 so you can give it before you leave.\n\n"
        "[Cabin budget.csv](/api/files/f-cabin-csv/download)"
    , trace=_trace(
        "Save the budget, then the packing list. Biscuit's evening dose collides with Friday's drive — worth a reminder.",
        [("save_document", '"Cabin weekend — Pine Lake"', "ok", 96, "Saved document 212: Cabin weekend — Pine Lake"),
         ("execute_code", '"import csv"', "ok", 301, "wrote cabin-budget.csv (6 rows)"),
         ("save_to_storage", '"cabin-budget.csv"', "ok", 58, "Stored f-cabin-csv"),
         ("schedule_event", '"Remind Ellis to give Biscuit\'s 18:00 dose before leaving"', "ok", 41,
          "Scheduled for Fri 17:45 (event ev-biscuit-cabin)")],
        memory_hits=4)),
    _u("perfect thanks"),
    _a("Anytime. I'll check the forecast Thursday and tell you if the kayaks look like a bad idea."),
]

BISCUIT_VET = [
    _u("Here's the photo of Biscuit's bloodwork from this morning. Anything I should ask Dr. Patel?",
       attachments=[{"file_id": "f-biscuit-labs", "filename": "biscuit-labs.jpg", "mime_type": "image/jpeg", "size": 184220}]),
    _a(
        "I can read it. Three things stand out:\n\n"
        "- **ALT 142 U/L** (reference 10–125) — mildly high. Some pain and joint medicines can do that, and "
        "the \"joint chew\" on her list has no product name or strength. Worth confirming it with Dr. Patel.\n"
        "- **BUN 31** — top of normal. Probably fine; ask whether she wants a recheck with the next draw.\n"
        "- Everything else is in range.\n\n"
        "Questions to bring:\n"
        "1. Which product is the \"joint chew\", and at what strength?\n"
        "2. Should the ALT change the dose, or just the recheck schedule?\n"
        "3. Is the allergy tablet on her chart the right strength?"
    , trace=_trace(
        "Read the photo first. ALT is above range; the 'joint chew' on her list has no name or strength, and some "
        "joint medicines can raise ALT. Don't guess what it is — make it a question for the vet.",
        [("read_storage_file", '"biscuit-labs.jpg"', "ok", 2210,
          "Chemistry panel: ALT 142 U/L (10–125) H; BUN 31 mg/dL (7–31); CREA 1.1; GLU 96 …"),
         ("remember_search", '"Biscuit medications"', "ok", 140,
          "Biscuit takes an allergy tablet, Carprofen 100mg, a joint chew and fish oil at 06:00 and 18:00")],
        memory_hits=5)),
    _u("Can you remind me to call them tomorrow at 10?"),
    _a("Done — I'll remind you tomorrow at 10:00 to call the vet about Biscuit's bloodwork, with those three questions attached."),
]

GARAGE = [
    _u("Garage wall is 14 ft 8 in wide. Shelf brackets every 16 inches on studs. How many brackets for two shelves?"),
    _a(
        "Studs on 16\" centers across 176\" gives you **12 studs**, so:\n\n"
        "```python\n"
        "wall_in = 14 * 12 + 8          # 176\n"
        "studs = wall_in // 16 + 1      # 12\n"
        "brackets = studs * 2           # two shelves\n"
        "print(brackets)\n"
        "```\n\n"
        "**24 brackets.** If you skip the two end studs (the shelf overhangs them), it's 20. "
        "A 12-pack at the hardware store is about $32, so two packs either way."
    ),
]

BDAY = [
    _u("Rowan's birthday is on the 29th. Ideas for dinner that aren't the usual?"),
    _a(
        "Three that fit what she's liked before:\n\n"
        "1. **Olive & Ash** — a Mediterranean tasting menu; she loved the mezze place you tried in spring\n"
        "2. **Ember** — tiny and very hard to book; I can watch for a 7pm table\n"
        "3. **A pasta-night cooking class** — you make it, then you eat what you made\n\n"
        "Want me to check availability for the 29th?"
    ),
    _u("Watch Ember for a 7pm table for two"),
    _a("I'll check Ember's booking page twice a day and tell you the moment a 7pm (or 6:30–7:30) table for two opens on the 29th."),
]

VOICE = [
    _u("", attachments=[{"file_id": "f-memo-1", "filename": "voice-memo.m4a", "mime_type": "audio/mp4", "size": 48213}]),
    _u("What's on today? Anything with the dogs?"),
    _a("Two things: Biscuit's evening meds at six, and the vet call you asked me to remind you about at ten tomorrow. Nothing else on the calendar until your 4:30 with Kai."),
]

HALF = [
    _u("Build me a 10-week plan for the half. I can run 4 days a week."),
    _a(
        "Here's the shape — the detail is in your goal page.\n\n"
        "| Week | Long run | Quality | Easy |\n|---|---|---|---|\n"
        "| 1–2 | 6 mi | 4×800m | 2×3 mi |\n| 3–4 | 8 mi | tempo 3 mi | 2×4 mi |\n"
        "| 5–6 | 10 mi | 5×1k | 2×4 mi |\n| 7–8 | 11–12 mi | tempo 5 mi | 2×5 mi |\n"
        "| 9 | 8 mi | race-pace 4 mi | 2×3 mi |\n| 10 | race | shakeout | rest |\n\n"
        "Target pace for a sub-2:00 is **9:09/mi**."
    ),
]

SMS_DAD = [
    _u("Hey it's Dad — are you and Rowan still coming for dinner Sunday?"),
    _a("Hi! This is Edward, Ellis's assistant. I'll let Ellis know you asked — they'll get back to you today."),
]

THERMOSTAT = [
    _u("Why is the upstairs always 4 degrees warmer than downstairs?"),
    _a(
        "Mostly physics plus one fixable thing:\n\n"
        "- Warm air rises and your returns are all on the first floor\n"
        "- The upstairs supply dampers are probably fully open\n\n"
        "Try closing the upstairs dampers about halfway in summer and opening them in winter. "
        "If it's still >3°F, a return-air vent in the upstairs hallway is the real fix."
    ),
]

QUICK = [
    _u("What's the capital of Montana"),
    _a("Helena."),
]

BUDGET = [
    _u("How are we doing against the October budget?"),
    _a(
        "Through the 24th:\n\n"
        "- **Groceries** $612 of $700 — on pace to go ~$90 over\n"
        "- **Dining out** $188 of $300 — fine\n"
        "- **Pets** $431 of $250 — the vet visit; expected\n\n"
        "Net: about $140 over if nothing else changes."
    ),
]

MIND_PACK_MORNING = (
    "[WAKE: morning] thu 7:00am. Ellis is at phone:pwa (idle 3m). At the hub: Rowan (since 6:48).\n"
    "Since your last wake: 14 new messages, 2 calendar changes.\n\n"
    "## Who you are and what you are doing\n"
    "I'm Edward. I keep Ellis's days light: I watch their messages, calendar and the pet board, and I only "
    "interrupt when something can't wait until they next look at Today. This week I'm watching Biscuit's "
    "PM dose adherence, Parker's Saturday plans, and Ellis's half-marathon training.\n\n"
    "## Ellis's goals\n- Run the Springfield half in under 2 hours (active, last: 2026-09-21 ran 8 mi at 9:31)\n"
    "- Hold a 10-minute conversation in Spanish (active)\n\n"
    "## Today's calendar\n- 09:30 Standup (Zoom)\n- 16:30 1:1 with Kai\n- 18:00 Biscuit PM meds\n\n"
    "## Pet board\n- Biscuit: AM meds given 06:12 by Rowan\n- Pepper: AM bowl done\n\n"
    "## Last 30 messages\n"
    "[Family] Mom: Did everyone see the photos from the lake?\n"
    "[Family] Sam: So cute!! Biscuit looks huge\n"
    "[Parker] Parker: are we still on for saturday? I can drive\n"
    "[Jordan] Jordan: can you guys do dinner sat or sun?\n"
    "…\n\n"
    "## Waiting on you\n- #8812 Jordan (Jordan): can you guys do dinner sat or sun? — 06:41\n\n"
    "## Ellis's verdicts since your last wake\n- right call on your wake: messages: acted — replied to Parker\n\n"
    "## Open intentions (3)\n- Check whether Parker confirmed Saturday before noon\n- Watch Biscuit's PM dose adherence this week\n"
    "- Find a quiet dishwasher under $900 for Ellis\n"
)

MIND_PACK_MESSAGES = (
    "[WAKE: messages] thu 2:14pm. Ellis is at mac:desk (active). \n"
    "Since your last wake: 6 new messages.\n\n"
    "## Waiting on you\n"
    "- #8840 Family (Mom): Is Ellis bringing the dogs to Sunday dinner? — 14:02\n"
    "- #8841 Family (Sam): pls say yes — 14:03\n"
    "- #8842 Family (Mom): I'll make the big pot of chili — 14:05\n\n"
    "Thread (last 8 lines):\n"
    "  Mom: Did everyone see the photos from the lake?\n  Sam: So cute!! Biscuit looks huge\n"
    "  Mom: Is Ellis bringing the dogs to Sunday dinner?\n  Sam: pls say yes\n  Mom: I'll make the big pot of chili\n\n"
    "## Standing instructions from Ellis — they outrank everything below, a mention included\n"
    "- Don't answer in the Family group for me; tell me instead.\n\n"
    + "(pack continues: calendar, surfaces, notes, pushes in the last 12h, your did memories…)\n" * 6
)

PULSE_PACK = (
    "[EXPRESSION PULSE] thu 3:00pm. Since your last pulse: 3 new messages; Ellis is at mac:desk; "
    "the widget was changed by owner.\n\n"
    "Widget now: \"Biscuit PM meds 18:00 · Standup done · 4:30 Kai\"\n"
    "Household screen now: photo slideshow\n"
    "Mac screen now: your tiles (clock, calendar, alerts, heartbeat, memory ticker)\n"
    "Your hands in Chrome: idle, no working tab\n\n"
    "Your last notes:\n- 14:45 left alone: nothing changed that matters\n- 14:15 changed widget: added the 4:30 1:1\n"
    + "(signals continue: recent messages, presence, pet board, calendar…)\n" * 8
)

RESEARCH_PACK = (
    "[RESEARCH: dishwasher] wed 11:10pm. Intention: find a quiet dishwasher under $900 for Ellis — they said the "
    "current one is loud enough to hear from the office.\n\n"
    + "Constraints: 24-inch, ≤ 44 dBA, third rack, under $900, available in Springfield this month.\n" * 10
)

MIND = [
    _u(MIND_PACK_MORNING),
    _a("Left it alone — Parker already has an answer, Jordan's question can wait for Ellis's lunch look, and Biscuit's AM meds were given."),
    _u(MIND_PACK_MESSAGES),
    _a("Ellis asked me to stay out of the Family group, so I left a note on Today instead of answering Mom."),
]

SURFACES = [
    _u(PULSE_PACK),
    _a("Changed the widget: moved Biscuit's 18:00 meds to the top and dropped the finished standup."),
]

RESEARCH = [
    _u(RESEARCH_PACK),
    _a(
        "Findings saved as **Research: quiet dishwashers**.\n\n"
        "| Model | dBA | Price |\n|---|---:|---:|\n| Bosch 500 SHP65CM5N | 44 | $849 |\n"
        "| Miele G 5008 SCU | 45 | $899 |\n| KitchenAid KDTM404KPS | 44 | $879 |\n\n"
        "The Bosch is the pick: quietest, cheapest, and in stock at two stores near you."
    ),
]

ENTRY_PARKER = (
    "**Why I looked** — A mention: Parker asked in your thread whether Saturday is still on and offered to drive.\n\n"
    "**What I noticed** — Ellis told me Tuesday that Saturday works and they'd rather not drive after the game. "
    "Parker's question was directly to me (@edward), so answering was mine to do.\n\n"
    "**What I did**\n"
    "- Replied to Parker: \"Saturday's still on — Ellis says yes to you driving, pick-up around 5:15?\"\n"
    "- Also: remembered that Parker is driving Saturday; set a check for 11:30 Saturday to confirm the time\n\n"
    "**Why** — He asked me by name and the answer was already in what Ellis said. Leaving him waiting would "
    "have cost Ellis a text later."
)

ENTRY_NOTE = (
    "**Why I looked** — Evening look.\n\n"
    "**What I noticed** — Biscuit's 18:00 meds weren't logged by 18:40, and Ellis's calendar shows them driving back "
    "from the office until about 19:00.\n\n"
    "**What I did**\n"
    "- Left a note on Today: \"Biscuit's PM meds are 40 min late — Rowan is home if you want to ask her\"\n\n"
    "**Why** — Late but not missed, and Ellis was driving. A note they see when they park beats a buzz on the highway."
)

ENTRY_PUSH = (
    "**Why I looked** — New messages: the doorbell camera's package alert and a text from the neighbour.\n\n"
    "**What I noticed** — Rain in 20 minutes, and the package is the dog food order (35 lb bag, paper sack).\n\n"
    "**What I did**\n"
    "- Pushed you: \"Dog food is on the porch — rain in 20 min\"\n\n"
    "**Why** — A soaked 35 lb bag of food is a real cost, and 20 minutes is too soon for Today."
)

ENTRY_GOAL = (
    "**Why I looked** — A check-in I set: Sunday check-in on the half-marathon goal.\n\n"
    "**What I noticed** — Strava shows three runs this week, 19 miles total, long run 8 mi at 9:31/mi.\n\n"
    "**What I did**\n"
    "- Logged progress on Run the Springfield half in under 2 hours: \"Week 4: 19 mi, long run 8 mi @ 9:31\"\n"
    "- Also: moved next week's tempo run to Thursday because of your Wednesday dinner\n\n"
    "**Why** — The plan only works if the log is honest; Ellis asked me to keep it without asking them."
)

ENTRY_JORDAN = (
    "**Why I looked** — New messages: Jordan asked in their thread if you and Rowan can do dinner Saturday or Sunday.\n\n"
    "**What I did**\n"
    "- Drafted a reply to Jordan: \"Sunday works — 6pm at ours?\" (held for your approval)\n\n"
    "**Why** — Saturday is the cabin weekend and Sunday evening is open on both calendars."
)

PARKER_THREAD = [
    _trigger("[Heartbeat — @mention] Parker in \"Parker\": @edward is saturday still on? I can drive", "heartbeat"),
    _a("Replied to Parker: \"Saturday's still on — Ellis says yes to you driving, pick-up around 5:15?\""),
    _trigger("[Heartbeat — follow-up reply] Parker in \"Parker\": perfect see you then", "heartbeat"),
    _a("Nothing to add — noted that Parker is driving Saturday."),
]

FAMILY_THREAD = [
    _trigger("[Heartbeat event] 3 new messages in \"Family\" from Mom, Sam", "heartbeat"),
    _a("Ellis asked me to stay out of the Family group. Left a note on Today: Mom is asking whether the dogs are coming Sunday."),
]

JORDAN_THREAD = [
    _trigger("[Heartbeat event] Jordan in \"Jordan\": can you guys do dinner sat or sun?", "heartbeat"),
    _a("Drafted a reply for Ellis to approve: \"Sunday works — 6pm at ours?\""),
]

WORKER_FLIGHTS = [
    _u("Compare nonstop flights SPI → DEN for Thanksgiving week, 2 adults, back Sunday. Keep it under $450/person."),
    _a(
        "Best three, all nonstop:\n\n"
        "| Airline | Out (Wed) | Back (Sun) | Price/person |\n|---|---|---|---:|\n"
        "| United | 7:05am | 6:40pm | $389 |\n| Southwest | 9:20am | 4:55pm | $402 |\n| Frontier | 6:00am | 9:15pm | $241 |\n\n"
        "United is the pick — best times and it's refundable to credit."
    ),
]

SCHEDULED_PLANTS = [
    _trigger("[Scheduled Event] Remind Ellis to water the plants (fern + fiddle leaf)", "scheduled_event"),
    _a("Pushed Ellis: \"Water the fern and the fiddle leaf — it's been 6 days.\""),
]

OLD_THOUGHT = [
    _a("Rowan's flight moved from 3:55 to 4:40. Nothing to do yet — pickup is still Ellis's; I'll adjust the reminder."),
]


def _conv(cid: str, title: str, source: str, updated: datetime, messages: List[dict], *,
          channel: str = "text", notified: bool = False, created: Optional[datetime] = None,
          last_activity: Optional[datetime] = None, thought: Optional[dict] = None,
          chat_identifier: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": cid,
        "title": title,
        "source": source,
        "channel": channel,
        "notified_user": notified,
        "created_at": iso(created or (updated - timedelta(hours=1))),
        "updated_at": iso(updated),
        "last_activity_at": iso(last_activity) if last_activity else None,
        "message_count": len(messages),
        "thought": thought,
        "messages": messages,
        "chat_identifier": chat_identifier,
    }


def _thought(ledger_id: int, reason: str, acted: datetime, outward: bool, verdict: Optional[str]) -> dict:
    return {"kind": "mind_action", "ledger_id": ledger_id, "reason": reason, "acted_at": iso(acted),
            "outward": outward, "verdict": verdict}


def build_conversations() -> Dict[str, Dict[str, Any]]:
    convs = [
        # Ellis's own
        _conv("c-cabin-weekend", "Cabin weekend at Pine Lake", "user", ago(minutes=25), CABIN, created=ago(days=2)),
        _conv("c-biscuit-vet", "Biscuit's bloodwork results", "user", ago(hours=2), BISCUIT_VET),
        _conv("c-voice-morning", "Morning check-in", "user", ago(hours=3, minutes=10), VOICE, channel="voice"),
        _conv("c-sms-dad", "SMS: Dad", "external_message", ago(hours=5), SMS_DAD, notified=True),
        _conv("c-garage-shelves", "Garage shelving — how many brackets?", "user", ago(days=1, hours=2), GARAGE),
        _conv("c-rowan-bday", "Rowan's birthday dinner", "user", ago(days=1, hours=6), BDAY),
        _conv("c-half-marathon", "Half marathon training plan", "user", ago(days=2, hours=3), HALF),
        _conv("c-thermostat", "Why is the upstairs so warm?", "user", ago(days=3), THERMOSTAT),
        _conv("c-montana", "What's the capital of Montana", "user", ago(days=4), QUICK),
        _conv("c-budget", "October budget check", "user", ago(days=6), BUDGET),
        # Edward's long-lived threads
        _conv("edward-mind", "Edward's mind", "edward_thought", ago(minutes=40), MIND, created=ago(days=40)),
        _conv("edward-surfaces", "Edward's surfaces", "edward_thought", ago(minutes=12), SURFACES, created=ago(days=40)),
        _conv("edward-research", "Edward's research", "edward_thought", ago(hours=15), RESEARCH, created=ago(days=10)),
        # Journal entries: wakes that acted
        _conv("t-reply-parker", "Replied to Parker", "edward_thought", ago(hours=1, minutes=5),
              [_a(ENTRY_PARKER)], thought=_thought(9104, "mention", ago(hours=1, minutes=5), True, None)),
        _conv("t-jordan-held", "Drafted a reply to Jordan", "edward_thought", ago(minutes=50),
              [_a(ENTRY_JORDAN)], thought=_thought(9105, "messages", ago(minutes=50), True, None)),
        _conv("t-push-package", "Pushed you: dog food on the porch", "edward_thought", ago(hours=4), [_a(ENTRY_PUSH)],
              notified=True, thought=_thought(9103, "messages", ago(hours=4), True, "right_call")),
        _conv("t-biscuit-note", "Left a note: Biscuit's PM meds are late", "edward_thought", ago(hours=20),
              [_a(ENTRY_NOTE), _u("why a note and not a push?"),
               _a("You were driving until about 7, and a 40-minute-late dose isn't an emergency — a note you'd see when "
                  "you parked was enough. If it had gone past 10pm I would have pushed.")],
              thought=_thought(9102, "evening", ago(hours=20), False, None)),
        _conv("t-goal-log", "Logged progress on the half marathon", "edward_thought", ago(days=4), [_a(ENTRY_GOAL)],
              thought=_thought(9101, "self", ago(days=4), False, "wrong_call")),
        # Older thoughts
        _conv("th-flight-moved", "Rowan's flight moved to 4:40", "edward_thought", ago(days=2), OLD_THOUGHT),
        _conv("th-untitled", "Edward", "edward_thought", ago(days=3, hours=4),
              [_a("Quiet evening. Nothing in the pack needed Ellis.")]),
        # Heartbeat threads
        _conv("hb-parker", "iMessage: Parker", "heartbeat", ago(hours=1), PARKER_THREAD,
              last_activity=ago(hours=1), chat_identifier="+15555550142"),
        _conv("hb-family", "iMessage: Family", "heartbeat", ago(hours=2, minutes=30), FAMILY_THREAD,
              last_activity=ago(minutes=35), chat_identifier="chat874412"),
        _conv("hb-jordan", "iMessage: Jordan", "heartbeat", ago(minutes=52), JORDAN_THREAD, notified=True,
              last_activity=ago(minutes=55), chat_identifier="+15555550187"),
        # Worker and scheduled
        _conv("w-flight-research", "Worker: Thanksgiving flights to Denver", "orchestrator_worker",
              ago(hours=6), WORKER_FLIGHTS),
        _conv("s-plants", "Scheduled: water the plants", "scheduled_event", ago(hours=9), SCHEDULED_PLANTS),
    ]
    return {c["id"]: c for c in convs}


# ---------------------------------------------------------------------------
# Pets (hub board)
# ---------------------------------------------------------------------------

def _feeding(item_id: Optional[str], done: bool, by: Optional[str] = None, at_local: Optional[str] = None,
             marker: Optional[str] = None, bundled: Optional[List[str]] = None) -> dict:
    return {"item_id": item_id, "done": done, "by": by, "at_local": at_local, "overdue": False,
            "marker": marker, "bundled_item_ids": bundled or []}


def _med(mid: str, name: str, dose: str, window: str, at_time: str, status: str, *, by=None, at_local=None,
         review=False, late=None) -> dict:
    return {"id": mid, "name": name, "dose": dose, "kind": "med", "window": window, "at_time": at_time,
            "status": status, "by": by, "at_local": at_local, "needs_review": review, "minutes_late": late}


def build_board() -> Dict[str, Any]:
    """A fixed, readable evening: AM all given, PM meds overdue for Biscuit."""
    pets = [
        {"id": "biscuit", "name": "Biscuit", "species": "dog", "photo_url": None,
         "feedings": {"am": _feeding("biscuit-food-am", True, "Rowan", "6:10a"),
                      "pm": _feeding("biscuit-food-pm", False)},
         "meds": [
             _med("biscuit-allergy-am", "Allergy tablet", "1 tablet", "am", "06:00", "given", by="Rowan", at_local="6:12a", review=True),
             _med("biscuit-carprofen-am", "Carprofen", "100mg", "am", "06:00", "given", by="Rowan", at_local="6:12a"),
             _med("biscuit-joint-am", "Joint chew", "1 chew", "am", "06:00", "given", by="Rowan", at_local="6:12a", review=True),
             _med("biscuit-fishoil-am", "Fish oil", "1 capsule", "am", "06:00", "given", by="Rowan", at_local="6:13a", review=True),
             _med("biscuit-carprofen-pm", "Carprofen", "100mg", "pm", "18:00", "overdue", late=42),
             _med("biscuit-joint-pm", "Joint chew", "1 chew", "pm", "18:00", "overdue", review=True, late=42),
             _med("biscuit-fishoil-pm", "Fish oil", "1 capsule", "pm", "18:00", "refused", by="Ellis", at_local="6:05p", review=True),
         ]},
        {"id": "maple", "name": "Maple", "species": "dog", "photo_url": None,
         "feedings": {"am": _feeding("maple-food-am", True, "Rowan", "6:10a"),
                      "pm": _feeding("maple-food-pm", True, "Ellis", "5:58p")},
         "meds": []},
        {"id": "pepper", "name": "Pepper", "species": "cat", "photo_url": None,
         "feedings": {"am": _feeding("pepper-food-am", True, "Rowan", "6:20a"),
                      "pm": _feeding("pepper-food-pm", False, marker="+P", bundled=["pepper-probiotic"])},
         "meds": []},
        {"id": "whiskers", "name": "Mr. Whiskers", "species": "cat", "photo_url": None,
         "feedings": {"am": _feeding("whiskers-food-am", True, "Rowan", "6:20a"),
                      "pm": _feeding("whiskers-food-pm", False)},
         "meds": []},
    ]
    alerts = [{"pet": "Biscuit", "item": "Carprofen", "minutes_late": 42},
              {"pet": "Biscuit", "item": "Joint chew", "minutes_late": 42}]
    return {"care_day": START.astimezone(UTC).date().isoformat(), "window": "pm", "pets": pets, "alerts": alerts}


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _hb(eid: str, source: str, status: str, when: datetime, *, sender=None, contact=None, chat=None, chat_name=None,
        summary=None, raw=None, from_user=False, event_type="message", briefed=False, conv=None,
        notified_at=None, batch=None) -> dict:
    return {"id": eid, "source": source, "event_type": event_type, "sender": sender, "contact_name": contact,
            "chat_identifier": chat, "chat_name": chat_name, "summary": summary, "raw_data": raw,
            "is_from_user": from_user, "created_at": iso(when), "triage_status": status, "briefed": briefed,
            # only for Today's composer
            "conversation_id": conv, "notified_at": notified_at, "notification_batch_id": batch}


def build_heartbeat_events() -> List[dict]:
    J = "+15555550187"
    C = "+15555550142"
    F = "chat874412"
    ev = [
        _hb("hb-8845", "imessage", "escalated", ago(minutes=55), sender=J, contact="Jordan", chat=J,
            summary="can you guys do dinner sat or sun?", raw={"text": "can you guys do dinner sat or sun?"}, conv="hb-jordan",
            notified_at=iso(ago(minutes=50)), batch="mind:9105"),
        _hb("hb-8842", "imessage", "escalated", ago(minutes=35), sender="+15555550111", contact="Mom", chat=F, chat_name="Family",
            summary="I'll make the big pot of chili", raw={"text": "I'll make the big pot of chili"}, conv="hb-family"),
        _hb("hb-8841", "imessage", "escalated", ago(minutes=37), sender="+15555550112", contact="Sam", chat=F, chat_name="Family",
            summary="pls say yes", raw={"text": "pls say yes"}, conv="hb-family"),
        _hb("hb-8840", "imessage", "escalated", ago(minutes=38), sender="+15555550111", contact="Mom", chat=F, chat_name="Family",
            summary="Is Ellis bringing the dogs to Sunday dinner?", raw={"text": "Is Ellis bringing the dogs to Sunday dinner?"}, conv="hb-family"),
        _hb("hb-8839", "imessage", "mention", ago(hours=1, minutes=6), sender=C, contact="Parker", chat=C,
            summary="@edward is saturday still on? I can drive", raw={"text": "@edward is saturday still on? I can drive"},
            conv="hb-parker", notified_at=iso(ago(hours=1, minutes=5)), batch="mind:9104"),
        _hb("hb-8838", "imessage", "handled_by_owner", ago(hours=1, minutes=40), sender="+15555550163", contact="Kai",
            chat="+15555550163", summary="Can we push 4:30 to 4:45?", raw={"text": "Can we push 4:30 to 4:45?"}),
        _hb("hb-8837", "imessage", "handled_by_owner", ago(hours=1, minutes=38), sender="me", contact=None,
            chat="+15555550163", summary="yep 4:45 works", raw={"text": "yep 4:45 works"}, from_user=True),
        _hb("hb-8846", "calendar", "escalated", ago(minutes=3), event_type="calendar_event",
            summary=f"[STARTING SOON] Call with the contractor — starts at {iso(ahead(minutes=12))}",
            raw={"title": "Call with the contractor", "start_date": iso(ahead(minutes=12)), "location": "Phone"}),
        _hb("hb-8836", "calendar", "calendar_urgent", ago(hours=2), event_type="calendar_event",
            summary="1:1 with Kai starts in 25 minutes",
            raw={"title": "1:1 with Kai", "start_date": iso(ago(hours=1, minutes=35)), "location": "Zoom"},
            notified_at=iso(ago(hours=2)), batch="mind:9099"),
        # The mind path escalates mail too. The Mail listener's real shape: a
        # bare address, no contact, no thread, summary "From <sender>: <subject>".
        _hb("hb-8847", "email", "escalated", ago(minutes=20), sender="frontdesk@patel-vet.example.com",
            event_type="email_received",
            summary="From frontdesk@patel-vet.example.com: Can Biscuit come in Thursday at 9 for a recheck?",
            raw={"subject": "Can Biscuit come in Thursday at 9 for a recheck?", "sender": "frontdesk@patel-vet.example.com"}),
        _hb("hb-8835", "email", "noted", ago(hours=3), sender="orders@example.com", contact="Pet store",
            summary="Your order has shipped — arriving today",
            raw={"subject": "Your order has shipped", "body": "35 lb bag of dog food — arriving today by 8pm."}),
        _hb("hb-8834", "imessage", "watching", ago(hours=3, minutes=20), sender="+15555550199", contact="Alex (landlord)",
            chat="+15555550199", summary="Plumber might come Thursday, will confirm",
            raw={"text": "Plumber might come Thursday, will confirm"}),
        _hb("hb-8833", "imessage", "follow_up", ago(hours=5), sender="+15555550121", contact="Dad", chat="+15555550121",
            summary="are you and Rowan still coming for dinner Sunday?",
            raw={"text": "are you and Rowan still coming for dinner Sunday?"}),
        _hb("hb-8832", "imessage", "acted", ago(hours=4), sender="+15555550177", contact="Neighbor (Dana)",
            chat="+15555550177", summary="there's a big box on your porch fyi", raw={"text": "there's a big box on your porch fyi"},
            notified_at=iso(ago(hours=4)), batch="mind:9103"),
        _hb("hb-8831", "imessage", "noted", ago(hours=6), sender=C, contact="Parker", chat=C,
            summary="found the tickets, see you there", raw={"text": "found the tickets, see you there"}),
        _hb("hb-8830", "imessage", "pending", ago(minutes=2), sender=F, contact="Sam", chat=F, chat_name="Family",
            summary="also can someone bring ice", raw={"text": "also can someone bring ice"}),
    ]
    dismissed_people = [("Mom", F, "Family"), ("Sam", F, "Family"), ("Parker", C, None), ("Taylor", "+15555550155", None),
                        ("Pharmacy", "72727", None), ("Taylor", "+15555550155", None), ("Mom", F, "Family"),
                        ("Uncle Rob", F, "Family"), ("Taylor", "+15555550155", None), ("Mom", F, "Family")]
    texts = ["haha", "👍", "lol yes", "ok sounds good", "Your prescription is ready for pickup", "same",
             "Loved an image", "Laughed at \"so cute\"", "k", "❤️"]
    for i, ((who, chat, chat_name), text) in enumerate(zip(dismissed_people, texts)):
        ev.append(_hb(f"hb-{8800 + i}", "imessage", "dismissed", ago(hours=7 + i, minutes=7 * i), sender=chat, contact=who,
                      chat=chat, chat_name=chat_name, summary=text, raw={"text": text}))
    ev.sort(key=lambda e: e["created_at"], reverse=True)
    return ev


def build_heartbeat_status() -> Dict[str, Any]:
    return {
        "running": True, "enabled": True, "triage_interval_seconds": 120, "pending_count": 1,
        "last_triage_at": iso(ago(minutes=3)), "next_triage_at": iso(ahead(minutes=1)),
        "listener_status": "running",
        "allowed_senders": [{"identifier": "+15555550142", "label": "Parker"},
                            {"identifier": "chat874412", "label": "Family"}],
        "tracks": {
            "imessage": {"enabled": True, "status": "running", "poll_seconds": 10},
            "calendar": {"enabled": True, "status": "running", "poll_seconds": 300, "lookahead_minutes": 30},
            "email": {"enabled": True, "status": "running", "poll_seconds": 600},
            "gmail": {"enabled": False, "status": "disabled", "poll_seconds": 300},
        },
        "imessage_enabled": True, "imessage_poll_seconds": 10, "imessage_thread_limit_direct": 20,
        "imessage_thread_limit_group": 30, "calendar_enabled": True, "calendar_poll_seconds": 300,
        "calendar_lookahead_minutes": 30, "email_enabled": True, "email_poll_seconds": 600,
        "gmail_enabled": False, "gmail_poll_seconds": 300, "digest_token_cap": 4000, "mind_enabled": True,
    }


def build_triage_cycles() -> List[dict]:
    out = []
    for i in range(12):
        total = [3, 0, 6, 2, 1, 9, 0, 4, 2, 5, 1, 3][i]
        out.append({
            "id": f"tc-{500 - i}", "cycle_number": 500 - i, "events_total": total,
            "events_rule_filtered": max(0, total - 2), "events_dismissed": max(0, total - 3),
            "events_noted": 1 if total > 2 else 0, "events_acted": 1 if i in (2, 5) else 0,
            "events_escalated": 1 if i in (0, 5) else 0, "layer_reached": 3 if total > 4 else (2 if total else 1),
            "haiku_input_tokens": total * 820, "haiku_output_tokens": total * 60, "sonnet_wakes": 1 if i in (0, 5) else 0,
            "duration_ms": 900 + total * 410, "summary": None if not total else f"{total} messages; mind woke" if i in (0, 5) else f"{total} messages",
            "created_at": iso(ago(minutes=3 + i * 2)),
        })
    return out


# ---------------------------------------------------------------------------
# The mind
# ---------------------------------------------------------------------------

def _wake(wid: int, when: datetime, source: str, outcome: str, note: str, *, tools=None, surfaces=None,
          delivered=False, pushes=None, verdict=None, thought_id=None) -> dict:
    row = {"id": wid, "at": iso(when), "source": source, "outcome": outcome, "note": note,
           "tools": tools or [], "surfaces": surfaces or [], "delivered": delivered,
           "turn_id": f"turn-{wid}", "pushes": pushes or [], "verdict": verdict}
    if thought_id:
        row["thought_id"] = thought_id
    return row


def build_mind_log() -> List[dict]:
    w = [
        _wake(9105, ago(minutes=50), "messages", "acted", "Jordan asked about dinner; drafted a reply and held it for Ellis.",
              tools=["read_messages", "send_imessage"], thought_id="t-jordan-held"),
        _wake(9106, ago(minutes=40), "surfaces", "left_alone", "Nothing changed that matters."),
        _wake(9104, ago(hours=1, minutes=5), "mention", "acted", "Parker asked me directly; the answer was in what Ellis said Tuesday.",
              tools=["send_imessage", "remember", "wake_me"], delivered=True, thought_id="t-reply-parker"),
        _wake(9100, ago(hours=2, minutes=40), "messages", "left_alone", "Kai's reschedule — Ellis already answered."),
        _wake(9103, ago(hours=4), "messages", "acted", "Package on the porch with rain coming.",
              tools=["send_push_notification"], delivered=True,
              pushes=[{"id": "p-771", "title": "Dog food is on the porch — rain in 20 min", "opened": True, "held": None}],
              verdict={"verdict": "right_call", "note": None}, thought_id="t-push-package"),
        _wake(9099, ago(hours=7), "morning", "left_alone", "Calendar is light; Biscuit's AM meds were given."),
        _wake(9098, ago(hours=7, minutes=30), "reflect_lite", "acted", "Closed two stale intentions; rewrote my state page.",
              tools=["edit_document", "remember"]),
        _wake(9097, ago(hours=10), "research", "acted", "Dishwasher research done; saved the findings.",
              tools=["web_search", "fetch_page_content", "save_document"]),
        _wake(9096, ago(hours=13), "messages", "failed", "Pack source timed out (calendar).", tools=[]),
        _wake(9102, ago(hours=20), "evening", "acted", "Biscuit's PM meds 40 min late while Ellis was driving: left a note.",
              tools=["leave_note"], surfaces=["today"], thought_id="t-biscuit-note"),
        _wake(9095, ago(hours=21), "messages", "left_alone", "Family chatter."),
        _wake(9094, ago(hours=22), "messages", "left_alone", "Two reactions and a 'haha'."),
        _wake(9093, ago(hours=23), "messages", "left_alone", "Nothing addressed to Ellis."),
        _wake(9092, ago(days=1, hours=6), "morning", "acted", "Pushed the leave-by time for the dentist because of a highway closure.",
              tools=["send_push_notification"], delivered=True,
              pushes=[{"id": "p-760", "title": "Leave by 8:10 for the dentist — highway lane closure", "opened": False, "held": None}]),
        _wake(9091, ago(days=1, hours=9), "manual", "left_alone", "Ellis asked me to look; nothing waiting."),
        _wake(9090, ago(days=2, hours=2), "prep", "acted", "Left a Before note for the 1:1 with Kai.",
              tools=["leave_note", "search_documents"]),
        _wake(9089, ago(days=2, hours=12), "messages", "skipped", "quota exhausted"),
        _wake(9101, ago(days=4), "self", "acted", "Sunday goal check-in: logged week 4.",
              tools=["update_goal"], verdict={"verdict": "wrong_call", "note": "Ask me before moving runs around"},
              thought_id="t-goal-log"),
        _wake(9088, ago(days=4, hours=1), "reflect", "acted", "Weekly reflection: learned Ellis prefers notes over pushes after 9pm.",
              tools=["remember", "edit_document", "leave_note"]),
        _wake(9087, ago(days=5), "evening", "left_alone", "Quiet."),
        _wake(9086, ago(days=6), "mention", "acted", "Answered Parker about the tickets.",
              tools=["send_imessage"], delivered=True, verdict={"verdict": "right_call", "note": "nice"}),
    ]
    w.sort(key=lambda r: r["at"], reverse=True)
    return w


def build_intentions() -> List[dict]:
    return [
        {"id": "mem-int-1", "content": "Check whether Parker confirmed the Saturday pick-up time before noon Saturday.",
         "created_at": iso(ago(hours=1, minutes=5)), "valid_until": iso(ahead(days=2)), "event_id": "ev-parker-check",
         "next_fire_at": iso(ahead(days=1, hours=14)), "next_fire_why": "Confirm Parker's pick-up time for Saturday"},
        {"id": "mem-int-2", "content": "Watch Biscuit's PM dose adherence this week — two late evenings already.",
         "created_at": iso(ago(days=2)), "valid_until": iso(ahead(days=5)), "event_id": None,
         "next_fire_at": None, "next_fire_why": None},
        {"id": "mem-int-3", "content": "Tell Ellis when Ember has a 7pm table for two on the 29th.",
         "created_at": iso(ago(days=1, hours=6)), "valid_until": iso(ahead(days=4)), "event_id": "ev-table-watch",
         "next_fire_at": iso(ahead(hours=5)), "next_fire_why": "Check Ember availability for the 29th"},
        {"id": "mem-int-4", "content": "Before the cabin weekend, check the Pine Lake forecast Thursday and warn about kayaks if windy.",
         "created_at": iso(ago(minutes=25)), "valid_until": iso(ahead(days=9)), "event_id": "ev-forecast",
         "next_fire_at": iso(ahead(days=6)), "next_fire_why": "Forecast check for the cabin weekend"},
    ]


# ---------------------------------------------------------------------------
# Goals and ideas
# ---------------------------------------------------------------------------

def _goal_content(why: str, plan: str, progress: List[str]) -> str:
    body = f"## Why\n{why}\n\n## Plan\n{plan}\n\n## Progress\n" + "\n".join(f"- {p}" for p in progress) + "\n"
    return body


# The retired goals_service's "Goal set (…)" line (goals left Today on
# 2026-10-11; the old panels still read these fields).
_SET_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):\s*Goal set \(([^)]*)\)")


def goal_counts(progress: List[str]) -> dict:
    """What goals_service.goal_summary derived from the log (2026-09-26): the
    "Goal set (…)" line is meta (set_on / set_by), never a check-in."""
    logged = [p for p in progress if not _SET_LINE_RE.match(p)]
    m = next((_SET_LINE_RE.match(p) for p in progress if _SET_LINE_RE.match(p)), None)
    return {"last_progress": logged[-1] if logged else None, "progress_count": len(logged),
            "set_on": m.group(1) if m else None, "set_by": m.group(2) if m else None, "progress": logged}


def _goal(gid: str, title: str, status: str, why: str, plan: str, progress: List[str], check_in: Optional[str],
          next_check: Optional[datetime], created: datetime, updated: datetime) -> dict:
    return {
        "id": gid, "title": title, "status": status, "why": why[:280], "plan": plan,
        **goal_counts(progress),
        "next_check_in": iso(next_check), "check_in": check_in,
        "check_in_event_id": f"ev-goal-{gid}" if next_check else None,
        "updated_at": iso(updated), "href": f"/today/goals/{gid}",
        "content": _goal_content(why, plan, progress), "log": list(progress), "created_at": iso(created),
    }


def build_goals() -> Dict[str, dict]:
    d = lambda n: (START - timedelta(days=n)).date().isoformat()  # noqa: E731
    goals = [
        _goal("g-half-marathon", "Run the Springfield half in under 2 hours", "active",
              "I ran 2:14 last year and felt terrible after mile 9. I want to finish strong, not just finish.",
              "Four runs a week for ten weeks: one long run, one quality session, two easy. "
              "Target race pace 9:09/mi. Edward logs the week every Sunday from Strava.",
              [f"{d(24)}: Goal set (by Ellis, on Today); check-in weekly.", f"{d(17)}: Week 2 — 14 mi, long run 6 mi @ 9:48",
               f"{d(10)}: Week 3 — 17 mi, tempo felt good", f"{d(4)}: Week 4 — 19 mi, long run 8 mi @ 9:31"],
              "weekly (Sundays 19:00)", next_local(6, 19), ago(days=24), ago(days=4)),
        _goal("g-spanish", "Hold a 10-minute conversation in Spanish", "active",
              "Rowan's family speaks Spanish at the holidays and I want to keep up.",
              "15 minutes of Duolingo daily, one iTalki lesson a week, Edward quizzes me on Fridays.",
              [f"{d(12)}: Goal set (in chat with Ellis); check-in Fridays.", f"{d(5)}: First iTalki lesson — survived 6 minutes"],
              "Fridays 19:00", next_local(4, 19), ago(days=12), ago(days=5)),
        _goal("g-garage", "Finish the garage shelves", "paused",
              "Half the garage is boxes and the car hasn't been inside since June.",
              "Buy brackets and boards, mount two shelves on the north wall, sort the boxes into keep / donate.",
              [f"{d(20)}: Goal set (by Ellis, on Today); check-in weekly."],
              None, None, ago(days=20), ago(days=1)),
        _goal("g-cabin-book", "Book the fall cabin weekend", "done",
              "We haven't had a weekend away since June.", "Pick a date, book somewhere dog-friendly.",
              [f"{d(9)}: Goal set (by Edward); check-in weekly.", f"{d(2)}: Booked Pine Lake, 17th–19th"],
              None, None, ago(days=9), ago(days=2)),
    ]
    return {g["id"]: g for g in goals}


def build_ideas() -> List[dict]:
    return [
        {"id": "idea-sitter-sheet", "title": "Turn Biscuit's med list into a sitter sheet",
         "why": "You rewrite the sitter instructions by hand before every trip; the cabin weekend is in 3 weeks.",
         "prompt": "Make a one-page pet sitter sheet from the pet board and Biscuit's med protocol, printable.",
         "href": "/chat?prompt=Make%20a%20one-page%20pet%20sitter%20sheet", "created_at": iso(ago(hours=3)),
         "expires_at": iso(ahead(days=6))},
        {"id": "idea-run-weather", "title": "Weather-aware long-run reminders",
         "why": "Two of your last four long runs got moved for rain. I can pick the dry window each Saturday.",
         "prompt": "Every Friday evening, check Saturday's hourly forecast and suggest the best 2-hour window for my long run.",
         "href": "/chat?prompt=Every%20Friday%20evening%2C%20check%20Saturday", "created_at": iso(ago(hours=9)),
         "expires_at": iso(ahead(days=6))},
        {"id": "idea-grocery-diff", "title": "A grocery list that knows what's already in the fridge",
         "why": "You bought cilantro three times this month.",
         "prompt": "Help me set up a running pantry list you can diff my grocery list against.",
         "href": "/chat?prompt=Help%20me%20set%20up%20a%20running%20pantry%20list", "created_at": iso(ago(days=1, hours=2)),
         "expires_at": iso(ahead(days=5))},
    ]


# ---------------------------------------------------------------------------
# Memories, documents, events, files, databases
# ---------------------------------------------------------------------------

def _mem(mid: str, content: str, mtype: str, importance: float, *, tier="observation", origin="heard", about="owner",
         temporal="permanent", created: Optional[datetime] = None, reinf=1, access=0, conv=None) -> dict:
    created = created or ago(days=10)
    return {"id": mid, "content": content, "memory_type": mtype, "importance": importance,
            "temporal_nature": temporal, "tier": tier, "reinforcement_count": reinf,
            "source_conversation_id": conv, "created_at": iso(created), "updated_at": iso(created),
            "last_accessed": iso(ago(hours=3)) if access else None, "access_count": access, "user_id": None,
            "origin": origin, "about": about, "score": None}


def build_memories() -> List[dict]:
    return [
        _mem("m-01", "Ellis's partner is Rowan; they live together in Springfield with two dogs and two cats.", "fact", 0.95, tier="knowledge", reinf=14, access=42),
        _mem("m-02", "Biscuit (dog) takes meds at 06:00 and 18:00 sharp: an allergy tablet, Carprofen 100mg, a joint chew, fish oil.", "fact", 0.95, tier="knowledge", reinf=9, access=31, conv="c-biscuit-vet"),
        _mem("m-03", "The \"joint chew\" on Biscuit's med list has no product or strength — unconfirmed, never guess it.", "fact", 0.8, tier="belief", origin="thought", about="contact", reinf=3, conv="c-biscuit-vet"),
        _mem("m-04", "Pepper gets a probiotic powder in her PM bowl.", "fact", 0.7, tier="knowledge", reinf=6, access=8),
        _mem("m-05", "Ellis prefers a note over a push after 9pm unless it truly can't wait until morning.", "preference", 0.9, tier="belief", origin="thought", about="owner", reinf=4, access=12),
        _mem("m-06", "Don't answer in the Family group for Ellis; tell them instead.", "instruction", 0.95, tier="knowledge", reinf=2, access=18),
        _mem("m-07", "Parker is driving Saturday; pick-up around 5:15.", "context", 0.6, temporal="temporary", origin="read", about="contact", created=ago(hours=1), conv="hb-parker"),
        _mem("m-08", "Ellis is training for the Springfield half; target sub-2:00 (9:09/mi).", "fact", 0.8, tier="belief", reinf=5, access=7, conv="c-half-marathon"),
        _mem("m-09", "Rowan's birthday is on the 29th; she loved the mezze place they tried in spring.", "fact", 0.85, tier="knowledge", reinf=3, access=4, conv="c-rowan-bday"),
        _mem("m-10", "Ellis dislikes driving after evening games.", "preference", 0.6, tier="observation", origin="heard"),
        _mem("m-11", "Jordan asked about dinner Saturday or Sunday; Ellis's calendar has Sunday evening free.", "context", 0.5, temporal="temporary", origin="read", about="contact", created=ago(minutes=50)),
        _mem("m-12", "Watch Biscuit's PM dose adherence this week — two late evenings already.", "intention", 0.7, origin="thought", about="self", temporal="evolving", created=ago(days=2)),
        _mem("m-13", "Replied to Parker about Saturday; he confirmed. Outcome: good.", "context", 0.4, origin="did", about="self", temporal="temporary", created=ago(hours=1)),
        _mem("m-14", "The upstairs runs ~4°F warmer; suggested half-closing the upstairs dampers in summer.", "context", 0.4, conv="c-thermostat"),
        _mem("m-15", "Ellis's dad texts from +1 555-0121 and prefers calls on Sundays.", "fact", 0.5, about="contact", origin="read"),
        _mem("m-16", "The Bosch 500 (44 dBA, $849) is the best quiet dishwasher under $900.", "fact", 0.5, origin="thought", about="world", created=ago(hours=10), conv="edward-research"),
        _mem("m-17", "Ellis uses the iPhone PWA mostly, in dark mode.", "preference", 0.6, tier="belief", reinf=3),
        _mem("m-18", "Maple might have no meds at all — unconfirmed.", "fact", 0.4, about="contact"),
        _mem("m-19", "October grocery budget is $700; dining out $300; pets $250.", "fact", 0.6, conv="c-budget"),
        _mem("m-20", "Ellis likes Edward's thinking visible — do not hide tool use behind a debug flag.", "instruction", 0.9, tier="knowledge", reinf=5, access=20),
        _mem("m-21", "Kai is Ellis's manager; 1:1 on Thursdays at 4:30.", "fact", 0.7, tier="belief", about="contact", reinf=6),
        _mem("m-22", "Ember is hard to book; Ellis wants a 7pm table on the 29th.", "context", 0.5, temporal="temporary", created=ago(days=1, hours=6)),
    ]


def memory_stats(memories: List[dict]) -> dict:
    by_type: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    for m in memories:
        by_type[m["memory_type"]] = by_type.get(m["memory_type"], 0) + 1
        by_tier[m["tier"]] = by_tier.get(m["tier"], 0) + 1
    total = len(memories)
    return {"total": 4821 + total, "live": 4410 + total, "unused_live": 1902, "created_last_24h": 37,
            "by_type": {k: v * 180 for k, v in by_type.items()}, "by_tier": {k: v * 200 for k, v in by_tier.items()},
            "average_importance": round(sum(m["importance"] for m in memories) / max(1, total), 3)}


def _doc(did: str, title: str, content: str, tags: str, *, created: datetime, updated: Optional[datetime] = None,
         conv=None, access=0) -> dict:
    return {"id": did, "title": title, "content": content, "tags": tags, "source_conversation_id": conv,
            "created_at": iso(created), "updated_at": iso(updated or created),
            "last_accessed": iso(ago(hours=5)) if access else None, "access_count": access, "user_id": None, "score": None}


def build_documents(goals: Dict[str, dict]) -> List[dict]:
    docs = [
        _doc("d-state", "Edward: state",
             "I'm Edward. I keep Ellis's days light.\n\n## What I'm watching\n- Biscuit's PM doses (two late this week)\n"
             "- Parker's Saturday plans\n- Ellis's half-marathon training\n\n## What I've learned about my judgment\n"
             "- Ellis prefers notes after 9pm.\n- Stay out of the Family group.\n", "state,self",
             created=ago(days=30), updated=ago(hours=7, minutes=30), access=140),
        _doc("d-cabin", "Cabin weekend — Pine Lake",
             "# Cabin weekend\n\nFri 17th – Sun 19th. Dogs coming.\n\n| Item | $ |\n|---|---:|\n| Cabin | 412 |\n| Total | 735 |\n\n"
             "Split 60/40: Ellis $441, Rowan $294.\n", "trip,plans", created=ago(minutes=30), conv="c-cabin-weekend", access=2),
        _doc("d-biscuit-protocol", "Biscuit — med protocol",
             "06:00 and 18:00 sharp.\n\n- Allergy tablet, 1 (strength unconfirmed)\n- Carprofen 100mg\n- Joint chew, 1 (product unconfirmed)\n"
             "- Fish oil, 1 capsule (strength unconfirmed)\n", "pets,biscuit,meds", created=ago(days=34), updated=ago(days=3), access=22),
        _doc("d-sitter", "Pet sitter notes", "Feeding is twice daily, whenever. Only Biscuit's meds are timed.\n\nVet: Dr. Patel, (555) 010-4477.\n",
             "pets", created=ago(days=60), access=5),
        _doc("d-dossier-parker", "Dossier: Parker", "College friend. Drives to games. Replies fast; prefers texts.",
             "dossier,contact", created=ago(days=6), access=3),
        _doc("d-research-dishwasher", "Research: quiet dishwashers", "Bosch 500 SHP65CM5N — 44 dBA — $849 (pick)\nMiele G 5008 — 45 dBA — $899\n",
             "research", created=ago(hours=10), conv="edward-research"),
        _doc("d-conv-budget", "Conversation: October budget check", "Groceries on pace to go ~$90 over; pets over because of the vet.",
             "summary", created=ago(days=5)),
    ]
    for g in goals.values():
        docs.append(_doc(g["id"], f"Goal: {g['title']}", g["content"], f"goal,{g['status']}",
                         created=datetime.fromisoformat(g["created_at"].replace("Z", "+00:00")),
                         updated=datetime.fromisoformat(g["updated_at"].replace("Z", "+00:00"))))
    return docs


def _ev(eid: str, desc: str, next_fire: datetime, *, status="pending", recurrence=None, created_by="edward",
        conv=None, channel=None, fire_count=0, last_fired=None, last_result=None, scheduled=None) -> dict:
    return {"id": eid, "conversation_id": conv, "description": desc, "scheduled_at": iso(scheduled or next_fire),
            "next_fire_at": iso(next_fire), "recurrence_pattern": recurrence, "status": status,
            "created_by": created_by, "delivery_channel": channel, "last_fired_at": iso(last_fired),
            "fire_count": fire_count, "last_result": last_result, "created_at": iso(ago(days=3)),
            "updated_at": iso(last_fired or ago(days=1))}


def build_events() -> List[dict]:
    return [
        _ev("ev-vet-call", "Remind Ellis to call the vet about Biscuit's bloodwork (three questions attached)", ahead(hours=3),
            created_by="user", conv="c-biscuit-vet", channel="push"),
        _ev("ev-rowan-flight", "Rowan's flight lands (UA 1442) — pickup at door 3", ahead(hours=7), conv="th-flight-moved"),
        _ev("ev-table-watch", "Check Ember availability for the 29th", ahead(hours=5), recurrence="0 9,17 * * *", conv="c-rowan-bday",
            fire_count=3, last_fired=ago(hours=19), last_result="No 7pm table yet."),
        _ev("ev-parker-check", "Confirm Parker's pick-up time for Saturday", ahead(days=1, hours=14), conv="t-reply-parker"),
        _ev("ev-forecast", "Forecast check for the cabin weekend", ahead(days=6), conv="c-cabin-weekend"),
        _ev("ev-tick-morning", "Morning wake", ahead(hours=11), recurrence="0 7 * * *", conv="edward-mind", fire_count=41,
            last_fired=ago(hours=7), last_result="left alone"),
        _ev("ev-tick-evening", "Evening wake", ahead(hours=22), recurrence="0 18 * * *", conv="edward-mind", fire_count=40,
            last_fired=ago(hours=2), last_result="acted: left a note"),
        _ev("ev-pulse", "Surfaces pulse", ahead(minutes=8), recurrence="*/15 * * * *", conv="edward-surfaces", fire_count=1920,
            last_fired=ago(minutes=7), last_result="left alone"),
        _ev("ev-plants", "Remind Ellis to water the plants (fern + fiddle leaf)", ago(hours=9), status="completed",
            conv="s-plants", fire_count=1, last_fired=ago(hours=9), last_result="Pushed Ellis."),
        _ev("ev-dentist", "Leave-by reminder for the dentist", ago(days=1, hours=6), status="completed", fire_count=1,
            last_fired=ago(days=1, hours=6), last_result="Pushed: leave by 8:10"),
        _ev("ev-failed", "Text Jordan the cabin address", ago(days=2), status="failed", fire_count=1,
            last_fired=ago(days=2), last_result="Tool error: blocked — stood down in that chat"),
        _ev("ev-cancelled", "Remind Ellis about the old gym membership", ago(days=5), status="cancelled", created_by="user"),
    ]


def build_files() -> List[dict]:
    def f(fid, name, mime, size, cat, desc, tags, source, conv=None, created=None):
        return {"id": fid, "filename": name, "mime_type": mime, "size_bytes": size, "category": cat,
                "description": desc, "tags": tags, "source": source, "source_conversation_id": conv,
                "created_at": iso(created or ago(days=2)), "updated_at": iso(created or ago(days=2)),
                "last_accessed": None, "access_count": 0, "download_url": f"/api/files/{fid}/download"}
    return [
        f("f-cabin-csv", "cabin-budget.csv", "text/csv", 412, "document", "Cabin weekend budget split", "trip", "sandbox", "c-cabin-weekend", ago(minutes=26)),
        f("f-biscuit-labs", "biscuit-labs.jpg", "image/jpeg", 184220, "image", "Biscuit bloodwork photo", "pets,biscuit", "upload", "c-biscuit-vet", ago(hours=2)),
        f("f-memo-1", "voice-memo.m4a", "audio/mp4", 48213, "audio", "Voice memo", "memo", "upload", "c-voice-morning", ago(hours=3)),
        f("f-flights", "thanksgiving-flights.md", "text/markdown", 1880, "document", "Flight comparison", "travel", "worker", "w-flight-research", ago(hours=6)),
        f("f-shelves", "garage-plan.png", "image/png", 90211, "image", "Shelf layout sketch", "garage", "sandbox", "c-garage-shelves", ago(days=1)),
        f("f-sitter-pdf", "sitter-sheet.pdf", "application/pdf", 55120, "document", None, None, "sandbox", None, ago(days=12)),
    ]


def build_databases() -> Dict[str, Any]:
    dbs = [
        {"id": "db-runs", "name": "runs", "schema_name": "edward_db_runs", "description": "Ellis's training runs from Strava",
         "created_at": iso(ago(days=20)), "updated_at": iso(ago(days=4)), "last_accessed": iso(ago(days=4))},
        {"id": "db-pets", "name": "pet_weights", "schema_name": "edward_db_pet_weights", "description": "Monthly pet weights",
         "created_at": iso(ago(days=80)), "updated_at": iso(ago(days=25)), "last_accessed": None},
    ]
    tables = {"runs": [{"name": "runs", "column_count": 6}, {"name": "weeks", "column_count": 4}],
              "pet_weights": [{"name": "weights", "column_count": 4}]}

    def col(name, dt, pos, nullable="NO", default=None):
        return {"name": name, "data_type": dt, "is_nullable": nullable, "column_default": default, "ordinal_position": pos}
    columns = {
        ("runs", "runs"): [col("id", "integer", 1, default="nextval('runs_id_seq')"), col("day", "date", 2), col("miles", "numeric", 3),
                           col("pace", "text", 4), col("kind", "text", 5), col("note", "text", 6, "YES")],
        ("runs", "weeks"): [col("week", "integer", 1), col("miles", "numeric", 2), col("long_run", "numeric", 3), col("note", "text", 4, "YES")],
        ("pet_weights", "weights"): [col("id", "integer", 1), col("pet", "text", 2), col("day", "date", 3), col("lbs", "numeric", 4)],
    }
    return {"dbs": dbs, "tables": tables, "columns": columns}


# ---------------------------------------------------------------------------
# Skills, servers, orchestrator, wishlist, dreaming, usage
# ---------------------------------------------------------------------------

SKILLS = [
    ("imessage_applescript", "iMessage (AppleScript)", "Send via osascript (macOS only)", True, "connected", None, None),
    ("twilio_sms", "SMS (Edward's Number)", "Send/receive SMS from Edward's phone", True, "connected", None, {"phone_number": "+1 (555) 010-0100"}),
    ("twilio_whatsapp", "WhatsApp (Edward's Number)", "Send/receive WhatsApp messages from Edward's phone via Twilio", False, "disabled", None, None),
    ("whatsapp_mcp", "WhatsApp (MCP)", "Read/send WhatsApp as the user via whatsapp-mcp bridge", True, "error", "Bridge not paired — scan the QR code in the bridge log", None),
    ("brave_search", "Web Search (Brave)", "Search the web using Brave Search API", True, "connected", None, {"tools_count": 2}),
    ("code_interpreter", "Code Interpreter", "Execute Python code in a sandboxed environment", True, "connected", None, None),
    ("javascript_interpreter", "JavaScript Interpreter", "Execute JavaScript code using Node.js", True, "connected", None, None),
    ("sql_interpreter", "SQL Database", "Execute SQL queries against a per-conversation SQLite database", True, "connected", None, None),
    ("shell_interpreter", "Shell/Bash", "Execute shell commands in a sandboxed environment", False, "disabled", None, None),
    ("contacts_lookup", "Contacts Lookup", "Search Contacts.app for names and phone numbers (macOS only)", True, "connected", None, None),
    ("push_notifications", "Push Notifications", "Send push notifications to user's devices (PWA)", True, "connected", None, None),
    ("apple_services", "Apple Services", "Calendar, Reminders, Notes, Mail, Contacts, Maps (macOS only)", True, "connecting", "Starting apple-mcp…", {"tools_count": 31}),
    ("voice", "Voice Conversations", "Voice memos and live conversation via ElevenLabs (Scribe v2 STT, streaming TTS)", True, "connected", None, None),
    ("html_hosting", "HTML Hosting", "Create, update, and delete hosted HTML pages on your page host (HTML_HOSTING_URL)", True, "connected", None, None),
    ("ios_widget", "iOS Widget", "Control iOS home screen widget via Scriptable app", True, "connected", None, None),
    ("orchestrator", "Orchestrator", "Spawn parallel worker agents for complex multi-step tasks", True, "connected", None, None),
    ("alexa_outbound", "Alexa Outbound (Home Assistant)", "TTS announcements and sounds on Echo devices via Home Assistant", False, "disabled", None, None),
    ("alexa_inbound", "Alexa Inbound (Custom Skill)", "Receive voice queries from Alexa custom skill", False, "disabled", None, None),
    ("claude_session", "Claude Code Session", "Start a Claude Code remote session accessible from the Claude app", False, "disabled", None, None),
]


def build_skills() -> List[dict]:
    return [{"id": i, "name": n, "description": d, "enabled": e, "status": s, "status_message": msg, "metadata": meta}
            for (i, n, d, e, s, msg, meta) in SKILLS]


def build_custom_mcp() -> List[dict]:
    return [
        {"id": "mcp-weather", "name": "weather", "description": "NWS forecasts and alerts", "package_name": "@example/mcp-weather",
         "runtime": "npx", "tool_prefix": "weather", "enabled": True, "status": "connected", "error": None,
         "tool_names": ["weather_forecast", "weather_alerts", "weather_hourly"], "tool_count": 3, "args": [],
         "env_var_keys": [], "source_url": "https://github.com/example/mcp-weather", "added_at": iso(ago(days=18)),
         "updated_at": iso(ago(days=2))},
        {"id": "mcp-strava", "name": "strava", "description": "Read activities from Strava", "package_name": "strava-mcp",
         "runtime": "uvx", "tool_prefix": "strava", "enabled": True, "status": "error",
         "error": "Token expired — re-authorize in the Strava app", "tool_names": [], "tool_count": 0, "args": ["--read-only"],
         "env_var_keys": ["STRAVA_CLIENT_ID", "STRAVA_REFRESH_TOKEN"], "source_url": None, "added_at": iso(ago(days=9)),
         "updated_at": iso(ago(hours=20))},
    ]


def build_orchestrator_tasks() -> List[dict]:
    def t(tid, desc, status, *, conv=None, parent="c-cabin-weekend", ttype="worker", model="gpt-5.6-terra",
          summary=None, error=None, started=None, completed=None):
        return {"id": tid, "parent_conversation_id": parent, "worker_conversation_id": conv, "task_description": desc,
                "task_type": ttype, "model": model, "cc_session_id": None, "status": status, "context_mode": "summary",
                "result_summary": summary, "error": error, "timeout_seconds": 600, "started_at": iso(started),
                "completed_at": iso(completed), "created_at": iso(started or ago(hours=1)),
                "updated_at": iso(completed or started or ago(hours=1))}
    return [
        t("task-flights", "Compare nonstop flights SPI → DEN for Thanksgiving week", "completed", conv="w-flight-research",
          parent="c-cabin-weekend", summary="United 7:05am out / 6:40pm back at $389 is the pick.",
          started=ago(hours=6, minutes=8), completed=ago(hours=6)),
        t("task-table-watch", "Watch Ember's booking page for a 7pm table on the 29th", "running", parent="c-rowan-bday",
          started=ago(minutes=3)),
        t("task-receipts", "Sort October receipts into the budget sheet", "failed", parent="c-budget",
          error="Timed out after 600s waiting for the spreadsheet", started=ago(days=1, hours=3), completed=ago(days=1, hours=2, minutes=50)),
        t("task-cc-widget", "Tidy the widget script's layout code", "completed", ttype="cc_session", model=None, parent="c-garage-shelves",
          summary="Refactored the section renderer; widget unchanged visually.", started=ago(days=2), completed=ago(days=2, minutes=-12)),
    ]


def build_wishlist() -> List[dict]:
    def w(wid, title, why, status, priority, source, *, detail=None, conv=None, times=1, created=None):
        c = created or ago(days=3)
        return {"id": wid, "title": title, "why": why, "detail": detail, "status": status, "priority": priority,
                "source": source, "conversation_id": conv, "times_wanted": times, "created_at": iso(c), "updated_at": iso(c)}
    return [
        w("wish-strava", "Read Strava directly", "Logging Ellis's runs by screenshot is slow and error-prone.", "open", 1, "edward",
          detail="An official Strava integration with read-only scope.", conv="t-goal-log", times=4, created=ago(hours=6)),
        w("wish-calendar-write", "Edit calendar events in place", "I add a corrected duplicate instead of fixing the wrong one.",
          "planned", 1, "edward", times=2, created=ago(days=4)),
        w("wish-hub-camera", "See the pet station camera", "I could tell whether a dose was actually given.", "open", 2, "edward", created=ago(days=6)),
        w("wish-sitter", "Generate the sitter sheet automatically", "Ellis rewrites it by hand every trip.", "building", 2, "owner", created=ago(days=10)),
        w("wish-weather", "Hourly weather on the widget", "Useful for long-run planning.", "done", 3, "edward", created=ago(days=20)),
        w("wish-voice-clone", "Speak in Ellis's voice on calls", "Would let me confirm appointments.", "declined", 3, "edward", created=ago(days=25)),
    ]


def build_dreaming() -> Dict[str, Any]:
    status = {"running": True, "enabled": True, "interval_seconds": 3600, "lookback_hours": 6,
              "last_run": iso(ago(minutes=38)), "next_run": iso(ahead(minutes=22)), "cycle_count": 912,
              "total_connections": 3814, "total_flags": 211, "max_cluster_size": 12, "max_pairs_per_cluster": 6,
              "max_llm_calls_per_cycle": 30}
    flags = [
        {"id": "fl-1", "memory_id": "m-02", "flag_type": "contradiction",
         "description": "Two different Carprofen strengths for Biscuit.", "related_memory_id": "m-02b",
         "resolved": False, "resolved_at": None, "created_at": iso(ago(hours=5)),
         "memory_content": "Biscuit takes Carprofen 100mg twice a day.",
         "related_memory_content": "Biscuit takes Carprofen 300mg in the evening."},
        {"id": "fl-2", "memory_id": "m-21", "flag_type": "contradiction",
         "description": "Kai's 1:1 day differs.", "related_memory_id": "m-21b", "resolved": False, "resolved_at": None,
         "created_at": iso(ago(days=1)), "memory_content": "Kai is Ellis's manager; 1:1 on Thursdays at 4:30.",
         "related_memory_content": "Ellis's 1:1 with Kai moved to Wednesdays."},
        {"id": "fl-3", "memory_id": "m-14", "flag_type": "stale", "description": "Thermostat advice is seasonal.",
         "related_memory_id": None, "resolved": False, "resolved_at": None, "created_at": iso(ago(days=2))},
        {"id": "fl-4", "memory_id": "m-10", "flag_type": "low_quality", "description": "Vague preference, one mention.",
         "related_memory_id": None, "resolved": True, "resolved_at": iso(ago(days=3)), "created_at": iso(ago(days=6))},
    ]
    connections = [
        {"id": f"cn-{i}", "memory_id_a": a, "memory_id_b": b, "connection_type": t, "strength": s, "created_at": iso(ago(hours=i * 3))}
        for i, (a, b, t, s) in enumerate([("m-02", "m-03", "elaborates", 0.92), ("m-08", "m-05", "related", 0.41),
                                          ("m-07", "m-10", "supports", 0.77), ("m-09", "m-22", "related", 0.83),
                                          ("m-01", "m-17", "related", 0.35)], start=1)
    ]
    return {"status": status, "flags": flags, "connections": connections}


def build_usage() -> dict:
    def s(i, o, r):
        return {"input_tokens": i, "output_tokens": o, "requests": r}
    by_model = {
        "gpt-6-astra": {"lifetime": s(48_200_000, 1_950_000, 9120), "session": s(1_240_000, 61_000, 212), "by_model": {}},
        "gpt-5.6-terra": {"lifetime": s(121_000_000, 3_100_000, 30114), "session": s(3_900_000, 98_000, 811), "by_model": {}},
        "gpt-5.6-luna": {"lifetime": s(6_100_000, 402_000, 2210), "session": s(88_000, 7_100, 31), "by_model": {}},
    }
    return {"providers": {"openai": {"lifetime": s(175_300_000, 5_452_000, 41444), "session": s(5_228_000, 166_100, 1054),
                                     "by_model": by_model}},
            "session_start": iso(ago(hours=26))}


def build_codex_usage() -> dict:
    return {
        "status": "ok", "plan_type": "pro",
        "meter": {"windows": {"primary": {"used_percent": 58.4, "window_minutes": 10080,
                                          "reset_after_seconds": 2 * 86400 + 5 * 3600,
                                          "reset_at": iso(ahead(days=2, hours=5)), "elapsed_fraction": 0.69}},
                  "age_seconds": 41, "limit_reached": False},
        "background_allowed": True, "background_reason": "58% used, pace line 84%", "background_ceiling_percent": 90,
        "cooldown_remaining": 0, "calls_last_hour": 64,
        "surplus": {"state": "spending", "reason": None, "used": 58.4, "reserve": 9.8, "headroom": 16.8,
                    "target_rate": 0.32, "r_int": 0.19, "hours_to_reset": 53.0, "projected_end": 83.1, "planned_end": 85.0,
                    "last_job": "research", "last_job_at": iso(ago(minutes=18)), "last_job_outcome": "saved 1 document"},
        "spend_top": [{"purpose": "chat", "pct": 21.4, "basis": "meter"}, {"purpose": "mind", "pct": 17.9, "basis": "meter"},
                      {"purpose": "extraction", "pct": 8.2, "basis": "tokens"}, {"purpose": "surplus:research", "pct": 4.1, "basis": "tokens"},
                      {"purpose": "pulse", "pct": 3.6, "basis": "meter"}],
    }


def build_proactive() -> Dict[str, Any]:
    loops = [
        {"id": "ol-1", "description": "Tell Dad whether you and Rowan are coming Sunday", "source_memory_id": None,
         "source_conversation_id": "c-sms-dad", "entity_id": "ent-dad", "status": "open", "priority": 0.8,
         "detected_at": iso(ago(hours=5)), "last_nudged_at": None, "resolved_at": None, "snooze_until": None, "nudge_count": 0},
        {"id": "ol-2", "description": "Order the shelf brackets (24) and boards for the garage", "source_memory_id": None,
         "source_conversation_id": "c-garage-shelves", "entity_id": None, "status": "open", "priority": 0.6,
         "detected_at": iso(ago(days=1)), "last_nudged_at": iso(ago(hours=20)), "resolved_at": None, "snooze_until": None,
         "nudge_count": 2},
        {"id": "ol-3", "description": "Book Ember if a 7pm table opens", "source_memory_id": None,
         "source_conversation_id": "c-rowan-bday", "entity_id": None, "status": "open", "priority": 0.55,
         "detected_at": iso(ago(days=1, hours=6)), "last_nudged_at": None, "resolved_at": None, "snooze_until": None,
         "nudge_count": 0},
        {"id": "ol-4", "description": "Return the extra dog bed", "source_memory_id": None, "source_conversation_id": None,
         "entity_id": None, "status": "open", "priority": 0.3, "detected_at": iso(ago(days=12)), "last_nudged_at": None,
         "resolved_at": None, "snooze_until": None, "nudge_count": 0},
        {"id": "ol-5", "description": "Renew the car registration", "source_memory_id": None, "source_conversation_id": None,
         "entity_id": None, "status": "resolved", "priority": 0.7, "detected_at": iso(ago(days=20)), "last_nudged_at": None,
         "resolved_at": iso(ago(days=3)), "snooze_until": None, "nudge_count": 1},
    ]
    entities = [
        {"id": "ent-rowan", "name": "Rowan", "entity_type": "person", "aliases": ["Ro"], "mention_count": 812,
         "first_seen": iso(ago(days=300)), "last_seen": iso(ago(hours=1))},
        {"id": "ent-parker", "name": "Parker", "entity_type": "person", "aliases": [], "mention_count": 143,
         "first_seen": iso(ago(days=200)), "last_seen": iso(ago(hours=1))},
        {"id": "ent-jordan", "name": "Jordan", "entity_type": "person", "aliases": [], "mention_count": 57,
         "first_seen": iso(ago(days=150)), "last_seen": iso(ago(minutes=55))},
        {"id": "ent-dad", "name": "Dad", "entity_type": "person", "aliases": ["Pops"], "mention_count": 96,
         "first_seen": iso(ago(days=300)), "last_seen": iso(ago(hours=5))},
        {"id": "ent-biscuit", "name": "Biscuit", "entity_type": "pet", "aliases": [], "mention_count": 402,
         "first_seen": iso(ago(days=300)), "last_seen": iso(ago(hours=2))},
        {"id": "ent-ember", "name": "Ember", "entity_type": "place", "aliases": [], "mention_count": 6,
         "first_seen": iso(ago(days=2)), "last_seen": iso(ago(days=1))},
    ]
    config = {"dossier_enabled": True, "dossier_min_acted_pushes": 3, "dossier_min_inbound_threads": 5, "dossier_window_days": 30}
    wakes = [{"kind": "morning", "cron": "0 7 * * *"}, {"kind": "evening", "cron": "0 18 * * *"},
             {"kind": "reflect", "cron": "0 9 * * 0"}]
    return {"loops": loops, "entities": entities, "config": config, "wakes": wakes}


def build_journal(mind_log: List[dict]) -> dict:
    by_outcome: Dict[str, int] = {}
    for w in mind_log:
        by_outcome[w["outcome"]] = by_outcome.get(w["outcome"], 0) + 1
    obs = [
        {"id": "ob-1", "kind": "tool_failure", "signature": "tool_failure.browser_open.disconnected", "occurrences": 4,
         "description": "Chrome extension link dropped during a browser_open", "last_seen_at": iso(ago(hours=13)), "status": "open"},
        {"id": "ob-2", "kind": "tool_failure", "signature": "tool_failure.apple_calendar_list.timeout", "occurrences": 2,
         "description": "Calendar MCP call timed out (10s)", "last_seen_at": iso(ago(hours=13)), "status": "open"},
        {"id": "ob-3", "kind": "triage", "signature": "triage.dismissed_then_owner_replied", "occurrences": 3,
         "description": "Set aside a message Ellis then answered themselves", "last_seen_at": iso(ago(days=2)), "status": "open"},
        {"id": "ob-4", "kind": "push_outcome", "signature": "push.unopened.evening", "occurrences": 5,
         "description": "Evening pushes left unopened", "last_seen_at": iso(ago(days=3)), "status": "superseded"},
    ]
    kinds: Dict[str, int] = {}
    for o in obs:
        kinds[o["kind"]] = kinds.get(o["kind"], 0) + o["occurrences"]
    return {"window_days": 7,
            "observations": {"by_kind": [{"kind": k, "count": v} for k, v in kinds.items()], "recent": obs,
                             "total_in_window": sum(kinds.values())},
            "wakes": {"by_outcome": [{"outcome": k, "count": v} for k, v in by_outcome.items()],
                      "recent": [{k: w[k] for k in ("id", "at", "source", "outcome", "note", "tools", "surfaces", "delivered")}
                                 for w in mind_log[:12]],
                      "total_in_window": len(mind_log)}}


# ---------------------------------------------------------------------------
# Today inputs (fed to the backend's own pure composer)
# ---------------------------------------------------------------------------

def build_notes() -> List[dict]:
    return [
        {"id": "n-passport", "kind": "mind", "title": "Your passport expires in five months",
         "body": "The trip abroad in March needs six months of validity, so renew in the next few weeks. Routine service "
                 "is 6–8 weeks right now; I can fill in the DS-82 if you want.", "needs_you": True, "href": None,
         "created_at": iso(ago(hours=7)), "expires_at": iso(ahead(hours=20))},
        {"id": "n-dishwasher", "kind": "mind", "title": "Found your quiet dishwasher",
         "body": "Bosch 500, 44 dBA, $849, in stock at two stores near you. Details in Research: quiet dishwashers.",
         "needs_you": False, "href": "/chat?c=edward-research", "created_at": iso(ago(hours=9)), "expires_at": iso(ahead(days=1))},
        {"id": "n-flag-1", "kind": "mind", "title": "Which is right?",
         "body": "Is Biscuit's Carprofen 100mg twice a day, or 300mg in the evening?", "needs_you": True,
         "href": "/settings/dreaming?flag=fl-1", "created_at": iso(ago(minutes=41)), "expires_at": iso(ahead(hours=23))},
        {"id": "n-flag-2", "kind": "mind", "title": "Which is right?",
         "body": "Is your 1:1 with Kai on Thursdays, or did it move to Wednesdays?", "needs_you": True,
         "href": "/settings/dreaming?flag=fl-2", "created_at": iso(ago(minutes=41, seconds=30)),
         "expires_at": iso(ahead(hours=23))},
        {"id": "n-whisper", "kind": "whisper", "title": "Alex says the plumber might come Thursday",
         "body": "Not confirmed yet — I'll tell you if it firms up.", "needs_you": False, "href": None,
         "created_at": iso(ago(hours=3, minutes=18)), "expires_at": iso(ahead(hours=12))},
    ]


def build_approvals() -> List[dict]:
    created = ago(minutes=49)
    return [{"id": 31, "status": "pending", "tool_name": "send_imessage",
             "summary": "iMessage to Jordan: “Sunday works — 6pm at ours?”", "conversation_id": "t-jordan-held",
             # compose_today expects naive UTC datetimes like the table
             "created_at": created.replace(tzinfo=None),
             "args": {"contact": "Jordan", "message": "Sunday works — 6pm at ours?"},
             "taint_reason": "heartbeat", "decided_at": None, "result_text": None}]


def build_state() -> Dict[str, Any]:
    goals = build_goals()
    mind_log = build_mind_log()
    state: Dict[str, Any] = {
        "settings": {"name": "Edward", "personality": "Dry, warm, brief. Says what he did and why.",
                     "provider": "openai", "model": "gpt-6-astra", "temperature": 0.7,
                     "system_prompt": "You are Edward, Ellis's assistant.", "debug_streaming": False, "timezone": TZ_NAME},
        "conversations": build_conversations(),
        "board": build_board(),
        "heartbeat_events": build_heartbeat_events(),
        "heartbeat_status": build_heartbeat_status(),
        "triage_cycles": build_triage_cycles(),
        "heartbeat_feedback": [
            {"id": "hf-1", "event_id": "hb-8832", "verdict": "right_call", "triage_status": "acted", "contact_key": "Neighbor (Dana)",
             "note": None, "created_at": iso(ago(hours=3, minutes=50))},
        ],
        "mind_log": mind_log,
        "intentions": build_intentions(),
        "goals": goals,
        "ideas": build_ideas(),
        "memories": build_memories(),
        "documents": build_documents(goals),
        "events": build_events(),
        "files": build_files(),
        "databases": build_databases(),
        "skills": build_skills(),
        "custom_mcp": build_custom_mcp(),
        "orchestrator": {"config": {"enabled": True, "max_concurrent_workers": 4, "max_concurrent_cc_sessions": 1,
                                    "default_worker_model": "gpt-5.6-terra", "default_worker_timeout": 600},
                         "tasks": build_orchestrator_tasks()},
        "wishlist": build_wishlist(),
        "dreaming": build_dreaming(),
        "usage": build_usage(),
        "codex_usage": build_codex_usage(),
        "proactive": build_proactive(),
        "journal": build_journal(mind_log),
        "notes": build_notes(),
        "approvals": build_approvals(),
        "hub_lists": {
            "shopping": [
                {"id": "li-1", "list_name": "shopping", "text": "Cilantro (we're out, really)", "done": False, "created_by": "Rowan", "done_by": None},
                {"id": "li-2", "list_name": "shopping", "text": "Dog food — 35 lb", "done": True, "created_by": "Ellis", "done_by": "Ellis"},
                {"id": "li-3", "list_name": "shopping", "text": "Oat milk", "done": False, "created_by": "Ellis", "done_by": None},
                {"id": "li-4", "list_name": "shopping", "text": "Paper towels", "done": False, "created_by": "Edward", "done_by": None},
            ],
            "household": [
                {"id": "li-5", "list_name": "household", "text": "Replace the upstairs air filter", "done": False, "created_by": "Ellis", "done_by": None},
                {"id": "li-6", "list_name": "household", "text": "Call the plumber back", "done": False, "created_by": "Rowan", "done_by": None},
            ],
        },
        "hub_settings": {"slide_seconds": 12, "transition": "kenburns", "edward_presents": True},
        "hub_who": {"person": "Rowan", "since": iso(ago(minutes=12)), "last_seen": iso(ago(minutes=2))},
        "display_settings": {"accent": "green", "time_of_day": "auto", "density": "normal", "avatar_size": "md",
                             "show_panels": {k: True for k in ("clock", "alerts", "calendar", "heartbeat", "now_thinking",
                                                               "recents", "workers", "vitals", "memory_ticker")},
                             "show_thinking": True, "speak_aloud": True},
        "screen_show": None,
        "screen_override": {},
        "screen_live": None,
        "hub_canvas": None,
        "photos": ["lake-dogs.jpg", "maple-snow.jpg", "pepper-window.jpg", "whiskers-box.jpg", "cabin-dock.jpg"],
        "push_log": [],
        "signals": [],
        "greeting": "Evening, Ellis. Biscuit's 6 o'clock meds are still waiting, and Jordan wants an answer about Sunday.",
        "counters": {"approval": 31, "wake": 9200},
    }
    return state


def fresh_state() -> Dict[str, Any]:
    return copy.deepcopy(build_state())


# ---------------------------------------------------------------------------
# GET /api/mind/now — Edward's own live state (backend services/wake_state.py).
# Derived from the live fixture state so it agrees with the Thoughts panel,
# the approvals and the sidebar; POST /__mock/mind_now overrides any field
# (or applies a preset) so every mood can be shown.
# ---------------------------------------------------------------------------

MIND_NOW_MAX_RECENT = 5
EDWARD_OWN_SOURCES = ("scheduled_event", "heartbeat", "edward_thought")


def _mind_now_row(w: Dict[str, Any]) -> Dict[str, Any]:
    line = " ".join(str(w.get("note") or "").split())
    at = w.get("at")
    at = at.split(".")[0] + "Z" if isinstance(at, str) and "." in at else at  # to the second, like the backend
    return {"at": at, "reason": w.get("source"), "outcome": w.get("outcome"),
            "line": line or None, "thought_id": w.get("thought_id") or None}


def mind_now(state: Dict[str, Any], at: datetime) -> Dict[str, Any]:
    """The fixture's /api/mind/now body, the backend's exact shape. The
    surfaces pulse writes no mind ledger row in production, so its rows are
    left out of ``recent``; a running mock wake is ``waking``."""
    rows = sorted((w for w in state.get("mind_log", []) if w.get("source") != "surfaces"),
                  key=lambda w: w.get("at") or "", reverse=True)[:MIND_NOW_MAX_RECENT]
    recent = [_mind_now_row(w) for w in rows]
    unread = sum(1 for c in state.get("conversations", {}).values()
                 if c.get("notified_user") and c.get("source") in EDWARD_OWN_SOURCES)
    return {
        "now": iso(at.replace(microsecond=0)),
        "waking": state.get("mind_waking"),
        "last": recent[0] if recent else None,
        "recent": recent,
        "next_wake_at": iso((at + timedelta(minutes=38)).replace(second=0, microsecond=0)),
        "approvals_waiting": sum(1 for a in state.get("approvals", []) if a.get("status") == "pending"),
        "unread_thoughts": unread,
        "quota": {"used_percent": 41.3, "exhausted": False,
                  "resets_at": iso((at + timedelta(hours=5, minutes=12)).replace(second=0, microsecond=0))},
        "hands": {"connected": True, "down_for_s": None},
    }


def mind_now_preset(name: str, at: datetime) -> Dict[str, Any]:
    """Overrides that put Edward in each mood (merged over ``mind_now``).
    ``offline`` / ``missing`` are handled by the server (503 / 404)."""
    started = iso((at - timedelta(seconds=20)).replace(microsecond=0))
    if name == "idle":
        return {"waking": None, "approvals_waiting": 0, "unread_thoughts": 0,
                "quota": {"exhausted": False}, "hands": {"connected": True, "down_for_s": None}}
    if name == "waking":
        return {"waking": {"reason": "morning", "started_at": started, "conversation_id": "edward-mind"}}
    if name == "mention":
        return {"waking": {"reason": "mention", "started_at": started, "conversation_id": "edward-mind"}}
    if name == "pulse":
        return {"waking": {"reason": "surfaces", "started_at": started, "conversation_id": "edward-surfaces"}}
    if name == "free":
        return {"waking": {"reason": "free", "started_at": started, "conversation_id": "edward-free-time"}}
    if name == "attention":
        return {"waking": None, "approvals_waiting": 2, "unread_thoughts": 3}
    if name == "acted":
        row = {"at": iso(at.replace(microsecond=0)), "reason": "messages", "outcome": "acted",
               "line": "Replied to Parker: the flight lands at 4:40.", "thought_id": "t-reply-parker"}
        return {"waking": None, "last": row, "prepend_recent": row}
    if name == "failed":
        row = {"at": iso(at.replace(microsecond=0)), "reason": "evening", "outcome": "failed",
               "line": "ValueError: Codex API error (HTTP 503): upstream connect error", "thought_id": None}
        return {"waking": None, "last": row, "prepend_recent": row}
    if name == "hands_down":
        return {"hands": {"connected": False, "down_for_s": 240}}
    if name == "quota_exhausted":
        return {"quota": {"used_percent": 100.0, "exhausted": True,
                          "resets_at": iso((at + timedelta(days=1, hours=3)).replace(second=0, microsecond=0))}}
    if name == "night":
        return {"waking": None, "approvals_waiting": 0, "unread_thoughts": 0,
                "next_wake_at": iso((at + timedelta(hours=7)).replace(second=0, microsecond=0))}
    raise KeyError(name)


MIND_NOW_PRESETS = ("idle", "waking", "mention", "pulse", "free", "attention", "acted", "failed", "hands_down",
                    "quota_exhausted", "night", "offline", "missing")


def merge_mind_now(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """``override`` over ``base``: nested ``quota`` / ``hands`` merge key by
    key; ``prepend_recent`` puts a row on top of ``recent`` (max five)."""
    out = dict(base)
    for key, value in (override or {}).items():
        if key == "prepend_recent":
            out["recent"] = ([value] + [r for r in out.get("recent", []) if r != value])[:MIND_NOW_MAX_RECENT]
        elif key in ("quota", "hands") and isinstance(value, dict):
            out[key] = {**(out.get(key) or {}), **value}
        elif key in base:
            out[key] = value
    return out
