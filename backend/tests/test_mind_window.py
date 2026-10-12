"""Ellis's window on the mind — mind rebuild Phase 11.

Ellis can see what Edward thought, intended and did, and say "right call?"
about any of it. His verdict is a ``user_signals`` row of kind
``mind_verdict`` that the next wake's pack renders beside the wake it
judged, so the feedback loop closes through the pack, not a threshold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


async def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       cookies={"edward_token": create_access_token()})


def test_mind_verdict_is_an_allowed_signal():
    from services.user_signals_service import SIGNAL_KINDS, normalize_signal

    assert "mind_verdict" in SIGNAL_KINDS
    kind, detail = normalize_signal("mind_verdict", {"verdict": "wrong_call", "note": "too eager"})
    assert kind == "mind_verdict" and "wrong_call" in detail


def test_pack_renders_verdicts_beside_the_wake_they_judged():
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(verdicts=[
        {"kind": "mind_verdict", "verdict": "wrong_call", "note": "you pushed me about ACME twice",
         "at": "2026-09-18T12:00:00Z", "ref": "41", "about": "evening: acted — Pushed Ellis about ACME again."},
        {"kind": "heartbeat", "verdict": "right_call", "at": "2026-09-18T11:00:00Z"},
    ]))
    assert "mind_verdict: wrong_call — you pushed me about ACME twice" in text
    assert "on your wake: evening: acted — Pushed Ellis about ACME again." in text
    assert "heartbeat: right_call" in text


@pytest.mark.integration
async def test_verdict_writes_a_user_signal_the_next_pack_sees(clean_db, monkeypatch):
    import services.mind_service as ms

    ledger_id = await ms._record_now(source="evening", outcome="acted", note="Pushed Ellis about ACME again.",
                                     tools=[{"n": "send_push_notification", "ok": True}])
    async with await _client() as c:
        r = await c.post("/api/mind/verdict", json={"ledger_id": ledger_id, "verdict": "wrong_call",
                                                    "note": "twice in one day"})
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "mind_verdict"
        r = await c.post("/api/mind/verdict", json={"ledger_id": ledger_id, "verdict": "banana"})
        assert r.status_code == 400
    ms.reset_state()
    verdicts = await ms._verdicts_since(None)
    v = next(x for x in verdicts if x["kind"] == "mind_verdict")
    assert v["verdict"] == "wrong_call" and v["note"] == "twice in one day" and v["ref"] == str(ledger_id)
    pack = await ms.collect_pack("manual")
    v = next(x for x in pack.verdicts if x["kind"] == "mind_verdict")
    assert v["about"] == "evening: acted — Pushed Ellis about ACME again."


@pytest.mark.integration
async def test_mind_log_joins_pushes_and_verdicts(clean_db):
    import services.mind_service as ms
    from services.database import PushLogModel, async_session
    from services.user_signals_service import normalize_signal, record_signal

    lid = await ms._record_now(source="evening", outcome="acted", note="Pushed Ellis about ACME.",
                               tools=[{"n": "send_push_notification", "ok": True}], turn_id="turn-1")
    quiet = await ms._record_now(source="morning", outcome="left_alone", note="Nothing needed me.", tools=[])
    async with async_session() as s:
        s.add(PushLogModel(id="p1", source="tool", title="ACME", body="down 8%", conversation_id="edward-mind",
                           sent=1, opened_at=datetime.utcnow()))
        s.add(PushLogModel(id="p-old", source="tool", title="old", body="x", conversation_id="edward-mind", sent=1,
                           sent_at=datetime.utcnow() - timedelta(hours=3)))
        await s.commit()
    kind, detail = normalize_signal("mind_verdict", {"verdict": "right_call", "note": "thanks"})
    await record_signal(kind, detail, ref_id=str(lid))

    async with await _client() as c:
        r = await c.get("/api/mind/log?days=7")
        assert r.status_code == 200, r.text
        body = r.json()
    rows = {row["id"]: row for row in body["wakes"]}
    assert set(rows) == {lid, quiet}
    acted = rows[lid]
    assert acted["tools"] == ["send_push_notification"] and acted["turn_id"] == "turn-1"
    assert [p["title"] for p in acted["pushes"]] == ["ACME"] and acted["pushes"][0]["opened"] is True
    assert acted["verdict"] == {"verdict": "right_call", "note": "thanks"}
    assert rows[quiet]["pushes"] == [] and rows[quiet]["verdict"] is None
    assert body["days"] == 7


@pytest.mark.integration
async def test_mind_intentions_carry_their_next_fire(clean_db):
    from services.memory_service import Memory, store_memory
    from services.scheduled_events_service import create_event

    held = (await store_memory(Memory(id=None, content="Watching ACME for Ellis's bonus", memory_type="intention",
                                      importance=0.5, origin="thought", about="self",
                                      valid_until=datetime(2026, 10, 18)))).id
    loose = (await store_memory(Memory(id=None, content="Keep an eye on the vet bill", memory_type="intention",
                                       importance=0.5, origin="thought", about="self"))).id
    await store_memory(Memory(id=None, content="Closed: the old one", memory_type="intention", importance=0.5,
                              origin="thought", about="self", valid_until=datetime(2020, 1, 1)))
    ev = await create_event(description="look at ACME", scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
                            conversation_id="edward-mind", memory_id=held,
                            event_metadata={"type": "mind_wake", "why": "see whether ACME recovered"})

    async with await _client() as c:
        r = await c.get("/api/mind/intentions")
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()["intentions"]}
    assert set(rows) == {held, loose}, "a closed intention is not open"
    assert rows[held]["next_fire_at"] and rows[held]["next_fire_why"] == "see whether ACME recovered"
    assert rows[held]["event_id"] == ev.id and rows[held]["valid_until"].startswith("2026-10-18")
    assert rows[loose]["next_fire_at"] is None
