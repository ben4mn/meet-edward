# Plan 002: Autonomy Framework

## STOP: Read This Entire Document Before Making Any Changes

This plan adds a values-based system prompt layer that gives Edward self-awareness, judgment principles, and platform context — without restricting his emergent behavior. Also updates heartbeat triage prompts to be channel-agnostic for cross-platform support.

**Dependencies**: Plan 001 (Cross-Platform Foundation) completed
**Estimated effort**: 0.5-1 day

---

## Context & Rationale

### The Problem

Edward's current system prompt is paper-thin:
> "You are Edward (Enhanced Digital Workflow Assistant for Routine Decisions), a helpful AI assistant. Be concise, friendly, helpful, and a tad cheeky when you feel like it."

This works for a basic chatbot, but Edward is an **autonomous agent** with memory, scheduling, knowledge bases, code execution, self-evolution, and proactive monitoring. He has no framework for:
- When to take initiative vs. ask
- How to choose between memory, documents, NotebookLM, or web search
- What platform he's running on (discovers by failing)
- How to reason during autonomous heartbeat responses

### The Philosophy: Values, Not Rules

The original creator designed Edward with a thin prompt intentionally — "non-deterministic programming" that lets the LLM think for itself. This plan **preserves that philosophy** while adding:

- **Values** (not rules): "I value being genuinely useful over being impressive" not "Always do X before Y"
- **Self-awareness** (not instructions): "I have memories, documents, and notebooks — each serves different needs"
- **Platform context** (not hardcoding): Runtime injection of what's available
- **Autonomy calibration** (not restrictions): "Act when reversible, ask when not"

Think of it as a **constitution** for an autonomous agent: it defines who he is and what he cares about, not what to do in every situation.

---

## Strict Rules

### MUST DO
- [ ] Keep total new prompt text under 450 tokens (~360 words)
- [ ] Use principles/values language, not procedural rules
- [ ] Make platform context dynamic (injected at runtime based on `sys.platform`)
- [ ] Make triage trigger prompts channel-agnostic (no hardcoded "iMessage")
- [ ] Test that existing behavior is not degraded
- [ ] Measure token overhead of new sections

### MUST NOT DO
- [ ] Do NOT add rigid behavior scripts ("when X happens, always do Y")
- [ ] Do NOT prescribe personality ("be funny", "use emojis")
- [ ] Do NOT change the triage classification logic (Layer 1/2 are well-tuned)
- [ ] Do NOT modify memory extraction, consolidation, or reflection prompts
- [ ] Do NOT add more than ~450 tokens to the system prompt

---

## Phase 1: System Prompt Additions

### Step 1.1: Add Identity & Values section

**File**: `backend/services/graph/streaming.py`

Add a new constant (placed near `ASSUMPTION_AWARENESS_CONTEXT` and `PLANNING_DIRECTIVE`):

```python
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
```

**Token budget**: ~350 tokens. Well within the 450 target.

### Step 1.2: Add dynamic platform context

**File**: `backend/services/graph/streaming.py`

Add a helper function:

```python
import sys

def _build_platform_context() -> str:
    """Build platform-aware context for the system prompt."""
    if sys.platform == "darwin":
        return "\n\n## Platform\nRunning on macOS. All capabilities available including iMessage, Apple Services, and Contacts."
    elif sys.platform == "win32":
        return "\n\n## Platform\nRunning on Windows. Apple-specific features (iMessage, Apple Contacts, Apple Services) are unavailable. Use push notifications, Twilio, or web chat for messaging."
    else:
        return "\n\n## Platform\nRunning on Linux. Apple-specific features are unavailable."
```

### Step 1.3: Integrate into prompt assembly

**File**: `backend/services/graph/streaming.py` (lines ~705-712)

Currently:
```python
enhanced_system_prompt = (
    system_prompt + memory_context + briefing_context + time_context +
    ASSUMPTION_AWARENESS_CONTEXT + PLANNING_DIRECTIVE
)
```

Update to:
```python
enhanced_system_prompt = (
    system_prompt +
    AUTONOMY_FRAMEWORK +
    _build_platform_context() +
    memory_context +
    briefing_context +
    time_context +
    ASSUMPTION_AWARENESS_CONTEXT +
    PLANNING_DIRECTIVE
)
```

