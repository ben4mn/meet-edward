# AGENTS.md

This file provides guidance to Codex when working in this repository.

## Commit Rules

- Do NOT add `Co-Authored-By` lines to commit messages in this repository.

## Project Overview

Edward is a full-stack AI assistant with long-term memory, built with Next.js, FastAPI, and PostgreSQL. The live chat runtime now uses direct provider SDKs and a simple checkpoint store:

- Tier 1 chat: direct Anthropic SDK or OpenAI Responses API / Codex OAuth
- Tier 2 background intelligence: Anthropic Haiku via `services/llm_client.py`
- Conversation state: `conversation_messages` table via `services/checkpoint_store.py`
- Legacy LangGraph code remains only for migration/debug fallback on old conversations

## Implementation Plans

Active implementation plans for the `cl-feature` branch live in `IMPLEMENTATION_PLANS/`.

| Plan | Description | Status |
|------|-------------|--------|
| [000_MASTER_PLAN.md](IMPLEMENTATION_PLANS/000_MASTER_PLAN.md) | Master architecture, decisions, cost estimates | Reference |
| [001_CROSS_PLATFORM_FOUNDATION.md](IMPLEMENTATION_PLANS/001_CROSS_PLATFORM_FOUNDATION.md) | PowerShell scripts, platform fixes for Windows + macOS | Complete |
| [002_AUTONOMY_FRAMEWORK.md](IMPLEMENTATION_PLANS/002_AUTONOMY_FRAMEWORK.md) | Values-based system prompt, platform context, channel-agnostic triage | Complete |
| [003_NOTEBOOKLM_INTEGRATION.md](IMPLEMENTATION_PLANS/003_NOTEBOOKLM_INTEGRATION.md) | Google NotebookLM skill integration | Complete |
| [004_PROMPT_CACHING.md](IMPLEMENTATION_PLANS/004_PROMPT_CACHING.md) | Anthropic prompt caching | Complete |
| [005_EXECUTION_FIX.md](IMPLEMENTATION_PLANS/005_EXECUTION_FIX.md) | Windows execution environment fix | Complete |
| [007_NOTEBOOKLM_REVAMP.md](IMPLEMENTATION_PLANS/007_NOTEBOOKLM_REVAMP.md) | `notebooklm-mcp-cli` migration and tool expansion | Complete |
| [008_PROMPT_OPTIMIZATION_AND_MEMORY_SEEDING.md](IMPLEMENTATION_PLANS/008_PROMPT_OPTIMIZATION_AND_MEMORY_SEEDING.md) | System prompt trim and instruction memory seeding | Complete |
| [009_DUAL_PROVIDER_LLM.md](IMPLEMENTATION_PLANS/009_DUAL_PROVIDER_LLM.md) | Direct-SDK dual-provider runtime, checkpoint store, OpenAI/Codex OAuth | Substantially implemented; read before editing related code |
| [DEFERRED_TELEGRAM_INTEGRATION.md](IMPLEMENTATION_PLANS/DEFERRED_TELEGRAM_INTEGRATION.md) | Telegram bot with long-polling | Deferred |

IMPORTANT: Read the relevant plan before making changes. Several plans include STOP warnings, strict rules, and verification checklists.

## Running The App

The backend runs on macOS and Windows. macOS has full feature support; Windows runs with graceful degradation for Apple-specific features.

### macOS

```bash
./setup.sh
./restart.sh
./restart.sh frontend
./restart.sh backend

cd backend && ./start.sh
cd frontend && npm install && npm run dev
```

### Windows (PowerShell)

```powershell
.\setup.ps1
.\restart.ps1
.\restart.ps1 frontend
.\restart.ps1 backend

cd backend; .\start.ps1
cd frontend; npm install; npm run dev
```

### Frontend Checks

```bash
cd frontend && npm run lint
cd frontend && npm run build
```

## High-Level Architecture

```text
Frontend (Next.js :3000)
    ->
Backend (FastAPI :8000)
    ->
Conversation runtime (services/graph/streaming.py)
    |- checkpoint store (conversation_messages)
    |- memory/document retrieval
    |- provider dispatch (Anthropic / OpenAI / Codex OAuth)
    |- tool loop
    `- post-turn background extraction/tagging/reflection
    ->
PostgreSQL (:5432)
```

### Provider Model

- Anthropic models are called through `anthropic.AsyncAnthropic`.
- OpenAI API-key models are called through `openai.AsyncOpenAI.responses.create()`.
- ChatGPT subscription-based OpenAI access uses Codex OAuth plus `https://chatgpt.com/backend-api/codex/responses`.
- Provider is derived from the model ID prefix, not stored separately.
- Tier 2 background jobs stay Anthropic/Haiku even when Tier 1 chat uses OpenAI.

