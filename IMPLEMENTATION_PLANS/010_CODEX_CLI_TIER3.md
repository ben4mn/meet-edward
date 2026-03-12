# Plan 010: Codex CLI as Default Tier 3 Provider

## STOP: Read This Entire Document Before Making Any Changes

This plan adds OpenAI Codex CLI as the **default** Tier 3 coding agent, replacing Claude Code as the primary provider while keeping it as a togglable fallback. Codex CLI uses ChatGPT subscription credits (effectively free), while Claude Code costs Anthropic API credits per-token.

**Dependencies**: Plan 009 (Dual-Provider LLM) completed — Codex OAuth already implemented
**Prerequisite**: User must install Codex CLI: `npm install -g @openai/codex`
**Estimated effort**: ~8-10 hours
**New files**: 1 (`codex_cli_service.py`)
**Modified files**: 6

---

## Context & Rationale

### Why Codex CLI for Tier 3?
1. **Cost**: Uses ChatGPT subscription credits (no per-token billing) vs Claude Code (Anthropic API credits ~$15/1M input, $75/1M output for Opus)
2. **Same OAuth**: Codex CLI uses the exact same `CLIENT_ID` (`app_EMoamEEZ73f0CkXaXp7hrann`) and `auth.openai.com` endpoints as our existing `codex_oauth_service.py`
3. **Same tools**: Read, Write, Edit, Bash, Glob, Grep — identical tool categories to Claude Code
4. **Headless mode**: `codex exec --json` outputs JSONL events to stdout — perfect for subprocess consumption
5. **Full-auto mode**: `--full-auto` bypasses approval prompts (equivalent to Claude Code's `permission_mode="acceptEdits"`)

### How Codex CLI Differs from Claude Code
| Aspect | Claude Code | Codex CLI |
|--------|-------------|-----------|
| Binary | Python SDK (`claude-agent-sdk`) | Rust binary (npm package `@openai/codex`) |
| Integration | Async generator in-process | Subprocess with JSONL stdout |
| Auth | Anthropic API key (`ANTHROPIC_API_KEY`) | OAuth tokens in `~/.codex/auth.json` |
| Model | `claude-opus-4-6` (hardcoded) | `gpt-5-codex` (configurable via `-m`) |
| Tool restriction | `allowed_tools` parameter | No parameter — system prompt only |
| Cost | Per-token API billing | ChatGPT subscription (free) |

### Provider Auto-Selection Logic
1. Codex OAuth tokens exist in DB AND `codex` binary found → **Codex CLI** (default)
2. `claude-agent-sdk` importable → **Claude Code** (fallback)
3. Neither → Tier 3 disabled with error message
4. Frontend toggle to manually override

---

## Target Architecture

```
TIER 3 — Coding Agent (evolution + orchestrator CC tasks)
├── Codex CLI (DEFAULT) → codex exec --json --full-auto -C <cwd> -m gpt-5-codex "<task>"
│   Auth: ~/.codex/auth.json (seeded from DB's codex_oauth_tokens table)
│   Events: JSONL stdout → mapped to cc_* protocol
└── Claude Code (FALLBACK) → claude-agent-sdk query() in ProactorEventLoop thread
    Auth: ANTHROPIC_API_KEY env var
    Events: SDK AssistantMessage → cc_* protocol
```

```
codex_oauth_service.py (EXISTING — Plan 009)
        │ _load_tokens() → access_token + refresh_token from DB
        ▼
codex_cli_service.py (NEW)
        │ 1. _seed_auth_json(): Write tokens to ~/.codex/auth.json
        │ 2. Spawn `codex exec --json --full-auto -C <cwd> -m gpt-5-codex "<task>"`
        │ 3. Parse JSONL stdout → yield cc_* events (same contract as Claude Code)
        │ 4. _sync_back_tokens(): Read auth.json after session → update DB if refreshed
        ▼
cc_manager_service.py (MODIFIED)
        │ _get_tier3_provider() → auto-detect or user override
        │ _run_cc_session() → dispatch to codex_cli_service OR claude_code_service
        ▼
streaming.py / evolution_service.py (SAME EVENT CONTRACT — no changes to consumers)
```

## What Stays Unchanged

| Component | Why |
|-----------|-----|
| `streaming.py` | Consumes cc_* events — same protocol regardless of provider |
| `frontend/` CCSessionBlock | Renders cc_* events — provider-agnostic |
| Tier 1 (main chat) | Independent of Tier 3 |
| Tier 2 (Haiku background) | Independent of Tier 3 |
| All tool definitions | Provider-agnostic Python functions |
| DB schema | Reuse `claude_code_sessions` table (works for both providers) |

---

## Strict Rules

### MUST DO
- [ ] Seed `~/.codex/auth.json` from DB tokens before each Codex CLI session
- [ ] Read back `~/.codex/auth.json` after session (Codex may refresh tokens)
- [ ] Use same `cc_*` event protocol as Claude Code (no new event types)
- [ ] Use thread + ProactorEventLoop pattern on Windows (SelectorEventLoop can't spawn subprocesses)
- [ ] Default to Codex CLI when OAuth tokens + binary available
- [ ] Gracefully fall back to Claude Code if Codex unavailable
- [ ] Pass `--skip-git-repo-check` flag (Edward may run tasks in non-git dirs)
- [ ] Pass `--full-auto` for autonomous execution (no approval prompts)
- [ ] Defensive JSONL parsing (unknown event types logged and skipped)

### MUST NOT DO
- [ ] Do NOT remove or modify `claude_code_service.py` — keep as fallback
- [ ] Do NOT change the cc_* SSE event protocol (frontend depends on it)
- [ ] Do NOT auto-install Codex CLI — user must install manually
- [ ] Do NOT store Codex CLI tokens separately — reuse existing `codex_oauth_tokens` table
- [ ] Do NOT add new database tables (reuse `claude_code_sessions` + `settings`)

---

## Phase 1: Core Codex CLI Service

### Files
- **New**: `backend/services/codex_cli_service.py` (~250 lines)

### What to Build

#### 1.1 Status Check
```python
def get_status() -> dict:
    """Check if Codex CLI binary is available on PATH."""
    import shutil
    path = shutil.which("codex")
    if path:
        return {"status": "connected", "status_message": f"Codex CLI at {path}"}
    return {"status": "error", "status_message": "Codex CLI not found. Install: npm i -g @openai/codex"}
```

#### 1.2 Auth Seeding
Write DB tokens to `~/.codex/auth.json` before each session:

```python
async def _seed_auth_json() -> None:
    """Seed ~/.codex/auth.json from stored Codex OAuth tokens."""
    from services.codex_oauth_service import _load_tokens
    record = await _load_tokens()
    if not record:
        raise RuntimeError("No Codex OAuth tokens — connect OpenAI in Settings first")

    codex_home = Path.home() / ".codex"
    codex_home.mkdir(exist_ok=True)
    auth_data = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": record.access_token,
            "refresh_token": record.refresh_token,
            "id_token": "",
        },
        "last_refresh": datetime.utcnow().isoformat() + "Z",
    }
    (codex_home / "auth.json").write_text(json.dumps(auth_data))
```

**Important**: Only seed if auth.json is missing OR our DB tokens are newer. Codex CLI may refresh tokens during a session — we must not overwrite fresh tokens with stale ones. Compare `last_refresh` timestamps.

#### 1.3 Token Sync-Back
After session ends, read auth.json and update DB if tokens changed:

```python
async def _sync_back_tokens() -> None:
    """Read ~/.codex/auth.json and update DB if Codex refreshed tokens."""
    auth_file = Path.home() / ".codex" / "auth.json"
    if not auth_file.exists():
        return
    data = json.loads(auth_file.read_text())
    tokens = data.get("tokens", {})
    new_access = tokens.get("access_token", "")
    new_refresh = tokens.get("refresh_token", "")
    if not new_access:
        return

    from services.codex_oauth_service import _load_tokens, _store_tokens
    record = await _load_tokens()
    if record and record.access_token != new_access:
        # Codex refreshed — update our DB
        from services.codex_oauth_service import _decode_jwt_claims
        claims = _decode_jwt_claims(new_access)
        auth_claim = claims.get("https://api.openai.com/auth", {})
        account_id = auth_claim.get("chatgpt_account_id", record.account_id)
        email = claims.get("email", record.email)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)  # conservative
        await _store_tokens(new_access, new_refresh, account_id, email, expires_at)
        print("[CODEX CLI] Synced refreshed tokens back to DB")
```

#### 1.4 Subprocess Execution (Thread Pattern)

Same Windows fix as Claude Code — run subprocess in thread with ProactorEventLoop:

```python
_STREAM_END = object()

def _run_codex_in_thread(task: str, cwd: str, model: str, system_prompt: str,
                          event_queue: ThreadQueue) -> None:
    """Run codex exec in a thread with its own ProactorEventLoop."""
    async def _run():
        cmd = ["codex", "exec", "--json", "--full-auto", "--skip-git-repo-check",
               "-C", cwd, "-m", model]
        if system_prompt:
            cmd.extend(["--config", f"instructions={system_prompt}"])
        cmd.append(task)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async for line in proc.stdout:
            line = line.decode().strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                mapped = _map_codex_event(event)
                if mapped:
                    event_queue.put(mapped)
            except json.JSONDecodeError:
                pass  # stderr bleeding or non-JSON line
        await proc.wait()
        if proc.returncode != 0:
            stderr = await proc.stderr.read()
            event_queue.put(("error",
                f"Codex exited with code {proc.returncode}: {stderr.decode()[:500]}"))
        else:
            event_queue.put(("done", None))

    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
    else:
        asyncio.run(_run())
    event_queue.put(_STREAM_END)
```

#### 1.5 JSONL Event Mapping

Map Codex JSONL events to our existing cc_* protocol:

```python
def _map_codex_event(event: dict) -> Optional[tuple]:
    """Map a Codex JSONL event to our internal tuple format."""
    etype = event.get("type", "")
    item = event.get("item", {})
    item_type = item.get("type", "")

    if etype == "thread.started":
        return ("started", event.get("thread_id"))

    elif etype == "item.completed" and item_type == "agent_message":
        text = item.get("text", "")
        if text:
            return ("text", text)

    elif etype == "item.started" and item_type == "command_execution":
        cmd = item.get("command", "")
        return ("tool_use", "Bash", cmd[:500])

    elif etype == "item.completed" and item_type == "command_execution":
        output = item.get("output", "")
        return ("tool_result", str(output)[:500])

    elif etype == "item.started" and item_type == "file_change":
        filename = item.get("filename", item.get("path", ""))
        return ("tool_use", "Edit", filename[:500])

    elif etype == "item.completed" and item_type == "file_change":
        return ("tool_result",
            f"File changed: {item.get('filename', item.get('path', ''))}")

    elif etype == "error":
        return ("error", event.get("message", str(event)))

    elif etype == "turn.completed":
        return None  # Internal tracking only

    return None  # Unknown event — skip
```

#### 1.6 Main Entry Point

Same public API as `run_claude_code()`:

```python
async def run_codex_cli(
    task: str,
    conversation_id: Optional[str] = None,
    cwd: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_turns: int = 25,
) -> AsyncGenerator[dict, None]:
    """Spawn a Codex CLI session and yield cc_* events (same protocol as Claude Code)."""
    # 1. Seed auth
    await _seed_auth_json()

    session_id = str(uuid.uuid4())
    project_root = cwd or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model = "gpt-5-codex"

    yield {"event_type": "cc_started", "session_id": session_id}

    # 2. Run in thread
    event_queue = ThreadQueue()
    thread = threading.Thread(
        target=_run_codex_in_thread,
        args=(task, project_root, model, system_prompt or "", event_queue),
        daemon=True,
    )
    thread.start()

    accumulated_text = []
    error_text = None

    # 3. Poll events (same pattern as claude_code_service.py)
    try:
        while True:
            try:
                item = event_queue.get_nowait()
            except Empty:
                if not thread.is_alive():
                    error_text = "Codex CLI thread exited unexpectedly"
                    yield {"event_type": "cc_error", "session_id": session_id,
                           "error": error_text}
                    break
                await asyncio.sleep(0.05)
                continue

            if item is _STREAM_END:
                break

            event_type = item[0]
            if event_type == "text":
                accumulated_text.append(item[1])
                yield {"event_type": "cc_text", "session_id": session_id,
                       "text": item[1]}
            elif event_type == "tool_use":
                yield {"event_type": "cc_tool_use", "session_id": session_id,
                       "tool_name": item[1], "tool_input": item[2]}
            elif event_type == "tool_result":
                yield {"event_type": "cc_tool_result", "session_id": session_id,
                       "content": item[1]}
            elif event_type == "error":
                error_text = item[1]
                yield {"event_type": "cc_error", "session_id": session_id,
                       "error": error_text}
                break
            elif event_type == "done":
                break
    except Exception as e:
        error_text = str(e)
        yield {"event_type": "cc_error", "session_id": session_id,
               "error": error_text}

    thread.join(timeout=10)

    # 4. Sync tokens back (Codex may have refreshed during session)
    try:
        await _sync_back_tokens()
    except Exception as e:
        print(f"[CODEX CLI] Token sync-back failed: {e}")

    # 5. Persist and finalize
    status = "failed" if error_text else "completed"
    full_output = "\n".join(accumulated_text)
    await _save_session_to_db(session_id, conversation_id, task, status,
                               project_root, full_output[:5000], error_text)

    yield {"event_type": "cc_done", "session_id": session_id, "status": status,
           "output_summary": full_output[:2000]}
```

### Reused Functions
- `_save_session_to_db()` — copy from `claude_code_service.py:257-282` (identical DB write to `ClaudeCodeSessionModel`)
- `codex_oauth_service._load_tokens()` — existing, reads tokens from DB
- `codex_oauth_service._store_tokens()` — existing, writes tokens to DB
- `codex_oauth_service._decode_jwt_claims()` — existing, JWT decoding

---

## Phase 2: Provider Dispatch

### Files
- **Edit**: `backend/services/cc_manager_service.py` (lines 104-159)

### What Changes

Add `_get_tier3_provider()` function and modify `_run_cc_session()` to dispatch:

```python
async def _get_tier3_provider() -> str:
    """Determine which Tier 3 provider to use.

    Priority:
    1. Explicit user setting (tier3_provider in SettingsModel)
    2. Auto-detect: Codex CLI (if OAuth tokens + binary) > Claude Code (if SDK available)
    """
    # Check user override
    try:
        from services.database import async_session, SettingsModel
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(
                select(SettingsModel).where(SettingsModel.id == "default")
            )
            settings = result.scalar_one_or_none()
            override = getattr(settings, "tier3_provider", None) if settings else None
            if override in ("codex_cli", "claude_code"):
                return override
    except Exception:
        pass

    # Auto-detect: prefer Codex (subscription credits)
    try:
        from services.codex_cli_service import get_status as codex_status
        from services.codex_oauth_service import has_valid_tokens
        if codex_status()["status"] == "connected" and await has_valid_tokens():
            return "codex_cli"
    except Exception:
        pass

    return "claude_code"
```

Modify `_run_cc_session()` at line ~104 to dispatch based on provider:
```python
async def _run_cc_session(task_id, conversation_id, task_description, cwd,
                           allowed_tools, max_turns, queue):
    provider = await _get_tier3_provider()
    print(f"[CC MANAGER] Using Tier 3 provider: {provider}")

    if provider == "codex_cli":
        from services.codex_cli_service import run_codex_cli
        event_source = run_codex_cli(
            task=task_description, conversation_id=conversation_id,
            cwd=cwd, max_turns=max_turns,
        )
    else:
        from services.claude_code_service import run_claude_code
        event_source = run_claude_code(
            task=task_description, conversation_id=conversation_id,
            cwd=cwd, allowed_tools=allowed_tools, max_turns=max_turns,
        )

    # Rest of function unchanged — forward events to queue, track session_id, etc.
```

---

## Phase 3: Evolution Service Adaptation

### Files
- **Edit**: `backend/services/evolution_service.py` (lines 518-551, 596-639)

### What Changes

Replace direct `from services.claude_code_service import run_claude_code` with a dispatcher at two call sites.

#### 3.1 Coding phase (line 518 — `_run_cc_for_evolution`)

Add provider dispatch before the `async for event in` loop:

```python
async def _run_cc_for_evolution(description: str, cycle_id: str) -> str:
    """Run coding agent for the evolution coding step."""
    from services.cc_manager_service import _get_tier3_provider

    system_prompt = f"""You are modifying the Edward AI assistant codebase.
    ... (existing prompt unchanged) ...
    """

    provider = await _get_tier3_provider()
    if provider == "codex_cli":
        from services.codex_cli_service import run_codex_cli
        event_source = run_codex_cli(task=description, cwd=PROJECT_ROOT,
                                      system_prompt=system_prompt, max_turns=25)
    else:
        from services.claude_code_service import run_claude_code
        event_source = run_claude_code(task=description, cwd=PROJECT_ROOT,
                                        system_prompt=system_prompt, max_turns=25)

    output_parts = []
    async for event in event_source:
        # ... existing event handling unchanged ...
```

#### 3.2 Review phase (line 596 — `_run_review`)

```python
async def _run_review(branch_name: str) -> Tuple[bool, str]:
    """Run review using coding agent (read-only)."""
    from services.cc_manager_service import _get_tier3_provider

    # ... existing diff preparation unchanged ...

    provider = await _get_tier3_provider()
    if provider == "codex_cli":
        from services.codex_cli_service import run_codex_cli
        # Codex CLI has no allowed_tools — use system prompt restriction
        event_source = run_codex_cli(
            task=review_prompt, cwd=PROJECT_ROOT,
            system_prompt="You MUST only read files. Do NOT create, edit, or "
                         "delete any files. Do NOT run shell commands.",
            max_turns=5)
    else:
        from services.claude_code_service import run_claude_code
        event_source = run_claude_code(
            task=review_prompt, cwd=PROJECT_ROOT,
            allowed_tools=["Read", "Glob", "Grep"], max_turns=5)

    output_parts = []
    async for event in event_source:
        # ... existing event handling unchanged ...
```

**Note on review safety**: Claude Code has `allowed_tools` to restrict to read-only tools. Codex CLI does not have this parameter. Instead, we pass a system prompt restriction ("only read, do not modify"). Combined with `max_turns=5`, this provides equivalent safety. The review phase only analyzes a diff — it shouldn't need to modify anything.

---

## Phase 4: Settings & Frontend

### Files
- **Edit**: `backend/services/database.py` (SettingsModel, line 29)
- **Edit**: `backend/models/schemas.py` (settings schema)
- **Edit**: `frontend/components/settings/GeneralPanel.tsx`
- **Edit**: `frontend/lib/api.ts` (Settings type)
- **Edit**: `backend/main.py` (startup log)

### 4.1 Database Column
Add `tier3_provider` to `SettingsModel` (after line 38 in `database.py`):

```python
tier3_provider = Column(String, nullable=True)  # "codex_cli" | "claude_code" | null (auto)
```

### 4.2 Settings Schema
Add to `schemas.py` settings type:
```python
tier3_provider: Optional[str] = None  # "codex_cli" | "claude_code" | null (auto-detect)
```

### 4.3 Frontend Selector
Add a "Coding Agent" dropdown in `GeneralPanel.tsx` below the model selector:
- Options: **Auto** (default — shows which provider is auto-selected), **Codex CLI** (subscription credits), **Claude Code** (API credits)
- Disabled options grayed out with reason (e.g., "Codex CLI not installed", "No OAuth tokens")

### 4.4 Startup Log
In `main.py` lifespan, log the auto-detected Tier 3 provider:
```python
from services.cc_manager_service import _get_tier3_provider
provider = await _get_tier3_provider()
print(f"[STARTUP] Tier 3 coding agent: {provider}")
```

---

## Files Summary

| File | Action | Phase | Description |
|------|--------|-------|-------------|
| `backend/services/codex_cli_service.py` | **CREATE** | 1 | Core service: auth seeding, subprocess, JSONL→cc_* mapping, token sync |
| `backend/services/cc_manager_service.py` | EDIT | 2 | `_get_tier3_provider()` + dispatch in `_run_cc_session()` |
| `backend/services/evolution_service.py` | EDIT | 3 | Replace direct `run_claude_code()` with provider dispatch (2 call sites) |
| `backend/services/database.py` | EDIT | 4 | Add `tier3_provider` column to `SettingsModel` |
| `backend/models/schemas.py` | EDIT | 4 | Add `tier3_provider` to settings schema |
| `backend/main.py` | EDIT | 4 | Log Tier 3 provider at startup |
| `frontend/components/settings/GeneralPanel.tsx` | EDIT | 4 | Add "Coding Agent" provider dropdown |
| `frontend/lib/api.ts` | EDIT | 4 | Add `tier3_provider` to Settings type |

## What Does NOT Change

| File | Why |
|------|-----|
| `backend/services/claude_code_service.py` | Kept as-is — fallback provider |
| `backend/services/graph/streaming.py` | Consumes cc_* events — protocol unchanged |
| `backend/services/codex_oauth_service.py` | Reused as-is — tokens already in DB |
| `frontend/components/chat/CCSessionBlock.tsx` | Renders cc_* events — provider-agnostic |
| All tool definitions (`tools.py`) | Not involved in Tier 3 |
| Database schema (tables) | Reuse existing `claude_code_sessions` + `settings` tables |

---

## Implementation Order

| Phase | What | Effort | Risk |
|-------|------|--------|------|
| 1 | `codex_cli_service.py` (core) | ~4 hours | Medium (JSONL format mapping) |
| 2 | Provider dispatch in `cc_manager_service.py` | ~1 hour | Low |
| 3 | Evolution service adaptation | ~1 hour | Low |
| 4 | Settings + frontend | ~2-3 hours | Low |
| **Total** | | **~8-10 hours** | |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Codex CLI not installed | Low | Auto-detect → fall back to Claude Code; clear error in settings |
| Auth.json token race (Codex refreshes, we overwrite) | Medium | Only seed if our tokens are newer; always sync-back after session |
| JSONL event format changes in Codex updates | Medium | Defensive parsing, unknown events skipped, log warnings |
| Windows SelectorEventLoop can't spawn subprocess | High | Thread + ProactorEventLoop (proven pattern from Claude Code) |
| No `allowed_tools` for evolution review | Low | System prompt restriction + low max_turns (5) |
| Codex CLI hangs or crashes | Medium | 10-min timeout, thread death detection (same as Claude Code) |
| Concurrent auth.json writes (multiple sessions) | Low | CC semaphore already limits to 2 concurrent sessions |

---

## Verification Checklist

### After Phase 1
- [ ] `codex` binary detected by `get_status()`
- [ ] `~/.codex/auth.json` seeded correctly from DB tokens
- [ ] `codex exec --json --full-auto -C . "list files"` produces parseable JSONL
- [ ] JSONL events map correctly to cc_* events
- [ ] Session persisted to `claude_code_sessions` table
- [ ] Tokens synced back to DB after session (if Codex refreshed them)

### After Phase 2
- [ ] `_get_tier3_provider()` returns `codex_cli` when OAuth tokens + binary available
- [ ] `_get_tier3_provider()` returns `claude_code` when Codex unavailable
- [ ] Spawning CC task from chat uses Codex CLI by default
- [ ] Events stream to frontend CCSessionBlock identically

### After Phase 3
- [ ] Evolution coding phase works with Codex CLI
- [ ] Evolution review phase works with Codex CLI (read-only via system prompt)
- [ ] Evolution full pipeline: branch → code → validate → test → review → merge
- [ ] Switch to Claude Code → evolution still works

### After Phase 4
- [ ] Settings page shows "Coding Agent" dropdown
- [ ] Auto option shows current auto-selected provider
- [ ] Manual override persists and takes effect
- [ ] Disabled options show reason (not installed, no tokens)

### End-to-End
- [ ] Codex CLI default: orchestrator CC task completes, events render in frontend
- [ ] Toggle to Claude Code: same task completes, same frontend rendering
- [ ] Remove Codex CLI from PATH: auto-fallback to Claude Code, no errors
- [ ] Revoke Codex OAuth: auto-fallback to Claude Code, no errors
- [ ] Both unavailable: clear error message, Tier 3 disabled

---

## References

- [Codex CLI docs](https://developers.openai.com/codex/cli/)
- [Codex exec (non-interactive)](https://developers.openai.com/codex/noninteractive/)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference/)
- [Codex auth](https://developers.openai.com/codex/auth/)
- [Codex auth CI/CD](https://developers.openai.com/codex/auth/ci-cd-auth)
- Existing implementation: `backend/services/claude_code_service.py` (pattern to follow)
- Existing OAuth: `backend/services/codex_oauth_service.py` (token reuse)
