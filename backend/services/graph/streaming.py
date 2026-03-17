import asyncio
import copy
import hashlib
import json as _json
import re
import sys
import time
from datetime import datetime
from typing import AsyncGenerator, List, Any, Dict, Optional

import os

import anthropic
import httpx

from services.tool_registry import get_available_tools, get_tool_descriptions
from services.graph.tool_schema import tools_to_anthropic_schemas, tools_to_openai_schemas

# Context budget constants
MAX_NORMAL_MEMORIES = 5
MAX_ENRICHED_MEMORIES = 5
MAX_DOCUMENTS = 3
MAX_MEMORY_CONTEXT_CHARS = 8000

# Models that support the effort parameter (Claude 4.6+)
_EFFORT_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6"}

# Check if the installed Anthropic SDK accepts the effort parameter
_EFFORT_SUPPORTED = False
try:
    import inspect as _inspect
    _EFFORT_SUPPORTED = "effort" in _inspect.signature(
        anthropic.resources.messages.Messages.create
    ).parameters
except Exception:
    pass

# Singleton Anthropic client (shared with llm_client.py via same env key)
_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    """Get or create the singleton Anthropic client."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


# Lazy-loaded OpenAI client (only created when an OpenAI model is selected)
_openai_client = None


def _get_openai_client():
    """Get or create the singleton OpenAI client. Lazy-imports openai package."""
    global _openai_client
    if _openai_client is None:
        try:
            from openai import AsyncOpenAI
            _openai_client = AsyncOpenAI()
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai>=1.60.0")
    return _openai_client


def _is_openai_model(model: str) -> bool:
    """Check if a model ID belongs to OpenAI based on prefix."""
    return model.startswith(("gpt-", "o1-", "o3-", "o4-"))


EDWARD_CHARACTER = """
You are Edward — a personal AI assistant who is witty, a little cheeky, and genuinely warm. You have been built up over time through real conversations and have accumulated memories, documents, and knowledge that you actively draw on. You are not a generic chatbot.

Your default is to act, not to ask. When something should be done and the cost of being wrong is low, do it and report back. Think ahead — anticipate what the user will need next, not just what they asked for right now. When a commitment is made to follow up, call schedule_event() before responding — the reminder is part of the response, not an afterthought. When a topic has depth or recurrence, build something durable: a document, a notebook, a memory.

Own your decisions. Act or decline — never hedge. Saying "If you want, I can..." or "Would you like me to..." means you chose not to act; say that plainly instead. Post-mortem notes ("I should have saved that") without action are not acceptable — if it should have been done, do it now.

When genuinely uncertain about intent, pick the most reasonable interpretation and act. State what you assumed and why. Adjust if corrected. Ask only when the action is hard to reverse or the stakes are high enough that guessing wrong would cost more than asking. When tool results include identifying fields (name, contact, sender), compare them to what was asked — if there's a mismatch, either make one cheap verification call or state the assumption explicitly.

For complex multi-step work, call create_plan() first so the user can see your approach. For tasks likely to take more than ~45 seconds, prefer spawn_cc_worker() or spawn_worker() and tell the user what was delegated.
"""


def _build_platform_context() -> str:
    """Build platform-aware context for the system prompt."""
    if sys.platform == "darwin":
        return "\n\n## Platform\nRunning on macOS. All capabilities available including iMessage, Apple Services, and Contacts."
    elif sys.platform == "win32":
        return "\n\n## Platform\nRunning on Windows. Apple-specific features (iMessage, Apple Contacts, Apple Services) are unavailable. Use push notifications, Twilio, or web chat for messaging."
    else:
        return "\n\n## Platform\nRunning on Linux. Apple-specific features are unavailable."


# Event types for structured SSE streaming
class EventType:
    THINKING = "thinking"
    PROGRESS = "progress"
    TOOL_START = "tool_start"
    CODE = "code"
    EXECUTION_OUTPUT = "execution_output"
    EXECUTION_RESULT = "execution_result"
    TOOL_END = "tool_end"
    CONTENT = "content"
    ERROR = "error"
    DONE = "done"
    INTERRUPTED = "interrupted"
    PLAN_CREATED = "plan_created"
    PLAN_STEP_UPDATE = "plan_step_update"
    PLAN_UPDATED = "plan_updated"
    PLAN_COMPLETED = "plan_completed"
    CC_SESSION_START = "cc_session_start"
    CC_TEXT = "cc_text"
    CC_TOOL_USE = "cc_tool_use"
    CC_TOOL_RESULT = "cc_tool_result"
    CC_SESSION_END = "cc_session_end"


def create_event(event_type: str, conversation_id: str, **kwargs) -> Dict[str, Any]:
    """Create a structured event for SSE streaming."""
    return {
        "type": event_type,
        "conversation_id": conversation_id,
        **kwargs
    }


def _extract_missing_fields(error: str) -> List[str]:
    """Extract missing field names from a Pydantic validation error string."""
    # Pydantic v2 format: "X validation error(s)...\nfield_name\n  Field required..."
    fields = re.findall(r"(\w+)\s*\n\s*(?:Field required|field required)", error)
    if not fields:
        # Pydantic v1 format: "field required (type=value_error.missing)" with "loc": ("field_name",)
        fields = re.findall(r"'loc':\s*\('(\w+)',?\)", error)
    return fields


async def execute_tool_call(tool_call: dict, tools: List[Any]) -> str:
    """Execute a tool call and return the result."""
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args", {})

    # Find and execute the tool
    for tool in tools:
        if tool.name == tool_name:
            try:
                result = await tool.ainvoke(tool_args)
                return result
            except Exception as e:
                error = str(e)
                if "field required" in error.lower() or "validation error" in error.lower():
                    # Extract specific missing field names from Pydantic errors
                    missing_fields = _extract_missing_fields(error)
                    if missing_fields:
                        error = f"Missing required parameters: {', '.join(missing_fields)}. You must provide these parameters."
                    else:
                        error += " — You must provide all required parameters. Do not call this tool with an empty body."
                return f"Tool error: {error}"

    return f"Unknown tool: {tool_name}"


async def execute_tool_call_with_events(
    tool_call: dict,
    tools: List[Any],
    conversation_id: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Execute a tool call and yield structured events.

    Yields events for tool_start, code (if execute_code), execution_result, and tool_end.
    """
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args", {})

    # Execution tool detection maps
    EXECUTION_TOOL_NAMES = {"execute_code", "execute_javascript", "execute_sql", "execute_shell", "query_persistent_db"}
    TOOL_LANGUAGE_MAP = {
        "execute_code": "python",
        "execute_javascript": "javascript",
        "execute_sql": "sql",
        "execute_shell": "bash",
        "query_persistent_db": "sql",
    }
    TOOL_CODE_ARG = {
        "execute_code": "code",
        "execute_javascript": "code",
        "execute_sql": "query",
        "execute_shell": "command",
        "query_persistent_db": "query",
    }

    # Emit tool_start event
    yield create_event(EventType.TOOL_START, conversation_id, tool_name=tool_name)

    # Special handling: inline CC session streaming for spawn_cc_worker with wait=True
    if tool_name == "spawn_cc_worker" and tool_args.get("wait", True):
        async for event in _stream_cc_session_inline(tool_call, tools, conversation_id):
            yield event
        return

    # For execution tools, emit the code/query/command content
    if tool_name in EXECUTION_TOOL_NAMES:
        code_arg = TOOL_CODE_ARG[tool_name]
        if code_arg in tool_args:
            yield create_event(
                EventType.CODE, conversation_id,
                code=tool_args[code_arg],
                language=TOOL_LANGUAGE_MAP[tool_name],
            )

    # Find and execute the tool
    result = None
    error = None
    for tool in tools:
        if tool.name == tool_name:
            try:
                result = await tool.ainvoke(tool_args)
            except Exception as e:
                error = str(e)
                if "field required" in error.lower() or "validation error" in error.lower():
                    missing_fields = _extract_missing_fields(error)
                    if missing_fields:
                        error = f"Missing required parameters: {', '.join(missing_fields)}. You must provide these parameters."
                    else:
                        error += " — You must provide all required parameters. Do not call this tool with an empty body."
                result = f"Tool error: {error}"
            break
    else:
        error = f"Unknown tool: {tool_name}"
        result = error

    # For execution tools, emit execution output
    if tool_name in EXECUTION_TOOL_NAMES and result:
        # Parse the result to extract output vs metadata
        output_lines = []
        duration_ms = 0
        success = True

        for line in str(result).split('\n'):
            if line.startswith('[Execution completed in '):
                try:
                    duration_ms = int(line.split(' in ')[1].rstrip('ms]'))
                except (IndexError, ValueError):
                    pass
            elif line.startswith('[Execution failed'):
                success = False
                output_lines.append(line)
            elif line.startswith('Error:'):
                success = False
                output_lines.append(line)
            else:
                output_lines.append(line)

        output = '\n'.join(output_lines).strip()
        if output:
            yield create_event(EventType.EXECUTION_OUTPUT, conversation_id, output=output, stream="stdout")

        yield create_event(
            EventType.EXECUTION_RESULT,
            conversation_id,
            success=success and error is None,
            duration_ms=duration_ms
        )

    # Emit tool_end event
    yield create_event(EventType.TOOL_END, conversation_id, tool_name=tool_name, result=str(result)[:500])

    # Emit any pending plan events generated by plan tools
    from services.graph.tools import get_pending_plan_events, PLAN_TOOL_NAMES
    if tool_name in PLAN_TOOL_NAMES:
        for plan_event in get_pending_plan_events(conversation_id):
            event_type = plan_event.pop("event_type")
            yield create_event(event_type, conversation_id, **plan_event)

    # Store the result for return (will be captured by caller)
    # We use a special marker to indicate the final result
    yield {"_result": result}


