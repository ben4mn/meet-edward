"""What a Claude call cost, in dollars — pure (2026-10-10).

Under ``EDWARD_LLM_PROVIDER=anthropic`` Edward pays per token from a
prepaid balance instead of spending a weekly Codex window, so every call is
priced from its ``usage`` and the daily cap (``services/llm_spend.py``)
adds the dollars up. Nothing here does I/O; ``price`` never raises.

The usage fields, as the Messages API reports them:

* ``input_tokens`` — the **uncached remainder** of the prompt (full price).
* ``cache_read_input_tokens`` — served from the prompt cache.
* ``cache_creation_input_tokens`` — written to it, split by TTL under
  ``cache_creation.ephemeral_5m_input_tokens`` / ``ephemeral_1h_input_tokens``
  (a remainder the split does not cover is the default 5-minute TTL).
* ``output_tokens`` — includes thinking, which bills as output;
  ``output_tokens_details.thinking_tokens`` only reports how much of it.
* ``server_tool_use.web_search_requests`` — $10 per 1,000 searches.
* ``iterations`` — a request that ran more than one attempt (a server-side
  refusal fallback, compaction) itemizes each; the top level then covers
  only the attempt that produced the message, so the iterations are summed,
  each at the rates of the model that ran it.

The prompt size that picks a long-prompt rate card (Haiku 5.5 over 100K;
the 1M-context Sonnet 4.x and Opus 4.6/4.7 over 200K) is the whole prompt —
uncached + cache read + cache write. A model the table does not know is
priced at its family's dearest rates — each rate the highest any listed
member charges (review 2026-10-11: ``claude-fable-5`` once fell to Fable
5.1's $0.25 cache read instead of its $1, ``claude-opus-4-1`` to Opus 5's
$5/$25 instead of $15/$75) — and an unknown family at the dearest rates of
all, never as free: the cap must over-count rather than under-count.

Prices are USD per million tokens (``docs/architecture/llm-and-quota.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, Optional, Tuple

MTOK = 1_000_000.0
WEB_SEARCH_USD = 10.0 / 1000.0


@dataclass(frozen=True)
class Rates:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


@dataclass(frozen=True)
class ModelPrice:
    base: Rates
    # A second rate card for prompts longer than ``long_threshold`` tokens
    # (Haiku 5.5: 100K). None for models with one card.
    long: Optional[Rates] = None
    long_threshold: Optional[int] = None


def _card(inp: float, out: float, read: Optional[float] = None) -> Rates:
    """The standard multipliers: cache read 0.1x input, 5-minute write
    1.25x, 1-hour write 2x (Opus 5.5 reads at 0.05x — pass ``read``)."""
    return Rates(inp, out, read if read is not None else inp * 0.1, inp * 1.25, inp * 2.0)


# The 1M-context premium over 200K-token prompts: 2x input, 1.5x output.
_LONG_CONTEXT = 200_000


def _long(inp: float, out: float) -> Rates:
    return _card(inp, out)


PRICES: Dict[str, ModelPrice] = {
    # The three Edward routes to.
    "claude-opus-5-5": ModelPrice(_card(4.0, 20.0, read=0.20)),
    "claude-sonnet-5-5": ModelPrice(_card(2.0, 10.0, read=0.20)),
    "claude-haiku-5-5": ModelPrice(
        _card(0.10, 0.50), long=_card(0.50, 2.50), long_threshold=100_000,
    ),
    # Server-side refusal fallbacks may serve an attempt on these.
    "claude-opus-5": ModelPrice(_card(5.0, 25.0)),
    "claude-opus-4-8": ModelPrice(_card(5.0, 25.0)),
    "claude-sonnet-5": ModelPrice(_card(2.0, 10.0, read=0.20)),
    # Older or dearer ids someone might point a knob at
    # (EDWARD_CLAUDE_<PURPOSE>_MODEL takes any claude-* id).
    "claude-fable-5-1": ModelPrice(_card(10.0, 50.0, read=0.25)),
    "claude-mythos-5-1": ModelPrice(_card(10.0, 50.0, read=0.25)),
    "claude-fable-5": ModelPrice(_card(10.0, 50.0, read=1.00)),
    "claude-mythos-5": ModelPrice(_card(10.0, 50.0, read=1.00)),
    "claude-opus-4-7": ModelPrice(_card(5.0, 25.0), long=_long(10.0, 37.50), long_threshold=_LONG_CONTEXT),
    "claude-opus-4-6": ModelPrice(_card(5.0, 25.0), long=_long(10.0, 37.50), long_threshold=_LONG_CONTEXT),
    "claude-opus-4-5": ModelPrice(_card(5.0, 25.0)),
    "claude-opus-4-1": ModelPrice(_card(15.0, 75.0)),
    "claude-opus-4-0": ModelPrice(_card(15.0, 75.0)),
    "claude-sonnet-4-6": ModelPrice(_card(3.0, 15.0), long=_long(6.0, 22.50), long_threshold=_LONG_CONTEXT),
    "claude-sonnet-4-5": ModelPrice(_card(3.0, 15.0), long=_long(6.0, 22.50), long_threshold=_LONG_CONTEXT),
    "claude-sonnet-4-0": ModelPrice(_card(3.0, 15.0), long=_long(6.0, 22.50), long_threshold=_LONG_CONTEXT),
    "claude-haiku-4-5": ModelPrice(_card(1.0, 5.0)),
}

# Full ids whose alias is not the id with its date cut off.
_ALIASES = {
    "claude-opus-4": "claude-opus-4-0",
    "claude-sonnet-4": "claude-sonnet-4-0",
}


def _max_rates(cards: Iterable[Rates]) -> Rates:
    cards = list(cards)
    return Rates(*(max(getattr(c, f.name) for c in cards) for f in fields(Rates)))


def _dearest(rows: Iterable[ModelPrice]) -> ModelPrice:
    """Rates at least as high as every row's, at every prompt size: each
    base rate the highest base; past the lowest long-prompt threshold,
    each rate the highest any row charges at all."""
    rows = list(rows)
    base = _max_rates(r.base for r in rows)
    longs = [r for r in rows if r.long is not None and r.long_threshold is not None]
    if not longs:
        return ModelPrice(base)
    long = _max_rates([base] + [r.long for r in longs])
    return ModelPrice(base, long=long, long_threshold=min(r.long_threshold for r in longs))


# An id the table does not list is priced at its family's dearest rates,
# an unknown family at the dearest rates of all.
FAMILIES: Tuple[str, ...] = ("fable", "mythos", "opus", "sonnet", "haiku")
FAMILY_FALLBACK: Dict[str, ModelPrice] = {
    word: _dearest(p for k, p in PRICES.items() if word in k) for word in FAMILIES
}
UNKNOWN_FALLBACK = "claude-*"
UNKNOWN_PRICE = _dearest(PRICES.values())

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalize_model(model: Any) -> str:
    """``anthropic.claude-opus-5-5-20261001[1m]`` → ``claude-opus-5-5``."""
    m = str(model or "").strip().lower()
    if "[" in m:
        m = m.split("[", 1)[0]
    if m.startswith("anthropic."):
        m = m[len("anthropic."):]
    if m.startswith("us.anthropic.") or m.startswith("eu.anthropic."):
        m = m.split("anthropic.", 1)[1]
    m = re.sub(r"-v\d+(:\d+)?$", "", m)          # Bedrock's -v1:0
    m = _DATE_SUFFIX.sub("", m)
    return _ALIASES.get(m, m)


def resolve(model: Any) -> Tuple[str, ModelPrice, bool]:
    """(the table row priced, its prices, whether the id was known). An
    unknown id is ``claude-<family>-*`` at its family's dearest rates."""
    key = normalize_model(model)
    if key in PRICES:
        return key, PRICES[key], True
    for word in FAMILIES:
        if word in key:
            return f"claude-{word}-*", FAMILY_FALLBACK[word], False
    return UNKNOWN_FALLBACK, UNKNOWN_PRICE, False


