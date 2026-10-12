"""The iPhone widget as Edward's expression space.

Why (2026-09-02): the Scriptable widget only ever showed the
auto-generated "next 3 events" and nothing refreshed it on Edward's
initiative. It is meant to be Edward's: whatever Edward has to say first,
then the house at a glance. Timed doses appear only while someone is on a
protocol; free-fed bowls are never "late".

Two halves:

* ``compose_widget_sections`` — pure. Turns the hub board (+ optional
  presence and a free-form "Edward's line") into the section types the
  fixed Scriptable renderer understands. Hub design rule 1 holds here too:
  window items (free-fed bowls) are never "late"; ``missed`` is shown but
  does not turn the accent amber; ``needs_review`` doses keep a "?".
* ``refresh_widget_expression`` — I/O. Reads the board, composes, and
  writes ``widget_state`` with ``updated_by="system"``. If Edward set
  custom content himself (``updated_by="edward"``, via the update_widget
  tool) it is left alone for ``CUSTOM_HOLD_HOURS`` — the composed default
  is a floor, not a ceiling.

Triggers: a care log on the hub (within seconds), the morning/evening
wake-up ticks, and a 30-minute cron event.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

CUSTOM_HOLD_HOURS = 6
MAX_BISCUIT_ROWS = 3  # rows in the doses list

AMBER = "#f5a524"
CALM_ACCENT = "#00d4aa"
THEME_BASE = {"backgroundColor": "#1a1a2e", "textColor": "#ffffff", "secondaryTextColor": "#a0a0a0"}


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def should_auto_refresh(*, updated_by: Optional[str], updated_at: Optional[datetime], now: datetime, force: bool = False) -> bool:
    """Leave Edward's own custom content alone for a while."""
    if force or updated_by != "edward" or updated_at is None:
        return True
    return _utc(now) - _utc(updated_at) >= timedelta(hours=CUSTOM_HOLD_HOURS)


def _clock(at_time: Optional[str]) -> str:
    """'18:00' → '6:00p' to match the hub's own clock style."""
    if not at_time:
        return ""
    try:
        h, m = (int(x) for x in at_time.split(":"))
    except ValueError:
        return at_time
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{suffix}"


def _med_row(med: Dict[str, Any]) -> Dict[str, Any]:
    name = med.get("name") or "dose"
    if med.get("needs_review"):
        name = f"{name} ?"
    status = med.get("status") or "due"
    if status == "given":
        who = med.get("by") or "someone"
        when = med.get("at_local") or ""
        return {"label": name, "detail": f"{who} · {when}".strip(" ·"), "icon": "✅"}
    if status == "overdue":
        return {"label": name, "detail": f"{med.get('minutes_late') or 0}m late", "icon": "⚠️"}
    if status == "missed":
        return {"label": name, "detail": "missed · check with vet before catching up", "icon": "⏭"}
    if status in ("refused", "skipped"):
        return {"label": name, "detail": status, "icon": "↩️"}
    when = _clock(med.get("at_time"))
    return {"label": name, "detail": f"due {when}" if when else "due", "icon": "⏰"}


def _rank(med: Dict[str, Any]) -> int:
    return {"overdue": 0, "due": 1, "missed": 2, "refused": 3, "skipped": 3, "given": 4}.get(med.get("status") or "due", 5)


def _bowls(pet: Dict[str, Any]) -> str:
    f = pet.get("feedings") or {}
    def mark(w):
        return "✓" if (f.get(w) or {}).get("done") else "–"
    return f"AM {mark('am')} · PM {mark('pm')}"


