"""The noticer pass and the speaker (``EDWARD_PROACTIVITY=shadow|notice``).

Why (proactivity-spec §2.4–§2.6, 2026-10-11): one cheap model call per
inbound batch writes private noticings; a pure gate decides; an Opus
``speak`` wake verifies and says it once through ``reach_service``. A dead
noticer must never look like a quiet day: a parse failure is ``failed``,
never ``[]``; a capped day is ``deferred``; a broken quota check fails
closed. The model is faked where the service looks it up
(``noticing_service.llm_utils.build_background_llm``) and the speak wake at
``mind_service.wake``; no real model, chat.db, push or message is touched.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from services import mind_service, noticing_service, reach_service
from tests.test_reach_service import FakePush

pytestmark = pytest.mark.integration

def _clock():
    """Now, read when called — module constants go stale in a long suite."""
    return datetime.now(timezone.utc)
REAL_QUOTA_WORD = getattr(noticing_service, "_quota_word", None)


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class FakeLLM:
    def __init__(self, content='{"noticings": []}', exc=None):
        self.content = content
        self.exc = exc
        self.calls = []

    async def ainvoke(self, messages):
        from services.call_purpose import current_purpose

        self.calls.append({"messages": messages, "purpose": current_purpose()})
        if self.exc:
            raise self.exc

        class R:
            pass

        r = R()
        r.content = self.content
        return r


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from services import push_service, timezone_utils

    monkeypatch.setenv("EDWARD_CHAT_DB", str(tmp_path / "no-chat.db"))   # never Ellis's chat.db
    monkeypatch.setenv("EDWARD_PROACTIVITY", "shadow")
    monkeypatch.delenv("EDWARD_NOTICE_PUSH", raising=False)
    monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
    monkeypatch.delenv("EDWARD_SPEAK_BUDGET", raising=False)
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "ok")
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    fake_push = FakePush()
    monkeypatch.setattr(push_service, "send_push_notification", fake_push)
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(reach_service, "_now", lambda: datetime.now(timezone.utc))
    return fake_push


@pytest.fixture
def llm(monkeypatch):
    holder = {"llm": FakeLLM(), "built": []}

    def build(**kw):
        holder["built"].append(kw)
        return holder["llm"]

    monkeypatch.setattr(noticing_service.llm_utils, "build_background_llm", build)
    return holder


async def _event(eid, text, *, chat="chat-quinn", chat_name="Quinn", sender="Quinn", from_me=False, ago_min=20,
                 raw=None, status="escalated", notified=None):
    from services.database import HeartbeatEventModel, async_session

    data = {"full_text": text}
    data.update(raw or {})
    async with async_session() as s:
        s.add(HeartbeatEventModel(
            id=eid, source="imessage", event_type="message_sent" if from_me else "message_received",
            sender=sender, contact_name="Ellis" if from_me else sender, chat_identifier=chat, chat_name=chat_name,
            summary=text[:200], raw_data=json.dumps(data), is_from_user=from_me, triage_status=status,
            notified_at=notified, created_at=_naive(_clock() - timedelta(minutes=ago_min))))
        await s.commit()


async def _events():
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        return {r.id: r for r in (await s.execute(select(HeartbeatEventModel))).scalars()}


async def _noticings():
    from services.database import NoticingModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(NoticingModel).order_by(NoticingModel.created_at))).scalars())


async def _ledger(kind="notice"):
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == kind)
                                     .order_by(LedgerEventModel.id))).scalars())


def _item(**kw):
    base = {"kind": "deadline", "category": "work", "claim": "Quinn's 2:10 pm text asks for the deck by 6 pm.",
            "sources": ["hb:e1"], "chat_identifier": "chat-quinn", "who": ["Quinn"],
            "closes_at": (_clock() + timedelta(hours=2)).isoformat(), "suggested": "note",
            "dedupe_key": "deck-by-6", "sensitive": False}
    base.update(kw)
    return base


def _reply(*items):
    return json.dumps({"noticings": list(items)})


# ------------------------------------------------------------ the pass --

async def test_a_noticing_becomes_a_row_and_the_events_are_stamped(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM(_reply(_item()))
    out = await noticing_service.notice_pass(["e1"])
    assert out["outcome"] == "ok" and out["noticed"] == 1
    rows = await _noticings()
    assert len(rows) == 1 and rows[0].kind == "deadline" and rows[0].pass_id == out["pass_id"]
    assert json.loads(rows[0].sources_json)[0]["ref"] == "hb:e1"
    ev = (await _events())["e1"]
    assert ev.notification_batch_id == f"notice:{out['pass_id']}" and ev.notified_at is not None
    led = (await _ledger())[-1]
    assert led.source == "noticer" and led.outcome == "ok" and led.ref_id == out["pass_id"]


async def test_the_noticer_runs_under_mind_notice_at_interactive_priority(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_NOTICE_MODEL", "claude-sonnet-5-5")
    await _event("e1", "Can you send the deck by 6?")
    await noticing_service.notice_pass(["e1"])
    assert llm["llm"].calls[0]["purpose"] == "mind:notice"
    built = llm["built"][0]
    assert built["priority"] == "interactive" and built["route"] == "background"
    assert built["model"] == "claude-sonnet-5-5"
    system, human = llm["llm"].calls[0]["messages"]
    assert system.content == noticing_service.NOTICER_PROMPT
    assert "hb:e1" in human.content


async def test_an_empty_answer_is_ok_with_nothing_noticed(clean_db, llm):
    await _event("e1", "lol")
    out = await noticing_service.notice_pass(["e1"])
    assert out["outcome"] == "ok" and out["noticed"] == 0 and await _noticings() == []


async def test_malformed_output_is_failed_not_empty(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM("I think there is nothing here")
    out = await noticing_service.notice_pass(["e1"])
    assert out["outcome"] == "failed" and out["error_class"] == "parse"
    assert await _noticings() == []
    ev = (await _events())["e1"]
    assert ev.notification_batch_id == f"notice-failed:{out['pass_id']}"
    led = (await _ledger())[-1]
    assert led.outcome == "failed" and json.loads(led.detail_json)["error_class"] == "parse"


async def test_a_failed_pass_is_retried_once_by_the_sweep(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM("not json")
    first = await noticing_service.notice_pass(["e1"])
    # Ten minutes later the sweep re-selects the pass's events by batch id.
    later = datetime.now(timezone.utc) + timedelta(minutes=11)   # the clock now, not at import
    llm["llm"] = FakeLLM("still not json")
    out = await noticing_service._sweep_retry_failed_passes(later)
    await noticing_service.drain()
    assert out["retried"] == 1
    ev = (await _events())["e1"]
    assert ev.notification_batch_id.startswith("notice-failed2:")
    # A second failure is final: no third try.
    out = await noticing_service._sweep_retry_failed_passes(later + timedelta(minutes=30))
    assert out["retried"] == 0
    assert first["pass_id"] in ev.notification_batch_id


async def test_a_retried_pass_that_succeeds_stamps_notice(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM("not json")
    await noticing_service.notice_pass(["e1"])
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service._sweep_retry_failed_passes(datetime.now(timezone.utc) + timedelta(minutes=11))
    await noticing_service.drain()
    assert (await _events())["e1"].notification_batch_id.startswith("notice:")
    assert len(await _noticings()) == 1


async def test_a_timeout_is_failed(clean_db, llm, monkeypatch):
    import asyncio

    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM(exc=asyncio.TimeoutError())
    out = await noticing_service.notice_pass(["e1"])
    assert out["outcome"] == "failed" and out["error_class"] == "TimeoutError"


async def test_the_spend_cap_defers_without_a_call(clean_db, llm, monkeypatch):
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "exhausted")
    await _event("e1", "Can you send the deck by 6?")
    out = await noticing_service.notice_pass(["e1"])
    assert out["outcome"] == "deferred" and llm["llm"].calls == []
    assert (await _events())["e1"].notification_batch_id == f"notice-deferred:{out['pass_id']}"
    assert (await _ledger())[-1].outcome == "deferred"


def test_a_broken_quota_check_fails_closed(monkeypatch, capsys):
    from services import llm_spend

    monkeypatch.setattr(llm_spend, "provider", lambda: llm_spend.ANTHROPIC)

    def broken():
        raise AttributeError("quota_state_cached moved")

    monkeypatch.setattr(llm_spend, "quota_state_cached", broken)
    assert REAL_QUOTA_WORD() == "exhausted"
    assert "[notice] quota check broken" in capsys.readouterr().out


def test_the_quota_word_reads_the_spend_cap_under_claude(monkeypatch):
    from services import llm_spend

    monkeypatch.setattr(llm_spend, "provider", lambda: llm_spend.ANTHROPIC)
    monkeypatch.setattr(llm_spend, "quota_state_cached", lambda: "paced")
    assert REAL_QUOTA_WORD() == "paced"


# -------------------------------------------------------- the pre-filter --

async def test_pings_are_never_stamped_nor_noticed(clean_db, llm):
    await _event("p1", "@Edward what time is it", status="ping")
    await _event("p2", "@edward hi", raw={"ping": {"kind": "mention"}})
    out = await noticing_service.notice_pass(["p1", "p2"])
    evs = await _events()
    assert evs["p1"].notification_batch_id is None and evs["p2"].notification_batch_id is None
    assert llm["llm"].calls == [] and out["events"] == 0


async def test_echoes_are_skipped_by_flag_signature_and_recent_send(clean_db, llm):
    from services.database import ImessageOutgoingSendModel, async_session

    await _event("x1", "on it", from_me=True, raw={"edward_echo": "rowid"})
    await _event("x2", "Found the spare tickets - Edward 🤖", from_me=True)
    await _event("x3", "sent you the link", from_me=True, chat="chat-trivia", ago_min=20)
    async with async_session() as s:
        s.add(ImessageOutgoingSendModel(chat_identifier="chat-trivia", text_hash="h",
                                        sent_at=_clock() - timedelta(minutes=20, seconds=30)))
        await s.commit()
    await _event("ok", "Can you send the deck by 6?")
    await noticing_service.notice_pass(["x1", "x2", "x3", "ok"])
    evs = await _events()
    for eid in ("x1", "x2", "x3"):
        assert evs[eid].notification_batch_id.startswith("notice-skip:echo:"), eid
    human = llm["llm"].calls[0]["messages"][1].content
    new_part = human.split("## Recent lines")[0]
    assert "hb:ok" in new_part
    assert "hb:x1" not in new_part and "hb:x2" not in new_part and "hb:x3" not in new_part
    # As chat context an echo is Edward's own line, labelled so.
    assert "hb:x1 " not in human or "Edward: on it" in human


async def test_a_stood_down_chat_is_skipped(clean_db, llm):
    from services.database import ChatStanddownModel, async_session

    async with async_session() as s:
        s.add(ChatStanddownModel(chat_identifier="chat-quiet", expires_at=_clock() + timedelta(hours=5)))
        await s.commit()
    await _event("q1", "anyone up?", chat="chat-quiet")
    out = await noticing_service.notice_pass(["q1"])
    assert (await _events())["q1"].notification_batch_id.startswith("notice-skip:stand-down:")
    assert llm["llm"].calls == [] and out["outcome"] == "ok"


async def test_tapbacks_and_empty_lines_are_skipped(clean_db, llm):
    await _event("t1", "Loved “see you at 6”")
    await _event("t2", "")
    await noticing_service.notice_pass(["t1", "t2"])
    evs = await _events()
    assert evs["t1"].notification_batch_id.startswith("notice-skip:tapback:")
    assert evs["t2"].notification_batch_id.startswith("notice-skip:empty:")
    assert llm["llm"].calls == []


KEY = "sk-proj-" + "Ab3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY"


async def test_a_secret_in_owners_line_is_a_code_security_noticing_and_redacted(clean_db, llm):
    await _event("s1", f"here's the key {KEY}", from_me=True, chat="chat-work", chat_name="Work")
    await noticing_service.notice_pass(["s1"])
    rows = await _noticings()
    sec = [r for r in rows if r.kind == "security"]
    assert len(sec) == 1 and sec[0].origin == "code" and sec[0].category == "security"
    assert KEY not in sec[0].claim and "Work" in sec[0].claim and "rotate it" in sec[0].claim
    human = llm["llm"].calls[0]["messages"][1].content
    assert KEY not in human and "[secret]" in human


# ----------------------------------------------------------- the context --

async def test_the_context_joins_owners_lines_from_other_chats_and_full_open_claims(clean_db, llm):
    await noticing_service.add_noticing(kind="deadline", claim="An earlier claim that is quite long " * 4,
                                        status="held", dedupe_key="earlier")
    await _event("owner", "en route to the airport", from_me=True, chat="chat-home", chat_name="Home", ago_min=30)
    await _event("e1", "Skyway: online check-in closes at 10 pm")
    await noticing_service.notice_pass(["e1"])
    human = llm["llm"].calls[0]["messages"][1].content
    assert "en route to the airport" in human and "hb:owner" in human and "Home" in human
    assert ("An earlier claim that is quite long " * 4).strip() in human
    assert "earlier" in human


async def test_the_pack_lists_coming_up_under_its_background_header(clean_db, llm, monkeypatch):
    async def coming(now):
        return ["- calendar 2026-10-12 12:00:00-05:00: Toll bill"]

    monkeypatch.setattr(noticing_service, "_coming_up", coming)
    await _event("e1", "Can you send the deck by 6?")
    await noticing_service.notice_pass(["e1"])
    human = llm["llm"].calls[0]["messages"][1].content
    assert noticing_service.COMING_UP_HEADER + "\n- calendar 2026-10-12 12:00:00-05:00: Toll bill" in human


# ---------------------------------------------------------- validation --

async def test_invalid_items_are_dropped_and_counted(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM(_reply(
        _item(kind="gossip"),                                    # unknown kind
        _item(sources=["hb:nope"], dedupe_key="a"),              # a source not in the pack
        _item(kind="conflict", sources=["hb:e1"], dedupe_key="b"),  # a conflict needs two
        _item(closes_at="tomorrowish", dedupe_key="c"),          # unparseable
        _item(dedupe_key="good"),
    ))
    out = await noticing_service.notice_pass(["e1"])
    assert out["noticed"] == 1 and out["invalid"] == 4


async def test_each_drop_is_logged_and_ledgered_by_rule_without_the_claim(clean_db, llm, capsys):
    """notice_eval's family case read "invalid 1" and nothing said why
    (2026-10-11): each drop names its rule in the log and the pass's ledger
    row, never the claim."""
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM(_reply(
        _item(kind="gossip", claim="Quinn's secret deck plan"),
        _item(sources=["hb:nope"], dedupe_key="a", claim="Quinn's secret deck plan"),
        _item(closes_at="tomorrowish", dedupe_key="c", claim="Quinn's secret deck plan"),
        _item(dedupe_key="good"),
    ))
    out = await noticing_service.notice_pass(["e1"])
    rules = {"unknown_kind": 1, "source_not_in_pack": 1, "unreadable_closes_at": 1}
    assert out["invalid"] == 3 and out["invalid_rules"] == rules
    row = [r for r in await _ledger() if r.source == "noticer"][-1]
    assert json.loads(row.detail_json)["invalid_rules"] == rules
    printed = capsys.readouterr().out
    for rule in rules:
        assert f"rule={rule}" in printed
    assert "hb:nope" in printed and "secret deck" not in printed


async def test_a_repeat_of_an_open_noticing_is_not_a_second_row(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    await _event("e2", "Still need that deck by 6!", ago_min=10)
    llm["llm"] = FakeLLM(_reply(_item(sources=["hb:e2"])))
    out = await noticing_service.notice_pass(["e2"])
    rows = await _noticings()
    assert len(rows) == 1 and out["noticed"] == 0 and out["merged"] == 1
    assert {s["ref"] for s in json.loads(rows[0].sources_json)} == {"hb:e1", "hb:e2"}


# --------------------------------------------------------- shadow, notice --

async def test_shadow_records_the_decision_and_never_speaks(clean_db, llm, monkeypatch):
    called = []

    async def wake(*a, **k):
        called.append(k)
        return {"ok": True}

    monkeypatch.setattr(mind_service, "wake", wake)
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    row = (await _noticings())[0]
    assert row.status == "shadow" and row.decision == "note" and row.decided_at is not None
    assert called == []


class FakeWake:
    """The speak wake: records its kwargs and, when told to, reaches Ellis
    through reach_service as the real turn's tell_owner would."""

    def __init__(self, *, deliver=None, note="LEFT: nothing", ok=True, tools=(), reached=False):
        self.calls = []
        self.deliver = deliver
        self.note = note
        self.ok = ok
        self.tools = list(tools)
        self.reached = reached

    async def __call__(self, reason, **kw):
        self.calls.append({"reason": reason, **kw})
        if self.deliver:
            await reach_service.tell_owner(self.deliver, title="Deck", urgent=self.deliver_urgent)
        out = {"ok": self.ok, "note": self.note, "tools": self.tools, "thought_id": "thought-1"}
        if not self.ok:
            out.update({"error_class": "TimeoutError", "transient": True, "reached": self.reached})
        return out

    deliver_urgent = False


