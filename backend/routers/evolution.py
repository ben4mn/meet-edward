"""REST API endpoints for the self-evolution engine."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.evolution_models import EvolutionConfigUpdate, EvolutionTriggerRequest

router = APIRouter()


@router.get("/evolution/status")
async def get_status():
    """Get evolution engine status: config, active cycle, last cycle time."""
    from services.evolution_service import get_status
    return await get_status()


@router.get("/evolution/history")
async def get_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List past evolution cycles."""
    from services.evolution_service import get_history
    return await get_history(limit=limit, offset=offset)


@router.patch("/evolution/config")
async def update_config(body: EvolutionConfigUpdate):
    """Update evolution configuration."""
    from services.evolution_service import update_config

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "min_interval_seconds" in updates and updates["min_interval_seconds"] < 300:
        raise HTTPException(
            status_code=400,
            detail="Minimum interval must be at least 300 seconds (5 minutes)",
        )

    return await update_config(updates)


@router.post("/evolution/trigger")
async def trigger_evolution(body: EvolutionTriggerRequest, background_tasks: BackgroundTasks):
    """Manually trigger an evolution cycle. Runs in background."""
    from services.evolution_service import can_evolve, evolve

    ok, reason = await can_evolve()
    if not ok:
        raise HTTPException(status_code=409, detail=reason)

    async def _run():
        try:
            await evolve(
                description=body.description,
                trigger=body.trigger,
            )
        except Exception as e:
            print(f"Evolution trigger error: {e}")

    background_tasks.add_task(_run)

    return {"status": "started", "description": body.description}


@router.post("/evolution/rollback/{cycle_id}")
async def rollback_evolution(cycle_id: str):
    """Rollback a completed evolution cycle."""
    from services.evolution_service import rollback

    result = await rollback(cycle_id)

    if "not found" in result.lower() or "cannot" in result.lower() or "failed" in result.lower():
        raise HTTPException(status_code=400, detail=result)

    return {"status": "rolled_back", "message": result}
