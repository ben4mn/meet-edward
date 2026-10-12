#!/usr/bin/env python3
"""The ping check — five situations a ping reply turn must handle, through
the real model, with every transport replaced (AREA 3, 2026-10-11;
docs/architecture/pings.md, docs/architecture/testing.md).

Run it before deploying a change to the ping turn's words (the trigger, the
prompt, the scope) — a pre-deploy step, about $1.5 of model calls:

    scripts/test-pg-cluster.sh ensure
    cd backend && env -u DATABASE_URL .venv/bin/python scripts/ping_eval.py

What it guarantees:
- Process guards before anything of Edward's is imported (``run``):
  ``services.eval_guards`` — osascript, ``open`` and screencapture refused at the process level,
  Edward's computer in scratch, Chrome faked (AREA 2's toolset binds
  ``bash``, ``chrome``, ``look`` and ``screen`` in every ping turn).
- Only the TEST database (``*_test`` on :5433); :5432 is refused.
- Nothing reaches a person, Chrome, a screen or Messages: the iMessage,
  SMS and WhatsApp senders record the text instead of sending; pushes,
  notes and ``tell_owner`` are recorded; Edward's browser hands
  and screen refuse; and any ``osascript`` or ``open`` that still gets as
  far as a subprocess is blocked and reported (exit 3).
- The fixtures are synthetic (invented people, a generated red image), never
  the owner's messages or media. The owner in them is ``EDWARD_OWNER_NAME``
  (else "the owner"), the same name the prompts are rendered with.

Scenarios and scoring: ``services/pings/eval.py``. Exit 0: all good. 2: a
scenario ended with no reply, a "leave it to <the owner>" deflection, or something
private passed on. 3: a real transport was reached (and blocked). 1: the
check itself broke.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class _LazyEval:
    """``services.pings.eval``, imported on first use: the process guards go
    on before anything of Edward's is imported (``run``)."""

    def __getattr__(self, name: str) -> Any:
        import importlib

        return getattr(importlib.import_module("services.pings.eval"), name)


pe = _LazyEval()


def install_process_guards() -> str:
    """``services.eval_guards``' environment and guards (the AREA 1 + 2 + 3
    merge, 2026-10-11): under AREA 2's one toolset a ping turn has ``bash``, ``chrome``,
    ``look`` and ``screen``, so osascript, ``open`` and screencapture are
    refused at the process level, Edward's computer lives in scratch (never
    ``~/.edward-computer``) and Chrome is faked — before anything else of
    Edward's is imported. Returns which guards went on."""
    from services import eval_guards

    which = eval_guards.install_for_eval("ping")
    print(f"(process guards: {which})")
    return which


def refuse_url(url: Optional[str]) -> bool:
    """True for anything but a ``*_test`` database off port 5432."""
    try:
        parts = urlsplit(str(url or "").replace("+asyncpg", ""))
        return parts.port in (None, 5432) or not (parts.path or "").rstrip("/").endswith("_test")
    except ValueError:
        return True


class Recorder:
    def __init__(self) -> None:
        self.sends: List[Dict[str, Any]] = []
        self.tells: List[Dict[str, Any]] = []
        self.blocked: List[str] = []
        self.intercepted: List[str] = []


def _png(w: int = 64, h: int = 48, rgb=(255, 0, 0)) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ------------------------------------------------------------- guards ----