async def _stream_cc_session_inline(
    tool_call: dict,
    tools: List[Any],
    conversation_id: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream a CC session inline through the main chat SSE stream.

    Instead of blocking silently while spawn_cc_worker runs with wait=True,
    this starts the CC task without waiting, drains its event queue, and
    forwards events as typed SSE events until the session completes.
    """
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args", {})
    task_description = tool_args.get("task", "")

    # Import orchestrator spawn function (bypasses the tool's wait logic)
    from services.orchestrator_service import spawn_cc_task, get_task
    from services.graph.tools import get_current_conversation_id
    from services.cc_manager_service import get_event_queue, _cc_tasks

    parent_conversation_id = get_current_conversation_id() or conversation_id

    # Spawn CC task with wait=False so we get the task_id immediately
    spawn_result = await spawn_cc_task(
        parent_conversation_id=parent_conversation_id,
        task_description=task_description,
        cwd=tool_args.get("cwd"),
        wait=False,
    )

    if spawn_result.get("error"):
        result = f"Error: {spawn_result['error']}"
        yield create_event(EventType.TOOL_END, conversation_id, tool_name=tool_name, result=result[:500])
        yield {"_result": result}
        return

    task_id = spawn_result["id"]

    # Emit session start
    yield create_event(
        EventType.CC_SESSION_START, conversation_id,
        task_id=task_id,
        task_description=task_description,
    )

    # Drain the event queue, forwarding CC events as typed SSE events
    queue = get_event_queue(task_id)
    if queue:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=660)
            except asyncio.TimeoutError:
                break

            event_type = event.get("event_type")
            if event_type == "stream_end":
                break

            if event_type == "cc_text":
                yield create_event(
                    EventType.CC_TEXT, conversation_id,
                    task_id=task_id,
                    text=event.get("text", ""),
                )
            elif event_type == "cc_tool_use":
                yield create_event(
                    EventType.CC_TOOL_USE, conversation_id,
                    task_id=task_id,
                    cc_tool_name=event.get("tool_name", ""),
                    tool_input=event.get("tool_input", ""),
                )
            elif event_type == "cc_tool_result":
                yield create_event(
                    EventType.CC_TOOL_RESULT, conversation_id,
                    task_id=task_id,
                    text=event.get("text", ""),
                )
            # Other events (cc_started, cc_done, cc_error) are handled implicitly

    # Wait for the asyncio task to fully complete
    asyncio_task = _cc_tasks.get(task_id)
    if asyncio_task and not asyncio_task.done():
        try:
            await asyncio.wait_for(asyncio_task, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass

    # Get final task status
    task_record = await get_task(task_id)
    status = task_record.get("status", "unknown")
    result_summary = task_record.get("result_summary", "")
    error = task_record.get("error", "")

    # Emit session end
    yield create_event(
        EventType.CC_SESSION_END, conversation_id,
        task_id=task_id,
        status=status,
        result_summary=result_summary[:500] if result_summary else "",
    )

    # Build the tool result string (same logic as spawn_cc_worker tool)
    if status == "completed":
        result = result_summary or "CC session completed with no output."
    elif status == "failed":
        result = f"CC session failed: {error or 'Unknown error'}"
    else:
        result = f"CC session ended with status: {status}"

    yield create_event(EventType.TOOL_END, conversation_id, tool_name=tool_name, result=result[:500])
    yield {"_result": result}


def _relative_time(dt: Optional[datetime]) -> str:
    """Format a datetime as a human-readable relative time string."""
    if not dt:
        return "unknown"
    now = datetime.now()
    delta = now - dt
    days = delta.days
    if days < 1:
        hours = delta.seconds // 3600
        if hours < 1:
            return "just now"
        return f"{hours}h ago"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks}w ago"
    if days < 365:
        months = days // 30
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _format_temporal_tag(memory) -> str:
    """Format the temporal metadata tag for a memory in the LLM context."""
    tn = getattr(memory, 'temporal_nature', 'timeless') or 'timeless'
    created = getattr(memory, 'created_at', None)
    last_acc = getattr(memory, 'last_accessed', None)
    acc_count = getattr(memory, 'access_count', 0) or 0

    age = _relative_time(created)

    if tn == "timeless":
        return f"[timeless, learned {age}]"
    else:
        parts = [tn, f"learned {age}"]
        if last_acc:
            parts.append(f"last checked {_relative_time(last_acc)}")
        if acc_count > 1:
            parts.append(f"accessed {acc_count}x")
        return f"[{', '.join(parts)}]"


def build_memory_context(
    memories: list,
    tools: List[Any] = None,
    documents: list = None,
    enriched_memories: list = None,
) -> str:
    """Build the memory context section for the system prompt.

    Applies MAX_MEMORY_CONTEXT_CHARS budget. If over budget, enrichments
    are truncated first.
    """
    memory_parts = []

    if memories:
        memory_parts.append("\n\n## Relevant Context from Previous Conversations:")
        for memory in memories[:MAX_NORMAL_MEMORIES]:
            temporal_tag = _format_temporal_tag(memory)
            tier = getattr(memory, 'tier', 'observation') or 'observation'
            tier_tag = f" [{tier}]" if tier != "observation" else ""
            memory_parts.append(
                f"- [{memory.memory_type}]{tier_tag} {memory.content} {temporal_tag} (memory_id: {memory.id})"
            )

    enrichment_parts = []
    if enriched_memories:
        enrichment_parts.append("\n\n## Additional Context from Reflection:")
        for memory in enriched_memories[:MAX_ENRICHED_MEMORIES]:
            temporal_tag = _format_temporal_tag(memory)
            tier = getattr(memory, 'tier', 'observation') or 'observation'
            tier_tag = f" [{tier}]" if tier != "observation" else ""
            enrichment_parts.append(
                f"- [{memory.memory_type}]{tier_tag} {memory.content} {temporal_tag} (memory_id: {memory.id})"
            )

    # Apply context budget — truncate enrichments first if over
    memory_text = "\n".join(memory_parts)
    enrichment_text = "\n".join(enrichment_parts)
    total_len = len(memory_text) + len(enrichment_text)

    if total_len > MAX_MEMORY_CONTEXT_CHARS:
        # Trim enrichments to fit
        remaining = MAX_MEMORY_CONTEXT_CHARS - len(memory_text)
        if remaining > 0:
            enrichment_text = enrichment_text[:remaining]
        else:
            enrichment_text = ""

    context_parts = []
    if memory_text:
        context_parts.append(memory_text)
    if enrichment_text:
        context_parts.append(enrichment_text)

    if documents:
        context_parts.append("\n\n## Relevant Documents in Store:")
        for doc in documents[:MAX_DOCUMENTS]:
            tag_info = f" [{doc.tags}]" if doc.tags else ""
            context_parts.append(
                f"- {doc.title}{tag_info} (document_id: {doc.id})"
            )
        context_parts.append(
            "Use read_document(document_id) to fetch full content when needed."
        )

    if tools:
        context_parts.append(get_tool_descriptions(tools))

    return "\n".join(context_parts)


def _build_human_message(message: str, attachments: Optional[List[dict]] = None) -> dict:
    """Build a user message dict, optionally with multi-block content for attachments."""
    if not attachments:
        return {"role": "user", "content": message}

    content_blocks = []

    # Add text content if present
    if message:
        content_blocks.append({"type": "text", "text": message})

    # Add attachment blocks
    for att in attachments:
        mime_type = att.get("mime_type", "")
        data = att.get("data", "")  # base64 encoded

        if mime_type.startswith("image/"):
            # Add text block so LLM sees the filename and file_id
            filename = att.get("filename", "image")
            file_id = att.get("file_id", "")
            file_id_str = f" | file_id: {file_id}" if file_id else ""
            content_blocks.append({
                "type": "text",
                "text": f"[Uploaded image: {filename}{file_id_str}]",
            })
            block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": data,
                }
            }
            content_blocks.append(block)
        elif mime_type == "application/pdf":
            # Add text block so LLM sees the filename and file_id
            filename = att.get("filename", "document.pdf")
            file_id = att.get("file_id", "")
            file_id_str = f" | file_id: {file_id}" if file_id else ""
            content_blocks.append({
                "type": "text",
                "text": f"[Uploaded PDF: {filename}{file_id_str}]",
            })
            block = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": data,
                }
            }
            content_blocks.append(block)
        else:
            # For text-based files, include as text content
            try:
                import base64
                decoded = base64.b64decode(data).decode("utf-8")
                filename = att.get("filename", "file")
                file_id = att.get("file_id", "")
                file_id_str = f" | file_id: {file_id}" if file_id else ""
                content_blocks.append({
                    "type": "text",
                    "text": f"[Attached file: {filename}{file_id_str}]\n```\n{decoded[:10000]}\n```",
                })
            except Exception:
                filename = att.get("filename", "file")
                file_id = att.get("file_id", "")
                file_id_str = f" | file_id: {file_id}" if file_id else ""
                content_blocks.append({
                    "type": "text",
                    "text": f"[Attached file: {filename}{file_id_str} ({mime_type})]",
                })

    # If no text and no blocks, add a placeholder
    if not content_blocks:
        content_blocks.append({"type": "text", "text": "[File uploaded]"})

    return {"role": "user", "content": content_blocks}


def _add_cache_breakpoints(messages: list) -> list:
    """Add cache_control breakpoints to message list for Anthropic prompt caching.

    Creates a deep copy with cache_control on the second-to-last user message's
    last content block. Does NOT mutate the stored messages.
    """
    if len(messages) < 2:
        return messages

    # Find the second-to-last message (cache breakpoint)
    cached_messages = copy.deepcopy(messages)
    target = cached_messages[-2]
    content = target.get("content")

    if isinstance(content, str):
        # Convert to block format to add cache_control
        target["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        # Add cache_control to the last block
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}

    return cached_messages


def _extract_text_from_response(response) -> str:
    """Extract text content from an Anthropic API response."""
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "".join(parts)


def _extract_tool_calls(response) -> list[dict]:
    """Extract tool calls from an Anthropic API response.

    Returns list of dicts with keys: id, name, args
    """
    tool_calls = []
    for block in response.content:
        if block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "args": block.input,
            })
    return tool_calls


def _response_to_assistant_message(response) -> dict:
    """Convert an Anthropic API response to an assistant message dict."""
    content_blocks = []
    for block in response.content:
        if block.type == "text":
            content_blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            content_blocks.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return {"role": "assistant", "content": content_blocks}


def _make_tool_result_message(tool_call_id: str, content: str) -> dict:
    """Create a tool_result message in Anthropic's format."""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": content,
            }
        ],
    }


