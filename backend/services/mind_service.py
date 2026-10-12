"""The wake — Edward thinking when nobody is talking to him.

See docs/architecture/mind.md and noticing.md; proactivity was rebuilt as
noticing, not a schedule, on 2026-10-11. One primitive:
``wake(reason)`` runs an ordinary Edward turn (``chat_with_memory``) with a
raw pack, under a ``TurnPolicy`` that says whom it may reach, records what it
decided as a ``ledger_events`` row (kind ``mind``), and never raises. What it
decides it writes to memory itself (``remember``); what it did and what came
of it returns in the next pack.

Reasons: ``manual`` (the owner or an operator), ``self`` (a wake he
scheduled — a promise made in a ping is followed up in that chat,
``event_meta.ping``), ``mention`` (an ``@edward`` while the ping runner is
off), ``surfaces`` (the pulse), ``speak`` (the noticing speaker, under its
caller's policy) and
``nightly`` (03:00, silent; skipped in code when nothing happened; Sunday
runs the decay pass). ``messages`` is only a shim: inbound is noticed
(``noticing_service.request_notice``), never woken for. Gone (2026-10-11):
the morning and evening ticks, the Sunday 09:00 reflect and the surplus
queue's reflect_lite, research, prep and free time.

The pack carries no score, boost, priority or suppression: the mind sees
the evidence, not a verdict. ``render_pack`` is the only place the pack
becomes text and is pure.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
import weakref
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services import thought_journal
from services.owner import owner_name, owner_text
from services.graph import chat_with_memory
from services.expression_service import quota_state
from services.memory_decay_service import decay_stale_memories


def _now() -> datetime:
    """The wall clock, aware UTC (tests pin it)."""
    return datetime.now(timezone.utc)


def route_for_wake(reason: str, turn_source: Optional[str], settings_model: str) -> Tuple[str, Optional[str]]:
    """(model, reasoning effort) for one wake. Pure; the knobs live in llm_utils.

    The pulse runs on the pulse knobs; every other wake on the mind's
    (``EDWARD_MIND_MODEL``, or the Claude ``mind`` route), except that a
    ``speak`` wake runs on ``EDWARD_SPEAK_MODEL`` when it is set. The
    spare-quota raise (``effort_with_headroom``) went with the surplus
    queue (2026-10-11)."""
    from services import llm_utils
    if turn_source == "pulse" or reason == "surfaces":
        model = llm_utils.pulse_model_for(settings_model)
        return model, llm_utils.pulse_effort(model=model)
    model = (_speak_model(settings_model) if reason == "speak" else None) or llm_utils.mind_model_for(settings_model)
    return model, llm_utils.mind_effort_for(reason, model=model)


def _speak_model(settings_model: str) -> Optional[str]:
    """``EDWARD_SPEAK_MODEL`` as the turn will build it: on Claude as named
    (``claude_model_for`` checks a requested Claude id), on Codex through
    the catalog. None when it is unset."""
    from services import llm_utils

    requested = (os.getenv("EDWARD_SPEAK_MODEL") or "").strip()
    if not requested:
        return None
    if llm_utils.anthropic_enabled():
        return requested
    return llm_utils._resolve_with_fallback(requested, settings_model, purpose="speak")


def routing_summary(settings_model: str) -> Dict[str, Any]:
    """What each kind of turn runs on right now, resolved the way the turn
    resolves it (``GET /api/settings/models``, 2026-09-26): an ordinary
    wake and each reason's effort through ``route_for_wake``, the pulse
    likewise, chat through ``chat_effort_for``. Efforts are the level the
    request will carry (``model_catalog.effort_for`` or "medium", as
    ``ChatCodexResponses`` sends it)."""
    from services import llm_utils, model_catalog

    if llm_utils.anthropic_enabled():
        # On Claude the table's effort is what every turn sends (the Codex
        # per-reason map does not apply).
        from services.claude_routing import claude_route

        chat, mind, pulse = claude_route("chat"), claude_route("mind"), claude_route("pulse")
        return {
            "provider": "anthropic",
            "mind_model": mind.model,
            "mind_effort": mind.effort,
            "mind_efforts": {reason: mind.effort for reason in WAKE_REASONS if reason != "surfaces"},
            "pulse_model": pulse.model,
            "pulse_effort": pulse.effort,
            "chat_model": chat.model,
            "chat_effort": chat.effort,
        }

    def sent(model: str, effort: Optional[str], turn_source: str) -> str:
        requested = llm_utils.chat_effort_for(model, effort, turn_source)
        return model_catalog.effort_for(model, requested) or "medium"

    mind_model, mind_effort = route_for_wake("self", "mind", settings_model)
    pulse_model, pulse_effort = route_for_wake("surfaces", "pulse", settings_model)
    chat_model = llm_utils.resolve_model(settings_model, purpose="chat")
    efforts = {}
    for reason in WAKE_REASONS:
        if reason == "surfaces":
            continue
        model, effort = route_for_wake(reason, "mind", settings_model)
        efforts[reason] = sent(model, effort, "mind")
    return {
        "mind_model": mind_model,
        "mind_effort": sent(mind_model, mind_effort, "mind"),
        "mind_efforts": efforts,
        "pulse_model": pulse_model,
        "pulse_effort": sent(pulse_model, pulse_effort, "pulse"),
        "chat_model": chat_model,
        "chat_effort": sent(chat_model, None, "chat"),
    }


MIND_CONVERSATION_ID = "edward-mind"
MIND_CONVERSATION_TITLE = "Edward's mind"
MIND_MAX_HISTORY_CHARS = 24_000
MIND_TIMEOUT_SECONDS = 240
# A mention must reply, and the old heartbeat path needed 420 s after
# overruns at 300 s delivered replies twice (2026-09-10).
MENTION_TIMEOUT_SECONDS = 420
# The speaker (noticing, 2026-10-11): one noticing, verified and said once,
# in its own thread with no history — the pack carries what was said today.
# Its caller passes 90 s when the item closes within half an hour.
SPEAK_CONVERSATION_ID = "edward-speak"
SPEAK_CONVERSATION_TITLE = "Edward's noticing"
SPEAK_TIMEOUT_SECONDS = 180
SPEAK_MAX_HISTORY_CHARS = 0
# The nightly (03:00, 2026-10-11) replaces the surplus queue's reflect_lite
# and the Sunday 09:00 reflect: a day's look on weeknights, the week's on
# Sunday, which also reads a week and may rewrite the state document.
NIGHTLY_CONVERSATION_ID = "edward-nightly"
NIGHTLY_CONVERSATION_TITLE = "Edward's nights"
NIGHTLY_TIMEOUT_SECONDS = 600
NIGHTLY_SUNDAY_TIMEOUT_SECONDS = 900
NIGHTLY_MAX_HISTORY_CHARS = 48_000
NIGHTLY_NOTHING_NOTE = "nothing new since last night"
# Contradiction triage after the nightly turn (its caller was the surplus
# queue): one question to the owner at most, by note, on its own bound.
NIGHTLY_TRIAGE_LIMIT = 5
NIGHTLY_TRIAGE_TIMEOUT_SECONDS = 240
# One budgeted free-time run a day (design decision, 2026-10-11: one
# budgeted run, ≈ $0.30–0.50 a run, for Edward's stage on the host Mac's
# screen and his own projects — no push, no send). Its thread is the old
# surplus free time's; the history is lean so one run stays inside the cap.
# The toolset's ``TurnPolicy.max_turn_usd`` is soft — it stops new rounds, not
# the round running nor the closing call after it — so every call of the run
# also writes at most ``FREE_MAX_OUTPUT_TOKENS`` (≈ $0.08 on Sonnet): $0.30
# plus one round and the closing call stays under $0.50 (with $0.40 and the
# mind route's 32,000 tokens a run could reach $0.6–0.8).
FREE_CONVERSATION_ID = "edward-free-time"
FREE_CONVERSATION_TITLE = "Edward's free time"
FREE_TIMEOUT_SECONDS = 480
FREE_MAX_HISTORY_CHARS = 12_000
FREE_MAX_TURN_USD = 0.30
FREE_MAX_OUTPUT_TOKENS = 8_000
# Threads the retired wakes wrote; old rows still live there, so they stay
# Edward's own (``EDWARD_OWN_THREAD_IDS``), though nothing writes them now.
RETIRED_THREAD_IDS = ("edward-research",)
# The pack is collected before the thread's lock, under its own deadline,
# and each source under a shorter one: a hung apple-mcp calendar call used
# to run inside the one wake lock with no deadline at all and freeze every
# later wake, pulse and reminder with no ledger row (2026-09-24).
PACK_TIMEOUT_SECONDS = 45
PACK_SOURCE_TIMEOUT_SECONDS = 15
# A wake that fails or times out counts an attempt on each escalation it
# was served; the third gives up on it rather than re-serving it forever.
MAX_WAKE_ATTEMPTS = 3
GAVE_UP_BATCH_ID = "mind:gave-up"
# Retrieval embeds this much of the pack's salient parts, not all of it.
MEMORY_QUERY_CHARS = 400
MAX_MESSAGES_IN_PACK = 30
MAX_THOUGHTS_IN_PACK = 20
MAX_ACTIONS_IN_PACK = 10
MAX_VERDICTS_IN_PACK = 10
MAX_ESCALATIONS_IN_PACK = 20
# An escalation's whole message, not the 200-character summary
# (2026-10-11): ``raw_data.full_text`` is capped at 4,000 by the
# listener; the pack shows up to this much of it.
EVENT_TEXT_MAX = 4_000
ESCALATION_TEXT_IN_PACK = 1_500
# Continuity (2026-09-25): the 24K thread window cannot hold even the last
# pack, so every pack lists the last few wakes from the ledger.
MAX_RECENT_WAKES_IN_PACK = 5
RECENT_WAKE_LINE_CHARS = 200
MAX_NOTE_CHARS = 300
THOUGHT_WINDOW_HOURS = 48
MESSAGE_WINDOW_HOURS = 6
# Pings in the pack: the last few hours of the ping runner's reply path.
PING_PACK_HOURS = 6
# The Sunday reflection looks back a week: the mind's ledger, every push
# and its outcome, the owner's verdicts, what Edward did, what he wished.
WEEK_DAYS = 7
MAX_WEEK_WAKES = 40
MAX_WEEK_DID = 40
MAX_WEEK_VERDICTS = 30
MAX_WEEK_PUSHES = 40
# The state document: the summary he carries between wakes.
STATE_DOC_TITLE = "Edward: state"
STATE_DOC_TAGS = "state,self"
STATE_DOC_MAX_CHARS = 4000

# The reasons a wake runs for. ``messages`` is accepted only as the
# ``request_wake`` shim (inbound is noticed, not woken for).
REASONS = ("manual", "self", "mention", "surfaces", "speak", "nightly", "free", "messages")
WAKE_REASONS = tuple(r for r in REASONS if r != "messages")

# Matches ``triage_service.MENTION_PATTERN``: an ``@edward`` is someone
# waiting on a reply, never inbound to store away (the shim skips it).
MENTION_RE = re.compile(r"@edward\b", re.IGNORECASE)

# ---- tools: the legacy tuples (2026-10-11) ----------------------------------------------
#
# Under the one toolset a wake's reach is its ``TurnPolicy``; tool lists
# are ignored. Under ``EDWARD_TOOLSET=legacy`` the old tools check no policy,
# so these tuples (passed as ``allowed_tools``) are what bounds a wake there.
# They go after the one-week rollback window. Never ``forget_about``:
# forgetting is the owner's call, made in a chat. The goal and idea tools are gone
# from the mind (2026-10-11).
LEGACY_WAKE_TOOLS: tuple[str, ...] = (
    "remember", "remember_search", "graph_memory_search", "remember_update", "remember_forget",
    "update_memory_confidence",
    # Closing an intention keeps its history (M4, 2026-09-27).
    "close_intention",
    "search_documents", "read_document", "save_document", "edit_document",
    "tell_owner", "leave_note", "send_push_notification",
    "schedule", "schedule_event", "list_scheduled_events", "cancel_scheduled_event", "wake_me",
    "read_imessage_thread", "get_recent_messages", "lookup_contact", "list_chat_participants",
    "send_imessage", "send_message", "stand_down",
    "web_search", "fetch_page_content",
    "review_heartbeat",
    "wish_for_feature", "list_wishlist",
    "get_widget_state_tool", "update_widget", "show_on_screen", "clear_screen",
    # A line on the host Mac's screen (the stage, 2026-09-28).
    "say_on_screen",
    "how_do_i_know",
)
# Sends to people, and what reaches the owner's phone.
REACH_PEOPLE_TOOLS = frozenset({"send_imessage", "send_message", "send_sms", "send_whatsapp"})
PUSH_TOOLS = frozenset({"send_push_notification"})
# The nightly: nobody is awake. No send, no push, no surface, no
# ``schedule_event`` (it fires a full-tool turn later — a text at 9 am from a
# 3 am reflection); ``wake_me`` stays, a self wake has every guard. A note
# reaches Today (``reach_service`` allows one a night).
_QUIET_DROPS = REACH_PEOPLE_TOOLS | PUSH_TOOLS | {
    "stand_down", "update_widget", "show_on_screen", "clear_screen", "say_on_screen", "review_heartbeat",
    "web_search", "fetch_page_content", "get_widget_state_tool", "schedule_event", "schedule"}
LEGACY_QUIET_TOOLS: tuple[str, ...] = tuple(t for t in LEGACY_WAKE_TOOLS if t not in _QUIET_DROPS)
# The speaker verifies, prepares and says one thing: reads, a note or
# ``tell_owner``, and only what its channel allows.
_SPEAK_BASE: tuple[str, ...] = (
    "remember_search", "graph_memory_search", "search_documents", "read_document", "save_document",
    "read_imessage_thread", "get_recent_messages", "lookup_contact", "list_chat_participants",
    "web_search", "fetch_page_content", "list_scheduled_events", "tell_owner", "leave_note",
)


# Free time: his own projects and his own stage — reading, remembering,
# documents, the wishlist, the web and the host Mac's screen (shown, never
# spoken there). No send, no push, no note, no ``schedule`` (a full-tool
# turn later), never the widget or the house screen, never raising Chrome.
LEGACY_FREE_TOOLS: tuple[str, ...] = (
    "remember", "remember_search", "graph_memory_search", "remember_update", "update_memory_confidence",
    "close_intention",
    "search_documents", "read_document", "save_document", "edit_document",
    "list_scheduled_events", "web_search", "fetch_page_content",
    "wish_for_feature", "list_wishlist", "how_do_i_know",
    "compose_screen", "say_on_screen",
)


def legacy_speak_tools(channel: str) -> tuple:
    """The speak wake's legacy tools for a gate channel (``note``, ``push``,
    ``thread``): a push adds the push tool, a thread the sends. Pure."""
    extra: tuple = ()
    if channel == "push":
        extra = ("send_push_notification",)
    elif channel == "thread":
        extra = ("send_push_notification", "send_imessage", "send_message")
    return _SPEAK_BASE + extra


def legacy_tools_for(reason: str, values: Dict[str, Any]) -> tuple:
    """The legacy ``allowed_tools`` a wake gets from its policy: the
    nightly's quiet set, else the wake set without sends when it may not
    reach people and without the push tool when it may not push. Pure."""
    if reason == "nightly":
        return LEGACY_QUIET_TOOLS
    if reason == "free":
        return LEGACY_FREE_TOOLS
    drop: Set[str] = set()
    if not values.get("may_reach_people"):
        drop |= REACH_PEOPLE_TOOLS
    if not values.get("may_push"):
        drop |= PUSH_TOOLS
    return tuple(t for t in LEGACY_WAKE_TOOLS if t not in drop)


def serves_escalations(reason: str) -> bool:
    """Is a wake for this reason shown the escalations (and does it stamp
    them considered)? Only a mention: since 2026-10-11 nothing else wakes
    for inbound — the noticer (or its stub) handles the rest. Pure."""
    return reason == "mention"


# ---- the policy (the toolset's TurnPolicy, feature-detected) ---------------------------

# Whom each reason may reach when its caller passes no policy: a mention
# answers where it was asked; a manual wake may tell the owner; the
# nightly and the pulse reach nobody; ``self`` depends on whether
# it keeps a promise made in a chat (``follow_through_for``); ``speak``
# always brings its own, so without one it is fail-closed.
_REACH_BY_REASON: Dict[str, Tuple[bool, bool]] = {
    "mention": (True, True),
    "manual": (False, True),
    "self": (False, True),
    "nightly": (False, False),
    "surfaces": (False, False),
    "speak": (False, False),
    "free": (False, False),
}


def policy_values(reason: str, *, reply_to: Sequence[str] = (), follow_through: Any = None,
                  timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """The fields of a wake's default ``TurnPolicy``. Pure."""
    may_reach, may_push = _REACH_BY_REASON.get(reason, (False, False))
    owner_asked = False
    reply: tuple = tuple(c for c in reply_to if c)
    values: Dict[str, Any] = {}
    if reason == "self" and follow_through is not None:
        may_reach, may_push = True, True
        reply = tuple(follow_through.reply_to)
        owner_asked = bool(follow_through.requested_by_owner)
        # A look that finds nothing ends with one line here, never a
        # "nothing yet" posted into the group: its closing text answers
        # the loop's reply check. Only a mention is nudged.
        values["final_text_is_reply"] = True
    if reason == "free":
        values["max_turn_usd"] = FREE_MAX_TURN_USD
        values["max_output_tokens"] = FREE_MAX_OUTPUT_TOKENS
    if not may_reach:
        reply = ()
    return {
        "source": "pulse" if reason == "surfaces" else "mind",
        "owner_asked": owner_asked,
        "may_reach_people": may_reach,
        "may_push": may_push,
        "reply_to": reply,
        "deadline_s": (float(timeout_s) - 30.0) if timeout_s and timeout_s > 60 else timeout_s,
        **values,
    }


def _turn_policy_cls() -> Any:
    try:
        from services.toolset.policy import TurnPolicy
    except ImportError:
        return None
    return TurnPolicy


def make_policy(values: Dict[str, Any]) -> Any:
    """A ``TurnPolicy`` from ``policy_values``, or None when the toolset's module
    is not there (the legacy tools then bound the wake). Only the fields the
    class has are passed."""
    cls = _turn_policy_cls()
    if cls is None:
        return None
    try:
        from dataclasses import fields as dc_fields

        names = {f.name for f in dc_fields(cls)}
    except TypeError:
        names = set(inspect.signature(cls).parameters)
    return cls(**{k: v for k, v in values.items() if k in names})