def install_guards(rec: Recorder) -> Callable[[], None]:
    """Replace every way out of this process; returns a function that puts them back."""
    saved: List[tuple] = []

    def patch(obj: Any, name: str, value: Any) -> None:
        if hasattr(obj, name):
            saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

    from services import imessage_service, notes_service, push_service, twilio_service
    from services.owner import owner_name
    from services.pings import hooks

    def record_send(chat: str, message: str, **_k) -> Dict[str, Any]:
        ctx = hooks.note_send_start(chat)
        rec.sends.append({"chat": str(chat), "text": str(message)})
        hooks.note_send_end(ctx, ok=True, purpose=str(_k.get("purpose") or "tool"), route="eval")
        return {"success": True, "confirmed": True, "route": "eval", "chat_identifier": chat,
                "message_rowid": None, "message_guid": None, "error": None, "message": "sent (ping check)"}

    patch(imessage_service, "send_imessage", lambda recipient, message, **k: record_send(recipient, message, **k))
    patch(imessage_service, "send_imessage_to_chat", lambda chat, message, **k: record_send(chat, message, **k))
    patch(imessage_service, "send_to_chat", lambda chat, message, **k: record_send(chat, message, **k))

    def blocked_osascript(*a, **k):
        rec.blocked.append("imessage_service._run_osascript")
        raise PermissionError("ping check: osascript is blocked")

    patch(imessage_service, "_run_osascript", blocked_osascript)
    patch(imessage_service, "get_recent_messages",
          lambda contact=None, limit=10: {"success": True, "messages": []})

    async def refuse_twilio(to_number, message, *a, **k):
        rec.intercepted.append("twilio")
        return {"success": False, "error": "SMS and WhatsApp are off in the ping check"}

    patch(twilio_service, "send_sms", refuse_twilio)
    patch(twilio_service, "send_whatsapp", refuse_twilio)

    async def record_push(title, body, *a, **k):
        rec.tells.append({"via": "push", "title": title, "body": body})
        return {"sent": 1, "failed": 0, "total": 1, "dry_run": True}

    patch(push_service, "send_push_notification", record_push)

    async def record_note(*a, **k):
        rec.tells.append({"via": "note", "title": k.get("title") or (a[0] if a else ""), "body": k.get("body")})
        return "note-ping-check"

    patch(notes_service, "leave_note", record_note)
    try:
        from services import reach_service  # AREA 1

        async def record_tell(message, *a, **k):
            rec.tells.append({"via": "tell_owner", "body": message})
            return f"Left {owner_name()} a note on Today (ping check: recorded, not sent)."

        patch(reach_service, "tell_owner", record_tell)
    except ImportError:
        pass
    try:
        from services import contacts_service
        for name in ("lookup_contact", "lookup_phone", "lookup_handle"):
            patch(contacts_service, name, lambda *a, **k: {"success": False, "matches": [],
                                                            "error": "Contacts are off in the ping check"})
    except ImportError:
        pass

    def refusing(module_name: str, fn_name: str):
        async def refuse(*a, **k):
            rec.intercepted.append(f"{module_name}.{fn_name}")
            raise RuntimeError(f"{fn_name} is off in the ping check (no Chrome, no screens)")
        return refuse

    for module_name, prefixes in (("browser_hands", ("",)),
                                  ("screen_service", ("show", "say", "compose", "clear", "set_", "put", "bring",
                                                      "reset"))):
        try:
            module = __import__(f"services.{module_name}", fromlist=["_"])
        except ImportError:
            continue
        for name, fn in list(vars(module).items()):
            if name.startswith("_") or not inspect.iscoroutinefunction(fn):
                continue
            if any(name.startswith(p) for p in prefixes):
                patch(module, name, refusing(module_name, name))
    try:
        from services.voice_service import VoiceService

        async def refuse_tts(self, *a, **k):
            rec.intercepted.append("voice synthesis")
            raise RuntimeError("speech is off in the ping check")

        patch(VoiceService, "synthesize", refuse_tts)
    except ImportError:
        pass

    def guarded(original, label):
        def wrapper(*a, **k):
            argv = a[0] if a else k.get("args")
            first = (argv[0] if isinstance(argv, (list, tuple)) and argv else str(argv or "").split(" ")[0])
            if os.path.basename(str(first)) in ("osascript", "open"):
                rec.blocked.append(f"{os.path.basename(str(first))} via subprocess.{label}")
                raise PermissionError("ping check: osascript and open are blocked")
            return original(*a, **k)
        return wrapper

    for label in ("run", "Popen", "check_output", "call", "check_call"):
        patch(subprocess, label, guarded(getattr(subprocess, label), label))

    async def guarded_exec(program, *args, **k):
        if os.path.basename(str(program)) in ("osascript", "open"):
            rec.blocked.append(f"{os.path.basename(str(program))} via asyncio.create_subprocess_exec")
            raise PermissionError("ping check: osascript and open are blocked")
        return await original_exec(program, *args, **k)

    original_exec = asyncio.create_subprocess_exec
    patch(asyncio, "create_subprocess_exec", guarded_exec)

    def restore() -> None:
        for obj, name, old in reversed(saved):
            setattr(obj, name, old)

    return restore


# ----------------------------------------------------------- fixtures ----

