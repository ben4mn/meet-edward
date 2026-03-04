# Plan 005: Execution Environment Fix

## STOP: Read This Entire Document Before Making Any Changes

This plan fixes three critical bugs that make all code execution environments (Python, JavaScript, Shell) and orchestrator workers completely non-functional on Windows.

**Dependencies**: Plans 001-004 completed
**Estimated effort**: 1-2 hours

---

## Context & Root Cause Analysis

### Bug 1: Subprocess creation crashes on Windows SelectorEventLoop (CRITICAL)

`backend/run.py` forces `SelectorEventLoop` on Windows because psycopg requires it (ProactorEventLoop crashes psycopg). However, `asyncio.create_subprocess_exec()` on Windows **only works with ProactorEventLoop**. This is a fundamental Windows asyncio limitation.

**Impact**: ALL execution environments fail instantly — Python, JavaScript, Shell. Every call to `run_subprocess()` in `base.py:75` throws an exception that is silently caught by `except Exception` at line 140, returning `"Execution error"` with 0ms duration.

**Affected call chain**:
```
execute_code/execute_shell/execute_javascript (tool)
  → run_subprocess() in base.py:75
    → asyncio.create_subprocess_exec()  ← CRASHES on SelectorEventLoop
      → except Exception (base.py:140) catches silently
        → ExecutionResult(success=False, error="Execution error: ...", duration_ms=0)
```

**NOT affected**: SQL execution (uses `loop.run_in_executor()` with blocking `sqlite3`, never calls `run_subprocess()`).

### Bug 2: Orchestrator tools return "Error: None" (CRITICAL)

`_task_to_dict()` in `orchestrator_service.py` always includes `"error": task.error` in the result dict, even when `task.error` is `None`. The tools check `if "error" in result:` which tests for key **presence**, not value **truthiness**. Since the key is always present, the error path always triggers.

**Impact**: All orchestrator tool calls (`spawn_worker`, `check_worker`, `cancel_worker`, `send_to_worker`, `spawn_cc_worker`) return `"Error: None"` even on success.

### Bug 3: `effort` parameter rejected by SDK (HIGH)

`_build_llm()` in `streaming.py` passes `model_kwargs={"effort": "high"}` for Claude 4.6 models. If the installed `anthropic` SDK version doesn't support the `effort` parameter, `Messages.create()` rejects it with a `TypeError`.

**Impact**: Orchestrator workers (which default to 4.6 models per `orchestrator_service.py:27`) may fail. Main chat may or may not be affected depending on the user's selected model.

---

## Strict Rules

### MUST DO
- [ ] Fix subprocess execution on Windows using `subprocess.run()` in a thread
- [ ] Keep macOS async subprocess path UNCHANGED (zero regression risk)
- [ ] Fix all 7 `"error" in result` checks to use `result.get("error")`
- [ ] Make `effort` parameter gracefully degrade when SDK doesn't support it
- [ ] Preserve all security restrictions (blocklists, env restrictions, timeouts)
- [ ] Preserve output capture, truncation, and file tracking behavior

### MUST NOT DO
- [ ] Do NOT change the event loop type (SelectorEventLoop is required for psycopg)
- [ ] Do NOT modify shell_execution.py, python_execution.py, or javascript_execution.py (fix is entirely in base.py)
- [ ] Do NOT change `_task_to_dict()` in orchestrator_service.py (REST API compatibility)
- [ ] Do NOT modify any tool definitions or signatures
- [ ] Do NOT remove the `effort` feature entirely — make it auto-detect SDK support

---

## Phase 1: Fix `effort` Parameter (streaming.py)

**File**: `backend/services/graph/streaming.py` (lines 19-28)

This is the highest-priority fix because it potentially blocks all chat on 4.6 models.

### Step 1.1: Add SDK capability detection at module level

Replace the current effort block (lines 19-28):

```python
# CURRENT CODE (lines 19-28):
# Models that support the effort parameter (Claude 4.6+)
_EFFORT_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6"}


def _build_llm(model: str, temperature: float, max_tokens: int = 16384) -> ChatAnthropic:
    """Build a ChatAnthropic instance, adding effort parameter for 4.6 models."""
    kwargs = {"model": model, "temperature": temperature, "max_tokens": max_tokens}
    if model in _EFFORT_MODELS:
        kwargs["model_kwargs"] = {"effort": "high"}
    return ChatAnthropic(**kwargs)
```

With:

