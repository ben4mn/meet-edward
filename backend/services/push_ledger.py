"""The push ledger — every notification that leaves for the owner's phone, whoever sent it.

Why (2026-09-12 audit): twelve code paths called ``push_service`` directly.
Four of them think before they ring (the heartbeat dispatcher, the
interrupt ladder, the ``send_push_notification`` tool, the wake-up ticks);
the rest are templated — "Good morning", "Meeting prep: …", "Open items
reminder", "Weekly patterns detected", the Codex cap alert — and none of
them could see what the others had already said. The result on one
morning: a raw-data briefing at 08:00, a memory report at 09:05, a
dispatcher push at 09:47 and an escalation at 09:50.

Three things live here:

* ``hold_reason`` — the hard quiet-hours rule for the templated senders.
  The judgment paths are never held (a door that needs unlocking at 23:30
  must still ring); the templated ones are, because everything they say
  is on the Today feed anyway.
* the ledger itself (``record`` / ``mark_opened`` / ``recent``) — one row
  per send, opened when the owner taps the notification or opens the
  conversation it pointed at. ``format_recent`` renders it for the
  dispatcher and wake-up prompts so they can see the whole day, not just
  their own history.
* ``opened_url_matches_conversation`` — how an opened chat closes the
  loop on a push that pointed at it.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select, update

from services.owner import owner_text
from services.speak_gate import in_quiet_hours, quiet_hours as configured_quiet_hours

logger = logging.getLogger(__name__)

# Local; 22:00–07:00 unless EDWARD_QUIET_HOURS says otherwise. The value at
# import, for callers that want a constant; ``hold_reason`` reads
# ``speak_gate.quiet_hours`` per call.
QUIET_HOURS: tuple[int, int] = configured_quiet_hours()

# Senders that weigh context (quiet hours, what was already said) before
# ringing. Everything else is a template and goes through the hard hold.
JUDGMENT_SOURCES = frozenset({"dispatcher", "interrupt", "tool", "wakeup", "alexa"})

_BODY_CLIP = 90


def _quiet(hour: int, start: int, end: int) -> bool:
    return in_quiet_hours(hour, start, end)


def hold_reason(
    *,
    local_hour: int,
    respect_quiet_hours: bool,
    quiet_hours: Optional[tuple[int, int]] = None,
) -> Optional[str]:
    """Why a push should not go out right now, or ``None`` to send it.
    ``quiet_hours`` defaults to the configured window (``EDWARD_QUIET_HOURS``,
    read per call)."""
    if respect_quiet_hours and _quiet(local_hour, *(quiet_hours or configured_quiet_hours())):
        return "quiet_hours"
    return None


def opened_url_matches_conversation(url: Optional[str], conversation_id: str) -> bool:
    if not url or not conversation_id:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if not parsed.path.endswith("/chat"):
        return False
    return parse_qs(parsed.query).get("c", [None])[0] == conversation_id


def _age(sent_at: datetime, now: datetime) -> str:
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    seconds = max(0, int((now - sent_at).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


_NOTHING_PUSHED = owner_text("(nothing pushed to {OWNER} recently)")


def format_recent(rows: Iterable[Any], *, now: Optional[datetime] = None) -> str:
    """One line per push, newest first as given: age · source · title — body · outcome."""
    now = now or datetime.now(timezone.utc)
    lines = []
    for row in rows:
        if row.held_reason:
            outcome = f"held ({row.held_reason})"
        elif row.opened_at:
            outcome = f"opened ({row.open_source or 'tap'})"
        elif not row.sent:
            outcome = "not delivered"
        else:
            outcome = "not opened"
        body = (row.body or "").strip().replace("\n", " ")
        if len(body) > _BODY_CLIP:
            body = body[: _BODY_CLIP - 1] + "…"
        title = (row.title or "").strip()
        lines.append(f"- {_age(row.sent_at, now)} · {row.source} · {title}" + (f" — {body}" if body else "") + f" · {outcome}")
    return "\n".join(lines) if lines else _NOTHING_PUSHED


def spoke_recently(
    rows: Iterable[Any],
    *,
    sources: set[str],
    within_hours: float,
    now: Optional[datetime] = None,
) -> bool:
    """Did one of ``sources`` actually reach the phone in the last N hours?

    The templated 08:00 briefing uses this to stay quiet when the 07:00
    wake-up tick (which has judgment) already said good morning.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=within_hours)
    for row in rows:
        if row.source not in sources or row.held_reason or not row.sent:
            continue
        sent_at = row.sent_at if row.sent_at.tzinfo else row.sent_at.replace(tzinfo=timezone.utc)
        if sent_at >= cutoff:
            return True
    return False


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def due_for_resend(rows: Iterable[Any], *, now: Optional[datetime] = None) -> list[Any]:
    """Snoozed pushes whose hour is up and that have not been re-sent yet."""
    now = now or datetime.now(timezone.utc)
    out = []
    for row in rows:
        until = getattr(row, "snoozed_until", None)
        if until is None or getattr(row, "resent_at", None) is not None:
            continue
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until <= now:
            out.append(row)
    return out


