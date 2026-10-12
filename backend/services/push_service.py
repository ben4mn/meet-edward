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

DEFAULT_VAPID_CONTACT = "mailto:admin@localhost"


def vapid_contact(raw: Optional[str] = None) -> str:
    """The VAPID ``sub`` claim: who a push service may contact about this
    sender. ``VAPID_CONTACT_EMAIL`` (an address or a ``mailto:``/``https:``
    URL); unset, ``mailto:admin@localhost``."""
    value = (os.getenv("VAPID_CONTACT_EMAIL") if raw is None else raw) or ""
    value = value.strip()
    if not value:
        return DEFAULT_VAPID_CONTACT
    if value.startswith(("mailto:", "https:")):
        return value
    return f"mailto:{value}"


VAPID_CLAIMS = {"sub": vapid_contact()}

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


def infer_kind(user_agent: Optional[str]) -> Optional[str]:
    """Device kind from the user agent when the client did not send one."""
    ua = user_agent or ""
    if not ua:
        return None
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        return "iphone-pwa"
    if "Macintosh" in ua:
        return "mac-web"
    return "mac-web"


async def backfill_device_kinds() -> int:
    """Fill kind/device_id on subscriptions saved before device identity existed."""
    fixed = 0
    async with async_session() as session:
        rows = (await session.execute(select(PushSubscriptionModel).where(PushSubscriptionModel.kind.is_(None)))).scalars().all()
        for row in rows:
            kind = infer_kind(row.user_agent)
            if not kind:
                continue
            row.kind = kind
            row.device_id = row.device_id or f"{kind}-{row.id[:6]}"
            row.device_name = row.device_name or ("iPhone" if kind == "iphone-pwa" else "Mac browser")
            row.last_seen_at = row.last_seen_at or row.last_used_at
            fixed += 1
        if fixed:
            await session.commit()
    if fixed:
        print(f"[push] backfilled device kind on {fixed} subscription(s)")
    return fixed


async def save_subscription(
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
    user_agent: Optional[str] = None,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    kind: Optional[str] = None,
) -> PushSubscriptionModel:
    """
    Save or update a push subscription.

    If the endpoint already exists, update the keys and reactivate.
    """
    import uuid

    kind = kind or infer_kind(user_agent)
    if kind and not device_id:
        device_id = f"{kind}-{uuid.uuid5(uuid.NAMESPACE_URL, endpoint).hex[:6]}"
        device_name = device_name or ("iPhone" if kind == "iphone-pwa" else "Mac browser")

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
            if device_id:
                existing.device_id = device_id
                existing.device_name = device_name or existing.device_name
                existing.kind = kind or existing.kind
            existing.last_seen_at = datetime.utcnow()
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
            device_id=device_id,
            device_name=device_name,
            kind=kind,
            last_seen_at=datetime.utcnow(),
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


