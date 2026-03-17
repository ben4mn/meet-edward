"""
ToolRegistry: Unified tool management for Edward.

Collects tools from multiple sources (memory, messaging, MCP) and filters
based on skill enabled state from the database.
"""

from typing import List, Any, Dict


# Skill-to-tool mapping
# Maps skill IDs to the tool names they gate
SKILL_TOOL_MAPPING: Dict[str, List[str]] = {
    "twilio_sms": ["send_sms"],
    "twilio_whatsapp": ["send_whatsapp"],
    "imessage_applescript": ["send_imessage", "get_recent_messages"],
    "code_interpreter": ["execute_code", "list_sandbox_files", "read_sandbox_file"],
    "javascript_interpreter": ["execute_javascript", "list_sandbox_files", "read_sandbox_file"],
    "sql_interpreter": [
        "execute_sql", "list_sandbox_files", "read_sandbox_file",
        "create_persistent_db", "query_persistent_db", "list_persistent_dbs", "delete_persistent_db",
    ],
    "shell_interpreter": ["execute_shell", "list_sandbox_files", "read_sandbox_file"],
    "brave_search": ["web_search", "fetch_page_content"],
    "html_hosting": ["create_hosted_page", "update_hosted_page", "delete_hosted_page", "check_hosted_slug"],
    "ios_widget": ["update_widget", "get_widget_state_tool"],
    "contacts_lookup": ["lookup_contact", "lookup_phone"],
    "orchestrator": ["spawn_worker", "check_worker", "list_workers", "cancel_worker", "wait_for_workers", "send_to_worker", "spawn_cc_worker"],
    "notebooklm": [
        # Notebook management
        "nlm_list_notebooks", "nlm_create_notebook", "nlm_delete_notebook",
        "nlm_get_notebook", "nlm_describe_notebook", "nlm_rename_notebook",
        # Source management
        "nlm_add_source", "nlm_list_sources", "nlm_delete_source",
        "nlm_get_source_text", "nlm_add_drive_source", "nlm_rename_source",
        "nlm_describe_source",
        # Chat
        "nlm_ask", "nlm_configure_chat",
        # Research
        "nlm_research", "nlm_poll_research", "nlm_import_research",
        # Artifacts / Studio
        "nlm_generate_artifact", "nlm_wait_artifact",
        "nlm_delete_artifact", "nlm_revise_slides",
        # Sharing
        "nlm_share_status", "nlm_share_public", "nlm_share_invite",
        # Notes
        "nlm_note",
        # Edward bridge tools
        "nlm_push_document", "nlm_push_file",
    ],
    # "whatsapp_mcp" and "apple_services" tools are handled dynamically since they come from MCP
}

# Track initialized state
_initialized = False


async def initialize_registry() -> None:
    """
    Initialize the tool registry.

    Called at startup to ensure the registry is ready before handling requests.
    """
    global _initialized

    # Force a cache refresh on startup
    await _get_skill_states(force_refresh=True)
    _initialized = True
    print("Tool registry initialized")


async def refresh_registry() -> None:
    """
    Refresh the tool registry after skill changes.

    Called when skills are enabled/disabled or reloaded.
    """
    await _get_skill_states(force_refresh=True)
    print("Tool registry refreshed")


# Simple skill state cache with short TTL
_skill_cache: Dict[str, bool] = {}
_cache_timestamp: float = 0
_CACHE_TTL_SECONDS = 5  # Short TTL to keep responsive while avoiding DB spam


async def _get_skill_states(force_refresh: bool = False) -> Dict[str, bool]:
    """
    Get enabled state for all skills.

    Uses a short-lived cache to avoid DB queries on every request.
    """
    global _skill_cache, _cache_timestamp

    import time
    now = time.time()

    if not force_refresh and _skill_cache and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _skill_cache

    from services.skills_service import is_skill_enabled

    _skill_cache = {
        "twilio_sms": await is_skill_enabled("twilio_sms"),
        "twilio_whatsapp": await is_skill_enabled("twilio_whatsapp"),
        "imessage_applescript": await is_skill_enabled("imessage_applescript"),
        "whatsapp_mcp": await is_skill_enabled("whatsapp_mcp"),
        "brave_search": await is_skill_enabled("brave_search"),
        "code_interpreter": await is_skill_enabled("code_interpreter"),
        "javascript_interpreter": await is_skill_enabled("javascript_interpreter"),
        "sql_interpreter": await is_skill_enabled("sql_interpreter"),
        "shell_interpreter": await is_skill_enabled("shell_interpreter"),
        "contacts_lookup": await is_skill_enabled("contacts_lookup"),
        "push_notifications": await is_skill_enabled("push_notifications"),
        "apple_services": await is_skill_enabled("apple_services"),
        "html_hosting": await is_skill_enabled("html_hosting"),
        "ios_widget": await is_skill_enabled("ios_widget"),
        "orchestrator": await is_skill_enabled("orchestrator"),
        "notebooklm": await is_skill_enabled("notebooklm"),
    }
    _cache_timestamp = now

    return _skill_cache


