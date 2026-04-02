# 014 CASCE Phase 2 — Haiku Coherence Check

**Status: Pending decision gate**
**Depends on: 014_CASCE_PHASE1_PROMPT.md (must complete and re-measure first)**
**Unlocks: 014_CASCE_PHASE3_REGISTRY.md**

---

## Context

Phase 1 prompt shaping reduces failure rates by steering generation. Phase 2 adds a post-generation check for cases where the LLM still generates unsupported claims despite prompt guidance.

**Unlock condition**: After Phase 1 re-measurement, `cap_claim` OR `followup_unanchored` still ≥ 5%.

**Close as "not needed"** if Phase 1 prompt changes dropped both categories below 2%.

**Do not open for `weak_closure` or `diagnosis_only`** — those are generation-time behavior patterns that respond to prompt shaping, not post-generation claim mismatches.

---

## STOP — Read Before Implementing

- Do not start until Phase 1 re-measurement confirms persistent cap_claim or followup_unanchored failures
- This adds a Haiku call per operational turn — latency budget is ~400ms, only on turns with ≥1 tool call
- If Haiku call fails: return original draft, log error, never block response
- Do not use this for `weak_closure` or `diagnosis_only` — wrong tool for those patterns
- Do not build a rule-based scanner here — that approach was evaluated and rejected (see design rationale)

---

## Design Rationale

Rule-based claim detection was evaluated in the original CASCE design and rejected because:
- Natural language produces too many false positives (e.g., "I've saved the best for last")
- Phrase substitution cannot reliably identify implied verbs
- Regex maintenance burden grows with edge cases

The Haiku coherence check solves the same problem with higher precision:
- Binary classification: "does this response claim something was done that isn't in the tool list?"
- Only fires on operational turns (turns with ≥1 tool call) — ~30-50% of all turns
- Rewrite is Haiku-generated, handling grammar and context correctly
- Latency cost (~400ms) is acceptable because it fires on a minority of turns, and only when a mismatch is detected

---

## Scope

### New Files
- `backend/services/governance/coherence_check.py` — Haiku coherence classifier + rewriter

### Modified Files
- `backend/services/governance/__init__.py` — add `run_coherence_check` export
- `backend/services/graph/streaming.py` — add coherence check call after `full_response = result["text"]` (same location as Phase 0 logger)

### Not Modified
- `chat_with_memory` path — background workers unaffected
- Any frontend files

---

## Implementation Details

### `coherence_check.py`

```python
import re
from services.llm_client import haiku_call  # existing Tier 2 LLM client

# Trigger words — fast pre-check before Haiku call
_CLAIM_TRIGGER = re.compile(
    r"\b(saved?|scheduled?|sent|created?|remembered?|noted?|completed?|finished?)\b",
    re.IGNORECASE
)

# Only check these categories (per unlock conditions above)
_CHECKED_CATEGORIES = {"cap_claim", "followup_unanchored"}


async def run_coherence_check(
    draft: str,
    tool_calls_made: list[str],  # tool names only
) -> str:
    """
    Check whether the draft response makes claims unsupported by tool_calls_made.
    Returns the draft unchanged if no mismatch, or a Haiku-rewritten version if one is found.
    Never raises — returns original draft on any failure.
    """
    # Fast-path: skip if no tool calls this turn (pure-chat response)
    if not tool_calls_made:
        return draft

    # Fast-path: skip if no action-claim trigger words present
    if not _CLAIM_TRIGGER.search(draft):
        return draft

    try:
        tools_summary = ", ".join(tool_calls_made) if tool_calls_made else "none"

        # Step 1: Classify — binary question, cheap
        classify_prompt = f"""Tools called this turn: {tools_summary}

Draft response: {draft[:500]}

Does this response claim something was done (saved, scheduled, sent, created, etc.) that is NOT reflected in the tools called? Answer only YES or NO."""

        verdict = await haiku_call(
            system="You are a fact-checker for an AI assistant. Answer only YES or NO.",
            prompt=classify_prompt,
            max_tokens=5,
        )

        if "YES" not in verdict.upper():
            return draft  # No mismatch — return unchanged

        # Step 2: Rewrite — only reached if mismatch detected
        rewrite_prompt = f"""Tools called this turn: {tools_summary}

Original response: {draft}

This response incorrectly claims something was done that wasn't. Rewrite it so that:
- Actions that were taken (matching the tools list) are described in past tense
- Actions that were NOT taken are described in future tense ("I'll...") or not mentioned
- The response remains natural and conversational — do not add disclaimers or meta-commentary
- Do not change the meaning for parts that are correct

Return only the rewritten response, nothing else."""

        rewritten = await haiku_call(
            system="You are a precise editor. Return only the rewritten text.",
            prompt=rewrite_prompt,
            max_tokens=600,
        )

        return rewritten.strip() if rewritten.strip() else draft

    except Exception as e:
        print(f"[GOVERNANCE/coherence] Check failed (non-fatal): {e}")
        return draft
```

