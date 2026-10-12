"""
Memory consolidation service for Edward.

Runs an in-process asyncio loop that periodically reviews recent memories,
clusters them by entity/topic, discovers connections, and flags contradictions.
Uses ``build_background_llm()`` (the ``background`` route: Claude under
``EDWARD_LLM_PROVIDER=anthropic``, else the Codex fallback) for lightweight
LLM analysis.
"""

import asyncio
import json
import logging
import math
import re
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select, func, or_, and_, delete

logger = logging.getLogger(__name__)

from services.database import (
    async_session,
    MemoryModel,
    MemoryConnectionModel,
    MemoryFlagModel,
    ConsolidationCycleModel,
    ConsolidationConfigModel,
    ConversationModel,
    MemoryEnrichmentModel,
)
from services.document_service import search_documents
from services.owner import owner_text

_consolidation_task: asyncio.Task | None = None


async def _find_existing_narrative(entity_name: str):
    """Return the existing system-generated narrative Document for an
    entity, or None.

    search_documents returns ``(docs, total)`` — the old inline code
    iterated the tuple itself, so every narrative synthesis burned an
    LLM call then crashed with "'list' object has no attribute 'tags'"
    before anything was saved.
    """
    docs, _total = await search_documents(f"Narrative: {entity_name}", limit=1)
    for d in docs:
        if d.tags and f"entity:{entity_name}" in d.tags:
            return d
    return None


async def _drop_entity_narratives(entity_name: str) -> int:
    """Delete this entity's system-generated narratives — exactly the title
    step 11 writes and the ``system_generated`` and ``entity:<name>`` tags,
    never a document the owner titled that way or another entity's whose name
    starts the same. Returns how many were deleted."""
    from services.database import DocumentModel

    title = f"Narrative: {entity_name}"
    async with async_session() as session:
        docs = (await session.execute(select(DocumentModel).where(DocumentModel.title == title))).scalars().all()
        ours = [d for d in docs
                if {"system_generated", f"entity:{entity_name}"} <= {t.strip() for t in (d.tags or "").split(",")}]
        for d in ours:
            await session.delete(d)
        await session.commit()
    return len(ours)


async def start_consolidation(startup_delay: float = 0.0) -> None:
    """Start the consolidation polling loop.

    ``startup_delay`` phase-offsets the first cycle so consolidation
    doesn't stampede the rate gate at boot alongside the other loops.
    """
    global _consolidation_task
    if _consolidation_task is not None:
        return
    _consolidation_task = asyncio.create_task(_consolidation_loop(startup_delay))
    logger.info("consolidation service started")


async def stop_consolidation() -> None:
    """Stop the consolidation polling loop."""
    global _consolidation_task
    if _consolidation_task is None:
        return
    _consolidation_task.cancel()
    try:
        await _consolidation_task
    except asyncio.CancelledError:
        pass
    _consolidation_task = None
    logger.info("consolidation service stopped")


async def _consolidation_loop(startup_delay: float = 0.0) -> None:
    """Main polling loop — runs forever until cancelled."""
    if startup_delay > 0:
        await asyncio.sleep(startup_delay)
    while True:
        try:
            # Read config each iteration so changes take effect immediately
            async with async_session() as session:
                result = await session.execute(
                    select(ConsolidationConfigModel).where(
                        ConsolidationConfigModel.id == "default"
                    )
                )
                config = result.scalar_one_or_none()

            if config and config.enabled:
                try:
                    await _run_consolidation_cycle()
                except Exception as e:
                    logger.exception("consolidation cycle error: %s", e)

            interval = config.interval_seconds if config else 3600
        except Exception as e:
            logger.exception("consolidation loop error: %s", e)
            interval = 3600

        await asyncio.sleep(interval)


# An entity narrative is written from at most this many live memories
# (retired rows are skipped, 2026-09-27).
NARRATIVE_MAX_MEMORIES = 10


# Rows below this importance are not worth an LLM call to cluster. 8 of 24
# cycles on 2026-09-05 exhausted their budget clustering chatter like
# "<a friend> says shoes cost $175" (importance 0.5 is the extractor's default).
CONSOLIDATION_MIN_IMPORTANCE = 0.5


async def load_candidate_memories(lookback_hours: int, hot_ids: list) -> list:
    """Memories a cycle may cluster: recent (created or accessed inside the
    lookback) or hot, live (not retired), and above the importance floor."""
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    now = datetime.utcnow()
    async with async_session() as session:
        recency_clause = or_(
            MemoryModel.created_at >= cutoff,
            and_(
                MemoryModel.last_accessed.isnot(None),
                MemoryModel.last_accessed >= cutoff,
            ),
        )
        scope = or_(recency_clause, MemoryModel.id.in_(list(hot_ids))) if hot_ids else recency_clause
        live = or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > now)
        result = await session.execute(
            select(MemoryModel).where(
                and_(scope, live, MemoryModel.importance >= CONSOLIDATION_MIN_IMPORTANCE)
            )
        )
        return list(result.scalars().all())


async def consolidation_enabled() -> bool:
    """Is consolidation switched on (the stored config, as the hourly loop
    reads it)? The surplus queue's deep cycle respects the same switch."""
    async with async_session() as session:
        config = (await session.execute(
            select(ConsolidationConfigModel).where(ConsolidationConfigModel.id == "default")
        )).scalar_one_or_none()
    return bool(config and config.enabled)


async def run_cycle(
    *,
    lookback_hours: int | None = None,
    max_llm_calls: int | None = None,
    max_narratives: int | None = None,
    priority: str = "background",
    purpose: str | None = None,
    effort: str | None = None,
    narrative_min_age_hours: float | None = None,
) -> dict:
    """One cycle with overrides passed in, never stored (2026-09-24).

    The surplus queue's ``consolidation_deep`` job buys a wider cycle — a
    24 h lookback, ~60 LLM calls, up to 10 entity narratives — at
    ``priority="surplus"`` with its calls labelled ``purpose``; the stored
    config keeps governing the hourly loop."""
    return await _run_consolidation_cycle(
        lookback_hours=lookback_hours, max_llm_calls=max_llm_calls, max_narratives=max_narratives,
        priority=priority, purpose=purpose, effort=effort, narrative_min_age_hours=narrative_min_age_hours,
    )


# Entity narratives synthesised per cycle unless a caller asks for more.
DEFAULT_MAX_NARRATIVES = 3

# The budget follows the quota (design decision, 2026-09-25: spare compute
# is there to be used).
# The weekly meter sat at 41 % hours before the reset while each hourly
# cycle stopped at its stored 15 calls. While the surplus decision allows
# spending with this much headroom, a cycle with no explicit budget gets
# ``SPARE_QUOTA_BUDGET_FACTOR`` times the stored one — for that cycle only.
DEFAULT_MAX_LLM_CALLS_PER_CYCLE = 15
SPARE_QUOTA_BUDGET_FACTOR = 3
SPARE_QUOTA_MIN_HEADROOM_PERCENT = 15.0


def budget_for_cycle(config, decision) -> int:
    """This cycle's LLM-call budget: the stored ``max_llm_calls_per_cycle``
    (15 when unset), times ``SPARE_QUOTA_BUDGET_FACTOR`` while ``decision``
    allows surplus spending with at least
    ``SPARE_QUOTA_MIN_HEADROOM_PERCENT`` headroom. Pure; never writes
    ``config``."""
    stored = getattr(config, "max_llm_calls_per_cycle", None) or DEFAULT_MAX_LLM_CALLS_PER_CYCLE
    headroom = getattr(decision, "headroom", None)
    if (decision is not None and getattr(decision, "allowed", False)
            and isinstance(headroom, (int, float)) and headroom >= SPARE_QUOTA_MIN_HEADROOM_PERCENT):
        return int(stored) * SPARE_QUOTA_BUDGET_FACTOR
    return int(stored)