def _get_memory_tools() -> List[Any]:
    """Get memory tools (always available)."""
    from services.graph.tools import MEMORY_TOOLS
    return MEMORY_TOOLS


def _get_document_tools() -> List[Any]:
    """Get document tools (always available)."""
    from services.graph.tools import DOCUMENT_TOOLS
    return DOCUMENT_TOOLS


def _get_file_storage_tools() -> List[Any]:
    """Get file storage tools (always available)."""
    from services.graph.tools import FILE_STORAGE_TOOLS
    return FILE_STORAGE_TOOLS


def _get_plan_tools() -> List[Any]:
    """Get plan tools (always available)."""
    from services.graph.tools import PLAN_TOOLS
    return PLAN_TOOLS


def _get_scheduled_event_tools() -> List[Any]:
    """Get scheduled event tools (always available)."""
    from services.graph.tools import SCHEDULED_EVENT_TOOLS
    return SCHEDULED_EVENT_TOOLS


def _get_heartbeat_tools() -> List[Any]:
    """Get heartbeat tools (always available)."""
    from services.graph.tools import HEARTBEAT_TOOLS
    return HEARTBEAT_TOOLS


async def _get_push_notification_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get push notification tools (available when skill enabled and configured)."""
    if not skill_states.get("push_notifications"):
        return []

    from services.push_service import is_configured
    if not is_configured():
        return []

    from services.graph.tools import PUSH_NOTIFICATION_TOOLS
    return PUSH_NOTIFICATION_TOOLS


def _get_messaging_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """
    Get messaging tools filtered by skill enabled state.

    Args:
        skill_states: Dict of skill_id -> enabled

    Returns:
        List of enabled messaging tools
    """
    from services.graph.tools import (
        send_sms,
        send_whatsapp,
        send_imessage,
        get_recent_messages,
        send_message,
    )

    tools = []

    # send_sms: gated by twilio_sms
    if skill_states.get("twilio_sms"):
        tools.append(send_sms)

    # send_whatsapp: gated by twilio_whatsapp
    if skill_states.get("twilio_whatsapp"):
        tools.append(send_whatsapp)

    # send_imessage, get_recent_messages: gated by imessage_applescript
    if skill_states.get("imessage_applescript"):
        tools.append(send_imessage)
        tools.append(get_recent_messages)

    # send_message: available if ANY messaging skill is enabled
    any_messaging_enabled = (
        skill_states.get("twilio_sms") or
        skill_states.get("twilio_whatsapp") or
        skill_states.get("imessage_applescript")
    )
    if any_messaging_enabled:
        tools.append(send_message)

    return tools


def _get_whatsapp_mcp_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """
    Get WhatsApp bridge tools if whatsapp_mcp is enabled.

    Uses the custom Baileys bridge REST API instead of MCP tools.
    """
    if not skill_states.get("whatsapp_mcp"):
        return []

    from services.whatsapp_bridge_client import is_available

    if not is_available():
        return []

    from services.whatsapp_bridge_tools import WHATSAPP_BRIDGE_TOOLS
    return WHATSAPP_BRIDGE_TOOLS


def _get_search_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """
    Get search tools if brave_search is enabled.

    Args:
        skill_states: Dict of skill_id -> enabled

    Returns:
        List of search tools
    """
    if not skill_states.get("brave_search"):
        return []

    from services.graph.tools import web_search, fetch_page_content

    return [web_search, fetch_page_content]


def _get_html_hosting_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get HTML hosting tools if html_hosting is enabled."""
    if not skill_states.get("html_hosting"):
        return []

    from services.graph.tools import HTML_HOSTING_TOOLS
    return HTML_HOSTING_TOOLS


