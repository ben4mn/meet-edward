"""Mock Edward backend for the UX harness.

Run with the backend's venv (FastAPI + uvicorn live there), from frontend/:

    ../backend/.venv/bin/python scripts/mock-api/server.py --port 8100

Every endpoint the frontend calls answers with synthetic data (``mockdata.py``,
an invented household — nothing in it is real);
``POST /api/chat`` streams a scripted turn (``chatscript.py``). No auth, no
database, no LLM, nothing leaves the machine. The Today feed is composed by
the backend's own pure ``services.today_service.compose_today`` (imported from
the worktree's backend) so its shape is exactly what production sends.

Mock-only routes:
  GET  /__mock/health    liveness
  GET  /__mock/misses    every /api request no route answered (should be empty)
  GET  /__mock/requests  the last 300 requests with status codes
  POST /__mock/reset     rebuild all fixtures (undo every mutation)
  POST /__mock/mind_now  override GET /api/mind/now: {"preset": name} and/or fields; {"clear": true}
  POST /__mock/display_signals  override GET /api/display/signals fields; {"clear": true}
  POST /__mock/screen    override GET /api/screen (the stage) fields, relative times; {"clear": true}
  POST /__mock/screen_live  set GET /api/screen/live (his thinking, folds, recent thoughts), relative times; {"clear": true}
  POST /__mock/auth      {"authenticated": false} signs everyone out; for one browser set the
                         cookie mock_signed_out=1 instead (POST /api/auth/logout does)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import struct
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# The worktree's backend, for its pure composers only (no DB module is imported).
BACKEND = HERE.parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402

import chatscript  # noqa: E402
import mockdata as md  # noqa: E402
from services.today_service import TodayInputs, compose_today  # noqa: E402

assert "services.database" not in sys.modules, "mock must never import the backend's database module"

UTC = timezone.utc
TZ = ZoneInfo(md.TZ_NAME)

app = FastAPI(title="Edward mock API")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

S: Dict[str, Any] = md.fresh_state()
MISSES: List[Dict[str, Any]] = []
REQUESTS: List[Dict[str, Any]] = []
PROCESSING: set = set()
FILE_BLOBS: Dict[str, tuple] = {}  # uploaded file id -> (mime, bytes)

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return md.iso(now())


def err(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def body_json(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    ms = int((time.monotonic() - t0) * 1000)
    row = {"at": iso_now(), "method": request.method, "path": request.url.path,
           "query": request.url.query, "status": response.status_code, "ms": ms}
    REQUESTS.append(row)
    del REQUESTS[:-300]
    if request.url.path.startswith("/api/"):
        tag = "MISS" if getattr(request.state, "miss", False) else "    "
        print(f"{tag} {response.status_code} {request.method} {request.url.path}"
              f"{'?' + request.url.query if request.url.query else ''} ({ms}ms)", flush=True)
    return response


# ---------------------------------------------------------------------------
# Mock control
# ---------------------------------------------------------------------------

@app.get("/__mock/health")
async def mock_health():
    return {"ok": True, "started": md.iso(md.START), "conversations": len(S["conversations"])}


@app.get("/__mock/misses")
async def mock_misses():
    return {"misses": MISSES}


@app.get("/__mock/requests")
async def mock_requests():
    return {"requests": REQUESTS}


@app.post("/__mock/reset")
async def mock_reset():
    global S
    S = md.fresh_state()
    MISSES.clear()
    FILE_BLOBS.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

MOCK_SIGNED_OUT_COOKIE = "mock_signed_out"


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Signed in, unless this browser signed out (a cookie, so one agent's
    login test never signs out another's screenshots) or /__mock/auth said so."""
    signed_out = request.cookies.get(MOCK_SIGNED_OUT_COOKIE) == "1"
    return {"configured": True,
            "authenticated": bool(S.get("mock_authenticated", True)) and not signed_out,
            # The fictional owner's name (EDWARD_OWNER_NAME on a real backend).
            "owner_name": "Ellis"}


@app.post("/api/auth/login")
@app.post("/api/auth/setup")
async def auth_login(request: Request):
    """Any password signs in, except the literal "wrong" (a 401 like the
    backend's), so the login page's wince can be seen."""
    body = await body_json(request)
    if body.get("password") == "wrong":
        return err(401, "Invalid password")
    resp = JSONResponse({"status": "ok", "message": "Logged in (mock)"})
    resp.delete_cookie(MOCK_SIGNED_OUT_COOKIE, path="/")
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(MOCK_SIGNED_OUT_COOKIE, "1", path="/")
    return resp


@app.post("/__mock/auth")
async def mock_auth(request: Request):
    """Everyone signed in or out: ``{"authenticated": false}``. Prefer the
    per-browser cookie (``document.cookie = "mock_signed_out=1"``) so other
    sessions are untouched; /__mock/reset signs everyone back in."""
    body = await body_json(request)
    S["mock_authenticated"] = bool(body.get("authenticated", True))
    return {"ok": True, "authenticated": S["mock_authenticated"]}


@app.post("/api/auth/change-password")
async def auth_change_password():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

YOURS = {"user", "external_message"}
LIST_KEYS = ("id", "title", "source", "channel", "notified_user", "created_at", "updated_at",
             "last_activity_at", "message_count", "thought")


def conv_public(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in LIST_KEYS}


def sort_key(c: Dict[str, Any]) -> str:
    if c.get("source") == "heartbeat" and c.get("last_activity_at"):
        return c["last_activity_at"]
    return c["updated_at"]


@app.get("/api/conversations")
async def list_conversations(limit: int = 50, offset: int = 0, include_scheduled: bool = False,
                             search: Optional[str] = None, group: Optional[str] = None):
    rows = list(S["conversations"].values())
    if group == "yours":
        rows = [c for c in rows if (c.get("source") or "user") in YOURS]
    elif group == "edwards":
        rows = [c for c in rows if (c.get("source") or "user") not in YOURS]
    elif not include_scheduled:
        rows = [c for c in rows if c.get("source") != "scheduled_event"]
    if search and search.strip():
        q = search.strip().lower()
        rows = [c for c in rows if q in (c.get("title") or "").lower()
                or any(q in (m.get("content") or "").lower() for m in c.get("messages", []))]
    rows.sort(key=sort_key, reverse=True)
    page = rows[offset: offset + limit]
    return {"conversations": [conv_public(c) for c in page], "has_more": offset + limit < len(rows)}


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    c = S["conversations"].get(cid)
    if not c:
        return err(404, "Conversation not found")
    out = conv_public(c)
    out.pop("last_activity_at", None)
    # ``trace``: the turn's thinking and tools, kept on assistant messages
    # (services/graph/turn_trace.py); None on turns stored before it.
    out["messages"] = [{**{k: m.get(k) for k in ("role", "content", "attachments", "is_trigger", "trigger_type")},
                        "trace": m.get("trace") if m.get("role") == "assistant" else None}
                       for m in c["messages"] if (m.get("content") or "").strip() or m.get("attachments")]
    out["cc_sessions"] = [
        {"task_id": t["id"], "description": t["task_description"], "status": t["status"],
         "result_summary": t.get("result_summary"), "error": t.get("error")}
        for t in S["orchestrator"]["tasks"] if t.get("parent_conversation_id") == cid and t.get("task_type") == "cc_session"
    ]
    c["notified_user"] = False  # reading clears the dot, like the real endpoint
    return out


@app.patch("/api/conversations/{cid}")
async def patch_conversation(cid: str, request: Request):
    c = S["conversations"].get(cid)
    if not c:
        return err(404, "Conversation not found")
    body = await body_json(request)
    if body.get("title"):
        c["title"] = body["title"]
        c["updated_at"] = iso_now()
    return conv_public(c)


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    if cid not in S["conversations"]:
        return err(404, "Conversation not found")
    del S["conversations"][cid]
    return {"status": "deleted", "id": cid}


# ---------------------------------------------------------------------------
# Chat (SSE)
# ---------------------------------------------------------------------------

@app.get("/api/chat/{cid}/processing")
async def chat_processing(cid: str):
    return {"processing": cid in PROCESSING}


def _store_upload(filename: str, mime: str, data: bytes, conv_id: str) -> Dict[str, Any]:
    fid = f"f-up-{uuid.uuid4().hex[:8]}"
    FILE_BLOBS[fid] = (mime or "application/octet-stream", data)
    S["files"].insert(0, {"id": fid, "filename": filename, "mime_type": mime, "size_bytes": len(data),
                          "category": "image" if mime.startswith("image/") else ("audio" if mime.startswith("audio/") else "document"),
                          "description": None, "tags": None, "source": "upload", "source_conversation_id": conv_id,
                          "created_at": iso_now(), "updated_at": iso_now(), "last_accessed": None, "access_count": 0,
                          "download_url": f"/api/files/{fid}/download"})
    return {"file_id": fid, "filename": filename, "mime_type": mime, "size": len(data)}


@app.post("/api/chat")
async def chat(request: Request):
    ctype = request.headers.get("content-type", "")
    uploads: List[Dict[str, Any]] = []
    if "multipart/form-data" in ctype:
        form = await request.form()
        message = str(form.get("message") or "")
        conv_id = form.get("conversation_id") or None
        raw_files = [(f.filename, f.content_type, await f.read()) for f in form.getlist("files") if hasattr(f, "read")]
    else:
        body = await body_json(request)
        message = str(body.get("message") or "")
        conv_id = body.get("conversation_id") or None
        raw_files = []
    if not message and not raw_files:
        return err(400, "Message or files required")

    _, flags = chatscript.build_turn(message, conv_id or "pending")
    if flags.get("status"):
        return err(flags["status"], flags["detail"])

    is_new = conv_id is None or conv_id not in S["conversations"]
    conv_id = conv_id or f"c-new-{uuid.uuid4().hex[:8]}"
    for name, mime, data in raw_files:
        uploads.append(_store_upload(name or "upload", mime or "application/octet-stream", data, conv_id))

    memo = [u for u in uploads if (u["mime_type"] or "").startswith("audio/")]
    if memo and not message.strip():
        message = "What's left on my list for today?"  # the mock "transcript"

    if is_new:
        title = (message or "Image upload")[:50].strip()
        S["conversations"][conv_id] = {
            "id": conv_id, "title": title, "source": "user", "channel": "text", "notified_user": False,
            "created_at": iso_now(), "updated_at": iso_now(), "last_activity_at": None, "message_count": 0,
            "thought": None, "messages": [], "chat_identifier": None,
        }
    conv = S["conversations"][conv_id]
    conv["messages"].append({"role": "user", "content": message, "attachments": uploads or None,
                             "is_trigger": False, "trigger_type": None})
    conv["message_count"] = len(conv["messages"])
    conv["updated_at"] = iso_now()

    turn, _ = chatscript.build_turn(message, conv_id)

    async def generate():
        PROCESSING.add(conv_id)
        sent: List[str] = []
        streamed: List[Tuple[float, Dict[str, Any]]] = []  # for the stored trace
        try:
            if memo:
                yield "data: " + json.dumps({
                    "type": "user_message", "conversation_id": conv_id, "content": message,
                    "attachments": [dict(u, transcript=message) for u in memo]}) + "\n\n"
            for step in turn.steps:
                if step[0] in ("abort", "end"):
                    yield "data: " + json.dumps({"type": "error", "conversation_id": conv_id,
                                                 "message": "mock: the turn failed mid-stream"}) + "\n\n"
                    await asyncio.sleep(0.05)
                    if step[0] == "end":
                        return
                    raise ConnectionAbortedError("mock stream cut (hangup)")
                delay, event = step
                if delay:
                    await asyncio.sleep(delay)
                if event.get("type") == "content":
                    sent.append(event.get("content") or "")
                streamed.append((time.monotonic(), event))
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            PROCESSING.discard(conv_id)
            text = "".join(sent)
            if text:
                conv["messages"].append({"role": "assistant", "content": text, "attachments": None,
                                         "is_trigger": False, "trigger_type": None,
                                         "trace": chatscript.trace_from_events(streamed)})
                conv["message_count"] = len(conv["messages"])
                conv["updated_at"] = iso_now()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/chat/simple")