OFFER = "Quinn's 2:10 pm text asks for the deck by 6 pm. Send him the deck? (not sent)"


async def test_notice_calls_the_speak_wake_with_the_decision_derived_policy(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    fake = FakeWake(deliver=OFFER)
    monkeypatch.setattr(mind_service, "wake", fake)
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    call = fake.calls[0]
    assert call["reason"] == "speak"
    assert call["conversation_id"] == "edward-speak" and call["max_history_chars"] == 0
    assert call["skip_extraction"] is True and call["system_prompt"] == noticing_service.SPEAK_SYSTEM_PROMPT
    pol = noticing_service.speak_policy_kwargs("note", "chat-quinn", 180)
    assert pol == {"source": "mind", "owner_asked": False, "may_push": False, "may_reach_people": False,
                   "reply_to": (), "deadline_s": 150}
    assert "Quinn" in call["message"] and "hb:e1" in call["message"]
    row = (await _noticings())[0]
    assert row.status == "spoken" and row.channel == "note" and row.note_id and row.thought_id == "thought-1"


def test_the_policy_for_a_thread_reaches_only_that_chat():
    pol = noticing_service.speak_policy_kwargs("thread", "chat-trivia", 90)
    assert pol["may_reach_people"] is True and pol["reply_to"] == ("chat-trivia",) and pol["may_push"] is False
    assert noticing_service.speak_policy_kwargs("push", "c", 180)["may_push"] is True


async def test_a_drop_closing_is_dropped(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake(note="DROP: Ellis sent the deck at 2:30 pm"))
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    row = (await _noticings())[0]
    assert row.status == "dropped" and "sent the deck" in row.decision_reason


async def test_nothing_delivered_is_silent(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake(note="LEFT: he is on it"))
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    row = (await _noticings())[0]
    assert row.status == "silent" and "speaker left it" in row.decision_reason


async def test_a_send_into_the_chat_is_spoken_in_the_thread(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake(note="Told the crew.",
                                                       tools=[{"n": "send_message", "ok": True}]))
    await _event("e1", "found any spare tickets under $50 yet?", chat="chat-trivia", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item(kind="follow_through", category="social", chat_identifier="chat-trivia",
                                      suggested="thread", closes_at=(_clock() + timedelta(minutes=40)).isoformat(),
                                      dedupe_key="trivia-pair")))
    monkeypatch.setattr(noticing_service, "_open_ping_chats", _async_set({"chat-trivia"}))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    row = (await _noticings())[0]
    assert row.status == "spoken" and row.channel == "thread"


