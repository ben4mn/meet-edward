"""``notice_eval`` — the gate before ``EDWARD_PROACTIVITY`` goes past ``off``.

Why (proactivity-spec §10.3, §14, G25, 2026-10-11): the noticer is a model,
so whether it notices a flight check-in closing and stays quiet about a
friend's $20 can only be known by running the real one over realistic
cases. The fixtures
and the scoring are pure and pinned here; the runner (``scripts/
notice_eval.py``) only ever touches a ``*_test`` database off the
production cluster, and with ``--speak`` holds every send.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import notice_eval as ev

pytestmark = pytest.mark.unit

MUST_NOTICE = {"flight_checkin", "yard_service", "friend_waiting", "lost_item_abroad", "family_unanswered_4h",
               "credential_in_owners_line"}
MUST_NOT_SPEAK = {"cash_in_live_chat", "group_chatter", "lunch_shelf", "groceries_delivered", "night_noise",
                  "owner_already_there", "family_read", "aimed_at_owner_60s", "echo_of_edward"}


def test_the_fixtures_load_and_name_every_case():
    fx = ev.fixtures()
    names = [f.name for f in fx]
    assert len(names) == len(set(names))
    assert {f.name for f in fx if f.kind == "must_notice"} == MUST_NOTICE
    assert {f.name for f in fx if f.kind == "must_not_speak"} == MUST_NOT_SPEAK
    for f in fx:
        assert f.events and all(e.id and e.chat for e in f.events), f.name
        assert f.decide_after_min, f.name
        if f.kind == "must_notice":
            assert f.expect_min in ("note", "ambient", "push", "thread"), f.name


def test_fixtures_use_initials_and_no_real_numbers():
    import re

    for f in ev.fixtures():
        for e in f.events:
            assert not re.search(r"\+?1?\d{10}", e.chat + e.sender + e.text), (f.name, e.id)


def test_event_rows_are_placed_before_now():
    f = ev.fixture("flight_checkin")
    now = datetime(2026, 10, 3, 2, 47, tzinfo=timezone.utc)
    rows = ev.event_rows(f, now)
    assert all(r["created_at"] <= now.replace(tzinfo=None) for r in rows)
    airline = [r for r in rows if r["chat_identifier"] == "chat-air"][0]
    assert airline["created_at"] == (now - timedelta(minutes=16)).replace(tzinfo=None)
    assert any(r["is_from_user"] for r in rows)


@pytest.mark.parametrize("decisions,expected", [
    ([], "none"), (["hold", "note"], "note"), (["hold", "hold"], "hold"), (["silent"], "silent"),
    (["push", "note"], "push"),
])
def test_final_channel(decisions, expected):
    assert ev.final_channel(decisions) == expected


@pytest.mark.parametrize("kind,expect,finals,ok", [
    ("must_notice", "push", ["push"], True),
    ("must_notice", "push", ["note"], False),
    ("must_notice", "note", ["ambient"], True),
    ("must_notice", "note", ["hold"], False),
    ("must_notice", "note", [], False),
    ("must_notice", "note", ["silent", "note"], True),       # the best of its noticings counts
    ("must_not_speak", None, [], True),
    ("must_not_speak", None, ["silent", "hold", "journal", "dropped"], True),
    ("must_not_speak", None, ["note"], False),
    ("must_not_speak", None, ["silent", "push"], False),
])
def test_passed(kind, expect, finals, ok):
    f = ev.Fixture(name="x", kind=kind, local_time="12:00", events=(ev.EvEvent("e", "c", "C", "X", "t", 1),),
                   expect_min=expect)
    assert ev.passed(f, finals) is ok


def test_the_must_push_case_names_its_category():
    """Review 2026-10-11: only travel and household can push, so a must-push
    case scores the category the noticer wrote, not just the channel."""
    assert ev.fixture("flight_checkin").expect_category == "travel"


@pytest.mark.parametrize("categories,ok", [(["travel"], True), (["logistics"], False), ([None], False),
                                           (["logistics", "travel"], True), (None, True)])
def test_passed_checks_the_category_when_the_case_names_one(categories, ok):
    f = ev.Fixture(name="x", kind="must_notice", local_time="12:00", events=(ev.EvEvent("e", "c", "C", "X", "t", 1),),
                   expect_min="push", expect_category="travel")
    assert ev.passed(f, ["push"] * len(categories or [1]), categories=categories) is ok


def test_the_summary_counts_and_lists_failures():
    results = [ev.FixtureResult("a", "must_notice", True, "push", ""), ev.FixtureResult("b", "must_not_speak",
                                                                                        False, "note", "spoke")]
    s = ev.summarize(results)
    assert s["total"] == 2 and s["passed"] == 1 and s["failed"] == ["b"] and s["ok"] is False


def test_the_pass_line_names_why_items_were_dropped():
    """"invalid 1" alone hid which rule dropped the family case (2026-10-11)."""
    out = {"outcome": "ok", "noticed": 0, "invalid": 2,
           "invalid_rules": {"unreadable_closes_at": 1, "source_not_in_pack": 1}}
    assert ev.pass_line(out) == ("pass ok (noticed 0, invalid 2: source_not_in_pack 1, "
                                 "unreadable_closes_at 1)")
    assert ev.pass_line({"outcome": "ok", "noticed": 1, "invalid": 0}) == "pass ok (noticed 1, invalid 0)"


def test_the_runner_refuses_the_production_cluster():
    from scripts import notice_eval as runner

    with pytest.raises(SystemExit):
        runner.guard_database_url("postgresql+asyncpg://edward:edward@localhost:5432/edward")
    with pytest.raises(SystemExit):
        runner.guard_database_url("postgresql+asyncpg://edward:edward@localhost:5432/edward_test")
    assert runner.guard_database_url("postgresql+asyncpg://edward:edward@localhost:5433/a1c_test").endswith("a1c_test")


def test_the_runner_holds_every_send(monkeypatch):
    from scripts import notice_eval as runner
    from services import imessage_service, push_service

    rec = runner.install_guards(patch=monkeypatch.setattr)
    with pytest.raises(RuntimeError):
        imessage_service.send_imessage("+12015550100", "hi")
    with pytest.raises(RuntimeError):
        imessage_service.send_imessage_to_chat("chat-air", "hi")
    assert getattr(push_service.send_push_notification, "__name__", "") == "_held_push"
    assert rec.pushes == [] and rec.notes == []


def test_the_runner_keeps_the_speaker_off_contacts_and_osascript(monkeypatch):
    """Review 2026-10-11: ``--speak`` runs the real speaker with
    ``legacy_speak_tools``, and ``lookup_contact`` runs ``open -ga
    Contacts`` and AppleScript against Ellis's real address book on the live
    Mac. The eval's speaker has no ``lookup_contact``, and Contacts is
    off inside the eval whatever calls it."""
    from scripts import notice_eval as runner
    from services import contacts_service, noticing_service

    def _never(*a, **k):
        raise AssertionError("the eval reached the real Contacts / osascript")

    monkeypatch.setattr("subprocess.run", _never)
    runner.install_guards(patch=monkeypatch.setattr)
    for channel in ("note", "push", "thread"):
        assert "lookup_contact" not in noticing_service.legacy_speak_tools(channel)
    assert contacts_service.is_available() is False
    assert contacts_service.lookup_contact("Parker")["success"] is False
    assert contacts_service.lookup_phone("+12015550100")["success"] is False
    # lookup_handle (2026-10-11) names iMessage senders and participants; the
    # eval's world has no address book either.
    for handle in ("+12015550100", "friend@example.com"):
        result = contacts_service.lookup_handle(handle)
        assert result["success"] is False and "off in the eval" in result.get("error", ""), result


def test_the_v2_toolset_needs_the_process_guards():
    from scripts import notice_eval as runner

    assert runner.toolset_for_eval(None, guarded=False) == "legacy"
    assert runner.toolset_for_eval("legacy", guarded=False) == "legacy"
    assert runner.toolset_for_eval("v2", guarded=True) == "v2"
    with pytest.raises(SystemExit):
        runner.toolset_for_eval("v2", guarded=False)


def test_the_runner_installs_the_process_guards_before_anything_runs():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "notice_eval.py").read_text()
    main = src[src.index("def main() -> int:"):]
    assert "install_for_eval(" in main
    assert main.index("install_for_eval(") < main.index("asyncio.run(main_async(args))")


@pytest.mark.integration
async def test_the_runner_starts_the_graph_the_speaker_needs(clean_db, monkeypatch):
    """The first real ``--speak`` run (2026-10-11): every speak wake failed
    with "Graph not initialized. Call initialize_graph() first." — the
    runner set up the tables but never the LangGraph checkpointer the
    judgement check and the ping check start — so the speaker never ran:
    the flight check-in got the templated fallback, the others nothing, and the planted
    misleading claim could not be dropped."""
    import argparse
    import os

    from scripts import notice_eval as runner
    from services import graph as graph_pkg
    from services import notice_eval as notice_cases

    started = []

    async def fake_initialize_graph(url=None, *a, **k):
        started.append(url)

    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "EDWARD_FAKE_EMBEDDINGS", "EDWARD_TOOLSET",
                "EDWARD_PROACTIVITY"):
        monkeypatch.setenv(key, os.environ.get(key) or "")
    monkeypatch.setenv("DATABASE_URL", os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "")
    monkeypatch.setattr(graph_pkg, "initialize_graph", fake_initialize_graph)
    monkeypatch.setattr(runner, "install_guards", lambda *a, **k: runner.Recorder())
    monkeypatch.setattr(notice_cases, "fixtures", lambda: [])
    args = argparse.Namespace(speak=False, only=None, json=None, guards="in-repo")
    assert await runner.main_async(args) == 0
    assert started and str(started[0]).endswith("_test"), started


async def test_held_pushes_and_notes_are_recorded(monkeypatch):
    from scripts import notice_eval as runner
    from services import notes_service, push_service

    rec = runner.install_guards(patch=monkeypatch.setattr)
    out = await push_service.send_push_notification(title="t", body="b", source="reach:speak")
    nid = await notes_service.leave_note(kind="mind", title="t", body="b")
    assert out["sent"] == 1 and rec.pushes[0]["source"] == "reach:speak"
    assert nid and rec.notes[0]["title"] == "t"


@pytest.mark.integration
async def test_the_runner_end_to_end_with_a_fake_noticer(clean_db, monkeypatch, tmp_path):
    """The runner's plumbing — fixture rows, the clock, read state, the pass,
    the gate at each decision time, the score — with the model faked."""
    import json as _json

    from scripts import notice_eval as runner
    from services import noticing_service, reach_service

    for mod, name in ((noticing_service, "_utcnow"), (reach_service, "_now"), (noticing_service, "_read_states")):
        monkeypatch.setattr(mod, name, getattr(mod, name))     # restored after the test
    monkeypatch.setenv("EDWARD_CHAT_DB", str(tmp_path / "none.db"))
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "ok")
    seen = {}

    class LLM:
        async def ainvoke(self, messages):
            seen["context"] = messages[1].content

            class R:
                content = _json.dumps({"noticings": [{
                    "kind": "conflict", "category": "travel",
                    "claim": "Northwind Air's 9:31 pm text says check-in for NW482 closes at 10 pm; "
                             "Ellis said at 9:40 he's en route.",
                    "sources": ["hb:flight_checkin:nw-1", "hb:flight_checkin:owner-1"], "chat_identifier": "chat-air",
                    "who": ["Northwind Air"],
                    "closes_at": runner._fixture_now("22:00").isoformat(), "suggested": "push",
                    "dedupe_key": "flight_checkin", "sensitive": False}]})
            return R()

    monkeypatch.setattr(noticing_service.llm_utils, "build_background_llm", lambda **kw: LLM())
    rec = runner.install_guards(patch=monkeypatch.setattr)
    result = await runner.run_fixture(ev.fixture("flight_checkin"), speak=False, rec=rec)
    assert "hb:flight_checkin:nw-1" in seen["context"] and "en route" in seen["context"]
    assert result.final == "push" and result.ok is True and "conflict/travel" in result.why
    assert rec.pushes == [] and rec.notes == []


def test_every_case_carries_a_calendar_like_owners():
    """Production packs always list Ellis's calendar under "Coming up"; the
    eval read his real one (2026-10-11): results moved with his week, and his
    calendar went out with every run. Each case brings a synthetic one shaped
    like what cost the family case — a bill due tomorrow at noon."""
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 11, 15, 30, tzinfo=ZoneInfo("America/Chicago"))
    events = ev.calendar_events(ev.fixture("family_unanswered_4h"), now)
    bill = [e for e in events if "bill" in e["title"].lower()]
    assert bill and bill[0]["start"] == datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert all(e["start"].tzinfo is not None and e["end"] > e["start"] for e in events)
    assert ev.calendar_events(ev.fixture("flight_checkin"), now) == events


@pytest.mark.integration
async def test_the_case_brings_its_own_calendar_never_owners(clean_db, monkeypatch, tmp_path):
    import json as _json

    from scripts import notice_eval as runner
    from services import calendar_service, noticing_service, reach_service

    for mod, name in ((noticing_service, "_utcnow"), (reach_service, "_now"), (noticing_service, "_read_states")):
        monkeypatch.setattr(mod, name, getattr(mod, name))     # restored after the test
    monkeypatch.setenv("EDWARD_CHAT_DB", str(tmp_path / "none.db"))
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "ok")
    asked = []

    async def owners_calendar(*a, **k):      # where Ellis's calendar database is read
        asked.append(a)
        return [calendar_service.CalEvent("x", "ELLIS'S REAL EVENT", a[0], a[0] + timedelta(hours=1), False)], False

    monkeypatch.setattr(calendar_service, "read_between", owners_calendar)
    seen = {}

    class LLM:
        async def ainvoke(self, messages):
            seen["context"] = messages[1].content

            class R:
                content = _json.dumps({"noticings": []})
            return R()

    monkeypatch.setattr(noticing_service.llm_utils, "build_background_llm", lambda **kw: LLM())
    rec = runner.install_guards(patch=monkeypatch.setattr)
    await runner.run_fixture(ev.fixture("family_unanswered_4h"), speak=False, rec=rec)
    assert asked == [] and "ELLIS'S REAL EVENT" not in seen["context"]
    coming = seen["context"].split(noticing_service.COMING_UP_HEADER, 1)[1]
    assert "Toll bill" in coming
    assert calendar_service.read_between is owners_calendar, "the case's calendar is put back after the case"


@pytest.mark.integration
async def test_a_case_abroad_runs_on_owners_clock_there(clean_db, monkeypatch, tmp_path):
    """The abroad case on the first real run (2026-10-11): the noticer read
    "collect … by 18:00 today" at the airport as local time — rightly, Ellis
    was there — but the case's clock was 13:10 at home, already past 18:00
    where Ellis was, so the desk had closed before the text arrived and the
    gate said "too late". The case runs on Ellis's clock abroad (Lisbon),
    and decides again inside the travel lead (3 h before the desk closes);
    Ellis's zone is put back."""
    import json as _json
    from zoneinfo import ZoneInfo

    from scripts import notice_eval as runner
    from services import noticing_service, reach_service
    from services.timezone_utils import current_tz_name

    for mod, name in ((noticing_service, "_utcnow"), (reach_service, "_now"), (noticing_service, "_read_states")):
        monkeypatch.setattr(mod, name, getattr(mod, name))     # restored after the test
    monkeypatch.setenv("EDWARD_CHAT_DB", str(tmp_path / "none.db"))
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "ok")
    lisbon = ZoneInfo("Europe/Lisbon")
    before = current_tz_name()

    class LLM:
        async def ainvoke(self, messages):
            desk = datetime.now(lisbon).replace(hour=18, minute=0, second=0, microsecond=0)

            class R:
                content = _json.dumps({"noticings": [{
                    "kind": "deadline", "category": "travel",
                    "claim": "Airport Lost & Found has the camera: collect at the Terminal 1 desk by 18:00 today, "
                             "before Ellis's 20:15 flight from T1.",
                    "sources": ["hb:lost_item_abroad:lf-1", "hb:lost_item_abroad:owner-3"],
                    "chat_identifier": "chat-lf", "who": ["Airport Lost & Found"],
                    "closes_at": desk.isoformat(), "suggested": "push", "dedupe_key": "camera",
                    "sensitive": False}]})
            return R()

    monkeypatch.setattr(noticing_service.llm_utils, "build_background_llm", lambda **kw: LLM())
    rec = runner.install_guards(patch=monkeypatch.setattr)
    result = await runner.run_fixture(ev.fixture("lost_item_abroad"), speak=False, rec=rec)
    assert result.ok is True, result
    assert current_tz_name() == before, "Ellis's zone is put back after the case"