async def chat_simple(request: Request):
    body = await body_json(request)
    turn, _ = chatscript.build_turn(str(body.get("message") or ""), body.get("conversation_id") or "simple")
    return {"response": turn.answer, "conversation_id": body.get("conversation_id") or "simple"}


@app.post("/api/greeting")
async def greeting():
    return {"greeting": S["greeting"]}


@app.get("/api/greeting/stream")
async def greeting_stream():
    async def gen():
        for chunk in chatscript.GREETING_CHUNKS:
            await asyncio.sleep(0.12)
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------------------
# Settings, models, usage
# ---------------------------------------------------------------------------

CATALOG = [
    ("gpt-6-astra", "GPT-6-Astra", "Frontier intelligence for the most demanding work.", "medium"),
    ("gpt-5.6-sol", "GPT-5.6-Sol", "Older coding model for complex work.", "low"),
    ("gpt-5.6-terra", "GPT-5.6-Terra", "Older balanced model for straightforward work.", "medium"),
    ("gpt-5.6-luna", "GPT-5.6-Luna", "Older fast and efficient model.", "medium"),
    ("gpt-5.5", "GPT-5.5", "Legacy coding model.", "medium"),
]


@app.get("/api/settings")
async def get_settings():
    return S["settings"]


@app.post("/api/settings")
async def post_settings(request: Request):
    body = await body_json(request)
    if "model" in body and body["model"] not in {c[0] for c in CATALOG}:
        return err(400, f"{body['model']!r} is not in the Codex catalog ({', '.join(c[0] for c in CATALOG)})")
    for k, v in body.items():
        if k in S["settings"] and v is not None:
            S["settings"][k] = v
    return S["settings"]


@app.get("/api/settings/models")
async def settings_models():
    entries = [{"id": i, "name": f"{n} (Recommended)" if i == "gpt-6-astra" else n, "description": d,
                "supports_images": True, "default_effort": e, "context_window": 272000} for i, n, d, e in CATALOG]
    return {"providers": {"openai": {"available": True, "models": entries},
                          "anthropic": {"available": False, "models": []}},
            "models": [{"id": m["id"], "name": m["name"]} for m in entries],
            "catalog": {"live": True, "path": "~/.codex/models_cache.json", "recommended": "gpt-6-astra"},
            "background_model": "gpt-5.6-terra", "voice_model": "gpt-5.6-luna",
            # What each kind of turn runs on (2026-09-26; the real endpoint
            # resolves these through mind_service.routing_summary).
            "mind_model": "gpt-6-astra", "mind_effort": "high",
            "mind_efforts": {"manual": "high", "morning": "high", "evening": "high", "self": "high",
                             "messages": "medium", "mention": "medium", "reflect": "xhigh",
                             "reflect_lite": "high", "research": "high", "prep": "medium", "free": "high"},
            "pulse_model": "gpt-5.6-terra", "pulse_effort": "low",
            "chat_model": S["settings"]["model"], "chat_effort": "medium"}


@app.get("/api/settings/codex-usage")
async def codex_usage():
    return S["codex_usage"]


@app.get("/api/usage")
async def usage():
    return S["usage"]


# ---------------------------------------------------------------------------
# Today, goals, ideas, approvals, signals
# ---------------------------------------------------------------------------

def goal_summary(g: Dict[str, Any]) -> Dict[str, Any]:
    return {k: g.get(k) for k in ("id", "title", "status", "why", "plan", "last_progress", "progress_count",
                                  "set_on", "set_by", "next_check_in", "check_in", "check_in_event_id",
                                  "updated_at", "href")}


# The fixture events' ``event_metadata`` type (and a tick's kind), as the
# real ``today_service._events`` reads it: Edward's plumbing is never a need
# or a "what I did" row, and the next wake is presence.
MOCK_EVENT_META = {
    "ev-tick-morning": ("wakeup_tick", "morning"), "ev-tick-evening": ("wakeup_tick", "evening"),
    "ev-pulse": ("expression_pulse", None), "ev-parker-check": ("mind_wake", None), "ev-forecast": ("mind_wake", None),
}


def _typed_event(e: Dict[str, Any]) -> Dict[str, Any]:
    t, k = MOCK_EVENT_META.get(e["id"], (None, None))
    return {**e, "type": t, "kind": k}


def _flags_for_today() -> Dict[str, Dict[str, Any]]:
    """What ``today_service._note_flags`` reads: each flag with both memories."""
    return {f["id"]: {"resolved": bool(f["resolved"]), "memory_a": f.get("memory_content"),
                      "memory_b": f.get("related_memory_content")} for f in S["dreaming"]["flags"]}


def today_inputs() -> TodayInputs:
    from services.today_service import did_since

    n = now()
    since = did_since(n, TZ)
    fired = [{"id": e["id"], "description": e["description"], "recurrence_pattern": e["recurrence_pattern"],
              "last_fired_at": e["last_fired_at"], "type": _typed_event(e)["type"]}
             for e in S["events"] if (parse_iso(e.get("last_fired_at")) or datetime.min.replace(tzinfo=UTC)) >= since]
    convs = sorted(S["conversations"].values(), key=lambda c: c["updated_at"], reverse=True)
    actions = []
    for c in S["conversations"].values():
        t = c.get("thought")
        if t and t.get("kind") == "mind_action":
            first = next((m["content"] for m in c["messages"] if m["role"] == "assistant"), "")
            line = next((ln[2:] for ln in first.splitlines() if ln.startswith("- ") and not ln.startswith("- Also")), "")
            actions.append({"ledger_id": t["ledger_id"], "thought_id": c["id"], "title": c["title"],
                            "first_line": line, "at": t["acted_at"]})
    wishes = [w for w in S["wishlist"] if (parse_iso(w["created_at"]) or n) >= n - timedelta(hours=24)]
    return TodayInputs(
        board=S["board"],
        heartbeat=S["heartbeat_events"],
        open_loops=[l for l in S["proactive"]["loops"]],
        events=[_typed_event(e) for e in S["events"] if e.get("status") == "pending"],
        workers=S["orchestrator"]["tasks"],
        fired_events=fired,
        triage_counts={"total": 47, "noted": 6, "escalated": 4, "handled_by_owner": 3, "dismissed": 34,
                       "latest_at": S["heartbeat_events"][0]["created_at"] if S["heartbeat_events"] else None},
        conversations=[{"id": c["id"], "title": c["title"], "source": c["source"], "updated_at": c["updated_at"]} for c in convs],
        status=S["heartbeat_status"],
        wishes=wishes,
        notes=S["notes"],
        # Goals and ideas left Today with unit G (2026-10-11); their own
        # routes below still serve the old panels.
        approvals=S["approvals"],
        actions=actions,
        wakes=[w for w in S["mind_log"] if (parse_iso(w["at"]) or n) >= since] or S["mind_log"][:1],
        flags=_flags_for_today(),
    )


@app.get("/api/today")
async def today():
    return compose_today(today_inputs(), now=now(), tz_name=md.TZ_NAME)


@app.post("/api/today/heartbeat/{eid}/done")
async def today_heartbeat_done(eid: str, request: Request):
    """"Done" on a message or calendar card: its events are stamped seen."""
    ids = {eid, *((await body_json(request)).get("also") or [])}
    rows = [e for e in S["heartbeat_events"] if e["id"] in ids]
    if not any(e["id"] == eid for e in rows):
        return err(404, "Event not found")
    done = 0
    for e in rows:
        if not e.get("notified_at") or e.get("notification_batch_id") == "mind:gave-up":
            e["notified_at"], e["notification_batch_id"] = iso_now(), "owner:done"
            done += 1
    return {"ok": True, "done": done}


@app.get("/api/notes")
async def list_notes(hours: float = 24, limit: int = 60):
    n = now()
    rows = [x for x in S["notes"] if (parse_iso(x.get("expires_at")) or n + timedelta(days=1)) > n
            and (parse_iso(x.get("created_at")) or n) >= n - timedelta(hours=hours) and x.get("kind") != "idea"]
    rows.sort(key=lambda x: x["created_at"], reverse=True)
    return {"notes": [{**x, "href": x.get("href") or "/today"} for x in rows[:limit]]}


@app.post("/api/notes/{nid}/dismiss")
async def dismiss_note(nid: str, request: Request):
    """"Done" on a note card: the note and the rest of its card expire now."""
    ids = {nid, *((await body_json(request)).get("also") or [])}
    rows = [x for x in S["notes"] if x["id"] in ids]
    if not any(x["id"] == nid for x in rows):
        return err(404, "Note not found")
    n, dismissed = now(), 0
    for x in rows:
        if (parse_iso(x.get("expires_at")) or n + timedelta(days=1)) > n:
            x["expires_at"] = iso_now()
            dismissed += 1
    return {"ok": True, "dismissed": dismissed}


@app.get("/api/goals")
async def list_goals(status: str = "live"):
    want = status.lower()
    order = {"active": 0, "paused": 1, "done": 2, "dropped": 3}
    rows = [g for g in S["goals"].values()
            if want == "all" or g["status"] == want or (want == "live" and g["status"] in ("active", "paused"))]
    rows.sort(key=lambda g: (order.get(g["status"], 9), g["next_check_in"] or "9999"))
    return {"status": status, "goals": [goal_summary(g) for g in rows]}