_ACCEPTS_POLICY: "weakref.WeakKeyDictionary[Any, bool]" = weakref.WeakKeyDictionary()


def _accepts_policy(fn: Any) -> bool:
    """Does this turn function take ``policy=``? (cached per function)"""
    try:
        return _ACCEPTS_POLICY[fn]
    except (KeyError, TypeError):
        pass
    try:
        params = inspect.signature(fn).parameters.values()
        ok = any(p.name == "policy" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    except (TypeError, ValueError):
        ok = False
    try:
        _ACCEPTS_POLICY[fn] = ok
    except TypeError:
        pass
    return ok


def _policy_kwargs(fn: Any, policy: Any, allowed_tools: Optional[Sequence[str]]) -> Dict[str, Any]:
    """The turn's ``policy`` (only when ``fn`` takes one) and its legacy
    ``allowed_tools`` (always: v2 ignores them, legacy needs them)."""
    kw: Dict[str, Any] = {}
    if policy is not None and _accepts_policy(fn):
        kw["policy"] = policy
    if allowed_tools is not None:
        kw["allowed_tools"] = list(allowed_tools)
    return kw


# Tools whose successful use means the wake *did* something, as opposed to
# looking. Used only to label the ledger row; nothing gates on it.
ACTING_TOOLS = {
    "remember", "remember_update", "remember_forget", "update_memory_confidence", "close_intention",
    "save_document", "edit_document", "tell_owner", "leave_note", "send_push_notification",
    "schedule", "schedule_event", "cancel_scheduled_event", "wake_me",
    "send_imessage", "send_message", "send_sms", "send_whatsapp", "stand_down",
    "wish_for_feature", "update_wish",
    "set_goal", "update_goal", "suggest_idea",
    "update_widget", "update_widget_code", "clear_widget_code",
    "show_on_screen", "clear_screen", "compose_screen", "say_on_screen", "reset_screen_layout",
    "delete_email", "move_to_junk", "archive_gmail_message", "delete_gmail_message",
}

MIND_SYSTEM_PROMPT = owner_text("""## Your mind

Nobody is talking to you. This is a wake: a moment you take on your own to look at what has happened and decide whether any of it needs you. What {OWNER} asked of you is judgment, not a schedule — nothing here is scripted, and nothing forces an action.

The pack below is raw: recent messages as they arrived, the calendar, what your surfaces show, your own recent thoughts and intentions, what you did lately and what came of it, and {OWNER}'s verdicts on your earlier calls. It carries no scores or flags. You read it the way a person would.

A wake can end four ways, and any of them is a real outcome:
- **Act**: reply where you were asked, reach {OWNER}, change a surface, or schedule something. Reach {OWNER} with `tell_owner`: a note on Today when it can wait, `urgent=true` for a push only when it can't wait until they next look — at night, only for what can't wait until morning; say something worth reading. (Where `tell_owner` is not among your tools, `leave_note` and `send_push_notification` do the same.) You reach {OWNER} only by `tell_owner` (or the note and push tools) — never by `send_message`, `send_imessage`, `send_sms` or `send_whatsapp` to them, except to answer them in their own chat when they have just messaged you there. When you're unsure whether to act, don't: leave a note saying what you would do and why, and let {OWNER} decide.
- **Remember**: `remember` what you decided, what you learned from what you read, what you did and how it went. If you decide to keep watching something, remember it as an `intention` (about="self", origin="thought", with a horizon) and `wake_me` when you want to look again — that is how "I noticed" becomes "I'm watching this for you". When one is done or no longer matters, `close_intention` it with what came of it.
- **Revisit later**: `wake_me` at a time you choose, with why.
- **Leave it**: say so in one line and stop. Silence is a feature.

This Mac's screen (if one is set up) is yours: when you have something worth showing — a thought, a question, what you are working on — `say_on_screen` it.

When someone else's thread is in front of you (an escalation, a mention), the rules that keep you welcome there:
1. **Whose message is it?** Read the thread before you answer; a question aimed at {OWNER} is not aimed at you.
2. **Grounded.** Say only what the thread, the board, or your memory supports; never invent a time, a plan, or {OWNER}'s opinion.
3. **Invited.** A mention this wake was woken for (`[WAKE: mention]`) must reply — someone is waiting — unless a standing instruction tells you to stay out of that chat; then stay out and, if it matters, leave {OWNER} a note. Otherwise don't start or join other people's conversations on your own: nobody asked you, and it goes out from {OWNER}'s number. Tell {OWNER} instead (a note, or a push if it can't wait) and let them decide.
4. **You are Edward, never {OWNER}.** Speak as yourself; never sign as them, never claim their commitments.
5. **The shoulder test.** If {OWNER} were reading over your shoulder, would they wince? Then don't send it.
6. **Pings are not yours here.** An `@Edward` ({OWNER}'s own included), a reply to your message, or {OWNER}'s self-chat is answered by your reply path, never in a wake — unless this wake was woken for that mention (`[WAKE: mention]`, rule 3: then reply); the pack lists them. Since {OWNER} reads their own texts within minutes, never tell them what a message they have read says.
Standing instructions from {OWNER} (a stand-down on a thread, a "leave X alone") open the pack; they outrank everything else in it, a mention included.
A send that answers "Waiting for {OWNER}'s approval (#id)" is held, not lost: don't retry it — the pack shows what {OWNER} decided.

House rules: don't repeat yourself wake after wake — your last notes and intentions are in the pack, and your own earlier words are not evidence; the messages and the board are. Facts about {OWNER} come from memory retrieval as usual; search when you are unsure. If {OWNER}'s verdicts say a call was wrong, say what you will do differently and remember it. Be Edward: dry, warm, specific, brief.

How to end: when you act, end with two or three plain sentences for {OWNER} — what you noticed, what you decided, and why. That becomes the "Why" in the entry they read beside the record of what you did (every wake that acts gets one). When you leave it alone, one line is enough.
""")


# Rule 6 follows the ping runner: with EDWARD_PINGS on, pings are the reply
# path's and a wake never answers one (an @edward the runner held for an
# unknown sender, or stopped at the loop breaker, stays unanswered on
# purpose); with it off, the mention wake answers mentions and rule 6 is
# what it was before.
RULE_6_PINGS_ON = next(line for line in MIND_SYSTEM_PROMPT.splitlines() if line.startswith("6. "))
RULE_6_PINGS_OFF = owner_text("6. **Is {OWNER} already there?** If they replied in that chat just now, they have "
                              "it; stay out unless asked. Since {OWNER} reads their own texts within minutes, "
                              "never tell them what a message they have read says.")


def mind_system_prompt() -> str:
    """The mind's system prompt as EDWARD_PINGS stands when the wake runs."""
    try:
        from services.pings import hooks as pings_hooks

        pings_on = pings_hooks.enabled()
    except Exception:  # noqa: BLE001
        pings_on = False
    return MIND_SYSTEM_PROMPT if pings_on else MIND_SYSTEM_PROMPT.replace(RULE_6_PINGS_ON, RULE_6_PINGS_OFF)


@dataclass
class MindPack:
    now: datetime
    reason: str
    last_wake_at: Optional[datetime]
    attention: Dict[str, Any]
    messages: List[Dict[str, Any]]
    calendar: List[str]
    board: str
    notes: str
    pushes: str
    verdicts: List[Dict[str, Any]]
    thoughts: List[Any]
    actions: List[Any]
    surfaces: Dict[str, str]
    escalations: List[Dict[str, Any]] = field(default_factory=list)
    state_document: Optional[str] = None
    extra: Optional[str] = None
    # Only the nightly carries a window: the day, or on Sunday the week
    # (``collect_week``).
    week: Dict[str, Any] = field(default_factory=dict)
    # Sources that could not be read this wake ("calendar", "messages
    # (timed out)"), so an empty section is not mistaken for a quiet one.
    unavailable: List[str] = field(default_factory=list)
    # Sends held for the owner's approval, and who is standing at the hub
    # (2026-09-24).
    approvals: List[str] = field(default_factory=list)
    hub_presence: Optional[str] = None
    # The owner's standing instructions and live stand-downs (2026-09-25): they
    # outrank everything else in the pack, so they come first.
    instructions: List[str] = field(default_factory=list)
    # The mind's last few ledger rows, newest first (2026-09-25).
    recent_wakes: List[Dict[str, Any]] = field(default_factory=list)
    # The pings in the last few hours and how each ended
    # (``pings.hooks.pack_lines``), 2026-10-11.
    ping_lines: List[str] = field(default_factory=list)
    # A self wake that keeps a promise made in a chat (``follow_through_context``).
    follow_through: Optional[Dict[str, Any]] = None
    # The nightly's code-written noticing digest (``noticing_service.digest``).
    digest: List[str] = field(default_factory=list)


@dataclass
class _State:
    last_wake_at: Optional[datetime] = None
    pending: Dict[str, "asyncio.Task"] = field(default_factory=dict)
    # Reasons whose requested wake has started (its pack may already be
    # collected), and the one further wake asked for meanwhile.
    running: set = field(default_factory=set)
    rearm: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # When each requested wake is due (wall clock), for ``/api/mind/now``.
    due: Dict[str, datetime] = field(default_factory=dict)
    # The mentions each pending slot was asked to answer (heartbeat event
    # ids, 2026-09-26). A slot absent here was asked for without ids (an
    # operator's ``POST /api/mind/wake``): the old, global check applies.
    mention_ids: Dict[str, set] = field(default_factory=dict)


_state = _State()

# One lock per thread (2026-09-24). Every judgment turn used to share one
# lock, so a mention could queue behind a four-minute pulse that writes a
# different thread. "One thing at a time" now holds per thread: two wakes
# never interleave in ``edward-mind``; the pulse in ``edward-surfaces``
# runs beside them. Keyed by the running loop as well, so a lock is never
# used on a loop it was not made for.
_thread_locks: "weakref.WeakKeyDictionary[Any, Dict[str, asyncio.Lock]]" = weakref.WeakKeyDictionary()
_loopless_locks: Dict[str, asyncio.Lock] = {}


def thread_lock(conversation_id: str) -> asyncio.Lock:
    """The lock that keeps one thread's turns one at a time."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _loopless_locks.setdefault(conversation_id, asyncio.Lock())
    locks = _thread_locks.get(loop)
    if locks is None:
        locks = {}
        _thread_locks[loop] = locks
    lock = locks.get(conversation_id)
    if lock is None:
        lock = locks[conversation_id] = asyncio.Lock()
    return lock


def reset_state() -> None:
    """Tests: forget the in-process wake history and pending requests."""
    global _state
    for t in _state.pending.values():
        t.cancel()
    _state = _State()


# ---- pure ----------------------------------------------------------------------------

def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _local(dt: datetime) -> str:
    try:
        from services.timezone_utils import utc_to_local
        return utc_to_local(dt).strftime("%a %-I:%M%p").lower()
    except Exception:  # noqa: BLE001
        return dt.strftime("%a %H:%M")


def _age(dt: Optional[datetime], now: datetime) -> str:
    dt = _aware(dt)
    if dt is None:
        return "?"
    s = int((_aware(now) - dt).total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _where(att: Dict[str, Any]) -> str:
    if att.get("owner_present"):
        where = f"{owner_name()} is at {att.get('active_surface') or 'one of their screens'}"
        if att.get("in_imessage_thread"):
            where += f", texting in {att['in_imessage_thread']}"
        return where
    return f"{owner_name()} is away from all their screens"


def has_edward_signature(text: Any) -> bool:
    """True when a message ends with Edward's signature ("- Edward 🤖" and
    the variants ``ensure_message_signature`` accepts) — every send tool
    signs, so a from-me message that carries it is Edward's, not the
    owner's."""
    from utils.message_signature import _SIGNATURE_PATTERNS

    stripped = str(text or "").rstrip()
    return any(re.search(p, stripped, re.IGNORECASE) for p in _SIGNATURE_PATTERNS)


def _unsigned(m: Dict[str, Any]) -> str:
    """The message text without Edward's signature (the label says who)."""
    text = str(m.get("text") or "")
    if m.get("is_from_me") and has_edward_signature(text):
        from utils.message_signature import _SIGNATURE_PATTERNS

        for p in _SIGNATURE_PATTERNS:
            text = re.sub(p, "", text.rstrip(), flags=re.IGNORECASE)
    return text.rstrip()


def speaker(m: Dict[str, Any]) -> str:
    """Who wrote a chat.db message: Edward (from-me and the reader's
    structural ``by_edward`` — the pings' sent index, 2026-10-11 — or, when
    the reader does not say, signed), the owner (``owner_name()``, from-me
    otherwise), else the chat or sender. Until 2026-09-24 every from-me
    line was the owner's, so the mind read its own replies as theirs."""
    if m.get("is_from_me"):
        if "by_edward" in m:
            return "Edward" if m.get("by_edward") else owner_name()
        return "Edward" if has_edward_signature(m.get("text")) else owner_name()
    return m.get("chat_name") or m.get("sender") or "?"


def event_text(row: Any) -> str:
    """A heartbeat event's whole message: ``media.present.event_text``
    when it is there (attachment descriptions and all), else
    ``raw_data.full_text``, else the 200-character summary. At most
    ``EVENT_TEXT_MAX`` characters. Never raises."""
    try:
        from services.media.present import event_text as _media_text

        text = _media_text(row)
        if text:
            return str(text)[:EVENT_TEXT_MAX]
    except Exception:  # noqa: BLE001 — media is optional and may be down
        pass
    raw = getattr(row, "raw_data", None)
    if raw:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("full_text"):
            return str(payload["full_text"])[:EVENT_TEXT_MAX]
    return str(getattr(row, "summary", None) or "")[:EVENT_TEXT_MAX]


def _msg_time(m: Dict[str, Any]) -> Optional[datetime]:
    raw = m.get("timestamp") or m.get("date") or m.get("sent_at")
    if isinstance(raw, datetime):
        return _aware(raw)
    if isinstance(raw, str):
        try:
            return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def new_messages_since(messages: List[Dict[str, Any]], since: Optional[datetime]) -> int:
    if since is None:
        return len(messages)
    since = _aware(since)
    n = 0
    for m in messages:
        t = _msg_time(m)
        if t is None or t > since:
            n += 1
    return n


def wake_outcome(tools: List[Dict[str, Any]]) -> str:
    """``acted`` when a successful call changed something outside the turn;
    ``left_alone`` when the wake only looked. A label, not a gate."""
    for t in tools or []:
        if t.get("ok", True) and t.get("n") in ACTING_TOOLS:
            return "acted"
    return "left_alone"


def is_sunday(now: Optional[datetime] = None) -> bool:
    """Is it Sunday in the owner's zone (never the host's)?"""
    now = _aware(now) or _now()
    try:
        from services.timezone_utils import utc_to_local

        return utc_to_local(now).weekday() == 6
    except Exception:  # noqa: BLE001
        return now.weekday() == 6


def wake_timeout_for(reason: str, *, now: Optional[datetime] = None) -> float:
    """How long a wake's turn may run: a mention 420 s, the nightly 600 s
    (900 s on Sunday, when it reads the week), a speak turn 180 s, every
    other wake 240 s."""
    if reason == "mention":
        return MENTION_TIMEOUT_SECONDS
    if reason == "nightly":
        return NIGHTLY_SUNDAY_TIMEOUT_SECONDS if is_sunday(now) else NIGHTLY_TIMEOUT_SECONDS
    if reason == "speak":
        return SPEAK_TIMEOUT_SECONDS
    if reason == "free":
        return FREE_TIMEOUT_SECONDS
    return MIND_TIMEOUT_SECONDS


def history_chars_for(reason: str, explicit: Optional[int]) -> int:
    """The thread window a wake carries: what the caller asked for, else
    none for a speak turn (its pack says what was said today), 48K for the
    nightly, else the mind's 24K."""
    if explicit is not None:
        return explicit
    if reason == "speak":
        return SPEAK_MAX_HISTORY_CHARS
    if reason == "nightly":
        return NIGHTLY_MAX_HISTORY_CHARS
    if reason == "free":
        return FREE_MAX_HISTORY_CHARS
    return MIND_MAX_HISTORY_CHARS


def attempt_outcome(attempts: Optional[int], max_attempts: int = MAX_WAKE_ATTEMPTS) -> Tuple[int, bool]:
    """(the new attempt count, give up?) after one more failed wake."""
    n = int(attempts or 0) + 1
    return n, n >= max_attempts