def _surplus_decision_now():
    """The rate gate's surplus decision from its in-memory meter (no I/O),
    or None when it cannot be read — then the stored budget holds."""
    try:
        from services.llm_rate_gate import get_llm_rate_gate
        return get_llm_rate_gate().surplus_decision()
    except Exception:  # noqa: BLE001
        return None

# One cycle at a time (2026-09-25). The hourly loop, the surplus queue's
# ``consolidation_deep`` and the manual run each merge near-duplicates with
# ``update_memory`` + a hard ``delete_memory``; two cycles merging the same
# pair in opposite order could each delete the other's survivor.
_cycle_lock = asyncio.Lock()


async def _run_consolidation_cycle(**overrides) -> dict:
    """One consolidation cycle, after any cycle already running finishes
    (``_cycle_lock``). Every caller comes through here."""
    async with _cycle_lock:
        return await _consolidation_cycle(**overrides)


async def _consolidation_cycle(
    *,
    lookback_hours: int | None = None,
    max_llm_calls: int | None = None,
    max_narratives: int | None = None,
    priority: str = "background",
    purpose: str | None = None,
    effort: str | None = None,
    narrative_min_age_hours: float | None = None,
) -> dict:
    """Run a single consolidation cycle: cluster, connect, flag.

    With no arguments this is the hourly loop's cycle, governed by the
    stored config. The keyword arguments override the lookback, the LLM
    budget and the narrative count for one call (``run_cycle``); a surplus
    cycle whose first call the rate gate defers raises
    ``LLMRateLimitDeferred`` rather than record an empty cycle. Returns
    what the cycle produced (``skipped`` when there was too little to
    cluster)."""
    from services.llm_utils import build_background_llm, make_background_system_message

    start_time = time.monotonic()
    haiku_calls = 0
    connections_created = 0
    flags_created = 0
    contradictions_found = 0
    clusters_found = 0
    merges_performed = 0
    promotions = 0
    narratives_written = 0

    # 1. Read config — lookback + cost caps (a caller's overrides win for
    # this cycle only; the row is never written).
    async with async_session() as session:
        result = await session.execute(
            select(ConsolidationConfigModel).where(
                ConsolidationConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()
        stored_lookback = config.lookback_hours if config else 2
        max_cluster_size = getattr(config, "max_cluster_size", None) or 20
        max_pairs_per_cluster = getattr(config, "max_pairs_per_cluster", None) or 30
        max_llm_calls_per_cycle = getattr(config, "max_llm_calls_per_cycle", None) or DEFAULT_MAX_LLM_CALLS_PER_CYCLE
    lookback_hours = lookback_hours if lookback_hours is not None else stored_lookback
    if max_llm_calls is not None:
        max_llm_calls_per_cycle = max_llm_calls
    else:
        # Spare quota widens the budget for this cycle only (never stored).
        max_llm_calls_per_cycle = budget_for_cycle(config, _surplus_decision_now())
    narrative_cap = max_narratives if max_narratives is not None else DEFAULT_MAX_NARRATIVES

    # 2. Query memories created or last_accessed within lookback window,
    #    unioned with any ids marked "hot" by recent retrieval (Track B3).
    try:
        from services.dream.hot_memories import iter_active as _iter_hot
        hot_ids = _iter_hot()
    except Exception:
        hot_ids = []

    memories = await load_candidate_memories(lookback_hours, hot_ids)

    # 3. If < 3 memories, skip
    if len(memories) < 3:
        logger.info(
            "consolidation skipped — too few recent memories",
            extra={"subsystem": "consolidation", "recent_memories": len(memories)},
        )
        return {"skipped": True, "reason": f"only {len(memories)} recent memories", "memories_reviewed": len(memories),
                "llm_calls": 0}

    logger.info(
        "consolidation reviewing memories",
        extra={"subsystem": "consolidation", "memory_count": len(memories)},
    )

    # 4. Send to background LLM for entity grouping
    llm_kwargs = {"max_tokens": 4096}
    if priority != "background":
        llm_kwargs["priority"] = priority
    if effort:
        llm_kwargs["effort"] = effort
    llm = build_background_llm(**llm_kwargs)
    if purpose:
        from services.call_purpose import stamp
        stamp(llm, purpose)

    memory_list = []
    for m in memories:
        memory_list.append({
            "id": m.id,
            "content": m.content,
            "type": m.memory_type,
        })

    cluster_prompt = f"""Analyze these memories and group them into clusters by person, topic, or event.
Each memory should appear in at most one cluster. Only create clusters with 2+ memories.

Memories:
{json.dumps(memory_list, indent=2)}

Return ONLY valid JSON with this structure:
{{
  "clusters": [
    {{
      "label": "short description of what connects these",
      "type": "same_person | same_topic | same_event | related",
      "memory_ids": ["id1", "id2", ...]
    }}
  ]
}}"""

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        cluster_response = await llm.ainvoke([
            make_background_system_message(
                "Analyze memories and group them into clusters by person, topic, or event. Return ONLY valid JSON.",
            ),
            HumanMessage(content=cluster_prompt),
        ])
        haiku_calls += 1
        cluster_text = cluster_response.content
        # Extract JSON from response (handle markdown code blocks)
        if "```" in cluster_text:
            cluster_text = cluster_text.split("```")[1]
            if cluster_text.startswith("json"):
                cluster_text = cluster_text[4:]
            cluster_text = cluster_text.strip()
        cluster_data = json.loads(cluster_text)
    except Exception as e:
        from services.llm_rate_gate import LLMRateLimitDeferred
        if priority == "surplus" and isinstance(e, LLMRateLimitDeferred):
            raise  # a surplus cycle the gate turned away is not an empty cycle
        logger.warning("consolidation cluster parsing failed: %s", e)
        cluster_data = {"clusters": []}

    clusters = cluster_data.get("clusters", [])
    clusters_found = len(clusters)

    # Build a lookup of memory id -> content for contradiction checking
    memory_lookup = {m.id: m.content for m in memories}
    # Every contradiction the model reports this cycle; flagged together
    # after the cluster loop, once per memory pair (step 7).
    reported_contradictions: list = []

    # 5. Within each cluster, check for contradictions and create connections
    for cluster in clusters:
        # Cost cap: stop making LLM calls once we've spent the cycle's budget.
        if haiku_calls >= max_llm_calls_per_cycle:
            logger.info(
                "consolidation LLM budget exhausted, skipping remaining clusters",
                extra={
                    "subsystem": "consolidation",
                    "llm_calls": haiku_calls,
                    "max_llm_calls_per_cycle": max_llm_calls_per_cycle,
                },
            )
            break

        cluster_ids = cluster.get("memory_ids", [])
        connection_type = cluster.get("type", "related")

        # Filter to only IDs that actually exist in our memory set
        cluster_ids = [mid for mid in cluster_ids if mid in memory_lookup]

        if len(cluster_ids) < 2:
            continue

        # Cost cap: truncate oversized clusters so pair work stays bounded.
        if len(cluster_ids) > max_cluster_size:
            logger.info(
                "consolidation truncated oversized cluster",
                extra={
                    "subsystem": "consolidation",
                    "cluster_size": len(cluster_ids),
                    "max_cluster_size": max_cluster_size,
                },
            )
            cluster_ids = cluster_ids[:max_cluster_size]

        # 5a. Ask background LLM for contradictions between memories in this cluster
        cluster_memories = [
            {"id": mid, "content": memory_lookup[mid]}
            for mid in cluster_ids
        ]

        contradiction_prompt = f"""Review these related memories for contradictions (conflicting facts, outdated info, or inconsistencies).

Memories:
{json.dumps(cluster_memories, indent=2)}

Return ONLY valid JSON:
{{
  "contradictions": [
    {{
      "memory_id_a": "id of first memory",
      "memory_id_b": "id of second memory",
      "description": "brief description of the contradiction"
    }}
  ]
}}

If no contradictions found, return {{"contradictions": []}}"""

        try:
            from services.json_parsing import extract_json_payload
            contradiction_response = await llm.ainvoke([
                make_background_system_message(
                    "Identify contradictions between related memories. Return ONLY valid JSON.",
                ),
                HumanMessage(content=contradiction_prompt),
            ])
            haiku_calls += 1
            contradiction_data = extract_json_payload(
                contradiction_response.content,
                expected="object",
                subsystem="consolidation.contradictions",
            )
        except Exception as e:
            logger.warning("consolidation contradiction parsing failed: %s", e)
            contradiction_data = {"contradictions": []}

        # 6. Create memory_connections for related memories in same cluster.
        # Cost cap: stop creating pairs for this cluster once
        # ``max_pairs_per_cluster`` new connections are committed.
        pairs_this_cluster = 0
        async with async_session() as session:
            outer_break = False
            for i in range(len(cluster_ids)):
                if outer_break:
                    break
                for j in range(i + 1, len(cluster_ids)):
                    if pairs_this_cluster >= max_pairs_per_cluster:
                        outer_break = True
                        break
                    id_a = cluster_ids[i]
                    id_b = cluster_ids[j]

                    # Normalize order so we can check for duplicates consistently
                    if id_a > id_b:
                        id_a, id_b = id_b, id_a

                    # Check for existing connection to avoid duplicates
                    existing = await session.execute(
                        select(MemoryConnectionModel).where(
                            and_(
                                MemoryConnectionModel.memory_id_a == id_a,
                                MemoryConnectionModel.memory_id_b == id_b,
                            )
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    connection = MemoryConnectionModel(
                        id=str(uuid.uuid4()),
                        memory_id_a=id_a,
                        memory_id_b=id_b,
                        connection_type=connection_type,
                        strength=strength_for_connection_type(connection_type),
                    )
                    session.add(connection)
                    connections_created += 1
                    pairs_this_cluster += 1

            await session.commit()

        found_contradictions = contradiction_data.get("contradictions", [])
        contradictions_found += len(found_contradictions)
        reported_contradictions.extend(c for c in found_contradictions if isinstance(c, dict))

    # 7. Create memory_flags for the contradictions found — at most one open
    # flag per memory pair and a few per cycle, the ones most worth the
    # owner's attention first (see ``select_new_contradictions``).
    flags_created = await record_contradiction_flags(reported_contradictions, {m.id: m for m in memories})

    # 8. Merge duplicates within clusters. Never a pair with the owner's row
    # on either side (pre-deploy review and decision (a), 2026-09-27): the
    # merge keeps one id with the LLM's merged text and hard-deletes the
    # other, so the paraphrase conflict detection keeps beside a heard/owner
    # row either rewrote the owner's row here or had it deleted. Two of the
    # owner's own rows are not merged either — an unattended LLM step
    # rewrites one and deletes the other, and the pairs seen were mostly
    # separate episodes a merge would lose (the pinned owner_confirmed
    # instructions are heard/owner too). What the owner said changes only
    # with the owner's words, in their own turn — the rule
    # store_memories_with_conflict_detection applies.
    #
    # Nor a pair whose lifetimes differ (``same_lifetime``, integration
    # review, 2026-09-27): the survivor keeps its own ``temporal_nature``
    # and ``valid_until``, so a one-off preference with its 30-day horizon
    # merged with a lasting taste lapsed with it — the lasting row was
    # deleted — or turned Saturday's order into an open-ended "likes" row.
    from services.memory_service import (
        find_similar_memories, _llm_merge_content, _compute_tier,
        update_memory, delete_memory, _is_owners_row,
    )

    rows_by_id = {m.id: m for m in memories}
    merge_budget_exhausted = False
    for cluster in clusters:
        if merge_budget_exhausted:
            break
        cluster_ids = [mid for mid in cluster.get("memory_ids", []) if mid in memory_lookup]
        if len(cluster_ids) < 2:
            continue

        # For each memory in the cluster, find near-duplicates via embedding similarity
        merged_away = set()
        for mid in cluster_ids:
            if merge_budget_exhausted:
                break
            if mid in merged_away:
                continue
            content = memory_lookup.get(mid)
            if not content:
                continue
            if _is_owners_row(rows_by_id.get(mid)):
                continue

            try:
                similar = await find_similar_memories(content, threshold=0.78, limit=5)
            except Exception:
                continue

            for other_memory, sim in similar:
                if other_memory.id == mid or other_memory.id in merged_away:
                    continue
                if other_memory.id not in set(cluster_ids) or _is_owners_row(other_memory):
                    continue
                if not same_lifetime(rows_by_id.get(mid), other_memory):
                    continue

                if haiku_calls >= max_llm_calls_per_cycle:
                    logger.info(
                        "consolidation LLM budget exhausted during merge phase",
                        extra={
                            "subsystem": "consolidation",
                            "llm_calls": haiku_calls,
                            "max_llm_calls_per_cycle": max_llm_calls_per_cycle,
                        },
                    )
                    merge_budget_exhausted = True
                    break

                # Merge: combine content, sum reinforcement counts.
                # The call counts against the budget whether or not it
                # succeeds (2026-09-24): counting only successes let a
                # failing LLM be retried for every pair on every cycle.
                haiku_calls += 1
                try:
                    merged_content = await _llm_merge_content(content, other_memory.content)

                    # Both rows must still exist once the merge call returns
                    # (2026-09-25): a memory deleted meanwhile — a forget,
                    # another merge — is never folded back into the survivor.
                    from services.memory_service import get_memory_by_id
                    surviving = await get_memory_by_id(mid)
                    if not surviving or not await get_memory_by_id(other_memory.id):
                        continue

                    new_count = (surviving.reinforcement_count or 0) + (other_memory.reinforcement_count or 0) + 1
                    await update_memory(
                        mid,
                        content=merged_content,
                        reinforcement_count=new_count,
                        tier=_compute_tier(new_count),
                    )
                    await delete_memory(other_memory.id)
                    merged_away.add(other_memory.id)
                    memory_lookup[mid] = merged_content  # update for further comparisons
                    merges_performed += 1
                    logger.info(
                        "consolidation merged memory",
                        extra={
                            "subsystem": "consolidation",
                            "merged_from": other_memory.id,
                            "merged_into": mid,
                        },
                    )
                except Exception as e:
                    logger.warning("consolidation merge failed: %s", e)

    # 9. Resolve contradictions — retire loser (valid_until) + downgrade confidence
    await resolve_contradictions()

    # 10. Auto-promote: fix tier mismatches across all memories
    async with async_session() as session:
        result = await session.execute(select(MemoryModel))
        all_mems = list(result.scalars().all())

    for m in all_mems:
        # ``content`` so a dated episode stops at belief here too (2026-09-27):
        # without it this asked for knowledge on every such row each hour,
        # counted and logged a promotion, and ``update_memory`` wrote belief.
        expected_tier = _compute_tier(
            m.reinforcement_count or 0,
            used_count=getattr(m, "used_count", 0) or 0,
            content=m.content,
        )
        current_tier = m.tier or "observation"
        if _is_promotion(current_tier, expected_tier):
            try:
                await update_memory(m.id, tier=expected_tier)
                promotions += 1
                logger.info(
                    "consolidation promoted memory tier",
                    extra={
                        "subsystem": "consolidation",
                        "memory_id": m.id,
                        "from_tier": current_tier,
                        "to_tier": expected_tier,
                    },
                )
            except Exception as e:
                logger.warning("consolidation promotion failed: %s", e)

    # 11. Entity narrative synthesis — for entities with 5+ linked memories
    try:
        from services.database import EntityModel, EntityMentionModel
        from services.entity_service import get_entity_memories
        from services.document_service import Document, save_document, search_documents, update_document

        async with async_session() as session:
            # Find entities with 5+ mentions (a wider surplus cycle takes the
            # most-mentioned first; the hourly cycle is unchanged).
            query = select(EntityModel).where(EntityModel.mention_count >= 5)
            if max_narratives is not None:
                query = query.order_by(EntityModel.mention_count.desc())
            entity_result = await session.execute(query)
            rich_entities = list(entity_result.scalars().all())

        from services.memory_service import get_memory_by_id, _is_live

        live_texts_by_entity: dict = {}

        async def live_texts(ent) -> list:
            """The entity's live memories as narrative lines, read once a
            cycle: live rows only (2026-09-27). ``get_memory_by_id`` returns
            a retired row too, and the memory audit retired ~600, many
            sensitive — each cycle would have written them back into the
            narrative. The mentions are filtered before the limit, so the
            rewrite drops what an older narrative carried."""
            if ent.id not in live_texts_by_entity:
                mentions = await get_entity_memories(ent.id, limit=NARRATIVE_MAX_MEMORIES, live_only=True)
                texts = []
                for m in mentions:
                    mem = await get_memory_by_id(m["memory_id"])
                    if mem and _is_live(mem):
                        texts.append(f"- {mem.content}")
                live_texts_by_entity[ent.id] = texts
            return live_texts_by_entity[ent.id]

        attempted = 0
        for entity in rich_entities:
            if attempted >= narrative_cap:  # 3 per hourly cycle
                break
            if narrative_min_age_hours is not None:
                # A narrative rewritten within the window is left alone, so a
                # wide cycle every two hours does not rewrite the same ten.
                fresh = await _find_existing_narrative(entity.name)
                updated = getattr(fresh, "updated_at", None) if fresh else None
                if updated is not None and datetime.utcnow() - updated < timedelta(hours=narrative_min_age_hours):
                    continue
            try:
                memory_texts = await live_texts(entity)

                if len(memory_texts) < 5:
                    # Too few live rows for a narrative: one written from
                    # rows since retired would keep their text for good, so
                    # it goes (a later cycle writes a new one once five are
                    # live). No model call, so no narrative slot is used —
                    # counted, three such entities kept a live one from
                    # ever being written.
                    #
                    # The narrative is the name's (its title and tag), so
                    # it goes only when no entity of that name has five
                    # live rows (integration review, 2026-09-27): one pet's
                    # name was a person with 8 and a topic with 3, and the
                    # thin twin deleted the narrative the rich one had just
                    # written.
                    twins = [e for e in rich_entities if e.name == entity.name and e.id != entity.id]
                    if [t for t in twins if len(await live_texts(t)) >= 5]:
                        continue
                    dropped = await _drop_entity_narratives(entity.name)
                    if dropped:
                        logger.info(
                            "consolidation deleted an entity narrative with too few live memories",
                            extra={"subsystem": "consolidation", "entity": entity.name, "documents": dropped},
                        )
                    continue

                attempted += 1
                narrative_response = await llm.ainvoke([
                    make_background_system_message(
                        "Synthesize these memories about a person/topic into a concise narrative summary. "
                        "Capture key facts, relationships, and context. 3-5 paragraphs max."
                    ),
                    HumanMessage(content=f"Entity: {entity.name} ({entity.entity_type})\n\nMemories:\n" + "\n".join(memory_texts)),
                ])
                haiku_calls += 1
                narrative = narrative_response.content
                if isinstance(narrative, list):
                    narrative = " ".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in narrative
                    )

                # Check for existing narrative
                doc_tags = f"narrative,system_generated,entity:{entity.name}"
                existing_narrative = await _find_existing_narrative(entity.name)

                if existing_narrative:
                    await update_document(existing_narrative.id, content=narrative.strip())
                    logger.info("consolidation updated entity narrative", extra={"subsystem": "consolidation", "entity": entity.name})
                else:
                    await save_document(Document(
                        id=None,
                        title=f"Narrative: {entity.name}",
                        content=narrative.strip(),
                        tags=doc_tags,
                    ))
                    logger.info("consolidation created entity narrative", extra={"subsystem": "consolidation", "entity": entity.name})
                narratives_written += 1

            except Exception as e:
                logger.warning("consolidation narrative synthesis failed for %s: %s", entity.name, e)

    except Exception as e:
        logger.warning("consolidation entity narrative step failed: %s", e)

    # 12. Open loop cleanup — expire old loops with no nudges
    try:
        from services.open_loop_service import expire_old_loops
        expired = await expire_old_loops(days=30)
    except Exception as e:
        logger.warning("consolidation open loop cleanup failed: %s", e)

    # 13. Log cycle metrics
    duration_ms = int((time.monotonic() - start_time) * 1000)

    async with async_session() as session:
        cycle = ConsolidationCycleModel(
            id=str(uuid.uuid4()),
            memories_reviewed=len(memories),
            clusters_found=clusters_found,
            connections_created=connections_created,
            flags_created=flags_created,
            contradictions_found=contradictions_found,
            merges_performed=merges_performed,
            promotions=promotions,
            haiku_calls=haiku_calls,
            duration_ms=duration_ms,
        )
        session.add(cycle)
        await session.commit()

    logger.info(
        "consolidation cycle complete",
        extra={
            "subsystem": "consolidation",
            "memory_count": len(memories),
            "clusters_found": clusters_found,
            "connections_created": connections_created,
            "contradictions_found": contradictions_found,
            "merges_performed": merges_performed,
            "promotions": promotions,
            "llm_calls": haiku_calls,
            "duration_ms": duration_ms,
        },
    )

    # 9. Cleanup old enrichments (older than 24h)
    await cleanup_old_enrichments()

    return {
        "skipped": False,
        "memories_reviewed": len(memories),
        "clusters_found": clusters_found,
        "connections_created": connections_created,
        "flags_created": flags_created,
        "contradictions_found": contradictions_found,
        "merges_performed": merges_performed,
        "promotions": promotions,
        "narratives_written": narratives_written,
        "llm_calls": haiku_calls,
        "duration_ms": duration_ms,
    }


def same_lifetime(mem_a, mem_b) -> bool:
    """Whether two rows last alike: the same ``temporal_nature`` and a
    horizon (``valid_until``) on both or neither. A merge keeps one row's
    lifetime for both, so only such a pair may merge. Pure."""
    if mem_a is None or mem_b is None:
        return False
    nature = [getattr(m, "temporal_nature", None) or "timeless" for m in (mem_a, mem_b)]
    horizon = [getattr(m, "valid_until", None) is not None for m in (mem_a, mem_b)]
    return nature[0] == nature[1] and horizon[0] == horizon[1]


_CONTRADICTION_SOURCE_RANK = {
    "user_stated": 4,
    "tool_derived": 3,
    "inferred": 2,
    "imported": 1,
}


def contradiction_pair(id_a: str, id_b: str) -> tuple:
    """The unordered memory pair a contradiction flag is about. Pure."""
    return (id_a, id_b) if id_a <= id_b else (id_b, id_a)


# At most this many new contradiction flags per cycle (2026-09-27). Every
# flag can end as a question for the owner, and the nightly triage asks at
# most one (``contradiction_triage_service.triage_unresolved``'s
# ``max_asks``; the surplus queue asked three);
# flags were created at 48–80 a day against 3–11 resolved. Once repeats are
# dropped, new pairs arrived at about 60 in nine days — under one per
# hourly cycle — so five never binds on an ordinary cycle and bounds a bad
# one (a model that calls a whole cluster contradictory). The pairs left
# out are not lost: the model reports them again on a later cycle.
MAX_NEW_CONTRADICTION_FLAGS_PER_CYCLE = 5


def _owner_stated(memory, conversation_sources: Optional[dict] = None) -> bool:
    """The owner's own word, by the decay pass's rule (``memory_decay_service.
    owners_own_word``): confirmed by them, or heard in their own chat.

    It was any heard + user_stated row (2026-09-27 review), which counts
    the reminder-turn echoes a memory audit showed are not the owner's: the
    top 11 open pairs in one store took the owner's first place through two
    blanket "in-app only" rules, while the decay pass, asking for the
    owner's own chat, would retire the same rows.
    ``conversation_sources`` maps a conversation id to its source."""
    from services.memory_decay_service import owners_own_word

    conversation = getattr(memory, "source_conversation_id", None)
    return owners_own_word(memory, conversation_source=(conversation_sources or {}).get(conversation))


async def conversation_sources_for(memories) -> dict:
    """Conversation id -> ``conversations.source`` for the rows' own
    conversations; one read. A deleted conversation is simply missing."""
    ids = {getattr(m, "source_conversation_id", None) for m in memories if m is not None} - {None}
    if not ids:
        return {}
    async with async_session() as session:
        rows = (await session.execute(
            select(ConversationModel.id, ConversationModel.source).where(ConversationModel.id.in_(ids))
        )).all()
    return {cid: source for cid, source in rows}


def contradiction_priority(mem_a, mem_b, conversation_sources: Optional[dict] = None) -> tuple:
    """How much a contradiction deserves the owner's attention, as a sort key
    (higher first). Pure.

    A conflict with something the owner said themselves comes first,
    whatever the other pairs weigh: one of their own statements may be
    wrong. Among pairs
    alike in that, the weightier side's importance (0–1) plus reach —
    ``used_count`` says how many turns each memory was placed in front of
    the model, so a conflict between rows used hundreds of times colours
    hundreds of answers (+0.1 per e-fold).

    The owner's word was +1 on one sum until the 2026-09-27 review: a pair
    of Edward's own rows at importance 1.0 with 2,137 uses scored 1.77, a
    conflict with the owner's word at 0.5 and no uses 1.50, and the busy pair
    took the one question on Today."""
    rows = [m for m in (mem_a, mem_b) if m is not None]
    owner = 1 if any(_owner_stated(m, conversation_sources) for m in rows) else 0
    importance = max((float(getattr(m, "importance", None) or 0.0) for m in rows), default=0.0)
    used = sum(int(getattr(m, "used_count", None) or 0) for m in rows)
    return (owner, importance + 0.1 * math.log1p(used))


def select_new_contradictions(reported, memories_by_id, open_pairs, cap: int | None = None,
                              conversation_sources: Optional[dict] = None) -> list:
    """The contradictions this cycle may flag, as ``(memory_id_a,
    memory_id_b, description)``, highest ``contradiction_priority`` first.
    Pure.

    One open flag per memory pair (2026-09-27): 348
    unresolved flags covered only 60 pairs — one was flagged 55 times —
    because every hourly cycle re-flagged whatever the model reported and
    nothing looked for the flag already open. A pair already open (in
    either order), a pair reported twice in one cycle, a memory paired with
    itself and an id the cycle did not load are all dropped; past ``cap``
    the lowest-priority pairs wait for a later cycle."""
    seen = set(open_pairs)
    out = []
    for c in reported:
        a, b = c.get("memory_id_a"), c.get("memory_id_b")
        if not a or not b or a == b or a not in memories_by_id or b not in memories_by_id:
            continue
        pair = contradiction_pair(a, b)
        if pair in seen:
            continue
        seen.add(pair)
        out.append((a, b, c.get("description") or "Contradiction detected"))
    out.sort(key=lambda p: contradiction_priority(memories_by_id[p[0]], memories_by_id[p[1]],
                                                  conversation_sources), reverse=True)
    return out if cap is None else out[:max(0, cap)]


# A pair the owner was asked about and left unanswered is not flagged again for
# this long. The daily triage asks a pair once and closes it as stale
# after a week (``contradiction_triage_service.STALE_AFTER_DAYS``); flagged
# again by the next hourly cycle it would be asked again a week later, and
# every week after — the nagging the ask-once rule (2026-09-07) stopped.
REASK_AFTER_DAYS = 30


async def open_contradiction_pairs() -> set:
    """Every memory pair an unresolved contradiction flag names, and every
    pair the owner was asked about in the last ``REASK_AFTER_DAYS`` whose flag
    closed unanswered (both memories still live — an answer retires one),
    or that Edward kept both of in that time (``KEPT_BOTH_MARK``: both
    stay live by design, and re-flagged every hour the pair would take one
    of the cycle's five slots each time)."""
    since = datetime.utcnow() - timedelta(days=REASK_AFTER_DAYS)
    async with async_session() as session:
        rows = (await session.execute(
            select(MemoryFlagModel.memory_id, MemoryFlagModel.related_memory_id).where(
                MemoryFlagModel.flag_type == "contradiction",
                MemoryFlagModel.related_memory_id.isnot(None),
                or_(
                    MemoryFlagModel.resolved == False,  # noqa: E712
                    and_(
                        MemoryFlagModel.resolved_at >= since,
                        or_(
                            MemoryFlagModel.description.like("%[asked %"),
                            MemoryFlagModel.description.like(f"%{KEPT_BOTH_MARK}%"),
                        ),
                    ),
                ),
            )
        )).all()
    return {contradiction_pair(a, b) for a, b in rows}


async def record_contradiction_flags(reported, memories_by_id,
                                     cap: int = MAX_NEW_CONTRADICTION_FLAGS_PER_CYCLE) -> int:
    """Write the flags ``select_new_contradictions`` allows; returns how
    many were written."""
    named = {i for c in reported if isinstance(c, dict)
             for i in (c.get("memory_id_a"), c.get("memory_id_b")) if i in memories_by_id}
    new = select_new_contradictions(reported, memories_by_id, await open_contradiction_pairs(), cap=cap,
                                    conversation_sources=await conversation_sources_for(
                                        memories_by_id[i] for i in named))
    if not new:
        return 0
    async with async_session() as session:
        for a, b, desc in new:
            session.add(MemoryFlagModel(
                id=str(uuid.uuid4()),
                memory_id=a,
                flag_type="contradiction",
                description=desc,
                related_memory_id=b,
            ))
        await session.commit()
    return len(new)


# Connection strength by type. Every edge in the live graph was written with
# a flat 0.5, which made the weight dimension carry no information at all:
# retrieval spends it on ``score += 0.1 * strength``, so a constant strength
# is a constant boost.
#
# The values track information content, which is roughly inverse to how often
# each type fires. ``same_person`` accounted for 11,672 of 21,326 edges — it
# matches whenever two memories mention the same frequent contact, which for
# someone the owner texts daily links nearly everything to everything. ``related``
# fired 865 times and is the consolidation LLM saying two memories connect
# when no cheaper category fit. Those are not equal evidence.
CONNECTION_TYPE_STRENGTH: dict[str, float] = {
    "same_person": 0.25,
    "same_topic": 0.45,
    "same_event": 0.70,
    "related": 0.85,
}

# Spreading activation pulls in at most 5 memories per query, and the live
# graph has a max degree of 300. Links beyond a handful of the strongest can
# never be used and only add noise to every query the memory takes part in.
MAX_CONNECTIONS_PER_MEMORY = 8


def strength_for_connection_type(connection_type: str | None) -> float:
    """Strength for a connection type; unknown types are weakest evidence."""
    if connection_type in CONNECTION_TYPE_STRENGTH:
        return CONNECTION_TYPE_STRENGTH[connection_type]
    return min(CONNECTION_TYPE_STRENGTH.values())


def select_surplus_connections(edges: list[dict]) -> list[dict]:
    """Return the edges to drop so no memory exceeds the degree cap.

    A connection is undirected: it occupies a slot on both endpoints, so
    degree is counted from both columns. Edges are considered strongest
    first, and one is kept only when BOTH of its memories still have room —
    which is what stops a single hub from consuming every neighbour's budget.

    Unlike memories, a surplus connection has nothing to preserve: it carries
    no independent content, and consolidation rebuilds the graph each cycle.
    """
    ordered = sorted(
        edges, key=lambda e: (-(e.get("strength") or 0.0), str(e.get("id")))
    )

    degree: dict[str, int] = {}
    surplus: list[dict] = []

    for edge in ordered:
        a, b = edge["memory_id_a"], edge["memory_id_b"]
        if (
            degree.get(a, 0) >= MAX_CONNECTIONS_PER_MEMORY
            or degree.get(b, 0) >= MAX_CONNECTIONS_PER_MEMORY
        ):
            surplus.append(edge)
            continue
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    return surplus


_TIER_RANK = {"observation": 0, "belief": 1, "knowledge": 2}


def _is_promotion(current_tier: str | None, expected_tier: str | None) -> bool:
    """True only when the tier change moves UP the ladder.

    The tier pass promotes; it does not demote. A memory that reached a tier
    earned it under the evidence available at the time, and P3 changed what
    counts as evidence — every knowledge-tier row in the live store predates
    ``used_count`` and would otherwise be downgraded en masse on the next
    cycle.

    Disuse is not meant to be handled here anyway. That is decay's job, and
    decay protects knowledge from auto-retirement on purpose.
    """
    current = _TIER_RANK.get(current_tier or "observation", 0)
    expected = _TIER_RANK.get(expected_tier or "observation", 0)
    return expected > current


# The marker on a flag left open for the owner to decide. Written and
# matched only through this constant (rendered once at import).
NEEDS_OWNER_MARK = owner_text("[needs {OWNER}]")
# A flag Edward closed with both rows kept as they are (decision (b),
# 2026-09-27): no side was the owner's own word, so there was nothing to ask
# them and nothing a label could settle.
KEPT_BOTH_MARK = "[kept both]"
_NEEDS_OWNER_RE = re.compile(r"\s*" + re.escape(NEEDS_OWNER_MARK) + r"(?: \([^)]*\))?")


def kept_both_description(description: str | None, reason: str) -> str:
    """The flag's description once Edward keeps both rows: an earlier
    cycle's ``NEEDS_OWNER_MARK`` goes (nothing is left for the owner), the
    ``[kept both]`` marker is added once. Pure."""
    base = _NEEDS_OWNER_RE.sub("", description or "").rstrip()
    if KEPT_BOTH_MARK in base:
        return base
    return f"{base} {KEPT_BOTH_MARK} ({reason})".strip()


def memory_lapsed(memory, now: datetime) -> bool:
    """Retired or superseded: its ``valid_until`` has passed (``now`` naive
    UTC). Pure."""
    until = getattr(memory, "valid_until", None)
    if until is None:
        return False
    if until.tzinfo is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None)
    return until <= now


def moot_reason(mem_a, mem_b, now: datetime | None = None) -> str | None:
    """Why a contradiction flag no longer needs deciding — a side is gone
    or has lapsed — or None. ``now`` is naive UTC, like the column. Pure."""
    now = now or datetime.utcnow()
    if mem_a is None or mem_b is None:
        return "one side missing"
    if memory_lapsed(mem_a, now) or memory_lapsed(mem_b, now):
        return "one side lapsed"
    return None


# A memory audit's moves off ``heard`` (2026-09-27): ~190 rows relabelled
# heard -> read/thought (and user_stated -> inferred), one instruction
# demoted, because what they record was not the owner speaking. Most of them
# carry nothing else — ``{"audit": {"op": "relabel", "was": "heard"}}`` — so
# ``is_owners_word`` found no profile, took the chat default and read the row
# as the owner's. A reword, a retype or an ``about`` relabel says nothing about
# who spoke; such a row is read like any row with no provenance.
_AUDIT_OFF_HEARD_OPS = frozenset({"relabel", "demote"})


def audit_moved_off_heard(source_ref) -> bool:
    """Whether the memory audit moved this row off ``heard`` (its ``source_ref``
    JSON). Pure."""
    from services.provenance_service import parse_source_ref

    audit = parse_source_ref(source_ref).get("audit")
    if not isinstance(audit, dict) or audit.get("op") not in _AUDIT_OFF_HEARD_OPS:
        return False
    return "heard" in str(audit.get("was") or "").split("/")


def rank_says_who(memory, conversation_sources: Optional[dict] = None) -> bool:
    """Whether this row's ``source_type`` can be trusted to rank it against
    another: it was written in a turn in which the owner speaks, or before
    provenance was recorded (``memory_service.is_owners_word`` on its own
    ``source_ref``). Pure.

    Not a row from any other turn (pre-deploy review, 2026-09-27):
    extraction under the mind and scheduled_event profiles turns every
    user_stated label into inferred, and an instruction from any turn that
    is not the owner's own goes the same way, so the label records the turn,
    not who said it. A wake that read the owner's newer text lost by rank
    to the older row it was kept beside — or to one of the ~1,100 heartbeat
    reads still labelled user_stated — and was retired with no question to
    the owner.

    Nor a row whose thread is one where the owner is not the speaker — a
    heartbeat, a journal entry or wake, a scheduled event, a worker
    (``mind_service.NOT_OWNERS_CONVERSATION_SOURCES``; ``conversation_sources``
    maps a conversation id to its source) — or one the audit moved off
    ``heard`` (``audit_moved_off_heard``). Integration review, 2026-09-27:
    their refs are empty or the audit's alone, both read as the chat
    default, and rank retired five of the 25 open pairs on the first cycle
    — a pet-care rule among them."""
    from services.memory_service import OWNER_SPEAKS_SOURCES, is_owners_word
    from services.mind_service import (OWNER_SPEAKS_THREAD_SOURCES, EDWARD_OWN_THREAD_IDS,
                                       NOT_OWNERS_CONVERSATION_SOURCES)
    from services.provenance_service import parse_source_ref

    source_ref = getattr(memory, "source_ref", None)
    if audit_moved_off_heard(source_ref):
        return False
    conversation = getattr(memory, "source_conversation_id", None)
    source = (conversation_sources or {}).get(conversation)
    if source in NOT_OWNERS_CONVERSATION_SOURCES:
        # Except the owner replying inside a journal entry: the ref names this
        # very thread and a turn in which they spoke (``is_owners_instruction``'s rule).
        ref = parse_source_ref(source_ref)
        journal_reply = (source in OWNER_SPEAKS_THREAD_SOURCES and conversation not in EDWARD_OWN_THREAD_IDS
                         and ref.get("conversation_id") == conversation
                         and ref.get("turn_source") in OWNER_SPEAKS_SOURCES)
        if not journal_reply:
            return False
    return is_owners_word(source_ref)


def _pinned(memory) -> bool:
    """A rule the owner confirmed (``source_ref`` kind ``owner_confirmed``). Pure."""
    from services.memory_decay_service import OWNER_CONFIRMED_KIND
    from services.provenance_service import parse_source_ref

    return parse_source_ref(getattr(memory, "source_ref", None)).get("kind") == OWNER_CONFIRMED_KIND


def _retirable(memory) -> bool:
    """Whether a verdict by label may retire this row: not what the decay
    pass holds as sensitive (pets and their meds, health, family, money,
    distress — the owner's line-item call), and not knowledge, which decay never
    retires either. Pure."""
    from services.memory_decay_service import _held_as_sensitive

    return not _held_as_sensitive(memory) and getattr(memory, "tier", None) != "knowledge"


def _not_edwards_to_decide(mem_a, mem_b, conversation_sources, why: str) -> tuple:
    """The owner's call when a side is their own word, else both kept
    (decision (b))."""
    if any(_owner_stated(m, conversation_sources) for m in (mem_a, mem_b)):
        return ("open", why)
    return ("keep_both", why)


# Why a verdict went the way it did: written into the flag's description
# (after ``NEEDS_OWNER_MARK`` or ``KEPT_BOTH_MARK``), which the Dreams panel
# shows and the triage model reads. Rendered once at import.
_REASON_PINNED = owner_text("a side is a rule {OWNER} confirmed")
_REASON_OWNER_VS_UNSPOKEN = owner_text("{OWNER}'s own word against a row written while they were not speaking")
_REASON_UNSPOKEN = owner_text("a side was written while {OWNER} was not speaking")
_REASON_NEITHER_OWNERS = owner_text("both user-stated, neither {OWNER}'s own word")


def decide_contradiction(mem_a, mem_b, now: datetime | None = None,
                         conversation_sources: Optional[dict] = None):
    """Who wins a contradiction, whether it is the owner's call, or whether
    Edward keeps both.

    Returns ``("auto", keep_id, retire_id)``, ``("open", reason)``,
    ``("keep_both", reason)`` or ``("moot", reason)``.
    Until 2026-09-05 every flag was auto-resolved: by source rank, then by
    reinforcement count, and a missing side was simply marked resolved. The
    owner never saw one, and a pet-medication contradiction was settled by
    rank. Then a tie between two user-stated memories stayed open, marked
    for the owner in the Dreams panel; since decision (b) (2026-09-27) only
    a tie with a side that is the owner's own word does, and Edward keeps
    both rows of any other — 42 of the 47 pairs then waiting for the owner
    were such ties, none their own words (the label was stamped on
    heartbeat reads and reminder echoes). Keeping both retires nothing.

    That alone did not stop the pet-medication mistake (integration
    review, 2026-09-27): replayed on the 25 open flags, rank retired a
    pet-medication rule an audit had just corrected (knowledge tier) in
    favour of a week-old episode, because both sides passed
    ``rank_says_who``. Now a verdict by label — rank or reinforcement —
    never retires a row that is sensitive or knowledge (``_retirable``):
    the pair is the owner's call when a side is their own word, else both
    are kept. And a rule the owner confirmed (``owner_confirmed``, an
    audit's pins) is settled: kept as it is against any row, never retired,
    never asked about again — the owner already answered it.

    A side that is gone or has lapsed (``valid_until`` passed) makes the
    flag moot (2026-09-27): time already answered it and the lapsed side is
    out of retrieval. Such flags used to stay open "for the owner" — 24 of
    them on 2026-09-27 — holding the daily triage's slots and
    turning into "Which is right?" notes about rules that no longer apply.
    ``now`` is naive UTC, like the column.

    Rank decides only between rows whose label says who spoke
    (``rank_says_who``, 2026-09-27). When a side was written in a turn
    where the owner was not speaking, the flag is the owner's call only if
    a side is their own word (``_owner_stated``; ``conversation_sources``
    maps a conversation id to its source); otherwise Edward keeps both rows
    as they are (decision (b), 2026-09-27). Asking the owner about all of
    them left the queue that never drains: of 106 new pairs in ten days, 64
    had such a side and none was the owner's own word — most were Edward's
    own did/self and
    thought/self logs. Keeping both retires, rewrites and deletes nothing.
    """
    moot = moot_reason(mem_a, mem_b, now)
    if moot:
        return ("moot", moot)
    if _pinned(mem_a) or _pinned(mem_b):
        return ("keep_both", _REASON_PINNED)
    if not (rank_says_who(mem_a, conversation_sources) and rank_says_who(mem_b, conversation_sources)):
        if any(_owner_stated(m, conversation_sources) for m in (mem_a, mem_b)):
            return ("open", _REASON_OWNER_VS_UNSPOKEN)
        return ("keep_both", _REASON_UNSPOKEN)
    rank_a = _CONTRADICTION_SOURCE_RANK.get(mem_a.source_type, 2)
    rank_b = _CONTRADICTION_SOURCE_RANK.get(mem_b.source_type, 2)
    if rank_a != rank_b:
        keep, retire = (mem_a, mem_b) if rank_a > rank_b else (mem_b, mem_a)
    elif rank_a >= _CONTRADICTION_SOURCE_RANK["user_stated"]:
        if any(_owner_stated(m, conversation_sources) for m in (mem_a, mem_b)):
            return ("open", "both user-stated")
        return ("keep_both", _REASON_NEITHER_OWNERS)
    else:
        keep, retire = (
            (mem_a, mem_b)
            if (mem_a.reinforcement_count or 0) >= (mem_b.reinforcement_count or 0)
            else (mem_b, mem_a)
        )
    if not _retirable(retire):
        return _not_edwards_to_decide(mem_a, mem_b, conversation_sources,
                                      "the losing side is sensitive or knowledge; a label never retires it")
    return ("auto", keep.id, retire.id)


async def retire_contradiction_loser(retire_id: str):
    """Retire the losing memory: ``valid_until=now`` so retrieval's temporal
    filter drops it, and confidence 0.2 as a belt-and-suspenders signal for
    any path that ignores ``valid_until``. Returns the stamp."""
    from services.memory_service import update_memory
    from services.database import MemoryModel
    from sqlalchemy import update as sql_update
    from datetime import datetime, timezone

    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    await update_memory(retire_id, confidence=0.2)
    async with async_session() as session:
        await session.execute(
            sql_update(MemoryModel).where(MemoryModel.id == retire_id).values(valid_until=now_utc_naive)
        )
        await session.commit()
    return now_utc_naive


async def mark_flag_resolved(flag_id: str) -> None:
    from services.database import MemoryFlagModel
    from datetime import datetime

    async with async_session() as session:
        f = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id))).scalar_one_or_none()
        if f:
            f.resolved = True
            f.resolved_at = datetime.utcnow()
            await session.commit()


