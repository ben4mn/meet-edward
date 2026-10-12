"""``plan`` — a visible checklist (TodoWrite-style; docs/architecture/tool-loop.md).

Each call sends the whole list. The harness diffs it against the stored
plan and emits the existing events (``plan_created``, ``plan_updated``,
``plan_step_update``, ``plan_completed``), so the frontend is unchanged;
the plan lives where it always did (``graph.tools._active_plans``, saved as
``checkpoint.plan_steps``).

It is not a gate: there are no plan nudges, and a plan does not raise the
round limit (the per-turn dollar cap is the real bound).

Statuses: ``pending`` | ``doing`` | ``done`` | ``dropped`` (a dropped step
leaves the visible checklist). The stored steps keep the frontend's names
(``pending`` / ``in_progress`` / ``completed``).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from services.owner import owner_doc
from services.toolset.result import ToolFailure

STATUSES = {"pending": "pending", "doing": "in_progress", "done": "completed", "dropped": None,
            # The frontend's names are accepted too.
            "in_progress": "in_progress", "completed": "completed"}
MAX_ITEMS = 40


class PlanItem(BaseModel):
    text: str = Field(description="the step, in a few words")
    status: str = Field(default="pending", description="pending | doing | done | dropped")
    note: Optional[str] = Field(default=None, description="what came of it, in a line (optional)")


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _as_dict(item) -> Dict:
    if isinstance(item, PlanItem):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        return {"text": item}
    raise ToolFailure("each item is {text, status, note?}")


def _summary(steps: List[Dict], dropped: int) -> str:
    done = sum(1 for s in steps if s["status"] == "completed")
    doing = [s["title"] for s in steps if s["status"] == "in_progress"]
    out = f"Plan: {done} of {len(steps)} done"
    if doing:
        out += f"; doing: {', '.join(doing)}"
    if dropped:
        out += f"; {dropped} dropped"
    return out + "."


async def plan_impl(items: List) -> str:
    legacy = _legacy()
    conversation_id = legacy.get_current_conversation_id()
    if not conversation_id:
        raise ToolFailure("no conversation to keep a plan in")
    raw = [_as_dict(i) for i in (items or [])]
    if not raw:
        raise ToolFailure("items is empty — send the whole checklist")
    if len(raw) > MAX_ITEMS:
        raise ToolFailure(f"at most {MAX_ITEMS} steps")

    old = legacy.get_active_plan(conversation_id) or []
    by_title = {s["title"]: s for s in old}
    used_ids = {s["id"] for s in old}
    next_n = max((int(str(s["id"]).split("-")[-1]) for s in old if str(s["id"]).split("-")[-1].isdigit()),
                 default=0)
    steps: List[Dict] = []
    dropped = 0
    for item in raw:
        title = " ".join(str(item.get("text") or "").split())
        if not title:
            raise ToolFailure("every item needs text")
        status_in = str(item.get("status") or "pending").strip().lower()
        if status_in not in STATUSES:
            raise ToolFailure(f"status must be pending, doing, done or dropped (got {status_in!r})")
        status = STATUSES[status_in]
        if status is None:
            dropped += 1
            continue
        prev = by_title.get(title)
        if prev is not None:
            step_id = prev["id"]
        else:
            next_n += 1
            step_id = f"step-{next_n}"
            while step_id in used_ids:
                next_n += 1
                step_id = f"step-{next_n}"
            used_ids.add(step_id)
        note = item.get("note")
        steps.append({"id": step_id, "title": title, "status": status,
                      "result": note if note not in (None, "") else (prev or {}).get("result")})

    if not steps:
        legacy._active_plans.pop(conversation_id, None)
        legacy._emit_plan_event(conversation_id, {"event_type": "plan_completed", "plan_steps": [],
                                                  "plan_summary": "All steps dropped."})
        return "Plan cleared: every step was dropped."

    if not old:
        legacy._active_plans[conversation_id] = steps
        legacy._emit_plan_event(conversation_id, {"event_type": "plan_created", "plan_steps": steps})
    elif [s["id"] for s in steps] != [s["id"] for s in old]:
        legacy._active_plans[conversation_id] = steps
        legacy._emit_plan_event(conversation_id, {"event_type": "plan_updated", "plan_steps": steps})
    else:
        before = {s["id"]: s for s in old}
        legacy._active_plans[conversation_id] = steps
        for s in steps:
            b = before[s["id"]]
            if b["status"] != s["status"] or b.get("result") != s.get("result"):
                legacy._emit_plan_event(conversation_id, {"event_type": "plan_step_update", "step_id": s["id"],
                                                          "step_status": s["status"], "step_result": s.get("result")})

    if all(s["status"] == "completed" for s in steps):
        legacy._emit_plan_event(conversation_id, {"event_type": "plan_completed", "plan_steps": steps,
                                                  "plan_summary": None})
        legacy._active_plans.pop(conversation_id, None)
        return f"Plan done: all {len(steps)} steps complete."
    return _summary(steps, dropped)


@tool
@owner_doc
async def plan(items: List[PlanItem]) -> str:
    """Your checklist for work with more than three steps; {OWNER} sees it on the work rail. Send the whole list each
    time: items = [{text, status: pending|doing|done|dropped, note?}]. Update it as you go; when an observation
    breaks a step, rewrite the plan."""
    return await plan_impl(items)