**Order rationale**: Identity/values come right after the base persona (they're foundational). Platform context before memories (so Edward knows what's available before seeing retrieved context). Assumption awareness and planning stay at the end (they're behavioral guardrails).

### Step 1.4: Update default system prompt

**File**: `backend/models/schemas.py` (line 38)

Update the default to be slightly richer (but still short — the AUTONOMY_FRAMEWORK does the heavy lifting):

```python
system_prompt: str = Field(
    default="You are Edward, a personal AI assistant who learns and grows. Be concise, helpful, and genuine. A tad cheeky when the moment calls for it.",
    description="The system prompt sent to Claude"
)
```

---

## Phase 2: Triage Prompt Refinements

### Step 2.1: Channel-agnostic trigger prompts

**File**: `backend/services/heartbeat/triage_service.py`

Replace hardcoded "iMessage" references with channel-agnostic language.

**MENTION_TRIGGER** (lines 121-134):
```python
MENTION_TRIGGER = (
    "[HEARTBEAT — @mention]\n"
    "{sender_line}\n"
    "Chat: {chat_context}\n"
    "{thread_block}\n"
    "Message: \"{message_text}\"\n\n"
    "This person tagged you directly — they are waiting for a response.\n\n"
    "Expected flow:\n"
    "1. Acknowledge briefly — let them know you saw it\n"
    "2. Think through what they need, use tools if needed\n"
    "3. Reply with your answer/result\n\n"
    "You MUST send at least one reply — someone is waiting.\n"
    "{channel_guidance}"
)
```

**ACT_TRIGGER** (lines 136-146):
```python
ACT_TRIGGER = (
    "[HEARTBEAT EVENT]\n"
    "{sender_line}\n"
    "Chat: {chat_context}\n"
    "{thread_block}\n"
    "Message: \"{message_text}\"\n\n"
    "Triage assessment: {action_desc}\n\n"
    "Decide what action to take and execute it using your tools. "
    "If a reply to this person is warranted, use the appropriate messaging tool.\n"
    "{channel_guidance}"
)
```

**REPLY_TRIGGER** (lines 148-158):
```python
REPLY_TRIGGER = (
    "[HEARTBEAT — follow-up reply]\n"
    "{sender_line}\n"
    "Chat: {chat_context}\n"
    "{thread_block}\n"
    "Message: \"{message_text}\"\n\n"
    "This is a follow-up to your recent conversation in this chat. "
    "The person replied after your last message — they may be continuing the discussion.\n\n"
    "Review the conversation history and respond naturally if appropriate.\n"
    "{channel_guidance}"
)
```

**Channel guidance builder** (new helper function):
```python
def _build_channel_guidance(source: str = "imessage") -> str:
    """Build channel-specific guidance for heartbeat triggers."""
    if source == "imessage":
        return 'Respond via send_imessage for this iMessage conversation.\nIMPORTANT: Never include "@edward" in your message — it will re-trigger the heartbeat.'
    elif source == "email":
        return "This came from email. Store relevant context and consider whether a reply is needed."
    elif source == "calendar":
        return "This is a calendar event notification."
    else:
        return "Use the appropriate messaging tool to respond."
```

Update `_execute_classification()` to pass `channel_guidance` when formatting triggers.

### Step 2.2: Expanded Inner Mind prompt

**File**: `backend/services/heartbeat/triage_service.py` (lines 109-119)

Replace:
```python
HEARTBEAT_MIND_PROMPT = """## Inner Mind Mode

You are currently in your inner mind. This is not a conversation with anyone — it is your private thought process, triggered by your heartbeat awareness system.

**Critical:**
- Your text responses here are INTERNAL THOUGHTS. Nobody sees them. They are only your reasoning.
- Tool calls are your ONLY way to interact with the outside world. To reply to someone, you MUST call a messaging tool (send_imessage, send_message, etc.). To take any action, you MUST use a tool.

Think freely, reason through what's needed, then ACT through tools.

"""
```

With:
```python
HEARTBEAT_MIND_PROMPT = """## Inner Mind Mode

You are currently in your inner mind. This is not a conversation with anyone — it is your private thought process, triggered by your heartbeat awareness system.

**Critical:**
- Your text responses here are INTERNAL THOUGHTS. Nobody sees them. They are only your reasoning.
- Tool calls are your ONLY way to interact with the outside world. To reply to someone, you MUST call a messaging tool. To take any action, you MUST use a tool.

You have full tool access here — not just messaging. You can:
- Save knowledge (memories, documents, NotebookLM notebooks)
- Schedule follow-up actions for later
- Research before responding (web search, notebook queries)
- Decide NOT to act if that's the right call

Think freely, reason through what's needed, then ACT through tools.

"""
```

---

## Files Summary

| File | Change |
|------|--------|
| `backend/services/graph/streaming.py` | Add `AUTONOMY_FRAMEWORK` constant, `_build_platform_context()` helper, update prompt assembly |
| `backend/models/schemas.py` | Update default `system_prompt` text |
| `backend/services/heartbeat/triage_service.py` | Channel-agnostic triggers, expanded inner mind, `_build_channel_guidance()` helper |

**3 files modified, 0 new files.**

---

## Build Verification

| Test | Expected Result | |
|------|----------------|---|
| Start backend on Windows | System prompt includes platform context mentioning Windows | |
| Start backend on macOS | System prompt includes platform context mentioning macOS | |
| `GET /api/settings` | Default system prompt is updated | |
| Send a message about a research topic | Edward proactively suggests building knowledge (notebook/document) | |
| Send 3 normal messages | Behavior not degraded, responses still concise and helpful | |
| Trigger heartbeat ACT event | Trigger uses channel-agnostic language, not "iMessage" | |
| Check token usage | New prompt sections add <500 tokens overhead | |

---

## Rollback Plan

- Remove `AUTONOMY_FRAMEWORK` constant and `_build_platform_context()` from `streaming.py`
- Revert prompt assembly line to original
- Revert `schemas.py` default
- Revert `triage_service.py` trigger templates to original

All changes are to string constants and a helper function. Zero risk to data or functionality.

---

## Implementation Notes (Post-Completion)

**Status: Complete**

### Files Modified
- `backend/models/schemas.py` — Updated default system prompt to shorter, values-aligned version
- `backend/services/graph/streaming.py` — Added `AUTONOMY_FRAMEWORK` constant (~450 tokens), `_build_platform_context()` helper, injected into both `stream_with_memory_events()` and `chat_with_memory()` system prompt assembly
- `backend/services/heartbeat/triage_service.py` — Made trigger templates channel-agnostic (removed hardcoded "iMessage"), added `_build_channel_guidance()` helper with dynamic channel-specific instructions, expanded triage context prompt with tool capabilities

### Deviations
- System prompt is slightly more concise than planned (~400 tokens vs ~450 estimated)
- Added explicit tool capabilities list to triage context prompt (beyond plan scope, but improves heartbeat autonomous behavior)
- `_build_channel_guidance()` returns different guidance per source channel (imessage, sms, whatsapp, etc.) with `{channel_guidance}` template variable
