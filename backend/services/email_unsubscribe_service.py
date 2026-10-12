"""
Email unsubscribe service for Edward.

Handles EMLX parsing for List-Unsubscribe headers, one-click unsubscribe
execution, and recommendation queue management.
"""

import asyncio
import email.parser
import glob
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy import select, update, func

from services.database import (
    async_session,
    EmailActionModel,
    EmailUnsubQueueModel,
)
from services.email_management_service import _extract_domain, MAIL_DB_PATH


def _find_emlx_path(rowid: int) -> Optional[str]:
    """Find the EMLX file path for a given message ROWID."""
    pattern = os.path.expanduser(
        f"~/Library/Mail/V10/*/INBOX.mbox/**/Messages/{rowid}.emlx"
    )
    matches = glob.glob(pattern, recursive=True)
    return matches[0] if matches else None


def _parse_emlx_headers(path: str) -> dict:
    """
    Parse RFC822 headers from an EMLX file.

    EMLX format: first line is byte count, rest is RFC822 message + Apple plist.
    """
    try:
        with open(path, "rb") as f:
            # Skip first line (byte count)
            f.readline()
            # Parse RFC822 headers
            parser = email.parser.BytesParser()
            msg = parser.parse(f, headersonly=True)
            return {
                "list_unsubscribe": msg.get("List-Unsubscribe", ""),
                "list_unsubscribe_post": msg.get("List-Unsubscribe-Post", ""),
                "from": msg.get("From", ""),
                "subject": msg.get("Subject", ""),
            }
    except Exception as e:
        return {"error": str(e)}


def _parse_unsubscribe_header(header: str) -> dict:
    """
    Parse List-Unsubscribe header into structured data.

    Format: <url1>, <mailto:addr>, <url2>
    Returns dict with urls, mailto, and detected method.
    """
    if not header:
        return {"urls": [], "mailto": None, "method": "unknown"}

    urls = []
    mailto = None

    # Extract angle-bracketed entries
    for match in re.finditer(r"<([^>]+)>", header):
        value = match.group(1).strip()
        if value.startswith("mailto:"):
            mailto = value[7:]  # Strip mailto: prefix
        elif value.startswith("http"):
            urls.append(value)

    return {
        "urls": urls,
        "mailto": mailto,
        "method": "unknown",  # Caller determines based on List-Unsubscribe-Post
    }


def _detect_unsub_method(headers: dict) -> str:
    """Detect the unsubscribe method from parsed headers."""
    has_post = bool(headers.get("list_unsubscribe_post", "").strip())
    unsub = _parse_unsubscribe_header(headers.get("list_unsubscribe", ""))

    if has_post and unsub["urls"]:
        return "one_click"
    elif unsub["urls"]:
        return "http_get"
    elif unsub["mailto"]:
        return "mailto"
    return "unknown"


async def execute_one_click_unsubscribe(url: str) -> dict:
    """Execute RFC 8058 one-click unsubscribe (POST)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                data="List-Unsubscribe=One-Click-Unsubscribe",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return {
                "success": response.status_code in (200, 202, 204),
                "status_code": response.status_code,
                "method": "one_click",
            }
    except Exception as e:
        return {"success": False, "error": str(e), "method": "one_click"}


async def execute_http_unsubscribe(url: str) -> dict:
    """Execute HTTP GET unsubscribe."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
            return {
                "success": response.status_code in (200, 202, 204),
                "status_code": response.status_code,
                "method": "http_get",
            }
    except Exception as e:
        return {"success": False, "error": str(e), "method": "http_get"}


async def execute_mailto_unsubscribe(mailto: str) -> dict:
    """Execute mailto unsubscribe via AppleScript."""
    # Parse mailto for address and optional subject
    addr = mailto.split("?")[0]
    subject = "Unsubscribe"
    if "subject=" in mailto:
        match = re.search(r"subject=([^&]+)", mailto)
        if match:
            subject = match.group(1)

    script = f'''
    tell application "Mail"
        set newMessage to make new outgoing message with properties {{subject:"{subject}", content:"Unsubscribe", visible:false}}
        tell newMessage
            make new to recipient at end of to recipients with properties {{address:"{addr}"}}
        end tell
        send newMessage
    end tell
    '''
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip(), "method": "mailto"}
        return {"success": True, "method": "mailto"}
    except Exception as e:
        return {"success": False, "error": str(e), "method": "mailto"}


