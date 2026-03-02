# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commit Rules

- **Do NOT add `Co-Authored-By` lines** to any commit messages in this repository. No Claude attribution in git history.

## Project Overview

Edward is a full-stack AI assistant with long-term memory, built with Next.js, FastAPI, and PostgreSQL. Uses LangGraph for conversation orchestration and pgvector for memory retrieval.

## Running the App

**The backend must run natively on macOS** (not in a container). This is required for the scheduled events scheduler and AppleScript/MCP integrations.

```bash
# First-time setup
./setup.sh                    # Creates venv, installs deps, sets up .env

# Recommended: restart script (handles both services)
./restart.sh                  # Restart both frontend + backend
./restart.sh frontend         # Restart only frontend
./restart.sh backend          # Stop and restart only backend

# Individual services
cd backend && ./start.sh                       # Backend (FastAPI :8000)
cd frontend && npm install && npm run dev      # Frontend (Next.js :3000)

# Lint frontend
cd frontend && npm run lint
```

After initial setup, `./start.sh` handles venv activation and auto-installs new dependencies.

### Build frontend for production
```bash
cd frontend && npm run build
```

## Architecture

```
Frontend (Next.js :3000)  →  Backend (FastAPI :8000)  →  PostgreSQL (:5432)
                                      ↓
                              LangGraph Agent
                                      ↓
                              Claude API (Anthropic)
```

### Frontend Routes

| Route | Auth | Description |
|-------|------|-------------|
| `/` | Public | Redirects to `/login` |
| `/login` | Public | Password login (AuthProvider only, no app shell) |
| `/chat` | Protected | Main chat interface (full app shell) |
| `/settings` | Protected | Settings page |

Route wrappers controlled by `ConditionalLayout.tsx`:
- `/` → redirects to `/login`
- `/login` → `<AuthProvider>` only
- Everything else → `<AuthProvider>` → `<AuthGuard>` → `<ChatProvider>` → `<ClientLayout>`

Single `AuthProvider` shared across `/login` and authenticated routes to preserve auth state during navigation. `AuthContext` uses sessionStorage hint for optimistic rendering (eliminates spinner flash on refresh).

### Startup Order (`main.py` lifespan)

Init order matters — tool registry must come after skills/MCP:

1. `init_db()` + `initialize_graph()` — DB tables + LangGraph checkpoint store
2. `init_skills()` — Load skill enabled states
3. MCP clients — WhatsApp, Apple Services subprocesses
4. Custom MCP servers — User-added servers from DB
5. Tool registry — Must be after all tool sources are initialized
6. Scheduler — Polls every 30s for due scheduled events
7. Heartbeat — iMessage listener + triage loop
8. Consolidation — Hourly memory clustering
9. Evolution — Check for pending deploys after restart
10. Orchestrator — Recover crashed worker tasks

All have matching shutdown hooks in reverse order.

### LangGraph Flow
```
preprocess → retrieve_memory → respond → extract_memory → END
                  ↓                ↓              ↓
            [pgvector search]  [tool loop]  [LLM extraction]
                  ↓                ↓              ↓
            ┌───────────────────────────────────────────────────────┐
            │              memories table (pgvector)                │
            └───────────────────────────────────────────────────────┘

Tool loop (in respond node):
┌─────────────────────────────────────────┐
│  LLM response                           │
│       ↓                                 │
│  tool_calls? ──yes──> execute tools     │
│       │                     ↓           │
│       no              add ToolMessage   │
│       ↓                     ↓           │
│    stream response    loop (max 5x)     │
└─────────────────────────────────────────┘
```

### Key Components

**LangGraph Agent** (`backend/services/graph/`)
- `state.py` - AgentState with messages, memories, node tracking
- `nodes.py` - preprocess, retrieve_memory, respond, extract_memory nodes
- `graph.py` - Graph construction and structure export
- `streaming.py` - SSE streaming with tool call loop and PostgreSQL checkpointing
- `tools.py` - Memory, document, messaging, search, code execution, and scheduled event tool definitions

**Tool Registry** (`backend/services/tool_registry.py`)
- Central tool management - dynamically loads tools based on skill state
- Uses short-lived cache (5s TTL) to avoid DB queries per request
- `get_available_tools()` returns filtered list for LLM binding
- Call `refresh_registry()` after skill enable/disable changes
- Memory, document, and scheduled event tools always available; other tools gated by skills