@app.get("/api/goals/{gid}")
async def get_goal(gid: str):
    g = S["goals"].get(gid)
    return g if g else err(404, "Goal not found")


@app.post("/api/goals")
async def create_goal(request: Request):
    body = await body_json(request)
    title, why = (body.get("title") or "").strip(), (body.get("why") or "").strip()
    if not title or not why:
        return err(400, "title and why are required")
    if any(g["title"].lower() == title.lower() and g["status"] in ("active", "paused") for g in S["goals"].values()):
        return err(409, f"Already a goal: {title}")
    gid = f"g-{uuid.uuid4().hex[:8]}"
    day = now().astimezone(TZ).date().isoformat()
    check_in = body.get("check_in") or "weekly"
    g = md._goal(gid, title, "active", why, body.get("plan") or "",
                 [f"{day}: Goal set (by Ellis, on Today); check-in {check_in}."],
                 {"weekly": "weekly (Sundays 19:00)", "daily": "daily (19:00)"}.get(check_in, check_in),
                 now() + timedelta(days=7), now(), now())
    S["goals"][gid] = g
    return g


@app.patch("/api/goals/{gid}")
async def patch_goal(gid: str, request: Request):
    g = S["goals"].get(gid)
    if not g:
        return err(404, "Goal not found")
    body = await body_json(request)
    day = now().astimezone(TZ).date().isoformat()
    if body.get("progress"):
        g["log"].append(f"{day}: {body['progress'].strip()}")
    if body.get("plan") is not None:
        g["plan"] = body["plan"]
    if body.get("status"):
        if body["status"] not in ("active", "paused", "done", "dropped"):
            return err(400, "status must be active, paused, done or dropped")
        g["status"] = body["status"]
        g["next_check_in"] = md.iso(now() + timedelta(days=7)) if g["status"] == "active" else None
    if body.get("check_in"):
        g["check_in"] = body["check_in"]
    g.update(md.goal_counts(g["log"]))
    g["content"] = md._goal_content(g["why"], g["plan"], g["log"])
    g["updated_at"] = iso_now()
    return g


@app.get("/api/ideas")
async def list_ideas():
    return {"ideas": S["ideas"]}


@app.post("/api/ideas/{iid}/{action}")
async def resolve_idea(iid: str, action: str):
    if action not in ("try", "dismiss"):
        return err(404, "Not found")
    idea = next((i for i in S["ideas"] if i["id"] == iid), None)
    if not idea:
        return err(404, "Idea not found")
    S["ideas"] = [i for i in S["ideas"] if i["id"] != iid]
    S["signals"].append({"kind": "idea_tried" if action == "try" else "idea_dismissed", "ref_id": iid, "at": iso_now()})
    return {"ok": True, "id": iid, "href": idea["href"], "prompt": idea["prompt"]}