def _get_widget_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get iOS widget tools if ios_widget is enabled."""
    if not skill_states.get("ios_widget"):
        return []

    from services.graph.tools import WIDGET_TOOLS
    return WIDGET_TOOLS


def _get_code_execution_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """
    Get code execution tools if code_interpreter is enabled.

    Args:
        skill_states: Dict of skill_id -> enabled

    Returns:
        List of code execution tools
    """
    if not skill_states.get("code_interpreter"):
        return []

    from services.graph.tools import CODE_EXECUTION_TOOLS
    return CODE_EXECUTION_TOOLS


def _get_javascript_execution_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get JavaScript execution tools if javascript_interpreter is enabled."""
    if not skill_states.get("javascript_interpreter"):
        return []

    from services.graph.tools import JAVASCRIPT_EXECUTION_TOOLS
    return JAVASCRIPT_EXECUTION_TOOLS


def _get_sql_execution_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get SQL execution tools if sql_interpreter is enabled."""
    if not skill_states.get("sql_interpreter"):
        return []

    from services.graph.tools import SQL_EXECUTION_TOOLS, PERSISTENT_DB_TOOLS
    return SQL_EXECUTION_TOOLS + PERSISTENT_DB_TOOLS


def _get_shell_execution_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get shell execution tools if shell_interpreter is enabled."""
    if not skill_states.get("shell_interpreter"):
        return []

    from services.graph.tools import SHELL_EXECUTION_TOOLS
    return SHELL_EXECUTION_TOOLS


def _get_contacts_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get contacts tools if contacts_lookup is enabled."""
    if not skill_states.get("contacts_lookup"):
        return []

    from services.graph.tools import CONTACTS_TOOLS
    return CONTACTS_TOOLS


def _get_apple_mcp_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """
    Get Apple Services MCP tools if apple_services is enabled.

    Provides access to Calendar, Reminders, Notes, Mail, Contacts, Maps.

    Args:
        skill_states: Dict of skill_id -> enabled

    Returns:
        List of Apple Services MCP tools (LangChain-compatible)
    """
    if not skill_states.get("apple_services"):
        return []

    from services.mcp_client import get_apple_mcp_tools, is_apple_available

    if not is_apple_available():
        return []

    return get_apple_mcp_tools()


def _get_orchestrator_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get orchestrator tools if orchestrator skill is enabled."""
    if not skill_states.get("orchestrator"):
        return []

    from services.graph.tools import ORCHESTRATOR_TOOLS
    return ORCHESTRATOR_TOOLS


