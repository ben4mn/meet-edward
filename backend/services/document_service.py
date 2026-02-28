"""
Document store service for Edward's persistent document storage.

Handles:
- CRUD operations on full documents (recipes, notes, reference guides, etc.)
- Hybrid search (70% vector similarity + 30% BM25 keyword) — same as memories
- Embedding generation reuses the same sentence-transformers model as memory_service
"""

import uuid
from typing import List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, text, func, update

from services.database import async_session, DocumentModel
from services.memory_service import get_embedding


@dataclass
class Document:
    """A document retrieved from or to be stored in the database."""
    id: Optional[str]
    title: str
    content: str
    tags: Optional[str] = None
    source_conversation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    user_id: Optional[str] = None
    score: float = 0.0


def _model_to_document(row, score: float = 0.0) -> Document:
    """Convert a DocumentModel row to a Document dataclass."""
    return Document(
        id=row.id,
        title=row.title,
        content=row.content,
        tags=row.tags,
        source_conversation_id=row.source_conversation_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_accessed=row.last_accessed,
        access_count=row.access_count or 0,
        user_id=row.user_id,
        score=score,
    )


async def save_document(doc: Document) -> Document:
    """Save a new document. Generates embedding from title + content[:500]."""
    async with async_session() as session:
        doc_id = doc.id or str(uuid.uuid4())
        embedding_text = f"{doc.title} {doc.content[:500]}"
        embedding = get_embedding(embedding_text)

        db_doc = DocumentModel(
            id=doc_id,
            title=doc.title,
            content=doc.content,
            tags=doc.tags,
            embedding=embedding,
            source_conversation_id=doc.source_conversation_id,
            user_id=doc.user_id,
        )

        session.add(db_doc)
        await session.commit()
        await session.refresh(db_doc)

        doc.id = db_doc.id
        doc.created_at = db_doc.created_at
        doc.updated_at = db_doc.updated_at
        return doc


