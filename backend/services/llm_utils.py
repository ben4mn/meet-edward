"""
Shared LLM helpers.

Policy: all runtime LLM calls must go through these builders. Anthropic
runs only through ``ChatClaude`` (``EDWARD_LLM_PROVIDER=anthropic``, the
default) — never import ``langchain_anthropic`` in application code.

``build_background_llm`` — the cheap/fast model for memory extraction,
triage, reflection, deep retrieval, consolidation, search tags.

``build_chat_llm`` — the chat model for the main conversation path
(longer ``max_tokens``, configurable temperature). Used by
``graph/nodes.py`` respond node and ``graph/streaming.py``.

Routing knobs for the Codex fallback (2026-09-24):
``mind_model_for`` / ``mind_effort_for`` and ``pulse_model_for`` /
``pulse_effort`` say which model and reasoning effort a wake or a pulse
runs on, so "mind on Astra/high, pulse on Terra/low" is configuration, not
code; ``effort_with_headroom`` raises either one level while the week has
spare quota (2026-09-25). ``provider_for`` derives the provider from the
model id.

Every model id passes through ``services.model_catalog`` on the way to the
Codex endpoint: a retired or mistyped id is rewritten to the catalog's
recommended model with one WARNING per id, never a silent 400 on every turn
(gpt-5.4, 2026-09-05) and never a silent switch nobody can see.

``EDWARD_LLM_PROVIDER=anthropic`` (the default, also when unset;
``services/claude_routing``) makes every builder here return ``ChatClaude``
with a per-purpose (model, effort) from one table; ``codex`` switches them to
the Codex fallback (GPT through the Codex CLI's login), where the catalog and
the knobs below apply.
"""

import logging
import os
import re
from typing import Dict, Optional

from langchain_core.messages import SystemMessage

from services import model_catalog
from services.claude_routing import DEFAULT_ROUTES, VOICE_TURN_SOURCES

logger = logging.getLogger(__name__)

# The defaults are the Claude routes (services/claude_routing.DEFAULT_ROUTES):
# the chat model and the background model. The live chat value is
# settings.model; these only cover callers with none.
DEFAULT_CHAT_MODEL = DEFAULT_ROUTES["chat"].model
DEFAULT_BACKGROUND_MODEL = DEFAULT_ROUTES["background"].model

# The Codex fallback's own defaults (EDWARD_LLM_PROVIDER=codex only).
# Chat: the catalog's preferred model (also the fallback for stale claude-*
# checkpoint strings). Background work (memory extraction, triage L2,
# reflection, consolidation, search tags) rides the like-for-like successor
# of the retired gpt-5.4; EDWARD_BACKGROUND_MODEL=gpt-5.6-luna is the
# cheaper lever if the subscription window gets tight. Read per call
# (``background_model()``) so a change needs no restart.
CODEX_CHAT_MODEL = model_catalog.PREFERRED_MODEL
CODEX_BACKGROUND_MODEL = "gpt-5.6-terra"

_warned: set = set()


