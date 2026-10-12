"""The Today feed: what rises to "needs you", what folds into the digest.

Today replaces the inbox. The inbox was a query result — every conversation
Edward had flagged, plus every pending event — and on 2026-09-05 it said
"26 things need you" when two were Ellis's. The composer here is where the
judgment lives, as pure rules over plain dicts so they can be pinned:

  * Only things with an action rise. Edward's recurring housekeeping crons
    ("every 2h — sweep notified pushes") never do.
  * Free-fed (window) pet items never alert; only timed doses do — the same
    alert-fatigue rule as the household hub.
  * Snoozed open loops stay quiet until their snooze passes.
  * Recurring events that fired today collapse to one digest row each.
"""

from datetime import datetime, timedelta, timezone

from services.today_service import TodayInputs, compose_today

NOW = datetime(2026, 9, 6, 1, 41, tzinfo=timezone.utc)  # 8:41 PM Springfield, Sat Sep 5


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def test_empty_inputs_give_an_empty_but_well_formed_feed():
    out = compose_today(TodayInputs(), now=NOW)
    assert out["needs"] == []
    assert out["did"] == []
    assert out["conversations"] == []
    assert out["thoughts_today"] == 0


def test_overdue_timed_dose_outranks_everything_and_carries_a_log_action():
    inputs = TodayInputs(
        board={
            "pets": [
                {
                    "id": "p1",
                    "name": "Biscuit",
                    "meds": [
                        {"id": "m1", "name": "Carprofen", "dose": "25 mg", "status": "overdue",
                         "minutes_late": 22, "at_time": "18:00", "kind": "med"},
                        {"id": "m2", "name": "Dinner", "status": "due", "at_time": None, "kind": "feeding"},
                    ],
                }
            ]
        },
        open_loops=[{"id": "l1", "description": "Confirm a time with Jordan", "priority": 0.9,
                     "status": "open", "snooze_until": None, "source_conversation_id": "c9"}],
    )
    out = compose_today(inputs, now=NOW)
    kinds = [n["kind"] for n in out["needs"]]
    assert kinds[0] == "dose"
    assert "open_loop" in kinds
    dose = out["needs"][0]
    assert dose["tone"] == "warn"
    assert "Biscuit" in dose["title"] and "Carprofen" in dose["title"]
    log = next(a for a in dose["actions"] if a["id"] == "log")
    assert log["method"] == "POST" and log["url"] == "/api/hub/care/m1/log"
    # The windowed dinner never becomes a need.
    assert all("Dinner" not in n["title"] for n in out["needs"])


def test_snoozed_open_loop_is_quiet_until_snooze_passes():
    later = _iso(NOW + timedelta(hours=10))
    earlier = _iso(NOW - timedelta(hours=1))
    inputs = TodayInputs(open_loops=[
        {"id": "a", "description": "Snoozed", "priority": 0.9, "status": "open", "snooze_until": later},
        {"id": "b", "description": "Woke up", "priority": 0.6, "status": "open", "snooze_until": earlier},
        {"id": "c", "description": "Low", "priority": 0.3, "status": "open", "snooze_until": None},
    ])
    out = compose_today(inputs, now=NOW)
    ids = [n["id"] for n in out["needs"]]
    assert ids == ["open_loop:b"]


def test_fresh_open_loops_come_before_ones_owner_has_been_declining_for_months():
    inputs = TodayInputs(open_loops=[
        {"id": "old", "description": "Nudged forever", "priority": 0.95, "status": "open",
         "snooze_until": None, "nudge_count": 21},
        {"id": "new", "description": "Detected today", "priority": 0.6, "status": "open",
         "snooze_until": None, "nudge_count": 0},
    ])
    out = compose_today(inputs, now=NOW)
    assert [n["id"] for n in out["needs"]] == ["open_loop:new", "open_loop:old"]


def test_open_loop_actions_resolve_and_snooze_via_the_proactive_api():
    inputs = TodayInputs(open_loops=[
        {"id": "l1", "description": "Reply to Sasha", "priority": 0.7, "status": "open",
         "snooze_until": None, "source_conversation_id": "conv1"},
    ])
    [need] = compose_today(inputs, now=NOW)["needs"]
    by_id = {a["id"]: a for a in need["actions"]}
    assert by_id["done"]["method"] == "PATCH"
    assert by_id["done"]["url"] == "/api/proactive/open-loops/l1"
    assert by_id["done"]["body"] == {"status": "resolved"}
    assert by_id["snooze"]["body"]["status"] == "snoozed"
    assert "snooze_until" in by_id["snooze"]["body"]
    assert need["href"] == "/chat?c=conv1"


def test_recurring_housekeeping_events_never_need_you_but_one_shots_within_24h_do():
    soon = _iso(NOW + timedelta(hours=3))
    far = _iso(NOW + timedelta(hours=40))
    inputs = TodayInputs(events=[
        {"id": "e1", "description": "Every 2h — sweep notified pushes", "next_fire_at": soon,
         "recurrence_pattern": "0 */2 * * *", "status": "pending", "created_by": "edward"},
        {"id": "e2", "description": "Remind Ellis to call the vet", "next_fire_at": soon,
         "recurrence_pattern": None, "status": "pending", "created_by": "user"},
        {"id": "e3", "description": "Next week thing", "next_fire_at": far,
         "recurrence_pattern": None, "status": "pending", "created_by": "user"},
    ])
    out = compose_today(inputs, now=NOW)
    ids = [n["id"] for n in out["needs"]]
    assert ids == ["event:e2"]
    assert out["needs"][0]["href"] == "/settings/events"


