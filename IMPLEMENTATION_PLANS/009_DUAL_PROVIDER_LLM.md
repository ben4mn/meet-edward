# Plan 009: Direct-SDK Dual-Provider Architecture

## STOP: Read This Entire Document Before Making Any Changes

This plan removes ALL LangChain and LangGraph dependencies, replacing them with direct `anthropic` + `openai` SDK calls. It creates a clean, zero-abstraction foundation for dual-provider LLM support with Codex OAuth for ChatGPT subscription-based billing.

**Dependencies**: All prior plans (001-008) completed
**Estimated effort**: ~34 hours (Phases 1-8)
**New dependency**: `openai>=1.60.0`
**Removed**: `langgraph`, `langgraph-checkpoint-postgres`, `langchain-core`, `langchain-anthropic`, `langchain-mcp-adapters`

---

## Context & Rationale

### Why Direct SDK?
1. **LangGraph is dead code** — graph nodes are never invoked; only checkpointing is used (replaceable with simple PostgreSQL table)
2. **LangChain adds abstraction tax** — `ChatAnthropic` wraps `anthropic.messages.create()` with no added value for our use case
3. **Codex OAuth needs Responses API** — `ChatOpenAI` targets Chat Completions; Codex OAuth uses Responses API (different format)
4. **Dependency risk** — LangChain's release velocity causes breaking changes; direct SDKs are stable
5. **Net -4 dependencies** — simpler, lighter, fewer upgrade risks

### Why Dual-Provider?
1. **Codex OAuth** — OpenAI allows subscription-based access via OAuth (ChatGPT Plus/Pro covers API usage, no per-token billing)
2. **GPT-5.4** — 1.05M context, strong reasoning and tool use, officially available in Codex
3. **Resilience** — if one provider has outage, switch to the other
4. **Codex OAuth (Phase 8)** — use ChatGPT subscription credits for GPT-5.4 access without per-token billing

### Anthropic Claude OAuth Status
Anthropic has explicitly banned Claude subscription OAuth in third-party apps (February 2026). Claude stays API-key-only.

---

## Target Architecture

```
TIER 1 — Main Chat (user-facing, configurable provider)
├── Claude Sonnet 4.6  →  anthropic.AsyncAnthropic.messages.create()
├── Claude Opus 4.6    →  anthropic.AsyncAnthropic.messages.create()
└── GPT-5.4 / 5.3      →  chatgpt.com/backend-api/codex/responses (OAuth)
                           OR openai.AsyncOpenAI.responses.create() (API key fallback)
    Auth priority: Codex OAuth (subscription) > OPENAI_API_KEY (pay-per-token)

TIER 2 — Background Intelligence (always Anthropic, always Haiku)
├── Memory extraction   →  anthropic.AsyncAnthropic.messages.create()
├── Triage              →  anthropic.AsyncAnthropic.messages.create()
├── Search tags         →  anthropic.AsyncAnthropic.messages.create()
├── Reflection          →  anthropic.AsyncAnthropic.messages.create()
├── Deep retrieval      →  anthropic.AsyncAnthropic.messages.create()
├── Consolidation       →  anthropic.AsyncAnthropic.messages.create()
├── Greeting            →  anthropic.AsyncAnthropic.messages.create()
└── Tool routing        →  anthropic.AsyncAnthropic.messages.create()

TIER 3 — Evolution & CC Workers (always Anthropic, Claude Code CLI)
└── Unchanged — separate process, not in scope
```

## What Stays Unchanged
- **Tier 3** (evolution, CC workers): Claude Code CLI — completely separate
- **All unique features**: Memory system (pgvector + BM25), NotebookLM, WhatsApp Bridge, scheduled events, heartbeat, autonomy framework
- **Tool execution**: `tool.ainvoke(args)` — just calling Python functions
- **SSE streaming protocol**: Same event types, same frontend parsing
- **Database schema**: All 26 existing tables preserved (+ 2 new: `conversation_messages`, `codex_oauth_tokens`)
- **Frontend**: Minimal changes (model dropdown + provider grouping + OpenAI sign-in button)

## Cross-Tier Compatibility

