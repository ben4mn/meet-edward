"""
Shared execution utilities for all execution backends.

Provides common ExecutionResult, sandbox management, and subprocess helpers.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Execution limits
EXECUTION_LIMITS = {
    "timeout_seconds": 30,
    "max_output_bytes": 100_000,  # 100KB
    "max_memory_mb": 256,
}

# Base directory for sandbox working directories
SANDBOX_BASE_DIR = Path(tempfile.gettempdir()) / "edward_sandbox"


@dataclass
class ExecutionResult:
    """Result of a code execution."""
    success: bool
    output: str
    error: Optional[str] = None
    duration_ms: int = 0
    truncated: bool = False
    files_created: list[str] = None

    def __post_init__(self):
        if self.files_created is None:
            self.files_created = []


def _get_sandbox_dir(conversation_id: str) -> Path:
    """Get or create the sandbox directory for a conversation."""
    sandbox_dir = SANDBOX_BASE_DIR / conversation_id
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    return sandbox_dir


def _run_subprocess_sync(
    args: list[str],
    working_dir: Path,
    timeout: int,
    env: dict,
) -> tuple[int, bytes, bytes, bool]:
    """Run a subprocess synchronously. Returns (returncode, stdout, stderr, timed_out).

    Called via asyncio.to_thread() on Windows where SelectorEventLoop
    does not support asyncio.create_subprocess_exec().
    """
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(working_dir),
            env=env,
            timeout=timeout,
        )
        return (result.returncode, result.stdout, result.stderr, False)
    except subprocess.TimeoutExpired:
        return (-1, b"", b"", True)


async def run_subprocess(
    args: list[str],
    working_dir: Path,
    timeout: Optional[int] = None,
    env: Optional[dict] = None,
) -> ExecutionResult:
    """
    Run a subprocess with timeout, output capture, and truncation.

    On Windows, uses subprocess.run() in a thread (SelectorEventLoop
    does not support create_subprocess_exec).
    On macOS/Linux, uses asyncio.create_subprocess_exec().
    """
    timeout = timeout or EXECUTION_LIMITS["timeout_seconds"]
    start_time = time.time()

    # Get list of files before execution
    files_before = set(os.listdir(working_dir)) if working_dir.exists() else set()

    try:
        if sys.platform == "win32":
            # Windows: SelectorEventLoop cannot create subprocesses.
            # Run synchronously in a thread instead.
            returncode, stdout_bytes, stderr_bytes, timed_out = await asyncio.to_thread(
                _run_subprocess_sync, args, working_dir, timeout, env or os.environ
            )
            if timed_out:
                duration_ms = int((time.time() - start_time) * 1000)
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Execution timed out after {timeout} seconds",
                    duration_ms=duration_ms,
                )
        else:
            # macOS/Linux: use async subprocess
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                env=env or os.environ,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                duration_ms = int((time.time() - start_time) * 1000)
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Execution timed out after {timeout} seconds",
                    duration_ms=duration_ms,
                )
            returncode = process.returncode

        duration_ms = int((time.time() - start_time) * 1000)

        # Process output
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        # Truncate if necessary
        truncated = False
        max_output = EXECUTION_LIMITS["max_output_bytes"]

        if len(stdout_text) > max_output:
            stdout_text = stdout_text[:max_output] + "\n... [output truncated]"
            truncated = True

        if len(stderr_text) > max_output:
            stderr_text = stderr_text[:max_output] + "\n... [error output truncated]"
            truncated = True

        # Combine output
        output = stdout_text
        if stderr_text and returncode != 0:
            error = stderr_text
        else:
            error = None
            if stderr_text:
                output = output + ("\n" if output else "") + stderr_text

        # Get list of new files created (exclude internal files)
        files_after = set(os.listdir(working_dir)) if working_dir.exists() else set()
        internal_files = {"_execute.py", "_execute.js", "_execute.sh", "_execute.sql"}
        new_files = list(files_after - files_before - internal_files)

        return ExecutionResult(
            success=returncode == 0,
            output=output.strip(),
            error=error.strip() if error else None,
            duration_ms=duration_ms,
            truncated=truncated,
            files_created=new_files,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Execution error: {str(e)}",
            duration_ms=duration_ms,
        )


async def list_sandbox_files(conversation_id: str) -> list[str]:
    """List files in the conversation's sandbox directory."""
    working_dir = _get_sandbox_dir(conversation_id)
    if not working_dir.exists():
        return []
    return [f for f in os.listdir(working_dir) if not f.startswith("_")]


async def read_sandbox_file(conversation_id: str, filename: str) -> Optional[str]:
    """Read a file from the conversation's sandbox directory."""
    working_dir = _get_sandbox_dir(conversation_id)
    # Prevent path traversal
    safe_filename = os.path.basename(filename)
    filepath = working_dir / safe_filename

    if not filepath.exists():
        return None

    try:
        return filepath.read_text()
    except Exception:
        return None


async def cleanup_old_sandboxes(max_age_hours: int = 24) -> int:
    """
    Clean up sandbox directories older than the specified age.

    Returns the number of directories cleaned up.
    """
    if not SANDBOX_BASE_DIR.exists():
        return 0

    cleaned = 0
    cutoff_time = time.time() - (max_age_hours * 3600)

    for sandbox_dir in SANDBOX_BASE_DIR.iterdir():
        if sandbox_dir.is_dir():
            mtime = sandbox_dir.stat().st_mtime
            if mtime < cutoff_time:
                try:
                    shutil.rmtree(sandbox_dir)
                    cleaned += 1
                except Exception as e:
                    print(f"Failed to clean up sandbox {sandbox_dir}: {e}")

    return cleaned
