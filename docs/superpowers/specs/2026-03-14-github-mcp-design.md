# Design: GitHub MCP Integration

**Date:** 2026-03-14
**Status:** Approved — ready for implementation
**Branch:** `feat/prompt-caching`
**Plan file:** `IMPLEMENTATION_PLANS/015_GITHUB_MCP.md` (to be created)

---

## 1. Overview

Add GitHub as a first-class hardcoded skill (`github_mcp`) that launches `github-mcp-server` via `npx` as a managed stdio MCP subprocess at startup. Tools are exposed to the LLM directly with the flat names the server provides. Access is read + soft-write (issues, comments, PRs); direct commits and pushes are not possible because the PAT lacks `contents:write` scope. Write actions require verbal confirmation from the user, enforced via a system prompt instruction injected when the skill is active.

This follows the same pattern as Apple Services (`mcp_client.py` + `apple_services` skill) rather than the user-managed `custom_mcp_service.py` pattern. `npx` is already available (Node.js is installed) and matches the existing custom MCP server launch mechanism.

---

## 2. Architecture

```
skill: github_mcp (skills_service.py)
       ↓ enabled + GITHUB_MCP_TOKEN set?
github_mcp_service.py  ←→  npx -y github-mcp-server stdio (subprocess)
       ↓ MCPToolWrapper instances
tool_registry.py  →  _get_github_mcp_tools(skill_states)
       ↓
streaming.py  →  bound to LLM alongside all other tools
```

The subprocess communicates over stdio (identical transport to Apple Services). A persistent `ClientSession` is held for the lifetime of the process. On skill disable/re-enable the session is torn down and recreated. `npx -y` downloads and caches the package on first use with no manual install step.

The existing `GITHUB_TOKEN` env var (used by `custom_mcp_tools.py` for GitHub REST API search rate limits) remains untouched. The new env var `GITHUB_MCP_TOKEN` is passed exclusively to the MCP subprocess.

---

## 3. PAT Setup

### Required Scopes (Fine-Grained PAT)

In the GitHub fine-grained PAT UI, set these **Repository permissions**:

| Permission label in UI | Access level | Reason |
|------------------------|-------------|--------|
| Contents | Read-only | Browse repos, read files, search code |
| Issues | Read and write | Read issues, create/comment/close issues |
| Pull requests | Read and write | Read PRs, create/comment on PRs |

Note: `Metadata` is a mandatory read-only permission automatically included for all fine-grained PATs — it appears pre-checked and cannot be disabled.

**Do not request:** Contents:write (prevents commits/pushes), Administration, Actions, Secrets, Deployments.

### Where to Create It

GitHub Settings → Developer Settings → Personal access tokens → Fine-grained tokens → Generate new token. Set expiration to 90 days and rotate on expiry.

### Environment Variable

Stored as `GITHUB_MCP_TOKEN` in `.env`. The service passes it to the subprocess as `GITHUB_PERSONAL_ACCESS_TOKEN` (the env var name the MCP server expects).

---

## 4. New Service File: `backend/services/github_mcp_service.py`

Model directly on the Apple Services section of `backend/services/mcp_client.py`.

### Module-level state

```python
_github_mcp_tools: List[Any] = []
_github_initialized: bool = False
_github_last_error: Optional[str] = None
_github_session = None
_github_stdio_context = None
_github_session_context = None
```

### `is_configured() -> bool`

Returns `True` if `os.getenv("GITHUB_MCP_TOKEN")` is set and non-empty.

### `async initialize_github_mcp() -> bool`

1. If `not is_configured()`: set `_github_last_error = "Set GITHUB_MCP_TOKEN in environment"`, return `False`.
2. If `_github_initialized`: return `True` (idempotent).
3. Read token: `token = os.getenv("GITHUB_MCP_TOKEN")`.
4. Build `StdioServerParameters`:
   - `command = "npx"`
   - `args = ["-y", "github-mcp-server", "stdio"]`
   - `env = {**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": token}`
5. Open `stdio_client(server_params)` — store context in `_github_stdio_context`.
6. Create `ClientSession` from the stdio transport — store context in `_github_session_context`, assign to `_github_session`.
7. Call `await _github_session.initialize()`.
8. Call `await _github_session.list_tools()` — wrap each with `MCPToolWrapper(session, tool.name, tool.description, tool.inputSchema)`.
9. Store in `_github_mcp_tools`, set `_github_initialized = True`, clear `_github_last_error`.
10. On any exception: capture error to `_github_last_error`, null all contexts/session, return `False`.

### `async shutdown_github_mcp()`

Exit `_github_session_context`, then `_github_stdio_context` (matching Apple Services teardown order). Null all module-level state. Set `_github_initialized = False`, clear `_github_mcp_tools`.