**Memory System** (`backend/services/memory_service.py`)
- Embedding: sentence-transformers (all-MiniLM-L6-v2, 384 dims)
- Search: Hybrid 70% vector similarity + 30% BM25 keyword
- Memory types: `fact`, `preference`, `context`, `instruction`
- Extraction: Uses Claude Haiku 4.5 to identify memorable info from conversations

**Document Store** (`backend/services/document_service.py`)
- Persistent storage for full documents (recipes, notes, reference guides, pet records, etc.)
- Embedding: same sentence-transformers model as memories, embeds `title + content[:500]`
- Search: Hybrid 70% vector + 30% BM25 (same weights as memories)
- Automatically surfaces relevant document titles in LLM context (title + ID only)
- LLM reads full content on demand via `read_document` tool
- Tools: `save_document`, `read_document`, `edit_document`, `search_documents`, `list_documents`, `delete_document`
- Always available (not skill-gated)
- REST API: `GET/POST /api/documents`, `GET/PATCH/DELETE /api/documents/{id}`
- Frontend: DocumentBrowser in settings page

**Custom MCP Servers** (`backend/services/custom_mcp_service.py`, `backend/services/custom_mcp_tools.py`)
- Self-service: Edward can discover, install, and use MCP servers autonomously at runtime
- Servers run as subprocesses via `npx` (Node.js) or `uvx` (Python) — no git clone needed
- Tool prefixing: each server's tools get prefixed with server name to avoid collisions
- Hot-reload: new tools available immediately via `refresh_registry()`
- LLM tools (always available): `search_mcp_servers`, `add_mcp_server`, `list_custom_servers`, `remove_mcp_server`
- REST API: `GET /api/custom-mcp`, `PATCH /api/custom-mcp/{id}`, `DELETE /api/custom-mcp/{id}`
- Frontend: CustomMCPPanel in settings page ("Edward's Servers")
- DB table: `custom_mcp_servers` (separate from hardcoded skills)
- GitHub search uses `GITHUB_TOKEN` env var for API access

**Skills System** (`backend/services/skills_service.py`)
- Manages integrations (iMessage AppleScript, Twilio SMS, Twilio WhatsApp, WhatsApp MCP, Brave Search, Code Interpreter, JavaScript Interpreter, SQL Database, Shell/Bash, Apple Services, HTML Hosting)
- Tracks enabled/disabled state in database
- Reports connection status for each skill
- Initializes MCP client on skill enable (not just DB toggle)
- Supports hot-reload via `POST /api/skills/reload`

**Brave Search** (`backend/services/brave_search_service.py`)
- Web search via Brave Search API
- Page content extraction using trafilatura
- Tools: `web_search`, `fetch_page_content`

**HTML Hosting** (`backend/services/html_hosting_service.py`)
- Create, update, and delete hosted HTML pages on html.zyroi.com
- Uses v2 JSON API with `X-API-Key` authentication
- Tools: `create_hosted_page`, `update_hosted_page`, `delete_hosted_page`, `check_hosted_slug`

**Execution System** (`backend/services/execution/`)
- Shared base: `base.py` with `ExecutionResult`, sandbox management, `run_subprocess()` helper
- **Python** (`python_execution.py`): Sandboxed via subprocess, blocked dangerous modules
- **JavaScript** (`javascript_execution.py`): Node.js subprocess, blocked network/child_process modules
- **SQL** (`sql_execution.py`): Per-conversation SQLite database, 500 row limit, 50MB max DB
- **Shell** (`shell_execution.py`): Bash with blocklist (sudo, curl, docker, etc.), restricted env
- `code_execution_service.py` is a thin re-export shim for backwards compatibility
- Per-conversation working directories (persisted between turns)
- 30-second timeout, 100KB output limit, automatic cleanup (24h)