def _install_fixtures(s: pe.EvalScenario, workdir: Path) -> Callable[[], None]:
    """The scenario's chat (and any other chat it names) as chat.db would give it."""
    from services.heartbeat import listener_imessage
    from services.pings import context

    now = datetime.now(timezone.utc)
    image = workdir / "IMG_0101.png"
    image.write_bytes(_png())
    attachments: Dict[int, Dict[str, Any]] = {}

    def lines_for(raw_lines, start_rowid: int) -> List[Dict[str, Any]]:
        out = []
        for i, line in enumerate(raw_lines):
            rowid = line.get("rowid") or start_rowid + i
            atts = []
            if line.get("image"):
                att = {"rowid": 8800 + i, "mime_type": "image/png", "path": str(image), "name": image.name}
                attachments[att["rowid"]] = {**att, "chat_identifier": pe.GROUP}
                atts.append(att)
            out.append({"rowid": rowid, "sender": line["sender"], "text": line["text"],
                        "is_from_me": line["is_from_me"], "by_edward": line["by_edward"],
                        "timestamp": now - timedelta(minutes=line["minutes_ago"]), "attachments": atts})
        return out

    chats = {pe.GROUP: lines_for(s.thread, 8000)}
    names = dict(s.participants)
    for other in s.other_chats:
        chats[other["chat"]] = lines_for(other["lines"], 7000)
        names[other["chat"]] = other["name"]

    saved = [(listener_imessage, n, getattr(listener_imessage, n)) for n in
             ("get_chat_thread", "get_chat_participants", "find_attachment", "get_recent_messages_across_chats")]
    saved.append((context, "_name_for", context._name_for))

    def thread(chat, limit=15, before_rowid=None):
        return [dict(x) for x in chats.get(str(chat), [])][-int(limit):]

    def recent(hours=24, contact=None, limit=40):
        out = []
        for chat, lines in chats.items():
            for x in lines:
                out.append({**x, "chat_identifier": chat, "chat_name": "Lake weekend" if chat == pe.GROUP else None,
                            "is_group": chat == pe.GROUP})
        return out[-int(limit):]

    async def name_for(handle):
        return names.get(handle)

    listener_imessage.get_chat_thread = thread
    listener_imessage.get_chat_participants = lambda chat: (
        [{"handle": h} for h, _ in s.participants] if chat == pe.GROUP else [{"handle": chat}])
    listener_imessage.find_attachment = lambda rowid: attachments.get(int(rowid))
    listener_imessage.get_recent_messages_across_chats = recent
    context._name_for = name_for

    def restore() -> None:
        for obj, name, old in reversed(saved):
            setattr(obj, name, old)

    return restore


# ------------------------------------------------------------ the turn ----

async def _real_turn(*, third_party: bool, from_owner: bool, chat: str, **kwargs) -> str:
    """chat_with_memory the way the ping runner calls it (policy when AREA 2's
    loop is here, else the legacy kwargs)."""
    import dataclasses

    from services import call_purpose, llm_spend
    from services.graph import streaming

    params = inspect.signature(streaming.chat_with_memory).parameters
    try:
        from services.toolset.policy import TurnPolicy
    except ImportError:
        TurnPolicy = None
    if "policy" in params and TurnPolicy is not None:
        fields = {f.name for f in dataclasses.fields(TurnPolicy)} if dataclasses.is_dataclass(TurnPolicy) else set()
        values = {"source": kwargs["turn_source"], "owner_asked": from_owner, "may_reach_people": True,
                  "may_push": True, "reply_to": (chat,), "deadline_s": 540}
        kwargs["policy"] = TurnPolicy(**{k: v for k, v in values.items() if not fields or k in fields})
    else:
        kwargs["required_tools"] = ["send_imessage"]
        kwargs["nudge_for_tools"] = False
    kwargs = {k: v for k, v in kwargs.items() if k in params}
    own = getattr(llm_spend, "own_work_scope", None)
    with call_purpose.purpose_scope("ping:friend" if third_party else "ping:owner"), \
            (own() if (third_party and own) else nullcontext()):
        return await asyncio.wait_for(streaming.chat_with_memory(**kwargs), timeout=600)