def test_escalated_heartbeat_ranks_above_open_loops_and_links_to_the_thread():
    inputs = TodayInputs(
        heartbeat=[
            {"id": "h1", "contact_name": "Ian Cho", "summary": "can we move to 2?",
             "triage_status": "escalated", "created_at": _iso(NOW - timedelta(minutes=30)),
             "conversation_id": "hb-conv"},
            {"id": "h2", "contact_name": "Promo", "summary": "SALE", "triage_status": "dismissed",
             "created_at": _iso(NOW - timedelta(minutes=5)), "conversation_id": None},
            {"id": "h3", "contact_name": "Old", "summary": "yesterday", "triage_status": "escalated",
             "created_at": _iso(NOW - timedelta(hours=30)), "conversation_id": None},
        ],
        open_loops=[{"id": "l1", "description": "x", "priority": 0.95, "status": "open", "snooze_until": None}],
    )
    out = compose_today(inputs, now=NOW)
    assert [n["kind"] for n in out["needs"]] == ["heartbeat", "open_loop"]
    assert out["needs"][0]["href"] == "/chat?c=hb-conv"
    assert "Ian Cho" in out["needs"][0]["title"]


def test_finished_worker_is_a_need_with_a_review_link():
    inputs = TodayInputs(
        workers=[{"id": "w1", "task_description": "Find Jordan's address", "status": "completed",
                  "result_summary": "Found it", "worker_conversation_id": "wc1",
                  "completed_at": _iso(NOW - timedelta(hours=2))},
                 {"id": "w2", "task_description": "Running", "status": "running",
                  "result_summary": None, "worker_conversation_id": "wc2", "completed_at": None}],
    )
    out = compose_today(inputs, now=NOW)
    by_id = {n["id"]: n for n in out["needs"]}
    assert set(by_id) == {"worker:w1"}
    assert by_id["worker:w1"]["href"] == "/chat?c=wc1"
    assert by_id["worker:w1"]["tone"] == "edward"


def test_digest_collapses_recurring_fires_and_summarises_triage():
    t1 = _iso(NOW - timedelta(hours=1))
    t2 = _iso(NOW - timedelta(hours=3))
    inputs = TodayInputs(
        fired_events=[
            {"id": "e1", "description": "Every 2h — sweep notified pushes", "recurrence_pattern": "x", "last_fired_at": t1},
            {"id": "e1", "description": "Every 2h — sweep notified pushes", "recurrence_pattern": "x", "last_fired_at": t2},
            {"id": "e5", "description": "Morning briefing", "recurrence_pattern": None, "last_fired_at": t2},
        ],
        triage_counts={"total": 50, "dismissed": 38, "noted": 3, "escalated": 0, "handled_by_owner": 6},
    )
    out = compose_today(inputs, now=NOW)
    titles = [d["title"] for d in out["did"]]
    # Plain words (review 2026-09-26): "Triaged 50 messages, noted 3,
    # escalated 0" was the heartbeat's jargon.
    assert titles[0] == "Read 50 messages"
    triage = out["did"][0]
    assert triage["detail"] == "Remembered 3 · you answered 6 yourself · 38 needed nothing"
    assert sum(1 for t in titles if "sweep notified pushes" in t) == 1
    sweep = next(d for d in out["did"] if "sweep" in d["title"])
    assert "×2" in sweep["detail"] or "2 times" in sweep["detail"]
    assert "Morning briefing" in titles


def test_conversations_are_owners_only_and_thoughts_are_counted():
    today = _iso(NOW - timedelta(hours=2))
    inputs = TodayInputs(conversations=[
        {"id": "c1", "title": "Find the address", "source": "user", "updated_at": today},
        {"id": "c2", "title": "Edward", "source": "edward_thought", "updated_at": today},
        {"id": "c3", "title": "Edward", "source": "edward_thought", "updated_at": _iso(NOW - timedelta(days=2))},
        {"id": "c4", "title": "iMessage: Ian Cho", "source": "heartbeat", "updated_at": today},
        {"id": "c5", "title": "SMS from Mom", "source": "external_message", "updated_at": today},
    ])
    out = compose_today(inputs, now=NOW, tz_name="America/Chicago")
    assert [c["id"] for c in out["conversations"]] == ["c1", "c5"]
    assert out["thoughts_today"] == 2  # c2 + c4 touched today; c3 was two days ago
    assert out["conversations"][0]["href"] == "/chat?c=c1"


# --- the wishlist ------------------------------------------------------------
#
# Self-evolution is gone (2026-09-12). A wish is Edward asking Ellis for a
# feature: it shows in the digest so Ellis sees it, never as a decision.

def test_a_wish_is_digest_only_and_links_to_the_wishlist():
    inputs = TodayInputs(
        wishes=[{"id": "w1", "title": "Read PDF attachments", "why": "Ellis sent a vet invoice I could not open.",
                 "status": "open", "created_at": _iso(NOW - timedelta(hours=1))}],
    )
    out = compose_today(inputs, now=NOW)
    assert out["needs"] == []
    rows = [d for d in out["did"] if d["kind"] == "wish"]
    assert len(rows) == 1
    assert rows[0]["id"] == "wish:w1"
    assert rows[0]["title"] == "Wished for: Read PDF attachments"
    assert rows[0]["detail"].startswith("Ellis sent a vet invoice")
    assert rows[0]["href"] == "/settings/wishlist"
    assert rows[0]["at"] == _iso(NOW - timedelta(hours=1))


