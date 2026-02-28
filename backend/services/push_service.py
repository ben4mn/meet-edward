"""
Push notification service for Edward.

Manages Web Push subscriptions and sends push notifications using VAPID
authentication. Self-hosted push (no Firebase/external services).
"""

import json
import os
from typing import Optional, List
from datetime import datetime

from pywebpush import webpush, WebPushException
from sqlalchemy import select, update, delete

from services.database import async_session, PushSubscriptionModel


# VAPID keys from environment
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_CLAIMS = {"sub": f"mailto:{os.environ.get('VAPID_CONTACT_EMAIL', 'admin@localhost')}"}

# Max failures before deactivating a subscription
MAX_FAILED_COUNT = 3


def is_configured() -> bool:
    """Check if push notifications are configured (VAPID keys present)."""
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def get_vapid_public_key() -> Optional[str]:
    """Get the VAPID public key for client subscription."""
    return VAPID_PUBLIC_KEY


def get_status() -> dict:
    """
    Get push notification service status for skills integration.

    Returns:
        Dict with status, status_message, and metadata
    """
    if not is_configured():
        return {
            "status": "error",
            "status_message": "VAPID keys not configured",
            "metadata": None,
        }

    # Note: We can't easily get subscription count synchronously here
    # since it requires async DB access. The status_message will show
    # that it's ready, and the actual count is available via /api/push/status
    return {
        "status": "connected",
        "status_message": "Ready to send notifications",
        "metadata": {
            "vapid_configured": True,
        },
    }


async def save_subscription(
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
    user_agent: Optional[str] = None,
) -> PushSubscriptionModel:
    """
    Save or update a push subscription.

    If the endpoint already exists, update the keys and reactivate.
    """
    import uuid

    async with async_session() as session:
        # Check for existing subscription by endpoint
        result = await session.execute(
            select(PushSubscriptionModel).where(
                PushSubscriptionModel.endpoint == endpoint
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing subscription
            existing.p256dh_key = p256dh_key
            existing.auth_key = auth_key
            existing.user_agent = user_agent
            existing.is_active = True
            existing.failed_count = 0
            existing.last_used_at = datetime.utcnow()
            await session.commit()
            await session.refresh(existing)
            return existing

        # Create new subscription
        subscription = PushSubscriptionModel(
            id=str(uuid.uuid4()),
            endpoint=endpoint,
            p256dh_key=p256dh_key,
            auth_key=auth_key,
            user_agent=user_agent,
        )
        session.add(subscription)
        await session.commit()
        await session.refresh(subscription)
        return subscription


async def remove_subscription(endpoint: str) -> bool:
    """Remove a push subscription by endpoint."""
    async with async_session() as session:
        result = await session.execute(
            delete(PushSubscriptionModel).where(
                PushSubscriptionModel.endpoint == endpoint
            )
        )
        await session.commit()
        return result.rowcount > 0


async def get_active_subscriptions() -> List[PushSubscriptionModel]:
    """Get all active push subscriptions."""
    async with async_session() as session:
        result = await session.execute(
            select(PushSubscriptionModel).where(
                PushSubscriptionModel.is_active == True
            )
        )
        return list(result.scalars().all())


async def get_subscription_count() -> int:
    """Get count of active subscriptions."""
    async with async_session() as session:
        result = await session.execute(
            select(PushSubscriptionModel).where(
                PushSubscriptionModel.is_active == True
            )
        )
        return len(list(result.scalars().all()))


async def _mark_subscription_failed(subscription_id: str) -> None:
    """Increment failed count and deactivate if threshold reached."""
    async with async_session() as session:
        result = await session.execute(
            select(PushSubscriptionModel).where(
                PushSubscriptionModel.id == subscription_id
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.failed_count += 1
            if sub.failed_count >= MAX_FAILED_COUNT:
                sub.is_active = False
            await session.commit()


async def _mark_subscription_used(subscription_id: str) -> None:
    """Update last_used_at timestamp on successful delivery."""
    async with async_session() as session:
        await session.execute(
            update(PushSubscriptionModel)
            .where(PushSubscriptionModel.id == subscription_id)
            .values(last_used_at=datetime.utcnow(), failed_count=0)
        )
        await session.commit()


def _send_to_subscription(
    subscription: PushSubscriptionModel,
    payload: dict,
) -> bool:
    """
    Send a push notification to a single subscription.

    Returns True if successful, False if failed.
    """
    if not is_configured():
        return False

    subscription_info = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh_key,
            "auth": subscription.auth_key,
        }
    }

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return True
    except WebPushException as e:
        # 404 or 410 means subscription is invalid (user unsubscribed)
        if e.response and e.response.status_code in (404, 410):
            print(f"Push subscription expired/invalid: {subscription.id}")
        else:
            print(f"Push failed for {subscription.id}: {e}")
        return False
    except Exception as e:
        print(f"Push error for {subscription.id}: {e}")
        return False


async def send_push_notification(
    title: str,
    body: str,
    url: Optional[str] = None,
    tag: Optional[str] = None,
    icon: Optional[str] = None,
) -> dict:
    """
    Send a push notification to all active subscriptions.

    Args:
        title: Notification title
        body: Notification body text
        url: Optional URL to open on click
        tag: Optional tag to replace existing notification with same tag
        icon: Optional icon URL (defaults to app icon)

    Returns:
        Dict with sent count, failed count, and total
    """
    if not is_configured():
        return {"error": "Push notifications not configured", "sent": 0, "failed": 0, "total": 0}

    subscriptions = await get_active_subscriptions()
    if not subscriptions:
        return {"sent": 0, "failed": 0, "total": 0, "message": "No active subscriptions"}

    payload = {
        "title": title,
        "body": body,
        "url": url or "/",
        "tag": tag,
        "icon": icon or "/icons/icon-192.png",
    }

    sent = 0
    failed = 0

    for sub in subscriptions:
        success = _send_to_subscription(sub, payload)
        if success:
            await _mark_subscription_used(sub.id)
            sent += 1
        else:
            await _mark_subscription_failed(sub.id)
            failed += 1

    return {"sent": sent, "failed": failed, "total": len(subscriptions)}