# ------------------------------------------------------------- usage --

def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _count(obj: Any, name: str) -> int:
    value = _get(obj, name)
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


@dataclass(frozen=True)
class _Tokens:
    uncached: int = 0
    cache_read: int = 0
    write_5m: int = 0
    write_1h: int = 0
    output: int = 0
    thinking: int = 0

    @property
    def cache_write(self) -> int:
        return self.write_5m + self.write_1h

    @property
    def prompt(self) -> int:
        return self.uncached + self.cache_read + self.cache_write


def _tokens(usage: Any) -> _Tokens:
    write_total = _count(usage, "cache_creation_input_tokens")
    split = _get(usage, "cache_creation")
    w1h = _count(split, "ephemeral_1h_input_tokens")
    w5m = _count(split, "ephemeral_5m_input_tokens")
    if w5m + w1h < write_total:              # the unsplit rest is the default TTL
        w5m = write_total - w1h
    return _Tokens(
        uncached=_count(usage, "input_tokens"),
        cache_read=_count(usage, "cache_read_input_tokens"),
        write_5m=w5m,
        write_1h=w1h,
        output=_count(usage, "output_tokens"),
        thinking=_count(_get(usage, "output_tokens_details"), "thinking_tokens"),
    )


def _rates_for(row: ModelPrice, prompt_tokens: int) -> Tuple[Rates, str]:
    if row.long is not None and row.long_threshold is not None and prompt_tokens > row.long_threshold:
        return row.long, "long"
    return row.base, "base"


