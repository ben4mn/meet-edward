"""The ping store: every read and write the runner, the report and the
mind's pack make on ``pings`` and the send ledger (AREA 3, 2026-10-11).

``pings`` times are naive UTC (``hooks.utcnow``). The send ledger
(``imessage_outgoing_sends``) is timestamptz: every value bound there is
aware UTC — a naive cutoff missed a row stamped now on a host Mac whose
clock is not UTC ("the timestamp bug"). Message text never goes to a log
from here.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql

from services.owner import owner_name
from services.pings.hooks import chat_key, utcnow

STATUSES = ("open", "running", "answered", "fallback", "declined", "failed", "held", "limited", "expired",
            "legacy")
TERMINAL = ("answered", "fallback", "declined", "failed", "held", "limited", "expired", "legacy")
RETENTION_DAYS = 90
OPEN_TOO_LONG_S = 600  # a ping still open or running this long after it was asked shows in open_now
_MARKABLE = {"status", "attempts", "next_attempt_at", "started_at", "first_send_at", "answered_at",
             "reply_message_rowid", "reply_route", "latency_s", "last_error", "note", "conversation_id",
             "chat_guid", "chat_name", "is_group", "merged_into", "media_ids"}
_COLUMNS = ("id, source_ref, channel, kind, status, must_answer, from_owner, third_party, sender_known, "
            "heartbeat_event_id, message_rowid, message_guid, chat_identifier, chat_guid, chat_name, is_group, "
            "sender, sender_label, text, conversation_id, attempts, next_attempt_at, asked_at, detected_at, "
            "started_at, first_send_at, answered_at, reply_message_rowid, reply_route, latency_s, last_error, note, "
            "updated_at")


def _session():
    from services.database import async_session

    return async_session()


def _row(r) -> Dict[str, Any]:
    return dict(r._mapping)


async def get(ping_id: int) -> Optional[Dict[str, Any]]:
    async with _session() as session:
        r = (await session.execute(sql(f"SELECT {_COLUMNS} FROM pings WHERE id = :id"),
                                   {"id": int(ping_id)})).first()
    return _row(r) if r is not None else None


async def due(now: Optional[datetime] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Open pings whose time has come, oldest asked first."""
    async with _session() as session:
        rows = (await session.execute(sql(
            f"SELECT {_COLUMNS} FROM pings WHERE status = 'open' "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) ORDER BY asked_at ASC, id ASC LIMIT :limit"),
            {"now": now or utcnow(), "limit": int(limit)})).all()
    return [_row(r) for r in rows]