def _one_line(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def memory_query_for(p: MindPack, limit: int = MEMORY_QUERY_CHARS) -> str:
    """What retrieval embeds for a wake: the reason and the salient parts —
    why he was woken, who is waiting, the newest messages, the day — cut to
    ``limit`` characters. The whole pack (12–20K) diluted the embedding into
    "a day in general" and pulled the same memories every wake."""
    parts: List[str] = []
    if p.extra:
        parts.append(_one_line(p.extra, 200))
    for e in (p.escalations or [])[:5]:
        parts.append(f"{e.get('sender') or '?'}: {_one_line(e.get('text'), 160)}")
    for m in reversed((p.messages or [])[-5:]):
        parts.append(f"{speaker(m)}: {_one_line(_unsigned(m), 120)}")
    if p.calendar:
        parts.append("today: " + "; ".join(p.calendar[:3]))
    digest = f"{p.reason}: " + " | ".join(x for x in parts if x)
    return digest[:limit].rstrip()


# Pack headings that name the owner, rendered once. ``memory_service``'s
# extraction addendum quotes the first and the verdicts heading (it renders
# them from the same ``{OWNER}`` text): keep the wording in step.
STANDING_INSTRUCTIONS_HEADING = owner_text(
    "Standing instructions from {OWNER} — they outrank everything below, a mention included:")
VERDICTS_HEADING = owner_text("{OWNER}'s verdicts since your last wake:")
PUSHED_HEADING = owner_text("Pushed to {OWNER}'s phone in the last 12h (every sender):")
CLOSING_ASK = owner_text("Decide what, if anything, this wake should do. If you act, close with two or three plain "
                         "sentences for {OWNER}: what you noticed, what you decided, and why. If you leave it, "
                         "one line.")


def render_pack(p: MindPack) -> str:
    """The pack as text. Raw, bounded, in time order. The only place the
    pack becomes words, so it stays pure."""
    lines = [f"[WAKE: {p.reason}] {_local(p.now)}. {_where(p.attention or {})}."
             + (f" {p.hub_presence}." if p.hub_presence else "")]
    if p.last_wake_at is None:
        lines.append("This is your first wake since a restart; your durable notes are below.")
    else:
        n = new_messages_since(p.messages, p.last_wake_at)
        lines.append(f"Since your last wake ({_age(p.last_wake_at, p.now)}): {n} new messages.")
    if p.unavailable:
        lines.append("Not readable this wake: " + ", ".join(p.unavailable)
                     + " — an empty section below may just mean it could not be read.")
    if p.follow_through:
        lines.append("")
        lines.extend(follow_through_lines(p.follow_through))
    if p.extra and not _repeats_the_promise(p.extra, p.follow_through):
        lines.append("")
        lines.append(p.extra.strip())

    lines.append("")
    if p.state_document and p.state_document.strip():
        body, clipped = clip_state_document(p.state_document)
        lines.append("Who you are and what you are doing (your state document):")
        lines.append(body)
        if clipped:
            lines.append(f"  (clipped at {STATE_DOC_MAX_CHARS:,} characters — shorten it next time you rewrite it; "
                         "a page, not a log)")
    else:
        lines.append(f"You have no state document yet. Write one this wake: `save_document(title=\"{STATE_DOC_TITLE}\", "
                     f"tags=\"{STATE_DOC_TAGS}\", content=...)` — a page on who you are, what you are doing for "
                     f"{owner_name()} and what you are watching. Every wake reads it first.")

    if p.instructions:
        lines.append("")
        lines.append(STANDING_INSTRUCTIONS_HEADING)
        lines.extend(p.instructions)

    lines.append("")
    lines.append("Recent messages (newest last):")
    msgs = (p.messages or [])[-MAX_MESSAGES_IN_PACK:]
    if not msgs:
        lines.append("  (none in the last few hours)")
    owner = owner_name()
    for m in msgs:
        who = speaker(m)
        chat = m.get("chat_identifier") or ""
        text = " ".join(_unsigned(m).split())[:200]
        when = _msg_time(m)
        stamp = f"{_local(when)} " if when else ""
        ping = f" (ping — {m['ping_status']})" if m.get("ping_status") else ""
        lines.append(f"  {stamp}{who}{' in ' + chat if chat and who != owner else ''}: {text}{ping}")

    if p.ping_lines:
        lines.append("")
        lines.append("Pings — answered by your reply path, not here (what was asked, and how it ended):")
        lines.extend(f"  {line}" for line in p.ping_lines)

    if p.escalations:
        lines.append("")
        lines.append(f"Waiting on you ({len(p.escalations)}) — messages the heartbeat thought you should see:")
        for e in p.escalations[:MAX_ESCALATIONS_IN_PACK]:
            flag = ""
            if e.get("sensitive"):
                flag = f" — sensitive ({e['sensitive']}): handle by a careful reply or a note, never a push"
            text = " ".join(str(e.get("text") or "").split())[:ESCALATION_TEXT_IN_PACK]
            lines.append(f"  [{e.get('id')}] {e.get('sender') or '?'}{' in ' + str(e.get('chat')) if e.get('chat') else ''} "
                         f"({e.get('at') or '?'}): {text}{flag}")
            tries = int(e.get("attempts") or 0)
            if tries:
                # A wake that timed out keeps its side effects (a send
                # cannot be taken back) but loses its turn: say so, so a
                # reply is not sent twice.
                lines.append(f"      (served to {tries} earlier wake{'s' if tries != 1 else ''} that did not finish — "
                             "you may already have acted on it; check the thread before you reply)")
            for t in (e.get("thread") or [])[-THREAD_LINES_IN_PACK:]:
                lines.append(f"      {t}")

    if p.approvals:
        lines.append("")
        lines.extend(p.approvals)

    lines.append("")
    calendar_unreadable = any(u.split(" ")[0] == "calendar" for u in (p.unavailable or []))
    if calendar_unreadable and not p.calendar:
        lines.append("Today: (the calendar could not be read this wake — do not assume the day is empty)")
    if p.calendar:
        lines.append("Today: " + " · ".join(p.calendar[:8]))
    lines.append(f"Pet board: {p.board or 'nothing notable'}")
    surf = p.surfaces or {}
    lines.append(f"Widget now: {surf.get('widget') or 'default'}")
    lines.append(f"Household screen now: {surf.get('house_screen') or 'its normal pages'}")
    lines.append(f"This Mac's screen now: {surf.get('mini_screen') or 'the stock scene'}")

    lines.append("")
    lines.append("Your notes for Today:")
    lines.append(p.notes.strip() if (p.notes or "").strip() else "  (none)")

    lines.append("")
    lines.append(PUSHED_HEADING)
    lines.append(p.pushes.strip() if (p.pushes or "").strip() else "  (nothing)")

    intentions = [m for m in (p.thoughts or []) if getattr(m, "memory_type", None) == "intention"]
    thoughts = [m for m in (p.thoughts or []) if getattr(m, "memory_type", None) != "intention"]
    lines.append("")
    lines.append(f"Open intentions ({len(intentions)}):")
    if not intentions:
        lines.append("  (none)")
    for m in intentions[:MAX_THOUGHTS_IN_PACK]:
        until = getattr(m, "valid_until", None)
        horizon = f", until {until.date().isoformat()}" if until else ""
        lines.append(f"  - [{horizon[2:] if horizon else 'no horizon'}] {getattr(m, 'content', '')} "
                     f"({_age(getattr(m, 'created_at', None), p.now)}) (memory_id: {getattr(m, 'id', '?')})")
    lines.append(f"Recent thoughts ({len(thoughts)}):")
    if not thoughts:
        lines.append("  (none)")
    for m in thoughts[:MAX_THOUGHTS_IN_PACK]:
        lines.append(f"  - {getattr(m, 'content', '')} ({_age(getattr(m, 'created_at', None), p.now)}) "
                     f"(memory_id: {getattr(m, 'id', '?')})")

    lines.extend(recent_wake_lines(p.recent_wakes, p.now))

    lines.append("")
    lines.append("What you did lately and what came of it:")
    if not p.actions:
        lines.append("  (nothing recorded)")
    for m in p.actions[:MAX_ACTIONS_IN_PACK]:
        lines.append(f"  - {getattr(m, 'content', '')} ({_age(getattr(m, 'created_at', None), p.now)}) "
                     f"(memory_id: {getattr(m, 'id', '?')})")

    lines.append("")
    lines.append(VERDICTS_HEADING)
    if not p.verdicts:
        lines.append("  (none)")
    for v in p.verdicts[:MAX_VERDICTS_IN_PACK]:
        note = f" — {v['note']}" if v.get("note") else ""
        lines.append(f"  - {v.get('kind')}: {v.get('verdict')}{note} ({v.get('at') or '?'})")
        if v.get("about"):
            lines.append(f"      on your wake: {v['about']}")

    if p.reason == "nightly":
        lines.extend(nightly_lines(p))
    if p.reason == "free":
        lines.extend(free_lines())
    lines.append("")
    lines.append(CLOSING_ASK)
    return "\n".join(lines)


def _repeats_the_promise(extra: str, ft: Optional[Dict[str, Any]]) -> bool:
    """Is ``extra`` only the promise the Follow-through block already
    states (the scheduler passes the event's ``why``)? Pure."""
    if not ft or not ft.get("why"):
        return False
    said = _one_line(extra, 600).lower()
    if said.startswith("why:"):
        said = said[4:].strip()
    return said == _one_line(ft["why"], 600).lower()


def follow_through_lines(ft: Dict[str, Any]) -> List[str]:
    """The block that leads a self wake keeping a promise made in a chat.
    Pure."""
    chat = ft.get("chat_identifier") or "?"
    name = ft.get("chat_name") or chat
    lines = [f"Follow-through — a promise you made in {name}" + (f" ({chat})" if name != chat else "") + ":"]
    if ft.get("why"):
        lines.append(f"  The promise: {_one_line(ft['why'], 400)}")
    if ft.get("asked"):
        lines.append(f"  Asked: {ft['asked']}")
    thread = list(ft.get("thread") or [])
    lines.append("  The chat's last lines (oldest first):")
    if not thread:
        lines.append("      (could not be read — check the thread before you post)")
    for t in thread:
        lines.append(f"      {t}")
    if ft.get("pings"):
        lines.append("  Pings in this chat:")
        lines.extend(f"      {line}" for line in ft["pings"])
    lines.append(f"  Reply in that chat when you have something, or when the promise is due — `send_message` to "
                 f"{chat}. If there's nothing new, say so in one line here, and either `schedule` the next look "
                 "(`wake_me`) or close the intention (`close_intention`).")
    return lines


_FREE_LINES: Tuple[str, ...] = tuple(owner_text(line) for line in (
    "",
    "This is your free time — one run a day, on a small budget, and the choice of what to do with it is "
    "yours: something genuinely worth doing for {OWNER} or for yourself, done properly, or nothing, said in "
    "one line. An open question, a dossier on someone who matters to them, tidying your intentions or your "
    "state document, learning something you keep needing. Leave what you make somewhere it lasts: a "
    "document or a memory; it reaches {OWNER} through your next wake.",
    "  Nobody asked for this turn: do not push or message anyone, and leave {OWNER} no note. The one surface "
    "you have here is this Mac's screen, if one is set up (`compose_screen`, `say_on_screen` — shown, never "
    "spoken): show what you are thinking or making, if you like. The messages above are context only; "
    "answering them is not this turn's work.",
))


def free_lines() -> List[str]:
    """The free-time asks (one budgeted run a day, 2026-10-11). Pure."""
    return list(_FREE_LINES)


def nightly_lines(p: MindPack) -> List[str]:
    """The nightly's window and asks: the day's on weeknights, the week's on
    Sunday (from reflect_lite and the Sunday reflect, 2026-10-11), then the
    noticing digest code wrote. Pure."""
    sunday = is_sunday(p.now)
    lines: List[str] = []
    if sunday:
        lines.extend(_week_lines(p.week or {}))
    else:
        lines.extend(_week_lines(p.week or {}, title="Your day (the last 24 hours):"))
    if p.digest:
        lines.append("")
        lines.append(f"Your noticing and how {owner_name()} likes to be reached (counted by your code — "
                     "read-only; you don't rewrite it):")
        lines.extend(f"  {line}" for line in p.digest)
    lines.append("")
    if sunday:
        lines.append("This is your Sunday reflection, the nightly that reads the week. In your own words:")
        lines.append("  1. Close the intentions above that no longer matter (`close_intention` with what came "
                     "of it), and say why. Keep the ones you still mean.")
        lines.append(f"  2. Write down what you learned about {owner_name()} this week (`remember`, about=\"owner\", "
                     "origin=\"thought\" — a belief, so it is capped at 0.6 confidence until they confirm it) "
                     "and about your own judgment (about=\"self\"): which calls their verdicts say were right, "
                     "which were wrong, what you will do differently.")
        lines.append(f"  3. Rewrite your state document (`edit_document` on \"{STATE_DOC_TITLE}\", or `save_document` "
                     "with that title if there is none) so it reads as a page, not a log: who you are, what you "
                     f"are doing for {owner_name()}, what you are watching, what you learned this week.")
    else:
        lines.append("This is your nightly reflection — short, a day's look. In your own words:")
        lines.append("  1. Close the intentions above that no longer matter (`close_intention` with what came "
                     "of it), and say why. Keep the ones you still mean.")
        lines.append(f"  2. Write down what you learned today: about {owner_name()} (`remember`, about=\"owner\", "
                     "origin=\"thought\" — a belief, capped at 0.6 confidence until they confirm it) and about "
                     "yourself (about=\"self\").")
        lines.append(f"  3. Edit your state document (\"{STATE_DOC_TITLE}\") only if something about you or "
                     "what you are doing changed today.")
    lines.append("  Nobody is awake: never a push, never a message to anyone, no surface. If something is worth "
                 f"{owner_name()}'s attention in the morning, leave them one note (`tell_owner` without urgent, or "
                 "`leave_note`) — at most one tonight. The messages above are context, not this turn's to answer.")
    return lines


def recent_wake_lines(rows: Sequence[Dict[str, Any]], now: datetime) -> List[str]:
    """"Your last few wakes": the mind's last ledger rows, newest first —
    reason, outcome, when, and the closing line cut to
    ``RECENT_WAKE_LINE_CHARS``. Pure."""
    lines = ["", "Your last few wakes (newest first):"]
    rows = list(rows or [])[:MAX_RECENT_WAKES_IN_PACK]
    if not rows:
        lines.append("  (none recorded)")
    for w in rows:
        at = _parse_iso(w.get("at")) if isinstance(w.get("at"), str) else _aware(w.get("at"))
        when = f"{_local(at)} ({_age(at, now)})" if at else "?"
        note = _one_line(w.get("note"), RECENT_WAKE_LINE_CHARS)
        lines.append(f"  - {when} {w.get('source') or '?'}: {w.get('outcome') or '?'}" + (f" — {note}" if note else ""))
    return lines


def clip_state_document(text: str, limit: int = STATE_DOC_MAX_CHARS) -> tuple[str, bool]:
    """The document, at most ``limit`` chars, cut on a word boundary. Pure."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit // 2:
        cut = cut[:last_space]
    return cut.rstrip(), True


async def load_state_document() -> Optional[str]:
    """The body of ``Edward: state``, or None when he has not written one."""
    from services.document_service import get_document_by_title

    doc = await get_document_by_title(STATE_DOC_TITLE)
    return doc.content if doc is not None else None


def _week_lines(week: Dict[str, Any], title: str = "Your week (the last seven days):") -> List[str]:
    """The week (or the nightly reflection's day), raw. Pure; only
    ``render_pack`` calls it."""
    lines = ["", title]
    wakes = week.get("wakes") or []
    lines.append(f"  Wakes ({len(wakes)}):")
    if not wakes:
        lines.append("    (none recorded)")
    for w in wakes[:MAX_WEEK_WAKES]:
        tools = ", ".join(t.get("n", "?") for t in (w.get("tools") or []) if isinstance(t, dict))
        lines.append(f"    - {str(w.get('at') or '?')[:16]} {w.get('source')}: {w.get('outcome')} — "
                     f"{w.get('note') or ''}" + (f" [{tools}]" if tools else ""))
    lines.append(f"  Pushed to {owner_name()}'s phone, every sender, and what they did with each:")
    pushes = (week.get("pushes") or "").strip()
    lines.append(pushes if pushes else "    (nothing)")
    verdicts = week.get("verdicts") or []
    lines.append(f"  {owner_name()}'s verdicts ({len(verdicts)}):")
    if not verdicts:
        lines.append("    (none)")
    for v in verdicts[:MAX_WEEK_VERDICTS]:
        note = f" — {v['note']}" if v.get("note") else ""
        lines.append(f"    - {v.get('kind')}: {v.get('verdict')}{note} ({str(v.get('at') or '?')[:16]})")
    did = week.get("did") or []
    lines.append(f"  What you did ({len(did)}):")
    if not did:
        lines.append("    (nothing recorded)")
    for line in did[:MAX_WEEK_DID]:
        lines.append(f"    - {line}")
    wishes = week.get("wishes") or []
    lines.append(f"  What you wished you could do ({len(wishes)}):")
    if not wishes:
        lines.append("    (nothing new)")
    for w in wishes[:10]:
        n = w.get("times_wanted") or 1
        lines.append(f"    - {w.get('title')} ({w.get('status') or 'open'}" + (f", wanted {n}x" if n > 1 else "") + ")")
    return lines


# ---- collection (I/O, each source isolated) -----------------------------------------------

async def _self_memories(now: datetime) -> tuple[List[Any], List[Any]]:
    """His own thoughts and intentions (last 48h, plus every live intention)
    and his last actions with outcomes."""
    from sqlalchemy import or_, select

    from services.database import MemoryModel, async_session
    from services.memory_service import Memory

    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    cutoff = naive_now - timedelta(hours=THOUGHT_WINDOW_HOURS)
    live = or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > naive_now)

    def to_memory(r) -> Memory:
        return Memory(id=r.id, content=r.content, memory_type=r.memory_type, importance=r.importance or 0.5,
                      created_at=r.created_at, temporal_nature=r.temporal_nature or "timeless",
                      tier=r.tier or "observation", origin=r.origin or "heard", about=r.about or "owner",
                      confidence=r.confidence if r.confidence is not None else 0.5,
                      valid_until=r.valid_until)

    async with async_session() as s:
        thoughts = (await s.execute(
            select(MemoryModel).where(
                MemoryModel.about == "self", MemoryModel.origin == "thought", live,
                or_(MemoryModel.created_at > cutoff, MemoryModel.memory_type == "intention"),
            ).order_by(MemoryModel.created_at.desc()).limit(MAX_THOUGHTS_IN_PACK)
        )).scalars().all()
        actions = (await s.execute(
            select(MemoryModel).where(MemoryModel.origin == "did", live)
            .order_by(MemoryModel.created_at.desc()).limit(MAX_ACTIONS_IN_PACK)
        )).scalars().all()
    return [to_memory(r) for r in thoughts], [to_memory(r) for r in actions]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


async def _verdicts_since(since: Optional[datetime], *, days: int = 2,
                          limit: int = MAX_VERDICTS_IN_PACK) -> List[Dict[str, Any]]:
    """The owner's implicit and explicit verdicts: heartbeat feedback and UI signals."""
    out: List[Dict[str, Any]] = []
    since = _aware(since)
    try:
        from services.heartbeat_feedback_service import list_feedback
        for f in await list_feedback(limit=max(50, limit)):
            at = _parse_iso(f.get("created_at"))
            if since and at and at <= since:
                continue
            out.append({"kind": "heartbeat", "verdict": f.get("verdict"), "note": f.get("note"),
                        "at": f.get("created_at"), "ref": f.get("event_id")})
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] feedback unavailable: {exc}")
    try:
        from services.user_signals_service import recent_signals
        for sgn in await recent_signals(days=days):
            at = _parse_iso(sgn.get("created_at"))
            if since and at and at <= since:
                continue
            if sgn.get("kind") not in ("feedback", "mind_verdict", "today_action_failed", "response_retry", "chat_stopped",
                                       "idea_tried", "idea_dismissed"):
                continue
            try:
                detail = json.loads(sgn.get("detail_json") or "{}")
            except ValueError:
                detail = {}
            out.append({"kind": sgn.get("kind"), "verdict": detail.get("verdict") or sgn.get("kind"),
                        "note": detail.get("note") or detail.get("text"), "at": sgn.get("created_at"),
                        "ref": sgn.get("ref_id")})
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] signals unavailable: {exc}")
    out.sort(key=lambda v: v.get("at") or "", reverse=True)
    return out[:limit]


