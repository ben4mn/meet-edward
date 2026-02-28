"""
Memory consolidation service for Edward.

Runs an in-process asyncio loop that periodically reviews recent memories,
clusters them by entity/topic, discovers connections, and flags contradictions.
Uses Claude Haiku for lightweight LLM analysis.
"""

import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import List, Tuple

from sqlalchemy import select, func, or_, and_, delete

from services.database import (
    async_session,
    MemoryModel,
    MemoryConnectionModel,
    MemoryFlagModel,
    ConsolidationCycleModel,
    ConsolidationConfigModel,
    MemoryEnrichmentModel,
)

_consolidation_task: asyncio.Task | None = None


async def start_consolidation() -> None:
    """Start the consolidation polling loop."""
    global _consolidation_task
    if _consolidation_task is not None:
        return
    _consolidation_task = asyncio.create_task(_consolidation_loop())
    print("Consolidation service started")


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
    print("Consolidation service stopped")


async def _consolidation_loop() -> None:
    """Main polling loop — runs forever until cancelled."""
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
                    print(f"Consolidation cycle error: {e}")
                    traceback.print_exc()

            interval = config.interval_seconds if config else 3600
        except Exception as e:
            print(f"Consolidation loop error: {e}")
            interval = 3600

        await asyncio.sleep(interval)


