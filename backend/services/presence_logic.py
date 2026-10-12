"""Presence: is the owner here, and which screen are they looking at? (pure logic)

Why (2026-09-02): nothing in Edward knew whether the owner was at a
keyboard, so triage acted on a text within a minute while they were typing
their own reply (46% of actions were preempted by the owner's reply).
Presence is a *fact* fed into Edward's judgment — never a cap. Two
consumers:

* settle-before-act: the settle window stretches when the owner is
  present, and more when they are already in that iMessage thread;
* the interrupt ladder: an "Edward wants a word" lands on the surface they
  are actually looking at (menu-bar → visible web tab → display → phone push).

Beacons come from many places (the host Mac's own idle probe, the web app,
/display, /hub, a menu-bar app, a 20-line script on any other Mac) and are
all normalised into ``Beacon``. ``DerivedSignals`` are things Edward already
knows without a beacon (an open chat stream, a live voice session, the
owner's last outbound iMessage). ``compute_attention`` folds both into one
``AttentionState`` with an explicit ``reason`` so the decision trail can
say *why* Edward thought the owner was present.

Design rules:
* The display kiosk is always "active" — it means the screen is lit, not
  that the owner is there. It never marks them present on its own.
* All time is injected (``now``) so every rule is testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple

from services.owner import owner_name

KINDS = ("mac", "menubar", "mac-web", "iphone-pwa", "display", "pi-hub", "widget")
STATES = ("active", "idle", "locked", "hidden")

BEACON_TTL_SECONDS = 90          # 3x the 30s beacon cadence
ACTIVE_IDLE_MAX_SECONDS = 120    # past this an "active" beacon is really idle
THREAD_WINDOW_SECONDS = 300      # the owner's outbound iMessage this recent = they are in that thread
PHONE_RECENT_SECONDS = 900       # a phone seen this recently gets targeted pushes

# Higher is better for landing an interrupt. Display/hub/widget are screens
# without a person attached; they trail the personal devices.
SURFACE_PRIORITY = ("menubar", "mac", "mac-web", "iphone-pwa", "display", "pi-hub", "widget")
PERSONAL_KINDS = frozenset({"mac", "menubar", "mac-web", "iphone-pwa"})

_HID_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


@dataclass(frozen=True)
class Beacon:
    device_id: str
    device_name: str
    kind: str
    state: str
    idle_seconds: int
    last_seen_at: datetime
    app: Optional[str] = None
    capabilities: frozenset = frozenset()

    def surface(self) -> str:
        return f"{self.kind}:{self.device_id}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "kind": self.kind,
            "state": self.state,
            "idle_seconds": self.idle_seconds,
            "last_seen_at": self.last_seen_at.isoformat(),
            "app": self.app,
            "capabilities": sorted(self.capabilities),
        }


@dataclass(frozen=True)
class DerivedSignals:
    active_chat_ids: frozenset = frozenset()
    voice_state: str = "idle"
    widget_last_used_at: Optional[datetime] = None
    push_last_used_at: Optional[datetime] = None
    last_owner_outbound: Optional[Tuple[str, datetime]] = None   # (chat_identifier, at)


@dataclass(frozen=True)
class AttentionState:
    owner_present: bool
    active_surface: Optional[str]
    best_surface_for_interrupt: str
    seconds_since_activity: Optional[int]
    in_imessage_thread: Optional[str]
    fresh_beacons: Tuple[Beacon, ...]
    reason: str
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "owner_present": self.owner_present,
            "active_surface": self.active_surface,
            "best_surface_for_interrupt": self.best_surface_for_interrupt,
            "seconds_since_activity": self.seconds_since_activity,
            "in_imessage_thread": self.in_imessage_thread,
            "fresh_beacons": [b.as_dict() for b in self.fresh_beacons],
            "reason": self.reason,
            "computed_at": self.computed_at.isoformat(),
        }


# ---- parsing / normalising ------------------------------------------------

def parse_hid_idle_seconds(ioreg_output: str) -> Optional[int]:
    """``ioreg -c IOHIDSystem`` reports ``"HIDIdleTime" = <nanoseconds>``."""
    m = _HID_IDLE_RE.search(ioreg_output or "")
    if not m:
        return None
    return int(int(m.group(1)) / 1_000_000_000)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_beacon(payload: Dict[str, Any], now: datetime) -> Beacon:
    """Validate an inbound beacon payload. Raises ``ValueError`` on bad input."""
    device_id = str(payload.get("device_id") or "").strip()[:120]
    if not device_id:
        raise ValueError("device_id is required")
    kind = str(payload.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    state = str(payload.get("state") or "active").strip()
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}; expected one of {STATES}")
    try:
        idle = max(0, int(payload.get("idle_seconds") or 0))
    except (TypeError, ValueError):
        raise ValueError("idle_seconds must be an integer")
    caps = payload.get("capabilities") or []
    if not isinstance(caps, (list, tuple, set, frozenset)):
        raise ValueError("capabilities must be a list")
    app = payload.get("app")
    return Beacon(
        device_id=device_id,
        device_name=str(payload.get("device_name") or device_id)[:120],
        kind=kind,
        state=state,
        idle_seconds=idle,
        last_seen_at=_as_utc(now),
        app=str(app)[:200] if app else None,
        capabilities=frozenset(str(c)[:40] for c in caps),
    )


# ---- freshness --------------------------------------------------------------

def _age(beacon: Beacon, now: datetime) -> float:
    return (_as_utc(now) - _as_utc(beacon.last_seen_at)).total_seconds()


def is_fresh(beacon: Beacon, now: datetime, ttl: int = BEACON_TTL_SECONDS) -> bool:
    return _age(beacon, now) <= ttl


def is_active(beacon: Beacon, now: datetime) -> bool:
    return is_fresh(beacon, now) and beacon.state == "active" and beacon.idle_seconds < ACTIVE_IDLE_MAX_SECONDS


def at_device(beacons: Sequence[Beacon], device_id: str, now: datetime) -> bool:
    """Is the owner at this one device right now — its own beacon active
    (fresh and recently touched)? For the host Mac's /screen (2026-09-28):
    that Mac's own probe, not whichever surface has the lowest idle (a phone
    in their hand would win that). A ``display`` beacon never counts: a lit kiosk is not
    a person."""
    return any(b.device_id == device_id and b.kind != "display" and is_active(b, now) for b in beacons)


def _priority(beacon: Beacon) -> int:
    return SURFACE_PRIORITY.index(beacon.kind) if beacon.kind in SURFACE_PRIORITY else len(SURFACE_PRIORITY)


# ---- the fold ---------------------------------------------------------------

def compute_attention(beacons: Sequence[Beacon], signals: DerivedSignals, now: datetime) -> AttentionState:
    now = _as_utc(now)
    fresh = tuple(sorted((b for b in beacons if is_fresh(b, now)), key=_priority))
    active_personal = [b for b in fresh if is_active(b, now) and b.kind in PERSONAL_KINDS]
    active_personal.sort(key=lambda b: (b.idle_seconds, _priority(b)))

    reasons = []
    active_surface = active_personal[0].surface() if active_personal else None
    if active_surface:
        reasons.append(f"active {active_surface} (idle {active_personal[0].idle_seconds}s)")

    in_thread = None
    owner_age: Optional[int] = None
    if signals.last_owner_outbound:
        chat_id, at = signals.last_owner_outbound
        owner_age = int((now - _as_utc(at)).total_seconds())
        if 0 <= owner_age <= THREAD_WINDOW_SECONDS:
            in_thread = chat_id
            reasons.append(f"{owner_name()} replied in {chat_id} {owner_age}s ago")

    if signals.active_chat_ids:
        reasons.append(f"chat stream open ({len(signals.active_chat_ids)})")
    if signals.voice_state != "idle":
        reasons.append(f"voice {signals.voice_state}")

    present = bool(active_surface or in_thread or signals.active_chat_ids or signals.voice_state != "idle")

    # Interrupt ladder: best *fresh* personal screen, else the display, else a
    # phone seen recently, else broadcast.
    best: Optional[str] = None
    for kind in ("menubar", "mac", "mac-web"):
        cands = [b for b in fresh if b.kind == kind and is_active(b, now)]
        if cands:
            best = cands[0].surface()
            break
    if best is None:
        disp = [b for b in fresh if b.kind == "display"]
        if disp:
            best = disp[0].surface()
    if best is None:
        phones = [b for b in beacons if b.kind == "iphone-pwa" and _age(b, now) <= PHONE_RECENT_SECONDS]
        best = f"push:{phones[0].device_id}" if phones else "push:all"

    candidates = [b.idle_seconds for b in active_personal]
    if owner_age is not None and owner_age >= 0:
        candidates.append(owner_age)
    if signals.widget_last_used_at:
        candidates.append(int((now - _as_utc(signals.widget_last_used_at)).total_seconds()))
    since = min(candidates) if candidates else None

    if not reasons:
        reasons.append("no fresh personal beacon and no owner activity")

    return AttentionState(
        owner_present=present,
        active_surface=active_surface,
        best_surface_for_interrupt=best,
        seconds_since_activity=since,
        in_imessage_thread=in_thread,
        fresh_beacons=fresh,
        reason="; ".join(reasons),
        computed_at=now,
    )
