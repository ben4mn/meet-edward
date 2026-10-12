"""Aggregators for the always-on display.

Each function returns a JSON-serializable dict. Endpoints in
``routers/display.py`` are thin wrappers around these.
"""

import asyncio
import json
import shutil
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func, select, or_

from services.database import (
    async_session,
    ConversationModel,
    DocumentModel,
    HeartbeatEventModel,
    MemoryModel,
    OrchestratorTaskModel,
    ScheduledEventModel,
    TriageResultModel,
)
from services.timezone_utils import now_local


# ---------------------------------------------------------------------------
# Calendar — Calendar's own database (``calendar_service``, 2026-10-11),
# else the apple-mcp call, behind a shared in-process cache.
#
# 2026-09-25 audit: with a 30 s cache, no single-flight and no memory of a
# failure, ``/screen``'s signal pollers (two every 2 s) each started their
# own 10 s MCP read whenever apple-mcp was slow — 17 MCP timeouts in 35
# minutes, each reopening the session under every other MCP caller, and
# every wake's pack reading "calendar unreadable". Now: one read at a time,
# a good read kept three minutes, a failed read remembered a minute, and
# for half an hour after a failure the last good list for today is served,
# flagged ``stale``.
# ---------------------------------------------------------------------------

# data / fetched_at / ttl: what to serve and until when; last_good,
# last_good_at, last_good_day: the last list apple-mcp actually returned.
_calendar_cache: Dict[str, Any] = {"data": None, "fetched_at": 0.0}
_CALENDAR_TTL_SECONDS = 180
_CALENDAR_FAILURE_TTL_SECONDS = 60
_CALENDAR_STALE_MAX_SECONDS = 30 * 60
_calendar_lock: Optional[asyncio.Lock] = None
_calendar_lock_loop: Any = None
_calendar_refresh_task: Optional["asyncio.Task[Any]"] = None
# The day's list is one small AppleScript read; ten seconds is plenty. It is
# awaited by every wake's pack and every pulse's signals, so a hung
# apple-mcp call used to freeze all of them (2026-09-24).
CALENDAR_MCP_TIMEOUT_SECONDS = 10


def _host_label() -> str:
    """This Mac's short name for the vitals panel (``my-mac`` from
    ``my-mac.local``)."""
    try:
        from services.host_config import local_hostname

        name = local_hostname()
    except Exception:  # noqa: BLE001
        name = ""
    return name[:-len(".local")] if name.endswith(".local") else (name or "this mac")


def _kind_for_event(title: str) -> str:
    t = (title or "").lower()
    if any(w in t for w in ("standup", "1:1", "review", "sync", "meet", "interview", "demo")):
        return "work"
    if any(w in t for w in ("focus", "block", "writing", "deep work")):
        return "focus"
    return "personal"


def _find_calendar_tool():
    """Find the calendar list/upcoming tool from Apple MCP tools."""
    from services.mcp_client import get_apple_mcp_tools, is_apple_available

    if not is_apple_available():
        return None

    tools = get_apple_mcp_tools()
    # Look for a tool that reads calendar events — tool may be named simply "calendar"
    for t in tools:
        name = t.name.lower()
        if "calendar" in name:
            return t
    return None