async def _mark_flag_for_owner(flag_id: str, reason: str) -> None:
    """Append the marker once; a second cycle must not stack it."""
    from services.database import MemoryFlagModel

    async with async_session() as session:
        f = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id))).scalar_one_or_none()
        if f and NEEDS_OWNER_MARK not in (f.description or ""):
            f.description = f"{f.description or ''} {NEEDS_OWNER_MARK} ({reason})".strip()
            await session.commit()


async def mark_flag_kept_both(flag_id: str, reason: str) -> None:
    """Close a contradiction flag with both memories kept as they are
    (``kept_both_description``); no memory is touched."""
    from services.database import MemoryFlagModel

    async with async_session() as session:
        f = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id))).scalar_one_or_none()
        if f:
            f.description = kept_both_description(f.description, reason)
            f.resolved = True
            f.resolved_at = datetime.utcnow()
            await session.commit()


async def resolve_contradictions() -> int:
    """Resolve unresolved contradiction flags where the answer is clear
    (see ``decide_contradiction``): retire the loser, close a moot flag, or
    keep both and close it — the last two without touching either memory.
    Mark the rest for the owner and leave them open. Returns the number of flags
    resolved."""
    from services.memory_service import get_memory_by_id
    from services.database import MemoryFlagModel

    async with async_session() as session:
        unresolved = await session.execute(
            select(MemoryFlagModel).where(
                and_(
                    MemoryFlagModel.flag_type == "contradiction",
                    MemoryFlagModel.resolved == False,
                )
            )
        )
        unresolved_flags = list(unresolved.scalars().all())

    loaded = []
    for flag in unresolved_flags:
        try:
            mem_a = await get_memory_by_id(flag.memory_id)
            mem_b = await get_memory_by_id(flag.related_memory_id) if flag.related_memory_id else None
            loaded.append((flag, mem_a, mem_b))
        except Exception as e:
            logger.warning("consolidation contradiction resolution failed: %s", e)
    # Whose conversation each row came from, read once for every flag.
    # Unread, the owner's own chat would look like no one's and be kept, not asked.
    try:
        sources = await conversation_sources_for(m for _, a, b in loaded for m in (a, b) if m is not None)
    except Exception as e:
        logger.warning("consolidation contradiction resolution skipped: %s", e)
        return 0

    resolved_count = 0
    for flag, _, _ in loaded:
        try:
            # Re-read both rows now: an earlier flag in this pass may have
            # retired one, and deciding on the rows read above retired both
            # sides of a pair flagged twice in swapped order (review,
            # 2026-09-27). The sources map stays valid — a row's
            # conversation does not change during the pass.
            mem_a = await get_memory_by_id(flag.memory_id)
            mem_b = await get_memory_by_id(flag.related_memory_id) if flag.related_memory_id else None
            verdict = decide_contradiction(mem_a, mem_b, conversation_sources=sources)
            if verdict[0] == "open":
                await _mark_flag_for_owner(flag.id, verdict[1])
                continue
            if verdict[0] == "keep_both":
                await mark_flag_kept_both(flag.id, verdict[1])
                resolved_count += 1
                logger.info(
                    "consolidation kept both sides of a contradiction",
                    extra={"subsystem": "consolidation", "flag_id": flag.id, "reason": verdict[1]},
                )
                continue
            if verdict[0] == "moot":
                await mark_flag_resolved(flag.id)
                resolved_count += 1
                logger.info(
                    "consolidation closed a moot contradiction flag",
                    extra={"subsystem": "consolidation", "flag_id": flag.id, "reason": verdict[1]},
                )
                continue

            _, keep_id, retire_id = verdict
            stamp = await retire_contradiction_loser(retire_id)
            await mark_flag_resolved(flag.id)
            resolved_count += 1
            logger.info(
                "consolidation retired memory (contradiction resolved)",
                extra={
                    "subsystem": "consolidation",
                    "kept": keep_id,
                    "retired": retire_id,
                    "valid_until": stamp.isoformat(),
                },
            )
        except Exception as e:
            logger.warning("consolidation contradiction resolution failed: %s", e)

    return resolved_count