async def _did_lines_since(since: datetime, *, limit: int = MAX_WEEK_DID) -> List[str]:
    """What he did since ``since`` (``origin="did"``), oldest first, one line each."""
    from sqlalchemy import select

    from services.database import MemoryModel, async_session

    naive = since.astimezone(timezone.utc).replace(tzinfo=None) if since.tzinfo else since
    async with async_session() as s:
        rows = (await s.execute(
            select(MemoryModel).where(MemoryModel.origin == "did", MemoryModel.created_at > naive)
            .order_by(MemoryModel.created_at.asc()).limit(limit)
        )).scalars().all()
    out = []
    for r in rows:
        stamp = r.created_at.strftime("%a %H:%M") if r.created_at else "?"
        out.append(f"{r.content} ({stamp}) (memory_id: {r.id})")
    return out


async def collect_week(now: datetime, days: int = WEEK_DAYS) -> Dict[str, Any]:
    """The Sunday reflection's window, or the nightly one's day
    (``days=1``, 2026-09-24). Each source fails on its own; a failing one
    leaves a blank, never a missing pack."""
    since = now - timedelta(days=days)
    week: Dict[str, Any] = {"wakes": [], "pushes": "", "verdicts": [], "did": [], "wishes": []}
    try:
        cutoff = since.isoformat()
        week["wakes"] = [w for w in await recent(limit=MAX_WEEK_WAKES * 2)
                         if (w.get("at") or "") >= cutoff][:MAX_WEEK_WAKES]
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] week: ledger unavailable: {exc}")
    try:
        from services.push_ledger import recent_text
        week["pushes"] = await recent_text(hours=24 * days, limit=MAX_WEEK_PUSHES)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] week: push ledger unavailable: {exc}")
    try:
        week["verdicts"] = await _verdicts_since(since, days=days, limit=MAX_WEEK_VERDICTS)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] week: verdicts unavailable: {exc}")
    try:
        week["did"] = await _did_lines_since(since, limit=MAX_WEEK_DID)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] week: did memories unavailable: {exc}")
    try:
        from services.wishlist_service import wishes_since
        week["wishes"] = [{"title": w.get("title"), "status": w.get("status"),
                           "times_wanted": w.get("times_wanted")} for w in await wishes_since(hours=24 * days)]
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] week: wishlist unavailable: {exc}")
    return week


async def _attach_wakes_to_verdicts(verdicts: List[Dict[str, Any]]) -> None:
    """A ``mind_verdict`` names the ledger row it judged; put that wake's
    own line beside it so the pack reads "you said X about Y"."""
    refs = {str(v.get("ref")) for v in verdicts if v.get("kind") == "mind_verdict" and v.get("ref")}
    if not refs:
        return
    by_id = {str(w["id"]): w for w in await recent(limit=200)}
    for v in verdicts:
        w = by_id.get(str(v.get("ref"))) if v.get("kind") == "mind_verdict" else None
        if w is not None:
            v["about"] = f"{w.get('source')}: {w.get('outcome')} — {w.get('note') or ''}".strip()


async def has_pending_escalations() -> bool:
    from sqlalchemy import func, select

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        n = (await s.execute(
            select(func.count(HeartbeatEventModel.id)).where(
                HeartbeatEventModel.triage_status == "escalated", HeartbeatEventModel.notified_at.is_(None))
        )).scalar()
    return bool(n)


async def _escalations() -> List[Dict[str, Any]]:
    """What the heartbeat thought the owner should see and nobody has acted on:
    raw, oldest first, with a sensitivity flag from the safety gate. No
    scores, no suppression — the mind judges."""
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session
    from services.push_safety_gate import classify_high_risk_push_label_text

    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel).where(
                HeartbeatEventModel.triage_status == "escalated", HeartbeatEventModel.notified_at.is_(None))
            .order_by(HeartbeatEventModel.created_at.asc()).limit(MAX_ESCALATIONS_IN_PACK)
        )).scalars().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        # The whole message (2026-10-11), not the summary.
        text = event_text(r).strip()
        item: Dict[str, Any] = {
            "id": r.id,
            "sender": (r.display_label or r.contact_name or r.sender or "?"),
            "chat": r.chat_name or r.chat_identifier,
            # Where a reply goes: a mention wake's ``reply_to``.
            "chat_identifier": r.chat_identifier,
            "text": text,
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        risk = classify_high_risk_push_label_text(text)
        if risk:
            item["sensitive"] = risk
        if getattr(r, "mind_attempts", None):
            item["attempts"] = int(r.mind_attempts)
        if r.chat_identifier:
            thread = await _thread_lines(r.chat_identifier)
            if thread:
                item["thread"] = thread
        out.append(item)
    return out


THREAD_LINES_IN_PACK = 8


async def _thread_lines(chat_identifier: str) -> List[str]:
    """The last few messages of a chat, oldest first, so an escalation is
    read in context. Empty when chat.db is not reachable."""
    try:
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats
        msgs = await asyncio.to_thread(get_recent_messages_across_chats, 48, chat_identifier, THREAD_LINES_IN_PACK)
    except Exception:  # noqa: BLE001
        return []
    lines = []
    for m in msgs[-THREAD_LINES_IN_PACK:]:
        who = speaker({**m, "chat_name": None})
        lines.append(f"{who}: {' '.join(_unsigned(m).split())[:160]}")
    return lines


async def _mark_considered(event_ids: List[str], batch_id: str) -> int:
    """The events a wake saw: stamped so the next wake sees new ones only.
    If the mind wanted to revisit, it said so with ``wake_me``."""
    if not event_ids:
        return 0
    from sqlalchemy import update

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        await s.execute(
            update(HeartbeatEventModel).where(HeartbeatEventModel.id.in_(event_ids))
            .values(notified_at=datetime.utcnow(), notification_batch_id=batch_id)
        )
        await s.commit()
    return len(event_ids)


async def _note_failed_attempt(event_ids: List[str]) -> List[str]:
    """A wake that was served these escalations failed or timed out: count
    the attempt on each, and give up on those that reached
    ``MAX_WAKE_ATTEMPTS`` (``notified_at`` stamped, batch ``mind:gave-up``)
    so a wake that keeps failing is not handed them again forever. Today
    still lists what the mind gave up on. Returns the ids given up on."""
    if not event_ids:
        return []
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session

    gave_up: List[str] = []
    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel).where(HeartbeatEventModel.id.in_(event_ids),
                                              HeartbeatEventModel.notified_at.is_(None))
        )).scalars().all()
        for r in rows:
            n, give_up = attempt_outcome(r.mind_attempts)
            r.mind_attempts = n
            if give_up:
                r.notified_at = datetime.utcnow()
                r.notification_batch_id = GAVE_UP_BATCH_ID
                gave_up.append(r.id)
        await s.commit()
    return gave_up


async def _still_waiting(event_ids: List[str]) -> set:
    """Which of these escalations nobody has considered yet."""
    if not event_ids:
        return set()
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel.id).where(HeartbeatEventModel.id.in_(event_ids),
                                                 HeartbeatEventModel.notified_at.is_(None))
        )).scalars().all()
    return set(rows)


async def last_wake_at() -> Optional[datetime]:
    """When the mind last woke, from the ledger (survives restarts)."""
    if _state.last_wake_at is not None:
        return _state.last_wake_at
    try:
        from sqlalchemy import desc, select

        from services.database import LedgerEventModel, async_session

        async with async_session() as s:
            row = (await s.execute(
                select(LedgerEventModel).where(LedgerEventModel.kind == "mind",
                                               LedgerEventModel.source.notin_(ATTENTION_BLIND_REASONS))
                .order_by(desc(LedgerEventModel.created_at)).limit(1)
            )).scalar_one_or_none()
        return _aware(row.created_at) if row is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _bounded(label: str, awaitable: Awaitable[Any], default: Any, unavailable: List[str],
                   *, timeout: Optional[float] = None) -> Any:
    """One pack source, on its own short bound. A source that fails or runs
    over leaves its default and a note in ``unavailable``; it never takes
    the pack (or the wake) down with it."""
    bound = PACK_SOURCE_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        return await asyncio.wait_for(awaitable, timeout=bound)
    except asyncio.TimeoutError:
        print(f"[mind] {label} timed out after {bound:g}s")
        unavailable.append(f"{label} (timed out)")
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] {label} unavailable: {exc}")
        unavailable.append(label)
    return default


async def _attention_source() -> Dict[str, Any]:
    from services.presence_service import get_attention_state
    a = await get_attention_state()
    return {"owner_present": a.owner_present, "active_surface": a.active_surface,
            "in_imessage_thread": a.in_imessage_thread}


async def _messages_source() -> List[Dict[str, Any]]:
    from services.heartbeat.listener_imessage import get_recent_messages_across_chats
    return await asyncio.to_thread(get_recent_messages_across_chats, MESSAGE_WINDOW_HOURS, None,
                                   MAX_MESSAGES_IN_PACK)


async def _calendar_source(unavailable: List[str]) -> List[str]:
    """Today's calendar lines. When Apple Calendar did not answer (the
    display falls back to Edward's own jobs), the calendar is marked
    unreadable rather than rendered as an empty day. When the display served
    the last list it did give (``stale``), the list is kept and the pack
    says how old it is: "calendar (the list below is as of 2:05pm — Apple
    Calendar is not answering)" — an empty one is still not an empty day."""
    from services import display_service
    from services.expression_service import calendar_lines, stale_calendar_note
    cal = await display_service.get_calendar()
    if cal.get("source") != "apple-mcp":
        unavailable.append("calendar")
    else:
        note = stale_calendar_note(cal)
        if note:
            unavailable.append(f"calendar (the list below is {note})")
    return calendar_lines(cal.get("events"), payload_source=cal.get("source"))


async def _board_source() -> str:
    from services import hub_service
    from services.expression_service import _board_summary
    return _board_summary(await hub_service.get_board())


async def _notes_source() -> str:
    from services.notes_service import recent_notes_text
    return await recent_notes_text(hours=24, limit=12)


async def _pushes_source() -> str:
    from services.push_ledger import recent_text
    return await recent_text(hours=12, limit=20)


async def _verdicts_source(since: Optional[datetime]) -> List[Dict[str, Any]]:
    verdicts = await _verdicts_since(since)
    try:
        await _attach_wakes_to_verdicts(verdicts)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] could not join verdicts to wakes: {exc}")
    return verdicts


async def _surfaces_source(now: datetime) -> Dict[str, str]:
    from services.expression_service import collect_signals, describe_mini_screen, summarise_widget_sections
    sig = await collect_signals(now=now)
    cw = sig.current_widget or {}
    says = summarise_widget_sections(cw.get("sections"))
    cs = sig.current_screen
    return {
        "widget": f"{cw.get('subtitle') or cw.get('title') or 'default'}" + (f" — {says}" if says else ""),
        "house_screen": (cs["kind"] + (" — " + (cs.get("title") or "")) if cs else "its normal pages"),
        "mini_screen": describe_mini_screen(sig.mini_screen),
    }


async def _nothing(value: Any) -> Any:
    return value


def _week_source(reason: str, now: datetime, unavailable: List[str]) -> Awaitable[Any]:
    """The nightly reads the day — on Sunday the week; no other wake
    carries a window."""
    if reason != "nightly":
        return _nothing({})
    if is_sunday(now):
        return _bounded("week", collect_week(now), {}, unavailable)
    return _bounded("day", collect_week(now, days=1), {}, unavailable)


def _pings_hooks() -> Any:
    """``services.pings.hooks``, or None when it is not there."""
    try:
        from services.pings import hooks
    except ImportError:
        return None
    return hooks


async def _ping_lines_source(now: datetime) -> List[str]:
    """Recent pings and how each ended (``pings.hooks.pack_lines``); [] when
    the pings module is not there. Its own reader never raises."""
    hooks = _pings_hooks()
    if hooks is None or not hasattr(hooks, "pack_lines"):
        return []
    naive = _aware(now).astimezone(timezone.utc).replace(tzinfo=None)  # the pings tables' convention
    return [str(line) for line in (await hooks.pack_lines(naive, hours=PING_PACK_HOURS) or [])]


async def _digest_source(now: datetime) -> List[str]:
    """The noticing digest code writes (``noticing_service.digest``):
    what he noticed and said, and how the owner likes to be reached. [] when the
    module is not there."""
    try:
        from services import noticing_service
    except ImportError:
        return []
    digest = getattr(noticing_service, "digest", None)
    if digest is None:
        return []
    out = digest(now)
    if inspect.isawaitable(out):
        out = await out
    return [str(line) for line in (out or [])]


async def collect_pack(reason: str, *, now: Optional[datetime] = None, extra: Optional[str] = None) -> MindPack:
    """Everything a wake can see. Every source fails on its own and runs on
    its own bound (``PACK_SOURCE_TIMEOUT_SECONDS``), all of them at once; a
    missing source leaves a blank and a line under "Not readable this
    wake", never a missing pack. The wake bounds the whole collection too
    (``PACK_TIMEOUT_SECONDS``). Only a mention is served the escalations,
    and only the nightly the day (or week) and the noticing digest."""
    now = now or _now()
    unavailable: List[str] = []
    since = await _bounded("last wake", last_wake_at(), None, unavailable)

    (attention, messages, calendar, board, notes, pushes, verdicts, memories, escalations, surfaces,
     state_document, week, approvals, hub_presence, instructions, recent_wakes, ping_lines,
     digest) = await asyncio.gather(
        _bounded("presence", _attention_source(), {}, unavailable),
        _bounded("messages", _messages_source(), [], unavailable),
        _bounded("calendar", _calendar_source(unavailable), [], unavailable),
        _bounded("pet board", _board_source(), "", unavailable),
        _bounded("notes", _notes_source(), "", unavailable),
        _bounded("push ledger", _pushes_source(), "", unavailable),
        _bounded("verdicts", _verdicts_source(since), [], unavailable),
        _bounded("self memories", _self_memories(now), ([], []), unavailable),
        (_bounded("escalations", _escalations(), [], unavailable) if serves_escalations(reason) else _nothing([])),
        _bounded("surfaces", _surfaces_source(now), {}, unavailable),
        _bounded("state document", load_state_document(), None, unavailable),
        _week_source(reason, now, unavailable),
        _bounded("approvals", _approvals_source(since), [], unavailable),
        _bounded("hub", _hub_source(now), None, unavailable),
        _bounded("standing instructions", _instructions_source(now), [], unavailable),
        _bounded("recent wakes", recent(limit=MAX_RECENT_WAKES_IN_PACK), [], unavailable),
        _bounded("pings", _ping_lines_source(now), [], unavailable),
        (_bounded("noticing digest", _digest_source(now), [], unavailable) if reason == "nightly"
         else _nothing([])),
    )
    thoughts, actions = memories

    return MindPack(now=now, reason=reason, last_wake_at=since, attention=attention, messages=messages,
                    calendar=calendar, board=board, notes=notes, pushes=pushes, verdicts=verdicts,
                    thoughts=thoughts, actions=actions, surfaces=surfaces, escalations=escalations,
                    state_document=state_document, extra=extra, week=week, unavailable=sorted(unavailable),
                    approvals=approvals or [], hub_presence=hub_presence,
                    instructions=instructions or [], recent_wakes=recent_wakes or [], ping_lines=ping_lines or [],
                    digest=digest or [])


MAX_INSTRUCTIONS_IN_PACK = 20

# Where a heard/owner instruction cannot have come from the owner (memory
# audit, 2026-09-27). Wakes minted them from their own packs (profile mind,
# in edward-mind) and about a quarter of the rows that qualified came from
# scheduled-event turns — a blanket "never contact anyone" rule among them.
# Rows stored before those turns were fixed may still be in the store; this
# keeps the ones whose thread or provenance says so from speaking as the
# owner. A row whose thread is missing — none recorded, or the owner deleted
# it — is theirs only when its own provenance vouches: a pin
# (``source_ref {"kind": "owner_confirmed"}``) or a turn in which the owner
# spoke. Before that rule such a row stayed theirs, which is how another
# blanket rule (no provenance, knowledge tier, thousands of retrievals, its
# conversation deleted) outranked every mention.
EDWARD_OWN_THREAD_IDS = frozenset({MIND_CONVERSATION_ID, "edward-surfaces", SPEAK_CONVERSATION_ID,
                                   NIGHTLY_CONVERSATION_ID, FREE_CONVERSATION_ID, *RETIRED_THREAD_IDS})
NOT_OWNERS_CONVERSATION_SOURCES = frozenset({"edward_thought", "scheduled_event", "heartbeat",
                                           "orchestrator_worker"})
NOT_OWNERS_PROFILES = frozenset({"mind", "pulse", "scheduled_event"})
# Threads of Edward's in which every human message is the owner's: a
# thought-journal entry opens with Edward's narrative as an assistant
# message, so a chat turn's extraction there reads only the owner (2026-09-27:
# the owner's reply inside an entry never reached this section, and the
# demotion script would have made it a thought). Not a reminder thread (its triggers
# are human messages in the extraction window — where the blanket "never
# contact anyone" rules came from), a heartbeat thread (other people's
# texts), a worker (its task) or Edward's own named threads (the packs).
OWNER_SPEAKS_THREAD_SOURCES = frozenset({"edward_thought"})
# The writes a turn stamps on its own rows. Until 2026-09-27 the PWA's chat
# stream ran each step of a turn in a fresh context
# (``routers/chat._yield_with_heartbeats``), so its post-turn extraction, and a
# ``remember`` after the first step, recorded the thread and no turn; rows
# written before that fix still do. With no turn on record the write took the
# chat profile as the owner speaking (``memory_service.is_owners_own_turn``), and so
# does the pack.
TURN_WRITE_KINDS = frozenset({"extraction", "remember"})


def _owner_spoke_on_record(ref: Dict[str, Any]) -> bool:
    """Whether a row's own ref says the owner was speaking when it was
    written: a turn of theirs, or a turn's own write with no turn recorded
    that the write itself took as theirs. Pure."""
    from services.memory_service import OWNER_SPEAKS_SOURCES, is_owners_own_turn

    if ref.get("turn_source") in OWNER_SPEAKS_SOURCES:
        return True
    return ref.get("kind") in TURN_WRITE_KINDS and is_owners_own_turn(ref.get("turn_source"),
                                                                    ref.get("profile") or "chat")