async def _calendar_via_mcp() -> Optional[List[Dict[str, Any]]]:
    """Fetch today's events from apple-mcp. Returns None if MCP unavailable."""
    try:
        tool = _find_calendar_tool()
    except Exception:
        return None
    if tool is None:
        return None

    now = now_local()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    try:
        from services.mcp_client import call_tool_bounded

        result = await call_tool_bounded(tool, {
            "operation": "list",
            "fromDate": start.isoformat(),
            "toDate": end.isoformat(),
        }, timeout=CALENDAR_MCP_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — a timeout included: None means "use the fallback"
        if isinstance(exc, TimeoutError):
            print(f"[display] calendar via apple-mcp gave up: {exc}")
        return None

    raw = result.content if hasattr(result, "content") else result
    parsed: Any = None
    if isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str or raw_str.lower().startswith("no events"):
            return []
        try:
            parsed = json.loads(raw_str)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw, (dict, list)):
        parsed = raw
    else:
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            return None

    items: List[Dict[str, Any]]
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = parsed.get("events") or parsed.get("items") or []
    else:
        return None

    # Normalize to the shape the panel expects.
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(items):
        if not isinstance(e, dict):
            continue
        # Apple-mcp returns startDate/endDate (ISO) or a similar field — be lenient.
        start_raw = e.get("startDate") or e.get("start") or e.get("start_date")
        end_raw = e.get("endDate") or e.get("end") or e.get("end_date")
        try:
            start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        title = e.get("title") or e.get("summary") or "(untitled)"
        location = e.get("location") or ""
        out.append({
            "id": str(e.get("id") or e.get("uid") or f"cal-{i}"),
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "where": location,
            "kind": _kind_for_event(title),
            # apple-mcp's own flag (review 2026-09-26): /screen showed an
            # all-day event as "12:00 am – 12:00 am · in now". The pages
            # infer it from a midnight-to-midnight shape only when absent.
            "all_day": bool(e.get("isAllDay") or e.get("allDay") or e.get("all_day")),
        })
    out.sort(key=lambda r: r["start"])
    return out


async def _calendar_read() -> Optional[Tuple[List[Dict[str, Any]], str, bool]]:
    """Today's events, (events, via, stale), or None when nothing can read
    Apple Calendar. Calendar's own database first (``calendar_service``,
    2026-10-11: milliseconds, no AppleScript), apple-mcp when that can't
    be read; an immutable database read (stale: it misses the WAL) is used
    only when apple-mcp fails too."""
    from services import calendar_service

    stale_rows: Optional[List[Dict[str, Any]]] = None
    if calendar_service.enabled():
        try:
            rows, stale = await calendar_service.today_display_events()
            if not stale:
                calendar_service.note_source("sqlite")
                return rows, "sqlite", False
            stale_rows = rows
        except calendar_service.CalendarUnavailable as exc:
            _log_calendar_once(str(exc))
    events = await _calendar_via_mcp()
    if events is not None:
        calendar_service.note_source("mcp")
        return events, "mcp", False
    if stale_rows is not None:
        calendar_service.note_source("sqlite", "an immutable read (may miss today's edits)")
        return stale_rows, "sqlite", True
    calendar_service.note_source("unavailable", "neither the database nor apple-mcp answered")
    return None


_calendar_logged: set = set()


def _log_calendar_once(reason: str) -> None:
    if reason not in _calendar_logged:
        _calendar_logged.add(reason)
        print(f"[display] calendar database unreadable, using apple-mcp: {reason}")


async def _calendar_fallback_from_events() -> List[Dict[str, Any]]:
    """If apple-mcp isn't configured, surface today's scheduled_events as a stand-in."""
    # ScheduledEventModel.next_fire_at is stored as TIMESTAMP WITHOUT TIME
    # ZONE. asyncpg refuses to bind a tz-aware value against it (DataError:
    # "can't subtract offset-naive and offset-aware datetimes"). Compute the
    # day window in local time, then convert to naive UTC for the query.
    now_l = now_local()
    start_local = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    async with async_session() as session:
        rows = (await session.execute(
            select(ScheduledEventModel)
            .where(ScheduledEventModel.next_fire_at >= start)
            .where(ScheduledEventModel.next_fire_at < end)
            .order_by(ScheduledEventModel.next_fire_at.asc())
        )).scalars().all()

    out: List[Dict[str, Any]] = []
    for r in rows:
        fire = r.next_fire_at
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=timezone.utc).astimezone()
        out.append({
            "id": r.id,
            "title": (r.description or "")[:80] or "(scheduled event)",
            "start": fire.isoformat(),
            "end": (fire + timedelta(minutes=15)).isoformat(),
            "where": "",
            "kind": "focus",
        })
    return out


def _calendar_fresh() -> Optional[Dict[str, Any]]:
    """The cached answer while it is still within its TTL, else None."""
    cached = _calendar_cache.get("data")
    if cached is None:
        return None
    ttl = _calendar_cache.get("ttl", _CALENDAR_TTL_SECONDS)
    if time.time() - _calendar_cache.get("fetched_at", 0.0) < ttl:
        return cached
    return None