### `is_github_available() -> bool`

Returns `_github_initialized and len(_github_mcp_tools) > 0`.

### `get_github_mcp_tools() -> List[Any]`

Returns `_github_mcp_tools`.

### `get_status() -> dict`

- Not configured → `{"status": "error", "status_message": "Set GITHUB_MCP_TOKEN in environment"}`
- Initialized with tools → `{"status": "connected", "status_message": f"{N} tools available", "metadata": {"tools_count": N}}`
- Initialized but no tools → `{"status": "error", "status_message": "No tools available from GitHub MCP server"}`
- Not initialized, has error → `{"status": "error", "status_message": _github_last_error}`
- Not initialized, no error → `{"status": "connecting", "status_message": "Not yet initialized"}`

### Tool naming

The GitHub MCP server exposes flat tool names (`list_issues`, `create_issue`, `get_file_contents`, `create_pull_request`, etc.) with no `github_` prefix. We accept these names as-is — no prefix added at the service layer. The tool description section makes the GitHub context clear. If name collision ever becomes an issue, prefixing can be added at the `MCPToolWrapper` call site without changing the service contract.

---

## 5. Skill Registration: `backend/services/skills_service.py`

### `SKILL_DEFINITIONS` addition

Add after the `notebooklm` entry:

```python
"github_mcp": {
    "name": "GitHub",
    "description": "Read repos, issues, and PRs; create issues and comments via GitHub MCP",
    "get_status": lambda: _get_github_mcp_status(),
},
```

### Status helper

```python
def _get_github_mcp_status() -> dict:
    from services.github_mcp_service import get_status
    return get_status()
```

### `set_skill_enabled()` — on enable only

After the `notebooklm` init block (line ~335 in `skills_service.py`):

```python
if skill_id == "github_mcp" and enabled:
    try:
        from services.github_mcp_service import initialize_github_mcp
        await initialize_github_mcp()
    except Exception as e:
        print(f"Failed to initialize GitHub MCP client: {e}")
```

No disable path needed here. The existing pattern for whatsapp_mcp, apple_services, and notebooklm does NOT call shutdown when the skill is disabled via `set_skill_enabled()` — only when the full `reload_skills()` is called. Subprocess shutdown on disable is intentionally deferred to avoid mid-session tool disruption. Match this existing behavior.

### `reload_skills()` addition

After the `notebooklm` reload block:

```python
try:
    from services.github_mcp_service import shutdown_github_mcp, initialize_github_mcp
    await shutdown_github_mcp()
    await initialize_github_mcp()
except Exception as e:
    print(f"Failed to reload GitHub MCP client: {e}")
```

---

## 6. Tool Registry: `backend/services/tool_registry.py`

### `_get_skill_states()` addition

Add alongside the other skill entries:

```python
"github_mcp": await is_skill_enabled("github_mcp"),
```

### New tool getter function

```python
def _get_github_mcp_tools(skill_states: Dict[str, bool]) -> List[Any]:
    """Get GitHub MCP tools if github_mcp skill is enabled."""
    if not skill_states.get("github_mcp"):
        return []
    from services.github_mcp_service import get_github_mcp_tools, is_github_available
    if not is_github_available():
        return []
    return get_github_mcp_tools()
```

### `get_available_tools()` addition

After the NotebookLM tools block:

```python
add_tools(_get_github_mcp_tools(skill_states))
```

### `get_tool_descriptions()` addition

Detect by the sentinel tool name `get_me`, which the GitHub MCP server always exposes regardless of toolset configuration:

```python
if "get_me" in tool_names:
    sections.append(_get_github_mcp_description())
```

Place this block after the NotebookLM section (after the `nlm_` startswith check, before the Apple Reminders section).

### New description function

```python
def _get_github_mcp_description() -> str:
    """Description and write-confirmation guardrail for GitHub tools."""
    return """## GitHub (Read + Soft Write)

You have access to GitHub tools for reading repositories, files, issues, and pull
requests, and for creating issues and comments.

IMPORTANT — Confirmation required before any write action:
Before calling any tool that creates, modifies, or closes a GitHub resource
(creating an issue, posting a comment, opening or updating a PR, etc.), you MUST
first describe exactly what you are about to do — including repo, resource type,
and content — and wait for the user to explicitly confirm before calling the tool.
Example: "I'm about to open an issue titled 'X' in org/repo. Shall I proceed?"

Read-only tools (list_*, get_*, search_*) do not require confirmation."""
```

---

## 7. Startup / Shutdown: `backend/main.py`

### Startup position

After `initialize_custom_servers()` (currently line ~59 in `main.py`), before `initialize_notebooklm()` (currently line ~65):

