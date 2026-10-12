"""
Skills service for managing Edward's integrations.

Manages messaging skills (iMessage, Twilio SMS) with their connection
status, enabled state, and hot-reload capability.
"""

import inspect
from typing import List, Optional
from datetime import datetime, timedelta

from services.database import async_session, SkillModel, HeartbeatEventModel
from models.schemas import Skill, SkillStatus, SkillMetadata
from sqlalchemy import select, text


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
    "voice": {
        "name": "Voice Conversations",
        "description": "Voice memos and live conversation via ElevenLabs (Scribe v2 STT, streaming TTS)",
        "get_status": lambda: _get_voice_status(),
    },
    "html_hosting": {
        "name": "HTML Hosting",
        "description": "Create, update, and delete hosted HTML pages (HTML_HOSTING_URL)",
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
    "alexa_outbound": {
        "name": "Alexa Outbound (Home Assistant)",
        "description": "TTS announcements and sounds on Echo devices via Home Assistant",
        "get_status": lambda: _get_alexa_outbound_status(),
    },
    "alexa_inbound": {
        "name": "Alexa Inbound (Custom Skill)",
        "description": "Receive voice queries from Alexa custom skill",
        "get_status": lambda: _get_alexa_inbound_status(),
    },
    "claude_session": {
        "name": "Claude Code Session",
        "description": "Start a Claude Code remote session accessible from the Claude app",
        "get_status": lambda: _get_claude_session_status(),
    },
}


# Rows whose toggle gates nothing under the v2 toolset (2026-10-11, verified
# by grep and pinned by tests/test_skills_status_honest.py): no toolset tool
# is behind them and no code asks ``is_skill_enabled`` about them outside the
# legacy registry. Their status says so, so a toggle never pretends.
# alexa_outbound reports its own status (Home Assistant's reachability); no
# tool announces on an Echo in this build (scheduler_service falls back to
# tell_owner). Code/JavaScript/SQL keep their own "Retired" line.
UNGATED_ON_V2 = frozenset({
    "twilio_sms", "twilio_whatsapp", "whatsapp_mcp", "contacts_lookup", "html_hosting",
    "orchestrator", "claude_session", "apple_services", "alexa_inbound",
})
UNGATED_SUFFIX = " — doesn't change Edward's tools (v2 toolset)"
# After an error the note is its own sentence, never glued to the error's advice.
UNGATED_SENTENCE = ". This switch doesn't change Edward's tools (v2 toolset)."


def _ungated_note(skill_id: str, message: Optional[str], status: Optional[str] = None) -> Optional[str]:
    if skill_id not in UNGATED_ON_V2:
        return message
    try:
        from services.toolset import is_v2

        if not is_v2():
            return message
    except Exception:  # noqa: BLE001
        return message
    if status == "error" and message:
        return f"{message.rstrip().rstrip('.')}{UNGATED_SENTENCE}"
    return f"{message or 'On'}{UNGATED_SUFFIX}"


async def _status_of(definition: dict) -> dict:
    """A row's live status; a getter may be async (the Twilio account check)."""
    info = definition["get_status"]()
    if inspect.isawaitable(info):
        info = await info
    return info


# Track last reload time
_last_reload: Optional[datetime] = None


def _get_imessage_applescript_status() -> dict:
    """Get status from AppleScript service."""
    from services.imessage_service import get_status
    return get_status()


async def _with_twilio_account(base: dict) -> dict:
    """A configured Twilio row is only connected while the account is
    active (``twilio_service.account_status``)."""
    if base.get("status") != "connected":
        return base
    from services.twilio_service import account_status

    account = await account_status()
    if account.get("status") == "connected":
        return base
    return {"status": "error", "status_message": account.get("status_message"), "metadata": None}


async def _get_twilio_status() -> dict:
    """Get status from Twilio service, checked against the account."""
    from services.twilio_service import get_status
    return await _with_twilio_account(get_status())


async def _get_twilio_whatsapp_status() -> dict:
    """Get status from Twilio WhatsApp service, checked against the account."""
    from services.twilio_service import get_whatsapp_status
    return await _with_twilio_account(get_whatsapp_status())


async def _get_whatsapp_mcp_status() -> dict:
    """Get status from WhatsApp MCP client. Its bridge check opens a socket
    and reads the bridge's stores, so it runs in a thread."""
    import asyncio

    from services import mcp_client
    return await asyncio.to_thread(mcp_client.get_whatsapp_status)


def _get_brave_search_status() -> dict:
    """Get status from Brave Search service."""
    from services.brave_search_service import get_status
    return get_status()


# The sandboxes Edward had before his computer (2026-10-11): Python,
# JavaScript and SQL ran in throwaway sandboxes (the Python one with the
# backend's whole environment — a live hole). They are deleted; his shell
# (services/computer, the bash tool) runs python, node and sqlite3. The rows
# stay in the Settings panel and gate nothing.
_RETIRED_FOR_BASH = {"status": "connected",
                     "status_message": "Retired — Edward runs this in his own shell now (bash: python, node, sqlite3)"}


def _get_code_interpreter_status() -> dict:
    return dict(_RETIRED_FOR_BASH)