def _async_set(value):
    async def f(*a, **k):
        return frozenset(value)

    return f


async def test_a_failed_wake_is_retried_through_the_gate(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake(ok=False))
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    row = (await _noticings())[0]
    assert row.status == "failed" and row.attempts == 1 and row.due_at is not None
    # The sweep re-gates it once due; a second failure is final.
    out = await noticing_service._sweep_due(datetime.now(timezone.utc) + timedelta(minutes=11))
    await noticing_service.drain()
    assert out["due"] == 1
    row = (await _noticings())[0]
    assert row.status == "failed" and row.attempts == 2
    out = await noticing_service._sweep_due(datetime.now(timezone.utc) + timedelta(minutes=30))
    assert out["due"] == 0


async def test_a_failed_wake_on_a_floor_item_closing_soon_sends_the_templated_push(clean_db, llm, monkeypatch,
                                                                                   _isolated):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake(ok=False))
    await _event("e1", "Skyway: online check-in closes at 10 pm", chat="chat-sky", chat_name="Skyway",
                 sender="Skyway", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item(kind="deadline", category="travel", who=["Skyway"], suggested="push",
                                      chat_identifier="chat-sky",
                                      closes_at=(_clock() + timedelta(minutes=20)).isoformat(),
                                      dedupe_key="flight-checkin")))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    assert len(_isolated.calls) == 1
    push = _isolated.calls[0]
    assert push["source"] == "reach:fallback"
    assert "Skyway" in push["body"] and "couldn't check it in time" in push["body"]
    row = (await _noticings())[0]
    assert row.status == "spoken" and row.channel == "push"