def _warn_once(key: str, reason: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning("model resolution: %s", reason)


def resolve_model(requested: str | None, *, purpose: str) -> str:
    """The catalog's answer for ``requested``, logging a rewrite once."""
    model, reason = model_catalog.resolve_chat_model(requested)
    if reason:
        _warn_once(f"{purpose}:{requested}", f"{purpose}: {reason}")
    return model


def llm_provider() -> str:
    """``anthropic`` (default) or ``codex`` (``EDWARD_LLM_PROVIDER``)."""
    from services.claude_routing import llm_provider as _provider

    return _provider()


def anthropic_enabled() -> bool:
    from services.claude_routing import anthropic_enabled as _enabled

    return _enabled()


def turn_model_for(model: Optional[str], turn_source: Optional[str] = None) -> Optional[str]:
    """The model a turn records and builds with. On Codex, ``model`` as
    given (``build_chat_llm`` resolves it through the catalog). On Claude,
    the table's model for the turn's purpose — a Codex id from settings or a
    stale checkpoint never reaches Anthropic."""
    if not anthropic_enabled():
        return model
    from services import claude_routing

    return claude_routing.claude_model_for(claude_routing.purpose_for_turn(turn_source, model), model)


def background_model() -> str:
    """The tier background work runs on right now (env, validated)."""
    if anthropic_enabled():
        from services.claude_routing import claude_route

        return claude_route("background").model
    return resolve_model(
        os.getenv("EDWARD_BACKGROUND_MODEL", CODEX_BACKGROUND_MODEL),
        purpose="background",
    )


def voice_model() -> str:
    """The tier spoken turns run on (``services.voice.prompt``, validated)."""
    if anthropic_enabled():
        from services.claude_routing import claude_route

        return claude_route("voice").model
    from services.voice.prompt import VOICE_MODEL

    return resolve_model(VOICE_MODEL, purpose="voice")


def provider_for(model: Optional[str], given: Optional[str] = None) -> str:
    """The provider of the LLM ``build_chat_llm`` will actually build.

    On the Codex fallback: GPT and o-series ids are OpenAI. Anthropic and
    empty ids are rewritten to the catalog's recommended GPT model there,
    so they are OpenAI too. ``given`` (the settings column) is consulted only for an id
    this function does not recognise. Until 2026-09-24 ``chat_with_memory``
    defaulted ``provider="anthropic"`` and the migration stamped old
    settings rows the same way, so the OpenAI-only branches (the 401
    refresh, forced ``tool_choice``) depended on a column nobody looked at.

    With ``EDWARD_LLM_PROVIDER=anthropic`` every builder builds Claude, so
    the answer is ``anthropic`` whatever the id (2026-10-10).
    """
    if anthropic_enabled():
        return "anthropic"
    m = (model or "").strip().lower()
    if not m or m.startswith(("gpt-", "claude")) or re.match(r"^o\d", m):
        return "openai"
    return (given or "openai").strip().lower() or "openai"


def _apply_effort(llm, model: str, effort: Optional[str]) -> None:
    """Set ``reasoning_effort`` through the catalog: a level the model does
    not support becomes its default instead of a 400."""
    if not effort or not hasattr(llm, "reasoning_effort"):
        return
    resolved = model_catalog.effort_for(model, effort)
    if not resolved:
        return
    try:
        llm.reasoning_effort = resolved
    except (AttributeError, ValueError, TypeError):
        pass


def _build_gpt_llm(
    model: str,
    temperature: float,
    max_tokens: int,
    priority: str = "interactive",
):
    """Shared GPT builder. Routes to ``ChatOpenAI`` when we have an API
    key, falls back to ``ChatCodexResponses`` for Codex OAuth.
    """
    from services.codex_auth_service import get_codex_auth_manager

    manager = get_codex_auth_manager()
    api_key = manager.get_api_key()

    if manager._auth_source == "api_key" and api_key:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
        )

    # Codex OAuth — any model in the ChatGPT subscription's Codex catalog;
    # the id is passed straight through to the Responses endpoint.
    from services.codex_chat_model import ChatCodexResponses

    return ChatCodexResponses(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key or "",
        priority=priority,
    )