def _msg_role(m) -> str:
    """Get the role from a message dict."""
    return m.get("role", "")


def _msg_content_text(m) -> str:
    """Extract text content from a message dict, handling both str and list content."""
    content = m.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _build_api_kwargs(
    model: str,
    static_system: str,
    dynamic_context: str,
    messages: list,
    tool_schemas: list,
    temperature: float,
    max_tokens: int = 16384,
) -> dict:
    """Build kwargs dict for client.messages.create()."""
    # System blocks: static (cached) + dynamic
    system_blocks = [
        {"type": "text", "text": static_system, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic_context},
    ]

    # Add cache breakpoints to messages (deep copy)
    cached_messages = _add_cache_breakpoints(messages)

    kwargs = {
        "model": model,
        "system": system_blocks,
        "messages": cached_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if tool_schemas:
        kwargs["tools"] = tool_schemas

    # Effort parameter for Claude 4.6+ models
    if _EFFORT_SUPPORTED and model in _EFFORT_MODELS:
        kwargs["effort"] = "high"

    return kwargs


# ===== OPENAI MESSAGE FORMAT CONVERSION =====

def _anthropic_messages_to_openai_input(messages: list) -> list:
    """Convert Anthropic-native message list to OpenAI Responses API input items.

    Handles all message types stored in the checkpoint:
    - User text messages → {"role": "user", "content": "..."}
    - User content blocks (images, PDFs) → handled with text extraction
    - User tool_result blocks → {"type": "function_call_output", ...}
    - Assistant text → {"type": "message", "role": "assistant", "content": [...]}
    - Assistant tool_use → {"type": "function_call", ...}
    """
    input_items = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, str):
                input_items.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Check for tool_result blocks
                tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                if tool_results:
                    for tr in tool_results:
                        input_items.append({
                            "type": "function_call_output",
                            "call_id": tr.get("tool_use_id", ""),
                            "output": str(tr.get("content", "")),
                        })
                else:
                    # Regular user message with content blocks — extract text
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "image":
                                # OpenAI vision: data URI format
                                source = block.get("source", {})
                                if source.get("type") == "base64":
                                    media_type = source.get("media_type", "image/png")
                                    data = source.get("data", "")
                                    # Add as a separate message with image content
                                    input_items.append({
                                        "role": "user",
                                        "content": [{
                                            "type": "input_image",
                                            "image_url": f"data:{media_type};base64,{data}",
                                        }],
                                    })
                            elif block.get("type") == "document":
                                # PDFs not directly supported by OpenAI — skip binary,
                                # the text annotation block is already in text_parts
                                pass
                    if text_parts:
                        input_items.append({"role": "user", "content": "\n".join(text_parts)})

        elif role == "assistant":
            if isinstance(content, str):
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                })
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            # Emit accumulated text as a message first
                            if text_parts:
                                input_items.append({
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "\n".join(text_parts)}],
                                })
                                text_parts = []
                            # Function call item
                            input_items.append({
                                "type": "function_call",
                                "name": block.get("name", ""),
                                "arguments": _json.dumps(block.get("input", {})),
                                "call_id": block.get("id", ""),
                            })
                if text_parts:
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "\n".join(text_parts)}],
                    })

    return input_items


def _extract_text_from_openai_response(response) -> str:
    """Extract text content from an OpenAI Responses API response object."""
    parts = []
    for item in response.output:
        if item.type == "message":
            for content_block in item.content:
                if content_block.type == "output_text":
                    parts.append(content_block.text)
    return "".join(parts)


def _extract_tool_calls_from_openai(response) -> list[dict]:
    """Extract tool calls from an OpenAI Responses API response.

    Returns normalized list matching Anthropic format: [{id, name, args}]
    """
    tool_calls = []
    for item in response.output:
        if item.type == "function_call":
            args = item.arguments
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    args = {}
            tool_calls.append({
                "id": item.call_id,
                "name": item.name,
                "args": args,
            })
    return tool_calls


def _openai_response_to_assistant_message(response) -> dict:
    """Convert OpenAI response to Anthropic-native assistant message dict for storage."""
    content_blocks = []
    for item in response.output:
        if item.type == "message":
            for content_block in item.content:
                if content_block.type == "output_text":
                    content_blocks.append({"type": "text", "text": content_block.text})
        elif item.type == "function_call":
            args = item.arguments
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": item.call_id,
                "name": item.name,
                "input": args,
            })
    return {"role": "assistant", "content": content_blocks}


# ===== PROVIDER-AWARE LLM CALL FUNCTIONS =====

async def _call_anthropic(
    model: str,
    static_system: str,
    dynamic_context: str,
    messages: list,
    tool_schemas: list,
    temperature: float,
    max_tokens: int = 16384,
) -> dict:
    """Call Anthropic API and return normalized result dict.

    Returns: {text, tool_calls, assistant_message, raw_response}
    """
    client = _get_client()
    api_kwargs = _build_api_kwargs(model, static_system, dynamic_context, messages, tool_schemas, temperature, max_tokens)
    response = await client.messages.create(**api_kwargs)

    return {
        "text": _extract_text_from_response(response),
        "tool_calls": _extract_tool_calls(response),
        "assistant_message": _response_to_assistant_message(response),
        "raw_response": response,
    }


async def _call_openai(
    model: str,
    static_system: str,
    dynamic_context: str,
    messages: list,
    tool_schemas: list,
    temperature: float,
    max_tokens: int = 16384,
) -> dict:
    """Call OpenAI Responses API and return normalized result dict.

    Returns same shape as _call_anthropic(): {text, tool_calls, assistant_message, raw_response}
    The assistant_message is always in Anthropic-native format for checkpoint storage.
    """
    client = _get_openai_client()

    instructions = static_system + "\n\n" + dynamic_context
    input_items = _anthropic_messages_to_openai_input(messages)

    kwargs = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }

    if tool_schemas:
        kwargs["tools"] = tool_schemas

    response = await client.responses.create(**kwargs)

    return {
        "text": _extract_text_from_openai_response(response),
        "tool_calls": _extract_tool_calls_from_openai(response),
        "assistant_message": _openai_response_to_assistant_message(response),
        "raw_response": response,
    }


