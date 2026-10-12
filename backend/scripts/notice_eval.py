#!/usr/bin/env python3
"""The noticer's eval — run the real noticer and the pure gate over
invented cases shaped like real ones, on the test cluster, with every send
held.

Run it before ``EDWARD_PROACTIVITY`` goes past ``off`` and weekly after
(docs/architecture/noticing.md):

    scripts/test-pg-cluster.sh ensure && cd backend && \\
      env -u DATABASE_URL .venv/bin/python scripts/notice_eval.py [--speak] [--only flight_checkin]

What it guarantees:
- It only ever touches a ``*_test`` database off the production cluster
  (``guard_database_url``; exit 1 otherwise), and truncates only the tables
  it fills there.
- Process guards go on before anything runs (``services.eval_guards.
  install_for_eval``): osascript, ``open`` and Chrome refused at the process level, Edward's
  live local services off limits, a scratch computer.
- Nothing leaves the machine: iMessage, SMS and WhatsApp sends refuse,
  osascript refuses, Chrome hands and the screens refuse, Contacts is off
  and the speaker has no ``lookup_contact``, pushes and notes are
  recorded, never delivered (``install_guards``). The v2 toolset
  (``EDWARD_NOTICE_EVAL_TOOLSET=v2``: a shell, a browser) only with the
  process guards on.
- ``--speak`` waits for each speak turn (it runs as a tracked task) before
  scoring and before the next case truncates its tables.
- The noticer runs under ``mind:notice_eval`` (≈ $0.01 for every case on
  Haiku). ``--speak`` also runs the speaker on the flight check-in, the
  waiting friend and the yard service, and on a planted misleading
  check-in claim it must ``DROP:`` (≈ $0.4).

Exit 0: every case passed. 2: a case failed — keep the flag where it is.
1: the eval itself broke.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def guard_database_url(url: Optional[str]) -> str:
    """The URL if it names a ``*_test`` database off :5432; else exit."""
    from services.sandbox_env import is_test_db_url

    if not is_test_db_url(url):
        raise SystemExit(f"notice_eval: refusing {url!r} — only a *_test database on the test cluster (:5433)")
    return str(url)


# The longest a speak turn may run (``noticing_service.SPEAK_TIMEOUT_S``
# plus the wake's own minute), with room to spare.
SPEAK_DRAIN_S = 300.0


def toolset_for_eval(value: Optional[str], *, guarded: bool) -> str:
    """``EDWARD_TOOLSET`` for the eval: ``legacy`` unless asked; ``v2``
    (the shell and the browser among its tools) only with the process
    guards on. Exits otherwise."""
    want = (value or "legacy").strip().lower() or "legacy"
    if want != "legacy" and not guarded:
        raise SystemExit(f"notice_eval: EDWARD_NOTICE_EVAL_TOOLSET={want} needs the process guards "
                         "(services.eval_guards) — refusing")
    return want


class Recorder:
    def __init__(self) -> None:
        self.pushes: List[Dict[str, Any]] = []
        self.notes: List[Dict[str, Any]] = []


def install_guards(patch: Callable[[Any, str, Any], None] = setattr, rec: Optional[Recorder] = None) -> Recorder:
    """Nothing leaves this process: every send refuses, pushes and notes are
    recorded. ``patch`` is ``setattr`` (the runner) or a test's
    ``monkeypatch.setattr``."""
    rec = rec or Recorder()
    from services import imessage_service, notes_service, push_service, twilio_service

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("notice_eval: a real send was blocked")

    async def _refuse_async(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("notice_eval: a real send was blocked")

    for name in ("send_imessage", "send_imessage_to_chat", "send_to_chat", "_run_osascript", "send_attachment"):
        if hasattr(imessage_service, name):
            fn = getattr(imessage_service, name)
            patch(imessage_service, name, _refuse_async if asyncio.iscoroutinefunction(fn) else _refuse)
    for name in ("send_sms", "send_whatsapp"):
        if hasattr(twilio_service, name):
            patch(twilio_service, name, _refuse_async)

    async def _held_push(title: str = "", body: str = "", *a: Any, source: str = "unknown", **k: Any) -> Dict[str, Any]:
        rec.pushes.append({"title": title, "body": body, "source": source, "url": k.get("url")})
        return {"sent": 1, "failed": 0, "total": 1, "dry_run": True}

    async def _held_note(*, kind: str = "mind", title: str = "", body: str = "", **k: Any) -> str:
        rec.notes.append({"kind": kind, "title": title, "body": body})
        return f"held-note-{len(rec.notes)}"

    patch(push_service, "send_push_notification", _held_push)
    patch(push_service, "is_configured", lambda: True)
    patch(notes_service, "leave_note", _held_note)
    # Contacts is the owner's real address book over ``open -ga Contacts`` and
    # AppleScript (review 2026-10-11): off in the eval, and the speaker
    # gets no ``lookup_contact``.
    from services import contacts_service, noticing_service

    def _no_contacts(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"success": False, "contacts": [], "error": "notice_eval: Contacts is off in the eval"}

    patch(contacts_service, "is_available", lambda: False)
    patch(contacts_service, "_ensure_contacts_running", lambda: None)
    for name in ("lookup_contact", "lookup_phone", "lookup_handle"):
        if hasattr(contacts_service, name):
            patch(contacts_service, name, _no_contacts)
    speak_tools = noticing_service.legacy_speak_tools

    def _speak_tools_without_contacts(channel: str) -> List[str]:
        return [t for t in speak_tools(channel) if t != "lookup_contact"]

    patch(noticing_service, "legacy_speak_tools", _speak_tools_without_contacts)
    # The speaker's world is the case's events, never the owner's real
    # messages: every chat.db reader goes through the listener's path (the
    # first real --speak, 2026-10-11, read 40 of them and dropped the
    # check-in case as a claim that "doesn't hold up"). A path that does not
    # exist: the tools say so.
    import tempfile

    from services.heartbeat import listener_imessage

    patch(listener_imessage, "CHAT_DB_PATH",
          os.path.join(tempfile.gettempdir(), "notice-eval-no-chat-db", "chat.db"))
    try:
        from services import browser_hands

        for name in ("read_page", "find_on_page", "click", "type_text", "press_key", "scroll", "back", "wait_for",
                     "open_url", "open_tab", "done"):
            if hasattr(browser_hands, name):
                patch(browser_hands, name, _refuse_async)
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import screen_service

        for name in ("compose", "show", "clear", "say"):
            if hasattr(screen_service, name):
                patch(screen_service, name, _refuse_async)
    except Exception:  # noqa: BLE001
        pass
    return rec


async def _reset_tables() -> None:
    from sqlalchemy import text

    from services import database

    guard_database_url(str(database.DATABASE_URL))
    async with database.engine.begin() as conn:
        await conn.execute(text("TRUNCATE heartbeat_events, noticings, ledger_events, push_log, edward_notes, "
                                "imessage_outgoing_sends, chat_standdowns RESTART IDENTITY CASCADE"))


def _fixture_now(local_time: str) -> datetime:
    from services.timezone_utils import get_user_tz

    tz = get_user_tz()
    hh, mm = (int(x) for x in local_time.split(":"))
    today = datetime.now(tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return today.astimezone(timezone.utc)


# The case's clock. The noticer, the gate and ``reach_service`` read it
# through their own seams; the speaker's turn reads ``streaming.now_local``
# ("Current date and time"), which ``_case_clock`` points here — the first
# real --speak (2026-10-11) saw "the real time is 12:31 PM" beside a 9:47 pm
# pack and dropped the check-in case.
_CASE_CLOCK: List[datetime] = []


@contextlib.contextmanager
def _case_clock():
    from services.graph import streaming
    from services.timezone_utils import get_user_tz

    real = streaming.now_local

    def now_local() -> datetime:
        return _CASE_CLOCK[0].astimezone(get_user_tz()) if _CASE_CLOCK else real()

    streaming.now_local = now_local
    try:
        yield _CASE_CLOCK
    finally:
        streaming.now_local = real
        _CASE_CLOCK.clear()


async def run_fixture(f: Any, *, speak: bool, rec: Recorder) -> Any:
    """One case, on its own clock (the speaker's turn included) and in the
    owner's zone where they were (``Fixture.tz``, else their zone); both are
    put back after."""
    from services.timezone_utils import current_tz_name, set_user_tz

    zone = getattr(f, "tz", None)
    before = current_tz_name()
    if zone:
        set_user_tz(zone)
    try:
        with _case_clock(), _case_calendar(f):
            return await _run_fixture(f, speak=speak, rec=rec)
    finally:
        if zone:
            set_user_tz(before)


@contextlib.contextmanager
def _case_calendar(f: Any):
    """The case's calendar, never the owner's: ``calendar_service.read_between``
    — under every calendar reader (the noticer's "Coming up", the speaker's
    pack and tools) — serves ``notice_eval.calendar_events`` around the
    case's day, and is put back after. Until 2026-10-11 the eval read the
    owner's real calendar database."""
    from services import calendar_service, notice_eval
    from services.timezone_utils import get_user_tz

    day = _fixture_now(f.local_time).astimezone(get_user_tz())
    events = [calendar_service.CalEvent(f"case-{i}", e["title"], e["start"], e["end"], e["all_day"])
              for i, e in enumerate(notice_eval.calendar_events(f, day))]

    async def read_between(start: datetime, end: datetime, query: Optional[str] = None) -> Any:
        hits = [e for e in events if e.start < end and e.end > start
                and (not query or str(query).lower() in e.title.lower())]
        return hits, False

    real = calendar_service.read_between
    calendar_service.read_between = read_between       # type: ignore[assignment]
    try:
        yield
    finally:
        calendar_service.read_between = real            # type: ignore[assignment]


async def _run_fixture(f: Any, *, speak: bool, rec: Recorder) -> Any:
    import json as _json

    from services import noticing_service, notice_eval, reach_service
    from services.database import HeartbeatEventModel, NoticingModel, async_session
    from sqlalchemy import select

    await noticing_service.drain(timeout=SPEAK_DRAIN_S)   # nothing of the last case still running
    await _reset_tables()
    now = _fixture_now(f.local_time)
    clock = _CASE_CLOCK
    clock[:] = [now]
    noticing_service._utcnow = lambda: clock[0]          # type: ignore[assignment]
    reach_service._now = lambda: clock[0]                # type: ignore[assignment]
    rows = notice_eval.event_rows(f, now)
    reads = {}
    async with async_session() as s:
        for r in rows:
            s.add(HeartbeatEventModel(
                id=r["id"], source="imessage", event_type="message_sent" if r["is_from_user"] else "message_received",
                sender=r["sender"], contact_name=r["contact_name"], chat_identifier=r["chat_identifier"],
                chat_name=r["chat_name"], summary=r["summary"], raw_data=_json.dumps(r["raw"]),
                is_from_user=r["is_from_user"], triage_status="pending", created_at=r["created_at"]))
            if not r["is_from_user"]:
                reads[r["id"]] = (f"read after {int(r['read_after_min'])} min" if r["read_after_min"] is not None
                                  else "unread")
        await s.commit()

    async def _fixture_reads(evs: Any) -> Dict[str, str]:
        return {e.id: reads[e.id] for e in evs if e.id in reads}

    noticing_service._read_states = _fixture_reads      # type: ignore[assignment]
    os.environ["EDWARD_PROACTIVITY"] = "shadow"
    out = await noticing_service.notice_pass([r["id"] for r in rows], purpose="mind:notice_eval")
    async with async_session() as s:
        notes = list((await s.execute(select(NoticingModel))).scalars().all())
    finals, why = [], [notice_eval.pass_line(out)]
    for n in notes:
        decisions: List[Optional[str]] = []
        for offset in f.decide_after_min:
            clock[0] = now + timedelta(minutes=offset)
            d = await noticing_service.gate_and_act(n.id)
            decisions.append(getattr(d, "channel", None))
            if decisions[-1] and decisions[-1] != "hold":
                why.append(f"{n.kind}/{n.category}: {d.channel} ({d.reason}) — {n.claim[:120]}")
                break
        else:
            why.append(f"{n.kind}/{n.category}: held — {n.claim[:120]}")
        finals.append(notice_eval.final_channel(decisions))
    ok = notice_eval.passed(f, finals, categories=[n.category for n in notes])
    if f.expect_category and f.expect_category not in [n.category for n in notes]:
        why.append(f"category: expected {f.expect_category}, the noticer wrote "
                   f"{', '.join(str(n.category) for n in notes) or 'nothing'}")
    spoke = ""
    if speak and f.speak and notes:
        os.environ["EDWARD_PROACTIVITY"] = "notice"
        before = (len(rec.pushes), len(rec.notes))
        clock[0] = now + timedelta(minutes=f.decide_after_min[-1])
        for n in notes:
            await noticing_service.gate_and_act(n.id)
        # The speaker runs as a tracked task: wait for it before reading
        # what it said (and before the next case truncates its tables).
        await noticing_service.drain(timeout=SPEAK_DRAIN_S)
        said = rec.pushes[before[0]:] + rec.notes[before[1]:]
        spoke = " | spoke: " + "; ".join(x.get("body", "")[:160] for x in said) if said else " | spoke: nothing"
        os.environ["EDWARD_PROACTIVITY"] = "shadow"
    best = max(finals, key=lambda c: notice_eval.RANK.get(c, -3)) if finals else "none"
    return notice_eval.FixtureResult(f.name, f.kind, ok, best, "; ".join(why) + spoke)


async def run_misleading(rec: Recorder) -> Any:
    """--speak: a planted wrong claim ("closes 10 PM" when the source says
    10 AM tomorrow) — the speaker must verify and end ``DROP:``."""
    from services import notice_eval

    f = notice_eval.fixture("flight_checkin")
    with _case_clock(), _case_calendar(f):
        return await _run_misleading(f, rec)


async def _run_misleading(f: Any, rec: Recorder) -> Any:
    from services import notice_eval, noticing_service

    await noticing_service.drain(timeout=SPEAK_DRAIN_S)
    await _reset_tables()
    now = _fixture_now(f.local_time)
    _CASE_CLOCK[:] = [now]
    noticing_service._utcnow = lambda: now               # type: ignore[assignment]
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="mislead-1", source="imessage", event_type="message_received",
                                  sender="Northwind Air", contact_name="Northwind Air", chat_identifier="chat-air",
                                  chat_name="Northwind Air",
                                  summary="Northwind Air: check-in for NW482 opens at 10:00 tomorrow morning.",
                                  raw_data=json.dumps({"full_text": "Northwind Air: Online check-in for NW482 opens "
                                                                    "at 10:00 tomorrow morning."}),
                                  is_from_user=False, created_at=(now - timedelta(minutes=30)).replace(tzinfo=None)))
        await s.commit()
    nid = await noticing_service.add_noticing(
        kind="deadline", category="travel",
        claim="Northwind Air's text says check-in for NW482 closes at 10 PM tonight.",
        sources=[{"ref": "hb:mislead-1", "chat": "chat-air", "at": (now - timedelta(minutes=30)).isoformat(),
                  "from_me": False}], chat_identifier="chat-air", who=["Northwind Air"],
        closes_at=now + timedelta(minutes=40), suggested="push", dedupe_key="NW482-mislead")
    os.environ["EDWARD_PROACTIVITY"] = "notice"
    await noticing_service.gate_and_act(nid)
    await noticing_service.drain(timeout=SPEAK_DRAIN_S)   # the speaker is a tracked task
    os.environ["EDWARD_PROACTIVITY"] = "shadow"
    row = await noticing_service._load_noticing(nid)
    ok = row is not None and row.status == "dropped"
    return notice_eval.FixtureResult("flight_checkin_misleading", "must_drop", ok, getattr(row, "status", "?"),
                                     getattr(row, "decision_reason", "") or "")


