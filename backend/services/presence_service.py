"""Presence I/O: beacon registry, derived signals, the host Mac's own probe.

Pure rules live in ``presence_logic``; this module owns the process-local
registry (authoritative for freshness), write-through persistence to
``presence_beacons`` (so a restart still knows which devices exist), the
derived-signal reads, and the 30s ``ioreg`` probe that makes the Mac
Edward runs on the first computer that reports itself.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

from services.database import (
    HeartbeatEventModel,
    PresenceBeaconModel,
    PushSubscriptionModel,
    WidgetTokenModel,
    async_session,
)
from services.presence_logic import (
    ACTIVE_IDLE_MAX_SECONDS,
    AttentionState,
    Beacon,
    DerivedSignals,
    at_device,
    compute_attention,
    normalize_beacon,
    parse_hid_idle_seconds,
)

# The host Mac's own probe. The id is stored in presence_beacons (and the
# tests use it), so it keeps its historical name on any Mac.
LOCAL_MAC_DEVICE_ID = "mac-mini-backend"
LOCAL_PROBE_INTERVAL_SECONDS = 30
_ATTENTION_CACHE_TTL = 5.0

_registry: Dict[str, Beacon] = {}
_probe_task: Optional[asyncio.Task] = None
_attention_cache: tuple[float, Optional[AttentionState]] = (0.0, None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- beacons ----------------------------------------------------------------

async def _persist_beacon(beacon: Beacon) -> None:
    async with async_session() as session:
        row = await session.get(PresenceBeaconModel, beacon.device_id)
        if row is None:
            row = PresenceBeaconModel(device_id=beacon.device_id)
            session.add(row)
        row.device_name = beacon.device_name
        row.kind = beacon.kind
        row.state = beacon.state
        row.idle_seconds = beacon.idle_seconds
        row.app = beacon.app
        row.capabilities = ",".join(sorted(beacon.capabilities)) or None
        row.last_seen_at = beacon.last_seen_at.replace(tzinfo=None)
        # The same beacon refreshes the device's push subscription: the
        # interrupt ladder only rings the phone first when it was seen in
        # the last 15 minutes, and that timestamp used to be written only
        # at subscribe time (2026-09-12).
        from sqlalchemy import update as _update
        from services.database import PushSubscriptionModel
        await session.execute(
            _update(PushSubscriptionModel)
            .where(PushSubscriptionModel.device_id == beacon.device_id, PushSubscriptionModel.is_active.is_(True))
            .values(last_seen_at=beacon.last_seen_at.replace(tzinfo=None))
        )
        await session.commit()


async def record_beacon(payload: Dict[str, Any], *, now: Optional[datetime] = None) -> Beacon:
    """Normalise, register in memory, then write through (best effort)."""
    global _attention_cache
    beacon = normalize_beacon(payload, now or _now())
    _registry[beacon.device_id] = beacon
    _attention_cache = (0.0, None)
    try:
        await _persist_beacon(beacon)
    except Exception as exc:  # noqa: BLE001 — memory copy is the live one
        print(f"[presence] persist failed for {beacon.device_id}: {exc}")
    return beacon


def list_beacons() -> list[Beacon]:
    return list(_registry.values())


def owner_at_mini(now: Optional[datetime] = None) -> bool:
    """The owner is at the host Mac itself: its own idle probe says the
    keyboard or mouse was touched in the last two minutes. In memory, no DB — the
    /screen snapshot asks every 4 s."""
    return at_device(list_beacons(), LOCAL_MAC_DEVICE_ID, now or _now())


# ---- derived signals -----------------------------------------------------------

async def collect_signals(*, now: Optional[datetime] = None) -> DerivedSignals:
    active_chats: frozenset = frozenset()
    try:
        from services.heartbeat.heartbeat_service import _active_chats
        active_chats = frozenset(_active_chats)
    except Exception:
        pass
    try:
        from services.display_signals import get_voice_state
        voice = get_voice_state()
    except Exception:
        voice = "idle"

    widget_last = push_last = None
    owner: Optional[tuple[str, datetime]] = None
    try:
        async with async_session() as session:
            widget_last = (
                await session.execute(
                    select(WidgetTokenModel.last_used_at)
                    .where(WidgetTokenModel.is_active.is_(True))
                    .order_by(WidgetTokenModel.last_used_at.desc().nullslast())
                    .limit(1)
                )
            ).scalar_one_or_none()
            push_last = (
                await session.execute(
                    select(PushSubscriptionModel.last_used_at)
                    .where(PushSubscriptionModel.is_active.is_(True))
                    .order_by(PushSubscriptionModel.last_used_at.desc().nullslast())
                    .limit(1)
                )
            ).scalar_one_or_none()
            row = (
                await session.execute(
                    select(HeartbeatEventModel.chat_identifier, HeartbeatEventModel.created_at)
                    .where(HeartbeatEventModel.is_from_user.is_(True))
                    .order_by(HeartbeatEventModel.created_at.desc())
                    .limit(1)
                )
            ).first()
            if row and row[0]:
                owner = (row[0], row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1])
    except Exception as exc:  # noqa: BLE001
        print(f"[presence] signal read failed: {exc}")

    def _utc(dt):
        return None if dt is None else (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt)

    return DerivedSignals(
        active_chat_ids=active_chats,
        voice_state=voice,
        widget_last_used_at=_utc(widget_last),
        push_last_used_at=_utc(push_last),
        last_owner_outbound=owner,
    )


async def get_attention_state(*, now: Optional[datetime] = None, fresh: bool = False) -> AttentionState:
    """Fold beacons + derived signals. Cached for a few seconds — the display
    polls at 2s and triage may ask several times per cycle."""
    global _attention_cache
    ts, cached = _attention_cache
    if cached is not None and not fresh and now is None and time.monotonic() - ts < _ATTENTION_CACHE_TTL:
        return cached
    state = compute_attention(list_beacons(), await collect_signals(now=now), now or _now())
    if now is None:
        _attention_cache = (time.monotonic(), state)
    return state


# ---- the host Mac's own probe --------------------------------------------------

def _read_hid_idle_output() -> str:
    return subprocess.run(
        ["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=5
    ).stdout


async def probe_local_mac_once(*, now: Optional[datetime] = None) -> Optional[Beacon]:
    try:
        out = await asyncio.to_thread(_read_hid_idle_output)
    except Exception as exc:  # noqa: BLE001
        print(f"[presence] ioreg probe failed: {exc}")
        return None
    idle = parse_hid_idle_seconds(out)
    if idle is None:
        return None
    payload = {
        "device_id": LOCAL_MAC_DEVICE_ID,
        "device_name": "This Mac (Edward)",
        "kind": "mac",
        "state": "active" if idle < ACTIVE_IDLE_MAX_SECONDS else "idle",
        "idle_seconds": idle,
    }
    return await record_beacon(payload, now=now)


async def _probe_loop() -> None:
    while True:
        try:
            await probe_local_mac_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[presence] probe loop error: {exc}")
        await asyncio.sleep(LOCAL_PROBE_INTERVAL_SECONDS)


async def start_local_mac_probe() -> None:
    global _probe_task
    if _probe_task is None:
        _probe_task = asyncio.create_task(_probe_loop())


async def stop_local_mac_probe() -> None:
    global _probe_task
    if _probe_task is not None:
        _probe_task.cancel()
        try:
            await _probe_task
        except asyncio.CancelledError:
            pass
        _probe_task = None
