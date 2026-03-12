# Plan 011: Stream Termination Reliability and Background Handoff

## STOP: Read This Entire Document Before Making Changes

This plan fixes a user-visible reliability issue in the post-LangGraph direct-SDK runtime: some conversations remain stuck on the frontend `Thinking...` state after Codex/tool activity appears to stop in the backend logs.

The plan keeps the Plan 009 architectural decision intact. We are **not** reintroducing LangGraph or LangChain. The issue is treated as a stream lifecycle / termination guarantee gap in the replacement runtime, plus missing explicit handling for long-running work.

**Dependencies**: Plan 009 is already implemented enough to be the active architecture. Read it first.
**Estimated effort**: ~3-5 hours
**Primary surfaces**:
- `backend/services/graph/streaming.py`
- `backend/routers/chat.py`
- `frontend/lib/api.ts`
- `frontend/lib/ChatContext.tsx`
- optional: `backend/services/orchestrator_service.py`, `backend/services/cc_manager_service.py`, `backend/services/heartbeat/heartbeat_service.py`, `backend/services/graph/tools.py`

---

## Problem Statement

Observed runtime pattern:
- Codex OAuth call starts
- Codex stream shows normal event traffic in logs
- tool calls complete
- backend logs return to normal polling / request noise
- frontend remains stuck in `Thinking...`

Example symptom class:
- terminal SSE event not emitted
- transport closes without explicit failure content
- frontend does not clear spinner on EOF alone
- long-running work is ambiguous: interactive turn vs background task

---

## Diagnosis

### 1. Codex terminal-event handling is too narrow

Plan 009 explicitly documented that Codex/Responses streaming may end with either:
- `response.done`
- `response.completed`

Current implementation in `streaming.py` only accepts `response.completed` as the terminal event. If Codex returns `response.done`, or the stream carries usable output/function-call items but no `response.completed`, the backend can misclassify a provider response as an abnormal termination.

### 2. `stream_with_memory_events()` does not guarantee `done` for the full request lifecycle

The current implementation only has a `finally` that guarantees `done` around the memory-extraction tail. Exceptions thrown after the LLM/tool loop but before that local `finally` can still terminate the generator early.

Examples:
- fallback response generation fails
- checkpoint save fails
- any unexpected error after content/tool events but before the tail `finally`

### 3. `/api/chat` router can still close the SSE body silently

If the underlying generator raises mid-stream, the route currently does not convert that into a structured `error` + `done` sequence before the socket ends.

### 4. Frontend relies on events, not transport EOF, to clear the spinner

`ChatContext.tsx` currently clears `isThinking` primarily on:
- `content`
- `tool_end`
- `error`

`done` itself is not used to clear the thinking state, and abrupt EOF without `done` is not treated as a terminal failure.

### 5. Long-running work is not always framed explicitly as background work

For coding / multi-step / potentially slow tasks, the current system can keep the main chat turn open while significant tool activity or CC work occurs. When this succeeds it is acceptable, but when the stream is interrupted it feels like a hang instead of a deliberate background handoff.

---

## Architectural Decision

Keep the direct-SDK runtime from Plan 009.

### Why this is still the right decision
- LangGraph node removal is not the primary cause of the observed bug.
- The direct-SDK architecture already preserves the important boundaries:
  - provider-native tool schema conversion
  - checkpoint-store persistence
  - plain-dict message format
  - unchanged SSE event protocol
- The problem is that the replacement runtime needs stronger lifecycle guarantees than it currently has.

### What changes philosophically
- Interactive turns must be **explicitly terminal**.
- Long-running work must be **explicitly backgrounded**.
- Silent EOF is always considered a bug.

---

## Goals

1. `/api/chat` must always end with a structured terminal event sequence.
2. Codex SSE parsing must accept both expected terminal event shapes.
3. Frontend spinner state must clear on terminal completion, error, or unexpected EOF.
4. Long-running coding/research tasks should have a clear background-handoff path.
5. Optional push notification should tell the user when background work finishes.

## Non-Goals

- Reintroducing LangGraph or LangChain
- Changing the primary checkpoint-store architecture
- Rewriting the orchestrator or CC runtime wholesale
- Adding new REST endpoints unless implementation reveals an unavoidable need