async def test_the_cap_on_a_floor_item_is_the_fallback_push_with_no_model(clean_db, llm, monkeypatch, _isolated):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    wake = FakeWake()
    monkeypatch.setattr(mind_service, "wake", wake)
    row_id = await noticing_service.add_noticing(
        kind="deadline", category="travel", claim="Skyway check-in closes at 10 pm", who=["Skyway"], chat_identifier="chat-sky",
        sources=[{"ref": "hb:e1", "chat": "chat-sky", "at": (_clock() - timedelta(minutes=30)).isoformat(),
                  "from_me": False}],
        closes_at=_clock() + timedelta(minutes=50), suggested="push", dedupe_key="flight-checkin")
    monkeypatch.setattr(noticing_service, "_quota_word", lambda: "exhausted")
    await noticing_service.gate_and_act(row_id)
    await noticing_service.drain()
    assert wake.calls == [] and _isolated.calls[0]["source"] == "reach:fallback"


async def test_speak_turns_are_counted_in_the_ledger(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake(deliver=OFFER))
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    rows = await _ledger("speak")
    assert len(rows) == 1 and rows[0].outcome == "spoken"   # the reservation, finished
    today, by_chat = await noticing_service._speak_counts(datetime.now(timezone.utc))
    assert today == 1 and by_chat.get("chat-quinn") == 1


