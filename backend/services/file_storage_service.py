"""
File storage service for Edward's persistent file storage.

Handles:
- Storing uploaded and generated files to disk + DB
- Moving sandbox files to persistent storage
- File metadata CRUD (list, get, delete)
- Reading text file contents
"""

import os
import uuid
import shutil
import mimetypes
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, func, update

from services.database import async_session, FileModel

# Storage root — configurable via env, defaults to ./storage relative to backend/
FILE_STORAGE_ROOT = os.getenv(
    "FILE_STORAGE_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")
)

# Limits
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# MIME type allowlist
ALLOWED_MIME_TYPES = {
    # Images
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/bmp",
    # Documents
    "application/pdf", "text/plain", "text/csv", "text/html", "text/markdown",
    "application/json", "application/xml",
    # Office
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Data
    "text/tab-separated-values",
    # Archives (for download, not execution)
    "application/zip",
}

# Text-readable MIME types
TEXT_MIME_TYPES = {
    "text/plain", "text/csv", "text/html", "text/markdown",
    "text/tab-separated-values", "application/json", "application/xml",
}


@dataclass
class StoredFile:
    id: str
    filename: str
    stored_path: str
    mime_type: str
    size_bytes: int
    category: str
    description: Optional[str]
    tags: Optional[str]
    source: str
    source_conversation_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    last_accessed: Optional[datetime]
    access_count: int