---

## Implementation Plan

### Phase 1: Harden Codex SSE terminal parsing

**File**: `backend/services/graph/streaming.py`

Update `_call_codex()`.

#### Required changes
- Accept both `response.completed` and `response.done` as terminal response events.
- Preserve the existing edge-case handling for a terminal event that arrives without a trailing blank line.
- If the stream ends with partial output/function-call events but without a terminal event:
  - log a distinct status such as `NO_TERMINAL_PARTIAL`
  - raise a clear recoverable error message
- Keep existing auth / 404 / timeout handling.
- Improve summary logging so the terminal event name is visible in the final OK case.

#### Acceptance criteria
- A valid `response.done` stream is parsed successfully.
- A valid `response.completed` stream is parsed successfully.
- A partial stream without terminal event produces a clear error rather than silently falling through.

---

### Phase 2: Guarantee terminal SSE from the streaming generator

**File**: `backend/services/graph/streaming.py`

Wrap the full body of `stream_with_memory_events()` in an outer `try/except/finally`.

#### Required changes
- Track whether assistant content has already been emitted in this turn.
- On any uncaught exception after streaming has begun:
  - emit `error`
  - emit fallback `content` only if no assistant content has been emitted yet
  - always emit `done`
- Keep the existing per-LLM-call error path, but do not rely on it as the only safety net.
- Move the current `done` guarantee from the memory-extraction tail to a full-function guarantee.
- Ensure failures in these areas still terminate cleanly:
  - fallback generation
  - `save_messages()`
  - post-processing / memory extraction entry

#### Important note
Do not emit duplicate user-visible fallback content if real assistant text already streamed. In that case emit only `error` then `done`.

#### Acceptance criteria
- Any exception inside the generator still produces a final `done` event.
- If no content was emitted before failure, the user sees a readable fallback message.
- If some content was already emitted, the stream closes cleanly without duplicating the response.

---

### Phase 3: Make router-level SSE termination defensive

**File**: `backend/routers/chat.py`

Update the nested `generate()` function.

#### Required changes
- Track whether a `done` event has already been streamed.
- Catch exceptions raised while iterating `stream_with_memory_events()`.
- If an exception occurs before `done`:
  - emit a structured `error` event
  - emit `done`
- In the router `finally`, only send a synthetic `done` if the inner stream never sent one.
- Keep active-chat registration/unregistration behavior intact.

#### Acceptance criteria
- Mid-stream generator failures no longer result in silent socket close.
- Duplicate `done` events are avoided.

---

### Phase 4: Make frontend treat `done` and EOF as terminal

**Files**:
- `frontend/lib/api.ts`
- `frontend/lib/ChatContext.tsx`

#### In `frontend/lib/api.ts`
Update `streamChatEvents()`.

Required changes:
- Track whether a `done` event was received.
- Track the last known `conversation_id`.
- If the fetch body reaches EOF without `done` and the request was not user-aborted:
  - synthesize an `error` event
  - synthesize a `done` event

#### In `frontend/lib/ChatContext.tsx`
Update the event loop in `sendMessage()`.

Required changes:
- Track whether `done` was seen.
- Track whether any `content` event was seen.
- On `done`, explicitly clear `isThinking`.
- After the loop finishes, if no `done` was seen:
  - clear `isThinking`
  - if no content was received, set a transport-failure fallback message
- Keep abort handling intact.
- Do not regress code-block, plan, or CC-session event rendering.

#### Acceptance criteria
- Spinner always clears on `done`.
- Spinner clears on abnormal EOF.
- An abrupt stream end with no content shows a user-visible fallback instead of an empty hanging bubble.

---

### Phase 5: Explicit background handoff for long-running work

This phase is recommended, but can be implemented after Phases 1-4 if desired.

#### Behavior change
When a task is likely to take longer than an interactive turn should reasonably stay open, Edward should explicitly hand it off to background execution rather than keeping the main reply in `Thinking...` state indefinitely.

#### Preferred routing
- Coding / filesystem / debugging / multi-file tasks:
  - prefer `spawn_cc_worker(wait=false)`
- Non-coding multi-step research / synthesis / operational tasks:
  - prefer `spawn_worker(wait=false)`