def build_background_llm(
    temperature: float = 0,
    max_tokens: int = 512,
    priority: str = "background",
    model: str | None = None,
    vision: bool = False,
    effort: str | None = None,
    route: str | None = None,
):
    """Build an LLM for background tasks (memory, triage, etc.).

    Uses ``background_model()``. Background priority: paced by the daily
    spend cap on Claude (``llm_spend``), or on the Codex fallback by the
    rate gate against the account's real usage meter — either way deferred
    once the budget is nearly spent so enrichment loops can't starve
    interactive chat.

    ``priority="interactive"`` opts a user-critical call (a look at a photo
    the owner asked about, the greeting) out of the budget deferral — it must
    never starve behind bulk enrichment traffic. ``"triage"`` is the same
    without the cooldown wait.

    ``model`` overrides the background model for one caller (entity
    extraction rides the cheaper Luna tier since 2026-09-05).

    ``vision=True`` guarantees a model that accepts images: the chosen tier
    when it can see, else the recommended one.

    ``effort`` asks one caller's reasoning effort; it wins over
    ``EDWARD_BACKGROUND_REASONING_EFFORT``. Either passes through the
    catalog, so an unsupported level falls back to the model's default.

    On Claude (``EDWARD_LLM_PROVIDER=anthropic``) the ``background`` row of
    ``claude_routing`` decides model and effort (``route`` picks another
    row, e.g. ``"judge"``); a current ``claude-*`` ``model`` is honoured,
    ``effort`` and ``vision`` need nothing (every Claude model sees).
    """
    if anthropic_enabled():
        from services.claude_routing import build_claude_llm

        llm = build_claude_llm(route or "background", model=model, max_tokens=max_tokens, priority=priority)
        try:
            from services.event_ledger import caller_label

            llm.purpose = caller_label()
        except Exception:  # noqa: BLE001
            pass
        return llm
    chosen = resolve_model(model, purpose="background-override") if model else background_model()
    if vision:
        chosen = model_catalog.vision_model(chosen)
    llm = _build_gpt_llm(chosen, temperature, max_tokens, priority=priority)
    try:
        # Name the job for the rate gate's ledger (ChatOpenAI has no such
        # field; a plain attribute is fine there and harmless).
        from services.event_ledger import caller_label

        llm.purpose = caller_label()
    except Exception:  # noqa: BLE001
        pass
    # EDWARD_BACKGROUND_REASONING_EFFORT=low is the cheap lever for the
    # enrichment tiers; unset means the model's catalog default. An explicit
    # per-call effort wins (2026-09-24).
    _apply_effort(llm, chosen, effort or os.getenv("EDWARD_BACKGROUND_REASONING_EFFORT") or None)
    return llm


def chat_effort_for(model: str, reasoning_effort: Optional[str] = None,
                    turn_source: Optional[str] = None) -> Optional[str]:
    """The reasoning effort a chat-path turn asks for (before the catalog).

    An explicit effort wins. Otherwise ``EDWARD_CHAT_REASONING_EFFORT``
    applies to every turn except a spoken one: first-token latency is the
    point there. The exemption is keyed on the turn source (``voice`` or the
    hub's ``hub_voice``, ``VOICE_TURN_SOURCES``); the old model-id check
    (the turn runs on the voice model) is only the fallback for callers that
    do not say what the turn is.
    """
    if reasoning_effort:
        return reasoning_effort
    if turn_source in VOICE_TURN_SOURCES:
        return None
    if turn_source is None and model == voice_model():
        return None
    return os.getenv("EDWARD_CHAT_REASONING_EFFORT") or None


def build_chat_llm(
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    reasoning_effort: Optional[str] = None,
    turn_source: Optional[str] = None,
):
    """Build an LLM for the main chat path.

    ``model`` None means the route's model on Claude (``DEFAULT_CHAT_MODEL``
    for chat) or ``CODEX_CHAT_MODEL`` on the Codex fallback; longer
    ``max_tokens`` than ``build_background_llm`` to leave room for streaming
    responses. On Codex, Anthropic model strings and ids the catalog no
    longer serves are rewritten to the recommended model (one WARNING per
    id) so stale ``state["model"]`` values from old checkpoints never fail a
    turn.

    ``reasoning_effort`` is this turn's effort (a wake's per-reason level,
    the pulse's ``low``); ``turn_source`` says what the turn is, which is
    what exempts a spoken turn from ``EDWARD_CHAT_REASONING_EFFORT``. See
    ``chat_effort_for``. Every effort passes through the catalog.

    On Claude the turn's purpose (``turn_source``: voice, mind, pulse, else
    chat) picks the table row; ``reasoning_effort`` and
    ``EDWARD_CHAT_REASONING_EFFORT`` are Codex knobs and do not apply.
    """
    if anthropic_enabled():
        from services import claude_routing

        purpose = claude_routing.purpose_for_turn(turn_source, model)
        return claude_routing.build_claude_llm(purpose, model=model, max_tokens=max_tokens, priority="interactive")
    chosen = resolve_model(model or CODEX_CHAT_MODEL, purpose="chat")
    llm = _build_gpt_llm(chosen, temperature, max_tokens)
    # EDWARD_CHAT_REASONING_EFFORT buys the chat path more thinking than the
    # model's catalog default (Sol's is "low"). Read per call, so no restart;
    # a level the model does not support falls back to its default
    # (model_catalog.effort_for). The background tiers have their own knob.
    _apply_effort(llm, chosen, chat_effort_for(chosen, reasoning_effort, turn_source))
    return llm


