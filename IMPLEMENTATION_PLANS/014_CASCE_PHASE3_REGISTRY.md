# 014 CASCE Phase 3 — Capability Registry + Audit Engine

**Status: Deferred**
**Depends on: 014_CASCE_PHASE2_COHERENCE.md (must complete and re-measure first)**

---

## Context

Phase 3 is the heaviest component of CASCE: a runtime capability registry and tiered audit engine. It addresses a specific failure mode that Phases 1 and 2 cannot fix — Edward making claims about system state (what tools are available, whether a service is connected, what a skill's status is) that are incorrect because the LLM inferred rather than verified.

**Unlock condition**: Phase 2 re-measurement shows persistent failures AND those failures are specifically capability-state claims — not just tense errors. Examples:
- "I can send you a WhatsApp message" when WhatsApp skill is disabled
- "I've checked your calendar" when Apple Services MCP is not connected
- "Your widget has been updated" when iOS widget skill is off

**Close as "not needed"** if Phase 2 coherence check resolves the remaining failures, OR if Phase 0 measurement showed failures were phrasing/tense errors only (not capability state claims).

---

## STOP — Read Before Implementing

- Do not start until Phase 2 re-measurement specifically shows capability-state claim failures
- This is the most complex phase — adds a registry, audit levels, and pre-response checks
- If Phase 2 fixed the problem: close this plan immediately
- Read the original CASCE spec (user's requirements document) for full context on audit levels

---

## Design Rationale

The Haiku coherence check in Phase 2 operates on tool call evidence: "was X in tool_calls_made?" This works for action claims but fails for capability claims, because capability claims aren't backed by tool calls at all — they're the LLM asserting knowledge about the runtime environment.

A capability registry solves this by providing a ground-truth snapshot of what's actually available at runtime, derived from `get_available_tools()` rather than model inference.

---

## Scope

### New Files
- `backend/services/governance/capability_registry.py` — runtime snapshot of available tools
- `backend/services/governance/audit_engine.py` — tiered pre-response audit

### Modified Files
- `backend/services/governance/__init__.py` — add exports
- `backend/services/graph/streaming.py` — pass capability snapshot to audit engine

---

## Capability Registry Design

```python
# capability_registry.py

from services.tool_registry import get_available_tools

class CapabilitySnapshot:
    """Immutable snapshot of available tools for a single turn."""

    def __init__(self, tools: list):
        self._names: set[str] = {t.name for t in tools}
        self._by_domain: dict[str, list[str]] = self._index_by_domain(tools)

    def has_tool(self, name: str) -> bool:
        return name in self._names

    def list_available(self) -> list[str]:
        return sorted(self._names)

    def find_by_domain(self, domain: str) -> list[str]:
        return self._by_domain.get(domain, [])

    def _index_by_domain(self, tools) -> dict[str, list[str]]:
        # Group by inferred domain (messaging, search, execution, etc.)
        # Uses tool name prefixes and known groupings from tool_registry.py
        ...


async def get_turn_snapshot() -> CapabilitySnapshot:
    """Get a capability snapshot for the current turn. Uses tool registry cache (5s TTL)."""
    tools = await get_available_tools()
    return CapabilitySnapshot(tools)
```

---

## Audit Engine Design

Four audit levels, matching the original CASCE spec:

| Level | When | What |
|---|---|---|
| 0 | Casual/chat turns | No audit |
| 1 | Any turn with tool calls | Lightweight: verify tool names in draft match actual available tools |
| 2 | Operational multi-step turns | Check system state for claimed capabilities (widget, DB, notebooks) |
| 3 | Messaging, destructive, self-evolution | Strict: verify recipient identity, confirm external action tools available |

**Key constraint**: Audit level is determined by request type, not run on every turn. Level 0 dominates (most turns are conversational). Level 3 fires only on the narrow set of sensitive actions.

```python
# audit_engine.py

from dataclasses import dataclass
from typing import Literal

@dataclass
class AuditResult:
    level: int
    claims_verified: list[str]
    claims_softened: list[str]   # claims that were downgraded
    claims_blocked: list[str]    # claims that require explicit correction
    modified_draft: str | None   # None if no changes needed


async def run_audit(
    draft: str,
    request_type: Literal["chat", "lookup", "operational", "messaging", "destructive", "self_evolution"],
    capability_snapshot: CapabilitySnapshot,
    tool_calls_made: list[str],
) -> AuditResult:
    """
    Run the appropriate audit level for this turn's request type.
    Returns AuditResult with modified_draft (or None if unchanged).
    Never raises.
    """
    level = _determine_audit_level(request_type, tool_calls_made)
    ...
```

**Request type classification** is itself a lightweight Haiku call (similar to existing triage in `heartbeat/triage_service.py`). Only needed when audit level > 0.

---

## Integration in `streaming.py`

The capability snapshot is taken once per turn, before the tool loop, and passed to the audit engine at response time:

```python
# Before tool loop (~line 1590):
from services.governance.capability_registry import get_turn_snapshot
_capability_snapshot = await get_turn_snapshot()  # uses 5s cache, cheap

# After full_response = result["text"] (alongside Phase 0 and Phase 2):
from services.governance.audit_engine import run_audit
audit_result = await run_audit(
    draft=full_response,
    request_type=...,  # classified from message
    capability_snapshot=_capability_snapshot,
    tool_calls_made=[tc["name"] for tc in tool_calls_made],
)
if audit_result.modified_draft:
    full_response = audit_result.modified_draft
```

---

## Latency Considerations

- `get_turn_snapshot()` reuses `get_available_tools()` which has a 5s TTL cache — effectively free
- Level 0 audit: 0ms
- Level 1 audit: <5ms (set membership checks only)
- Level 2 audit: ~10–50ms (a few lightweight tool probe calls — e.g., `list_persistent_dbs()`)
- Level 3 audit: ~100–300ms (may include identity verification tool calls)
- Request type classification: ~300–500ms (Haiku) — only for Level 2/3

Worst-case additional latency for a Level 3 turn: ~800ms. Acceptable for messaging/destructive/evolution turns where correctness matters more than speed.

---

## Verification Checklist

- [ ] Phase 2 re-measurement specifically shows capability-state claim failures (not just tense errors)
- [ ] `CapabilitySnapshot` correctly reflects current `get_available_tools()` output
- [ ] Level 0 audit adds no latency (pure fast-path)
- [ ] Level 1 audit correctly identifies tool name mismatches
- [ ] Level 2 audit correctly probes widget/DB/notebook state without false positives
- [ ] Level 3 audit blocks messaging claims when messaging tools are absent from snapshot
- [ ] All exceptions return original draft unchanged
- [ ] `chat_with_memory` path unaffected

---

## Expected Outcome

If Phase 3 is implemented and deployed, the series closes here. No Phase 4 is planned. The original CASCE spec's request profiler and action receipts system can be added as standalone utilities if metrics and logging requirements grow.

---

## Series Close Conditions

This plan (and the entire CASCE series) closes when any of the following is true:
1. All four CASCE categories measure below 2% in any phase re-measurement
2. Edward's qualitative assessment confirms the failure patterns are no longer noticeable in normal use
3. Phase 3 is deployed and post-deployment measurement confirms resolution

At close: update all four plan files to **Complete** and update the `CLAUDE.md` table.