def _calendar_read_lock() -> asyncio.Lock:
    """One calendar read at a time (a Lock belongs to the loop it was
    first used on; tests run a loop per test)."""
    global _calendar_lock, _calendar_lock_loop
    loop = asyncio.get_running_loop()
    if _calendar_lock is None or _calendar_lock_loop is not loop:
        _calendar_lock = asyncio.Lock()
        _calendar_lock_loop = loop
    return _calendar_lock


def _last_good_calendar(now: float) -> Optional[Dict[str, Any]]:
    """The last list apple-mcp returned, flagged stale, while it is under
    ``_CALENDAR_STALE_MAX_SECONDS`` old and still today's."""
    last_good = _calendar_cache.get("last_good")
    if last_good is None:
        return None
    if now - _calendar_cache.get("last_good_at", 0.0) >= _CALENDAR_STALE_MAX_SECONDS:
        return None
    if _calendar_cache.get("last_good_day") != now_local().date().isoformat():
        return None
    return {**last_good, "stale": True}


async def get_calendar() -> Dict[str, Any]:
    """Today's calendar: Calendar's own database or apple-mcp when either
    answers (``source="apple-mcp"``, ``via`` sqlite|mcp), the last good list
    (``stale``) for a while when neither does, else Edward's own jobs
    (``source="scheduled_events"``, which readers treat as unreadable)."""
    cached = _calendar_fresh()
    if cached is not None:
        return cached
    async with _calendar_read_lock():
        # Whoever held the lock may have just read it.
        cached = _calendar_fresh()
        if cached is not None:
            return cached

        read = await _calendar_read()
        now = time.time()
        if read is not None and not read[2]:
            # ``source`` stays "apple-mcp" — Apple Calendar answered — the
            # field every reader checks (mind pack, meeting prep, the
            # stale note); ``via`` says how (sqlite | mcp).
            result = {
                "source": "apple-mcp",
                "via": read[1],
                "fetched_at": datetime.utcnow().isoformat() + "Z",
                "events": read[0],
            }
            _calendar_cache.update(data=result, fetched_at=now, ttl=_CALENDAR_TTL_SECONDS,
                                   last_good=result, last_good_at=now,
                                   last_good_day=now_local().date().isoformat())
            return result
        if read is not None:
            result = {"source": "apple-mcp", "via": read[1], "stale": True,
                      "fetched_at": datetime.utcnow().isoformat() + "Z", "events": read[0]}
            _calendar_cache.update(data=result, fetched_at=now, ttl=_CALENDAR_FAILURE_TTL_SECONDS)
            return result

        result = _last_good_calendar(now)
        if result is None:
            result = {
                "source": "scheduled_events",
                "fetched_at": datetime.utcnow().isoformat() + "Z",
                "events": await _calendar_fallback_from_events(),
            }
        _calendar_cache.update(data=result, fetched_at=now, ttl=_CALENDAR_FAILURE_TTL_SECONDS)
        return result


def get_calendar_nowait() -> Optional[Dict[str, Any]]:
    """Whatever the cache holds — however old, or None — without waiting.
    An expired cache starts one background read. For pollers (the display
    signals, every 2 s) that must never sit behind apple-mcp."""
    global _calendar_refresh_task
    if _calendar_fresh() is None and (_calendar_refresh_task is None or _calendar_refresh_task.done()):
        try:
            _calendar_refresh_task = asyncio.get_running_loop().create_task(_refresh_calendar_quietly())
        except RuntimeError:  # no running loop
            pass
    return _calendar_cache.get("data")


async def _refresh_calendar_quietly() -> None:
    try:
        await get_calendar()
    except Exception as exc:  # noqa: BLE001 — a background read never raises into a poller
        print(f"[display] background calendar read failed: {exc}")


# ---------------------------------------------------------------------------
# Vitals — host stats via psutil + token totals + memory/doc counts.
# ---------------------------------------------------------------------------

_psutil_warned = False


