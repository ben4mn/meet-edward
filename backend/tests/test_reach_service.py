"""``reach_service.tell_owner`` — the one way Edward reaches Ellis.

Why (proactivity-spec §2.7, §4, 2026-10-11): twelve code paths used to ring
Ellis's phone and none could see what the others had said; 0 of 33 pushes
were opened. Every reach now goes through one door that holds, in code:
where it came from (the origin table), the egress secret scan, the high-risk
gate, the offer grammar, one follow-up per thing, the push budget and quiet
hours. A downgrade is a success with the reason; a refusal is a
``Tool error:`` the model can act on. Nothing here is a real send: the push
transport is faked at ``push_service.send_push_notification``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from services import reach_service

pytestmark = pytest.mark.integration

GOOD = ("BA's 9:31 pm text says check-in closes at 10; you said you're en route. "
        "Want me to open online check-in? (not done)")
# 15:00 in Springfield — the middle of Ellis's day.
DAY = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)


class FakePush:
    """Records the call and writes the ledger row a real send would."""

    def __init__(self, sent=1):
        self.calls = []
        self.sent = sent

    async def __call__(self, title, body, url=None, tag=None, icon=None, target=None, delivery_id=None,
                       source="unknown", conversation_id=None, respect_quiet_hours=False, approval_id=None):
        import uuid

        from services.database import PushLogModel, async_session

        self.calls.append({"title": title, "body": body, "url": url, "source": source,
                           "respect_quiet_hours": respect_quiet_hours})
        lid = str(uuid.uuid4())
        sent_at = reach_service._now().astimezone(timezone.utc).replace(tzinfo=None)
        async with async_session() as s:   # the row a real send writes, on the test's clock
            s.add(PushLogModel(id=lid, source=source, title=title, body=body, url=url, tag=tag,
                               conversation_id=conversation_id, sent=self.sent, failed=0, sent_at=sent_at))
            await s.commit()
        return {"sent": self.sent, "failed": 0, "total": 1, "ledger_id": lid}


@pytest.fixture
def push(monkeypatch):
    from services import push_service, timezone_utils

    fake = FakePush()
    monkeypatch.setattr(push_service, "send_push_notification", fake)
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setattr(reach_service, "_now", lambda: DAY)
    monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
    monkeypatch.delenv("EDWARD_EGRESS_SECRET_SCAN", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    return fake


async def _notes():
    from services.database import EdwardNoteModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(EdwardNoteModel).order_by(EdwardNoteModel.created_at))).scalars())


async def _reach_rows():
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "reach")
                                     .order_by(LedgerEventModel.id))).scalars())


# ----------------------------------------------------- origin, derived --

@pytest.mark.unit
@pytest.mark.parametrize("kw,expected", [
    (dict(explicit="care"), "care"),
    (dict(explicit="ping:owner"), "ping:owner"),
    (dict(explicit="nonsense!"), "other"),
    (dict(scope=True, purpose="mind:speak"), "speak"),
    (dict(policy_owner_asked=True, purpose="mind:self"), "owner"),
    (dict(ping_third_party=False), "owner"),
    (dict(ping_third_party=True), "ping"),
    (dict(purpose="mind:self"), "self"),
    (dict(purpose="mind:nightly"), "nightly"),
    (dict(purpose="mind:mention"), "mention"),
    (dict(purpose="mind:manual"), "manual"),
    (dict(purpose="mind:speak"), "speak"),
    (dict(purpose="mind:surfaces"), "surfaces"),
    (dict(purpose="mind:notice"), "other"),
    (dict(purpose="pulse"), "surfaces"),
    (dict(purpose="surplus:free_time"), "surfaces"),
    (dict(turn_source="chat"), "owner"),
    (dict(turn_source="voice"), "owner"),
    (dict(turn_source="scheduled_event"), "scheduled_event"),
    (dict(turn_source="pulse"), "surfaces"),
    (dict(turn_source="worker"), "worker"),
    # The household hub's voice (2026-10-11): someone in Ellis's home, not
    # necessarily Ellis — its own origin, never "owner" and never refused.
    (dict(turn_source="hub_voice"), "hub"),
    (dict(policy_owner_asked=False, purpose="turn:hub_voice", turn_source="hub_voice"), "hub"),
    (dict(), "other"),
    # AREA 3's runner before the lead's one-line hook passes origin: its
    # notices are ping notices, held at night like a friend's.
    (dict(caller_module="services.pings.runner"), "ping:other"),
    (dict(caller_module="services.care_watch"), "other"),
    (dict(purpose="mind:self", caller_module="services.pings.runner"), "self"),
])
def test_derive_origin(kw, expected):
    assert reach_service.derive_origin(**kw) == expected


@pytest.mark.unit
def test_every_origin_has_a_rule_and_unknown_model_origins_get_the_counted_row():
    for origin in ("owner", "ping:owner", "ping:other", "care", "fallback", "speak", "self", "manual", "mention",
                   "scheduled_event", "nightly", "surfaces", "other", "ping", "worker"):
        assert reach_service.rule_for(origin) is not None
    assert reach_service.rule_for("messages") == reach_service.rule_for("self")
    assert reach_service.rule_for("other").refuse and reach_service.rule_for("surfaces").refuse


# ------------------------------------------------------- the origin table --

async def test_owner_asked_is_never_linted_and_pushes(clean_db, push):
    out = await reach_service.tell_owner("hi", title="Ping", urgent=True, origin="owner")
    assert out.startswith("Pushed to Ellis's phone")
    assert push.calls[0]["source"] == "reach:owner"
    assert push.calls[0]["respect_quiet_hours"] is False


async def test_a_self_wake_is_linted_and_nothing_goes_out_on_a_problem(clean_db, push):
    out = await reach_service.tell_owner("Hey! Just checking in — let me know if you need anything",
                                       urgent=True, origin="self")
    assert out.startswith("Tool error:")
    assert "greeting" in out and "one question" in out
    assert push.calls == [] and await _notes() == []
    rows = await _reach_rows()
    assert rows[-1].outcome == "refused" and rows[-1].source == "self"


async def test_a_self_note_lands_on_today_and_needs_owner_when_it_asks(clean_db, push):
    out = await reach_service.tell_owner(GOOD, title="UA958 check-in", origin="self")
    assert out.startswith("Left Ellis a note on Today")
    notes = await _notes()
    assert len(notes) == 1 and notes[0].kind == "mind" and notes[0].needs_you is True
    assert notes[0].url == "/today" and notes[0].body == GOOD
    assert push.calls == []
    assert (await _reach_rows())[-1].outcome == "note"


async def test_a_note_that_reports_is_not_held_to_the_offer_grammar(clean_db, push):
    """Review 2026-10-11: a follow-through's report has nothing to ask;
    the grammar is for can't-wait reach and what the speaker offers."""
    report = "I posted the $140 pair in Trivia crew at 6:58 pm, as you asked."
    assert (await reach_service.tell_owner(report, title="Trivia crew", origin="self")).startswith("Left Ellis a note")
    out = await reach_service.tell_owner("Parker says he is running 15 minutes late.", urgent=True, origin="self")
    assert out.startswith("Tool error:") and "one question" in out, "a push is still an offer"
    with reach_service.speak_scope(noticing_id=None, channel="note", who=("Parker",)):
        spoken = await reach_service.tell_owner("Parker says he is running 15 minutes late.")
    assert spoken.startswith("Tool error:") and "one question" in spoken, "the speaker always offers"
    assert len(await _notes()) == 1


