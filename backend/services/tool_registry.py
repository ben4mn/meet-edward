"""
ToolRegistry: which tools a turn is given.

Under the v2 toolset (default; ``EDWARD_TOOLSET`` unset or ``v2``, read per
call) every turn on every surface gets the same 14 tools in one order
(``services/toolset``; docs/architecture/tool-loop.md), plus the tools of
custom MCP servers the owner installed. What a turn may do with them is its
``TurnPolicy``, enforced by the executor and the tools, not a shorter list.

``EDWARD_TOOLSET=legacy`` restores the old registry — the families still
defined in ``services.graph.tools``, gated by skills — minus the families
retired with the toolset (code execution, hosted pages, storage, mail,
workers and Codex, custom-MCP self-service, Alexa), minus the
apple-mcp and WhatsApp MCP tools (their sends bypassed the send guard) and
minus the keyword gating that hid tools (the 08-25 missing-tool incident;
docs/architecture/tool-loop.md). Callers' ``allowed_tools`` / ``excluded_tools`` apply in legacy only.
"""

import asyncio
import logging
from typing import List, Any, Dict, Optional
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


# Skill-to-tool mapping: the tool names each skill gates. Under v2 six
# toggles gate the toolset (``services/toolset/skills.py``); the legacy
# names are what ``EDWARD_TOOLSET=legacy`` still binds.
SKILL_TOOL_MAPPING: Dict[str, List[str]] = {
    "imessage_applescript": ["messages", "send_message", "send_imessage", "get_recent_messages",
                             "read_imessage_thread", "list_chat_participants", "get_imessage_attachment"],
    "shell_interpreter": ["bash", "str_replace_based_edit_tool"],
    "brave_search": ["web_search", "web_fetch", "fetch_page_content"],
    "ios_widget": ["update_widget", "get_widget_state_tool"],
    "push_notifications": ["send_push_notification"],
    "contacts_lookup": ["lookup_contact", "lookup_phone"],
    "twilio_sms": ["send_sms"],
    "twilio_whatsapp": ["send_whatsapp"],
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


# Skill state cache. Skill toggles call refresh_registry() explicitly, so the
# TTL is just a safety net — 30s keeps cache misses infrequent in steady state.
_skill_cache: Dict[str, bool] = {}
_cache_timestamp: float = 0
_CACHE_TTL_SECONDS = 30

_SKILL_IDS = (
    "twilio_sms",
    "twilio_whatsapp",
    "imessage_applescript",
    "whatsapp_mcp",
    "brave_search",
    "code_interpreter",
    "javascript_interpreter",
    "sql_interpreter",
    "shell_interpreter",
    "contacts_lookup",
    "push_notifications",
    "apple_services",
    "html_hosting",
    "ios_widget",
    "orchestrator",
    "alexa_outbound",
    "claude_session",
)


async def _get_skill_states(force_refresh: bool = False) -> Dict[str, bool]:
    """
    Get enabled state for all skills.

    Uses a short-lived cache to avoid DB queries on every request.
    On cache miss, all skill lookups are issued in parallel via asyncio.gather.
    """
    global _skill_cache, _cache_timestamp

    import time
    now = time.time()

    if not force_refresh and _skill_cache and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _skill_cache

    from services.skills_service import is_skill_enabled

    results = await asyncio.gather(*(is_skill_enabled(skill_id) for skill_id in _SKILL_IDS))
    _skill_cache = dict(zip(_SKILL_IDS, results))
    _cache_timestamp = now

    return _skill_cache


def _family(name: str) -> List[Any]:
    """A tool family still defined in ``services.graph.tools``, or [] once
    it is gone (retired families are deleted from there)."""
    from services.graph import tools as tools_mod

    return list(getattr(tools_mod, name, None) or [])


def _named(*names: str) -> List[Any]:
    from services.graph import tools as tools_mod

    return [t for t in (getattr(tools_mod, n, None) for n in names) if t is not None]


async def _get_push_notification_tools(skill_states: Dict[str, bool]) -> List[BaseTool]:
    """Legacy: push tools when the skill is on and VAPID is configured."""
    if not skill_states.get("push_notifications"):
        return []
    try:
        from services.push_service import is_configured
        if not is_configured():
            return []
    except Exception:  # noqa: BLE001
        return []
    return _family("PUSH_NOTIFICATION_TOOLS")


def _get_messaging_tools(skill_states: Dict[str, bool]) -> List[BaseTool]:
    """Legacy: the send and read tools their skills allow (no MCP sends)."""
    tools: List[Any] = []
    if skill_states.get("twilio_sms"):
        tools += _named("send_sms")
    if skill_states.get("twilio_whatsapp"):
        tools += _named("send_whatsapp")
    if skill_states.get("imessage_applescript"):
        tools += _named("send_imessage", "get_recent_messages", "read_imessage_thread", "list_chat_participants",
                        "get_imessage_attachment", "stand_down")
    if skill_states.get("twilio_sms") or skill_states.get("twilio_whatsapp") or skill_states.get("imessage_applescript"):
        tools += _named("send_message")
    return tools


def _tag_custom_mcp(tool: Any, server: str) -> Optional[Any]:
    """``metadata.custom_mcp`` (its server), ``tier: outward`` and the MCP
    call bound as its deadline. Nothing can tell what a custom tool does, so
    the rails treat every call as outward (``toolset.names.CUSTOM_MCP_TOOLS``).
    None when the tag doesn't hold: untagged, the tool would run with none
    of the rails, so it is not bound at all."""
    from services.mcp_session_holder import mcp_call_timeout_seconds
    from services.toolset.names import CUSTOM_MCP_KEY

    try:
        md = dict(getattr(tool, "metadata", None) or {})
        md.update({CUSTOM_MCP_KEY: server, "tier": "outward", "timeout_s": mcp_call_timeout_seconds()})
        tool.metadata = md
        kept = getattr(tool, "metadata", None)
        if isinstance(kept, dict) and kept.get(CUSTOM_MCP_KEY) == server:
            return tool
    except Exception:  # noqa: BLE001 — a frozen tool can't be tagged
        pass
    logger.warning("custom MCP tool %s (%s) could not be tagged; not bound",
                   getattr(tool, "name", "?"), server)
    return None


def _get_custom_mcp_tools() -> List[Any]:
    """Tools from the custom MCP servers the owner installed (opt-in; their
    environment is an allowlist), each tagged ``custom_mcp``: every string
    argument egress-scanned (``secret_scan.egress_args``). One that can't
    be tagged is left out."""
    try:
        from services.custom_mcp_service import get_all_custom_tools_by_server
        tagged = (_tag_custom_mcp(t, server) for server, t in get_all_custom_tools_by_server())
        return [t for t in tagged if t is not None]
    except Exception:
        return []


def _legacy_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """``EDWARD_TOOLSET=legacy``: today's families (sync part)."""
    tools: List[Any] = []
    tools += _family("MEMORY_TOOLS")
    tools += _family("DOCUMENT_TOOLS")
    tools += _family("PLAN_TOOLS")
    tools += _family("SCHEDULED_EVENT_TOOLS")
    tools += _family("HEARTBEAT_TOOLS")
    tools += _get_messaging_tools(skill_states)
    if skill_states.get("brave_search"):
        tools += _named("web_search", "fetch_page_content")
    if skill_states.get("ios_widget"):
        tools += _family("WIDGET_TOOLS")
    if skill_states.get("contacts_lookup"):
        tools += _family("CONTACTS_TOOLS")
    tools += _family("SCREEN_TOOLS")
    tools += _family("BROWSER_TOOLS")
    tools += _family("WISHLIST_TOOLS")
    tools += _family("MIND_EXTRA_TOOLS")
    tools += _family("PRODUCT_TOOLS")
    return tools


async def get_available_tools(message: Optional[str] = None) -> List[Any]:
    """The tools for a turn. ``message`` is accepted for old callers and
    ignored: nothing is gated on the words of the message any more.

    v2: the toolset (``services.toolset.build_toolset``), the same on every
    surface, plus owner-installed custom MCP tools. Legacy: see the module
    docstring."""
    skill_states = await _get_skill_states()
    from services.toolset import build_toolset, is_v2

    if is_v2():
        tools = list(build_toolset(skill_states))
    else:
        tools = _legacy_tools(skill_states)
        tools += await _get_push_notification_tools(skill_states)
    out: List[Any] = []
    names_out: set = set()
    for t in tools:
        if t.name not in names_out:
            out.append(t)
            names_out.add(t.name)
    from services.toolset.names import note_custom_mcp

    for t in _get_custom_mcp_tools():
        if t.name not in names_out:
            out.append(t)
            names_out.add(t.name)
            # Counted only when bound: one named like a tool above is dropped.
            note_custom_mcp(t)
    return out


def get_tool_descriptions(tools: List[Any]) -> str:
    """
    The tool manual for the system prompt. Under v2 it is the static
    ``TOOL_MANUAL`` (``services/toolset/manual.py``), the same bytes on every surface; in
    legacy, the old per-family sections for the bound tools.
    """
    from services.toolset import is_v2

    if is_v2():
        from services.toolset.manual import TOOL_MANUAL

        return TOOL_MANUAL
    return _legacy_tool_descriptions(tools)


def _legacy_tool_descriptions(tools: List[Any]) -> str:
    from services.graph import tools as tools_mod

    def _desc(name: str):
        fn = getattr(tools_mod, name, None)
        return fn if callable(fn) else (lambda *a, **k: "")

    tool_names = {t.name for t in tools}
    sections = []
    for marker, fn in (
        ({"remember", "remember_update", "close_intention", "remember_search"}, "get_memory_tools_description"),
        ({"save_document", "read_document", "edit_document", "search_documents"}, "get_document_tools_description"),
        ({"create_plan", "update_plan_step", "edit_plan", "complete_plan"}, "get_plan_tools_description"),
        ({"schedule_event", "list_scheduled_events", "cancel_scheduled_event"}, "get_scheduled_event_tools_description"),
        ({"review_heartbeat"}, "get_heartbeat_tools_description"),
        ({"send_sms", "send_whatsapp", "send_imessage", "get_recent_messages", "send_message"},
         "get_messaging_tools_description"),
        ({"web_search", "fetch_page_content", "browser_open"}, "get_web_tools_description"),
        ({"lookup_contact", "lookup_phone"}, "get_contacts_tools_description"),
        ({"send_push_notification"}, "get_push_notification_tools_description"),
        ({"update_widget"}, "get_widget_tools_description"),
        ({"leave_note", "wake_me"}, "get_mind_tools_description"),
        ({"wish_for_feature"}, "get_wishlist_tools_description"),
    ):
        if tool_names & marker:
            text = _desc(fn)()
            if text:
                sections.append(text)
    product = getattr(tools_mod, "PRODUCT_TOOL_NAMES", set())
    if tool_names & set(product):
        text = _desc("get_product_tools_description")(tool_names)
        if text:
            sections.append(text)
    return "\n".join(sections)


async def get_worker_tools() -> List[Any]:
    """The tools of a worker turn. v2: the same toolset as every other
    turn (``forget`` refuses there by policy). Legacy: the registry minus
    ``forget_about``."""
    from services.toolset import is_v2

    tools = await get_available_tools()
    if is_v2():
        return tools
    return [t for t in tools if t.name != "forget_about"]


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