def is_owners_instruction(row: Dict[str, Any], conversation_sources: Dict[str, Optional[str]], *,
                        when_untold: bool = False) -> bool:
    """Whether a live heard/owner instruction may be rendered as the
    owner's: not when its provenance names a profile or a turn that is not
    the owner speaking, or its thread is one of Edward's own — unless it is
    a journal entry and the provenance says the owner said it there. When its thread is
    missing (none recorded, or not in ``conversation_sources``, which holds
    every conversation that exists among those the rows name), only its own
    provenance can vouch: a ``owner_confirmed`` pin or a turn in which the owner
    spoke (``_owner_spoke_on_record``). Otherwise nothing on record says
    whose it is, and the answer is ``when_untold`` — no for the pack; the
    demotion script passes True, so it rewrites only what the record shows
    is not the owner's. Pure."""
    from services.memory_decay_service import OWNER_CONFIRMED_KIND
    from services.memory_service import OWNER_SPEAKS_SOURCES
    from services.provenance_service import parse_source_ref

    ref = parse_source_ref(row.get("source_ref"))
    thread = row.get("source_conversation_id")
    # The ref tells where the row came from only when it names the row's own
    # thread: a row stored before provenance (2026-09-24) took the ref of
    # whatever reinforced it first — an instruction from the owner's chat
    # could carry a wake's ``mind`` ref. Then the thread decides.
    own_ref = not thread or not ref.get("conversation_id") or ref.get("conversation_id") == thread
    if own_ref:
        if ref.get("profile") in NOT_OWNERS_PROFILES:
            return False
        turn_source = ref.get("turn_source")
        if turn_source and turn_source not in OWNER_SPEAKS_SOURCES:
            return False
        if (turn_source and thread and ref.get("conversation_id") == thread
                and thread not in EDWARD_OWN_THREAD_IDS
                and conversation_sources.get(thread) in OWNER_SPEAKS_THREAD_SOURCES):
            return True
    thread = thread or ref.get("conversation_id")
    if thread in EDWARD_OWN_THREAD_IDS:
        return False
    if thread not in conversation_sources:
        if own_ref and (ref.get("kind") == OWNER_CONFIRMED_KIND or _owner_spoke_on_record(ref)):
            return True
        return when_untold
    return conversation_sources.get(thread) not in NOT_OWNERS_CONVERSATION_SOURCES


def conversations_named(rows: Iterable[Any]) -> Set[str]:
    """Every conversation instruction rows name — a row's thread, else its
    ref's — for the ``conversation_sources`` lookup ``is_owners_instruction``
    reads (one missing from it reads as gone). The pack and the demotion
    script build it here so they judge a row alike. Pure."""
    from services.provenance_service import parse_source_ref

    return {getattr(r, "source_conversation_id", None)
            or parse_source_ref(getattr(r, "source_ref", None)).get("conversation_id")
            for r in rows} - {None, ""}


def instruction_lines(memories: List[Dict[str, Any]], standdowns: List[Dict[str, Any]], now: datetime,
                      names: Optional[Dict[str, str]] = None) -> List[str]:
    """The owner's standing instructions (``instruction`` memories) and every live
    stand-down, one line each; past ``MAX_INSTRUCTIONS_IN_PACK`` it says how
    many older ones it left out (2026-09-27: 15 were dropped silently). Pure."""
    names = names or {}
    lines: List[str] = []
    for m in memories[:MAX_INSTRUCTIONS_IN_PACK]:
        text = " ".join(str(m.get("content") or "").split())
        if not text:
            continue
        at = m.get("created_at")
        when = f" (said {_aware(at):%b %-d})" if isinstance(at, datetime) else ""
        lines.append(f"  - {text[:300]}{when}")
    left_out = len(memories) - MAX_INSTRUCTIONS_IN_PACK
    if left_out > 0:
        lines.append(f"  - ({left_out} more older instruction{'s' if left_out != 1 else ''} not shown — "
                     "`remember_search` with memory_type=\"instruction\" finds them.)")
    for d in standdowns:
        chat = str(d.get("chat_identifier") or "")
        label = names.get(chat) or chat
        until = d.get("expires_at")
        until_txt = f" until {_local(_aware(until))}" if isinstance(until, datetime) else ""
        who = f", asked by {d['requested_by']}" if d.get("requested_by") else ""
        lines.append(f"  - Stand down in {label}{' (' + chat + ')' if label != chat else ''}{until_txt}{who}: "
                     "don't post there, even when mentioned.")
    return lines


async def _instructions_source(now: datetime) -> List[str]:
    from sqlalchemy import or_, select

    from services.database import (ChatStanddownModel, ConversationModel, HeartbeatEventModel, MemoryModel,
                                   async_session)

    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        candidates = (await s.execute(
            select(MemoryModel).where(
                MemoryModel.memory_type == "instruction",
                # Only what the owner said (2026-09-25): 41 of the 95 live
                # rows were inferred from other people's messages (``read``)
                # and one was Edward's own decision — none of them the
                # owner's to outrank a mention.
                MemoryModel.origin == "heard", MemoryModel.about == "owner",
                or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > naive_now),
            ).order_by(MemoryModel.created_at.desc())
        )).scalars().all()
        threads = conversations_named(candidates)
        sources: Dict[str, Optional[str]] = {}
        if threads:
            sources = dict((await s.execute(
                select(ConversationModel.id, ConversationModel.source).where(ConversationModel.id.in_(threads))
            )).all())
        mems = [m for m in candidates
                if is_owners_instruction({"source_ref": m.source_ref,
                                        "source_conversation_id": m.source_conversation_id}, sources)]
        downs = (await s.execute(
            select(ChatStanddownModel).where(ChatStanddownModel.released_at.is_(None))
        )).scalars().all()
        live = [d for d in downs if d.expires_at is None or _aware(d.expires_at) > now]
        names: Dict[str, str] = {}
        chats = [d.chat_identifier for d in live]
        if chats:
            rows = (await s.execute(
                select(HeartbeatEventModel.chat_identifier, HeartbeatEventModel.chat_name)
                .where(HeartbeatEventModel.chat_identifier.in_(chats), HeartbeatEventModel.chat_name.isnot(None))
            )).all()
            names = {c: n for c, n in rows if n}
    return instruction_lines(
        [{"content": m.content, "created_at": m.created_at} for m in mems],
        [{"chat_identifier": d.chat_identifier, "expires_at": d.expires_at, "requested_by": d.requested_by}
         for d in live],
        now, names,
    )


async def _approvals_source(since):
    from services import approvals_service
    return await approvals_service.load_pack_lines(since=since)


async def _hub_source(now):
    from services import hub_service
    return await hub_service.hub_presence_line(now)


# ---- the wake ------------------------------------------------------------------------

async def _settings():
    from services.settings_service import get_settings
    return await get_settings()


async def _graph():
    from services.graph import get_graph
    return await get_graph()


# Edward's own threads, created on first use as ``edward_thought`` so they
# show under Edward's in the sidebar.
_OWN_THREADS: Dict[str, str] = {
    MIND_CONVERSATION_ID: MIND_CONVERSATION_TITLE,
    SPEAK_CONVERSATION_ID: SPEAK_CONVERSATION_TITLE,
    NIGHTLY_CONVERSATION_ID: NIGHTLY_CONVERSATION_TITLE,
    FREE_CONVERSATION_ID: FREE_CONVERSATION_TITLE,
}


async def _ensure_conversation(conversation_id: str = MIND_CONVERSATION_ID) -> None:
    from services.conversation_service import create_conversation, get_conversation
    title = _OWN_THREADS.get(conversation_id)
    if title is None:
        return
    if await get_conversation(conversation_id) is None:
        await create_conversation(conversation_id, title=title, source="edward_thought")


async def _record_now(*, source: str, outcome: str, note: str, tools: List[Dict[str, Any]],
                      turn_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> Optional[int]:
    from services import event_ledger

    return await event_ledger.record_event_now(kind="mind", source=source, outcome=outcome, reason=note,
                                               ref_id=turn_id, detail=wake_ledger_detail(tools, detail))


def wake_ledger_detail(tools: List[Dict[str, Any]], detail: Optional[Dict[str, Any]] = None,
                       limit: Optional[int] = None) -> Dict[str, Any]:
    """A wake row's detail that fits the ledger's cap as JSON that parses
    (2026-09-27 review). ``bounded_detail`` cuts the JSON text at 600
    characters, and ``tools`` is one name per call: a reflection of ~28
    calls — inside its default 30 rounds — lost ``decay.stamp``, the value
    ``run_memory_decay.py --revert-stamp`` needs to undo a wrong weekly
    forget, and ``detail_with_thought`` then read the cut row as ``{}``.
    The caller's keys come first; the tools list is shortened from the end
    (``tools_omitted`` says by how many) before the row would be cut."""
    import json

    from services import event_ledger

    cap = limit or event_ledger.MAX_DETAIL_CHARS
    names = [t.get("n") for t in tools or []]
    out: Dict[str, Any] = dict(detail or {})
    out.setdefault("tools", names)
    out.setdefault("surfaces", event_ledger.surfaces_touched(tools or []))
    out.setdefault("delivered", event_ledger.delivered(tools or []))
    if out["tools"] is not names:
        return out  # a caller that wrote its own tools keeps them as given

    def size(o: Dict[str, Any]) -> int:
        return len(json.dumps(o, separators=(",", ":"), default=str))

    keep = len(names)
    while keep and size(out) > cap:
        keep -= 1
        out["tools"] = names[:keep]
        out["tools_omitted"] = len(names) - keep
    return out


def _last_turn() -> Any:
    try:
        from services import turn_ledger
        return turn_ledger.last_finished_turn()
    except Exception:  # noqa: BLE001
        return None


def _new_turn(prior: Any) -> Any:
    """The turn this wake finished, or None when no new one finished."""
    turn = _last_turn()
    return turn if turn is not None and turn is not prior else None


async def _journal(*, reason: str, turn: Any, turn_source: str, closing: str, p: Optional[MindPack],
                   message: Optional[str], conversation_id: str, ledger_id: Optional[int]) -> Optional[str]:
    """The thought journal (2026-09-25): a wake that chose to act — sent,
    pushed, left a note, scheduled, committed — becomes one short
    ``edward_thought`` conversation built from the turn's record and his
    closing sentences, linked from its ledger row. Bounded by
    ``thought_journal.JOURNAL_TIMEOUT_SECONDS``; never raises and never
    fails the wake."""
    # The id begin_entry handed out before the turn: a push or note the turn
    # sent may already link to it, so the entry is written under it.
    entry_id = thought_journal.current_entry_id()
    thought_id: Optional[str] = None
    try:
        actions = list(getattr(turn, "actions", None) or [])
        if turn is not None and thought_journal.should_journal(actions, turn_source):

            async def _write() -> Optional[str]:
                woke_for = (thought_journal.woke_for_line(p) if p is not None
                            else thought_journal.woke_for_message(message))
                written = await thought_journal.record_entry(
                    reason=reason, woke_for=woke_for, reasoning=list(getattr(turn, "reasoning", None) or []),
                    closing_note=closing, actions=actions, when=datetime.now(timezone.utc), ledger_id=ledger_id,
                    wake_thread=conversation_id, turn_id=getattr(turn, "id", None), pack_excerpt=message,
                    names=thought_journal.chat_names(p), conversation_id=entry_id)
                if written and ledger_id is not None:
                    await thought_journal.attach_to_ledger(ledger_id, written)
                return written

            thought_id = await asyncio.wait_for(_write(), timeout=thought_journal.JOURNAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print(f"[mind] journal entry for the {reason} wake timed out after "
              f"{thought_journal.JOURNAL_TIMEOUT_SECONDS:g}s")
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] journal entry for the {reason} wake failed: {exc}")
    # Whatever the journal did, the link a push or note carries must open
    # something: close the pending entry, writing a minimal one if needed.
    return await thought_journal.finish_entry(thought_id, reason=reason, closing_note=closing,
                                              ledger_id=ledger_id, wake_thread=conversation_id)


def _error_class(exc: BaseException) -> str:
    try:
        from services.turn_ledger import error_class
        return error_class(exc)
    except Exception:  # noqa: BLE001
        return type(exc).__name__


async def _gather_pack(reason: str, extra: Optional[str]) -> MindPack:
    """``collect_pack`` under its deadline — outside any lock."""
    return await asyncio.wait_for(collect_pack(reason, extra=extra), timeout=PACK_TIMEOUT_SECONDS)


async def _drop_handled_escalations(p: MindPack) -> bool:
    """The pack was collected before the lock; a wake that ran meanwhile
    may have considered some of its escalations. Drop those so the same
    message is not handed to two wakes in a row. True when it changed."""
    if not p.escalations:
        return False
    try:
        waiting = await asyncio.wait_for(_still_waiting([e["id"] for e in p.escalations]),
                                         timeout=PACK_SOURCE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] could not re-check escalations after waiting for the lock: {exc}")
        return False
    kept = [e for e in p.escalations if e["id"] in waiting]
    if len(kept) == len(p.escalations):
        return False
    p.escalations = kept
    return True


async def _count_failed_attempt(event_ids: List[str], why: str, detail: Dict[str, Any]) -> str:
    """One more attempt on each escalation a failed or cancelled turn was
    served, into ``detail``; returns ``why``, saying so if it gave up."""
    # The turn may have acted before it died (a send cannot be taken back),
    # so the next pack says these were served before; after three tries the
    # mind stops being handed them.
    try:
        gave_up = await _note_failed_attempt(event_ids)
    except Exception as stamp_exc:  # noqa: BLE001
        gave_up = []
        print(f"[mind] could not count the failed attempt: {stamp_exc}")
    # Counts, and a few ids: the ledger's detail is capped at 600
    # characters and a cut list would no longer parse.
    detail["attempted"] = len(event_ids)
    if gave_up:
        detail["gave_up"] = gave_up[:5]
        detail["gave_up_count"] = len(gave_up)
        why = (f"{why[:180]}; gave up on {len(gave_up)} escalation"
               f"{'s' if len(gave_up) != 1 else ''} after {MAX_WAKE_ATTEMPTS} failed wakes")
        print(f"[mind] gave up on {gave_up} after {MAX_WAKE_ATTEMPTS} failed wakes")
    return why


# A wake cancelled from outside settles its books under this bound before
# the cancellation goes on: whoever cancelled it is waiting for it to end.
CANCEL_SETTLE_SECONDS = 10


def cancelled_note(stage: str) -> str:
    """The ledger reason for a wake cancelled from outside, by how far it
    got: ``pack``, ``waiting`` (for the thread), ``before`` (its turn),
    ``turn``, ``after`` (the turn, before its row). Pure."""
    if stage == "waiting":
        return "Cancelled: while waiting for the thread (another turn held it); no turn ran"
    if stage == "turn":
        return "Cancelled: mid-turn; what it had done by then stands"
    if stage == "after":
        return "Cancelled: after its turn, before the wake was recorded"
    if stage == "pack":
        return "Cancelled: while collecting the pack; no turn ran"
    return "Cancelled: before its turn started; no turn ran"


async def _settle_cancelled(reason: str, stage: str, *, record: bool, served: Sequence[str] = (),
                            journal: Optional[Dict[str, Any]] = None) -> None:
    """The books of a wake cancelled from outside (2026-09-25). The
    scheduler bounds a fired wake with ``wait_for`` and the wake may spend
    that bound waiting for the thread behind a long reflection;
    ``CancelledError`` is not an ``Exception``, so such a wake left no row
    and counted no attempt. Here: an attempt on each escalation its turn was
    ``served`` (none when no turn ran — it was shown nothing), one failed
    row, and the journal entry when a turn ran. Bounded; never raises —
    the caller re-raises the cancellation."""
    why = cancelled_note(stage)
    print(f"[mind] wake cancelled ({reason}): {why}")
    detail: Dict[str, Any] = {"error_class": "Cancelled", "stage": stage}

    async def _books() -> Optional[int]:
        note = await _count_failed_attempt(list(served), why, detail) if served else why
        if not record:
            return None
        return await _record_now(source=reason, outcome="failed", note=note, tools=[], detail=detail)

    try:
        ledger_id = await asyncio.wait_for(_books(), timeout=CANCEL_SETTLE_SECONDS)
        if journal is not None:
            await _journal(**journal, ledger_id=ledger_id,
                           closing=f"This wake did not finish ({why}); the above is what it had done by then.")
    except asyncio.CancelledError:
        print(f"[mind] the {reason} wake was cancelled again while settling its books")
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] could not settle the cancelled {reason} wake: {exc}")


# ---- retry (2026-09-25) ---------------------------------------------------------------
#
# A one-hour server-side Codex outage (401 "Incorrect API key", 503s before
# it) lost the evening wake outright: a failed wake was never asked for
# again. Now a wake that failed for a transient reason is re-requested after
# 10, 20 and 40 minutes (``retry_delay``), keeping its profile and its
# purpose label. A failure that would fail the same way again (a bug, a bad
# request, a rate limit the gate owns) and the wake's own deadline (the turn
# may already have acted) never retry. The escalation attempt count
# (``MAX_WAKE_ATTEMPTS``) is unchanged: a retried mention still gives up on
# an escalation after its third failed wake, and a retry with nothing left
# waiting is skipped.

RETRY_REASONS = ("self", "mention", "nightly")
MAX_WAKE_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 600
RETRY_MARK = "(Retry "
_MODEL_NEVER_ANSWERED = "I stopped before finishing: the model did not answer within"
_HTTP_5XX_RE = re.compile(r"\bHTTP 5\d\d\b")
_AUTH_STILL_REJECTED = ("HTTP 401", "token expired and refresh failed")
_AUTH_REFRESH_COULD_NOT_RUN = "refresh failed"


def is_transient_error(exc: BaseException) -> bool:
    """Would the same wake likely succeed in a few minutes? Pure.

    Yes: a Codex stream error it marks retryable (server errors, a
    truncated stream), an ``LLMProviderError`` it marks retryable (Claude:
    overloaded, 5xx, 429, a dropped connection), a 5xx, a 401 still
    rejected after the refresh (the
    2026-09-25 outage answered "Incorrect API key" for an hour), a
    connection error, an LLM call past its deadline. No: everything else —
    a rate limit or the weekly cap (the gate owns those), a bad request, a
    bug, and the wake's own deadline (the turn may already have acted)."""
    try:
        from services.graph.tool_deadlines import LLMCallTimeout
        if isinstance(exc, LLMCallTimeout):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        # The provider-neutral error (ChatClaude, 2026-10-10) says itself:
        # overloaded, 5xx, 429 and connection faults yes; a 400 or a 401 no.
        from services.llm_errors import LLMProviderError
        if isinstance(exc, LLMProviderError):
            return bool(exc.retryable)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, asyncio.TimeoutError):
        return False
    try:
        from services.codex_chat_model import CodexAuthError, CodexStreamError
        if isinstance(exc, CodexStreamError):
            return bool(exc.retryable)
        if isinstance(exc, CodexAuthError) and not exc.transient and _AUTH_REFRESH_COULD_NOT_RUN not in str(exc):
            # A fresh token that was rejected too ("Run `codex login`") fails
            # the same way every time; each retry would cost a token POST and
            # a Codex request (review 2026-09-26). The service-side "Incorrect
            # API key" fault is marked transient; a refresh that could not run
            # at all (a network fault mid-outage) still falls through.
            return False
    except Exception:  # noqa: BLE001
        pass
    try:
        import httpx
        if isinstance(exc, httpx.TransportError):
            return True
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, ConnectionError):
        return True
    text = str(exc)
    if _HTTP_5XX_RE.search(text):
        return True
    return any(marker in text for marker in _AUTH_STILL_REJECTED)