**Scheduled Events** (`backend/services/scheduled_events_service.py`, `backend/services/scheduler_service.py`)
- Self-triggering system: Edward can schedule future actions (reminders, messages, tasks)
- Events stored in PostgreSQL `scheduled_events` table
- In-process asyncio loop polls every 30s for due events
- Executes events via `chat_with_memory()` — Edward has full tool access when processing
- Supports one-time and recurring events (cron patterns via `croniter`)
- Atomic pickup: `SELECT ... FOR UPDATE` prevents double-firing
- Failed recurring events still advance to next fire time
- LLM tools: `schedule_event`, `list_scheduled_events`, `cancel_scheduled_event` (always available)
- REST API: `GET/POST /api/events`, `GET/PATCH/DELETE /api/events/{id}`

**LangSmith Trace Inspection** (`backend/services/langsmith_service.py`)
- Lazy singleton LangSmith `Client`, returns `None` if not configured
- Queries traces by `thread_id` metadata using `and(in(metadata_key, ["thread_id"]), eq(metadata_value, "..."))` filter
- LangGraph creates flat root-level runs (not nested) — groups runs into conversation turns by timestamp proximity
- `get_latest_trace()` returns all runs in the most recent turn with aggregated latency/tokens
- Frontend: `TraceInspector` component in Debug Panel shows per-run timing bars, token counts, type badges
- Hidden entirely when LangSmith env vars are not configured

**Frontend State** (`frontend/lib/ChatContext.tsx`)
- React Context for chat state management
- Handles conversation selection, message history, streaming state

**Database** (`backend/services/database.py`)
- `settings` - Assistant configuration (includes `password_hash` for auth)
- `memories` - Long-term memory with vector embeddings (includes `temporal_nature`, `updated_at`)
- `conversations` - Conversation metadata and titles (includes `source`, `channel`, `notified_user`, `search_tags`)
- `checkpoints` - LangGraph persistence (managed by langgraph-checkpoint-postgres)
- `external_contacts` - SMS/WhatsApp contact to conversation mapping (with `last_channel` for reply routing)
- `skills` - Integration enabled state
- `scheduled_events` - Scheduled reminders, messages, and tasks
- `documents` - Persistent document store with vector embeddings
- `memory_enrichments` - Reflection service output (Haiku-generated queries + results)
- `memory_connections` - Links between related memories (from consolidation)
- `memory_flags` - Memory quality/staleness markers (from consolidation)
- `consolidation_cycles` - Consolidation run metrics
- `consolidation_config` - Consolidation settings (enabled, interval, etc.)
- `heartbeat_events` - iMessage/external message events
- `triage_results` - Heartbeat triage cycle metrics
- `heartbeat_config` - Heartbeat configuration
- `push_subscriptions` - Web Push subscription endpoints and keys
- `files` - Persistent file storage metadata
- `widget_state` - iOS Scriptable widget content and theme
- `widget_tokens` - Widget access tokens
- `persistent_databases` - Named PostgreSQL schema metadata
- `custom_mcp_servers` - User-added MCP server configurations
- `claude_code_sessions` - Claude Code session execution tracking
- `evolution_config` - Self-evolution settings
- `evolution_history` - Evolution cycle records and outcomes
- `orchestrator_tasks` - Worker agent task queue
- `orchestrator_config` - Orchestrator settings

**Messaging Services** (`backend/services/`)
- `twilio_service.py` - SMS and WhatsApp send/receive via Edward's phone number
- `mcp_client.py` - MCP client management for multiple integrations:
  - WhatsApp via whatsapp-mcp bridge
  - Apple Services via apple-mcp (Calendar, Reminders, Notes, Mail, Contacts, Maps, Messages)
- `imessage_service.py` - iMessage via AppleScript (macOS)
- `routers/webhooks.py` - Twilio inbound webhooks for SMS and WhatsApp (processes async, responds via API not TwiML)

Messaging tools available to LLM:
- `send_message` - Smart routing (default: Twilio SMS, WhatsApp if contact last used WhatsApp, or iMessage when requested)
- `send_sms` - Direct Twilio SMS
- `send_whatsapp` - Direct Twilio WhatsApp
- `send_imessage` - Send as user via iMessage
- `get_recent_messages` - Read user's iMessage history
- WhatsApp MCP tools (prefixed `whatsapp_*`) - Read/send WhatsApp as user via whatsapp-mcp bridge

