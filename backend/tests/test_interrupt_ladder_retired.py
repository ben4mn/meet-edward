"""The interrupt ladder is gone (proactivity rebuild, 2026-10-11).

The ladder (menu bar → web tab → display → phone → a text to himself, each
step waiting for an ack) delivered 21 times in its life and Ellis never
answered one through it. Every way Edward reaches Ellis now goes through
``reach_service.tell_owner``. What stays:

* push targeting (``push_targeting``: a device id or ``kind:<kind>``),
  moved verbatim out of ``interrupt_logic``;
* ``/api/push/ack`` and ``/api/push/snooze`` still stamp the push ledger
  (opened, snoozed) — only the ladder's ack is gone;
* the menu-bar stream sends ``state`` frames, never ``interrupt`` frames;
* the display signals keep ``pending_interrupt`` (always null) so the
  stage's alert strip keeps reading the same shape.
"""

from __future__ import annotations

from datetime import datetime

import pytest


@pytest.mark.unit
def test_push_targeting_moved_verbatim():
    from services.push_targeting import SubscriptionInfo, push_target_matches

    phone = SubscriptionInfo(id="a", device_id="iphone-1", kind="iphone-pwa", last_seen_at=datetime(2026, 10, 1))
    assert push_target_matches(phone, None)
    assert push_target_matches(phone, "iphone-1") and not push_target_matches(phone, "mac-1")
    assert push_target_matches(phone, "kind:iphone-pwa") and not push_target_matches(phone, "kind:mac-web")


@pytest.mark.unit
def test_the_menu_bar_stream_sends_state_only():
    from routers.presence import _has_pending, stream_frames_once

    frames = stream_frames_once(device_id="bar-1", attention={"owner_present": True}, seen=set())
    assert [f.split("\n")[0] for f in frames] == ["event: state"]
    assert _has_pending("bar-1") is False


@pytest.mark.unit
async def test_the_display_signals_keep_a_null_pending_interrupt():
    from services import display_signals

    out = await display_signals.get_signals_summary()
    assert "pending_interrupt" in out and out["pending_interrupt"] is None


@pytest.fixture
def ledger(monkeypatch):
    from services import push_ledger

    calls = []

    async def mark_opened(*, source, delivery_id=None, ledger_id=None, conversation_id=None):
        calls.append(("opened", source, delivery_id, ledger_id, conversation_id))
        return True

    async def snooze(ledger_id, minutes):
        calls.append(("snooze", ledger_id, minutes))
        return True

    monkeypatch.setattr(push_ledger, "mark_opened", mark_opened)
    monkeypatch.setattr(push_ledger, "snooze", snooze)
    return calls


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from main import app
    from services.auth_service import create_access_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        yield c


@pytest.mark.unit
async def test_ack_stamps_the_push_ledger_only(client, ledger):
    r = await client.post("/api/push/ack", json={"delivery_id": "d1", "source": "sw-click"})
    assert r.status_code == 200, r.text
    assert r.json()["opened"] is True
    r = await client.post("/api/push/ack", json={"conversation_id": "c1", "source": "open"})
    assert r.status_code == 200 and r.json()["opened"] is True
    r = await client.post("/api/push/ack", json={"ledger_id": "l1", "source": "sw-click"})
    assert r.status_code == 200 and r.json()["opened"] is True
    assert [c[0] for c in ledger] == ["opened", "opened", "opened"]
    assert (await client.post("/api/push/ack", json={"source": "x"})).status_code == 400


@pytest.mark.unit
async def test_snooze_brings_the_push_back_later(client, ledger):
    r = await client.post("/api/push/snooze", json={"ledger_id": "l1", "delivery_id": "d1", "minutes": 60})
    assert r.status_code == 200 and r.json() == {"snoozed": True, "minutes": 60}
    assert ledger == [("snooze", "l1", 60)]