async def get_document_by_id(document_id: str) -> Optional[Document]:
    """Fetch a document by ID and increment access_count."""
    async with async_session() as session:
        result = await session.execute(
            select(DocumentModel).where(DocumentModel.id == document_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None

        # Update access tracking
        await session.execute(
            update(DocumentModel)
            .where(DocumentModel.id == document_id)
            .values(
                access_count=DocumentModel.access_count + 1,
                last_accessed=func.now()
            )
        )
        await session.commit()

        return _model_to_document(row)


async def update_document(
    document_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    tags: Optional[str] = None,
) -> Optional[Document]:
    """Partial update. Re-embeds if title or content changes."""
    async with async_session() as session:
        result = await session.execute(
            select(DocumentModel).where(DocumentModel.id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            return None

        re_embed = False

        if title is not None and title != doc.title:
            doc.title = title
            re_embed = True

        if content is not None and content != doc.content:
            doc.content = content
            re_embed = True

        if tags is not None:
            doc.tags = tags

        if re_embed:
            embedding_text = f"{doc.title} {doc.content[:500]}"
            doc.embedding = get_embedding(embedding_text)

        await session.commit()
        await session.refresh(doc)

        return _model_to_document(doc)


async def delete_document(document_id: str) -> bool:
    """Delete a document by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(DocumentModel).where(DocumentModel.id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            return False

        await session.delete(doc)
        await session.commit()
        return True


async def search_documents(
    query: str,
    tags: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> tuple[List[Document], int]:
    """
    Hybrid search: 70% vector similarity + 30% BM25 keyword on title+content.

    Args:
        query: Search query text
        tags: Optional comma-separated tags to filter by (any match)
        limit: Max results
        offset: Pagination offset
        vector_weight: Weight for vector similarity
        keyword_weight: Weight for keyword matching

    Returns:
        Tuple of (documents, total_count)
    """
    query_embedding = get_embedding(query)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Build filter conditions
    filter_conditions = "embedding IS NOT NULL"
    params = {"embedding": embedding_str, "query": query}

    if tags:
        # Filter: any of the provided tags must appear in the document's tags
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        tag_conditions = []
        for i, tag in enumerate(tag_list):
            param_name = f"tag_{i}"
            tag_conditions.append(f"tags ILIKE '%' || :{param_name} || '%'")
            params[param_name] = tag
        if tag_conditions:
            filter_conditions += f" AND ({' OR '.join(tag_conditions)})"

    fetch_limit = (limit + offset) * 2
    params["fetch_limit"] = fetch_limit

    async with async_session() as session:
        # Vector search
        vector_sql = text(f"""
            SELECT
                id, title, content, tags,
                source_conversation_id, created_at, updated_at, last_accessed,
                access_count, user_id,
                1 - (embedding <=> CAST(:embedding AS vector)) as vector_score
            FROM documents
            WHERE {filter_conditions}
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :fetch_limit
        """)

        vector_result = await session.execute(vector_sql, params)
        vector_rows = {row.id: row for row in vector_result.fetchall()}

        # Keyword search (BM25 on title + content)
        keyword_sql = text(f"""
            SELECT
                id,
                ts_rank_cd(
                    to_tsvector('english', title || ' ' || content),
                    plainto_tsquery('english', :query)
                ) as keyword_score
            FROM documents
            WHERE {filter_conditions}
              AND to_tsvector('english', title || ' ' || content)
                  @@ plainto_tsquery('english', :query)
            ORDER BY keyword_score DESC
            LIMIT :fetch_limit
        """)

        keyword_result = await session.execute(keyword_sql, params)
        keyword_scores = {row.id: row.keyword_score for row in keyword_result.fetchall()}

        # Combine scores
        scored = []
        for doc_id, row in vector_rows.items():
            v_score = row.vector_score or 0
            k_score = keyword_scores.get(doc_id, 0)
            combined = vector_weight * v_score + keyword_weight * k_score

            scored.append(Document(
                id=row.id,
                title=row.title,
                content=row.content,
                tags=row.tags,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                user_id=row.user_id,
                score=combined,
            ))

        scored.sort(key=lambda d: d.score, reverse=True)
        total = len(scored)
        return scored[offset:offset + limit], total


async def list_documents(
    limit: int = 50,
    offset: int = 0,
    tags: Optional[str] = None,
) -> tuple[List[Document], int]:
    """List documents ordered by updated_at DESC, optionally filtered by tags."""
    async with async_session() as session:
        base_query = select(DocumentModel)
        count_query = select(func.count(DocumentModel.id))

        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            for tag in tag_list:
                base_query = base_query.where(DocumentModel.tags.ilike(f"%{tag}%"))
                count_query = count_query.where(DocumentModel.tags.ilike(f"%{tag}%"))

        base_query = base_query.order_by(DocumentModel.updated_at.desc()).limit(limit).offset(offset)

        result = await session.execute(base_query)
        rows = result.scalars().all()

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return [_model_to_document(row) for row in rows], total


async def retrieve_relevant_documents(query: str, limit: int = 3) -> List[Document]:
    """
    Lightweight retrieval — returns documents with title+id only (for context injection).
    Uses vector similarity only for speed.
    """
    query_embedding = get_embedding(query)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    async with async_session() as session:
        sql = text("""
            SELECT id, title, tags,
                   1 - (embedding <=> CAST(:embedding AS vector)) as score
            FROM documents
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)

        result = await session.execute(sql, {"embedding": embedding_str, "limit": limit})
        rows = result.fetchall()

        # Only return documents with reasonable relevance (> 0.3 similarity)
        return [
            Document(
                id=row.id,
                title=row.title,
                content="",  # Not loaded for context injection
                tags=row.tags,
                score=row.score,
            )
            for row in rows
            if row.score > 0.3
        ]


async def get_document_stats() -> dict:
    """Get statistics: total count, tag breakdown."""
    async with async_session() as session:
        total_result = await session.execute(
            select(func.count(DocumentModel.id))
        )
        total = total_result.scalar() or 0

        # Get all tags and count them
        tags_result = await session.execute(
            select(DocumentModel.tags).where(DocumentModel.tags.isnot(None))
        )
        tag_counts: dict[str, int] = {}
        for row in tags_result.fetchall():
            if row[0]:
                for tag in row[0].split(","):
                    tag = tag.strip()
                    if tag:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return {
            "total": total,
            "by_tag": tag_counts,
        }