def retry_delay(attempt: int) -> float:
    """Seconds before retry ``attempt + 1``: 600, 1200, 2400."""
    return float(RETRY_BASE_DELAY_SECONDS * 2 ** max(0, int(attempt)))


def should_retry_wake(reason: str, out: Dict[str, Any], attempt: int) -> bool:
    """Retry this wake? A reason on the list, fewer than ``MAX_WAKE_RETRIES``
    retries so far, and either a transient failure whose turn neither
    reached anyone nor committed to anything, or a turn whose model never
    answered and that did nothing. Pure."""
    if reason not in RETRY_REASONS or int(attempt or 0) >= MAX_WAKE_RETRIES:
        return False
    if out.get("skipped"):
        return False
    if not out.get("ok"):
        # It acted, then it died: never twice. A send cannot be taken back,
        # and a retry told "Reply to the mention … Do one of these now" could
        # text the same group again, reworded (review 2026-09-26). A wake
        # that only wrote memory still retries.
        return bool(out.get("transient")) and not out.get("reached") and not out.get("committed")
    return (out.get("outcome") == "left_alone"
            and str(out.get("note") or "").startswith(_MODEL_NEVER_ANSWERED))


def retry_extra(extra: Optional[str], attempt: int, why: str) -> str:
    """The original ``extra`` (any earlier retry line dropped) and one line
    saying this is retry ``attempt`` and why the last one failed. Pure."""
    base = "\n".join(line for line in str(extra or "").splitlines() if not line.startswith(RETRY_MARK)).strip()
    note = (f"{RETRY_MARK}{attempt} of {MAX_WAKE_RETRIES}: the last attempt at this wake failed — "
            f"{_one_line(why, 200)}. Whatever it did before failing stands; check before repeating it.)")
    return f"{base}\n{note}" if base else note


# Wakes that serve no escalation and answer no verdict: they do not move
# "since your last wake", so the owner's verdicts, decided approvals and the
# new-message count still reach the next wake that can act on them (review
# 2026-09-26). ``speak`` sees one noticing, not the pack. The retired
# surplus reasons stay listed: their ledger rows are still read.
ATTENTION_BLIND_REASONS = ("speak", "free", "reflect_lite", "prep", "research")
# A mention whose own escalation is gone hands what else waits to a plain
# messages wake after this long (the heartbeat's coalescing delay).
FOLLOW_UP_MESSAGES_DELAY_S = 60.0


def moves_last_wake(reason: str) -> bool:
    """Does a wake for this reason count as "your last wake"? Pure."""
    return reason not in ATTENTION_BLIND_REASONS


def mention_still_served(reason: str, mention_ids: Optional[Sequence[str]], waiting: Optional[set]) -> bool:
    """Is a mention this wake was asked to answer still waiting? Pure.
    True when the wake is not a mention, carries no ids (an operator's ask)
    or the check could not be made (``waiting`` None); False only when none
    of the mentions it was asked for is still waiting."""
    if reason != "mention" or mention_ids is None or waiting is None:
        return True
    return any(str(i) in {str(w) for w in waiting} for i in mention_ids)


async def _mentions_waiting(reason: str, mention_ids: Optional[Sequence[str]]) -> Optional[set]:
    """Which of this mention wake's mentions nobody has considered yet, from
    the table (not the pack, which lists only the oldest twenty); None when
    there is nothing to check or the check failed (then the wake keeps its
    forced reply)."""
    if reason != "mention" or not mention_ids:
        return None
    try:
        return set(await asyncio.wait_for(_still_waiting([str(i) for i in mention_ids]),
                                          timeout=PACK_SOURCE_TIMEOUT_SECONDS))
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] could not check the mention wake's mentions: {exc}")
        return None


def _wake_purpose(reason: str, turn_source: str):
    """``mind:<reason>`` (or ``pulse``) for the spend ledger — unless an
    outer scope already names the job."""
    from contextlib import nullcontext

    from services.call_purpose import current_purpose, purpose_scope

    if current_purpose():
        return nullcontext()
    return purpose_scope("pulse" if turn_source == "pulse" else f"mind:{reason}")


# Background work a wake starts and does not wait for (the nightly's
# contradiction triage, the inbound shim): kept referenced until done.
_BACKGROUND: Set["asyncio.Task"] = set()


