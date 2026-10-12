"""Edward's notes — what the templated services have to say, as inputs.

Why (2026-09-12): the morning briefing, meeting prep, the open-loop nudge
and weekly patterns each rang the phone on their own schedule, and the
wake-up whisper buzzed it too. They are inputs, not voices. A note lands
on Today (as a need when it is time-sensitive, in the digest otherwise)
and in the next wake-up tick's signal pack; one judgment decides whether
anything reaches the phone.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, quote, urlsplit

from sqlalchemy import select

from services.owner import owner_text

logger = logging.getLogger(__name__)

# Two voices leave notes now: the mind (``leave_note`` tool) and the
# heartbeat's whispers. The templated services that used to are gone.
# Ideas (2026-09-24): ``suggest_idea`` leaves a note of kind
# ``idea`` whose ``url`` is ``/chat?prompt=<prompt>``. It lives a week on
# Today's Ideas list — never a need, never a buzz.
IDEA_KIND = "idea"
IDEA_TTL_HOURS = 24 * 7
MAX_IDEA_PROMPT_CHARS = 1000
_TTL_HOURS = {
    "mind": 24,
    "whisper": 24,
    IDEA_KIND: IDEA_TTL_HOURS,
}
_DEFAULT_TTL_HOURS = 24
_BODY_CLIP = 220
# How a pack line marks a note that waits on the owner (model-facing).
NEEDS_OWNER_FLAG = owner_text(" · needs {OWNER}")


def ttl_hours_for(kind: str) -> int:
    return _TTL_HOURS.get(kind, _DEFAULT_TTL_HOURS)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age(created_at: datetime, now: datetime) -> str:
    seconds = max(0, int((now - _aware(created_at)).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_notes_for_pack(notes: Iterable[Any], *, now: Optional[datetime] = None) -> str:
    """One line per note for a wake-up signal pack; empty when there are none."""
    now = now or datetime.now(timezone.utc)
    lines = []
    for n in notes:
        body = (n.body or "").strip().replace("\n", " ")
        if len(body) > _BODY_CLIP:
            body = body[: _BODY_CLIP - 1] + "…"
        flag = NEEDS_OWNER_FLAG if getattr(n, "needs_you", False) else ""
        lines.append(f"- [{n.kind}] {_age(n.created_at, now)} · {(n.title or '').strip()} — {body}{flag}")
    return "\n".join(lines)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return _aware(dt).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def note_dict(n: Any) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "href": n.url or "/today",
        "needs_you": bool(getattr(n, "needs_you", False)),
        "created_at": _iso(n.created_at),
        "expires_at": _iso(getattr(n, "expires_at", None)),
    }


async def leave_note(
    *,
    kind: str,
    title: str,
    body: str,
    url: Optional[str] = None,
    needs_you: bool = False,
    ttl_hours: Optional[float] = None,
) -> Optional[str]:
    """Persist one note. Never raises — a note that cannot be written is logged."""
    try:
        from services.database import EdwardNoteModel, async_session

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ttl = ttl_hours if ttl_hours is not None else ttl_hours_for(kind)
        note_id = str(uuid.uuid4())
        async with async_session() as session:
            session.add(EdwardNoteModel(
                id=note_id,
                kind=kind[:40],
                title=(title or "")[:200],
                body=(body or "")[:4000],
                url=url,
                needs_you=bool(needs_you),
                created_at=now,
                expires_at=now + timedelta(hours=ttl),
            ))
            await session.commit()
        print(f"[note] {kind} · {title!r}" + (" (needs the owner)" if needs_you else ""))
        return note_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("note write failed (%s): %s", kind, exc)
        return None


async def recent_notes(*, hours: float = 24, limit: int = 30, unexpired_only: bool = False) -> list[Any]:
    """Newest first. Empty on any failure so a feed or a pack still builds."""
    try:
        from services.database import EdwardNoteModel, async_session

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=hours)
        async with async_session() as session:
            stmt = select(EdwardNoteModel).where(EdwardNoteModel.created_at > cutoff)
            if unexpired_only:
                stmt = stmt.where(EdwardNoteModel.expires_at > now)
            stmt = stmt.order_by(EdwardNoteModel.created_at.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("note read failed: %s", exc)
        return []


async def recent_notes_text(*, hours: float = 24, limit: int = 12) -> str:
    return format_notes_for_pack(await recent_notes(hours=hours, limit=limit))


async def dismiss_notes(note_id: str, also: Iterable[str] = ()) -> Optional[int]:
    """The owner pressed "Done" on a note card (UX pass 2026-09-25): the note — and
    the rest of its card, ``also`` — leave Today now (expire). None when
    ``note_id`` is no note; otherwise how many were live and now are not.
    The rows stay: the next wake's pack still reads the last day of notes."""
    from services.database import EdwardNoteModel, async_session

    ids = [note_id] + [str(i) for i in also if i and str(i) != note_id][:50]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as session:
        rows = list((await session.execute(
            select(EdwardNoteModel).where(EdwardNoteModel.id.in_(ids))
        )).scalars().all())
        if not any(n.id == note_id for n in rows):
            return None
        dismissed = 0
        for n in rows:
            if n.expires_at is None or n.expires_at > now:
                n.expires_at = now
                dismissed += 1
            # A dismissal, not an expiry (2026-10-11): the noticing ladder
            # demotes a category only on the owner's explicit "Done". The first
            # press is the one that counts.
            if getattr(n, "dismissed_at", None) is None:
                n.dismissed_at = now
        await session.commit()
    print(f"[note] dismissed {dismissed} from Today")
    return dismissed


# ---------------------------------------------------------------- ideas --

def idea_href(prompt: str) -> str:
    """The chat URL that opens the composer with ``prompt`` typed."""
    return "/chat?prompt=" + quote((prompt or "").strip()[:MAX_IDEA_PROMPT_CHARS], safe="")


def prompt_from_href(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        values = parse_qs(urlsplit(url).query).get("prompt")
    except ValueError:
        return None
    return values[0] if values else None


def normalize_idea_title(title: Optional[str]) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def idea_dict(n: Any) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "why": n.body,
        "prompt": prompt_from_href(n.url) or "",
        "href": n.url or "/chat",
        "created_at": _iso(n.created_at),
        "expires_at": _iso(getattr(n, "expires_at", None)),
    }


async def _recent_ideas(days: float = 7) -> List[Any]:
    from services.database import EdwardNoteModel, async_session

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    async with async_session() as session:
        return list((await session.execute(
            select(EdwardNoteModel)
            .where(EdwardNoteModel.kind == IDEA_KIND)
            .where(EdwardNoteModel.created_at > cutoff)
            .order_by(EdwardNoteModel.created_at.desc())
        )).scalars().all())


async def leave_idea(*, title: str, why: str, prompt: str) -> Optional[str]:
    """Persist one idea. Never raises; None if it could not be written."""
    return await leave_note(kind=IDEA_KIND, title=title, body=why, url=idea_href(prompt), needs_you=False)


async def idea_seen_this_week(title: str) -> Optional[str]:
    """``live`` when the same idea is on Today now, ``resolved`` when the
    owner tried or dismissed it this week, None when it is new."""
    key = normalize_idea_title(title)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for n in await _recent_ideas():
        if normalize_idea_title(n.title) != key:
            continue
        if n.expires_at is None or n.expires_at > now:
            return "live"
        return "resolved"
    return None


