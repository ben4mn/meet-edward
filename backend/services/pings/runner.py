"""The ping runner: every ping gets one reply turn, and is tracked until a
send into its chat succeeds (AREA 3, 2026-10-11; docs/architecture/pings.md).

The listener records a ``pings`` row for each @Edward, each message the
owner writes in their self-chat and each inline reply to Edward, and kicks
this runner after its commit. The sweeper wakes on a kick or every 20 s:

1. ``recover()`` at start and every 60 s: a ``running`` row with no turn in
   this process (a restart) is answered when the ledger or chat.db holds
   Edward's reply, else it runs again (or gets its template);
2. claims due ``open`` rows, oldest first, at most
   ``EDWARD_PING_CONCURRENCY`` turns at once and never two in one chat;
3. **claim-time rules**, before any turn: an unknown number one-to-one (or
   in a group with no contact in it) is ``held`` and the owner gets a note;
   a ping a day late ``expired``; a may-answer ping under a stand-down, or
   with only the legacy toolset, ``declined``; the loop breaker (more than
   3 friends' pings in a chat in 5 min, or 12 of the owner's — each party
   counts its own) and the per-chat third-party rate (6 turns an hour)
   ``limited``; iMessage switched off ``failed``; then a mention or the
   owner's own ping releases the chat's stand-down. A claim that ends
   without a turn gives its attempt back;
4. **the reply turn** in ``ping-<id>`` (the owner's "yours" list): the whole
   message, the chat, its roster, 20 thread lines, up to 6 images, the
   thread's recent voice messages and the links in the message read first
   (``context.gather``; the runner's own reading is the fallback), under
   ``purpose_scope("ping:owner"|"ping:friend")``; a friend's ping also under
   ``llm_spend.own_work_scope()``. Friends' pings run the owner's policy with
   ``owner_asked=False``; ``pings.scope`` then refuses the shell, editor,
   Chrome and screens unless ``EDWARD_FRIENDS_FULL_TOOLSET=on``; on the legacy
   loop, which can't keep that decision's conditions, without
   ``scope.LEGACY_THIRD_PARTY_EXCLUDED``. A friend's trigger carries the
   owner's own standing instructions;
5. **the outcome** (``outcomes.decide``): the turn's own delivered send
   answers when the turn then ended cleanly (a send and then a failure was
   progress: the ``unfinished`` note follows, never a re-run); a stand-down
   or ``tell_owner`` declines; otherwise the runner posts the final text or
   an honest template (``delivery.deliver``), retries a transient failure
   (the legacy loop's honest ending included) once after 30 s, and tells
   the owner when a reply could not be delivered.

No double replies: the runner waits for a send still inside osascript
before it composes anything (``wait_inflight``), the delivery dedupes on
the ledger (per ping) and reads chat.db before its one retry, and recovery
reads chat.db as well as the ledger, telling the runner's own delivery
(its ``delivering`` note) from the turn's progress send.

``EDWARD_PINGS=off`` stops this runner (``hooks.start`` never calls
``start``); the rows are still written, as ``legacy``.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import importlib
import inspect
import json
import logging
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

from services.owner import owner_name, owner_text
from services.pings import hooks, outcomes, store

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


TURN_TIMEOUT_S = _env_float("EDWARD_PING_TURN_TIMEOUT_S", 600.0)
TURN_BACKSTOP_S = 60.0          # the outer bound past the loop's own deadline (TURN_TIMEOUT_S - 30)
CONCURRENCY = max(1, int(_env_float("EDWARD_PING_CONCURRENCY", 2)))
SWEEP_INTERVAL_S = 20.0
RECOVER_EVERY_S = 60.0
PRUNE_EVERY_S = 24 * 3600.0
RETRY_AFTER_S = 30
STALE_AFTER = timedelta(minutes=30)
EXPIRE_AFTER = timedelta(hours=24)
WAIT_INFLIGHT_S = 40.0
GATHER_TIMEOUT_S = 25.0
# ``context.gather`` bounds each of its sources at 25 s, and the links and
# voice messages are read after the thread: the whole of it gets twice that.
CONTEXT_GATHER_FACTOR = 2.0
NAMES_TIMEOUT_S = 8.0           # contact names for a roster (Contacts lookups can be slow)
THREAD_LINES = 20
RECENT_IMAGES = timedelta(minutes=30)
MAX_IMAGES = 6
TELL_OWNER_EVERY_S = 3600.0
HELD_NOTE_EVERY_S = 24 * 3600.0
LOOP_WINDOW = timedelta(minutes=5)
LOOP_MAX = 3
RATE_WINDOW = timedelta(hours=1)
RATE_MAX = 6
MEMORY_QUERY_CHARS = 400
MAX_HISTORY_CHARS = 48_000
# Templates that promise the owner was told ("I've flagged it", "I've let <the owner> know").
TELL_OWNER_TEMPLATES = frozenset({"error", "spend_cap_friend", "spend_cap_group", "scope_unavailable",
                                "unfinished", "unfinished_timeout"})
UNFINISHED_TEMPLATES = frozenset({"unfinished", "unfinished_timeout"})
# The note a row carries while the runner posts its own reply or template:
# recovery reads it to tell that delivery from the turn's own sends.
DELIVERING = "delivering"
IMESSAGE_SKILL = "imessage_applescript"


class BackendRestarted(Exception):
    """A running ping whose turn a restart cut, with no reply found."""


@dataclass
class Gathered:
    """What the reply turn is given besides the message itself."""

    thread: List[Dict[str, Any]] = field(default_factory=list)
    participants: List[Dict[str, Any]] = field(default_factory=list)
    attachments: List[Dict[str, Any]] = field(default_factory=list)   # chat_with_memory dicts
    attachment_lines: List[str] = field(default_factory=list)
    media_bytes: int = 0
    # Set when ``context.gather`` built the whole trigger (links
    # read, voice messages heard) and counted the media itself.
    trigger: Optional[str] = None
    media_images: Optional[int] = None


class _State:
    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self.wake: Optional[asyncio.Event] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.running: Dict[int, Set[str]] = {}
        self.tasks: Set[asyncio.Task] = set()
        self.told: Dict[str, float] = {}
        self.once: Dict[str, float] = {}
        self.last_recover = float("-inf")
        self.last_prune = float("-inf")


_S = _State()


def reset_for_tests() -> None:
    global _S, _POLICY_IMPORT_LOGGED
    _S = _State()
    _POLICY_IMPORT_LOGGED = False


# ------------------------------------------------------------ seams ----

async def _settings():
    from services.settings_service import get_settings

    return await get_settings()


async def _graph():
    from services.graph import get_graph

    return await get_graph()


async def _skill_enabled() -> bool:
    try:
        from services import skills_service

        return bool(await skills_service.is_skill_enabled(IMESSAGE_SKILL))
    except Exception as exc:  # noqa: BLE001 - a broken read must not silence every ping
        logger.warning("pings: skill check failed: %s", hooks.brief_error(exc))
        return True


async def _contact_name(handle: str) -> Optional[str]:
    from services.heartbeat import listener_imessage

    try:
        return await listener_imessage._resolve_contact_name(handle)
    except Exception:  # noqa: BLE001
        return None


async def _standdown_active(chat: Optional[str]):
    """The live stand-down covering this chat in any spelling (the model
    may have written ``any;+;chat123`` for ``chat123``), or None."""
    if not chat:
        return None
    try:
        from services.graph.tools import _active_standdown_for

        return await _active_standdown_for(chat)
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.heartbeat.triage_service import _get_active_standdown

        return await _get_active_standdown(chat)
    except Exception:  # noqa: BLE001
        return None


async def _release_standdown(chat: str) -> None:
    try:
        from services.heartbeat.triage_service import _release_standdown as release

        await release(chat)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: could not release the stand-down on %s: %s", chat, hooks.brief_error(exc))


def _optional(name: str):
    """One of the sibling modules (``limits``, ``fallback``, ``render``,
    ``context``), or None when it can't be imported."""
    try:
        return importlib.import_module(f"services.pings.{name}")
    except ImportError:
        return None


