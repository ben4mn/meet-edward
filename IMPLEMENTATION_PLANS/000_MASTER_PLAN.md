# Plan 000: Master Plan — Edward Cross-Platform + Autonomous Knowledge

## STOP: Read This Entire Document Before Making Any Changes

This is the master architecture document for the Edward project fork. Every subsequent plan (001-004) is derived from the decisions documented here.

**Revised**: Based on brainstorming workshop. Original scope (Windows + Telegram) expanded to cross-platform foundation + autonomy framework + NotebookLM knowledge system.

---

## Why We're Forking

The original Edward is a powerful full-stack AI assistant, but it has gaps:

| Problem | Root Cause | Impact |
|---------|-----------|--------|
| **Can't run on Windows** | Shell scripts use bash/brew, startup assumes macOS | Backend won't start at all |
| **os.uname() crashes** | `os.uname()` doesn't exist on Windows | Import errors on 2 services |
| **Shell exec hardcoded** | PATH hardcoded to `/opt/homebrew/bin:...` | Shell execution skill broken |
| **Thin system prompt** | Paper-thin persona, no self-awareness or judgment framework | Autonomous behavior is unpredictable |
| **No deep knowledge system** | Documents embed only first 500 chars, no chunking or ingestion | Can't act as a knowledge base |
| **Triage hardcodes iMessage** | Heartbeat triggers say "send via iMessage" | Broken on Windows, inflexible |
| **No prompt caching** | All 9 LLM call sites pay full token price | Higher cost than necessary |

**This fork addresses ALL of these while preserving 100% of existing functionality on macOS.**

---

## Architecture: What Changes vs. What Stays

### Stays Exactly The Same (No Touch)
- Frontend (Next.js) — fully cross-platform already
- Core backend (FastAPI, LangGraph, memory, documents, scheduling)
- Database (PostgreSQL + pgvector + asyncpg)
- Twilio SMS/WhatsApp integration
- Code execution (Python, JS, SQL)
- Web search (Brave), HTML hosting, file storage
- Orchestrator, evolution service
- Memory system (extraction, reflection, deep retrieval, consolidation)

### Changes

| Component | Before | After | Why |
|-----------|--------|-------|-----|
| **Startup scripts** | Bash only | + PowerShell equivalents (.ps1) | Windows can't run bash |
| **Platform checks** | `os.uname()` | `sys.platform == "darwin"` | os.uname() crashes on Windows |
| **Shell execution** | Hardcoded bash + macOS PATH | Platform-aware (cmd.exe on Windows) | Shell skill works on Windows |
| **System prompt** | 1-sentence persona | + Values, capabilities map, platform context, autonomy calibration | Autonomous agent needs self-awareness |
| **Triage prompts** | Hardcoded "iMessage" | Channel-agnostic, dynamic | Works on any platform |
| **Knowledge** | Documents only | + NotebookLM skill (13 tools) | Deep, source-grounded knowledge bases |
| **LLM calls** | No prompt caching | Ephemeral cache on all call sites | ~30-50% token savings |

### New Architecture Diagram
```
                    ┌─────────────────────────────┐
                    │         Frontend             │
                    │    (Next.js :3000 / PWA)     │
                    └──────────┬──────────────────┘
                               │ SSE + HTTP
                    ┌──────────┴──────────────────┐
                    │      Backend (FastAPI :8000)  │
                    │                              │
                    │  ┌────────────────────────┐  │
                    │  │   LangGraph Agent       │  │
                    │  │   (memory, tools, LLM)  │  │
                    │  └────────┬───────────────┘  │
                    │           │                   │
                    │  ┌────────┴───────────────┐  │
                    │  │  Tool Registry          │  │
                    │  │  (skill-gated)          │  │
                    │  └────────────────────────┘  │
                    │           │                   │
                    │  ┌────────┴───────────────┐  │
                    │  │  Services               │  │
                    │  │  ├─ Messaging (Twilio)  │  │
                    │  │  ├─ iMessage (macOS)    │  │
                    │  │  ├─ NotebookLM (NEW)   │  │
                    │  │  └─ Push (VAPID)       │  │
                    │  └────────────────────────┘  │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────┴──────────────────┐
                    │  PostgreSQL + pgvector        │
                    │  (memories, conversations,    │
                    │   contacts, checkpoints)      │
                    └──────────────────────────────┘
                               │
                    ┌──────────┴──────────────────┐
                    │  Google NotebookLM (NEW)      │
                    │  (via notebooklm-py library)  │
                    │  Notebooks, sources, Q&A,     │
                    │  research, artifacts          │
                    └──────────────────────────────┘
```

---

## Key Design Decisions

### 1. Cross-Platform (Not Windows-Only)
- **Decision**: Support both macOS and Windows. Don't migrate to one OS.
- **Rationale**: User is on Windows now but may switch to macOS later. Build once, run anywhere.