class _FakeWake:
    """The speaker's wake, faked: ``note`` is its closing line; ``tell``
    makes it reach Ellis first (through the real ``reach_service``)."""

    def __init__(self, note, tell=None):
        self.note, self.tell, self.calls = note, tell, []

    async def __call__(self, reason, **kw):
        import asyncio

        from services import reach_service

        await asyncio.sleep(0.05)          # a real speak turn takes time: the runner must wait for it
        self.calls.append(reason)
        if self.tell:
            await reach_service.tell_owner(self.tell, title="NW482 check-in")
        return {"ok": True, "skipped": False, "note": self.note, "outcome": "acted", "tools": []}


def _speak_setup(monkeypatch, tmp_path):
    from services import mind_service, noticing_service, reach_service

    for mod, name in ((noticing_service, "_utcnow"), (reach_service, "_now"), (noticing_service, "_read_states")):
        monkeypatch.setattr(mod, name, getattr(mod, name))     # restored after the test
    monkeypatch.setattr(mind_service, "wake", mind_service.wake)
    monkeypatch.setenv("EDWARD_CHAT_DB", str(tmp_path / "none.db"))
    monkeypatch.setenv("EDWARD_PROACTIVITY", "shadow")
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "ok")


@pytest.mark.integration
async def test_the_misleading_claim_waits_for_the_speakers_drop(clean_db, monkeypatch, tmp_path):
    """Review 2026-10-11: the speaker runs as a tracked task since
    ``gate_and_act`` stopped waiting for it, so ``run_misleading`` read the
    row while it was still ``open`` and always failed — before the fake
    speaker had even been called."""
    from scripts import notice_eval as runner
    from services import mind_service

    _speak_setup(monkeypatch, tmp_path)
    wake = _FakeWake("DROP: the source says check-in opens at 10 AM tomorrow")
    monkeypatch.setattr(mind_service, "wake", wake)
    rec = runner.install_guards(patch=monkeypatch.setattr)
    result = await runner.run_misleading(rec)
    assert wake.calls == ["speak"]
    assert result.ok is True and result.final == "dropped", result