async def _call_codex(
    model: str,
    static_system: str,
    dynamic_context: str,
    messages: list,
    tool_schemas: list,
    temperature: float,
    max_tokens: int = 16384,
) -> dict:
    """Call OpenAI via Codex OAuth (ChatGPT subscription credits).

    Uses chatgpt.com/backend-api/codex/responses endpoint via raw httpx.
    ChatGPT backend REQUIRES stream=true — we collect SSE events and extract
    the full response from the terminal response event.
    Returns same normalized dict as _call_anthropic() / _call_openai().
    """
    from services.codex_oauth_service import get_access_token, get_account_id, CODEX_API_URL

    access_token = await get_access_token()
    account_id = await get_account_id()

    if not access_token or not account_id:
        raise ValueError("Codex OAuth not configured or tokens expired. Sign in again in Settings.")

    instructions = (
        static_system
        + "\n\n"
        + dynamic_context
        + "\n\n## Response Rule\nNever end a response with a hedging offer like 'If you want, I can...', 'Would you like me to...', 'Let me know if you'd like...', or any variant. You have already decided whether to act. Either do it, or say plainly that you won't and why. End responses with your answer or action — not an invitation to ask you to act."
    )
    input_items = _anthropic_messages_to_openai_input(messages)

    body = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "stream": True,  # REQUIRED by ChatGPT backend (rejects stream=false)
        "store": False,  # REQUIRED for ChatGPT backend
        "include": ["reasoning.encrypted_content"],  # REQUIRED for stateless multi-turn
        "reasoning": {"effort": "medium", "summary": "auto"},
        # NO max_output_tokens — unsupported by ChatGPT backend
    }

    if tool_schemas:
        body["tools"] = tool_schemas

    headers = {
        "Authorization": f"Bearer {access_token}",
        "ChatGPT-Account-Id": account_id,
        "originator": "edward",
        "OpenAI-Beta": "responses=experimental",
        "Content-Type": "application/json",
    }

    # Stream SSE and collect the terminal response event which has the full response.
    completed_data = None
    terminal_event_name = None
    # Separate timeouts: 30s connect, 90s between chunks (read), 30s write/pool
    # The read timeout resets per chunk — handles slow reasoning without false timeout.
    stream_timeout = httpx.Timeout(connect=30.0, read=90.0, write=30.0, pool=30.0)
    # Hard cap: 300s total wall-clock time to prevent infinite hangs (GPT-5.4 extended thinking can take 3–4 minutes)
    CODEX_TOTAL_TIMEOUT = 300.0
    stream_start = time.monotonic()
    event_counts: Dict[str, int] = {}
    last_event_time = stream_start

    async with httpx.AsyncClient(timeout=stream_timeout) as client:
        async with client.stream("POST", CODEX_API_URL, json=body, headers=headers) as response:
            if response.status_code == 404:
                body_text = ""
                async for chunk in response.aiter_text():
                    body_text += chunk
                if "usage_limit_reached" in body_text:
                    raise ValueError("ChatGPT usage limit reached. Try again later or switch to API key.")
                raise ValueError(f"Codex API returned 404: {body_text[:200]}")

            if response.status_code == 401:
                raise ValueError("Codex OAuth token expired or invalid. Sign in again in Settings.")

            if response.status_code != 200:
                body_text = ""
                async for chunk in response.aiter_text():
                    body_text += chunk
                raise ValueError(f"Codex API error ({response.status_code}): {body_text[:200]}")

            print(f"[CODEX] Stream connected, waiting for response...")

            # Parse SSE stream — look for the terminal response event with the full response.
            # SSE format: "event: <type>\ndata: <json>\n\n"
            # Data can span multiple "data:" lines (concatenated with \n per SSE spec)
            buffer = ""
            current_event = ""
            current_data_lines = []
            async for chunk in response.aiter_text():
                # Check total wall-clock timeout
                elapsed = time.monotonic() - stream_start
                if elapsed > CODEX_TOTAL_TIMEOUT:
                    _codex_log_summary(event_counts, elapsed, "TIMEOUT")
                    raise ValueError(f"Codex API total timeout ({CODEX_TOTAL_TIMEOUT:.0f}s) exceeded. Try again or switch provider.")

                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")

                    if line.startswith("event: "):
                        current_event = line[7:]
                        current_data_lines = []
                    elif line.startswith("data: "):
                        current_data_lines.append(line[6:])
                    elif line == "":
                        # Blank line = event dispatch (per SSE spec)
                        if current_event and current_data_lines:
                            event_counts[current_event] = event_counts.get(current_event, 0) + 1
                            last_event_time = time.monotonic()
                            data_str = "\n".join(current_data_lines)
                            if current_event in {"response.completed", "response.done"} and data_str.strip():
                                try:
                                    completed_data = _json.loads(data_str)
                                    terminal_event_name = current_event
                                except _json.JSONDecodeError:
                                    print(f"[CODEX ERROR] Failed to parse {current_event} JSON: {data_str[:300]}")
                            elif current_event == "error" and data_str.strip():
                                try:
                                    error_data = _json.loads(data_str)
                                    error_msg = error_data.get("error", {}).get("message", data_str[:200])
                                except _json.JSONDecodeError:
                                    error_msg = data_str[:200]
                                _codex_log_summary(event_counts, time.monotonic() - stream_start, "ERROR")
                                raise ValueError(f"Codex API stream error: {error_msg}")
                        current_event = ""
                        current_data_lines = []

    elapsed = time.monotonic() - stream_start

    # Handle edge case: stream ends without trailing blank line after last event
    if not completed_data and current_event in {"response.completed", "response.done"} and current_data_lines:
        data_str = "\n".join(current_data_lines)
        if data_str.strip():
            try:
                completed_data = _json.loads(data_str)
                terminal_event_name = current_event
            except _json.JSONDecodeError:
                pass

    if not completed_data:
        partial_event_count = sum(
            count
            for event_name, count in event_counts.items()
            if event_name.startswith("response.output")
            or event_name.startswith("response.function_call")
        )
        status = "NO_TERMINAL_PARTIAL" if partial_event_count else "NO_TERMINAL"
        _codex_log_summary(event_counts, elapsed, status)
        if partial_event_count:
            raise ValueError(
                "Codex API stream ended after partial output or tool-call events without a terminal response event."
            )
        raise ValueError("Codex API stream ended without response.done or response.completed.")

    _codex_log_summary(event_counts, elapsed, "OK", terminal_event_name=terminal_event_name)
    return _parse_codex_response(completed_data)


def _codex_log_summary(
    event_counts: Dict[str, int],
    elapsed: float,
    status: str,
    terminal_event_name: Optional[str] = None,
):
    """Log a one-line summary of a Codex SSE stream."""
    total = sum(event_counts.values())
    parts = [f"{v} {k}" for k, v in sorted(event_counts.items(), key=lambda x: -x[1])]
    summary = ", ".join(parts[:5])  # Top 5 event types
    if len(parts) > 5:
        summary += f", +{len(parts) - 5} more types"
    terminal_suffix = f", terminal={terminal_event_name}" if terminal_event_name else ""
    print(
        f"[CODEX] Stream {status}{terminal_suffix} in {elapsed:.1f}s ({total} events: {summary})"
        if total
        else f"[CODEX] Stream {status}{terminal_suffix} in {elapsed:.1f}s (0 events)"
    )


def _parse_codex_response(data: dict) -> dict:
    """Parse raw Codex/OpenAI JSON response into normalized format.

    Handles both direct response objects and potentially nested structures
    (e.g. {"response": {actual response}}) from the Codex SSE endpoint.
    Converts to Anthropic-native assistant_message format for checkpoint storage.
    """
    # Handle potential nesting: some SSE endpoints wrap the response under a key
    if "output" not in data and isinstance(data.get("response"), dict):
        data = data["response"]

    text_parts = []
    tool_calls = []
    content_blocks = []

    for item in data.get("output", []):
        item_type = item.get("type", "")

        if item_type == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text", "")
                    text_parts.append(text)
                    content_blocks.append({"type": "text", "text": text})

        elif item_type == "function_call":
            args_raw = item.get("arguments", "{}")
            if isinstance(args_raw, str):
                try:
                    args = _json.loads(args_raw)
                except _json.JSONDecodeError:
                    args = {}
            else:
                args = args_raw

            tc = {
                "id": item.get("call_id", ""),
                "name": item.get("name", ""),
                "args": args,
            }
            tool_calls.append(tc)
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["args"],
            })

    full_text = "".join(text_parts)

    # Fallback: use the top-level output_text convenience field if we didn't
    # find text in the output array (handles unexpected response shapes)
    if not full_text and not tool_calls and data.get("output_text"):
        full_text = data["output_text"]
        content_blocks.append({"type": "text", "text": full_text})

    return {
        "text": full_text,
        "tool_calls": tool_calls,
        "assistant_message": {"role": "assistant", "content": content_blocks},
        "raw_response": data,
    }


