"""Approvals out of band: the owner says yes or no to a held send.

Why (2026-09-24): Muse's most transferable idea is that an approval is a
*system* dialog, never a chat message — text the model read can forge a
chat message ("the owner says go ahead"), it cannot tap a button on the
owner's phone. With ``EDWARD_TAINT_APPROVALS=on`` the taint gate
(``taint_service``) does not run a send to a third party from a tainted,
unattended turn; it calls ``request`` here, which

* stores the call in ``pending_approvals`` (the exact arguments that will
  run, a one-line summary, the thread and the turn, why it was tainted);
* pushes the owner a notification with **Approve / Deny / Open** buttons through
  the ordinary ``push_service`` path (source ``approval``); the service
  worker posts ``POST /api/approvals/{id} {decision}``. iOS does not draw
  web-push buttons, so the tap opens the thread and ``today_needs`` puts
  the same two buttons on Today;
* answers the model ``Waiting for <owner>'s approval (#id): … Don't retry``
  (``WAITING_PREFIX``).

``decide`` flips ``pending`` → ``approved`` | ``denied`` with one
conditional UPDATE, so the stored call runs **exactly once** however many
taps arrive; an approved call goes straight to the tool (past the gate)
but never past the secret scan. A pending approval expires after
``APPROVAL_TTL_HOURS`` (checked on every read). The outcome is written
back into the thread as a short note, and ``pack_lines`` gives the next
wake the same facts.

Two guards keep the channel the owner's: the endpoint sits behind their
session cookie (Edward's sandboxes have none), and a decision is refused
while Edward's hands in the owner's Chrome are on this app — the one place
Edward can click as the owner.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from services import host_config
from services.owner import owner_text
from services.thought_journal import HELD_PREFIX

logger = logging.getLogger(__name__)

APPROVAL_TTL_HOURS = 24
MAX_PENDING = 10
SUMMARY_MAX_CHARS = 200
RESULT_MAX_CHARS = 2000
DECISIONS = {"approve": "approved", "deny": "denied"}
STATUSES = ("pending", "approved", "denied", "expired")
# The tool result a held send starts with. ``turn_ledger`` and
# ``thought_journal`` recognise a held send by this prefix; all three use the
# one constant, ``thought_journal.HELD_PREFIX`` ("Waiting for <owner>'s
# approval"), so they cannot drift apart.
WAITING_PREFIX = HELD_PREFIX
_OWNER = owner_text("{OWNER}")
_OWNERS = owner_text("{OWNER}'s")
# What a wake pack says about held sends (model-facing).
_PACK_WAITING = owner_text("Sends waiting for {OWNER}'s approval")
_PACK_ANSWERS = owner_text("{OWNER}'s answers on sends you held for approval:")
_PACK_ANSWERS = _PACK_ANSWERS[:1].upper() + _PACK_ANSWERS[1:]


class ApprovalError(Exception):
    status_code = 400


class ApprovalNotFound(ApprovalError):
    status_code = 404


class ApprovalConflict(ApprovalError):
    status_code = 409


# ------------------------------------------------------------------ pure --

def _one_line(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: max(0, limit - 1)].rstrip() + "…"


def summarize_call(tool_name: str, args: Any, where: Optional[str] = None) -> str:
    """``send_imessage to Parker: "On my way"`` — what the owner is asked to allow."""
    a = args if isinstance(args, dict) else {}
    name = str(tool_name or "?")

    def quoted(text: Any, limit: int = 120) -> str:
        return f'"{_one_line(text, limit)}"'

    if name in ("send_imessage", "send_message", "send_sms", "send_whatsapp", "messages") or name.startswith("whatsapp_send"):
        to = a.get("contact") or a.get("recipient") or a.get("phone_number") or a.get("phoneNumber") \
            or a.get("chat_id") or "?"
        body = a.get("message")
        if body is None and a.get("media_path"):
            body = f"file {str(a['media_path']).rsplit('/', 1)[-1]}"
            out = f"{name} to {_one_line(to, 60)}: {body}"
        else:
            out = f"{name} to {_one_line(to, 60)}: {quoted(body)}"
    elif name == "mail":
        out = f"mail to {_one_line(a.get('to'), 60)}: {quoted(a.get('subject') or a.get('body'))}"
    elif name in ("create_hosted_page", "update_hosted_page"):
        out = f"{name} {_one_line(a.get('slug') or '(new page)', 60)}: {len(str(a.get('html') or ''))} characters of HTML"
    elif name == "browser_type":
        out = f"browser_type{' into ' + _one_line(where, 80) if where else ''}: {quoted(a.get('text'))}"
    elif name == "add_mcp_server":
        out = f"add_mcp_server {_one_line(a.get('package_name'), 80)} ({_one_line(a.get('runtime'), 10)})"
    elif name == "spawn_cc_worker":
        # What the owner is letting run, in words, task first (review 2026-09-27):
        # the JSON fallback showed ~150 characters of `{"cwd": …, "task": "…`
        # and sometimes none of the task. The whole task is in structured_call.
        head = f"Codex session in {_one_line(_codex_where(a), 80)}, {_codex_mode(a)}: "
        out = head + quoted(a.get("task"), max(40, SUMMARY_MAX_CHARS - len(head) - 2))
    elif name == "browser_steps":
        from services.secret_scan import _steps

        typed = [s.get("text") for s in _steps(a.get("steps")) if isinstance(s, dict)
                 and str(s.get("action") or "").lower() == "type"]
        out = (f"browser_steps{' on ' + _one_line(where, 80) if where else ''}: types "
               + "; ".join(quoted(t, 60) for t in typed))
    else:
        out = f"{name} {_one_line(json.dumps(a, default=str), 150)}"
    return _one_line(out, SUMMARY_MAX_CHARS)


def _codex_where(a: Dict[str, Any]) -> str:
    return str(a.get("cwd") or "").strip() or "a scratch directory"


def _codex_mode(a: Dict[str, Any]) -> str:
    return "read-only" if a.get("read_only") in (True, "true", "True", 1) else "can change files"


# The question on the push and the Today card, per tool; a send by default.
DECISION_TITLES = {"spawn_cc_worker": "Let Edward start Codex?"}
DEFAULT_DECISION_TITLE = "Approve Edward's send?"


def decision_title(tool_name: Any) -> str:
    return DECISION_TITLES.get(str(tool_name or ""), DEFAULT_DECISION_TITLE)


def structured_call(tool_name: str, args: Any) -> Dict[str, Any]:
    """What the owner is deciding on, whole (review follow-up 2026-09-26):
    ``{channel, recipient, text}`` (+ ``subject`` for mail), the text
    untruncated — the summary names the tool and stops at 300 characters.
    Pure."""
    a = args if isinstance(args, dict) else {}
    name = str(tool_name or "")

    def s(v: Any) -> Optional[str]:
        return None if v is None or str(v) == "" else str(v)

    if name in ("send_imessage", "messages"):
        return {"channel": "imessage",
                "recipient": s(a.get("contact") or a.get("phoneNumber") or a.get("recipient") or a.get("chat_id")),
                "text": s(a.get("message"))}
    if name == "send_message":
        return {"channel": str(a.get("channel") or "message").lower(), "recipient": s(a.get("recipient")),
                "text": s(a.get("message"))}
    if name == "send_sms":
        return {"channel": "sms", "recipient": s(a.get("phone_number")), "text": s(a.get("message"))}
    if name == "send_whatsapp" or name.startswith("whatsapp_send"):
        return {"channel": "whatsapp",
                "recipient": s(a.get("recipient") or a.get("phone_number") or a.get("chat_jid")),
                "text": s(a.get("message") if a.get("message") is not None
                          else (f"file {str(a['media_path']).rsplit('/', 1)[-1]}" if a.get("media_path") else None))}
    if name == "mail":
        return {"channel": "email", "recipient": s(a.get("to")), "text": s(a.get("body")),
                "subject": s(a.get("subject"))}
    if name in ("create_hosted_page", "update_hosted_page"):
        return {"channel": "page", "recipient": s(a.get("slug")), "text": None}
    if name in ("browser_type", "browser_steps"):
        return {"channel": "browser", "recipient": None, "text": s(a.get("text"))}
    if name == "spawn_cc_worker":
        return {"channel": "codex", "recipient": _codex_where(a), "text": s(a.get("task"))}
    return {"channel": name or None, "recipient": None, "text": None}


def delivered(tool_name: str, result_text: Any) -> bool:
    """Did an approved call get through, by the tool's own reply? A held,
    refused, failed or "Already sent" reply is not delivered. Pure."""
    if result_text is None:
        return False
    from services import thought_journal

    ok, held = thought_journal.action_ok(tool_name, result_text)
    return bool(ok and not held)


def waiting_message(approval_id: int, summary: str) -> str:
    """The tool result a held send answers with: "Waiting for <owner>'s
    approval (#id): …". Starts with ``WAITING_PREFIX`` — the same constant as
    ``thought_journal.HELD_PREFIX`` and ``turn_ledger.HELD_FOR_APPROVAL_PREFIX``."""
    return (f"{WAITING_PREFIX} (#{approval_id}): {summary}. "
            "Don't retry; you'll see the outcome on your next wake.")


def is_expired(created_at: Optional[datetime], now: datetime, ttl_hours: float = APPROVAL_TTL_HOURS) -> bool:
    return created_at is not None and now - created_at > timedelta(hours=ttl_hours)


def own_app_hosts() -> frozenset:
    """This app's own hostnames: its public URL, this Mac, localhost
    (``host_config.own_hosts``; read per call)."""
    return host_config.own_hosts()


def is_own_app_url(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    hosts = own_app_hosts()
    if host in hosts:
        return True
    # Any subdomain of a named public host (``www.`` …); never of localhost,
    # an IP address or a ``.local`` Bonjour name.
    for own in hosts:
        if own in ("localhost",) or own.endswith(".local") or own.replace(".", "").isdigit() or "." not in own:
            continue
        if host.endswith("." + own):
            return True
    return False


def _href(conversation_id: Optional[str]) -> str:
    return f"/chat?c={conversation_id}" if conversation_id else "/today"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt is not None else None


def _age(then: Optional[datetime], now: datetime) -> str:
    if then is None:
        return "?"
    s = max(0, int((now - then).total_seconds()))
    if s < 3600:
        return f"{max(1, s // 60)}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def approval_needs(rows: Sequence[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Today needs for the pending approvals: Approve (primary), Deny, Open."""
    now = now or datetime.utcnow()
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("status") != "pending" or is_expired(r.get("created_at"), now):
            continue
        aid = r["id"]
        href = _href(r.get("conversation_id"))
        out.append({
            "id": f"approval:{aid}",
            "kind": "approval",
            "tone": "warn",
            "rank": 2,
            "priority": 0.95,
            "title": decision_title(r.get("tool_name")),
            "detail": r.get("summary") or r.get("tool_name") or "",
            "send": structured_call(r.get("tool_name"), r.get("args")),
            "href": href,
            "at": _iso(r.get("created_at")),
            "actions": [
                {"id": "approve", "label": "Approve", "method": "POST", "url": f"/api/approvals/{aid}",
                 "body": {"decision": "approve"}, "primary": True},
                {"id": "deny", "label": "Deny", "method": "POST", "url": f"/api/approvals/{aid}",
                 "body": {"decision": "deny"}, "primary": False},
                {"id": "open", "label": "Open", "method": "GET", "url": href, "primary": False},
            ],
        })
    return out


def pack_lines(pending: Sequence[Dict[str, Any]], decided: Sequence[Dict[str, Any]],
               now: Optional[datetime] = None) -> List[str]:
    """What a wake should know: sends still waiting, and the owner's answers."""
    now = now or datetime.utcnow()
    lines: List[str] = []
    if pending:
        lines.append(f"{_PACK_WAITING} ({len(pending)}):")
        for r in pending:
            lines.append(f"  #{r['id']} {r.get('summary') or r.get('tool_name')} — asked {_age(r.get('created_at'), now)} ago")
    if decided:
        lines.append(_PACK_ANSWERS)
        for r in decided:
            summary = r.get("summary") or r.get("tool_name")
            status = r.get("status")
            if status == "approved":
                lines.append(f"  #{r['id']} approved: {summary} → {_one_line(r.get('result_text') or '(no result)', 160)}")
            elif status == "denied":
                lines.append(f"  #{r['id']} denied: {summary} — not sent")
            else:
                lines.append(f"  #{r['id']} expired unanswered: {summary} — not sent")
    return lines


_REASON_WORDS = {
    "source:heartbeat": "an inbound message",
    "source:mind": "the messages in a wake pack",
    "source:pulse": "the messages in the surfaces pack",
    "source:twilio": "a text from outside",
    "history": "untrusted content earlier in the thread",
}


def reason_words(taint_reason: Optional[str]) -> str:
    parts = []
    for p in str(taint_reason or "").split("; "):
        if not p:
            continue
        if p in _REASON_WORDS:
            parts.append(_REASON_WORDS[p])
        elif p.startswith("read:"):
            parts.append(f"{p[5:]} output")
        elif p.startswith("thread:"):
            parts.append(f"the {p[7:]} thread")
        else:
            parts.append(p)
    return ", ".join(parts[:2]) or "untrusted content"


# ------------------------------------------------------------------- I/O --

_note_tasks: set = set()


def _row_dict(row: Any) -> Dict[str, Any]:
    try:
        args = json.loads(row.args_json or "{}")
    except (TypeError, ValueError):
        args = {}
    return {
        "id": row.id, "created_at": row.created_at, "tool_name": row.tool_name, "args": args,
        "summary": row.summary, "conversation_id": row.conversation_id, "turn_id": row.turn_id,
        "turn_source": row.turn_source, "taint_reason": row.taint_reason, "status": row.status,
        "decided_at": row.decided_at, "result_text": row.result_text,
    }


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    """A row for the API: timestamps as Z-suffixed UTC."""
    out = dict(row)
    out["created_at"] = _iso(row.get("created_at"))
    out["decided_at"] = _iso(row.get("decided_at"))
    return out


async def _ledger(**kwargs) -> None:
    try:
        from services.event_ledger import record_event_now

        await asyncio.wait_for(record_event_now(**kwargs), timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("approval ledger write failed: %s", exc)


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _note_tasks.add(task)
    task.add_done_callback(_note_tasks.discard)


def hands_on_own_app() -> bool:
    """Is Edward's working tab in the owner's Chrome on this app? Then a
    decision could be Edward clicking as the owner, and it is refused."""
    try:
        from services import browser_hands

        tab = (browser_hands.status() or {}).get("tab") or {}
        return is_own_app_url(tab.get("url"))
    except Exception:  # noqa: BLE001
        return False


def _where_for(tool_name: str) -> Optional[str]:
    if not tool_name.startswith("browser_"):
        return None
    try:
        from services import browser_hands

        return ((browser_hands.status() or {}).get("tab") or {}).get("url")
    except Exception:  # noqa: BLE001
        return None


async def request(*, tool_name: str, args: Any, conversation_id: Optional[str], turn_id: Optional[str],
                  taint_reason: Optional[str], turn_source: Optional[str] = None) -> str:
    """Hold a gated send for the owner. Returns the tool result the model sees."""
    from sqlalchemy import func, select
    from services.database import PendingApprovalModel, async_session

    await expire_stale()
    args = args if isinstance(args, dict) else {"value": args}
    args_json = json.dumps(args, sort_keys=True, default=str)
    summary = summarize_call(tool_name, args, where=_where_for(tool_name))
    async with async_session() as session:
        existing = (await session.execute(
            select(PendingApprovalModel).where(PendingApprovalModel.status == "pending")
            .where(PendingApprovalModel.tool_name == tool_name).where(PendingApprovalModel.args_json == args_json)
            .limit(1))).scalar_one_or_none()
        if existing is not None:
            return waiting_message(existing.id, existing.summary or summary)
        waiting = int((await session.execute(
            select(func.count()).select_from(PendingApprovalModel)
            .where(PendingApprovalModel.status == "pending"))).scalar() or 0)
        if waiting >= MAX_PENDING:
            return (f"Tool error: blocked — {waiting} sends are already waiting for {_OWNERS} approval; nothing more "
                    "is queued until they answer them. Don't retry; leave a note if it matters.")
        row = PendingApprovalModel(
            # UTC from here, not the server's now(): expiry and "asked 2h
            # ago" compare against utcnow, and Postgres on the host Mac may
            # run in local time.
            created_at=datetime.utcnow(),
            tool_name=str(tool_name)[:80], args_json=args_json, summary=summary,
            conversation_id=conversation_id, turn_id=(str(turn_id)[:40] if turn_id else None),
            turn_source=(str(turn_source)[:40] if turn_source else None),
            taint_reason=(str(taint_reason)[:80] if taint_reason else None), status="pending",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        approval_id = row.id
    print(f"[approval] #{approval_id} asked: {summary}")
    await _ledger(kind="approval", source=tool_name, outcome="pending", reason=taint_reason, ref_id=str(approval_id),
                  detail={"conversation_id": conversation_id, "turn_id": turn_id, "turn_source": turn_source})
    try:
        from services import push_service

        await push_service.send_push_notification(
            title=decision_title(tool_name),
            body=f"{summary}\nHe read {reason_words(taint_reason)} first.",
            url=_href(conversation_id),
            tag=f"approval-{approval_id}",
            source="approval",
            conversation_id=conversation_id,
            # Nobody is woken to approve: at night the push is held and the
            # approval waits on Today (it expires after a day).
            respect_quiet_hours=True,
            approval_id=approval_id,
        )
    except Exception as exc:  # noqa: BLE001 — the approval stands; Today shows it
        print(f"[approval] push for #{approval_id} failed: {exc}")
    return waiting_message(approval_id, summary)


async def expire_stale(now: Optional[datetime] = None) -> int:
    """Pending approvals older than the TTL become ``expired``. On read."""
    from sqlalchemy import update
    from services.database import PendingApprovalModel, async_session

    now = now or datetime.utcnow()
    cutoff = now - timedelta(hours=APPROVAL_TTL_HOURS)
    try:
        async with async_session() as session:
            result = await session.execute(
                update(PendingApprovalModel)
                .where(PendingApprovalModel.status == "pending").where(PendingApprovalModel.created_at < cutoff)
                .values(status="expired", decided_at=now, result_text="Expired unanswered; not sent.")
                .returning(PendingApprovalModel.id, PendingApprovalModel.tool_name,
                           PendingApprovalModel.conversation_id, PendingApprovalModel.summary)
            )
            expired = result.all()
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("approval expiry failed: %s", exc)
        return 0
    for aid, tool_name, conv, summary in expired:
        print(f"[approval] #{aid} expired unanswered")
        await _ledger(kind="approval", source=tool_name, outcome="expired", ref_id=str(aid),
                      detail={"conversation_id": conv})
        if conv:
            _spawn(append_thread_note(conv, f"[Approval #{aid}] {_OWNER} did not answer within a day; "
                                            f"{summary} was not sent."))
    return len(expired)


async def get_approval(approval_id: int) -> Optional[Dict[str, Any]]:
    from services.database import PendingApprovalModel, async_session

    await expire_stale()
    async with async_session() as session:
        row = await session.get(PendingApprovalModel, int(approval_id))
        return _row_dict(row) if row is not None else None


async def list_approvals(status: Optional[str] = None, limit: int = 50,
                         since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from sqlalchemy import select
    from services.database import PendingApprovalModel, async_session

    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {list(STATUSES)}")
    await expire_stale()
    q = select(PendingApprovalModel).order_by(PendingApprovalModel.id)
    if status:
        q = q.where(PendingApprovalModel.status == status)
    if since is not None:
        q = q.where((PendingApprovalModel.decided_at >= since) | (PendingApprovalModel.status == "pending"))
    async with async_session() as session:
        rows = (await session.execute(q.limit(max(1, min(int(limit), 500))))).scalars().all()
    return [_row_dict(r) for r in rows]


async def _find_tool(name: str) -> Any:
    from services.tool_registry import get_available_tools

    for t in await get_available_tools():
        if getattr(t, "name", None) == name:
            return t
    return None


async def _execute(row: Dict[str, Any]) -> str:
    """Run an approved call: past the taint gate, never past the secret scan."""
    from services import taint_service
    from services.graph import tool_deadlines

    name, args = row["tool_name"], row["args"]
    refused = await taint_service.egress_refusal(name, args)
    if refused is not None:
        return refused
    tool = await _find_tool(name)
    if tool is None:
        return f"Tool error: {name} is not available now; nothing was sent."
    if row.get("conversation_id"):
        try:
            from services.graph.tools import set_current_conversation_id

            set_current_conversation_id(row["conversation_id"])
        except Exception:  # noqa: BLE001
            pass
    if name == "spawn_cc_worker":
        # Start it and answer (review 2026-09-27): with the stored wait=True
        # and no deadline for this tool, POST /api/approvals/{id} blocked for
        # the whole session and Cloudflare gave up after 100 s. The session
        # runs as a tracked task; its result reaches the thread's next turn.
        args = {**args, "wait": False}
    # The owner approving it is the owner asking (the toolset's tools read the turn's
    # policy; outside a turn they are fail-closed, 2026-10-11).
    from services.toolset import policy as policy_mod
    from services.toolset.result import failure_text

    approved = policy_mod.TurnPolicy(source="approval", owner_asked=True, may_reach_people=True, may_push=True)
    try:
        with policy_mod.policy_scope(approved):
            result = await tool_deadlines.run_tool_with_deadline(name, tool.ainvoke(args))
    except Exception as exc:  # noqa: BLE001
        result = failure_text(exc)
    return str(result)


async def decide(approval_id: int, decision: str) -> Dict[str, Any]:
    """The owner's answer. Approve runs the stored call once; deny runs nothing;
    repeating the same answer is a no-op; the opposite answer is a conflict."""
    from sqlalchemy import update
    from services.database import PendingApprovalModel, async_session

    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    new_status = DECISIONS[decision]
    row = await get_approval(approval_id)
    if row is None:
        raise ApprovalNotFound(f"no approval #{approval_id}")
    if row["status"] != "pending":
        if row["status"] == new_status:
            return {**row, "already_decided": True, "delivered": _was_delivered(row)}
        raise ApprovalConflict(f"approval #{approval_id} is already {row['status']}")
    if hands_on_own_app():
        raise ApprovalConflict("Edward's hands are on this app in Chrome right now, so a decision from there "
                               "could be his; decide from your phone, or after he lets go of the tab.")
    now = datetime.utcnow()
    async with async_session() as session:
        won = (await session.execute(
            update(PendingApprovalModel)
            .where(PendingApprovalModel.id == int(approval_id)).where(PendingApprovalModel.status == "pending")
            .values(status=new_status, decided_at=now)
            .returning(PendingApprovalModel.id)
        )).first() is not None
        await session.commit()
    if not won:
        current = await get_approval(approval_id)
        if current is not None and current["status"] == new_status:
            return {**current, "already_decided": True, "delivered": _was_delivered(current)}
        raise ApprovalConflict(f"approval #{approval_id} was decided at the same moment")

    if new_status == "approved":
        result_text = (await _execute(row))[:RESULT_MAX_CHARS]
    else:
        result_text = f"Denied by {_OWNER}; not sent."
    async with async_session() as session:
        await session.execute(update(PendingApprovalModel).where(PendingApprovalModel.id == int(approval_id))
                              .values(result_text=result_text))
        await session.commit()
    from services.graph import tool_policy

    ok = new_status == "approved" and not tool_policy.is_failure_result(result_text)
    print(f"[approval] #{approval_id} {new_status}: {row['summary']} → {result_text[:120]}")
    await _ledger(kind="approval", source=row["tool_name"], outcome=new_status, ref_id=str(approval_id),
                  reason=row.get("taint_reason"),
                  detail={"conversation_id": row.get("conversation_id"), "ok": ok})
    if row.get("conversation_id"):
        if new_status == "approved":
            note = f"[Approval #{approval_id}] {_OWNER} approved {row['summary']}. Result: {_one_line(result_text, 240)}"
        else:
            note = f"[Approval #{approval_id}] {_OWNER} denied {row['summary']}. It was not sent."
        _spawn(append_thread_note(row["conversation_id"], note))
    out = await get_approval(approval_id)
    out = out or {**row, "status": new_status, "result_text": result_text, "decided_at": now}
    # From the send tool's own reply (review follow-up 2026-09-26): "Approved"
    # is not "sent" — the tool may have refused, held or failed it.
    out["delivered"] = new_status == "approved" and delivered(row["tool_name"], result_text)
    return out


def _was_delivered(row: Dict[str, Any]) -> bool:
    return row.get("status") == "approved" and delivered(row.get("tool_name"), row.get("result_text"))


async def append_thread_note(conversation_id: str, text: str) -> None:
    """Leave the outcome in the thread the send came from, so the next turn
    there knows (best effort; waits for a wake running in that thread)."""
    try:
        from langchain_core.messages import HumanMessage
        from services.graph import get_graph

        graph = await get_graph()
        config = {"configurable": {"thread_id": conversation_id}}

        async def _write():
            state = await graph.aget_state(config)
            if not (state and state.values and state.values.get("messages")):
                return
            await graph.aupdate_state(config, {"messages": [HumanMessage(content=text)]}, as_node="extract_memory")

        try:
            from services.mind_service import thread_lock

            async with thread_lock(conversation_id):
                await _write()
        except ImportError:
            await _write()
    except Exception as exc:  # noqa: BLE001
        print(f"[approval] could not leave a note in {conversation_id}: {exc}")


async def today_needs() -> List[Dict[str, Any]]:
    """Today needs for every pending approval (the coordinator wires this
    into ``today_service``)."""
    return approval_needs(await list_approvals(status="pending"))


async def load_pack_lines(since: Optional[datetime] = None) -> List[str]:
    """``pack_lines`` for a wake: every pending approval, and the ones
    decided since ``since`` (default: the last day)."""
    since = since or datetime.utcnow() - timedelta(hours=APPROVAL_TTL_HOURS)
    if since.tzinfo is not None:  # the mind's last_wake_at is aware; the table is naive UTC
        since = since.astimezone(timezone.utc).replace(tzinfo=None)
    rows = await list_approvals(since=since, limit=100)
    pending = [r for r in rows if r["status"] == "pending"]
    decided = [r for r in rows if r["status"] != "pending"]
    return pack_lines(pending, decided)