PART_NAMES = ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "web_search")


@dataclass(frozen=True)
class Priced:
    """A call's cost in USD, its parts and its token totals."""

    total: float
    parts: Dict[str, float] = field(default_factory=dict)
    priced_as: str = ""
    known: bool = True
    tier: str = "base"
    input_tokens: int = 0          # uncached remainder
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0         # includes thinking
    thinking_tokens: int = 0
    web_searches: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total, "parts": dict(self.parts), "priced_as": self.priced_as, "known": self.known,
            "tier": self.tier, "input_tokens": self.input_tokens, "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens, "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens, "web_searches": self.web_searches,
        }


def _price_tokens(t: _Tokens, row: ModelPrice) -> Tuple[Dict[str, float], str]:
    rates, tier = _rates_for(row, t.prompt)
    parts = {
        "input": t.uncached * rates.input / MTOK,
        "cache_read": t.cache_read * rates.cache_read / MTOK,
        "cache_write_5m": t.write_5m * rates.cache_write_5m / MTOK,
        "cache_write_1h": t.write_1h * rates.cache_write_1h / MTOK,
        "output": t.output * rates.output / MTOK,
    }
    return parts, tier


def _iterations(usage: Any) -> Iterable[Any]:
    its = _get(usage, "iterations")
    return its if isinstance(its, (list, tuple)) and its else ()


def price(model: Any, usage: Any, *, served_model: Any = None) -> Priced:
    """Price one call. ``model`` is the model asked for; ``served_model``
    the one that produced the message (``response.model``) when a fallback
    served it: the top-level usage is priced at it when there are no
    itemized ``iterations``, and so is every iteration from the first
    ``fallback_message`` on that names no ``model`` of its own (an
    iteration that names one is priced at it). Never raises; bad or
    missing usage costs 0."""
    try:
        return _price(model, usage, served_model)
    except Exception:  # noqa: BLE001 — a pricing bug must never break a call
        key, _row, known = resolve(model)
        return Priced(total=0.0, parts={p: 0.0 for p in PART_NAMES}, priced_as=key, known=known)


def _price(model: Any, usage: Any, served_model: Any) -> Priced:
    its = list(_iterations(usage))
    # Without itemized attempts the top level is the attempt that produced
    # the message — the served model's (sticky fallback routing serves a
    # conversation on the fallback model directly for about an hour).
    top_model = model if its else (served_model or model)
    key, _row, known = resolve(top_model)
    parts = {p: 0.0 for p in PART_NAMES}
    totals = _Tokens()
    tier = "base"
    attempts = []
    if its:
        # An attempt that names no model ran on the one asked for until the
        # first ``fallback_message``, and on the served model from there on
        # (sticky routing keeps the conversation on the fallback model).
        fell_back = False
        for it in its:
            if _get(it, "type") == "fallback_message":
                fell_back = True
            it_model = _get(it, "model")
            if not it_model:
                it_model = served_model if (fell_back and served_model) else model
            attempts.append((it_model, _tokens(it)))
        # Thinking is reported at the top level when the iterations do not
        # carry it themselves.
        if not any(t.thinking for _m, t in attempts):
            top_thinking = _tokens(usage).thinking
            if top_thinking:
                m0, t0 = attempts[-1]
                attempts[-1] = (m0, _Tokens(t0.uncached, t0.cache_read, t0.write_5m, t0.write_1h, t0.output,
                                            top_thinking))
    else:
        attempts.append((top_model, _tokens(usage)))

    for it_model, t in attempts:
        _it_key, it_row, it_known = resolve(it_model)
        known = known and it_known
        it_parts, it_tier = _price_tokens(t, it_row)
        if it_tier == "long":
            tier = "long"
        for name, value in it_parts.items():
            parts[name] += value
        totals = _Tokens(
            totals.uncached + t.uncached, totals.cache_read + t.cache_read, totals.write_5m + t.write_5m,
            totals.write_1h + t.write_1h, totals.output + t.output, totals.thinking + t.thinking,
        )

    searches = _count(_get(usage, "server_tool_use"), "web_search_requests")
    parts["web_search"] = searches * WEB_SEARCH_USD
    total = sum(parts.values())
    return Priced(
        total=total, parts=parts, priced_as=key, known=known, tier=tier,
        input_tokens=totals.uncached, cache_read_tokens=totals.cache_read, cache_write_tokens=totals.cache_write,
        output_tokens=totals.output, thinking_tokens=totals.thinking, web_searches=searches,
    )


def cost(model: Any, usage: Any, *, served_model: Any = None) -> float:
    """The call's cost in USD (``price(...).total``)."""
    return price(model, usage, served_model=served_model).total
