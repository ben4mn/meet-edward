"""
Documents router for Edward's persistent document store.

Provides endpoints for:
- Listing and searching documents
- Creating new documents
- Updating document content/title/tags
- Deleting documents
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel

from services.document_service import (
    search_documents,
    list_documents,
    get_document_by_id,
    save_document,
    update_document,
    delete_document,
    get_document_stats,
    Document,
)

router = APIRouter()


class DocumentCreateRequest(BaseModel):
    title: str
    content: str
    tags: Optional[str] = None


class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[str] = None


def _doc_to_dict(doc: Document) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "tags": doc.tags,
        "source_conversation_id": doc.source_conversation_id,
        "created_at": (doc.created_at.isoformat() + "Z") if doc.created_at else None,
        "updated_at": (doc.updated_at.isoformat() + "Z") if doc.updated_at else None,
        "last_accessed": (doc.last_accessed.isoformat() + "Z") if doc.last_accessed else None,
        "access_count": doc.access_count,
        "user_id": doc.user_id,
        "score": doc.score if doc.score > 0 else None,
    }


@router.get("/documents")
async def list_or_search_documents(
    query: Optional[str] = Query(None, description="Text search query (triggers semantic search)"),
    tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """List or search documents. If query is provided, uses hybrid semantic search."""
    try:
        if query:
            docs, total = await search_documents(query=query, tags=tags, limit=limit, offset=offset)
        else:
            docs, total = await list_documents(limit=limit, offset=offset, tags=tags)

        stats = await get_document_stats()

        return {
            "documents": [_doc_to_dict(d) for d in docs],
            "stats": stats,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents")
async def create_document(request: DocumentCreateRequest):
    """Create a new document."""
    try:
        doc = Document(
            id=None,
            title=request.title,
            content=request.content,
            tags=request.tags,
        )
        saved = await save_document(doc)
        return _doc_to_dict(saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/{document_id}")
async def get_document(document_id: str):
    """Get a single document by ID."""
    doc = await get_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _doc_to_dict(doc)


@router.patch("/documents/{document_id}")
async def patch_document(document_id: str, request: DocumentUpdateRequest):
    """Update a document's title, content, or tags."""
    doc = await update_document(
        document_id=document_id,
        title=request.title,
        content=request.content,
        tags=request.tags,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _doc_to_dict(doc)


@router.delete("/documents/{document_id}")
async def remove_document(document_id: str):
    """Delete a document by ID."""
    deleted = await delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "id": document_id}