# ---------------------------------------------------------------- routing --
#
# The Codex fallback's routing knobs (2026-09-24). The mind
# used settings.model for everything and one env var set the effort of every
# chat-path turn, so a Sunday reflect and a surfaces pulse cost the same.
# Each helper reads its env per call (no restart) and resolves through the
# catalog like every other id.

# The mind's default effort when neither the map nor a built-in names the
# reason. "high": nobody is waiting on a morning or evening wake.
DEFAULT_MIND_EFFORT = "high"
# Built-in per-reason efforts. Reflect is the week's deepest turn; a mention
# has someone waiting in a thread; a messages wake is triage. ``surfaces``
# (the pulse's wake) follows ``pulse_effort()`` so a generic wiring of
# ``mind_effort_for(reason)`` never puts 15-25 pulses a day on "high".
# The surplus wakes (2026-09-24): nobody is waiting on a nightly
# reflection or a research turn; a prep wake has a meeting coming. Free time
# (2026-09-25) is spare quota spent on his own judgment: think hard.
MIND_EFFORT_BY_REASON: Dict[str, str] = {
    "reflect": "xhigh", "mention": "medium", "messages": "medium",
    "reflect_lite": "high", "research": "high", "prep": "medium", "free": "high",
}
DEFAULT_PULSE_EFFORT = "low"
# The pulse's preferred tier when the catalog lists it.
DEFAULT_PULSE_MODEL = "gpt-5.6-terra"


def _parse_effort_map(raw: Optional[str]) -> Dict[str, str]:
    """``"reflect=xhigh, mention=medium"`` → dict. Malformed entries are
    skipped; ``*`` means every reason not named."""
    out: Dict[str, str] = {}
    for part in (raw or "").split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if key and value:
            out[key] = value
    return out


def _resolve_with_fallback(requested: Optional[str], fallback: str, *, purpose: str) -> str:
    """The catalog's answer for ``requested``; when the catalog rejects it,
    the (validated) ``fallback`` rather than the recommended model."""
    if requested:
        model, reason = model_catalog.resolve_chat_model(requested)
        if not reason:
            return model
        _warn_once(f"{purpose}:{requested}", f"{purpose}: {reason}; using {fallback}")
    return resolve_model(fallback, purpose=purpose)


def _effort(effort: Optional[str], model: Optional[str]) -> Optional[str]:
    if not effort:
        return None
    return model_catalog.effort_for(model, effort) if model else effort


def mind_model_for(settings_model: str) -> str:
    """The model a mind wake runs on: ``EDWARD_MIND_MODEL``, else settings
    (on Claude, the table's ``mind`` row)."""
    if anthropic_enabled():
        from services.claude_routing import claude_route

        return claude_route("mind").model
    return _resolve_with_fallback(os.getenv("EDWARD_MIND_MODEL") or None, settings_model, purpose="mind")


def mind_effort_for(reason: str, *, model: Optional[str] = None) -> Optional[str]:
    """The reasoning effort for a wake of ``reason``.

    Order: the ``EDWARD_MIND_EFFORT_MAP`` entry for the reason, its ``*``
    entry, the built-in per-reason level, then ``EDWARD_MIND_REASONING_EFFORT``
    (default ``high``). With ``model`` the answer is resolved through the
    catalog (an unsupported level becomes the model's default); without,
    ``build_chat_llm`` resolves it when the turn is built.
    """
    reason = (reason or "").strip().lower()
    env_map = _parse_effort_map(os.getenv("EDWARD_MIND_EFFORT_MAP"))
    if reason in env_map:
        return _effort(env_map[reason], model)
    if "*" in env_map:
        return _effort(env_map["*"], model)
    if reason == "surfaces":
        return pulse_effort(model=model)
    if reason in MIND_EFFORT_BY_REASON:
        return _effort(MIND_EFFORT_BY_REASON[reason], model)
    return _effort(os.getenv("EDWARD_MIND_REASONING_EFFORT") or DEFAULT_MIND_EFFORT, model)


