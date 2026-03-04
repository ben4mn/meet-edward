# PR #7 Review: Add Anthropic Prompt Caching Across All LLM Call Sites

**Author:** cchen362 (Chee)
**Commit:** `47149c6` — "feat: add Anthropic prompt caching across all LLM call sites"
**Files changed:** 9 | +174 / -131

---

## Summary

This PR adds Anthropic prompt caching by splitting LLM prompts into static
instructions (cached via `SystemMessage` with `cache_control: {"type":
"ephemeral"}`) and dynamic data (uncached `HumanMessage`). It also changes
`.get("error")` to `"error" in result` in orchestrator tool functions.

---

## Issues

### BUG (High): `.get("error")` → `"error" in result` breaks orchestrator tools

**Files:** `backend/services/graph/tools.py`, `backend/services/orchestrator_service.py`

`_task_to_dict()` (orchestrator_service.py:48-67) **always** includes
`"error": task.error`, where `task.error` is `None` for successful tasks.

- **Before:** `.get("error")` returns `None` → falsy → continues normally
- **After:** `"error" in result` returns `True` (key exists) → bails with `"Error: None"`

This breaks `check_worker`, `cancel_worker`, `send_to_worker`,
`spawn_worker`, `spawn_cc_worker`, and `wait_for_workers` — they'll all
return "Error: None" for every successful task.

**Fix:** Revert these changes back to `.get("error")`.

---

### BUG (Medium): Double braces `{{` not converted to single braces after removing `.format()`

**Files:** `backend/services/memory_service.py`, `backend/services/heartbeat/triage_service.py`

Several prompts that previously used Python `.format()` still have `{{`/`}}`
escaping. With `.format()`, `{{` produces literal `{`. But now these strings
are passed directly as `SystemMessage` content, so `{{` will appear literally
as `{{` in the prompt sent to the LLM — producing malformed JSON examples.

**Affected prompts:**
- `MEMORY_EXTRACTION_INSTRUCTIONS` (memory_service.py:465-467) — JSON examples
- `CONFLICT_RESOLUTION_INSTRUCTIONS` (memory_service.py:1078) — JSON response format
- `BACKFILL_INSTRUCTIONS` (memory_service.py:1178) — JSON response format
- `TRIAGE_INSTRUCTIONS` (triage_service.py:351) — JSON response format

**Already correctly fixed:**
- `consolidation_service.py` — `{{` → `{` ✓
- `DEDUP_CLASSIFY_INSTRUCTIONS` in memory_service.py — `{{` → `{` ✓

**Fix:** Replace `{{` with `{` and `}}` with `}` in these four prompts.

---

### MINOR: Missed call site in `nodes.py`

**File:** `backend/services/graph/nodes.py:91-92`

```python
messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
response = await llm.ainvoke(messages)
```

This LLM call site wasn't updated. May be intentional if `nodes.py` is a
legacy path superseded by `streaming.py`, but worth confirming.

---

### MINOR: In-place mutation of message objects in tool loop

**File:** `backend/services/graph/streaming.py`

The conversation history cache breakpoint mutates `additional_kwargs` on
message objects in-place. During the tool loop, `messages` grows each
iteration and previously-marked messages retain their `cache_control`.
Functionally fine (Anthropic uses the last 4 breakpoints), but these messages
get saved to LangGraph checkpoints — verify that `cache_control` in
`additional_kwargs` doesn't cause issues with checkpoint
serialization/deserialization.

---

## What Looks Good

**Prompt splitting pattern:** The consistent static instructions → cached
`SystemMessage` + dynamic data → `HumanMessage` pattern is correct and
well-applied across all Haiku call sites (consolidation, deep retrieval,
triage, memory extraction/merge/conflict/backfill/dedup, reflection, search
tags). With ephemeral caching's 5-min TTL, repeated calls within a
conversation turn will hit the cache.

**System prompt splitting in streaming.py:** Separating
`system_prompt + ASSUMPTION_AWARENESS_CONTEXT + PLANNING_DIRECTIVE` (static,
cached) from `memory_context + briefing_context + time_context` (dynamic,
uncached) is well-reasoned. The static portion is large and identical across
turns.

**JSON template cleanup:** Removing f-string prefixes and `.format()` from
prompt templates with JSON examples (where done correctly) is a nice
readability improvement.

**Correct import additions:** `SystemMessage` imports properly added where
needed.

---

## Verdict

**Request changes** — two bugs need fixing:
1. Revert `.get("error")` → `"error" in result` changes (or don't include them in this PR)
2. Fix remaining `{{`/`}}` → `{`/`}` in four prompt templates

The prompt caching work itself is solid and ready to merge once these are addressed.