# Rough $/Mtok rates (input, output) for the models Edward dispatches against.
# Substring-matched against the model_id stored on each usage row; the first
# substring that matches wins. Update when pricing shifts.
_MODEL_RATES: list[tuple[str, tuple[float, float]]] = [
    # Anthropic
    ("opus-4-7",       (15.0, 75.0)),
    ("opus-4-6",       (15.0, 75.0)),
    ("opus-4",         (15.0, 75.0)),
    ("sonnet-4-7",     (3.0, 15.0)),
    ("sonnet-4-6",     (3.0, 15.0)),
    ("sonnet-4",       (3.0, 15.0)),
    ("haiku-4-5",      (1.0, 5.0)),
    ("haiku",          (0.25, 1.25)),
    # OpenAI
    ("gpt-5-4",        (3.0, 12.0)),
    ("gpt-5",          (3.0, 12.0)),
    ("gpt-5-mini",     (0.4, 1.6)),
    ("gpt-4o",         (2.5, 10.0)),
    ("gpt-4o-mini",    (0.15, 0.6)),
]


def _model_rate(model_id: str) -> tuple[float, float]:
    m = (model_id or "").lower()
    for prefix, rates in _MODEL_RATES:
        if prefix in m:
            return rates
    # Unknown model — return 0 so an unfamiliar model doesn't inflate the
    # cost number with a wild guess.
    return (0.0, 0.0)


async def get_vitals() -> Dict[str, Any]:
    global _psutil_warned

    cpu_pct: Optional[float] = None
    mem_pct: Optional[float] = None
    disk_pct: Optional[float] = None
    net_kbps: Optional[float] = None
    uptime_seconds: Optional[float] = None

    try:
        import psutil
        # Non-blocking sample. First call to cpu_percent(interval=None) returns
        # 0.0; rely on the periodic poll cadence (5s) to give a meaningful
        # number on the second tick.
        cpu_pct = psutil.cpu_percent(interval=None)
        mem_pct = psutil.virtual_memory().percent
        # NOT "/" — on Apple Silicon that is the sealed read-only system
        # volume (~40% used and basically static), so the panel reported a
        # reassuring number while the data volume it actually shares with
        # Postgres, backups and storage/ sat at 92%. Measure where the
        # bytes really land. (Found 2026-08-22 building the hub meters.)
        disk_pct = psutil.disk_usage(os.path.expanduser("~")).percent
        # Net rate measured over a 0.4s window — small jitter in the panel is
        # fine and a longer sample would block the request.
        n0 = psutil.net_io_counters()
        await asyncio.sleep(0.4)
        n1 = psutil.net_io_counters()
        bytes_per_s = ((n1.bytes_sent + n1.bytes_recv) - (n0.bytes_sent + n0.bytes_recv)) / 0.4
        net_kbps = bytes_per_s / 1024.0
        uptime_seconds = time.time() - psutil.boot_time()
    except ImportError:
        if not _psutil_warned:
            print("[display] psutil not installed — host stats will be null")
            _psutil_warned = True
    except Exception as e:
        print(f"[display] psutil error: {e}")

    # Disk-free fallback (no psutil)
    if disk_pct is None:
        try:
            total, used, _free = shutil.disk_usage("/")
            disk_pct = (used / total) * 100 if total else None
        except Exception:
            pass

    # Token totals + convos today + computed cost
    tokens_in_today = 0
    tokens_out_today = 0
    cost_today: float = 0.0
    convos_today = 0
    try:
        from services.database import UsageEventModel
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with async_session() as session:
            row = (await session.execute(
                select(
                    func.coalesce(func.sum(UsageEventModel.input_tokens), 0),
                    func.coalesce(func.sum(UsageEventModel.output_tokens), 0),
                ).where(UsageEventModel.created_at >= today_start)
            )).one()
            tokens_in_today = int(row[0] or 0)
            tokens_out_today = int(row[1] or 0)

            convos_today = int((await session.execute(
                select(func.count(ConversationModel.id))
                .where(ConversationModel.created_at >= today_start)
            )).scalar() or 0)

            # Per-model cost. Rates are best-effort approximations of public
            # pricing for the models Edward actually uses; small drift is
            # fine for a desk display. Cache-aware totals are a TODO — for
            # now we lump cache_creation/read into input.
            per_model = (await session.execute(
                select(
                    UsageEventModel.model,
                    func.coalesce(func.sum(UsageEventModel.input_tokens), 0),
                    func.coalesce(func.sum(UsageEventModel.output_tokens), 0),
                    func.coalesce(func.sum(UsageEventModel.cache_creation_tokens), 0),
                    func.coalesce(func.sum(UsageEventModel.cache_read_tokens), 0),
                )
                .where(UsageEventModel.created_at >= today_start)
                .group_by(UsageEventModel.model)
            )).all()
            for model_name, in_t, out_t, cc_t, cr_t in per_model:
                rate_in, rate_out = _model_rate(model_name or "")
                # cache_creation ≈ 1.25× input, cache_read ≈ 0.1× input — the
                # standard Anthropic ratios; close enough for OpenAI too.
                cost_today += (
                    (in_t / 1_000_000.0) * rate_in
                    + (out_t / 1_000_000.0) * rate_out
                    + (cc_t / 1_000_000.0) * rate_in * 1.25
                    + (cr_t / 1_000_000.0) * rate_in * 0.1
                )
    except Exception as e:
        print(f"[display] usage tally failed: {e}")

    # Memory + document counts
    memory_count = 0
    docs_count = 0
    try:
        async with async_session() as session:
            memory_count = int((await session.execute(
                select(func.count(MemoryModel.id))
            )).scalar() or 0)
            docs_count = int((await session.execute(
                select(func.count(DocumentModel.id))
            )).scalar() or 0)
    except Exception as e:
        print(f"[display] count queries failed: {e}")

    # Configured model
    model = "claude-sonnet-4-7"
    try:
        from services.settings_service import get_settings
        s = await get_settings()
        model = s.model or model
    except Exception:
        pass

    return {
        "host": _host_label(),
        "uptime_seconds": uptime_seconds,
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "disk_pct": disk_pct,
        "net_kbps": net_kbps,
        "model": model,
        "tokens_in_today": tokens_in_today,
        "tokens_out_today": tokens_out_today,
        "cost_today": cost_today,
        "convos_today": convos_today,
        "memory_count": memory_count,
        "docs_count": docs_count,
    }


