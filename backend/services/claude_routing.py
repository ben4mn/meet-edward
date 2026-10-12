"""Which provider Edward's model calls use, and on Claude, which model for what.

``EDWARD_LLM_PROVIDER`` (read per call, so no restart): ``anthropic`` — the
default, also when unset — makes every builder in ``services/llm_utils``
return ``ChatClaude`` (``services/claude_chat_model``) with the (model,
effort) of the purpose it serves, from one table; ``codex`` switches every
builder to the Codex fallback (``services/codex_chat_model``, off unless
asked for):

=========== ================== ======== ===== =====================================
purpose     model              effort   cache used by
=========== ================== ======== ===== =====================================
chat        claude-sonnet-5-5  medium   5m    the owner's chat, ping replies, scheduled
                                              events, heartbeat replies, workers —
                                              every chat-path turn not named below
mind        claude-sonnet-5-5  medium   5m    wakes (``turn_source="mind"``): the
                                              speaker (``EDWARD_SPEAK_MODEL`` when
                                              set), the nightly, free time
pulse       claude-sonnet-5-5  medium   5m    the surfaces pulse
background  claude-haiku-5-5   high     5m    enrichment: extraction, entities,
                                              consolidation, the noticer
                                              (``EDWARD_NOTICE_MODEL`` when set),
                                              photo descriptions, tags
voice       claude-sonnet-5-5  low      1h    spoken turns: the app's live voice
                                              and the household hub's
                                              (``VOICE_TURN_SOURCES``)
judge       claude-opus-5-5    high     5m    ``build_background_llm(route="judge")``
=========== ================== ======== ===== =====================================

The defaults are the routes a model benchmark measured on the original
build (2026-10-11; the benchmark tooling is not part of this repo;
``docs/architecture/llm-and-quota.md``): Sonnet
5.5 at medium tied Opus 5.5 at medium on ping turns (judge 0.92 against
0.94, 2 wins / 10 ties / 4 losses) at 46 % of the cost and 2.3× the speed,
and agentic turns share that shape; Haiku 5.5 at high beat Sonnet at low on
extraction, entities and consolidation and caught 4 of 4 must-notice cases
with no false noticing. Never run an agentic route at Sonnet low (the
weakest agent tested). Turns are hours apart, so a 1-hour cache write is
rarely read again: every route but voice caches for 5 minutes.
If the judgement check fails on Sonnet, ``EDWARD_CLAUDE_MIND_MODEL=
claude-opus-5-5`` with ``_EFFORT=low`` is the measured fallback (set R-opus).

Each row is overridable: ``EDWARD_CLAUDE_<PURPOSE>_MODEL`` (a ``claude-*``
id), ``_EFFORT`` (low | medium | high | xhigh | max), ``_MAX_TOKENS``,
``_THINKING_DISPLAY`` (summarized | omitted) and ``_CACHE_TTL`` (5m | 1h |
off). A bad override is ignored with one WARNING.

Model ids are fixed strings, never looked up in the Codex catalog. A Codex
id (``settings.model``, a stale checkpoint) and a retired Claude id never
reach Anthropic: only a current id (``KNOWN_CLAUDE_MODELS`` or one a route
is configured with) is honoured when a caller names one, everything else
gets the purpose's model.

The Codex effort knobs (``EDWARD_CHAT_REASONING_EFFORT``, the mind's
per-reason map and the spare-quota raise, which reads the Codex meter) do
not apply on Claude: the table's effort is what is sent. The surplus
queue, which spent a prepaid Codex week, is retired (2026-10-11); its switch
(``llm_rate_gate.surplus_enabled``) still reads off on Claude unless
``EDWARD_SURPLUS`` asks, and the spend gate refuses every ``surplus`` call
whatever it says (``services/llm_spend.py``).

This module imports nothing heavy at load: ``llm_rate_gate`` asks it which
provider is on.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PROVIDER_ENV = "EDWARD_LLM_PROVIDER"
CODEX = "codex"
ANTHROPIC = "anthropic"
_ANTHROPIC_SPELLINGS = ("anthropic", "claude")
_CODEX_SPELLINGS = ("codex", "openai", "gpt")

EFFORTS = ("low", "medium", "high", "xhigh", "max")
THINKING_DISPLAYS = ("summarized", "omitted")
CACHE_TTLS = ("5m", "1h")
MIN_MAX_TOKENS = 1024
MAX_MAX_TOKENS = 128_000

# Current Claude models a caller may name. Anything else is mapped.
KNOWN_CLAUDE_MODELS = frozenset({
    "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5", "claude-sonnet-5",
})
_CLAUDE_ID = re.compile(r"^claude-[a-z0-9.-]+$")
_DATE_SUFFIX = re.compile(r"-\d{8}$")

_warned: set = set()


def _warn_once(key: str, text: str, *args) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(text, *args)


def llm_provider() -> str:
    """``anthropic`` (default, also when unset) or ``codex``, from
    ``EDWARD_LLM_PROVIDER``."""
    raw = (os.getenv(PROVIDER_ENV) or "").strip().lower()
    if raw in _CODEX_SPELLINGS:
        return CODEX
    if raw and raw not in _ANTHROPIC_SPELLINGS:
        _warn_once(f"provider:{raw}", "%s=%r is not codex or anthropic; staying on anthropic", PROVIDER_ENV, raw)
    return ANTHROPIC


def anthropic_enabled() -> bool:
    return llm_provider() == ANTHROPIC


@dataclass(frozen=True)
class ClaudeRoute:
    purpose: str
    model: str
    effort: str
    max_tokens: int
    thinking_display: str = "summarized"
    cache_ttl: Optional[str] = "1h"


# The defaults (the benchmark's set R, 2026-10-11; see the table above).
# Thinking counts against max_tokens, so every row leaves room for it (Codex
# never sent a max at all, so callers' small values were reply sizes, not
# caps). Background calls are short-lived single shots: no summaries (nobody
# reads their reasoning).
DEFAULT_ROUTES: Dict[str, ClaudeRoute] = {
    "chat": ClaudeRoute("chat", "claude-sonnet-5-5", "medium", 32000, "summarized", "5m"),
    "mind": ClaudeRoute("mind", "claude-sonnet-5-5", "medium", 32000, "summarized", "5m"),
    "pulse": ClaudeRoute("pulse", "claude-sonnet-5-5", "medium", 16000, "summarized", "5m"),
    "background": ClaudeRoute("background", "claude-haiku-5-5", "high", 16000, "omitted", "5m"),
    "voice": ClaudeRoute("voice", "claude-sonnet-5-5", "low", 8000, "summarized", "1h"),
    "judge": ClaudeRoute("judge", "claude-opus-5-5", "high", 16000, "omitted", "5m"),
}
PURPOSES = tuple(DEFAULT_ROUTES)

# Spoken turns: the owner's live voice in the app (``voice``) and the
# household hub's (``hub_voice``, 2026-10-11 — someone in the owner's home,
# not necessarily the owner; ``toolset.policy``). One route and the same latency rules for both;
# who may do what is the policy's business, never this table's.
VOICE_TURN_SOURCES = frozenset({"voice", "hub_voice"})


def model_family(model: Optional[str]) -> str:
    return _DATE_SUFFIX.sub("", (model or "").strip().lower())


def claude_route(purpose: str) -> ClaudeRoute:
    """The route for ``purpose`` (unknown purposes are chat), with its env
    overrides applied. Read per call."""
    key = (purpose or "chat").strip().lower()
    route = DEFAULT_ROUTES.get(key) or DEFAULT_ROUTES["chat"]
    prefix = f"EDWARD_CLAUDE_{route.purpose.upper()}_"
    updates: Dict[str, object] = {}

    model = (os.getenv(prefix + "MODEL") or "").strip().lower()
    if model:
        if _CLAUDE_ID.match(model):
            updates["model"] = model
        else:
            _warn_once(f"{prefix}MODEL:{model}", "%sMODEL=%r is not a claude-* id; ignored", prefix, model)
    effort = (os.getenv(prefix + "EFFORT") or "").strip().lower()
    if effort:
        if effort in EFFORTS:
            updates["effort"] = effort
        else:
            _warn_once(f"{prefix}EFFORT:{effort}", "%sEFFORT=%r is not one of %s; ignored", prefix, effort, EFFORTS)
    raw_max = (os.getenv(prefix + "MAX_TOKENS") or "").strip()
    if raw_max:
        try:
            value = int(raw_max)
            if not MIN_MAX_TOKENS <= value <= MAX_MAX_TOKENS:
                raise ValueError(raw_max)
            updates["max_tokens"] = value
        except ValueError:
            _warn_once(f"{prefix}MAX_TOKENS:{raw_max}", "%sMAX_TOKENS=%r is not a token count; ignored",
                       prefix, raw_max)
    display = (os.getenv(prefix + "THINKING_DISPLAY") or "").strip().lower()
    if display:
        if display in THINKING_DISPLAYS:
            updates["thinking_display"] = display
        else:
            _warn_once(f"{prefix}DISPLAY:{display}", "%sTHINKING_DISPLAY=%r ignored", prefix, display)
    ttl = (os.getenv(prefix + "CACHE_TTL") or "").strip().lower()
    if ttl:
        if ttl in CACHE_TTLS:
            updates["cache_ttl"] = ttl
        elif ttl in ("off", "none", "0"):
            updates["cache_ttl"] = None
        else:
            _warn_once(f"{prefix}TTL:{ttl}", "%sCACHE_TTL=%r ignored", prefix, ttl)
    return replace(route, **updates) if updates else route


def known_claude_model(model: Optional[str]) -> bool:
    """A current Claude id a caller may name: a known one, or one a route
    is configured with."""
    family = model_family(model)
    if not family.startswith("claude-"):
        return False
    if family in KNOWN_CLAUDE_MODELS:
        return True
    return any(model_family(claude_route(p).model) == family for p in PURPOSES)


def _codex_voice_model() -> Optional[str]:
    try:
        from services.voice.prompt import VOICE_MODEL

        return VOICE_MODEL
    except Exception:  # noqa: BLE001
        return None


def purpose_for_turn(turn_source: Optional[str], model: Optional[str] = None) -> str:
    """The table row a chat-path turn uses: voice (either spoken source),
    mind and pulse by their turn source, everything else chat. A turn that
    does not say what it is but asks for the voice model is a spoken one
    (as ``chat_effort_for`` reads it)."""
    source = (turn_source or "").strip().lower()
    if source in VOICE_TURN_SOURCES:
        return "voice"
    if source in ("mind", "pulse"):
        return source
    if not source and model and model in (_codex_voice_model(), claude_route("voice").model):
        return "voice"
    return "chat"


def claude_model_for(purpose: str, requested: Optional[str] = None) -> str:
    """``requested`` when it is a current Claude id, else the purpose's model."""
    if requested and known_claude_model(requested):
        return requested.strip()
    return claude_route(purpose).model


def build_claude_llm(purpose: str, *, model: Optional[str] = None, max_tokens: Optional[int] = None,
                     priority: str = "interactive"):
    """A ``ChatClaude`` for ``purpose``. A caller's ``max_tokens`` larger than
    the route's is kept; a smaller one is not (it was sized for a reply with
    no thinking)."""
    from services.claude_chat_model import ChatClaude

    route = claude_route(purpose)
    cap = route.max_tokens
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > cap:
        cap = min(max_tokens, MAX_MAX_TOKENS)
    return ChatClaude(
        model=claude_model_for(route.purpose, model),
        effort=route.effort,
        max_tokens=cap,
        thinking_display=route.thinking_display,
        cache_ttl=route.cache_ttl,
        priority=priority,
    )
