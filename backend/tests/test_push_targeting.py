"""push_service learns device identity and targeting (Session E)."""

from datetime import datetime

import pytest

from services import push_service

pytestmark = pytest.mark.unit


def _sub(id, device_id=None, kind=None):
    from services.database import PushSubscriptionModel
    return PushSubscriptionModel(id=id, endpoint=f"https://p/{id}", p256dh_key="k", auth_key="a",
                                 device_id=device_id, kind=kind, is_active=True, failed_count=0)


@pytest.fixture(autouse=True)
def _ledger_in_memory(monkeypatch):
    """The three targeting tests exercise the real send path with the DB
    faked away. The push ledger (2026-09-12) writes a row per send; without
    this it would open a real session in a test that has no clean_db, and
    the pooled connection then outlives the test's event loop."""
    from services import push_ledger

    written = []

    async def _record(**kw):
        written.append(kw)
        return "ledger-fake"

    monkeypatch.setattr(push_ledger, "record", _record)
    return written


async def test_send_push_notification_target_filters_subscriptions(monkeypatch):
    sent = []
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(push_service, "get_active_subscriptions", _const([_sub("a", "iphone-1", "iphone-pwa"), _sub("b", "mac-1", "mac-web")]))
    monkeypatch.setattr(push_service, "_send_to_subscription", lambda sub, payload: sent.append((sub.id, payload)) or True)
    monkeypatch.setattr(push_service, "_mark_subscription_used", _const(None))
    monkeypatch.setattr(push_service, "_mark_subscription_failed", _const(None))
    result = await push_service.send_push_notification("t", "b", target="iphone-1", delivery_id="d1")
    assert [s for s, _ in sent] == ["a"]
    assert sent[0][1]["delivery_id"] == "d1"
    assert result["sent"] == 1 and result["total"] == 1


async def test_send_push_notification_without_target_broadcasts_as_before(monkeypatch):
    sent = []
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(push_service, "get_active_subscriptions", _const([_sub("a"), _sub("b")]))
    monkeypatch.setattr(push_service, "_send_to_subscription", lambda sub, payload: sent.append(sub.id) or True)
    monkeypatch.setattr(push_service, "_mark_subscription_used", _const(None))
    monkeypatch.setattr(push_service, "_mark_subscription_failed", _const(None))
    result = await push_service.send_push_notification("t", "b")
    assert sent == ["a", "b"] and result["sent"] == 2


async def test_target_with_no_match_falls_back_to_broadcast(monkeypatch):
    """A stale device id must not silently drop the notification."""
    sent = []
    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(push_service, "get_active_subscriptions", _const([_sub("a"), _sub("b")]))
    monkeypatch.setattr(push_service, "_send_to_subscription", lambda sub, payload: sent.append(sub.id) or True)
    monkeypatch.setattr(push_service, "_mark_subscription_used", _const(None))
    monkeypatch.setattr(push_service, "_mark_subscription_failed", _const(None))
    result = await push_service.send_push_notification("t", "b", target="gone-device")
    assert sent == ["a", "b"] and result.get("target_fallback") is True


@pytest.mark.integration
async def test_save_subscription_stores_device_fields(clean_db):
    sub = await push_service.save_subscription("https://p/x", "k", "a", user_agent="ua",
                                               device_id="iphone-1", device_name="Ellis's iPhone", kind="iphone-pwa")
    assert sub.device_id == "iphone-1" and sub.kind == "iphone-pwa" and sub.last_seen_at is not None


def _const(v):
    async def _f(*a, **k):
        return v
    return _f


@pytest.mark.integration
async def test_touch_device_seen_refreshes_the_phone_subscription(clean_db):
    """The interrupt ladder only rings the phone first when it was seen in
    the last 15 minutes, but last_seen_at was only written on subscribe
    (last: 2026-09-03). A presence beacon from the phone now refreshes it."""
    from datetime import datetime, timedelta

    await push_service.save_subscription(
        endpoint="https://p/phone", p256dh_key="k", auth_key="a", user_agent="iPhone",
        device_id="iphone-pwa-abc", device_name="iPhone", kind="iphone-pwa",
    )
    stale = datetime.utcnow() - timedelta(days=9)
    async with push_service.async_session() as s:
        row = (await s.execute(
            push_service.select(push_service.PushSubscriptionModel)
        )).scalar_one()
        row.last_seen_at = stale
        await s.commit()

    touched = await push_service.touch_device_seen("iphone-pwa-abc")
    assert touched == 1
    async with push_service.async_session() as s:
        row = (await s.execute(push_service.select(push_service.PushSubscriptionModel))).scalar_one()
    assert (datetime.utcnow() - row.last_seen_at).total_seconds() < 60
    assert await push_service.touch_device_seen("nobody") == 0
