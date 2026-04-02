"""
Shell/Bash command execution service.

Provides shell command execution with:
- Minimal blocklist for catastrophic commands only
- Restricted environment variables (no API keys passed through)
- Per-conversation working directory
- Destructive pattern detection
"""

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

from services.execution.base import (
    EXECUTION_LIMITS,
    ExecutionResult,
    _get_sandbox_dir,
    run_subprocess,
)


# Timeout for shell commands (seconds)
SHELL_TIMEOUT = 120

# Blocked commands — only catastrophic/privilege-escalation commands
BLOCKED_COMMANDS = {
    # Privilege escalation
    "sudo", "su",
    # System shutdown/reboot
    "shutdown", "reboot", "halt", "poweroff",
    # Raw disk/partition operations
    "dd", "mkfs", "fdisk", "parted",
    # Filesystem root escape
    "chroot",
}

# Patterns that indicate dangerous shell expansion
BLOCKED_PATTERNS = [
    re.compile(r"<\([^)]+\)"),          # <() process substitution
    re.compile(r">\([^)]+\)"),          # >() process substitution
]

# Patterns for catastrophically destructive commands
BLOCKED_DESTRUCTIVE = [
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$"),   # rm -rf /
    re.compile(r"\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/\s*$"),   # rm -fr /
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\*"),     # rm -rf /*
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+~\s*$"),   # rm -rf ~
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+~/"),      # rm -rf ~/...
]


def _validate_command(command: str, sandbox_dir: Path) -> Optional[str]:
    """Validate a shell command. Returns error message or None."""
    # Check for blocked patterns (process substitution)
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(command):
            return f"Blocked: Process substitution is not allowed"

    # Check for destructive patterns
    for pattern in BLOCKED_DESTRUCTIVE:
        if pattern.search(command):
            return "Blocked: Destructive command pattern detected"

    # Extract the first command word(s) from potentially piped commands
    # Split by pipe, semicolon, &&, ||
    segments = re.split(r"[|;&]", command)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # Get the command name (first word, ignoring env var assignments)
        words = segment.split()
        cmd_name = None
        for word in words:
            if "=" in word and not word.startswith("-"):
                continue  # Skip VAR=value assignments
            cmd_name = word
            break

        if cmd_name and os.path.basename(cmd_name) in BLOCKED_COMMANDS:
            return f"Blocked: '{os.path.basename(cmd_name)}' is not allowed for security reasons"

    return None


async def execute_shell(
    command: str,
    conversation_id: str,
    timeout: Optional[int] = None,
) -> ExecutionResult:
    """
    Execute a shell command in a sandboxed environment.

    Args:
        command: Shell command to execute
        conversation_id: ID for the conversation (determines working directory)
        timeout: Optional timeout in seconds

    Returns:
        ExecutionResult with output, errors, and execution info
    """
    timeout = timeout or SHELL_TIMEOUT

    working_dir = _get_sandbox_dir(conversation_id)

    # Validate command
    validation_error = _validate_command(command, working_dir)
    if validation_error:
        return ExecutionResult(
            success=False,
            output="",
            error=validation_error,
        )

    # Restricted environment - no API keys passed through
    if sys.platform == "win32":
        restricted_env = {
            "PATH": os.environ.get("PATH", ""),
            "USERPROFILE": os.environ.get("USERPROFILE", str(working_dir)),
            "TEMP": str(working_dir),
            "TMP": str(working_dir),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
        }
        shell_args = ["cmd.exe", "/c", command]
    else:
        restricted_env = {
            "PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": os.environ.get("HOME", str(working_dir)),
            "TMPDIR": str(working_dir),
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "SHELL": "/bin/bash",
            "USER": os.environ.get("USER", ""),
        }
        shell_args = ["bash", "-c", command]

    result = await run_subprocess(
        args=shell_args,
        working_dir=working_dir,
        timeout=timeout,
        env=restricted_env,
    )
    return result


def is_available() -> bool:
    """Check if shell execution is available."""
    if sys.platform == "win32":
        return shutil.which("cmd.exe") is not None
    return shutil.which("bash") is not None


def get_status() -> dict:
    """Get the status of the shell execution service."""
    available = is_available()
    shell_name = "cmd.exe" if sys.platform == "win32" else "Bash"
    return {
        "status": "connected" if available else "error",
        "status_message": f"{shell_name} shell available" if available else f"{shell_name} not found",
        "metadata": {
            "timeout_seconds": EXECUTION_LIMITS["timeout_seconds"],
            "max_output_bytes": EXECUTION_LIMITS["max_output_bytes"],
        },
    }