async def snooze(ledger_id: str, *, minutes: int = 60, now: Optional[datetime] = None) -> bool:
    """"Snooze 1h" from the notification: the push comes back once, later."""
    now = now or datetime.now(timezone.utc)
    try:
        from services.database import PushLogModel, async_session

        async with async_session() as session:
            result = await session.execute(
                update(PushLogModel)
                .where(PushLogModel.id == ledger_id)
                .values(
                    snoozed_until=(now + timedelta(minutes=max(1, minutes))).replace(tzinfo=None),
                    resent_at=None,
                    opened_at=now.replace(tzinfo=None),
                    open_source="snooze",
                )
            )
            await session.commit()
            return bool(result.rowcount)
    except Exception as exc:  # noqa: BLE001
        logger.warning("push ledger snooze failed: %s", exc)
        return False


async def resend_due_snoozes(*, now: Optional[datetime] = None) -> int:
    """Called from the ``*/5`` ``proactive_sweep`` (2026-10-11; the retired
    interrupt ladder's advance loop called it every 30 s)."""
    now = now or datetime.now(timezone.utc)
    try:
        from services.database import PushLogModel, async_session

        async with async_session() as session:
            rows = list((await session.execute(
                select(PushLogModel).where(
                    PushLogModel.snoozed_until.is_not(None),
                    PushLogModel.resent_at.is_(None),
                    PushLogModel.snoozed_until <= now.replace(tzinfo=None),
                ).limit(10)
            )).scalars().all())
            due = due_for_resend(rows, now=now)
            for row in due:
                row.resent_at = now.replace(tzinfo=None)
            await session.commit()
            resend = [(r.title, r.body, r.url, r.tag, r.conversation_id) for r in due]
    except Exception as exc:  # noqa: BLE001
        logger.warning("push ledger resend scan failed: %s", exc)
        return 0
    sent = 0
    for title, body, url, tag, conversation_id in resend:
        try:
            from services import push_service

            await push_service.send_push_notification(
                title=f"Again: {title}", body=body, url=url, tag=tag,
                source="snooze", conversation_id=conversation_id,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("snoozed push resend failed: %s", exc)
    return sent


async def record(
    *,
    source: str,
    title: str,
    body: str,
    url: Optional[str],
    tag: Optional[str],
    conversation_id: Optional[str],
    delivery_id: Optional[str],
    sent: int,
    failed: int,
    held_reason: Optional[str],
    row_id: Optional[str] = None,
) -> Optional[str]:
    """Insert one ledger row. Never raises — a ledger failure must not stop a push."""
    try:
        from services.database import PushLogModel, async_session

        row_id = row_id or str(uuid.uuid4())
        async with async_session() as session:
            session.add(
                PushLogModel(
                    id=row_id,
                    source=(source or "unknown")[:40],
                    title=(title or "")[:200],
                    body=(body or "")[:1000],
                    url=(url or None),
                    tag=(tag or None),
                    conversation_id=conversation_id,
                    delivery_id=delivery_id,
                    sent=int(sent),
                    failed=int(failed),
                    held_reason=held_reason,
                )
            )
            await session.commit()
        return row_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("push ledger write failed: %s", exc)
        return None


async def mark_opened(
    *,
    source: str,
    delivery_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    ledger_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    """Stamp ``opened_at`` on the pushes a tap or an opened chat answers."""
    if not delivery_id and not conversation_id and not ledger_id:
        return 0
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    try:
        from services.database import PushLogModel, async_session

        async with async_session() as session:
            stmt = (
                update(PushLogModel)
                .where(PushLogModel.opened_at.is_(None))
                .values(opened_at=now, open_source=(source or "tap")[:20])
            )
            if ledger_id:
                stmt = stmt.where(PushLogModel.id == ledger_id)
            elif delivery_id:
                stmt = stmt.where(PushLogModel.delivery_id == delivery_id)
            else:
                # A chat opened within a day answers every push that pointed at it.
                stmt = stmt.where(
                    PushLogModel.conversation_id == conversation_id,
                    PushLogModel.sent_at > now - timedelta(hours=24),
                )
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("push ledger open-stamp failed: %s", exc)
        return 0


def local_day_bounds_utc(now: Optional[datetime] = None, tz: Any = None) -> tuple[datetime, datetime]:
    """The owner's local day around ``now`` as naive UTC ``[start, end)`` —
    the column convention. ``tz`` defaults to their zone (``timezone_utils``),
    never the host's. Pure but for the zone lookup."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if tz is None:
        from services.timezone_utils import get_user_tz

        tz = get_user_tz()
    local = now.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    # Midnight-to-midnight in the zone, re-resolved so a DST change day is
    # 23 or 25 hours long rather than off by one.
    next_local = (start_local + timedelta(days=1, hours=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = next_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


async def count_sent_today(
    source_prefix: str = "reach:",
    exclude: Iterable[str] = (),
    tz: Any = None,
    *,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """How many pushes under ``source_prefix`` (minus the ``exclude``d
    sources) reached a device in the owner's local day. ``reach_service``'s push
    budget reads it (2026-10-11). ``None`` when the ledger cannot be read —
    the caller treats that as "budget used", never as zero."""
    start, end = local_day_bounds_utc(now, tz)
    try:
        from sqlalchemy import func as sa_func

        from services.database import PushLogModel, async_session

        stmt = select(sa_func.count()).select_from(PushLogModel).where(
            PushLogModel.source.like(f"{source_prefix}%"),
            PushLogModel.sent > 0,
            PushLogModel.held_reason.is_(None),
            PushLogModel.sent_at >= start,
            PushLogModel.sent_at < end,
        )
        skip = [s for s in exclude if s]
        if skip:
            stmt = stmt.where(PushLogModel.source.not_in(skip))
        async with async_session() as session:
            return int((await session.execute(stmt)).scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("push ledger count failed: %s", exc)
        return None


async def recent(*, hours: float = 12, limit: int = 20) -> list[Any]:
    """Newest first. Empty on any failure so a prompt still builds."""
    try:
        from services.database import PushLogModel, async_session

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        async with async_session() as session:
            result = await session.execute(
                select(PushLogModel)
                .where(PushLogModel.sent_at > cutoff)
                .order_by(PushLogModel.sent_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("push ledger read failed: %s", exc)
        return []


async def recent_text(*, hours: float = 12, limit: int = 20) -> str:
    return format_recent(await recent(hours=hours, limit=limit))


def serialize(row: Any) -> dict:
    return {
        "id": row.id,
        "sent_at": row.sent_at.isoformat() + ("Z" if row.sent_at.tzinfo is None else ""),
        "source": row.source,
        "title": row.title,
        "body": row.body,
        "url": row.url,
        "tag": row.tag,
        "conversation_id": row.conversation_id,
        "delivery_id": row.delivery_id,
        "sent": row.sent,
        "failed": row.failed,
        "held_reason": row.held_reason,
        "opened_at": (row.opened_at.isoformat() + "Z") if row.opened_at else None,
        "open_source": row.open_source,
        "snoozed_until": (row.snoozed_until.isoformat() + "Z") if getattr(row, "snoozed_until", None) else None,
    }
