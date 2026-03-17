# Plan 004: Prompt Caching

## STOP: Read This Entire Document Before Making Any Changes

This plan adds Anthropic prompt caching to all LLM call sites in Edward. Static instruction text gets cached for 5 minutes, reducing token costs by ~30-50% on the main chat and ~90% on Haiku overhead calls.

**Dependencies**: Plans 001-003 completed (apply caching after all LLM call sites are finalized)
**Estimated effort**: 0.5-1 day

---

## Context & Rationale

Every message to Edward triggers multiple LLM calls:

| Call Site | Model | Static Content (tokens) | Frequency |
|-----------|-------|------------------------|-----------|
| Main chat (streaming) | Sonnet | ~500-800 | Every message |
| Main chat (non-streaming) | Sonnet | ~500-800 | Workers/scheduler |
| Memory extraction | Haiku | ~200 | Every message |
| Memory merge | Haiku | ~100 | ~5x/hour |
| Reflection queries | Haiku | ~150 | Every message |
| Deep retrieval queries | Haiku | ~100 | ~30% of messages |
| Search tag generation | Haiku | ~40 | Every message |
| Consolidation (cluster+contradiction) | Haiku | ~200 | Hourly |
| Heartbeat triage | Haiku | ~250 | <10x/day |

**None of these currently use prompt caching.** All static instruction text is paid at full price every time.

### How Anthropic Prompt Caching Works
- Mark message blocks with `cache_control: {"type": "ephemeral"}`
- First call: full price + small cache write fee
- Subsequent calls (within 5 min): 90% discount on cached tokens
- Cache auto-refreshes on use (stays alive as long as you keep calling)
- Works with LangChain's `ChatAnthropic` via `additional_kwargs`

---

## Strict Rules