### Conversation Flow

1. Load messages from `conversation_messages` through `services.checkpoint_store`.
2. Append the new user message, including attachment blocks when present.
3. Retrieve memories, optional deep retrieval enrichments, and relevant documents.
4. Build system prompt from base settings plus autonomy/platform/assumption context and live time.
5. Get currently available tools from `services.tool_registry`.
6. Convert tools into provider-native schemas with `services/graph/tool_schema.py`.
7. Run the LLM/tool loop in `services/graph/streaming.py`.
8. Persist updated messages plus metadata back to `conversation_messages`.
9. Run memory extraction, search-tag generation, and reflection asynchronously after the turn.

### Legacy Graph Status

- `backend/services/graph/graph.py`, `nodes.py`, and `state.py` still exist.
- They are not the primary chat path anymore.
- They are kept for migration/debug support and old-conversation fallback via `get_legacy_graph()`.
- Do not build new features on the legacy LangGraph path unless you are intentionally touching migration support.

## Frontend Routes

| Route | Auth | Description |
|------|------|-------------|
| `/` | Public | Redirects to `/login` |
| `/login` | Public | Password login |
| `/chat` | Protected | Main chat interface |
| `/settings` | Protected | Settings dashboard |

Route wrappers are controlled by `ConditionalLayout.tsx`:

- `/login` uses `AuthProvider` only
- Authenticated routes use `AuthProvider -> AuthGuard -> ChatProvider -> ClientLayout`

The settings page is tile-based and currently includes:

- General
- Skills
- Edward's Servers
- Heartbeat
- Memories
- Documents
- Events
- Databases
- Files
- Evolution
- Orchestrator

## Startup Order (`backend/main.py`)

Initialization order matters:

1. `init_db()`
2. `initialize_checkpoint_store(DATABASE_URL)`
3. file storage directory setup
4. `init_skills()`
5. built-in MCP clients: WhatsApp bridge, Apple Services
6. custom MCP servers from DB
7. NotebookLM client
8. tool registry
9. scheduled-event scheduler
10. heartbeat service
11. memory consolidation service
12. evolution deploy recovery
13. orchestrator recovery

Shutdown happens in roughly reverse order. Legacy LangGraph shutdown is still called last if it was initialized for migration.

## Key Components

### Conversation Runtime (`backend/services/graph/streaming.py`)

- Main chat orchestration for both streaming and non-streaming flows
- Direct provider dispatch:
  - `_call_anthropic()`
  - `_call_openai()`
  - `_call_codex()`
  - `_call_llm()`
- Structured SSE event generation
- Tool loop with retry/circuit-breaker logic
- Plan-tool event emission
- CC session inline streaming support

### Checkpoint Store (`backend/services/checkpoint_store.py`)

- Replaces LangGraph as the primary message persistence layer
- Stores messages in `conversation_messages`
- Stores per-conversation metadata snapshots alongside messages
- Conversations router falls back to legacy graph state only when new-store messages are absent

### Tool System (`backend/services/graph/`)

- `tools.py`: core tool definitions
- `tool_decorator.py`: lightweight replacement for LangChain's `@tool`
- `tool_schema.py`: converts Edward/MCP tools into Anthropic or OpenAI schema formats
- Tools are provider-agnostic at execution time; only schema formatting changes per provider

### Tool Registry (`backend/services/tool_registry.py`)

- Central tool filtering based on skill state and runtime availability
- Short TTL cache to avoid DB reads on every request
- Always-on categories include memory, documents, file storage, plans, scheduled events, heartbeat, evolution, custom MCP management
- Other categories are gated by skill enablement and service availability
- Worker agents use `get_worker_tools()` to exclude orchestrator/evolution recursion

### Shared Tier 2 LLM Client (`backend/services/llm_client.py`)

- Central Anthropic Haiku wrapper for background intelligence
- Used by memory extraction helpers, heartbeat triage, reflection, search tags, deep retrieval, consolidation, and greeting generation

### Memory System (`backend/services/memory_service.py`)

- Embeddings: `all-MiniLM-L6-v2` (384 dimensions)
- Retrieval: hybrid vector similarity plus BM25
- Memory types: `fact`, `preference`, `context`, `instruction`
- Tiering: `observation`, `belief`, `knowledge`
- Temporal tracking: `timeless`, `temporary`, `evolving`

