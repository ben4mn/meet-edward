# Plan 007: NotebookLM Revamp — Replace notebooklm-py with notebooklm-mcp-cli

## STOP: Read This Entire Document Before Making Any Changes

This plan replaces the NotebookLM backend library to fix chronic auth expiration issues, and expands from 13 to 31 tools. The existing 13 tool names stay identical — the LLM sees no breaking change. 18 new tools are added for sharing, notes, Drive sync, downloads, exports, slide revision, and chat configuration.

**Dependencies**: Plan 003 (NotebookLM Integration) completed — we are revamping, not building from scratch
**Estimated effort**: 1-2 days
**New library**: `notebooklm-mcp-cli>=0.3.19` ([GitHub](https://github.com/jacob-bd/notebooklm-mcp-cli), 2k+ stars)
**Replaces**: `notebooklm-py[browser]>=0.3.2`

---

## Context & Rationale

### The Problem

The current `notebooklm-py` library authenticates via Playwright browser sessions stored in `~/.notebooklm/storage_state.json`. These sessions:
- Expire every **1-2 weeks**
- Require manual `notebooklm login` each time
- Sometimes need manual deletion of `~/.notebooklm/browser_profile/` (stale Playwright cookies)
- Have **zero automatic auth recovery** — when auth fails, every tool call returns an error until the user manually re-authenticates

### The Solution

`notebooklm-mcp-cli` uses a fundamentally different auth approach:
- **Cookie-based** — extracts 5 Google cookies (SID, HSID, SSID, APISID, SAPISID) from Chrome/Edge/Brave
- Cookies last **weeks** (not 1-2 weeks like Playwright storage_state)
- **3-layer auto-recovery** on 401/403:
  1. Layer 1: Re-fetch CSRF/session tokens from NotebookLM homepage using existing cookies
  2. Layer 2: Reload cookies from disk (in case another process refreshed them)
  3. Layer 3: Headless Chrome re-authentication if desktop environment available
- **No Playwright dependency** — uses `httpx` for all API calls, `websocket-client` only for initial login
- **Retry logic** — exponential backoff on 429/5xx transient errors
- Uses the same underlying Google `batchexecute` RPC protocol — all existing notebooks/data unaffected

### Additional Benefit: 29 Library Capabilities → 31 Edward Tools

The new library exposes 29 capabilities (vs 13 in notebooklm-py). Combined with Edward's 2 bridge tools (`nlm_push_document`, `nlm_push_file`), we get 31 tools total.

### Why Direct Import (Not MCP Server)

The library can run as a standalone MCP server, but we import it as a Python library instead because:
- Preserves `nlm_push_document`/`nlm_push_file` bridge tools (need Edward DB access)
- Keeps all 31 tools under the `nlm_` namespace with consistent naming
- No subprocess management overhead
- Full control over error messages
- `fastmcp` dependency is NOT eagerly imported (safe)

---

## Strict Rules

### MUST DO
- [ ] Keep all 13 existing `nlm_*` tool names identical (no breaking changes)
- [ ] Wrap every client call in `asyncio.to_thread()` (library is synchronous)
- [ ] Wrap every operation in try/except with meaningful error messages
- [ ] Use `load_cached_tokens()` from the library's auth module for credential checks
- [ ] Keep defensive attribute access (`getattr(obj, 'attr', fallback)`) for library responses
- [ ] Keep name-based notebook resolution (case-insensitive `_resolve_notebook_id()`)
- [ ] Add all 18 new tools to `SKILL_TOOL_MAPPING` in tool_registry.py
- [ ] Update `get_notebooklm_tools_description()` to document all 31 tools

### MUST NOT DO
- [ ] Do NOT run the library as an MCP server subprocess
- [ ] Do NOT change existing tool function signatures
- [ ] Do NOT add database tables
- [ ] Do NOT store credentials in Edward's database
- [ ] Do NOT import `fastmcp` or MCP server modules directly
- [ ] Do NOT remove `nlm_push_document` or `nlm_push_file` bridge tools

---

## Phase 1: Dependencies

### Step 1.1: Update `backend/requirements.txt` (line 37-38)

```diff
 # Google NotebookLM integration
-notebooklm-py[browser]>=0.3.2
+notebooklm-mcp-cli>=0.3.19
```

**New transitive dependencies** (all lightweight):
- `typer>=0.9.0` — CLI framework (only used by `nlm` command, not runtime)
- `rich>=13.0.0` — console formatting (used by typer)
- `websocket-client>=1.6.0` — CDP connection for `nlm login` only
- `platformdirs>=4.0.0` — cross-platform config directory resolution
- `fastmcp>=0.1.0` — NOT eagerly imported; package-level `__init__.py` only imports `NotebookLMClient`

**Dependency compatibility**:
- `httpx>=0.27.0` — our `>=0.25.0` will auto-resolve upward (safe)
- `pydantic>=2.0.0` — our `>=2.10.0` satisfies this

### Step 1.2: Install and authenticate

```powershell
cd backend && .venv\Scripts\Activate.ps1
pip install notebooklm-mcp-cli
nlm login          # Opens Chrome → log into Google → cookies extracted
nlm notebook list  # Verify it works
```

Credentials stored at `~/.notebooklm-mcp-cli/profiles/default/auth.json`.

---

## Phase 2: Service Layer Rewrite

### Step 2.1: Rewrite `backend/services/notebooklm_service.py`

**Full rewrite of the file.** Same export surface, new internals + 18 new functions.

#### 2.1.1: Imports and Configuration

```python
"""
Google NotebookLM service for Edward.

Uses notebooklm-mcp-cli library (cookie-based auth, 3-layer recovery).
Initial setup: run `nlm login` to extract cookies from Chrome.
"""

import os
import asyncio
from typing import Optional, List, Dict, Any
from pathlib import Path

# Optional profile override
NOTEBOOKLM_PROFILE = os.getenv("NOTEBOOKLM_PROFILE", None)  # None = default profile

# Singleton client
_client: Optional[Any] = None
```

#### 2.1.2: `is_configured()` — New credential check

```python
def is_configured() -> bool:
    """Check if NotebookLM credentials are configured."""
    try:
        from notebooklm_tools.core.auth import load_cached_tokens
        tokens = load_cached_tokens(profile=NOTEBOOKLM_PROFILE)
        return tokens is not None and bool(getattr(tokens, 'cookies', None))
    except Exception:
        return False
```

Remove: `NOTEBOOKLM_AUTH_JSON` env var, `NOTEBOOKLM_STORAGE_PATH` env var, `DEFAULT_STORAGE_PATH` constant.

#### 2.1.3: `_get_client()` — New singleton pattern

```python
async def _get_client() -> Any:
    """Get or create the NotebookLM client singleton."""
    global _client

    if _client is not None:
        return _client

    try:
        from notebooklm_tools import NotebookLMClient
        from notebooklm_tools.core.auth import load_cached_tokens
    except ImportError:
        raise Exception(
            "notebooklm-mcp-cli not installed. Run: pip install notebooklm-mcp-cli"
        )

    tokens = load_cached_tokens(profile=NOTEBOOKLM_PROFILE)
    if not tokens or not getattr(tokens, 'cookies', None):
        raise Exception(
            "NotebookLM credentials not found. Run: nlm login"
        )

    # Create sync client — all calls will use asyncio.to_thread()
    client = NotebookLMClient(cookies=tokens.cookies)
    _client = client
    return client
```

**Key differences from old code**:
- No `_context_manager` global
- No async context manager (`__aenter__`/`__aexit__`)
- Client is synchronous — plain object, not coroutine
- Cookies loaded from `load_cached_tokens()` not `from_storage()`

#### 2.1.4: `_resolve_notebook_id()` — Updated for sync client

```python
async def _resolve_notebook_id(notebook_name: str) -> Optional[str]:
    """Resolve notebook name to ID via case-insensitive match."""
    client = await _get_client()
    notebooks = await asyncio.to_thread(client.list_notebooks)

    name_lower = notebook_name.lower()
    for nb in notebooks:
        title = getattr(nb, "title", "") if hasattr(nb, "title") else nb.get("title", "")
        if title.lower() == name_lower:
            nb_id = getattr(nb, "id", None) if hasattr(nb, "id") else nb.get("id", None)
            return nb_id
    return None
```

#### 2.1.5: Existing 13 operations — Rewrite internals

Each function keeps its exact signature and return shape. Only the client call changes.

**Pattern** — replace all `await client.X.Y(...)` with `await asyncio.to_thread(client.Z, ...)`:

```python
# EXAMPLE: list_notebooks()
async def list_notebooks() -> List[Dict[str, Any]]:
    client = await _get_client()
    notebooks = await asyncio.to_thread(client.list_notebooks)
    return [
        {
            "id": getattr(nb, "id", None) if hasattr(nb, "id") else nb.get("id"),
            "name": getattr(nb, "title", "Untitled") if hasattr(nb, "title") else nb.get("title", "Untitled"),
        }
        for nb in notebooks
    ]
```

**Full mapping for existing operations:**

| Function | Old client call | New client call |
|---|---|---|
| `list_notebooks()` | `client.notebooks.list()` | `client.list_notebooks()` |
| `create_notebook(name)` | `client.notebooks.create(name)` | `client.create_notebook(title=name)` |
| `delete_notebook(notebook_name)` | `client.notebooks.delete(notebook_id)` | `client.delete_notebook(notebook_id)` |
| `add_url_source(nb_name, url)` | `client.sources.add_url(nb_id, url, wait=True)` | `client.add_url_source(nb_id, url, wait=True)` |
| `add_youtube_source(nb_name, url)` | `client.sources.add_youtube(nb_id, url)` | `client.add_url_source(nb_id, url)` — auto-detects YouTube |
| `add_text_source(nb_name, text, title)` | `client.sources.add_text(nb_id, title, text)` | `client.add_text_source(nb_id, text, title=title)` — note arg order swap |
| `add_file_source(nb_name, path)` | `client.sources.add_file(nb_id, path)` | `client.add_file(nb_id, path)` |
| `list_sources(nb_name)` | `client.sources.list(nb_id)` | `client.get_notebook_sources_with_types(nb_id)` |
| `delete_source(nb_name, src_id)` | `client.sources.delete(nb_id, src_id)` | `client.delete_source(src_id)` — no nb_id needed |
| `get_source_fulltext(nb_name, src_id)` | `client.sources.get_fulltext(nb_id, src_id)` | `client.get_source_fulltext(src_id)` — no nb_id needed, returns dict |
| `ask_notebook(nb_name, question)` | `client.chat.ask(nb_id, question)` | `client.query(nb_id, question)` — returns dict with `answer`, `citations` |
| `web_research(nb_name, query, mode)` | `client.research.web_search(nb_id, query, mode)` | `client.start_research(nb_id, query, mode=mode)` — returns dict with `task_id` |
| `generate_artifact(nb_name, type, instr)` | `client.artifacts.generate_X(nb_id, ...)` | `client.create_X(nb_id, ...)` — see artifact table |
| `wait_artifact(nb_name, task_id)` | `client.artifacts.wait_for_completion(nb_id, task_id)` | `client.poll_studio_status(nb_id)` — returns `list[dict]` of all artifacts |

**Artifact type → method mapping:**

| Type | New client method |
|---|---|
| `audio` | `client.create_audio_overview(nb_id, focus_prompt=instructions)` |
| `video` | `client.create_video_overview(nb_id, focus_prompt=instructions)` |
| `quiz` | `client.create_quiz(nb_id, focus_prompt=instructions)` |
| `flashcards` | `client.create_flashcards(nb_id, focus_prompt=instructions)` |
| `slide_deck` | `client.create_slide_deck(nb_id, focus_prompt=instructions)` |
| `infographic` | `client.create_infographic(nb_id, focus_prompt=instructions)` |
| `mind_map` | `client.generate_mind_map(nb_id)` |
| `data_table` | `client.create_data_table(nb_id, description=instructions)` |
| `report` | `client.create_report(nb_id, custom_prompt=instructions)` |

#### 2.1.6: 18 NEW service functions

Add these new functions to the service file, following the same pattern (resolve notebook name → get client → asyncio.to_thread → return dict):

**Notebooks (3):**
```python
async def get_notebook(notebook_name: str) -> Dict[str, Any]:
    """Get notebook details with sources."""
    # client.get_notebook(notebook_id) → returns dict or None

async def describe_notebook(notebook_name: str) -> Dict[str, Any]:
    """Get AI-generated notebook summary and suggested topics."""
    # client.get_notebook_summary(notebook_id) → returns dict with "summary", "suggested_topics"

async def rename_notebook(notebook_name: str, new_title: str) -> bool:
    """Rename a notebook."""
    # client.rename_notebook(notebook_id, new_title) → returns bool
```

**Sources (3):**
```python
async def add_drive_source(notebook_name: str, document_id: str, title: str,
                           mime_type: str = "application/vnd.google-apps.document") -> Dict[str, Any]:
    """Add a Google Drive document as source."""
    # client.add_drive_source(nb_id, document_id, title, mime_type, wait=True)

async def rename_source(notebook_name: str, source_id: str, new_title: str) -> bool:
    """Rename a source in a notebook."""
    # client.rename_source(nb_id, source_id, new_title)

async def describe_source(notebook_name: str, source_id: str) -> Dict[str, Any]:
    """Get AI-generated source summary with keywords."""
    # client.get_source_guide(source_id) → returns dict with "summary", "keywords"
```

**Chat (1):**
```python
async def configure_chat(notebook_name: str, goal: str = "default",
                         custom_prompt: Optional[str] = None,
                         response_length: str = "default") -> Dict[str, Any]:
    """Configure notebook chat settings."""
    # client.configure_chat(nb_id, goal, custom_prompt, response_length)
```

**Research (2):**
```python
async def poll_research(notebook_name: str, task_id: Optional[str] = None) -> Dict[str, Any]:
    """Check research progress."""
    # client.poll_research(nb_id, target_task_id=task_id)

async def import_research_sources(notebook_name: str, task_id: str,
                                  source_indices: Optional[List[int]] = None) -> List[Dict]:
    """Import discovered research sources into notebook."""
    # client.import_research_sources(nb_id, task_id, sources)
```

**Studio (2):**
```python
async def delete_artifact(notebook_name: str, artifact_id: str) -> bool:
    """Delete a studio artifact."""
    # client.delete_studio_artifact(artifact_id, nb_id)

async def revise_slides(notebook_name: str, artifact_id: str,
                        slide_instructions: List[Dict]) -> Dict[str, Any]:
    """Revise individual slides in a slide deck."""
    # client.revise_slide_deck(artifact_id, [(s["slide_number"], s["instruction"]) for s in slide_instructions])
```

**Sharing (3):**
```python
async def get_share_status(notebook_name: str) -> Dict[str, Any]:
    """Get notebook sharing settings and collaborators."""
    # Need to check library's sharing mixin methods

async def share_public(notebook_name: str, is_public: bool = True) -> Dict[str, Any]:
    """Enable or disable public link access."""
    # Via sharing mixin

async def share_invite(notebook_name: str, email: str, role: str = "viewer") -> Dict[str, Any]:
    """Invite a collaborator by email."""
    # Via sharing mixin
```

**Notes (1):**
```python
async def manage_note(notebook_name: str, action: str,
                      note_id: Optional[str] = None,
                      content: Optional[str] = None,
                      title: Optional[str] = None) -> Dict[str, Any]:
    """Create, list, update, or delete notes in a notebook."""
    # Dispatches based on action: create/list/update/delete
```

**Downloads & Exports (2):**
```python
async def download_artifact_file(notebook_name: str, artifact_type: str,
                                 output_path: str,
                                 artifact_id: Optional[str] = None) -> Dict[str, Any]:
    """Download a studio artifact to a file."""
    # client.download_artifact(...) — needs path sandboxing

async def export_artifact(notebook_name: str, artifact_id: str,
                          export_type: str, title: Optional[str] = None) -> Dict[str, Any]:
    """Export artifact to Google Docs or Sheets."""
    # export_type: "docs" or "sheets"
```

#### 2.1.7: Lifecycle hooks — Simplified

```python
async def initialize_notebooklm():
    """Initialize NotebookLM client on startup."""
    if not is_configured():
        print("NotebookLM credentials not found, skipping initialization")
        return
    try:
        await _get_client()
        print("NotebookLM client initialized")
    except Exception as e:
        print(f"NotebookLM client initialization failed: {e}")

async def shutdown_notebooklm():
    """Shutdown NotebookLM client."""
    global _client
    if _client is not None:
        try:
            # Close httpx session if the client has a close method
            if hasattr(_client, 'close'):
                _client.close()
            print("NotebookLM client shutdown complete")
        except Exception as e:
            print(f"NotebookLM shutdown error: {e}")
        finally:
            _client = None
```

No more `_context_manager`. No `__aexit__`. Just close and nullify.

---

## Phase 3: Tool Definitions

### Step 3.1: Update existing tools in `backend/services/graph/tools.py`

In all existing 13 `nlm_*` tool functions, update error messages:
- `"notebooklm login"` → `"nlm login"`
- `"notebooklm-py not installed"` → `"notebooklm-mcp-cli not installed"`

No other changes to existing tool signatures.

### Step 3.2: Add 18 new tool functions

Follow the EXACT pattern of existing `nlm_*` tools. Each tool:
1. Checks `is_configured()` first → returns error string if false
2. Calls service function in try/except
3. Returns human-readable string (never raises)
4. Uses `@tool` decorator with clear description

**New tools to add:**

```python
# --- Notebook Management (3 new) ---
@tool
def nlm_get_notebook(notebook_name: str) -> str:
    """Get notebook details with list of sources."""

@tool
def nlm_describe_notebook(notebook_name: str) -> str:
    """Get AI-generated summary and suggested topics for a notebook."""

@tool
def nlm_rename_notebook(notebook_name: str, new_title: str) -> str:
    """Rename a notebook."""

# --- Source Management (3 new) ---
@tool
def nlm_add_drive_source(notebook_name: str, document_id: str, title: str,
                         mime_type: str = "application/vnd.google-apps.document") -> str:
    """Add a Google Drive document as a source to a notebook."""

@tool
def nlm_rename_source(notebook_name: str, source_id: str, new_title: str) -> str:
    """Rename a source in a notebook."""

@tool
def nlm_describe_source(notebook_name: str, source_id: str) -> str:
    """Get AI-generated summary and keywords for a source."""

# --- Chat Configuration (1 new) ---
@tool
def nlm_configure_chat(notebook_name: str, goal: str = "default",
                       custom_prompt: str = "", response_length: str = "default") -> str:
    """Configure notebook chat settings. Goals: default, learning_guide, custom."""

# --- Research (2 new) ---
@tool
def nlm_poll_research(notebook_name: str, task_id: str = "") -> str:
    """Check research progress and get results when complete."""

@tool
def nlm_import_research(notebook_name: str, task_id: str,
                        source_indices: str = "") -> str:
    """Import discovered research sources into the notebook. Indices as comma-separated list, empty=all."""

# --- Studio / Artifacts (2 new) ---
@tool
def nlm_delete_artifact(notebook_name: str, artifact_id: str) -> str:
    """Delete a studio artifact permanently."""

@tool
def nlm_revise_slides(notebook_name: str, artifact_id: str,
                      slide_instructions: str) -> str:
    """Revise individual slides in a slide deck. Instructions as JSON: [{"slide_number": 1, "instruction": "..."}]"""

# --- Sharing (3 new) ---
@tool
def nlm_share_status(notebook_name: str) -> str:
    """Get notebook sharing settings, collaborators, and public link status."""

@tool
def nlm_share_public(notebook_name: str, is_public: bool = True) -> str:
    """Enable or disable public link access for a notebook."""

@tool
def nlm_share_invite(notebook_name: str, email: str, role: str = "viewer") -> str:
    """Invite a collaborator to a notebook. Roles: viewer, editor."""

# --- Notes (1 new) ---
@tool
def nlm_note(notebook_name: str, action: str, note_id: str = "",
             content: str = "", title: str = "") -> str:
    """Manage notes in a notebook. Actions: create, list, update, delete."""

# --- Downloads & Exports (2 new) ---
@tool
def nlm_download_artifact(notebook_name: str, artifact_type: str,
                          output_path: str = "", artifact_id: str = "") -> str:
    """Download a studio artifact to file. Types: audio, video, report, slide_deck, etc."""

@tool
def nlm_export_artifact(notebook_name: str, artifact_id: str,
                        export_type: str, title: str = "") -> str:
    """Export artifact to Google Docs or Sheets. Types: docs, sheets."""
```

### Step 3.3: Update tool group constants

```python
NOTEBOOKLM_TOOLS = [
    # Existing 13
    nlm_list_notebooks, nlm_create_notebook, nlm_delete_notebook,
    nlm_add_source, nlm_list_sources, nlm_delete_source, nlm_get_source_text,
    nlm_ask, nlm_research, nlm_generate_artifact, nlm_wait_artifact,
    nlm_push_document, nlm_push_file,
    # New 18
    nlm_get_notebook, nlm_describe_notebook, nlm_rename_notebook,
    nlm_add_drive_source, nlm_rename_source, nlm_describe_source,
    nlm_configure_chat,
    nlm_poll_research, nlm_import_research,
    nlm_delete_artifact, nlm_revise_slides,
    nlm_share_status, nlm_share_public, nlm_share_invite,
    nlm_note,
    nlm_download_artifact, nlm_export_artifact,
]
NOTEBOOKLM_TOOL_NAMES = {t.name for t in NOTEBOOKLM_TOOLS}
```

### Step 3.4: Update `get_notebooklm_tools_description()`

Expand the system prompt description to cover new capabilities:
- Notebook management: get details, AI summary, rename, delete
- Source management: add (URL/YouTube/text/file/Drive), list, rename, delete, describe, get fulltext
- Query: ask with citations, configure chat persona, multi-turn conversations
- Research: start web/Drive research (fast/deep), poll status, import sources
- Artifacts: generate 9 types, check status, delete, revise slides, download, export to Docs/Sheets
- Sharing: get status, toggle public link, invite collaborators
- Notes: create/list/update/delete notebook notes
- Edward bridge: push documents/files from Edward's storage into notebooks

---

## Phase 4: Tool Registry

### Step 4.1: Update `backend/services/tool_registry.py`

Update `SKILL_TOOL_MAPPING["notebooklm"]` to include all 31 tool names:

```python
"notebooklm": [
    # Notebook management
    "nlm_list_notebooks", "nlm_create_notebook", "nlm_delete_notebook",
    "nlm_get_notebook", "nlm_describe_notebook", "nlm_rename_notebook",
    # Source management
    "nlm_add_source", "nlm_list_sources", "nlm_delete_source",
    "nlm_get_source_text", "nlm_add_drive_source", "nlm_rename_source",
    "nlm_describe_source",
    # Chat
    "nlm_ask", "nlm_configure_chat",
    # Research
    "nlm_research", "nlm_poll_research", "nlm_import_research",
    # Artifacts / Studio
    "nlm_generate_artifact", "nlm_wait_artifact", "nlm_delete_artifact",
    "nlm_revise_slides", "nlm_download_artifact", "nlm_export_artifact",
    # Sharing
    "nlm_share_status", "nlm_share_public", "nlm_share_invite",
    # Notes
    "nlm_note",
    # Edward bridge tools
    "nlm_push_document", "nlm_push_file",
],
```

---

## Phase 5: No Changes Required

These files need NO modifications:
- `backend/services/skills_service.py` — skill definition, status function, enable/disable handlers all call `notebooklm_service` functions that keep their signatures
- `backend/main.py` — `initialize_notebooklm()` and `shutdown_notebooklm()` keep their signatures

---

## Phase 6: Documentation

### Step 6.1: Update `CLAUDE.md`

In the NotebookLM section, update:
- Library: `notebooklm-py[browser]>=0.3.2` → `notebooklm-mcp-cli>=0.3.19`
- Auth: `notebooklm login` → `nlm login`
- Credential path: `~/.notebooklm/storage_state.json` → `~/.notebooklm-mcp-cli/profiles/default/auth.json`
- Tool count: 13 → 31
- Add new tool names to the tool list
- Update environment variables section (remove `NOTEBOOKLM_STORAGE_PATH`, `NOTEBOOKLM_AUTH_JSON`; add optional `NOTEBOOKLM_PROFILE`)

### Step 6.2: Update plan table in CLAUDE.md

Add row:
```
| [007_NOTEBOOKLM_REVAMP.md](IMPLEMENTATION_PLANS/007_NOTEBOOKLM_REVAMP.md) | Replace notebooklm-py with notebooklm-mcp-cli, expand to 31 tools | **Complete** |
```

---

## Files Summary

| File | Change |
|------|--------|
| `backend/requirements.txt` | Replace `notebooklm-py[browser]` with `notebooklm-mcp-cli` |
| `backend/services/notebooklm_service.py` | **Full rewrite** — new client, new auth, 18 new functions |
| `backend/services/graph/tools.py` | 18 new `nlm_*` tools + updated constants + updated description |
| `backend/services/tool_registry.py` | Expand SKILL_TOOL_MAPPING from 13 to 31 entries |
| `CLAUDE.md` | Update NotebookLM docs, add plan 007 |

**0 new files, 5 modified files.**

---

## Build Verification

| Test | Expected Result |
|------|----------------|
| `pip install notebooklm-mcp-cli` | Installs without errors |
| `nlm login` | Browser opens, cookies extracted |
| `nlm notebook list` | Returns list (CLI verification) |
| Start backend WITH credentials | "NotebookLM client initialized" in logs |
| Start backend WITHOUT credentials | "NotebookLM credentials not found, skipping" |
| `GET /api/skills` | Shows "notebooklm" with "connected" status |
| "List my notebooks" | Returns list via `nlm_list_notebooks` |
| "Create a notebook called Test" | Created via `nlm_create_notebook` |
| "Describe notebook Test" | AI summary via `nlm_describe_notebook` |
| "Add URL to Test: https://en.wikipedia.org/wiki/Python" | Source added via `nlm_add_source` |
| "Describe the Wikipedia source" | AI summary via `nlm_describe_source` |
| "Ask Test: What is Python?" | Answer with citations via `nlm_ask` |
| "Generate an audio overview of Test" | Audio started via `nlm_generate_artifact` |
| "Check artifact status for Test" | Status list via `nlm_wait_artifact` |
| "Share Test publicly" | Public link via `nlm_share_public` |
| "Delete notebook Test" | Deleted via `nlm_delete_notebook` |
| Call tool with skill disabled | "NotebookLM not configured" message |
| Call tool with expired credentials | Auto-recovery or clear re-auth error |

---

## Rollback Plan

1. Revert `backend/requirements.txt` (restore `notebooklm-py[browser]>=0.3.2`)
2. Revert `backend/services/notebooklm_service.py` to old version
3. Revert `backend/services/graph/tools.py` (remove new tools, restore old error messages)
4. Revert `backend/services/tool_registry.py` (restore 13-tool mapping)
5. Run `pip install 'notebooklm-py[browser]'`
6. User runs `notebooklm login` if needed
7. Old `~/.notebooklm/storage_state.json` credentials still exist

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Both libraries use undocumented Google APIs | Medium | Same risk either way; mcp-cli is more actively maintained |
| `notebooklm-mcp-cli` internal API changes | Medium | Pin version `>=0.3.19`; defensive attribute access |
| 31 tools adds LLM context overhead | Low | System prompt description is gated by skill enabled state |
| Sync client wrapped in `asyncio.to_thread()` | Low | Well-tested pattern; NLM calls are infrequent |
| Cookie extraction fails on Windows | Low | `nlm login` supports Chrome/Edge on Windows |
| `download_artifact` file path security | Medium | Use Edward's sandbox directories for output paths |
| `fastmcp` dependency conflicts | Low | Not eagerly imported; compat with existing `mcp>=1.26.0` |