def approval_public(a: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k in ("created_at", "decided_at"):
        v = a.get(k)
        out[k] = (v.isoformat() + "Z") if isinstance(v, datetime) else v
    return out


@app.get("/api/approvals")
async def list_approvals(status: Optional[str] = None, limit: int = 50):
    rows = [a for a in S["approvals"] if not status or a["status"] == status]
    return {"approvals": [approval_public(a) for a in rows[:limit]]}


@app.get("/api/approvals/{aid}")
async def get_approval(aid: int):
    a = next((a for a in S["approvals"] if a["id"] == aid), None)
    return approval_public(a) if a else err(404, f"no approval #{aid}")


@app.post("/api/approvals/{aid}")
async def decide_approval(aid: int, request: Request):
    body = await body_json(request)
    decision = body.get("decision")
    if decision not in ("approve", "deny"):
        return err(400, "decision must be approve or deny")
    a = next((a for a in S["approvals"] if a["id"] == aid), None)
    if not a:
        return err(404, f"no approval #{aid}")
    if a["status"] != "pending":
        if a["status"] == ("approved" if decision == "approve" else "denied"):
            from services.approvals_service import delivered
            return {**approval_public(a), "already_decided": True,
                    "delivered": a["status"] == "approved" and delivered(a["tool_name"], a.get("result_text"))}
        return err(409, f"approval #{aid} is already {a['status']}")
    a["status"] = "approved" if decision == "approve" else "denied"
    a["decided_at"] = now().replace(tzinfo=None)
    who = (a.get("args") or {}).get("contact") or "them"
    a["result_text"] = (f"iMessage sent to {who} (mock — nothing left this machine)." if decision == "approve"
                        else "Denied by Ellis; not sent.")
    # From the send tool's own reply, like approvals_service.decide (2026-09-26).
    from services.approvals_service import delivered
    return {**approval_public(a), "delivered": decision == "approve" and delivered(a["tool_name"], a["result_text"])}


@app.post("/api/signals")
async def post_signal(request: Request):
    body = await body_json(request)
    S["signals"].append({**body, "at": iso_now()})
    return {"id": len(S["signals"]), "kind": body.get("kind")}


@app.get("/api/signals")
async def get_signals(days: int = 7):
    return {"signals": S["signals"]}


@app.post("/api/presence/beacon")
async def presence_beacon(request: Request):
    body = await body_json(request)
    return {"ok": True, "device_id": body.get("device_id"), "state": body.get("state")}


# ---------------------------------------------------------------------------
# The mind, journal
# ---------------------------------------------------------------------------

@app.get("/api/mind/log")
async def mind_log(days: int = 7):
    cutoff = now() - timedelta(days=days)
    return {"days": days, "wakes": [w for w in S["mind_log"] if (parse_iso(w["at"]) or now()) >= cutoff]}


@app.get("/api/mind/recent")
async def mind_recent(limit: int = 20):
    return {"wakes": S["mind_log"][:limit]}


@app.get("/api/mind/intentions")
async def mind_intentions():
    return {"intentions": S["intentions"]}


@app.post("/api/mind/verdict")
async def mind_verdict(request: Request):
    body = await body_json(request)
    verdict = body.get("verdict")
    if verdict not in ("right_call", "wrong_call"):
        return err(400, "verdict must be one of ['right_call', 'wrong_call']")
    lid = int(body.get("ledger_id") or 0)
    wake = next((w for w in S["mind_log"] if w["id"] == lid), None)
    if wake is None:
        return err(404, "no such wake")
    wake["verdict"] = {"verdict": verdict, "note": body.get("note")}
    for c in S["conversations"].values():
        if (c.get("thought") or {}).get("ledger_id") == lid:
            c["thought"]["verdict"] = verdict
    return {"id": f"sig-{uuid.uuid4().hex[:6]}", "kind": "mind_verdict", "ledger_id": lid}


MOCK_WAKE_SECONDS = 6.0


@app.post("/api/mind/wake")
async def mind_wake(request: Request):
    """Like the backend since 2026-09-25: 202 at once, the wake runs in the
    background (``waking`` in /api/mind/now meanwhile), a press while it runs
    joins it."""
    body = await body_json(request)
    reason = body.get("reason") or "manual"
    if S.get("mind_waking"):
        return JSONResponse(status_code=202, content={"accepted": True, "reason": reason, "joined": True})
    S["mind_waking"] = {"reason": reason, "started_at": md.iso(now().replace(microsecond=0)),
                        "conversation_id": "edward-mind"}
    state = S

    async def _finish():
        await asyncio.sleep(MOCK_WAKE_SECONDS)
        if state is not S:  # /__mock/reset ran meanwhile
            return
        S["counters"]["wake"] += 1
        S["mind_log"].insert(0, md._wake(S["counters"]["wake"], now(), reason, "left_alone",
                                         "Looked because you asked; nothing needs you right now."))
        S["mind_waking"] = None

    asyncio.get_running_loop().create_task(_finish())
    return JSONResponse(status_code=202, content={"accepted": True, "reason": reason, "joined": False})


@app.get("/api/mind/now")
async def mind_now():
    """Edward's own live state (backend ``services/wake_state.py``)."""
    override = dict(S.get("mind_now_override") or {})
    preset = override.pop("preset", None)
    if preset == "offline":
        return err(503, "mock: Edward is offline")
    if preset == "missing":
        return err(404, "Not Found")
    body = md.mind_now(S, now())
    if preset:
        body = md.merge_mind_now(body, md.mind_now_preset(preset, now()))
    return md.merge_mind_now(body, override)


@app.post("/__mock/mind_now")
async def mock_mind_now(request: Request):
    """Override /api/mind/now for UI testing: ``{"preset": <name>}`` (one of
    ``md.MIND_NOW_PRESETS``) and/or any top-level field (``quota`` / ``hands``
    merge key by key). ``{"clear": true}`` (or /__mock/reset) removes it."""
    body = await body_json(request)
    if body.pop("clear", False):
        S["mind_now_override"] = {}
    preset = body.get("preset")
    if preset is not None and preset not in md.MIND_NOW_PRESETS:
        return err(400, f"unknown preset {preset!r}; expected one of {list(md.MIND_NOW_PRESETS)}")
    S["mind_now_override"] = {**(S.get("mind_now_override") or {}), **body}
    return {"ok": True, "override": S["mind_now_override"]}


@app.get("/api/journal/week")
async def journal_week():
    return S["journal"]


# ---------------------------------------------------------------------------
# Memories, documents, provenance, forget
# ---------------------------------------------------------------------------

def memory_page(rows: List[Dict[str, Any]], limit: int, offset: int) -> Dict[str, Any]:
    return {"memories": rows[offset: offset + limit], "stats": md.memory_stats(S["memories"]),
            "pagination": {"limit": limit, "offset": offset, "total": len(rows)}}


@app.get("/api/memories")
async def search_memories(query: Optional[str] = None, memory_type: Optional[str] = None,
                          min_importance: Optional[float] = None, temporal_nature: Optional[str] = None,
                          tier: Optional[str] = None, limit: int = 50, offset: int = 0):
    rows = list(S["memories"])
    if query:
        words = [w for w in query.lower().split() if w]
        scored = []
        for m in rows:
            hits = sum(1 for w in words if w in m["content"].lower())
            if hits:
                scored.append(dict(m, score=round(0.55 + 0.4 * hits / len(words), 3)))
        rows = sorted(scored, key=lambda m: m["score"], reverse=True)
    if memory_type:
        rows = [m for m in rows if m["memory_type"] == memory_type]
    if min_importance is not None:
        rows = [m for m in rows if m["importance"] >= min_importance]
    if temporal_nature:
        rows = [m for m in rows if m["temporal_nature"] == temporal_nature]
    if tier:
        rows = [m for m in rows if m["tier"] == tier]
    return memory_page(rows, limit, offset)


@app.get("/api/debug/memories")
async def debug_memories(limit: int = 50, offset: int = 0):
    return memory_page(list(S["memories"]), limit, offset)


@app.get("/api/debug/memories/stats")
async def debug_memory_stats():
    return md.memory_stats(S["memories"])


@app.get("/api/memories/context")
async def memory_context(chat_id: Optional[str] = None, limit: int = 2):
    conv = next((c for c in S["conversations"].values() if c.get("chat_identifier") == chat_id), None)
    name = (conv["title"].replace("iMessage: ", "") if conv else None)
    mems = [m for m in S["memories"] if name and name.lower() in m["content"].lower()][:limit]
    return {"contact_name": name, "memories": [{k: m[k] for k in ("id", "content", "memory_type", "importance")} for m in mems]}


@app.get("/api/memories/{mid}")
async def get_memory(mid: str):
    # One memory by id (routers/memories.get_memory): the Dreaming panel reads
    # the two sides of a connection, MemoryBrowser a deep link past the page.
    m = next((m for m in S["memories"] if m["id"] == mid), None)
    return dict(m) if m else err(404, "Memory not found")


@app.get("/api/memories/{mid}/provenance")
async def memory_provenance(mid: str):
    m = next((m for m in S["memories"] if m["id"] == mid), None)
    if not m:
        return err(404, "Memory not found")
    conv = S["conversations"].get(m.get("source_conversation_id") or "")
    chat = None
    if conv and conv.get("chat_identifier"):
        chat = {"chat_identifier": conv["chat_identifier"], "chat_name": conv["title"].replace("iMessage: ", ""),
                "is_group": conv["chat_identifier"].startswith("chat")}
    ref: Dict[str, Any] = {"kind": "remember" if m["origin"] in ("thought", "did") else "extraction",
                           "turn_source": "mind" if m["origin"] in ("thought", "did") else "chat"}
    if m["origin"] == "read" and chat:
        ref.update(event_ids=["hb-8839"], rowids=[418221])
    if m["reinforcement_count"] > 2:
        ref["also"] = [{"at": md.iso(md.ago(days=i))} for i in range(1, min(4, m["reinforcement_count"]))]
    return {
        "memory": {"id": m["id"], "content": m["content"], "origin": m["origin"], "about": m["about"],
                   "source_type": "conversation" if conv else "unknown", "created_at": m["created_at"],
                   "source_conversation_id": m.get("source_conversation_id"), "confidence": 0.6 if m["origin"] == "thought" else 0.9,
                   "tier": m["tier"], "reinforcement_count": m["reinforcement_count"]},
        "conversation": ({"id": conv["id"], "title": conv["title"], "source": conv["source"], "channel": conv["channel"]}
                         if conv else None),
        "chat": chat,
        "contact": None,
        "fold": None,
        "source_ref": ref,
        "summary": f"Stored {m['created_at'][:10]} from {'“' + conv['title'] + '”' if conv else 'no recorded conversation'}.",
    }


FORGET_PLANS: Dict[str, Dict[str, Any]] = {}


@app.post("/api/memories/forget")
async def memories_forget(request: Request):
    body = await body_json(request)
    subject = (body.get("subject") or "").strip()
    if not subject:
        return err(400, "subject is required")
    if not body.get("confirm"):
        terms = [subject] + ([subject.split()[0]] if " " in subject else [])
        pat = re.compile(r"\b" + re.escape(subject.lower()) + r"\b")
        mems = [{"id": m["id"], "content": m["content"], "why": f"mentions “{subject}”"}
                for m in S["memories"] if pat.search(m["content"].lower())]
        docs = [{"id": d["id"], "title": d["title"], "why": f"mentions “{subject}”"}
                for d in S["documents"] if pat.search((d["title"] + " " + d["content"]).lower()) and d["title"] != "Edward: state"]
        protected = [{"id": d["id"], "title": d["title"], "why": "the state document is never deleted"}
                     for d in S["documents"] if d["title"] == "Edward: state" and pat.search(d["content"].lower())]
        convs = [c["title"] for c in S["conversations"].values()
                 if any(pat.search((mm.get("content") or "").lower()) for mm in c["messages"])]
        plan = {"id": f"plan-{uuid.uuid4().hex[:8]}", "subject": subject, "terms": terms, "memories": mems,
                "documents": docs, "protected_documents": protected,
                "related": {"enrichments": len(mems) * 2, "connections": len(mems), "flags": 0,
                            "entity_mentions": len(mems), "entities": 1 if mems else 0, "graph_queue": 0},
                "conversations": {"count": len(convs), "titles": convs[:5]}}
        FORGET_PLANS[plan["id"]] = plan
        return {"plan": plan}
    plan = FORGET_PLANS.get(body.get("plan_id") or "")
    if not plan:
        return err(409, "That preview has expired; preview again.")
    ids = {m["id"] for m in plan["memories"]}
    S["memories"] = [m for m in S["memories"] if m["id"] not in ids]
    ndocs = 0
    if body.get("include_documents"):
        dids = {d["id"] for d in plan["documents"]}
        ndocs = len(dids)
        S["documents"] = [d for d in S["documents"] if d["id"] not in dids]
    return {"result": {"subject": plan["subject"], "plan_id": plan["id"],
                       "deleted": dict(plan["related"], memories=len(ids), documents=ndocs),
                       "still_matching": {"memories": 0, "documents": 0 if body.get("include_documents") else len(plan["documents"])},
                       "protected_documents": [d["title"] for d in plan["protected_documents"]],
                       "conversations_mentioning": plan["conversations"]["count"]}}


@app.patch("/api/memories/{mid}")
async def patch_memory(mid: str, request: Request):
    m = next((m for m in S["memories"] if m["id"] == mid), None)
    if not m:
        return err(404, "Memory not found")
    body = await body_json(request)
    for k in ("content", "memory_type", "importance"):
        if body.get(k) is not None:
            m[k] = body[k]
    m["updated_at"] = iso_now()
    return m


@app.delete("/api/memories/{mid}")
async def delete_memory(mid: str):
    before = len(S["memories"])
    S["memories"] = [m for m in S["memories"] if m["id"] != mid]
    if len(S["memories"]) == before:
        return err(404, "Memory not found")
    return {"status": "deleted", "id": mid}


def doc_stats() -> Dict[str, Any]:
    by_tag: Dict[str, int] = {}
    for d in S["documents"]:
        for t in (d.get("tags") or "").split(","):
            if t.strip():
                by_tag[t.strip()] = by_tag.get(t.strip(), 0) + 1
    return {"total": len(S["documents"]), "by_tag": by_tag}


@app.get("/api/documents")
async def search_documents(query: Optional[str] = None, tags: Optional[str] = None, limit: int = 50, offset: int = 0):
    rows = sorted(S["documents"], key=lambda d: d["updated_at"], reverse=True)
    if query:
        q = query.lower()
        rows = [dict(d, score=0.8) for d in rows if q in d["title"].lower() or q in d["content"].lower()]
    if tags:
        want = {t.strip() for t in tags.split(",") if t.strip()}
        rows = [d for d in rows if want & {t.strip() for t in (d.get("tags") or "").split(",")}]
    return {"documents": rows[offset: offset + limit], "stats": doc_stats(),
            "pagination": {"limit": limit, "offset": offset, "total": len(rows)}}


@app.post("/api/documents")
async def create_document(request: Request):
    body = await body_json(request)
    d = md._doc(f"d-{uuid.uuid4().hex[:8]}", body.get("title") or "Untitled", body.get("content") or "",
                body.get("tags") or "", created=now())
    S["documents"].insert(0, d)
    return d


@app.get("/api/documents/{did}")
async def get_document(did: str):
    d = next((d for d in S["documents"] if d["id"] == did), None)
    return d if d else err(404, "Document not found")


@app.patch("/api/documents/{did}")
async def patch_document(did: str, request: Request):
    d = next((d for d in S["documents"] if d["id"] == did), None)
    if not d:
        return err(404, "Document not found")
    body = await body_json(request)
    for k in ("title", "content", "tags"):
        if body.get(k) is not None:
            d[k] = body[k]
    d["updated_at"] = iso_now()
    return d


@app.delete("/api/documents/{did}")
async def delete_document(did: str):
    S["documents"] = [d for d in S["documents"] if d["id"] != did]
    return {"status": "deleted", "id": did}


# ---------------------------------------------------------------------------
# Events, files, databases
# ---------------------------------------------------------------------------

def event_public(e: Dict[str, Any]) -> Dict[str, Any]:
    """An event as GET /api/events returns it since 2026-09-26: with its
    ``event_metadata`` (parsed) and ``event_type``."""
    t, k = MOCK_EVENT_META.get(e["id"], (None, None))
    meta = e.get("event_metadata")
    if meta is None and t:
        meta = {"type": t, **({"kind": k} if k else {})}
        if t == "mind_wake":
            meta["why"] = e["description"]
    if meta is None and str(e["id"]).startswith("ev-goal-"):
        meta = {"type": "mind_wake", "goal_document_id": e["id"][len("ev-goal-"):], "check_in": "weekly"}
    return {**e, "event_metadata": meta, "event_type": (meta or {}).get("type")}


@app.get("/api/events")
async def list_events(status: Optional[str] = None, sort_order: str = "asc", search: Optional[str] = None, limit: int = 100):
    rows = [e for e in S["events"] if not status or e["status"] == status]
    if search:
        rows = [e for e in rows if search.lower() in e["description"].lower()]
    rows.sort(key=lambda e: e["next_fire_at"] or "", reverse=(sort_order == "desc"))
    return [event_public(e) for e in rows[:limit]]


@app.post("/api/events")
async def create_event(request: Request):
    body = await body_json(request)
    e = md._ev(f"ev-{uuid.uuid4().hex[:8]}", body.get("description") or "Scheduled",
               parse_iso(body.get("scheduled_at")) or now() + timedelta(hours=1),
               recurrence=body.get("recurrence_pattern"), created_by="user")
    S["events"].append(e)
    return e


@app.get("/api/events/{eid}")
async def get_event(eid: str):
    e = next((e for e in S["events"] if e["id"] == eid), None)
    return event_public(e) if e else err(404, "Event not found")


@app.patch("/api/events/{eid}")
async def patch_event(eid: str, request: Request):
    e = next((e for e in S["events"] if e["id"] == eid), None)
    if not e:
        return err(404, "Event not found")
    body = await body_json(request)
    for k in ("status", "description", "next_fire_at", "recurrence_pattern"):
        if k in body:
            e[k] = body[k]
    e["updated_at"] = iso_now()
    return e


@app.delete("/api/events/{eid}")
async def delete_event(eid: str):
    S["events"] = [e for e in S["events"] if e["id"] != eid]
    return {"status": "deleted"}


@app.get("/api/files")
async def list_files(category: Optional[str] = None, source: Optional[str] = None, limit: int = 10, offset: int = 0):
    rows = [f for f in S["files"] if (not category or f["category"] == category) and (not source or f["source"] == source)]
    return {"files": rows[offset: offset + limit], "pagination": {"limit": limit, "offset": offset, "total": len(rows)}}


@app.get("/api/files/{fid}")
async def get_file(fid: str):
    f = next((f for f in S["files"] if f["id"] == fid), None)
    return f if f else err(404, "File not found")


@app.patch("/api/files/{fid}")
async def patch_file(fid: str, request: Request):
    f = next((f for f in S["files"] if f["id"] == fid), None)
    if not f:
        return err(404, "File not found")
    body = await body_json(request)
    for k in ("description", "tags", "category"):
        if k in body:
            f[k] = body[k]
    return f


@app.delete("/api/files/{fid}")
async def delete_file(fid: str):
    S["files"] = [f for f in S["files"] if f["id"] != fid]
    return {"status": "deleted", "id": fid}


def svg_art(label: str, hue: int, w: int = 1200, h: int = 800) -> bytes:
    h2 = (hue + 40) % 360
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="hsl({hue},45%,38%)"/><stop offset="1" stop-color="hsl({h2},55%,18%)"/></linearGradient></defs>'
            f'<rect width="100%" height="100%" fill="url(#g)"/>'
            f'<circle cx="{w * 0.72:.0f}" cy="{h * 0.3:.0f}" r="{h * 0.16:.0f}" fill="hsla({h2},80%,85%,.35)"/>'
            f'<text x="48" y="{h - 56}" font-family="-apple-system,Helvetica,sans-serif" font-size="{h // 16}" '
            f'fill="rgba(255,255,255,.85)">{label}</text></svg>').encode()