### Document Store (`backend/services/document_service.py`)

- Persistent long-form document storage with semantic retrieval
- Tools: `save_document`, `read_document`, `edit_document`, `search_documents`, `list_documents`, `delete_document`
- Relevant document titles are surfaced into LLM context; full content is pulled on demand

### Skills And Integrations (`backend/services/skills_service.py`)

- Tracks enable/disable state in the database
- Initializes or tears down integrations as needed
- Supports hot reload through the skills API

Current built-in skill families include:

- Twilio SMS
- Twilio WhatsApp
- iMessage AppleScript
- WhatsApp bridge
- Brave Search
- Python / JavaScript / SQL / Shell execution
- Apple Services MCP
- iOS widget
- contacts lookup
- HTML hosting
- orchestrator
- NotebookLM
- push notifications

### Custom MCP (`backend/services/custom_mcp_service.py`, `backend/services/custom_mcp_tools.py`)

- Edward can search for, install, restart, update, and remove runtime MCP servers
- Supports both `npx` and `uvx` runtimes
- Added server tools are prefixed and hot-loaded into the registry

### NotebookLM (`backend/services/notebooklm_service.py`)

- Uses `notebooklm-mcp-cli`
- Lazy singleton client
- Name-based notebook references for the LLM
- Broad surface area across notebooks, sources, chat, research, artifacts, sharing, notes, and Edward bridge tools

### Execution System (`backend/services/execution/`)

- Python, JavaScript, SQL, and shell execution use per-conversation sandboxes
- Shared sandbox file tools are deduplicated at registry level
- Persistent database tools are bundled with SQL skill availability

### Scheduled Events (`backend/services/scheduled_events_service.py`, `backend/services/scheduler_service.py`)

- In-process scheduler polls every 30 seconds
- Supports one-time and recurring execution
- Runs scheduled work through `chat_with_memory()`

### Heartbeat (`backend/services/heartbeat/`)

- Multi-track background monitoring
- Current tracks include iMessage, Calendar, Email, and WhatsApp polling/config
- Layered triage pipeline
- Pending awareness can be injected into the next user turn

### Reflection / Deep Retrieval / Consolidation / Search Tags

- `reflection_service.py`: post-turn enrichment for next-turn memory recall
- `deep_retrieval_service.py`: gated multi-query retrieval for harder turns
- `consolidation_service.py`: background clustering, flags, and connections
- `search_tag_service.py`: generates searchable conversation tags

### Auth, Push, Widget, Files, Databases

- `auth_service.py`: single-user password auth via JWT cookie
- `push_service.py`: VAPID-based Web Push
- `widget_service.py`: Scriptable widget state and tokenized public API
- `file_storage_service.py`: persistent file storage and metadata
- `persistent_db_service.py`: named PostgreSQL schemas exposed as persistent databases

### Orchestrator / Evolution / Codex

- `orchestrator_service.py`: spawns internal worker agents and CC sessions
- `evolution_service.py`: self-evolution workflow and deploy recovery
- `claude_code_service.py` / `cc_manager_service.py`: Claude Code session execution and streaming into the UI

## Database Tables

Primary tables in `backend/services/database.py`:

- `settings`
- `conversations`
- `conversation_messages`
- `memories`
- `documents`
- `external_contacts`
- `skills`
- `scheduled_events`
- `files`
- `widget_state`
- `widget_tokens`
- `push_subscriptions`
- `persistent_databases`
- `custom_mcp_servers`
- `heartbeat_events`
- `triage_results`
- `heartbeat_config`
- `memory_enrichments`
- `memory_connections`
- `memory_flags`
- `consolidation_cycles`
- `consolidation_config`
- `claude_code_sessions`
- `evolution_config`
- `evolution_history`
- `orchestrator_tasks`
- `orchestrator_config`
- `codex_oauth_tokens`

Legacy LangGraph checkpoint tables may still exist in some databases. Do not assume they are the source of truth for active conversations.

## API Surface

Core routes:

- `POST /api/chat`
- `POST /api/chat/simple`
- `POST /api/greeting`
- `GET/POST /api/settings`
- `GET /api/settings/models`
- `GET /api/settings/openai/status`
- `POST /api/settings/openai/login`
- `POST /api/settings/openai/logout`

Conversation and memory routes:

- `GET/PATCH/DELETE /api/conversations/{id}`
- `GET /api/conversations`
- `GET/PATCH/DELETE /api/memories/{id}`
- `GET /api/memories`
- `GET/POST /api/documents`
- `GET/PATCH/DELETE /api/documents/{id}`

