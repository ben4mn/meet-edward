"""Token usage tracking service — records per-request usage and provides summaries."""
import logging
from datetime import datetime
from sqlalchemy import select, func

from services.database import async_session, UsageEventModel

logger = logging.getLogger(__name__)

# Track when this module was loaded (≈ backend start) for session stats
_session_start = datetime.utcnow()


async def record_usage(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    conversation_id: str | None = None,
    source: str = "chat",
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
):
    """Insert a usage event row."""
    try:
        async with async_session() as session:
            event = UsageEventModel(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                conversation_id=conversation_id,
                source=source,
            )
            session.add(event)
            await session.commit()
    except Exception as e:
        logger.error(f"Failed to record usage: {e}")


def usage_counts(message) -> dict | None:
    """Token counts from an LLM response message, whichever shape it has.

    LangChain's ``usage_metadata`` first (``ChatCodexResponses`` fills it,
    with cached input under ``input_token_details.cache_read`` and
    reasoning under ``output_token_details.reasoning``), then the raw
    ``response_metadata["usage"]`` in Codex shape
    (``input_tokens_details.cached_tokens``) or Anthropic shape
    (``cache_read_input_tokens``). Before 2026-09-24 the loops read only
    the Anthropic names off Codex responses, so cached tokens were always
    0. ``None`` when the message reported no usage. Pure.
    """
    if message is None:
        return None

    def _n(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    meta = getattr(message, "usage_metadata", None)
    if meta:
        return {
            "input_tokens": _n(meta.get("input_tokens")),
            "output_tokens": _n(meta.get("output_tokens")),
            "cache_read_tokens": _n((meta.get("input_token_details") or {}).get("cache_read")),
            "cache_creation_tokens": _n((meta.get("input_token_details") or {}).get("cache_creation")),
            "reasoning_tokens": _n((meta.get("output_token_details") or {}).get("reasoning")),
        }
    usage = (getattr(message, "response_metadata", None) or {}).get("usage") or {}
    if not isinstance(usage, dict) or not ("input_tokens" in usage or "output_tokens" in usage):
        return None
    return {
        "input_tokens": _n(usage.get("input_tokens")),
        "output_tokens": _n(usage.get("output_tokens")),
        "cache_read_tokens": _n((usage.get("input_tokens_details") or {}).get("cached_tokens")
                                or usage.get("cache_read_input_tokens")),
        "cache_creation_tokens": _n(usage.get("cache_creation_input_tokens")),
        "reasoning_tokens": _n((usage.get("output_tokens_details") or {}).get("reasoning_tokens")),
    }


async def record_message_usage(
    message,
    *,
    provider: str,
    model: str,
    conversation_id: str | None = None,
    source: str = "chat",
) -> None:
    """``record_usage`` from a response message: the right field names for
    either provider, and the caller's real ``source`` (the turn source —
    ``mind``, ``pulse``, ``scheduled_event`` … — not ``chat`` for all)."""
    counts = usage_counts(message)
    if counts is None:
        return
    await record_usage(
        provider=provider,
        model=model,
        input_tokens=counts["input_tokens"],
        output_tokens=counts["output_tokens"],
        cache_creation_tokens=counts["cache_creation_tokens"],
        cache_read_tokens=counts["cache_read_tokens"],
        conversation_id=conversation_id,
        source=source,
    )


async def get_usage_summary() -> dict:
    """Return per-provider, per-model usage totals (lifetime + session)."""
    async with async_session() as session:
        _zero = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0, "requests": 0}

        # Lifetime totals by provider+model
        rows = (await session.execute(
            select(
                UsageEventModel.provider,
                UsageEventModel.model,
                func.sum(UsageEventModel.input_tokens).label("input_tokens"),
                func.sum(UsageEventModel.output_tokens).label("output_tokens"),
                func.sum(UsageEventModel.cache_creation_tokens).label("cache_creation_tokens"),
                func.sum(UsageEventModel.cache_read_tokens).label("cache_read_tokens"),
                func.count().label("requests"),
            ).group_by(UsageEventModel.provider, UsageEventModel.model)
        )).all()

        # Session totals
        session_rows = (await session.execute(
            select(
                UsageEventModel.provider,
                UsageEventModel.model,
                func.sum(UsageEventModel.input_tokens).label("input_tokens"),
                func.sum(UsageEventModel.output_tokens).label("output_tokens"),
                func.sum(UsageEventModel.cache_creation_tokens).label("cache_creation_tokens"),
                func.sum(UsageEventModel.cache_read_tokens).label("cache_read_tokens"),
                func.count().label("requests"),
            ).where(UsageEventModel.created_at >= _session_start)
            .group_by(UsageEventModel.provider, UsageEventModel.model)
        )).all()

    providers: dict = {}

    for row in rows:
        p = row.provider
        if p not in providers:
            providers[p] = {"lifetime": {**_zero}, "session": {**_zero}, "by_model": {}}
        providers[p]["lifetime"]["input_tokens"] += row.input_tokens or 0
        providers[p]["lifetime"]["output_tokens"] += row.output_tokens or 0
        providers[p]["lifetime"]["cache_creation_tokens"] += row.cache_creation_tokens or 0
        providers[p]["lifetime"]["cache_read_tokens"] += row.cache_read_tokens or 0
        providers[p]["lifetime"]["requests"] += row.requests or 0
        providers[p]["by_model"][row.model] = {
            "lifetime": {
                "input_tokens": row.input_tokens or 0, "output_tokens": row.output_tokens or 0,
                "cache_creation_tokens": row.cache_creation_tokens or 0, "cache_read_tokens": row.cache_read_tokens or 0,
                "requests": row.requests or 0,
            },
            "session": {**_zero},
        }

    for row in session_rows:
        p = row.provider
        if p not in providers:
            providers[p] = {"lifetime": {**_zero}, "session": {**_zero}, "by_model": {}}
        providers[p]["session"]["input_tokens"] += row.input_tokens or 0
        providers[p]["session"]["output_tokens"] += row.output_tokens or 0
        providers[p]["session"]["cache_creation_tokens"] += row.cache_creation_tokens or 0
        providers[p]["session"]["cache_read_tokens"] += row.cache_read_tokens or 0
        providers[p]["session"]["requests"] += row.requests or 0
        if row.model in providers[p]["by_model"]:
            providers[p]["by_model"][row.model]["session"] = {
                "input_tokens": row.input_tokens or 0, "output_tokens": row.output_tokens or 0,
                "cache_creation_tokens": row.cache_creation_tokens or 0, "cache_read_tokens": row.cache_read_tokens or 0,
                "requests": row.requests or 0,
            }

    return {"providers": providers, "session_start": _session_start.isoformat()}
