"""
Skills service for managing Edward's integrations.

Manages messaging skills (iMessage, Twilio SMS) with their connection
status, enabled state, and hot-reload capability.
"""

from typing import List, Optional
from datetime import datetime

from services.database import async_session, SkillModel
from models.schemas import Skill, SkillStatus, SkillMetadata
from sqlalchemy import select


# Skill registry with metadata
SKILL_DEFINITIONS = {
    "imessage_applescript": {
        "name": "iMessage (AppleScript)",
        "description": "Send via osascript (macOS only)",
        "get_status": lambda: _get_imessage_applescript_status(),
    },
    "twilio_sms": {
        "name": "SMS (Edward's Number)",
        "description": "Send/receive SMS from Edward's phone",
        "get_status": lambda: _get_twilio_status(),
    },
    "twilio_whatsapp": {
        "name": "WhatsApp (Edward's Number)",
        "description": "Send/receive WhatsApp messages from Edward's phone via Twilio",
        "get_status": lambda: _get_twilio_whatsapp_status(),
    },
    "whatsapp_mcp": {
        "name": "WhatsApp (MCP)",
        "description": "Read/send WhatsApp as the user via whatsapp-mcp bridge",
        "get_status": lambda: _get_whatsapp_mcp_status(),
    },
    "brave_search": {
        "name": "Web Search (Brave)",
        "description": "Search the web using Brave Search API",
        "get_status": lambda: _get_brave_search_status(),
    },
    "code_interpreter": {
        "name": "Code Interpreter",
        "description": "Execute Python code in a sandboxed environment",
        "get_status": lambda: _get_code_interpreter_status(),
    },
    "javascript_interpreter": {
        "name": "JavaScript Interpreter",
        "description": "Execute JavaScript code using Node.js",
        "get_status": lambda: _get_javascript_interpreter_status(),
    },
    "sql_interpreter": {
        "name": "SQL Database",
        "description": "Execute SQL queries against a per-conversation SQLite database",
        "get_status": lambda: _get_sql_interpreter_status(),
    },
    "shell_interpreter": {
        "name": "Shell/Bash",
        "description": "Execute shell commands in a sandboxed environment",
        "get_status": lambda: _get_shell_interpreter_status(),
    },
    "contacts_lookup": {
        "name": "Contacts Lookup",
        "description": "Search Contacts.app for names and phone numbers (macOS only)",
        "get_status": lambda: _get_contacts_lookup_status(),
    },
    "push_notifications": {
        "name": "Push Notifications",
        "description": "Send push notifications to user's devices (PWA)",
        "get_status": lambda: _get_push_notifications_status(),
    },
    "apple_services": {
        "name": "Apple Services",
        "description": "Calendar, Reminders, Notes, Mail, Contacts, Maps (macOS only)",
        "get_status": lambda: _get_apple_services_status(),
    },
    "html_hosting": {
        "name": "HTML Hosting",
        "description": "Create, update, and delete hosted HTML pages on html.zyroi.com",
        "get_status": lambda: _get_html_hosting_status(),
    },
    "ios_widget": {
        "name": "iOS Widget",
        "description": "Control iOS home screen widget via Scriptable app",
        "get_status": lambda: _get_ios_widget_status(),
    },
    "orchestrator": {
        "name": "Orchestrator",
        "description": "Spawn parallel worker agents for complex multi-step tasks",
        "get_status": lambda: {"status": "connected", "status_message": "Ready"},
    },
}


# Track last reload time
_last_reload: Optional[datetime] = None


def _get_imessage_applescript_status() -> dict:
    """Get status from AppleScript service."""
    from services.imessage_service import get_status
    return get_status()


def _get_twilio_status() -> dict:
    """Get status from Twilio service."""
    from services.twilio_service import get_status
    return get_status()


def _get_twilio_whatsapp_status() -> dict:
    """Get status from Twilio WhatsApp service."""
    from services.twilio_service import get_whatsapp_status
    return get_whatsapp_status()


def _get_whatsapp_mcp_status() -> dict:
    """Get status from WhatsApp MCP client."""
    from services.mcp_client import get_whatsapp_status
    return get_whatsapp_status()


def _get_brave_search_status() -> dict:
    """Get status from Brave Search service."""
    from services.brave_search_service import get_status
    return get_status()


def _get_code_interpreter_status() -> dict:
    """Get status from code execution service."""
    from services.code_execution_service import get_status
    return get_status()


def _get_javascript_interpreter_status() -> dict:
    """Get status from JavaScript execution service."""
    from services.execution.javascript_execution import get_status
    return get_status()


def _get_sql_interpreter_status() -> dict:
    """Get status from SQL execution service."""
    from services.execution.sql_execution import get_status
    return get_status()


def _get_shell_interpreter_status() -> dict:
    """Get status from shell execution service."""
    from services.execution.shell_execution import get_status
    return get_status()


def _get_contacts_lookup_status() -> dict:
    """Get status from contacts service."""
    from services.contacts_service import get_status
    return get_status()


def _get_push_notifications_status() -> dict:
    """Get status from push notification service."""
    from services.push_service import get_status
    return get_status()


def _get_apple_services_status() -> dict:
    """Get status from Apple Services MCP client."""
    from services.mcp_client import get_apple_status
    return get_apple_status()


def _get_html_hosting_status() -> dict:
    """Get status from HTML hosting service."""
    from services.html_hosting_service import get_status
    return get_status()


def _get_ios_widget_status() -> dict:
    """Get status from widget service."""
    from services.widget_service import get_status
    return get_status()


