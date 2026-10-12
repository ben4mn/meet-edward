"""``POST /api/signals`` — Ellis's implicit verdicts from the UI.

A Today need acted on or failing, and later whatever else the pages
notice, lands as a small row a health check can count. Kinds are an
allowlist so the table cannot become a dumping ground.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import user_signals_service as us


def test_normalize_signal_accepts_known_kind_and_caps_detail():
    kind, detail = us.normalize_signal("today_action", {"need_kind": "pet_dose", "action": "Given", "blob": "x" * 5000})
    assert kind == "today_action"
    assert len(detail) <= us.MAX_DETAIL_CHARS


def test_normalize_signal_rejects_unknown_kind():
    with pytest.raises(ValueError):
        us.normalize_signal("made_up", {})
    with pytest.raises(ValueError):
        us.normalize_signal("", {})


def test_route_posts_and_lists(monkeypatch):
    from routers import signals as router_mod

    stored = []

    async def fake_record(kind, detail_json, conversation_id=None, ref_id=None):
        stored.append((kind, detail_json, conversation_id, ref_id))
        return "sig-1"

    async def fake_recent(days):
        return [{"kind": "today_action", "detail_json": "{}", "created_at": None}]

    monkeypatch.setattr(router_mod.us, "record_signal", fake_record)
    monkeypatch.setattr(router_mod.us, "recent_signals", fake_recent)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api")
    client = TestClient(app)

    r = client.post("/api/signals", json={"kind": "today_action", "detail": {"need_kind": "pet_dose", "action": "Given"}, "ref_id": "care-1"})
    assert r.status_code == 200 and r.json()["id"] == "sig-1"
    assert stored[0][0] == "today_action" and stored[0][3] == "care-1"

    r = client.post("/api/signals", json={"kind": "nope"})
    assert r.status_code == 400

    r = client.get("/api/signals?days=3")
    assert r.status_code == 200 and r.json()["signals"][0]["kind"] == "today_action"


@pytest.mark.integration
async def test_record_signal_writes_row(clean_db):
    from sqlalchemy import select
    from services.database import async_session, UserSignalModel

    sid = await us.record_signal("today_action", '{"action":"Given"}', ref_id="care-1")
    async with async_session() as s:
        row = (await s.execute(select(UserSignalModel).where(UserSignalModel.id == sid))).scalar_one()
    assert row.kind == "today_action" and row.ref_id == "care-1"
    recent = await us.recent_signals(days=1)
    assert recent and recent[0]["kind"] == "today_action"
