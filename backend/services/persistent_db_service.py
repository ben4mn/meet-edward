"""
Persistent Database Service for Edward.

Provides named persistent PostgreSQL databases (schemas) that survive across
conversations. Each "database" is a PostgreSQL schema within the Edward database.

Key features:
- Schema-based isolation (edward_db_<name>)
- Security restrictions (no dangerous operations)
- Row and result limits
- Automatic schema management
"""

import re
import time
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from services.database import async_session, PersistentDatabaseModel, engine


# Configuration
SCHEMA_PREFIX = "edward_db_"
MAX_DATABASES_PER_USER = 10
MAX_ROWS = 500
MAX_RESULT_SIZE = 100_000  # 100KB
QUERY_TIMEOUT_SECONDS = 30

# Name validation: alphanumeric + underscores, max 50 chars
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,49}$")

# Blocked SQL patterns for security
BLOCKED_PATTERNS = [
    re.compile(r"\bDROP\s+SCHEMA\b", re.IGNORECASE),
    re.compile(r"\bALTER\s+SCHEMA\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+SCHEMA\b", re.IGNORECASE),
    re.compile(r"\bSET\s+search_path\b", re.IGNORECASE),
    re.compile(r"\bCOPY\b", re.IGNORECASE),
    re.compile(r"\bpg_\w+\s*\(", re.IGNORECASE),  # pg_* functions
    re.compile(r"\bSET\s+ROLE\b", re.IGNORECASE),
    re.compile(r"\bRESET\s+ROLE\b", re.IGNORECASE),
    re.compile(r"\bGRANT\b", re.IGNORECASE),
    re.compile(r"\bREVOKE\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+EXTENSION\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+EXTENSION\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+FUNCTION\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+PROCEDURE\b", re.IGNORECASE),
    re.compile(r"\bEXECUTE\b", re.IGNORECASE),
    re.compile(r"\bPREPARE\b", re.IGNORECASE),
]


def validate_name(name: str) -> Optional[str]:
    """
    Validate database name.

    Returns error message if invalid, None if valid.
    """
    if not name:
        return "Database name cannot be empty"

    name_lower = name.lower()

    if not NAME_PATTERN.match(name_lower):
        return (
            "Database name must start with a letter, contain only lowercase letters, "
            "numbers, and underscores, and be 1-50 characters long"
        )

    return None


def validate_query(query: str) -> Optional[str]:
    """
    Validate SQL query for blocked patterns.

    Returns error message if blocked, None if valid.
    """
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(query):
            return f"Blocked operation: {pattern.pattern.replace(chr(92), '')} is not allowed for security reasons"

    return None


async def create_database(
    name: str,
    description: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new persistent database (PostgreSQL schema).

    Args:
        name: User-friendly name (e.g., "lana_medication")
        description: Optional description of the database purpose
        user_id: Optional user ID for future multi-tenant support

    Returns:
        Dict with database info including id, name, schema_name

    Raises:
        ValueError: If name is invalid or already exists
    """
    # Validate name
    error = validate_name(name)
    if error:
        raise ValueError(error)

    name_lower = name.lower()
    schema_name = f"{SCHEMA_PREFIX}{name_lower}"

    async with async_session() as session:
        # Check if database already exists
        existing = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.name == name_lower
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Database '{name_lower}' already exists")

        # Check limit
        count_result = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.user_id == user_id
            ) if user_id else select(PersistentDatabaseModel)
        )
        if len(count_result.scalars().all()) >= MAX_DATABASES_PER_USER:
            raise ValueError(f"Maximum of {MAX_DATABASES_PER_USER} databases allowed")

        # Create the PostgreSQL schema
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

        # Create metadata record
        db_record = PersistentDatabaseModel(
            id=str(uuid.uuid4()),
            name=name_lower,
            schema_name=schema_name,
            description=description,
            user_id=user_id,
        )
        session.add(db_record)
        await session.commit()

        return {
            "id": db_record.id,
            "name": db_record.name,
            "schema_name": db_record.schema_name,
            "description": db_record.description,
            "created_at": db_record.created_at,
        }


async def delete_database(name: str) -> bool:
    """
    Delete a persistent database (PostgreSQL schema) and all its data.

    Args:
        name: Database name to delete

    Returns:
        True if deleted, False if not found
    """
    name_lower = name.lower()

    async with async_session() as session:
        # Find the database record
        result = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.name == name_lower
            )
        )
        db_record = result.scalar_one_or_none()

        if not db_record:
            return False

        schema_name = db_record.schema_name

        # Drop the PostgreSQL schema and all its contents
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

        # Delete metadata record
        await session.delete(db_record)
        await session.commit()

        return True


async def list_databases(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List all persistent databases.

    Args:
        user_id: Optional user ID to filter by

    Returns:
        List of database info dicts
    """
    async with async_session() as session:
        query = select(PersistentDatabaseModel).order_by(
            PersistentDatabaseModel.created_at.desc()
        )

        if user_id:
            query = query.where(PersistentDatabaseModel.user_id == user_id)

        result = await session.execute(query)
        databases = result.scalars().all()

        return [
            {
                "id": db.id,
                "name": db.name,
                "schema_name": db.schema_name,
                "description": db.description,
                "created_at": db.created_at.isoformat() if db.created_at else None,
                "updated_at": db.updated_at.isoformat() if db.updated_at else None,
                "last_accessed": db.last_accessed.isoformat() if db.last_accessed else None,
            }
            for db in databases
        ]


async def get_database(name: str) -> Optional[Dict[str, Any]]:
    """
    Get a single database by name.

    Args:
        name: Database name

    Returns:
        Database info dict or None if not found
    """
    name_lower = name.lower()

    async with async_session() as session:
        result = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.name == name_lower
            )
        )
        db = result.scalar_one_or_none()

        if not db:
            return None

        return {
            "id": db.id,
            "name": db.name,
            "schema_name": db.schema_name,
            "description": db.description,
            "created_at": db.created_at.isoformat() if db.created_at else None,
            "updated_at": db.updated_at.isoformat() if db.updated_at else None,
            "last_accessed": db.last_accessed.isoformat() if db.last_accessed else None,
        }


