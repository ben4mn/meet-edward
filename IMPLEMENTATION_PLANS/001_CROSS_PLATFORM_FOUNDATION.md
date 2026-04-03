# Plan 001: Cross-Platform Foundation

## STOP: Read This Entire Document Before Making Any Changes

This plan makes Edward's backend run on **both Windows and macOS**. It creates PowerShell startup scripts alongside existing bash scripts and fixes platform-specific code that crashes on Windows. All macOS features degrade gracefully — no functionality is removed.

**Dependencies**: Plan 000 (Master Plan) read and understood
**Estimated effort**: 0.5-1 day

---

## Context & Rationale

The goal is **cross-platform support** (not a Windows-only migration). If the user switches to macOS later, everything works without code changes. The original codebase assumes macOS:
- `setup.sh` uses Homebrew (`brew install`)
- `start.sh` uses `source .venv/bin/activate` (Unix path)
- `restart.sh` uses `lsof` (not available on Windows)
- `os.uname()` in 2 service files crashes on Windows (function doesn't exist)
- Shell execution hardcodes `/opt/homebrew/bin` in PATH

Most of the backend (FastAPI, LangGraph, PostgreSQL, memory system) is already cross-platform. Only infrastructure and 4 files need changes.

---

## Strict Rules

### MUST DO
- [ ] Create PowerShell scripts alongside bash scripts (don't replace them)
- [ ] Use `sys.platform` instead of `os.uname()` for platform detection
- [ ] Test that existing bash scripts still work unchanged
- [ ] Preserve all macOS functionality — this is additive only

### MUST NOT DO
- [ ] Do NOT delete or modify setup.sh, start.sh, or restart.sh
- [ ] Do NOT add Windows-specific code to services that already degrade gracefully
- [ ] Do NOT attempt to emulate iMessage, Apple Contacts, or Apple Services on Windows
- [ ] Do NOT change database schema or API endpoints

---

## Phase 1: PowerShell Startup Scripts

### Step 1.1: Create `setup.ps1` (root)

PowerShell equivalent of `setup.sh`. Must:
- Check prerequisites: Python 3.11+, Node.js 18+, PostgreSQL 16+
- Create Python virtual environment at `backend/.venv`
- Install Python dependencies from `backend/requirements.txt`
- Install frontend dependencies via `npm install`
- Create PostgreSQL database and user (using `psql` CLI)
- Enable pgvector extension
- Copy `.env.example` to `.env` if it doesn't exist
- Print clear error messages if any prerequisite is missing
- Print success message with next steps

### Step 1.2: Create `backend/start.ps1`

PowerShell equivalent of `backend/start.sh`. Must:
- Activate virtual environment: `.venv\Scripts\Activate.ps1`
- Check if requirements have changed (compare hash)
- Auto-install new dependencies if needed
- Set environment variables (skip macOS-only ones)
- Start uvicorn: `python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload`

### Step 1.3: Create `restart.ps1` (root)

PowerShell equivalent of `restart.sh`. Must:
- Accept optional argument: `frontend`, `backend`, or both (default)
- Find and kill processes on ports 8000 and 3000 using `Get-NetTCPConnection` + `Stop-Process`
- Restart services
- Support the same UX as the bash version

---

## Phase 2: Fix Platform-Specific Crashes

### Step 2.1: Fix `os.uname()` in imessage_service.py

**File**: `backend/services/imessage_service.py`

Replace (2 occurrences):
```python
# OLD (crashes on Windows — os.uname() doesn't exist)
os.uname().sysname == "Darwin"

# NEW (works everywhere)
sys.platform == "darwin"
```

Add `import sys` at the top of the file.

### Step 2.2: Fix `os.uname()` in contacts_service.py

**File**: `backend/services/contacts_service.py`

Same replacement (2 occurrences). Add `import sys`.

### Step 2.3: Fix shell execution for Windows

**File**: `backend/services/execution/shell_execution.py`

Current code (line 121-134):
```python
restricted_env = {
    "PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:...",
    "HOME": os.environ.get("HOME", str(working_dir)),
    "TMPDIR": str(working_dir),
    ...
    "SHELL": "/bin/bash",
}
result = await run_subprocess(
    args=["bash", "-c", command],
    ...
)
```

Replace with platform-aware code:
```python
import sys

if sys.platform == "win32":
    restricted_env = {
        "PATH": os.environ.get("PATH", ""),
        "USERPROFILE": os.environ.get("USERPROFILE", str(working_dir)),
        "TEMP": str(working_dir),
        "TMP": str(working_dir),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
        "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
    }
    args = ["cmd.exe", "/c", command]
else:
    restricted_env = {
        "PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ.get("HOME", str(working_dir)),
        "TMPDIR": str(working_dir),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TERM": "xterm-256color",
        "SHELL": "/bin/bash",
        "USER": os.environ.get("USER", ""),
    }
    args = ["bash", "-c", command]
```

Update `is_available()`:
```python
def is_available() -> bool:
    if sys.platform == "win32":
        return shutil.which("cmd.exe") is not None
    return shutil.which("bash") is not None
```

---

## Phase 3: Verify Graceful Degradation

These items need NO code changes — just verification:

- [ ] `heartbeat/listener_imessage.py` — checks `os.path.exists(CHAT_DB_PATH)` → path won't exist on Windows → skips
- [ ] `heartbeat/listener_email.py` — checks `os.path.exists(MAIL_DB_PATH)` → skips
- [ ] `heartbeat/listener_calendar.py` — checks `is_apple_available()` → returns False → skips
- [ ] `mcp_client.py` `initialize_apple_mcp()` — wrapped in try/except in main.py → prints skip message
- [ ] `main.py` lifespan — all init calls wrapped in try/except → won't crash on Windows

---

## Build Verification

| Test | Expected Result | ✓ |
|------|----------------|---|
| Run `setup.ps1` on Windows | Venv created, deps installed, DB initialized | |
| Run `backend/start.ps1` | Uvicorn starts on :8000, no import errors | |
| `GET http://localhost:8000/health` | `{"status": "healthy"}` | |
| `GET http://localhost:8000/api/skills` | macOS skills show status "error"/"unavailable" | |
| `GET http://localhost:8000/api/auth/status` | Returns auth status | |
| Frontend: `cd frontend && npm run dev` | Starts on :3000 | |
| Frontend: `cd frontend && npm run lint` | No errors | |
| Frontend: `cd frontend && npm run build` | Builds successfully | |
| Existing bash scripts unchanged | `setup.sh`, `start.sh`, `restart.sh` still work on macOS | |

---

## Rollback Plan

All changes are additive. To rollback:
- Delete the 3 `.ps1` files
- Revert the 3 modified Python files (`git checkout` the 4 changed lines)

---

## Implementation Notes (Post-Completion)

**Status: Complete**

### Files Created
- `setup.ps1` — PowerShell equivalent of `setup.sh`
- `restart.ps1` — PowerShell equivalent of `restart.sh`
- `backend/start.ps1` — PowerShell equivalent of `backend/start.sh`

### Files Modified
- `backend/services/contacts_service.py` — Replaced `os.uname()` with `sys.platform == "darwin"`
- `backend/services/imessage_service.py` — Replaced `os.uname()` with `sys.platform == "darwin"`
- `backend/services/execution/shell_execution.py` — Platform-aware PATH and shell selection

### Deviations
- None significant. All bash scripts preserved unchanged alongside new PowerShell equivalents.