async def get_connected_memory_ids(memory_id: str) -> List[Tuple[str, float]]:
    """Query memory_connections where memory_id_a or memory_id_b matches.

    Returns a list of (other_memory_id, strength) tuples.
    """
    async with async_session() as session:
        result = await session.execute(
            select(MemoryConnectionModel).where(
                or_(
                    MemoryConnectionModel.memory_id_a == memory_id,
                    MemoryConnectionModel.memory_id_b == memory_id,
                )
            )
        )
        connections = list(result.scalars().all())

    pairs = []
    for conn in connections:
        other_id = conn.memory_id_b if conn.memory_id_a == memory_id else conn.memory_id_a
        pairs.append((other_id, conn.strength))

    return pairs


async def get_memory_flags(memory_id: str) -> List[dict]:
    """Return unresolved flags for a given memory as dicts."""
    async with async_session() as session:
        result = await session.execute(
            select(MemoryFlagModel).where(
                and_(
                    MemoryFlagModel.memory_id == memory_id,
                    MemoryFlagModel.resolved == False,
                )
            )
        )
        flags = list(result.scalars().all())

    return [
        {
            "id": f.id,
            "flag_type": f.flag_type,
            "description": f.description,
            "related_memory_id": f.related_memory_id,
        }
        for f in flags
    ]