@pytest.mark.parametrize("origin", ["surfaces", "other"])
async def test_the_surfaces_and_unknown_turns_cannot_reach_owner(clean_db, push, origin):
    out = await reach_service.tell_owner(GOOD, urgent=True, origin=origin)
    assert out == "Tool error: this turn can't reach Ellis"
    assert push.calls == [] and await _notes() == []


async def test_with_no_origin_and_no_context_the_call_is_refused(clean_db, push):
    out = await reach_service.tell_owner(GOOD, urgent=True)
    assert out == "Tool error: this turn can't reach Ellis"


async def test_origin_is_derived_from_the_wake_purpose(clean_db, push):
    from services.call_purpose import purpose_scope

    with purpose_scope("mind:self"):
        out = await reach_service.tell_owner(GOOD, title="UA958", urgent=True)
    assert out.startswith("Pushed")
    assert push.calls[0]["source"] == "reach:self"


# ----------------------------------------------------------- secrets --

KEY = "sk-proj-" + "Ab3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY"


async def test_a_secret_from_a_model_origin_is_refused(clean_db, push):
    out = await reach_service.tell_owner(f"BA's 9:31 pm text has the key {KEY} — rotate it?", origin="self")
    assert out.startswith("Tool error: that would put a secret on Ellis's lock screen")
    assert KEY not in out
    assert push.calls == [] and await _notes() == []