# ---------------------------------------------------------------------------
# Workers — heartbeat tracks + in-process loop statuses.
# ---------------------------------------------------------------------------

def _seconds_ago(then: Optional[datetime]) -> Optional[int]:
    if then is None:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds()))


def _fmt_ago(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


async def get_workers() -> Dict[str, Any]:
    workers: List[Dict[str, Any]] = []

    # Per-source "last event seen" map for heartbeat tracks. One query per
    # source is fine — there are 4 sources and these have indexes on created_at.
    last_per_source: Dict[str, Optional[datetime]] = {}
    try:
        async with async_session() as session:
            for source in ("imessage", "calendar", "email", "gmail"):
                row = (await session.execute(
                    select(func.max(HeartbeatEventModel.created_at))
                    .where(HeartbeatEventModel.source == source)
                )).scalar()
                last_per_source[source] = row
    except Exception:
        pass

    # Heartbeat tracks (already a structured snapshot)
    try:
        from services.heartbeat.heartbeat_service import get_heartbeat_status
        hb = await get_heartbeat_status()
        for name, track in (hb.get("tracks") or {}).items():
            enabled = bool(track.get("enabled"))
            status = (track.get("status") or "").lower() or ("running" if enabled else "disabled")
            running = enabled and status in ("running", "polling", "listening", "active")
            error = "error" in status or status == "failed"
            tone = "ok" if running else ("danger" if error else "mute")
            last_seen = last_per_source.get(name)
            workers.append({
                "name": f"{name}-listener",
                "status": status,
                "tone": tone,
                "last": _fmt_ago(_seconds_ago(last_seen)),
            })
    except Exception as e:
        print(f"[display] heartbeat status failed: {e}")

    # Scheduler — last_fired_at from any scheduled event
    try:
        from services import scheduler_service
        running = bool(getattr(scheduler_service, "_scheduler_task", None))
        last_fire: Optional[datetime] = None
        try:
            async with async_session() as session:
                last_fire = (await session.execute(
                    select(func.max(ScheduledEventModel.last_fired_at))
                )).scalar()
        except Exception:
            pass
        workers.append({
            "name": "scheduled-events",
            "status": "running" if running else "idle",
            "tone": "ok" if running else "mute",
            "last": _fmt_ago(_seconds_ago(last_fire)),
        })
    except Exception:
        pass

    # MCP clients
    try:
        from services.mcp_client import is_apple_available, is_whatsapp_available
        if is_apple_available():
            workers.append({"name": "apple-mcp", "status": "running", "tone": "ok", "last": "—"})
        if is_whatsapp_available():
            workers.append({"name": "whatsapp-bridge", "status": "running", "tone": "ok", "last": "—"})
    except Exception:
        pass

    # Consolidation loop — last cycle timestamp
    try:
        from services import consolidation_service
        running = bool(getattr(consolidation_service, "_consolidation_task", None))
        last_cycle: Optional[datetime] = None
        try:
            from services.database import ConsolidationCycleModel
            async with async_session() as session:
                last_cycle = (await session.execute(
                    select(func.max(ConsolidationCycleModel.created_at))
                )).scalar()
        except Exception:
            pass
        workers.append({
            "name": "memory-consolidator",
            "status": "running" if running else "idle",
            "tone": "ok" if running else "mute",
            "last": _fmt_ago(_seconds_ago(last_cycle)),
        })
    except Exception:
        pass

    # Orchestrator (count active worker tasks)
    active_workers = 0
    try:
        async with async_session() as session:
            active_workers = int((await session.execute(
                select(func.count(OrchestratorTaskModel.id))
                .where(OrchestratorTaskModel.status == "running")
            )).scalar() or 0)
    except Exception:
        pass
    workers.append({
        "name": "orchestrator",
        "status": "running" if active_workers > 0 else "idle",
        "tone": "ok" if active_workers > 0 else "mute",
        "last": f"{active_workers} active" if active_workers else "—",
    })

    # Cloudflared tunnel — best-effort process check via psutil
    try:
        import psutil
        tunnel_running = any(
            (p.info.get("name") or "").startswith("cloudflared")
            for p in psutil.process_iter(attrs=["name"])
        )
        if tunnel_running:
            workers.append({"name": "cloudflared-tunnel", "status": "running", "tone": "ok", "last": "—"})
    except Exception:
        pass

    running_count = sum(1 for w in workers if w["status"] == "running")
    return {
        "running_count": running_count,
        "total": len(workers),
        "workers": workers,
    }


# ---------------------------------------------------------------------------
# Recents — union of triage/events/memories/conversations.
# ---------------------------------------------------------------------------

async def get_recents(limit: int = 10) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []

    async with async_session() as session:
        # Recent escalated/acted triage cycles
        triage_rows = (await session.execute(
            select(TriageResultModel)
            .order_by(desc(TriageResultModel.created_at))
            .limit(limit)
        )).scalars().all()
        for t in triage_rows:
            verb = "ran"
            obj = f"heartbeat triage cycle #{t.cycle_number}"
            actions = []
            if t.events_acted:
                actions.append(f"{t.events_acted} acted")
            if t.events_escalated:
                actions.append(f"{t.events_escalated} escalated")
            if t.events_noted:
                actions.append(f"{t.events_noted} noted")
            status = " · ".join(actions) if actions else "ok"
            items.append({
                "t": t.created_at.isoformat() if t.created_at else None,
                "verb": verb,
                "obj": obj,
                "status": status,
            })

        # Recent fired scheduled_events (those with last_fired_at populated)
        event_rows = (await session.execute(
            select(ScheduledEventModel)
            .where(ScheduledEventModel.last_fired_at.is_not(None))
            .order_by(desc(ScheduledEventModel.last_fired_at))
            .limit(limit)
        )).scalars().all()
        for e in event_rows:
            items.append({
                "t": e.last_fired_at.isoformat() if e.last_fired_at else None,
                "verb": "fired",
                "obj": (e.description or "")[:80],
                "status": (e.last_result or e.status or "ok")[:40],
            })

        # Recent memories
        memory_rows = (await session.execute(
            select(MemoryModel)
            .order_by(desc(MemoryModel.created_at))
            .limit(limit)
        )).scalars().all()
        for m in memory_rows:
            items.append({
                "t": m.created_at.isoformat() if m.created_at else None,
                "verb": "remembered",
                "obj": (m.content or "")[:80],
                "status": (m.memory_type or "stored").upper(),
            })

        # Recent conversations
        conv_rows = (await session.execute(
            select(ConversationModel)
            .order_by(desc(ConversationModel.updated_at))
            .limit(limit)
        )).scalars().all()
        for c in conv_rows:
            items.append({
                "t": c.updated_at.isoformat() if c.updated_at else None,
                "verb": "chatted",
                "obj": (c.title or "(untitled)")[:80],
                "status": (c.source or "user").upper(),
            })

    # Sort by timestamp desc, take top N
    items.sort(key=lambda x: x["t"] or "", reverse=True)
    return {"items": items[:limit]}


# ---------------------------------------------------------------------------
# Memory ticker — random subset of (presumed) shareable memories.
# ---------------------------------------------------------------------------

async def get_memory_ticker(limit: int = 20) -> Dict[str, Any]:
    """Random short, non-private memories for the marquee.

    Filter: 30 < length < 200 chars (kills the noisy edge cases — empty
    fragments and giant document-y memories that won't fit a marquee).
    """
    async with async_session() as session:
        # postgres random() to avoid pulling everything into memory
        rows = (await session.execute(
            select(MemoryModel.content, MemoryModel.id)
            .where(MemoryModel.content.isnot(None))
            .where(func.length(MemoryModel.content) > 30)
            .where(func.length(MemoryModel.content) < 200)
            .where(or_(
                MemoryModel.valid_until.is_(None),
                MemoryModel.valid_until > datetime.utcnow(),
            ))
            .order_by(func.random())
            .limit(limit)
        )).all()

        total = int((await session.execute(
            select(func.count(MemoryModel.id))
        )).scalar() or 0)

    return {
        "total": total,
        "items": [r[0] for r in rows],
    }


# ---------------------------------------------------------------------------
# Now thinking — most recent running orchestrator task.
# ---------------------------------------------------------------------------

async def get_now_thinking() -> Optional[Dict[str, Any]]:
    async with async_session() as session:
        row = (await session.execute(
            select(OrchestratorTaskModel)
            .where(OrchestratorTaskModel.status == "running")
            .order_by(desc(OrchestratorTaskModel.started_at))
            .limit(1)
        )).scalar_one_or_none()

    if row is None:
        return None

    started = row.started_at or row.created_at
    elapsed = _seconds_ago(started) if started else 0
    return {
        "id": row.id,
        "title": (row.task_description or "")[:120],
        "detail": f"{row.task_type} · model {row.model}",
        "started_at": started.isoformat() if started else None,
        "elapsed_seconds": elapsed,
        # Steps/sources/confidence aren't tracked yet — leave empty so the
        # frontend renders a tasteful minimal card. Wire these up if/when
        # orchestrator records intermediate progress.
        "steps": [],
        "sources": [],
        "confidence": None,
    }


# ---------------------------------------------------------------------------
# Alerts — escalated triage + soon-upcoming events + warn workers.
# ---------------------------------------------------------------------------

async def get_alerts(limit: int = 5) -> Dict[str, Any]:
    alerts: List[Dict[str, Any]] = []

    # Soon-upcoming calendar events (next 30 minutes)
    try:
        cal = await get_calendar()
        now = datetime.now().astimezone()
        for ev in cal.get("events", []):
            try:
                start = datetime.fromisoformat(ev["start"])
            except (TypeError, ValueError, KeyError):
                continue
            delta = start - now
            if timedelta(minutes=0) <= delta <= timedelta(minutes=30):
                mins = max(1, int(delta.total_seconds() / 60))
                alerts.append({
                    "level": "warn",
                    "t": "now",
                    "text": f"{ev['title']} in {mins}m" + (f" — {ev['where']}" if ev.get("where") else ""),
                })
    except Exception:
        pass

    # Recent escalated/mention heartbeat events
    try:
        async with async_session() as session:
            hb_rows = (await session.execute(
                select(HeartbeatEventModel)
                .where(HeartbeatEventModel.triage_status.in_(["escalated", "mention"]))
                .order_by(desc(HeartbeatEventModel.created_at))
                .limit(3)
            )).scalars().all()
        for r in hb_rows:
            text_str = r.summary or ""
            alerts.append({
                "level": "info" if r.triage_status == "mention" else "warn",
                "t": _fmt_ago(_seconds_ago(r.created_at)),
                "text": f"{r.contact_name or r.sender or '?'}: {text_str[:80]}",
            })
    except Exception:
        pass

    return {"items": alerts[:limit]}
