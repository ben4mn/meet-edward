# Plan 013: Live Activity Feed During LLM Processing

## Status: Complete

## Problem

When Edward uses GPT-5.4 (Codex OAuth) for deep conversations, the chat UI freezes with no feedback for 60–180+ seconds. The backend emits no `progress` events during the most expensive phases — LLM reasoning and non-execution tool calls — so the frontend's `ThinkingIndicator` has nothing to render.

Two compounding root causes:
1. **No events during model reasoning** — `_call_codex()` blocks the async generator for 60–120s with no yields while GPT-5.4 thinks
2. **Non-execution tool calls are invisible** — `tool_start`/`tool_end` for non-code tools (memory search, web search, schedule, etc.) are discarded by the frontend; the backend emits no meaningful `progress` event for them

The same issue affects Claude (Anthropic) since Plan 009 removed token streaming — both providers now return all content at once.

## What Already Works (Do Not Rebuild)

- `ThinkingIndicator` (`frontend/components/chat/ThinkingIndicator.tsx`) renders a live step list with spinners/checkmarks during streaming, and collapses to "Used X tools · Y memories" after completion
- `PROGRESS` event pipeline: `create_event(EventType.PROGRESS, ...)` → SSE → `ChatContext.tsx` progress handler → `progressSteps` array → `ThinkingIndicator`
- Memory search already emits progress events (`streaming.py` lines 1357–1397)
- "Generating response..." / "Response complete" progress events already exist at lines 1600–1614, but fire AFTER `_call_llm()` returns — useless for non-streaming providers

## Changes

### File: `backend/services/graph/streaming.py`

All changes are within `stream_with_memory_events()`.

---

#### Change 1 — Raise `CODEX_TOTAL_TIMEOUT` (line 1069)

```python
# Before
CODEX_TOTAL_TIMEOUT = 180.0

# After
CODEX_TOTAL_TIMEOUT = 300.0  # GPT-5.4 extended thinking can take 3–4 minutes
```

---

#### Change 2 — Add `_tool_label()` helper (new, near tool loop)

Add before the tool loop (`while iteration < max_tool_iterations:`):

```python
# Human-readable labels for common tools (used in progress events)
_TOOL_LABELS: Dict[str, str] = {
    "web_search": "Searching the web",
    "fetch_page_content": "Reading page",
    "remember_search": "Searching memories",
    "remember_update": "Saving memory",
    "remember_forget": "Forgetting memory",
    "schedule_event": "Scheduling event",
    "list_scheduled_events": "Checking schedule",
    "cancel_scheduled_event": "Cancelling event",
    "save_document": "Saving document",
    "read_document": "Reading document",
    "edit_document": "Editing document",
    "search_documents": "Searching documents",
    "list_documents": "Listing documents",
    "delete_document": "Deleting document",
    "send_message": "Sending message",
    "send_sms": "Sending SMS",
    "send_whatsapp": "Sending WhatsApp",
    "send_imessage": "Sending iMessage",
    "execute_code": "Running Python",
    "execute_javascript": "Running JavaScript",
    "execute_sql": "Running SQL",
    "execute_shell": "Running shell command",
    "list_sandbox_files": "Listing sandbox files",
    "read_sandbox_file": "Reading sandbox file",
    "save_to_storage": "Saving to storage",
    "list_storage_files": "Listing files",
    "read_storage_file": "Reading file",
    "send_push_notification": "Sending notification",
    "create_persistent_db": "Creating database",
    "query_persistent_db": "Querying database",
    "update_widget": "Updating widget",
    "search_mcp_servers": "Searching MCP servers",
    "add_mcp_server": "Adding MCP server",
}

def _tool_label(tool_name: str) -> str:
    """Return a human-readable label for a tool name."""
    if tool_name in _TOOL_LABELS:
        return _TOOL_LABELS[tool_name]
    # MCP tool prefix (e.g. "nlm_list_notebooks" → "Nlm: List Notebooks")
    if "_" in tool_name:
        parts = tool_name.split("_", 1)
        return f"{parts[0].upper()}: {parts[1].replace('_', ' ').title()}"
    return tool_name.replace("_", " ").title()
```

---

#### Change 3 — Keepalive heartbeat + move "Generating response..." before `_call_llm`

Replace the current block around line 1477 (the bare `result = await _call_llm(...)` call):

```python
# BEFORE (simplified):
try:
    result = await _call_llm(model, ...)
except Exception as e:
    ...
```

With:

```python
# Emit "Generating response..." BEFORE the model call (visible while model thinks)
yield create_event(EventType.PROGRESS, conversation_id,
    step="generating",
    status="started",
    message="Generating response..."
)

# Keepalive heartbeat: yield progress updates every 5s while waiting for model
_llm_task = asyncio.ensure_future(_call_llm(
    model, static_system, dynamic_context, messages, tool_schemas, temperature
))
_KEEPALIVE_INTERVAL = 5.0
_elapsed_s = 0
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
    # (break handled by outer logic — restructure to fit existing error block)
```

**Note:** The existing `except Exception as e:` block (lines 1481–1490) must be merged into the new structure. Easiest: wrap the entire keepalive block in a try/except that mirrors the existing error handler.

---

#### Change 4 — Human-readable tool progress + completion event

Update the tool progress events inside the tool loop (currently around lines 1516–1545):