### 2. PWA as Primary Interface
- **Decision**: Push notifications + PWA chat as the primary user interaction channel.
- **Rationale**: Already fully implemented (VAPID Web Push, installable app, mobile-responsive). No need for Telegram or additional messaging channels.

### 3. NotebookLM for Deep Knowledge
- **Decision**: Integrate Google NotebookLM as a skill via `notebooklm-py` library.
- **Rationale**: Provides source-grounded Q&A, cross-source reasoning, and artifact generation (audio, quizzes, mind maps) that would take months to build in-house. Acceptable trade-off: uses undocumented APIs, suitable for personal projects.

### 4. Values-Based Autonomy (Not Rules)
- **Decision**: Add lightweight system prompt sections for identity, capabilities, platform awareness, and autonomy calibration. No rigid behavior rules.
- **Rationale**: Preserves the original creator's "non-deterministic programming" philosophy while giving Edward self-awareness and judgment principles.

### 5. Prompt Caching: Ephemeral on All Static Content
- **Decision**: Add `cache_control: {"type": "ephemeral"}` to all static prompt prefixes.
- **Rationale**: 9+ LLM call sites, all have static instruction text. ~30-50% savings on main chat.

### 6. Telegram: Deferred
- **Decision**: Deprioritize Telegram integration. PWA covers the use case.
- **Rationale**: Telegram would just be another Edward↔user channel, not outbound messaging to others. Can be revisited if push notifications prove unreliable.

---

## Implementation Order & Dependencies

```
Plan 001: Cross-Platform Foundation
    │   (no dependencies — pure infrastructure)
    │
Plan 002: Autonomy Framework
    │   (depends on 001 — prompt references platform context)
    │
Plan 003: NotebookLM Integration
    │   (depends on 002 — Edward needs autonomy framework to use NLM with judgment)
    │
Plan 004: Prompt Caching
        (depends on 003 — apply caching after all LLM call sites are finalized)
```

| # | Plan | Status | Effort |
|---|------|--------|--------|
| 001 | [Cross-Platform Foundation](001_CROSS_PLATFORM_FOUNDATION.md) | **Complete** | 0.5-1 day |
| 002 | [Autonomy Framework](002_AUTONOMY_FRAMEWORK.md) | **Complete** | 0.5-1 day |
| 003 | [NotebookLM Integration](003_NOTEBOOKLM_INTEGRATION.md) | **Complete** | 2-3 days |
| 004 | [Prompt Caching](004_PROMPT_CACHING.md) | Active | 0.5-1 day |
| — | [Telegram Integration](DEFERRED_TELEGRAM_INTEGRATION.md) | Deferred | — |

**Total estimated effort**: ~4-6 days

---

## Cost Estimates

### Per-Message Cost (with caching, Plan 004)

| Component | Model | Without Cache | With Cache | Frequency |
|-----------|-------|-------------|-----------|-----------|
| Main response | Sonnet 4.5/4.6 | $0.02-0.04 | $0.01-0.025 | Every message |
| Memory extraction | Haiku 4.5 | $0.001 | $0.0002 | Every message |
| Search tags | Haiku 4.5 | $0.0005 | $0.0001 | Every message |
| Reflection | Haiku 4.5 | $0.001 | $0.0002 | Every message |
| Deep retrieval | Haiku 4.5 | $0.001 | $0.0002 | ~30% of messages |

**Monthly estimate (50 messages/day with caching): ~$10-20/month**

### NotebookLM (Plan 003)
- **Google NotebookLM**: Free for personal use (as of 2026)
- **notebooklm-py**: MIT license, no API costs
- **Only cost**: Anthropic tokens for Edward's tool calls that trigger NLM operations

---

## Success Criteria

- [ ] Backend starts on both Windows and macOS without errors
- [ ] macOS-only skills show "unavailable" (not crash) on Windows
- [ ] System prompt includes identity, capabilities, platform context, and autonomy sections
- [ ] Triage prompts are channel-agnostic (no hardcoded "iMessage")
- [ ] NotebookLM skill creates notebooks, adds sources, queries, and generates artifacts
- [ ] Prompt caching reduces token usage by 30%+
- [ ] All existing macOS functionality preserved (no regressions)
- [ ] Edward demonstrates autonomous knowledge-building behavior

---

## Non-Goals (Explicitly Out of Scope)

- Docker support (use native installs for now)
- WSL2 setup guide (native Windows is simpler)
- Discord/Telegram integration (deferred, PWA is sufficient)
- Rich UI for NotebookLM (skill toggle + chat tools is sufficient)
- Windows Contacts integration (no equivalent to AppleScript)
- Renaming Edward to "Edweird" (deferred, can be done anytime)