def _get_javascript_interpreter_status() -> dict:
    return dict(_RETIRED_FOR_BASH)


def _get_sql_interpreter_status() -> dict:
    return dict(_RETIRED_FOR_BASH)


def _get_shell_interpreter_status() -> dict:
    """Edward's computer (``services.computer.status``): its shell sessions
    and jobs, or the error that keeps it from starting."""
    try:
        from services import computer  # type: ignore
        info = computer.status()
    except Exception:  # noqa: BLE001 — the computer module may not be merged yet
        return {"status": "connected", "status_message": "Edward's shell (bash) in ~/edward-home"}
    if not isinstance(info, dict):
        return {"status": "connected", "status_message": "Edward's shell (bash) in ~/edward-home"}
    if info.get("status") in ("connected", "connecting", "error"):
        return {"status": info["status"], "status_message": info.get("status_message")}
    sessions = int(info.get("sessions") or 0)
    jobs = int(info.get("jobs") or 0)
    msg = f"{sessions} shell session{'s' if sessions != 1 else ''} open"
    if jobs:
        msg += f", {jobs} background job{'s' if jobs != 1 else ''}"
    return {"status": "connected", "status_message": msg}


def _get_contacts_lookup_status() -> dict:
    """Get status from contacts service."""
    from services.contacts_service import get_status
    return get_status()


def _get_push_notifications_status() -> dict:
    """Get status from push notification service."""
    from services.push_service import get_status
    return get_status()


def _get_apple_services_status() -> dict:
    """apple-mcp as it is, declared: never a Reminders or Notes probe (each
    raises a macOS privacy (TCC) dialog on the host Mac)."""
    from services.mcp_client import get_apple_status
    status = get_apple_status()
    if status.get("status") != "connected":
        return status
    tools = (status.get("metadata") or {}).get("tools_count")
    return {**status, "status_message": (f"apple-mcp running ({tools} tools); Calendar is read from Calendar's own "
                                         "database; Reminders and Notes don't go through apple-mcp")}


def _get_voice_status() -> dict:
    """Get status from voice service."""
    from services.voice_service import get_status
    return get_status()


def _get_html_hosting_status() -> dict:
    """Get status from HTML hosting service."""
    from services.html_hosting_service import get_status
    return get_status()


def _get_ios_widget_status() -> dict:
    """Get status from widget service."""
    from services.widget_service import get_status
    return get_status()


def _get_alexa_outbound_status() -> dict:
    """Get status from Home Assistant service."""
    from services.home_assistant_service import get_status
    return get_status()


def _get_alexa_inbound_status() -> dict:
    """Get status from Alexa skill service."""
    from services.alexa_service import get_status
    return get_status()


def _get_claude_session_status() -> dict:
    """Get Claude Code remote session status."""
    import shutil
    claude_path = shutil.which("claude")

    if not claude_path:
        return {
            "status": "error",
            "status_message": "Claude Code CLI not found in PATH",
        }
    return {
        "status": "connected",
        "status_message": "Claude Code CLI available",
    }


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


# Sentinel so callers can ask to *clear* last_error vs *skip* updating it.
# ``last_error=None`` historically meant "skip" which made it impossible to
# wipe a stale message; we now overload None to mean "clear" and use this
# sentinel for "skip."
_SKIP = object()


async def _update_skill_db(
    skill_id: str,
    enabled: Optional[bool] = None,
    last_error=_SKIP,
    last_connected_at: Optional[datetime] = None
) -> SkillModel:
    """Update skill in database.

    ``last_error``:
      * ``_SKIP`` (default) — don't touch the column
      * ``None`` or empty string — clear the stored error
      * any other string — store as the new error message
    """
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
        if last_error is not _SKIP:
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
        status_info = await _status_of(definition)

        # html_hosting can only report "connecting" until something actually
        # calls the API — a present key proves nothing. Probe once here so the
        # UI shows a verified verdict instead of an assumption.
        if skill_id == "html_hosting" and db_skill.enabled and status_info.get("status") == "connecting":
            try:
                from services.html_hosting_service import check_connection
                await check_connection()
                status_info = await _status_of(definition)
            except Exception as probe_exc:
                print(f"[Skills] html_hosting probe failed: {probe_exc}")

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
            status_message=_ungated_note(skill_id, status_message, status.value),
            metadata=metadata
        ))

    return skills


# --- Guardrails (2026-07-05 mass-disable incident) --------------------------
# All 21 skills were disabled by a direct DB write and nobody noticed for two
# days: iMessage replies silently stopped delivering. Any disable now raises
# a visible signal, and bursts / all-disabled states raise the alarm.

MASS_DISABLE_BURST_THRESHOLD = 5
MASS_DISABLE_BURST_WINDOW_MINUTES = 5
MASS_DISABLE_ALERT_COOLDOWN = timedelta(hours=1)

_last_mass_disable_alert: Optional[datetime] = None


async def _push_skills_alert(title: str, body: str) -> None:
    """Best-effort push. push_service works off VAPID env keys alone (it is
    not skill-gated), so this still delivers while skills are down."""
    try:
        from services import push_service
        await push_service.send_push_notification(
            title=title, body=body, url="/settings/room/mind", tag="skills-guard", source="skills_guard",
        )
    except Exception as exc:
        print(f"[SKILLS GUARD] push notification failed: {exc}")