async def get_connected_memory_ids_batch(
    memory_ids: list[str],
) -> dict[str, list[Tuple[str, float]]]:
    """Batched version of ``get_connected_memory_ids`` — one DB round-trip for N ids.

    Returns a dict keyed by every input memory_id. Ids with no connections
    map to an empty list.
    """
    if not memory_ids:
        return {}

    id_set = set(memory_ids)
    result: dict[str, list[Tuple[str, float]]] = {mid: [] for mid in id_set}

    async with async_session() as session:
        rows = await session.execute(
            select(MemoryConnectionModel).where(
                or_(
                    MemoryConnectionModel.memory_id_a.in_(id_set),
                    MemoryConnectionModel.memory_id_b.in_(id_set),
                )
            )
        )
        connections = list(rows.scalars().all())

    for conn in connections:
        a, b, strength = conn.memory_id_a, conn.memory_id_b, conn.strength
        if a in id_set:
            result[a].append((b, strength))
        if b in id_set:
            result[b].append((a, strength))

    return result


async def get_memory_flags_batch(memory_ids: list[str]) -> dict[str, list[dict]]:
    """Batched version of ``get_memory_flags`` — one DB round-trip for N ids."""
    if not memory_ids:
        return {}

    id_set = set(memory_ids)
    result: dict[str, list[dict]] = {mid: [] for mid in id_set}

    async with async_session() as session:
        rows = await session.execute(
            select(MemoryFlagModel).where(
                and_(
                    MemoryFlagModel.memory_id.in_(id_set),
                    MemoryFlagModel.resolved == False,
                )
            )
        )
        flags = list(rows.scalars().all())

    for f in flags:
        result[f.memory_id].append({
            "id": f.id,
            "flag_type": f.flag_type,
            "description": f.description,
            "related_memory_id": f.related_memory_id,
        })

    return result