# ---------------------------------------------------- telling the owner ----

def _once(key: str, every_s: float) -> bool:
    """True the first time ``key`` is seen in each ``every_s`` window."""
    now = time.monotonic()
    last = _S.once.get(key)
    if last is not None and now - last < every_s:
        return False
    _S.once[key] = now
    return True


def notice_origin(row: Optional[Dict[str, Any]]) -> str:
    """Whose ping a notice is about, as ``reach_service`` names the origin:
    ``ping:owner`` for the owner's own (never held),
    else ``ping:other`` (a note at night). Both are exempt from the push
    budget and never linted. Pure."""
    return "ping:owner" if (row or {}).get("from_owner") else "ping:other"


async def tell_owner(text: str, *, urgent: bool = False, link: Optional[str] = None,
                   key: Optional[str] = None, origin: Optional[str] = None) -> bool:
    """Tell the owner, at most once per ``key`` an hour: AREA 1's
    ``reach_service.tell_owner`` when it exists — with the notice's origin
    (``notice_origin``; ``ping:other`` when none is given, so a friend's
    ping in flight is never mistaken for Edward's own words) and the key as
    its ``dedupe_key`` (the reach row names the notice) — else a push
    (urgent) or a Today note. Never raises."""
    k = key or text[:80]
    now = time.monotonic()
    last = _S.told.get(k)
    if last is not None and now - last < TELL_OWNER_EVERY_S:
        return False
    _S.told[k] = now
    title = "Edward"
    try:
        reach = importlib.import_module("services.reach_service")
        result = reach.tell_owner(text, title, urgent, link, origin=origin or "ping:other", dedupe_key=k)
        if inspect.isawaitable(result):
            await result
        return True
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - fall back to the push/note path
        logger.warning("pings: reach_service.tell_owner failed: %s", hooks.brief_error(exc))
    try:
        if urgent:
            from services import push_service

            await push_service.send_push_notification(title=title, body=text[:300], url=link, source="ping")
        else:
            from services import notes_service

            first = text.split(". ")[0][:120]
            await notes_service.leave_note(kind="mind", title=first, body=text, url=link)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: could not tell the owner: %s", hooks.brief_error(exc))
        return False


# ------------------------------------------------------------ helpers ----

def _chat_keys(row: Dict[str, Any]) -> Set[str]:
    return {k for k in (hooks.chat_key(row.get("chat_identifier")), hooks.chat_key(row.get("chat_guid"))) if k}


def chats_in_flight() -> Set[str]:
    """Chat keys with a ping turn running in this process now."""
    out: Set[str] = set()
    for keys in list(_S.running.values()):
        out |= keys
    return out


def _who(row: Dict[str, Any]) -> str:
    """Who asked, for the turn and its memory query: the owner by name."""
    if row.get("from_owner"):
        return owner_name()
    return str(row.get("sender_label") or row.get("sender") or "someone")


def _who_to_owner(row: Dict[str, Any]) -> str:
    """Who asked, in a notice to the owner: "you" for their own ping."""
    if row.get("from_owner"):
        return "you"
    return _who(row)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _where(row: Dict[str, Any]) -> str:
    if row.get("kind") == "owner_direct":
        return "your chat with me"
    return str(row.get("chat_name") or row.get("chat_identifier") or "a chat")


def _link(row: Dict[str, Any]) -> str:
    return f"/chat?c=ping-{row.get('id')}"


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _latency(row: Dict[str, Any], at: Optional[datetime]) -> Optional[float]:
    if at is None or row.get("asked_at") is None:
        return None
    return max(0.0, (_naive(at) - row["asked_at"]).total_seconds())


def _fallback_text(reason: str, detail: str = "") -> str:
    mod = _optional("fallback")
    minutes = max(1, int(round(TURN_TIMEOUT_S / 60)))
    if mod is not None:
        try:
            return str(mod.fallback_text(reason, detail=detail, minutes=minutes))
        except Exception as exc:  # noqa: BLE001
            logger.warning("pings: fallback_text failed: %s", hooks.brief_error(exc))
    template = _FALLBACKS.get(reason) or _FALLBACKS["error"]
    return template.format(detail=detail or "an error", minutes=minutes)


# The templates, used only when ``fallback.fallback_text`` fails.
# ``{detail}`` is an error class name, never an error message; ``{OWNER}``
# is rendered once at import.
_FALLBACKS = {
    "spend_cap": "{detail}",
    "spend_cap_friend": "I can't look into this right now — I've hit my limit for today. I've let {OWNER} know.",
    "spend_cap_group": "I've hit my spending limit for today, so I can't look into this until tomorrow.",
    "provider": ("Sorry — I couldn't answer this just now: my AI service kept failing ({detail}). "
                 "Ask me again in a few minutes."),
    "timeout": ("Sorry — this took longer than {minutes} minutes and I stopped before finishing. "
                "Ask me again and I'll pick it up."),
    "error": "Sorry — something broke on my side while I was on this ({detail}). I've flagged it.",
    "empty": "I looked into this but came up empty. Can you give me a little more to go on?",
    "refused": "I can't help with that one.",
    "incomplete": "I ran out of room before finishing that answer — ask me for the rest and I'll pick it up.",
    "scope_unavailable": "I can't answer this here yet — I've passed it to {OWNER}.",
    "rate": "I've answered a lot here in the last hour, so I'm taking a break — {OWNER} can ask me directly.",
    "skills_off": "I can't reply here right now: iMessage sending is switched off in my settings.",
    "unfinished": ("I got cut off on my side before I'd wrapped this up ({detail}) — if you still need "
                   "something from me here, ask me again."),
    "unfinished_timeout": ("I ran out of time ({minutes} minutes) before I'd wrapped this up — if you still "
                           "need something from me here, ask me again."),
    "garbled": "Sorry — my answer came out garbled on my side, so I didn't post it. Ask me again and I'll have "
               "another go.",
}
_FALLBACKS = {reason: owner_text(text) for reason, text in _FALLBACKS.items()}