# ------------------------------------------------------------- the hand-off --

async def test_request_notice_in_shadow_coalesces_into_one_pass(clean_db, llm, monkeypatch):
    monkeypatch.setattr(noticing_service, "COALESCE_S", 0.05)
    await _event("e1", "one")
    await _event("e2", "two")
    assert noticing_service.request_notice(["e1"]) is True
    assert noticing_service.request_notice(["e2"]) is True
    await noticing_service.drain()
    assert len(llm["llm"].calls) == 1
    human = llm["llm"].calls[0]["messages"][1].content
    assert "hb:e1" in human and "hb:e2" in human


async def test_the_messages_shim_selects_escalated_events_in_shadow(clean_db, llm, monkeypatch):
    monkeypatch.setattr(noticing_service, "COALESCE_S", 0.05)
    await _event("e1", "Can you send the deck by 6?")
    assert noticing_service.request_notice(None) is True
    await noticing_service.drain()
    assert (await _events())["e1"].notification_batch_id.startswith("notice:")


async def test_off_mode_never_calls_the_noticer(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    await _event("e1", "Can you send the deck by 6?")
    noticing_service.request_notice(["e1"])
    await noticing_service.drain()
    assert llm["llm"].calls == []
    assert (await _events())["e1"].notification_batch_id.startswith("stored:")


# ------------------------------------------- a promise whose time came --

async def _intention(mid, *, turn_source="ping", lapsed_min=30, closed=False, chat="chat-trivia"):
    from services.database import MemoryModel, async_session

    ref = {"kind": "extraction", "turn_source": turn_source, "chat_identifier": chat}
    if turn_source.startswith("ping"):
        ref["ping_id"] = 12
    if closed:
        ref["closed"] = {"at": "2026-10-11T10:00:00Z", "outcome": "done"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        s.add(MemoryModel(id=mid, content="Tell the trivia crew when a spare pair of tickets under $50 shows up",
                          memory_type="intention", origin="thought", about="self", source_ref=json.dumps(ref),
                          created_at=now - timedelta(days=1), valid_until=now - timedelta(minutes=lapsed_min)))
        await s.commit()


async def test_a_ping_promise_past_its_horizon_becomes_a_follow_through(clean_db, llm):
    await _intention("i1")
    out = await noticing_service._sweep_intentions(datetime.now(timezone.utc))
    assert out["intentions"] == 1
    rows = await _noticings()
    assert len(rows) == 1 and rows[0].kind == "follow_through" and rows[0].origin == "sweep"
    assert rows[0].chat_identifier == "chat-trivia" and rows[0].dedupe_key == "intention:i1"
    assert "under $50" in rows[0].claim
    # Keyed: the next sweep does not add it again.
    await noticing_service._sweep_intentions(datetime.now(timezone.utc))
    assert len(await _noticings()) == 1


async def test_closed_waiting_or_non_ping_promises_are_left_alone(clean_db, llm):
    from services.database import ScheduledEventModel, async_session

    await _intention("closed", closed=True)
    await _intention("not-ping", turn_source="chat")
    await _intention("old", lapsed_min=60 * 30)
    await _intention("waiting")
    async with async_session() as s:
        s.add(ScheduledEventModel(description="look again", status="pending",
                                  scheduled_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                  next_fire_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
                                  event_metadata=json.dumps({"memory_id": "waiting"})))
        await s.commit()
    out = await noticing_service._sweep_intentions(datetime.now(timezone.utc))
    assert out["intentions"] == 0 and await _noticings() == []


async def test_off_mode_turns_no_promise_into_a_noticing(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    await _intention("i1")
    assert (await noticing_service._sweep_intentions(datetime.now(timezone.utc)))["intentions"] == 0


async def test_two_noticings_in_one_chat_get_one_speak_turn_an_hour(clean_db, llm, monkeypatch):
    # The slot is reserved at decision time, so a second decision in the same
    # pass already sees the first speak turn.
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    fake = FakeWake(note="LEFT: fine")
    monkeypatch.setattr(mind_service, "wake", fake)
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    await _event("e2", "And the budget sheet by 7?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item(), _item(sources=["hb:e2"], dedupe_key="sheet-by-7",
                                               claim="Quinn's text asks for the budget sheet by 7 pm.")))
    await noticing_service.notice_pass(["e1", "e2"])
    await noticing_service.drain()
    assert len(fake.calls) == 1
    statuses = sorted(r.status for r in await _noticings())
    assert statuses == ["silent", "silent"]          # one spoke (and left it), one hit the per-chat limit


async def test_the_speaker_runs_in_the_background_of_the_gate(clean_db, llm, monkeypatch):
    import asyncio

    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    gate = asyncio.Event()

    async def slow_wake(reason, **kw):
        await gate.wait()
        return {"ok": True, "note": "LEFT: fine"}

    monkeypatch.setattr(mind_service, "wake", slow_wake)
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item()))
    out = await asyncio.wait_for(noticing_service.notice_pass(["e1"]), 30)
    assert out["noticed"] == 1                         # returned while the speaker still waits
    gate.set()
    await noticing_service.drain()
    assert (await _noticings())[0].status == "silent"


