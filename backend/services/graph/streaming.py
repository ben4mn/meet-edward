import asyncio
import hashlib
import json as _json
import re
import sys
from datetime import datetime
from typing import AsyncGenerator, List, Any, Dict, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_anthropic import ChatAnthropic

from services.tool_registry import get_available_tools, get_tool_descriptions

# Context budget constants
MAX_NORMAL_MEMORIES = 5
MAX_ENRICHED_MEMORIES = 5
MAX_DOCUMENTS = 3
MAX_MEMORY_CONTEXT_CHARS = 8000

# Models that support the effort parameter (Claude 4.6+)
_EFFORT_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6"}


def _build_llm(model: str, temperature: float, max_tokens: int = 16384) -> ChatAnthropic:
    """Build a ChatAnthropic instance, adding effort parameter for 4.6 models."""
    kwargs = {"model": model, "temperature": temperature, "max_tokens": max_tokens}
    if model in _EFFORT_MODELS:
        kwargs["model_kwargs"] = {"effort": "high"}
    return ChatAnthropic(**kwargs)


# Assumption awareness instructions - helps the agent recognize when it's making
# unverified inferences and either verify or state assumptions explicitly
PLANNING_DIRECTIVE = """

## Planning Requirement
You MUST call create_plan() BEFORE starting work when a request involves 3+ tool calls or multiple steps (build, create, research, multi-part requests). Do NOT skip planning on complex tasks."""

ASSUMPTION_AWARENESS_CONTEXT = """

## Assumption Awareness

When you make inferences not explicitly stated in tool results or user messages, pause to verify:

**Common assumption traps:**
- Name matching: When searching for "Clay" and finding messages, CHECK the `chat_name` or contact name in results - don't assume the first result is the right person
- Identity inference: Phone numbers, emails, and usernames don't automatically map to the name the user asked about
- Ambiguous references: "the meeting", "that file", "my friend" need clarification if context is unclear

**Verification protocol:**
1. If tool results include identifying fields (chat_name, contact_name, sender_name, etc.), COMPARE them to what the user asked about
2. If there's a mismatch or the field is missing, either:
   - Use another tool call to verify (if cheap, <1 additional call)
   - State your assumption explicitly: "I found messages from [X], assuming this is [Y] you asked about"
3. Never confidently report information about Person A when you only verified it's from Person B

**When assumptions are acceptable:**
- High confidence matches (exact name match in tool results)
- User has provided clear context that resolves ambiguity
- The assumption doesn't change the core answer

When uncertain, ask the user rather than guess wrong."""