def _limit_decision(*, pings_in_chat_5m: int, third_party: bool, third_party_turns_in_chat_1h: int) -> Optional[str]:
    mod = _optional("limits")
    if mod is not None:
        try:
            return mod.decide(pings_in_chat_5m=pings_in_chat_5m, third_party=third_party,
                              third_party_turns_in_chat_1h=third_party_turns_in_chat_1h)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pings: limits.decide failed: %s", hooks.brief_error(exc))
    if pings_in_chat_5m > LOOP_MAX:
        return "loop"
    if third_party and third_party_turns_in_chat_1h >= RATE_MAX:
        return "third_party_rate"
    return None


def _limit_note(limit: str, row: Dict[str, Any], count: int) -> str:
    """What the owner is told when a limit stops Edward in a chat: ``limits.owner_note``
    with the count that stopped it, else the runner's own line."""
    mod = _optional("limits")
    if mod is not None and hasattr(mod, "owner_note"):
        try:
            return str(mod.owner_note(limit, chat=_where(row), count=count))
        except Exception as exc:  # noqa: BLE001
            logger.warning("pings: limits.owner_note failed: %s", hooks.brief_error(exc))
    if limit == "loop":
        return f"I stopped answering in {_where(row)} — {count} pings in 5 minutes looks like a loop."
    return f"I've stopped answering friends in {_where(row)} for an hour — {count} turns there in the last hour."


# ------------------------------------------------------- the call shape ----

def _chat_with_memory():
    from services.graph import streaming

    return streaming.chat_with_memory


_POLICY_IMPORT_LOGGED = False


def _turn_policy_cls():
    """AREA 2's ``TurnPolicy``, or None. Not merged is quiet; a broken import
    is logged as an error (once) — friends' turns then run on the legacy
    loop without its unsandboxed tools, never silently with them."""
    global _POLICY_IMPORT_LOGGED
    try:
        return importlib.import_module("services.toolset.policy").TurnPolicy
    except ModuleNotFoundError as exc:
        if str(getattr(exc, "name", "") or "").startswith("services.toolset"):
            return None  # AREA 2 not merged: the legacy loop
        failure = exc
    except Exception as exc:  # noqa: BLE001
        failure = exc
    if not _POLICY_IMPORT_LOGGED:
        _POLICY_IMPORT_LOGGED = True
        logger.error("pings: services.toolset.policy is here but would not import (%s); ping turns run on the "
                     "legacy loop, and friends' without its unsandboxed tools", hooks.brief_error(failure))
    return None


def has_policy() -> bool:
    """Is AREA 2's tool loop here (``chat_with_memory(policy=…)``)?"""
    try:
        params = inspect.signature(_chat_with_memory()).parameters
    except (TypeError, ValueError):
        return False
    return "policy" in params and _turn_policy_cls() is not None


def _policy_for(row: Dict[str, Any], turn_source: str):
    """Friends' pings get the same policy as the owner's (design decision,
    2026-10-11), but ``owner_asked`` is only true when the owner is the one
    asking."""
    TurnPolicy = _turn_policy_cls()
    names = {f.name for f in dataclasses.fields(TurnPolicy)} if dataclasses.is_dataclass(TurnPolicy) else set()
    chat = row.get("chat_identifier") or row.get("chat_guid")
    values = {"source": turn_source, "owner_asked": bool(row.get("from_owner")), "may_reach_people": True,
              "may_push": True, "reply_to": (chat,) if row.get("must_answer") else (),
              "deadline_s": TURN_TIMEOUT_S - 30}
    return TurnPolicy(**{k: v for k, v in values.items() if not names or k in names})


