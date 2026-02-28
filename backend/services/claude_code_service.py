"""
Claude Code integration service for Edward.

Delegates coding tasks to Claude Code via the claude-agent-sdk Python package.
"""

import uuid
import json
import asyncio
from typing import Optional, Dict, List, Any, AsyncGenerator
from datetime import datetime

from services.database import async_session, ClaudeCodeSessionModel
from sqlalchemy import select, desc


# In-memory tracking of active sessions
_active_sessions: Dict[str, dict] = {}


def get_status() -> dict:
    """Check if claude-agent-sdk is available."""
    try:
        import claude_agent_sdk  # noqa: F401
        return {
            "status": "connected",
            "status_message": "Claude Agent SDK available",
        }
    except ImportError:
        return {
            "status": "error",
            "status_message": "claude-agent-sdk not installed. Run: pip install claude-agent-sdk",
        }


async def run_claude_code(
    task: str,
    conversation_id: Optional[str] = None,
    cwd: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
    max_turns: int = 25,
) -> AsyncGenerator[dict, None]:
    """
    Spawn a Claude Code session and yield structured events.

    Yields dicts with event_type: cc_text, cc_tool_use, cc_done, cc_error
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock
    except ImportError:
        yield {
            "event_type": "cc_error",
            "error": "claude-agent-sdk not installed. Run: pip install claude-agent-sdk",
        }
        return

    import os

    session_id = str(uuid.uuid4())
    project_root = cwd or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Track session
    _active_sessions[session_id] = {
        "id": session_id,
        "conversation_id": conversation_id,
        "task": task,
        "status": "running",
        "cwd": project_root,
        "started_at": datetime.utcnow().isoformat(),
        "output_parts": [],
    }

    yield {
        "event_type": "cc_started",
        "session_id": session_id,
    }

    default_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    options = ClaudeAgentOptions(
        model="claude-opus-4-6",
        max_turns=max_turns,
        system_prompt=system_prompt or "",
        allowed_tools=allowed_tools or default_tools,
        cwd=project_root,
        permission_mode="acceptEdits",
    )

    accumulated_text = []
    error_text = None

    try:
        async for message in query(
            prompt=task,
            options=options,
        ):
            if isinstance(message, AssistantMessage):
                # Process content blocks
                for block in message.content:
                    if isinstance(block, TextBlock):
                        accumulated_text.append(block.text)
                        yield {
                            "event_type": "cc_text",
                            "session_id": session_id,
                            "text": block.text,
                        }
                    elif isinstance(block, ToolUseBlock):
                        yield {
                            "event_type": "cc_tool_use",
                            "session_id": session_id,
                            "tool_name": block.name,
                            "tool_input": str(getattr(block, "input", ""))[:500],
                        }
                    elif isinstance(block, ToolResultBlock):
                        yield {
                            "event_type": "cc_tool_result",
                            "session_id": session_id,
                            "content": str(block.content)[:500],
                        }
    except Exception as e:
        error_text = str(e)
        yield {
            "event_type": "cc_error",
            "session_id": session_id,
            "error": error_text,
        }

    # Finalize session
    full_output = "\n".join(accumulated_text)
    status = "failed" if error_text else "completed"

    _active_sessions[session_id]["status"] = status
    _active_sessions[session_id]["completed_at"] = datetime.utcnow().isoformat()

    # Persist to DB
    await _save_session_to_db(
        session_id=session_id,
        conversation_id=conversation_id,
        task=task,
        status=status,
        cwd=project_root,
        output_summary=full_output[:5000],
        error=error_text,
    )

    # Clean up in-memory tracking
    _active_sessions.pop(session_id, None)

    yield {
        "event_type": "cc_done",
        "session_id": session_id,
        "status": status,
        "output_summary": full_output[:2000],
    }


async def _save_session_to_db(
    session_id: str,
    conversation_id: Optional[str],
    task: str,
    status: str,
    cwd: str,
    output_summary: str,
    error: Optional[str],
) -> None:
    """Persist a completed session to the database."""
    try:
        async with async_session() as session:
            db_record = ClaudeCodeSessionModel(
                id=session_id,
                conversation_id=conversation_id,
                task=task,
                status=status,
                cwd=cwd,
                output_summary=json.dumps({"text": output_summary}),
                error=error,
                completed_at=datetime.utcnow(),
            )
            session.add(db_record)
            await session.commit()
    except Exception as e:
        print(f"Failed to save CC session to DB: {e}")


async def get_session(session_id: str) -> Optional[dict]:
    """Look up a session by ID (in-memory first, then DB)."""
    # Check active sessions
    if session_id in _active_sessions:
        return _active_sessions[session_id]

    # Check DB
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ClaudeCodeSessionModel).where(ClaudeCodeSessionModel.id == session_id)
            )
            record = result.scalar_one_or_none()
            if record:
                return {
                    "id": record.id,
                    "conversation_id": record.conversation_id,
                    "task": record.task,
                    "status": record.status,
                    "cwd": record.cwd,
                    "output_summary": record.output_summary,
                    "error": record.error,
                    "started_at": record.started_at.isoformat() if record.started_at else None,
                    "completed_at": record.completed_at.isoformat() if record.completed_at else None,
                }
    except Exception as e:
        print(f"Failed to look up CC session: {e}")

    return None


async def list_sessions(conversation_id: Optional[str] = None, limit: int = 10) -> List[dict]:
    """List recent sessions."""
    try:
        async with async_session() as session:
            query = select(ClaudeCodeSessionModel).order_by(desc(ClaudeCodeSessionModel.created_at)).limit(limit)
            if conversation_id:
                query = query.where(ClaudeCodeSessionModel.conversation_id == conversation_id)

            result = await session.execute(query)
            records = result.scalars().all()

            return [
                {
                    "id": r.id,
                    "conversation_id": r.conversation_id,
                    "task": r.task[:100],
                    "status": r.status,
                    "error": r.error,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in records
            ]
    except Exception as e:
        print(f"Failed to list CC sessions: {e}")
        return []
