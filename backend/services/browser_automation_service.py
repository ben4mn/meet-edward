"""
Browser Automation Service for Edward.

Thin shim that re-exports everything from the refactored execution package.
All logic lives in services.execution.browser_execution.
"""

from services.execution.browser_execution import (
    BROWSER_LIMITS,
    navigate,
    click,
    type_text,
    extract_text,
    screenshot,
    close_context,
    shutdown_browser,
    is_available,
    get_status,
)

__all__ = [
    "BROWSER_LIMITS",
    "navigate",
    "click",
    "type_text",
    "extract_text",
    "screenshot",
    "close_context",
    "shutdown_browser",
    "is_available",
    "get_status",
]