def _filter_kwargs(fn, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


# ------------------------------------------------------------ gathering ----

async def _event_attachments(event_id: Optional[str]) -> List[Dict[str, Any]]:
    if not event_id:
        return []
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as session:
        event = await session.get(HeartbeatEventModel, event_id)
    if event is None:
        return []
    try:
        data = json.loads(event.raw_data or "{}")
    except (TypeError, ValueError):
        return []
    return [a for a in (data.get("attachments") or []) if isinstance(a, dict)]


def _by_edward(m: Dict[str, Any]) -> bool:
    if "by_edward" in m:
        return bool(m["by_edward"])
    from services import imessage_sent
    from services.mind_service import has_edward_signature

    return imessage_sent.is_edward_rowid(m.get("rowid")) or (bool(m.get("is_from_me"))
                                                              and has_edward_signature(m.get("text")))


async def _names(handles: List[Optional[str]]) -> List[tuple]:
    """``[(handle, contact name or None)]``, looked up in parallel and
    bounded: a slow Contacts lookup costs the names, never the thread."""
    hs = [h for h in handles if h]
    try:
        names = await asyncio.wait_for(asyncio.gather(*(_contact_name(h) for h in hs), return_exceptions=True),
                                       timeout=NAMES_TIMEOUT_S)
    except asyncio.TimeoutError:
        names = [None] * len(hs)
    return [(h, n if isinstance(n, str) and n else None) for h, n in zip(hs, names)]


def _context_module():
    """``services.pings.context`` (the links, the voice messages and the
    trigger built from all of it), or None when it can't be imported."""
    mod = _optional("context")
    return mod if mod is not None and hasattr(mod, "gather") else None


async def gather(row: Dict[str, Any]) -> Gathered:
    """Everything the reply turn reads: ``context.gather`` when it is here
    (the thread, the roster, this message's media and the thread's recent
    photos and voice messages, the links in the message read first, and the
    trigger built from all of it), else the runner's own reading. A broken
    context falls back to the runner's own."""
    mod = _context_module()
    if mod is None:
        return await _gather_own(row)
    try:
        tc = await mod.gather(row, attempt=int(row.get("attempts") or 1), max_attempts=outcomes.MAX_ATTEMPTS,
                              last_error=row.get("last_error"), legacy=not has_policy())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the runner's own reading still works
        logger.warning("pings: context.gather for ping %s failed, reading it myself: %s", row.get("id"),
                       hooks.brief_error(exc))
        return await _gather_own(row)
    media = dict(getattr(tc, "media", None) or {})
    return Gathered(thread=list(getattr(tc, "thread", None) or []),
                    participants=[{"handle": h, "name": n} for h, n in (getattr(tc, "names", None) or {}).items()],
                    attachments=list(getattr(tc, "attachments", None) or []),
                    attachment_lines=[str(a.get("filename")) for a in getattr(tc, "attachments", None) or []
                                      if isinstance(a, dict) and a.get("filename")],
                    media_bytes=int(media.get("bytes") or 0), media_images=int(media.get("images") or 0),
                    trigger=str(tc.trigger))


async def _gather_own(row: Dict[str, Any]) -> Gathered:
    """The thread, the roster with names, and the images, each read in
    parallel; whatever fails is left out."""
    from services.heartbeat import listener_imessage
    from services.media import hooks as media_hooks
    from services.pings.store import clock

    chat = row.get("chat_identifier") or ""

    async def thread():
        return await asyncio.to_thread(listener_imessage.get_chat_thread, chat, THREAD_LINES)

    async def roster():
        handles = await asyncio.to_thread(listener_imessage.get_chat_participants, chat)
        return [{"handle": h, "name": n} for h, n in await _names([x.get("handle") for x in handles or []])]

    results = await asyncio.gather(thread(), roster(), _event_attachments(row.get("heartbeat_event_id")),
                                   return_exceptions=True)
    lines, people, own = [(r if not isinstance(r, BaseException) else []) for r in results]
    for r in results:
        if isinstance(r, BaseException):
            logger.warning("pings: gathering for ping %s: %s", row.get("id"), hooks.brief_error(r))
    names = {p["handle"]: p.get("name") for p in people if p.get("handle")}
    for m in lines:
        m["by_edward"] = _by_edward(m)
        m["label"] = ("Edward" if m["by_edward"] else owner_name() if m.get("is_from_me")
                      else names.get(m.get("sender")) or m.get("sender") or "someone")

    # This message's attachments first, then images from the thread in the
    # last 30 minutes, newest first.
    media: List[Dict[str, Any]] = []
    seen: Set[int] = set()
    asker = _who(row)
    when = clock(row.get("asked_at"))
    for a in own:
        if a.get("rowid") is not None and int(a["rowid"]) not in seen:
            seen.add(int(a["rowid"]))
            media.append({**a, "label": f"attachment:{a['rowid']} · {a.get('name')} · from {asker} {when}"})
    cutoff = hooks.utcnow() - RECENT_IMAGES
    for m in reversed(lines):
        at = _naive(m.get("timestamp"))
        if at is not None and at < cutoff:
            continue
        for a in m.get("attachments") or []:
            mime = str(a.get("mime_type") or "")
            if not mime.startswith("image/") or a.get("rowid") is None or int(a["rowid"]) in seen:
                continue
            seen.add(int(a["rowid"]))
            media.append({**a, "label": f"attachment:{a['rowid']} · {a.get('name')} · from {m['label']} "
                                        f"{clock(m.get('timestamp'))}"})
    attachments = await media_hooks.turn_attachments(media, max_images=MAX_IMAGES) if media else []
    raw = 0
    for att in attachments:
        try:
            raw += len(base64.b64decode(att.get("data") or "", validate=False))
        except Exception:  # noqa: BLE001
            pass
    return Gathered(thread=lines, participants=people, attachments=attachments,
                    attachment_lines=[m["label"] for m in media], media_bytes=raw)


async def _gather_bounded(row: Dict[str, Any]) -> Gathered:
    bound = GATHER_TIMEOUT_S * (CONTEXT_GATHER_FACTOR if _context_module() is not None else 1.0)
    try:
        return await asyncio.wait_for(gather(row), timeout=bound)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the turn runs on the message alone
        logger.warning("pings: gathering for ping %s gave up: %s", row.get("id"), hooks.brief_error(exc))
        return Gathered()


# -------------------------------------------------------------- words ----

def _system_prompt(row: Dict[str, Any], *, legacy: Optional[bool] = None) -> str:
    """The ping part of the system prompt (``prompt.system_prompt``).
    ``legacy`` is the call's own toolset test (``has_policy``), so the prompt
    never names a tool the turn doesn't have."""
    from services.pings import prompt

    return prompt.system_prompt(must_answer=bool(row.get("must_answer")),
                                third_party=bool(row.get("third_party")), legacy=legacy)


def _untrusted(source: str, value: str) -> str:
    return f'<untrusted source="{source}">{value}</untrusted>'


def _fallback_trigger(row: Dict[str, Any], g: Gathered, *, legacy: bool, now: datetime) -> str:
    """The trigger, used only when ``render.build_trigger`` fails or can't be
    called with what the runner has."""
    from services.pings.store import clock

    chat = row.get("chat_identifier") or row.get("chat_guid") or ""
    asked = row.get("asked_at") or now
    lines = [f"[PING] {clock(asked)} — iMessage"]
    names = [p.get("name") or p.get("handle") for p in g.participants if p.get("handle")]
    owner = owner_name()
    if row.get("kind") == "owner_direct":
        where = f"{owner}'s own chat with you"
    elif row.get("is_group"):
        roster = ", ".join([owner] + [n for n in names if n]) if names else ""
        where = f'"{row.get("chat_name") or "a group"}" — group' + (f" of {len(names) + 1}: {roster}" if roster else "")
    else:
        where = f"one-to-one with {_who(row)}"
    how = (f'send_imessage(chat_id="{chat}", message=…)' if legacy else f'send_message(chat_id="{chat}", message=…)')
    lines.append(f"Chat: {where} · chat_id: {chat}   (reply with {how})")
    if row.get("kind") == "owner_direct":
        lines.append(f"From: {owner}, in their own chat with you")
    elif row.get("from_owner"):
        lines.append(f"From: {owner} (they wrote @Edward)" if row.get("kind") == "mention" else f"From: {owner}")
    else:
        known = "a contact" if row.get("sender_known") else f"not in {owner}'s contacts"
        lines.append(f"From: {_who(row)}, {known} (not {owner})")
    attempts = int(row.get("attempts") or 1)
    if attempts > 1:
        lines.append(f"(Retry {attempts} of {outcomes.MAX_ATTEMPTS} — the last attempt stopped: "
                     f"{row.get('last_error') or 'an error'}. Pick up where it stopped.)")
    late = now - asked
    if late > STALE_AFTER:
        lines.append(f"(This came in {int(late.total_seconds() // 60)} minutes ago while you were offline — "
                     "open with a short apology.)")
    text = str(row.get("text") or "")
    lines += ["", "The message, whole:", _untrusted(_who(row), text) if row.get("third_party") else text, ""]
    if g.thread:
        lines.append("Recent messages in this chat (oldest first):")
        budget = 12_000
        rendered = []
        for m in g.thread[-THREAD_LINES:]:
            body = " ".join(str(m.get("text") or "").split())[:1500]
            label = m.get("label") or "someone"
            line = (f"  {clock(m.get('timestamp'))} {label}: {body}" if label in (owner, "Edward")
                    else f"  {clock(m.get('timestamp'))} {_untrusted(label, body)}")
            rendered.append(line)
        kept: List[str] = []
        for line in reversed(rendered):
            if budget - len(line) < 0:
                break
            budget -= len(line)
            kept.append(line)
        lines += list(reversed(kept))
        lines.append("")
    if g.attachment_lines:
        shown = len(g.attachments)
        lines.append("Photos and files: " + "; ".join(f"[{label}]" for label in g.attachment_lines)
                     + (f" — {shown} attached below" if shown else ""))
    return "\n".join(lines).rstrip()


def _trigger(row: Dict[str, Any], g: Gathered, *, legacy: bool) -> str:
    if g.trigger:
        return g.trigger   # ``context.gather`` built it, with the same toolset test
    now = hooks.utcnow()
    mod = _optional("render")
    if mod is not None and hasattr(mod, "build_trigger"):
        offered = {"ping": row, "row": row, "text": row.get("text"), "thread": g.thread, "participants": g.participants,
                   "attachments": g.attachment_lines, "attachment_lines": g.attachment_lines,
                   "safe_memories": [], "now": now, "legacy": legacy, "attempts": int(row.get("attempts") or 1),
                   "last_error": row.get("last_error")}
        try:
            params = inspect.signature(mod.build_trigger).parameters
            required = [n for n, p in params.items() if p.default is inspect.Parameter.empty
                        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)]
            if all(n in offered for n in required):
                out = str(mod.build_trigger(**{k: v for k, v in offered.items() if k in params}))
                if legacy:
                    chat = row.get("chat_identifier") or row.get("chat_guid") or ""
                    out += (f'\n\n(Your send tool in this turn is send_imessage(chat_id="{chat}", message=…).)')
                return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("pings: render.build_trigger failed: %s", hooks.brief_error(exc))
    return _fallback_trigger(row, g, legacy=legacy, now=now)


