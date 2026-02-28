"""
Code Execution Service for Edward.

Thin shim that re-exports everything from the refactored execution package.
All logic now lives in services.execution.python_execution and services.execution.base.
"""

# Re-export everything from the new locations for backwards compatibility
from services.execution.base import (
    EXECUTION_LIMITS,
    SANDBOX_BASE_DIR,
    ExecutionResult,
    cleanup_old_sandboxes,
    list_sandbox_files,
    read_sandbox_file,
)
from services.execution.python_execution import (
    AVAILABLE_PACKAGES,
    BLOCKED_MODULES,
    execute_code,
    get_status,
    is_available,
)

__all__ = [
    "EXECUTION_LIMITS",
    "SANDBOX_BASE_DIR",
    "ExecutionResult",
    "AVAILABLE_PACKAGES",
    "BLOCKED_MODULES",
    "execute_code",
    "is_available",
    "get_status",
    "list_sandbox_files",
    "read_sandbox_file",
    "cleanup_old_sandboxes",
]
