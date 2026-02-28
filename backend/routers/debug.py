"""
Debug router for Edward development and observability.

Provides endpoints for:
- Graph structure visualization
- Memory statistics and listing
- Session state inspection
"""

from fastapi import APIRouter, HTTPException
from typing import Optional

from services.graph import get_graph_structure
from services.memory_service import get_memory_stats, get_all_memories
from services.langsmith_service import is_configured as langsmith_configured, get_traces_for_conversation, get_latest_trace, get_trace_detail

router = APIRouter()


@router.get("/debug/graph")
async def get_graph():
    """
    Get the LangGraph structure for visualization.

    Returns nodes and edges describing the agent's processing flow.
    """
    return get_graph_structure()


@router.get("/debug/memories")
async def get_memories(limit: int = 50, offset: int = 0):
    """
    Get stored memories with pagination.

    Args:
        limit: Maximum number of memories to return (default 50)
        offset: Number of memories to skip (default 0)

    Returns:
        List of memories and pagination info
    """
    try:
        memories = await get_all_memories(limit=limit, offset=offset)
        stats = await get_memory_stats()

        return {
            "memories": [
                {
                    "id": m.id,
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "importance": m.importance,
                    "source_conversation_id": m.source_conversation_id,
                    "created_at": (m.created_at.isoformat() + "Z") if m.created_at else None,
                    "updated_at": (m.updated_at.isoformat() + "Z") if m.updated_at else None,
                    "last_accessed": (m.last_accessed.isoformat() + "Z") if m.last_accessed else None,
                    "access_count": m.access_count,
                    "user_id": m.user_id
                }
                for m in memories
            ],
            "stats": stats,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": stats["total"]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/memories/stats")
async def get_memories_stats():
    """
    Get statistics about stored memories.

    Returns:
        - Total count
        - Count by type (fact, preference, context, instruction)
        - Average importance
    """
    try:
        stats = await get_memory_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/health")
async def debug_health():
    """
    Extended health check for debugging.

    Returns status of various components.
    """
    from services.graph import get_graph
    from services.database import async_session

    status = {
        "graph": "unknown",
        "database": "unknown",
        "memory_service": "unknown"
    }

    # Check graph
    try:
        graph = await get_graph()
        status["graph"] = "healthy" if graph else "not_initialized"
    except Exception as e:
        status["graph"] = f"error: {str(e)}"

    # Check database
    try:
        async with async_session() as session:
            await session.execute("SELECT 1")
            status["database"] = "healthy"
    except Exception as e:
        status["database"] = f"error: {str(e)}"

    # Check memory service
    try:
        stats = await get_memory_stats()
        status["memory_service"] = "healthy"
        status["memory_count"] = stats["total"]
    except Exception as e:
        status["memory_service"] = f"error: {str(e)}"

    return status


# --- LangSmith trace endpoints ---

@router.get("/debug/langsmith/status")
async def langsmith_status():
    """Check if LangSmith tracing is configured."""
    return {"configured": langsmith_configured()}


@router.get("/debug/traces/{conversation_id}")
async def list_traces(conversation_id: str, limit: int = 10):
    """List root traces for a conversation."""
    if not langsmith_configured():
        raise HTTPException(status_code=503, detail="LangSmith is not configured")
    traces = get_traces_for_conversation(conversation_id, limit=limit)
    return {"traces": traces}


@router.get("/debug/traces/{conversation_id}/latest")
async def latest_trace(conversation_id: str):
    """Get the latest trace with all child runs for a conversation."""
    if not langsmith_configured():
        raise HTTPException(status_code=503, detail="LangSmith is not configured")
    result = get_latest_trace(conversation_id)
    if not result:
        return {"root": None, "runs": []}
    return result


@router.get("/debug/trace/{trace_id}")
async def trace_detail(trace_id: str):
    """Get all runs within a specific trace."""
    if not langsmith_configured():
        raise HTTPException(status_code=503, detail="LangSmith is not configured")
    runs = get_trace_detail(trace_id)
    return {"runs": runs}