Operational routes:

- skills: `/api/skills`, `/api/skills/{id}`, `/api/skills/reload`
- events: `/api/events`, `/api/events/{id}`
- custom MCP: `/api/custom-mcp`, `/api/custom-mcp/{id}`, `/api/custom-mcp/{id}/restart`
- heartbeat: `/api/heartbeat/status`, `/api/heartbeat/events`, `/api/heartbeat/triage`, `/api/heartbeat/config`, `/api/heartbeat/recent-senders`
- files: `/api/files`, `/api/files/{id}`, `/api/files/{id}/download`
- databases: `/api/databases`, `/api/databases/{name}`, `/api/databases/{name}/tables`, `/api/databases/{name}/tables/{table}/columns`
- widget: `/api/widget`, `/api/widget/chat`, `/api/widget/token`, `/api/widget/token/regenerate`
- push: `/api/push/vapid-key`, `/api/push/subscribe`, `/api/push/unsubscribe`, `/api/push/status`, `/api/push/test`
- auth: `/api/auth/status`, `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/change-password`
- evolution: `/api/evolution/status`, `/api/evolution/history`, `/api/evolution/config`, `/api/evolution/trigger`, `/api/evolution/rollback/{cycle_id}`
- orchestrator: `/api/orchestrator/status`, `/api/orchestrator/config`, `/api/orchestrator/tasks`, `/api/orchestrator/tasks/{id}`, `/api/orchestrator/tasks/{id}/cancel`, `/api/orchestrator/tasks/{id}/message`, `/api/orchestrator/tasks/{id}/events`
- debug: `/api/debug/graph`, `/api/debug/health`, LangSmith trace routes
- webhooks: Twilio SMS and WhatsApp inbound webhook routes

## Environment Variables

Required:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Common optional variables:

```env
DATABASE_URL=postgresql+asyncpg://edward:edward@localhost:5432/edward
OPENAI_API_KEY=sk-...

LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...
LANGCHAIN_PROJECT=edward

TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
TWILIO_WEBHOOK_URL=https://your-domain.com/api/webhook/twilio
TWILIO_WHATSAPP_WEBHOOK_URL=https://your-domain.com/api/webhook/twilio/whatsapp

BRAVE_SEARCH_API_KEY=BSA...
HTML_HOSTING_API_KEY=...
HTML_HOSTING_URL=https://html.zyroi.com
GITHUB_TOKEN=ghp_...

JWT_SECRET_KEY=change-me
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
FILE_STORAGE_ROOT=./storage

MCP_WHATSAPP_ENABLED=false
MCP_WHATSAPP_SERVER_DIR=/path/to/whatsapp-mcp/whatsapp-mcp-server
MCP_APPLE_ENABLED=true
```

Notes:

- Codex OAuth stores tokens in `codex_oauth_tokens`; no static OpenAI OAuth env vars are required.
- NotebookLM uses local profile credentials from `notebooklm-mcp-cli` rather than env vars.
- Legacy LangGraph packages are optional and only needed for old-conversation fallback.

## Key Integration Points

- Live chat does not use LangChain message objects in the primary runtime.
- Primary message persistence is `conversation_messages`, not LangGraph checkpoints.
- The legacy graph is read-only fallback for old conversation history.
- Provider dispatch is based on model ID prefix:
  - Anthropic: `claude-*`
  - OpenAI: `gpt-*`, `o1-*`, `o3-*`, `o4-*`
- OpenAI model visibility in settings depends on `OPENAI_API_KEY` or valid Codex OAuth tokens.
- Tool execution remains `await tool.ainvoke(args)` regardless of provider.
- Tool schema conversion happens at call time through `tool_schema.py`.
- Search tags, reflection, and memory extraction run after the user-visible response path.
- Orchestrator workers call `chat_with_memory()` and use filtered tools.
- Evolution and CC sessions are separate from the normal chat tool loop, but their events are surfaced into the frontend.

## SSE Event Protocol

`POST /api/chat` emits structured SSE events. Event types currently include:

- `thinking`
- `progress`
- `tool_start`
- `code`
- `execution_output`
- `execution_result`
- `tool_end`
- `content`
- `error`
- `done`
- `interrupted`
- `plan_created`
- `plan_step_update`
- `plan_updated`
- `plan_completed`
- `cc_session_start`
- `cc_text`
- `cc_tool_use`
- `cc_tool_result`
- `cc_session_end`

Frontend parsing lives in `frontend/lib/api.ts` via `streamChatEvents()`.
