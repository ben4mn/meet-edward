"""``screen`` — Edward's stage on this Mac's screen, the house screen and the
owner's widget.

Each action dispatches to today's tool body in ``services.graph.tools``:
``compose`` / ``say`` / ``show`` / ``clear`` / ``reset`` / ``bring_up`` →
``compose_screen`` … ``bring_up_screen``; ``widget`` / ``widget_code`` /
``widget_clear`` → ``update_widget`` … ``clear_widget_code``; ``state`` →
the widget's and the stage's state (AREA 1 puts it in the pulse pack too,
ending 2,689 ``get_widget_state_tool`` calls a month).

Carried over unchanged: the owner's hold on the screen (a refusal is now a
``Tool error``), quiet-hours and free-time silence on ``say aloud``, no
``web`` kind. Widget actions are refused while the ``ios_widget`` skill is
off. The description is AREA 1's to edit (text only); the dispatcher is
AREA 2's.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from services.owner import owner_doc
from services.speak_gate import quiet_hours_doc
from services.toolset import policy as policy_mod
from services.toolset.result import ToolFailure

SCREEN_ACTIONS = ("compose", "say", "show", "clear", "reset", "bring_up", "widget", "widget_code",
                  "widget_clear", "state")
WIDGET_ACTIONS = ("widget", "widget_code", "widget_clear")
# What changes something the owner (or the household) sees: a dry run records it.
CHANGING_ACTIONS = frozenset(SCREEN_ACTIONS) - {"state"}

_FAILURE_HEADS = ("Tool error:", "Not changed:", "Could not", "Not said:", "Failed to", "Websites are not drawn")


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _checked(reply: Any) -> str:
    text = str(reply if reply is not None else "")
    if text.strip().startswith(_FAILURE_HEADS):
        raise ToolFailure(text.strip().removeprefix("Tool error:").strip())
    return text


def _drop_none(args: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None}


async def _state() -> str:
    parts: Dict[str, Any] = {}
    try:
        from services.widget_service import get_widget_state

        parts["widget"] = await get_widget_state()
    except Exception as exc:  # noqa: BLE001
        parts["widget"] = f"unreadable ({type(exc).__name__})"
    try:
        from services import screen_service

        for name in ("get_state", "current_state", "state"):
            fn = getattr(screen_service, name, None)
            if callable(fn):
                value = fn()
                if hasattr(value, "__await__"):
                    value = await value
                parts["mini"] = value
                break
    except Exception as exc:  # noqa: BLE001
        parts["mini"] = f"unreadable ({type(exc).__name__})"
    return json.dumps(parts, default=str, ensure_ascii=False)[:12000]


async def screen_impl(action: str, pieces: Optional[List[Dict]] = None, me: Optional[Dict] = None,
                      why: Optional[str] = None, hold_minutes: Optional[int] = None, line: Optional[str] = None,
                      aloud: Optional[bool] = None, screen: Optional[str] = None, kind: Optional[str] = None,
                      body: Optional[str] = None, source: Optional[str] = None, title: Optional[str] = None,
                      seconds: Optional[int] = None, sections: Optional[List[Dict]] = None,
                      code: Optional[str] = None, subtitle: Optional[str] = None) -> str:
    act = (action or "").strip().lower()
    if act not in SCREEN_ACTIONS:
        raise ToolFailure(f"action must be one of {', '.join(SCREEN_ACTIONS)}")
    if act in WIDGET_ACTIONS:
        from services.toolset.skills import skill_enabled

        if not await skill_enabled("ios_widget"):
            raise ToolFailure("the widget is switched off in Settings (ios_widget)")
    if act == "state":
        return await _state()
    policy = policy_mod.current_policy()
    if policy.dry_run_outward and act in CHANGING_ACTIONS:
        return f"[dry run] Would have changed the screen ({act})."
    legacy = _legacy()
    if act == "compose":
        if not pieces:
            raise ToolFailure("compose needs pieces (a list; see the description)")
        return _checked(await legacy.compose_screen.ainvoke(
            {"pieces": pieces, "me": me, "why": why or "", "hold_minutes": int(hold_minutes or 0)}))
    if act == "say":
        if not (line or "").strip():
            raise ToolFailure("say needs line")
        return _checked(await legacy.say_on_screen.ainvoke({"line": line, "aloud": bool(aloud)}))
    if act == "show":
        if not (kind or "").strip():
            raise ToolFailure("show needs kind (alert|text|image|video|html)")
        return _checked(await legacy.show_on_screen.ainvoke(_drop_none(
            {"kind": kind, "body": body, "source": source, "title": title, "seconds": int(seconds or 0),
             "screen": screen or "house"})))
    if act == "clear":
        return _checked(await legacy.clear_screen.ainvoke({"screen": screen or "house"}))
    if act == "reset":
        return _checked(await legacy.reset_screen_layout.ainvoke({}))
    if act == "bring_up":
        return _checked(await legacy.bring_up_screen.ainvoke({}))
    if act == "widget":
        if not sections:
            raise ToolFailure("widget needs sections")
        return _checked(await legacy.update_widget.ainvoke(_drop_none(
            {"sections": sections, "title": title, "subtitle": subtitle})))
    if act == "widget_code":
        if not (code or "").strip():
            raise ToolFailure("widget_code needs code")
        return _checked(await legacy.update_widget_code.ainvoke({"code": code}))
    return _checked(await legacy.clear_widget_code.ainvoke({}))


@tool
@owner_doc
@quiet_hours_doc
async def screen(action: str, pieces: Optional[List[Dict]] = None, me: Optional[Dict] = None,
                 why: Optional[str] = None, hold_minutes: Optional[int] = None, line: Optional[str] = None,
                 aloud: Optional[bool] = None, screen: Optional[str] = None, kind: Optional[str] = None,
                 body: Optional[str] = None, source: Optional[str] = None, title: Optional[str] = None,
                 seconds: Optional[int] = None, sections: Optional[List[Dict]] = None, code: Optional[str] = None,
                 subtitle: Optional[str] = None) -> str:
    """Your surfaces: your stage on this Mac's screen (yours), the house screen (shared, if there is one), {OWNER}'s iPhone widget.

    action:
      compose — set your stage: pieces (≤ 8: {kind, id?, place, size, enter, title, …}; yours: words, note,
        list, stat, countdown, timer, image, video, html; live: clock, calendar, pets, alerts, heartbeat,
        recents, workers, vitals, memory_ticker), me ({"at": "floor"} | {"on": "<piece id>"} | corner/edge,
        "look"), why (one line, shown small), hold_minutes ({OWNER}'s turn only). Change is welcome.
      say — line (one or two sentences, beside you; aloud=true speaks it, never in quiet hours ({QUIET_HOURS}) or in free time);
      show — over a screen: screen house|mini, kind alert|text|image|video|html, body/source/title, seconds;
      clear — screen house|mini (mini is your stage); reset — the stock stage; bring_up — find or open the
        /screen tab in Chrome (needs Chrome hands on);
      widget — sections (header, text, list, stat, stats_row, progress, countdown, divider, spacer), title,
        subtitle; widget_code — Scriptable code; widget_clear; state — what the widget and stage show now.
    Websites go to {OWNER}'s Chrome (chrome open), not the screen. What {OWNER} put up holds until it lapses."""
    return await screen_impl(action=action, pieces=pieces, me=me, why=why, hold_minutes=hold_minutes, line=line,
                             aloud=aloud, screen=screen, kind=kind, body=body, source=source, title=title,
                             seconds=seconds, sections=sections, code=code, subtitle=subtitle)