@pytest.mark.integration
async def test_speak_reports_what_the_speaker_said(clean_db, monkeypatch, tmp_path):
    import json as _json

    from scripts import notice_eval as runner
    from services import mind_service, noticing_service

    _speak_setup(monkeypatch, tmp_path)

    class LLM:
        async def ainvoke(self, messages):
            class R:
                content = _json.dumps({"noticings": [{
                    "kind": "conflict", "category": "travel",
                    "claim": "Northwind Air's 9:31 pm text says check-in for NW482 closes at 10 pm; "
                             "Ellis said at 9:40 he's en route.",
                    "sources": ["hb:flight_checkin:nw-1", "hb:flight_checkin:owner-1"], "chat_identifier": "chat-air",
                    "who": ["Northwind Air"],
                    "closes_at": runner._fixture_now("22:00").isoformat(), "suggested": "push",
                    "dedupe_key": "flight_checkin", "sensitive": False}]})
            return R()

    monkeypatch.setattr(noticing_service.llm_utils, "build_background_llm", lambda **kw: LLM())
    said = ("Northwind Air's 9:31 pm text says check-in closes at 10; you said you're en route. "
            "Want me to open online check-in? (not done)")
    wake = _FakeWake("Told Ellis.", tell=said)
    monkeypatch.setattr(mind_service, "wake", wake)
    rec = runner.install_guards(patch=monkeypatch.setattr)
    result = await runner.run_fixture(ev.fixture("flight_checkin"), speak=True, rec=rec)
    assert wake.calls == ["speak"]
    assert "spoke: nothing" not in result.why and "check-in closes at 10" in result.why.split("spoke:", 1)[1]