def tiny_wav(seconds: float = 1.2, rate: int = 8000) -> bytes:
    n = int(seconds * rate)
    samples = b"".join(struct.pack("<h", int(1200 * math.sin(2 * math.pi * 220 * i / rate) * (1 - i / n))) for i in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(samples)) + samples)


def file_body(fid: str) -> tuple:
    if fid in FILE_BLOBS:
        return FILE_BLOBS[fid]
    f = next((f for f in S["files"] if f["id"] == fid), None)
    mime = (f or {}).get("mime_type", "text/plain")
    name = (f or {}).get("filename", fid)
    if mime.startswith("image/"):
        return "image/svg+xml", svg_art(name, abs(hash(fid)) % 360)
    if mime.startswith("audio/"):
        return "audio/wav", tiny_wav()
    if mime == "text/csv":
        return mime, b"item,estimate\ncabin,412\ngas,38\ngroceries,120\nkayaks,70\ndinner,95\n"
    if mime == "application/pdf":
        return mime, b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    return "text/plain; charset=utf-8", f"# {name}\n\n(mock file content)\n".encode()


@app.get("/api/files/{fid}/download")
async def download_file(fid: str):
    mime, data = file_body(fid)
    return Response(content=data, media_type=mime)


@app.get("/api/databases")
async def list_databases():
    return S["databases"]["dbs"]


@app.get("/api/databases/{name}")
async def get_database(name: str):
    d = next((d for d in S["databases"]["dbs"] if d["name"] == name), None)
    return dict(d, tables=S["databases"]["tables"].get(name, [])) if d else err(404, "Database not found")


@app.delete("/api/databases/{name}")
async def delete_database(name: str):
    S["databases"]["dbs"] = [d for d in S["databases"]["dbs"] if d["name"] != name]
    return {"status": "deleted", "name": name}


@app.get("/api/databases/{name}/tables")
async def database_tables(name: str):
    return S["databases"]["tables"].get(name, [])


@app.get("/api/databases/{name}/tables/{table}/columns")
async def database_columns(name: str, table: str):
    return S["databases"]["columns"].get((name, table), [])


# ---------------------------------------------------------------------------
# Skills, custom MCP, voice, gmail, alexa, push, wishlist
# ---------------------------------------------------------------------------

@app.get("/api/skills")
async def get_skills():
    return {"skills": S["skills"], "last_reload": md.iso(md.ago(hours=26))}


@app.patch("/api/skills/{sid}")
async def set_skill(sid: str, request: Request):
    body = await body_json(request)
    s = next((s for s in S["skills"] if s["id"] == sid), None)
    if not s:
        return err(404, "Skill not found")
    s["enabled"] = bool(body.get("enabled"))
    s["status"] = "connected" if s["enabled"] else "disabled"
    s["status_message"] = None
    return s


@app.post("/api/skills/reload")
async def reload_skills():
    await asyncio.sleep(0.6)
    return {"skills": S["skills"], "last_reload": iso_now()}


@app.get("/api/custom-mcp")
async def custom_mcp():
    return {"servers": S["custom_mcp"]}


@app.patch("/api/custom-mcp/{sid}")
async def custom_mcp_patch(sid: str, request: Request):
    body = await body_json(request)
    s = next((s for s in S["custom_mcp"] if s["id"] == sid), None)
    if not s:
        return err(404, "Server not found")
    s["enabled"] = bool(body.get("enabled"))
    s["status"] = "connected" if s["enabled"] else "disabled"
    return s


@app.post("/api/custom-mcp/{sid}/restart")
async def custom_mcp_restart(sid: str):
    s = next((s for s in S["custom_mcp"] if s["id"] == sid), None)
    if not s:
        return err(404, "Server not found")
    await asyncio.sleep(0.8)
    return s


@app.delete("/api/custom-mcp/{sid}")
async def custom_mcp_delete(sid: str):
    S["custom_mcp"] = [s for s in S["custom_mcp"] if s["id"] != sid]
    return {"status": "removed", "server_id": sid}


@app.get("/api/voice/status")
async def voice_status():
    return {"enabled": True, "status": "connected", "status_message": "ElevenLabs ready (mock)",
            "metadata": {"voice_id": "mock-voice"}}


@app.post("/api/voice/transcribe")
async def voice_transcribe():
    await asyncio.sleep(0.5)
    return {"text": "What's left on my list for today?", "language": "en"}


@app.post("/api/voice/synthesize")
async def voice_synthesize():
    return Response(content=tiny_wav(0.6), media_type="audio/wav")


@app.get("/api/gmail/status")
async def gmail_status():
    return {"configured": True, "email_address": "owner@example.com", "token_valid": True}


@app.get("/api/gmail/oauth/url")
async def gmail_oauth_url():
    return {"url": "http://localhost:3100/settings/gmail?mock_oauth=1"}


@app.post("/api/gmail/disconnect")
async def gmail_disconnect():
    return {"status": "disconnected"}


@app.get("/api/alexa/status")
async def alexa_status():
    return {"outbound": {"configured": True, "healthy": True, "ha_url": "http://homeassistant.local:8123", "device_count": 2},
            "inbound": {"configured": False, "skill_id": ""},
            "devices": [{"entity_id": "media_player.kitchen_echo", "friendly_name": "Kitchen Echo", "state": "idle"},
                        {"entity_id": "media_player.office_echo", "friendly_name": "Office Echo", "state": "idle"}],
            "recent_announcements": [{"id": "an-1", "message": "Biscuit's meds are due", "device": "Kitchen Echo",
                                      "timestamp": md.iso(md.ago(days=1, hours=1)), "success": True}]}


@app.post("/api/alexa/test")
async def alexa_test():
    await asyncio.sleep(0.4)
    return {"success": True, "message": "Announced on Kitchen Echo (mock)"}


VAPID = "BDummyMockVapidKeyForHarness0000000000000000000000000000000000000000000000000000000000"


@app.get("/api/push/status")
async def push_status():
    return {"configured": True, "subscriptionCount": 2, "vapidPublicKey": VAPID}


@app.get("/api/push/vapid-key")
async def push_vapid():
    return {"vapidPublicKey": VAPID}


@app.post("/api/push/subscribe")
@app.post("/api/push/unsubscribe")
@app.post("/api/push/ack")
@app.post("/api/push/snooze")
async def push_ok(request: Request):
    return {"ok": True, "status": "ok"}


@app.post("/api/push/test")
async def push_test():
    return {"ok": True, "sent": 2, "failed": 0, "message": "Test notification sent (mock — nothing left this machine)"}


@app.get("/api/push/log")
async def push_log():
    return {"pushes": [
        {"id": "p-771", "source": "mind", "title": "Dog food is on the porch — rain in 20 min", "body": "", "url": "/chat?c=t-push-package",
         "sent_at": md.iso(md.ago(hours=4)), "opened_at": md.iso(md.ago(hours=3, minutes=55)), "snoozed_until": None},
        {"id": "p-768", "source": "approval", "title": "Send Jordan \"Sunday works for us\"?", "body": "", "url": "/today",
         "sent_at": md.iso(md.ago(hours=1, minutes=2)), "opened_at": None, "snoozed_until": None},
        {"id": "p-760", "source": "codex_limit", "title": "I'm at 85% of the week's quota", "body": "", "url": "/settings/usage",
         "sent_at": md.iso(md.ago(days=1, hours=2)), "opened_at": None, "snoozed_until": None},
        {"id": "p-752", "source": "voice_handoff", "title": "Done: the cabin packing list is in Documents", "body": "", "url": "/chat?c=c-cabin-weekend",
         "sent_at": md.iso(md.ago(days=2, hours=5)), "opened_at": md.iso(md.ago(days=2, hours=4)), "snoozed_until": None},
    ]}


@app.get("/api/wishlist")
async def get_wishlist(status: Optional[str] = None):
    return {"items": [w for w in S["wishlist"] if not status or w["status"] == status]}


@app.post("/api/wishlist")
async def create_wish(request: Request):
    body = await body_json(request)
    if not (body.get("title") or "").strip():
        return err(400, "title is required")
    w = {"id": f"wish-{uuid.uuid4().hex[:6]}", "title": body["title"], "why": body.get("why") or "",
         "detail": body.get("detail"), "status": "open", "priority": body.get("priority") or 2, "source": "owner",
         "conversation_id": None, "times_wanted": 1, "created_at": iso_now(), "updated_at": iso_now()}
    S["wishlist"].insert(0, w)
    return w


@app.patch("/api/wishlist/{wid}")
async def patch_wish(wid: str, request: Request):
    w = next((w for w in S["wishlist"] if w["id"] == wid), None)
    if not w:
        return err(404, "Wish not found")
    body = await body_json(request)
    for k in ("status", "title", "why", "detail", "priority"):
        if k in body:
            w[k] = body[k]
    w["updated_at"] = iso_now()
    return w


@app.delete("/api/wishlist/{wid}")
async def delete_wish(wid: str):
    S["wishlist"] = [w for w in S["wishlist"] if w["id"] != wid]
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

HB_KEYS = ("id", "source", "event_type", "sender", "contact_name", "chat_identifier", "chat_name", "summary",
           "raw_data", "is_from_user", "created_at", "triage_status", "briefed")