async def test_an_event_a_pass_already_read_is_not_read_again(clean_db, llm):
    await _event("e1", "Can you send the deck by 6?")
    await noticing_service.notice_pass(["e1"])
    await noticing_service.notice_pass(["e1"])          # the hand-off and the shim both named it
    assert len(llm["llm"].calls) == 1


async def test_a_secret_in_someone_elses_line_is_redacted_but_not_a_security_noticing(clean_db, llm):
    await _event("s2", f"use this api key: {KEY}", chat="chat-dev", chat_name="Dev")
    await noticing_service.notice_pass(["s2"])
    human = llm["llm"].calls[0]["messages"][1].content
    assert KEY not in human and "[secret]" in human
    assert [r for r in await _noticings() if r.kind == "security"] == []


async def test_secrets_in_chat_context_lines_are_redacted_too(clean_db, llm):
    await _event("old", f"the wifi key is {KEY}", chat="chat-quinn", ago_min=120, from_me=True)
    await _event("e1", "Can you send the deck by 6?", chat="chat-quinn", ago_min=20)
    await noticing_service.notice_pass(["e1"])
    human = llm["llm"].calls[0]["messages"][1].content
    assert KEY not in human and "hb:old" in human


async def test_the_messages_shim_also_brings_owners_own_unstamped_lines(clean_db, llm, monkeypatch):
    # The identity filter dismisses Ellis's own lines, but they carry his
    # commitments and the keys he pastes: the noticer reads them too.
    monkeypatch.setattr(noticing_service, "COALESCE_S", 0.05)
    await _event("owner", f"staging key {KEY}", from_me=True, status="dismissed", chat="chat-work", ago_min=10)
    await _event("e1", "Can you send the deck by 6?")
    assert noticing_service.request_notice(None) is True
    await noticing_service.drain()
    assert [r.kind for r in await _noticings()] == ["security"]
    assert (await _events())["owner"].notification_batch_id.startswith("notice:")