def test_a_note_that_needs_owner_is_a_need_and_a_plain_note_is_digest():
    at = _iso(NOW - timedelta(minutes=20))
    inputs = TodayInputs(notes=[
        {"id": "n1", "kind": "meeting_prep", "title": "Meeting prep: Dentist", "body": "Bring the card.",
         "href": "/today", "needs_you": True, "created_at": at, "expires_at": _iso(NOW + timedelta(hours=2))},
        {"id": "n2", "kind": "morning_briefing", "title": "Good morning", "body": "Two things today.",
         "href": "/today", "needs_you": False, "created_at": at, "expires_at": _iso(NOW + timedelta(hours=20))},
        {"id": "n3", "kind": "meeting_prep", "title": "Stale prep", "body": "old", "href": "/today",
         "needs_you": True, "created_at": _iso(NOW - timedelta(hours=9)), "expires_at": _iso(NOW - timedelta(hours=6))},
    ])
    out = compose_today(inputs, now=NOW)
    need_ids = [n["id"] for n in out["needs"]]
    assert "note:n1" in need_ids and "note:n3" not in need_ids, need_ids
    need = next(n for n in out["needs"] if n["id"] == "note:n1")
    assert need["kind"] == "note" and need["detail"] == "Bring the card."
    assert any(a["method"] == "GET" for a in need["actions"])
    did_ids = [d["id"] for d in out["did"]]
    assert "note:n2" in did_ids and "note:n1" not in did_ids


def test_escalated_messages_from_one_thread_collapse_into_one_need():
    """2026-09-21: a busy group chat put "Nico Fern needs a reply" on
    Today five times (31 escalations from Quinn, 28 from Sasha in a day).
    One thread is one need — the newest message carries it, the count shows."""
    inputs = TodayInputs(heartbeat=[
        {"id": "h1", "contact_name": "Nico Fern", "summary": "you coming?", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=50)), "conversation_id": "group-conv", "chat_identifier": "chat805"},
        {"id": "h2", "contact_name": "Quinn", "summary": "bring the cards", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=20)), "conversation_id": "group-conv", "chat_identifier": "chat805"},
        {"id": "h3", "contact_name": "Nico Fern", "summary": "??", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=5)), "conversation_id": "group-conv", "chat_identifier": "chat805"},
        {"id": "h4", "contact_name": "Rowan", "summary": "ETA?", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=10)), "conversation_id": "rowan-conv", "chat_identifier": "+15550120"},
    ])
    out = compose_today(inputs, now=NOW)
    hb = [n for n in out["needs"] if n["kind"] == "heartbeat"]
    assert len(hb) == 2, [n["title"] for n in hb]
    group = next(n for n in hb if n["href"] == "/chat?c=group-conv")
    assert group["id"] == "heartbeat:h3"  # the newest message carries the need
    assert "Nico Fern" in group["title"]
    assert "3 messages" in group["detail"]
    assert group["at"] == _iso(NOW - timedelta(minutes=5))
    rowan = next(n for n in hb if n["href"] == "/chat?c=rowan-conv")
    assert "messages" not in rowan["detail"] and rowan["detail"] == "ETA?"


def test_escalations_without_a_thread_collapse_by_chat_identifier_then_stand_alone():
    inputs = TodayInputs(heartbeat=[
        {"id": "h1", "sender": "+15550119", "summary": "a", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=9)), "conversation_id": None, "chat_identifier": "+15550119"},
        {"id": "h2", "sender": "+15550119", "summary": "b", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=8)), "conversation_id": None, "chat_identifier": "+15550119"},
        {"id": "h3", "sender": "someone", "summary": "c", "triage_status": "mention",
         "created_at": _iso(NOW - timedelta(minutes=7)), "conversation_id": None},
    ])
    out = compose_today(inputs, now=NOW)
    ids = sorted(n["id"] for n in out["needs"] if n["kind"] == "heartbeat")
    assert ids == ["heartbeat:h2", "heartbeat:h3"]


def test_a_note_without_a_link_still_ships_an_href_and_a_tone():
    """2026-09-21/22: the mind's `leave_note` writes notes without a URL; the
    note need shipped no `href`, the Today page handed `undefined` to Next's
    <Link>, and every open of the PWA died on "Application error: a
    client-side exception has occurred" (TypeError: Cannot destructure
    property 'auth' of 'urlObj'). A need always carries somewhere to go."""
    at = _iso(NOW - timedelta(minutes=20))
    inputs = TodayInputs(notes=[
        {"id": "n1", "kind": "mind", "title": "Jordan is ready to build", "body": "He asked for a time.",
         "needs_you": True, "created_at": at, "expires_at": _iso(NOW + timedelta(hours=2))},
    ])
    out = compose_today(inputs, now=NOW)
    need = next(n for n in out["needs"] if n["id"] == "note:n1")
    assert isinstance(need.get("href"), str) and need["href"].startswith("/"), need
    assert need["href"] == "/chat?c=edward-mind"  # a mind note opens the thread it came from
    assert need["tone"] == "edward"
    assert need["actions"][0]["url"] == need["href"]


def test_every_need_carries_the_fields_the_page_renders():
    """The page trusts these; a missing one is a white screen, not a blank card."""
    at = _iso(NOW - timedelta(minutes=10))
    inputs = TodayInputs(
        heartbeat=[{"id": "h1", "contact_name": "Jordan", "summary": "?", "triage_status": "escalated",
                    "created_at": at, "conversation_id": "hb-conv"}],
        open_loops=[{"id": "l1", "description": "x", "priority": 0.95, "status": "open", "snooze_until": None}],
        events=[{"id": "e1", "description": "Call the vet", "next_fire_at": _iso(NOW + timedelta(hours=3)),
                 "recurrence_pattern": None, "status": "pending"}],
        notes=[{"id": "n1", "kind": "whisper", "title": "Heads up", "body": "b", "needs_you": True,
                "created_at": at, "expires_at": _iso(NOW + timedelta(hours=2))}],
    )
    out = compose_today(inputs, now=NOW)
    assert len(out["needs"]) >= 4, [n["id"] for n in out["needs"]]
    for need in out["needs"]:
        assert isinstance(need.get("href"), str) and need["href"].startswith("/"), need["id"]
        assert isinstance(need.get("title"), str) and need["title"], need["id"]
        assert need.get("tone") in ("warn", "info", "edward", "ok", "bad", "neutral"), (need["id"], need.get("tone"))
        assert isinstance(need.get("actions"), list), need["id"]


