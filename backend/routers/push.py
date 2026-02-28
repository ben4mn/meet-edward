"""
Push notification API endpoints.

Handles Web Push subscription management and notification sending.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.push_service import (
    is_configured,
    get_vapid_public_key,
    save_subscription,
    remove_subscription,
    get_subscription_count,
    send_push_notification,
)

router = APIRouter()


class PushSubscription(BaseModel):
    """Push subscription data from the browser."""
    endpoint: str
    keys: dict  # Contains p256dh and auth


class UnsubscribeRequest(BaseModel):
    """Unsubscribe request."""
    endpoint: str


class TestNotificationRequest(BaseModel):
    """Test notification request."""
    title: str = "Test Notification"
    body: str = "Hello from Edward!"
    url: Optional[str] = None


@router.get("/push/vapid-key")
async def get_vapid_key():
    """
    Get the VAPID public key for push subscription.

    The client needs this key to subscribe to push notifications.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured. Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY."
        )

    return {"vapidPublicKey": get_vapid_public_key()}


@router.post("/push/subscribe")
async def subscribe(subscription: PushSubscription, request: Request):
    """
    Subscribe to push notifications.

    Saves the push subscription for later notification delivery.
    """
    print(f"[Push] Subscribe request received: endpoint={subscription.endpoint[:50]}...")

    if not is_configured():
        print("[Push] Error: Push notifications not configured")
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured"
        )

    # Extract keys from subscription
    keys = subscription.keys
    if "p256dh" not in keys or "auth" not in keys:
        print(f"[Push] Error: Missing keys. Got keys: {list(keys.keys())}")
        raise HTTPException(
            status_code=400,
            detail="Invalid subscription: missing p256dh or auth keys"
        )

    # Get user agent for debugging
    user_agent = request.headers.get("user-agent", "")
    print(f"[Push] Saving subscription with p256dh={keys['p256dh'][:20]}..., auth={keys['auth'][:10]}...")

    try:
        saved = await save_subscription(
            endpoint=subscription.endpoint,
            p256dh_key=keys["p256dh"],
            auth_key=keys["auth"],
            user_agent=user_agent,
        )
        print(f"[Push] Subscription saved successfully: id={saved.id}")
    except Exception as e:
        print(f"[Push] Error saving subscription: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save subscription: {str(e)}"
        )

    return {
        "success": True,
        "subscriptionId": saved.id,
        "message": "Subscription saved successfully"
    }


@router.post("/push/unsubscribe")
async def unsubscribe(request: UnsubscribeRequest):
    """
    Unsubscribe from push notifications.

    Removes the push subscription from the database.
    """
    removed = await remove_subscription(request.endpoint)

    return {
        "success": removed,
        "message": "Subscription removed" if removed else "Subscription not found"
    }


@router.get("/push/status")
async def push_status():
    """
    Get push notification configuration status.

    Returns whether push is configured and subscription count.
    """
    configured = is_configured()
    count = await get_subscription_count() if configured else 0

    return {
        "configured": configured,
        "subscriptionCount": count,
        "vapidPublicKey": get_vapid_public_key() if configured else None,
    }


@router.post("/push/test")
async def test_notification(request: TestNotificationRequest):
    """
    Send a test push notification.

    Useful for verifying push is working correctly.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured"
        )

    result = await send_push_notification(
        title=request.title,
        body=request.body,
        url=request.url,
        tag="test",
    )

    return result