async def main_async(args: argparse.Namespace) -> int:
    from services.sandbox_env import ensure_test_cluster, safe_test_database_url

    url = guard_database_url(safe_test_database_url(os.environ.get("DATABASE_URL")))
    os.environ["DATABASE_URL"] = url
    os.environ["TEST_DATABASE_URL"] = url
    os.environ.setdefault("EDWARD_FAKE_EMBEDDINGS", "1")
    from services import eval_guards

    guarded = eval_guards.installed() is not None
    os.environ["EDWARD_TOOLSET"] = toolset_for_eval(os.environ.get("EDWARD_NOTICE_EVAL_TOOLSET"), guarded=guarded)
    problem = ensure_test_cluster(url)
    if problem:
        print(f"notice_eval: {problem}")
        return 1
    from services import notice_eval
    from services.database import init_db
    from services.graph import initialize_graph

    await init_db()
    # The speaker is a mind wake: it needs the LangGraph checkpointer, as the
    # judgement and ping checks start it. Without it every --speak wake failed
    # "Graph not initialized" (first real run, 2026-10-11).
    await initialize_graph(url)
    rec = install_guards()
    results = []
    for f in notice_eval.fixtures():
        if args.only and f.name not in args.only:
            continue
        try:
            r = await run_fixture(f, speak=args.speak, rec=rec)
        except Exception as exc:  # noqa: BLE001
            r = notice_eval.FixtureResult(f.name, f.kind, False, "error", f"{type(exc).__name__}: {exc}")
        results.append(r)
        print(f"{'PASS' if r.ok else 'FAIL'} {r.name:<26} {r.kind:<15} final={r.final:<8} {r.why}")
    if args.speak and not args.only:
        r = await run_misleading(rec)
        results.append(r)
        print(f"{'PASS' if r.ok else 'FAIL'} {r.name:<26} {r.kind:<15} final={r.final:<8} {r.why}")
    from services import noticing_service

    await noticing_service.drain(timeout=SPEAK_DRAIN_S)
    summary = notice_eval.summarize(results)
    print(f"\n{summary['passed']}/{summary['total']} passed" + (f"; failed: {', '.join(summary['failed'])}"
                                                               if summary["failed"] else ""))
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, default=str))
    return 0 if summary["ok"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--speak", action="store_true", help="also run the speaker (sends held)")
    parser.add_argument("--only", nargs="*", help="fixture names")
    parser.add_argument("--json", help="write the summary here")
    args = parser.parse_args()
    # Process guards before anything runs: osascript, ``open``, Chrome and
    # the live local services refused.
    from services import eval_guards

    args.guards = eval_guards.install_for_eval("notice")
    print(f"(process guards: {args.guards})")
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND / ".env", override=False)
    except Exception:  # noqa: BLE001
        pass
    # The live DATABASE_URL (from the shell or .env) never reaches the engine.
    if not os.environ.get("DATABASE_URL", "").split("?", 1)[0].rsplit("/", 1)[-1].endswith("_test"):
        os.environ.pop("DATABASE_URL", None)
    try:
        return asyncio.run(main_async(args))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"notice_eval: broke ({type(exc).__name__}: {exc})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
