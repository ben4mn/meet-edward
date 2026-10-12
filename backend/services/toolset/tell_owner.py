"""``tell_owner`` — reach the owner (docs/architecture/push-and-notes.md;
``reach_service`` owns the semantics).

It calls ``reach_service.tell_owner(message, title, urgent, link)`` when
that exists: the reach service decides note vs push vs an "I noticed"
bubble, quiet hours, one follow-up then stop, and the high-risk gate (there
is no push count unless ``EDWARD_PUSH_BUDGET`` sets one: by design,
2026-10-11, pushes are judgment, not a quota); it returns a success
sentence (a downgrade to a note is a success, with the reason) or
``Tool error: …``, and raises only on a transport failure.

Without it, the fallback is the older paths: ``urgent`` → a plain push
(``send_push_notification`` with ``push_safety_gate``; never the
interrupt ladder, which could end in a text to the owner), otherwise a note
on Today (``leave_note``).

``urgent`` needs ``TurnPolicy.may_push``. The description names no push
count: no cap exists on this path, so it says only what is judgment (quiet
hours, no repeats).
"""

from __future__ import annotations

import importlib
from typing import Any, Optional

from langchain_core.tools import tool

from services.owner import owner_doc, owner_name
from services.speak_gate import quiet_hours_doc
from services.toolset import policy as policy_mod
from services.toolset.result import ToolFailure

TITLE_MAX = 60
_PUSH_FAILURES = ("Push notifications not configured", "Push notification failed", "No active push subscriptions",
                  "Push notification error", "Tool error:")


def _reach_service():
    try:
        return importlib.import_module("services.reach_service")
    except ImportError:
        return None


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _title_from(message: str) -> str:
    first = message.strip().split("\n", 1)[0]
    words = first.split()
    title = " ".join(words[:8])
    return title[:TITLE_MAX] or "Edward"


async def _push_skill_on() -> bool:
    from services.toolset.skills import skill_enabled

    return await skill_enabled("push_notifications")


async def tell_owner_impl(message: str, title: Optional[str] = None, urgent: bool = False,
                        link: Optional[str] = None) -> str:
    text = (message or "").strip()
    if not text:
        raise ToolFailure("the message is empty")
    policy = policy_mod.current_policy()
    urgent = bool(urgent)
    owner = owner_name()
    if urgent and not policy.may_push:
        raise ToolFailure(f"this turn can't push {owner} — leave it as a note (urgent=false), and it waits on Today")
    if urgent and not await _push_skill_on():
        raise ToolFailure("push notifications are switched off in Settings — leave it as a note (urgent=false)")
    if policy.dry_run_outward:
        kind = "push" if urgent else "note"
        return f"[dry run] Would have told {owner} ({kind}): {(title + ' — ') if title else ''}{text[:300]}"

    reach = _reach_service()
    if reach is not None and hasattr(reach, "tell_owner"):
        try:
            reply: Any = await reach.tell_owner(text, title, urgent, link)
        except ToolFailure:
            raise
        except Exception as exc:  # noqa: BLE001 — a transport failure
            raise ToolFailure(f"couldn't reach {owner} ({type(exc).__name__}: {exc})") from exc
        out = str(reply if reply is not None else "")
        if out.strip().startswith("Tool error:"):
            raise ToolFailure(out.strip())
        return out or (f"Pushed {owner}." if urgent else f"Left {owner} a note on Today.")

    legacy = _legacy()
    head = (title or "").strip() or _title_from(text)
    if urgent:
        reply = str(await legacy.send_push_notification.ainvoke(
            {"title": head, "body": text, "url": link, "urgency": "normal"}))
        if reply.strip().startswith(_PUSH_FAILURES):
            raise ToolFailure(reply.strip().removeprefix("Tool error:").strip())
        return f"Pushed {owner}: {head} ({reply.strip()})"
    reply = str(await legacy.leave_note.ainvoke({"title": head, "body": text, "needs_you": False, "url": link}))
    if not reply.strip().startswith("ok"):
        raise ToolFailure(reply.strip().removeprefix("Tool error:").strip() or "the note could not be written")
    return f"Left {owner} a note on Today: {head}"


@tool
@owner_doc
@quiet_hours_doc
async def tell_owner(message: str, title: Optional[str] = None, urgent: bool = False, link: Optional[str] = None) -> str:
    """Reach {OWNER}: a note on Today, or urgent=true for a push when it can't wait. In quiet hours ({QUIET_HOURS})
    push only what truly can't wait — a care alert, travel closing within hours — and never repeat a push they already
    have. Shape: what you saw and where (whose message, when) → what it means → what you prepared ("not sent") → one
    question. Never text {OWNER} from your own turns; this is how you reach them. link = where a tap should go (e.g.
    /chat?c=<id>)."""
    return await tell_owner_impl(message=message, title=title, urgent=urgent, link=link)