async def test_a_secret_from_a_code_origin_is_redacted_and_delivered(clean_db, push):
    out = await reach_service.tell_owner(f"Biscuit's dose note says {KEY}", title="Biscuit's 6:00 pm dose",
                                       urgent=True, origin="care")
    assert out.startswith("Pushed")
    assert KEY not in push.calls[0]["body"] and "[secret]" in push.calls[0]["body"]


async def test_the_secret_scan_switch_turns_it_off(clean_db, push, monkeypatch):
    monkeypatch.setenv("EDWARD_EGRESS_SECRET_SCAN", "off")
    out = await reach_service.tell_owner(f"BA's 9:31 pm text has {KEY} — rotate it?", origin="self")
    assert out.startswith("Left Ellis a note")


# --------------------------------------------------------- high risk --

async def test_high_risk_text_in_an_urgent_autonomous_reach_is_refused(clean_db, push):
    text = "M's 9:31 pm text says she wants to end it all. Call her now?"
    out = await reach_service.tell_owner(text, urgent=True, origin="self")
    assert out.startswith("Tool error:") and "reply directly" in out.lower() and "leave Ellis a note" in out
    assert push.calls == []


async def test_high_risk_text_may_still_be_a_note(clean_db, push):
    text = "M's 9:31 pm text says she wants to end it all. Call her now?"
    out = await reach_service.tell_owner(text, urgent=False, origin="self")
    assert out.startswith("Left Ellis a note")


async def test_owner_asked_high_risk_still_pushes(clean_db, push):
    out = await reach_service.tell_owner("remind me to call the suicide hotline volunteer line", urgent=True,
                                       origin="owner")
    assert out.startswith("Pushed")


# ----------------------------------------------------- one follow-up --

async def test_the_same_thing_twice_in_24h_is_refused_to_a_model(clean_db, push):
    assert (await reach_service.tell_owner(GOOD, title="UA958 check-in", origin="self")).startswith("Left")
    out = await reach_service.tell_owner(GOOD, title="UA958 check-in", origin="self")
    assert out.startswith("Tool error: you told Ellis this at ")
    assert "once is enough" in out
    assert len(await _notes()) == 1


async def test_an_explicit_dedupe_key_wins_over_the_title(clean_db, push):
    await reach_service.tell_owner(GOOD, title="One", origin="self", dedupe_key="UA958")
    out = await reach_service.tell_owner(GOOD, title="Another title", origin="self", dedupe_key="UA958")
    assert out.startswith("Tool error: you told Ellis this")


async def test_a_repeat_from_code_is_skipped_silently(clean_db, push):
    k = "UA958-fallback"
    text = "A text from BA at 9:31 pm looks like it needs you before 10:00 pm — I couldn't check it in time."
    assert (await reach_service.tell_owner(text, urgent=True, origin="fallback", dedupe_key=k)).startswith("Pushed")
    out = await reach_service.tell_owner(text, urgent=True, origin="fallback", dedupe_key=k)
    assert not out.startswith("Tool error") and "already" in out.lower()
    assert len(push.calls) == 1


