"""Edward's wishlist — features he wants the owner to build.

Replaces the self-evolution machinery (2026-09-12). Edward cannot change
his own code; when he notices something he wishes he could do, he logs it
here with ``wish_for_feature`` and the owner reads the list in
Settings → Work → Wishlist, builds what they agree with, and moves items
through ``open → planned → building → done`` (or ``declined``).

Pure decisions (title normalization, duplicate detection, status and
priority validation) sit above the I/O so they can be tested without a
database. Wishing for the same thing twice merges into one row and
bumps ``times_wanted`` — the count is the signal the owner reads.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, select

from services.database import WishlistItemModel, async_session

STATUSES = ("open", "planned", "building", "done", "declined")
ACTIVE_STATUSES = ("open", "planned", "building")

MAX_TITLE = 200
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------

def normalize_title(title: Optional[str]) -> str:
    """Lowercase, punctuation stripped, whitespace collapsed — the key two
    wishes are compared on."""
    if not title:
        return ""
    return _WS.sub(" ", _PUNCT.sub(" ", title.lower())).strip()


def find_existing(items: Iterable[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    """The open / planned / building item whose normalized title matches,
    else None. Closed items never absorb a new wish."""
    key = normalize_title(title)
    if not key:
        return None
    for item in items:
        if (item.get("status") or "open") not in ACTIVE_STATUSES:
            continue
        if normalize_title(item.get("title")) == key:
            return item
    return None


def normalize_status(value: Any) -> str:
    """One of :data:`STATUSES` or ``ValueError`` naming them."""
    status = (value or "").strip().lower() if isinstance(value, str) else ""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)} (got {value!r})")
    return status


def normalize_priority(value: Any) -> int:
    """1 high, 2 normal, 3 low; anything unreadable is normal."""
    if value is None:
        return 2
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, min(3, n))


def serialize(row: WishlistItemModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "why": row.why or "",
        "detail": row.detail,
        "status": row.status or "open",
        "priority": row.priority if row.priority is not None else 2,
        "source": row.source or "edward",
        "conversation_id": row.conversation_id,
        "times_wanted": row.times_wanted or 1,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _merge_detail(existing: Optional[str], why: str, detail: Optional[str]) -> Optional[str]:
    """Append what the repeated wish adds, if anything new."""
    have = existing or ""
    additions: List[str] = []
    for piece in (why, detail):
        piece = (piece or "").strip()
        if piece and piece not in have and piece not in additions:
            additions.append(piece)
    if not additions:
        return existing
    return (have + ("\n" if have else "") + "\n".join(additions)).strip()


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

async def add_wish(
    *,
    title: str,
    why: str,
    detail: Optional[str] = None,
    priority: int = 2,
    source: str = "edward",
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Log a wish, or fold it into the matching open one (``merged: True``)."""
    title = (title or "").strip()[:MAX_TITLE]
    if not title:
        raise ValueError("a wish needs a title")
    why = (why or "").strip()
    detail = (detail or "").strip() or None
    source = source if source in ("edward", "owner") else "edward"

    async with async_session() as session:
        rows = (await session.execute(
            select(WishlistItemModel).where(WishlistItemModel.status.in_(ACTIVE_STATUSES))
        )).scalars().all()
        existing = find_existing([serialize(r) for r in rows], title)
        if existing is not None:
            row = next(r for r in rows if r.id == existing["id"])
            row.times_wanted = (row.times_wanted or 1) + 1
            row.detail = _merge_detail(row.detail, why if why != (row.why or "") else "", detail)
            if not (row.why or "").strip() and why:
                row.why = why
            if normalize_priority(priority) < normalize_priority(row.priority):
                row.priority = normalize_priority(priority)
            if conversation_id and not row.conversation_id:
                row.conversation_id = conversation_id
            row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            out = serialize(row)
            out["merged"] = True
            return out

        row = WishlistItemModel(
            title=title,
            why=why,
            detail=detail,
            status="open",
            priority=normalize_priority(priority),
            source=source,
            conversation_id=conversation_id,
            times_wanted=1,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        print(f"[wish] {source} wants: {title} — {why[:120]}")
        out = serialize(row)
        out["merged"] = False
        return out


async def list_wishes(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Newest-updated first; open / planned / building before done / declined."""
    async with async_session() as session:
        q = select(WishlistItemModel)
        if status:
            q = q.where(WishlistItemModel.status == normalize_status(status))
        q = q.order_by(WishlistItemModel.updated_at.desc()).limit(limit)
        rows = (await session.execute(q)).scalars().all()
    items = [serialize(r) for r in rows]
    # Stable sort: closed items sink; newest-updated order survives within each band.
    items.sort(key=lambda i: 0 if i["status"] in ACTIVE_STATUSES else 1)
    return items


async def update_wish(
    wish_id: str,
    *,
    status: Optional[str] = None,
    title: Optional[str] = None,
    why: Optional[str] = None,
    detail: Optional[str] = None,
    priority: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Edit any field; None when there is no such wish. A bad status raises."""
    new_status = normalize_status(status) if status is not None else None
    async with async_session() as session:
        row = await session.get(WishlistItemModel, wish_id)
        if row is None:
            return None
        if new_status is not None:
            row.status = new_status
        if title is not None and title.strip():
            row.title = title.strip()[:MAX_TITLE]
        if why is not None:
            row.why = why.strip()
        if detail is not None:
            row.detail = detail.strip() or None
        if priority is not None:
            row.priority = normalize_priority(priority)
        row.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(row)
        return serialize(row)


async def delete_wish(wish_id: str) -> bool:
    async with async_session() as session:
        result = await session.execute(delete(WishlistItemModel).where(WishlistItemModel.id == wish_id))
        await session.commit()
        return bool(result.rowcount)


async def wishes_since(hours: int = 24) -> List[Dict[str, Any]]:
    """Wishes logged or re-wanted in the last ``hours`` — Today's digest."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        rows = (await session.execute(
            select(WishlistItemModel)
            .where(WishlistItemModel.updated_at >= since)
            .order_by(WishlistItemModel.updated_at.desc())
            .limit(20)
        )).scalars().all()
    return [serialize(r) for r in rows]