```python
# Models that support the effort parameter (Claude 4.6+)
_EFFORT_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6"}

# Check if the installed Anthropic SDK accepts the effort parameter
_EFFORT_SUPPORTED = False
try:
    import anthropic as _anthropic
    import inspect as _inspect
    _EFFORT_SUPPORTED = "effort" in _inspect.signature(
        _anthropic.resources.messages.Messages.create
    ).parameters
except Exception:
    pass


def _build_llm(model: str, temperature: float, max_tokens: int = 16384) -> ChatAnthropic:
    """Build a ChatAnthropic instance, adding effort parameter for supported 4.6 models."""
    kwargs = {"model": model, "temperature": temperature, "max_tokens": max_tokens}
    if _EFFORT_SUPPORTED and model in _EFFORT_MODELS:
        kwargs["model_kwargs"] = {"effort": "high"}
    return ChatAnthropic(**kwargs)
```

**Why this approach**: Auto-detects at import time. When SDK is updated, effort activates automatically. No runtime cost. Degrades gracefully.

---

## Phase 2: Fix Orchestrator "Error: None" (tools.py + orchestrator_service.py)

### Step 2.1: Fix 6 error checks in tools.py

**File**: `backend/services/graph/tools.py`

Change every instance of `if "error" in result:` to `if result.get("error"):` at these lines:

| Line | Function | Change |
|------|----------|--------|
| 3112 | `spawn_worker` | `if "error" in result:` → `if result.get("error"):` |
| 3142 | `check_worker` | `if "error" in result:` → `if result.get("error"):` |
| 3212 | `cancel_worker` | `if "error" in result:` → `if result.get("error"):` |
| 3238 | `wait_for_workers` | `if "error" in r and not r.get("status"):` → `if r.get("error") and not r.get("status"):` |
| 3273 | `send_to_worker` | `if "error" in result:` → `if result.get("error"):` |
| 3315 | `spawn_cc_worker` | `if "error" in result:` → `if result.get("error"):` |

### Step 2.2: Fix 1 error check in orchestrator_service.py

**File**: `backend/services/orchestrator_service.py` (line 467)

```python
# CURRENT:
if "error" in task:
    return task

# FIX:
if task.get("error"):
    return task
```

**Why `result.get("error")` not fixing `_task_to_dict()`**: Removing the `error` key from `_task_to_dict()` when `None` would be a breaking change for the REST API (`GET /api/orchestrator/tasks/{id}`) which may rely on the field always being present. The `.get()` fix is safer and more localized.

---

## Phase 3: Fix Subprocess Execution on Windows (base.py)

**File**: `backend/services/execution/base.py`

This is the core fix that enables all code execution on Windows.

### Step 3.1: Add imports

Add `subprocess` and `sys` to the imports at the top of the file:

```python
import asyncio
import os
import shutil
import subprocess  # NEW
import sys          # NEW
import tempfile
import time
```

### Step 3.2: Add synchronous subprocess helper

Add this function BEFORE the existing `run_subprocess()` function (after the `_get_sandbox_dir` function):

```python
def _run_subprocess_sync(
    args: list[str],
    working_dir: Path,
    timeout: int,
    env: dict,
) -> tuple[int, bytes, bytes, bool]:
    """Run a subprocess synchronously. Returns (returncode, stdout, stderr, timed_out).

    Called via asyncio.to_thread() on Windows where SelectorEventLoop
    does not support asyncio.create_subprocess_exec().
    """
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(working_dir),
            env=env,
            timeout=timeout,
        )
        return (result.returncode, result.stdout, result.stderr, False)
    except subprocess.TimeoutExpired:
        return (-1, b"", b"", True)
```

### Step 3.3: Modify `run_subprocess()` with platform branching

Replace the existing `run_subprocess()` function (lines 50-147) with a version that branches on platform:

```python
async def run_subprocess(
    args: list[str],
    working_dir: Path,
    timeout: Optional[int] = None,
    env: Optional[dict] = None,
) -> ExecutionResult:
    """
    Run a subprocess with timeout, output capture, and truncation.

    On Windows, uses subprocess.run() in a thread (SelectorEventLoop
    does not support create_subprocess_exec).
    On macOS/Linux, uses asyncio.create_subprocess_exec().
    """
    timeout = timeout or EXECUTION_LIMITS["timeout_seconds"]
    start_time = time.time()

    # Get list of files before execution
    files_before = set(os.listdir(working_dir)) if working_dir.exists() else set()

    try:
        if sys.platform == "win32":
            # Windows: SelectorEventLoop cannot create subprocesses.
            # Run synchronously in a thread instead.
            returncode, stdout_bytes, stderr_bytes, timed_out = await asyncio.to_thread(
                _run_subprocess_sync, args, working_dir, timeout, env or os.environ
            )
            if timed_out:
                duration_ms = int((time.time() - start_time) * 1000)
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Execution timed out after {timeout} seconds",
                    duration_ms=duration_ms,
                )
        else:
            # macOS/Linux: use async subprocess
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                env=env or os.environ,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                duration_ms = int((time.time() - start_time) * 1000)
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Execution timed out after {timeout} seconds",
                    duration_ms=duration_ms,
                )
            returncode = process.returncode

        duration_ms = int((time.time() - start_time) * 1000)

        # Process output
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        # Truncate if necessary
        truncated = False
        max_output = EXECUTION_LIMITS["max_output_bytes"]

        if len(stdout_text) > max_output:
            stdout_text = stdout_text[:max_output] + "\n... [output truncated]"
            truncated = True

        if len(stderr_text) > max_output:
            stderr_text = stderr_text[:max_output] + "\n... [error output truncated]"
            truncated = True

        # Combine output
        output = stdout_text
        if stderr_text and returncode != 0:
            error = stderr_text
        else:
            error = None
            if stderr_text:
                output = output + ("\n" if output else "") + stderr_text

        # Get list of new files created (exclude internal files)
        files_after = set(os.listdir(working_dir)) if working_dir.exists() else set()
        internal_files = {"_execute.py", "_execute.js", "_execute.sh", "_execute.sql"}
        new_files = list(files_after - files_before - internal_files)

        return ExecutionResult(
            success=returncode == 0,
            output=output.strip(),
            error=error.strip() if error else None,
            duration_ms=duration_ms,
            truncated=truncated,
            files_created=new_files,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Execution error: {str(e)}",
            duration_ms=duration_ms,
        )
```

**Key design decisions**:
- **macOS path is byte-for-byte identical** to current code — zero regression risk
- **Windows path** uses `asyncio.to_thread()` + synchronous `subprocess.run()` which works on ANY event loop
- **Shared post-processing** (output decode, truncation, file tracking) — both paths converge after getting `returncode`, `stdout_bytes`, `stderr_bytes`
- **No changes to callers** — shell_execution.py, python_execution.py, javascript_execution.py all call `run_subprocess()` unchanged

---

## Files Summary

| File | Change | Lines Affected |
|------|--------|----------------|
| `backend/services/graph/streaming.py` | Add SDK effort detection, guard `_build_llm()` | 19-28 |
| `backend/services/graph/tools.py` | Fix 6 `"error" in result` → `result.get("error")` | 3112, 3142, 3212, 3238, 3273, 3315 |
| `backend/services/orchestrator_service.py` | Fix 1 `"error" in task` → `task.get("error")` | 467 |
| `backend/services/execution/base.py` | Add `_run_subprocess_sync()`, platform-branch `run_subprocess()` | 7-8, 49-147 |

**4 files modified, 0 new files.**

---

## Build Verification

After changes, restart backend: `cd backend && .venv\Scripts\Activate.ps1 && python run.py`

| Test | How to Test | Expected Result |
|------|-------------|-----------------|
| Python execution | Ask Edward: "Run `print('hello world')`" | Returns "hello world" with non-zero duration |
| Shell execution | Ask Edward: "Run the command `echo hello`" | Returns "hello" |
| JavaScript execution | Ask Edward: "Run `console.log('test')` in JavaScript" | Returns "test" |
| Spawn worker | Ask Edward: "Spawn a worker to tell me a joke" | Returns task ID and eventually a joke, NOT "Error: None" |
| Check worker | Use `check_worker` on completed task | Returns status and result summary |
| CC worker | Ask Edward: "Spawn a CC worker to read this file" | Returns task info or proper error message |
| Basic chat (4.6 model) | Send a normal message | Response streams correctly, no TypeError |

### Quick Smoke Test (all 3 fixes at once)
1. Start backend → send a chat message → verify response works
2. Ask "run `print(2+2)` in Python" → verify output is "4"
3. Ask "spawn a worker to say hi" → verify task ID returned, not "Error: None"

---

## Rollback Plan

Each fix is independent and can be rolled back separately:
- **Phase 1**: Remove `_EFFORT_SUPPORTED` check, revert to always passing effort (risk: may break on old SDK)
- **Phase 2**: Revert `.get("error")` back to `"error" in result` (restores the "Error: None" bug)
- **Phase 3**: Remove `_run_subprocess_sync()` and platform branch, revert to pure async (restores Windows execution failure)