async def execute_unsubscribe(queue_id: str) -> dict:
    """Execute an approved unsubscribe from the queue."""
    async with async_session() as session:
        result = await session.execute(
            select(EmailUnsubQueueModel).where(EmailUnsubQueueModel.id == queue_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return {"success": False, "error": "Queue item not found"}

        if item.status not in ("approved", "recommended"):
            return {"success": False, "error": f"Item status is '{item.status}', expected 'approved'"}

        # Mark as executing
        item.status = "executing"
        await session.commit()

    # Route to method
    exec_result = {"success": False, "error": "No unsubscribe method available"}

    if item.unsubscribe_method == "one_click" and item.unsubscribe_url:
        exec_result = await execute_one_click_unsubscribe(item.unsubscribe_url)
    elif item.unsubscribe_method == "http_get" and item.unsubscribe_url:
        exec_result = await execute_http_unsubscribe(item.unsubscribe_url)
    elif item.unsubscribe_method == "mailto" and item.unsubscribe_mailto:
        exec_result = await execute_mailto_unsubscribe(item.unsubscribe_mailto)

    # Update queue status
    async with async_session() as session:
        new_status = "completed" if exec_result.get("success") else "failed"
        await session.execute(
            update(EmailUnsubQueueModel)
            .where(EmailUnsubQueueModel.id == queue_id)
            .values(
                status=new_status,
                error=exec_result.get("error"),
                updated_at=func.now(),
            )
        )

        # Log to email_actions audit trail
        action = EmailActionModel(
            id=str(uuid.uuid4()),
            sender=item.sender_address,
            sender_domain=item.sender_domain,
            subject=item.subject_sample,
            action="unsubscribe",
            action_source="auto",
            status=new_status,
            error=exec_result.get("error"),
            conversation_id=item.approval_conversation_id,
        )
        session.add(action)
        await session.commit()

    exec_result["queue_id"] = queue_id
    exec_result["sender_domain"] = item.sender_domain
    return exec_result


async def analyze_marketing_senders(
    min_count: int = 3,
    days_back: int = 30,
) -> list[dict]:
    """
    Find repeat marketing senders, parse their unsub headers,
    and create recommendation queue entries.
    """
    import sqlite3

    if not os.path.exists(MAIL_DB_PATH):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    cutoff_ts = cutoff.timestamp()

    # Query Mail DB for marketing senders with counts
    def _query_marketing_senders():
        conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                """
                SELECT a.address as sender, s.subject, COUNT(*) as cnt,
                       m.ROWID as sample_rowid
                FROM messages m
                LEFT JOIN addresses a ON m.sender = a.ROWID
                LEFT JOIN subjects s ON m.subject = s.ROWID
                LEFT JOIN message_global_data mgd ON m.message_id = mgd.message_id
                WHERE mgd.model_category = 3
                  AND m.date_received > ?
                GROUP BY a.address
                HAVING COUNT(*) >= ?
                ORDER BY cnt DESC
                LIMIT 50
                """,
                (cutoff_ts, min_count),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    senders = await asyncio.to_thread(_query_marketing_senders)
    if not senders:
        return []

    recommendations = []
    async with async_session() as session:
        for sender_info in senders:
            sender_addr = sender_info.get("sender", "")
            domain = _extract_domain(sender_addr)
            if not domain:
                continue

            # Check if already in queue
            existing = await session.execute(
                select(EmailUnsubQueueModel).where(
                    EmailUnsubQueueModel.sender_domain == domain,
                    EmailUnsubQueueModel.status.in_(["recommended", "approved", "executing"]),
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Try to find EMLX and parse headers
            rowid = sender_info.get("sample_rowid")
            unsub_method = "unknown"
            unsub_url = None
            unsub_mailto = None

            if rowid:
                emlx_path = await asyncio.to_thread(_find_emlx_path, rowid)
                if emlx_path:
                    headers = await asyncio.to_thread(_parse_emlx_headers, emlx_path)
                    if "error" not in headers:
                        unsub_method = _detect_unsub_method(headers)
                        parsed = _parse_unsubscribe_header(
                            headers.get("list_unsubscribe", "")
                        )
                        unsub_url = parsed["urls"][0] if parsed["urls"] else None
                        unsub_mailto = parsed["mailto"]

            queue_item = EmailUnsubQueueModel(
                sender_domain=domain,
                sender_address=sender_addr[:255] if sender_addr else None,
                subject_sample=sender_info.get("subject", "")[:500],
                email_count=sender_info.get("cnt", 0),
                unsubscribe_method=unsub_method,
                unsubscribe_url=unsub_url,
                unsubscribe_mailto=unsub_mailto[:255] if unsub_mailto else None,
                status="recommended",
            )
            session.add(queue_item)
            recommendations.append({
                "sender_domain": domain,
                "sender_address": sender_addr,
                "email_count": sender_info.get("cnt", 0),
                "unsubscribe_method": unsub_method,
                "subject_sample": sender_info.get("subject", ""),
            })

        if recommendations:
            await session.commit()

    return recommendations


async def get_recommendations(status: Optional[str] = None) -> list[dict]:
    """Get unsubscribe recommendations from the queue."""
    async with async_session() as session:
        query = select(EmailUnsubQueueModel).order_by(
            EmailUnsubQueueModel.email_count.desc()
        )
        if status:
            query = query.where(EmailUnsubQueueModel.status == status)
        result = await session.execute(query)
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "sender_domain": r.sender_domain,
                "sender_address": r.sender_address,
                "subject_sample": r.subject_sample,
                "email_count": r.email_count,
                "unsubscribe_method": r.unsubscribe_method,
                "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


async def approve_unsubscribe(queue_id: str, conversation_id: Optional[str] = None) -> dict:
    """Approve and execute an unsubscribe recommendation."""
    async with async_session() as session:
        result = await session.execute(
            select(EmailUnsubQueueModel).where(EmailUnsubQueueModel.id == queue_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return {"success": False, "error": "Queue item not found"}
        if item.status not in ("recommended",):
            return {"success": False, "error": f"Cannot approve item with status '{item.status}'"}

        item.status = "approved"
        item.approval_conversation_id = conversation_id
        await session.commit()

    # Execute the unsubscribe
    return await execute_unsubscribe(queue_id)


async def skip_unsubscribe(queue_id: str) -> dict:
    """Skip/dismiss an unsubscribe recommendation."""
    async with async_session() as session:
        result = await session.execute(
            select(EmailUnsubQueueModel).where(EmailUnsubQueueModel.id == queue_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return {"success": False, "error": "Queue item not found"}

        item.status = "skipped"
        await session.commit()
        return {"success": True, "sender_domain": item.sender_domain}
