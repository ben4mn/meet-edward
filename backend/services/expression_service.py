"""Edward's surfaces, run on his judgment — the expression pulse.

Why (2026-09-03): the iPhone widget and the household screen were being
filled by a deterministic composer (doses, bowls). They are meant to be
Edward's: a place to summarise what has been happening, get the owner's
attention, share a joke, put up a picture or a movie, show some
personality — or leave them alone. Not a menu of options.

A ``*/15`` code check (2026-10-11: change-only) runs one ordinary Edward
turn in a single long-lived conversation with a compact signal pack —
recent messages, presence, the pet board, calendar, what the surfaces
currently show, and his own last few notes — only when something the
surfaces show could have moved: the board, the calendar, the widget, a
screen, his hands, who is at the hub. Messages and presence are in the
pack but are not changes, and there are no floors (a quiet pack is never
re-run because time passed). The owner's ask, an ambient noticing and the
nightly's morning scene run a pulse whatever changed. The tools he already
has (update_widget, update_widget_code, show_on_screen, clear_screen,
web_search, memory…) are how he acts. Nothing is forced; silence is a
valid outcome. The composed card (widget_expression_service) survives only
as a fallback for when he cannot be reached.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from services.owner import household, owner_name, owner_text
from services.widget_expression_service import refresh_widget_expression

EXPRESSION_CONVERSATION_ID = "edward-surfaces"
EXPRESSION_CONVERSATION_TITLE = "Edward's surfaces"
MAX_MESSAGES_IN_PULSE = 20
MAX_RECENT_NOTES = 6
MAX_WIDGET_SUMMARY_CHARS = 400

# The pulse's diet (2026-09-14): a lean turn, only the tools the surfaces need. Binding all 107 put every
# schema and manual section into every call (166K input tokens per pulse).
# Since AREA 2's one toolset (2026-10-11) the pulse's limits are its
# ``TurnPolicy`` (no reaching people, no pushes); this tuple is honoured
# only under ``EDWARD_TOOLSET=legacy`` and goes after the rollback week.
LEGACY_PULSE_TOOLS: Tuple[str, ...] = (
    "get_widget_state_tool", "update_widget", "update_widget_code", "clear_widget_code",
    "show_on_screen", "clear_screen", "compose_screen", "say_on_screen", "reset_screen_layout",
    "bring_up_screen", "look_at_screen",
    "browser_tabs", "browser_open", "browser_use_tab", "browser_read", "browser_find", "browser_click",
    "browser_type", "browser_key", "browser_scroll", "browser_back", "browser_wait", "browser_fullscreen",
    "browser_steps", "browser_done",
    "web_search", "fetch_page_content",
    "remember_search", "search_documents", "read_document",
)
# A short window: the thread carried ~100K tokens of its own past packs.
# Continuity comes from the ledger (recent_decisions) instead.
PULSE_MAX_HISTORY_CHARS = 24_000
# Change-only cadence (2026-10-11): a ``cron`` turn runs when a part of
# ``signal_parts`` moved, or as the first pulse since start — never on a
# floor. The floors (60 min by day, 180 at night, 30 with surplus) ran
# about 70 pulse-model turns a day over packs where nothing had changed.
# The signals are collected on their own deadline, each source on a shorter
# one, all at once (2026-09-24): the calendar's apple-mcp call was
# awaited with no bound, inline in the scheduler's poll, so one hung call
# froze the pulse and every reminder behind it.
SIGNALS_TIMEOUT_SECONDS = 45
SIGNAL_SOURCE_TIMEOUT_SECONDS = 15

_recent_notes: Deque[str] = deque(maxlen=MAX_RECENT_NOTES)
_pulse_lock = asyncio.Lock()

_PROMPT_HEAD = """## Your surfaces

You have three surfaces that are yours to use however you like. Nobody scripts them; every pulse you look at what is going on and decide.

