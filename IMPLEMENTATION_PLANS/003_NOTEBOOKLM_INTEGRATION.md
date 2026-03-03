# Plan 003: NotebookLM Integration

## STOP: Read This Entire Document Before Making Any Changes

This plan adds Google NotebookLM as a skill for Edward, allowing him to autonomously create knowledge bases, add sources, query them, run research, and generate artifacts (audio overviews, quizzes, mind maps, etc.). Follows the exact pattern of existing skills.

**Dependencies**: Plan 002 (Autonomy Framework) completed — Edward needs the autonomy prompt to use NotebookLM with judgment
**Estimated effort**: 2-3 days
**Library**: `notebooklm-py>=0.3.2` ([GitHub](https://github.com/teng-lin/notebooklm-py))

---

## Context & Rationale

Edward currently has:
- **Memories** — short semantic snippets (auto-extracted, vector search)
- **Documents** — full text storage (recipes, notes, guides) — but only embeds title + first 500 chars
- **Web Search** — Brave Search for real-time lookups

NotebookLM adds a fundamentally different capability: **curated, source-grounded knowledge bases**. Unlike memories (fragments) or documents (standalone text), NotebookLM notebooks are structured collections of diverse sources (URLs, PDFs, YouTube videos, raw text) that can be queried together with source attribution.

### How It Works

The `notebooklm-py` library provides programmatic access to Google NotebookLM via undocumented Google APIs. It supports:
- Notebooks: create, list, rename, delete
- Sources: add URLs, YouTube, PDFs, text, Google Drive files
- Chat: ask questions grounded in sources with citations
- Research: web research with auto-import of discovered sources
- Artifacts: generate audio overviews, quizzes, mind maps, reports

**Important caveats**:
- Uses **undocumented Google APIs** — could break without notice
- Requires **one-time browser login** (Playwright) — then credentials persist ~1-2 weeks
- Best for "prototypes, research, personal projects" — which is exactly our use case

---

## Strict Rules

### MUST DO
- [ ] Follow the EXACT pattern of `html_hosting_service.py` for service structure
- [ ] Follow the EXACT pattern of `brave_search` tools for tool definitions in `tools.py`
- [ ] Register in `skills_service.py`, `tool_registry.py`, and `main.py`
- [ ] Add `notebooklm-py[browser]>=0.3.2` to `requirements.txt`
- [ ] Wrap EVERY library call in try/except with meaningful error messages
- [ ] Initialize client lazily (not at import time)
- [ ] Add `get_notebooklm_tools_description()` for system prompt guidance

### MUST NOT DO
- [ ] Do NOT modify any existing tool or service code
- [ ] Do NOT add database tables (NotebookLM manages its own state)
- [ ] Do NOT attempt automated Google login
- [ ] Do NOT expose raw notebook IDs to the user (reference by name)
- [ ] Do NOT add frontend UI components (skill toggle + chat tools is sufficient)
- [ ] Do NOT store NotebookLM credentials in Edward's database

---

## Phase 1: Service File

### Step 1.1: Create `backend/services/notebooklm_service.py`

New file. Singleton async client with lazy initialization.

**Structure**:
- `is_configured()` — checks if credentials file exists or env var set
- `get_status()` — returns connected/error/connecting status
- `_get_client()` — lazy singleton, creates client on first use
- `initialize_notebooklm()` — startup hook (graceful failure)
- `shutdown_notebooklm()` — cleanup hook
- Notebook operations: `list_notebooks()`, `create_notebook()`, `delete_notebook()`, `rename_notebook()`
- Source operations: `add_url_source()`, `add_text_source()`, `add_file_source()`, `add_youtube_source()`, `list_sources()`, `delete_source()`
- Query: `ask_notebook()`
- Research: `start_research()`, `poll_research()`, `import_research_sources()`
- Artifacts: `generate_audio_overview()`, `generate_quiz()`, `generate_mind_map()`, `generate_report()`, `list_artifacts()`, `get_artifact_status()`

**Key design**: All response objects use `getattr(obj, 'attr', fallback)` for defensive attribute access, since the library's response objects may change with API updates.

**Credential locations**:
- Default: `~/.notebooklm/storage_state.json`
- Override: `NOTEBOOKLM_STORAGE_PATH` env var
- Headless: `NOTEBOOKLM_AUTH_JSON` env var

---

## Phase 2: Tool Definitions

### Step 2.1: Add 13 tools to `backend/services/graph/tools.py`

All tools prefixed with `nlm_` to avoid name collisions:

| Tool | Args | Purpose |
|------|------|---------|
| `nlm_list_notebooks` | — | List all notebooks |
| `nlm_create_notebook` | name | Create a new notebook |
| `nlm_delete_notebook` | notebook_id | Delete a notebook (permanent) |
| `nlm_add_source` | notebook_id, source_type, content | Add source (url/youtube/text/file) |
| `nlm_list_sources` | notebook_id | List sources in a notebook |
| `nlm_ask` | notebook_id, question | Ask a question with source citations |
| `nlm_research` | notebook_id, query, mode? | Start web research (fast/deep) |
| `nlm_check_research` | notebook_id, research_id | Poll research status |
| `nlm_import_research` | notebook_id, research_id | Import discovered sources |
| `nlm_generate_artifact` | notebook_id, artifact_type, instructions? | Generate audio/quiz/mind_map/report |
| `nlm_check_artifact` | notebook_id, artifact_id | Check artifact generation status |
| `nlm_push_document` | document_id, notebook_id | Bridge: Edward document → NLM text source |
| `nlm_push_file` | file_id, notebook_id | Bridge: Edward file (PDF) → NLM file source |

**Every tool**:
1. Checks `is_configured()` first
2. Wraps in try/except
3. Returns human-readable string (never raises)
4. Uses lazy imports from service module

### Step 2.2: Add tool group constants and description function

```python
NOTEBOOKLM_TOOLS = [nlm_list_notebooks, nlm_create_notebook, ...]
NOTEBOOKLM_TOOL_NAMES = {t.name for t in NOTEBOOKLM_TOOLS}

def get_notebooklm_tools_description() -> str:
    """System prompt guidance for NotebookLM tools."""
    ...
```

---

## Phase 3: Skill Registration

### Step 3.1: Register skill in `backend/services/skills_service.py`

Add to `SKILL_DEFINITIONS`:
```python
"notebooklm": {
    "name": "Google NotebookLM",
    "description": "Build knowledge bases, query sources, and generate artifacts",
    "get_status": lambda: _get_notebooklm_status(),
},
```

Add status function, enable/disable handler in `set_skill_enabled()`, and reload handler in `reload_skills()`.

---

## Phase 4: Tool Registry Integration

### Step 4.1: Add to `backend/services/tool_registry.py`

1. Add `SKILL_TOOL_MAPPING` entry: `"notebooklm": ["nlm_list_notebooks", ...]`
2. Add `_get_skill_states()` cache entry: `"notebooklm": await is_skill_enabled("notebooklm")`
3. Add `_get_notebooklm_tools()` getter function
4. Add to `get_available_tools()` before custom MCP section
5. Add to `get_tool_descriptions()` with description function import

---

## Phase 5: Lifecycle Hooks

### Step 5.1: Add to `backend/main.py` startup

After custom MCP servers initialization, before tool registry:
```python
# Initialize NotebookLM client (if credentials exist)
try:
    from services.notebooklm_service import initialize_notebooklm
    await initialize_notebooklm()
except Exception as e:
    print(f"NotebookLM initialization skipped: {e}")
```

### Step 5.2: Add to shutdown

Before MCP shutdown:
```python
try:
    from services.notebooklm_service import shutdown_notebooklm
    await shutdown_notebooklm()
except Exception as e:
    print(f"NotebookLM shutdown error: {e}")
```

---

## Phase 6: Dependencies and Environment

### Step 6.1: Add to `backend/requirements.txt`

```
# Google NotebookLM integration
notebooklm-py[browser]>=0.3.2
```

### Step 6.2: Environment variables (all optional)

```bash
# NOTEBOOKLM_STORAGE_PATH=~/.notebooklm/storage_state.json
# NOTEBOOKLM_AUTH_JSON=
```

---

## Phase 7: First-Time Authentication

1. `pip install "notebooklm-py[browser]"`
2. `notebooklm login` — opens browser for Google OAuth
3. Credentials saved to `~/.notebooklm/storage_state.json`
4. Verify: `notebooklm notebooks list`
5. Enable skill: toggle in settings UI or `PATCH /api/skills/notebooklm`

**Credential rotation**: Expires ~1-2 weeks. Re-run `notebooklm login` when skill status shows "error".

---

## Files Summary

| File | Change |
|------|--------|
| `backend/services/notebooklm_service.py` | **NEW** — Singleton service with all NLM operations |
| `backend/services/graph/tools.py` | 13 new `nlm_*` tools + group constants + description |
| `backend/services/skills_service.py` | Skill definition + status + enable/disable/reload |
| `backend/services/tool_registry.py` | SKILL_TOOL_MAPPING + getter + integration |
| `backend/main.py` | Startup init + shutdown hook |
| `backend/requirements.txt` | Add `notebooklm-py[browser]>=0.3.2` |

**1 new file, 5 modified files.**

---

## Build Verification

| Test | Expected Result | |
|------|----------------|---|
| `pip install notebooklm-py[browser]>=0.3.2` | Installs without errors | |
| `notebooklm login` | Browser opens, credentials saved | |
| `notebooklm notebooks list` | Returns list (possibly empty) | |
| Start backend WITH credentials | "NotebookLM client initialized" in logs | |
| Start backend WITHOUT credentials | "NotebookLM credentials not found, skipping" | |
| `GET /api/skills` | Shows "notebooklm" skill with correct status | |
| Enable skill via settings | Skill enabled, tools available | |
| "List my NotebookLM notebooks" | Returns list via `nlm_list_notebooks` | |
| "Create a notebook called Test" | Notebook created, ID returned | |
| "Add this URL to my notebook: [url]" | Source added | |
| "What does my notebook say about [topic]?" | Answer with citations | |
| "Research [topic] for my notebook" | Research started | |
| "Generate a podcast from my notebook" | Audio generation started | |
| "Push my [document] to the notebook" | Document content added as source | |
| Call tool with skill disabled | "NotebookLM not configured" message | |
| Call tool with expired credentials | Clear error about re-authentication | |

---

## Rollback Plan

1. Delete `backend/services/notebooklm_service.py`
2. Remove NotebookLM tools from `backend/services/graph/tools.py`
3. Revert: `skills_service.py`, `tool_registry.py`, `main.py`
4. Remove `notebooklm-py[browser]` from `requirements.txt`
5. No database changes to revert (no tables were created)

---

## Implementation Notes (Post-Completion)

**Status: Complete**

### Files Created
- `backend/services/notebooklm_service.py` — Singleton async service with lazy client, name-based notebook resolution, defensive `getattr()` on all library responses

### Files Modified
- `backend/services/graph/tools.py` — 12 `nlm_*` tools + `NOTEBOOKLM_TOOLS` + `NOTEBOOKLM_TOOL_NAMES` + `get_notebooklm_tools_description()`
- `backend/services/skills_service.py` — Skill definition, status function, init-on-enable, reload handler
- `backend/services/tool_registry.py` — SKILL_TOOL_MAPPING, cache entry, getter, wired into `get_available_tools()` and `get_tool_descriptions()`
- `backend/main.py` — Startup init (after custom MCP, before tool registry) + shutdown hook (before custom MCP shutdown)
- `backend/requirements.txt` — Added `notebooklm-py[browser]>=0.3.2`

### Deviations from Plan
- **12 tools instead of 13**: Removed `nlm_check_research` and `nlm_import_research` because the library's `web_search()` handles polling and auto-import internally. Added `nlm_get_source_text` for fulltext extraction.
- **Expanded artifact types**: `nlm_generate_artifact` supports 9 types (audio, video, quiz, flashcards, slide_deck, infographic, mind_map, data_table, report) — plan originally listed 4 (audio, quiz, mind_map, report).
- **Client lifecycle**: Uses `__aenter__`/`__aexit__` on the context manager from `NotebookLMClient.from_storage()` rather than direct client instantiation, since the library requires async context manager pattern.
- **Import name**: Library imports as `from notebooklm import NotebookLMClient` (not `notebooklm_py`).