# --- needs you, without the flood (UX pass 2026-09-25) -------------------------
#
# The audit found 15 unexpired needs_you notes in a day, seven identical
# "Which is right?" cards within 41 s (the surplus contradictions job), and
# no way to clear a note from Today but to wait a day for it to expire.

def _note(nid, title, body="b", *, minutes_ago=10, href=None, kind="mind", needs_you=True, ttl_h=20):
    return {"id": nid, "kind": kind, "title": title, "body": body, "href": href or "/today",
            "needs_you": needs_you, "created_at": _iso(NOW - timedelta(minutes=minutes_ago)),
            "expires_at": _iso(NOW + timedelta(hours=ttl_h))}


def test_notes_sharing_a_title_collapse_into_one_need_with_a_count():
    inputs = TodayInputs(notes=[
        _note("q1", "Which is right?", "Dentist: Dr. Hale or Dr. Park?", minutes_ago=1),
        _note("q2", "Which is right?", "Gym: Tuesday or Thursday?", minutes_ago=2),
        _note("q3", "Which is right?", "Car: blue or grey?", minutes_ago=3),
        _note("p1", "Your passport expires soon", "Five months left.", minutes_ago=30),
    ])
    out = compose_today(inputs, now=NOW)
    notes = [n for n in out["needs"] if n["kind"] == "note"]
    assert [n["title"] for n in notes] == ["Which is right?", "Your passport expires soon"]
    group = notes[0]
    assert group["id"] == "note:q1"  # the newest carries the card
    assert group["count"] == 3
    assert group["detail"] == "Dentist: Dr. Hale or Dr. Park?"
    assert group["note_ids"] == ["q1", "q2", "q3"]
    assert notes[1]["count"] == 1


def test_note_needs_are_capped_and_the_rest_is_an_overflow_count():
    inputs = TodayInputs(notes=[_note(f"n{i}", f"Note {i}", minutes_ago=i) for i in range(7)])
    out = compose_today(inputs, now=NOW)
    notes = [n for n in out["needs"] if n["kind"] == "note"]
    assert [n["id"] for n in notes] == ["note:n0", "note:n1", "note:n2", "note:n3"]
    assert out["overflow"]["note"] == 3


def test_every_note_need_can_be_cleared_with_done():
    inputs = TodayInputs(notes=[
        _note("q1", "Which is right?", minutes_ago=1),
        _note("q2", "Which is right?", minutes_ago=2),
        _note("solo", "Heads up", minutes_ago=5),
    ])
    out = compose_today(inputs, now=NOW)
    by_id = {n["id"]: n for n in out["needs"]}
    solo_done = next(a for a in by_id["note:solo"]["actions"] if a["id"] == "done")
    assert solo_done["method"] == "POST" and solo_done["url"] == "/api/notes/solo/dismiss"
    assert solo_done["primary"] is True
    assert solo_done.get("body") in (None, {"also": []})
    group_done = next(a for a in by_id["note:q1"]["actions"] if a["id"] == "done")
    assert group_done["url"] == "/api/notes/q1/dismiss"
    assert group_done["body"] == {"also": ["q2"]}  # the whole card goes


def test_a_contradiction_note_opens_the_flag_in_the_dreaming_panel():
    """The flag lives in the Dreaming panel; /settings/memories ignored it."""
    inputs = TodayInputs(notes=[_note("q1", "Which is right?", href="/settings/memories?flag=f-7")])
    out = compose_today(inputs, now=NOW)
    need = next(n for n in out["needs"] if n["kind"] == "note")
    assert need["href"] == "/settings/dreaming?flag=f-7"
    assert next(a for a in need["actions"] if a["id"] == "open")["url"] == "/settings/dreaming?flag=f-7"


def test_a_contradiction_can_be_answered_on_today():
    inputs = TodayInputs(
        notes=[_note("q1", "Which is right?", "Is your dentist Dr. Hale or Dr. Park?",
                     href="/settings/dreaming?flag=f-7")],
        flags={"f-7": {"resolved": False, "memory_a": "Ellis's dentist is Dr. Hale",
                       "memory_b": "Ellis's dentist is Dr. Park"}},
    )
    need = next(n for n in compose_today(inputs, now=NOW)["needs"] if n["kind"] == "note")
    choice = need["choice"]
    assert choice["flag_id"] == "f-7"
    assert choice["question"] == "Is your dentist Dr. Hale or Dr. Park?"
    assert [(o["id"], o["text"]) for o in choice["options"]] == [
        ("a", "Ellis's dentist is Dr. Hale"), ("b", "Ellis's dentist is Dr. Park")]
    by_id = {a["id"]: a for a in need["actions"]}
    for opt in choice["options"]:
        act = by_id[opt["action_id"]]
        assert act["method"] == "POST"
        assert act["url"] == "/api/consolidation/flags/f-7/resolve"
        assert act["body"] == {"keep": opt["id"]}
    assert "done" in by_id  # "not now" is still one tap