def _get_notebooklm_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get NotebookLM tools if notebooklm skill is enabled."""
    if not skill_states.get("notebooklm"):
        return []

    from services.graph.tools import NOTEBOOKLM_TOOLS
    return NOTEBOOKLM_TOOLS


def _get_custom_mcp_tools() -> List[Any]:
    """Get tools from all running custom MCP servers."""
    try:
        from services.custom_mcp_service import get_all_custom_tools
        return get_all_custom_tools()
    except Exception:
        return []


def _get_custom_mcp_self_service_tools() -> List[Any]:
    """Get the LLM tools for managing custom MCP servers (always available)."""
    from services.custom_mcp_tools import CUSTOM_MCP_TOOLS
    return CUSTOM_MCP_TOOLS


async def get_available_tools() -> List[Any]:
    """
    Get all tools that are currently available based on skill state.

    Returns:
        List of tools filtered by enabled skills.
        Memory tools are always included.
    """
    skill_states = await _get_skill_states()

    tools = []
    seen_names = set()

    def add_tools(new_tools: List[Any]) -> None:
        """Add tools, deduplicating shared tools like list_sandbox_files."""
        for tool in new_tools:
            if tool.name not in seen_names:
                tools.append(tool)
                seen_names.add(tool.name)

    # Memory tools are always available
    add_tools(_get_memory_tools())

    # Document tools are always available
    add_tools(_get_document_tools())

    # File storage tools are always available
    add_tools(_get_file_storage_tools())

    # Plan tools are always available
    tools.extend(_get_plan_tools())

    # Scheduled event tools are always available
    add_tools(_get_scheduled_event_tools())

    # Heartbeat tools are always available
    add_tools(_get_heartbeat_tools())

    # Push notification tools (available when skill enabled and VAPID keys configured)
    add_tools(await _get_push_notification_tools(skill_states))

    # Add enabled messaging tools
    add_tools(_get_messaging_tools(skill_states))

    # Add WhatsApp MCP tools if enabled
    add_tools(_get_whatsapp_mcp_tools(skill_states))

    # Add Apple Services MCP tools if enabled
    add_tools(_get_apple_mcp_tools(skill_states))

    # Add search tools if enabled
    add_tools(_get_search_tools(skill_states))

    # Add HTML hosting tools if enabled
    add_tools(_get_html_hosting_tools(skill_states))

    # Add iOS widget tools if enabled
    add_tools(_get_widget_tools(skill_states))

    # Add contacts tools if enabled
    add_tools(_get_contacts_tools(skill_states))

    # Add execution tools if enabled
    add_tools(_get_code_execution_tools(skill_states))
    add_tools(_get_javascript_execution_tools(skill_states))
    add_tools(_get_sql_execution_tools(skill_states))
    add_tools(_get_shell_execution_tools(skill_states))

    # Evolution tools are always available
    from services.graph.tools import EVOLUTION_TOOLS
    add_tools(EVOLUTION_TOOLS)

    # Orchestrator tools if enabled
    add_tools(_get_orchestrator_tools(skill_states))

    # NotebookLM tools if enabled
    add_tools(_get_notebooklm_tools(skill_states))

    # Custom MCP self-service tools (always available)
    add_tools(_get_custom_mcp_self_service_tools())

    # Tools from custom MCP servers Edward has added
    add_tools(_get_custom_mcp_tools())

    return tools


def get_tool_descriptions(tools: List[Any]) -> str:
    """
    Generate system prompt section describing available tools.

    Args:
        tools: List of available tools

    Returns:
        Formatted string for system prompt
    """
    from services.graph.tools import (
        get_memory_tools_description,
        get_document_tools_description,
        get_file_storage_tools_description,
        get_plan_tools_description,
        get_scheduled_event_tools_description,
        get_contacts_tools_description,
        get_messaging_tools_description,
        get_search_tools_description,
        get_code_execution_tools_description,
        get_javascript_execution_tools_description,
        get_sql_execution_tools_description,
        get_shell_execution_tools_description,
        get_push_notification_tools_description,
        get_html_hosting_tools_description,
        get_widget_tools_description,
        get_heartbeat_tools_description,
    )

    # Get tool names for filtering
    tool_names = {t.name for t in tools}

    sections = []

    # Memory tools section (always included since memory tools always available)
    if any(name in tool_names for name in ["remember_update", "remember_forget", "remember_search"]):
        sections.append(get_memory_tools_description())

    # Document tools section (always included)
    if any(name in tool_names for name in ["save_document", "read_document", "edit_document", "search_documents", "list_documents", "delete_document"]):
        sections.append(get_document_tools_description())

    # File storage tools section (always included)
    if any(name in tool_names for name in ["save_to_storage", "list_storage_files", "get_storage_file_url", "read_storage_file", "delete_storage_file", "tag_storage_file"]):
        sections.append(get_file_storage_tools_description())

    # Plan tools section (always included)
    if any(name in tool_names for name in ["create_plan", "update_plan_step", "edit_plan", "complete_plan"]):
        sections.append(get_plan_tools_description())

    # Scheduled event tools section (always included)
    if any(name in tool_names for name in ["schedule_event", "list_scheduled_events", "cancel_scheduled_event"]):
        sections.append(get_scheduled_event_tools_description())

    # Heartbeat tools section (always included)
    if "review_heartbeat" in tool_names:
        sections.append(get_heartbeat_tools_description())

    # Messaging tools section
    messaging_tools = ["send_sms", "send_whatsapp", "send_imessage", "get_recent_messages", "send_message"]
    if any(name in tool_names for name in messaging_tools):
        sections.append(get_messaging_tools_description())

    # Search tools section
    if any(name in tool_names for name in ["web_search", "fetch_page_content"]):
        sections.append(get_search_tools_description())

    # Contacts tools section
    if any(name in tool_names for name in ["lookup_contact", "lookup_phone"]):
        sections.append(get_contacts_tools_description())

    # Code execution tools section
    if "execute_code" in tool_names:
        sections.append(get_code_execution_tools_description())

    # JavaScript execution tools section
    if "execute_javascript" in tool_names:
        sections.append(get_javascript_execution_tools_description())

    # SQL execution tools section
    if "execute_sql" in tool_names:
        sections.append(get_sql_execution_tools_description())

    # Shell execution tools section
    if "execute_shell" in tool_names:
        sections.append(get_shell_execution_tools_description())

    # Push notification tools section
    if "send_push_notification" in tool_names:
        sections.append(get_push_notification_tools_description())

    # HTML hosting tools section
    if "create_hosted_page" in tool_names:
        sections.append(get_html_hosting_tools_description())

    # iOS widget tools section
    if "update_widget" in tool_names:
        sections.append(get_widget_tools_description())

    # Evolution tools section
    if "trigger_self_evolution" in tool_names:
        from services.graph.tools import get_evolution_tools_description
        sections.append(get_evolution_tools_description())

    # Orchestrator tools section
    if "spawn_worker" in tool_names:
        from services.graph.tools import get_orchestrator_tools_description
        sections.append(get_orchestrator_tools_description())

    # NotebookLM tools section
    if any(name.startswith("nlm_") for name in tool_names):
        from services.graph.tools import get_notebooklm_tools_description
        sections.append(get_notebooklm_tools_description())

    # GitHub MCP tools section (guardrail for write actions)
    # Sentinel: github-mcp-server always exposes "get_me" (prefixed as "github_get_me" by custom MCP)
    if "get_me" in tool_names or "github_get_me" in tool_names:
        sections.append(_get_github_mcp_description())

    # Apple Reminders tools section (special guidance to avoid confusion with scheduled events)
    if any(name.startswith("reminders_") for name in tool_names):
        sections.append(_get_apple_reminders_description())

    # Custom MCP self-service tools section
    if any(name in tool_names for name in ["search_mcp_servers", "add_mcp_server", "list_custom_servers", "remove_mcp_server", "update_mcp_server", "restart_mcp_server"]):
        sections.append(_get_custom_mcp_description())

    # MCP tools use their own descriptions from the MCP server

    return "\n".join(sections)


def _get_github_mcp_description() -> str:
    """Write-confirmation guardrail for GitHub MCP tools."""
    return """## GitHub (Read + Soft Write)