@app.get("/api/heartbeat/status")
async def heartbeat_status():
    st = S["heartbeat_status"]
    st["last_triage_at"] = md.iso(now() - timedelta(minutes=3))
    st["next_triage_at"] = md.iso(now() + timedelta(minutes=1))
    return st


@app.get("/api/heartbeat/events")
async def heartbeat_events(limit: int = 50, offset: int = 0, triage_status: Optional[str] = None, source: Optional[str] = None):
    rows = [e for e in S["heartbeat_events"] if (not triage_status or e["triage_status"] == triage_status)
            and (not source or e["source"] == source)]
    return [{k: e[k] for k in HB_KEYS} for e in rows[offset: offset + limit]]


@app.get("/api/heartbeat/triage")
async def heartbeat_triage(limit: int = 20):
    return S["triage_cycles"][:limit]


@app.post("/api/heartbeat/triage/run")
async def heartbeat_triage_run():
    return {"ok": True}


@app.get("/api/heartbeat/recent-senders")
async def heartbeat_recent_senders(limit: int = 50):
    seen, out = set(), []
    for e in S["heartbeat_events"]:
        key = e.get("chat_identifier")
        if key and key not in seen and not e["is_from_user"]:
            seen.add(key)
            out.append({"identifier": key, "label": e.get("chat_name") or e.get("contact_name") or key})
    return out[:limit]


@app.patch("/api/heartbeat/config")
async def heartbeat_config(request: Request):
    body = await body_json(request)
    st = S["heartbeat_status"]
    for k, v in body.items():
        st[k] = v
        for track in ("imessage", "calendar", "email", "gmail"):
            if k == f"{track}_enabled":
                st["tracks"][track]["enabled"] = bool(v)
                st["tracks"][track]["status"] = "running" if v else "disabled"
            if k == f"{track}_poll_seconds":
                st["tracks"][track]["poll_seconds"] = v
    return {k: st.get(k) for k in ("enabled", "triage_interval_seconds", "digest_token_cap", "allowed_senders",
                                   "imessage_enabled", "imessage_poll_seconds", "imessage_thread_limit_direct",
                                   "imessage_thread_limit_group", "calendar_enabled", "calendar_poll_seconds",
                                   "calendar_lookahead_minutes", "email_enabled", "email_poll_seconds",
                                   "gmail_enabled", "gmail_poll_seconds", "mind_enabled")}


@app.post("/api/heartbeat/events/{eid}/feedback")
async def heartbeat_feedback_post(eid: str, request: Request):
    body = await body_json(request)
    e = next((e for e in S["heartbeat_events"] if e["id"] == eid), None)
    if not e:
        return err(404, "Event not found")
    fb = {"id": f"hf-{uuid.uuid4().hex[:6]}", "event_id": eid, "verdict": body.get("verdict"),
          "triage_status": e["triage_status"], "contact_key": e.get("contact_name"), "note": body.get("note"),
          "created_at": iso_now()}
    S["heartbeat_feedback"] = [f for f in S["heartbeat_feedback"] if f["event_id"] != eid] + [fb]
    return fb


@app.get("/api/heartbeat/feedback")
async def heartbeat_feedback(limit: int = 200):
    return S["heartbeat_feedback"][-limit:]


# ---------------------------------------------------------------------------
# Orchestrator, proactive, dreaming
# ---------------------------------------------------------------------------

@app.get("/api/orchestrator/status")
async def orch_status():
    tasks = S["orchestrator"]["tasks"]
    return {"config": S["orchestrator"]["config"], "active_count": sum(1 for t in tasks if t["status"] == "running"),
            "recent_tasks": tasks}


@app.get("/api/orchestrator/tasks")
async def orch_tasks(parent_conversation_id: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0):
    rows = [t for t in S["orchestrator"]["tasks"] if (not parent_conversation_id or t["parent_conversation_id"] == parent_conversation_id)
            and (not status or t["status"] == status)]
    return rows[offset: offset + limit]


@app.post("/api/orchestrator/tasks")
async def orch_create(request: Request):
    body = await body_json(request)
    t = {"id": f"task-{uuid.uuid4().hex[:6]}", "parent_conversation_id": body.get("parent_conversation_id") or "",
         "worker_conversation_id": None, "task_description": body.get("task_description") or body.get("task") or "Task",
         "task_type": body.get("task_type") or "worker", "model": "gpt-5.6-terra", "cc_session_id": None, "status": "running",
         "context_mode": "summary", "result_summary": None, "error": None, "timeout_seconds": 600, "started_at": iso_now(),
         "completed_at": None, "created_at": iso_now(), "updated_at": iso_now()}
    S["orchestrator"]["tasks"].insert(0, t)
    return t


@app.get("/api/orchestrator/tasks/{tid}")
async def orch_task(tid: str):
    t = next((t for t in S["orchestrator"]["tasks"] if t["id"] == tid), None)
    return t if t else err(404, "Task not found")


@app.post("/api/orchestrator/tasks/{tid}/cancel")
async def orch_cancel(tid: str):
    t = next((t for t in S["orchestrator"]["tasks"] if t["id"] == tid), None)
    if not t:
        return err(404, "Task not found")
    t["status"] = "cancelled"
    t["completed_at"] = t["updated_at"] = iso_now()
    return t


@app.post("/api/orchestrator/tasks/{tid}/message")
async def orch_message(tid: str, request: Request):
    body = await body_json(request)
    await asyncio.sleep(0.8)
    return {"response": f"(worker) Got it: {str(body.get('message') or '')[:80]}"}


@app.get("/api/orchestrator/tasks/{tid}/events")
async def orch_events(tid: str):
    async def gen():
        for e in [{"event_type": "cc_started", "session_id": "sess-1"},
                  {"event_type": "cc_text", "text": "Checking the booking page…"},
                  {"event_type": "cc_tool_use", "tool_name": "fetch_page_content", "tool_input": '{"url": "https://example.com/ember"}'},
                  {"event_type": "cc_tool_result", "text": "No 7pm table on the 29th yet."}]:
            await asyncio.sleep(0.6)
            yield f"data: {json.dumps(e)}\n\n"
        await asyncio.sleep(30)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/orchestrator/config")
async def orch_config():
    return S["orchestrator"]["config"]


@app.patch("/api/orchestrator/config")
async def orch_config_patch(request: Request):
    S["orchestrator"]["config"].update({k: v for k, v in (await body_json(request)).items() if k in S["orchestrator"]["config"]})
    return S["orchestrator"]["config"]


@app.get("/api/proactive/status")
async def proactive_status():
    p = S["proactive"]
    return {"config": p["config"], "entity_count": len(p["entities"]),
            "open_loop_count": sum(1 for l in p["loops"] if l["status"] == "open"), "wakes": p["wakes"]}


@app.get("/api/proactive/config")
async def proactive_config():
    return S["proactive"]["config"]


@app.patch("/api/proactive/config")
async def proactive_config_patch(request: Request):
    S["proactive"]["config"].update({k: v for k, v in (await body_json(request)).items() if k in S["proactive"]["config"]})
    return S["proactive"]["config"]


@app.get("/api/proactive/open-loops")
async def open_loops(status: Optional[str] = None):
    return [l for l in S["proactive"]["loops"] if not status or l["status"] == status]


@app.patch("/api/proactive/open-loops/{lid}")
async def open_loop_patch(lid: str, request: Request):
    l = next((l for l in S["proactive"]["loops"] if l["id"] == lid), None)
    if not l:
        return err(404, "Open loop not found")
    body = await body_json(request)
    l["status"] = body.get("status") or l["status"]
    if body.get("snooze_until"):
        l["snooze_until"] = body["snooze_until"]
    if l["status"] == "resolved":
        l["resolved_at"] = iso_now()
    return l


@app.get("/api/proactive/entities")
async def entities(entity_type: Optional[str] = None):
    return [e for e in S["proactive"]["entities"] if not entity_type or e["entity_type"] == entity_type]


@app.get("/api/consolidation/status")
async def consolidation_status():
    return S["dreaming"]["status"]


@app.get("/api/consolidation/config")
async def consolidation_config():
    return S["dreaming"]["status"]


@app.patch("/api/consolidation/config")
async def consolidation_config_patch(request: Request):
    S["dreaming"]["status"].update({k: v for k, v in (await body_json(request)).items() if k in S["dreaming"]["status"]})
    return S["dreaming"]["status"]


@app.post("/api/consolidation/trigger")
async def consolidation_trigger():
    S["dreaming"]["status"]["last_run"] = iso_now()
    S["dreaming"]["status"]["cycle_count"] += 1
    return {"status": "triggered"}


@app.get("/api/consolidation/flags")
async def consolidation_flags(resolved: Optional[bool] = None, limit: int = 50, with_content: bool = False):
    rows = [f for f in S["dreaming"]["flags"] if resolved is None or f["resolved"] == resolved]
    if not with_content:
        rows = [{k: v for k, v in f.items() if k not in ("memory_content", "related_memory_content")} for f in rows]
    return rows[:limit]


@app.get("/api/consolidation/flags/{fid}")
async def consolidation_flag(fid: str):
    f = next((f for f in S["dreaming"]["flags"] if f["id"] == fid), None)
    if not f:
        return err(404, "Flag not found")
    return f


@app.patch("/api/consolidation/flags/{fid}")
async def consolidation_flag_patch(fid: str, request: Request):
    f = next((f for f in S["dreaming"]["flags"] if f["id"] == fid), None)
    if not f:
        return err(404, "Flag not found")
    f["resolved"] = bool((await body_json(request)).get("resolved"))
    f["resolved_at"] = iso_now() if f["resolved"] else None
    return f


@app.post("/api/consolidation/flags/{fid}/resolve")
async def consolidation_flag_resolve(fid: str, request: Request):
    f = next((f for f in S["dreaming"]["flags"] if f["id"] == fid), None)
    if not f:
        return err(404, "Flag not found")
    f["resolved"], f["resolved_at"] = True, iso_now()
    f["description"] += f" (kept side {(await body_json(request)).get('keep', 'a')})"
    return f


@app.get("/api/consolidation/connections")
async def consolidation_connections(limit: int = 50, with_content: bool = False):
    rows = S["dreaming"]["connections"][:limit]
    if not with_content:
        return rows
    text = {m["id"]: m.get("content") for m in S.get("memories", []) if isinstance(m, dict) and "id" in m}
    return [{**c, "memory_a_content": text.get(c["memory_id_a"]), "memory_b_content": text.get(c["memory_id_b"])} for c in rows]


# ---------------------------------------------------------------------------
# Hub, display, screen
# ---------------------------------------------------------------------------