def test_an_answered_contradiction_leaves_today_and_the_next_one_takes_the_card():
    inputs = TodayInputs(
        notes=[_note("q1", "Which is right?", "first?", href="/settings/dreaming?flag=f-1", minutes_ago=1),
               _note("q2", "Which is right?", "second?", href="/settings/dreaming?flag=f-2", minutes_ago=2)],
        flags={"f-2": {"resolved": False, "memory_a": "A", "memory_b": "B"}},  # f-1 answered (gone)
    )
    notes = [n for n in compose_today(inputs, now=NOW)["needs"] if n["kind"] == "note"]
    assert len(notes) == 1
    assert notes[0]["id"] == "note:q2" and notes[0]["count"] == 1
    assert notes[0]["choice"]["flag_id"] == "f-2"


def test_a_rewrite_question_links_but_offers_no_inline_choice():
    inputs = TodayInputs(
        notes=[_note("q1", "Which is right?", 'I have a memory that says "x". Should it say "y" instead?',
                     href="/settings/dreaming?flag=f-3&ask=rewrite")],
        flags={"f-3": {"resolved": False, "memory_a": "x", "memory_b": "z"}},
    )
    need = next(n for n in compose_today(inputs, now=NOW)["needs"] if n["kind"] == "note")
    assert "choice" not in need
    assert need["href"].startswith("/settings/dreaming?flag=f-3")


def test_when_flags_cannot_be_read_contradiction_notes_stay_without_a_choice():
    inputs = TodayInputs(notes=[_note("q1", "Which is right?", href="/settings/dreaming?flag=f-9")], flags=None)
    need = next(n for n in compose_today(inputs, now=NOW)["needs"] if n["kind"] == "note")
    assert "choice" not in need


def test_a_heartbeat_need_can_be_cleared_with_done():
    inputs = TodayInputs(heartbeat=[
        {"id": "h1", "contact_name": "Jordan", "summary": "?", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=5)), "conversation_id": "hb", "chat_identifier": "+1"},
    ])
    [need] = compose_today(inputs, now=NOW)["needs"]
    done = next(a for a in need["actions"] if a["id"] == "done")
    assert done["method"] == "POST" and done["url"] == "/api/today/heartbeat/h1/done"
    assert need["count"] == 1


def test_heartbeat_needs_are_capped_at_five_threads_with_an_overflow():
    inputs = TodayInputs(heartbeat=[
        {"id": f"h{i}", "contact_name": f"P{i}", "summary": "hi", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=i + 1)), "conversation_id": f"c{i}", "chat_identifier": f"x{i}"}
        for i in range(8)
    ])
    out = compose_today(inputs, now=NOW)
    hb = [n for n in out["needs"] if n["kind"] == "heartbeat"]
    assert [n["id"] for n in hb] == [f"heartbeat:h{i}" for i in range(5)]
    assert out["overflow"]["heartbeat"] == 3


def test_a_starting_soon_calendar_event_is_an_event_need_not_a_reply():
    start = NOW + timedelta(minutes=25)
    inputs = TodayInputs(heartbeat=[
        {"id": "cal1", "source": "calendar", "summary": f"[STARTING SOON] Dentist — starts at {_iso(start)}",
         "triage_status": "calendar_urgent", "created_at": _iso(NOW - timedelta(minutes=2)),
         "conversation_id": None, "chat_identifier": None},
    ])
    [need] = compose_today(inputs, now=NOW, tz_name="America/Chicago")["needs"]
    assert need["kind"] == "calendar"
    assert need["title"] == "Dentist in 25 min"
    assert "reply" not in need["title"].lower() and "reply" not in need["detail"].lower()
    assert need["tone"] == "info"
    assert need["at"] == _iso(start)
    # Lower-case am/pm like the rest of Today (review 2026-09-26).
    assert need["detail"] == "Starts at 9:06 pm"  # local start time (8:41 pm + 25 min in Springfield)
    assert need["href"].startswith("/")
    assert any(a["id"] == "done" and a["url"] == "/api/today/heartbeat/cal1/done" for a in need["actions"])


def test_a_calendar_event_long_started_is_not_a_need_any_more():
    start = NOW - timedelta(minutes=90)
    inputs = TodayInputs(heartbeat=[
        {"id": "cal1", "summary": f"[STARTING SOON] Standup — starts at {_iso(start)}",
         "triage_status": "calendar_urgent", "created_at": _iso(NOW - timedelta(minutes=100))},
    ])
    assert compose_today(inputs, now=NOW)["needs"] == []


def test_edwards_own_wakes_are_never_needs():
    soon = _iso(NOW + timedelta(hours=2))
    inputs = TodayInputs(events=[
        {"id": "w1", "description": "wake_me: check Parker's flight", "next_fire_at": soon,
         "recurrence_pattern": None, "status": "pending", "created_by": "edward", "type": "mind_wake"},
        {"id": "e2", "description": "Remind Ellis to call the vet", "next_fire_at": soon,
         "recurrence_pattern": None, "status": "pending", "created_by": "user", "type": None},
    ])
    assert [n["id"] for n in compose_today(inputs, now=NOW)["needs"]] == ["event:e2"]


# --- what I did ---------------------------------------------------------------

