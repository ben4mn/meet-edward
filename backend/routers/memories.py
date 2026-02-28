"""
Memories router for Edward memory management.

Provides endpoints for:
- Listing and searching memories with filters
- Updating memory content/type/importance/temporal_nature
- Deleting memories
- Backfilling temporal nature classifications
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel

from services.memory_service import (
    search_memories,
    delete_memory,
    update_memory,
    get_memory_by_id,
    get_memory_stats,
    backfill_temporal_nature,
    backfill_memory_tiers,
)

router = APIRouter()


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    memory_type: Optional[str] = None
    importance: Optional[float] = None
    temporal_nature: Optional[str] = None


@router.get("/memories")
async def list_memories(
    query: Optional[str] = Query(None, description="Text search query"),
    memory_type: Optional[str] = Query(None, description="Filter by type: fact, preference, context, instruction"),
    temporal_nature: Optional[str] = Query(None, description="Filter by temporal nature: timeless, temporary, evolving"),
    tier: Optional[str] = Query(None, description="Filter by tier: observation, belief, knowledge"),
    min_importance: Optional[float] = Query(None, ge=0, le=1, description="Minimum importance threshold"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Number of results to skip")
):
    """
    List and search memories with optional filters.

    Args:
        query: Text search query for semantic similarity search
        memory_type: Filter by memory type
        temporal_nature: Filter by temporal nature
        min_importance: Filter by minimum importance score
        limit: Maximum number of results (default 50, max 100)
        offset: Pagination offset

    Returns:
        List of memories with pagination info and stats
    """
    try:
        memories, total = await search_memories(
            query=query,
            memory_type=memory_type,
            min_importance=min_importance,
            temporal_nature=temporal_nature,
            tier=tier,
            limit=limit,
            offset=offset
        )
        stats = await get_memory_stats()

        return {
            "memories": [
                {
                    "id": m.id,
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "importance": m.importance,
                    "temporal_nature": m.temporal_nature,
                    "tier": getattr(m, 'tier', 'observation') or 'observation',
                    "reinforcement_count": getattr(m, 'reinforcement_count', 0) or 0,
                    "source_conversation_id": m.source_conversation_id,
                    "created_at": (m.created_at.isoformat() + "Z") if m.created_at else None,
                    "updated_at": (m.updated_at.isoformat() + "Z") if m.updated_at else None,
                    "last_accessed": (m.last_accessed.isoformat() + "Z") if m.last_accessed else None,
                    "access_count": m.access_count,
                    "user_id": m.user_id,
                    "score": m.score if m.score > 0 else None
                }
                for m in memories
            ],
            "stats": stats,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str):
    """
    Get a single memory by ID.

    Args:
        memory_id: The memory ID

    Returns:
        Memory details
    """
    memory = await get_memory_by_id(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {
        "id": memory.id,
        "content": memory.content,
        "memory_type": memory.memory_type,
        "importance": memory.importance,
        "temporal_nature": memory.temporal_nature,
        "tier": getattr(memory, 'tier', 'observation') or 'observation',
        "reinforcement_count": getattr(memory, 'reinforcement_count', 0) or 0,
        "source_conversation_id": memory.source_conversation_id,
        "created_at": (memory.created_at.isoformat() + "Z") if memory.created_at else None,
        "updated_at": (memory.updated_at.isoformat() + "Z") if memory.updated_at else None,
        "last_accessed": (memory.last_accessed.isoformat() + "Z") if memory.last_accessed else None,
        "access_count": memory.access_count,
        "user_id": memory.user_id
    }


@router.patch("/memories/{memory_id}")
async def patch_memory(memory_id: str, request: MemoryUpdateRequest):
    """
    Update a memory's content, type, importance, or temporal nature.

    If content is changed, the embedding is automatically regenerated.

    Args:
        memory_id: The memory ID
        request: Fields to update

    Returns:
        Updated memory details
    """
    memory = await update_memory(
        memory_id=memory_id,
        content=request.content,
        memory_type=request.memory_type,
        importance=request.importance,
        temporal_nature=request.temporal_nature
    )

    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {
        "id": memory.id,
        "content": memory.content,
        "memory_type": memory.memory_type,
        "importance": memory.importance,
        "temporal_nature": memory.temporal_nature,
        "tier": getattr(memory, 'tier', 'observation') or 'observation',
        "reinforcement_count": getattr(memory, 'reinforcement_count', 0) or 0,
        "source_conversation_id": memory.source_conversation_id,
        "created_at": (memory.created_at.isoformat() + "Z") if memory.created_at else None,
        "updated_at": (memory.updated_at.isoformat() + "Z") if memory.updated_at else None,
        "last_accessed": (memory.last_accessed.isoformat() + "Z") if memory.last_accessed else None,
        "access_count": memory.access_count,
        "user_id": memory.user_id
    }


@router.delete("/memories/{memory_id}")
async def remove_memory(memory_id: str):
    """
    Delete a memory by ID.

    Args:
        memory_id: The memory ID

    Returns:
        Success message
    """
    deleted = await delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {"status": "deleted", "id": memory_id}


@router.post("/memories/backfill-temporal")
async def backfill_temporal():
    """
    One-time backfill: classify all existing memories' temporal_nature using Haiku.

    Processes memories in batches and returns a summary of classifications.
    """
    try:
        summary = await backfill_temporal_nature()
        return {
            "status": "completed",
            "summary": summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memories/backfill-tiers")
async def backfill_tiers(
    dry_run: bool = Query(False, description="Preview changes without applying them"),
    threshold: float = Query(0.78, ge=0.5, le=0.99, description="Similarity threshold for dedup"),
):
    """
    One-time migration: deduplicate all memories, merge near-duplicates via Haiku,
    set reinforcement counts, and compute proper tiers.

    Use dry_run=true to preview what would happen without making changes.
    """
    try:
        summary = await backfill_memory_tiers(
            similarity_threshold=threshold,
            dry_run=dry_run,
        )
        return {
            "status": "preview" if dry_run else "completed",
            "summary": summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
