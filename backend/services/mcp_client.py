"""
MCP (Model Context Protocol) client for messaging integrations.

Manages MCP server subprocesses for Apple Services.
WhatsApp is now handled by the Baileys bridge (whatsapp_bridge_client.py).
Uses langchain-mcp-adapters for seamless LangChain tool integration.
"""

import os
from typing import Optional, List, Any

# MCP configuration — Apple Services (unified: Calendar, Reminders, Notes, Mail, Contacts, Maps, Messages)
MCP_APPLE_ENABLED = os.getenv("MCP_APPLE_ENABLED", "false").lower() == "true"

# Global client state — Apple Services (unified)
_apple_mcp_client = None
_apple_mcp_tools: List[Any] = []
_apple_initialized = False
_apple_last_error: Optional[str] = None
_apple_session = None
_apple_session_context = None


# ============================================================================
# WHATSAPP — delegated to whatsapp_bridge_client.py
# ============================================================================

async def initialize_whatsapp_mcp() -> bool:
    """Initialize WhatsApp via Baileys bridge (not MCP anymore)."""
    from services.whatsapp_bridge_client import initialize_bridge
    return await initialize_bridge()


async def shutdown_whatsapp_mcp():
    """Shutdown WhatsApp bridge."""
    from services.whatsapp_bridge_client import shutdown_bridge
    await shutdown_bridge()


def is_whatsapp_available() -> bool:
    """Check if WhatsApp bridge is available."""
    from services.whatsapp_bridge_client import is_available
    return is_available()


def get_whatsapp_mcp_tools() -> List[Any]:
    """No longer used — tools come from whatsapp_bridge_tools.py."""
    return []


def get_whatsapp_status() -> dict:
    """Get WhatsApp bridge status."""
    from services.whatsapp_bridge_client import get_status
    return get_status()


# ============================================================================
# APPLE SERVICES MCP (unified: Calendar, Reminders, Notes, Mail, Contacts, Maps, Messages)
# ============================================================================

async def initialize_apple_mcp() -> bool:
    """
    Initialize the unified Apple Services MCP client with a persistent session.

    Uses the 'apple-mcp' package which provides access to:
    - Calendar
    - Reminders
    - Notes
    - Mail (Apple Mail.app)
    - Contacts
    - Maps

    Returns:
        True if initialization succeeded, False otherwise.
    """
    global _apple_mcp_client, _apple_mcp_tools, _apple_initialized
    global _apple_last_error, _apple_session, _apple_session_context

    if not MCP_APPLE_ENABLED:
        _apple_last_error = "Disabled via MCP_APPLE_ENABLED=false"
        return False

    if _apple_initialized:
        return True

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools

        # Use jxnl/apple-mcp fork with working calendar code (requires bun)
        bun_path = os.path.expanduser("~/.bun/bin/bun")
        apple_mcp_dir = os.path.expanduser("~/apple-mcp")
        _apple_mcp_client = MultiServerMCPClient({
            "apple": {
                "command": bun_path,
                "args": ["run", "index.ts"],
                "transport": "stdio",
                "cwd": apple_mcp_dir
            }
        })

        # Open a persistent session
        _apple_session_context = _apple_mcp_client.session("apple")
        _apple_session = await _apple_session_context.__aenter__()

        # Load tools bound to persistent session
        _apple_mcp_tools = await load_mcp_tools(_apple_session)
        _apple_initialized = True
        _apple_last_error = None

        print(f"Apple Services MCP client initialized with {len(_apple_mcp_tools)} tools")
        for tool in _apple_mcp_tools:
            desc = tool.description[:50] if tool.description else "No description"
            print(f"  - {tool.name}: {desc}...")

        return True

    except ImportError:
        _apple_last_error = "langchain-mcp-adapters not installed"
        print("langchain-mcp-adapters not installed. Apple Services MCP disabled.")
        return False
    except Exception as e:
        _apple_last_error = str(e)
        print(f"Failed to initialize Apple Services MCP client: {e}")
        _apple_mcp_client = None
        _apple_session = None
        _apple_session_context = None
        return False


async def shutdown_apple_mcp():
    """Shutdown the Apple Services MCP client and its persistent session."""
    global _apple_mcp_client, _apple_mcp_tools, _apple_initialized
    global _apple_session, _apple_session_context

    if _apple_session_context is not None:
        try:
            await _apple_session_context.__aexit__(None, None, None)
        except Exception as e:
            print(f"Error shutting down Apple Services MCP session: {e}")
        finally:
            _apple_session = None
            _apple_session_context = None

    _apple_mcp_client = None
    _apple_mcp_tools = []
    _apple_initialized = False


def is_apple_available() -> bool:
    """Check if Apple Services MCP is available."""
    return _apple_initialized and len(_apple_mcp_tools) > 0


def get_apple_mcp_tools() -> List[Any]:
    """Get the Apple Services MCP tools for binding to the LLM."""
    return _apple_mcp_tools


def get_apple_status() -> dict:
    """Get the current status of the Apple Services MCP."""
    if not MCP_APPLE_ENABLED:
        return {
            "status": "error",
            "status_message": "Set MCP_APPLE_ENABLED=true in environment",
            "metadata": None
        }

    if not _apple_initialized:
        if _apple_last_error:
            return {
                "status": "error",
                "status_message": _apple_last_error,
                "metadata": None
            }
        return {
            "status": "connecting",
            "status_message": "Not yet initialized",
            "metadata": None
        }

    if len(_apple_mcp_tools) == 0:
        return {
            "status": "error",
            "status_message": "No tools available from Apple Services MCP server",
            "metadata": None
        }

    return {
        "status": "connected",
        "status_message": f"{len(_apple_mcp_tools)} tools available",
        "metadata": {"tools_count": len(_apple_mcp_tools)}
    }


# Compatibility aliases for old function names
async def initialize_calendar_mcp() -> bool:
    return await initialize_apple_mcp()

async def initialize_notes_mcp() -> bool:
    return await initialize_apple_mcp()

async def initialize_reminders_mcp() -> bool:
    return await initialize_apple_mcp()

async def initialize_mail_mcp() -> bool:
    return await initialize_apple_mcp()

async def shutdown_calendar_mcp():
    pass  # Handled by shutdown_apple_mcp

async def shutdown_notes_mcp():
    pass  # Handled by shutdown_apple_mcp

async def shutdown_reminders_mcp():
    pass  # Handled by shutdown_apple_mcp

async def shutdown_mail_mcp():
    pass  # Handled by shutdown_apple_mcp

def get_calendar_status() -> dict:
    return get_apple_status()

def get_notes_status() -> dict:
    return get_apple_status()

def get_reminders_status() -> dict:
    return get_apple_status()

def get_mail_status() -> dict:
    return get_apple_status()

def is_calendar_available() -> bool:
    return is_apple_available()

def is_notes_available() -> bool:
    return is_apple_available()

def is_reminders_available() -> bool:
    return is_apple_available()

def is_mail_available() -> bool:
    return is_apple_available()

def get_calendar_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()

def get_notes_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()

def get_reminders_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()

def get_mail_mcp_tools() -> List[Any]:
    return get_apple_mcp_tools()