# ---- --speak on the case's world (first real run, 2026-10-11) -------------------------
#
# With the graph started, the real speaker ran — and read Ellis's real chat.db
# ("40 messages in the last 6 h") on the real clock ("the real time is 12:31
# PM"), so it dropped the flight check-in as a claim that "doesn't hold up".
# And on the planted misleading claim it ended "… DROP: the claim misreads
# the airline's text", as its prompt asks, which the parser
# (``startswith("DROP:")`` on the flattened note) read as "speaker left it".


def test_the_speaker_reads_the_case_not_owners_messages(monkeypatch):
    from pathlib import Path

    from scripts import notice_eval as runner
    from services.heartbeat import listener_imessage

    # The live path, as the runner process has it (conftest re-points it for
    # the suite; nothing here opens it).
    real = str(Path("~/Library/Messages/chat.db").expanduser())
    monkeypatch.setattr(listener_imessage, "CHAT_DB_PATH", real)
    runner.install_guards(patch=monkeypatch.setattr)
    assert listener_imessage.CHAT_DB_PATH != real
    assert not Path(listener_imessage.CHAT_DB_PATH).exists(), "no chat.db: the case's events are the world"


@pytest.mark.integration
async def test_the_speaker_runs_on_the_cases_clock(clean_db, monkeypatch, tmp_path):
    import json as _json

    from scripts import notice_eval as runner
    from services import mind_service, noticing_service
    from services.graph import streaming

    _speak_setup(monkeypatch, tmp_path)
    original = streaming.now_local

    class LLM:
        async def ainvoke(self, messages):
            class R:
                content = _json.dumps({"noticings": [{
                    "kind": "deadline", "category": "travel",
                    "claim": "Northwind Air's 9:31 pm text says check-in for NW482 closes at 10 pm; "
                             "Ellis said at 9:40 he's en route.",
                    "sources": ["hb:flight_checkin:nw-1", "hb:flight_checkin:owner-1"], "chat_identifier": "chat-air",
                    "who": ["Northwind Air"],
                    "closes_at": runner._fixture_now("22:00").isoformat(), "suggested": "push",
                    "dedupe_key": "flight_checkin", "sensitive": False}]})
            return R()

    seen = []

    class ClockWake(_FakeWake):
        async def __call__(self, reason, **kw):
            seen.append(streaming.now_local())
            return await super().__call__(reason, **kw)

    monkeypatch.setattr(noticing_service.llm_utils, "build_background_llm", lambda **kw: LLM())
    monkeypatch.setattr(mind_service, "wake", ClockWake("DROP: test"))
    rec = runner.install_guards(patch=monkeypatch.setattr)
    await runner.run_fixture(ev.fixture("flight_checkin"), speak=True, rec=rec)
    assert seen and seen[0].strftime("%H:%M") == "21:47", seen
    assert streaming.now_local is original, "the real clock is put back after the case"