async def _get_or_create_skill_db(skill_id: str) -> SkillModel:
    """Get skill from DB or create default entry."""
    async with async_session() as session:
        result = await session.execute(
            select(SkillModel).where(SkillModel.id == skill_id)
        )
        skill = result.scalar_one_or_none()

        if not skill:
            # Create default entry (disabled)
            skill = SkillModel(
                id=skill_id,
                enabled=False,
                last_error=None,
                last_connected_at=None
            )
            session.add(skill)
            await session.commit()
            await session.refresh(skill)

        return skill


async def _update_skill_db(
    skill_id: str,
    enabled: Optional[bool] = None,
    last_error: Optional[str] = None,
    last_connected_at: Optional[datetime] = None
) -> SkillModel:
    """Update skill in database."""
    async with async_session() as session:
        result = await session.execute(
            select(SkillModel).where(SkillModel.id == skill_id)
        )
        skill = result.scalar_one_or_none()

        if not skill:
            # Create if doesn't exist
            skill = SkillModel(id=skill_id, enabled=False)
            session.add(skill)

        if enabled is not None:
            skill.enabled = enabled
        if last_error is not None:
            skill.last_error = last_error if last_error else None
        if last_connected_at is not None:
            skill.last_connected_at = last_connected_at

        await session.commit()
        await session.refresh(skill)
        return skill


async def get_all_skills() -> List[Skill]:
    """
    Get all skills with their current status.

    Returns:
        List of Skill objects with status information
    """
    skills = []

    for skill_id, definition in SKILL_DEFINITIONS.items():
        # Get enabled state from DB
        db_skill = await _get_or_create_skill_db(skill_id)

        # Get live status from the service
        status_info = definition["get_status"]()

        # If disabled in DB, override status
        if not db_skill.enabled:
            status = SkillStatus.DISABLED
            status_message = "Disabled by user"
        else:
            status = SkillStatus(status_info["status"])
            status_message = status_info.get("status_message")

            # Update DB with connection status
            if status == SkillStatus.CONNECTED:
                await _update_skill_db(
                    skill_id,
                    last_connected_at=datetime.utcnow(),
                    last_error=None
                )
            elif status == SkillStatus.ERROR:
                await _update_skill_db(
                    skill_id,
                    last_error=status_message
                )

        # Build metadata if available
        metadata = None
        if status_info.get("metadata"):
            metadata = SkillMetadata(**status_info["metadata"])

        skills.append(Skill(
            id=skill_id,
            name=definition["name"],
            description=definition["description"],
            enabled=db_skill.enabled,
            status=status,
            status_message=status_message,
            metadata=metadata
        ))

    return skills


async def set_skill_enabled(skill_id: str, enabled: bool) -> Optional[Skill]:
    """
    Enable or disable a skill.

    Args:
        skill_id: The skill identifier
        enabled: Whether to enable the skill

    Returns:
        Updated Skill object or None if not found
    """
    if skill_id not in SKILL_DEFINITIONS:
        return None

    # Update database
    await _update_skill_db(skill_id, enabled=enabled)

    # Initialize MCP client when enabling MCP skills
    if skill_id == "whatsapp_mcp" and enabled:
        try:
            from services.mcp_client import initialize_whatsapp_mcp
            await initialize_whatsapp_mcp()
        except Exception as e:
            print(f"Failed to initialize WhatsApp MCP client: {e}")

    if skill_id == "apple_services" and enabled:
        try:
            from services.mcp_client import initialize_apple_mcp
            await initialize_apple_mcp()
        except Exception as e:
            print(f"Failed to initialize Apple Services MCP client: {e}")

    # Refresh tool registry to pick up skill state change
    try:
        from services.tool_registry import refresh_registry
        await refresh_registry()
    except Exception as e:
        print(f"Failed to refresh tool registry: {e}")

    # Re-fetch all skills to get updated status
    skills = await get_all_skills()
    return next((s for s in skills if s.id == skill_id), None)


async def reload_skills() -> List[Skill]:
    """
    Reload/reinitialize all skills.

    This attempts to reconnect any services that may have failed.

    Returns:
        List of all skills with updated status
    """
    global _last_reload

    # Reload MCP clients if needed
    try:
        from services.mcp_client import shutdown_whatsapp_mcp, initialize_whatsapp_mcp
        await shutdown_whatsapp_mcp()
        await initialize_whatsapp_mcp()
    except Exception as e:
        print(f"Failed to reload WhatsApp MCP client: {e}")

    try:
        from services.mcp_client import shutdown_apple_mcp, initialize_apple_mcp
        await shutdown_apple_mcp()
        await initialize_apple_mcp()
    except Exception as e:
        print(f"Failed to reload Apple Services MCP client: {e}")

    # Refresh tool registry to pick up changes
    try:
        from services.tool_registry import refresh_registry
        await refresh_registry()
    except Exception as e:
        print(f"Failed to refresh tool registry: {e}")

    _last_reload = datetime.utcnow()

    return await get_all_skills()


def get_last_reload() -> Optional[str]:
    """Get the timestamp of the last reload."""
    return _last_reload.isoformat() if _last_reload else None


async def is_skill_enabled(skill_id: str) -> bool:
    """
    Check if a skill is enabled.

    Args:
        skill_id: The skill identifier

    Returns:
        True if enabled, False otherwise
    """
    db_skill = await _get_or_create_skill_db(skill_id)
    return db_skill.enabled


async def init_skills():
    """
    Initialize skills on startup.

    Creates database entries for any new skills and initializes services
    for enabled skills.
    """
    global _last_reload

    # Ensure all skills have DB entries
    for skill_id in SKILL_DEFINITIONS.keys():
        await _get_or_create_skill_db(skill_id)

    _last_reload = datetime.utcnow()
    print(f"Skills service initialized with {len(SKILL_DEFINITIONS)} skills")