QUINN = ("Quinn's 2:10 pm text asked for your referral link and hasn't heard back. I found it in your email — "
         "send it to him? (not sent)")


async def test_two_different_things_under_one_title_both_reach_owner(clean_db, push):
    """Review 2026-10-11: with no key the follow-up key fell back to the
    title before the message, so every reach titled "Heads up" (or AREA 3's
    "Edward") was one thing — the second refused as a repeat."""
    assert (await reach_service.tell_owner(GOOD, title="Heads up", origin="self")).startswith("Left")
    out = await reach_service.tell_owner(QUINN, title="Heads up", origin="self")
    assert out.startswith("Left"), out
    assert len(await _notes()) == 2
    # The same message is still the same thing, whatever its title.
    again = await reach_service.tell_owner(GOOD, title="Something else", origin="self")
    assert again.startswith("Tool error: you told Ellis this")


async def test_ping_notices_titled_edward_all_reach_owner(clean_db, push):
    """AREA 3's runner calls ``tell_owner(text, "Edward", urgent, link)``:
    every failed or held ping is its own notice (the runner throttles each
    key per hour itself), Ellis's own included."""
    import types

    mod = types.ModuleType("services.pings.runner")
    exec("async def notify(reach, text, urgent=True):\n    return await reach.tell_owner(text, 'Edward', urgent, None)\n",
         mod.__dict__)
    a = await mod.notify(reach_service, "S. (not in your contacts) asked me in Soccer: who's bringing oranges?")
    b = await mod.notify(reach_service, "Something went wrong while I was answering Mom — I'll look again.")
    c = await mod.notify(reach_service, "Something went wrong while I was answering Mom — I'll look again.")
    assert all(x.startswith("Pushed") for x in (a, b, c)), (a, b, c)
    mine = [await reach_service.tell_owner("I couldn't finish your @Edward in Family.", "Edward", True, None,
                                         origin="ping:owner") for _ in range(2)]
    assert all(x.startswith("Pushed") for x in mine)
    assert len(push.calls) == 5


async def test_care_is_deduped_by_its_caller_not_here(clean_db, push):
    """care_watch keys each dose and retries one whose push failed; a key
    reach_service had already seen must not swallow that retry."""
    k = "care:biscuit:2026-10-11:18:00"
    for _ in range(2):
        out = await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", urgent=True, origin="care",
                                           dedupe_key=k)
        assert out.startswith(reach_service.PUSHED)
    assert len(push.calls) == 2


async def test_without_the_note_fallback_a_failed_push_is_not_delivered(clean_db, push):
    """``note_fallback=False`` (care_watch's retry after it already left
    Ellis a note): a failed push says so and leaves no second note."""
    push.sent = 0
    out = await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", urgent=True, origin="care",
                                       dedupe_key="care:k", note_fallback=False)
    assert out.startswith(reach_service.NOT_DELIVERED), out
    assert await _notes() == []
    assert [r.outcome for r in await _reach_rows()] == ["failed"]


async def test_a_repeat_after_24h_goes_out(clean_db, push, monkeypatch):
    await reach_service.tell_owner(GOOD, title="UA958", origin="self")
    monkeypatch.setattr(reach_service, "_now", lambda: DAY + timedelta(hours=25))
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:   # the first reach, a day and an hour ago
        for row in (await s.execute(select(LedgerEventModel))).scalars():
            row.created_at = (DAY - timedelta(hours=1)).replace(tzinfo=None)
        await s.commit()
    out = await reach_service.tell_owner(GOOD, title="UA958", origin="self")
    assert out.startswith("Left")