def pulse_model_for(settings_model: str) -> str:
    """The model the surfaces pulse runs on: ``EDWARD_PULSE_MODEL``, else
    Terra when the catalog lists it (frequent, modest value per call), else
    settings. On Claude, the table's ``pulse`` row."""
    if anthropic_enabled():
        from services.claude_routing import claude_route

        return claude_route("pulse").model
    requested = os.getenv("EDWARD_PULSE_MODEL") or None
    if requested:
        return _resolve_with_fallback(requested, settings_model, purpose="pulse")
    if any(m.slug == DEFAULT_PULSE_MODEL for m in model_catalog.listed_models()):
        return DEFAULT_PULSE_MODEL
    return resolve_model(settings_model, purpose="pulse")


def pulse_effort(*, model: Optional[str] = None) -> Optional[str]:
    """The pulse's reasoning effort: ``EDWARD_PULSE_REASONING_EFFORT``, else low."""
    return _effort(os.getenv("EDWARD_PULSE_REASONING_EFFORT") or DEFAULT_PULSE_EFFORT, model)


# ------------------------------------------------------ depth follows quota --
#
# 2026-09-25 (a design decision: give the mind as much freedom and compute
# as the paid week allows, Codex fallback only). The weekly
# Codex meter sat flat at 41 % with hours to the reset: more than half the
# paid week expired unused while 127 of 134 wakes ran at medium effort with
# almost no reasoning. While the week has spare quota — the surplus decision
# allows spending and leaves at least ``DEPTH_HEADROOM_PERCENT`` of headroom —
# the mind and the pulse think one level harder. The decision is recomputed
# from the live meter, so this stops by itself as the week fills up.

EFFORT_LEVELS: tuple = ("minimal", "low", "medium", "high", "xhigh")
DEPTH_HEADROOM_PERCENT = 20.0


def effort_with_headroom(base: Optional[str], decision, *, model: Optional[str] = None,
                         turn_source: Optional[str] = None,
                         min_headroom: float = DEPTH_HEADROOM_PERCENT) -> Optional[str]:
    """``base`` one level higher while surplus is allowed with at least
    ``min_headroom`` % headroom, else ``base``. Pure (reads the catalog).

    The raise is capped at what ``model`` supports (the next supported level
    above, or ``base`` when there is none). ``base=None`` means the model's
    default, which is what gets raised; with no model there is nothing to
    raise. Never a spoken turn (``VOICE_TURN_SOURCES``): first-token
    latency is the point there."""
    if turn_source in VOICE_TURN_SOURCES:
        return base
    if decision is None or not getattr(decision, "allowed", False):
        return base
    headroom = getattr(decision, "headroom", None)
    if not isinstance(headroom, (int, float)) or headroom < min_headroom:
        return base
    start = base if base is not None else (model_catalog.default_effort(model) if model else None)
    if start not in EFFORT_LEVELS:
        return base
    info = model_catalog.get_model(model) if model else None
    supported = info.efforts if (info is not None and info.efforts) else None
    for level in EFFORT_LEVELS[EFFORT_LEVELS.index(start) + 1:]:
        if supported is None or level in supported:
            return level
    return base


def mind_effort_is_pinned(reason: str) -> bool:
    """Does an env setting name this wake's effort? Then spare quota does not
    raise it: ``EDWARD_MIND_EFFORT_MAP`` naming the reason (or ``*``), or
    ``EDWARD_MIND_REASONING_EFFORT`` for a reason with no built-in level."""
    reason = (reason or "").strip().lower()
    env_map = _parse_effort_map(os.getenv("EDWARD_MIND_EFFORT_MAP"))
    if reason in env_map or "*" in env_map:
        return True
    if reason == "surfaces":
        return pulse_effort_is_pinned()
    if reason in MIND_EFFORT_BY_REASON:
        return False
    return bool(os.getenv("EDWARD_MIND_REASONING_EFFORT"))


def pulse_effort_is_pinned() -> bool:
    """``EDWARD_PULSE_REASONING_EFFORT`` set: the pulse keeps that level."""
    return bool(os.getenv("EDWARD_PULSE_REASONING_EFFORT"))


def make_background_system_message(content: str) -> SystemMessage:
    """Build a SystemMessage for background tasks."""
    return SystemMessage(content=content)