### MUST DO
- [ ] Add caching to ALL 9 call sites (don't leave money on the table)
- [ ] Only cache truly STATIC content (instructions, format specs, examples)
- [ ] Keep dynamic content (memories, messages, user input) OUTSIDE cached blocks
- [ ] Test that responses are identical with and without caching

### MUST NOT DO
- [ ] Do NOT cache dynamic content (memories, time, briefings change per turn)
- [ ] Do NOT change any prompt text — only add cache_control metadata
- [ ] Do NOT add caching to Claude Code / external process calls (not our LLM calls)

---

## Phase 1: Main Chat Flow (Biggest Win)

### Step 1.1: Split system prompt into static + dynamic

**File**: `backend/services/graph/streaming.py`

Currently the system prompt is one concatenated string:
```python
enhanced_system_prompt = system_prompt + memory_context + briefing_context + time_context + ASSUMPTION_AWARENESS_CONTEXT + PLANNING_DIRECTIVE
```

Split into cached and uncached SystemMessages:
```python
from langchain_core.messages import SystemMessage

# Static prefix (cacheable) — personality + directives
static_system = system_prompt + "\n\n" + ASSUMPTION_AWARENESS_CONTEXT + "\n\n" + PLANNING_DIRECTIVE

# Dynamic suffix (not cached) — changes per turn
dynamic_context = memory_context + briefing_context + time_context

full_messages = [
    SystemMessage(
        content=static_system,
        additional_kwargs={"cache_control": {"type": "ephemeral"}}
    ),
    SystemMessage(content=dynamic_context),
] + messages
```

Apply to BOTH:
- `stream_with_memory_events()` (streaming path — web UI)
- `chat_with_memory()` (non-streaming path — webhooks, scheduler, workers)

### Step 1.2: Cache conversation history prefix

In multi-turn conversations, prior messages are identical each turn. Mark the second-to-last message as the cache breakpoint:

```python
# Cache the conversation history up to the previous turn
if len(messages) > 1:
    # Make a copy to avoid mutating the original
    messages = list(messages)
    prev_msg = messages[-2]
    if not hasattr(prev_msg, 'additional_kwargs') or prev_msg.additional_kwargs is None:
        prev_msg.additional_kwargs = {}
    prev_msg.additional_kwargs["cache_control"] = {"type": "ephemeral"}
```

This ensures the full conversation history is cached and only the newest user message + LLM response are charged at full price.

---

## Phase 2: Haiku Utility Calls

All Haiku utility calls follow the same pattern: static instruction prompt + dynamic user content. We cache the static part.

### Step 2.1: Memory Extraction

**File**: `backend/services/memory_service.py`

Find the memory extraction call (uses `MEMORY_EXTRACTION_PROMPT` or inline prompt). Wrap in SystemMessage with cache:

```python
response = await llm.ainvoke([
    SystemMessage(
        content=MEMORY_EXTRACTION_PROMPT,
        additional_kwargs={"cache_control": {"type": "ephemeral"}}
    ),
    HumanMessage(content=conversation_text),
])
```

If the current code passes everything as a single HumanMessage, split into SystemMessage (instruction) + HumanMessage (conversation data).

### Step 2.2: Memory Merge

**File**: `backend/services/memory_service.py`

The `_llm_merge_content()` function has a static merge instruction. Same pattern — split and cache.

### Step 2.3: Reflection Service

**File**: `backend/services/reflection_service.py`

Cache the `REFLECTION_QUERY_PROMPT`:
```python
response = await llm.ainvoke([
    SystemMessage(
        content=REFLECTION_QUERY_PROMPT,
        additional_kwargs={"cache_control": {"type": "ephemeral"}}
    ),
    HumanMessage(content=conversation_context),
])
```

### Step 2.4: Deep Retrieval

**File**: `backend/services/deep_retrieval_service.py`

Cache the `DEEP_QUERY_PROMPT`. Same pattern.

### Step 2.5: Search Tag Generation

**File**: `backend/services/search_tag_service.py`

The system message is already separate. Add cache_control:
```python
SystemMessage(
    content=system,
    additional_kwargs={"cache_control": {"type": "ephemeral"}}
),
```

### Step 2.6: Consolidation Service

**File**: `backend/services/consolidation_service.py`

The cluster prompt and contradiction prompt are built inline with f-strings. Split the static instruction part from the dynamic memory data. Cache the instruction part.

### Step 2.7: Heartbeat Triage

**File**: `backend/services/heartbeat/triage_service.py`

The `TRIAGE_PROMPT` has a static instruction block and dynamic `{contact_context}` + `{events_digest}`. Split into:
- SystemMessage with static classification instructions (cached)
- HumanMessage with the actual events to classify (not cached)

---

## Files Summary

| File | Change | Call Site |
|------|--------|-----------|
| `backend/services/graph/streaming.py` | Split system prompt, cache static prefix + history | Main chat (both paths) |
| `backend/services/memory_service.py` | Cache extraction + merge instructions | Memory extraction, merge |
| `backend/services/reflection_service.py` | Cache query generation prompt | Reflection |
| `backend/services/deep_retrieval_service.py` | Cache query rewrite prompt | Deep retrieval |
| `backend/services/search_tag_service.py` | Cache tag generation system message | Search tags |
| `backend/services/consolidation_service.py` | Cache cluster + contradiction instructions | Consolidation |
| `backend/services/heartbeat/triage_service.py` | Cache triage classification instructions | Heartbeat triage |

**7 files modified, 0 new files.**

---

## Build Verification

| Test | Expected Result | ✓ |
|------|----------------|---|
| Send 3 messages in a row | 2nd and 3rd messages show reduced token usage | |
| Check Anthropic usage dashboard | `cache_read_input_tokens` > 0 after 2nd message | |
| Compare response quality | Responses identical with/without caching | |
| Memory extraction | Still extracts correct memories | |
| Search tags | Still generates relevant tags | |
| Deep retrieval | Still generates useful search queries | |
| Triage classification | Still classifies messages correctly | |
| Consolidation cycle | Still clusters memories properly | |
| Reflection enrichment | Still generates useful related queries | |

### How to Verify Cache is Working

1. **Anthropic Dashboard** (console.anthropic.com):
   - Check API Usage → look for `cache_read_input_tokens` column
   - After sending 2+ messages in a row, `cache_read_input_tokens` should be > 0

2. **LangSmith** (if configured):
   - Check trace details → each run shows `cache_creation_input_tokens` and `cache_read_input_tokens`
   - First message: `cache_creation` > 0, `cache_read` = 0
   - Second message: `cache_creation` = 0, `cache_read` > 0

3. **Quick cost test**:
   - Send 5 messages in a row, note total cost
   - Compare to 5 messages without caching (revert changes temporarily)
   - Expected: ~30-50% reduction on main chat calls

---

## Rollback Plan

Remove all `additional_kwargs={"cache_control": {"type": "ephemeral"}}` additions. If any SystemMessages were split into static+dynamic, recombine them. Zero risk — caching is purely a cost optimization with no behavior change.

---

## Implementation Notes (Post-Completion)

### Key Discovery: Minimum Token Thresholds
Anthropic enforces minimum cacheable token counts per model:
- Sonnet 4.5/4: 1,024 tokens | Sonnet 4.6: 2,048 | Haiku 4.5: 4,096 | Opus 4.5/4.6: 4,096
- All standalone Haiku prompts are under 600 tokens — below the 4,096 threshold
- `cache_control` is silently ignored when below threshold (no error, no cost impact)
- Real savings come only from the main chat Sonnet path (tools + system + history exceed thresholds)

### Deviations from Plan
1. **More call sites than documented**: Found 5 Haiku calls in memory_service.py (extraction, merge, conflict resolution, temporal backfill, dedup/classify) vs 2 in the plan. All 5 were updated.
2. **Consolidation service** used bare strings (`llm.ainvoke(prompt)`) instead of message lists — converted to proper SystemMessage + HumanMessage.
3. **Prompt constants renamed** (e.g., `MEMORY_EXTRACTION_PROMPT` → `MEMORY_EXTRACTION_INSTRUCTIONS`) since they no longer contain the dynamic placeholder portions.
4. **No middleware approach**: LangChain 1.0 has `AnthropicPromptCachingMiddleware` but requires `langchain-anthropic>=1.0.0`. Project uses `>=0.3.5`, so we used the `additional_kwargs` approach.
5. **Haiku calls are structural-only**: The SystemMessage + HumanMessage split is correct architecture but won't deliver cache savings until Anthropic lowers the Haiku threshold.

### Files Modified (7 files, 15 call sites)
All files compile cleanly. No prompt text was changed — only metadata additions and message restructuring.