def test_scheduler_plumbing_never_reaches_what_i_did():
    t = _iso(NOW - timedelta(hours=1))
    inputs = TodayInputs(fired_events=[
        {"id": "s", "description": "surplus_tick — spare quota", "recurrence_pattern": "*/10 * * * *",
         "last_fired_at": t, "type": "surplus_tick"},
        {"id": "p", "description": "expression_pulse — surfaces", "recurrence_pattern": "*/15 * * * *",
         "last_fired_at": t, "type": "expression_pulse"},
        {"id": "m", "description": "morning wake", "recurrence_pattern": "0 7 * * *", "last_fired_at": t,
         "type": "wakeup_tick"},
        {"id": "w", "description": "check the flight", "recurrence_pattern": None, "last_fired_at": t,
         "type": "mind_wake"},
        {"id": "r", "description": "Remind Ellis to call the vet", "recurrence_pattern": None, "last_fired_at": t,
         "type": None},
    ])
    titles = [d["title"] for d in compose_today(inputs, now=NOW)["did"]]
    assert titles == ["Remind Ellis to call the vet"]


def test_the_plumbing_list_covers_every_routed_event_type():
    from services.scheduler_service import event_route
    from services.today_service import PLUMBING_EVENT_TYPES

    for t in ("expression_pulse", "widget_refresh", "mind_wake", "wakeup_tick", "surplus_tick", "proactive_sweep"):
        assert event_route({"type": t}) is not None
        assert t in PLUMBING_EVENT_TYPES, t


def test_the_minds_wakes_fold_into_one_row():
    wakes = [
        {"id": 6, "at": _iso(NOW - timedelta(minutes=10)), "source": "messages", "outcome": "left_alone",
         "note": "Nothing new since the last look."},
        {"id": 5, "at": _iso(NOW - timedelta(hours=1)), "source": "mention", "outcome": "acted", "note": "Replied."},
        {"id": 4, "at": _iso(NOW - timedelta(hours=2)), "source": "messages", "outcome": "acted", "note": "Noted."},
        {"id": 3, "at": _iso(NOW - timedelta(hours=3)), "source": "evening", "outcome": "left_alone", "note": "."},
        {"id": 2, "at": _iso(NOW - timedelta(hours=4)), "source": "self", "outcome": "failed", "note": "timeout"},
        {"id": 1, "at": _iso(NOW - timedelta(hours=5)), "source": "surfaces", "outcome": "skipped", "note": "quota"},
        {"id": 0, "at": _iso(NOW - timedelta(hours=30)), "source": "morning", "outcome": "acted", "note": "old"},
    ]
    did = compose_today(TodayInputs(wakes=wakes), now=NOW)["did"]
    rows = [d for d in did if d["kind"] == "wakes"]
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Woke 5 times · acted on 2 · 1 failed"
    assert row["href"] == "/settings/thoughts"
    assert row["at"] == _iso(NOW - timedelta(minutes=10))
    assert row["detail"] == "Nothing new since the last look."
    assert row["count"] == 5


def test_the_triage_row_says_how_many_were_for_owner():
    inputs = TodayInputs(triage_counts={"total": 47, "dismissed": 40, "noted": 3, "escalated": 4,
                                        "handled_by_owner": 0, "latest_at": _iso(NOW - timedelta(minutes=5))})
    [row] = [d for d in compose_today(inputs, now=NOW)["did"] if d["kind"] == "triage"]
    assert row["title"] == "Read 47 messages · 4 for you"
    assert row["detail"] == "Remembered 3 · 40 needed nothing"
    one = TodayInputs(triage_counts={"total": 1, "escalated": 1, "latest_at": _iso(NOW - timedelta(minutes=5))})
    [row] = [d for d in compose_today(one, now=NOW)["did"] if d["kind"] == "triage"]
    assert row["title"] == "Read 1 message · 1 for you" and row["detail"] == ""


def test_the_wakes_row_quotes_the_latest_wake_that_did_not_fail():
    """A failed wake's note is an error class ("CodexStreamError: …"); the
    row reads what he last *said*, and the title already counts failures."""
    wakes = [
        {"id": 3, "at": _iso(NOW - timedelta(minutes=5)), "source": "self", "outcome": "failed",
         "note": "CodexStreamError: server_error"},
        {"id": 2, "at": _iso(NOW - timedelta(hours=1)), "source": "evening", "outcome": "left_alone",
         "note": "All quiet; the vet can wait until Monday."},
    ]
    [row] = [d for d in compose_today(TodayInputs(wakes=wakes), now=NOW)["did"] if d["kind"] == "wakes"]
    assert row["title"] == "Woke twice · 1 failed"
    assert row["detail"] == "All quiet; the vet can wait until Monday."
    assert row["at"] == _iso(NOW - timedelta(minutes=5)), "the row is still stamped with the newest wake"
    only_failed = [wakes[0]]
    [row] = [d for d in compose_today(TodayInputs(wakes=only_failed), now=NOW)["did"] if d["kind"] == "wakes"]
    assert row["detail"] == ""


def test_dose_times_read_like_the_rest_of_today():
    board = {"pets": [{"id": "p1", "name": "Biscuit", "meds": [
        {"id": "m1", "name": "Carprofen", "status": "overdue", "minutes_late": 22, "at_time": "18:00"},
    ]}]}
    [need] = compose_today(TodayInputs(board=board), now=NOW, tz_name="America/Chicago")["needs"]
    assert need["detail"] == "Due 6:00 pm, 22 min late."


def test_a_calendar_event_that_already_began_says_started_at():
    start = NOW - timedelta(minutes=16)
    inputs = TodayInputs(heartbeat=[
        {"id": "cal2", "source": "calendar", "summary": f"[STARTING SOON] Call with the contractor — starts at {_iso(start)}",
         "triage_status": "calendar_urgent", "created_at": _iso(NOW - timedelta(minutes=20)),
         "conversation_id": None, "chat_identifier": None},
    ])
    [need] = compose_today(inputs, now=NOW, tz_name="America/Chicago")["needs"]
    assert need["title"] == "Call with the contractor started 16 min ago"
    assert need["detail"] == "Started at 8:25 pm"