async def claim(ping_id: int, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """open → running, one attempt more; the row, or None when another
    sweep (or a restart's recovery) got it first."""
    now = now or utcnow()
    async with _session() as session:
        r = (await session.execute(sql(
            "UPDATE pings SET status = 'running', attempts = attempts + 1, started_at = :now, updated_at = :now "
            f"WHERE id = :id AND status = 'open' RETURNING {_COLUMNS}"), {"id": int(ping_id), "now": now})).first()
        await session.commit()
    return _row(r) if r is not None else None


async def mark(ping_id: int, status: Optional[str] = None, **fields: Any) -> None:
    """Set ``status`` and any of the known columns; unknown keys are ignored."""
    values = {k: v for k, v in fields.items() if k in _MARKABLE}
    if status is not None:
        values["status"] = status
    if "last_error" in values and values["last_error"] is not None:
        values["last_error"] = str(values["last_error"])[:1000]
    if "note" in values and values["note"] is not None:
        values["note"] = str(values["note"])[:1000]
    values["updated_at"] = utcnow()
    sets = ", ".join(f"{k} = :{k}" for k in values)
    async with _session() as session:
        await session.execute(sql(f"UPDATE pings SET {sets} WHERE id = :id"), {**values, "id": int(ping_id)})
        await session.commit()


async def running() -> List[Dict[str, Any]]:
    async with _session() as session:
        rows = (await session.execute(sql(f"SELECT {_COLUMNS} FROM pings WHERE status = 'running' ORDER BY id"))).all()
    return [_row(r) for r in rows]


# The runner's own deliveries (``delivery.deliver``); every other ok send
# tied to a ping is the turn's own (``tool``).
RUNNER_PURPOSES = ("reply", "fallback", "limit")


async def answered_in_ledger(ping_id: int) -> Optional[Dict[str, Any]]:
    """An ok send the ledger tied to this ping that is not an ack: the
    runner's own delivery when there is one (it settled the ping), else the
    turn's first send."""
    async with _session() as session:
        r = (await session.execute(sql(
            "SELECT message_rowid, route, sent_at, purpose FROM imessage_outgoing_sends "
            "WHERE ping_id = :pid AND ok IS TRUE AND COALESCE(purpose, '') <> 'ack' "
            "ORDER BY CASE WHEN purpose IN ('reply', 'fallback', 'limit') THEN 0 ELSE 1 END, sent_at ASC LIMIT 1"),
            {"pid": int(ping_id)})).first()
    return _row(r) if r is not None else None


async def pings_in_chat_since(chat_identifier: str, since: datetime, *, third_party: Optional[bool] = None) -> int:
    """Pings asked in this chat since ``since`` (naive UTC); the loop limit's
    input. ``third_party`` counts only friends' (True) or only the owner's (False)."""
    where = "chat_identifier = :chat AND asked_at >= :since"
    params: Dict[str, Any] = {"chat": chat_identifier, "since": since}
    if third_party is not None:
        where += " AND third_party = :third"
        params["third"] = bool(third_party)
    async with _session() as session:
        return int((await session.execute(sql(f"SELECT COUNT(*) FROM pings WHERE {where}"), params)).scalar() or 0)


async def third_party_turns_in_chat_since(chat_identifier: str, since: datetime) -> int:
    """Third-party ping turns started in this chat since ``since``; the rate limit's input."""
    async with _session() as session:
        return int((await session.execute(sql(
            "SELECT COUNT(*) FROM pings WHERE chat_identifier = :chat AND third_party IS TRUE "
            "AND started_at IS NOT NULL AND started_at >= :since"), {"chat": chat_identifier, "since": since})
        ).scalar() or 0)


async def recent_ok_send(chat: str, norm_hash: str, since: datetime, *,
                         ping_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """An ok send of the same normalised text into this chat (any spelling)
    since ``since`` — aware UTC (a naive one is taken as UTC). With
    ``ping_id``, only that ping's own sends count: two friends' asks that
    both end in the same template are two replies, not one (review
    2026-10-11)."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    key = chat_key(chat)
    where = "ok IS TRUE AND text_norm_hash = :h AND sent_at >= :since"
    params: Dict[str, Any] = {"h": norm_hash, "since": since}
    if ping_id is not None:
        where += " AND ping_id = :pid"
        params["pid"] = int(ping_id)
    async with _session() as session:
        rows = (await session.execute(sql(
            "SELECT chat_identifier, chat_guid, sent_at, message_rowid, route FROM imessage_outgoing_sends "
            f"WHERE {where} ORDER BY sent_at DESC"), params)).all()
    for r in rows:
        if key and key in (chat_key(r.chat_identifier), chat_key(r.chat_guid)):
            return _row(r)
    return None


# ----------------------------------------------------- what the owner sees ----

def _local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    try:
        from services.timezone_utils import utc_to_local

        return utc_to_local(dt)
    except Exception:  # noqa: BLE001
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def clock(dt: Optional[datetime]) -> str:
    """"7:34 pm" in the owner's zone (the one clock format)."""
    local = _local(dt)
    if local is None:
        return "?"
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def _who(row: Dict[str, Any]) -> str:
    if row.get("from_owner"):
        return owner_name()
    return str(row.get("sender_label") or row.get("sender") or "someone")


def _where(row: Dict[str, Any]) -> str:
    if row.get("kind") == "owner_direct":
        return "in their chat with you"
    return f"in {row.get('chat_name') or row.get('chat_identifier') or 'a chat'}"


def _quote(value: Optional[str], limit: int = 60) -> str:
    t = " ".join(str(value or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _ending(row: Dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    if status in ("answered", "fallback") and row.get("answered_at"):
        latency = row.get("latency_s")
        took = f" ({int(round(latency))} s)" if latency is not None else ""
        return f"{status} {clock(row['answered_at'])}{took}"
    return status


async def pack_lines(now: Optional[datetime] = None, hours: float = 6) -> List[str]:
    """Recent pings and how each ended, for the mind's pack:
    ``7:28 pm Sam in Trivia crew "@edward look at…" → answered 7:29 pm (61 s)``
    (the owner by ``owner.owner_name()``)."""
    now = now or utcnow()
    async with _session() as session:
        rows = (await session.execute(sql(
            f"SELECT {_COLUMNS} FROM pings WHERE asked_at >= :since AND asked_at <= :now ORDER BY asked_at ASC"),
            {"since": now - timedelta(hours=float(hours)), "now": now + timedelta(minutes=1)})).all()
    out = []
    for r in rows:
        row = _row(r)
        out.append(f'{clock(row["asked_at"])} {_who(row)} {_where(row)} "{_quote(row.get("text"))}" → {_ending(row)}')
    return out


async def report(days: int = 7) -> Dict[str, Any]:
    """The ``pings`` section of ``/api/health/report``: counts over ``days``
    plus one bucket per day (the owner's zone), oldest first."""
    from services import imessage_sent

    now = utcnow()
    since = now - timedelta(days=int(days))
    async with _session() as session:
        rows = [_row(r) for r in (await session.execute(sql(
            f"SELECT {_COLUMNS} FROM pings WHERE asked_at >= :since ORDER BY asked_at ASC"),
            {"since": since})).all()]
    by_status = {s: 0 for s in STATUSES}
    by_who = {"owner": 0, "self_chat": 0, "friend": 0}
    latencies: List[float] = []
    within = 0
    open_now = []
    self_replies = 0
    today_local = _local(now).date()
    first_day = today_local - timedelta(days=int(days) - 1)
    buckets: Dict[str, Dict[str, Any]] = {}
    for i in range(int(days)):
        d = (first_day + timedelta(days=i)).isoformat()
        buckets[d] = {"date": d, "total": 0, "answered": 0, "fallback": 0, "declined": 0, "failed": 0,
                      "other": 0, "within_2m": 0}
    for row in rows:
        status = str(row.get("status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        if row.get("kind") == "owner_direct":
            by_who["self_chat"] += 1
        elif row.get("from_owner"):
            by_who["owner"] += 1
        else:
            by_who["friend"] += 1
        latency = row.get("latency_s")
        replied = status in ("answered", "fallback") and latency is not None
        if replied:
            latencies.append(float(latency))
            if float(latency) <= 120:
                within += 1
        if status in ("open", "running"):
            age = (now - row["asked_at"]).total_seconds()
            if age > OPEN_TOO_LONG_S:
                open_now.append({"id": row["id"], "chat": row.get("chat_name") or row.get("chat_identifier"),
                                 "age_s": int(age)})
        if imessage_sent.is_edward_rowid(row.get("message_rowid")):
            self_replies += 1
        day = _local(row["asked_at"]).date().isoformat()
        bucket = buckets.get(day)
        if bucket is not None:
            bucket["total"] += 1
            bucket[status if status in ("answered", "fallback", "declined", "failed") else "other"] += 1
            if replied and float(latency) <= 120:
                bucket["within_2m"] += 1
    return {
        "days": int(days),
        "total": len(rows),
        "by_status": by_status,
        "by_who": by_who,
        "answered_within_2m": within,
        "median_latency_s": int(round(statistics.median(latencies))) if latencies else None,
        "open_now": open_now,
        "self_replies": self_replies,
        "by_day": list(buckets.values()),
    }


async def prune(days: int = RETENTION_DAYS) -> Dict[str, int]:
    """Drop pings and send-ledger rows older than ``days`` (90: the ledger's
    ``ok`` and ``message_rowid`` are load-bearing for that long)."""
    async with _session() as session:
        p = await session.execute(sql("DELETE FROM pings WHERE asked_at < :cut"),
                                  {"cut": utcnow() - timedelta(days=days)})
        s = await session.execute(sql("DELETE FROM imessage_outgoing_sends WHERE sent_at < :cut"),
                                  {"cut": datetime.now(timezone.utc) - timedelta(days=days)})
        await session.commit()
    return {"pings": int(p.rowcount or 0), "sends": int(s.rowcount or 0)}