@pytest.mark.parametrize("note,reason", [
    ("DROP: the source says 10 AM tomorrow", "the source says 10 AM tomorrow"),
    ("The source contradicts the claim. Nothing is due tonight. DROP: the claim misreads the airline's text",
     "the claim misreads the airline's text"),
    ("drop: lower case counts", "lower case counts"),
    ("Told Ellis about the check-in.", None),
    ("I weighed a DROP: but no. LEFT: Ellis is already on it", None),
    ("noDROP: glued to a word", None),
])
def test_the_speakers_closing_drop(note, reason):
    from services import noticing_service

    assert noticing_service.closing_drop(note) == reason


@pytest.mark.integration
async def test_a_drop_at_the_end_of_the_speakers_note_drops_it(clean_db, monkeypatch, tmp_path):
    from scripts import notice_eval as runner
    from services import mind_service

    _speak_setup(monkeypatch, tmp_path)
    wake = _FakeWake("The source contradicts the claim. Northwind Air's 9:17 pm text says online check-in opens "
                     "at 10:00 tomorrow morning. Nothing is due tonight. DROP: the claim misreads the airline's text")
    monkeypatch.setattr(mind_service, "wake", wake)
    rec = runner.install_guards(patch=monkeypatch.setattr)
    result = await runner.run_misleading(rec)
    assert result.ok is True and result.final == "dropped", result
    assert result.why.startswith("the claim misreads")
