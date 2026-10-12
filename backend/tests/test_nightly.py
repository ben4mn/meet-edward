"""The 03:00 nightly: a silent reflection that skips empty days
(proactivity spec §2.3(b), G15; 2026-10-11).

It replaces the surplus queue's reflect_lite and the Sunday 09:00 reflect.

* **Pre-check in code** (``nightly_has_material``): something happened
  since the last nightly — a noticing, a ping, a verdict, a lapsed
  intention, Ellis talking to him, a new thing Ellis said. Otherwise one
  ledger row ("nothing new since last night") and no model call. Decay
  still runs on Sundays.
* **The turn**: ``wake("nightly")`` in ``edward-nightly``, 600 s (900 s on
  Sunday), a 48K window, a policy that reaches nobody — not Ellis's phone,
  not a chat.
* **The pack**: weekdays the day's asks (reflect_lite's), Sundays the
  week's (reflect's), and the noticing digest code wrote, read-only.
* **After**: Sunday's decay pass with its stamp; contradiction triage by
  note (one question at most). The surfaces pulse for the morning scene
  is the scheduler's, once, after the tick's wake returns
  (``test_proactivity_modes``) — never a second one from inside the wake.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from tests.area1_fakes import fake_module, install_turn_policy
from tests.test_mind_service import _S, _const, _pack

WEDNESDAY = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)  # 03:00 Wed in Springfield
SUNDAY = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)     # 03:00 Sun in Springfield


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    import services.mind_service as ms
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setattr(timezone_utils, "_tz", None)
    ms.reset_state()
    yield
    ms.reset_state()


class _After:
    def __init__(self):
        self.decay, self.triage, self.pulses = [], [], []


def _stub(monkeypatch, ms, chat, *, now, material=True, reason="nightly"):
    after = _After()
    monkeypatch.setattr(ms, "_now", lambda: now)
    monkeypatch.setattr(ms, "collect_pack", _const(_pack(reason=reason, now=now)))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "nightly_has_material", _const(material))

    async def decay(dry_run=True, **kw):
        after.decay.append(dry_run)
        return {"candidates": 4, "retired": 1, "stamp": "2026-10-11T08:00:01"}

    async def triage(limit=5, **kw):
        after.triage.append({"limit": limit, **kw})
        return []

    monkeypatch.setattr(ms, "decay_stale_memories", decay)
    from services import contradiction_triage_service, expression_service

    monkeypatch.setattr(contradiction_triage_service, "triage_unresolved", triage)
    monkeypatch.setattr(expression_service, "pulse_in_background", lambda reason: after.pulses.append(reason))
    recorded = []

    async def _rec(**kw):
        recorded.append(kw)
        return len(recorded)

    monkeypatch.setattr(ms, "_record_now", _rec)
    return recorded, after


async def _settle():
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0)


# ------------------------------------------------------- pre-check --

@pytest.mark.unit
async def test_no_material_is_a_ledger_row_and_no_model(monkeypatch):
    import services.mind_service as ms

    async def never(**kw):
        raise AssertionError("no model call on an empty day")

    recorded, after = _stub(monkeypatch, ms, never, now=WEDNESDAY, material=False)
    out = await ms.wake("nightly")
    await _settle()
    assert out["ok"] and out["skipped"]
    assert recorded == [{"source": "nightly", "outcome": "left_alone", "note": "nothing new since last night",
                         "tools": []}]
    assert after.decay == [] and after.triage == [] and after.pulses == []


@pytest.mark.unit
async def test_an_empty_sunday_still_runs_decay(monkeypatch):
    import services.mind_service as ms

    async def never(**kw):
        raise AssertionError("no model call on an empty day")

    recorded, after = _stub(monkeypatch, ms, never, now=SUNDAY, material=False)
    out = await ms.wake("nightly")
    assert out["skipped"]
    assert after.decay == [False]
    assert recorded[0]["outcome"] == "left_alone"
    assert recorded[0]["detail"]["decay"]["stamp"] == "2026-10-11T08:00:01"


# ------------------------------------------------------- the turn --

@pytest.mark.unit
async def test_a_weeknight_reflects_on_the_day_and_reaches_nobody(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Closed two intentions; noted that Ellis prefers texts after 6."

    recorded, after = _stub(monkeypatch, ms, chat, now=WEDNESDAY)
    out = await ms.wake("nightly")
    await _settle()
    assert out["ok"] and not out.get("skipped")
    kw = calls[0]
    assert kw["conversation_id"] == "edward-nightly"
    assert kw["max_history_chars"] == 48_000
    p = kw["policy"]
    assert (p.may_reach_people, p.may_push, p.owner_asked) == (False, False, False)
    assert p.deadline_s == 600 - 30
    assert tuple(kw["allowed_tools"]) == ms.LEGACY_QUIET_TOOLS
    msg = kw["message"]
    assert "This is your nightly reflection" in msg
    assert "Sunday" not in msg.split("This is your nightly reflection")[1]
    assert "goal" not in msg.lower()
    assert after.decay == [], "decay is Sunday's"
    assert after.triage == [{"limit": 5, "priority": "background", "purpose": "mind:nightly", "ask_via": "note",
                             "max_asks": 1}]
    assert after.pulses == [], ("the scheduler's nightly tick pulses once, after the wake returns; a second "
                                "pulse from inside the wake ran the surfaces turn twice (integration, 2026-10-11)")
    assert recorded[-1]["source"] == "nightly"


@pytest.mark.unit
async def test_sunday_reflects_on_the_week_and_runs_decay(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Rewrote the state document."

    recorded, after = _stub(monkeypatch, ms, chat, now=SUNDAY)
    await ms.wake("nightly")
    await _settle()
    msg = calls[0]["message"]
    assert "This is your Sunday reflection" in msg
    assert calls[0]["policy"].deadline_s == 900 - 30
    assert after.decay == [False]
    assert recorded[-1]["detail"]["decay"] == {"candidates": 4, "retired": 1, "stamp": "2026-10-11T08:00:01"}
    assert after.triage and after.pulses == []


@pytest.mark.unit
async def test_the_digest_rides_in_the_pack_when_noticing_exists(monkeypatch):
    import services.mind_service as ms

    mod = types.ModuleType("services.noticing_service")

    async def digest(now):
        return ["Noticed 9, spoke 2 (1 push, 1 note), held 1, silent 6 today.", "Travel: push (3 of 4 acted)."]

    mod.digest = digest
    fake_module(monkeypatch, "services.noticing_service", mod)
    p = _pack(reason="nightly", now=WEDNESDAY, digest=await ms._digest_source(WEDNESDAY))
    text = ms.render_pack(p)
    assert "Noticed 9, spoke 2" in text and "Travel: push (3 of 4 acted)." in text
    assert "read-only" in text


@pytest.mark.unit
async def test_no_noticing_module_no_digest(monkeypatch):
    import services.mind_service as ms

    fake_module(monkeypatch, "services.noticing_service", None)
    assert await ms._digest_source(WEDNESDAY) == []


@pytest.mark.unit
async def test_an_eval_nightly_with_its_own_pack_runs_no_after_steps(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Nothing for Ellis tonight."

    recorded, after = _stub(monkeypatch, ms, chat, now=SUNDAY, material=False)
    out = await ms.wake("nightly", message="[WAKE: nightly] test pack", conversation_id="eval-nightly-1",
                        record=False, skip_extraction=True)
    await _settle()
    assert out["ok"] and calls, "a caller's own pack is not pre-checked"
    assert after.decay == [] and after.triage == [] and after.pulses == []


@pytest.mark.unit
def test_the_nightly_asks_render_from_the_reason():
    import services.mind_service as ms

    weekday = ms.render_pack(_pack(reason="nightly", now=WEDNESDAY))
    sunday = ms.render_pack(_pack(reason="nightly", now=SUNDAY))
    assert "Your day (the last 24 hours):" in weekday and "nightly reflection" in weekday
    assert "Your week (the last seven days):" in sunday and "Sunday reflection" in sunday
    for text in (weekday, sunday):
        assert "Nobody is awake" in text
        assert "do not push" in text.lower() or "never a push" in text.lower()


# --------------------------------------------- nightly_has_material --

@pytest.mark.integration
async def test_material_since_the_last_nightly(clean_db):
    import services.mind_service as ms
    from services.database import LedgerEventModel, MemoryModel, TurnLogModel, UserSignalModel, async_session

    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None)
    assert await ms.nightly_has_material(now) is False

    async with async_session() as s:
        s.add(LedgerEventModel(kind="mind", source="nightly", outcome="acted", reason="ok",
                               created_at=naive - timedelta(hours=2)))
        s.add(TurnLogModel(id="t-old", source="chat", started_at=naive - timedelta(hours=3)))
        s.add(TurnLogModel(id="t-mind", source="mind", started_at=naive - timedelta(hours=1)))
        await s.commit()
    assert await ms.nightly_has_material(now) is False, "older than the last nightly, or not Ellis's"

    async with async_session() as s:
        s.add(TurnLogModel(id="t-new", source="voice", started_at=naive - timedelta(minutes=30)))
        await s.commit()
    assert await ms.nightly_has_material(now) is True


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["heard", "verdict", "lapsed"])
async def test_each_kind_of_material_counts(clean_db, kind):
    import services.mind_service as ms
    from services.database import MemoryModel, UserSignalModel, async_session

    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None)
    async with async_session() as s:
        if kind == "heard":
            s.add(MemoryModel(content="Ellis: no pushes about soccer", memory_type="instruction", origin="heard",
                              about="owner"))
        elif kind == "verdict":
            s.add(UserSignalModel(kind="mind_verdict", ref_id="12", detail_json='{"verdict": "wrong_call"}'))
        else:
            s.add(MemoryModel(content="watch the delivery", memory_type="intention", origin="thought", about="self",
                              created_at=naive - timedelta(days=3), valid_until=naive - timedelta(hours=1)))
        await s.commit()
    assert await ms.nightly_has_material(now) is True
