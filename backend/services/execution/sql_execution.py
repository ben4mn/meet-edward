"""
SQL (SQLite) execution service.

Provides per-conversation SQLite database with:
- Query execution with result formatting
- Security restrictions (no ATTACH, no LOAD EXTENSION)
- Row and database size limits
- Timeout via progress handler
"""

import asyncio
import re
import sqlite3
import time
from typing import Optional

from services.execution.base import (
    EXECUTION_LIMITS,
    ExecutionResult,
    _get_sandbox_dir,
)


# SQL security patterns to block
BLOCKED_SQL_PATTERNS = [
    re.compile(r"\bATTACH\s+DATABASE\b", re.IGNORECASE),
    re.compile(r"\bLOAD\s+EXTENSION\b", re.IGNORECASE),
]

# Limits
MAX_ROWS = 500
MAX_DB_SIZE_PAGES = 12800  # ~50MB with 4KB page size


def _validate_sql(query: str) -> Optional[str]:
    """Validate SQL query for blocked patterns. Returns error message or None."""
    for pattern in BLOCKED_SQL_PATTERNS:
        if pattern.search(query):
            return f"Blocked: {pattern.pattern} is not allowed for security reasons"
    return None


def _format_results(cursor: sqlite3.Cursor) -> str:
    """Format query results as an aligned table."""
    if cursor.description is None:
        return f"Query executed successfully. Rows affected: {cursor.rowcount}"

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchmany(MAX_ROWS + 1)

    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]

    if not rows:
        return f"Columns: {', '.join(columns)}\n(0 rows)"

    # Calculate column widths
    str_rows = [[str(val) for val in row] for row in rows]
    widths = [max(len(col), max((len(r[i]) for r in str_rows), default=0))
              for i, col in enumerate(columns)]

    # Build table
    header = " | ".join(col.ljust(w) for col, w in zip(columns, widths))
    separator = "-+-".join("-" * w for w in widths)

    lines = [header, separator]
    for row in str_rows:
        lines.append(" | ".join(val.ljust(w) for val, w in zip(row, widths)))

    result = "\n".join(lines)
    result += f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})"

    if truncated:
        result += f"\n... [results truncated at {MAX_ROWS} rows]"

    return result


def _execute_in_thread(db_path: str, query: str, timeout: int) -> ExecutionResult:
    """Execute SQL in a thread (blocking). Called via run_in_executor."""
    start_time = time.time()
    timed_out = False

    def progress_handler():
        nonlocal timed_out
        if time.time() - start_time > timeout:
            timed_out = True
            return 1  # Non-zero cancels the operation
        return 0

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.set_progress_handler(progress_handler, 1000)

        # Set size limit
        conn.execute(f"PRAGMA max_page_count = {MAX_DB_SIZE_PAGES}")

        cursor = conn.cursor()

        # Support multiple statements separated by semicolons
        # Use executescript for multiple statements, execute for single
        stripped = query.strip().rstrip(";")
        if ";" in stripped:
            # Multiple statements - use executescript (auto-commits)
            conn.executescript(query)
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=True,
                output="Multiple statements executed successfully.",
                duration_ms=duration_ms,
            )
        else:
            cursor.execute(query)
            conn.commit()

            output = _format_results(cursor)
            duration_ms = int((time.time() - start_time) * 1000)

            conn.close()

            return ExecutionResult(
                success=True,
                output=output,
                duration_ms=duration_ms,
            )

    except sqlite3.OperationalError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        if timed_out:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Query timed out after {timeout} seconds",
                duration_ms=duration_ms,
            )
        return ExecutionResult(
            success=False,
            output="",
            error=f"SQL Error: {e}",
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Error: {type(e).__name__}: {e}",
            duration_ms=duration_ms,
        )


async def execute_sql(
    query: str,
    conversation_id: str,
    timeout: Optional[int] = None,
) -> ExecutionResult:
    """
    Execute a SQL query against a per-conversation SQLite database.

    Args:
        query: SQL query to execute
        conversation_id: ID for the conversation (determines database file)
        timeout: Optional timeout in seconds

    Returns:
        ExecutionResult with formatted results or errors
    """
    timeout = timeout or EXECUTION_LIMITS["timeout_seconds"]

    # Validate query
    validation_error = _validate_sql(query)
    if validation_error:
        return ExecutionResult(
            success=False,
            output="",
            error=validation_error,
        )

    working_dir = _get_sandbox_dir(conversation_id)
    db_path = str(working_dir / "sandbox.db")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _execute_in_thread, db_path, query, timeout
    )
    return result


def is_available() -> bool:
    """SQLite is always available (stdlib)."""
    return True


def get_status() -> dict:
    """Get the status of the SQL execution service."""
    return {
        "status": "connected",
        "status_message": "SQLite available (stdlib)",
        "metadata": {
            "timeout_seconds": EXECUTION_LIMITS["timeout_seconds"],
            "max_rows": MAX_ROWS,
            "max_db_size_mb": 50,
        },
    }