@app.get("/api/hub/board")
async def hub_board():
    return S["board"]


def _find_board_item(item_id: str):
    for pet in S["board"]["pets"]:
        for med in pet["meds"]:
            if med["id"] == item_id:
                return pet, "med", med
        for window, feed in pet["feedings"].items():
            if feed["item_id"] == item_id or item_id in feed["bundled_item_ids"]:
                return pet, "feeding", feed
    return None, None, None


def _recompute_alerts() -> None:
    S["board"]["alerts"] = [{"pet": p["name"], "item": m["name"], "minutes_late": m["minutes_late"]}
                            for p in S["board"]["pets"] for m in p["meds"] if m["status"] == "overdue"]


@app.post("/api/hub/care/{item_id}/log")
async def hub_log(item_id: str, request: Request):
    body = await body_json(request)
    pet, kind, entry = _find_board_item(item_id)
    if not entry:
        return err(404, "Care item not found")
    who = body.get("given_by") or "Ellis"
    clock = now().astimezone(TZ).strftime("%-I:%M") + ("a" if now().astimezone(TZ).hour < 12 else "p")
    # Remember the state before the log so an undo restores it (the real
    # board recomputes overdue/late from the schedule; the fixture cannot).
    entry.setdefault("_before_log", {k: entry.get(k) for k in ("status", "by", "at_local", "minutes_late", "done")})
    if kind == "med":
        entry.update(status=body.get("status") or "given", by=who, at_local=clock, minutes_late=None)
    else:
        entry.update(done=True, by=who, at_local=clock)
    _recompute_alerts()
    return {"ok": True, "logged": [item_id], "board": S["board"]}


@app.delete("/api/hub/care/{item_id}/log")
async def hub_undo(item_id: str):
    pet, kind, entry = _find_board_item(item_id)
    if not entry:
        return err(404, "Care item not found")
    before = entry.pop("_before_log", None)
    if before:
        entry.update({k: v for k, v in before.items() if k in entry or v is not None})
    elif kind == "med":
        entry.update(status="due", by=None, at_local=None)
    else:
        entry.update(done=False, by=None, at_local=None)
    _recompute_alerts()
    return {"ok": True}


# Who lives at the hub (EDWARD_HOUSEHOLD on a real backend, owner first);
# GET and POST /api/hub/who both carry it as "people", as the real API does.
HUB_PEOPLE = ["Ellis", "Rowan"]


@app.get("/api/hub/who")
async def hub_who_get():
    return {**S["hub_who"], "people": list(HUB_PEOPLE)}


@app.post("/api/hub/who")
async def hub_who(request: Request):
    body = await body_json(request)
    now = iso_now()
    S["hub_who"] = {"person": body.get("person"), "since": now, "last_seen": now}
    return {"ok": True, **S["hub_who"], "people": list(HUB_PEOPLE)}


@app.get("/api/hub/lists")
async def hub_lists():
    return {"lists": S["hub_lists"], "names": list(S["hub_lists"].keys())}


@app.post("/api/hub/lists/{name}/items")
async def hub_list_add(name: str, request: Request):
    body = await body_json(request)
    item = {"id": f"li-{uuid.uuid4().hex[:6]}", "list_name": name, "text": body.get("text") or "", "done": False,
            "created_by": body.get("created_by"), "done_by": None}
    S["hub_lists"].setdefault(name, []).insert(0, item)
    return item


def _list_item(iid: str):
    for items in S["hub_lists"].values():
        for it in items:
            if it["id"] == iid:
                return items, it
    return None, None


@app.patch("/api/hub/lists/items/{iid}")
async def hub_list_patch(iid: str, request: Request):
    items, it = _list_item(iid)
    if not it:
        return err(404, "Item not found")
    body = await body_json(request)
    it["done"] = bool(body.get("done"))
    it["done_by"] = body.get("done_by") if it["done"] else None
    return it


@app.delete("/api/hub/lists/items/{iid}")
async def hub_list_delete(iid: str):
    items, it = _list_item(iid)
    if it:
        items.remove(it)
    return {"ok": True}


@app.get("/api/hub/health")
async def hub_health():
    def comp(label, status, detail=None, pid=None, pct=None):
        return {"label": label, "status": status, "detail": detail, "pid": pid, "exit": 0 if pid else None, "pct": pct}
    return {
        "overall": "repaired",
        "watchdog": {"status": "repaired", "stale": False, "age_minutes": 3, "checked_at": md.iso(now() - timedelta(minutes=3)),
                     "problems": [], "repairs": ["graph-worker restarted (exit 1) at 04:10"]},
        "agents": [comp("Backend", "healthy", "uvicorn :8000", 4211), comp("Frontend", "healthy", "next start :3000", 4302),
                   comp("Graph worker", "repaired", "restarted 04:10", 5120), comp("Tunnel", "healthy", "edward-tunnel (http2)", 612)],
        "components": [comp("Postgres", "healthy", "16.4, 3.1 GB"), comp("Disk", "stale", "21 GB free of 228 GB", pct=91),
                       comp("Backups", "healthy", "last dump 03:30, 14 kept"), comp("Scheduler", "healthy", "stamped 12s ago")],
        "host": "my-mac.local", "checked_at": iso_now(),
    }


@app.get("/api/hub/settings")
async def hub_settings():
    return S["hub_settings"]


@app.patch("/api/hub/settings")
async def hub_settings_patch(request: Request):
    S["hub_settings"].update(await body_json(request))
    return S["hub_settings"]


@app.get("/api/hub/photos")
async def hub_photos(seed: Optional[int] = None):
    return {"photos": S["photos"], "skipped_unsupported": 2, "dir": "~/edward-photos", "dir_present": True}


@app.post("/api/hub/photos")
async def hub_photos_upload(request: Request):
    form = await request.form()
    saved = []
    for f in form.getlist("files"):
        name = re.sub(r"[^A-Za-z0-9._-]", "-", getattr(f, "filename", "photo.jpg") or "photo.jpg")
        converted = name.lower().endswith(".heic")
        if converted:
            name = name[:-5] + ".jpg"
        S["photos"].insert(0, name)
        saved.append({"ok": True, "name": name, "original": getattr(f, "filename", name), "converted": converted})
    return {"saved": saved, "rejected": [], "saved_count": len(saved), "converted_count": sum(1 for s in saved if s["converted"])}


@app.get("/api/hub/photos/{name}")
async def hub_photo(name: str):
    return Response(content=svg_art(name.rsplit(".", 1)[0].replace("-", " "), abs(hash(name)) % 360),
                    media_type="image/svg+xml")


@app.delete("/api/hub/photos/{name}")
async def hub_photo_delete(name: str):
    S["photos"] = [p for p in S["photos"] if p != name]
    return {"ok": True}


@app.get("/api/hub/canvas")
async def hub_canvas():
    return S["hub_canvas"]


@app.delete("/api/hub/canvas")
async def hub_canvas_delete(by: Optional[str] = None):
    S["hub_canvas"] = None
    return {"ok": True}


