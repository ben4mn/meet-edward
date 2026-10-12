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
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    kind: Optional[str] = None


class AckRequest(BaseModel):
    delivery_id: Optional[str] = None
    conversation_id: Optional[str] = None
    ledger_id: Optional[str] = None
    source: str = "click"


class SnoozeRequest(BaseModel):
    ledger_id: str
    delivery_id: Optional[str] = None
    minutes: int = 60


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
            device_id=subscription.device_id,
            device_name=subscription.device_name,
            kind=subscription.kind,
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

    Returns whether push is configured, active subscription count,
    and the number of dead (auto-deactivated) subscriptions so the
    UI can surface "N device(s) lost their push" when nonzero.
    """
    configured = is_configured()
    # Counts are independent of VAPID configuration — even an un-configured
    # service can have subscription rows lingering, and the user needs to
    # see them so they can clean up or re-enable.
    from services.push_service import get_dead_subscription_count
    count = await get_subscription_count()
    dead = await get_dead_subscription_count()

    return {
        "configured": configured,
        "subscriptionCount": count,
        "dead_count": dead,
        "vapidPublicKey": get_vapid_public_key() if configured else None,
    }


@router.post("/push/test")
async def test_notification(request: Optional[TestNotificationRequest] = None):
    """
    Send a test push notification.

    Useful for verifying push is working correctly.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured"
        )

    request = request or TestNotificationRequest()
    result = await send_push_notification(
        title=request.title,
        body=request.body,
        url=request.url,
        tag="test",
    )

    return result


@router.post("/push/ack")
async def ack_delivery(req: AckRequest):
    """The user saw it: a notification click, or opening the landing
    conversation. Stamps the push ledger (``opened``). The interrupt ladder
    this also stopped is gone (2026-10-11); ``acked`` says the same as
    ``opened`` for old clients."""
    from services import push_ledger

    if req.delivery_id:
        opened = await push_ledger.mark_opened(source=req.source, delivery_id=req.delivery_id)
    elif req.ledger_id:
        opened = await push_ledger.mark_opened(source=req.source, ledger_id=req.ledger_id)
    elif req.conversation_id:
        opened = await push_ledger.mark_opened(source=req.source, conversation_id=req.conversation_id)
    else:
        raise HTTPException(status_code=400, detail="delivery_id or conversation_id required")
    return {"acked": bool(opened), "opened": opened}


@router.post("/push/snooze")
async def snooze_push(req: SnoozeRequest):
    """"Snooze 1h" on the notification: bring the push back later."""
    from services import push_ledger

    ok = await push_ledger.snooze(req.ledger_id, minutes=max(1, min(req.minutes, 24 * 60)))
    return {"snoozed": ok, "minutes": req.minutes}


@router.get("/push/log")
async def push_log(hours: float = 24, limit: int = 50):
    """The push ledger: what reached the owner's phone, from whom, and whether they opened it."""
    from services import push_ledger

    rows = await push_ledger.recent(hours=hours, limit=min(max(limit, 1), 200))
    return {"hours": hours, "pushes": [push_ledger.serialize(r) for r in rows]}
