"""
Weekly pattern detection service for Edward.

Analyzes last 7 days of conversations, entity mentions, and memory types
to identify recurring themes, frequency changes, and unresolved threads.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta

_task: asyncio.Task | None = None
_INTERVAL_SECONDS = 7 * 24 * 3600  # Weekly


def is_pattern_detection_running() -> bool:
    return _task is not None and not _task.done()


async def start_pattern_detection() -> None:
    global _task
    if _task is not None:
        return
    _task = asyncio.create_task(_detection_loop())
    print("Pattern detection started (weekly)")


async def stop_pattern_detection() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    print("Pattern detection stopped")


async def _detection_loop() -> None:
    # Wait 1 hour on first run to avoid startup load
    await asyncio.sleep(3600)

    while True:
        try:
            await _run_detection()
        except Exception as e:
            print(f"[PATTERN] Detection failed: {e}")

        await asyncio.sleep(_INTERVAL_SECONDS)


async def _run_detection() -> None:
    from sqlalchemy import select, func
    from services.database import (
        async_session, ConversationModel, MemoryModel,
        EntityMentionModel, EntityModel,
    )
    from services.llm_utils import build_background_llm, make_background_system_message
    from langchain_core.messages import HumanMessage

    cutoff = datetime.utcnow() - timedelta(days=7)

    async with async_session() as session:
        conv_result = await session.execute(
            select(ConversationModel.title, ConversationModel.search_tags)
            .where(ConversationModel.created_at >= cutoff)
            .order_by(ConversationModel.created_at.desc())
            .limit(50)
        )
        conversations = [
            {"title": r[0], "tags": r[1]}
            for r in conv_result.fetchall()
        ]

        mem_result = await session.execute(
            select(MemoryModel.memory_type, func.count())
            .where(MemoryModel.created_at >= cutoff)
            .group_by(MemoryModel.memory_type)
        )
        memory_types = {r[0]: r[1] for r in mem_result.fetchall()}

        entity_result = await session.execute(
            select(EntityModel.name, EntityModel.entity_type, EntityModel.mention_count)
            .where(EntityModel.last_seen >= cutoff)
            .order_by(EntityModel.mention_count.desc())
            .limit(15)
        )
        top_entities = [
            {"name": r[0], "type": r[1], "mentions": r[2]}
            for r in entity_result.fetchall()
        ]

    if not conversations and not memory_types and not top_entities:
        print("[PATTERN] No data for pattern detection, skipping")
        return

    llm = build_background_llm(max_tokens=1024)

    data_summary = json.dumps({
        "conversations": conversations[:20],
        "memory_type_distribution": memory_types,
        "top_entities": top_entities,
        "period": f"{cutoff.isoformat()} to {datetime.utcnow().isoformat()}",
    }, indent=2, default=str)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(
                    "Analyze weekly activity patterns. Identify: "
                    "1) Recurring themes or topics, "
                    "2) Notable frequency changes, "
                    "3) Unresolved threads that may need attention. "
                    "Be concise and actionable. Format as markdown."
                ),
                HumanMessage(content=f"Weekly activity data:\n{data_summary}"),
            ]),
            timeout=15.0,
        )

        analysis_text = response.content
        if isinstance(analysis_text, list):
            analysis_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in analysis_text
            )
        analysis_text = analysis_text.strip()

    except Exception as e:
        print(f"[PATTERN] LLM analysis failed: {e}")
        return

    from services.document_service import store_document, search_documents, update_document, Document

    week_label = datetime.utcnow().strftime("%Y-W%V")
    doc_title = f"Weekly Patterns — {week_label}"

    existing = await search_documents(f"Weekly Patterns {week_label}", limit=1)
    if existing and existing[0].title == doc_title:
        await update_document(existing[0].id, content=analysis_text)
        print(f"[PATTERN] Updated weekly pattern doc: {doc_title}")
    else:
        doc = Document(
            id=None,
            title=doc_title,
            content=analysis_text,
            tags="pattern,weekly,system_generated",
        )
        await store_document(doc)
        print(f"[PATTERN] Created weekly pattern doc: {doc_title}")

    try:
        from services.push_service import send_push_to_all
        await send_push_to_all(
            title="Weekly patterns detected",
            body=analysis_text[:200],
        )
    except Exception:
        pass