async def test_owner_asking_twice_is_never_deduped(clean_db, push):
    await reach_service.tell_owner("call mom", title="Reminder", urgent=True, origin="owner")
    out = await reach_service.tell_owner("call mom", title="Reminder", urgent=True, origin="owner")
    assert out.startswith("Pushed") and len(push.calls) == 2


# --------------------------------------------------------- sensitive --

async def test_a_sensitive_speak_push_shows_nothing_on_the_lock_screen(clean_db, push):
    closes = datetime(2026, 10, 12, 0, 30, tzinfo=timezone.utc)   # 7:30 pm Springfield
    text = "Mom's 2:10 pm text about Dad's test results hasn't had an answer. Call her? (not done)"
    with reach_service.speak_scope(noticing_id=None, channel="push", floor_push=False, who=("Mom",),
                                   sensitive=True, dedupe_key="mom-results", category="family",
                                   closes_at=closes):
        out = await reach_service.tell_owner(text, title="Mom", urgent=True)
    assert out.startswith("Pushed")
    assert push.calls[0]["title"] == "Edward"
    assert push.calls[0]["body"] == "Something needs you before 7:30 pm — tap to read"
    assert "Dad" not in push.calls[0]["body"]
    notes = await _notes()
    assert len(notes) == 1 and notes[0].body == text      # the full offer is in the note


async def test_health_and_family_categories_are_sensitive_even_unflagged(clean_db, push):
    with reach_service.speak_scope(noticing_id=None, channel="push", floor_push=False, who=("Mom",),
                                   sensitive=False, dedupe_key="mom", category="health"):
        await reach_service.tell_owner("Mom's 2:10 pm text needs an answer. Call her? (not done)", urgent=True)
    assert push.calls[0]["title"] == "Edward"
    assert push.calls[0]["body"] == "Something needs you — tap to read"


# ------------------------------------------------------------- speak --

async def test_speak_cannot_raise_the_channel_above_its_scope(clean_db, push):
    with reach_service.speak_scope(noticing_id=None, channel="note", floor_push=False, who=("BA",),
                                   sensitive=False, dedupe_key="UA958"):
        out = await reach_service.tell_owner(GOOD, title="UA958", urgent=True)
    assert out.startswith("Left it as a note")
    assert push.calls == [] and len(await _notes()) == 1
    assert (await _reach_rows())[-1].outcome == "downgraded"


async def test_speak_lint_uses_the_scope_who(clean_db, push):
    with reach_service.speak_scope(noticing_id=None, channel="note", floor_push=False, who=("Quinn",),
                                   sensitive=False, dedupe_key="quinn"):
        out = await reach_service.tell_owner("Quinn still needs your referral link. Send it? (not sent)")
    assert out.startswith("Left Ellis a note")


async def test_speak_dedupe_uses_the_scope_key(clean_db, push):
    await reach_service.tell_owner(GOOD, title="first", origin="self", dedupe_key="UA958")
    with reach_service.speak_scope(noticing_id=None, channel="note", floor_push=False, who=("BA",),
                                   sensitive=False, dedupe_key="UA958"):
        out = await reach_service.tell_owner(GOOD, title="second")
    assert out.startswith("Tool error: you told Ellis this")


async def test_the_scope_records_what_was_delivered(clean_db, push):
    with reach_service.speak_scope(noticing_id=None, channel="push", floor_push=True, who=("BA",),
                                   sensitive=False, dedupe_key="UA958") as scope:
        await reach_service.tell_owner(GOOD, title="UA958", urgent=True)
    assert scope.delivered and scope.delivered[0]["channel"] == "push"
    assert scope.delivered[0]["push_log_id"]


# ----------------------------------------------------------- nightly --

async def test_the_nightly_never_pushes_and_leaves_at_most_one_note(clean_db, push):
    out = await reach_service.tell_owner(GOOD, title="One", urgent=True, origin="nightly")
    assert out.startswith("Left it as a note") and push.calls == []
    out = await reach_service.tell_owner(GOOD.replace("BA", "AA"), title="Two", origin="nightly")
    assert out.startswith("Tool error:") and "one note" in out
    assert len(await _notes()) == 1


