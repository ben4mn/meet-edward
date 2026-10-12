"""Tests for services/presence_service.py and routers/presence.py."""

from datetime import datetime, timedelta, timezone

import pytest

from services import presence_service
from services.presence_logic import Beacon

NOW = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


@pytest.mark.unit
class TestRegistry:
    async def test_record_beacon_updates_registry_even_when_db_is_down(self, monkeypatch):
        presence_service._registry.clear()

        class Boom:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(presence_service, "async_session", lambda: Boom())
        b = await presence_service.record_beacon(
            {"device_id": "owners-mbp", "kind": "mac", "state": "active", "idle_seconds": 3}, now=NOW
        )
        assert isinstance(b, Beacon)
        assert presence_service._registry["owners-mbp"].idle_seconds == 3

    async def test_record_beacon_rejects_bad_payload(self):
        with pytest.raises(ValueError):
            await presence_service.record_beacon({"device_id": "x", "kind": "nope", "state": "active"}, now=NOW)

    async def test_local_mac_probe_records_mac_beacon(self, monkeypatch):
        presence_service._registry.clear()
        monkeypatch.setattr(presence_service, "_read_hid_idle_output",
                            lambda: '  |   "HIDIdleTime" = 7000000000\n')
        monkeypatch.setattr(presence_service, "_persist_beacon", _noop)
        await presence_service.probe_local_mac_once(now=NOW)
        b = presence_service._registry[presence_service.LOCAL_MAC_DEVICE_ID]
        assert b.kind == "mac" and b.idle_seconds == 7 and b.state == "active"

    async def test_local_mac_probe_marks_idle_past_threshold(self, monkeypatch):
        presence_service._registry.clear()
        monkeypatch.setattr(presence_service, "_read_hid_idle_output",
                            lambda: '"HIDIdleTime" = 900000000000\n')
        monkeypatch.setattr(presence_service, "_persist_beacon", _noop)
        await presence_service.probe_local_mac_once(now=NOW)
        assert presence_service._registry[presence_service.LOCAL_MAC_DEVICE_ID].state == "idle"


async def _noop(*a, **k):
    return None


@pytest.mark.integration
async def test_beacon_endpoint_requires_widget_token_and_returns_attention(clean_db):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.widget_service import get_or_create_token

    presence_service._registry.clear()
    token = await get_or_create_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.post("/api/presence/beacon", json={"token": "wrong", "device_id": "d", "kind": "mac", "state": "active"})
        assert r.status_code == 401
        r = await client.post("/api/presence/beacon", json={"token": token, "device_id": "owners-mbp",
                                                            "kind": "mac", "state": "active", "idle_seconds": 2})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["attention"]["owner_present"] is True
        assert body["attention"]["active_surface"] == "mac:owners-mbp"


@pytest.mark.integration
async def test_record_beacon_upserts_row_and_state_endpoint_lists_it(clean_db):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from main import app
    from services import database
    from services.auth_service import create_access_token
    from services.database import PresenceBeaconModel

    presence_service._registry.clear()
    await presence_service.record_beacon({"device_id": "pi", "kind": "pi-hub", "state": "active", "idle_seconds": 0})
    await presence_service.record_beacon({"device_id": "pi", "kind": "pi-hub", "state": "idle", "idle_seconds": 300})
    async with database.async_session() as session:
        rows = (await session.execute(select(PresenceBeaconModel))).scalars().all()
    assert len(rows) == 1 and rows[0].state == "idle"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.get("/api/presence/state")
    assert r.status_code == 200
    assert any(b["device_id"] == "pi" for b in r.json()["beacons"])


@pytest.mark.integration
async def test_collect_signals_reads_latest_owner_outbound(clean_db):
    from services import database
    from services.database import HeartbeatEventModel

    now = datetime.utcnow()
    async with database.async_session() as session:
        session.add(HeartbeatEventModel(id="o1", source="imessage", event_type="message_sent", chat_identifier="+1555",
                                        summary="yo", is_from_user=True, triage_status="dismissed",
                                        created_at=now - timedelta(seconds=30)))
        await session.commit()
    sig = await presence_service.collect_signals()
    assert sig.last_owner_outbound is not None and sig.last_owner_outbound[0] == "+1555"


@pytest.mark.unit
class TestOwnerAtMini:
    async def test_owner_at_mini_reads_the_minis_own_probe(self, monkeypatch):
        presence_service._registry.clear()
        monkeypatch.setattr(presence_service, "_persist_beacon", _noop)
        assert presence_service.owner_at_mini(NOW) is False
        await presence_service.record_beacon({"device_id": presence_service.LOCAL_MAC_DEVICE_ID, "kind": "mac",
                                              "state": "active", "idle_seconds": 4}, now=NOW)
        assert presence_service.owner_at_mini(NOW) is True
        assert presence_service.owner_at_mini(NOW + timedelta(seconds=200)) is False  # the beacon went stale
        presence_service._registry.clear()
        await presence_service.record_beacon({"device_id": "tab-1", "kind": "display", "state": "active",
                                              "idle_seconds": 0}, now=NOW)
        assert presence_service.owner_at_mini(NOW) is False
        presence_service._registry.clear()
