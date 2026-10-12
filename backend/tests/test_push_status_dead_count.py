"""R/G TDD for Task #23 — surface dead push subscriptions.

Push subs auto-deactivate after 3 failures. In prod we have 1 active,
2 dead — only one device left receiving pushes and no surface signal
that the others fell off. /api/push/status currently returns only the
active count, so the user has no way to notice.

Fix: include ``dead_count`` (is_active=false rows) in the status
response, and the push panel renders a banner when dead_count > 0.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_get_dead_subscription_count(clean_db):
    """The helper returns the count of is_active=false subscriptions."""
    from services.database import async_session, PushSubscriptionModel
    from services.push_service import get_dead_subscription_count

    async with async_session() as session:
        session.add(PushSubscriptionModel(
            id="d1",
            endpoint="https://fcm.googleapis.com/dead1",
            p256dh_key="x",
            auth_key="y",
            is_active=False,
            failed_count=3,
        ))
        session.add(PushSubscriptionModel(
            id="d2",
            endpoint="https://fcm.googleapis.com/dead2",
            p256dh_key="x",
            auth_key="y",
            is_active=False,
            failed_count=3,
        ))
        session.add(PushSubscriptionModel(
            id="a1",
            endpoint="https://fcm.googleapis.com/alive",
            p256dh_key="x",
            auth_key="y",
            is_active=True,
            failed_count=0,
        ))
        await session.commit()

    count = await get_dead_subscription_count()
    assert count == 2


@pytest.mark.integration
async def test_push_status_endpoint_includes_dead_count(clean_db):
    """The /api/push/status response surfaces dead_count alongside
    subscriptionCount so the frontend can render an alert."""
    from services.database import async_session, PushSubscriptionModel
    from routers.push import push_status

    async with async_session() as session:
        session.add(PushSubscriptionModel(
            id="d1",
            endpoint="https://fcm.googleapis.com/dead1",
            p256dh_key="x",
            auth_key="y",
            is_active=False,
            failed_count=3,
        ))
        session.add(PushSubscriptionModel(
            id="a1",
            endpoint="https://fcm.googleapis.com/alive",
            p256dh_key="x",
            auth_key="y",
            is_active=True,
            failed_count=0,
        ))
        await session.commit()

    resp = await push_status()
    assert "dead_count" in resp
    assert resp["dead_count"] == 1
    # Existing fields must still be present
    assert "subscriptionCount" in resp
    assert "configured" in resp