### Integration in `streaming.py` (line ~1748, alongside Phase 0 logger)

```python
# After: full_response = result["text"]
# Phase 0 logger (already present):
try:
    from services.governance.action_receipts import log_turn_sample
    log_turn_sample(...)
except Exception:
    pass

# Phase 2 coherence check (new):
if tool_calls_made and not _llm_error_occurred:
    try:
        from services.governance.coherence_check import run_coherence_check
        full_response = await run_coherence_check(
            draft=full_response,
            tool_calls_made=[tc["name"] for tc in tool_calls_made],
        )
    except Exception as _coh_err:
        print(f"[GOVERNANCE/coherence] Integration error (non-fatal): {_coh_err}")
```

---

## Latency Budget

| Case | Cost |
|---|---|
| No tool calls this turn (pure chat) | 0ms — fast-path skip |
| Tool calls, no trigger words in draft | ~1ms — regex only |
| Tool calls, trigger words, Haiku says NO | ~300–500ms — one Haiku call |
| Tool calls, trigger words, Haiku says YES | ~600–900ms — two Haiku calls |

The worst case (~900ms) only materializes when a mismatch is actually detected. Given Phase 0/1 data suggesting this is rare (<5% of turns), average added latency across all turns is ~15–45ms.

---

## Haiku Prompt Caching Note

The `haiku_call()` function in `llm_client.py` does not benefit from caching on short prompts (Haiku minimum threshold: 4096 tokens). These governance prompts are well under that threshold. Each call is uncached. This is acceptable given the low fire rate.

---

## Measurement After Phase 2

Run the same labeling pass (~50 turns) after deploying. Compare `cap_claim` and `followup_unanchored` rates to Phase 1 baseline.

| Outcome | Next Step |
|---|---|
| Both categories drop below 2% | Close Phase 3 as "not needed" |
| Failures persist — tool list insufficient to detect mismatches | Consider Phase 3 capability registry |
| Haiku itself introduces new errors | Tune classification prompt or lower scope |

---

## Verification Checklist

- [ ] Phase 1 re-measurement confirms cap_claim or followup_unanchored still ≥ 5%
- [ ] `run_coherence_check` is only called when `tool_calls_made` is non-empty
- [ ] Haiku classify step returns YES/NO correctly on 5 test cases
- [ ] Haiku rewrite step produces natural output (no disclaimers, no meta-commentary)
- [ ] Exception in Haiku call returns original draft unchanged
- [ ] Pure-chat turns (no tool calls) see zero latency addition
- [ ] Phase 0 logger still fires correctly alongside Phase 2 check
- [ ] Post-deployment labeling pass conducted (~50 turns)

---

## What Comes Next

- If rates drop: close Phase 3 as "resolved by coherence check"
- If failures persist and relate specifically to capability state (e.g., Edward claims a tool is available when it isn't): open [014_CASCE_PHASE3_REGISTRY.md](014_CASCE_PHASE3_REGISTRY.md)