Tier 1 provider choice (Claude vs GPT-5.4) has **zero impact** on Tier 2 and Tier 3 because:

1. **Data boundary is plain text** — After Tier 1 responds, messages are converted to simple dicts `[{"role": "human/assistant", "content": "..."}]` before being passed to Tier 2 services. No provider-specific response objects leak across the boundary.

2. **Tier 2 is always Haiku** — Memory extraction, search tags, reflection, deep retrieval, consolidation, triage, and greeting all hardcode `claude-haiku-4-5-20251001`. They don't know or care what Tier 1 model was used.

3. **Tier 3 is a separate process** — Evolution and Claude Code workers run via CLI, not through the chat pipeline.

4. **Tools are provider-agnostic** — `tool.ainvoke(args)` is just calling Python functions. Tool schemas are converted to provider-specific format at call time (Anthropic format or OpenAI function calling format), but execution is identical.

5. **`chat_with_memory()` adapts** — Used by scheduler and orchestrator, it reads the model from settings and routes through `_call_llm()`. If the user has GPT-5.4 selected, scheduled events execute with GPT-5.4. If Claude is selected, they use Claude. Post-turn Tier 2 flows (memory extraction, reflection) always use Haiku regardless.

---

## Strict Rules

### MUST DO
- [ ] Use `anthropic.AsyncAnthropic` for all Anthropic calls (both Tier 1 and Tier 2)
- [ ] Use `openai.AsyncOpenAI` for all OpenAI calls (Tier 1 only)
- [ ] Provider detection via model ID prefix (`gpt-`, `o1-`, `o3-`, `o4-` = OpenAI)
- [ ] Lazy-import `openai` so app starts without it installed
- [ ] Skip Anthropic-specific `cache_control` kwargs for OpenAI models
- [ ] Keep all 88 tool definitions working with both providers
- [ ] Run both checkpoint stores in parallel during migration
- [ ] Self-routing via Haiku (Tier 2) — always Anthropic, independent of Tier 1 provider

### MUST NOT DO
- [ ] Do NOT use any LangChain imports (`langchain_core`, `langchain_anthropic`, etc.)
- [ ] Do NOT modify tool definitions in `tools.py` beyond import changes
- [ ] Do NOT change any database table schemas (only add new `conversation_messages` and `codex_oauth_tokens` tables)
- [ ] Do NOT store provider as a separate setting — derive from model ID prefix
- [ ] Do NOT use LangChain message types (`HumanMessage`, `AIMessage`, etc.) — use simple dicts
- [ ] Do NOT remove LangGraph checkpoint tables from database (keep for migration fallback)

---

## Dependencies

### Remove (5 packages)
| Package | Reason |
|---------|--------|
| `langgraph>=0.2.60` | Replaced by custom checkpoint store |
| `langgraph-checkpoint-postgres>=2.0.11` | Replaced by custom checkpoint store |
| `langchain-core>=0.3.36` | Replaced by direct SDK calls + simple dicts |
| `langchain-anthropic>=0.3.5` | Replaced by `anthropic` SDK (already a dependency) |
| `langchain-mcp-adapters>=0.1.14` | MCP tools already wrapped manually |

### Add (1 package)
| Package | Reason |
|---------|--------|
| `openai>=1.60.0` | Direct OpenAI SDK for Responses API |

### Keep (already present)
| Package | Reason |
|---------|--------|
| `anthropic>=0.40.0` | Direct Anthropic SDK — becomes the primary LLM interface |

**Net result: -4 dependencies**

---

## Phase 1: Custom Checkpoint Store (replace LangGraph)

### Files
- **New**: `backend/services/checkpoint_store.py`
- **Edit**: `backend/services/graph/__init__.py` (remove LangGraph init)
- **Edit**: `backend/services/database.py` (add table model)

### What to Build
Simple PostgreSQL JSONB table replacing LangGraph's `AsyncPostgresSaver`:

```sql
CREATE TABLE IF NOT EXISTS conversation_messages (
    conversation_id TEXT PRIMARY KEY,
    messages JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

API (2 functions):
```python
async def get_messages(conversation_id: str) -> list[dict]
async def save_messages(conversation_id: str, messages: list[dict])
```

Uses the existing `database.py` async engine — no new connection pool needed.

### Migration Strategy
- New table created alongside existing LangGraph `checkpoints` table
- New conversations use new store
- `routers/conversations.py` loads from new store; falls back to LangGraph table for old conversations
- LangGraph tables left in place (can be cleaned up later)

### Currently Used At
- `streaming.py:~671` → `graph.aget_state(config)` — load messages
- `streaming.py:~996` → `graph.aupdate_state(config, {...})` — save messages
- `streaming.py:~1200+` → same pattern in `chat_with_memory()`
- `routers/conversations.py:~139` → `graph.aget_state()` — fetch for API response
- `graph/__init__.py:~20-50` → LangGraph initialization + connection pool

---

## Phase 2: Direct Anthropic SDK for Tier 1 (Main Chat)

### Files
- **Edit**: `backend/services/graph/streaming.py` (major — ~300 lines affected)

### What Changes
Replace `ChatAnthropic.ainvoke()` + `bind_tools()` with direct `anthropic.AsyncAnthropic.messages.create()`.

**Before** (LangChain):
```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

llm = ChatAnthropic(model=model, temperature=temp, max_tokens=16384)
llm_with_tools = llm.bind_tools(tools)
response = await llm_with_tools.ainvoke(full_messages)
for tc in response.tool_calls:
    result = await tool.ainvoke(tc["args"])
    messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
```

**After** (direct SDK):
```python
import anthropic

client = anthropic.AsyncAnthropic()
response = await client.messages.create(
    model=model,
    system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
    messages=messages,
    tools=tool_schemas,
    temperature=temp,
    max_tokens=16384,
)
for block in response.content:
    if block.type == "tool_use":
        result = await execute_tool(block.name, block.input)
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
        ]})
```

### Key Changes in streaming.py
1. Remove `from langchain_anthropic import ChatAnthropic`
2. Remove `from langchain_core.messages import ...`
3. Replace `_build_llm()` with `_call_anthropic()` / `_call_openai()` / `_call_llm()` dispatcher
4. Replace LangChain message construction with simple dicts
5. Replace `response.tool_calls` parsing with `response.content` block parsing
6. Replace `ToolMessage(...)` with Anthropic-native tool_result format
7. Update both `stream_with_memory_events()` and `chat_with_memory()`
8. Prompt caching via `cache_control` on system blocks (already Anthropic-native)

### Internal Message Format
Used in checkpoint store and throughout streaming.py:
```python
{"role": "user", "content": "Hello"}
{"role": "assistant", "content": "Hi!", "tool_calls": [{"id": ..., "name": ..., "args": {...}}]}
{"role": "tool", "tool_call_id": "...", "content": "result text"}
```

Conversion to/from provider-specific formats happens at call time.

---

## Phase 3: Direct Anthropic SDK for Tier 2 (Background Haiku Calls)

### Files (11 call sites across 7 files)
- `backend/services/memory_service.py` (5 calls)
- `backend/services/heartbeat/triage_service.py` (1 call)
- `backend/services/search_tag_service.py` (1 call)
- `backend/services/reflection_service.py` (1 call)
- `backend/services/deep_retrieval_service.py` (1 call)
- `backend/services/consolidation_service.py` (1 call)
- `backend/routers/chat.py` (1 call — greeting)

### What Changes
Mechanical replacement — each call follows the same pattern:

**Before**:
```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0, max_tokens=N)
response = await llm.ainvoke([SystemMessage(content=sys), HumanMessage(content=msg)])
text = response.content
```

**After**:
```python
from services.llm_client import haiku_call
text = await haiku_call(system=sys, message=msg, max_tokens=N)
```

### Shared Helper
```python
# backend/services/llm_client.py
import anthropic

_client = None