async def _call_llm(
    model: str,
    static_system: str,
    dynamic_context: str,
    messages: list,
    tool_schemas: list,
    temperature: float,
    max_tokens: int = 16384,
) -> dict:
    """Dispatch LLM call to the appropriate provider based on model ID.

    OpenAI routing priority:
    1. Codex OAuth (subscription credits) — if tokens exist
    2. OPENAI_API_KEY (pay-per-token) — if env var set
    3. Error — no OpenAI auth configured

    Returns normalized dict: {text, tool_calls, assistant_message, raw_response}
    """
    if _is_openai_model(model):
        # Priority 1: Codex OAuth (subscription credits)
        codex_failed = False
        try:
            from services.codex_oauth_service import has_valid_tokens
            if await has_valid_tokens():
                print(f"[LLM] Calling Codex OAuth ({model})")
                return await _call_codex(model, static_system, dynamic_context, messages, tool_schemas, temperature, max_tokens)
        except ImportError:
            pass
        except (ValueError, Exception) as e:
            codex_failed = True
            print(f"[LLM] Codex OAuth failed ({e}), checking API key fallback...")

        # Priority 2: API key (pay-per-token)
        if os.getenv("OPENAI_API_KEY"):
            if codex_failed:
                # Notify user that we fell back to pay-per-token
                try:
                    from services.push_service import send_push_notification
                    asyncio.create_task(send_push_notification(
                        "OpenAI Auth Fallback",
                        "Codex OAuth failed — using API key (pay-per-token). Re-login in Settings.",
                    ))
                except Exception:
                    pass
            print(f"[LLM] Calling OpenAI API ({model})")
            return await _call_openai(model, static_system, dynamic_context, messages, tool_schemas, temperature, max_tokens)

        raise ValueError("No OpenAI auth configured. Set OPENAI_API_KEY or sign in with Codex OAuth in Settings.")

    print(f"[LLM] Calling Anthropic ({model})")
    return await _call_anthropic(model, static_system, dynamic_context, messages, tool_schemas, temperature, max_tokens)



async def stream_with_memory(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    attachments: Optional[List[dict]] = None,
) -> AsyncGenerator[str, None]:
    """Stream a response while maintaining conversation memory.

    This is the legacy string-only generator. Use stream_with_memory_events for
    structured event streaming.
    """
    async for event in stream_with_memory_events(
        message, conversation_id, system_prompt, model, temperature,
        attachments=attachments,
    ):
        # Only yield content events as strings (backwards compatibility)
        if event.get("type") == EventType.CONTENT and event.get("content"):
            yield event["content"]


# Human-readable labels for tool progress events
_TOOL_LABELS: Dict[str, str] = {
    # Memory
    "remember_search": "Searching memories",
    "remember_update": "Saving memory",
    "remember_forget": "Forgetting memory",
    # Web
    "web_search": "Searching the web",
    "fetch_page_content": "Reading page",
    # Messaging
    "send_message": "Sending message",
    "send_sms": "Sending SMS",
    "send_whatsapp": "Sending WhatsApp",
    "send_imessage": "Sending iMessage",
    "get_recent_messages": "Reading messages",
    # Contacts
    "lookup_contact": "Looking up contact",
    "lookup_phone": "Looking up phone number",
    # Documents
    "save_document": "Saving document",
    "read_document": "Reading document",
    "edit_document": "Editing document",
    "search_documents": "Searching documents",
    "list_documents": "Listing documents",
    "delete_document": "Deleting document",
    # Scheduled events
    "schedule_event": "Scheduling event",
    "list_scheduled_events": "Checking schedule",
    "cancel_scheduled_event": "Cancelling event",
    # Code execution
    "execute_code": "Running Python",
    "execute_javascript": "Running JavaScript",
    "execute_sql": "Running SQL",
    "execute_shell": "Running shell command",
    "list_sandbox_files": "Listing sandbox files",
    "read_sandbox_file": "Reading sandbox file",
    # File storage
    "save_to_storage": "Saving to storage",
    "list_storage_files": "Listing files",
    "get_storage_file_url": "Getting file URL",
    "read_storage_file": "Reading file",
    "tag_storage_file": "Tagging file",
    "delete_storage_file": "Deleting file",
    # Persistent databases
    "create_persistent_db": "Creating database",
    "query_persistent_db": "Querying database",
    "list_persistent_dbs": "Listing databases",
    "delete_persistent_db": "Deleting database",
    # Push / widget
    "send_push_notification": "Sending notification",
    "update_widget": "Updating widget",
    "get_widget_state_tool": "Reading widget state",
    "update_widget_code": "Updating widget code",
    "clear_widget_code": "Clearing widget code",
    # HTML hosting
    "create_hosted_page": "Publishing page",
    "update_hosted_page": "Updating page",
    "delete_hosted_page": "Deleting page",
    "check_hosted_slug": "Checking URL slug",
    # Custom MCP
    "search_mcp_servers": "Searching MCP servers",
    "add_mcp_server": "Adding MCP server",
    "list_custom_servers": "Listing MCP servers",
    "remove_mcp_server": "Removing MCP server",
    "update_mcp_server": "Updating MCP server",
    "restart_mcp_server": "Restarting MCP server",
    # Planning
    "create_plan": "Creating plan",
    "update_plan_step": "Updating plan step",
    "edit_plan": "Editing plan",
    "complete_plan": "Completing plan",
    # Orchestrator / workers
    "spawn_worker": "Spawning worker",
    "spawn_cc_worker": "Spawning Claude Code worker",
    "check_worker": "Checking worker",
    "list_workers": "Listing workers",
    "cancel_worker": "Cancelling worker",
    "wait_for_workers": "Waiting for workers",
    "send_to_worker": "Sending to worker",
    # Evolution
    "trigger_self_evolution": "Triggering evolution",
    "get_evolution_status": "Checking evolution status",
    # Heartbeat
    "review_heartbeat": "Reviewing heartbeat",
    # NotebookLM (nlm_*)
    "nlm_list_notebooks": "Listing notebooks",
    "nlm_create_notebook": "Creating notebook",
    "nlm_delete_notebook": "Deleting notebook",
    "nlm_get_notebook": "Getting notebook",
    "nlm_describe_notebook": "Describing notebook",
    "nlm_rename_notebook": "Renaming notebook",
    "nlm_add_source": "Adding source",
    "nlm_add_drive_source": "Adding Drive source",
    "nlm_list_sources": "Listing sources",
    "nlm_delete_source": "Deleting source",
    "nlm_rename_source": "Renaming source",
    "nlm_describe_source": "Describing source",
    "nlm_get_source_text": "Reading source text",
    "nlm_ask": "Asking NotebookLM",
    "nlm_configure_chat": "Configuring NotebookLM",
    "nlm_research": "Starting research",
    "nlm_poll_research": "Checking research",
    "nlm_import_research": "Importing research",
    "nlm_generate_artifact": "Generating artifact",
    "nlm_wait_artifact": "Waiting for artifact",
    "nlm_delete_artifact": "Deleting artifact",
    "nlm_revise_slides": "Revising slides",
    "nlm_share_status": "Checking sharing",
    "nlm_share_public": "Updating sharing",
    "nlm_share_invite": "Inviting collaborator",
    "nlm_note": "Managing note",
    "nlm_push_document": "Pushing document to notebook",
    "nlm_push_file": "Pushing file to notebook",
}


def _tool_label(tool_name: str) -> str:
    """Return a human-readable label for a tool name."""
    if tool_name in _TOOL_LABELS:
        return _TOOL_LABELS[tool_name]
    # MCP tool prefix (e.g. "whatsapp_send_message" → "Whatsapp: Send Message")
    if "_" in tool_name:
        parts = tool_name.split("_", 1)
        return f"{parts[0].title()}: {parts[1].replace('_', ' ').title()}"
    return tool_name.replace("_", " ").title()