def test_one_quiet_wake_reads_naturally():
    wakes = [{"id": 1, "at": _iso(NOW - timedelta(minutes=10)), "source": "evening", "outcome": "left_alone",
              "note": "All quiet."}]
    [row] = [d for d in compose_today(TodayInputs(wakes=wakes), now=NOW)["did"] if d["kind"] == "wakes"]
    assert row["title"] == "Woke once · left things alone"


def test_the_triage_row_is_stamped_with_its_newest_message_not_now():
    latest = NOW - timedelta(minutes=47)
    inputs = TodayInputs(triage_counts={"total": 3, "noted": 1, "escalated": 0, "dismissed": 2,
                                        "latest_at": _iso(latest)})
    [row] = [d for d in compose_today(inputs, now=NOW)["did"] if d["kind"] == "triage"]
    assert row["at"] == _iso(latest)


def test_what_i_did_has_one_window_and_says_where_it_starts():
    """Since local midnight or 18 h ago, whichever is earlier: at 8:41 pm the
    day so far; at 7 am the evening before too. Every row carries ``at``."""
    chicago_night = datetime(2026, 9, 6, 1, 41, tzinfo=timezone.utc)   # 8:41 pm Sat, Springfield
    chicago_morning = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)  # 7:00 am Sun, Springfield
    night = compose_today(TodayInputs(), now=chicago_night, tz_name="America/Chicago")
    assert night["did_since"] == _iso(datetime(2026, 9, 5, 5, 0, tzinfo=timezone.utc))  # local midnight
    morning = compose_today(TodayInputs(), now=chicago_morning, tz_name="America/Chicago")
    assert morning["did_since"] == _iso(chicago_morning - timedelta(hours=18))

    old = _iso(chicago_morning - timedelta(hours=20))
    fresh = _iso(chicago_morning - timedelta(hours=2))
    inputs = TodayInputs(
        wishes=[{"id": "w-old", "title": "old", "why": "", "created_at": old},
                {"id": "w-new", "title": "new", "why": "", "created_at": fresh}],
        notes=[{"id": "n-old", "kind": "mind", "title": "old note", "body": "", "needs_you": False,
                "created_at": old, "expires_at": fresh},
               {"id": "n-new", "kind": "mind", "title": "new note", "body": "", "needs_you": False,
                "created_at": fresh, "expires_at": _iso(chicago_morning + timedelta(hours=9))}],
        actions=[{"ledger_id": 1, "thought_id": "t1", "title": "Replied to Parker", "first_line": "x", "at": old}],
    )
    did = compose_today(inputs, now=chicago_morning, tz_name="America/Chicago")["did"]
    assert {d["id"] for d in did} == {"wish:w-new", "note:n-new"}
    assert all(isinstance(d.get("at"), str) and d["at"].endswith("Z") for d in did)


# --- presence -----------------------------------------------------------------

_STATUS = {
    "running": True, "enabled": True, "last_triage_at": "2026-09-05T20:39:00-05:00",
    "tracks": {"imessage": {"enabled": True}, "calendar": {"enabled": True},
               "email": {"enabled": False}, "gmail": {"enabled": False}},
    "reply_affinity_enabled": True, "contact_reply_prior_window_days": 30,
}


def test_presence_says_what_edward_is_listening_to_and_when_his_mind_last_woke():
    inputs = TodayInputs(
        status=_STATUS,
        wakes=[{"id": 9, "at": _iso(NOW - timedelta(minutes=12)), "source": "messages", "outcome": "left_alone",
                "note": "Parker's thread is handled — Ellis answered."}],
        events=[
            {"id": "t-eve", "description": "evening", "next_fire_at": _iso(NOW + timedelta(hours=20)),
             "recurrence_pattern": "0 18 * * *", "status": "pending", "type": "wakeup_tick", "kind": "evening"},
            {"id": "t-self", "description": "check flight", "next_fire_at": _iso(NOW + timedelta(hours=2)),
             "recurrence_pattern": None, "status": "pending", "type": "mind_wake"},
            {"id": "e-owner", "description": "Call the vet", "next_fire_at": _iso(NOW + timedelta(hours=1)),
             "recurrence_pattern": None, "status": "pending", "type": None},
        ],
    )
    p = compose_today(inputs, now=NOW)["presence"]
    # An old status that still carries a calendar track: the track is gone
    # (2026-10-11), so it is never listed as something Edward listens to.
    assert p["listening"] == ["imessage"]
    assert p["last_look_at"] == "2026-09-06T01:39:00Z"
    m = p["mind"]
    assert m["last_wake_at"] == _iso(NOW - timedelta(minutes=12))
    assert m["last_reason"] == "messages"
    assert m["last_outcome"] == "left_alone"
    assert m["last_line"] == "Parker's thread is handled — Ellis answered."
    assert m["next_wake_at"] == _iso(NOW + timedelta(hours=2))
    assert m["next_wake_reason"] == "self"


def test_presence_is_honest_when_the_heartbeat_is_down_or_unknown():
    down = compose_today(TodayInputs(status={**_STATUS, "running": False}), now=NOW)["presence"]
    assert down["listening"] == []
    unknown = compose_today(TodayInputs(), now=NOW)["presence"]
    assert unknown["listening"] is None
    assert unknown["mind"]["last_wake_at"] is None and unknown["mind"]["next_wake_at"] is None


def test_the_payload_no_longer_carries_the_raw_heartbeat_config():
    out = compose_today(TodayInputs(status=_STATUS), now=NOW)
    assert out["status"] == {"running": True, "last_triage_at": "2026-09-06T01:39:00Z"}