async def _run_consolidation_cycle() -> None:
    """Run a single consolidation cycle: cluster, connect, flag."""
    from langchain_anthropic import ChatAnthropic

    start_time = time.monotonic()
    haiku_calls = 0
    connections_created = 0
    flags_created = 0
    contradictions_found = 0
    clusters_found = 0
    merges_performed = 0
    promotions = 0

    # 1. Read lookback_hours from config
    async with async_session() as session:
        result = await session.execute(
            select(ConsolidationConfigModel).where(
                ConsolidationConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()
        lookback_hours = config.lookback_hours if config else 2

    # 2. Query memories created or last_accessed within lookback window
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)

    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(
                or_(
                    MemoryModel.created_at >= cutoff,
                    and_(
                        MemoryModel.last_accessed.isnot(None),
                        MemoryModel.last_accessed >= cutoff,
                    ),
                )
            )
        )
        memories = list(result.scalars().all())

    # 3. If < 3 memories, skip
    if len(memories) < 3:
        print(f"Consolidation: only {len(memories)} recent memories, skipping")
        return

    print(f"Consolidation: reviewing {len(memories)} memories")

    # 4. Send to Haiku for entity grouping
    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0, max_tokens=4096)

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
        cluster_response = await llm.ainvoke(cluster_prompt)
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
        print(f"Consolidation: cluster parsing failed: {e}")
        cluster_data = {"clusters": []}

    clusters = cluster_data.get("clusters", [])
    clusters_found = len(clusters)

    # Build a lookup of memory id -> content for contradiction checking
    memory_lookup = {m.id: m.content for m in memories}

    # 5. Within each cluster, check for contradictions and create connections
    for cluster in clusters:
        cluster_ids = cluster.get("memory_ids", [])
        connection_type = cluster.get("type", "related")

        # Filter to only IDs that actually exist in our memory set
        cluster_ids = [mid for mid in cluster_ids if mid in memory_lookup]

        if len(cluster_ids) < 2:
            continue

        # 5a. Ask Haiku for contradictions between memories in this cluster
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
            contradiction_response = await llm.ainvoke(contradiction_prompt)
            haiku_calls += 1
            contradiction_text = contradiction_response.content
            if "```" in contradiction_text:
                contradiction_text = contradiction_text.split("```")[1]
                if contradiction_text.startswith("json"):
                    contradiction_text = contradiction_text[4:]
                contradiction_text = contradiction_text.strip()
            contradiction_data = json.loads(contradiction_text)
        except Exception as e:
            print(f"Consolidation: contradiction parsing failed: {e}")
            contradiction_data = {"contradictions": []}

        # 6. Create memory_connections for related memories in same cluster
        async with async_session() as session:
            for i in range(len(cluster_ids)):
                for j in range(i + 1, len(cluster_ids)):
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
                        strength=0.5,
                    )
                    session.add(connection)
                    connections_created += 1

            await session.commit()

        # 7. Create memory_flags for any contradictions found
        found_contradictions = contradiction_data.get("contradictions", [])
        contradictions_found += len(found_contradictions)

        if found_contradictions:
            async with async_session() as session:
                for c in found_contradictions:
                    mid_a = c.get("memory_id_a")
                    mid_b = c.get("memory_id_b")
                    desc = c.get("description", "Contradiction detected")

                    # Validate that these memory IDs exist
                    if mid_a not in memory_lookup or mid_b not in memory_lookup:
                        continue

                    flag = MemoryFlagModel(
                        id=str(uuid.uuid4()),
                        memory_id=mid_a,
                        flag_type="contradiction",
                        description=desc,
                        related_memory_id=mid_b,
                    )
                    session.add(flag)
                    flags_created += 1

                await session.commit()

    # 8. Merge duplicates within clusters
    from services.memory_service import (
        find_similar_memories, _llm_merge_content, _compute_tier,
        update_memory, delete_memory,
    )

    for cluster in clusters:
        cluster_ids = [mid for mid in cluster.get("memory_ids", []) if mid in memory_lookup]
        if len(cluster_ids) < 2:
            continue

        # For each memory in the cluster, find near-duplicates via embedding similarity
        merged_away = set()
        for mid in cluster_ids:
            if mid in merged_away:
                continue
            content = memory_lookup.get(mid)
            if not content:
                continue

            try:
                similar = await find_similar_memories(content, threshold=0.78, limit=5)
            except Exception:
                continue

            for other_memory, sim in similar:
                if other_memory.id == mid or other_memory.id in merged_away:
                    continue
                if other_memory.id not in set(cluster_ids):
                    continue

                # Merge: combine content, sum reinforcement counts
                try:
                    merged_content = await _llm_merge_content(content, other_memory.content)
                    haiku_calls += 1

                    # Get current state of surviving memory
                    from services.memory_service import get_memory_by_id
                    surviving = await get_memory_by_id(mid)
                    if not surviving:
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
                    print(f"Consolidation: merged {other_memory.id[:8]}... into {mid[:8]}...")
                except Exception as e:
                    print(f"Consolidation: merge failed: {e}")

    # 9. Resolve contradictions — keep higher reinforcement_count, delete other
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

    for flag in unresolved_flags:
        try:
            from services.memory_service import get_memory_by_id
            mem_a = await get_memory_by_id(flag.memory_id)
            mem_b = await get_memory_by_id(flag.related_memory_id) if flag.related_memory_id else None

            if not mem_a or not mem_b:
                # One side already deleted — mark resolved
                async with async_session() as session:
                    await session.execute(
                        select(MemoryFlagModel).where(MemoryFlagModel.id == flag.id)
                    )
                    result = await session.execute(
                        select(MemoryFlagModel).where(MemoryFlagModel.id == flag.id)
                    )
                    f = result.scalar_one_or_none()
                    if f:
                        f.resolved = True
                        await session.commit()
                continue

            # Keep higher reinforcement_count (newer if tied)
            keep, discard = (mem_a, mem_b) if (mem_a.reinforcement_count or 0) >= (mem_b.reinforcement_count or 0) else (mem_b, mem_a)
            await delete_memory(discard.id)

            # Mark flag resolved
            async with async_session() as session:
                result = await session.execute(
                    select(MemoryFlagModel).where(MemoryFlagModel.id == flag.id)
                )
                f = result.scalar_one_or_none()
                if f:
                    f.resolved = True
                    await session.commit()
            print(f"Consolidation: resolved contradiction, kept {keep.id[:8]}..., deleted {discard.id[:8]}...")
        except Exception as e:
            print(f"Consolidation: contradiction resolution failed: {e}")

    # 10. Auto-promote: fix tier mismatches across all memories
    async with async_session() as session:
        result = await session.execute(select(MemoryModel))
        all_mems = list(result.scalars().all())

    for m in all_mems:
        expected_tier = _compute_tier(m.reinforcement_count or 0)
        current_tier = m.tier or "observation"
        if current_tier != expected_tier:
            try:
                await update_memory(m.id, tier=expected_tier)
                promotions += 1
                print(f"Consolidation: promoted {m.id[:8]}... {current_tier} -> {expected_tier}")
            except Exception as e:
                print(f"Consolidation: promotion failed: {e}")

    # 11. Log cycle metrics
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

    print(
        f"Consolidation cycle complete: {len(memories)} memories, "
        f"{clusters_found} clusters, {connections_created} connections, "
        f"{contradictions_found} contradictions, {merges_performed} merges, "
        f"{promotions} promotions, {haiku_calls} Haiku calls, "
        f"{duration_ms}ms"
    )

    # 9. Cleanup old enrichments (older than 24h)
    await cleanup_old_enrichments()


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

    print("Consolidation: cleaned up old enrichments (>24h)")