Code execution tools available to LLM (when skill enabled):
- `execute_code` - Execute Python code and return results
- `execute_javascript` - Execute JavaScript code via Node.js
- `execute_sql` - Execute SQL queries against per-conversation SQLite
- `execute_shell` - Execute shell commands in sandbox
- `list_sandbox_files` - List files in the conversation's sandbox (shared across execution skills)
- `read_sandbox_file` - Read a file from the sandbox (shared across execution skills)

Document tools available to LLM (always available):
- `save_document` - Save a new document to the store
- `read_document` - Read full content of a stored document
- `edit_document` - Update a document's title, content, or tags
- `search_documents` - Semantic search across documents
- `list_documents` - List all stored documents
- `delete_document` - Remove a document

Scheduled event tools available to LLM (always available):
- `schedule_event` - Schedule a future action, reminder, or message
- `list_scheduled_events` - List upcoming/past events
- `cancel_scheduled_event` - Cancel a scheduled event

Search tools available to LLM (when skill enabled):
- `web_search` - Search the web using Brave Search
- `fetch_page_content` - Get full content of a web page

HTML hosting tools available to LLM (when skill enabled):
- `create_hosted_page` - Publish an HTML page to html.zyroi.com
- `update_hosted_page` - Update an existing hosted page
- `delete_hosted_page` - Delete a hosted page
- `check_hosted_slug` - Check if a URL slug is available

Apple Services tools available to LLM (when apple_services skill enabled):
- Calendar tools - Read/manage calendar events
- Reminders tools - Manage user's Apple Reminders (NOT for Edward's internal scheduling)
- Notes tools - Read/create notes
- Mail tools - Read/send emails via Apple Mail.app
- Contacts tools - Search/read contacts
- Maps tools - Location and directions

Custom MCP tools available to LLM (always available):
- `search_mcp_servers` - Search GitHub for MCP server packages
- `add_mcp_server` - Install and start a new MCP server (npx/uvx)
- `list_custom_servers` - List all custom servers with status
- `remove_mcp_server` - Stop and remove a custom server

File storage tools available to LLM (always available):
- `save_to_storage` - Move sandbox file to persistent storage
- `list_storage_files` - List stored files with filters (category, tags)
- `get_storage_file_url` - Get download URL for a file
- `read_storage_file` - Read text file contents
- `tag_storage_file` - Update file metadata (description, tags, category)
- `delete_storage_file` - Delete a stored file

Persistent database tools available to LLM (always available):
- `create_persistent_db` - Create a named PostgreSQL schema
- `query_persistent_db` - Execute SQL against a persistent database
- `list_persistent_dbs` - List all persistent databases
- `delete_persistent_db` - Delete a persistent database

Widget tools available to LLM (always available):
- `update_widget` - Update iOS widget state (title, subtitle, sections, theme)
- `get_widget_state_tool` - Get current widget state
- `update_widget_code` - Store raw Scriptable JavaScript
- `clear_widget_code` - Clear custom script, revert to structured data

Push notification tools available to LLM (always available):
- `send_push_notification` - Send Web Push notification to all subscribed devices

Contact tools available to LLM (always available on macOS):
- `lookup_contact` - Search macOS Contacts by name
- `lookup_phone` - Reverse phone number lookup

**Heartbeat System** (`backend/services/heartbeat/`)
- Monitors iMessage, Apple Calendar, and Apple Mail for incoming items requiring attention
- Multi-track listener architecture with per-track enable/disable and polling intervals
- Multi-layer triage: Layer 1 rules (zero cost) → Layer 2 Haiku classification → Layer 3 execute (memory/chat/push)
- `listener_imessage.py` - Polls `~/Library/Messages/chat.db` every 10s (Apple timestamps = date/1e9 + 978307200)
- `listener_calendar.py` - Polls Apple Calendar via MCP tools for upcoming events (configurable lookahead)
- `listener_email.py` - Polls Apple Mail via MCP tools for unread emails (filters by `@edward` mentions)
- `triage_service.py` - Classifies messages and decides action (ignore, remember, respond, push)
- `heartbeat_service.py` - Orchestrates all listeners + triage, injects briefing into system prompt
- DB tables: `heartbeat_events`, `triage_results`, `heartbeat_config`
- `heartbeat_config` supports per-track fields: `calendar_enabled`, `calendar_poll_seconds`, `calendar_lookahead_minutes`, `email_enabled`, `email_poll_seconds`
- REST API: `GET /api/heartbeat/status`, `GET /api/heartbeat/events`, `GET /api/heartbeat/triage`, `PATCH /api/heartbeat/config`
- Frontend: HeartbeatPanel in settings page (timeline view + config)