# ------------------------------------------------------- delivery -----

async def test_delivery_records_the_noticing(clean_db, push):
    from services.database import NoticingModel, async_session

    async with async_session() as s:
        s.add(NoticingModel(id="n-1", kind="conflict", category="travel", claim="UA958", status="open"))
        await s.commit()
    await reach_service.tell_owner(GOOD, title="UA958", urgent=True, origin="fallback", noticing_id="n-1")
    async with async_session() as s:
        n = (await s.execute(select(NoticingModel).where(NoticingModel.id == "n-1"))).scalar_one()
    assert n.surfaced_at is not None and n.channel == "push" and n.push_log_id
    assert n.status == "spoken"


async def test_a_note_delivery_records_the_note_id(clean_db, push):
    from services.database import NoticingModel, async_session

    async with async_session() as s:
        s.add(NoticingModel(id="n-2", kind="unanswered", category="work", claim="Quinn"))
        await s.commit()
    await reach_service.tell_owner(GOOD, title="Quinn", origin="self", noticing_id="n-2")
    async with async_session() as s:
        n = (await s.execute(select(NoticingModel).where(NoticingModel.id == "n-2"))).scalar_one()
    assert n.note_id and n.channel == "note"


async def test_the_link_defaults_to_the_journal_entry_inside_a_wake(clean_db, push):
    from services import thought_journal

    entry = thought_journal.begin_entry()
    try:
        await reach_service.tell_owner(GOOD, title="UA958", urgent=True, origin="self")
    finally:
        thought_journal.clear_entry()
    assert push.calls[0]["url"] == f"/chat?c={entry}"


async def test_an_explicit_link_is_kept(clean_db, push):
    await reach_service.tell_owner(GOOD, title="UA958", urgent=True, origin="self", link="/hub/pets")
    assert push.calls[0]["url"] == "/hub/pets"


async def test_a_push_that_reaches_no_device_becomes_a_note(clean_db, push):
    push.sent = 0
    out = await reach_service.tell_owner(GOOD, title="UA958", urgent=True, origin="self")
    assert out.startswith("Left it as a note") and "reached no device" in out
    assert len(await _notes()) == 1


async def test_a_push_transport_error_falls_back_to_a_note(clean_db, push, monkeypatch):
    from services import push_service

    async def boom(**kw):
        raise ConnectionError("web push down")

    monkeypatch.setattr(push_service, "send_push_notification", boom)
    out = await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", urgent=True, origin="care")
    assert out.startswith("Left it as a note")
    assert len(await _notes()) == 1


async def test_when_both_transports_fail_it_raises(clean_db, push, monkeypatch):
    from services import notes_service, push_service

    async def boom(**kw):
        raise ConnectionError("web push down")

    async def no_note(**kw):
        return None

    monkeypatch.setattr(push_service, "send_push_notification", boom)
    monkeypatch.setattr(notes_service, "leave_note", no_note)
    with pytest.raises(RuntimeError):
        await reach_service.tell_owner("Biscuit's 6:00 pm dose isn't logged", urgent=True, origin="care")


async def test_the_ping_runner_without_an_origin_is_a_ping_notice(clean_db, push):
    import types

    mod = types.ModuleType("services.pings.runner")
    exec("async def notify(reach, text):\n    return await reach.tell_owner(text, 'Edward', True, None)\n",
         mod.__dict__)
    out = await mod.notify(reach_service, "I couldn't answer T.'s ping in Trivia crew: the send failed.")
    assert out.startswith("Pushed") and push.calls[0]["source"] == "reach:ping:other"


# ------------------------------------------------- the household hub --
# The hub's voice (``hub_voice``, 2026-10-11) is someone in Ellis's home, not
# necessarily Ellis. It may not text him — ``send_message`` refuses and says
# tell_owner — so tell_owner has to reach him, under the rules of a reach Ellis did
# not ask for: quiet hours, one follow-up, the secret scan, the high-risk gate.