#### Trigger guidance
Add explicit prompt/tool guidance for cases like:
- multi-file code edits
- build/test/debug loops
- expected duration > ~45 seconds
- task needs follow-up polling rather than a single response

#### Suggested implementation surfaces
- `backend/services/graph/tools.py`
  - update orchestrator tool descriptions to recommend `wait=false` for long-running jobs
- optionally `streaming.py`
  - add a short system directive clarifying interactive-vs-background behavior

#### Acceptance criteria
- Edward has a documented bias toward background handoff for long-running work.
- The main chat turn can return quickly with a status summary instead of feeling hung.

---

### Phase 6: Completion notification for background work

This phase is optional but strongly recommended if Phase 5 is implemented.

#### Goal
If a background worker or CC session completes after the user has moved on, Edward should notify them when appropriate.

#### Suggested approach
- Reuse existing push infrastructure.
- On worker / CC completion or failure:
  - if push is configured
  - and the parent conversation is not the currently active chat
  - send a push notification with a deep link back to the parent conversation
- Mark the parent conversation as notified using existing conversation notification plumbing.

#### Candidate files
- `backend/services/orchestrator_service.py`
- `backend/services/cc_manager_service.py`
- `backend/services/heartbeat/heartbeat_service.py`
- `backend/services/conversation_service.py`

#### Minimal implementation shape
- Add a small helper to determine whether a specific conversation is actively open.
- Add a small best-effort notifier helper reused by worker and CC completion paths.
- Do not block task completion on push success.

#### Acceptance criteria
- Successful background completion can notify the user.
- Failed background completion can also notify the user.
- Notification failure never fails the task itself.

---

## Test Plan

### Backend unit/integration tests

#### Codex parser
- stream ends with `response.completed`
- stream ends with `response.done`
- terminal event arrives without trailing blank line
- partial output exists but no terminal event arrives
- explicit SSE `error` event arrives

#### Streaming lifecycle
- `_call_llm()` raises before any content
- fallback generation raises
- `save_messages()` raises
- memory extraction raises
- normal successful turn still emits exactly one terminal `done`

#### Router behavior
- generator exception still produces `error` + `done`
- router does not emit duplicate `done` after an already completed stream

### Frontend tests
- `done` clears `isThinking`
- abnormal EOF clears `isThinking`
- abnormal EOF with no content shows fallback text
- existing plan/code/CC rendering still works

### Manual validation
1. Reproduce a Codex tool-heavy turn.
2. Confirm backend logs show final terminal event or explicit error classification.
3. Confirm frontend never stays stuck on `Thinking...` after stream end.
4. Trigger a background worker/CC task and confirm the main conversation returns immediately.
5. If push is configured, confirm completion notification deep-links back correctly.

---

## Rollout Order

Implement in this order:
1. Phase 1 (Codex parser)
2. Phase 2 (generator termination guarantee)
3. Phase 3 (router fallback)
4. Phase 4 (frontend EOF handling)
5. Verify the original stuck-`Thinking...` symptom is gone
6. Then optionally implement Phase 5 and Phase 6

This order gives the fastest path to fixing the user-visible hang before introducing the background-handoff improvement.

---

## Risks and Notes

### Risk: duplicate terminal events
Mitigation:
- Track whether `done` was already sent at both generator and router layers.
- Frontend should tolerate duplicate terminal semantics gracefully even if one slips through.

### Risk: duplicate fallback content
Mitigation:
- Only emit synthetic fallback content when no assistant content was already streamed.

### Risk: too many push notifications
Mitigation:
- Only notify for background tasks
- only notify when push is configured
- only notify when the conversation is not actively open
- keep notifications concise and actionable

### Risk: overusing background workers
Mitigation:
- Keep the handoff rule narrow: only tasks that are obviously long-running or iterative.
- Preserve inline execution for short, self-contained tasks.

---

## Definition of Done

This plan is complete when all of the following are true:
- Codex terminal parsing accepts both `response.done` and `response.completed`
- `/api/chat` always ends with `done`
- frontend spinner cannot remain indefinitely after transport EOF
- tool-heavy Codex turns no longer appear to "fall off" silently
- optional: long-running tasks can be backgrounded and notify on completion