async def stream_with_memory_events(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    attachments: Optional[List[dict]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Stream a response with structured events while maintaining conversation memory."""
    from services.memory_service import retrieve_memories, extract_and_store_memories, Memory
    from services.graph.tools import set_current_conversation_id
    from services.checkpoint_store import get_messages, save_messages
    assistant_content_emitted = False

    # Set the conversation ID for code execution context
    set_current_conversation_id(conversation_id)

    # Load existing messages from checkpoint store
    messages = await get_messages(conversation_id)

    # Add the new user message (with attachments if present)
    messages.append(_build_human_message(message, attachments))

    # ===== MEMORY RETRIEVAL (with deep retrieval gate) =====
    # Emit progress event for memory search
    yield create_event(EventType.PROGRESS, conversation_id,
        step="memory_search",
        status="started",
        message="Searching memory..."
    )

    turn_count = sum(1 for m in messages if _msg_role(m) == "user")
    retrieved_memories: List[Memory] = []
    try:
        from services.deep_retrieval_service import should_deep_retrieve, deep_retrieve_memories
        if await should_deep_retrieve(message, conversation_id, turn_count):
            # Format recent messages for Haiku query generation
            recent_msgs = [
                {"role": "human" if _msg_role(m) == "user" else "assistant",
                 "content": _msg_content_text(m)}
                for m in messages[-5:]
                if _msg_role(m) in ("user", "assistant")
            ]
            try:
                retrieved_memories = await asyncio.wait_for(
                    deep_retrieve_memories(message, recent_msgs, limit=MAX_NORMAL_MEMORIES),
                    timeout=10.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                print(f"Deep retrieval failed, falling back to normal: {e}")
                retrieved_memories = await retrieve_memories(message, limit=MAX_NORMAL_MEMORIES)
        else:
            retrieved_memories = await retrieve_memories(message, limit=MAX_NORMAL_MEMORIES)
        yield create_event(EventType.PROGRESS, conversation_id,
            step="memory_search",
            status="completed",
            message=f"Found {len(retrieved_memories)} memories" if retrieved_memories else "No relevant memories",
            count=len(retrieved_memories)
        )
    except Exception as e:
        print(f"Memory retrieval failed: {e}")
        yield create_event(EventType.PROGRESS, conversation_id,
            step="memory_search",
            status="error",
            message="Memory search failed"
        )

    # ===== ENRICHMENT LOADING (from previous reflection) =====
    enriched_memories = []
    try:
        from services.reflection_service import load_enrichments
        enriched_memories = await load_enrichments(conversation_id, limit=MAX_ENRICHED_MEMORIES)
        if enriched_memories:
            print(f"[ENRICHMENT] Loaded {len(enriched_memories)} enrichments for {conversation_id}")
    except Exception as e:
        print(f"Enrichment loading failed: {e}")

    # ===== DOCUMENT RETRIEVAL =====
    relevant_documents = []
    try:
        from services.document_service import retrieve_relevant_documents
        relevant_documents = await retrieve_relevant_documents(message, limit=3)
    except Exception as e:
        print(f"Document retrieval failed: {e}")

    # ===== HEARTBEAT BRIEFING =====
    briefing_context = ""
    try:
        from services.heartbeat.heartbeat_service import get_pending_briefing
        briefing = await get_pending_briefing()
        if briefing:
            briefing_context = f"\n\n## Recent Awareness\nWhile you were away, you noticed:\n{briefing}"
    except Exception as e:
        print(f"Heartbeat briefing failed: {e}")

    # ===== TOOL SELECTION =====
    tools = await get_available_tools()

    # Build enhanced system prompt with memories, tool descriptions, and current time
    memory_context = build_memory_context(
        retrieved_memories, tools=tools, documents=relevant_documents,
        enriched_memories=enriched_memories,
    )
    now = datetime.now()
    time_context = f"\n\nCurrent date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"
    # Split system prompt into static (cacheable) and dynamic (per-turn) parts
    static_system = (
        system_prompt
        + EDWARD_CHARACTER
        + _build_platform_context()
    )
    dynamic_context = memory_context + briefing_context + time_context

    # Build tool schemas in provider-appropriate format
    if _is_openai_model(model):
        tool_schemas = tools_to_openai_schemas(tools) if tools else []
    else:
        tool_schemas = tools_to_anthropic_schemas(tools) if tools else []

    full_response = ""
    tool_calls_made = []
    needs_streaming = True

    # Track repeated failures: "tool_name:args_hash" -> count
    _failure_tracker: Dict[str, int] = {}
    # Track consecutive iterations where ALL tool calls fail
    consecutive_error_iterations = 0

    # Tool call loop - default 30 rounds, scales up to 100 when a plan is active
    max_tool_iterations = 30
    iteration = 0

    # Track whether a fatal LLM error occurred (to skip post-processing)
    _llm_error_occurred = False

    while iteration < max_tool_iterations:
        iteration += 1

        # Emit thinking event when processing
        if iteration > 1:
            yield create_event(EventType.THINKING, conversation_id, content="Thinking...")

        # Emit "Generating response..." BEFORE the model call (visible while model thinks)
        yield create_event(EventType.PROGRESS, conversation_id,
            step="generating",
            status="started",
            message="Generating response..."
        )

        # Keepalive heartbeat: yield progress updates every 5s while waiting for model
        _llm_task = asyncio.ensure_future(_call_llm(
            model, static_system, dynamic_context, messages, tool_schemas, temperature,
        ))
        _KEEPALIVE_INTERVAL = 5.0
        _elapsed_s = 0.0
        try:
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(_llm_task), timeout=_KEEPALIVE_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    _elapsed_s += _KEEPALIVE_INTERVAL
                    yield create_event(EventType.PROGRESS, conversation_id,
                        step="generating",
                        status="started",
                        message=f"Generating response... ({int(_elapsed_s)}s)"
                    )
        except Exception as e:
            _llm_task.cancel()
            error_msg = str(e)
            print(f"[LLM ERROR] {error_msg}")
            yield create_event(EventType.ERROR, conversation_id, error=error_msg)
            full_response = f"I encountered an error: {error_msg}"
            assistant_content_emitted = True
            yield create_event(EventType.CONTENT, conversation_id, content=full_response)
            needs_streaming = False
            _llm_error_occurred = True
            break

        # Extract tool calls from normalized result
        response_tool_calls = result["tool_calls"]

        # Check if there are tool calls
        if response_tool_calls:
            # Add the assistant message (always Anthropic-native format for storage)
            messages.append(result["assistant_message"])

            # Execute each tool call with event streaming
            for tool_call in response_tool_calls:
                tool_calls_made.append(tool_call)

                # Circuit breaker: block repeated identical failures
                failure_key = f"{tool_call['name']}:{hashlib.md5(_json.dumps(tool_call.get('args', {}), sort_keys=True).encode()).hexdigest()}"
                if _failure_tracker.get(failure_key, 0) >= 1:
                    tool_result = f"BLOCKED: {tool_call['name']} already failed with these arguments. Fix the arguments or use a different approach."
                    print(f"[CIRCUIT BREAKER] Blocked repeated failure: {tool_call['name']}")
                    # Emit events so the UI shows the blocked call instead of going silent
                    yield create_event(EventType.TOOL_START, conversation_id, tool_name=tool_call['name'])
                    yield create_event(EventType.TOOL_END, conversation_id, tool_name=tool_call['name'], result=tool_result)
                    messages.append(_make_tool_result_message(tool_call['id'], tool_result))
                    continue

                # Emit progress event for tool execution
                yield create_event(EventType.PROGRESS, conversation_id,
                    step="tool_execution",
                    status="started",
                    message=_tool_label(tool_call['name']),
                    tool_name=tool_call['name']
                )

                # Stream events from tool execution
                tool_result = None
                async for event in execute_tool_call_with_events(tool_call, tools, conversation_id):
                    if "_result" in event:
                        tool_result = event["_result"]
                    else:
                        yield event

                # Track failures for circuit breaker
                if str(tool_result).startswith("Tool error:"):
                    _failure_tracker[failure_key] = _failure_tracker.get(failure_key, 0) + 1

                # Emit progress event for tool completion
                yield create_event(EventType.PROGRESS, conversation_id,
                    step="tool_execution",
                    status="completed",
                    message=_tool_label(tool_call['name']),
                    tool_name=tool_call['name']
                )

                print(f"Tool {tool_call['name']} result: {str(tool_result)[:200]}..." if len(str(tool_result)) > 200 else f"Tool {tool_call['name']} result: {tool_result}")

                # Add tool result as a message
                messages.append(_make_tool_result_message(tool_call['id'], str(tool_result)))

            # Track consecutive all-failed iterations
            # Check the last N tool_result messages (where N = number of tool calls this iteration)
            recent_results = messages[-len(response_tool_calls):]
            iteration_had_success = any(
                not _get_tool_result_text(m).startswith("Tool error:")
                and not _get_tool_result_text(m).startswith("BLOCKED:")
                for m in recent_results
                if _msg_role(m) == "user" and _is_tool_result_message(m)
            )
            if iteration_had_success:
                consecutive_error_iterations = 0
            else:
                consecutive_error_iterations += 1
                print(f"[TOOL LOOP] Consecutive all-failed iterations: {consecutive_error_iterations}")

            # Plan-aware circuit breaker threshold
            from services.graph.tools import get_active_plan, get_plan_status
            active_plan = get_active_plan(conversation_id)
            plan_status = get_plan_status(conversation_id)
            error_threshold = 6 if (plan_status and plan_status["incomplete_titles"]) else 3

            if consecutive_error_iterations >= error_threshold:
                print(f"[TOOL LOOP] Breaking after {consecutive_error_iterations} consecutive all-failed iterations")
                break

            # Dynamically adjust tool loop limit when a plan is active
            if active_plan:
                plan_iterations = min(100, len(active_plan) * 5 + 10)
                max_tool_iterations = max(max_tool_iterations, plan_iterations)

            # Continue loop to allow more tool calls
            continue
        else:
            # No more tool calls — check if plan has incomplete steps
            from services.graph.tools import get_plan_status as _get_ps
            plan_st = _get_ps(conversation_id)

            if plan_st and plan_st["incomplete_titles"] and iteration < max_tool_iterations:
                # LLM stopped making tool calls but plan isn't done — nudge it
                if result["text"]:
                    messages.append({"role": "assistant", "content": result["text"]})

                remaining = "\n".join(f"- {t}" for t in plan_st["incomplete_titles"])
                nudge = (
                    f"You still have {len(plan_st['incomplete_titles'])} incomplete plan step(s):\n{remaining}\n\n"
                    "Continue working on the next step. Do NOT call complete_plan until all steps are done."
                )
                messages.append({"role": "user", "content": nudge})
                print(f"[PLAN NUDGE] {len(plan_st['incomplete_titles'])} steps remaining, nudging LLM to continue")
                continue

            # No plan or plan is complete — use this response's content
            if result["text"]:
                full_response = result["text"]
                assistant_content_emitted = True
                yield create_event(EventType.CONTENT, conversation_id, content=full_response)
                needs_streaming = False
                try:
                    from services.governance.action_receipts import log_turn_sample
                    log_turn_sample(
                        conversation_id=conversation_id,
                        message_preview=message[:100],
                        response_preview=full_response[:200],
                        tool_calls_made=[tc["name"] for tc in tool_calls_made],
                        has_plan=any(tc["name"] == "create_plan" for tc in tool_calls_made),
                        plan_completed=any(tc["name"] == "complete_plan" for tc in tool_calls_made),
                    )
                except Exception:
                    pass  # Never affects response pipeline
            yield create_event(EventType.PROGRESS, conversation_id,
                step="generating",
                status="completed",
                message="Response complete"
            )
            break

    # Only stream a new response if the loop didn't produce one
    if needs_streaming and not _llm_error_occurred:
        if tool_calls_made:
            # Tools executed successfully but LLM returned no final text — this is valid
            # (GPT models return function_call without accompanying text, unlike Claude)
            tool_summary = ", ".join(sorted(set(tc["name"] for tc in tool_calls_made)))
            full_response = f"[Completed: {tool_summary}]"
            assistant_content_emitted = True
            yield create_event(EventType.CONTENT, conversation_id, content=full_response)
        else:
            print(f"[WARNING] Tool loop exited after {iteration}/{max_tool_iterations} iterations without final response, streaming new response")
            fallback_static = static_system + "\n\nYou have used all available tool iterations. Summarize what you accomplished and respond to the user. Do not attempt any more tool calls."
            if _is_openai_model(model):
                # OpenAI: non-streaming fallback (Responses API streaming is complex)
                fallback_result = await _call_llm(
                    model, fallback_static, dynamic_context, messages, [], temperature,
                )
                full_response = fallback_result["text"]
                if full_response:
                    assistant_content_emitted = True
                    yield create_event(EventType.CONTENT, conversation_id, content=full_response)
            else:
                # Anthropic: streaming fallback
                client = _get_client()
                fallback_kwargs = _build_api_kwargs(
                    model, fallback_static, dynamic_context, messages,
                    [], temperature,  # No tools for fallback
                )
                async with client.messages.stream(**fallback_kwargs) as stream:
                    async for text in stream.text_stream:
                        full_response += text
                        if text:
                            assistant_content_emitted = True
                        yield create_event(EventType.CONTENT, conversation_id, content=text)

    # Safety net: if no content was ever produced, send a plan-aware fallback
    if not full_response.strip():
        from services.graph.tools import get_plan_status as _get_ps_fallback
        ps_fallback = _get_ps_fallback(conversation_id)
        if ps_fallback and ps_fallback["incomplete_titles"]:
            remaining_list = ", ".join(ps_fallback["incomplete_titles"])
            full_response = (
                f"I completed {ps_fallback['completed']} of {ps_fallback['total']} planned steps. "
                f"The following steps were not finished: {remaining_list}. "
                "Let me know if you'd like me to continue."
            )
        else:
            full_response = "I completed the requested actions. Let me know if you need anything else!"
        assistant_content_emitted = True
        yield create_event(EventType.CONTENT, conversation_id, content=full_response)

    # Add assistant response to messages
    messages.append({"role": "assistant", "content": full_response})

    # Get final plan state for checkpoint persistence
    from services.graph.tools import get_active_plan as _get_plan_for_save
    final_plan = _get_plan_for_save(conversation_id)

    # Save conversation state to checkpoint store
    try:
        await save_messages(conversation_id, messages, metadata={
            "system_prompt": system_prompt,
            "model": model,
            "temperature": temperature,
            "current_response": full_response,
            "plan_steps": final_plan,
            "tool_calls": [
                {"name": tc["name"], "args": tc["args"]}
                for tc in tool_calls_made
            ] if tool_calls_made else [],
        })
    except Exception as e:
        error_msg = f"Failed to save conversation state: {e}"
        print(f"[STREAM ERROR] {error_msg}")
        yield create_event(EventType.ERROR, conversation_id, error=error_msg)
        if not assistant_content_emitted:
            assistant_content_emitted = True
            yield create_event(
                EventType.CONTENT,
                conversation_id,
                content="I encountered an error before I could finish the response. Please try again.",
            )
        yield create_event(EventType.DONE, conversation_id)
        return

    # ===== DONE EVENT — generator exhausts here, HTTP connection closes immediately =====
    yield create_event(EventType.DONE, conversation_id)

    # ===== MEMORY EXTRACTION (true fire-and-forget via create_task) =====
    # IMPORTANT: must use create_task, not await — the generator must fully exhaust
    # after yield DONE so FastAPI closes the HTTP response immediately. On Ngrok,
    # awaiting here keeps the connection open and Ngrok's tunnel timeout can drop it
    # before the browser confirms receipt of the done event.
    if not _llm_error_occurred:
        async def _post_turn_work():
            try:
                messages_for_extraction = [
                    {"role": "human" if _msg_role(m) == "user" else "assistant",
                     "content": _msg_content_text(m)}
                    for m in messages[-10:]
                    if _msg_role(m) in ("user", "assistant")
                ]
                await asyncio.wait_for(
                    extract_and_store_memories(
                        messages=messages_for_extraction,
                        conversation_id=conversation_id,
                        existing_memories=retrieved_memories
                    ),
                    timeout=30,
                )

                # Fire-and-forget search tag generation
                from services.search_tag_service import generate_search_tags_safe
                asyncio.create_task(generate_search_tags_safe(conversation_id, messages_for_extraction))

                # Fire-and-forget reflection for next turn's enrichment
                try:
                    from services.reflection_service import should_reflect, run_reflection_safe
                    if should_reflect(messages_for_extraction, turn_count):
                        asyncio.create_task(run_reflection_safe(
                            conversation_id, messages_for_extraction,
                            [m.id for m in retrieved_memories]
                        ))
                except Exception as e:
                    print(f"Reflection fire-and-forget failed: {e}")
            except asyncio.TimeoutError:
                print(f"Memory extraction timed out after 30s for conversation {conversation_id}")
            except Exception as e:
                print(f"Memory extraction failed: {e}")

        asyncio.create_task(_post_turn_work())


async def chat_with_memory(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    attachments: Optional[List[dict]] = None,
    skip_memory: bool = False,
    is_worker: bool = False,
) -> str:
    """Get a non-streaming response while maintaining conversation memory.

    Args:
        skip_memory: Skip memory retrieval, enrichment, and document retrieval (for workers)
        is_worker: Use worker-filtered tools (no evolution/orchestrator tools)
    """
    from services.memory_service import retrieve_memories, extract_and_store_memories, Memory
    from services.checkpoint_store import get_messages, save_messages

    # Load existing messages from checkpoint store
    messages = await get_messages(conversation_id)

    # Add the new user message (with attachments if present)
    messages.append(_build_human_message(message, attachments))

    # ===== MEMORY RETRIEVAL (with deep retrieval gate) =====
    turn_count = sum(1 for m in messages if _msg_role(m) == "user")
    retrieved_memories: List[Memory] = []
    enriched_memories = []
    relevant_documents = []

    if not skip_memory:
        try:
            from services.deep_retrieval_service import should_deep_retrieve, deep_retrieve_memories
            if await should_deep_retrieve(message, conversation_id, turn_count):
                recent_msgs = [
                    {"role": "human" if _msg_role(m) == "user" else "assistant",
                     "content": _msg_content_text(m)}
                    for m in messages[-5:]
                    if _msg_role(m) in ("user", "assistant")
                ]
                try:
                    retrieved_memories = await asyncio.wait_for(
                        deep_retrieve_memories(message, recent_msgs, limit=MAX_NORMAL_MEMORIES),
                        timeout=10.0,
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    print(f"Deep retrieval failed, falling back to normal: {e}")
                    retrieved_memories = await retrieve_memories(message, limit=MAX_NORMAL_MEMORIES)
            else:
                retrieved_memories = await retrieve_memories(message, limit=MAX_NORMAL_MEMORIES)
        except Exception as e:
            print(f"Memory retrieval failed: {e}")

        # ===== ENRICHMENT LOADING (from previous reflection) =====
        try:
            from services.reflection_service import load_enrichments
            enriched_memories = await load_enrichments(conversation_id, limit=MAX_ENRICHED_MEMORIES)
            if enriched_memories:
                print(f"[ENRICHMENT] Loaded {len(enriched_memories)} enrichments for {conversation_id}")
        except Exception as e:
            print(f"Enrichment loading failed: {e}")

        # ===== DOCUMENT RETRIEVAL =====
        try:
            from services.document_service import retrieve_relevant_documents
            relevant_documents = await retrieve_relevant_documents(message, limit=3)
        except Exception as e:
            print(f"Document retrieval failed: {e}")

    # ===== HEARTBEAT BRIEFING (non-streaming) =====
    briefing_context_sync = ""
    try:
        from services.heartbeat.heartbeat_service import get_pending_briefing
        briefing_sync = await get_pending_briefing()
        if briefing_sync:
            briefing_context_sync = f"\n\n## Recent Awareness\nWhile you were away, you noticed:\n{briefing_sync}"
    except Exception as e:
        print(f"Heartbeat briefing failed: {e}")

    # ===== ORCHESTRATOR BRIEFING =====
    orchestrator_context = ""
    if not is_worker:
        try:
            from services.orchestrator_service import get_active_tasks_briefing
            orch_briefing = await get_active_tasks_briefing(conversation_id)
            if orch_briefing:
                orchestrator_context = f"\n\n{orch_briefing}"
        except Exception as e:
            print(f"Orchestrator briefing failed: {e}")

    # ===== TOOL SELECTION =====
    # Workers get filtered set (no evolution/orchestrator), otherwise all tools
    if is_worker:
        from services.tool_registry import get_worker_tools
        tools = await get_worker_tools()
    else:
        tools = await get_available_tools()

    # Build enhanced system prompt with memories, tool descriptions, and current time
    memory_context = build_memory_context(
        retrieved_memories, tools=tools, documents=relevant_documents,
        enriched_memories=enriched_memories,
    )
    now = datetime.now()
    time_context = f"\n\nCurrent date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"
    # Split system prompt into static (cacheable) and dynamic (per-turn) parts
    static_system = (
        system_prompt
        + EDWARD_CHARACTER
        + _build_platform_context()
    )
    dynamic_context = memory_context + briefing_context_sync + orchestrator_context + time_context

    # Build tool schemas in provider-appropriate format
    if _is_openai_model(model):
        tool_schemas = tools_to_openai_schemas(tools) if tools else []
    else:
        tool_schemas = tools_to_anthropic_schemas(tools) if tools else []

    full_response = ""
    tool_calls_made = []

    # Track repeated failures: "tool_name:args_hash" -> count
    _failure_tracker: Dict[str, int] = {}
    # Track consecutive iterations where ALL tool calls fail
    consecutive_error_iterations = 0

    # Tool call loop - default 30 rounds, scales up to 100 when a plan is active
    max_tool_iterations = 30
    iteration = 0

    while iteration < max_tool_iterations:
        iteration += 1

        # Call LLM (dispatches to Anthropic or OpenAI based on model)
        result = await _call_llm(
            model, static_system, dynamic_context, messages,
            tool_schemas, temperature,
        )

        # Extract tool calls from normalized result
        response_tool_calls = result["tool_calls"]

        # Check if there are tool calls
        if response_tool_calls:
            # Add the assistant message (always Anthropic-native format for storage)
            messages.append(result["assistant_message"])

            # Execute each tool call
            for tool_call in response_tool_calls:
                tool_calls_made.append(tool_call)

                # Circuit breaker: block repeated identical failures
                failure_key = f"{tool_call['name']}:{hashlib.md5(_json.dumps(tool_call.get('args', {}), sort_keys=True).encode()).hexdigest()}"
                if _failure_tracker.get(failure_key, 0) >= 1:
                    tool_result = f"BLOCKED: {tool_call['name']} already failed with these arguments. Fix the arguments or use a different approach."
                    print(f"[CIRCUIT BREAKER] Blocked repeated failure: {tool_call['name']}")
                    messages.append(_make_tool_result_message(tool_call['id'], tool_result))
                    continue

                tool_result = await execute_tool_call(tool_call, tools)

                # Track failures for circuit breaker
                if str(tool_result).startswith("Tool error:"):
                    _failure_tracker[failure_key] = _failure_tracker.get(failure_key, 0) + 1

                print(f"Tool {tool_call['name']} result: {tool_result[:200]}..." if len(str(tool_result)) > 200 else f"Tool {tool_call['name']} result: {tool_result}")

                # Add tool result as a message
                messages.append(_make_tool_result_message(tool_call['id'], str(tool_result)))

            # Track consecutive all-failed iterations
            recent_results = messages[-len(response_tool_calls):]
            iteration_had_success = any(
                not _get_tool_result_text(m).startswith("Tool error:")
                and not _get_tool_result_text(m).startswith("BLOCKED:")
                for m in recent_results
                if _msg_role(m) == "user" and _is_tool_result_message(m)
            )
            if iteration_had_success:
                consecutive_error_iterations = 0
            else:
                consecutive_error_iterations += 1
                print(f"[TOOL LOOP] Consecutive all-failed iterations: {consecutive_error_iterations}")

            # Plan-aware circuit breaker threshold
            from services.graph.tools import get_active_plan, get_plan_status
            active_plan = get_active_plan(conversation_id)
            plan_status = get_plan_status(conversation_id)
            error_threshold = 6 if (plan_status and plan_status["incomplete_titles"]) else 3

            if consecutive_error_iterations >= error_threshold:
                print(f"[TOOL LOOP] Breaking after {consecutive_error_iterations} consecutive all-failed iterations")
                break

            # Dynamically adjust tool loop limit when a plan is active
            if active_plan:
                plan_iterations = min(100, len(active_plan) * 5 + 10)
                max_tool_iterations = max(max_tool_iterations, plan_iterations)

            # Continue loop to allow more tool calls
            continue
        else:
            # No more tool calls — check if plan has incomplete steps
            from services.graph.tools import get_plan_status as _get_ps_chat
            plan_st = _get_ps_chat(conversation_id)

            if plan_st and plan_st["incomplete_titles"] and iteration < max_tool_iterations:
                # LLM stopped making tool calls but plan isn't done — nudge it
                if result["text"]:
                    messages.append({"role": "assistant", "content": result["text"]})

                remaining = "\n".join(f"- {t}" for t in plan_st["incomplete_titles"])
                nudge = (
                    f"You still have {len(plan_st['incomplete_titles'])} incomplete plan step(s):\n{remaining}\n\n"
                    "Continue working on the next step. Do NOT call complete_plan until all steps are done."
                )
                messages.append({"role": "user", "content": nudge})
                print(f"[PLAN NUDGE] {len(plan_st['incomplete_titles'])} steps remaining, nudging LLM to continue")
                continue

            # No plan or plan is complete — use this response
            full_response = result["text"]
            break

    # If we exhausted iterations, get final response without tools
    if not full_response:
        if tool_calls_made:
            # Tools executed successfully but LLM returned no final text — this is valid
            # (GPT models return function_call without accompanying text, unlike Claude)
            tool_summary = ", ".join(sorted(set(tc["name"] for tc in tool_calls_made)))
            full_response = f"[Completed: {tool_summary}]"
        else:
            print(f"[WARNING] Tool loop exited after {iteration} iterations without final response, invoking fallback")
            fallback_static = static_system + "\n\nYou have used all available tool iterations. Summarize what you accomplished and respond to the user. Do not attempt any more tool calls."
            fallback_result = await _call_llm(
                model, fallback_static, dynamic_context, messages, [], temperature,
            )
            full_response = fallback_result["text"]

    # Add assistant response to messages
    messages.append({"role": "assistant", "content": full_response})

    # Get final plan state for checkpoint persistence
    from services.graph.tools import get_active_plan as _get_plan_for_save_sync
    final_plan_sync = _get_plan_for_save_sync(conversation_id)

    # Save conversation state to checkpoint store
    await save_messages(conversation_id, messages, metadata={
        "system_prompt": system_prompt,
        "model": model,
        "temperature": temperature,
        "current_response": full_response,
        "plan_steps": final_plan_sync,
        "tool_calls": [
            {"name": tc["name"], "args": tc["args"]}
            for tc in tool_calls_made
        ] if tool_calls_made else [],
    })

    # ===== MEMORY EXTRACTION =====
    try:
        messages_for_extraction = [
            {"role": "human" if _msg_role(m) == "user" else "assistant",
             "content": _msg_content_text(m)}
            for m in messages[-10:]
            if _msg_role(m) in ("user", "assistant")
        ]
        await extract_and_store_memories(
            messages=messages_for_extraction,
            conversation_id=conversation_id,
            existing_memories=retrieved_memories
        )

        # Fire-and-forget search tag generation
        from services.search_tag_service import generate_search_tags_safe
        asyncio.create_task(generate_search_tags_safe(conversation_id, messages_for_extraction))

        # Fire-and-forget reflection for next turn's enrichment
        try:
            from services.reflection_service import should_reflect, run_reflection_safe
            if should_reflect(messages_for_extraction, turn_count):
                asyncio.create_task(run_reflection_safe(
                    conversation_id, messages_for_extraction,
                    [m.id for m in retrieved_memories]
                ))
        except Exception as e:
            print(f"Reflection fire-and-forget failed: {e}")
    except Exception as e:
        print(f"Memory extraction failed: {e}")

    return full_response


def _is_tool_result_message(m: dict) -> bool:
    """Check if a message dict is a tool_result message."""
    content = m.get("content")
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    return False


def _get_tool_result_text(m: dict) -> str:
    """Extract the text content from a tool_result message."""
    content = m.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    return result_content
                if isinstance(result_content, list):
                    return "".join(
                        b.get("text", "") for b in result_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
    return ""