async def get_consolidation_status() -> dict:
    """Return running state, config, cycle count, total connections/flags."""
    global _consolidation_task

    async with async_session() as session:
        # Config
        result = await session.execute(
            select(ConsolidationConfigModel).where(
                ConsolidationConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()

        # Cycle count
        cycle_count_result = await session.execute(
            select(func.count()).select_from(ConsolidationCycleModel)
        )
        cycle_count = cycle_count_result.scalar() or 0

        # Last run
        last_cycle_result = await session.execute(
            select(ConsolidationCycleModel)
            .order_by(ConsolidationCycleModel.created_at.desc())
            .limit(1)
        )
        last_cycle = last_cycle_result.scalar_one_or_none()

        # Total connections
        conn_count_result = await session.execute(
            select(func.count()).select_from(MemoryConnectionModel)
        )
        total_connections = conn_count_result.scalar() or 0

        # Total flags
        flag_count_result = await session.execute(
            select(func.count()).select_from(MemoryFlagModel)
        )
        total_flags = flag_count_result.scalar() or 0

    running = _consolidation_task is not None and not _consolidation_task.done()
    enabled = config.enabled if config else False
    interval_seconds = config.interval_seconds if config else 3600
    lookback_hours = config.lookback_hours if config else 2
    max_cluster_size = getattr(config, "max_cluster_size", None) or 20
    max_pairs_per_cluster = getattr(config, "max_pairs_per_cluster", None) or 30
    max_llm_calls_per_cycle = getattr(config, "max_llm_calls_per_cycle", None) or 15

    last_run = None
    next_run = None
    if last_cycle and last_cycle.created_at:
        last_run = last_cycle.created_at.isoformat() + "Z"
        if enabled:
            next_run_dt = last_cycle.created_at + timedelta(seconds=interval_seconds)
            next_run = next_run_dt.isoformat() + "Z"

    return {
        "running": running,
        "enabled": enabled,
        "interval_seconds": interval_seconds,
        "lookback_hours": lookback_hours,
        "last_run": last_run,
        "next_run": next_run,
        "cycle_count": cycle_count,
        "total_connections": total_connections,
        "total_flags": total_flags,
        "max_cluster_size": max_cluster_size,
        "max_pairs_per_cluster": max_pairs_per_cluster,
        "max_llm_calls_per_cycle": max_llm_calls_per_cycle,
    }


async def cleanup_old_enrichments() -> None:
    """Delete memory enrichments older than 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)

    async with async_session() as session:
        await session.execute(
            delete(MemoryEnrichmentModel).where(
                MemoryEnrichmentModel.created_at < cutoff
            )
        )
        await session.commit()

    logger.info("consolidation cleaned up old enrichments (>24h)", extra={"subsystem": "consolidation"})