async def haiku_call(system: str, message: str, max_tokens: int = 256) -> str:
    """Convenience wrapper for Haiku background calls."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        system=system,
        messages=[{"role": "user", "content": message}],
        temperature=0,
        max_tokens=max_tokens,
    )
    return response.content[0].text
```

---

## Phase 4: Tool Schema Conversion

### Files
- **New**: `backend/services/graph/tool_schema.py`
- **Edit**: `backend/services/graph/tools.py` (import change only)
- **Edit**: `backend/services/tool_registry.py` (update tool type handling)

### What Changes
Extract tool schemas from existing `BaseTool` objects (from `@tool` decorator):

```python
# tool_schema.py
def tool_to_anthropic_schema(tool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.args_schema.schema() if tool.args_schema else {"type": "object", "properties": {}}
    }

def tool_to_openai_schema(tool) -> dict:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.args_schema.schema() if tool.args_schema else {"type": "object", "properties": {}}
    }
```

Tool execution stays the same: `await tool.ainvoke(args)` — just calling the Python function.

Note: `@tool` decorator still works for schema extraction. Full replacement with custom decorator is optional future work. The critical thing is that `tools.py` (4,004 LOC, 88 tool definitions) doesn't need massive changes.

---

## Phase 5: Self-Routing Tool Selection

### Files
- **Edit**: `backend/services/tool_registry.py` (add categories + routing helpers)
- **Edit**: `backend/services/graph/streaming.py` (add routing call)

### Design
Haiku classifies each message → returns tool categories + complexity level → only relevant tools bound. Cost: ~$0.0002/call, ~200ms latency. Saves 50-70% on token costs.

**TOOL_CATEGORIES** (20 categories):
```python
TOOL_CATEGORIES = {
    "memory": {"description": "Update, forget, or search long-term memories", "always_on": True},
    "documents": {"description": "Save, read, edit, search persistent documents", "always_on": True},
    "file_storage": {"description": "Persist sandbox files, list/download/tag stored files", "always_on": True},
    "planning": {"description": "Create and manage multi-step plans", "always_on": True},
    "custom_mcp": {"description": "Discover, install, manage MCP servers", "always_on": True},
    "scheduled_events": {"description": "Schedule reminders, messages, recurring tasks", "always_on": False},
    "messaging": {"description": "Send SMS, WhatsApp, iMessage; read messages", "always_on": False},
    "whatsapp_bridge": {"description": "Read/send WhatsApp via Baileys bridge", "always_on": False},
    "web_search": {"description": "Search web and fetch page content", "always_on": False},
    "code_execution": {"description": "Execute Python, JavaScript, SQL, shell", "always_on": False},
    "notebooklm": {"description": "Manage knowledge bases with source-grounded Q&A", "always_on": False},
    "orchestrator": {"description": "Spawn parallel worker agents", "always_on": False},
    "evolution": {"description": "Self-evolve codebase via Claude Code", "always_on": False},
    "apple_services": {"description": "Calendar, Reminders, Notes, Mail, Contacts, Maps", "always_on": False},
    "html_hosting": {"description": "Create/update hosted HTML pages", "always_on": False},
    "widget": {"description": "Update iOS home screen widget", "always_on": False},
    "contacts": {"description": "Search contacts by name or phone", "always_on": False},
    "persistent_db": {"description": "Create and query persistent PostgreSQL databases", "always_on": False},
    "push_notifications": {"description": "Send push notification to user's devices", "always_on": False},
    "heartbeat": {"description": "Review incoming messages from background monitoring", "always_on": False},
}
```

**Routing call** (Tier 2 — Haiku via `haiku_call()`):
```python
async def _route_tools(message: str, recent_messages: list) -> tuple[set, str|None]:
    """Returns (categories, effort_level). Falls back to ({"all"}, None) on error."""
```

**Integration**:
```python
categories, effort = await _route_tools(message, messages)
tools = await get_tools_by_categories(categories)
schemas = tools_to_anthropic_schema(tools)  # or tools_to_openai_schema
response = await _call_llm(model, messages, system, schemas, effort=effort)
```

---

## Phase 6: OpenAI Responses API (Tier 1 second provider)

### Files
- **Edit**: `backend/services/graph/streaming.py` (add `_call_openai()`)

### Design
```python
from openai import AsyncOpenAI

async def _call_openai(model, messages, system, tools, temperature, max_tokens):
    client = _get_openai_client()
    input_items = _to_openai_input(system, messages)
    tool_schemas = [tool_to_openai_schema(t) for t in tools]
    response = await client.responses.create(
        model=model, input=input_items, tools=tool_schemas,
        temperature=temperature, max_output_tokens=max_tokens,
    )
    return _from_openai_response(response)

def _is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1-", "o3-", "o4-"))

async def _call_llm(model, messages, system, tools, temperature, max_tokens, effort=None):
    if _is_openai_model(model):
        return await _call_openai(model, messages, system, tools, temperature, max_tokens)
    return await _call_anthropic(model, messages, system, tools, temperature, max_tokens, effort)
```

**Provider-specific concerns**:
- Anthropic: `cache_control` on system messages, `effort` parameter for 4.6 models
- OpenAI: No cache_control (automatic prefix caching), no effort parameter
- Image format conversion at call time

---

## Phase 7: Frontend Model Selection

### Files
- **Edit**: `backend/routers/settings.py` (add model list endpoint)
- **New**: `frontend/components/settings/GeneralPanel.tsx`
- **Edit**: `frontend/app/(app)/settings/page.tsx` (add General tile)
- **Edit**: `frontend/lib/api.ts` (add provider to Model type)

### Design
- `/api/settings/models` returns models grouped by provider
- GeneralPanel: model dropdown (grouped by provider), temperature slider, system prompt textarea
- Show OpenAI models only when Codex OAuth tokens exist OR `OPENAI_API_KEY` is set

### Hardcoded Model List (curated, easy to extend)
```python
AVAILABLE_MODELS = [
    # Anthropic (always available — ANTHROPIC_API_KEY required for app to start)
    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "provider": "anthropic", "recommended": True},
    {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "provider": "anthropic"},
    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "provider": "anthropic"},
    {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5 (Legacy)", "provider": "anthropic"},
    # OpenAI (shown only when OAuth or API key configured)
    {"id": "gpt-5.4", "name": "GPT-5.4", "provider": "openai", "recommended": True},
    {"id": "gpt-5.3-codex", "name": "GPT-5.3 Codex", "provider": "openai"},
]
```
Frontend groups by provider. Adding a new model = one-line addition (no dynamic API fetch).

---

## Phase 8: Codex OAuth (GPT-5.4 on ChatGPT Subscription Credits)

Use OpenAI's Codex OAuth to authenticate with ChatGPT Plus/Pro subscription, giving Edward access to GPT-5.4 without per-token API billing. Implementation inspired by Cline (merged PR #8664) and opencode-openai-codex-auth — both battle-tested in production.

### Approach: Direct ChatGPT Backend (Approach A)

Two approaches exist in the wild:
- **A: Direct ChatGPT Backend** — `chatgpt.com/backend-api/codex/responses` with OAuth access_token (used by Cline, opencode-auth)
- **B: Token Exchange → API Key** — exchange id_token for sk-... API key, call standard `api.openai.com/v1/responses` (used by official Codex CLI)

**We use Approach A** — simpler (no token exchange step), directly uses subscription credits, and proven by multiple third-party tools.

### Files
- **New**: `backend/services/codex_oauth_service.py` — OAuth PKCE flow, token storage, refresh, JWT decode
- **Edit**: `backend/services/graph/streaming.py` — add `_call_codex()` path in `_call_llm()` dispatcher
- **Edit**: `backend/services/database.py` — add `codex_oauth_tokens` table model
- **Edit**: `backend/routers/settings.py` — add `/api/settings/openai/login` and `/api/settings/openai/logout` endpoints
- **Edit**: `frontend/components/settings/GeneralPanel.tsx` — "Sign in with OpenAI" button + connection status

### OAuth Constants

| Parameter | Value |
|-----------|-------|
| Client ID | `app_EMoamEEZ73f0CkXaXp7hrann` |
| Auth URL | `https://auth.openai.com/oauth/authorize` |
| Token URL | `https://auth.openai.com/oauth/token` |
| Redirect URI | `http://localhost:1455/auth/callback` |
| Scopes | `openid profile email offline_access` |
| PKCE | S256 (SHA-256 challenge) |
| Callback Port | 1455 (fixed, matches all other implementations) |

### OAuth Flow (codex_oauth_service.py)

1. Generate PKCE verifier (43-char random) + S256 challenge
2. Start local `aiohttp` server on port 1455 for callback
3. Open browser to `auth.openai.com/oauth/authorize?client_id=...&code_challenge=...&scope=...&state=...&code_challenge_method=S256&response_type=code`
4. User authenticates → callback hits `localhost:1455/auth/callback?code=...&state=...`
5. Exchange authorization code for tokens at `auth.openai.com/oauth/token` (POST, form-urlencoded, **NO `state` in body**)
6. Decode JWT `access_token` to extract `chatgpt_account_id` from `https://api.openai.com/auth` claim
7. Store `{access_token, refresh_token, expires_at, account_id}` in DB
8. Auto-refresh when within 5 minutes of expiry; on 401, force-refresh once then require re-auth

### Token Storage

```sql
CREATE TABLE codex_oauth_tokens (
    id TEXT PRIMARY KEY DEFAULT 'default',
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    account_id TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Token Refresh

```
POST https://auth.openai.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
client_id=app_EMoamEEZ73f0CkXaXp7hrann
refresh_token=<refresh_token>
```

Refresh failure modes: `refresh_token_expired`, `refresh_token_reused`, `refresh_token_invalidated` → clear stored tokens, require re-auth.

### API Call Pattern

**Endpoint**: `https://chatgpt.com/backend-api/codex/responses`

**Headers**:
```
Authorization: Bearer <access_token>
ChatGPT-Account-Id: <account_id>
originator: edward
OpenAI-Beta: responses=experimental
Accept: text/event-stream
Content-Type: application/json
```

**Request body** (Responses API format with Codex-specific requirements):
```python
{
    "model": "gpt-5.4",
    "input": [...],           # Responses API input format
    "tools": [...],           # Native function calling (NOT XML)
    "instructions": "...",    # System prompt (replaces "system" field)
    "stream": True,
    "store": False,           # REQUIRED for ChatGPT backend
    "include": ["reasoning.encrypted_content"],  # REQUIRED for stateless multi-turn
    "reasoning": {"effort": "medium", "summary": "auto"},
    # NO max_output_tokens (unsupported by ChatGPT backend)
}
```

**Response**: SSE stream with events:
- `response.output_text.delta` → text content
- `response.function_call_arguments.delta` → tool call args
- `response.output_item.added` / `response.output_item.done` → output items
- `response.done` / `response.completed` → final usage data

### Provider Routing (updated `_call_llm` dispatcher)

```python
async def _call_llm(model, messages, system, tools, ...):
    if _is_openai_model(model):
        if await _has_codex_oauth():              # Priority 1: subscription credits
            return await _call_codex(model, messages, system, tools, ...)
        elif os.getenv("OPENAI_API_KEY"):         # Priority 2: pay-per-token
            return await _call_openai(model, messages, system, tools, ...)
        else:
            raise ValueError("No OpenAI auth configured")
    return await _call_anthropic(model, messages, system, tools, ...)
```

### Frontend (GeneralPanel.tsx additions)

- If no OAuth tokens: **"Sign in with OpenAI"** button → calls `POST /api/settings/openai/login` → opens browser for OAuth
- If connected: **"Connected as [email]"** + **"Disconnect"** button → calls `POST /api/settings/openai/logout`
- OpenAI models visible in dropdown **only when OAuth or API key is configured**

### Critical Gotchas (from Cline + opencode-auth source analysis)

1. **`store: false`** — REQUIRED for ChatGPT backend, requests fail without it
2. **`include: ["reasoning.encrypted_content"]`** — REQUIRED for stateless operation across turns
3. **`ChatGPT-Account-Id` header** — decoded from JWT claim at `https://api.openai.com/auth` → `chatgpt_account_id`
4. **Always SSE** — ChatGPT backend always streams, even if client sends `stream: false`
5. **No `max_output_tokens`** — unsupported by ChatGPT backend, must be removed from body
6. **Strip `id` fields** from input items when `store: false` (stateless mode)
7. **Do NOT send `state` in token exchange POST body** — OpenAI rejects it (state is only validated in callback)
8. **Native function calling only** — XML-formatted tool definitions in system prompt cause duplicate tool invocations
9. **Port 1455 is fixed** — all implementations use this; check availability, show clear error if occupied
10. **404 with `usage_limit_reached`** → map to 429 for retry logic
11. **Codex models require reasoning** — `none` effort not supported; default to `medium`

### References
- [Cline OpenAI Codex OAuth (merged PR)](https://github.com/cline/cline/discussions/8667)
- [opencode-openai-codex-auth](https://github.com/numman-ali/opencode-openai-codex-auth)
- [OpenAI Codex Auth Docs](https://developers.openai.com/codex/auth/)
- [GPT-5.4 via OAuth endpoint issue](https://github.com/openclaw/openclaw/issues/38706)
- [GPT-5.4 announcement](https://openai.com/index/introducing-gpt-5-4/)

---

## Files Changed Summary

| File | Action | Phase |
|------|--------|-------|
| `backend/services/checkpoint_store.py` | CREATE | 1 |
| `backend/services/database.py` | EDIT (add tables) | 1, 8 |
| `backend/services/graph/__init__.py` | EDIT (remove LangGraph) | 1 |
| `backend/services/graph/streaming.py` | MAJOR EDIT (~300 lines) | 2, 5, 6, 8 |
| `backend/services/llm_client.py` | CREATE (shared Haiku helper) | 3 |
| `backend/services/memory_service.py` | EDIT (5 call sites) | 3 |
| `backend/services/heartbeat/triage_service.py` | EDIT (1 call site) | 3 |
| `backend/services/search_tag_service.py` | EDIT (1 call site) | 3 |
| `backend/services/reflection_service.py` | EDIT (1 call site) | 3 |
| `backend/services/deep_retrieval_service.py` | EDIT (1 call site) | 3 |
| `backend/services/consolidation_service.py` | EDIT (1 call site) | 3 |
| `backend/routers/chat.py` | EDIT (1 call site + imports) | 3 |
| `backend/routers/conversations.py` | EDIT (checkpoint load) | 1 |
| `backend/services/graph/tool_schema.py` | CREATE | 4 |
| `backend/services/graph/tools.py` | EDIT (import change) | 4 |
| `backend/services/tool_registry.py` | EDIT (categories, routing) | 5 |
| `backend/routers/settings.py` | EDIT (model list + OAuth endpoints) | 7, 8 |
| `backend/requirements.txt` | EDIT (add/remove deps) | 1 |
| `frontend/components/settings/GeneralPanel.tsx` | CREATE | 7, 8 |
| `frontend/app/(app)/settings/page.tsx` | EDIT (add tile) | 7 |
| `frontend/lib/api.ts` | EDIT (provider type) | 7 |
| `backend/services/codex_oauth_service.py` | CREATE (OAuth PKCE + token mgmt) | 8 |

---

## What Does NOT Change

| File | Why Unchanged |
|------|--------------|
| `backend/services/graph/tools.py` | Tool definitions — provider-agnostic (only import line changes) |
| `backend/services/memory_service.py` | Memory system — only LLM call wrapper changes, search/embedding unchanged |
| `backend/services/notebooklm_service.py` | NotebookLM — independent service, no LLM dependency |
| `backend/services/whatsapp_bridge_client.py` | WhatsApp — HTTP client, no LLM dependency |
| `backend/services/scheduled_events_service.py` | Events — DB operations, execution via `chat_with_memory()` which adapts |
| `backend/services/heartbeat/` | Heartbeat — listeners unchanged, only triage LLM call changes |
| `backend/services/evolution_service.py` | Evolution — Claude Code CLI, separate process |
| `backend/services/orchestrator_service.py` | Orchestrator — calls `chat_with_memory()` which adapts |
| `backend/main.py` | Startup — remove `initialize_graph()` call, rest unchanged |
| `frontend/` (mostly) | SSE parsing, components, state — all unchanged |

---

## Implementation Order

**Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8**

| Phase | What | Effort | Risk |
|-------|------|--------|------|
| 1 | Custom checkpoint store | ~3 hours | Low |
| 2 | Direct SDK for Tier 1 (streaming.py) | ~8 hours | High (critical path) |
| 3 | Direct SDK for Tier 2 (11 Haiku calls) | ~3 hours | Low (mechanical) |
| 4 | Tool schema conversion | ~3 hours | Medium |
| 5 | Self-routing tool selection | ~4 hours | Low (additive) |
| 6 | OpenAI Responses API (API key) | ~4 hours | Medium |
| 7 | Frontend model selection | ~3 hours | Low |
| 8 | Codex OAuth (subscription credits) | ~6 hours | Medium |
| **Total (Phases 1-8)** | | **~34 hours** | |

---

## Verification Checklist

### After Phase 1
- [ ] Backend starts, `conversation_messages` table created
- [ ] New conversation → messages stored in new table
- [ ] Page reload → messages load from new store
- [ ] Old conversations still accessible via LangGraph fallback

### After Phase 2
- [ ] Claude Sonnet: streaming chat works
- [ ] Tool calls execute (try "remember that I like pizza")
- [ ] Multi-turn tool loop works (try "search the web for X then save it")
- [ ] Prompt caching active (check Anthropic dashboard for cache hits)
- [ ] SSE events unchanged (thinking, tool_start, content, done)

### After Phase 3
- [ ] Memory extraction runs after conversation (check logs)
- [ ] Search tags generated post-turn
- [ ] Triage classifies heartbeat events (if any)
- [ ] Greeting call works in chat.py
- [ ] No `langchain` imports remain in codebase

### After Phase 4-5
- [ ] `[ROUTING]` log shows category selection per message
- [ ] "Hi" → minimal/no tools bound
- [ ] "Search the web for recipes" → `web_search` category
- [ ] "Create a notebook" → `notebooklm` category
- [ ] Routing timeout → falls back to all tools

### After Phase 6
- [ ] Set `OPENAI_API_KEY`, select GPT-5.4 → streaming works
- [ ] Tool calls work with OpenAI (try memory save)
- [ ] Image upload works with OpenAI
- [ ] Switch back to Claude → still works

### After Phase 7
- [ ] Settings page shows "General" panel
- [ ] Model dropdown groups by provider
- [ ] Model change persists and takes effect

### After Phase 8
- [ ] "Sign in with OpenAI" button appears in Settings (GeneralPanel)
- [ ] OAuth flow opens browser, user authenticates, callback succeeds, tokens stored in DB
- [ ] GPT-5.4 and GPT-5.3 Codex appear in model dropdown after sign-in
- [ ] Select GPT-5.4 → streaming chat works with subscription credits
- [ ] Tool calls work with Codex backend (try "remember that I like pizza")
- [ ] Multi-turn tool loop works (try "search the web for X then save it")
- [ ] Tier 2 (Haiku) still runs correctly after GPT-5.4 Tier 1 response (memory extraction, search tags, reflection)
- [ ] Disconnect → GPT-5.4 hidden from dropdown (or falls back to API key if `OPENAI_API_KEY` set)
- [ ] Token refresh works (wait for expiry, send message, auto-refresh happens)
- [ ] Switch back to Claude Sonnet → still works perfectly
- [ ] Scheduler/orchestrator `chat_with_memory()` works with selected OpenAI model

### End-to-End Integration
- [ ] NotebookLM tools work (create notebook, add source)
- [ ] WhatsApp Bridge tools work (send message)
- [ ] Scheduled events execute correctly
- [ ] Evolution tools still available (require Anthropic key)
- [ ] Memory system unchanged (hybrid search, temporal boosting)
- [ ] All SSE event types render in frontend

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| streaming.py rewrite breaks chat | Critical | Incremental phases; test after each |
| Tool schema format mismatch | High | Test every tool category with both providers |
| Checkpoint migration loses history | Medium | Dual-store with fallback |
| OpenAI Responses API incompatibility | Medium | Fallback to Chat Completions API if needed |
| Routing misclassifies message | Low | Always-on categories + "all" fallback + 5s timeout |
| @tool decorator replacement breaks tools | Low | Option A (keep BaseTool) minimizes risk |
| Codex OAuth private API changes | Medium | Monitor Cline/opencode-auth repos for endpoint changes; API key fallback available |
| Token refresh race conditions | Low | Single refresh promise pattern; force re-auth on persistent failure |
| Port 1455 conflict on localhost | Low | Check port availability; clear error message if occupied |