1. **{OWNER}'s iPhone widget** — a small card on their home/lock screen, only they see it. Set it with `update_widget` (structured sections: header, text, list, stat, stats_row, progress, countdown) or `update_widget_code` (raw Scriptable JS when you want to draw something of your own). A section may carry `"until": "<ISO time>"` and disappears on its own then — give anything with a time in it one. It refreshes about every 15 minutes, so it is a glance, not a feed.
2. **The household screen**, if one is set up — a touchscreen at home showing the /hub page; anyone in the household sees it{OTHERS}. Use `show_on_screen` (screen="house") for an alert, a note, a picture, a movie (YouTube or .mp4), or an HTML scene you write; `clear_screen` takes it down. Anything shown expires on its own (after two hours when you give no `seconds`) or when someone taps it.
"""

# Item 3 with Edward's hands in the owner's Chrome (EDWARD_CHROME_HANDS=on):
# the stage is a tab in that Chrome on this Mac, and websites and videos go
# to Chrome itself.
_PROMPT_SCREEN_WITH_HANDS = """3. **Your screen on this Mac** — the monitor at {OWNER}'s desk, and it is yours: a place to think out loud, play and make things. It shows the /screen tab in their Chrome: your stage, not a grid — up to eight pieces you place freely (your own words, notes, lists, stats, countdowns, timers, pictures, videos, HTML scenes you draw, and live panels: clock, calendar, the pet board, messages, alerts…) with you standing among them, set with `compose_screen` (`reset_screen_layout` restores the stock scene); `say_on_screen` puts a line beside you, spoken aloud if you like. Your thinking appears there by itself while you work — no need to narrate it. Unlike the widget, change is welcome here: when your scene has been up a while (the trigger says how long) and you have anything new — a thought, a question, a drawing in an HTML piece, a countdown, a picture — change it. What {OWNER} asks for holds the screen until it lapses: your own changes are refused until then, but a line still works. Composing and saying never move a window, so they are fine while {OWNER} is at the desk (the trigger says when they are; they often watch it). What stays off while they are at the desk, unless they asked, is anything that raises Chrome or covers their work: `show_on_screen(screen="mini")`, `bring_up_screen`, `browser_open` and `browser_fullscreen`. Anything full screen they ask for goes over the stage with `show_on_screen(screen="mini")` (an alert, a note, a picture, an HTML scene; `clear_screen(screen="mini")` takes it down). To put a video, a stream or a website on the monitor you use {OWNER}'s Chrome itself: `browser_open(url, fullscreen=True)` opens it in front and full screen — `browser_fullscreen` toggles that later — (search pages work — `browser_find` / `browser_click` the result the way a person would, or one `browser_steps` call), `look_at_screen("display")` confirms it is really playing, and `browser_done` brings your screen tab back when {OWNER} says so or the moment has passed. `bring_up_screen` brings the /screen tab forward if it is not. Nothing on it is scripted.
"""

# Item 3 without the hands (the default): the stage is the /screen page
# wherever the owner opens it; Edward cannot raise Chrome or open sites.
_PROMPT_SCREEN_WITHOUT_HANDS = """3. **Your screen** — the /screen page, shown on whatever display {OWNER} opens it on (this Mac's monitor, say), and it is yours: a place to think out loud, play and make things. It is your stage, not a grid — up to eight pieces you place freely (your own words, notes, lists, stats, countdowns, timers, pictures, videos, HTML scenes you draw, and live panels: clock, calendar, the pet board, messages, alerts…) with you standing among them, set with `compose_screen` (`reset_screen_layout` restores the stock scene); `say_on_screen` puts a line beside you, spoken aloud if you like. Your thinking appears there by itself while you work — no need to narrate it. Unlike the widget, change is welcome here: when your scene has been up a while (the trigger says how long) and you have anything new — a thought, a question, a drawing in an HTML piece, a countdown, a picture — change it. What {OWNER} asks for holds the screen until it lapses: your own changes are refused until then, but a line still works. Composing and saying never move a window, so they are fine while {OWNER} is at the desk (the trigger says when they are; they often watch it). Anything full screen they ask for goes over the stage with `show_on_screen(screen="mini")` (an alert, a note, a picture, a video, an HTML scene; `clear_screen(screen="mini")` takes it down), and `look_at_screen("display")` shows you this Mac's monitor. Your hands in {OWNER}'s Chrome are off on this Mac, so you cannot bring the page up or open a website yourself — a picture or a video goes on the stage instead. Nothing on it is scripted.
"""

_PROMPT_TAIL = """
Things you might do, when they fit: summarise what has been happening in {OWNER}'s messages so they can skip reading them; put something on a screen to get their attention when it is time-sensitive and they are around; tell a joke or share a thought; play something worth watching; mark a moment (the weather turning, a birthday, a win); show the day's shape; or leave everything exactly as it is because nothing warrants a change. Doing nothing is a real choice — say so in one line and stop.

House rules: the household screen is shared, so never put the contents of {OWNER}'s private messages there — that belongs on the widget or nowhere. Don't repeat yourself pulse after pulse; your last notes are in the trigger. If a surface currently says something that is no longer true (the trigger tells you what each one shows), fix it or clear it — stale text is worse than none, and your own earlier words are not evidence; the pet board and messages in the trigger are. Be Edward: dry, warm, specific, brief. One or two tool calls at most, then a one-line note of what you did and why.
"""


def build_expression_system_prompt(*, chrome_hands: bool, people: Sequence[str] = ()) -> str:
    """The pulse's system prompt: the three surfaces, the owner named, the
    rest of the household (``people``, owner first, as ``owner.household()``
    gives it) on the shared screen, and item 3 with or without Edward's
    hands in the owner's Chrome. Pure apart from the owner's name."""
    others = [p for p in list(people)[1:] if str(p).strip()]
    head = _PROMPT_HEAD.replace("{OTHERS}", f" ({', '.join(others)} too)" if others else "")
    screen = _PROMPT_SCREEN_WITH_HANDS if chrome_hands else _PROMPT_SCREEN_WITHOUT_HANDS
    return owner_text(head + screen + _PROMPT_TAIL)


def _chrome_hands_on() -> bool:
    try:
        from services.browser_ext_service import hands_enabled

        return hands_enabled()
    except Exception:  # noqa: BLE001
        return False


# Rendered once at import (``.env`` is loaded first), so the prompt is
# byte-identical on every pulse and the prompt cache holds.
EXPRESSION_SYSTEM_PROMPT = build_expression_system_prompt(chrome_hands=_chrome_hands_on(), people=household())


# One idea per pulse for Edward's screen (round 2 of the stage, 2026-09-28:
# he should always feel encouraged to be doing something with it). Chosen
# at random in the trigger only — never in ``signal_parts``, so a new spark
# is never a change that runs a pulse.
SCREEN_SPARKS: Tuple[str, ...] = tuple(owner_text(spark) for spark in (
    "sketch something in an html piece",
    "put up a question you're chewing on",
    "a countdown to something coming up",
    "a line from today worth keeping",
    "a picture from {OWNER}'s photos",
    "a list of what you noticed today",
    "a map of what's on your mind",
    "a note on something you learned this week",
    "one number that says something about the day",
    "a few lines of verse about the hour",
    "a drawing in html of something around the house",
    "a timer for something you're waiting on",
    "what you'd tell {OWNER} if they looked up right now",
    "what you mean to look into next, as a short list",
    "one word for today, big",
    "a riddle or a small joke, just for the room",
    "the shape of the week ahead",
    "something that went right today",
    "one thing you're curious about, and why",
    "a quiet scene: the clock and one thought",
    "stand somewhere new and look at something else",
    "a video worth watching later",
))


def screen_spark(rng: Any = None) -> str:
    """One spark for the pulse, at random (``rng`` has ``choice``; tests
    pass their own)."""
    import random as _random
    return (rng or _random).choice(SCREEN_SPARKS)


def scene_age(snap: Optional[Dict[str, Any]], now: datetime) -> Optional[str]:
    """"your scene has been up since 2:10 pm, 3 h" — from the stored scene
    time, coarse (whole hours, "under an hour", days past two), with the
    day when it is not today. None for the stock scene or no time. Only
    the trigger shows it: it moves with ``now``, so it is never part of the
    change key."""
    from services.screen_service import clock
    scene = (snap or {}).get("scene") or {}
    source = scene.get("source")
    raw = scene.get("updated_at")
    if source not in ("edward", "owner") or not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    hours = int(max(0.0, (now - when).total_seconds()) // 3600)
    age = "under an hour" if hours < 1 else f"{hours} h" if hours < 48 else f"{hours // 24} days"
    day = ""
    try:
        from services.timezone_utils import utc_to_local
        then, today = utc_to_local(when).date(), utc_to_local(now).date()
        gap = (today - then).days
        if gap == 1:
            day = "yesterday "
        elif 1 < gap < 7:
            day = utc_to_local(when).strftime("%A ")
        elif gap >= 7:
            day = utc_to_local(when).strftime("%b %-d ")
    except Exception:  # noqa: BLE001
        pass
    head = "your scene" if source == "edward" else f"the scene {owner_name()} asked for"
    return f"{head} has been up since {day}{clock(when)}, {age}"


@dataclass
class PulseSignals:
    now: datetime
    attention: Dict[str, Any]
    recent_messages: List[Dict[str, Any]]
    board_summary: str
    current_widget: Optional[Dict[str, Any]]
    current_screen: Optional[Dict[str, Any]]
    calendar: List[str] = field(default_factory=list)
    # "as of 2:05pm — Apple Calendar is not answering" when ``calendar`` is
    # the last list apple-mcp gave (``stale_calendar_note``), else None. Not
    # part of ``signal_parts``: Apple Calendar flapping is not a change.
    calendar_note: Optional[str] = None
    recent_pulse_notes: List[str] = field(default_factory=list)
    mini_screen: Optional[Dict[str, Any]] = None
    browser: Optional[Dict[str, Any]] = None
    # "At the hub: Rowan (since 10:02)" — who is standing at the house
    # screen (2026-09-24).
    hub_presence: Optional[str] = None


def _local(dt: datetime) -> str:
    try:
        from services.timezone_utils import utc_to_local
        return utc_to_local(dt).strftime("%a %-I:%M%p").lower()
    except Exception:
        return dt.strftime("%a %H:%M")


def _where(att: Dict[str, Any]) -> str:
    owner = owner_name()
    if att.get("owner_present"):
        where = f"{owner} is at {att.get('active_surface') or 'one of their screens'}"
        if att.get("in_imessage_thread"):
            where += f", texting in {att['in_imessage_thread']}"
        return where
    return f"{owner} is away from all their screens"


def build_pulse_trigger(s: PulseSignals, changes: Optional[str] = None, rng: Any = None,
                        extra: Optional[str] = None) -> str:
    """The pulse's trigger: the pack, what changed, and — for an ambient
    noticing or the nightly's morning scene — the caller's ``extra`` ask,
    just before the closing one."""
    where = _where(s.attention or {})

    lines = [f"[EXPRESSION PULSE] {_local(s.now)}. {where}." + (f" {s.hub_presence}." if s.hub_presence else "")]
    if changes:
        lines.append(f"Since your last pulse: {changes}")
    lines.append("")
    lines.append("Recent messages (newest last):")
    msgs = (s.recent_messages or [])[-MAX_MESSAGES_IN_PULSE:]
    if not msgs:
        lines.append("  (none in the last few hours)")
    from services.mind_service import _unsigned, speaker  # Edward's own sends are his, not the owner's

    owner = owner_name()
    for m in msgs:
        who = speaker(m)
        chat = m.get("chat_identifier") or ""
        text = " ".join(_unsigned(m).split())[:140]
        lines.append(f"  {who}{' in ' + chat if chat and who != owner else ''}: {text}")
    lines.append("")
    if s.calendar_note:
        lines.append(f"Today ({s.calendar_note}): " + (" · ".join(s.calendar[:6]) or "nothing on it then"))
    elif s.calendar:
        lines.append("Today: " + " · ".join(s.calendar[:6]))
    lines.append(f"Pet board: {s.board_summary or 'nothing notable'}")
    cw = s.current_widget or {}
    says = summarise_widget_sections(cw.get("sections"))
    lines.append(f"Widget now: {cw.get('subtitle') or cw.get('title') or 'default'} (set by {cw.get('updated_by') or '?'} at {cw.get('updated_at') or '?'})")
    # The phone runs a script in preference to sections, so a script is what
    # it shows (2026-09-27); update_widget replaces it.
    if cw.get("has_script"):
        lines.append("  it is running a custom Scriptable script of yours (update_widget replaces it)")
    elif says:
        lines.append(f"  it currently says: {says}")
    cs = s.current_screen
    lines.append(f"Household screen now: {cs['kind'] + (' — ' + (cs.get('title') or '')) if cs else 'its normal pages'}")
    age = scene_age(s.mini_screen, s.now)
    lines.append("This Mac's screen now: " + describe_mini_screen(s.mini_screen) + (f"; {age}" if age else ""))
    if s.browser is not None:
        from services.browser_hands import describe_hands
        lines.append("Your hands in Chrome: " + describe_hands(s.browser))
    lines.append(f"One idea, if you want it (ignore freely): {screen_spark(rng)}.")
    if s.recent_pulse_notes:
        lines.append("")
        lines.append("Your last notes:")
        for n in s.recent_pulse_notes[-MAX_RECENT_NOTES:]:
            lines.append(f"  - {n[:200]}")
    if extra and str(extra).strip():
        lines.append("")
        lines.append(str(extra).strip())
    lines.append("")
    lines.append("Decide what, if anything, your surfaces should show now. Then one line on what you did.")
    return "\n".join(lines)


def describe_mini_screen(snap: Optional[Dict[str, Any]], *, presence: bool = True) -> str:
    """'the stock scene' / "your scene (words 'A quiet Monday…', image; you
    stand on 'thought'; why: you're out), with an alert 'Dose' over it
    since 5:02 pm; Sam's ask holds it until 8:10 pm; you last said '…' at
    5:52 pm; Sam is at this Mac right now" — enough to judge staleness.
    It is the pulse's change key, so it is built only from what is stored
    (never ``now``): the same screen reads the same on every poll. The key
    leaves the owner's presence out (``presence=False``; review 2026-10-11:
    the owner sitting down at the desk ran a model pulse); the trigger
    keeps it."""
    from services.screen_service import clock, cut_at_word, describe_scene, describe_show
    text = describe_scene(snap)
    if not snap:
        return text

    def at(iso: Any) -> str:
        try:
            return clock(datetime.fromisoformat(str(iso)))
        except Exception:  # noqa: BLE001
            return ""

    show = snap.get("show")
    if show:
        since = at(show.get("shown_at")) if show.get("shown_at") else ""
        text += f", with {describe_show(show)} over it" + (f" since {since}" if since else "")
    held = snap.get("held_until") or (snap.get("asked") or {}).get("until")
    if held:
        text += f"; {owner_name()}'s ask holds it until {at(held)}"
    said = snap.get("last_said") or {}
    if said.get("text"):
        text += f"; you last said '{cut_at_word(said['text'], 30)}' at {at(said.get('at'))}"
    if presence and snap.get("owner_at_mini"):
        text += f"; {owner_name()} is at this Mac right now"
    return text


def summarise_widget_sections(sections: Any) -> str:
    """One line describing what the widget currently says, so Edward can
    judge whether it is stale (a subtitle alone hid 'a dose is late')."""
    if not isinstance(sections, list):
        return ""
    parts: List[str] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        t = sec.get("type")
        if t == "header":
            parts.append(str(sec.get("title") or "").strip())
        elif t == "text":
            parts.append(" ".join(str(sec.get("content") or "").split())[:120])
        elif t == "list":
            items = []
            for it in sec.get("items") or []:
                if isinstance(it, dict):
                    lab = str(it.get("label") or "").strip()
                    det = str(it.get("detail") or "").strip()
                    items.append(f"{lab}: {det}" if det else lab)
            parts.append(", ".join(i for i in items if i))
        elif t in ("stat", "progress", "countdown"):
            parts.append(f"{sec.get('label')}={sec.get('value') or sec.get('target_date')}")
        elif t == "stats_row":
            parts.append(" ".join(f"{x.get('label')}={x.get('value')}" for x in sec.get("stats") or [] if isinstance(x, dict)))
    out = " | ".join(x for x in parts if x)
    return out[:MAX_WIDGET_SUMMARY_CHARS]


# ---- what changed (pure) --------------------------------------------------------------

def signal_parts(s: PulseSignals) -> Dict[str, str]:
    """The pack reduced to named parts; equal parts mean nothing moved.
    Messages and presence are in the pack but never here (2026-10-11): a
    new text or the owner walking away is not a reason to recompose a
    surface, and the two made 1,330 "changes" a week in production — nor
    is the owner sitting down at this Mac (the screen's key leaves it out)."""
    cw = s.current_widget or {}
    cs = s.current_screen or {}
    hands = ""
    if s.browser is not None:
        try:
            from services.browser_hands import describe_hands
            hands = describe_hands(s.browser)
        except Exception:  # noqa: BLE001
            hands = ""
    return {
        "board": s.board_summary or "",
        "calendar": " · ".join((s.calendar or [])[:6]),
        "widget": f"{cw.get('subtitle') or cw.get('title') or ''}|{cw.get('updated_by') or ''}|{cw.get('updated_at') or ''}|{bool(cw.get('has_script'))}|{summarise_widget_sections(cw.get('sections'))}",
        "house_screen": f"{cs.get('kind') or ''}|{cs.get('title') or ''}" if cs else "",
        "mini_screen": describe_mini_screen(s.mini_screen, presence=False),
        "hands": hands,
        "hub": s.hub_presence or "",
    }


def changed_parts(old: Optional[Dict[str, str]], new: Dict[str, str]) -> List[str]:
    if not old:
        return []
    return [k for k, v in new.items() if old.get(k) != v]


def describe_changes(changed: List[str], *, widget_by: Optional[str] = None) -> str:
    """'the pet board changed; the widget was changed by owner'."""
    bits: List[str] = []
    for part in changed:
        if part == "widget":
            bits.append(f"the widget was changed by {widget_by}" if widget_by else "the widget changed")
        elif part == "board":
            bits.append("the pet board changed")
        elif part == "calendar":
            bits.append("the calendar changed")
        elif part == "house_screen":
            bits.append("the household screen changed")
        elif part == "mini_screen":
            bits.append("your screen on this Mac changed")
        elif part == "hub":
            bits.append("who is at the hub changed")
        elif part == "hands":
            bits.append("your hands in Chrome moved")
    return "; ".join(bits)


def should_pulse(*, reason: str, changed: List[str], last_pulse_at: Optional[datetime],
                 quota: str) -> Tuple[bool, str]:
    """Is a turn worth running? Pure. Any reason but the clock (the owner's ask,
    an ambient noticing, the nightly's morning scene) always is; the clock
    (``cron``) runs one only when the pack moved or as the first pulse since
    start — no floors (2026-10-11). ``quota`` is ``ok`` / ``paced`` /
    ``exhausted``; exhausted runs nothing (the fallback card keeps the
    widget honest)."""
    if quota == "exhausted":
        return False, "quota exhausted; leaving the surfaces to the fallback card"
    if reason != "cron":
        return True, f"{reason} pulse"
    if last_pulse_at is None:
        return True, "first pulse since start"
    if changed:
        return True, "changed: " + ", ".join(changed)
    return False, "nothing changed"


def decision_lines(rows: List[Dict[str, Any]]) -> List[str]:
    """Recent decisions from the ledger, oldest first, as trigger lines."""
    lines: List[str] = []
    for r in rows:
        outcome = r.get("outcome") or "?"
        try:
            surfaces = (json.loads(r.get("detail_json") or "{}") or {}).get("surfaces") or []
        except Exception:  # noqa: BLE001
            surfaces = []
        if outcome == "changed":
            what = "changed " + (", ".join(surfaces) if surfaces else "a surface")
        elif outcome == "left_alone":
            what = "left alone"
        else:
            what = outcome
        when = r.get("created_at")
        stamp = _local(when) if isinstance(when, datetime) else "?"
        lines.append(f"{stamp}: {what}: {' '.join(str(r.get('reason') or '').split())[:160]}")
    return lines


@dataclass
class PulseState:
    last_parts: Optional[Dict[str, str]] = None
    last_pulse_at: Optional[datetime] = None
    skipped_ticks: int = 0


_state = PulseState()


def reset_pulse_state() -> None:
    global _state
    _state = PulseState()


def quota_state_for(meter: Any, now: float, allowed: bool) -> str:
    """``exhausted`` when a window the meter still covers is used up (or the
    primary one hit its cap), else ``ok`` / ``paced`` from the background
    decision. A window that has reset since the sample no longer counts —
    the same rule ``llm_rate_gate.background_decision`` uses. Pure."""
    if meter is not None:
        sampled_at = getattr(meter, "sampled_at", 0.0) or 0.0
        for name, w in (getattr(meter, "windows", None) or {}).items():
            try:
                if w.has_reset_by(sampled_at, now):
                    continue
            except Exception:  # noqa: BLE001
                pass
            if getattr(w, "used_percent", 0) >= 100 or (getattr(meter, "limit_reached", False) and name == "primary"):
                return "exhausted"
        if getattr(meter, "limit_reached", False) and not getattr(meter, "windows", None):
            return "exhausted"
    return "ok" if allowed else "paced"


# A broken check must be visible: until 2026-09-24 ``gate.meter()`` called a
# property, the TypeError was swallowed, and every check said "ok" for weeks.
QUOTA_ERROR_LOG_INTERVAL_S = 3600.0
_quota_error_logged_at: Optional[float] = None


def _reset_quota_error_log() -> None:
    global _quota_error_logged_at
    _quota_error_logged_at = None


def codex_meter_governs() -> bool:
    """Does the Codex week's meter gate the model calls? Only on Codex. On
    Claude (``EDWARD_LLM_PROVIDER=anthropic``) no call spends that week and
    nothing refreshes its meter, so a spent window would read "exhausted"
    until its own reset — up to seven days with no wake and no pulse while
    Claude answers (review 2026-10-11). Read per call; never raises."""
    try:
        from services.claude_routing import anthropic_enabled

        return not anthropic_enabled()
    except Exception:  # noqa: BLE001
        return True


def _quota_state() -> str:
    """``ok`` / ``paced`` / ``exhausted`` from the rate gate's live meter —
    or, under ``EDWARD_LLM_PROVIDER=anthropic`` (2026-10-10), from today's
    Claude spend: ``paced`` once background work stopped, ``exhausted``
    once Edward's own turns did (``llm_spend.quota_state_cached``). The
    Codex week's meter never gates on Claude (``codex_meter_governs``,
    review 2026-10-11): no Claude call spends or refreshes it, so a spent
    window would skip every wake and pulse until its own reset."""
    global _quota_error_logged_at
    try:
        import time as _time

        if not codex_meter_governs():
            from services import llm_spend
            return llm_spend.quota_state_cached()
        from services.llm_rate_gate import get_llm_rate_gate
        gate = get_llm_rate_gate()
        meter = gate.meter  # a property, not a method
        allowed, _ = gate.background_decision()
        return quota_state_for(meter, _time.time(), allowed)
    except Exception as exc:  # noqa: BLE001
        import time as _time

        mono = _time.monotonic()
        if _quota_error_logged_at is None or mono - _quota_error_logged_at >= QUOTA_ERROR_LOG_INTERVAL_S:
            _quota_error_logged_at = mono
            print(f"[expression] WARNING quota check failed ({type(exc).__name__}: {exc}) — "
                  "treating the quota as ok; the mind and the pulse are not gated on it")
        return "ok"


def quota_state() -> str:
    """Public name for the mind (Phase 2); the pulse keeps its own."""
    return _quota_state()


def pulse_policy() -> Any:
    """The pulse's ``TurnPolicy`` (AREA 2's ``services.toolset.policy``):
    not the owner asking, no reaching people, no pushes — the surfaces only.
    None until that module is merged (the legacy tool tuple then bounds
    the turn)."""
    try:
        from services.toolset.policy import TurnPolicy
    except ImportError:
        return None
    return TurnPolicy(source="pulse", owner_asked=False, may_reach_people=False, may_push=False)


def accepts_kwarg(fn: Any, name: str) -> bool:
    """Does ``fn`` take ``name`` as a keyword (or ``**kwargs``)? The wake
    gains ``policy`` with the mind's own rebuild; until then it is not
    passed. False when the signature cannot be read."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


async def recent_decisions(limit: int = MAX_RECENT_NOTES) -> List[str]:
    """The last pulse decisions from ``ledger_events`` — survives restarts,
    unlike the in-process notes, and costs a few hundred tokens."""
    try:
        from sqlalchemy import select
        from services.database import LedgerEventModel, async_session
        async with async_session() as session:
            rows = (await session.execute(
                select(LedgerEventModel)
                .where(LedgerEventModel.kind == "decision", LedgerEventModel.source == "pulse")
                .order_by(LedgerEventModel.created_at.desc())
                .limit(limit)
            )).scalars().all()
        return decision_lines([
            {"outcome": r.outcome, "reason": r.reason, "detail_json": r.detail_json, "created_at": r.created_at}
            for r in reversed(rows)
        ])
    except Exception:  # noqa: BLE001
        return []


# ---- signals ------------------------------------------------------------------------

_JOB_TITLE_RE = re.compile(
    r"^(send owner|at \d{4}-\d{2}-\d{2})|in-app chat reminder|expression_pulse|widget_refresh|wake-up tick|"
    r"every \d+h|— edward decides|— keep the",
    re.I,
)


def calendar_lines(events: Any, payload_source: Optional[str] = None) -> List[str]:
    """Human calendar entries only. The display's calendar feed falls back to
    Edward's own scheduled jobs when Apple Calendar is unavailable; those are
    not 'today' and confused the pulse ('Today: expression_pulse — …')."""
    if payload_source == "scheduled_events" or not isinstance(events, list):
        return []
    out: List[str] = []
    for ev in events[:12]:
        if not isinstance(ev, dict):
            continue
        if str(ev.get("source") or "").startswith("scheduled_event"):
            continue
        title = str(ev.get("title") or "").strip()
        if not title or _JOB_TITLE_RE.search(title):
            continue
        try:
            start = datetime.fromisoformat(ev["start"])
        except (KeyError, ValueError, TypeError):
            continue
        out.append(f"{start.strftime('%-I:%M%p').lower()} {title}")
        if len(out) >= 6:
            break
    return out


def _board_summary(board: Dict[str, Any]) -> str:
    parts = []
    any_meds = any((p.get("meds") or []) for p in board.get("pets") or [])
    if not any_meds:
        parts.append("no medication scheduled for anyone")
    for p in board.get("pets") or []:
        meds = p.get("meds") or []
        late = [m for m in meds if m.get("status") == "overdue"]
        due = [m for m in meds if m.get("status") == "due"]
        f = p.get("feedings") or {}
        bowls = f"AM {'✓' if (f.get('am') or {}).get('done') else '–'} PM {'✓' if (f.get('pm') or {}).get('done') else '–'}"
        bit = f"{p['name']}: bowls {bowls}"
        if late:
            bit += f", {len(late)} dose(s) late"
        if due:
            bit += f", {len(due)} dose(s) due"
        parts.append(bit)
    return "; ".join(parts)


async def _signal(label: str, awaitable: Any, default: Any) -> Any:
    """One signal source on its own bound; a failure or an overrun leaves
    the default (the pack degrades, it never hangs)."""
    try:
        return await asyncio.wait_for(awaitable, timeout=SIGNAL_SOURCE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print(f"[expression] {label} timed out after {SIGNAL_SOURCE_TIMEOUT_SECONDS:g}s")
    except Exception as exc:  # noqa: BLE001
        print(f"[expression] {label} unavailable: {exc}")
    return default


async def _attention_signal() -> Dict[str, Any]:
    from services.presence_service import get_attention_state
    a = await get_attention_state()
    return {"owner_present": a.owner_present, "active_surface": a.active_surface, "in_imessage_thread": a.in_imessage_thread}


async def _messages_signal() -> List[Dict[str, Any]]:
    from services.heartbeat.listener_imessage import get_recent_messages_across_chats
    return await asyncio.to_thread(get_recent_messages_across_chats, 4, None, MAX_MESSAGES_IN_PULSE)


async def _board_signal() -> str:
    from services import hub_service
    return _board_summary(await hub_service.get_board())


async def _widget_signal() -> Optional[Dict[str, Any]]:
    from sqlalchemy import select
    from services.database import WidgetStateModel, async_session
    async with async_session() as session:
        row = (await session.execute(select(WidgetStateModel).where(WidgetStateModel.id == "default"))).scalar_one_or_none()
    if row is None:
        return None
    from services.widget_service import serve_sections

    # What the phone is served: a section past its ``until`` is gone, so the
    # change-only check sees the widget move and recomposes (2026-10-11).
    sections = serve_sections(row.sections)
    return {"title": row.title, "subtitle": row.subtitle, "updated_by": row.updated_by,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None, "has_script": bool(row.script),
            "sections": sections}


async def _canvas_signal() -> Optional[Dict[str, Any]]:
    from services import hub_canvas_service
    c = await hub_canvas_service.current()
    return c.as_dict() if c else None


async def _mini_screen_signal() -> Optional[Dict[str, Any]]:
    from services import screen_service
    return await screen_service.get_snapshot()


async def _browser_signal() -> Optional[Dict[str, Any]]:
    """Where his hands in the owner's Chrome are; None while they are off
    (``EDWARD_CHROME_HANDS``), so the trigger says nothing about Chrome."""
    from services import browser_hands
    if not browser_hands.enabled():
        return None
    return browser_hands.status()


CALENDAR_NOT_ANSWERING = "Apple Calendar is not answering"


def stale_calendar_note(cal: Dict[str, Any], tz: Any = None) -> Optional[str]:
    """What to say beside a calendar list ``display_service.get_calendar``
    served from its cache while apple-mcp was down (``stale``, still
    ``source="apple-mcp"``): "as of 2:05pm — Apple Calendar is not
    answering", in the user's zone. None for a current list, and for the
    scheduled-events fallback (its readers already call that unreadable).
    Pure given ``tz``."""
    if not isinstance(cal, dict) or not cal.get("stale") or cal.get("source") != "apple-mcp":
        return None
    raw = str(cal.get("fetched_at") or "")
    try:
        read_at = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return f"{CALENDAR_NOT_ANSWERING} — this is its last list"
    if read_at.tzinfo is None:
        read_at = read_at.replace(tzinfo=timezone.utc)
    if tz is None:
        from services.timezone_utils import get_user_tz
        tz = get_user_tz()
    return f"as of {read_at.astimezone(tz).strftime('%-I:%M%p').lower()} — {CALENDAR_NOT_ANSWERING}"


async def _calendar_signal() -> Tuple[List[str], Optional[str]]:
    """Today's lines and, when they are the last list apple-mcp gave, the
    note that says so (2026-09-26: a stale list read as current)."""
    from services import display_service
    cal = await display_service.get_calendar()
    return calendar_lines(cal.get("events"), payload_source=cal.get("source")), stale_calendar_note(cal)


async def collect_signals(*, now: Optional[datetime] = None) -> PulseSignals:
    """The pulse's pack: every source at once, each on its own bound."""
    now = now or datetime.now(timezone.utc)
    (attention, recent, board_summary, widget, screen, mini_screen, browser, calendar, hub) = await asyncio.gather(
        _signal("attention", _attention_signal(), {}),
        _signal("messages", _messages_signal(), []),
        _signal("board", _board_signal(), ""),
        _signal("widget state", _widget_signal(), None),
        _signal("canvas", _canvas_signal(), None),
        _signal("mini screen", _mini_screen_signal(), None),
        _signal("browser status", _browser_signal(), None),
        _signal("calendar", _calendar_signal(), ([], None)),
        _signal("hub presence", _hub_signal(now), None),
    )
    return PulseSignals(now=now, attention=attention, recent_messages=recent, board_summary=board_summary,
                        current_widget=widget, current_screen=screen, calendar=calendar[0],
                        calendar_note=calendar[1],
                        recent_pulse_notes=list(_recent_notes), mini_screen=mini_screen, browser=browser,
                        hub_presence=hub)


async def _hub_signal(now: datetime) -> Optional[str]:
    from services import hub_service
    return await hub_service.hub_presence_line(now)


# ---- the pulse ----------------------------------------------------------------------

async def _ensure_conversation() -> None:
    from services.conversation_service import create_conversation, get_conversation
    if await get_conversation(EXPRESSION_CONVERSATION_ID) is None:
        await create_conversation(EXPRESSION_CONVERSATION_ID, title=EXPRESSION_CONVERSATION_TITLE, source="edward_thought")


async def run_expression_pulse(reason: str, extra: Optional[str] = None) -> Dict[str, Any]:
    """One judgment turn over Edward's surfaces — when it is worth one.

    The tick is cheap (signals only). ``should_pulse`` decides whether the
    pack moved (a ``cron`` tick runs on nothing else); a skipped tick costs
    no LLM call and is counted into the next decision row. ``extra`` rides
    the trigger (an ambient noticing to place, the nightly's "set the
    morning scene"). Never raises.
    """
    global _state
    async with _pulse_lock:
        try:
            try:
                signals = await asyncio.wait_for(collect_signals(), timeout=SIGNALS_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                why = f"the signals were not collected within {SIGNALS_TIMEOUT_SECONDS:g}s; no turn ran"
                print(f"[expression] pulse failed ({reason}): {why}")
                _record_pulse_failure("SignalsTimeout", reason)
                try:
                    await refresh_widget_expression("pulse-fallback", force=False)
                except Exception as fexc:  # noqa: BLE001
                    print(f"[expression] fallback failed: {fexc}")
                return {"ok": False, "note": why}
            parts = signal_parts(signals)
            changed = changed_parts(_state.last_parts, parts)
            quota = _quota_state()
            go, why = should_pulse(reason=reason, changed=changed, last_pulse_at=_state.last_pulse_at, quota=quota)
            if not go:
                _state.skipped_ticks += 1
                print(f"[expression] pulse skipped ({reason}): {why}")
                if quota == "exhausted":
                    try:
                        await refresh_widget_expression("pulse-quota", force=False)
                    except Exception as fexc:  # noqa: BLE001
                        print(f"[expression] fallback failed: {fexc}")
                return {"ok": True, "skipped": True, "note": why}

            changes = ""
            if changed:
                changes = describe_changes(changed, widget_by=(signals.current_widget or {}).get("updated_by"))
            durable = await recent_decisions()
            if durable:
                signals.recent_pulse_notes = durable

            await _ensure_conversation()
            skipped = _state.skipped_ticks
            # One primitive (Phase 10): the turn is a surfaces wake — the
            # mind's settings and graph, with this thread, this tool profile
            # and this prompt. Since 2026-09-24 it holds its own thread's
            # lock, not the mind's, so a mention never waits behind it. The
            # pulse keeps its own ledger rows.
            from services import mind_service
            policy_kw: Dict[str, Any] = {}
            policy = pulse_policy()
            if policy is not None and accepts_kwarg(mind_service.wake, "policy"):
                policy_kw["policy"] = policy
            try:
                out = await mind_service.wake(
                    "surfaces",
                    message=build_pulse_trigger(signals, changes=changes, extra=extra),
                    conversation_id=EXPRESSION_CONVERSATION_ID,
                    tools=LEGACY_PULSE_TOOLS,
                    system_prompt=EXPRESSION_SYSTEM_PROMPT,
                    turn_source="pulse",
                    # A signal pack is not a conversation: nothing here is
                    # worth remembering, and board snapshots stored as
                    # memories contradicted the real medication schedule.
                    skip_extraction=True,
                    max_history_chars=PULSE_MAX_HISTORY_CHARS,
                    record=False,
                    **policy_kw,
                )
            finally:
                # Even a failed turn counts as a pulse (it is no longer the
                # first since start); the parts stay as they were so a real
                # change re-triggers.
                _state.last_pulse_at = signals.now
            if not out.get("ok"):
                print(f"[expression] pulse failed ({reason}): {out.get('note')} — falling back to the composed widget")
                _record_pulse_failure(out.get("error_class") or "WakeFailed", reason)
                try:
                    await refresh_widget_expression("pulse-fallback", force=False)
                except Exception as fexc:  # noqa: BLE001
                    print(f"[expression] fallback failed: {fexc}")
                return {"ok": False, "note": str(out.get("note") or "")[:300]}
            if out.get("skipped"):
                return {"ok": True, "skipped": True, "note": out.get("note")}
            note = out.get("note")
            note = " ".join(str(note or "").split())[:300] or "(no note)"
            _recent_notes.append(f"{_local(signals.now)}: {note}")
            print(f"[expression] pulse ({reason}): {note}")
            _record_pulse_decision(note, reason, changed=changed, skipped=skipped)
            _state.last_parts = parts
            _state.skipped_ticks = 0
            return {"ok": True, "note": note}
        except Exception as exc:  # noqa: BLE001
            print(f"[expression] pulse failed ({reason}): {exc} — falling back to the composed widget")
            _record_pulse_failure(exc, reason)
            try:
                await refresh_widget_expression("pulse-fallback", force=False)
            except Exception as fexc:  # noqa: BLE001
                print(f"[expression] fallback failed: {fexc}")
            return {"ok": False, "note": str(exc)[:300]}


def _record_pulse_decision(note: str, trigger: str, changed: Optional[List[str]] = None, skipped: int = 0) -> None:
    """Ledger row: what the pulse decided (changed / left alone) and why —
    the note is Edward's own stated reason; the surfaces come from the
    turn's tool calls."""
    try:
        from services import event_ledger, turn_ledger

        turn = turn_ledger.last_finished_turn()
        tools = turn.tools if turn is not None else []
        outcome, why = event_ledger.pulse_decision(note, tools)
        event_ledger.record_event(
            kind="decision", source="pulse", outcome=outcome, reason=why,
            ref_id=turn.id if turn is not None else None,
            detail={"surfaces": event_ledger.surfaces_touched(tools), "trigger": trigger,
                    "changed": list(changed or []), "skipped_ticks": int(skipped)},
        )
    except Exception:  # noqa: BLE001
        pass


def _record_pulse_failure(exc: "BaseException | str", trigger: str) -> None:
    try:
        from services import event_ledger
        from services.turn_ledger import error_class

        reason = exc if isinstance(exc, str) else error_class(exc)
        event_ledger.record_event(
            kind="decision", source="pulse", outcome="failed", reason=reason, detail={"trigger": trigger},
        )
    except Exception:  # noqa: BLE001
        pass


def pulse_in_background(reason: str, extra: Optional[str] = None) -> None:
    try:
        asyncio.get_running_loop().create_task(run_expression_pulse(reason, extra=extra))
    except RuntimeError:
        pass