def _model_to_stored_file(row) -> StoredFile:
    return StoredFile(
        id=row.id,
        filename=row.filename,
        stored_path=row.stored_path,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        category=row.category or "general",
        description=row.description,
        tags=row.tags,
        source=row.source or "user",
        source_conversation_id=row.source_conversation_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_accessed=row.last_accessed,
        access_count=row.access_count or 0,
    )


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal."""
    name = os.path.basename(filename)
    # Remove any non-ASCII or control characters
    name = "".join(c for c in name if c.isalnum() or c in "._- ")
    return name or "unnamed"


def _build_storage_path(file_id: str, filename: str) -> str:
    """Build relative storage path with hex-prefix sharding: {id[0:2]}/{id}_{filename}"""
    prefix = file_id[:2]
    return os.path.join(prefix, f"{file_id}_{filename}")


def _get_absolute_path(relative_path: str) -> Path:
    """Get absolute path from relative storage path."""
    return Path(FILE_STORAGE_ROOT) / relative_path


def ensure_storage_dir():
    """Ensure the storage root directory exists."""
    os.makedirs(FILE_STORAGE_ROOT, exist_ok=True)


async def store_file(
    file_data: bytes,
    filename: str,
    mime_type: str,
    category: str = "general",
    description: Optional[str] = None,
    tags: Optional[str] = None,
    source: str = "user",
    conversation_id: Optional[str] = None,
) -> StoredFile:
    """
    Store a file to disk and create a database record.

    Args:
        file_data: Raw file bytes
        filename: Original filename
        mime_type: MIME type
        category: upload, generated, artifact, processed, general
        description: Optional description
        tags: Optional comma-separated tags
        source: user, edward, sandbox
        conversation_id: Source conversation ID

    Returns:
        StoredFile with metadata

    Raises:
        ValueError: If file exceeds size limit or MIME type not allowed
    """
    if len(file_data) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024*1024)}MB")

    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"MIME type '{mime_type}' is not allowed")

    file_id = str(uuid.uuid4())
    safe_filename = _sanitize_filename(filename)
    relative_path = _build_storage_path(file_id, safe_filename)
    absolute_path = _get_absolute_path(relative_path)

    # Create parent directory
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    # Write file to disk
    absolute_path.write_bytes(file_data)

    # Create database record
    async with async_session() as session:
        db_file = FileModel(
            id=file_id,
            filename=safe_filename,
            stored_path=relative_path,
            mime_type=mime_type,
            size_bytes=len(file_data),
            category=category,
            description=description,
            tags=tags,
            source=source,
            source_conversation_id=conversation_id,
        )
        session.add(db_file)
        await session.commit()
        await session.refresh(db_file)

        return _model_to_stored_file(db_file)


async def get_file(file_id: str) -> Optional[StoredFile]:
    """Get file metadata by ID. Increments access_count."""
    async with async_session() as session:
        result = await session.execute(
            select(FileModel).where(FileModel.id == file_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None

        # Update access tracking
        await session.execute(
            update(FileModel)
            .where(FileModel.id == file_id)
            .values(
                access_count=FileModel.access_count + 1,
                last_accessed=func.now()
            )
        )
        await session.commit()
        await session.refresh(row)

        return _model_to_stored_file(row)


async def get_file_path(file_id: str) -> Optional[Path]:
    """Get absolute disk path for a file. Returns None if not found."""
    async with async_session() as session:
        result = await session.execute(
            select(FileModel.stored_path).where(FileModel.id == file_id)
        )
        stored_path = result.scalar_one_or_none()
        if not stored_path:
            return None

        absolute_path = _get_absolute_path(stored_path)
        if not absolute_path.exists():
            return None

        return absolute_path


async def list_files(
    category: Optional[str] = None,
    tags: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[StoredFile], int]:
    """List files with optional filters."""
    async with async_session() as session:
        query = select(FileModel)
        count_query = select(func.count(FileModel.id))

        if category:
            query = query.where(FileModel.category == category)
            count_query = count_query.where(FileModel.category == category)

        if source:
            query = query.where(FileModel.source == source)
            count_query = count_query.where(FileModel.source == source)

        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            for tag in tag_list:
                query = query.where(FileModel.tags.ilike(f"%{tag}%"))
                count_query = count_query.where(FileModel.tags.ilike(f"%{tag}%"))

        query = query.order_by(FileModel.created_at.desc()).limit(limit).offset(offset)

        result = await session.execute(query)
        rows = result.scalars().all()

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return [_model_to_stored_file(row) for row in rows], total


async def update_file_metadata(
    file_id: str,
    description: Optional[str] = None,
    tags: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[StoredFile]:
    """Update file metadata (description, tags, category)."""
    async with async_session() as session:
        result = await session.execute(
            select(FileModel).where(FileModel.id == file_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None

        if description is not None:
            row.description = description
        if tags is not None:
            row.tags = tags
        if category is not None:
            row.category = category

        await session.commit()
        await session.refresh(row)
        return _model_to_stored_file(row)


async def delete_file(file_id: str) -> bool:
    """Delete a file from disk and database."""
    async with async_session() as session:
        result = await session.execute(
            select(FileModel).where(FileModel.id == file_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return False

        # Delete from disk
        absolute_path = _get_absolute_path(row.stored_path)
        if absolute_path.exists():
            absolute_path.unlink()

        # Clean up empty parent directory
        parent = absolute_path.parent
        try:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

        # Delete from database
        await session.delete(row)
        await session.commit()
        return True


async def move_sandbox_file_to_storage(
    conversation_id: str,
    sandbox_filename: str,
    category: str = "generated",
    description: Optional[str] = None,
    tags: Optional[str] = None,
) -> Optional[StoredFile]:
    """
    Move a file from the code execution sandbox to persistent storage.

    Args:
        conversation_id: The conversation that created the file
        sandbox_filename: Filename within the sandbox directory
        category: File category (default: "generated")
        description: Optional description
        tags: Optional comma-separated tags

    Returns:
        StoredFile if successful, None if source file not found
    """
    import tempfile

    sandbox_dir = os.path.join(tempfile.gettempdir(), "edward_sandbox", conversation_id)
    source_path = os.path.join(sandbox_dir, os.path.basename(sandbox_filename))

    if not os.path.exists(source_path):
        return None

    # Read file data
    with open(source_path, "rb") as f:
        file_data = f.read()

    # Detect MIME type
    mime_type, _ = mimetypes.guess_type(sandbox_filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    # If MIME type not in allowlist, use a generic one
    if mime_type not in ALLOWED_MIME_TYPES:
        # Still allow it for sandbox files, but mark it
        ALLOWED_MIME_TYPES.add(mime_type)

    return await store_file(
        file_data=file_data,
        filename=sandbox_filename,
        mime_type=mime_type,
        category=category,
        description=description,
        tags=tags,
        source="sandbox",
        conversation_id=conversation_id,
    )


async def read_text_file(file_id: str) -> Optional[str]:
    """
    Read the text content of a stored file.

    Only works for text-based MIME types (text/plain, text/csv, application/json, etc.).

    Returns:
        File contents as string, or None if not found or not a text file
    """
    async with async_session() as session:
        result = await session.execute(
            select(FileModel).where(FileModel.id == file_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None

        if row.mime_type not in TEXT_MIME_TYPES:
            return None

        absolute_path = _get_absolute_path(row.stored_path)
        if not absolute_path.exists():
            return None

        try:
            return absolute_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