def test_dismissed_questions_fold_into_one_did_row_not_seven():
    """"Done" expires a note now, so it becomes history: seven dismissed
    "Which is right?" cards must not come back as seven "What I did" rows."""
    inputs = TodayInputs(notes=[
        {**_note(f"q{i}", "Which is right?", f"question {i}", minutes_ago=i + 1),
         "expires_at": _iso(NOW - timedelta(seconds=30))}
        for i in range(7)
    ] + [_note("plain", "Parker landed", "On time.", needs_you=False, minutes_ago=20)])
    did = compose_today(inputs, now=NOW)["did"]
    rows = [d for d in did if d["kind"] == "note"]
    assert [d["title"] for d in rows] == ["Which is right?", "Parker landed"]
    assert rows[0]["id"] == "note:q0" and rows[0]["count"] == 7
    assert rows[0]["detail"] == "question 0"
    assert rows[1].get("count", 1) == 1


def test_a_calendar_event_the_mind_path_escalated_is_still_an_event_need():
    """With ``mind_enabled`` every surviving event is ``escalated`` —
    calendar ones too, in the listener's plain "Title — start to end" form.
    A calendar row is an event whatever its status, never "needs a reply"."""
    start, end = NOW + timedelta(hours=2), NOW + timedelta(hours=3)
    inputs = TodayInputs(heartbeat=[
        {"id": "cal2", "source": "calendar", "sender": None, "contact_name": None,
         "summary": f"Dentist — {start.isoformat()} to {end.isoformat()}",
         "triage_status": "escalated", "created_at": _iso(NOW - timedelta(minutes=3)),
         "conversation_id": None, "chat_identifier": None},
        {"id": "msg", "source": "imessage", "contact_name": "Jordan", "summary": "?", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=4)), "conversation_id": "hb", "chat_identifier": "+1"},
    ])
    needs = compose_today(inputs, now=NOW)["needs"]
    by_kind = {n["kind"]: n for n in needs}
    assert set(by_kind) == {"calendar", "heartbeat"}
    assert by_kind["calendar"]["title"] == "Dentist in 2h"
    assert by_kind["calendar"]["id"] == "calendar:cal2"
    assert by_kind["heartbeat"]["title"] == "Jordan needs a reply"


def test_calendar_summaries_parse_in_both_listener_forms():
    from services.today_service import calendar_title_and_start

    s = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    assert calendar_title_and_start(f"[STARTING SOON] Standup — starts at {s.isoformat()}") == ("Standup", s)
    assert calendar_title_and_start(f"Standup — {s.isoformat()} to {(s + timedelta(hours=1)).isoformat()}") == ("Standup", s)
    assert calendar_title_and_start("Lunch — with Parker") == ("Lunch — with Parker", None)
    assert calendar_title_and_start("") == ("Calendar event", None)


def test_an_escalated_email_says_it_is_an_email_not_a_reply():
    """The mind path escalates email too (the listener's row: no contact,
    no thread, summary "From <sender>: <subject>"). "frontdesk@… needs a
    reply" read like a text; an email card says who emailed and about what,
    with its own icon kind (2026-09-26)."""
    inputs = TodayInputs(heartbeat=[
        {"id": "em1", "source": "email", "sender": "frontdesk@vet.example", "contact_name": None,
         "chat_name": None, "summary": "From frontdesk@vet.example: Biscuit's bloodwork results",
         "triage_status": "escalated", "created_at": _iso(NOW - timedelta(minutes=12)),
         "conversation_id": None, "chat_identifier": None},
        {"id": "gm1", "source": "gmail", "sender": "Dana Whitfield <dana@example.com>", "contact_name": None,
         "chat_name": None, "summary": "From Dana Whitfield <dana@example.com>: (no subject)",
         "triage_status": "escalated", "created_at": _iso(NOW - timedelta(minutes=20)),
         "conversation_id": None, "chat_identifier": None},
        {"id": "msg", "source": "imessage", "contact_name": "Jordan", "summary": "?", "triage_status": "escalated",
         "created_at": _iso(NOW - timedelta(minutes=4)), "conversation_id": "hb", "chat_identifier": "+1"},
    ])
    needs = {n["id"]: n for n in compose_today(inputs, now=NOW)["needs"]}
    mail = needs["heartbeat:em1"]
    assert mail["kind"] == "email"
    assert mail["title"] == "frontdesk@vet.example emailed you: Biscuit's bloodwork results"
    assert "reply" not in mail["title"].lower()
    assert any(a["id"] == "done" and a["url"] == "/api/today/heartbeat/em1/done" for a in mail["actions"])
    assert mail["detail"] == "", "the subject is the title; the summary would only repeat it"

    gmail = needs["heartbeat:gm1"]
    assert gmail["kind"] == "email"
    assert gmail["title"] == "Dana Whitfield emailed you", "a display name wins; no subject, no colon"

    assert needs["heartbeat:msg"]["kind"] == "heartbeat"
    assert needs["heartbeat:msg"]["title"] == "Jordan needs a reply"


def test_a_long_email_subject_is_clipped_on_the_card():
    subject = "Re: " + "the quarterly planning offsite agenda and travel " * 5
    inputs = TodayInputs(heartbeat=[
        {"id": "em2", "source": "email", "sender": "parker@example.com", "summary": f"From parker@example.com: {subject}"[:200],
         "triage_status": "escalated", "created_at": _iso(NOW - timedelta(minutes=2)),
         "conversation_id": None, "chat_identifier": None},
    ])
    [need] = compose_today(inputs, now=NOW)["needs"]
    assert need["title"].startswith("parker@example.com emailed you: Re: the quarterly planning")
    assert len(need["title"]) <= 140 and need["title"].endswith("…")
