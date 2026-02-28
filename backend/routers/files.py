"""
Files router for Edward's persistent file storage.

Provides endpoints for:
- Listing files with filters
- Uploading files
- Downloading files
- Updating file metadata
- Deleting files
"""

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from typing import Optional
from pydantic import BaseModel

from services.file_storage_service import (
    store_file,
    get_file,
    get_file_path,
    list_files,
    update_file_metadata,
    delete_file,
    StoredFile,
    MAX_FILE_SIZE,
    ALLOWED_MIME_TYPES,
)

router = APIRouter()


class FileUpdateRequest(BaseModel):
    description: Optional[str] = None
    tags: Optional[str] = None
    category: Optional[str] = None


def _file_to_dict(f: StoredFile) -> dict:
    return {
        "id": f.id,
        "filename": f.filename,
        "mime_type": f.mime_type,
        "size_bytes": f.size_bytes,
        "category": f.category,
        "description": f.description,
        "tags": f.tags,
        "source": f.source,
        "source_conversation_id": f.source_conversation_id,
        "created_at": (f.created_at.isoformat() + "Z") if f.created_at else None,
        "updated_at": (f.updated_at.isoformat() + "Z") if f.updated_at else None,
        "last_accessed": (f.last_accessed.isoformat() + "Z") if f.last_accessed else None,
        "access_count": f.access_count,
        "download_url": f"/api/files/{f.id}/download",
    }


@router.get("/files")
async def list_files_endpoint(
    category: Optional[str] = Query(None, description="Filter by category"),
    tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
    source: Optional[str] = Query(None, description="Filter by source (user, edward, sandbox)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List files with optional filters."""
    try:
        files, total = await list_files(
            category=category, tags=tags, source=source,
            limit=limit, offset=offset,
        )
        return {
            "files": [_file_to_dict(f) for f in files],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    category: str = "upload",
    description: Optional[str] = None,
    tags: Optional[str] = None,
    source: str = "user",
    conversation_id: Optional[str] = None,
):
    """Upload a file to persistent storage."""
    try:
        # Read file data
        file_data = await file.read()

        # Determine MIME type
        mime_type = file.content_type or "application/octet-stream"

        stored = await store_file(
            file_data=file_data,
            filename=file.filename or "unnamed",
            mime_type=mime_type,
            category=category,
            description=description,
            tags=tags,
            source=source,
            conversation_id=conversation_id,
        )
        return _file_to_dict(stored)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files/{file_id}")
async def get_file_metadata(file_id: str):
    """Get file metadata by ID."""
    f = await get_file(file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return _file_to_dict(f)


@router.get("/files/{file_id}/download")
async def download_file(file_id: str):
    """Download a file by ID."""
    f = await get_file(file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")

    path = await get_file_path(file_id)
    if not path:
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=str(path),
        filename=f.filename,
        media_type=f.mime_type,
    )


@router.patch("/files/{file_id}")
async def patch_file(file_id: str, request: FileUpdateRequest):
    """Update file metadata."""
    f = await update_file_metadata(
        file_id=file_id,
        description=request.description,
        tags=request.tags,
        category=request.category,
    )
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return _file_to_dict(f)


@router.delete("/files/{file_id}")
async def remove_file(file_id: str):
    """Delete a file."""
    deleted = await delete_file(file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return {"status": "deleted", "id": file_id}