**Memory Reflection** (`backend/services/reflection_service.py`)
- Post-turn fire-and-forget enrichment of conversation context
- Generates 3-5 Haiku queries to find related memories, stores results in `memory_enrichments` table
- Enrichments loaded on the next turn to provide deeper context
- Runs asynchronously after each conversation turn (no latency impact)

**Deep Retrieval** (`backend/services/deep_retrieval_service.py`)
- Pre-turn gated multi-query memory search for complex conversations
- Runs 4 parallel queries (original + 3 Haiku-rewritten) when message is short or turn_count >= 3
- Context budget: MAX_MEMORY_CONTEXT_CHARS=8000

**Memory Consolidation** (`backend/services/consolidation_service.py`)
- Hourly background loop that clusters related memories via Haiku
- Creates `memory_connections` (links between related memories) and `memory_flags` (quality/staleness markers)
- DB tables: `memory_connections`, `memory_flags`, `consolidation_cycles`, `consolidation_config`
- Disabled by default (`consolidation_config.enabled = False`), enable via `PATCH /api/consolidation/config`
- REST API: `GET/PATCH /api/consolidation/config`, `GET /api/consolidation/status`

**Push Notifications** (`backend/services/push_service.py`, `backend/routers/push.py`)
- Self-hosted Web Push via VAPID authentication
- Tracks failed deliveries, auto-deactivates subscriptions after 3 failures
- DB table: `push_subscriptions` (endpoint, keys, is_active, failed_count)
- LLM tool: `send_push_notification(title, body, url?)` (always available)
- REST API: `GET /api/push/vapid-key`, `POST /api/push/subscribe`, `POST /api/push/unsubscribe`, `GET /api/push/status`, `POST /api/push/test`
- Requires `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` environment variables

**File Storage** (`backend/services/file_storage_service.py`, `backend/routers/files.py`)
- Persistent file storage with categories, tags, and metadata tracking
- Hex-prefix sharding on disk (`{id[0:2]}/{id}_{filename}`)
- Supports upload, download, sandbox-to-storage move, text file reading
- DB table: `files` (filename, stored_path, mime_type, size_bytes, category, tags, source)
- 50 MB max file size, whitelist of allowed MIME types
- LLM tools (always available): `save_to_storage`, `list_storage_files`, `get_storage_file_url`, `read_storage_file`, `tag_storage_file`, `delete_storage_file`
- REST API: `GET/POST /api/files`, `GET/PATCH/DELETE /api/files/{id}`, `GET /api/files/{id}/download`
- `FILE_STORAGE_ROOT` env var (default: `./storage`)

**Persistent Databases** (`backend/services/persistent_db_service.py`, `backend/routers/databases.py`)
- Named PostgreSQL schemas that persist across conversations (unlike per-conversation SQLite)
- Each "database" is a schema `edward_db_<name>` within the main PostgreSQL database
- SQL query validation with blocked patterns (DROP SCHEMA, GRANT, pg_* functions, etc.)
- Max 10 databases per user, 500 row limit, 30s query timeout
- LLM tools (always available): `create_persistent_db`, `query_persistent_db`, `list_persistent_dbs`, `delete_persistent_db`
- REST API: `GET /api/databases`, `GET/DELETE /api/databases/{name}`, `GET /api/databases/{name}/tables`

**Widget Service** (`backend/services/widget_service.py`, `backend/routers/widget.py`)
- iOS home screen widget via Scriptable app with token-based public access
- Supports structured data (sections with title/items) or raw Scriptable JavaScript
- Auto-generates content from scheduled events + memory stats when no custom state set
- DB tables: `widget_state` (title, subtitle, theme, sections, script), `widget_tokens` (token, is_active)
- LLM tools (always available): `update_widget`, `get_widget_state_tool`, `update_widget_code`, `clear_widget_code`
- REST API: `GET /api/widget?token=<token>` (public), `POST /api/widget/chat`, `GET /api/widget/token`, `POST /api/widget/token/regenerate`