# ------------------------------------ the owner's standing instructions ----

INSTRUCTIONS_TIMEOUT_S = 8.0


async def _standing_instructions() -> Optional[List[str]]:
    """The owner's own standing instructions (heard/owner ``instruction`` rows, the
    mind pack's rule: ``mind_service._instructions_source``), without the
    stand-down lines — a mention releases a stand-down at claim time. None
    when they can't be read."""
    try:
        from services import mind_service

        lines = await mind_service._instructions_source(datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: could not read the owner's standing instructions: %s", hooks.brief_error(exc))
        return None
    return [str(line) for line in lines or [] if not str(line).lstrip(" -").startswith("Stand down in")]


async def _standing_instructions_bounded() -> Optional[List[str]]:
    try:
        return await asyncio.wait_for(_standing_instructions(), timeout=INSTRUCTIONS_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: the owner's standing instructions timed out: %s", hooks.brief_error(exc))
        return None


def _instructions_block(lines: Optional[List[str]], *, legacy: bool) -> str:
    """The trigger's block of the owner's standing instructions, for a
    friend's ping (review 2026-10-11: the mention wake's pack opened with
    them, and they outrank a mention; a ping turn saw none). ``render``'s,
    else this copy of it."""
    mod = _optional("render")
    if mod is not None and hasattr(mod, "instructions_block"):
        try:
            return str(mod.instructions_block(lines, legacy=legacy))
        except Exception as exc:  # noqa: BLE001
            logger.warning("pings: render.instructions_block failed: %s", hooks.brief_error(exc))
    stand_down = "stand_down" if legacy else "messages stand_down"
    tell = "leave_note" if legacy else "tell_owner"
    owner = owner_name()
    if lines is None:
        return (f"(I couldn't read {owner}'s standing instructions just now. If you know they asked you to stay "
                f"out of this chat, use {stand_down} for it and {tell}.)")
    if not lines:
        return ""
    return "\n".join([_cap(f"{owner}'s standing instructions (what they told you themselves; they outrank this "
                           "ping):"), *lines,
                      f"If one says to stay out of this chat or away from this person, don't reply: use {stand_down} "
                      f"for this chat and {tell} what was asked — nothing is posted then."])


# ------------------------------------------------------- claim time ----

async def _group_has_contacts(row: Dict[str, Any]) -> bool:
    """An unknown number in a group of contacts is answered (design
    decision). A group counts when someone other than the sender is in the
    owner's contacts."""
    from services.heartbeat import listener_imessage

    try:
        handles = await asyncio.to_thread(listener_imessage.get_chat_participants, row.get("chat_identifier") or "")
        sender = hooks.chat_key(row.get("sender"))
        others = [h.get("handle") for h in handles or [] if h.get("handle") and hooks.chat_key(h.get("handle")) != sender]
        return any(name for _h, name in await _names(others))
    except Exception as exc:  # noqa: BLE001 - unknown stays held
        logger.warning("pings: roster check failed: %s", hooks.brief_error(exc))
    return False


def _unspent(row: Dict[str, Any]) -> int:
    """The attempts count before this claim: a claim that ends without a
    turn spends none (review 2026-10-11)."""
    return max(0, int(row.get("attempts") or 1) - 1)


async def _no_turn(row: Dict[str, Any], status: str, note: str, **fields: Any) -> None:
    fields.setdefault("attempts", _unspent(row))
    await store.mark(row["id"], status=status, started_at=None, note=note, **fields)
    print(f"[pings] ping {row['id']} {status}: {note}")


async def _claim_time(row: Dict[str, Any]) -> bool:
    """The rules before any turn; True when the turn should run."""
    pid = row["id"]
    chat = row.get("chat_identifier") or ""
    third, must = bool(row.get("third_party")), bool(row.get("must_answer"))

    if third and not row.get("sender_known") and not (row.get("is_group") and await _group_has_contacts(row)):
        await _no_turn(row, "held", "the sender is not in the owner's contacts")
        if _once(f"held:{hooks.chat_key(row.get('sender'))}", HELD_NOTE_EVERY_S):
            excerpt = " ".join(str(row.get("text") or "").split())[:120]
            await tell_owner(f"{row.get('sender') or 'Someone'} (not in your contacts) asked me in {_where(row)}: "
                           f"'{excerpt}'. I didn't answer — tell me if I should.", urgent=False, link=_link(row),
                           key=f"held:{pid}", origin=notice_origin(row))
        return False

    late = hooks.utcnow() - row["asked_at"]
    if late > EXPIRE_AFTER:
        await _no_turn(row, "expired", f"picked up {int(late.total_seconds() // 3600)} h late")
        await tell_owner(f"{_cap(_who_to_owner(row))} asked me in {_where(row)} over a day ago and I never got "
                         "to it.",
                       urgent=False, link=_link(row), key=f"expired:{pid}", origin=notice_origin(row))
        return False

    if not must:
        if not has_policy():
            await _no_turn(row, "declined", "may-answer, and only the legacy toolset is here")
            return False
        if await _standdown_active(chat) is not None:
            await _no_turn(row, "declined", "may-answer under a stand-down")
            return False

    now = hooks.utcnow()
    in_5m = await store.pings_in_chat_since(chat, now - LOOP_WINDOW, third_party=third)
    turns_1h = max(0, await store.third_party_turns_in_chat_since(chat, now - RATE_WINDOW) - 1) if third else 0
    limit = _limit_decision(pings_in_chat_5m=in_5m, third_party=third, third_party_turns_in_chat_1h=turns_1h)
    if limit:
        await _no_turn(row, "limited", f"limit: {limit}")
        key = next(iter(sorted(_chat_keys(row))), str(pid))
        if limit == "loop":
            if not _once(f"loop:{key}", TELL_OWNER_EVERY_S):
                return False
            await tell_owner(_limit_note(limit, row, in_5m), urgent=True, link=_link(row), key=f"loop:{key}",
                           origin=notice_origin(row))
        elif _once(f"rate:{key}", TELL_OWNER_EVERY_S):
            from services.pings import delivery

            await delivery.deliver(row, _fallback_text("rate"), purpose="limit")
            await tell_owner(_limit_note(limit, row, turns_1h), urgent=False, link=_link(row), key=f"rate:{key}",
                           origin=notice_origin(row))
        return False

    if not await _skill_enabled():
        await _no_turn(row, "failed", "iMessage sending is switched off", last_error="skill imessage_applescript off")
        if _once("skills_off", TELL_OWNER_EVERY_S):
            await tell_owner(f"{_cap(_who_to_owner(row))} asked me in {_where(row)}, but iMessage sending is "
                           "switched off in my "
                           "settings, so I can't answer pings.", urgent=False, link=_link(row), key="skills_off",
                           origin=notice_origin(row))
        return False

    if row.get("kind") == "mention" or row.get("from_owner"):
        sd = await _standdown_active(chat)
        if sd is not None:
            await _release_standdown(getattr(sd, "chat_identifier", None) or chat)
    return True


# ------------------------------------------------------------ the turn ----

async def _ensure_conversation(conversation_id: str, row: Dict[str, Any]) -> None:
    try:
        from services.conversation_service import create_conversation, get_conversation

        if await get_conversation(conversation_id) is None:
            title = (f"{row.get('chat_name') or _cap(_who_to_owner(row))}: "
                     f"{' '.join(str(row.get('text') or '').split())[:60]}")
            await create_conversation(conversation_id, title=title, source="external_message", channel="imessage")
    except Exception as exc:  # noqa: BLE001 - the turn still runs
        logger.warning("pings: could not create %s: %s", conversation_id, hooks.brief_error(exc))


async def wait_inflight(ctx: Dict[str, Any], timeout_s: float = WAIT_INFLIGHT_S) -> bool:
    """Wait for a send still inside osascript to finish and note itself;
    True when nothing is in flight any more (path d: never compose a
    fallback over a reply that is about to land)."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    while int(ctx.get("inflight") or 0) > 0:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


async def _standdown_made_in_turn(ctx: Dict[str, Any], row: Dict[str, Any], started: datetime) -> None:
    """The legacy loop reports no tools to the ping context: a stand-down
    for this chat that is active after the turn (a must-answer ping
    released any older one at claim) is the turn's decline."""
    if outcomes.answering_send(ctx) or outcomes.declined_by_tool(ctx):
        return
    sd = await _standdown_active(row.get("chat_identifier"))
    if sd is None:
        return
    at = _aware(getattr(sd, "requested_at", None))
    if at is None or at >= started - timedelta(seconds=1):
        ctx["tools"].append({"name": "messages", "action": "stand_down", "chat": row.get("chat_identifier"),
                             "urgent": False, "ok": True})


async def _remember_owners_words(row: Dict[str, Any], conversation_id: str) -> None:
    """``memory_service.remember_owners_words`` for the owner's ping: their
    own text, read alone as the owner speaking. Lazy; never raises."""
    try:
        from services import memory_service

        await memory_service.remember_owners_words(str(row.get("text") or ""), conversation_id=conversation_id,
                                                 ping_id=row.get("id"), chat_identifier=row.get("chat_identifier"))
    except Exception as exc:  # noqa: BLE001 - never fails the ping
        logger.warning("pings: could not read the owner's words from ping %s: %s", row.get("id"),
                       hooks.brief_error(exc))


def read_owners_words_after_turn(row: Dict[str, Any], turn_source: str,
                               conversation_id: str) -> Optional[asyncio.Task]:
    """The AREA 1 + 3 merge: after the first turn for a ping of the owner's
    in a group or someone else's chat (a ``ping`` turn, read under the
    heartbeat profile because other people's lines are in it), their own
    words are read once more, alone, as the owner speaking — "stop pushing
    me about soccer" becomes their instruction. Their self-chat turn
    (``ping_owner``) already reads them so; a retry would read them twice.
    In the background: the reply never waits on it. Returns the task, or
    None when there is nothing to read."""
    if not row.get("from_owner") or turn_source != "ping" or int(row.get("attempts") or 1) != 1:
        return None
    if not str(row.get("text") or "").strip():
        return None
    try:
        task = asyncio.get_running_loop().create_task(_remember_owners_words(row, conversation_id),
                                                      name=f"ping-{row.get('id')}-owners-words")
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: could not schedule the owner's words for ping %s: %s", row.get("id"),
                       hooks.brief_error(exc))
        return None
    _S.tasks.add(task)
    task.add_done_callback(_S.tasks.discard)
    return task


async def run_ping(row: Dict[str, Any]) -> str:
    """One reply turn for a claimed ping, then its outcome. Returns the
    status it ended in."""
    from services import call_purpose, llm_spend, provenance_service

    pid = int(row["id"])
    chat = row.get("chat_identifier") or row.get("chat_guid") or ""
    must, third = bool(row.get("must_answer")), bool(row.get("third_party"))
    conversation_id = f"ping-{pid}"
    turn_source = "ping_owner" if row.get("kind") == "owner_direct" else "ping"
    profile = "chat" if turn_source == "ping_owner" else "heartbeat"
    await _ensure_conversation(conversation_id, row)
    await store.mark(pid, conversation_id=conversation_id)

    policy_mode = has_policy()
    gathered = await _gather_bounded(row)
    settings = await _settings()
    graph = await _graph()
    trigger = _trigger(row, gathered, legacy=not policy_mode)
    if third:
        block = _instructions_block(await _standing_instructions_bounded(), legacy=not policy_mode)
        if block:
            trigger += "\n\n" + block
    ctx = hooks.new_context(ping_id=pid, chat_identifier=chat, chat_guid=row.get("chat_guid"),
                            is_group=bool(row.get("is_group")), conversation_id=conversation_id,
                            must_answer=must, third_party=third, kind=row.get("kind"))
    images = gathered.media_images
    if images is None:
        images = sum(1 for a in gathered.attachments if str(a.get("mime_type", "")).startswith("image/"))
    ctx["media"].update(images=images, bytes=gathered.media_bytes)
    excerpt = str(row.get("text") or "")[:MEMORY_QUERY_CHARS]
    kwargs: Dict[str, Any] = dict(
        message=trigger, attachments=gathered.attachments or None, conversation_id=conversation_id,
        model=settings.model, temperature=settings.temperature, graph=graph, turn_source=turn_source,
        extraction_profile=profile, max_history_chars=MAX_HISTORY_CHARS,
        system_prompt=_system_prompt(row, legacy=not policy_mode) + "\n\n" + (getattr(settings, "system_prompt", None) or ""),
        memory_query=f"{_who(row)}: {excerpt}")
    if policy_mode:
        kwargs["policy"] = _policy_for(row, turn_source)
    else:
        kwargs.update(required_tools=["send_imessage"], nudge_for_tools=False)
        if third:
            # The full-toolset decision holds on its conditions, which only
            # AREA 2's sandbox keeps (scope.py): the legacy loop goes without these.
            from services.pings import scope

            kwargs["excluded_tools"] = sorted(scope.LEGACY_THIRD_PARTY_EXCLUDED)
    fn = _chat_with_memory()
    kwargs = _filter_kwargs(fn, kwargs)
    event = SimpleNamespace(id=row.get("heartbeat_event_id"), chat_identifier=row.get("chat_identifier"),
                            source="imessage",
                            source_id=f"imessage:{row['message_rowid']}" if row.get("message_rowid") else None)
    try:
        from services.graph.tools import set_current_conversation_id

        set_current_conversation_id(conversation_id)
    except Exception:  # noqa: BLE001
        pass

    started = datetime.now(timezone.utc)
    final: Optional[str] = None
    error: Optional[BaseException] = None
    print(f"[pings] ping {pid} turn {row.get('attempts')}/{outcomes.MAX_ATTEMPTS} in {turn_source} "
          f"({'policy' if policy_mode else 'legacy'}; {'friend' if third else 'owner'})")
    token = hooks.CURRENT_PING.set(ctx)
    try:
        with call_purpose.purpose_scope("ping:friend" if third else "ping:owner"), \
                (llm_spend.own_work_scope() if third else nullcontext()), \
                provenance_service.source_context(**provenance_service.heartbeat_source_fields(event), ping_id=pid,
                                                  turn_source=turn_source):
            final = await asyncio.wait_for(fn(**kwargs), timeout=TURN_TIMEOUT_S + TURN_BACKSTOP_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the outcome rules decide
        error = exc
    finally:
        hooks.CURRENT_PING.reset(token)
        # A send that lands after this was still in flight at the cut: the
        # turn's last act (path d), not a progress line (outcomes rule 1).
        ctx["ended_at"] = hooks.utcnow()
    await wait_inflight(ctx, WAIT_INFLIGHT_S)
    await _standdown_made_in_turn(ctx, row, started)
    read_owners_words_after_turn(row, turn_source, conversation_id)
    outcome = outcomes.decide(ctx, final, error, attempts=int(row.get("attempts") or 1))
    return await apply_outcome(row, ctx, outcome)


async def apply_outcome(row: Dict[str, Any], ctx: Dict[str, Any], outcome: outcomes.Outcome) -> str:
    """Write what happened, delivering the runner's own reply when needed."""
    from services.pings import delivery

    pid = int(row["id"])
    sends = ctx.get("sends") or []
    first_send_at = sends[0]["at"] if sends else None
    if outcome.status == "answered":
        send = outcome.send or {}
        at = send.get("at") or hooks.utcnow()
        await store.mark(pid, status="answered", answered_at=at, latency_s=_latency(row, at),
                         reply_message_rowid=send.get("rowid"), reply_route=send.get("route"),
                         first_send_at=first_send_at, note=outcome.why, last_error=None)
        print(f"[pings] ping {pid} answered by the turn")
        return "answered"
    if outcome.status == "declined":
        await store.mark(pid, status="declined", note=outcome.why, first_send_at=first_send_at)
        print(f"[pings] ping {pid} declined: {outcome.why}")
        return "declined"
    if outcome.status == "retry":
        await store.mark(pid, status="open", next_attempt_at=hooks.utcnow() + timedelta(seconds=RETRY_AFTER_S),
                         last_error=outcome.why, first_send_at=first_send_at)
        print(f"[pings] ping {pid} will retry in {RETRY_AFTER_S} s: {outcome.why}")
        return "open"

    reply = outcome.purpose == "reply"
    text = outcome.text if reply else _fallback_text(outcome.template or "error", outcome.detail)
    await store.mark(pid, note=f"{DELIVERING} {'reply' if reply else 'fallback'}")
    d = await delivery.deliver(row, text or "", purpose="reply" if reply else "fallback", ctx=ctx)
    if d.ok:
        status = "answered" if reply else "fallback"
        at = hooks.utcnow()
        await store.mark(pid, status=status, answered_at=at, latency_s=_latency(row, at), reply_message_rowid=d.rowid,
                         reply_route=d.route, first_send_at=first_send_at or at,
                         note=f"{outcome.why}{' (deduped)' if d.deduped else ''}")
        print(f"[pings] ping {pid} {status} ({outcome.template or 'final text'})")
        if not reply and outcome.template in TELL_OWNER_TEMPLATES:
            detail = (f" ({outcome.detail})" if outcome.detail and outcome.template in ("error", "unfinished")
                      else "")
            if outcome.template == "spend_cap_group":
                # The cap's own sentence (the owner's dollars, the knob) is for
                # the owner only; the chat got the plain line (review 2026-10-11).
                sentence = " ".join(str(outcome.detail or "").split())[:400]
                said = ("I couldn't answer your ping in {where} — I only said there that I'm at my limit for "
                        "today. " + sentence.replace("{", "(").replace("}", ")")).strip()
            elif outcome.template == "spend_cap_friend":
                said = "I couldn't answer {who} in {where}: I'm at today's spending limit; I told them so."
            elif outcome.template in UNFINISHED_TEMPLATES:
                said = ("I was cut off answering {who} in {where} after my first message{detail}, so they may "
                        "have had only that; I told them so.")
            else:
                said = "Something went wrong while I was answering {who} in {where}{detail}; I told them so."
            await tell_owner(said.format(who=_who_to_owner(row), where=_where(row), detail=detail), urgent=False,
                           link=_link(row), key=f"fallback:{pid}", origin=notice_origin(row))
        return status
    await store.mark(pid, status="failed", last_error=d.error, first_send_at=first_send_at,
                     note=outcome.template or "final text")
    print(f"[pings] ping {pid} failed: Messages couldn't deliver the reply")
    answer = " ".join(str(d.text or text or "").split())[:300]
    await tell_owner(f"I answered {_who_to_owner(row)} in {_where(row)} but Messages couldn't deliver it: "
                   f"{str(d.error or 'unknown error')[:200]}. My answer: {answer}", urgent=True, link=_link(row),
                   key=f"failed:{pid}", origin=notice_origin(row))
    return "failed"


# ------------------------------------------------------------ recovery ----

async def recover() -> int:
    """Settle ``running`` rows with no turn in this process (a restart cut
    them). Recovery reads the ledger, then chat.db (a SIGKILL can lose the
    fire-and-forget ledger write; chat.db can't):

    - the runner's own reply or template there settled the ping: a
      ``reply``/``fallback`` ledger row, or — the row's ``delivering`` note
      says the runner was posting — a signed chat.db row since that note;
    - the turn's own send there, then the restart: the turn was cut after
      it, so that send was progress, not the answer — the outcome rules
      post the ``unfinished`` note (review 2026-10-11);
    - nothing: run again, or after the last attempt the outcome rules.

    Returns how many rows it looked at."""
    from services import imessage_service

    seen = 0
    for row in await store.running():
        if row["id"] in _S.running:
            continue
        seen += 1
        pid = int(row["id"])
        try:
            note = str(row.get("note") or "")
            led = await store.answered_in_ledger(pid)
            if led is not None and led.get("purpose") in store.RUNNER_PURPOSES:
                at = _naive(led.get("sent_at")) or hooks.utcnow()
                await _settled(row, "answered" if led.get("purpose") == "reply" else "fallback", at,
                               led.get("message_rowid"), led.get("route"), "the ledger")
                continue
            if note.startswith(DELIVERING):
                # The runner was posting its own reply or template; chat.db
                # since that moment says whether it landed.
                since = _aware(row.get("updated_at") or row.get("started_at") or row.get("asked_at"))
                found = await asyncio.to_thread(imessage_service.recent_from_me_since,
                                                row.get("chat_identifier") or "", since)
                if found is not None:
                    kind = note[len(DELIVERING):].strip()
                    await _settled(row, "answered" if kind == "reply" else "fallback", hooks.utcnow(),
                                   found.get("rowid"), "found", "chat.db")
                    continue
            turn_send: Optional[Dict[str, Any]] = None
            if led is not None:
                turn_send = {"rowid": led.get("message_rowid"), "route": led.get("route"),
                             "at": _naive(led.get("sent_at")) or hooks.utcnow()}
            else:
                since = _aware(row.get("started_at") or row.get("asked_at"))
                found = await asyncio.to_thread(imessage_service.recent_from_me_since,
                                                row.get("chat_identifier") or "", since)
                if found is not None:
                    turn_send = {"rowid": found.get("rowid"), "route": "found", "at": hooks.utcnow()}
            if turn_send is not None:
                # The turn's own send, then the restart: progress, not the answer.
                ctx = _recovery_context(row)
                ctx["sends"].append({"ok": True, "purpose": "tool", "error": None, **turn_send})
                await _settle(row, ctx, BackendRestarted("the backend restarted mid-turn"))
                continue
            if int(row.get("attempts") or 0) < outcomes.MAX_ATTEMPTS:
                await store.mark(pid, status="open", next_attempt_at=hooks.utcnow(),
                                 last_error="a restart cut the last attempt")
                continue
            await _settle(row, _recovery_context(row), BackendRestarted("the backend restarted mid-turn"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one row never stops the rest
            logger.warning("pings: recovering ping %s failed: %s", pid, hooks.brief_error(exc))
    return seen


async def _settled(row: Dict[str, Any], status: str, at: datetime, rowid: Any, route: Optional[str],
                   how: str) -> None:
    """The runner's own reply or template landed before the restart."""
    await store.mark(int(row["id"]), status=status, answered_at=at, latency_s=_latency(row, at),
                     reply_message_rowid=rowid, reply_route=route, note=f"recovered from {how}")


def _recovery_context(row: Dict[str, Any]) -> Dict[str, Any]:
    return hooks.new_context(ping_id=int(row["id"]), chat_identifier=row.get("chat_identifier") or "",
                             chat_guid=row.get("chat_guid"), is_group=bool(row.get("is_group")),
                             conversation_id=f"ping-{row['id']}", must_answer=bool(row.get("must_answer")),
                             third_party=bool(row.get("third_party")), kind=row.get("kind"))


async def _settle(row: Dict[str, Any], ctx: Dict[str, Any], error: BaseException) -> None:
    """The outcome rules for a turn a restart cut, never retried from here."""
    pid = int(row["id"])
    outcome = outcomes.decide(ctx, None, error, attempts=int(row.get("attempts") or 0),
                              is_transient=lambda _e: False)
    _S.running[pid] = _chat_keys(row)
    try:
        await apply_outcome(row, ctx, outcome)
    finally:
        _S.running.pop(pid, None)


# ------------------------------------------------------------ sweeping ----

async def _run_and_release(row: Dict[str, Any]) -> None:
    pid = int(row["id"])
    try:
        await run_ping(row)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - leave it running; recovery settles it
        logger.warning("pings: ping %s crashed: %s", pid, hooks.brief_error(exc))
        print(f"[pings] ping {pid} crashed: {hooks.brief_error(exc)}")
    finally:
        _S.running.pop(pid, None)
        kick()


async def sweep_once(now: Optional[datetime] = None) -> List[asyncio.Task]:
    """Claim due pings and start their turns; the started tasks."""
    started: List[asyncio.Task] = []
    for row in await store.due(now or hooks.utcnow(), limit=CONCURRENCY * 5 + 5):
        if len(_S.running) >= CONCURRENCY:
            break
        keys = _chat_keys(row)
        if keys & chats_in_flight():
            continue
        claimed = await store.claim(row["id"])
        if claimed is None:
            continue
        pid = int(claimed["id"])
        _S.running[pid] = keys
        try:
            go = await _claim_time(claimed)
        except asyncio.CancelledError:
            _S.running.pop(pid, None)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("pings: claim-time rules failed for ping %s: %s", pid, hooks.brief_error(exc))
            await store.mark(pid, status="open", next_attempt_at=hooks.utcnow() + timedelta(seconds=RETRY_AFTER_S),
                             last_error=hooks.brief_error(exc), attempts=_unspent(claimed), started_at=None)
            go = False
        if not go:
            _S.running.pop(pid, None)
            continue
        task = asyncio.create_task(_run_and_release(claimed), name=f"ping-{pid}")
        _S.tasks.add(task)
        task.add_done_callback(_S.tasks.discard)
        started.append(task)
    return started


async def _loop() -> None:
    while True:
        try:
            mono = time.monotonic()
            if mono - _S.last_recover >= RECOVER_EVERY_S:
                _S.last_recover = mono
                await recover()
            if mono - _S.last_prune >= PRUNE_EVERY_S:
                _S.last_prune = mono
                counts = await store.prune()
                if any(counts.values()):
                    print(f"[pings] pruned {counts['pings']} pings, {counts['sends']} sends older than 90 days")
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the sweeper never dies
            logger.warning("pings: sweep failed: %s", hooks.brief_error(exc))
        wake = _S.wake
        if wake is None:
            return
        try:
            await asyncio.wait_for(wake.wait(), timeout=SWEEP_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
        wake.clear()


def kick() -> None:
    """Wake the sweeper now (any thread)."""
    loop, wake = _S.loop, _S.wake
    if loop is None or wake is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(wake.set)
    except RuntimeError:
        pass


async def start() -> None:
    """Start the sweeper (from ``main.py``'s lifespan through ``hooks.start``)."""
    if _S.task is not None and not _S.task.done():
        return
    loop = asyncio.get_running_loop()
    try:
        from services import imessage_service

        imessage_service.note_main_loop(loop)
    except Exception:  # noqa: BLE001
        pass
    _S.loop, _S.wake = loop, asyncio.Event()
    # The first prune waits five minutes: startup is busy enough.
    _S.last_prune = time.monotonic() - PRUNE_EVERY_S + 300
    _S.task = loop.create_task(_loop(), name="ping-runner")
    print(f"[pings] runner started (concurrency {CONCURRENCY}, turn bound {int(TURN_TIMEOUT_S)} s)")


async def stop() -> None:
    task, _S.task = _S.task, None
    pending = [t for t in [task, *_S.tasks] if t is not None and not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending, timeout=10)
    _S.wake = None