AUTONOMY_FRAMEWORK = """

## Identity & Values

You are a personal AI assistant who grows smarter over time. You are not a generic chatbot — you serve a specific person, remember their context, and build knowledge proactively.

Core values:
- Genuine usefulness over impressiveness
- Action over inaction when the cost of being wrong is low
- Proactive knowledge building — don't wait to be asked to learn
- Honesty about uncertainty — say what you don't know

## Your Systems

You have multiple knowledge layers — use the right one for the situation:
- **Memories**: Short snippets auto-extracted from conversations. Good for quick recall of facts and preferences.
- **Documents**: Full text storage for articles, notes, and reference material. Search by title/content.
- **NotebookLM notebooks**: Deep, curated knowledge bases with source-grounded Q&A and citations. Use for research topics that need multiple sources cross-referenced.
- **Scheduled events**: Future actions and proactive outreach. You can remind, check in, and follow up.
- **Web search**: Real-time information. Use when your stored knowledge might be outdated.
- **File storage**: Persistent files and PDFs. Can be pushed to NotebookLM as sources.
- **Evolution engine**: You can modify your own code to fix bugs or improve capabilities. Consider this when you encounter recurring limitations.

## Autonomy

- Prefer action when reversible. Ask when consequences are hard to undo.
- Build knowledge proactively — if a topic comes up repeatedly, create a notebook for it.
- When uncertain, try then adjust. Don't ask-wait-ask repeatedly.
- You can evolve your own capabilities. If a tool doesn't exist for something you need, consider whether to build it.

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

    if "error" in spawn_result:
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


def _build_human_message(message: str, attachments: Optional[List[dict]] = None) -> HumanMessage:
    """Build a HumanMessage, optionally with multi-block content for attachments."""
    if not attachments:
        return HumanMessage(content=message)

    content_blocks = []

    # Add text content if present
    if message:
        content_blocks.append({"type": "text", "text": message})

    # Add attachment blocks
    attachment_metadata = []

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
            # Track metadata separately (not sent to API)
            attachment_metadata.append({
                "block_index": len(content_blocks) - 1,
                "file_id": att.get("file_id"),
                "filename": att.get("filename"),
                "mime_type": mime_type,
            })
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
            attachment_metadata.append({
                "block_index": len(content_blocks) - 1,
                "file_id": att.get("file_id"),
                "filename": att.get("filename"),
                "mime_type": mime_type,
            })
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

    return HumanMessage(
        content=content_blocks,
        additional_kwargs={"attachments": attachment_metadata} if attachment_metadata else {},
    )


async def stream_with_memory(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
) -> AsyncGenerator[str, None]:
    """Stream a response while maintaining conversation memory via graph state.

    This is the legacy string-only generator. Use stream_with_memory_events for
    structured event streaming.
    """
    async for event in stream_with_memory_events(
        message, conversation_id, system_prompt, model, temperature, graph,
        attachments=attachments,
    ):
        # Only yield content events as strings (backwards compatibility)
        if event.get("type") == EventType.CONTENT and event.get("content"):
            yield event["content"]


async def stream_with_memory_events(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Stream a response with structured events while maintaining conversation memory."""
    from services.memory_service import retrieve_memories, extract_and_store_memories, Memory
    from services.graph.tools import set_current_conversation_id

    # Set the conversation ID for code execution context
    set_current_conversation_id(conversation_id)

    config = {"configurable": {"thread_id": conversation_id}}

    # Get existing state for this conversation
    existing = await graph.aget_state(config)

    # Build message list from existing state or start fresh
    messages = list(existing.values.get("messages", [])) if existing.values else []

    # Use existing settings if available (allows mid-conversation setting changes)
    if existing.values:
        system_prompt = existing.values.get("system_prompt", system_prompt)
        model = existing.values.get("model", model)
        temperature = existing.values.get("temperature", temperature)

    # Add the new user message (with attachments if present)
    messages.append(_build_human_message(message, attachments))

    # ===== MEMORY RETRIEVAL (with deep retrieval gate) =====
    # Emit progress event for memory search
    yield create_event(EventType.PROGRESS, conversation_id,
        step="memory_search",
        status="started",
        message="Searching memory..."
    )

    turn_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    retrieved_memories: List[Memory] = []
    try:
        from services.deep_retrieval_service import should_deep_retrieve, deep_retrieve_memories
        if await should_deep_retrieve(message, conversation_id, turn_count):
            # Format recent messages for Haiku query generation
            recent_msgs = [
                {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
                for m in messages[-5:]
                if isinstance(m, (HumanMessage, AIMessage))
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

    # ===== DYNAMIC TOOL BINDING =====
    # Get tools based on enabled skills
    tools = await get_available_tools()

    # Build enhanced system prompt with memories, tool descriptions, and current time
    memory_context = build_memory_context(
        retrieved_memories, tools=tools, documents=relevant_documents,
        enriched_memories=enriched_memories,
    )
    now = datetime.now()
    time_context = f"\n\nCurrent date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"
    enhanced_system_prompt = system_prompt + AUTONOMY_FRAMEWORK + _build_platform_context() + memory_context + briefing_context + time_context + ASSUMPTION_AWARENESS_CONTEXT + PLANNING_DIRECTIVE

    # Create LLM with dynamic tool binding
    llm = _build_llm(model, temperature)
    llm_with_tools = llm.bind_tools(tools) if tools else llm

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

    while iteration < max_tool_iterations:
        iteration += 1

        # Emit thinking event when processing
        if iteration > 1:
            yield create_event(EventType.THINKING, conversation_id, content="Thinking...")

        # Get response (may include tool calls)
        full_messages = [SystemMessage(content=enhanced_system_prompt)] + messages
        response = await llm_with_tools.ainvoke(full_messages)

        # Check if there are tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Add the assistant message with tool calls
            messages.append(response)

            # Execute each tool call with event streaming
            for tool_call in response.tool_calls:
                tool_calls_made.append(tool_call)

                # Circuit breaker: block repeated identical failures
                failure_key = f"{tool_call['name']}:{hashlib.md5(_json.dumps(tool_call.get('args', {}), sort_keys=True).encode()).hexdigest()}"
                if _failure_tracker.get(failure_key, 0) >= 1:
                    tool_result = f"BLOCKED: {tool_call['name']} already failed with these arguments. Fix the arguments or use a different approach."
                    print(f"[CIRCUIT BREAKER] Blocked repeated failure: {tool_call['name']}")
                    # Emit events so the UI shows the blocked call instead of going silent
                    yield create_event(EventType.TOOL_START, conversation_id, tool_name=tool_call['name'])
                    yield create_event(EventType.TOOL_END, conversation_id, tool_name=tool_call['name'], result=tool_result)
                    messages.append(ToolMessage(content=tool_result, tool_call_id=tool_call['id']))
                    continue

                # Emit progress event for tool execution
                yield create_event(EventType.PROGRESS, conversation_id,
                    step="tool_execution",
                    status="started",
                    message=f"Running {tool_call['name']}...",
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
                    message=f"Completed {tool_call['name']}",
                    tool_name=tool_call['name']
                )

                print(f"Tool {tool_call['name']} result: {str(tool_result)[:200]}..." if len(str(tool_result)) > 200 else f"Tool {tool_call['name']} result: {tool_result}")

                # Add tool result as a message
                messages.append(ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tool_call['id']
                ))

            # Track consecutive all-failed iterations
            iteration_had_success = any(
                not str(tc_result).startswith("Tool error:") and not str(tc_result).startswith("BLOCKED:")
                for tc_result in [
                    m.content for m in messages[-len(response.tool_calls):]
                    if isinstance(m, ToolMessage)
                ]
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
                response_content = response.content
                if isinstance(response_content, list):
                    response_content = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in response_content
                    )
                if response_content:
                    messages.append(AIMessage(content=response_content))

                remaining = "\n".join(f"- {t}" for t in plan_st["incomplete_titles"])
                nudge = (
                    f"You still have {len(plan_st['incomplete_titles'])} incomplete plan step(s):\n{remaining}\n\n"
                    "Continue working on the next step. Do NOT call complete_plan until all steps are done."
                )
                messages.append(HumanMessage(content=nudge))
                print(f"[PLAN NUDGE] {len(plan_st['incomplete_titles'])} steps remaining, nudging LLM to continue")
                continue

            # No plan or plan is complete — use this response's content
            yield create_event(EventType.PROGRESS, conversation_id,
                step="generating",
                status="started",
                message="Generating response..."
            )
            response_content = response.content
            if isinstance(response_content, list):
                response_content = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in response_content
                )
            if response_content:
                full_response = response_content
                yield create_event(EventType.CONTENT, conversation_id, content=full_response)
                needs_streaming = False
            yield create_event(EventType.PROGRESS, conversation_id,
                step="generating",
                status="completed",
                message="Response complete"
            )
            break

    # Only stream a new response if the loop didn't produce one
    if needs_streaming:
        print(f"[WARNING] Tool loop exited after {iteration}/{max_tool_iterations} iterations without final response, streaming new response")
        fallback_prompt = enhanced_system_prompt + "\n\nYou have used all available tool iterations. Summarize what you accomplished and respond to the user. Do not attempt any more tool calls."
        full_messages = [SystemMessage(content=fallback_prompt)] + messages
        llm_no_tools = _build_llm(model, temperature)
        async for chunk in llm_no_tools.astream(full_messages):
            if chunk.content:
                if isinstance(chunk.content, str):
                    content = chunk.content
                elif isinstance(chunk.content, list):
                    content = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in chunk.content
                    )
                else:
                    content = ""
                if content:
                    full_response += content
                    yield create_event(EventType.CONTENT, conversation_id, content=content)

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
        yield create_event(EventType.CONTENT, conversation_id, content=full_response)

    # Add assistant response to messages
    messages.append(AIMessage(content=full_response))

    # Get final plan state for checkpoint persistence
    from services.graph.tools import get_active_plan as _get_plan_for_save
    final_plan = _get_plan_for_save(conversation_id)

    # Update graph state with the full conversation
    await graph.aupdate_state(config, {
        "messages": messages,
        "conversation_id": conversation_id,
        "system_prompt": system_prompt,
        "model": model,
        "temperature": temperature,
        "current_response": full_response,
        "is_complete": True,
        "retrieved_memories": [
            {
                "id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "importance": m.importance,
                "temporal_nature": m.temporal_nature,
                "tier": getattr(m, 'tier', 'observation') or 'observation',
                "reinforcement_count": getattr(m, 'reinforcement_count', 0) or 0,
                "score": m.score
            }
            for m in retrieved_memories
        ],
        "plan_steps": final_plan,
        "tool_calls": [
            {"name": tc["name"], "args": tc["args"]}
            for tc in tool_calls_made
        ] if tool_calls_made else [],
        "current_node": "complete",
        "node_history": ["preprocess", "retrieve_memory", "respond", "extract_memory"]
    }, as_node="extract_memory")

    # ===== MEMORY EXTRACTION (with timeout to guarantee done event) =====
    try:
        messages_for_extraction = [
            {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
            for m in messages[-10:]
            if isinstance(m, (HumanMessage, AIMessage))
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
    finally:
        # Always emit done event so the stream never hangs
        yield create_event(EventType.DONE, conversation_id)


async def chat_with_memory(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
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

    config = {"configurable": {"thread_id": conversation_id}}

    # Get existing state for this conversation
    existing = await graph.aget_state(config)

    # Build message list from existing state or start fresh
    messages = list(existing.values.get("messages", [])) if existing.values else []

    # Use existing settings if available
    if existing.values:
        system_prompt = existing.values.get("system_prompt", system_prompt)
        model = existing.values.get("model", model)
        temperature = existing.values.get("temperature", temperature)

    # Add the new user message (with attachments if present)
    messages.append(_build_human_message(message, attachments))

    # ===== MEMORY RETRIEVAL (with deep retrieval gate) =====
    turn_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    retrieved_memories: List[Memory] = []
    enriched_memories = []
    relevant_documents = []

    if not skip_memory:
        try:
            from services.deep_retrieval_service import should_deep_retrieve, deep_retrieve_memories
            if await should_deep_retrieve(message, conversation_id, turn_count):
                recent_msgs = [
                    {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
                    for m in messages[-5:]
                    if isinstance(m, (HumanMessage, AIMessage))
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

    # ===== DYNAMIC TOOL BINDING =====
    # Get tools based on enabled skills (workers get filtered set)
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
    enhanced_system_prompt = system_prompt + AUTONOMY_FRAMEWORK + _build_platform_context() + memory_context + briefing_context_sync + orchestrator_context + time_context + ASSUMPTION_AWARENESS_CONTEXT + PLANNING_DIRECTIVE

    # Create LLM with dynamic tool binding
    llm = _build_llm(model, temperature)
    llm_with_tools = llm.bind_tools(tools) if tools else llm

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

        # Get response (may include tool calls)
        full_messages = [SystemMessage(content=enhanced_system_prompt)] + messages
        response = await llm_with_tools.ainvoke(full_messages)

        # Check if there are tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Add the assistant message with tool calls
            messages.append(response)

            # Execute each tool call
            for tool_call in response.tool_calls:
                tool_calls_made.append(tool_call)

                # Circuit breaker: block repeated identical failures
                failure_key = f"{tool_call['name']}:{hashlib.md5(_json.dumps(tool_call.get('args', {}), sort_keys=True).encode()).hexdigest()}"
                if _failure_tracker.get(failure_key, 0) >= 1:
                    tool_result = f"BLOCKED: {tool_call['name']} already failed with these arguments. Fix the arguments or use a different approach."
                    print(f"[CIRCUIT BREAKER] Blocked repeated failure: {tool_call['name']}")
                    messages.append(ToolMessage(content=tool_result, tool_call_id=tool_call['id']))
                    continue

                tool_result = await execute_tool_call(tool_call, tools)

                # Track failures for circuit breaker
                if str(tool_result).startswith("Tool error:"):
                    _failure_tracker[failure_key] = _failure_tracker.get(failure_key, 0) + 1

                print(f"Tool {tool_call['name']} result: {tool_result[:200]}..." if len(str(tool_result)) > 200 else f"Tool {tool_call['name']} result: {tool_result}")

                # Add tool result as a message
                messages.append(ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tool_call['id']
                ))

            # Track consecutive all-failed iterations
            iteration_had_success = any(
                not str(m.content).startswith("Tool error:") and not str(m.content).startswith("BLOCKED:")
                for m in messages[-len(response.tool_calls):]
                if isinstance(m, ToolMessage)
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
                response_content = response.content
                if isinstance(response_content, list):
                    response_content = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in response_content
                    )
                if response_content:
                    messages.append(AIMessage(content=response_content))

                remaining = "\n".join(f"- {t}" for t in plan_st["incomplete_titles"])
                nudge = (
                    f"You still have {len(plan_st['incomplete_titles'])} incomplete plan step(s):\n{remaining}\n\n"
                    "Continue working on the next step. Do NOT call complete_plan until all steps are done."
                )
                messages.append(HumanMessage(content=nudge))
                print(f"[PLAN NUDGE] {len(plan_st['incomplete_titles'])} steps remaining, nudging LLM to continue")
                continue

            # No plan or plan is complete — use this response
            full_response = response.content
            break

    # If we exhausted iterations, get final response without tools
    if not full_response:
        print(f"[WARNING] Tool loop exited after {iteration} iterations without final response, invoking fallback")
        fallback_prompt = enhanced_system_prompt + "\n\nYou have used all available tool iterations. Summarize what you accomplished and respond to the user. Do not attempt any more tool calls."
        full_messages = [SystemMessage(content=fallback_prompt)] + messages
        llm_no_tools = _build_llm(model, temperature)
        final_response = await llm_no_tools.ainvoke(full_messages)
        full_response = final_response.content

    # Add assistant response to messages
    messages.append(AIMessage(content=full_response))

    # Get final plan state for checkpoint persistence
    from services.graph.tools import get_active_plan as _get_plan_for_save_sync
    final_plan_sync = _get_plan_for_save_sync(conversation_id)

    # Update graph state
    await graph.aupdate_state(config, {
        "messages": messages,
        "conversation_id": conversation_id,
        "system_prompt": system_prompt,
        "model": model,
        "temperature": temperature,
        "current_response": full_response,
        "is_complete": True,
        "retrieved_memories": [
            {
                "id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "importance": m.importance,
                "temporal_nature": m.temporal_nature,
                "tier": getattr(m, 'tier', 'observation') or 'observation',
                "reinforcement_count": getattr(m, 'reinforcement_count', 0) or 0,
                "score": m.score
            }
            for m in retrieved_memories
        ],
        "plan_steps": final_plan_sync,
        "tool_calls": [
            {"name": tc["name"], "args": tc["args"]}
            for tc in tool_calls_made
        ] if tool_calls_made else [],
        "current_node": "complete",
        "node_history": ["preprocess", "retrieve_memory", "respond", "extract_memory"]
    }, as_node="extract_memory")

    # ===== MEMORY EXTRACTION =====
    try:
        messages_for_extraction = [
            {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
            for m in messages[-10:]
            if isinstance(m, (HumanMessage, AIMessage))
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
