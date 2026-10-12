"""``ledger_events`` — one narrow table for what a health check wants to
see beyond turns.

Kinds (``kind`` / ``source`` / ``outcome``):

* ``decision`` / ``pulse`` / ``changed`` | ``left_alone`` | ``failed`` — what
  the expression pulse decided and why (its note), with the surfaces it
  touched. Heartbeat decisions live in ``triage_decisions``.
* ``mind`` / ``<reason>`` / ``acted`` | ``left_alone`` | ``failed`` |
  ``skipped`` — what a wake of the mind decided (its closing line), the
  tools it used and the surfaces it touched (mind rebuild Phase 2).
* ``rate_gate`` / ``<caller>`` / ``deferred`` — which background job the
  Codex rate gate turned away, and why.
* ``scheduled_event`` / ``scheduler`` / ``completed`` | ``failed`` —
  attempts, whether anything was delivered, the turn it ran as.
* ``browser`` / ``<action>`` / ``failed`` — where Edward's hands in Chrome
  failed and how (settle timeout, no tab, extension gone …).
* ``voice_session`` / ``<sensitivity>`` / ``ended`` — turns, bridges,
  barge-ins, interrupts, noise restarts, errors, duration.
* ``egress`` / ``<tool>`` / ``blocked`` — the secret scan refused an
  outbound argument; reason = the kinds found, never the secret (2026-09-24).
* ``taint`` / ``<tool>`` / ``would_gate`` — shadow mode: an unattended send
  from a tainted turn that ``EDWARD_TAINT_APPROVALS=on`` would have held.
* ``approval`` / ``<tool>`` / ``pending`` | ``approved`` | ``denied`` |
  ``expired`` — a held send and the owner's answer.
* ``surplus`` / ``<job>`` | ``controller`` / ``done`` | ``skipped`` |
  ``failed`` — the surplus queue (2026-09-24): outputs, the meter before and
  after, calls; ``controller`` rows say why it waited (at most hourly).
* ``forget`` / ``<subject kind>`` / ``applied`` — a verified forget, counts
  only, never the content.

Rows are a few hundred bytes; ``turn_ledger.prune`` keeps 30 days.
Writes are fire-and-forget and never raise into the caller.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MAX_DETAIL_CHARS = 600
MAX_REASON_CHARS = 300

SURFACE_TOOLS = {
    "update_widget": "widget",
    "update_widget_code": "widget",
    "clear_widget_code": "widget",
    "show_on_screen": "screen",
    "clear_screen": "screen",
    "compose_screen": "screen",
    "say_on_screen": "screen",
    "reset_screen_layout": "screen",
    # The toolset (2026-10-11): one ``screen`` tool for the stage, the house
    # screen and the widget. The ledger records names only, so any
    # successful call counts (``state`` reads are rare: the pulse pack
    # carries the state).
    "screen": "screen",
}
# ``tell_owner`` reaches the owner (a push, or a note on Today) — a fired reminder
# delivered through it was delivered.
OUTBOUND_TOOLS = {"send_message", "send_sms", "send_whatsapp", "send_imessage", "send_push_notification", "tell_owner"}

_CALLER_SKIP = (
    "services.llm_rate_gate", "services.codex_chat_model", "services.llm_utils",
    "services.event_ledger", "services.turn_ledger",
    # The pay-per-token path (2026-10-10): the Claude adapter and its ledger.
    "services.claude_chat_model", "services.llm_spend", "services.llm_pricing",
)


# ------------------------------------------------------------------ pure --

def _ok(tool: Dict[str, Any]) -> bool:
    return bool(tool.get("ok", True))


def surfaces_touched(tools: Iterable[Dict[str, Any]]) -> List[str]:
    """Which of Edward's surfaces a turn changed (successful calls only)."""
    seen = set()
    for t in tools or []:
        surface = SURFACE_TOOLS.get(str(t.get("n")))
        if surface and _ok(t):
            seen.add(surface)
    return sorted(seen)


def pulse_decision(note: Optional[str], tools: Iterable[Dict[str, Any]]) -> Tuple[str, str]:
    """``changed`` when a surface tool ran, else ``left_alone``; reason = the note."""
    outcome = "changed" if surfaces_touched(tools) else "left_alone"
    return outcome, " ".join(str(note or "").split())[:MAX_REASON_CHARS]


def delivered(tools: Iterable[Dict[str, Any]]) -> bool:
    """Did the turn get anything to the owner or anyone else (a successful send)?"""
    for t in tools or []:
        name = str(t.get("n") or "")
        # A send held for the owner's approval (taint gate, 2026-09-24) delivered nothing.
        if (name in OUTBOUND_TOOLS or name.startswith("whatsapp_send")) and _ok(t) and not t.get("held"):
            return True
    return False


def pick_caller(frames: Sequence[Tuple[str, str]]) -> str:
    """First frame that belongs to an Edward job, not LLM plumbing."""
    for module, func in frames:
        head = module.split(".", 1)[0]
        if head not in ("services", "routers", "scripts", "tests") and not head.startswith("test_"):
            continue
        if any(module.startswith(skip) for skip in _CALLER_SKIP):
            continue
        label = module[len("services."):] if module.startswith("services.") else module
        return f"{label}.{func}"[:60]
    return "unknown"


def caller_label(depth: int = 40) -> str:
    frames: List[Tuple[str, str]] = []
    try:
        frame = sys._getframe(1)
        while frame is not None and len(frames) < depth:
            frames.append((frame.f_globals.get("__name__", ""), frame.f_code.co_name))
            frame = frame.f_back
    except Exception:
        pass
    return pick_caller(frames)


def bounded_detail(detail: Optional[Dict[str, Any]]) -> Optional[str]:
    if not detail:
        return None
    try:
        text = json.dumps(detail, separators=(",", ":"), default=str)
        if len(text) > MAX_DETAIL_CHARS:
            trimmed = {k: (v[:120] if isinstance(v, str) else v) for k, v in detail.items()}
            text = json.dumps(trimmed, separators=(",", ":"), default=str)[:MAX_DETAIL_CHARS]
        return text
    except Exception:
        return None


def new_voice_stats() -> Dict[str, int]:
    return {"turns": 0, "bridges": 0, "barge_ins": 0, "interrupts": 0, "restarts": 0, "errors": 0}


# --------------------------------------------------------------- writes --

async def record_event_now(
    *, kind: str, source: str, outcome: str, reason: Optional[str] = None,
    ref_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    try:
        from services.database import async_session, LedgerEventModel

        row = LedgerEventModel(
            kind=str(kind)[:30], source=str(source or "unknown")[:60], outcome=str(outcome)[:20],
            reason=(" ".join(str(reason).split())[:MAX_REASON_CHARS] if reason else None),
            ref_id=(str(ref_id)[:80] if ref_id else None), detail_json=bounded_detail(detail),
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
    except Exception as exc:
        logger.debug("ledger event write failed (%s/%s): %s", kind, source, exc)
        return None


def record_event(**kwargs) -> None:
    """Fire-and-forget ``record_event_now``; never raises."""
    try:
        import asyncio

        asyncio.get_running_loop().create_task(record_event_now(**kwargs))
    except RuntimeError:
        pass  # no loop (tests, shutdown) — nothing to record on
    except Exception as exc:
        logger.debug("ledger event schedule failed: %s", exc)
