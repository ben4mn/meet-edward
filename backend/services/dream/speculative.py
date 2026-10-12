"""
Speculative deep retrieval.

Deep retrieval was originally fire-and-forget: results only showed up as
``memory_enrichments`` for the next turn. Two-turn conversations never saw
any benefit. This helper speculatively kicks off deep retrieval at the
start of a turn and waits up to ``timeout`` seconds. If it returns in
time, the caller gets memories to merge into the current turn. If not, a
background fallback task writes the result as enrichments for the next
turn — matching the original behavior so the feature never gets worse.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Tuple

from services.memory_service import Memory

logger = logging.getLogger(__name__)


async def _fallback_write_enrichments(
    task: "asyncio.Task[list[Memory]]",
    conversation_id: str,
    query_source: str,
) -> None:
    """When speculation misses, complete the task and persist its result
    as ``memory_enrichments`` so the next turn still benefits.
    """
    try:
        memories = await task
    except Exception as e:
        logger.warning("speculative deep retrieval fallback failed: %s", e)
        return

    if not memories:
        return

    try:
        from services.database import async_session, MemoryEnrichmentModel

        async with async_session() as session:
            for m in memories:
                session.add(
                    MemoryEnrichmentModel(
                        conversation_id=conversation_id,
                        memory_id=m.id,
                        memory_content=m.content,
                        memory_type=m.memory_type,
                        importance=m.importance,
                        temporal_nature=m.temporal_nature,
                        query_source=query_source,
                        score=m.score,
                        consumed=False,
                    )
                )
            await session.commit()
        logger.info(
            "speculative deep retrieval stored enrichments (fallback)",
            extra={
                "subsystem": "speculative_deep_retrieval",
                "conversation_id": conversation_id,
                "count": len(memories),
            },
        )
    except Exception as e:
        logger.warning(
            "speculative deep retrieval enrichment write failed: %s", e
        )


async def speculative_deep_retrieve(
    message: str,
    recent_msgs: list,
    conversation_id: str,
    already_retrieved_ids: Iterable[str] | None = None,
    timeout: float = 0.8,
    limit: int = 15,
) -> Tuple[List[Memory], bool]:
    """Kick off deep retrieval, wait up to ``timeout`` seconds for it.

    Returns ``(memories, consumed)``. When ``consumed`` is True the caller
    should merge the memories into the current turn. When False, the
    caller proceeds without them; a background task completes deep
    retrieval and writes enrichments for the next turn.
    """
    # Import at call time so tests can monkeypatch
    # ``services.deep_retrieval_service.deep_retrieve_memories``.
    from services import deep_retrieval_service

    already_ids = set(already_retrieved_ids or [])

    async def _fetch() -> List[Memory]:
        try:
            retrieved = await deep_retrieval_service.deep_retrieve_memories(
                message, recent_msgs, limit=limit, conversation_id=conversation_id
            )
        except Exception as e:
            logger.warning("speculative deep retrieval inner call failed: %s", e)
            return []
        return [m for m in retrieved if m.id not in already_ids]

    task = asyncio.create_task(_fetch())
    try:
        memories = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return memories, True
    except asyncio.TimeoutError:
        asyncio.create_task(
            _fallback_write_enrichments(task, conversation_id, message)
        )
        return [], False
    except Exception as e:
        logger.warning("speculative deep retrieval failed: %s", e)
        return [], False