async def _notify_skill_disabled(skill_id: str) -> None:
    print(f"[SKILLS GUARD] Skill '{skill_id}' was disabled")
    await _push_skills_alert(
        title="Edward skill disabled",
        body=f"Skill '{skill_id}' was disabled. If this wasn't you, check Settings → Skills.",
    )


async def _a_skill_was_ever_switched_off() -> bool:
    """True when skills_audit holds an enabled -> disabled change (or can't be
    read, so the alarm errs on the side of speaking)."""
    try:
        async with async_session() as session:
            row = (await session.execute(text(
                "SELECT 1 FROM skills_audit WHERE old_enabled IS TRUE AND new_enabled IS FALSE LIMIT 1"
            ))).first()
        return row is not None
    except Exception:
        return True


async def _check_mass_disable_on_boot() -> None:
    """Alarm when the process boots into the all-skills-disabled state — the
    exact state the 2026-07-05 incident left behind. Alongside the push, a
    system heartbeat event is stored so the warning surfaces in the next chat
    briefing even if push delivery is dead."""
    async with async_session() as session:
        result = await session.execute(select(SkillModel))
        rows = result.scalars().all()

    if not rows or any(r.enabled for r in rows):
        return
    if not await _a_skill_was_ever_switched_off():
        # A fresh install starts with every skill off: that is a setup to
        # finish, not the incident. Only a state something switched off
        # (skills_audit records every change, from any writer) is alarming.
        return

    print(
        f"[SKILLS GUARD] ALL {len(rows)} skills are disabled at startup — Edward is "
        "running with no integrations (iMessage send, search, code execution are "
        "all unavailable). If this wasn't deliberate, re-enable skills in settings."
    )
    await _push_skills_alert(
        title="Edward: all skills disabled",
        body="Every skill is disabled — iMessage replies and other integrations cannot work.",
    )
    try:
        async with async_session() as session:
            existing = await session.execute(
                select(HeartbeatEventModel).where(
                    HeartbeatEventModel.event_type == "skills_mass_disable",
                    HeartbeatEventModel.briefed == False,  # noqa: E712
                )
            )
            if existing.scalars().first() is None:
                session.add(HeartbeatEventModel(
                    source="system",
                    event_type="skills_mass_disable",
                    summary=(
                        f"All {len(rows)} skills are disabled. Edward cannot send "
                        "iMessages, search, or run code until they are re-enabled."
                    ),
                    triage_status="noted",
                    briefed=False,
                ))
                await session.commit()
    except Exception as exc:
        print(f"[SKILLS GUARD] failed to store mass-disable heartbeat event: {exc}")


async def check_mass_disable_burst() -> bool:
    """Alert (once per cooldown) when many skills were disabled within a short
    window — the signature of a scripted mass-disable. Reads `skills_audit`,
    which a Postgres trigger populates for ANY writer, so this also catches
    raw-SQL writes that bypass the app. Called from the triage cycle."""
    global _last_mass_disable_alert

    now = datetime.utcnow()
    if (
        _last_mass_disable_alert is not None
        and now - _last_mass_disable_alert < MASS_DISABLE_ALERT_COOLDOWN
    ):
        return False

    try:
        async with async_session() as session:
            count = (await session.execute(text(
                "SELECT count(*) FROM skills_audit "
                "WHERE new_enabled = false "
                f"AND changed_at > now() - interval '{MASS_DISABLE_BURST_WINDOW_MINUTES} minutes'"
            ))).scalar()
    except Exception as exc:
        print(f"[SKILLS GUARD] mass-disable burst check failed: {exc}")
        return False

    if not count or count < MASS_DISABLE_BURST_THRESHOLD:
        return False

    _last_mass_disable_alert = now
    print(
        f"[SKILLS GUARD] {count} skill disables in the last "
        f"{MASS_DISABLE_BURST_WINDOW_MINUTES} minutes — possible mass-disable event"
    )
    await _push_skills_alert(
        title="Edward: mass skill-disable detected",
        body=(
            f"{count} skills were disabled in the last "
            f"{MASS_DISABLE_BURST_WINDOW_MINUTES} minutes. If this wasn't you, "
            "check Settings → Skills."
        ),
    )
    return True


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

    if not enabled:
        await _notify_skill_disabled(skill_id)

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

    # Mass-disable detector (2026-07-05 incident)
    try:
        await _check_mass_disable_on_boot()
    except Exception as exc:
        print(f"[SKILLS GUARD] boot mass-disable check failed: {exc}")

    # Drive a status sweep so stale last_error / last_connected_at from
    # prior boots gets refreshed against current env. Without this,
    # skills whose env vars have since been configured still show old
    # "X not set" errors in the settings UI.
    try:
        await get_all_skills()
    except Exception as exc:
        print(f"Skills boot refresh failed: {exc}")

    _last_reload = datetime.utcnow()
    print(f"Skills service initialized with {len(SKILL_DEFINITIONS)} skills")