async def get_dead_subscription_count() -> int:
    """Count of subscriptions auto-deactivated after repeated failures.

    Surfaced to the frontend so the user can notice when devices have
    silently fallen off (otherwise the only signal is pushes stop
    arriving — too easy to miss).
    """
    async with async_session() as session:
        result = await session.execute(
            select(PushSubscriptionModel).where(
                PushSubscriptionModel.is_active == False
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
    target: Optional[str] = None,
    delivery_id: Optional[str] = None,
    source: str = "unknown",
    conversation_id: Optional[str] = None,
    respect_quiet_hours: bool = False,
    approval_id: Optional[int] = None,
) -> dict:
    """
    Send a push notification to all active subscriptions — or, with
    ``target`` (a device_id or ``kind:<kind>``), only the matching ones.
    A target that matches nothing falls back to broadcast rather than
    silently dropping the notification. ``delivery_id`` rides in the
    payload so the service worker can acknowledge the click.

    Every call is written to the push ledger (``services.push_ledger``)
    under ``source`` so the judgment paths can see what the whole system
    has already told the owner. ``respect_quiet_hours`` is for the templated
    senders (briefings, reminders, system alerts): inside quiet hours
    (``EDWARD_QUIET_HOURS``, else 22:00-07:00) local the push is held — recorded, not sent — because everything
    they say is on the Today feed anyway. The paths that think
    (dispatcher, interrupt ladder, tool, wake-up ticks) leave it False.

    Args:
        title: Notification title
        body: Notification body text
        url: Optional URL to open on click — every push should have one
        tag: Optional tag to replace existing notification with same tag
        icon: Optional icon URL (defaults to app icon)
        source: which code path is speaking (ledger column)
        conversation_id: the thread the push points at, when there is one
        approval_id: a held send the owner is asked about (``source="approval"``,
            ``approvals_service``); the payload then carries Approve / Deny

    Returns:
        Dict with sent count, failed count, and total
    """
    from services import push_ledger

    if not is_configured():
        return {"error": "Push notifications not configured", "sent": 0, "failed": 0, "total": 0}

    if conversation_id is None and url:
        conversation_id = _conversation_from_url(url)

    held = push_ledger.hold_reason(
        local_hour=_local_hour(), respect_quiet_hours=respect_quiet_hours
    )
    if held:
        print(f"[push] held ({held}): {source} · {title!r}")
        await push_ledger.record(
            source=source, title=title, body=body, url=url, tag=tag,
            conversation_id=conversation_id, delivery_id=delivery_id,
            sent=0, failed=0, held_reason=held,
        )
        return {"sent": 0, "failed": 0, "total": 0, "held": held}

    subscriptions = await get_active_subscriptions()
    if not subscriptions:
        return {"sent": 0, "failed": 0, "total": 0, "message": "No active subscriptions"}

    target_fallback = False
    if target:
        from services.push_targeting import SubscriptionInfo, push_target_matches
        matched = [
            sub for sub in subscriptions
            if push_target_matches(SubscriptionInfo(id=sub.id, device_id=getattr(sub, "device_id", None),
                                                    kind=getattr(sub, "kind", None)), target)
        ]
        if matched:
            subscriptions = matched
        else:
            target_fallback = True

    import uuid as _uuid

    ledger_id = str(_uuid.uuid4())
    payload = build_payload(
        title=title, body=body, url=url, tag=tag, icon=icon,
        delivery_id=delivery_id, ledger_id=ledger_id, source=source,
        approval_id=approval_id, conversation_id=conversation_id,
    )

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

    result = {"sent": sent, "failed": failed, "total": len(subscriptions)}
    if target_fallback:
        result["target_fallback"] = True
    print(f"[push] {source} · {title!r} → {url or '/'} (sent {sent}, failed {failed})")
    recorded = await push_ledger.record(
        source=source, title=title, body=body, url=url, tag=tag,
        conversation_id=conversation_id, delivery_id=delivery_id,
        sent=sent, failed=failed, held_reason=None, row_id=ledger_id,
    )
    if recorded:
        result["ledger_id"] = recorded
    return result


# The three buttons every notification carries. "open" is the tap itself;
# "done" acknowledges without opening; "snooze" brings it back in an hour.
NOTIFICATION_ACTIONS = (
    {"action": "open", "title": "Open"},
    {"action": "done", "title": "Done"},
    {"action": "snooze", "title": "Snooze 1h"},
)

# A held send (taint and approvals, 2026-09-24): Approve and Deny post
# ``POST /api/approvals/{approval_id}`` from the service worker — the
# owner's answer arrives out of band, never as chat text a page could forge. Open
# (or a tap, on iOS where web-push buttons are not drawn) goes to the thread.
APPROVAL_ACTIONS = (
    {"action": "approve", "title": "Approve"},
    {"action": "deny", "title": "Deny"},
    {"action": "open", "title": "Open"},
)
APPROVAL_KIND = "approval"


def build_payload(
    *,
    title: str,
    body: str,
    url: Optional[str],
    tag: Optional[str],
    icon: Optional[str],
    delivery_id: Optional[str],
    ledger_id: Optional[str],
    source: str,
    approval_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
) -> dict:
    """What the service worker receives. ``kind`` is the sender (the SW
    can pick treatment per source); ``ledger_id`` lets Done / Snooze
    address the ledger row; ``delivery_id`` stops the interrupt ladder.
    An approval (``source="approval"``) carries ``approval_id`` and the
    thread, and Approve / Deny / Open instead of Open / Done / Snooze."""
    payload = {
        "title": title,
        "body": body,
        "url": url or "/",
        "tag": tag,
        "icon": icon or "/icons/icon-192.png",
        "delivery_id": delivery_id,
        "ledger_id": ledger_id,
        "kind": source or "unknown",
        "actions": [dict(a) for a in NOTIFICATION_ACTIONS],
    }
    if source == APPROVAL_KIND:
        payload["actions"] = [dict(a) for a in APPROVAL_ACTIONS]
        payload["approval_id"] = approval_id
        payload["conversation_id"] = conversation_id
    return payload


async def touch_device_seen(device_id: str, *, now: Optional[datetime] = None) -> int:
    """A presence beacon from a device refreshes its subscription's
    ``last_seen_at`` — the interrupt ladder only rings the phone first when
    it was seen in the last 15 minutes, and until 2026-09-12 that
    timestamp was only written at subscribe time."""
    if not device_id:
        return 0
    now = now or datetime.utcnow()
    try:
        async with async_session() as session:
            result = await session.execute(
                update(PushSubscriptionModel)
                .where(PushSubscriptionModel.device_id == device_id, PushSubscriptionModel.is_active.is_(True))
                .values(last_seen_at=now)
            )
            await session.commit()
            return int(result.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[push] touch_device_seen failed for {device_id}: {exc}")
        return 0


def _conversation_from_url(url: str) -> Optional[str]:
    """``/chat?c=<id>`` → ``<id>``; anything else → None."""
    try:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        if not parsed.path.endswith("/chat"):
            return None
        return parse_qs(parsed.query).get("c", [None])[0]
    except ValueError:
        return None


def _local_hour() -> int:
    try:
        from services.timezone_utils import now_local

        return now_local().hour
    except Exception:  # noqa: BLE001 — never let a tz lookup block a push
        return datetime.now().hour