def _local_clock(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


@app.get("/api/display/calendar")
async def display_calendar():
    base = now().astimezone(TZ).replace(minute=0, second=0, microsecond=0)
    items = [("Standup", -8, 0.5, "Zoom", "work"), ("1:1 with Kai", 1, 0.5, "Zoom", "work"),
             ("Focus: budget review", 2, 1, "", "focus"), ("Rowan lands — UA 1442", 3, 0.5, "Arrivals door 3", "personal"),
             ("Vet call (Biscuit)", 16, 0.25, "", "personal")]
    events = []
    for i, (title, h, dur, where, kind) in enumerate(items):
        start = base + timedelta(hours=h)
        events.append({"id": f"cal-{i}", "title": title, "start": md.iso(start), "end": md.iso(start + timedelta(hours=dur)),
                       "where": where, "kind": kind, "all_day": False})
    events.insert(0, {"id": "cal-allday", "title": "Rowan's sister in town", "start": md.iso(base.replace(hour=0)),
                      "end": md.iso(base.replace(hour=0) + timedelta(days=1)), "where": "", "kind": "personal", "all_day": True})
    return {"source": "apple_calendar", "fetched_at": iso_now(), "events": events}


@app.get("/api/display/vitals")
async def display_vitals():
    return {"host": "my-mac.local", "uptime_seconds": 9 * 86400 + 4 * 3600, "cpu_pct": 17.5, "mem_pct": 71.2,
            "disk_pct": 91.0, "net_kbps": 142.0, "model": S["settings"]["model"], "tokens_in_today": 3_912_004,
            "tokens_out_today": 98_220, "cost_today": 0.0, "convos_today": 14, "memory_count": 4843, "docs_count": len(S["documents"])}


@app.get("/api/display/workers")
async def display_workers():
    tasks = S["orchestrator"]["tasks"]
    tone = {"running": "info", "completed": "ok", "failed": "danger", "cancelled": "mute"}
    return {"running_count": sum(1 for t in tasks if t["status"] == "running"), "total": len(tasks),
            "workers": [{"name": t["task_description"][:40], "status": t["status"], "tone": tone.get(t["status"], "mute"),
                         "last": t["updated_at"]} for t in tasks]}


@app.get("/api/display/recents")
async def display_recents():
    return {"items": [
        {"t": md.iso(md.ago(minutes=50)), "verb": "drafted", "obj": "a reply to Jordan (held)", "status": "warn"},
        {"t": md.iso(md.ago(hours=1, minutes=5)), "verb": "replied", "obj": "to Parker", "status": "ok"},
        {"t": md.iso(md.ago(hours=4)), "verb": "pushed", "obj": "dog food on the porch", "status": "ok"},
        {"t": md.iso(md.ago(hours=10)), "verb": "saved", "obj": "Research: quiet dishwashers", "status": "ok"},
    ]}


@app.get("/api/display/memory_ticker")
async def display_memory_ticker():
    return {"total": 4843, "items": [m["content"] for m in S["memories"][:8]]}


@app.get("/api/display/now_thinking")
async def display_now_thinking():
    # Production reads both this and the signals' worker count from the running orchestrator tasks.
    if (S.get("display_signals_override") or {}).get("active_orchestrator_tasks") == 0:
        return {"item": None}
    return {"item": {"id": "nt-1", "title": "Watching Ember for a 7pm table", "detail": "Checks twice a day until the 29th",
                     "started_at": md.iso(md.ago(minutes=3)), "elapsed_seconds": 180,
                     "steps": [{"label": "Open the booking page", "state": "done"}, {"label": "Look for 6:30–7:30", "state": "active"},
                               {"label": "Tell Ellis", "state": "pending"}],
                     "sources": ["Ember's booking page"], "confidence": 0.7}}


@app.get("/api/display/alerts")
async def display_alerts():
    items = [{"level": "warn", "t": md.iso(md.ago(minutes=42)), "text": "Biscuit's 18:00 meds are 42 min late"}]
    items.append({"level": "info", "t": md.iso(md.ago(minutes=50)), "text": "A send to Jordan is waiting for your approval"})
    return {"items": items}


@app.get("/api/display/signals")
async def display_signals():
    body = {"pending_interrupt": None, "voice_state": "idle", "active_chat_streams": len(PROCESSING),
            "active_orchestrator_tasks": 1, "recent_alert_at": md.iso(md.ago(minutes=42)), "calendar_near_fire_at": None,
            "fetched_at": iso_now()}
    body.update(S.get("display_signals_override") or {})
    return body


@app.post("/__mock/display_signals")
async def mock_display_signals(request: Request):
    """Override /api/display/signals fields for /screen and hub testing, e.g.
    ``{"active_orchestrator_tasks": 0, "voice_state": "speaking"}``; ``{"clear": true}``
    (or /__mock/reset) removes it. ``"recent_alert_at": "now"`` stamps the current time."""
    body = await body_json(request)
    if body.pop("clear", False):
        S["display_signals_override"] = {}
    for key in ("recent_alert_at", "calendar_near_fire_at"):
        if body.get(key) == "now":
            body[key] = iso_now()
    S["display_signals_override"] = {**(S.get("display_signals_override") or {}), **body}
    return {"ok": True, "override": S["display_signals_override"]}


@app.get("/api/display/settings")
async def display_settings():
    return S["display_settings"]


@app.patch("/api/display/settings")
async def display_settings_patch(request: Request):
    S["display_settings"].update(await body_json(request))
    return S["display_settings"]


# The stage (2026-09-28). The scene comes placed, as screen_service.place_scene
# would send it (rects in 24×12 cells). "His own scene" mirrors the canvas board.
STAGE = {"cols": 24, "rows": 12}


def stage_scene() -> Dict[str, Any]:
    return {
        "pieces": [
            {"id": "title", "kind": "words", "size": "l", "enter": "fade", "place": "top-left",
             "rect": {"x": 1, "y": 1, "w": 14, "h": 3}, "title": "4:12 pm · the house has been still since noon.",
             "text": "A quiet Monday afternoon."},
            {"id": "photo", "kind": "image", "size": "m", "enter": "fade", "place": "top-right",
             "rect": {"x": 15, "y": 1, "w": 8, "h": 5}, "title": "From your photos · Aug 2026",
             "source": "/api/hub/photos/lake-dogs.jpg"},
            {"id": "thought", "kind": "note", "size": "m", "enter": "rise", "place": {"x": 1, "y": 8},
             "rect": {"x": 1, "y": 8, "w": 9, "h": 3}, "title": "A thought",
             "body": "While it's quiet I'm reading up on what you asked about Saturday. "
                     "I'll have a short summary for you tonight."},
            {"id": "later", "kind": "list", "size": "m", "enter": "fade", "place": "bottom-right",
             "rect": {"x": 15, "y": 7, "w": 8, "h": 4}, "title": "Later today",
             "items": [{"label": "Biscuit's evening meds", "detail": "6:00 pm"}, {"label": "Wet food", "detail": "Twice, whenever"}]},
        ],
        "me": {"at": "on", "rect": {"x": 6, "y": 4, "w": 3, "h": 4}, "on": "thought", "side": "left", "look": "thought"},
        "why": "You're out and nothing needs you.",
        "source": "edward",
        "updated_at": md.iso(now() - timedelta(minutes=2)),
        "updated_by": "edward",
    }


def _stamp(value: Any, key: str) -> Any:
    """Relative times in a /__mock override become real ones: {"ago": s} → at, "ends_in" → ends_at…"""
    if isinstance(value, list):
        return [_stamp(v, key) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: _stamp(v, k) for k, v in value.items()}
    t = now()
    if "ago" in out:
        out["at"] = md.iso(t - timedelta(seconds=float(out.pop("ago"))))
    if "since_ago" in out:
        out["since"] = md.iso(t - timedelta(seconds=float(out.pop("since_ago"))))
    if "started_ago" in out:
        out["started_at"] = md.iso(t - timedelta(seconds=float(out.pop("started_ago"))))
    if "ends_in" in out:
        out["ends_at"] = md.iso(t + timedelta(seconds=float(out.pop("ends_in"))))
    if "for" in out and key == "asked":
        out["until"] = md.iso(parse_iso(out["at"]) + timedelta(seconds=float(out.pop("for"))))
    if "updated_ago" in out:
        out["updated_at"] = md.iso(t - timedelta(seconds=float(out.pop("updated_ago"))))
    return out


@app.get("/api/screen")
async def screen():
    body = {"stage": STAGE, "scene": stage_scene(), "asked": None, "lines": [], "owner_at_mini": False,
            "show": S["screen_show"], "now": iso_now(),
            # legacy keys, one release
            "grid": {"cols": 6, "rows": 4}, "layout": [], "layout_source": "default", "layout_note": None,
            "layout_updated_at": None, "layout_updated_by": None}
    body.update(S.get("screen_override") or {})
    return body


@app.post("/__mock/screen")
async def mock_screen(request: Request):
    """Override /api/screen fields for the stage's screenshots: ``scene``, ``asked``, ``lines``,
    ``owner_at_mini``, ``show``. Relative times: ``{"ago": s}`` → ``at``; a timer's ``started_ago`` /
    ``ends_in``; ``asked: {"ago": s, "for": s}``; a scene's ``updated_ago``. ``{"clear": true}`` removes it."""
    body = await body_json(request)
    if body.pop("clear", False):
        S["screen_override"] = {}
    S["screen_override"] = {**(S.get("screen_override") or {}), **{k: _stamp(v, k) for k, v in body.items()}}
    return {"ok": True, "override": S["screen_override"]}


# His own recent thoughts (round 2): what the lane drifts through when no turn runs.
# Seconds ago, newest last, like services/screen_thoughts.py `recent`.
SAMPLE_RECENT = [
    ("Biscuit's evening meds are at six and nobody has logged them yet — I'll look again at 6:15.", 4 * 3600, "evening", "mind"),
    ("Parker's Saturday plan is still open. Ellis hasn't answered him, and it isn't mine to answer.", 2 * 3600 + 600, "messages", "mind"),
    ("I read up on kayak rentals near the lake. Two places still have a 1 pm slot on Saturday.", 5400, "free", "surplus"),
    ("The Riverside Fest dates are in the project doc now; Rowan has them too.", 3000, "morning", "mind"),
    ("A quiet afternoon. Nothing in the messages needs Ellis before dinner.", 1500, "messages", "mind"),
]


_RECENT_BASE: Dict[str, Any] = {}


def _sample_recent() -> list:
    """Stamped once per mock state (a reset restamps), so each poll sends the same thoughts."""
    key = id(S)
    if _RECENT_BASE.get("state") != key:
        _RECENT_BASE.update(state=key, at=now())
    t = _RECENT_BASE["at"]
    return [{"text": text, "at": md.iso(t - timedelta(seconds=ago)), "about": about, "kind": kind}
            for text, ago, about, kind in SAMPLE_RECENT]


@app.get("/api/screen/live")
async def screen_live():
    live = S.get("screen_live") or {"thinking": None, "folded": []}
    recent = live["recent"] if "recent" in live else _sample_recent()
    return {**live, "recent": recent, "now": iso_now()}


@app.post("/__mock/screen_live")
async def mock_screen_live(request: Request):
    """Set GET /api/screen/live: ``{"thinking": {...} | null, "folded": [...], "recent": [...]}`` with
    relative times (``since_ago``, and ``ago`` on lines, receipts, folds and recent thoughts). Without
    ``recent`` the sample thoughts are served; ``"recent": []`` means none. ``{"clear": true}`` removes it."""
    body = await body_json(request)
    if body.pop("clear", False):
        S["screen_live"] = None
    S["screen_live"] = {"thinking": None, "folded": [], **(S.get("screen_live") or {}),
                        **{k: _stamp(v, k) for k, v in body.items()}}
    return {"ok": True, "live": S["screen_live"]}


@app.delete("/api/screen/show")
async def screen_show_delete(by: Optional[str] = None):
    S["screen_show"] = None
    return {"ok": True}


@app.post("/api/screen/window")
async def screen_window():
    return {"ok": True, "note": "mock: no Chrome was touched"}


@app.get("/api/screen/look")
async def screen_look(at: str = "page"):
    return {"ok": True, "description": "Mock screen: the stage with his scene, his words and his thinking."}


# ---------------------------------------------------------------------------
# Voice websocket (minimal)
# ---------------------------------------------------------------------------

@app.websocket("/api/voice/ws")
async def voice_ws(ws: WebSocket):
    await ws.accept()
    conv_id = None
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                continue  # mic frames: ignored
            try:
                data = json.loads(msg.get("text") or "{}")
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "start_session":
                conv_id = data.get("conversation_id") or f"c-voice-{uuid.uuid4().hex[:6]}"
                await ws.send_json({"type": "session_started", "conversation_id": conv_id, "mode": data.get("mode", "auto"),
                                    "sensitivity": data.get("sensitivity", "normal"), "sample_rate_in": 16000,
                                    "sample_rate_out": 24000})
                await ws.send_json({"type": "state", "state": "listening"})
            elif kind == "commit":
                text = "What's left on my list for today?"
                await ws.send_json({"type": "partial_transcript", "text": "What's left on"})
                await asyncio.sleep(0.3)
                await ws.send_json({"type": "transcript", "text": text})
                await ws.send_json({"type": "state", "state": "thinking"})
                await asyncio.sleep(0.8)
                await ws.send_json({"type": "state", "state": "speaking"})
                reply = "Biscuit's evening meds, then Rowan lands at ten past seven."
                for i, w in enumerate(reply.split(" ")):
                    await ws.send_json({"type": "response_text", "text": w + " ", "is_final": False})
                    await asyncio.sleep(0.08)
                await ws.send_json({"type": "response_text", "text": "", "is_final": True})
                await ws.send_json({"type": "response_done"})
                await ws.send_json({"type": "state", "state": "listening"})
            elif kind == "interrupt":
                await ws.send_json({"type": "state", "state": "listening", "reason": "interrupt"})
            elif kind == "end_session":
                await ws.close()
                break
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---------------------------------------------------------------------------
# Anything else: record it so a tester can see what is not mocked
# ---------------------------------------------------------------------------

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def not_mocked(path: str, request: Request):
    request.state.miss = True
    MISSES.append({"at": iso_now(), "method": request.method, "path": "/api/" + path, "query": request.url.query})
    return err(404, f"mock: /api/{path} is not mocked")


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args()
    if args.port in (8000, 3000):
        raise SystemExit("refusing to bind a production port")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