async def test_the_off_mode_stub_leaves_owners_lines_alone(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    await _event("owner", "en route", from_me=True, status="dismissed", ago_min=10)
    await noticing_service.stub_pass(None)
    assert (await _events())["owner"].notified_at is None


async def test_the_speak_pack_carries_the_ladders_advice(clean_db, llm, monkeypatch):
    from services.database import NoticingModel, async_session

    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    fake = FakeWake(note="LEFT: fine")
    monkeypatch.setattr(mind_service, "wake", fake)
    then = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    async with async_session() as s:
        for i, outcome in enumerate(("acted", "ignored", "ignored")):
            s.add(NoticingModel(id=f"h{i}", kind="deadline", category="work", claim="c", status="spoken",
                                channel="note" if i == 0 else "push", surfaced_at=then + timedelta(minutes=i),
                                created_at=then, outcome=outcome))
        await s.commit()
    await _event("e1", "Can you send the deck by 6?", ago_min=30)
    llm["llm"] = FakeLLM(_reply(_item(suggested="push", dedupe_key="deck-advice")))
    monkeypatch.setenv("EDWARD_NOTICE_PUSH", "gate")
    await noticing_service.notice_pass(["e1"])
    await noticing_service.drain()
    message = fake.calls[0]["message"]
    assert "No daily push limit." in message
    assert "How Ellis has answered this kind of thing" in message and "work" in message


async def test_the_sweep_never_waits_on_a_retried_model_call(clean_db, llm, monkeypatch):
    import asyncio

    await _event("e1", "Can you send the deck by 6?")
    llm["llm"] = FakeLLM("not json")
    await noticing_service.notice_pass(["e1"])
    release = asyncio.Event()

    class Slow(FakeLLM):
        async def ainvoke(self, messages):
            await release.wait()
            return await super().ainvoke(messages)

    llm["llm"] = Slow(_reply(_item()))
    out = await asyncio.wait_for(noticing_service._sweep_retry_failed_passes(datetime.now(timezone.utc) + timedelta(minutes=11)), 10)
    assert out["retried"] == 1
    release.set()
    await noticing_service.drain()
    assert (await _events())["e1"].notification_batch_id.startswith("notice:")


# ------------------------------------------------ nothing is left open --
# Review 2026-10-11 (minor, verified): a noticing the gate set ``open`` whose
# speak wake died with the process (every deploy is a SIGKILL kickstart), or
# whose surface or gate raised, stayed ``open`` with no due_at: no sweep step
# looked at it again, and for 72 h it absorbed every re-noticing of the same
# thing (``_existing_for`` matches open; ``_merge_into`` with no new sources
# re-gates nothing).

async def _travel_noticing(*, closes_in=timedelta(hours=2), key="sky-6am"):
    return await noticing_service.add_noticing(
        kind="deadline", category="travel", claim="Skyway moved the flight to 6 am tomorrow.", who=["Skyway"],
        chat_identifier="chat-sky",
        sources=[{"ref": "hb:e1", "chat": "chat-sky", "at": (_clock() - timedelta(minutes=30)).isoformat(),
                  "from_me": False}],
        closes_at=_clock() + closes_in, suggested="push", dedupe_key=key)


def _gate_recorder(monkeypatch):
    real = noticing_service.gate_and_act
    gated = []

    async def recording(nid, now=None):
        gated.append(nid)
        return await real(nid, now=now)

    monkeypatch.setattr(noticing_service, "gate_and_act", recording)
    return gated


async def _age(nid, **values):
    """Write the row as a dead process left it (``_update_noticing`` would
    stamp ``updated_at`` now)."""
    from sqlalchemy import update

    from services.database import NoticingModel, async_session

    values = {k: (_naive(v) if isinstance(v, datetime) else v) for k, v in values.items()}
    async with async_session() as s:
        await s.execute(update(NoticingModel).where(NoticingModel.id == nid).values(**values))
        await s.commit()


async def _row_of(nid):
    return {r.id: r for r in await _noticings()}[nid]


async def test_an_open_noticing_left_by_a_restart_is_regated_by_the_sweep(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    wake = FakeWake(note="LEFT: he has already rebooked")
    monkeypatch.setattr(mind_service, "wake", wake)
    nid = await _travel_noticing()
    long_ago = _clock() - timedelta(hours=2)
    # The gate decided push and the speak wake started (attempt 1); then the
    # backend was kickstarted: no task survives, the row says open.
    await _age(nid, status="open", decision="push", decided_at=long_ago, attempts=1, updated_at=long_ago)
    gated = _gate_recorder(monkeypatch)
    out = await noticing_service.sweep()
    await noticing_service.drain()
    assert out.get("stranded") == 1 and gated == [nid]
    assert wake.calls, "re-gated and spoken again"
    row = await _row_of(nid)
    assert row.status != "open"


async def test_a_never_gated_open_noticing_is_gated_by_the_sweep(clean_db, llm, monkeypatch):
    """add_noticing writes ``open``; a crash before its gate left it there."""
    nid = await _travel_noticing()
    await _age(nid, created_at=_clock() - timedelta(minutes=20))
    gated = _gate_recorder(monkeypatch)
    await noticing_service.sweep()
    assert gated == [nid]
    assert (await _row_of(nid)).status == "shadow"


async def test_a_fresh_open_noticing_is_left_to_its_speaker(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    nid = await _travel_noticing()
    await noticing_service._update_noticing(nid, status="open", decision="push", decided_at=_clock(), attempts=1)
    gated = _gate_recorder(monkeypatch)
    out = await noticing_service.sweep()
    assert out.get("stranded") == 0 and gated == []
    assert (await _row_of(nid)).status == "open"


async def test_a_noticing_whose_speaker_is_still_running_is_never_taken_for_stranded(clean_db, llm, monkeypatch):
    import asyncio

    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    release = asyncio.Event()

    async def slow_wake(reason, **kw):
        await release.wait()
        return {"ok": True, "note": "LEFT: fine"}

    monkeypatch.setattr(mind_service, "wake", slow_wake)
    nid = await _travel_noticing()
    await noticing_service.gate_and_act(nid)
    await asyncio.sleep(0.05)
    assert (await _row_of(nid)).status == "open"
    out = await noticing_service._sweep_stranded(datetime.now(timezone.utc) + timedelta(hours=1))
    assert out["stranded"] == 0
    release.set()
    await noticing_service.drain()
    assert (await _row_of(nid)).status == "silent"


async def test_a_surface_that_raises_leaves_a_failed_noticing_due_a_retry(clean_db, llm, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(mind_service, "wake", FakeWake())

    async def broken_pack(*a, **k):
        raise RuntimeError("the pack could not be read")

    monkeypatch.setattr(noticing_service, "_speak_pack", broken_pack)
    nid = await _travel_noticing(closes_in=timedelta(hours=10), key="sky-later")
    from services import speak_gate

    monkeypatch.setattr(speak_gate, "decide", lambda n, ctx: speak_gate.Decision(
        channel="note", hold_until=None, reason="test: a note", floor_push=False))
    await noticing_service.gate_and_act(nid)
    await noticing_service.drain()
    row = await _row_of(nid)
    assert row.status == "failed" and row.attempts == 1 and row.due_at is not None
    assert "RuntimeError" in (row.decision_reason or "")


async def test_a_gate_that_raises_leaves_a_failed_noticing_the_sweep_regates(clean_db, llm, monkeypatch):
    nid = await _travel_noticing()

    async def broken_context(*a, **k):
        raise RuntimeError("the database blinked")

    real_context = noticing_service.gate_context
    monkeypatch.setattr(noticing_service, "gate_context", broken_context)
    await noticing_service._gate_safely(nid)
    row = await _row_of(nid)
    assert row.status == "failed" and row.due_at is not None and row.attempts == 1
    monkeypatch.setattr(noticing_service, "gate_context", real_context)
    out = await noticing_service._sweep_due(datetime.now(timezone.utc) + timedelta(minutes=11))
    assert out["due"] == 1
    assert (await _row_of(nid)).status == "shadow"


async def test_a_gate_that_keeps_raising_gives_up(clean_db, llm, monkeypatch):
    nid = await _travel_noticing()

    async def broken_context(*a, **k):
        raise RuntimeError("still down")

    monkeypatch.setattr(noticing_service, "gate_context", broken_context)
    await noticing_service._gate_safely(nid)
    await noticing_service._sweep_due(datetime.now(timezone.utc) + timedelta(minutes=11))
    row = await _row_of(nid)
    assert row.status == "failed" and row.due_at is None
    out = await noticing_service._sweep_due(datetime.now(timezone.utc) + timedelta(hours=1))
    assert out["due"] == 0