NIGHT = datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc)    # 11:30 pm in Springfield
HUB_SAYS = "Rowan says Biscuit threw up after dinner."


class _HubTurn:
    """A ``hub_voice`` turn as the voice socket runs one: the ledger's turn
    record and the default policy for the source."""

    def __enter__(self):
        from services import turn_ledger
        from services.toolset import policy as P

        rec = turn_ledger.TurnRecord.start(conversation_id="hub-voice-1", source="hub_voice", model=None,
                                           provider=None)
        self._turn = turn_ledger._current.set(rec)
        self._policy = P.set_policy(P.default_policy("hub_voice"))
        return rec

    def __exit__(self, *exc):
        from services import turn_ledger
        from services.toolset import policy as P

        P.reset_policy(self._policy)
        turn_ledger._current.reset(self._turn)
        return False


@pytest.mark.unit
def test_the_hub_has_a_row_that_reaches_owner_under_the_autonomous_rules():
    rule = reach_service.rule_for("hub")
    assert rule is reach_service.RULES["hub"]
    assert not rule.refuse and rule.may_push
    assert rule.quiet == "note" and rule.counts and rule.dedupe and not rule.code


async def test_someone_at_the_hub_can_leave_owner_a_note(clean_db, push):
    with _HubTurn():
        out = await reach_service.tell_owner(HUB_SAYS)
    assert out.startswith("Left Ellis a note"), out
    assert [(r.source, r.outcome) for r in await _reach_rows()] == [("hub", "note")]
    assert len(await _notes()) == 1


async def test_someone_at_the_hub_can_push_owner_by_day(clean_db, push):
    with _HubTurn():
        out = await reach_service.tell_owner(HUB_SAYS, title="Biscuit", urgent=True)
    assert out.startswith("Pushed"), out
    assert push.calls[0]["source"] == "reach:hub"


async def test_a_push_from_the_hub_at_night_waits_as_a_note(clean_db, push, monkeypatch):
    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    with _HubTurn():
        out = await reach_service.tell_owner(HUB_SAYS, urgent=True)
    assert "quiet hours" in out and push.calls == []
    assert [(r.source, r.outcome) for r in await _reach_rows()] == [("hub", "downgraded")]


async def test_the_hub_never_puts_a_secret_on_the_lock_screen(clean_db, push):
    with _HubTurn():
        out = await reach_service.tell_owner(f"Rowan read me the wifi key {KEY}", urgent=True)
    assert out.startswith("Tool error: that would put a secret on Ellis's lock screen")
    assert push.calls == [] and await _notes() == []


async def test_the_hub_says_a_thing_once(clean_db, push):
    with _HubTurn():
        assert (await reach_service.tell_owner(HUB_SAYS)).startswith("Left")
        again = await reach_service.tell_owner(HUB_SAYS)
    assert again.startswith("Tool error: you told Ellis this at")


async def test_the_tool_the_send_refusal_points_to_works_from_the_hub(clean_db, push, monkeypatch):
    """``send_message``'s recipient rule tells a hub turn to tell_owner what
    it would send; the toolset's tell_owner from that turn reaches Ellis."""
    from services.toolset import tell_owner as tool
    from services.toolset.tell_owner import tell_owner_impl

    async def push_skill_on():
        return True

    monkeypatch.setattr(tool, "_push_skill_on", push_skill_on)

    with _HubTurn():
        note = await tell_owner_impl(HUB_SAYS)
        pushed = await tell_owner_impl("Rowan says the vet called back about Biscuit.", urgent=True)
    assert note.startswith("Left Ellis a note"), note
    assert pushed.startswith("Pushed"), pushed
    assert [(r.source, r.outcome) for r in await _reach_rows()] == [("hub", "note"), ("hub", "push")]