def compose_widget_sections(
    *,
    board: Dict[str, Any],
    attention: Optional[Any],
    now: datetime,
    edwards_line: Optional[str] = None,
    calendar_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    pets = board.get("pets") or []
    sections: List[Dict[str, Any]] = []

    if edwards_line:
        sections.append({"type": "text", "role": "edward", "content": edwards_line.strip()[:200]})

    # Timed doses across all pets — only while someone is on a protocol.
    dosed = [(p, m) for p in pets for m in (p.get("meds") or [])]
    anything_late = any(m.get("status") == "overdue" for _, m in dosed)
    if dosed:
        dosed.sort(key=lambda pm: _rank(pm[1]))
        rows = []
        for pet, med in dosed[:MAX_BISCUIT_ROWS]:
            row = _med_row(med)
            row["label"] = f"{pet['name']} · {row['label']}"
            rows.append(row)
        sections.append({"type": "header", "role": "doses-header", "title": "Doses", "icon": "heart"})
        sections.append({"type": "list", "role": "doses", "items": rows})

    if pets:
        sections.append({
            "type": "stats_row", "role": "bowls",
            "stats": [{"label": p["name"], "value": _bowls(p)} for p in pets[:4]],
        })

    for line in (calendar_lines or [])[:2]:
        sections.append({"type": "text", "role": "calendar", "content": line[:120]})

    local = now.astimezone() if now.tzinfo else now
    subtitle = f"{'Something is late' if anything_late else 'All quiet'} · {local.strftime('%-I:%M%p').lower()}"
    if attention is not None and getattr(attention, "owner_present", False) and getattr(attention, "active_surface", None):
        subtitle += f" · you're at {attention.active_surface.split(':')[0]}"

    theme = dict(THEME_BASE, accentColor=AMBER if anything_late else CALM_ACCENT)
    return {"title": "Edward", "subtitle": subtitle, "sections": sections[:7], "theme": theme}


# ---- I/O ------------------------------------------------------------------------

async def refresh_widget_expression(reason: str, *, force: bool = False, edwards_line: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Recompose and write the widget unless Edward's own content is fresh."""
    try:
        from sqlalchemy import select
        from services.database import WidgetStateModel, async_session
        from services import hub_service

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            state = (await session.execute(select(WidgetStateModel).where(WidgetStateModel.id == "default"))).scalar_one_or_none()
            # A raw Scriptable script is custom content like any other: it is
            # honoured for CUSTOM_HOLD_HOURS and then replaced. (A script from
            # Aug 23 silently blocked every refresh for twelve days.)
            custom_by = "edward" if (state is not None and (state.script or state.updated_by == "edward")) else (state.updated_by if state else None)
            if state is not None and not should_auto_refresh(updated_by=custom_by, updated_at=state.updated_at, now=now, force=force):
                what = "script" if state.script else "content"
                print(f"[widget] skip ({reason}): Edward's own {what} from {state.updated_at} is still fresh")
                return None

        board = await hub_service.get_board()
        attention = None
        try:
            from services.presence_service import get_attention_state
            attention = await get_attention_state()
        except Exception:
            attention = None
        composed = compose_widget_sections(board=board, attention=attention, now=now, edwards_line=edwards_line)

        async with async_session() as session:
            state = (await session.execute(select(WidgetStateModel).where(WidgetStateModel.id == "default"))).scalar_one_or_none()
            if state is None:
                state = WidgetStateModel(id="default")
                session.add(state)
            if state.script:
                print(f"[widget] replacing stale script from {state.updated_at}")
            state.script = None
            state.title = composed["title"]
            state.subtitle = composed["subtitle"]
            state.sections = json.dumps(composed["sections"])
            state.theme = json.dumps(composed["theme"])
            state.updated_by = "system"
            await session.commit()
        print(f"[widget] refreshed ({reason}): {composed['subtitle']}")
        return composed
    except Exception as exc:  # noqa: BLE001 — never break the caller
        print(f"[widget] refresh failed ({reason}): {exc}")
        return None


def refresh_in_background(reason: str) -> None:
    """Fire-and-forget for request handlers and ticks."""
    try:
        asyncio.get_running_loop().create_task(refresh_widget_expression(reason))
    except RuntimeError:
        pass