def _spawn(coro: Any) -> bool:
    """Run ``coro`` on the running loop without waiting for it. False (and
    the coroutine closed) when there is no loop."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return False
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return True


# ---- follow-through (2026-10-11) ------------------------------------------------------
#
# A two-way relay is the act people liked best: asked in a group, Edward
# said he would look, and came back to the group. A promise
# made while answering a ping is kept with an intention and a scheduled
# wake; ``scheduled_events_service.create_event`` stamps the ping's chat on
# the event (``event_metadata.ping``), and the ``self`` wake it fires may
# reply there. A self wake with no such chat reaches nobody but the owner.

# While a ping turn runs in the chat, the wake looks again this much later
# (at most this many times, then it runs anyway).
FOLLOW_THROUGH_REARM_MINUTES = 10
MAX_FOLLOW_THROUGH_REARMS = 6
FOLLOW_THROUGH_THREAD_LINES = 20
FOLLOW_THROUGH_THREAD_HOURS = 24 * 14
FOLLOW_THROUGH_PING_HOURS = 24 * 7
# The turn sources of a ping turn: a third party's, and the owner's own ping.
PING_TURN_SOURCES = ("ping", "ping_owner")


@dataclass(frozen=True)
class FollowThrough:
    """The chat a self wake keeps a promise in."""
    chat_identifier: str
    chat_guid: Optional[str] = None
    is_group: bool = False
    chat_name: Optional[str] = None
    conversation_id: Optional[str] = None
    ping_id: Optional[int] = None
    memory_id: Optional[str] = None
    why: Optional[str] = None
    requested_by_owner: bool = False

    @property
    def reply_to(self) -> Tuple[str, ...]:
        """Every spelling of the chat a reply may be sent to."""
        out = [self.chat_identifier]
        if self.chat_guid and self.chat_guid != self.chat_identifier:
            out.append(self.chat_guid)
        return tuple(out)


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _memory_source_ref(memory_id: str) -> Dict[str, Any]:
    from sqlalchemy import select

    from services.database import MemoryModel, async_session
    from services.provenance_service import parse_source_ref

    try:
        async with async_session() as s:
            raw = (await s.execute(
                select(MemoryModel.source_ref).where(MemoryModel.id == memory_id))).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] follow-through: could not read memory {memory_id}: {exc}")
        return {}
    return parse_source_ref(raw)


async def follow_through_for(meta: Any) -> Optional[FollowThrough]:
    """The chat a self wake keeps its promise in, or None: from the event's
    ``ping`` stamp, else from its intention's ``source_ref`` when that was
    written in a ping turn (``turn_source`` ping/ping_owner, with the chat).
    ``requested_by_owner`` (the toolset's stamp) makes the follow-up the
    owner's ask.
    Pure but for that one memory read."""
    if not isinstance(meta, dict):
        return None
    why = str(meta.get("why") or "").strip() or None
    by_owner = bool(meta.get("requested_by_owner"))
    ping = meta.get("ping")
    if isinstance(ping, dict) and ping.get("chat_identifier"):
        return FollowThrough(
            chat_identifier=str(ping["chat_identifier"]), chat_guid=ping.get("chat_guid") or None,
            is_group=bool(ping.get("is_group")), chat_name=ping.get("chat_name") or None,
            conversation_id=ping.get("conversation_id") or None, ping_id=_int_or_none(ping.get("ping_id")),
            memory_id=str(meta["memory_id"]) if meta.get("memory_id") else None, why=why, requested_by_owner=by_owner)
    memory_id = meta.get("memory_id")
    if not memory_id:
        return None
    ref = await _memory_source_ref(str(memory_id))
    if ref.get("turn_source") not in PING_TURN_SOURCES or not ref.get("chat_identifier"):
        return None
    return FollowThrough(
        chat_identifier=str(ref["chat_identifier"]), chat_guid=ref.get("chat_guid") or None,
        is_group=bool(ref.get("is_group")), conversation_id=ref.get("conversation_id") or None,
        ping_id=_int_or_none(ref.get("ping_id")), memory_id=str(memory_id), why=why, requested_by_owner=by_owner)


def follow_through_busy(ft: FollowThrough) -> bool:
    """Is a ping turn running in that chat right now (the pings'
    ``chats_in_flight``)? False without the pings module."""
    hooks = _pings_hooks()
    if hooks is None or not hasattr(hooks, "chats_in_flight"):
        return False
    try:
        busy = set(hooks.chats_in_flight())
    except Exception:  # noqa: BLE001
        return False
    key = getattr(hooks, "chat_key", None) or (lambda c: str(c or "").strip().lower())
    return any(key(c) in busy for c in ft.reply_to if c)


async def _rearm_follow_through(ft: FollowThrough, meta: Dict[str, Any], extra: Optional[str]) -> Optional[str]:
    """The same wake again in ``FOLLOW_THROUGH_REARM_MINUTES``: a new
    ``mind_wake`` event with the same metadata and one more ``rearmed``."""
    from services import scheduled_events_service

    new_meta = {**meta, "rearmed": int(meta.get("rearmed") or 0) + 1}
    new_meta.setdefault("type", "mind_wake")
    at = datetime.utcnow() + timedelta(minutes=FOLLOW_THROUGH_REARM_MINUTES)
    description = str(meta.get("why") or extra or f"Follow up in {ft.chat_name or ft.chat_identifier}")[:500]
    event = await scheduled_events_service.create_event(
        description, at, conversation_id=MIND_CONVERSATION_ID, created_by="edward", event_metadata=new_meta,
        memory_id=str(meta["memory_id"]) if meta.get("memory_id") else None)
    return getattr(event, "id", None)


async def _ping_asked_line(ping_id: Optional[int]) -> Optional[str]:
    """"7:28pm by Parker: “…”" from the pings row, or None."""
    if ping_id is None:
        return None
    from sqlalchemy import text as sql

    from services.database import async_session

    async with async_session() as s:
        if (await s.execute(sql("SELECT to_regclass('public.pings')"))).scalar() is None:
            return None
        row = (await s.execute(sql(
            "SELECT sender_label, sender, asked_at, text, from_owner FROM pings WHERE id = :id"),
            {"id": int(ping_id)})).first()
    if row is None:
        return None
    who = owner_name() if row.from_owner else (row.sender_label or row.sender or "someone")
    when = _local(_aware(row.asked_at)) if row.asked_at else "?"
    return f"{when} by {who}: “{_one_line(row.text, 200)}”"


async def follow_through_context(ft: FollowThrough, now: datetime) -> Dict[str, Any]:
    """The Follow-through block's facts: the chat, the promise, who asked
    and when, the chat's last lines (Edward's own labelled from the
    reader's ``by_edward``) and the chat's pings. Each part fails on its own."""
    ctx: Dict[str, Any] = {"chat_identifier": ft.chat_identifier, "chat_name": ft.chat_name, "why": ft.why,
                           "thread": [], "pings": []}
    try:
        from services.heartbeat.listener_imessage import get_recent_messages_across_chats

        msgs = await asyncio.to_thread(get_recent_messages_across_chats, FOLLOW_THROUGH_THREAD_HOURS,
                                       ft.chat_identifier, FOLLOW_THROUGH_THREAD_LINES)
        for m in list(msgs or [])[-FOLLOW_THROUGH_THREAD_LINES:]:
            if not ctx["chat_name"] and m.get("chat_name"):
                ctx["chat_name"] = m["chat_name"]
            ctx["thread"].append(f"{speaker({**m, 'chat_name': None})}: {_one_line(_unsigned(m), 300)}")
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] follow-through: the thread of {ft.chat_identifier} is unreadable: {exc}")
    hooks = _pings_hooks()
    if hooks is not None and hasattr(hooks, "pack_lines"):
        try:
            naive = _aware(now).astimezone(timezone.utc).replace(tzinfo=None)
            names = [n.lower() for n in (ctx["chat_name"], ft.chat_identifier) if n]
            ctx["pings"] = [str(line) for line in (await hooks.pack_lines(naive, hours=FOLLOW_THROUGH_PING_HOURS)
                                                   or []) if any(n in str(line).lower() for n in names)]
        except Exception as exc:  # noqa: BLE001
            print(f"[mind] follow-through: ping lines unavailable: {exc}")
    try:
        ctx["asked"] = await _ping_asked_line(ft.ping_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] follow-through: the ping row is unreadable: {exc}")
    return ctx


# ---- the nightly (2026-10-11) ---------------------------------------------------------
#
# 03:00 in the owner's zone. A code pre-check first: nothing happened since the
# last nightly → one ledger row and no model call (Sunday's decay still
# runs). Otherwise a silent turn in ``edward-nightly``, then Sunday's decay
# and contradiction triage by note. The pulse for the morning scene is the
# scheduler's nightly tick's, once, after this wake returns (one pulse, not
# two).

NIGHTLY_LOOKBACK_HOURS = 24
# The owner's own words: the turns in which they speak (``memory_service.OWNER_SPEAKS_SOURCES``).
_FALLBACK_OWNER_TURN_SOURCES = ("chat", "voice", "widget", "alexa")


async def _last_nightly_at() -> Optional[datetime]:
    """When the last nightly ran or found nothing (naive UTC), or None."""
    from sqlalchemy import desc, select

    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        row = (await s.execute(
            select(LedgerEventModel.created_at).where(
                LedgerEventModel.kind == "mind", LedgerEventModel.source == "nightly",
                LedgerEventModel.outcome.in_(("acted", "left_alone")))
            .order_by(desc(LedgerEventModel.created_at)).limit(1))).scalar_one_or_none()
    return row


async def nightly_has_material(now: Optional[datetime] = None) -> bool:
    """Did anything happen since the last nightly (else the last 24 h)? A
    noticing, a ping, a verdict, an intention whose horizon lapsed, a turn
    in which the owner spoke, or a new thing the owner said (a heard/owner memory).
    Code only. A check that cannot run says yes — a skipped nightly must be
    one that had nothing, not one that could not look."""
    from sqlalchemy import text as sql

    from services.database import async_session

    now = _aware(now) or _now()
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        from services.memory_service import OWNER_SPEAKS_SOURCES

        owner_sources = tuple(OWNER_SPEAKS_SOURCES)
    except Exception:  # noqa: BLE001
        owner_sources = _FALLBACK_OWNER_TURN_SOURCES
    try:
        since = await _last_nightly_at() or (naive_now - timedelta(hours=NIGHTLY_LOOKBACK_HOURS))
        params = {"since": since, "now": naive_now}
        checks = [
            ("SELECT 1 FROM user_signals WHERE kind IN ('mind_verdict', 'feedback') AND created_at > :since LIMIT 1",
             None),
            ("SELECT 1 FROM heartbeat_feedback WHERE created_at > :since LIMIT 1", None),
            ("SELECT 1 FROM memories WHERE memory_type = 'intention' AND valid_until > :since "
             "AND valid_until <= :now LIMIT 1", None),
            ("SELECT 1 FROM memories WHERE origin = 'heard' AND about = 'owner' AND created_at > :since LIMIT 1",
             None),
            ("SELECT 1 FROM turn_log WHERE source = ANY(:owner) AND started_at > :since LIMIT 1", None),
            ("SELECT 1 FROM noticings WHERE created_at > :since LIMIT 1", "noticings"),
            ("SELECT 1 FROM pings WHERE asked_at > :since LIMIT 1", "pings"),
        ]
        async with async_session() as s:
            for query, table in checks:
                if table is not None and (await s.execute(
                        sql("SELECT to_regclass(:t)"), {"t": f"public.{table}"})).scalar() is None:
                    continue
                if (await s.execute(sql(query), {**params, "owner": list(owner_sources)})).first() is not None:
                    return True
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] nightly: the material check failed ({exc}) — running the nightly anyway")
        return True


async def _sunday_decay() -> Dict[str, Any]:
    """The forgetting pass, for real, as after the Sunday reflection:
    thoughts that did not become intentions or get reinforced
    age out; a held intention is skipped. The stamp is the one
    ``valid_until`` every row it retired carries — a wrong forget is undone
    by it (``run_memory_decay.py --revert-stamp``)."""
    try:
        decay = await decay_stale_memories(dry_run=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] decay pass failed: {exc}")
        return {}
    out = {"candidates": decay.get("candidates", 0), "retired": decay.get("retired", 0), "stamp": decay.get("stamp")}
    print(f"[mind] decay pass: {out}")
    return {"decay": out}


async def _nightly_triage() -> None:
    """Contradiction triage after the nightly: by note, one question to the owner
    at most (its old caller was the surplus queue). Bounded; never raises."""
    try:
        from services import contradiction_triage_service

        await asyncio.wait_for(
            contradiction_triage_service.triage_unresolved(
                limit=NIGHTLY_TRIAGE_LIMIT, priority="background", purpose="mind:nightly", ask_via="note",
                max_asks=1),
            timeout=NIGHTLY_TRIAGE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] nightly contradiction triage failed: {type(exc).__name__}: {exc}")


async def _after_nightly(*, sunday: bool) -> Dict[str, Any]:
    """After a nightly turn: Sunday's decay (its stamp goes on the row) and
    contradiction triage in the background. No pulse here: the scheduler's
    nightly tick asks for the morning scene once the wake returns
    (``scheduler_service.NIGHTLY_PULSE_EXTRA``)."""
    detail = await _sunday_decay() if sunday else {}
    _spawn(_nightly_triage())
    return detail


# ---- the messages shim: inbound is noticed, never woken for (2026-10-11) --------------

# How far back the fallback looks for inbound nobody stored (as ``_escalations``).
STORED_WINDOW_HOURS = 24


def _raw(ev: Any) -> Dict[str, Any]:
    raw = getattr(ev, "raw_data", None)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def storable_inbound(ev: Any) -> bool:
    """May the shim store this event away (stamp it ``stored:``)? Never a
    ping (``triage_status`` ping or ``raw_data.ping``), never the owner
    asking Edward (``owner_direct``) and never an ``@edward`` — someone is
    waiting on those, and with the ping runner off a mention wake answers them. Only
    an iMessage can be one (2026-10-11): an ``@edward`` email gets no mention
    wake, so it is stored like the rest (no source reads as iMessage). Pure."""
    if getattr(ev, "triage_status", None) == "ping":
        return False
    if (getattr(ev, "source", None) or "imessage") != "imessage":
        return True
    raw = _raw(ev)
    if raw.get("ping") or raw.get("owner_direct"):
        return False
    texts = [getattr(ev, "summary", None)] + [raw.get(k) for k in ("full_text", "normalized_body", "text", "body")]
    return not any(MENTION_RE.search(str(t)) for t in texts if t)


def _pending_mention_ids() -> Set[str]:
    out: Set[str] = set()
    for ids in _state.mention_ids.values():
        out |= {str(i) for i in ids or ()}
    return out


async def store_unseen_inbound() -> int:
    """The shim's fallback while ``noticing_service`` is not
    there: stamp escalated inbound nobody has seen (last 24 h) as
    ``stored:<pass>`` so Today does not flood with "waiting on you", and
    write one ``notice`` ledger row (``source="stub"``), as the stub does.
    Never a ping or a mention (``storable_inbound``)."""
    from sqlalchemy import select, update

    from services import event_ledger
    from services.database import HeartbeatEventModel, async_session

    cutoff = datetime.utcnow() - timedelta(hours=STORED_WINDOW_HOURS)
    waiting = _pending_mention_ids()
    async with async_session() as s:
        rows = (await s.execute(
            select(HeartbeatEventModel).where(
                HeartbeatEventModel.triage_status == "escalated", HeartbeatEventModel.notified_at.is_(None),
                HeartbeatEventModel.created_at >= cutoff))).scalars().all()
        ids = [r.id for r in rows if storable_inbound(r) and str(r.id) not in waiting]
        if not ids:
            return 0
        pass_id = uuid.uuid4().hex[:12]
        await s.execute(update(HeartbeatEventModel).where(HeartbeatEventModel.id.in_(ids))
                        .values(notified_at=datetime.utcnow(), notification_batch_id=f"stored:{pass_id}"))
        await s.commit()
    await event_ledger.record_event_now(kind="notice", source="stub", outcome="ok",
                                        reason=f"stored {len(ids)} inbound (no noticer)", ref_id=pass_id,
                                        detail={"events": len(ids)})
    print(f"[notice] stub stored events={len(ids)} pass={pass_id}")
    return len(ids)


def _notice_inbound() -> bool:
    """``request_wake("messages")``: hand inbound to the noticer
    (``noticing_service.request_notice(None)``), or store it here when the
    noticer is not there or fails. True when it was handed on."""
    try:
        from services import noticing_service
    except ImportError:
        noticing_service = None  # type: ignore[assignment]
    request = getattr(noticing_service, "request_notice", None) if noticing_service is not None else None
    if request is not None:
        try:
            out = request(None)
            if inspect.isawaitable(out):
                return _spawn(out)
            return out is None or bool(out)
        except Exception as exc:  # noqa: BLE001
            print(f"[mind] request_notice failed ({type(exc).__name__}: {exc}) — storing inbound here")
    return _spawn(store_unseen_inbound())


def _maybe_retry(reason: str, out: Dict[str, Any], *, attempt: int, extra: Optional[str],
                 message: Optional[str], required_tools: Optional[List[str]], profile: Dict[str, Any],
                 mention_ids: Optional[Sequence[str]] = None) -> None:
    if not should_retry_wake(reason, out, attempt):
        return
    from services.call_purpose import current_purpose

    nxt = int(attempt or 0) + 1
    why = str(out.get("note") or out.get("error_class") or "unknown")
    carried = dict(profile)
    if message is not None:
        carried["message"] = retry_extra(message, nxt, why)
    kw: Dict[str, Any] = {}
    if mention_ids is not None:
        kw["mention_ids"] = list(mention_ids)
    scheduled = request_wake(reason, delay_s=retry_delay(attempt), extra=retry_extra(extra, nxt, why),
                             required_tools=required_tools, attempt=nxt, profile=carried,
                             purpose=current_purpose(), **kw)
    if scheduled is False:
        print(f"[mind] the {reason} wake failed ({_one_line(why, 120)}); a retry of it is already pending")
    else:
        print(f"[mind] the {reason} wake failed ({_one_line(why, 120)}); retry {nxt} of {MAX_WAKE_RETRIES} "
              f"in {retry_delay(attempt):.0f}s")


def thread_for(reason: str, conversation_id: str) -> str:
    """The thread a wake runs in: the caller's, except that the nightly and
    the speaker have their own when the caller left the default. Pure."""
    if conversation_id == MIND_CONVERSATION_ID:
        if reason == "nightly":
            return NIGHTLY_CONVERSATION_ID
        if reason == "speak":
            return SPEAK_CONVERSATION_ID
        if reason == "free":
            return FREE_CONVERSATION_ID
    return conversation_id


async def wake(reason: str, *, extra: Optional[str] = None, required_tools: Optional[List[str]] = None,
               pack: Optional[MindPack] = None, message: Optional[str] = None,
               conversation_id: str = MIND_CONVERSATION_ID, tools: Optional[Sequence[str]] = None,
               system_prompt: Optional[str] = None, turn_source: str = "mind", skip_extraction: bool = False,
               max_history_chars: Optional[int] = None, record: bool = True,
               memory_query: Optional[str] = None, attempt: int = 0,
               mention_ids: Optional[Sequence[str]] = None, policy: Any = None,
               event_meta: Optional[Dict[str, Any]] = None, timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """One wake (``_wake_once``) under its purpose label, shown as running
    in ``wake_state`` while it lasts (``GET /api/mind/now``), and a retry
    asked for when it failed transiently (``should_retry_wake``).
    ``attempt`` is how many retries came before this one; ``mention_ids``
    are the heartbeat events a mention wake was asked for (2026-09-26).

    2026-10-11: ``policy`` (the toolset's ``TurnPolicy``; else one is derived
    from the reason), ``event_meta`` (the scheduled event's metadata — a
    ``self`` wake keeps a promise made in a ping in that chat) and
    ``timeout_s`` (else ``wake_timeout_for``). ``tools`` is the legacy
    ``allowed_tools`` list (``EDWARD_TOOLSET=legacy`` only); without it the
    policy picks one. ``wake("messages")`` is the inbound shim."""
    from services import wake_state

    if reason == "messages":
        handed = _notice_inbound()
        return {"ok": True, "skipped": True, "outcome": "left_alone",
                "note": "inbound is noticed, not woken for" + ("" if handed else " (could not hand it on)")}
    conversation_id = thread_for(reason, conversation_id)
    follow_through: Optional[FollowThrough] = None
    if reason == "self" and event_meta:
        try:
            follow_through = await follow_through_for(event_meta)
        except Exception as exc:  # noqa: BLE001
            print(f"[mind] could not resolve the follow-through: {exc}")
        if (follow_through is not None and follow_through_busy(follow_through)
                and int(event_meta.get("rearmed") or 0) < MAX_FOLLOW_THROUGH_REARMS):
            # A ping turn is talking in that chat right now: look again in
            # ten minutes rather than talk over it.
            try:
                event_id = await _rearm_follow_through(follow_through, event_meta, extra)
            except Exception as exc:  # noqa: BLE001
                print(f"[mind] could not re-arm the follow-through in {follow_through.chat_identifier}: {exc}")
                event_id = None
            if event_id is not None:
                note = (f"A ping turn is running in {follow_through.chat_name or follow_through.chat_identifier}; "
                        f"looking again in {FOLLOW_THROUGH_REARM_MINUTES} minutes")
                print(f"[mind] self wake re-armed: {note}")
                if record:
                    await _record_now(source=reason, outcome="skipped", note=note, tools=[],
                                      detail={"rearmed": event_id})
                return {"ok": True, "skipped": True, "rearmed": event_id, "note": note}
    token = wake_state.begin(reason, conversation_id)
    try:
        with _wake_purpose(reason, turn_source):
            out = await _wake_once(reason, extra=extra, required_tools=required_tools, pack=pack, message=message,
                                   conversation_id=conversation_id, tools=tools, system_prompt=system_prompt,
                                   turn_source=turn_source, skip_extraction=skip_extraction,
                                   max_history_chars=max_history_chars, record=record, memory_query=memory_query,
                                   mention_ids=mention_ids, policy=policy, follow_through=follow_through,
                                   timeout_s=timeout_s)
    finally:
        wake_state.end(token)
    try:
        _maybe_retry(reason, out, attempt=attempt, extra=extra, message=message, required_tools=required_tools,
                     profile=dict(conversation_id=conversation_id, tools=tuple(tools) if tools is not None else None,
                                  system_prompt=system_prompt, turn_source=turn_source,
                                  skip_extraction=skip_extraction, max_history_chars=max_history_chars,
                                  record=record, policy=policy, event_meta=event_meta, timeout_s=timeout_s),
                     mention_ids=mention_ids)
    except Exception as exc:  # noqa: BLE001 — a retry that cannot be asked for never fails the wake
        print(f"[mind] could not ask for a retry of the {reason} wake: {exc}")
    return out


def _mention_reply_to(p: Optional[MindPack], mention_ids: Optional[Sequence[str]]) -> Tuple[str, ...]:
    """The chats a mention wake was asked in: its mentions' chats, else
    every escalation's. Pure."""
    if p is None:
        return ()
    wanted = {str(i) for i in mention_ids} if mention_ids else None
    out: List[str] = []
    for e in p.escalations or []:
        if wanted is not None and str(e.get("id")) not in wanted:
            continue
        chat = e.get("chat_identifier")
        if chat and chat not in out:
            out.append(str(chat))
    return tuple(out)


def _policy_reach(policy: Any) -> Dict[str, Any]:
    """The reach a caller's policy grants, for the legacy tool list."""
    return {"may_reach_people": bool(getattr(policy, "may_reach_people", False)),
            "may_push": bool(getattr(policy, "may_push", False))}


async def _skip_nightly(record: bool) -> Dict[str, Any]:
    """Nothing happened since the last nightly: one row, no model call;
    Sunday's decay still runs."""
    detail = await _sunday_decay() if is_sunday(_now()) else {}
    print(f"[mind] nightly skipped: {NIGHTLY_NOTHING_NOTE}")
    if record:
        kw: Dict[str, Any] = {"detail": detail} if detail else {}
        await _record_now(source="nightly", outcome="left_alone", note=NIGHTLY_NOTHING_NOTE, tools=[], **kw)
    return {"ok": True, "skipped": True, "outcome": "left_alone", "note": NIGHTLY_NOTHING_NOTE}


async def _wake_once(reason: str, *, extra: Optional[str] = None, required_tools: Optional[List[str]] = None,
                     pack: Optional[MindPack] = None, message: Optional[str] = None,
                     conversation_id: str = MIND_CONVERSATION_ID, tools: Optional[Sequence[str]] = None,
                     system_prompt: Optional[str] = None, turn_source: str = "mind", skip_extraction: bool = False,
                     max_history_chars: Optional[int] = None, record: bool = True,
                     memory_query: Optional[str] = None,
                     mention_ids: Optional[Sequence[str]] = None, policy: Any = None,
                     follow_through: Optional[FollowThrough] = None,
                     timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """One wake. Serialised per thread, bounded, recorded. Never raises —
    except a cancellation, which it records and passes on (2026-09-25).

    The defaults are the mind: its pack, its thread, its ledger row. A
    caller may bring its own ``message`` (then no pack is collected, and the
    nightly's pre-check and after-steps do not run), thread, tool list,
    policy and system prompt — the surfaces pulse and the speaker do, with
    ``record=False`` for the pulse, which keeps its own ledger rows.

    Since 2026-09-24: the pack is collected first, outside the lock and on
    its own deadline; turns in one thread run one at a time; a failed wake
    counts an attempt on the escalations it was served; retrieval embeds a
    digest of the pack. Since 2026-10-11 the turn runs under a policy and
    the nightly skips an empty day in code.
    """
    max_history_chars = history_chars_for(reason, max_history_chars)
    p: Optional[MindPack] = None
    own_pack = message is None and pack is None
    nightly = reason == "nightly" and own_pack and record
    sunday = nightly and is_sunday(_now())
    try:
        if nightly and not await nightly_has_material(_now()):
            return await _skip_nightly(record)
        if quota_state() == "exhausted":
            if record:
                from services import llm_spend

                note = llm_spend.QUOTA_SKIP_NOTE if llm_spend.is_anthropic() else "Codex quota exhausted"
                detail = await _sunday_decay() if sunday else {}
                await _record_now(source=reason, outcome="skipped", note=note, tools=[],
                                  **({"detail": detail} if detail else {}))
            return {"ok": True, "skipped": True, "note": "quota exhausted"}
        if message is None:
            try:
                p = pack or await _gather_pack(reason, extra)
            except asyncio.TimeoutError:
                why = (f"PackTimeout: the pack was not collected within {PACK_TIMEOUT_SECONDS:g}s; "
                       "no turn ran")
                print(f"[mind] wake failed ({reason}): {why}")
                if record:
                    await _record_now(source=reason, outcome="failed", note=why, tools=[],
                                      detail={"error_class": "PackTimeout"})
                return {"ok": False, "skipped": False, "note": why, "error_class": "PackTimeout"}
            except asyncio.CancelledError:
                await _settle_cancelled(reason, "pack", record=record)
                raise
            if not serves_escalations(reason):
                # Not this wake's to answer: unseen and unstamped (2026-09-25).
                p = replace(p, escalations=[],
                            unavailable=[u for u in p.unavailable if not u.startswith("escalations")])
            if follow_through is not None:
                try:
                    ctx = await asyncio.wait_for(follow_through_context(follow_through, p.now),
                                                 timeout=PACK_SOURCE_TIMEOUT_SECONDS)
                except Exception as exc:  # noqa: BLE001
                    print(f"[mind] follow-through context unavailable: {exc}")
                    ctx = {"chat_identifier": follow_through.chat_identifier, "chat_name": follow_through.chat_name,
                           "why": follow_through.why, "thread": []}
                if ctx:
                    p = replace(p, follow_through=ctx)
            message = render_pack(p)
            memory_query = memory_query or memory_query_for(p)
    except Exception as exc:  # noqa: BLE001
        klass = _error_class(exc)
        why = f"{klass}: could not collect the pack: {str(exc)[:200]}"
        print(f"[mind] wake failed ({reason}): {why}")
        if record:
            await _record_now(source=reason, outcome="failed", note=why, tools=[])
        return {"ok": False, "skipped": False, "note": why, "error_class": klass}

    lock = thread_lock(conversation_id)
    try:
        await lock.acquire()
    except asyncio.CancelledError:
        # The caller's bound ran out while another turn held the thread (a
        # wake_me behind a long nightly): no turn ran, nothing was served.
        await _settle_cancelled(reason, "waiting", record=record)
        raise
    # How far this wake got, for a cancellation from outside: ``before`` its
    # turn, in the ``turn``, ``after`` it, or ``settled`` once its row is
    # being written (a second row is never written).
    stage, counted = "before", False
    turn_timeout = float(timeout_s) if timeout_s else wake_timeout_for(reason, now=_now())
    try:
        # The turn this wake runs is the one finished after this point; a
        # record left from an earlier turn in this task is never journaled.
        prior_turn = _last_turn()
        try:
            if p is not None and pack is None and await _drop_handled_escalations(p):
                message = render_pack(p)
                memory_query = memory_query_for(p)
            if required_tools and not mention_still_served(reason, mention_ids,
                                                           await _mentions_waiting(reason, mention_ids)):
                # The mention this wake was asked to answer is gone (the owner
                # pressed Done, another wake handled it, the mind gave up): the wake
                # still runs for what else waits, but nothing forces a reply
                # into a thread that no longer needs one (review 2026-09-26).
                print(f"[mind] the {reason} wake's mentions are no longer waiting — no forced reply")
                required_tools = None
            # Whom this turn may reach (the toolset's TurnPolicy, 2026-10-11): the
            # caller's, else the reason's default; and the legacy tool list
            # it implies for EDWARD_TOOLSET=legacy.
            if policy is None:
                values = policy_values(reason, reply_to=_mention_reply_to(p, mention_ids) if reason == "mention"
                                       else (), follow_through=follow_through, timeout_s=turn_timeout)
                policy = make_policy(values)
            else:
                values = _policy_reach(policy)
            if tools is None:
                tools = legacy_tools_for(reason, values)
            if conversation_id in _OWN_THREADS:
                await _ensure_conversation(conversation_id)
            settings = await _settings()
            graph = await _graph()
            from services.graph.tools import set_current_conversation_id
            try:
                set_current_conversation_id(conversation_id)
            except Exception:  # noqa: BLE001
                pass
            # The journal entry this wake may write gets its id now, so a push
            # or note the turn sends opens the entry, not this raw thread
            # (thought journal, 2026-09-25). The pulse writes none.
            if turn_source in thought_journal.NEVER_JOURNAL_SOURCES:
                thought_journal.clear_entry()
            else:
                thought_journal.begin_entry()
            try:
                # Routing (2026-09-24): the mind thinks on its
                # own model/effort, the pulse on the pulse knobs, a speak turn
                # on EDWARD_SPEAK_MODEL when set.
                turn_model, turn_effort = route_for_wake(reason, turn_source, settings.model)
                # Provenance: what this wake remembers points
                # at its reason and the heartbeat events it was served.
                from services import provenance_service
                with provenance_service.source_context(
                        **provenance_service.wake_source_fields(reason, p.escalations if p else None)):
                    stage = "turn"
                    note = await asyncio.wait_for(
                        chat_with_memory(
                            turn_source=turn_source,
                            message=message,
                            conversation_id=conversation_id,
                            system_prompt=(system_prompt or mind_system_prompt()) + "\n\n" + (settings.system_prompt or ""),
                            model=turn_model,
                            reasoning_effort=turn_effort,
                            nudge_for_tools=False,
                            temperature=settings.temperature,
                            graph=graph,
                            required_tools=required_tools,
                            max_history_chars=max_history_chars,
                            extraction_profile="mind",
                            skip_extraction=skip_extraction,
                            memory_query=memory_query,
                            **_policy_kwargs(chat_with_memory, policy, tools),
                        ),
                        timeout=turn_timeout,
                    )
            finally:
                if p is not None and moves_last_wake(reason):
                    _state.last_wake_at = p.now
            stage = "after"
            # His closing sentences, whole, for the journal's "Why"; the
            # ledger keeps the first 300 characters as before.
            closing = str(note or "")
            note = " ".join(closing.split())[:MAX_NOTE_CHARS] or "(no note)"
            used: List[Dict[str, Any]] = []
            turn_id = None
            try:
                from services import turn_ledger
                turn = turn_ledger.last_finished_turn()
                if turn is not None:
                    used, turn_id = list(turn.tools or []), turn.id
            except Exception:  # noqa: BLE001
                pass
            outcome = wake_outcome(used)
            print(f"[mind] wake ({reason}) {outcome}: {note}")
            journal = dict(reason=reason, turn=_new_turn(prior_turn), turn_source=turn_source, closing=closing,
                           p=p, message=message, conversation_id=conversation_id)
            if not record:
                # A caller that keeps its own rows (the pulse, the judgement
                # check); its entry has no mind row to hang a verdict on.
                out = {"ok": True, "skipped": False, "note": note, "outcome": outcome, "tools": used,
                       "turn_id": turn_id}
                stage = "settled"
                thought_id = await _journal(**journal, ledger_id=None)
                if thought_id:
                    out["thought_id"] = thought_id
                return out
            detail: Dict[str, Any] = {}
            if nightly:
                # Sunday's decay, then triage;
                # the scheduler's tick pulses for the morning scene.
                detail.update(await _after_nightly(sunday=sunday))
            stage = "settled"
            ledger_id = await _record_now(source=reason, outcome=outcome, note=note, tools=used, turn_id=turn_id,
                                          detail=detail)
            if p is not None and p.escalations:
                try:
                    await _mark_considered([e["id"] for e in p.escalations], f"mind:{ledger_id or 'unrecorded'}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[mind] could not stamp considered events: {exc}")
            out = {"ok": True, "skipped": False, "note": note, "outcome": outcome, "tools": used,
                   "turn_id": turn_id, "ledger_id": ledger_id}
            # A wake that chose to act becomes one entry the owner can read
            # (thought journal, 2026-09-25); its id rides on the ledger row.
            thought_id = await _journal(**journal, ledger_id=ledger_id)
            if thought_id:
                out["thought_id"] = thought_id
            return out
        except Exception as exc:  # noqa: BLE001
            klass = _error_class(exc)
            if isinstance(exc, asyncio.TimeoutError):
                why = f"{klass}: the turn ran past {turn_timeout:g}s"
            else:
                why = f"{klass}: {str(exc)[:200]}"
            print(f"[mind] wake failed ({reason}): {why}")
            detail: Dict[str, Any] = {}
            if p is not None and p.escalations:
                counted = True
                why = await _count_failed_attempt([e["id"] for e in p.escalations], why, detail)
            ledger_id = None
            stage = "settled"
            if record:
                ledger_id = await _record_now(source=reason, outcome="failed", note=why, tools=[], detail=detail)
            # What the failed turn did before it died decides whether it may
            # be retried: never after a delivered send, a push or a note
            # (review 2026-09-26).
            failed_turn = _new_turn(prior_turn)
            acts = list(getattr(failed_turn, "actions", None) or [])
            out = {"ok": False, "skipped": False, "note": why, "error_class": klass,
                   "transient": is_transient_error(exc),
                   "outcome": wake_outcome(list(getattr(failed_turn, "tools", None) or [])),
                   "reached": thought_journal.reached_someone(acts),
                   "committed": thought_journal.should_journal(acts, turn_source)}
            # A wake can act and then die (a send cannot be taken back): what
            # it had done by then is still an entry, and says it stopped.
            thought_id = await _journal(
                reason=reason, turn=failed_turn, turn_source=turn_source,
                closing=f"This wake did not finish ({why[:200]}); the above is what it had done by then.",
                p=p, message=message, conversation_id=conversation_id, ledger_id=ledger_id)
            if thought_id:
                out["thought_id"] = thought_id
            return out
    except asyncio.CancelledError:
        # Cancelled from outside (2026-09-25) — the scheduler's bound,
        # shutdown. Settled once, then passed on: a cancellation is never
        # swallowed.
        if stage != "settled":
            ran = stage in ("turn", "after")
            await _settle_cancelled(
                reason, stage, record=record,
                served=[e["id"] for e in p.escalations] if ran and not counted and p is not None else (),
                journal=dict(reason=reason, turn=_new_turn(prior_turn), turn_source=turn_source, p=p,
                             message=message, conversation_id=conversation_id) if ran else None)
        raise
    finally:
        lock.release()


# A request that arrives while a mention wake is already running (its pack
# may be collected) is not dropped: exactly one more wake follows, if
# anything is still waiting then. A request before the wake starts is
# simply part of it — that is the coalescing.
REARM_REASONS = ("mention",)


def request_wake(reason: str, *, delay_s: float = 60.0, extra: Optional[str] = None,
                 required_tools: Optional[List[str]] = None, attempt: int = 0,
                 profile: Optional[Dict[str, Any]] = None, purpose: Optional[str] = None,
                 mention_ids: Optional[Sequence[str]] = None) -> bool:
    """Ask for a wake soon; repeated asks within ``delay_s`` become one.
    Ten texts in a minute are one wake, not ten (pacing, not judgment).
    True when a new wake was scheduled, False when the ask joined one.

    ``messages`` is a shim (2026-10-11): inbound goes to the noticer
    (``noticing_service.request_notice(None)``), or is stored here, so Today
    never floods and no model wakes for it.

    A retry (``attempt`` > 0, 2026-09-25) is kept apart from ordinary asks
    (its own slot, ``retry:<reason>``) so neither swallows the other; it
    carries the failed wake's ``profile`` (thread, tools, prompt, policy,
    event metadata …) and ``purpose`` label, and is skipped when a mention
    has nothing left waiting.

    ``mention_ids`` (2026-09-26) are the heartbeat events a mention wake is
    asked to answer; coalesced and rearmed asks merge them. A mention retry
    or follow-up whose mentions nobody waits on any more hands what else
    waits to the shim, or does nothing."""
    if reason == "messages":
        return _notice_inbound()
    st = _state
    ids = set(str(i) for i in mention_ids) if mention_ids else None
    if attempt:
        key = f"retry:{reason}"
        existing = st.pending.get(key)
        # A retry task asks for its own successor from inside itself; its
        # own slot is not "busy" to it (the chain used to stop after one).
        if existing is not None and not existing.done() and existing is not _current_task():
            return False
        _schedule_wake(st, reason, delay_s=delay_s, extra=extra, required_tools=required_tools,
                       attempt=attempt, profile=profile, purpose=purpose, key=key, mention_ids=ids)
        return True
    existing = st.pending.get(reason)
    if existing is not None and not existing.done():
        if reason in REARM_REASONS and reason in st.running:
            if reason not in st.rearm:
                print(f"[mind] a {reason} arrived during a {reason} wake — one more wake will follow it")
            prior = st.rearm.get(reason) or {}
            st.rearm[reason] = {"delay_s": delay_s, "extra": extra, "required_tools": required_tools,
                                "mention_ids": _merge_ids(prior.get("mention_ids"), ids)}
        elif reason in st.mention_ids and ids is not None:
            st.mention_ids[reason] |= ids
        return False
    _schedule_wake(st, reason, delay_s=delay_s, extra=extra, required_tools=required_tools, mention_ids=ids)
    return True


def _current_task() -> Optional["asyncio.Task"]:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _merge_ids(a: Optional[set], b: Optional[set]) -> Optional[set]:
    """Union of two id sets; unknown (None) only when both are unknown."""
    if a is None and b is None:
        return None
    return set(a or ()) | set(b or ())


def _schedule_wake(st: _State, reason: str, *, delay_s: float, extra: Optional[str],
                   required_tools: Optional[List[str]], rearmed: bool = False, attempt: int = 0,
                   profile: Optional[Dict[str, Any]] = None, purpose: Optional[str] = None,
                   key: Optional[str] = None, mention_ids: Optional[set] = None) -> None:
    key = key or reason
    st.due[key] = datetime.now(timezone.utc) + timedelta(seconds=max(0.0, float(delay_s)))
    if mention_ids is not None:
        st.mention_ids[key] = set(mention_ids)
    else:
        st.mention_ids.pop(key, None)

    async def _later():
        cancelled = False
        try:
            await asyncio.sleep(delay_s)
            # Started: it is ``waking`` now, no longer the next wake. Its
            # mentions are fixed now; later asks rearm a follow-up.
            ids: Optional[set] = None
            if st.pending.get(key) is task:
                st.due.pop(key, None)
                ids = st.mention_ids.pop(key, None)
            if not attempt:
                st.running.add(reason)
            follow_up = bool(rearmed or (attempt and reason in REARM_REASONS))
            if follow_up and reason == "mention" and ids is not None:
                # Keyed on the mentions themselves, not "anything pending":
                # with the mind path on nearly every inbound line waits, so
                # the global check almost never skipped (review 2026-09-26).
                ids = set(await _still_waiting(sorted(ids)))
            if follow_up and reason == "mention" and ids is not None and not ids:
                if await has_pending_escalations():
                    print(f"[mind] follow-up {reason} wake: its mentions are handled — the rest of the inbound "
                          "goes to the noticer")
                    request_wake("messages", delay_s=FOLLOW_UP_MESSAGES_DELAY_S)
                else:
                    print(f"[mind] follow-up {reason} wake skipped: nothing is waiting any more")
            elif follow_up and ids is None and not await has_pending_escalations():
                print(f"[mind] follow-up {reason} wake skipped: nothing is waiting any more")
            elif attempt:
                from contextlib import nullcontext

                from services.call_purpose import purpose_scope

                kw = {k: v for k, v in (profile or {}).items() if v is not None}
                if ids is not None:
                    kw["mention_ids"] = sorted(ids)
                with (purpose_scope(purpose) if purpose else nullcontext()):
                    await wake(reason, extra=extra, required_tools=required_tools, attempt=attempt, **kw)
            elif ids is not None:
                await wake(reason, extra=extra, required_tools=required_tools, mention_ids=sorted(ids))
            else:
                await wake(reason, extra=extra, required_tools=required_tools)
        except asyncio.CancelledError:
            cancelled = True
        except Exception as exc:  # noqa: BLE001
            print(f"[mind] requested {reason} wake failed: {exc}")
        finally:
            if not attempt:
                st.running.discard(reason)
            if st.pending.get(key) is task:
                st.pending.pop(key, None)
                st.due.pop(key, None)
                st.mention_ids.pop(key, None)
            again = st.rearm.pop(reason, None) if not attempt else None
            if again is not None and not cancelled and st is _state:
                _schedule_wake(st, reason, delay_s=again["delay_s"], extra=again["extra"],
                               required_tools=again["required_tools"], rearmed=True,
                               mention_ids=again.get("mention_ids"))

    try:
        task = asyncio.get_running_loop().create_task(_later())
    except RuntimeError:
        st.due.pop(key, None)
        return
    st.pending[key] = task


def pending_wake_due() -> Optional[datetime]:
    """When the soonest requested wake (a coalesced ask, a retry) is due,
    or None. In-process; ``/api/mind/now`` folds it into ``next_wake_at``."""
    st = _state
    due = [st.due[k] for k, t in st.pending.items() if k in st.due and not t.done()]
    return min(due) if due else None


async def recent(limit: int = 20, *, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The mind's ledger, newest first."""
    from sqlalchemy import desc, select

    from services.database import LedgerEventModel, async_session

    q = select(LedgerEventModel).where(LedgerEventModel.kind == "mind")
    if since is not None:
        naive = since.astimezone(timezone.utc).replace(tzinfo=None) if since.tzinfo else since
        q = q.where(LedgerEventModel.created_at >= naive)
    async with async_session() as s:
        rows = (await s.execute(q.order_by(desc(LedgerEventModel.created_at), desc(LedgerEventModel.id))
                                .limit(limit))).scalars().all()
    out = []
    for r in rows:
        try:
            detail = json.loads(r.detail_json) if r.detail_json else {}
        except ValueError:
            detail = {}
        row = {"id": r.id, "at": (r.created_at.isoformat() + "Z") if r.created_at else None,
               "source": r.source, "outcome": r.outcome, "note": r.reason, "turn_id": r.ref_id,
               "tools": detail.get("tools", []), "surfaces": detail.get("surfaces", []),
               "delivered": detail.get("delivered", False)}
        # The wake's journal entry, when it acted (thought journal, 2026-09-25).
        if isinstance(detail.get("thought_id"), str) and detail["thought_id"]:
            row["thought_id"] = detail["thought_id"]
        out.append(row)
    return out


# ---- the owner's window ---------------------------------------------------------------

VERDICTS = ("right_call", "wrong_call")
LOG_PUSH_WINDOW_S = 300  # a push counts as this wake's when sent within the turn's timeout before its row


async def log(days: int = 7) -> Dict[str, Any]:
    """The wakes of the last ``days``, each with the pushes it sent (from
    ``push_log``, by the mind's thread and time) and the owner's verdict on it."""
    from sqlalchemy import select

    from services.database import PushLogModel, async_session
    from services.user_signals_service import recent_signals

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    wakes = await recent(limit=400, since=since)

    pushes: List[Any] = []
    # A push from a wake is logged under the wake's journal entry since
    # 2026-09-25 (it opens the entry), under the mind's thread before that.
    by_thought = {w["thought_id"]: w for w in wakes if w.get("thought_id")}
    try:
        naive_since = (since - timedelta(seconds=LOG_PUSH_WINDOW_S)).replace(tzinfo=None)
        async with async_session() as s:
            pushes = (await s.execute(
                select(PushLogModel).where(
                    PushLogModel.conversation_id.in_([MIND_CONVERSATION_ID, *by_thought]),
                    PushLogModel.sent_at >= naive_since)
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] log: push ledger unavailable: {exc}")

    verdicts: Dict[str, Dict[str, Any]] = {}
    try:
        for sgn in reversed(await recent_signals(days=days)):  # oldest first, so the latest verdict wins
            if sgn.get("kind") != "mind_verdict" or not sgn.get("ref_id"):
                continue
            try:
                detail = json.loads(sgn.get("detail_json") or "{}")
            except ValueError:
                detail = {}
            verdicts[str(sgn["ref_id"])] = {"verdict": detail.get("verdict"), "note": detail.get("note")}
    except Exception as exc:  # noqa: BLE001
        print(f"[mind] log: signals unavailable: {exc}")

    # A push belongs to the first wake recorded after it was sent (the
    # row is written when the turn ends), never to a later one as well.
    for w in wakes:
        w["pushes"] = []
        w["verdict"] = verdicts.get(str(w["id"]))
    oldest_first = sorted((w for w in wakes if _parse_iso(w.get("at")) is not None),
                          key=lambda w: _parse_iso(w["at"]))
    for row in sorted(pushes, key=lambda r: r.sent_at or datetime.min):
        entry_wake = by_thought.get(row.conversation_id)
        if entry_wake is not None:
            entry_wake["pushes"].append({"id": row.id, "title": row.title, "opened": row.opened_at is not None,
                                         "held": row.held_reason})
            continue
        sent = _aware(row.sent_at)
        if sent is None:
            continue
        for w in oldest_first:
            at = _parse_iso(w["at"])
            if at + timedelta(seconds=5) >= sent and (at - sent).total_seconds() <= LOG_PUSH_WINDOW_S:
                w["pushes"].append({"id": row.id, "title": row.title, "opened": row.opened_at is not None,
                                    "held": row.held_reason})
                break
    return {"days": days, "wakes": wakes}


async def open_intentions() -> List[Dict[str, Any]]:
    """Live ``intention`` memories with the wake, if any, that is waiting on each."""
    from sqlalchemy import or_, select

    from services.database import MemoryModel, ScheduledEventModel, async_session
    from services.scheduled_events_service import linked_memory_id

    now = datetime.utcnow()
    async with async_session() as s:
        rows = (await s.execute(
            select(MemoryModel).where(
                MemoryModel.memory_type == "intention",
                or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > now),
            ).order_by(MemoryModel.created_at.desc()).limit(200)
        )).scalars().all()
        events = (await s.execute(
            select(ScheduledEventModel).where(
                ScheduledEventModel.status.in_(("pending", "processing")),
                ScheduledEventModel.event_metadata.isnot(None),
            ).order_by(ScheduledEventModel.next_fire_at.asc())
        )).scalars().all()
    waiting: Dict[str, Any] = {}
    for ev in events:
        mid = linked_memory_id(ev.event_metadata)
        if mid and mid not in waiting:
            try:
                why = (json.loads(ev.event_metadata or "{}") or {}).get("why")
            except ValueError:
                why = None
            waiting[mid] = {"event_id": ev.id, "next_fire_at": _iso(ev.next_fire_at), "next_fire_why": why or ev.description}
    out = []
    for r in rows:
        w = waiting.get(r.id, {})
        out.append({"id": r.id, "content": r.content, "created_at": _iso(r.created_at), "valid_until": _iso(r.valid_until),
                    "event_id": w.get("event_id"), "next_fire_at": w.get("next_fire_at"),
                    "next_fire_why": w.get("next_fire_why")})
    return out


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat() + ("" if dt.tzinfo else "Z")


async def record_verdict(ledger_id: int, verdict: str, note: Optional[str] = None) -> Optional[str]:
    """The owner's "right call?" on a wake — or on a turn that ran code on its own
    (``thought_journal.ACTED_KIND``) — → a ``user_signals`` row the next
    pack renders."""
    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session
    from services.user_signals_service import normalize_signal, record_signal

    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {list(VERDICTS)}")
    async with async_session() as s:
        row = (await s.execute(
            select(LedgerEventModel).where(LedgerEventModel.id == int(ledger_id),
                                           LedgerEventModel.kind.in_(("mind", thought_journal.ACTED_KIND)))
        )).scalar_one_or_none()
    if row is None:
        return None
    kind, detail = normalize_signal("mind_verdict", {"verdict": verdict, "note": (note or "").strip() or None})
    return await record_signal(kind, detail, conversation_id=MIND_CONVERSATION_ID, ref_id=str(row.id))
