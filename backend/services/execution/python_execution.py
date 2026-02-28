"""
Python code execution service.

Provides sandboxed Python code execution with:
- Subprocess isolation with resource limits
- Per-conversation working directories
- Timeout enforcement
- Output capture and truncation
- Security via blocked dangerous modules
"""

import os
import sys
from typing import Optional

from services.execution.base import (
    EXECUTION_LIMITS,
    ExecutionResult,
    _get_sandbox_dir,
    run_subprocess,
)


# Pre-installed packages (these should be available in the Python environment)
AVAILABLE_PACKAGES = [
    "numpy", "pandas", "matplotlib",  # Data science
    "requests", "pillow",             # Web/images
    "json", "csv", "datetime",        # Standard library
    "math", "random", "statistics",   # Math
    "re", "collections", "itertools", # Utilities
]

# Blocked modules for security
BLOCKED_MODULES = [
    "subprocess", "os.system", "socket", "ctypes", "multiprocessing",
    "shutil", "pty", "fcntl", "resource", "signal",
    "__import__", "eval", "exec", "compile",
    "open",  # We'll provide a safe file API instead
]


def _indent_code(code: str, spaces: int = 4) -> str:
    """Indent code by the specified number of spaces."""
    indent = " " * spaces
    lines = code.split("\n")
    return "\n".join(indent + line for line in lines)


def _generate_safe_wrapper(code: str, working_dir) -> str:
    """
    Generate a wrapper script that executes user code safely.

    The wrapper:
    - Sets the working directory
    - Captures stdout/stderr
    - Blocks dangerous operations
    - Handles matplotlib non-interactive backend
    """
    wrapper = f'''
import sys
import os

# Set working directory
os.chdir({repr(str(working_dir))})

# Configure matplotlib for non-interactive use (if available)
try:
    import matplotlib
    matplotlib.use('Agg')
except ImportError:
    pass  # matplotlib not installed, skip configuration

# Block dangerous modules by removing them from available imports
_blocked_modules = {repr(BLOCKED_MODULES)}

class ImportBlocker:
    """Import hook to block dangerous modules."""
    def find_module(self, fullname, path=None):
        for blocked in _blocked_modules:
            if fullname == blocked or fullname.startswith(blocked + '.'):
                return self
        return None

    def load_module(self, fullname):
        raise ImportError(f"Module '{{fullname}}' is not available for security reasons")

sys.meta_path.insert(0, ImportBlocker())

# Provide a safe file writing function
_safe_write_count = 0
_max_safe_writes = 10
_original_open = open  # Save reference to original open before any blocking

def save_file(filename, content, mode='w'):
    """Safely write a file within the sandbox."""
    global _safe_write_count
    if _safe_write_count >= _max_safe_writes:
        raise RuntimeError("Maximum file write limit reached")

    # Prevent path traversal
    safe_filename = os.path.basename(filename)
    if not safe_filename:
        raise ValueError("Invalid filename")

    filepath = os.path.join({repr(str(working_dir))}, safe_filename)
    with _original_open(filepath, mode) as f:
        f.write(content if isinstance(content, str) else str(content))
    _safe_write_count += 1
    print(f"[File written: {{safe_filename}}]")
    return safe_filename

# Execute user code
try:
{_indent_code(code)}
except Exception as e:
    print(f"Error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
    sys.exit(1)
'''
    return wrapper


async def execute_code(
    code: str,
    conversation_id: str,
    timeout: Optional[int] = None,
) -> ExecutionResult:
    """
    Execute Python code in a sandboxed subprocess.

    Args:
        code: Python code to execute
        conversation_id: ID for the conversation (determines working directory)
        timeout: Optional timeout in seconds (defaults to EXECUTION_LIMITS)

    Returns:
        ExecutionResult with output, errors, and execution info
    """
    timeout = timeout or EXECUTION_LIMITS["timeout_seconds"]

    # Get or create sandbox directory
    working_dir = _get_sandbox_dir(conversation_id)

    # Generate the safe wrapper script
    wrapper_code = _generate_safe_wrapper(code, working_dir)

    # Write wrapper to a temporary file
    script_path = working_dir / "_execute.py"
    script_path.write_text(wrapper_code)

    try:
        python_path = sys.executable or "python3"
        result = await run_subprocess(
            args=[python_path, str(script_path)],
            working_dir=working_dir,
            timeout=timeout,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
        )
        return result
    finally:
        # Clean up the script file
        if script_path.exists():
            script_path.unlink()


def is_available() -> bool:
    """Check if code execution is available (Python interpreter exists)."""
    import shutil as sh
    return sh.which("python3") is not None or sh.which("python") is not None


def get_status() -> dict:
    """Get the status of the code execution service."""
    available = is_available()
    return {
        "status": "connected" if available else "error",
        "status_message": "Python interpreter available" if available else "Python interpreter not found",
        "metadata": {
            "timeout_seconds": EXECUTION_LIMITS["timeout_seconds"],
            "max_output_bytes": EXECUTION_LIMITS["max_output_bytes"],
        },
    }