You have access to GitHub tools for reading repositories, files, issues, and pull
requests, and for creating issues and comments.

IMPORTANT — Confirmation required before any write action:
Before calling any tool that creates, modifies, or closes a GitHub resource
(creating an issue, posting a comment, opening or updating a PR, etc.), you MUST
first describe exactly what you are about to do — including repo, resource type,
and content — and wait for the user to explicitly confirm before calling the tool.
Example: "I'm about to open an issue titled 'X' in org/repo. Shall I proceed?"

Read-only tools (list_*, get_*, search_*) do not require confirmation."""


def _get_apple_reminders_description() -> str:
    """Get special guidance for Apple Reminders tools."""
    return """## Apple Reminders (User's Personal System)

IMPORTANT: These are the USER'S personal Apple Reminders - NOT for Edward's tracking.

ONLY use reminders_ tools when user explicitly asks:
- "Add to my reminders"
- "Check my reminders list"
- "Show my Apple Reminders"
- "Create a reminder in Apple Reminders"

DO NOT use for:
- Edward's scheduled events (use schedule_event instead)
- Internal task tracking
- Anything not explicitly requested by the user

When the user asks to "remind me" without specifying Apple Reminders, use schedule_event instead."""


def _get_custom_mcp_description() -> str:
    """Get description for custom MCP self-service tools."""
    return """## Custom MCP Servers (Self-Service)

You can discover and install MCP servers to extend your own capabilities at runtime.
No restart required — new tools become available immediately.

- `search_mcp_servers` — Search GitHub for MCP server packages
- `add_mcp_server` — Install and start a new MCP server (npx or uvx)
- `list_custom_servers` — List servers you've added with their status and config
- `update_mcp_server` — Update a server's env vars, args, or description (auto-restarts if running)
- `restart_mcp_server` — Restart a server (useful for error recovery)
- `remove_mcp_server` — Stop and remove a server

Use "npx" runtime for Node.js/TypeScript packages, "uvx" for Python packages, and "binary" for pre-installed native binaries already on PATH (package_name becomes the command directly, no package manager involved).
Environment variables can be passed as a JSON object to configure servers that need API keys.
To update env vars on an existing server, use update_mcp_server — env vars merge by default (set a key to "" to remove it)."""


async def get_worker_tools() -> List[Any]:
    """
    Get tools available to worker agents.

    Workers get all normal tools EXCEPT evolution and orchestrator tools
    (prevents workers from spawning sub-workers or self-evolving).
    """
    from services.graph.tools import EVOLUTION_TOOL_NAMES, ORCHESTRATOR_TOOL_NAMES

    excluded = EVOLUTION_TOOL_NAMES | ORCHESTRATOR_TOOL_NAMES
    all_tools = await get_available_tools()
    return [t for t in all_tools if t.name not in excluded]


async def is_any_messaging_enabled() -> bool:
    """
    Check if any messaging skill is enabled.

    Used by send_message to determine if it should be available.
    """
    skill_states = await _get_skill_states()
    return (
        skill_states.get("twilio_sms") or
        skill_states.get("twilio_whatsapp") or
        skill_states.get("imessage_applescript") or
        skill_states.get("whatsapp_mcp")
    )


