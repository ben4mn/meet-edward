"""What came of what Edward said: ``classify_outcome`` and the sweep that runs it.

Why (proactivity-spec §2.8, 2026-10-11): learning from being ignored needs
honest outcomes. Lock-screen reads are invisible, and Ellis is often already
mid-conversation when Edward speaks, so: a tapback or inline reply on
Edward's message, a logged dose, or Ellis messaging the chat (only if he was
not already in exchange there) is ``acted``; a push followed by Ellis being
active elsewhere is ``seen`` (neutral); only a closed window with nothing at
all is ``ignored``. First match wins; the table rows are tested one by one.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import noticing_service as ns

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 3, 21, 47, tzinfo=timezone.utc)


def item(**kw):
    base = dict(channel="push", surfaced_at=T0, closes_at=T0 + timedelta(minutes=13), chat="chat-ba",
                who=("BA",), kind="conflict", origin="noticer")
    base.update(kw)
    return ns.OutcomeItem(**base)


def signals(**kw):
    base = dict(verdict=None, push_opened_at=None, reaction_at=None, reply_at=None, care_logged_at=None,
                owner_lines=(), owner_prior_in_chat_30m=False, note_dismissed_at=None)
    base.update(kw)
    return ns.OutcomeSignals(**base)


def line(chat, minutes, chat_name=None):
    return ns.OwnerLine(chat=chat, at=T0 + timedelta(minutes=minutes), chat_name=chat_name)


def test_1_a_wrong_call_verdict_is_rejected_whatever_else():
    s = signals(verdict="wrong_call", push_opened_at=T0 + timedelta(minutes=1))
    assert ns.classify_outcome(item(), s, T0 + timedelta(hours=1)) == "rejected"


def test_2_an_opened_push_is_opened():
    s = signals(push_opened_at=T0 + timedelta(minutes=2))
    assert ns.classify_outcome(item(), s, T0 + timedelta(minutes=3)) == "opened"


def test_2b_a_right_call_verdict_is_opened():
    assert ns.classify_outcome(item(), signals(verdict="right_call"), T0) == "opened"


def test_3_a_tapback_on_edwards_message_is_acted():
    s = signals(reaction_at=T0 + timedelta(minutes=1))
    assert ns.classify_outcome(item(channel="thread"), s, T0 + timedelta(minutes=2)) == "acted"


def test_3_an_inline_reply_is_acted():
    s = signals(reply_at=T0 + timedelta(minutes=1))
    assert ns.classify_outcome(item(channel="thread"), s, T0 + timedelta(minutes=2)) == "acted"


def test_3_a_dose_logged_after_the_alert_is_acted():
    s = signals(care_logged_at=T0 + timedelta(minutes=4))
    assert ns.classify_outcome(item(origin="care", kind="household"), s, T0 + timedelta(minutes=5)) == "acted"


def test_3_owner_messaging_the_chat_before_it_closes_is_acted():
    s = signals(owner_lines=(line("chat-ba", 3),))
    assert ns.classify_outcome(item(), s, T0 + timedelta(minutes=4)) == "acted"


def test_3_owner_messaging_a_named_person_is_acted():
    s = signals(owner_lines=(line("chat-other", 3, chat_name="BA Support"),))
    assert ns.classify_outcome(item(), s, T0 + timedelta(minutes=4)) == "acted"


def test_3_a_message_after_the_close_is_not_acted():
    s = signals(owner_lines=(line("chat-ba", 30),))
    assert ns.classify_outcome(item(), s, T0 + timedelta(hours=1)) != "acted"


def test_3_without_a_close_the_window_is_a_day():
    s = signals(owner_lines=(line("chat-ba", 60 * 20),))
    assert ns.classify_outcome(item(closes_at=None), s, T0 + timedelta(hours=21)) == "acted"


def test_3_already_in_exchange_is_confounded_and_only_seen():
    s = signals(owner_lines=(line("chat-ba", 3),), owner_prior_in_chat_30m=True)
    assert ns.classify_outcome(item(), s, T0 + timedelta(minutes=4)) is None          # window still open
    assert ns.classify_outcome(item(), s, T0 + timedelta(hours=1)) == "seen"


def test_4_a_dismissed_note_is_dismissed():
    s = signals(note_dismissed_at=T0 + timedelta(hours=2))
    assert ns.classify_outcome(item(channel="note"), s, T0 + timedelta(hours=3)) == "dismissed"


def test_5_a_push_then_owner_active_elsewhere_within_ten_minutes_is_seen():
    s = signals(owner_lines=(line("chat-home", 6),))
    assert ns.classify_outcome(item(), s, T0 + timedelta(hours=1)) == "seen"


def test_5_activity_after_ten_minutes_does_not_make_it_seen():
    s = signals(owner_lines=(line("chat-home", 12),))
    assert ns.classify_outcome(item(), s, T0 + timedelta(hours=1)) == "ignored"


def test_6_nothing_once_the_window_closed_is_ignored():
    assert ns.classify_outcome(item(), signals(), T0 + timedelta(hours=1)) == "ignored"


def test_6_a_note_with_nothing_is_ignored_after_a_day():
    it = item(channel="note", closes_at=None)
    assert ns.classify_outcome(it, signals(), T0 + timedelta(hours=23)) is None
    assert ns.classify_outcome(it, signals(), T0 + timedelta(hours=25)) == "ignored"


def test_nothing_yet_inside_the_window_is_none():
    assert ns.classify_outcome(item(), signals(), T0 + timedelta(minutes=5)) is None


# ------------------------------------------------------------ the sweep --

@pytest.mark.integration
async def test_the_sweep_stamps_outcomes_on_spoken_noticings(clean_db):
    from sqlalchemy import select

    from services.database import EdwardNoteModel, NoticingModel, PushLogModel, async_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        s.add(PushLogModel(id="push-1", source="reach:speak", title="t", body="b", sent=1, failed=0,
                           sent_at=now - timedelta(hours=2), opened_at=now - timedelta(hours=1, minutes=55)))
        s.add(EdwardNoteModel(id="note-1", kind="mind", title="t", body="b", created_at=now - timedelta(hours=30),
                              dismissed_at=now - timedelta(hours=29)))
        s.add(NoticingModel(id="a", kind="conflict", category="travel", claim="c", status="spoken", channel="push",
                            push_log_id="push-1", surfaced_at=now - timedelta(hours=2),
                            closes_at=now - timedelta(hours=1)))
        s.add(NoticingModel(id="b", kind="unanswered", category="work", claim="c", status="spoken", channel="note",
                            note_id="note-1", surfaced_at=now - timedelta(hours=30)))
        s.add(NoticingModel(id="c", kind="deadline", category="work", claim="c", status="spoken", channel="note",
                            surfaced_at=now - timedelta(minutes=5)))
        await s.commit()
    out = await ns._sweep_outcomes(datetime.now(timezone.utc))
    assert out["outcomes"] == 2
    async with async_session() as s:
        rows = {r.id: r for r in (await s.execute(select(NoticingModel))).scalars()}
    assert rows["a"].outcome == "opened" and rows["a"].outcome_at is not None
    assert rows["b"].outcome == "dismissed"
    assert rows["c"].outcome is None


@pytest.mark.integration
async def test_a_wrong_call_on_the_speak_wake_is_rejected(clean_db):
    import json

    from sqlalchemy import select

    from services.database import NoticingModel, UserSignalModel, async_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        s.add(UserSignalModel(kind="mind_verdict", ref_id="77", detail_json=json.dumps({"verdict": "wrong_call"})))
        s.add(NoticingModel(id="r", kind="deadline", category="family", claim="c", status="spoken", channel="note",
                            ledger_id=77, surfaced_at=now - timedelta(minutes=30)))
        await s.commit()
    await ns._sweep_outcomes(datetime.now(timezone.utc))
    async with async_session() as s:
        r = (await s.execute(select(NoticingModel).where(NoticingModel.id == "r"))).scalar_one()
    assert r.outcome == "rejected"


@pytest.mark.integration
async def test_owner_texting_the_chat_after_a_note_is_acted(clean_db):
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, NoticingModel, async_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        s.add(NoticingModel(id="t", kind="unanswered", category="work", claim="c", status="spoken", channel="note",
                            chat_identifier="chat-quinn", surfaced_at=now - timedelta(hours=2)))
        s.add(HeartbeatEventModel(id="owner-1", source="imessage", event_type="message_sent", is_from_user=True,
                                  chat_identifier="chat-quinn", summary="here's the link",
                                  created_at=now - timedelta(hours=1)))
        await s.commit()
    await ns._sweep_outcomes(datetime.now(timezone.utc))
    async with async_session() as s:
        r = (await s.execute(select(NoticingModel).where(NoticingModel.id == "t"))).scalar_one()
    assert r.outcome == "acted"


@pytest.mark.integration
async def test_a_dose_logged_after_a_care_alert_is_acted(clean_db):
    from datetime import time as dtime

    from sqlalchemy import select

    from services.database import CareEventModel, CareItemModel, NoticingModel, async_session

    from sqlalchemy import delete

    async def _clear():   # clean_db does not truncate the hub's tables
        async with async_session() as s:
            await s.execute(delete(CareEventModel).where(CareEventModel.care_item_id.in_(("item-am", "item-pm"))))
            await s.execute(delete(CareItemModel).where(CareItemModel.id.in_(("item-am", "item-pm"))))
            await s.commit()

    await _clear()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        s.add(CareItemModel(id="item-pm", pet_id="biscuit", kind="med", name="Carprofen", schedule_type="timed",
                            window="pm", at_time=dtime(18, 0)))
        s.add(CareItemModel(id="item-am", pet_id="biscuit", kind="med", name="Carprofen", schedule_type="timed",
                            window="am", at_time=dtime(6, 0)))
        s.add(CareEventModel(care_item_id="item-am", given_at=now - timedelta(minutes=10)))   # the wrong slot
        s.add(NoticingModel(id="care-1", kind="household", category="household", claim="c", origin="care",
                            status="spoken", channel="push", surfaced_at=now - timedelta(hours=2),
                            dedupe_key="care:biscuit:2026-10-11:18:00"))
        s.add(NoticingModel(id="care-2", kind="household", category="household", claim="c", origin="care",
                            status="spoken", channel="push", surfaced_at=now - timedelta(hours=2),
                            dedupe_key="care:biscuit:2026-10-11:06:00"))
        await s.commit()
    try:
        await ns._sweep_outcomes(datetime.now(timezone.utc))
        async with async_session() as s:
            rows = {r.id: r for r in (await s.execute(select(NoticingModel))).scalars()}
        assert rows["care-2"].outcome == "acted"      # the 6:00 dose was logged after its alert
        assert rows["care-1"].outcome is None         # the 6:00 pm one is not, and its day is still open
    finally:
        await _clear()
