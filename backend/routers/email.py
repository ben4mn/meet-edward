"""REST API endpoints for email management (actions, preferences, unsubscribe queue)."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


# ===== Pydantic schemas =====

class SenderPrefUpdate(BaseModel):
    preference: str  # keep, delete, junk, unsubscribe
    source: str = "user"


# ===== Email Actions Audit Log =====

@router.get("/email/actions")
async def list_email_actions(
    sender_domain: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    """List email action audit log."""
    from services.email_management_service import get_actions
    return await get_actions(sender_domain, action, limit)


@router.get("/email/sender-stats")
async def get_sender_stats(
    sender_domain: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    """Aggregate email action stats by sender domain."""
    from services.email_management_service import get_sender_stats
    return await get_sender_stats(sender_domain, limit)


# ===== Sender Preferences =====

@router.get("/email/preferences")
async def list_preferences(
    limit: int = Query(100, le=500),
):
    """List all sender preferences."""
    from services.email_management_service import get_all_preferences
    return await get_all_preferences(limit)


@router.put("/email/preferences/{domain}")
async def set_preference(domain: str, body: SenderPrefUpdate):
    """Set or update preference for a sender domain."""
    if body.preference not in ("keep", "delete", "junk", "unsubscribe"):
        raise HTTPException(400, f"Invalid preference: {body.preference}")
    from services.email_management_service import set_sender_preference
    result = await set_sender_preference(domain, body.preference, body.source)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Failed to set preference"))
    return result


@router.delete("/email/preferences/{domain}")
async def delete_preference(domain: str):
    """Remove preference for a sender domain."""
    from services.email_management_service import delete_sender_preference
    result = await delete_sender_preference(domain)
    if not result.get("success"):
        raise HTTPException(404, result.get("error", "Preference not found"))
    return result


# ===== Unsubscribe Queue =====

@router.get("/email/unsub-queue")
async def list_unsub_queue(
    status: Optional[str] = Query(None),
):
    """List unsubscribe recommendations."""
    from services.email_unsubscribe_service import get_recommendations
    return await get_recommendations(status)


@router.post("/email/unsub-queue/{queue_id}/approve")
async def approve_unsub(queue_id: str):
    """Approve and execute an unsubscribe recommendation."""
    from services.email_unsubscribe_service import approve_unsubscribe
    result = await approve_unsubscribe(queue_id)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Unsubscribe failed"))
    return result


@router.post("/email/unsub-queue/{queue_id}/skip")
async def skip_unsub(queue_id: str):
    """Skip/dismiss an unsubscribe recommendation."""
    from services.email_unsubscribe_service import skip_unsubscribe
    result = await skip_unsubscribe(queue_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("error", "Queue item not found"))
    return result
