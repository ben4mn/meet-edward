"""
Skills router for Edward integrations management.

Provides endpoints for:
- Listing all skills with their connection status
- Enabling/disabling individual skills
- Reloading/reinitializing skills
"""

from fastapi import APIRouter, HTTPException

from services.skills_service import (
    get_all_skills,
    set_skill_enabled,
    reload_skills,
    get_last_reload,
)
from models.schemas import SkillUpdateRequest

router = APIRouter()


@router.get("/skills")
async def list_skills():
    """
    List all available skills with their current status.

    Returns:
        List of skills with status, metadata, and last reload time
    """
    try:
        skills = await get_all_skills()
        return {
            "skills": [s.model_dump() for s in skills],
            "last_reload": get_last_reload()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/skills/{skill_id}")
async def update_skill(skill_id: str, request: SkillUpdateRequest):
    """
    Enable or disable a skill.

    Args:
        skill_id: The skill identifier (e.g., 'twilio_sms', 'imessage_applescript')
        request: Contains 'enabled' boolean

    Returns:
        Updated skill information
    """
    try:
        skill = await set_skill_enabled(skill_id, request.enabled)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        return skill.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/reload")
async def reload_all_skills():
    """
    Reload/reinitialize all skills.

    This attempts to reconnect any services that may have failed.

    Returns:
        List of all skills with updated status
    """
    try:
        skills = await reload_skills()
        return {
            "skills": [s.model_dump() for s in skills],
            "last_reload": get_last_reload()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