**Authentication** (`backend/services/auth_service.py`, `backend/routers/auth.py`)
- Single-user password auth with JWT tokens and HttpOnly cookie sessions
- bcrypt password hashing, 30-day JWT expiration, HS256 algorithm
- Password stored in `settings.password_hash` (one-time setup, then changeable)
- Middleware validates `edward_token` cookie on protected endpoints
- REST API: `GET /api/auth/status`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/logout`, `POST /api/auth/change-password`
- `JWT_SECRET_KEY` env var (defaults to dev key -- change in production)

**Contacts** (`backend/services/contacts_service.py`)
- macOS Contacts.app lookup via AppleScript (name search + phone reverse lookup)
- Phone normalization: strips non-digits, keeps last 10 digits
- LLM tools (always available on macOS): `lookup_contact`, `lookup_phone`
- No database tables (reads directly from macOS Contacts.app)

**Search Tags** (`backend/services/search_tag_service.py`)
- Auto-generated search keywords per conversation using Claude Haiku 4.5
- Generates 5-10 keywords (topics, entities, intents) from last 5 messages
- Stored in `conversations.search_tags` for full-text search
- Runs post-turn automatically (fire-and-forget, no LLM tools exposed)

**Orchestrator** (`backend/services/orchestrator_service.py`, `backend/services/orchestrator_models.py`, `backend/routers/orchestrator.py`)
- Spawns lightweight worker agents (mini-Edwards) that run within Edward's process via `chat_with_memory()`
- Workers have full tool access, memory retrieval, and state persistence
- Worker conversations appear in sidebar with distinct source (`source: "orchestrator_worker"`)
- DB tables: `orchestrator_tasks`, `orchestrator_config`
- REST API: `GET/POST /api/orchestrator/tasks`, `GET/PATCH /api/orchestrator/tasks/{id}`, `POST /api/orchestrator/tasks/{id}/cancel`, `GET /api/orchestrator/status`, `GET/PATCH /api/orchestrator/config`
- Frontend: OrchestratorPanel in settings page (task browser, spawning, cancellation, streaming events)

**Evolution Service** (`backend/services/evolution_service.py`, `backend/services/evolution_models.py`, `backend/routers/evolution.py`)
- Self-evolution engine: manages pipeline branch -> code -> validate -> test -> review -> merge using Claude Code
- Merging to main triggers `uvicorn --reload` automatically
- DB tables: `evolution_config`, `evolution_history`
- REST API: `GET /api/evolution/status`, `GET /api/evolution/history`, `PATCH /api/evolution/config`, `POST /api/evolution/trigger`, `POST /api/evolution/rollback`
- Frontend: EvolutionPanel in settings page (evolution cycle history, config, trigger/rollback controls)

**Claude Code Service** (`backend/services/claude_code_service.py`, `backend/services/cc_manager_service.py`)
- Delegates coding tasks to Claude Code via `claude-agent-sdk`
- CC Manager bridges Claude Code with orchestrator task system
- Separate concurrency semaphore from internal workers (prevents starvation)
- DB table: `claude_code_sessions` (tracks session execution, events streamed to frontend)
- Frontend: CCSessionBlock component renders inline CC session events in chat UI

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/chat` | Streaming chat (SSE) |
| `POST /api/chat/simple` | Non-streaming chat |
| `GET/POST /api/settings` | Settings CRUD |
| `GET /api/conversations` | List conversations |
| `GET/PATCH/DELETE /api/conversations/{id}` | Conversation CRUD |
| `GET /api/memories` | List/search memories (with filters) |
| `GET/PATCH/DELETE /api/memories/{id}` | Memory CRUD |
| `GET/POST /api/documents` | List/search/create documents |
| `GET/PATCH/DELETE /api/documents/{id}` | Document CRUD |
| `GET /api/skills` | List all skills with status |
| `PATCH /api/skills/{id}` | Enable/disable a skill |
| `POST /api/skills/reload` | Reinitialize all skills |
| `POST /api/webhook/twilio` | Twilio SMS inbound webhook |
| `POST /api/webhook/twilio/whatsapp` | Twilio WhatsApp inbound webhook |
| `GET/POST /api/events` | List/create scheduled events |
| `GET/PATCH/DELETE /api/events/{id}` | Event CRUD |
| `GET /api/debug/graph` | Graph structure for visualization |
| `GET /api/debug/health` | Component health check |
| `GET /api/debug/langsmith/status` | Check if LangSmith is configured |
| `GET /api/debug/traces/{conversation_id}` | List trace summaries for a conversation |
| `GET /api/debug/traces/{conversation_id}/latest` | Latest trace with all runs |
| `GET /api/debug/trace/{trace_id}` | All runs in a specific trace |
| `GET /api/custom-mcp` | List custom MCP servers |
| `PATCH /api/custom-mcp/{id}` | Enable/disable custom MCP server |
| `DELETE /api/custom-mcp/{id}` | Remove custom MCP server |
| `GET /api/auth/status` | Check auth configured + authenticated |
| `POST /api/auth/setup` | Set initial password |
| `POST /api/auth/login` | Authenticate with password |
| `POST /api/auth/logout` | Clear session cookie |
| `POST /api/auth/change-password` | Change password |
| `GET /api/push/vapid-key` | Get VAPID public key for subscription |
| `POST /api/push/subscribe` | Save push subscription |
| `POST /api/push/unsubscribe` | Remove push subscription |
| `GET /api/push/status` | Push status + subscription count |
| `POST /api/push/test` | Send test notification |
| `GET/POST /api/files` | List/upload files |
| `GET/PATCH/DELETE /api/files/{id}` | File metadata CRUD |
| `GET /api/files/{id}/download` | Download file |
| `GET /api/databases` | List persistent databases |
| `GET/DELETE /api/databases/{name}` | Database info/delete |
| `GET /api/databases/{name}/tables` | List tables in database |
| `GET /api/widget` | Fetch widget JSON (token-authenticated) |
| `POST /api/widget/chat` | Send message from widget |
| `GET /api/widget/token` | Get widget token |
| `POST /api/widget/token/regenerate` | Regenerate widget token |
| `GET /api/heartbeat/status` | Heartbeat service status |
| `GET /api/heartbeat/events` | List heartbeat events |
| `GET /api/heartbeat/triage` | List triage results |
| `PATCH /api/heartbeat/config` | Update heartbeat config |
| `GET/PATCH /api/consolidation/config` | Consolidation config |
| `GET /api/consolidation/status` | Consolidation status |
| `GET/POST /api/orchestrator/tasks` | List/create orchestrator tasks |
| `GET/PATCH /api/orchestrator/tasks/{id}` | Task detail/update |
| `POST /api/orchestrator/tasks/{id}/cancel` | Cancel running task |
| `GET /api/orchestrator/status` | Orchestrator status |
| `GET/PATCH /api/orchestrator/config` | Orchestrator config |
| `GET /api/evolution/status` | Evolution service status |
| `GET /api/evolution/history` | Evolution cycle history |
| `PATCH /api/evolution/config` | Evolution config |
| `POST /api/evolution/trigger` | Trigger evolution cycle |
| `POST /api/evolution/rollback` | Rollback evolution change |