```python
# Initialize GitHub MCP client (gracefully skipped if GITHUB_MCP_TOKEN not set)
try:
    from services.github_mcp_service import initialize_github_mcp
    await initialize_github_mcp()
except Exception as e:
    print(f"GitHub MCP initialization skipped: {e}")
```

`initialize_github_mcp()` handles missing token gracefully without raising — the try/except is only for unexpected errors.

### Shutdown position

Reverse of startup: after `shutdown_notebooklm()` (currently line ~140 in `main.py`), before `shutdown_custom_servers()` (currently line ~146):

```python
try:
    from services.github_mcp_service import shutdown_github_mcp
    await shutdown_github_mcp()
except Exception as e:
    print(f"GitHub MCP shutdown error: {e}")
```

---

## 8. Environment Configuration

### `.env.example` addition

Add after the `CUSTOM MCP SERVERS` section:

```ini
# =============================================================================
# GITHUB MCP (optional — enables GitHub read/write skill)
# =============================================================================

# Fine-grained PAT with: contents:read, issues:write, pull_requests:write
# Create at: GitHub → Settings → Developer Settings → Fine-grained tokens
# GITHUB_MCP_TOKEN=github_pat_...
```

### Distinction from `GITHUB_TOKEN`

`GITHUB_TOKEN` — GitHub REST API for MCP server discovery search in `custom_mcp_tools.py`. Unaffected.
`GITHUB_MCP_TOKEN` — Passed as `GITHUB_PERSONAL_ACCESS_TOKEN` to the `github-mcp-server` subprocess.

---

## 9. Frontend

No changes required. `SkillsPanel.tsx` renders skills dynamically from `GET /api/skills`. The `github_mcp` entry will appear automatically with its name, description, enabled toggle, and live status. The `tools_count` metadata field is already rendered by the existing panel.

---

## 10. Write Confirmation Guardrail — Scope

The guardrail in `_get_github_mcp_description()` requires confirmation before any tool that "creates, modifies, or closes a GitHub resource." Representative examples:

- **Read (no confirmation needed):** `list_issues`, `get_issue`, `list_pull_requests`, `get_pull_request`, `get_file_contents`, `list_commits`, `search_repositories`, `search_code`
- **Write (confirmation required):** `create_issue`, `update_issue`, `add_issue_comment`, `create_pull_request`, `update_pull_request`, `add_pull_request_review_comment`

Guardrail is enforced by the LLM following the system prompt instruction — consistent with the "values, not rules" autonomy philosophy in Edward.

---

## 11. Worker Agent Scope

GitHub tools are available to orchestrator workers (not excluded by `get_worker_tools()`). Workers doing research tasks may legitimately need to read repos. The write confirmation guardrail applies to workers too since `get_tool_descriptions()` is included in all system prompts.

---

## 12. Verification Steps

1. **Token check**: Set `GITHUB_MCP_TOKEN` in `.env`. Restart backend. Check logs for GitHub MCP init success with tool count.
2. **SkillsPanel**: Open Settings → Skills. Confirm `GitHub` skill appears. Enable it — status shows `connected` with tool count.
3. **Tool list**: Ask Edward "what GitHub tools do you have?" — should enumerate the available tools.
4. **Read tool (no confirmation)**: Ask Edward "list open issues in [your-repo]" — should call a read tool and return results without pausing.
5. **Write guardrail**: Ask Edward "create an issue in [your-repo] titled 'test'" — he MUST describe the action and pause. Confirm; verify the issue appears on GitHub.
6. **PAT scope enforcement**: Ask Edward to push a commit — the PAT has no `contents:write` so the MCP server should return an authorization error; verify Edward surfaces it gracefully.
7. **Skill disable**: Disable the skill. Ask about GitHub — Edward should say it's unavailable. Re-enable; tools return without backend restart.
8. **Reload**: `POST /api/skills/reload`. Confirm GitHub MCP session tears down and reconnects in logs.
9. **Missing token**: Remove `GITHUB_MCP_TOKEN` from `.env`, restart. SkillsPanel shows "Set GITHUB_MCP_TOKEN in environment".

---

## 13. Files to Create or Modify

| File | Change |
|------|--------|
| `backend/services/github_mcp_service.py` | **Create** — new service (~130 lines) |
| `backend/services/skills_service.py` | Add skill definition, status helper, enable/reload hooks |
| `backend/services/tool_registry.py` | Add skill state, tool getter, description function, wiring |
| `backend/main.py` | Add startup init and shutdown blocks |
| `.env.example` | Add `GITHUB MCP` section |

**No database migrations. No frontend changes. No new Python package dependencies.**
