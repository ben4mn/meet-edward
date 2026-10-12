"""The legacy note and push tools reach Ellis through ``reach_service`` inside
a wake (review of AREA 1, 2026-10-11).

This branch has no ``tell_owner`` tool (AREA 2 brings it), so a wake or the
speaker reached Ellis through the legacy ``leave_note`` and
``send_push_notification`` — which never called ``reach_service``. In
``notice`` mode a speak wake's note never reached ``speak_scope.delivered``
(the row ended ``silent``, "speaker left it", and outcomes never saw it);
the gate's channel ceiling, the lint, quiet hours, the dedupe and the
nightly's one note were prompt, not code; a self wake at 23:30 could push.
Inside a wake (a ``mind:*`` purpose) or a speak scope both tools now hand
the reach to ``reach_service.tell_owner``; anywhere else they behave as
before. (``EDWARD_TOOLSET=legacy`` after AREA 2 keeps these tools too.)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

DAY = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)      # 3 pm in Springfield
NIGHT = datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc)    # 11:30 pm in Springfield


@pytest.fixture
def push(monkeypatch):
    from services import push_service, reach_service, timezone_utils

    calls = []

    async def send(title, body, url=None, **kw):
        calls.append({"title": title, "body": body, "source": kw.get("source")})
        return {"sent": 1, "failed": 0, "total": 1}

    monkeypatch.setattr(push_service, "send_push_notification", send)
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setattr(reach_service, "_now", lambda: DAY)
    monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    return calls


async def _reach_rows():
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        return [(r.source, r.outcome) for r in (await s.execute(
            select(LedgerEventModel).where(LedgerEventModel.kind == "reach").order_by(LedgerEventModel.id))).scalars()]


async def _notes():
    from services.database import EdwardNoteModel, async_session

    async with async_session() as s:
        return list((await s.execute(select(EdwardNoteModel))).scalars())


NOTE = {"title": "Trivia crew", "body": "I posted the $140 pair in Trivia crew at 6:58 pm, as you asked."}
PUSH = {"title": "UA958 check-in",
        "body": "BA's 9:31 pm text says check-in closes at 10; you said you're en route. Open check-in? (not done)"}


async def test_a_wakes_note_goes_through_reach(clean_db, push):
    from services.call_purpose import purpose_scope
    from services.graph.tools import leave_note

    with purpose_scope("mind:self"):
        out = await leave_note.ainvoke(NOTE)
    assert out.startswith("Left Ellis a note"), out
    assert await _reach_rows() == [("self", "note")]
    assert len(await _notes()) == 1


async def test_a_self_wakes_push_at_night_is_held_as_a_note(clean_db, push, monkeypatch):
    from services import reach_service
    from services.call_purpose import purpose_scope
    from services.graph.tools import send_push_notification

    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    with purpose_scope("mind:self"):
        out = await send_push_notification.ainvoke(PUSH)
    assert "quiet hours" in out and push == []
    assert await _reach_rows() == [("self", "downgraded")]


async def test_the_nightly_leaves_one_note_and_never_pushes(clean_db, push):
    from services.call_purpose import purpose_scope
    from services.graph.tools import leave_note, send_push_notification

    with purpose_scope("mind:nightly"):
        first = await send_push_notification.ainvoke(PUSH)
        second = await leave_note.ainvoke(NOTE)
    assert push == [] and first.startswith("Left it as a note")
    assert second.startswith("Tool error:") and "one note" in second


async def test_the_speakers_note_is_delivered_into_its_scope(clean_db, push):
    from services import reach_service
    from services.graph.tools import leave_note

    with reach_service.speak_scope(noticing_id=None, channel="note", who=("BA",)) as scope:
        out = await leave_note.ainvoke({"title": "UA958", "body": PUSH["body"]})
    assert out.startswith("Left Ellis a note"), out
    assert [d["channel"] for d in scope.delivered] == ["note"]


async def test_outside_a_wake_the_tools_are_what_they_were(clean_db, push):
    from services.graph.tools import leave_note, send_push_notification

    assert await leave_note.ainvoke(NOTE) == "ok"
    out = await send_push_notification.ainvoke({"title": "Reminder", "body": "Call Mom"})
    assert push and push[-1]["source"] == "tool" and "Tool error" not in out
    assert await _reach_rows() == []


class _HubTurn:
    """The household hub's voice (``hub_voice``, 2026-10-11): someone in
    Ellis's home, not necessarily Ellis."""

    def __enter__(self):
        from services import turn_ledger

        rec = turn_ledger.TurnRecord.start(conversation_id="hub-voice-1", source="hub_voice", model=None,
                                           provider=None)
        self._token = turn_ledger._current.set(rec)
        return rec

    def __exit__(self, *exc):
        from services import turn_ledger

        turn_ledger._current.reset(self._token)
        return False


async def test_the_hubs_note_goes_through_reach(clean_db, push):
    """Under ``EDWARD_TOOLSET=legacy`` the hub's turn may not text Ellis, so
    these tools are how it reaches him — with the hub's reach rules."""
    from services.graph.tools import leave_note

    with _HubTurn():
        out = await leave_note.ainvoke({"title": "Biscuit", "body": "Rowan says Biscuit threw up after dinner."})
    assert out.startswith("Left Ellis a note"), out
    assert await _reach_rows() == [("hub", "note")]


async def test_the_hubs_push_at_night_is_held_as_a_note(clean_db, push, monkeypatch):
    from services import reach_service
    from services.graph.tools import send_push_notification

    monkeypatch.setattr(reach_service, "_now", lambda: NIGHT)
    with _HubTurn():
        out = await send_push_notification.ainvoke({"title": "Biscuit", "body": "Rowan says Biscuit threw up."})
    assert "quiet hours" in out and push == []
    assert await _reach_rows() == [("hub", "downgraded")]
