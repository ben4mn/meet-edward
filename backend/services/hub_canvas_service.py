"""The pet-station screen as Edward's universal expression surface.

Why (2026-09-02): the 7" touchscreen built for a pet's doses is also a
generic canvas Edward uses at will — a movie, an image, an alert, a note,
or a small HTML scene. One row of
state; the hub polls it and draws whatever is there over its normal
pages. A tap dismisses it and records that someone saw it.

Pure: ``make_content`` validates and normalises; ``is_expired`` decides
when the hub should drop it. I/O: ``show`` / ``clear`` / ``current``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

KINDS = ("image", "video", "alert", "text", "html")
MAX_HTML_CHARS = 60_000
MAX_BODY_CHARS = 4_000
MAX_SECONDS = 12 * 3600
# A show the owner did not ask for that names no end expires after this
# (2026-10-11): the pulse is change-only, so a failed clear must never leave
# a kitchen alert up. What the owner asks for (their turn, the hub's own
# panel) keeps "until cleared".
AUTONOMOUS_SHOW_SECONDS = 2 * 3600
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


@dataclass(frozen=True)
class CanvasContent:
    kind: str
    title: Optional[str]
    source: Optional[str]      # image / video URL
    body: Optional[str]        # alert / text / html
    embed_url: Optional[str]   # video: iframe URL for YouTube
    shown_at: datetime
    expires_at: Optional[datetime]
    shown_by: str = "edward"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "title": self.title, "source": self.source, "body": self.body,
            "embed_url": self.embed_url, "shown_at": self.shown_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None, "shown_by": self.shown_by,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CanvasContent":
        return cls(
            kind=d["kind"], title=d.get("title"), source=d.get("source"), body=d.get("body"),
            embed_url=d.get("embed_url"), shown_at=datetime.fromisoformat(d["shown_at"]),
            expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
            shown_by=d.get("shown_by") or "edward",
        )


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _check_url(source: Optional[str]) -> str:
    s = (source or "").strip()
    if s.startswith("/api/hub/photos/") or s.startswith("/api/files/"):
        return s
    p = urlparse(s)
    if p.scheme != "https" or not p.netloc:
        raise ValueError("source must be an https:// URL or a local /api/hub/photos/... path")
    return s


def _youtube_embed(source: str) -> Optional[str]:
    p = urlparse(source)
    if p.netloc not in _YT_HOSTS:
        return None
    vid = None
    if p.netloc == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    elif p.path.startswith("/embed/"):
        vid = p.path.split("/embed/", 1)[1].split("/")[0]
    else:
        vid = (parse_qs(p.query).get("v") or [None])[0]
    if not vid or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
        raise ValueError("could not read a YouTube video id from that URL")
    return f"https://www.youtube.com/embed/{vid}?autoplay=1&mute=0"


def make_content(
    *, kind: str, title: Optional[str] = None, source: Optional[str] = None, body: Optional[str] = None,
    seconds: int = 0, now: Optional[datetime] = None, shown_by: str = "edward",
) -> CanvasContent:
    now = _utc(now or datetime.now(timezone.utc))
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")
    title = (title or "").strip()[:120] or None
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        raise ValueError("seconds must be an integer")
    expires = None if seconds <= 0 else now + timedelta(seconds=min(seconds, MAX_SECONDS))

    src = embed = None
    text = None
    if kind in ("image", "video"):
        src = _check_url(source)
        if kind == "video":
            embed = _youtube_embed(src)
    else:
        text = (body or "").strip()
        if not text:
            raise ValueError(f"{kind} needs a body")
        cap = MAX_HTML_CHARS if kind == "html" else MAX_BODY_CHARS
        if len(text) > cap:
            raise ValueError(f"{kind} body is too long ({len(text)} > {cap} chars)")
    return CanvasContent(kind=kind, title=title, source=src, body=text, embed_url=embed,
                         shown_at=now, expires_at=expires, shown_by=shown_by)


def is_expired(content: CanvasContent, now: datetime) -> bool:
    return content.expires_at is not None and _utc(now) >= _utc(content.expires_at)


# ---- I/O ------------------------------------------------------------------------

async def _row(session):
    from services.database import HubCanvasModel
    row = await session.get(HubCanvasModel, "default")
    if row is None:
        row = HubCanvasModel(id="default")
        session.add(row)
    return row


async def current(*, now: Optional[datetime] = None) -> Optional[CanvasContent]:
    from services.database import async_session
    async with async_session() as session:
        row = await _row(session)
        if not row.content:
            return None
        try:
            content = CanvasContent.from_dict(json.loads(row.content))
        except (ValueError, KeyError, TypeError):
            return None
    if is_expired(content, now or datetime.now(timezone.utc)):
        return None
    return content


def default_seconds(seconds: Any, shown_by: str, owner_asked: bool) -> Any:
    """``AUTONOMOUS_SHOW_SECONDS`` for Edward's own show with no end; else
    ``seconds`` as given. Pure."""
    try:
        given = int(seconds or 0)
    except (TypeError, ValueError):
        return seconds  # make_content says what is wrong with it
    if given <= 0 and shown_by == "edward" and not owner_asked:
        return AUTONOMOUS_SHOW_SECONDS
    return seconds


async def show(**kwargs) -> Dict[str, Any]:
    from services.database import async_session
    from services.screen_service import owner_asked_now

    kwargs["seconds"] = default_seconds(kwargs.get("seconds", 0), kwargs.get("shown_by", "edward"),
                                        owner_asked_now())
    content = make_content(**kwargs)
    async with async_session() as session:
        row = await _row(session)
        row.content = json.dumps(content.as_dict())
        row.dismissed_at = None
        row.dismissed_by = None
        await session.commit()
    print(f"[canvas] show {content.kind}" + (f" '{content.title}'" if content.title else "") +
          (f" until {content.expires_at:%H:%M:%S}Z" if content.expires_at else " until cleared"))
    return content.as_dict()


async def clear(*, by: str = "edward") -> bool:
    from services.database import async_session
    async with async_session() as session:
        row = await _row(session)
        had = bool(row.content)
        row.content = None
        row.dismissed_at = datetime.utcnow()
        row.dismissed_by = by
        await session.commit()
    if had:
        print(f"[canvas] cleared by {by}")
    return had