## Environment Variables

Required in `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

Optional:
```
# LangSmith observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...
LANGCHAIN_PROJECT=edward

# Twilio SMS & WhatsApp
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
TWILIO_WEBHOOK_URL=https://your-domain.com/api/webhook/twilio
TWILIO_WHATSAPP_WEBHOOK_URL=https://your-domain.com/api/webhook/twilio/whatsapp

# WhatsApp via MCP (requires whatsapp-mcp Go bridge running)
MCP_WHATSAPP_ENABLED=false
MCP_WHATSAPP_SERVER_DIR=/path/to/whatsapp-mcp/whatsapp-mcp-server

# Web search via Brave Search API
BRAVE_SEARCH_API_KEY=BSA...

# Apple Services MCP (macOS only - Calendar, Reminders, Notes, Mail, Contacts, Maps)
MCP_APPLE_ENABLED=true

# HTML Hosting (html.zyroi.com)
HTML_HOSTING_API_KEY=...
HTML_HOSTING_URL=https://html.zyroi.com  # Optional: defaults to this

# Custom MCP server search (GitHub API)
GITHUB_TOKEN=ghp_...

# Authentication (single-user password)
JWT_SECRET_KEY=your-secret-key  # Default: dev key -- change in production

# Push Notifications (Web Push via VAPID)
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...

