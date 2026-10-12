"""The late-dose alert, in code (proactivity rebuild, 2026-10-11).

The judgement check's ``late_dose`` scenario used to ask a model, in an
evening wake, to notice that Biscuit's pill had not been logged. A missed dose
is real harm, so this does not ride an LLM, ``EDWARD_PROACTIVITY`` or the
spend cap: the ``*/5`` ``proactive_sweep`` calls ``check()``, which reads
the hub's pet board and pushes the owner once when a timed dose is still unlogged
45 minutes after it was due.

The hub's own rules stand (``hub_pet_service``):

* only ``timed`` items go overdue — a ``window`` item (the cats' wet food,
  "twice daily, whenever") never alerts;
* a dose more than 240 min late is ``missed`` and stops alerting (still
  recorded on the hub) — an alert nobody can act on teaches people to
  ignore the colour.

One alert per pet and slot, not per drug: Biscuit's three 6:00 pm items are
one push ("Carprofen, Probiotic and Melatonin aren't logged"). Someone
who touched the hub in the last 15 minutes is standing at the station and
sees its own alert, so the owner is not pushed then. Each alert is a keyed
``care_alert`` ledger row (``care:<pet>:<care day>:<HH:MM>``), so a restart
or the next sweep never re-alerts once a push landed. A push that did not
land — the transport failed, or reached no device — is not a push:
``reach_service`` leaves the owner a note instead (``downgraded``), and the next
sweep tries the push again, without another note, until it lands or the
dose is logged or missed (review 2026-10-11: "Left it as a note" used to
count as pushed, so the alert lived only on Today and was never retried).

Delivery is ``reach_service.tell_owner(..., urgent=True, origin="care")`` —
exempt from the push budget and from quiet hours (06:45 is a real dose).
Until ``reach_service`` is merged the push goes straight to
``push_service`` under ``source="reach:care"``. ``EDWARD_CARE_WATCH=off``
is its own kill switch.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.owner import owner_text

CARE_LATE_MINUTES = 45
HUB_TOUCH_MINUTES = 15
ALERT_LINK = "/hub/pets"
LEDGER_KIND = "care_alert"
LEDGER_SOURCE = "care_watch"
# A care day is ~24 h; this window comfortably covers it for the keys.
_ALERTED_LOOKBACK = timedelta(hours=36)

_last_check: Dict[str, Any] = {"at": None, "result": None}


@dataclass(frozen=True)
class CareAlert:
    key: str
    pet: str
    at_time: str
    items: Tuple[str, ...]
    item_ids: Tuple[str, ...]
    minutes_late: int
    needs_review: bool
    title: str
    text: str
    link: str = ALERT_LINK


def enabled() -> bool:
    """``EDWARD_CARE_WATCH`` (read per call): anything but ``off`` is on."""
    return (os.environ.get("EDWARD_CARE_WATCH") or "on").strip().lower() != "off"


# ------------------------------------------------------------------ pure --

def clock_label(at_time: str) -> str:
    """``"18:00"`` → ``"6:00 pm"`` (the one clock format). Pure."""
    try:
        hh, mm = (int(x) for x in str(at_time).split(":")[:2])
    except (TypeError, ValueError):
        return str(at_time)
    return f"{hh % 12 or 12}:{mm:02d} {'am' if hh < 12 else 'pm'}"


def _names(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def alert_key(pet_id: str, care_day: str, at_time: str) -> str:
    return f"care:{pet_id}:{care_day}:{at_time}"


def _alert(pet: Dict[str, Any], care_day: str, at_time: str, meds: List[Dict[str, Any]]) -> CareAlert:
    name = str(pet.get("name") or "A pet")
    names = tuple(str(m.get("name") or "a dose") for m in meds)
    late = max(int(m.get("minutes_late") or 0) for m in meds)
    review = any(m.get("needs_review") for m in meds)
    when = clock_label(at_time)
    many = len(names) > 1
    text = (f"{name}'s {when} {_names(names)} {'aren' if many else 'isn'}'t logged — {late} min late. "
            f"Given? Log {'them' if many else 'it'} on the hub.")
    if review:
        text += " (schedule unconfirmed)"
    return CareAlert(
        key=alert_key(str(pet.get("id") or name.lower()), care_day, at_time),
        pet=name, at_time=at_time, items=names, item_ids=tuple(str(m.get("id")) for m in meds),
        minutes_late=late, needs_review=review,
        title=f"{name}'s {when} dose{'s' if many else ''}", text=text,
    )


def due_alerts(board: Dict[str, Any], now: datetime, already_alerted: Iterable[str]) -> List[CareAlert]:
    """The alerts the board calls for now: per pet and slot, every timed
    item that is ``overdue`` (never ``missed``, never a window item, never
    logged) and at least ``CARE_LATE_MINUTES`` late, unless that slot's key
    was already alerted. Pure (``now`` is for the caller's record)."""
    done: Set[str] = set(already_alerted or ())
    care_day = str((board or {}).get("care_day") or "")
    out: List[CareAlert] = []
    for pet in (board or {}).get("pets") or []:
        slots: Dict[str, List[Dict[str, Any]]] = {}
        for med in pet.get("meds") or []:
            if med.get("kind") == "feeding" or med.get("status") != "overdue" or not med.get("at_time"):
                continue
            if int(med.get("minutes_late") or 0) < CARE_LATE_MINUTES:
                continue
            slots.setdefault(str(med["at_time"]), []).append(med)
        for at_time in sorted(slots):
            alert = _alert(pet, care_day, at_time, slots[at_time])
            if alert.key not in done:
                out.append(alert)
    return out


def hub_touched_within(person: Optional[Dict[str, Any]], now: datetime, minutes: int = HUB_TOUCH_MINUTES) -> bool:
    """Did someone act at the hub in the last ``minutes``? Pure."""
    raw = (person or {}).get("last_seen")
    if not (person or {}).get("person") or not raw:
        return False
    try:
        seen = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return timedelta(0) <= now - seen <= timedelta(minutes=minutes) or seen > now


# ------------------------------------------------------------------- I/O --

async def _keys_with(now: datetime, outcome: str) -> Set[str]:
    """Keys of ``care_alert`` rows with this outcome in the last 36 h."""
    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session

    since = (now.astimezone(timezone.utc) - _ALERTED_LOOKBACK).replace(tzinfo=None)
    async with async_session() as s:
        rows = (await s.execute(
            select(LedgerEventModel.ref_id).where(
                LedgerEventModel.kind == LEDGER_KIND, LedgerEventModel.outcome == outcome,
                LedgerEventModel.created_at >= since)
        )).scalars().all()
    return {r for r in rows if r}


async def _alerted_keys(now: datetime) -> Set[str]:
    """Keys of alerts whose push landed (never alerted again)."""
    return await _keys_with(now, "pushed")


async def _noted_keys(now: datetime) -> Set[str]:
    """Keys the owner already has a note for (a push that did not land)."""
    return await _keys_with(now, "downgraded")


# Built from the same text as ``reach_service.PUSHED`` (what ``tell_owner``
# answers when a push landed), so the two always match.
PUSHED_PREFIX = owner_text("Pushed to {OWNER}'s phone")
NOT_DELIVERED_PREFIX = "Not delivered"


def delivery_outcome(said: Any) -> str:
    """What ``tell_owner``'s answer means for a care alert: ``pushed`` only
    when a push landed; ``refused`` for a ``Tool error``; ``failed`` when
    nothing was delivered; otherwise ``downgraded`` — the owner got a note, the
    push will be tried again. Pure."""
    text = str(said or "")
    if text.startswith(PUSHED_PREFIX):
        return "pushed"
    if text.startswith("Tool error"):
        return "refused"
    if text.startswith(NOT_DELIVERED_PREFIX):
        return "failed"
    return "downgraded"


async def _deliver(alert: CareAlert, *, note_fallback: bool = True) -> str:
    """``reach_service.tell_owner`` (care is exempt from the budget and quiet
    hours there, and deduped here, not there); straight to ``push_service``
    until it is merged. ``note_fallback=False`` once the owner has a note for
    this dose: a retry tries the push, never a second note."""
    try:
        from services import reach_service
    except ImportError:
        reach_service = None
    if reach_service is not None:
        kw = {} if note_fallback else {"note_fallback": False}
        return await reach_service.tell_owner(alert.text, title=alert.title, urgent=True, link=alert.link,
                                            origin="care", dedupe_key=alert.key, **kw)
    from services import push_service

    result = await push_service.send_push_notification(
        title=alert.title, body=alert.text, url=alert.link, tag=alert.key, source="reach:care",
        respect_quiet_hours=False,
    )
    if not result.get("sent"):
        raise RuntimeError(str(result.get("error") or "the push reached no device")[:200])
    return PUSHED_PREFIX


async def _note_noticing(alert: CareAlert, now: datetime) -> None:
    """The care alert as a ``noticings`` row when ``noticing_service`` is
    merged (``origin='care'``, spoken by push) so the proactivity report
    counts it; never fails the alert."""
    try:
        from services import noticing_service
    except ImportError:
        return
    add = getattr(noticing_service, "add_noticing", None)
    if add is None:
        return
    try:
        await add(origin="care", kind="household", category="household", claim=alert.text, sources=[],
                  dedupe_key=alert.key, status="spoken", channel="push", surfaced_at=now)
    except Exception as exc:  # noqa: BLE001 — the ledger row is the key that counts
        print(f"[care] could not record a noticing for {alert.key}: {type(exc).__name__}: {exc}")


async def _record(alert: CareAlert, outcome: str, reason: Optional[str] = None) -> None:
    from services import event_ledger

    await event_ledger.record_event_now(
        kind=LEDGER_KIND, source=LEDGER_SOURCE, outcome=outcome, ref_id=alert.key, reason=reason,
        detail={"pet": alert.pet, "at": alert.at_time, "items": list(alert.items),
                "minutes_late": alert.minutes_late, "needs_review": alert.needs_review},
    )


# ADAPT: the late-dose alert watches a household's pets through the hub's pet board.
#   How it works here: timed meds on the hub board (``hub_service.get_board``, read in
#   ``hub_pet_service.HOUSEHOLD_TZ``) push the owner once per pet and slot 45 min after they were due.
#   To adapt: enter your pets' timed doses on the hub, or set EDWARD_CARE_WATCH=off if there are none.
async def check(now: Optional[datetime] = None) -> Dict[str, Any]:
    """One pass: read the board in the household's zone, push what is due.
    Raises only when the board or the ledger cannot be read (the sweep
    records that and runs again in five minutes)."""
    if not enabled():
        return {"enabled": False}
    from services import hub_service
    from services.hub_pet_service import HOUSEHOLD_TZ

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(HOUSEHOLD_TZ)
    board = await hub_service.get_board(local)
    result: Dict[str, Any] = {"alerts": 0, "failed": 0}
    candidates = due_alerts(board, local, ())
    result["due"] = len(candidates)
    if candidates:
        if hub_touched_within(await hub_service.get_hub_person(now), now):
            result["skipped"] = "someone is at the hub"
            candidates = []
        else:
            done = await _alerted_keys(now)
            candidates = [a for a in candidates if a.key not in done]
    noted = await _noted_keys(now) if candidates else set()
    for alert in candidates:
        try:
            said = await _deliver(alert, note_fallback=alert.key not in noted)
        except Exception as exc:  # noqa: BLE001 — tried again next sweep
            result["failed"] += 1
            print(f"[care] alert {alert.key} failed: {type(exc).__name__}: {exc}")
            await _record(alert, "failed", reason=f"{type(exc).__name__}: {exc}")
            continue
        outcome = delivery_outcome(said)
        if outcome != "pushed":
            # Not a push: kept out of the alerted keys, so the next sweep
            # tries the push again (with no second note).
            result["failed"] += 1
            print(f"[care] alert {alert.key} {outcome}: {said}")
            await _record(alert, outcome, reason=str(said))
            continue
        result["alerts"] += 1
        print(f"[care] pushed {alert.key}: {alert.title}")
        await _record(alert, "pushed")
        await _note_noticing(alert, now)
    _last_check.update(at=now.astimezone(timezone.utc).isoformat(), result=dict(result))
    return result


def last_check() -> Dict[str, Any]:
    """When this process last ran ``check`` and what it found."""
    return dict(_last_check)