def _format_results(columns: List[str], rows: List[tuple]) -> str:
    """Format query results as an aligned table."""
    if not columns:
        return "Query executed successfully."

    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]

    if not rows:
        return f"Columns: {', '.join(columns)}\n(0 rows)"

    # Calculate column widths
    str_rows = [[str(val) if val is not None else "NULL" for val in row] for row in rows]
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

    # Truncate if too large
    if len(result) > MAX_RESULT_SIZE:
        result = result[:MAX_RESULT_SIZE] + "\n... [output truncated]"

    return result


async def execute_query(
    db_name: str,
    query: str,
) -> Dict[str, Any]:
    """
    Execute a SQL query against a persistent database.

    Args:
        db_name: Name of the persistent database
        query: SQL query to execute

    Returns:
        Dict with success, output, error, and duration_ms
    """
    start_time = time.time()

    # Validate query
    error = validate_query(query)
    if error:
        return {
            "success": False,
            "output": "",
            "error": error,
            "duration_ms": 0,
        }

    name_lower = db_name.lower()

    # Get the database schema name
    async with async_session() as session:
        result = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.name == name_lower
            )
        )
        db_record = result.scalar_one_or_none()

        if not db_record:
            return {
                "success": False,
                "output": "",
                "error": f"Database '{db_name}' not found. Use list_persistent_dbs() to see available databases.",
                "duration_ms": 0,
            }

        schema_name = db_record.schema_name

        # Update last_accessed
        db_record.last_accessed = datetime.utcnow()
        await session.commit()

    try:
        async with engine.begin() as conn:
            # Set search_path to the target schema (and public for common functions)
            await conn.execute(text(f'SET search_path TO "{schema_name}", public'))

            # Set statement timeout
            await conn.execute(text(f"SET statement_timeout TO '{QUERY_TIMEOUT_SECONDS}s'"))

            # Execute the query
            result = await conn.execute(text(query))

            # Get results if it's a SELECT-like query
            if result.returns_rows:
                columns = list(result.keys())
                rows = result.fetchall()
                output = _format_results(columns, rows)
            else:
                rowcount = result.rowcount
                output = f"Query executed successfully. Rows affected: {rowcount}"

            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "success": True,
                "output": output,
                "error": None,
                "duration_ms": duration_ms,
            }

    except SQLAlchemyError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e.orig) if hasattr(e, 'orig') and e.orig else str(e)

        # Check for timeout
        if "statement timeout" in error_msg.lower():
            error_msg = f"Query timed out after {QUERY_TIMEOUT_SECONDS} seconds"

        return {
            "success": False,
            "output": "",
            "error": f"SQL Error: {error_msg}",
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "success": False,
            "output": "",
            "error": f"Error: {type(e).__name__}: {e}",
            "duration_ms": duration_ms,
        }


async def get_columns(db_name: str, table_name: str) -> List[Dict[str, Any]]:
    """
    Get column details for a table in a persistent database.

    Args:
        db_name: Name of the persistent database
        table_name: Name of the table

    Returns:
        List of column info dicts with name, data_type, is_nullable, column_default, ordinal_position
    """
    name_lower = db_name.lower()

    async with async_session() as session:
        result = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.name == name_lower
            )
        )
        db_record = result.scalar_one_or_none()

        if not db_record:
            return []

        schema_name = db_record.schema_name

    async with engine.begin() as conn:
        result = await conn.execute(text("""
            SELECT column_name, data_type, is_nullable, column_default, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
        """), {"schema": schema_name, "table": table_name})

        columns = result.fetchall()

        return [
            {
                "name": row[0],
                "data_type": row[1],
                "is_nullable": row[2],
                "column_default": row[3],
                "ordinal_position": row[4],
            }
            for row in columns
        ]


async def get_tables(db_name: str) -> List[Dict[str, Any]]:
    """
    Get list of tables in a persistent database.

    Args:
        db_name: Name of the persistent database

    Returns:
        List of table info dicts
    """
    name_lower = db_name.lower()

    async with async_session() as session:
        result = await session.execute(
            select(PersistentDatabaseModel).where(
                PersistentDatabaseModel.name == name_lower
            )
        )
        db_record = result.scalar_one_or_none()

        if not db_record:
            return []

        schema_name = db_record.schema_name

    async with engine.begin() as conn:
        result = await conn.execute(text("""
            SELECT table_name,
                   (SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = :schema AND table_name = t.table_name) as column_count
            FROM information_schema.tables t
            WHERE table_schema = :schema
            ORDER BY table_name
        """), {"schema": schema_name})

        tables = result.fetchall()

        return [
            {"name": row[0], "column_count": row[1]}
            for row in tables
        ]