# File Storage
FILE_STORAGE_ROOT=./storage  # Optional: defaults to ./storage

# Claude Code (for evolution service + orchestrator CC tasks)
# Requires `claude-agent-sdk` in requirements.txt
# Claude Code CLI must be installed and authenticated on the host
```

Database defaults to `edward`/`edward`/`edward` (user/password/database).

## Key Integration Points

- Chat streaming uses `ChatAnthropic.astream()` with tool call loop (up to 5 iterations)
- Frontend parses SSE via `lib/api.ts:streamChatEvents()` async generator (structured events)
- Memories are retrieved before responding and extracted after
- LLM tools are bound dynamically via `ToolRegistry.get_available_tools()`
- Tool availability depends on skill enabled state in database
- Memory tools (`remember_update`, `remember_forget`, `remember_search`) are always available
- Document tools (`save_document`, `read_document`, etc.) are always available
- Scheduled event tools (`schedule_event`, `list_scheduled_events`, `cancel_scheduled_event`) are always available
- File storage tools (`save_to_storage`, `list_storage_files`, etc.) are always available
- Persistent database tools (`create_persistent_db`, `query_persistent_db`, etc.) are always available
- Widget tools (`update_widget`, `get_widget_state_tool`, etc.) are always available
- Push notification tool (`send_push_notification`) is always available
- Contact tools (`lookup_contact`, `lookup_phone`) are always available on macOS
- Scheduler runs in-process, polls every 30s, executes events via `chat_with_memory()`
- Messaging tools filtered by skill state: twilio_sms, twilio_whatsapp, imessage_applescript, whatsapp_mcp
- Search tools filtered by skill state: brave_search
- Code execution tools filtered by skill state: code_interpreter, javascript_interpreter, sql_interpreter, shell_interpreter
- Apple Services tools filtered by skill state: apple_services
- HTML hosting tools filtered by skill state: html_hosting
- Twilio inbound webhooks process SMS and WhatsApp asynchronously to avoid timeouts, responds via API not TwiML
- WhatsApp and SMS from same phone number share the same external contact and conversation; `last_channel` tracks reply channel
- MCP client manages multiple subprocess servers: WhatsApp, Apple Services (includes Messages)
- Conversation state persisted to PostgreSQL via langgraph-checkpoint-postgres
- Edward's name is fixed (read-only in settings UI)
- **Reflection**: Fire-and-forget post-turn -- generates Haiku queries to find related memories, stores enrichments for next turn
- **Deep Retrieval**: Pre-turn gate -- when message is short or turn_count >= 3, runs 4 parallel memory queries for richer context
- **Consolidation**: Hourly background loop -- clusters memories via Haiku, creates connections and quality flags
- **Heartbeat**: Multi-track listeners (iMessage, Calendar, Email) poll for events, triage classifies urgency, briefing injected into system prompt
- **Search Tags**: Post-turn Haiku generates 5-10 keywords per conversation for full-text search
- **Authentication**: Cookie-based JWT middleware protects all endpoints except `/api/auth/*` and `/api/widget` (token-authenticated)
- **Orchestrator**: Spawns worker agents via `chat_with_memory()`, workers have full tool access, conversations tracked with `source: "orchestrator_worker"`
- **Evolution**: Self-coding pipeline using Claude Code (`claude-agent-sdk`), auto-deploys via `uvicorn --reload` on merge
- **Claude Code**: Separate concurrency from orchestrator workers; sessions tracked in DB and streamed to frontend

## SSE Event Protocol

The chat endpoint emits structured events for real-time UI updates:

| Event Type | Fields | Description |
|------------|--------|-------------|
| `thinking` | `content` | LLM is processing (between tool calls) |
| `tool_start` | `tool_name` | Tool execution beginning |
| `code` | `code`, `language` | Code/query/command to execute (python/javascript/sql/bash) |
| `execution_output` | `output`, `stream` | stdout/stderr from code execution |
| `execution_result` | `success`, `duration_ms` | Code execution completed |
| `tool_end` | `tool_name`, `result` | Tool execution finished |
| `content` | `content` | Text content from LLM response |
| `done` | - | Stream complete |
