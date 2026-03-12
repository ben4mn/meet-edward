# 014 CASCE Phase 1 — Upstream Prompt Shaping

**Status: Pending decision gate**
**Depends on: 014_CASCE_PHASE0_MEASURE.md (must complete measurement pass first)**
**Unlocks: 014_CASCE_PHASE2_COHERENCE.md**

---

## Context

Phase 0 established a measurement baseline. Phase 1 is opened only when the decision gate report shows ≥1 failure per 20 turns in at least one CASCE category.

**Unlock condition**: `cap_claim` OR `followup_unanchored` OR `weak_closure` OR `diagnosis_only` ≥ 5% in the Phase 0 measurement report.

**Close as "not needed"** if all categories < 2% — Phase 0 system prompt additions were sufficient.

---

## STOP — Read Before Implementing

- Do not start this plan until the Phase 0 decision gate report is produced
- Do not add any post-generation rewriting logic in this phase
- This phase is prompt-only: no new Python files, no new runtime logic
- If prompt shaping eliminates the failure pattern, close Phase 2 as "not needed"

---

## Design Rationale

Post-generation filtering (rule-based or LLM-based) is architecturally backwards: it patches behavior after the fact rather than steering generation toward the right behavior from the start. Concrete few-shot examples in the system prompt consistently outperform abstract directives for production LLMs. Phase 1 tests whether targeted prompt additions alone close the gap before adding any runtime cost.

---

## Scope

### Modified Files
- `backend/services/graph/streaming.py` — system prompt constant updates only

### Not Modified
- Any new Python files (none created in this phase)
- Any other backend or frontend files

---

## Implementation Details

Phase 1 changes are targeted per failing category. Only implement sections corresponding to categories that actually appeared in the Phase 0 report.

### If `cap_claim` ≥ 5%

Extend `CLOSURE_DIRECTIVE` (added in Phase 0) with a negative-example section:

```python
# Append to CLOSURE_DIRECTIVE:
"""
## Response Accuracy

Only use past tense for actions that actually happened this turn:
- ✓ "I've saved this." — after save_document() ran
- ✓ "I've scheduled a reminder." — after schedule_event() ran
- ✓ "I've sent the message." — after send_* ran
- ✗ "I've saved this." — when no save tool was called
- ✗ "I've scheduled that." — when no schedule_event was called

If you haven't taken the action yet, either take it now or say "I'll [action]" — not "I've [action]"."""
```

### If `followup_unanchored` ≥ 5%

The Phase 0 `PLANNING_DIRECTIVE` addition already covers this. If still failing, add a stronger concrete example to `AUTONOMY_FRAMEWORK`:

```
- When you make a follow-up commitment, back it immediately:
  ✓ "I've set a reminder to check in on [topic] on [date]." — after schedule_event() ran
  ✗ "I'll follow up on that." — with no scheduled event created
```

### If `weak_closure` ≥ 5%

Add a dedicated section to `CLOSURE_DIRECTIVE`:

```
## Action Default

When you have everything needed to act and the action is reversible:
- Act, then report what you did.
- Do not ask "Would you like me to?" — that is avoidance, not service.
- Do not say "I'd be happy to [action] if you want." — either do it or explain the specific blocker.

Blockers that justify deferral:
- Missing required input (e.g., no time given for a reminder)
- Action is irreversible or high-consequence (e.g., sending an external message to someone not mentioned)
- Genuine ambiguity about which of two options the user wants

Blockers that do not justify deferral:
- You haven't done it yet
- You're not sure the user will appreciate it
- The action is slightly inconvenient to take
```

### If `diagnosis_only` ≥ 5%

Add to `AUTONOMY_FRAMEWORK` under knowledge systems:

```
- When a response would benefit from persistence — a reusable recipe, a reference guide, an ongoing
  project summary — save it as a document proactively. Don't require the user to ask.
- When a multi-step analysis concludes, the default artifact is a document or memory, not a long chat message.
```

---

## Measurement After Phase 1

After deploying Phase 1 prompt changes, run another labeling pass (~50 turns) with the same rubric from Phase 0. Compare rates.

| Outcome | Next Step |
|---|---|
| All categories drop below 2% | Close Phase 2 and Phase 3 as "not needed" |
| `cap_claim` or `followup_unanchored` still ≥ 5% | Open Phase 2 (Haiku coherence check) |
| `weak_closure` or `diagnosis_only` still ≥ 5% | Further prompt refinement; Phase 2 less applicable here |

---

## Verification Checklist

- [ ] Phase 0 decision gate report confirms ≥1 category at ≥5%
- [ ] Only modified system prompt constants — no new files, no new runtime logic
- [ ] Updated prompts verified in LangSmith trace (or debug print)
- [ ] Second labeling pass conducted after ~50 turns
- [ ] Rates compared to Phase 0 baseline
- [ ] Decision made on whether Phase 2 is warranted

---

## What Comes Next

- If rates drop: close Phase 2 and Phase 3 as "resolved by prompt shaping"
- If `cap_claim` or `followup_unanchored` persist: open [014_CASCE_PHASE2_COHERENCE.md](014_CASCE_PHASE2_COHERENCE.md)
