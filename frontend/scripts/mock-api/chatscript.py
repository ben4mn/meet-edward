"""Scripted chat turns for the mock ``POST /api/chat`` stream.

A turn is a list of steps. Each step is ``(delay_seconds, event)`` where
``event`` is a dict shaped exactly like ``services.graph.streaming.create_event``
(``{"type", "conversation_id", ...}``), or one of the control markers:

* ``("end",)`` — end the stream cleanly *without* ``done`` (after an
  ``error`` event the frontend ignores): the frontend treats the turn as
  interrupted and reloads the thread when the tab becomes visible again.
* ``("abort",)`` — cut the TCP connection mid-stream. Through Next's
  ``/api`` rewrite proxy the browser is never told: the stream just hangs
  with the stop button showing — what Ellis sees if the backend dies mid-turn.

The sequence mirrors the real tool loop in ``stream_with_memory_events``:
memory search + dream progress, reasoning deltas, tool-args deltas, then
per tool ``progress tool_execution started`` → ``tool_start`` (with
``args_summary``) → [code / execution_output / execution_result for the
execution tools, search progress for web tools] → ``tool_end`` →
``progress tool_execution completed``; ``thinking`` before each later
iteration; answer text streamed word by word; ``progress generating``;
``done``.

Keywords in the message pick the script (case-insensitive):

  quick     no tools, one short answer
  code      execute_code with code, stdout and a result (also: python, calculate)
  plan      create_plan / update_plan_step / complete_plan with plan events
  worker    spawn_cc_worker streamed inline (cc_session_* events)
  remember  a remember_update tool call
  slow      every delay ×3 (good for mid-stream screenshots)
  long      a long markdown answer (headings, table, code, quote)
  exhaust   ran out of tool iterations, then a summary
  explain   a tool fails and Edward explains what broke (N7)
  error     an "error" event mid-answer, then the stream ends without "done"
  hangup    the backend's connection is cut mid-answer (hangs behind the Next proxy)
  fail      HTTP 500 before any event (the canned error)
  refuse    HTTP 409 with a sentence meant for Ellis
  anything else: remember_search + web_search + list_scheduled_events
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

TOKEN_DELAY = 0.025
Step = Tuple[Any, ...]


def ev(conv: str, type_: str, **kw) -> Dict[str, Any]:
    return {"type": type_, "conversation_id": conv, **kw}


def words(text: str) -> List[str]:
    """Split into word-sized deltas that re-join exactly (keeps spaces/newlines)."""
    return re.findall(r"\S+\s*|\s+", text)


class Turn:
    def __init__(self, conv: str, slow: bool = False):
        self.conv = conv
        self.steps: List[Step] = []
        self.factor = 3.0 if slow else 1.0
        self.answer = ""

    def add(self, delay: float, type_: str, **kw) -> None:
        self.steps.append((delay * self.factor, ev(self.conv, type_, **kw)))

    def progress(self, step: str, status: str, message: str, delay: float = 0.05, **kw) -> None:
        self.add(delay, "progress", step=step, status=status, message=message, **kw)

    def memory(self, found: int = 7) -> None:
        self.progress("memory_search", "started", "Searching memory...", 0.05)
        self.progress("dream", "started", "Dreaming...", 0.08)
        self.progress("dream", "completed", "Dreaming continues in background", 0.35, count=0, consumed=False)
        self.progress("memory_search", "completed",
                      f"Found {found} memories" if found else "No relevant memories", 0.05, count=found)

    def reasoning(self, text: str, per: float = 0.03) -> None:
        for w in words(text):
            self.add(per, "reasoning", content=w)

    def thinking(self) -> None:
        self.add(0.1, "thinking", content="Thinking...")

    def args_delta(self, name: str, call_id: str, args: Dict[str, Any]) -> None:
        raw = json.dumps(args)
        n = max(1, len(raw) // 3)
        for i in range(0, len(raw), n):
            self.add(0.06, "tool_args_delta", tool_name=name, call_id=call_id, delta=raw[i:i + n])

    def tool(self, name: str, args: Dict[str, Any], result: str, *, summary: str | None = None,
             run_for: float = 0.7, call_id: str = "call_x", search: str | None = None,
             search_done: str | None = None, search_count: int | None = None) -> None:
        self.args_delta(name, call_id, args)
        self.progress("tool_execution", "started", f"Running {name}...", 0.05, tool_name=name)
        kw = {"tool_name": name}
        if summary:
            kw["args_summary"] = summary
        self.add(0.05, "tool_start", **kw)
        if search:
            self.progress("search", "started", search, 0.05, tool_name=name)
            self.progress("search", "completed", search_done or "Search complete", run_for,
                          count=search_count, tool_name=name)
            self.add(0.05, "tool_end", tool_name=name, result=result[:500])
        else:
            self.add(run_for, "tool_end", tool_name=name, result=result[:500])
        self.progress("tool_execution", "completed", f"Completed {name}", 0.03, tool_name=name)

    def code(self, code: str, output: str, *, lang: str = "python", name: str = "execute_code",
             duration_ms: int = 184, ok: bool = True) -> None:
        self.args_delta(name, "call_code", {"code": code})
        self.progress("tool_execution", "started", f"Running {name}...", 0.05, tool_name=name)
        self.add(0.05, "tool_start", tool_name=name)
        self.add(0.1, "code", code=code, language=lang)
        for line in output.splitlines(keepends=True):
            self.add(0.25, "execution_output", output=line, stream="stdout")
        self.add(0.1, "execution_result", success=ok, duration_ms=duration_ms)
        self.add(0.02, "tool_end", tool_name=name,
                 result=f"{output}\n[Execution completed in {duration_ms}ms]"[:500])
        self.progress("tool_execution", "completed", f"Completed {name}", 0.03, tool_name=name)

    def content(self, text: str) -> None:
        for w in words(text):
            self.add(TOKEN_DELAY, "content", content=w)
        self.answer += text

    def finish(self) -> None:
        self.progress("generating", "started", "Generating response...", 0.02)
        self.progress("generating", "completed", "Response complete", 0.02)
        self.add(0.02, "done")


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def _topic(msg: str) -> str:
    m = msg.lower()
    if any(k in m for k in ("biscuit", "dog", "meds", "dose", "pet")):
        return "pets"
    if any(k in m for k in ("weather", "forecast", "rain", "kayak")):
        return "weather"
    if any(k in m for k in ("calendar", "today", "schedule", "tomorrow", "week")):
        return "calendar"
    return "general"


ANSWERS = {
    "pets": (
        "Biscuit's **evening meds are 42 minutes late** — Carprofen and the joint chew are still open on the board, "
        "and she refused the fish oil at 6:05.\n\n"
        "- Rowan is home (she's at the hub), so she can give them now\n"
        "- It's still inside the window — nothing to catch up tomorrow\n\n"
        "Want me to leave Rowan a note on the hub?"
    ),
    "weather": (
        "Saturday at Pine Lake looks good for kayaks:\n\n"
        "| Time | Temp | Wind | Rain |\n|---|---:|---:|---:|\n"
        "| 10am | 61°F | 6 mph | 5% |\n| 1pm | 67°F | 9 mph | 5% |\n| 4pm | 66°F | 14 mph | 10% |\n\n"
        "Book the **1–4pm** slot, but come in by 3:30 — the wind picks up after that."
    ),
    "calendar": (
        "Here's the rest of today:\n\n"
        "1. **4:45** — 1:1 with Kai (moved from 4:30; you already told them yes)\n"
        "2. **6:00** — Biscuit's PM meds *(overdue)*\n"
        "3. **7:10** — Rowan's flight lands, UA 1442, door 3\n\n"
        "Tomorrow starts with the vet call at 10. Nothing conflicts."
    ),
    "general": (
        "Here's what I found.\n\n"
        "- From memory: you mentioned this last week in **Cabin weekend at Pine Lake**\n"
        "- From the web: the top result agrees with what you remembered\n"
        "- Nothing on your calendar gets in the way\n\n"
        "Want me to save this as a note, or schedule a reminder?"
    ),
}

LONG = (
    "## The short version\n\n"
    "You're **on pace** for the sub‑2:00 half, with one risk: long runs have been drifting slow.\n\n"
    "## What the last four weeks say\n\n"
    "| Week | Miles | Long run | Pace |\n|---|---:|---:|---:|\n"
    "| 1 | 12 | 5 mi | 9:58 |\n| 2 | 14 | 6 mi | 9:48 |\n| 3 | 17 | 7 mi | 9:40 |\n| 4 | 19 | 8 mi | 9:31 |\n\n"
    "The trend is right. Race pace is **9:09/mi**, so the gap is closing by ~9 s/mi per week.\n\n"
    "## What I'd change\n\n"
    "1. Make the last 2 miles of each long run *at* race pace\n"
    "2. Swap one easy run for strides (6 × 20s)\n"
    "3. Sleep before Saturday — your two slowest runs followed <6h nights\n\n"
    "```text\nweek 5: 10 mi long (last 2 @ 9:09), tempo 4 mi @ 8:50, 2 × 4 easy\n```\n\n"
    "> If a week goes sideways, drop the tempo, never the long run.\n\n"
    "I've logged this on the goal page."
)


def build_turn(message: str, conv: str) -> Tuple[Turn, Dict[str, Any]]:
    """Returns the scripted turn and control flags (``status``/``detail`` for
    HTTP refusals, ``abort_after`` for a cut stream)."""
    m = (message or "").lower()
    flags: Dict[str, Any] = {}
    if "refuse" in m:
        flags.update(status=409, detail="I'm still writing that entry — open it again in a minute and reply then.")
        return Turn(conv), flags
    if re.search(r"\bfail\b", m):
        flags.update(status=500, detail="Mock failure before the stream started")
        return Turn(conv), flags

    t = Turn(conv, slow="slow" in m)
    topic = _topic(m)

    if "quick" in m:
        t.memory(2)
        t.reasoning("**Answering directly**\n\nSimple question; no tools needed.")
        t.content("Helena. (Montana's capital since 1875 — and yes, it's smaller than Billings.)" if "montana" in m
                  else "Quick answer: yes — and nothing else on your plate conflicts with it.")
        t.finish()
        return t, flags

    t.memory(7)

    if "code" in m or "python" in m or "calculate" in m:
        t.reasoning("**Working it out**\n\nThis is arithmetic; I'll run it rather than guess.")
        code = ("total = 412 + 38 + 120 + 70 + 95\n"
                "for name, share in ((\"Ellis\", 0.6), (\"Rowan\", 0.4)):\n"
                "    print(f\"{name}: ${total * share:,.2f}\")\n"
                "print(f\"Total: ${total:,.2f}\")")
        t.code(code, "Ellis: $441.00\nRowan: $294.00\nTotal: $735.00\n")
        t.thinking()
        t.reasoning("The numbers check out.")
        t.content("Ran it:\n\n- **Ellis:** $441.00\n- **Rowan:** $294.00\n- **Total:** $735.00\n\nSame as before — the split holds.")
        t.finish()
        return t, flags

    if "plan" in m:
        steps = [{"id": "step-1", "title": "Check the forecast for Saturday", "status": "in_progress", "result": None},
                 {"id": "step-2", "title": "Find a kayak rental with a 1pm slot", "status": "pending", "result": None},
                 {"id": "step-3", "title": "Add it to the trip document", "status": "pending", "result": None}]
        t.reasoning("**Planning the steps**\n\nThree things to do in order; I'll track them.")
        t.tool("create_plan", {"steps": [s["title"] for s in steps]},
               "Plan created with 3 steps:\n  step-1: Check the forecast for Saturday\n  step-2: Find a kayak rental\n  step-3: Add it to the trip document",
               summary='"Check the forecast for Saturday"', call_id="call_plan")
        t.add(0.02, "plan_created", plan_steps=steps)
        results = ["Sunny, 67°F, wind 9 mph at 1pm", "South Dock Rentals has a tandem at 1pm ($70)", "Added to Cabin weekend"]
        for i, s in enumerate(steps):
            if i:
                t.add(0.02, "plan_step_update", step_id=s["id"], step_status="in_progress", step_result=None)
            t.tool("update_plan_step", {"step_id": s["id"], "status": "completed", "result": results[i]},
                   f"Step {s['id']} updated to completed.", summary=f'"{s["id"]}"', run_for=0.8, call_id=f"call_s{i}")
            t.add(0.02, "plan_step_update", step_id=s["id"], step_status="completed", step_result=results[i])
        done = [dict(s, status="completed", result=results[i]) for i, s in enumerate(steps)]
        t.tool("complete_plan", {"summary": "Kayaks booked-ready for Saturday 1pm"}, "Plan completed.",
               summary='"Kayaks booked-ready for Saturday 1pm"', call_id="call_done")
        t.add(0.02, "plan_completed", plan_steps=done, plan_summary="Saturday 1pm kayaks look good and are in the trip doc.")
        t.thinking()
        t.content("All three done: the forecast is kind (67°F, light wind at 1pm), South Dock has a tandem at 1pm "
                  "for $70, and it's in **Cabin weekend — Pine Lake**.")
        t.finish()
        return t, flags

    if "worker" in m or "claude code" in m:
        tid = "task-inline-cc"
        t.reasoning("**Handing this to a worker**\n\nThis is a code change; a Codex session is the right tool.")
        t.args_delta("spawn_cc_worker", "call_cc", {"task": "Tidy the widget script", "wait": True})
        t.progress("tool_execution", "started", "Running spawn_cc_worker...", 0.05, tool_name="spawn_cc_worker")
        t.add(0.05, "tool_start", tool_name="spawn_cc_worker", args_summary='"Tidy the widget script"')
        t.add(0.2, "cc_session_start", task_id=tid, task_description="Tidy the widget script")
        t.add(0.5, "cc_text", task_id=tid, text="Reading widget/render.js to see how sections are laid out.")
        t.add(0.4, "cc_tool_use", task_id=tid, cc_tool_name="read_file", tool_input='{"path": "widget/render.js"}')
        t.add(0.6, "cc_tool_result", task_id=tid, text="142 lines; sections are rendered in two nearly identical loops.")
        t.add(0.5, "cc_text", task_id=tid, text="Merging the two loops into one renderSection() helper.")
        t.add(0.4, "cc_tool_use", task_id=tid, cc_tool_name="apply_patch", tool_input="*** Update File: widget/render.js")
        t.add(0.5, "cc_tool_result", task_id=tid, text="Patched: -38 +21 lines.")
        t.add(0.3, "cc_session_end", task_id=tid, status="completed", result_summary="Merged two render loops; no visual change.")
        t.add(0.05, "tool_end", tool_name="spawn_cc_worker", result="Merged two render loops; no visual change.")
        t.progress("tool_execution", "completed", "Completed spawn_cc_worker", 0.03, tool_name="spawn_cc_worker")
        t.thinking()
        t.content("The worker merged the two render loops into one helper (−17 lines). The widget looks the same.")
        t.finish()
        return t, flags

    if "exhaust" in m:
        t.reasoning("**Searching**\n\nThe answer isn't in the first page of results; keep looking.")
        for i in range(3):
            if i:
                t.thinking()
            t.tool("web_search", {"query": f"cabin pine lake pet policy page {i + 1}"}, "1. No relevant results",
                   summary=f'"cabin pine lake pet policy page {i + 1}"', call_id=f"call_w{i}",
                   search=f"Searching: cabin pine lake pet policy page {i + 1}", search_done="Search complete")
        t.progress("tool_loop", "exhausted", "Ran out of tool iterations (30)", 0.05, count=30)
        t.content("I stopped before finishing: I ran out of tool iterations. Done so far: three searches for the cabin's "
                  "pet policy, none of which found it. Left: call the owner — the number is in the booking email.")
        t.finish()
        return t, flags

    if "explain" in m:
        t.reasoning("**Checking the calendar**")
        t.tool("apple_calendar_list_events", {"days": 1}, "Tool error: calendar MCP call timed out after 10s",
               summary='"today"', run_for=1.2, call_id="call_cal")
        t.thinking()
        t.content("Tried to read your calendar and the Calendar connection timed out (10s). "
                  "Want me to try again, or go from what I already know about today?")
        t.finish()
        return t, flags

    if "remember" in m:
        t.reasoning("**Saving it**\n\nEllis asked me to remember something about themselves.")
        fact = message.split("remember", 1)[-1].strip(" :,.") or "that"
        t.tool("remember_update", {"memory": fact, "memory_type": "fact"}, f"Remembered: {fact}",
               summary=f'"{fact[:60]}"', run_for=0.4, call_id="call_mem")
        t.thinking()
        t.content(f"Got it — I'll remember {fact}.")
        t.finish()
        return t, flags

    # Default: three tools across two iterations.
    t.reasoning("**Checking what I already know**\n\nEllis's asking about something I've seen before. "
                "Memory first, then the web, then their schedule.")
    t.tool("remember_search", {"query": message[:60]},
           "Found 3 memories:\n- [fact] Biscuit takes meds at 06:00 and 18:00\n- [preference] notes over pushes after 9pm\n"
           "- [context] Cabin weekend 17th–19th",
           summary=f'"{message[:60]}"', run_for=0.5, call_id="call_mem")
    t.thinking()
    t.reasoning("\n\n**Looking it up**\n\nMemory helps, but I want a current source.")
    q = {"pets": "dog late dose guidance", "weather": "Pine Lake hourly forecast Saturday",
         "calendar": "UA 1442 arrival status", "general": message[:80]}[topic]
    t.tool("web_search", {"query": q},
           f"1. {q} — example.com\n2. Related guide — example.org\n3. Forum thread — example.net\n4. News — example.com\n5. FAQ — example.org",
           summary=f'"{q}"', call_id="call_web", run_for=0.9,
           search=f"Searching: {q}", search_done="Found 5 results", search_count=5)
    t.tool("list_scheduled_events", {"status": "pending"},
           "3 pending events:\n- Remind Ellis to call the vet (tomorrow 10:00)\n- Rowan's flight lands (7:10pm)\n- Surfaces pulse (*/15)",
           summary='"pending"', run_for=0.4, call_id="call_ev")
    t.thinking()
    t.reasoning("\n\n**Answering**\n\nI have enough.")
    t.content(LONG if "long" in m else ANSWERS[topic])
    if "error" in m or "hangup" in m:
        # Stop most of the way through the answer.
        cut = int(len(t.steps) * 0.85)
        t.steps = t.steps[:cut] + [("abort",) if "hangup" in m else ("end",)]
        return t, flags
    t.finish()
    return t, flags


def trace_from_events(sent: List[Tuple[float, Dict[str, Any]]]) -> Dict[str, Any] | None:
    """The stored turn trace (services/graph/turn_trace.py shape, as
    ``mockdata._trace``) for the events a live turn actually streamed, so a
    reload after the stream shows the same rail. ``sent`` is (monotonic
    seconds, event) in stream order."""
    reasoning = "".join(e.get("content") or "" for _, e in sent if e.get("type") == "reasoning")
    tools: List[Dict[str, Any]] = []
    running: List[Tuple[Dict[str, Any], float]] = []
    memory_hits = None
    for at, e in sent:
        kind = e.get("type")
        if kind == "progress" and e.get("step") == "memory_search" and e.get("status") == "completed":
            memory_hits = e.get("count")
        elif kind == "tool_start" and e.get("tool_name") not in ("execute_code", "execute_javascript", "execute_sql", "execute_shell"):
            row = {"name": e["tool_name"], "args_summary": e.get("args_summary"), "status": "ok",
                   "duration_ms": None, "result_preview": ""}
            tools.append(row)
            running.append((row, at))
        elif kind == "tool_end":
            for i in range(len(running) - 1, -1, -1):
                row, started = running[i]
                if row["name"] == e.get("tool_name"):
                    result = str(e.get("result") or "")
                    row["status"] = "error" if result.startswith(("Tool error:", "Unknown tool:", "BLOCKED:")) else "ok"
                    row["duration_ms"] = int((at - started) * 1000)
                    row["result_preview"] = result[:200]
                    running.pop(i)
                    break
    if not reasoning.strip() and not tools and memory_hits is None:
        return None
    return {"reasoning": reasoning[:4000] or None, "tools": tools, "memory_hits": memory_hits}


GREETING_CHUNKS = ["Evening, Ellis. ", "Biscuit's 6 o'clock meds ", "are still waiting, ", "and Jordan wants ", "an answer about Sunday."]