```python
# BEFORE (line 1516):
yield create_event(EventType.PROGRESS, conversation_id,
    step="tool_execution",
    status="started",
    message=f"Running {tool_call['name']}...",
    tool_name=tool_call['name']
)

# AFTER:
yield create_event(EventType.PROGRESS, conversation_id,
    step="tool_execution",
    status="started",
    message=_tool_label(tool_call['name']),
    tool_name=tool_call['name']
)
```

After the `async for event in execute_tool_call_with_events(...)` loop, add:

```python
# Emit completion after tool finishes
yield create_event(EventType.PROGRESS, conversation_id,
    step="tool_execution",
    status="completed",
    message=_tool_label(tool_call['name']),
    tool_name=tool_call['name']
)
```

---

#### Change 5 — Remove redundant "Generating response..." at lines 1600–1614

The "started" event at line 1600 is now emitted before `_call_llm` (Change 3). Remove only the `status="started"` event at 1600–1604. Keep the `status="completed"` event at 1610–1614 (marks the end of the generating step).

---

## Files Modified

| File | Changes |
|------|---------|
| `backend/services/graph/streaming.py` | `CODEX_TOTAL_TIMEOUT` · `_tool_label()` helper · keepalive wrapper · tool progress labels + completions · remove duplicate "started" event |
| `backend/routers/chat.py` | Added `X-Accel-Buffering: no` header to SSE response |
| `backend/services/memory_service.py` | Fixed `UnicodeEncodeError` on Windows cp1252 terminals for 3 print calls |
| `frontend/lib/api.ts` | Added `yieldToEventLoop` + stream drop recovery helper |
| `frontend/lib/ChatContext.tsx` | Set `isThinking: true` on first progress event · stream drop recovery (fetch last message from DB on network error) |
| `frontend/components/chat/MessageBubble.tsx` | Fixed fallback "Thinking..." condition: guard on `progressSteps.length > 0` instead of `isThinking` |

## Post-Implementation Fix (2026-03-17)

**All progress events now stream in real time via Ngrok/PWA**, including the step list and keepalive timer.

Root cause of the buffering: Next.js `rewrites()` proxy buffers the entire SSE response before forwarding to the client. Fixed by adding a streaming passthrough App Router route handler at `frontend/app/api/chat/route.ts` that pipes the backend stream directly to the browser without buffering.

Same fix resolved the "Thinking" animation stuck bug when accessing via Ngrok (all SSE events — content, progress, done — were buffered and only delivered when the backend closed the connection).

---

## Known Limitation — No Token Streaming During LLM Reasoning

**The step list appears at stream end for fast responses (<5s)**, not progressively. This is an architectural constraint introduced by Plan 009:

- Before Plan 009: LangGraph's `astream_events()` natively yielded internal events token-by-token
- After Plan 009: `_call_llm()` is a blocking call — it waits for the complete response before returning
  - `_call_anthropic()`: uses `client.messages.create()` (non-streaming)
  - `_call_codex()`: reads SSE internally but collects everything into a dict before returning
  - `_call_openai()`: uses `client.responses.create()` (non-streaming)

**What Plan 013 delivers** (working as designed):
- For responses >5s: keepalive timer ticks (`Generating response... (5s)`, `(10s)`, etc.)
- Tool steps always show with human-readable labels in the collapsed post-response summary
- Step list appears as a flash on fast responses — visible but brief

**To restore true live token streaming** (separate future plan):

*Option A — Anthropic only (Medium complexity, ~1 day):*
- Switch `_call_anthropic()` to `async with client.messages.stream() as stream`
- Yield `CONTENT` delta events as tokens arrive
- Tool calls accumulate via `input_json_delta` chunks — no tool loop restructuring needed for non-tool responses
- Covers simple Q&A (majority of use cases)

*Option B — Full dual-provider streaming (High complexity, ~2 days):*
- Restructure `_call_llm()` from returning a dict to being an async generator
- Anthropic: `messages.stream()`, Codex: re-yield `response.output_text.delta` events already present in the SSE stream
- Requires refactoring the entire tool loop in `stream_with_memory_events()` to handle streaming + tool call accumulation
- Essentially rebuilds what LangGraph's `astream_events` provided

## Result UX

**Simple query (no tools):**
```
✓ Found 8 memories
⟳ Generating response... (5s)
⟳ Generating response... (10s)
⟳ Generating response... (15s)
→ [response text appears]
```

**With tool calls:**
```
✓ Found 12 memories
⟳ Generating response... (10s)
✓ Generating response...
⟳ Searching the web
✓ Searching the web
⟳ Reading page
✓ Reading page
⟳ Generating response... (5s)
→ [response text appears]
```

**Collapsed after completion:** `Used 2 tools · 12 memories`

**Works identically for Claude (Anthropic) and GPT-5.4 (Codex)** — the keepalive wraps `_call_llm()` which is the shared dispatch point.

## Verification

1. Start backend, open chat, select GPT-5.4 or Claude model
2. Send a complex question (e.g. "Explain quantum entanglement and its implications for computing")
3. Observe `ThinkingIndicator` — should show live step list with timer ticking up every 5s
4. Test with a tool-using query (e.g. "Search the web for today's AI news and summarize")
5. Verify each tool appears with a human-readable label, not "Running tool_name..."
6. Verify collapse summary after response: "Used X tools · Y memories"
7. Fast query: keepalive fires 0 times, no timer suffix in step message
8. Confirm `CODEX_TOTAL_TIMEOUT` in logs allows 5-minute responses without timeout error
