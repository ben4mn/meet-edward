"""``TurnPolicy`` — what one turn may do (docs/architecture/tool-loop.md;
2026-10-11).

The toolset is the same 14 tools on every surface (chat, voice, a ping, a
wake, the pulse, a fired event). What a turn may *do* with them is not
expressed by hiding tools any more — the mind could not see, browse or run
code because its list was cut — but by this policy, which
the executor and the tools read. A refusal is an honest ``Tool error:``
the model can act on, never a missing tool.

* ``owner_asked`` — the owner is the one asking in this turn (chat, voice,
  the widget, Alexa, their own ping, a task they scheduled). It lifts the
  recipient rule, the stand-down check, the Chrome typing rule and the
  never-text-the-owner rule. Never the household hub's voice
  (``hub_voice``): whoever stands at the shared hub panel is someone in the
  owner's home, not necessarily the owner (security fix, 2026-10-11).
* ``may_reach_people`` — ``send_message`` is allowed at all (then the
  recipient rule applies), and so is a custom MCP tool, which may reach
  people through its own service (``scope_refusal``). ``may_push`` —
  ``tell_owner(urgent=True)``.
* ``reply_to`` — the chats this turn was asked in: the reply check.
* ``max_turn_usd`` / ``deadline_s`` — the per-turn dollar cap and a
  graceful deadline.
* ``third_party`` — a friend's ping. It narrows nothing here: what a
  friend's ping may not do is ``pings.scope``'s, asked at the tool
  boundary for every call. By default (2026-10-12) that is the owner's
  machine — the shell and the file editor, the owner's Chrome, the screens,
  and ``look`` at the owner's display, Chrome or local files — plus a
  custom MCP tool, and any send to someone who asked Edward nothing.
  ``EDWARD_FRIENDS_FULL_TOOLSET=on`` gives friends the full toolset (the
  reference host's 2026-10-11 decision); the send rule and the custom MCP
  refusal hold either way.
* ``final_text_is_reply`` — the caller posts the turn's final text into the
  chat (the ping runner), so a final answer satisfies the reply check.
  ``None`` = yes for ping turns.
* ``dry_run_outward`` — the judgement check: outward calls are recorded,
  never executed.
* ``may_schedule`` / ``may_wake_later`` — ``schedule(add)`` of a task or
  reminder / of a self-wake. An event fires later as its own turn with a
  fired event's (or a wake's) reach, so a turn that may not reach anyone
  — a surplus job, the pulse — must not leave one behind: a text at 9 am
  from a 3 am reflection (mind_service, 2026-09-26; review 2026-10-11).
  A legacy list says which by naming ``schedule_event`` / ``wake_me``.
* ``may_change_owners_calendar()`` — ``schedule``'s calendar and Reminders
  writes (add, move, delete, invite; add, check off, delete): when the
  owner asks (``owner_asked``), one line to flip for Edward's own
  initiative. Reads are open in every turn (design decision, 2026-10-11:
  restrict little; only writes and sends wait for the owner).

A policy nobody set is **fail-closed** for every turn the owner is not part of:
a wake that forgot to set one fails visibly ("this turn can't message
people"), never silently sends.

The rails here are deterministic (pure functions over the call and the
policy); the database lookups the recipient rule needs are injectable.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from services.owner import owner_name, owner_text

logger = logging.getLogger(__name__)

# Turns in which the owner is speaking. ``ping_owner`` is their own ping
# (their self-chat).
OWNER_SOURCES = frozenset({"chat", "voice", "widget", "alexa", "ping_owner"})
# Live voice on the household hub (``/hub/ask``): someone in the owner's
# home, not necessarily the owner. It used to run as ``voice`` and so as the
# owner — anyone at the panel could have Edward text anyone from the owner's
# number (fixed 2026-10-11). Never in a list that means the owner is speaking.
HUB_VOICE_SOURCE = "hub_voice"
# Ping reply turns (the ping runner), whose final text the runner posts.
PING_SOURCES = frozenset({"ping", "ping_owner"})
# ``memory(forget)`` — the owner's own conversation only (tools.FORGET_TURN_SOURCES).
FORGET_SOURCES = frozenset({"chat", "voice", "widget", "ping_owner"})

TURN_MAX_USD_ENV = "EDWARD_TURN_MAX_USD"
AUTONOMOUS_TURN_MAX_USD_ENV = "EDWARD_AUTONOMOUS_TURN_MAX_USD"
DEFAULT_TURN_MAX_USD = 3.00
DEFAULT_AUTONOMOUS_TURN_MAX_USD = 1.00

# The legacy send tools a caller's ``allowed_tools`` may still name.
LEGACY_SEND_NAMES = frozenset({"send_imessage", "send_message", "send_sms", "send_whatsapp"})
LEGACY_PUSH_NAMES = frozenset({"send_push_notification"})
# A legacy list naming one of these may leave an event behind (a task or
# reminder); ``wake_me`` alone, only a self-wake (reflect_lite).
LEGACY_SCHEDULE_NAMES = frozenset({"schedule_event", "schedule"})
LEGACY_WAKE_NAMES = LEGACY_SCHEDULE_NAMES | {"wake_me"}
# A legacy ``required_tools`` naming one of these asks for a reply.
LEGACY_REPLY_NAMES = LEGACY_SEND_NAMES | {"stand_down"}


def _env_usd(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class TurnPolicy:
    source: str = "background"
    owner_asked: bool = False
    may_reach_people: bool = False
    may_push: bool = False
    reply_to: Tuple[str, ...] = ()
    max_turn_usd: Optional[float] = None
    # Each model call's output ceiling for this turn (``max_tokens``, thinking
    # included), never above the route's: the per-turn dollar cap is soft —
    # it stops new rounds, not the one running nor the closing call — so a
    # run that must stay under a ceiling also bounds each call (review
    # 2026-10-11: one free run could land at $0.6–0.8 against ≈ $0.30–0.50).
    max_output_tokens: Optional[int] = None
    deadline_s: Optional[float] = None
    third_party: bool = False
    final_text_is_reply: Optional[bool] = None
    dry_run_outward: bool = False
    may_schedule: bool = True
    may_wake_later: bool = True
    # Legacy ``required_tools`` without ``reply_to``: the reply check is
    # satisfied by any successful send, stand-down or tell_owner.
    any_reply: bool = False

    def __post_init__(self) -> None:
        raw = self.reply_to
        if isinstance(raw, str):
            raw = (raw,)
        cleaned = tuple(str(c).strip() for c in (raw or ()) if c is not None and str(c).strip())
        object.__setattr__(self, "reply_to", cleaned)
        object.__setattr__(self, "source", str(self.source or "background"))

    def wants_reply(self) -> bool:
        return bool(self.reply_to) or self.any_reply

    def final_text_answers(self) -> bool:
        if self.final_text_is_reply is not None:
            return bool(self.final_text_is_reply)
        return self.source in PING_SOURCES

    def turn_cap_usd(self) -> float:
        """The per-turn dollar cap: explicit, else the env default
        for whose turn it is."""
        if self.max_turn_usd is not None and self.max_turn_usd > 0:
            return float(self.max_turn_usd)
        if self.owner_asked:
            return _env_usd(TURN_MAX_USD_ENV, DEFAULT_TURN_MAX_USD)
        return _env_usd(AUTONOMOUS_TURN_MAX_USD_ENV, DEFAULT_AUTONOMOUS_TURN_MAX_USD)

    def may_forget(self) -> bool:
        return self.source in FORGET_SOURCES

    def may_change_owners_calendar(self) -> bool:
        """Calendar and Reminders writes — add, move, complete, delete, invite — happen when the owner asks
        (2026-10-11). Flip this one line to let Edward act on his own initiative."""
        return self.owner_asked


def _scheduled_event_is_owners() -> bool:
    """A fired event's turn: ``scheduler_service`` runs an event the owner
    did not make under ``llm_spend.own_work_scope`` (``event_is_owners``: the
    REST routes, an owner turn's ``created_in`` stamp, or an old event
    outside Edward's own threads). Outside that scope the event is theirs."""
    try:
        from services import llm_spend

        return not llm_spend.in_own_work()
    except Exception:  # noqa: BLE001 — unknown is not the owner
        return False


def default_policy(source: Optional[str]) -> TurnPolicy:
    """The policy for a turn whose caller set none."""
    s = (source or "").strip() or "background"
    if s in OWNER_SOURCES:
        return TurnPolicy(source=s, owner_asked=True, may_reach_people=True, may_push=True)
    if s == HUB_VOICE_SOURCE:
        # Someone at the hub may ask for a text (the recipient rule decides
        # where; tell_owner reaches the owner), a push or a reminder — never a wake
        # later, which would run as Edward's own with nobody there.
        return TurnPolicy(source=s, owner_asked=False, may_reach_people=True, may_push=True, may_schedule=True,
                          may_wake_later=False)
    if s == "scheduled_event":
        return TurnPolicy(source=s, owner_asked=_scheduled_event_is_owners(), may_reach_people=True, may_push=True)
    # twilio (the router sends the reply), mind, pulse, heartbeat, worker,
    # a ping without a policy, background — and anything new: fail-closed.
    return TurnPolicy(source=s)


def legacy_policy(source: Optional[str], *, allowed_tools: Optional[Iterable[str]] = None,
                  required_tools: Optional[Iterable[str]] = None) -> TurnPolicy:
    """The policy for a caller that still speaks the old arguments (one
    that does not pass ``policy=``): ``allowed_tools`` naming a send
    tool means the turn may reach people, ``send_push_notification`` that
    it may push, ``schedule_event`` that it may leave an event behind and
    ``wake_me`` that it may wake itself later; ``required_tools`` naming a
    send turns on the reply check."""
    p = default_policy(source)
    if allowed_tools is not None:
        names = {str(n) for n in allowed_tools}
        p = replace(p, may_reach_people=bool(names & LEGACY_SEND_NAMES), may_push=bool(names & LEGACY_PUSH_NAMES),
                    may_schedule=bool(names & LEGACY_SCHEDULE_NAMES), may_wake_later=bool(names & LEGACY_WAKE_NAMES))
    if required_tools and any(str(t) in LEGACY_REPLY_NAMES for t in required_tools):
        # Asked to answer (a mention): the turn may reply — the recipient
        # rule still decides where — unless its list said no sends at all.
        reach = p.may_reach_people or allowed_tools is None
        p = replace(p, any_reply=True, may_reach_people=reach)
    return p


# ------------------------------------------------------------ current --

_CURRENT: "contextvars.ContextVar[Optional[TurnPolicy]]" = contextvars.ContextVar(
    "edward_turn_policy", default=None)


def policy_is_set() -> bool:
    return _CURRENT.get() is not None


def current_policy() -> TurnPolicy:
    """This turn's policy; outside a scope, the default for the turn the
    ledger has open (fail-closed when there is none)."""
    p = _CURRENT.get()
    if p is not None:
        return p
    source = None
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
        source = getattr(rec, "source", None) if rec is not None else None
    except Exception:  # noqa: BLE001
        source = None
    return default_policy(source)


def set_policy(policy: Optional[TurnPolicy]) -> "contextvars.Token":
    return _CURRENT.set(policy)


def reset_policy(token: "contextvars.Token") -> None:
    try:
        _CURRENT.reset(token)
    except ValueError:  # reset from another context (a cancelled generator): clear instead
        _CURRENT.set(None)


@contextmanager
def policy_scope(policy: Optional[TurnPolicy]) -> Iterator[Optional[TurnPolicy]]:
    token = set_policy(policy)
    try:
        yield policy
    finally:
        reset_policy(token)


def custom_mcp_reach_refusal(name: str) -> str:
    return (f"Tool error: {name} comes from a server {owner_name()} installed, and I can't tell whether it "
            "reaches people — this turn reaches nobody. If it matters, tell_owner what you'd do with it.")


def _is_custom_mcp(name: str) -> bool:
    try:
        from services.toolset.names import is_custom_mcp

        return is_custom_mcp(name)
    except Exception:  # noqa: BLE001 — the registry tags through the same module: none are bound
        return False


def scope_refusal(policy: TurnPolicy, name: str, args: Any) -> Optional[str]:
    """What this turn's policy refuses before a call runs (the executor
    asks for every call). A friend's ping is narrowed at the tool boundary
    (``pings.scope.refusal``: by default the owner's machine is off,
    ``EDWARD_FRIENDS_FULL_TOOLSET=on`` lifts that), not here; the real rails
    (never text the owner from someone else's turn, the recipient rule,
    payments, Chrome typing, the egress secret scan) hold for every turn.
    One rule lives here: a custom MCP tool (a server the owner installed)
    may post, mail or pay through its own service and nothing can tell
    which, so a turn that may not reach people — the pulse, free time, the
    nightly, a turn with no policy — is refused it, as it is refused
    ``send_message`` (review 2026-10-11). Someone at the hub may reach
    people through the recipient rule, but a custom tool's reach can't be
    checked, so for someone who isn't the owner it is the owner's call, as
    in a friend's ping."""
    n = str(name or "")
    if not _is_custom_mcp(n):
        return None
    if not policy.may_reach_people:
        return custom_mcp_reach_refusal(n)
    if policy.source == HUB_VOICE_SOURCE:
        owner = owner_name()
        return (f"Tool error: {n} comes from a server {owner} installed, and I can't tell whether it reaches "
                f"people — from the hub that's {owner}'s call. If it matters, tell_owner what you'd do with it.")
    return None


# --------------------------------------------------------- Chrome rails --

# Hosts where typing is a search, not a message (taint_service).
def _search_hosts() -> frozenset:
    try:
        from services.taint_service import SEARCH_TYPING_HOSTS

        return frozenset(SEARCH_TYPING_HOSTS)
    except Exception:  # noqa: BLE001
        return frozenset({"www.youtube.com", "youtube.com", "www.google.com", "google.com"})


# Edward's own stage (``/screen``) is his to type on. Its hosts come from
# ``host_config.own_hosts()`` (EDWARD_PUBLIC_BASE_URL, EDWARD_PUBLIC_SCREEN_URL,
# localhost and this Mac's Bonjour name), read per call.
def own_screen_hosts() -> frozenset:
    """The hosts that serve Edward's own ``/screen``: ``host_config.own_hosts()``
    plus the ``www.`` form of each public hostname."""
    from services.host_config import own_hosts

    hosts = set(own_hosts())
    for h in list(hosts):
        if ("." in h and not h.startswith("www.") and not h.endswith(".local")
                and not h.replace(".", "").isdigit()):
            hosts.add("www." + h)
    return frozenset(hosts)


def __getattr__(name: str) -> Any:
    # ``OWN_SCREEN_HOSTS`` was a fixed set; it is now computed on each read
    # so a changed EDWARD_PUBLIC_BASE_URL takes effect without a restart.
    if name == "OWN_SCREEN_HOSTS":
        return own_screen_hosts()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


PAYMENT_RE = re.compile(
    r"\b(place\s+(?:your\s+)?order|buy\s+now|pay(?:ment)?|checkout|check\s+out|purchase|"
    r"confirm\s+(?:order|payment|purchase)|submit\s+order|add\s+(?:a\s+)?card|complete\s+purchase)\b",
    re.IGNORECASE)
ACTING_RE = re.compile(
    r"\b(send|post|reply|reply\s+all|publish|delete|remove|accept|decline|confirm|submit|unsubscribe|"
    r"sign\s+out|log\s+out|transfer|tweet|share)\b",
    re.IGNORECASE)
_CARD_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def looks_like_card_number(text: Any) -> bool:
    for run in _CARD_RUN.findall(str(text or "")):
        digits = re.sub(r"\D", "", run)
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def is_payment_label(text: Any) -> bool:
    return bool(PAYMENT_RE.search(str(text or "")))


def _host(url: Optional[str]) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _typing_is_free(url: Optional[str]) -> bool:
    """A search box, or Edward's own /screen."""
    host = _host(url)
    if not host:
        return False
    if host in _search_hosts():
        return True
    try:
        path = urlparse(str(url)).path or ""
    except ValueError:
        path = ""
    return host in own_screen_hosts() and path.rstrip("/").endswith("/screen")


PAYMENT_REFUSAL = owner_text("Tool error: that's a payment step — {OWNER} does those themselves; tell_owner it's "
                             "ready (say where) and stop there.")


def _typing_refusal(host: str) -> str:
    where = host or "a page I can't place"
    owner = owner_name()
    return (f"Tool error: typing into {where} in {owner}'s Chrome acts as {owner} — only when they ask in this "
            "turn. tell_owner what you'd type and where.")


def _acting_refusal(label: str) -> str:
    owner = owner_name()
    return (f"Tool error: clicking \"{label.strip()[:60]}\" in {owner}'s Chrome acts as {owner} — only when they "
            "ask in this turn. tell_owner what you'd do.")


def _steps(value: Any) -> List[Any]:
    try:
        from services.secret_scan import _steps as parse

        return parse(value)
    except Exception:  # noqa: BLE001
        return list(value) if isinstance(value, (list, tuple)) else []


def _step_label(step: dict) -> str:
    return " ".join(str(step.get(k) or "") for k in ("label", "name", "text_of", "element", "target_text")).strip()


def chrome_action_allowed(action: Any, args: Any = None, policy: Optional[TurnPolicy] = None, *,
                          current_url: Optional[str] = None,
                          element_text: Optional[str] = None) -> Optional[str]:
    """``None`` when this ``chrome`` call may run, else the refusal
    (``Tool error: …``). ``element_text`` is the label of the element a
    click or type lands on (from the last read), ``current_url`` the working
    tab's page; both may be unknown.

    1. Payments, in **every** turn: a click or type on a payment control,
       or typing a card number (Luhn).
    2. Typing outside a search box or Edward's /screen: the owner's turn
       only.
    3. Clicks and keys on a control that acts as the owner (Send, Post,
       Delete, Unsubscribe, …): the owner's turn only.
    """
    policy = policy or current_policy()
    act = str(action or "").strip().lower()
    a = args if isinstance(args, dict) else {}

    if act in ("click", "type", "key"):
        if element_text and is_payment_label(element_text):
            return PAYMENT_REFUSAL
    if act == "type":
        if looks_like_card_number(a.get("text")):
            return PAYMENT_REFUSAL
        if not policy.owner_asked and not _typing_is_free(current_url):
            return _typing_refusal(_host(current_url))
        return None
    if act in ("click", "key"):
        if not policy.owner_asked and element_text and ACTING_RE.search(element_text):
            return _acting_refusal(element_text)
        return None
    if act == "steps":
        url = current_url
        for step in _steps(a.get("steps")):
            if not isinstance(step, dict):
                continue
            kind = str(step.get("action") or "").strip().lower()
            label = _step_label(step)
            if kind in ("click", "type", "key") and label and is_payment_label(label):
                return PAYMENT_REFUSAL
            if kind == "open":
                url = str(step.get("url") or "")
            elif kind == "type":
                if looks_like_card_number(step.get("text")):
                    return PAYMENT_REFUSAL
                if not policy.owner_asked and not _typing_is_free(url):
                    return _typing_refusal(_host(url))
            elif kind in ("click", "key", "back"):
                if not policy.owner_asked and label and ACTING_RE.search(label):
                    return _acting_refusal(label)
                url = None  # a click can navigate: the next typing's page is unknown
        return None
    return None


# ------------------------------------------------- the recipient rule --

ADDRESSED_HOURS = 24
IN_CONVERSATION_DAYS = 7
IN_CONVERSATION_REPLY_HOURS = 2
_MENTION_RE = re.compile(r"@edward\b", re.IGNORECASE)

AddressedLookup = Callable[[str, float], Awaitable[bool]]
ConversationLookup = Callable[[str], Awaitable[bool]]


def _same_chat(a: Optional[str], b: Optional[str]) -> bool:
    try:
        from services.graph.tools import same_chat

        return same_chat(a, b)
    except Exception:  # noqa: BLE001
        return bool(a) and str(a).strip().lower() == str(b or "").strip().lower()


def in_reply_to(policy: TurnPolicy, target: Optional[str]) -> bool:
    return any(_same_chat(target, chat) for chat in policy.reply_to)


async def addressed_recently(chat: str, hours: float = ADDRESSED_HOURS) -> bool:
    """Rule (b): the chat addressed Edward (an @mention, a reply to him,
    the owner's self-chat ping) within ``hours`` — the ping records
    (``ping_service.addressed_recently``). Without them, the heartbeat
    events themselves: an ``@edward`` in that chat, or the owner's own
    self-chat ping. False on any error (fail-closed)."""
    try:
        from services import ping_service  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        ping_service = None
    if ping_service is not None and hasattr(ping_service, "addressed_recently"):
        try:
            return bool(await ping_service.addressed_recently(chat, hours))
        except Exception as exc:  # noqa: BLE001
            logger.warning("addressed_recently (pings) failed: %s", exc)
            return False
    return await _addressed_in_heartbeat(chat, hours)


async def _addressed_in_heartbeat(chat: str, hours: float) -> bool:
    """Only rows that could count — an ``@edward`` in the text or the owner's own
    self-chat ping — are loaded, so a busy day in other chats can't push an
    earlier mention out of the limit (review 2026-10-11: 547 iMessage
    events in one day against a limit of 500 across every chat)."""
    try:
        from sqlalchemy import or_, select

        from services.database import HeartbeatEventModel, async_session
        from services.heartbeat.mind_path import _is_edwards_echo, _is_owner_direct, _text

        since = datetime.utcnow() - timedelta(hours=float(hours))
        async with async_session() as session:
            rows = (await session.execute(
                select(HeartbeatEventModel).where(
                    HeartbeatEventModel.created_at >= since,
                    HeartbeatEventModel.source == "imessage",
                    or_(HeartbeatEventModel.summary.ilike("%@edward%"),
                        HeartbeatEventModel.raw_data.ilike("%@edward%"),
                        HeartbeatEventModel.raw_data.ilike("%owner_direct%")),
                ).order_by(HeartbeatEventModel.created_at.desc()).limit(500)
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("addressed_recently (heartbeat fallback) failed: %s", exc)
        return False
    for row in rows:
        if not _same_chat(chat, row.chat_identifier or row.sender):
            continue
        try:
            if _is_edwards_echo(row):
                continue
            if _is_owner_direct(row) or _MENTION_RE.search(_text(row) or ""):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def conversation_is_live(sends: Sequence[datetime], inbound: Sequence[datetime], *,
                         reply_hours: float = IN_CONVERSATION_REPLY_HOURS) -> bool:
    """Rule (c), pure: Edward's latest send in the chat was answered by
    someone other than the owner within ``reply_hours``."""
    sends = sorted(d for d in (_aware(s) for s in sends) if d is not None)
    if not sends:
        return False
    last = sends[-1]
    window = timedelta(hours=reply_hours)
    return any(last < d <= last + window for d in (_aware(i) for i in inbound) if d is not None)


async def in_conversation(chat: str) -> bool:
    """Rule (c): Edward sent into this chat in the last 7 days
    (``imessage_outgoing_sends``) and someone other than the owner (an inbound
    heartbeat event, ``is_from_user`` false) answered his latest send
    within 2 h. False on any error."""
    try:
        from sqlalchemy import select

        from services.database import HeartbeatEventModel, ImessageOutgoingSendModel, async_session

        since = datetime.now(timezone.utc) - timedelta(days=IN_CONVERSATION_DAYS)
        async with async_session() as session:
            sent = (await session.execute(
                select(ImessageOutgoingSendModel.chat_identifier, ImessageOutgoingSendModel.sent_at)
                .where(ImessageOutgoingSendModel.sent_at >= since).limit(5000)
            )).all()
            sends = [at for c, at in sent if _same_chat(chat, c)]
            if not sends:
                return False
            first = min(_aware(s) for s in sends).astimezone(timezone.utc).replace(tzinfo=None)
            got = (await session.execute(
                select(HeartbeatEventModel.chat_identifier, HeartbeatEventModel.sender,
                       HeartbeatEventModel.created_at)
                .where(HeartbeatEventModel.created_at >= first, HeartbeatEventModel.is_from_user.is_(False))
                .limit(5000)
            )).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("in_conversation lookup failed: %s", exc)
        return False
    inbound = [at for c, s, at in got if _same_chat(chat, c or s)]
    return conversation_is_live(sends, inbound)


def not_asked_refusal(who: str) -> str:
    return (f"Tool error: {who} hasn't asked you anything and you're not in a conversation with them — "
            f"messaging them is {owner_name()}'s call. If it matters, tell_owner what you'd send.")


async def recipient_refusal(target: str, policy: TurnPolicy, *, who: Optional[str] = None,
                            addressed: Optional[AddressedLookup] = None,
                            conversation: Optional[ConversationLookup] = None) -> Optional[str]:
    """The recipient rule (docs/architecture/tool-loop.md): a send is
    allowed when (a) the owner asked in this turn, the chat is one this turn
    was asked in (``reply_to``), (b) the chat addressed Edward in the last
    24 h, or (c) Edward is in a live conversation there. Otherwise messaging
    that person is the owner's call. ``None`` = allowed."""
    if policy.owner_asked or in_reply_to(policy, target):
        return None
    addressed = addressed or addressed_recently
    conversation = conversation or in_conversation
    try:
        if await addressed(target, ADDRESSED_HOURS):
            return None
        if await conversation(target):
            return None
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.warning("recipient rule lookup failed for %s: %s", target, exc)
    return not_asked_refusal(who or target)
