"""``speak_gate.decide`` — the pure gate between noticing and speaking.

Why (proactivity-spec §2.5, 2026-10-11): the noticer is cheap and says what
it saw; whether that reaches Ellis is decided here, in code, from facts — has
Ellis already replied there, is he talking there right now, has the read state
had time to settle, is it too early, too late, already said, over budget,
inside quiet hours. F1–F24 are the production cases (names and numbers
invented) the evidence condemned or praised. First match wins; every case
names the rule it pins.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services import speak_gate as g

pytestmark = pytest.mark.unit

TZ = ZoneInfo("America/Chicago")


def at(h, m=0, day=3):
    return datetime(2026, 10, day, h, m, tzinfo=TZ)


def src(ref, minute_or_dt, chat="chat-a", from_me=False, read_at=None):
    t = minute_or_dt if isinstance(minute_or_dt, datetime) else at(15, minute_or_dt)
    return g.Source(ref=ref, chat=chat, at=t, from_me=from_me, read_at=read_at)


def noticing(**kw):
    base = dict(id="n1", kind="deadline", category="work", claim="c", sources=(src("hb:1", at(14, 0)),),
                chat="chat-a", who=("X",), closes_at=None, suggested="note", dedupe_key="k1", sensitive=False,
                origin="noticer")
    base.update(kw)
    return g.Noticing(**base)


def ctx(now=None, **kw):
    base = dict(now=now or at(15, 0), stood_down=frozenset(), busy_chats=frozenset(), owner_last_line={},
                live_chats=frozenset(), open_ping_chats=frozenset(), history=(), pushes_today=0, speaks_today=0,
                speaks_by_chat_1h={}, push_scope="gate", quota="ok")
    base.update(kw)
    return g.GateContext(**base)


def surfaced(category="work", channel="push", outcome=None, at_=None, dedupe_key="other", chat="chat-z",
             kind="deadline", refs=("hb:99",)):
    return g.Surfaced(category=category, channel=channel, outcome=outcome, at=at_ or at(9, 0), dedupe_key=dedupe_key,
                      chat=chat, kind=kind, refs=frozenset(refs))


# ------------------------------------------------------------- F1–F24 --

def test_f1_a_money_ask_in_a_live_one_to_one_is_silent():
    n = noticing(kind="unanswered", category="money", sources=(src("hb:1", at(14, 30), chat="m"),), chat="m",
                 suggested="note")
    d = g.decide(n, ctx(now=at(15, 0), live_chats=frozenset({"m"})))
    assert d.channel == "silent" and "talking there" in d.reason


def test_f2_a_vendor_relay_with_no_deadline_is_silent():
    n = noticing(kind="household", category="household", sources=(src("hb:2", at(11, 0), chat="vendor"),),
                 chat="vendor", suggested="ambient", closes_at=None)
    d = g.decide(n, ctx(now=at(11, 30)))
    assert d.channel == "silent" and "relay" in d.reason


def flight_checkin(**kw):
    base = dict(id="fl", kind="conflict", category="travel",
                sources=(src("hb:812", at(21, 31), chat="airline"), src("hb:815", at(21, 40), chat="home", from_me=True)),
                chat="airline", who=("Airline",), closes_at=at(22, 0), suggested="push", dedupe_key="XY123-checkin")
    base.update(kw)
    return noticing(**base)


def test_f3_a_flight_check_in_against_en_route_pushes_without_settling():
    d = g.decide(flight_checkin(), ctx(now=at(21, 47), push_scope="floor"))
    assert d.channel == "push" and d.floor_push is True


def test_f4_a_credential_in_owners_own_line_is_a_note():
    n = noticing(kind="security", category="security", origin="code",
                 sources=(src("hb:7", at(15, 7), chat="work", from_me=True),), chat="work", suggested="push")
    d = g.decide(n, ctx(now=at(15, 46), owner_last_line={"work": at(15, 7)}))
    assert d.channel == "note"


def test_f5_the_same_key_said_two_hours_ago_with_nothing_new_is_silent():
    n = noticing(sources=(src("hb:1", at(12, 0)),), suggested="note", dedupe_key="k1")
    history = (surfaced(dedupe_key="k1", channel="note", at_=at(13, 0), refs=("hb:1",)),)
    d = g.decide(n, ctx(now=at(15, 0), history=history))
    assert d.channel == "silent" and "said already" in d.reason


def test_f5b_the_same_key_with_a_new_source_speaks_again():
    n = noticing(sources=(src("hb:1", at(12, 0)), src("hb:2", at(14, 0))), suggested="note", dedupe_key="k1")
    history = (surfaced(dedupe_key="k1", channel="note", at_=at(13, 0), refs=("hb:1",)),)
    assert g.decide(n, ctx(now=at(15, 0), history=history)).channel == "note"


def test_f6_family_after_two_ignored_pushes_is_capped_at_note():
    history = (surfaced(category="family", outcome="acted", channel="note", at_=at(9, 0, day=1)),
               surfaced(category="family", outcome="ignored", channel="push", at_=at(9, 0, day=2)),
               surfaced(category="family", outcome="ignored", channel="push", at_=at(10, 0, day=2)))
    n = noticing(kind="unanswered", category="family", sources=(src("hb:1", at(8, 0)),), suggested="push")
    d = g.decide(n, ctx(now=at(15, 0), history=history))
    assert d.channel == "note"


def test_f7_the_third_counted_push_is_a_note_when_a_budget_is_set():
    d = g.decide(flight_checkin(), ctx(now=at(21, 47), pushes_today=2, push_budget=2))
    assert d.channel == "note" and "budget" in d.reason


def test_f7_no_budget_means_no_push_limit():
    # Ellis's answers #2 (2026-10-11): "push, with no limit".
    d = g.decide(flight_checkin(), ctx(now=at(21, 47), pushes_today=50))
    assert d.channel == "push"


def test_f7b_a_household_item_past_the_budget_also_goes_ambient():
    n = noticing(kind="household", category="household", sources=(src("hb:1", at(14, 0), chat="v"),), chat="v",
                 closes_at=at(16, 0), suggested="push")
    d = g.decide(n, ctx(now=at(15, 0), pushes_today=2, push_budget=2))
    assert d.channel == "note" and "ambient" in d.also


def test_f8_a_push_at_2330_for_tomorrow_afternoon_holds_until_seven():
    history = (surfaced(category="money", outcome="acted", channel="note", at_=at(9, 0, day=2)),)
    n = noticing(kind="deadline", category="money", sources=(src("hb:1", at(20, 0)),), suggested="push",
                 closes_at=at(17, 0, day=4))
    d = g.decide(n, ctx(now=at(23, 30), history=history, push_scope="gate"))
    assert d.channel == "hold" and d.hold_until == at(7, 0, day=4)


def test_f9_a_stood_down_chat_is_silent():
    d = g.decide(flight_checkin(), ctx(now=at(21, 47), stood_down=frozenset({"airline"})))
    assert d.channel == "silent" and "stand-down" in d.reason


def test_f10_owner_replied_there_four_minutes_after_the_source():
    n = noticing(kind="unanswered", sources=(src("hb:1", at(14, 0), chat="t"),), chat="t")
    d = g.decide(n, ctx(now=at(15, 0), owner_last_line={"t": at(14, 4)}))
    assert d.channel == "silent" and "Ellis replied there at 2:04 pm" in d.reason


def follow_through(**kw):
    base = dict(kind="follow_through", category="social", sources=(src("hb:1", at(14, 0), chat="group"),),
                chat="group", suggested="thread", closes_at=at(18, 0))
    base.update(kw)
    return noticing(**base)


def test_f11_follow_through_in_a_chat_that_asked_replies_there():
    d = g.decide(follow_through(), ctx(now=at(17, 30), open_ping_chats=frozenset({"group"})))
    assert d.channel == "thread"


def test_f11b_follow_through_where_nobody_asked_is_at_most_a_push():
    d = g.decide(follow_through(), ctx(now=at(17, 30)))
    assert d.channel == "push"
    assert g.decide(follow_through(), ctx(now=at(17, 30), push_scope="floor")).channel == "note"


def test_the_ladder_is_advice_not_a_block():
    # Ellis's answers #2: learning from being ignored is judgment input for the
    # speaker, never a hard block.
    history = (surfaced(category="family", outcome="acted", channel="note", at_=at(9, 0, day=1)),
               surfaced(category="family", outcome="ignored", channel="push", at_=at(9, 0, day=2)),
               surfaced(category="family", outcome="ignored", channel="push", at_=at(10, 0, day=2)))
    n = noticing(kind="deadline", category="family", sources=(src("hb:1", at(13, 0)),), suggested="push",
                 closes_at=at(17, 0))
    d = g.decide(n, ctx(now=at(15, 0), history=history))
    assert d.channel == "push"
    assert "family" in d.advice and "note" in d.advice


def test_owners_own_words_still_bind():
    n = noticing(kind="deadline", category="family", sources=(src("hb:1", at(13, 0)),), suggested="push",
                 closes_at=at(17, 0))
    d = g.decide(n, ctx(now=at(15, 0), ceilings_from_owner={"family": "note"}))
    assert d.channel == "note"


def parker(age_h, **kw):
    now = at(15, 0)
    sent = now - timedelta(hours=age_h)
    base = dict(kind="unanswered", category="work", who=("Parker",), chat="parker", suggested="note",
                sources=(src("hb:9", sent, chat="parker", read_at=sent + timedelta(minutes=2)),))
    base.update(kw)
    return noticing(**base), now


def test_f12_parker_read_and_unanswered_for_five_hours_is_a_note():
    n, now = parker(5)
    d = g.decide(n, ctx(now=now))
    assert d.channel == "note"


def test_f13_parker_at_one_hour_holds_until_four():
    n, now = parker(1)
    d = g.decide(n, ctx(now=now))
    assert d.channel == "hold" and d.hold_until == now - timedelta(hours=1) + timedelta(hours=4)


def test_f14_parker_but_owner_replied_three_hours_ago_is_silent():
    n, now = parker(5)
    d = g.decide(n, ctx(now=now, owner_last_line={"parker": now - timedelta(hours=3)}))
    assert d.channel == "silent"


def test_f15_travel_closing_before_dawn_pushes_at_night():
    n = noticing(kind="deadline", category="travel", sources=(src("hb:1", at(1, 0, day=4)),), suggested="push",
                 closes_at=at(5, 0, day=4))
    d = g.decide(n, ctx(now=at(2, 40, day=4), push_scope="floor"))
    assert d.channel == "push" and d.floor_push


def test_f16_precision_first_keeps_a_work_push_a_note():
    history = (surfaced(category="work", outcome="acted", channel="note", at_=at(9, 0, day=2)),)
    n = noticing(kind="deadline", category="work", sources=(src("hb:1", at(13, 0)),), suggested="push",
                 closes_at=at(17, 0))
    d = g.decide(n, ctx(now=at(15, 0), push_scope="floor", history=history))
    assert d.channel == "note" and "floor" in d.reason


def test_f17_a_conflict_with_one_source_is_dropped():
    n = flight_checkin(sources=(src("hb:812", at(21, 31), chat="airline"),))
    assert g.decide(n, ctx(now=at(21, 47))).channel == "dropped"


def test_f17b_two_sources_with_the_same_ref_are_one():
    n = flight_checkin(sources=(src("hb:812", at(21, 31), chat="airline"), src("hb:812", at(21, 31), chat="airline")))
    assert g.decide(n, ctx(now=at(21, 47))).channel == "dropped"


def test_f18_closes_at_passed_is_too_late():
    d = g.decide(flight_checkin(closes_at=at(21, 45)), ctx(now=at(21, 47)))
    assert d.channel == "silent" and "too late" in d.reason


def test_f19_a_fresh_household_delivery_settles_fifteen_minutes():
    arrived = at(14, 59)
    n = noticing(kind="household", category="household", sources=(src("hb:1", arrived, chat="v"),), chat="v",
                 closes_at=at(17, 0), suggested="ambient")
    d = g.decide(n, ctx(now=at(15, 0)))
    assert d.channel == "hold" and d.hold_until == arrived + timedelta(minutes=15)


def test_f20_speak_budget_used_is_journal():
    n, now = parker(5)
    d = g.decide(n, ctx(now=now, speaks_today=8))
    assert d.channel == "journal" and "speak budget" in d.reason


def test_f20b_a_floor_item_past_the_speak_budget_is_the_templated_fallback():
    d = g.decide(flight_checkin(), ctx(now=at(21, 47), speaks_today=8))
    assert d.channel == "push" and d.is_fallback


def test_f20c_one_speak_per_chat_per_hour():
    n, now = parker(5)
    d = g.decide(n, ctx(now=now, speaks_by_chat_1h={"parker": 1}))
    assert d.channel == "journal"


def test_f21_quota_exhausted_is_journal():
    n, now = parker(5)
    d = g.decide(n, ctx(now=now, quota="exhausted"))
    assert d.channel == "journal" and "spend cap" in d.reason


def test_f21b_quota_exhausted_floor_item_is_the_fallback_push():
    d = g.decide(flight_checkin(), ctx(now=at(21, 47), quota="exhausted"))
    assert d.channel == "push" and d.is_fallback


def test_f21c_paced_lets_only_floor_items_through():
    n, now = parker(5)
    assert g.decide(n, ctx(now=now, quota="paced")).channel == "journal"
    assert g.decide(flight_checkin(), ctx(now=at(21, 47), quota="paced")).channel == "push"


def test_f22_a_ping_open_in_the_chat_holds_ten_minutes():
    now = at(21, 47)
    d = g.decide(flight_checkin(), ctx(now=now, busy_chats=frozenset({"airline"})))
    assert d.channel == "hold" and d.hold_until == now + timedelta(minutes=10)


def test_f23_a_sensitive_health_item_suggested_ambient_is_a_note():
    n = noticing(kind="household", category="health", sensitive=True, sources=(src("hb:1", at(13, 0), chat="v"),),
                 chat="v", closes_at=at(16, 0), suggested="ambient")
    d = g.decide(n, ctx(now=at(15, 0)))
    assert d.channel == "note"


def test_f24_security_at_2330_is_a_note_never_a_push():
    n = noticing(kind="security", category="security", origin="code", suggested="push",
                 sources=(src("hb:7", at(23, 25), chat="work", from_me=True),), chat="work")
    d = g.decide(n, ctx(now=at(23, 30)))
    assert d.channel == "note"


# ---------------------------------------------------------- the rules --

def test_chat_keys_match_any_spelling():
    n = flight_checkin(chat="iMessage;-;+12015550100")
    d = g.decide(n, ctx(now=at(21, 47), stood_down=frozenset({"+1 (201) 555-0100"})))
    assert d.channel == "silent"


def test_r7_holds_until_the_lead():
    n = noticing(kind="deadline", category="travel", sources=(src("hb:1", at(9, 0)),), suggested="push",
                 closes_at=at(20, 0))
    d = g.decide(n, ctx(now=at(10, 0)))
    assert d.channel == "hold" and d.hold_until == at(17, 0)


@pytest.mark.parametrize("category,kind,lead_h", [
    ("travel", "deadline", 3), ("household", "deadline", 1), ("work", "conflict", 3),
    ("work", "follow_through", 1), ("work", "deadline", 24), ("work", "unanswered", 3),
    ("household", "conflict", 1),
])
def test_lead_for_category_wins_over_kind(category, kind, lead_h):
    assert g.lead_for(noticing(category=category, kind=kind)) == timedelta(hours=lead_h)


def test_floor_push_is_travel_or_household_closing_within_three_hours():
    now = at(15, 0)
    assert g.is_floor_push(noticing(category="travel", closes_at=at(17, 0)), now)
    assert not g.is_floor_push(noticing(category="travel", closes_at=at(19, 0)), now)
    assert not g.is_floor_push(noticing(category="work", closes_at=at(16, 0)), now)
    assert not g.is_floor_push(noticing(category="household", closes_at=None), now)


def test_r11_a_floor_item_closing_after_dawn_is_held_or_noted():
    # Closing at 08:00: after 07:00 + 30 min → hold until 07:00.
    n = noticing(kind="deadline", category="travel", sources=(src("hb:1", at(1, 0, day=4)),), suggested="push",
                 closes_at=at(8, 0, day=4))
    d = g.decide(n, ctx(now=at(6, 0, day=4)))
    assert d.channel == "hold" and d.hold_until == at(7, 0, day=4)
    # Closing at 07:15: after 07:00 but within the half hour → a note.
    n2 = replace(n, closes_at=at(7, 15, day=4))
    assert g.decide(n2, ctx(now=at(6, 0, day=4))).channel == "note"


def test_r4_owner_wrote_within_ten_minutes_before_the_source():
    n = noticing(kind="unanswered", sources=(src("hb:1", at(10, 0), chat="t"),), chat="t")
    d = g.decide(n, ctx(now=at(15, 0), owner_last_line={"t": at(9, 55)}))
    assert d.channel == "silent"


def test_r4_does_not_silence_follow_through():
    d = g.decide(follow_through(), ctx(now=at(17, 30), open_ping_chats=frozenset({"group"}),
                                       owner_last_line={"group": at(17, 0)}))
    assert d.channel == "thread"


def test_r6_a_deadline_with_no_close_is_capped_at_note():
    history = (surfaced(category="money", outcome="acted", channel="note", at_=at(9, 0, day=2)),)
    n = noticing(kind="deadline", category="money", sources=(src("hb:1", at(13, 0)),), suggested="push",
                 closes_at=None)
    assert g.decide(n, ctx(now=at(15, 0), history=history)).channel == "note"


def test_r8_the_same_chat_and_kind_with_overlapping_refs_is_said_already():
    n = noticing(kind="unanswered", sources=(src("hb:5", at(9, 0), chat="t"),), chat="t", dedupe_key="new-key")
    history = (surfaced(dedupe_key="old-key", chat="t", kind="unanswered", channel="note", at_=at(14, 0),
                        refs=("hb:5", "hb:4")),)
    d = g.decide(n, ctx(now=at(15, 0), history=history))
    assert d.channel == "silent" and "said already" in d.reason


def test_ambient_is_household_only():
    n = noticing(kind="deadline", category="work", sources=(src("hb:1", at(13, 0)),), suggested="ambient",
                 closes_at=at(16, 0))
    assert g.decide(n, ctx(now=at(15, 0))).channel == "note"


def test_household_ambient_passes():
    n = noticing(kind="household", category="household", sources=(src("hb:1", at(13, 0), chat="v"),), chat="v",
                 closes_at=at(15, 50), suggested="ambient")
    assert g.decide(n, ctx(now=at(15, 0))).channel == "ambient"


# ------------------------------------------------------------ the ladder --

def test_ladder_starts_travel_and_household_at_push_others_at_note():
    now = at(15, 0)
    assert g.channel_ceiling("travel", (), now) == "push"
    assert g.channel_ceiling("household", (), now) == "push"
    assert g.channel_ceiling("work", (), now) == "note"
    assert g.channel_ceiling("security", (), now) == "note"


def test_ladder_two_ignored_pushes_demote_to_note():
    h = (surfaced(category="travel", outcome="ignored", channel="push", at_=at(9, 0, day=1)),
         surfaced(category="travel", outcome="ignored", channel="push", at_=at(9, 0, day=2)))
    assert g.channel_ceiling("travel", h, at(15, 0, day=3)) == "note"


def test_ladder_ignored_notes_never_demote():
    h = tuple(surfaced(category="work", outcome="ignored", channel="note", at_=at(9, 0, day=d)) for d in (1, 2, 3))
    assert g.channel_ceiling("work", h, at(15, 0, day=3)) == "note"


def test_ladder_two_dismissed_notes_demote_to_journal():
    h = (surfaced(category="work", outcome="dismissed", channel="note", at_=at(9, 0, day=1)),
         surfaced(category="work", outcome="dismissed", channel="note", at_=at(9, 0, day=2)))
    assert g.channel_ceiling("work", h, at(15, 0, day=3)) == "journal"


def test_ladder_a_rejection_lowers_one_level():
    h = (surfaced(category="travel", outcome="rejected", channel="push", at_=at(9, 0, day=1)),)
    assert g.channel_ceiling("travel", h, at(15, 0, day=2)) == "note"


def test_ladder_acted_raises_one_level_and_resets():
    h = (surfaced(category="work", outcome="acted", channel="note", at_=at(9, 0, day=1)),)
    assert g.channel_ceiling("work", h, at(15, 0, day=2)) == "push"


def test_ladder_seen_is_neutral():
    h = (surfaced(category="travel", outcome="ignored", channel="push", at_=at(9, 0, day=1)),
         surfaced(category="travel", outcome="seen", channel="push", at_=at(9, 0, day=2)),
         surfaced(category="travel", outcome="ignored", channel="push", at_=at(10, 0, day=2)))
    assert g.channel_ceiling("travel", h, at(15, 0, day=3)) == "note"


def test_ladder_probation_restores_after_seven_quiet_days():
    h = (surfaced(category="travel", outcome="ignored", channel="push", at_=at(9, 0, day=1)),
         surfaced(category="travel", outcome="ignored", channel="push", at_=at(9, 0, day=2)))
    assert g.channel_ceiling("travel", h, at(9, 0, day=8)) == "note"
    assert g.channel_ceiling("travel", h, at(9, 1, day=9)) == "push"


def test_ladder_security_is_pinned():
    h = (surfaced(category="security", outcome="acted", channel="note", at_=at(9, 0, day=1)),)
    assert g.channel_ceiling("security", h, at(15, 0, day=2)) == "note"


def test_ceilings_from_owner_override_the_fold():
    n = noticing(kind="unanswered", category="social", sources=(src("hb:1", at(8, 0)),), suggested="push")
    d = g.decide(n, ctx(now=at(15, 0), ceilings_from_owner={"social": "journal"}))
    assert d.channel == "journal"


@pytest.mark.parametrize("line,expected", [
    ("@Edward push me about travel", {"travel": "push"}),
    ("@edward text me about anything money", {"money": "push"}),
    ("@Edward stop pushing me about soccer stuff — family is fine", {}),
    ("@Edward don't push me about work", {"work": "note"}),
    ("@Edward leave family alone", {"family": "journal"}),
    ("@Edward stop telling me about social stuff", {"social": "journal"}),
    ("what time is dinner", {}),
])
def test_ceilings_from_owner_grammar(line, expected):
    assert g.ceilings_from_owner([line]) == expected


# ---- F25: night pushes are travel's only (Ellis's answer #2, 2026-10-11) ----
#
# "Keep quiet hours (22:00–07:00) except for items that cannot wait (care
# alerts, travel closing within 3 h)". The floor is travel *and* household
# closing within 3 h, and R11 (and the templated fallback of R0 and R13) let
# any floor item push at night — so a courier's gate-code text at 23:10
# buzzed Ellis's phone (review 2026-10-11). At night a household item is a
# note, with the household screen when it isn't sensitive.

def gate_code(**kw):
    base = dict(id="gc", kind="household", category="household",
                sources=(src("hb:1", at(22, 50), chat="courier"),), chat="courier", who=("Courier",),
                closes_at=at(23, 55), suggested="push", dedupe_key="gate-code")
    base.update(kw)
    return noticing(**base)


def test_f25_a_household_floor_item_at_night_is_a_note_not_a_push():
    d = g.decide(gate_code(), ctx(now=at(23, 10), push_scope="gate"))
    assert d.channel == "note" and "quiet hours" in d.reason
    assert "ambient" in d.also
    assert g.decide(gate_code(sensitive=True), ctx(now=at(23, 10), push_scope="gate")).also == ()


def test_f25b_at_night_a_household_floor_item_never_takes_the_fallback_push():
    for c in (ctx(now=at(23, 10), quota="exhausted"), ctx(now=at(23, 10), speaks_today=8)):
        d = g.decide(gate_code(), c)
        assert d.channel != "push" and not d.is_fallback, d


def test_f25c_travel_closing_tonight_still_pushes_at_night():
    n = flight_checkin(sources=(src("hb:812", at(22, 31), chat="airline"), src("hb:815", at(22, 40), chat="home", from_me=True)),
              closes_at=at(23, 30))
    assert g.decide(n, ctx(now=at(23, 0))).channel == "push"
    assert g.decide(n, ctx(now=at(23, 0), quota="exhausted")).is_fallback


def test_f25d_by_day_the_household_floor_still_pushes():
    d = g.decide(gate_code(sources=(src("hb:1", at(14, 30), chat="courier"),), closes_at=at(16, 0)),
                 ctx(now=at(15, 0), push_scope="floor"))
    assert d.channel == "push" and d.floor_push