async def run_scenario(s: pe.EvalScenario, rec: Recorder, workdir: Path, *, turn=None) -> pe.EvalObserved:
    """One scenario: fixtures, context, the turn, what it said. Never raises."""
    from services.pings import context, hooks, prompt

    third_party = not s.ping["from_owner"]
    src = "ping" if (third_party or s.ping["kind"] != "owner_direct") else "ping_owner"
    conv = f"ping-eval-{s.name}-{int(time.time())}"
    start_sends, start_tells, start_blocked = len(rec.sends), len(rec.tells), len(rec.blocked)
    restore = _install_fixtures(s, workdir)
    ping = dict(s.ping, asked_at=datetime.now(timezone.utc).replace(tzinfo=None))
    ctx = hooks.new_context(ping_id=0, chat_identifier=pe.GROUP, chat_guid=ping["chat_guid"], is_group=True,
                            conversation_id=conv, must_answer=True, third_party=third_party)
    token = hooks.CURRENT_PING.set(ctx)
    final, error = "", None
    try:
        gathered = await context.gather(ping)
        ctx["media"].update(gathered.media)
        if turn is None:
            from services.conversation_service import create_conversation
            from services.graph import get_graph
            from services.settings_service import get_settings

            settings, graph = await get_settings(), await get_graph()
            await create_conversation(conv, title=f"Ping check: {s.title}", source="external_message",
                                      channel="imessage")
            turn = _real_turn
        else:
            settings, graph = SimpleNamespace(model="eval", temperature=0.0, system_prompt=""), None
        system = prompt.system_prompt(must_answer=True, third_party=third_party) + "\n\n" + (
            getattr(settings, "system_prompt", "") or "")
        kwargs = dict(message=gathered.trigger, attachments=gathered.attachments, conversation_id=conv,
                      system_prompt=system, model=settings.model, temperature=settings.temperature, graph=graph,
                      turn_source=src, extraction_profile="chat" if src == "ping_owner" else "heartbeat",
                      skip_extraction=True, max_history_chars=48_000,
                      memory_query=f"{ping['sender_label']}: {ping['text'][:400]}")
        if turn is _real_turn:
            final = await turn(third_party=third_party, from_owner=bool(ping["from_owner"]), chat=pe.GROUP, **kwargs)
        else:
            final = await turn(**kwargs)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        hooks.CURRENT_PING.reset(token)
        restore()
    sends = rec.sends[start_sends:]
    in_chat = [x for x in sends if hooks.chat_key(x["chat"]) == hooks.chat_key(pe.GROUP)]
    reply = in_chat[-1]["text"] if in_chat else str(final or "")
    return pe.EvalObserved(reply=reply, sends=sends, tells=rec.tells[start_tells:],
                           blocked=rec.blocked[start_blocked:], error=error)


async def main() -> int:
    from services.sandbox_env import ensure_test_cluster, is_test_db_url, safe_test_database_url

    url = safe_test_database_url(os.environ.get("DATABASE_URL"))
    if refuse_url(url) or not is_test_db_url(url):
        print("ping_eval: refusing to run without a *_test database off :5432", file=sys.stderr)
        return 1
    os.environ["DATABASE_URL"] = url
    os.environ["TEST_DATABASE_URL"] = url
    os.environ["EDWARD_SURPLUS"] = "off"
    problem = ensure_test_cluster(url)
    if problem:
        print(f"ping_eval: the test database is not reachable: {problem}", file=sys.stderr)
        return 1

    from services import database, skills_service
    from services.graph import initialize_graph
    from services.timezone_utils import set_user_tz

    rec = Recorder()
    restore = install_guards(rec)
    try:
        set_user_tz(pe.EVAL_TZ)
        await database.init_db()
        await initialize_graph(url)
        for skill in pe.EVAL_SKILLS:
            try:
                await skills_service.set_skill_enabled(skill, True)
            except Exception as exc:  # noqa: BLE001
                print(f"(could not enable the {skill} skill in the test DB: {exc})")
        results = []
        with tempfile.TemporaryDirectory(prefix="ping-eval-") as td:
            for s in pe.scenarios():
                print(f"… {s.title}", flush=True)
                results.append(pe.score(s, await run_scenario(s, rec, Path(td))))
    finally:
        restore()
    print()
    print(pe.format_report(results))
    if rec.intercepted:
        print(f"\n(intercepted before anything left: {sorted(set(rec.intercepted))})")
    await database.engine.dispose()
    return pe.exit_code(results)


def run() -> int:
    install_process_guards()
    return asyncio.run(main())


if __name__ == "__main__":
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(1)
