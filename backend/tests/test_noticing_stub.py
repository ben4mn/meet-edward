"""``noticing_service`` in ``off`` mode: store and stamp, never a model call.

Why (proactivity-spec §2.3(f), §1.4, 2026-10-11): the ``messages`` wake used
to stamp every escalated event it was shown; with it retired, nothing would
stamp them and Today would flood with every inbound message. In ``off`` mode
``request_notice`` stamps each non-ping event ``stored:<pass>`` and writes one
ledger row per pass. It never stamps a ping, a mention or Ellis's self-chat
line: the ping runner (or, with ``EDWARD_PINGS=off``, the mention wake) owns
those, and the mention wake decides "still waiting" by ``notified_at``.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from services import noticing_service

pytestmark = pytest.mark.integration


async def _event(eid, *, status="escalated", raw=None, summary="hello", from_me=False, notified=None,
                 created=None):
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(
            id=eid, source="imessage", event_type="message_received", sender="+12015550100",
            chat_identifier="chat-1", chat_name="Friends", summary=summary,
            raw_data=json.dumps(raw) if raw is not None else None, is_from_user=from_me,
            triage_status=status, notified_at=notified,
            created_at=created or datetime.now(timezone.utc).replace(tzinfo=None)))
        await s.commit()


async def _rows():
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        return {r.id: r for r in (await s.execute(select(HeartbeatEventModel))).scalars()}


async def _notice_ledger():
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "notice"))).scalars())


# ---------------------------------------------------------------- mode --

@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (None, "off"), ("", "off"), ("off", "off"), ("shadow", "shadow"), ("NOTICE", "notice"),
    (" notice ", "notice"), ("legacy", "off"), ("on", "off"),
])
def test_mode_reads_the_env_and_unknown_means_off(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("EDWARD_PROACTIVITY", raising=False)
    else:
        monkeypatch.setenv("EDWARD_PROACTIVITY", value)
    assert noticing_service.mode() == expected


@pytest.mark.unit
def test_push_scope_defaults_to_floor(monkeypatch):
    monkeypatch.delenv("EDWARD_NOTICE_PUSH", raising=False)
    assert noticing_service.push_scope() == "floor"
    monkeypatch.setenv("EDWARD_NOTICE_PUSH", "gate")
    assert noticing_service.push_scope() == "gate"
    monkeypatch.setenv("EDWARD_NOTICE_PUSH", "everything")
    assert noticing_service.push_scope() == "floor"


# ---------------------------------------------------------------- stub --

async def test_off_stamps_the_events_stored(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    await _event("e1")
    await _event("e2", status="pending")
    out = await noticing_service.stub_pass(["e1", "e2"])
    rows = await _rows()
    assert rows["e1"].notified_at is not None and rows["e2"].notified_at is not None
    assert rows["e1"].notification_batch_id == f"stored:{out['pass_id']}"
    assert out["stamped"] == 2


async def test_never_stamps_a_ping_by_status_or_by_marker(clean_db):
    await _event("p1", status="ping")
    await _event("p2", status="pending", raw={"ping": {"kind": "mention", "ping_id": 7}})
    await _event("ok", status="pending")
    out = await noticing_service.stub_pass(["p1", "p2", "ok"])
    rows = await _rows()
    assert rows["p1"].notified_at is None and rows["p1"].notification_batch_id is None
    assert rows["p2"].notified_at is None and rows["p2"].notification_batch_id is None
    assert rows["ok"].notified_at is not None
    assert out["pings"] == 2 and out["stamped"] == 1


async def test_never_stamps_a_mention_or_owners_self_chat(clean_db):
    # The mention wake decides "still waiting" by notified_at: stamping these
    # would silence it (EDWARD_PINGS=off, or a degraded ping store).
    await _event("m1", summary="@Edward can you check the gate?")
    await _event("m2", raw={"has_mentions": True})
    await _event("o1", from_me=True, raw={"owner_direct": True}, summary="what time is the vet?")
    await _event("plain", summary="Edward Jones called")
    await noticing_service.stub_pass(["m1", "m2", "o1", "plain"])
    rows = await _rows()
    assert rows["m1"].notified_at is None and rows["m2"].notified_at is None and rows["o1"].notified_at is None
    assert rows["plain"].notified_at is not None


async def test_a_full_text_mention_is_seen_even_when_the_summary_is_clipped(clean_db):
    await _event("m3", summary="long message…", raw={"full_text": "long message about soccer @edward thoughts?"})
    await noticing_service.stub_pass(["m3"])
    assert (await _rows())["m3"].notified_at is None


async def test_an_already_stamped_event_keeps_its_stamp(clean_db):
    stamped_at = datetime(2026, 10, 10, 12, 0)
    await _event("s1", notified=stamped_at)
    await noticing_service.stub_pass(["s1"])
    r = (await _rows())["s1"]
    assert r.notified_at == stamped_at and r.notification_batch_id is None


async def test_none_selects_escalated_unstamped_events_from_the_last_day(clean_db):
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).replace(tzinfo=None)
    await _event("fresh")
    await _event("pending", status="pending")
    await _event("old", created=old)
    await _event("done", notified=datetime(2026, 10, 10, 12, 0))
    out = await noticing_service.stub_pass(None)
    rows = await _rows()
    assert rows["fresh"].notified_at is not None
    assert rows["pending"].notified_at is None
    assert rows["old"].notified_at is None
    assert out["stamped"] == 1


async def test_each_pass_writes_one_ledger_row(clean_db):
    await _event("a")
    await _event("b")
    out = await noticing_service.stub_pass(["a", "b"])
    await noticing_service.stub_pass([])
    rows = await _notice_ledger()
    assert len(rows) == 2
    first = [r for r in rows if r.ref_id == out["pass_id"]][0]
    assert first.source == "stub" and first.outcome == "ok"
    assert json.loads(first.detail_json)["stamped"] == 2


async def test_request_notice_returns_at_once_and_the_pass_runs_in_the_background(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    await _event("bg")
    assert noticing_service.request_notice(["bg"]) is True
    await noticing_service.drain()
    assert (await _rows())["bg"].notification_batch_id.startswith("stored:")


async def test_request_notice_never_raises(clean_db, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(noticing_service, "stub_pass", boom)
    assert noticing_service.request_notice(["x"]) in (True, False)
    await noticing_service.drain()


def test_request_notice_without_a_loop_returns_false():
    assert noticing_service.request_notice(["x"]) is False


async def test_shadow_and_notice_still_stamp_until_the_noticer_lands(clean_db, monkeypatch):
    # P0 ships the stub for every mode; P1's noticer replaces it in
    # shadow/notice. Whatever the mode, Today must not flood.
    monkeypatch.setenv("EDWARD_PROACTIVITY", "shadow")
    monkeypatch.setattr(noticing_service, "noticer_available", lambda: False)
    await _event("sh")
    assert noticing_service.request_notice(["sh"]) is True
    await noticing_service.drain()
    assert (await _rows())["sh"].notification_batch_id.startswith("stored:")


# ------------------------------------------------------------ add_noticing --

async def test_add_noticing_writes_a_row_and_returns_its_id(clean_db):
    from services.database import NoticingModel, async_session

    nid = await noticing_service.add_noticing(
        kind="household", claim="Biscuit's 6:00 pm dose isn't logged", origin="care", category="household",
        status="spoken", channel="push", dedupe_key="care:item-1:2026-10-11:18:00")
    async with async_session() as s:
        n = (await s.execute(select(NoticingModel).where(NoticingModel.id == nid))).scalar_one()
    assert n.origin == "care" and n.status == "spoken" and n.channel == "push"


async def test_add_noticing_is_keyed_for_code_origins(clean_db):
    k = "care:item-1:2026-10-11:18:00"
    a = await noticing_service.add_noticing(kind="household", claim="x", origin="care", dedupe_key=k)
    b = await noticing_service.add_noticing(kind="household", claim="x", origin="care", dedupe_key=k)
    assert a == b


async def test_add_noticing_stores_sources_and_who_as_json(clean_db):
    from services.database import NoticingModel, async_session

    nid = await noticing_service.add_noticing(
        kind="conflict", claim="c", sources=[{"ref": "hb:1", "chat": "BA", "at": "2026-10-03T21:31:00Z",
                                              "from_me": False}], who=["BA"])
    async with async_session() as s:
        n = (await s.execute(select(NoticingModel).where(NoticingModel.id == nid))).scalar_one()
    assert json.loads(n.sources_json)[0]["ref"] == "hb:1" and json.loads(n.who_json) == ["BA"]


# ----------------------------------------------------------------- sweep --

async def test_sweep_never_raises_and_prunes_noticings_past_30_days(clean_db, monkeypatch):
    from services.database import NoticingModel, async_session

    old = (datetime.now(timezone.utc) - timedelta(days=31)).replace(tzinfo=None)
    async with async_session() as s:
        s.add(NoticingModel(id="old", kind="deadline", claim="old", created_at=old))
        s.add(NoticingModel(id="new", kind="deadline", claim="new"))
        await s.commit()
    monkeypatch.setattr(noticing_service, "_last_prune_day", None)
    out = await noticing_service.sweep()
    assert out["pruned"] == 1
    async with async_session() as s:
        ids = {n.id for n in (await s.execute(select(NoticingModel))).scalars()}
    assert ids == {"new"}
    assert noticing_service.last_sweep_at() is not None


async def test_sweep_survives_a_broken_database(monkeypatch):
    import services.database as database

    def broken(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(database, "async_session", broken)
    monkeypatch.setattr(noticing_service, "_last_prune_day", None)
    out = await noticing_service.sweep()
    assert isinstance(out, dict) and "error" in out


async def test_digest_in_off_mode_is_the_mode_line(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    lines = await noticing_service.digest()
    assert len(lines) >= 1 and "off" in lines[0].lower()
