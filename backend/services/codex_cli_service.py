"""
Codex CLI integration service for Edward.

Delegates coding tasks to OpenAI Codex CLI via subprocess with JSONL parsing.
Replaces the previous claude-agent-sdk based claude_code_service.py.
"""

import uuid
import json
import asyncio
import re
import subprocess
import tempfile
import time
import os
from typing import Optional, Dict, List, Any, AsyncGenerator, Callable, Set
from datetime import datetime

from services.database import async_session, ClaudeCodeSessionModel, SettingsModel
from sqlalchemy import select, desc


# Codex JSONL output can include a single line with a full file diff
# or a large tool result. asyncio's default StreamReader limit is 64KB,
# which overflows with the cryptic
# ``ValueError: Separator is not found, and chunk exceed the limit``.
# 8 MB is plenty of headroom for any realistic Codex line.
CODEX_STDOUT_BUFFER_LIMIT = 8 * 1024 * 1024


# In-memory tracking of active sessions
_active_sessions: Dict[str, dict] = {}


# Where Codex may run (Evolution v2 Phase 0, 2026-09-27): a scratch
# directory or one of Edward's own git worktrees, never Edward's live code,
# a release, the owner's worktrees, or a directory holding any of them — ``services/codex_paths.py`` says why. Imported
# here so the tool, the orchestrator and the tests keep one name for it.
from services.codex_paths import (  # noqa: E402,F401
    DEFAULT_LIVE_REPO,
    DEFAULT_RELEASES_DIR,
    DEFAULT_SCRATCH_ROOT,
    DEFAULT_WORKTREE_ROOTS,
    LIVE_REPO_ENV,
    RELEASES_ENV,
    SCRATCH_ROOT_ENV,
    WORKTREE_ROOTS_ENV,
    _real,
    cwd_refusal,
    protected_roots,
    running_repo_root,
    scratch_dir_for,
)


def exec_usage(event: dict) -> Dict[str, int]:
    """Token counts from a ``codex exec --json`` ``turn.completed`` event.

    The CLI reports ``input_tokens`` (cached included), ``cached_input_tokens``,
    ``output_tokens`` and, on newer versions, ``reasoning_output_tokens``;
    the Responses-style ``*_details`` spellings are read too. Pure.
    """
    usage = event.get("usage") if isinstance(event, dict) else None
    usage = usage if isinstance(usage, dict) else {}

    def _n(*values) -> int:
        for value in values:
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    return {
        "input_tokens": _n(usage.get("input_tokens")),
        "cached_tokens": _n(usage.get("cached_input_tokens"),
                            (usage.get("input_tokens_details") or {}).get("cached_tokens")),
        "output_tokens": _n(usage.get("output_tokens")),
        "reasoning_tokens": _n(usage.get("reasoning_output_tokens"),
                               (usage.get("output_tokens_details") or {}).get("reasoning_tokens")),
    }


def _record_exec_call(*, model: Optional[str], conversation_id: Optional[str], started: float,
                      status: str, usage: Optional[Dict[str, int]] = None,
                      error_class: Optional[str] = None) -> None:
    """One ``codex_calls`` row for a ``codex exec`` turn (spend ledger,
    2026-09-24). ``codex exec`` bypasses the rate gate, so this row is the
    only place its spend against the shared weekly quota shows up."""
    try:
        import time as _time
        from services import codex_call_ledger

        codex_call_ledger.schedule_call(
            purpose="codex_cli_service.run_codex",
            priority=None,
            turn_source="codex_exec",
            conversation_id=conversation_id,
            model=model,
            latency_ms=int((_time.monotonic() - started) * 1000),
            status=status,
            error_class=error_class,
            **(usage or {}),
        )
    except Exception as e:  # noqa: BLE001 — accounting never breaks a session
        print(f"[codex] call ledger row not written: {e}")


def _build_codex_cmd(
    task: str,
    *,
    model: Optional[str] = None,
    instructions_path: Optional[str] = None,
    cwd: Optional[str] = None,
    read_only: bool = False,
) -> List[str]:
    """Assemble the ``codex exec`` argv.

    ``model`` is the most load-bearing flag — without it Codex falls back
    to a built-in default that ChatGPT-account auth no longer accepts
    (see 2026-05-18 → 2026-05-26 evolution outage: all cycles died with
    ``invalid_request_error: 'gpt-5.2-codex' is not supported``).
    """
    # Never the owner's own Codex setup (Evolution v2 Phase 0, 2026-09-27):
    # a personal config.toml can enable computer-use, chrome and browser
    # plugins, MCP servers and a notify hook. ``--ignore-user-config`` skips it (auth
    # still comes from CODEX_HOME), ``--ignore-rules`` skips the execpolicy
    # ``.rules`` it does not cover — an "always allow" the owner gives in
    # their own Codex would otherwise auto-approve that command prefix here too
    # (review 2026-09-27) — ``--ephemeral`` keeps no session files,
    # and ``--skip-git-repo-check`` lets it start in a scratch directory,
    # which is not a repo (the trust the owner gave their checkout lived in the
    # config it no longer reads). Every flag here must be in ``codex exec
    # --help``; ``codex_preflight`` checks that before a session starts.
    cmd: List[str] = [
        "codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check",
    ]
    # The sandbox, and never a way out of it (2026-09-27). ``--approve-for-me``
    # (what replaced ``--full-auto`` in 0.153) routes each request to leave
    # the sandbox — a write outside the worktree, the network — to an
    # automatic reviewer that can say yes, and while the taint gate runs in
    # shadow a tainted turn can still start Codex. ``approval_policy="never"``
    # makes such a request fail instead; a smoke run on 0.153.2 wrote in its
    # scratch directory and got "operation not permitted" writing to ~.
    # (``--approve-for-me`` and ``--sandbox`` cannot be combined; neither is
    # needed now.)
    cmd.extend(["-s", "read-only" if read_only else "workspace-write"])
    cmd.extend(["--config", 'approval_policy="never"'])
    if model:
        cmd.extend(["-m", model])
    if instructions_path:
        # ``model_instructions_file`` is the key 0.153.2 reads; the old
        # ``experimental_instructions_file`` is unknown to it, and an unknown
        # ``-c`` key is dropped silently (review 2026-09-27).
        cmd.extend(["--config", f"model_instructions_file={instructions_path}"])
    if cwd:
        cmd.extend(["-C", cwd])
    # The task last, after ``--`` (review 2026-09-27): right after ``exec``
    # clap read a task starting with "-" (a markdown bullet list) as an
    # option and died in under a second, a task that is exactly "review" or
    # "resume" as that subcommand, and one flag-shaped token as that flag.
    cmd.extend(["--", task])
    return cmd


# ---- the argv contract (Evolution v2 Phase 0, 2026-09-27) ------------------
#
# An argv the installed CLI does not accept kills every session in under a
# second: 0.153 dropping ``--full-auto`` (2026-09-05) and 0.153.2 refusing
# ``--sandbox`` beside ``--approve-for-me`` (2026-09-07) each did. Before a
# session starts, ``codex --version`` and ``codex exec --help`` are read and
# every flag ``_build_codex_cmd`` can emit must be listed; otherwise nothing
# is spawned and the log says why, once. ``scripts/codex_probe.py`` prints
# the same check by hand.

PREFLIGHT_TTL_SECONDS = 300
# A failure is probed again sooner (review 2026-09-27): one timeout under
# load, an EAGAIN, or the moment ``brew upgrade --cask codex`` swaps the
# Caskroom symlink used to refuse every session for the full five minutes.
PREFLIGHT_FAILURE_TTL_SECONDS = 20
PREFLIGHT_TIMEOUT_SECONDS = 10
_OPTION_TOKEN = re.compile(r"(?:^|,\s*)(--?[A-Za-z0-9][A-Za-z0-9-]*)")
_preflight_cache: Optional[tuple] = None  # (checked_at, result)
_preflight_logged: Optional[str] = None


def help_flags(help_text: str) -> Set[str]:
    """Every option a clap ``--help`` lists (``-m``, ``--model`` …): the
    tokens that open an option line. Prose and examples are not. Pure."""
    flags: Set[str] = set()
    for line in (help_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        head = stripped.split("  ", 1)[0].split(" <", 1)[0]
        flags.update(_OPTION_TOKEN.findall(head))
    return flags


def argv_contract() -> List[str]:
    """Every flag ``_build_codex_cmd`` can emit, in order. Pure."""
    flags: List[str] = []
    for read_only in (False, True):
        cmd = _build_codex_cmd("probe", model="m", instructions_path="/i", cwd="/c", read_only=read_only)
        for token in cmd[2:cmd.index("--")]:  # between ``codex exec`` and ``-- <task>``
            if token.startswith("-") and token not in flags:
                flags.append(token)
    return flags


def contract_missing(help_text: str) -> List[str]:
    """The contract's flags ``codex exec --help`` does not list. Pure."""
    listed = help_flags(help_text)
    return [flag for flag in argv_contract() if flag not in listed]


def reset_preflight() -> None:
    """Forget the cached preflight (tests)."""
    global _preflight_cache, _preflight_logged
    _preflight_cache = None
    _preflight_logged = None


def codex_preflight(*, run: Callable[..., Any] = subprocess.run, force: bool = False) -> Dict[str, Any]:
    """``{"ok", "version", "missing", "error"}`` for the installed CLI,
    cached ``PREFLIGHT_TTL_SECONDS`` (a failure ``PREFLIGHT_FAILURE_TTL_SECONDS``).
    Reads ``--version`` and ``exec --help`` only; never runs a task."""
    global _preflight_cache, _preflight_logged
    now = time.monotonic()
    if not force and _preflight_cache:
        checked_at, cached = _preflight_cache
        ttl = PREFLIGHT_TTL_SECONDS if cached.get("ok") else PREFLIGHT_FAILURE_TTL_SECONDS
        if now - checked_at < ttl:
            return cached
    result: Dict[str, Any] = {"ok": False, "version": None, "missing": [], "error": None}
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": PREFLIGHT_TIMEOUT_SECONDS}
        version = run(["codex", "--version"], **kwargs)
        help_proc = run(["codex", "exec", "--help"], **kwargs)
        result["version"] = (version.stdout or "").strip() or None
        if version.returncode != 0 or help_proc.returncode != 0:
            result["error"] = (f"codex --version / exec --help failed "
                               f"({version.returncode}/{help_proc.returncode}): "
                               f"{((version.stderr or '') + (help_proc.stderr or '')).strip()[:200]}")
        else:
            result["missing"] = contract_missing(help_proc.stdout or "")
            if result["missing"]:
                result["error"] = (f"{result['version'] or 'codex'} exec does not accept "
                                   f"{', '.join(result['missing'])}; not starting Codex until the argv matches")
            else:
                result["ok"] = True
    except FileNotFoundError:
        result["error"] = "Codex CLI not installed (codex is not on PATH)"
    except Exception as e:  # noqa: BLE001 — a probe that breaks is a failed probe
        result["error"] = f"codex preflight failed: {type(e).__name__}: {e}"
    _preflight_cache = (now, result)
    if result["ok"]:
        # Recovered: the same failure next time (the next upgrade) is news.
        _preflight_logged = None
    return result


def _log_preflight_failure(result: Dict[str, Any]) -> None:
    """One log line per distinct failure, not one per attempt."""
    global _preflight_logged
    signature = f"{result.get('version')}|{result.get('error')}"
    if signature != _preflight_logged:
        _preflight_logged = signature
        print(f"[codex] preflight failed — not spawning: {result.get('error')}")


async def _get_default_codex_model() -> Optional[str]:
    """Resolve the model that Codex should use when the caller hasn't
    specified one. Mirrors ``settings.model`` so orchestrator CC tasks and
    other Codex-driven flows always match the user's active chat model."""
    try:
        async with async_session() as session:
            row = (await session.execute(select(SettingsModel))).scalar_one_or_none()
            return row.model if row else None
    except Exception:
        return None


def get_status() -> dict:
    """Check if Codex CLI is available."""
    try:
        result = asyncio.get_event_loop().run_in_executor(None, _check_codex_version)
    except Exception:
        pass

    # Synchronous fallback check
    import subprocess
    try:
        proc = subprocess.run(
            ["codex", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return {
                "status": "connected",
                "status_message": f"Codex CLI {proc.stdout.strip()}",
            }
        return {
            "status": "error",
            "status_message": f"Codex CLI returned error: {proc.stderr.strip()}",
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "status_message": "Codex CLI not installed. Run: npm install -g @openai/codex",
        }
    except Exception as e:
        return {
            "status": "error",
            "status_message": f"Codex CLI check failed: {e}",
        }


def _check_codex_version() -> str:
    import subprocess
    result = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
    return result.stdout.strip()


async def run_codex(
    task: str,
    conversation_id: Optional[str] = None,
    cwd: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
    max_turns: int = 25,
    timeout: int = 600,
    model: Optional[str] = None,
    read_only: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Spawn a Codex CLI session and yield structured events.

    ``read_only`` runs it in the read-only sandbox (it may look, not change).

    Yields dicts with event_type: cc_started, cc_text, cc_tool_use, cc_done, cc_error.
    Keeps cc_ prefix for frontend compatibility (CCSessionBlock, MessageBubble, etc.).
    """
    session_id = str(uuid.uuid4())
    # Never the live tree (Phase 0, 2026-09-27). The resolved path is what
    # runs, so a symlink swapped after the check cannot redirect it; a
    # scratch root configured inside a protected root is refused before
    # anything is created there.
    scratch = not (cwd and str(cwd).strip())
    project_root = scratch_dir_for(session_id) if scratch else _real(cwd)
    refused = cwd_refusal(cwd) or cwd_refusal(project_root)
    if refused:
        print(f"[codex] refused to spawn: {refused}")
        yield {"event_type": "cc_error", "session_id": session_id, "error": refused}
        yield {"event_type": "cc_done", "session_id": session_id, "status": "failed", "output_summary": refused}
        return
    preflight = await asyncio.to_thread(codex_preflight)
    if not preflight.get("ok"):
        _log_preflight_failure(preflight)
        error = preflight.get("error") or "codex preflight failed"
        yield {"event_type": "cc_error", "session_id": session_id, "error": error}
        yield {"event_type": "cc_done", "session_id": session_id, "status": "failed", "output_summary": error}
        return
    if scratch:
        os.makedirs(project_root, exist_ok=True)

    # Track session
    _active_sessions[session_id] = {
        "id": session_id,
        "conversation_id": conversation_id,
        "task": task,
        "status": "running",
        "cwd": project_root,
        "started_at": datetime.utcnow().isoformat(),
        "output_parts": [],
    }

    yield {
        "event_type": "cc_started",
        "session_id": session_id,
    }

    # Resolve model to use. Explicit arg wins; otherwise mirror the user's
    # active chat model from settings so Codex stays in lockstep.
    resolved_model = model if model else await _get_default_codex_model()

    # System prompt via temp file
    instructions_file = None
    if system_prompt:
        instructions_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="codex_instructions_"
        )
        instructions_file.write(system_prompt)
        instructions_file.close()

    cmd = _build_codex_cmd(
        task=task,
        model=resolved_model,
        instructions_path=instructions_file.name if instructions_file else None,
        cwd=project_root,
        read_only=read_only,
    )

    accumulated_text = []
    error_text = None

    try:
        # An allowlisted environment (PATH, HOME for Codex's auth, the
        # locale) — never the backend's secrets, and never the live
        # DATABASE_URL: Codex runs pytest in the worktree, and the suite's
        # clean_db fixture truncates whatever database it is pointed at.
        from services.sandbox_env import sandbox_env
        env = sandbox_env()

        import time as _time
        exec_started = _time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root,
            env=env,
            limit=CODEX_STDOUT_BUFFER_LIMIT,
        )

        async def read_stderr():
            """Drain stderr to avoid blocking."""
            stderr_lines = []
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode("utf-8", errors="replace"))
            return "".join(stderr_lines)

        stderr_task = asyncio.create_task(read_stderr())

        # Parse JSONL from stdout
        try:
            async def process_stdout():
                nonlocal error_text
                while True:
                    line = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=timeout,
                    )
                    if not line:
                        break

                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue

                    try:
                        event = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    if event_type == "thread.started":
                        # Already sent cc_started
                        pass

                    elif event_type in ("item.completed", "item.started"):
                        item = event.get("item", {})
                        item_type = item.get("type", "")

                        if item_type == "agent_message":
                            text = item.get("text", "")
                            if text:
                                accumulated_text.append(text)
                                yield {
                                    "event_type": "cc_text",
                                    "session_id": session_id,
                                    "text": text,
                                }

                        elif item_type == "command_execution":
                            command = item.get("command", "")
                            output = item.get("aggregated_output", "")
                            yield {
                                "event_type": "cc_tool_use",
                                "session_id": session_id,
                                "tool_name": "shell",
                                "tool_input": command[:500],
                            }
                            if output and event_type == "item.completed":
                                yield {
                                    "event_type": "cc_tool_result",
                                    "session_id": session_id,
                                    "content": output[:500],
                                }

                        elif item_type == "file_change":
                            filename = item.get("filename", "")
                            yield {
                                "event_type": "cc_tool_use",
                                "session_id": session_id,
                                "tool_name": "file_edit",
                                "tool_input": filename[:500],
                            }

                    elif event_type == "turn.completed":
                        # The turn's usage used to be discarded here, so
                        # codex exec spend against the shared weekly quota
                        # was invisible (spend ledger, 2026-09-24).
                        _record_exec_call(
                            model=resolved_model, conversation_id=conversation_id, started=exec_started,
                            status="ok", usage=exec_usage(event),
                        )

                    elif event_type in ("turn.failed", "error"):
                        error_msg = event.get("error", event.get("message", "Unknown Codex error"))
                        error_text = str(error_msg)
                        if event_type == "turn.failed":
                            limited = any(k in error_text.lower() for k in ("usage limit", "rate limit", "usage_limit"))
                            _record_exec_call(
                                model=resolved_model, conversation_id=conversation_id, started=exec_started,
                                status="rate_limited" if limited else "error",
                                usage=exec_usage(event) if event.get("usage") else None,
                                error_class="usage_limit_reached" if limited else "turn_failed",
                            )
                        yield {
                            "event_type": "cc_error",
                            "session_id": session_id,
                            "error": error_text,
                        }

            async for evt in process_stdout():
                yield evt

        except asyncio.TimeoutError:
            error_text = f"Codex session timed out after {timeout}s"
            _record_exec_call(
                model=resolved_model, conversation_id=conversation_id, started=exec_started,
                status="timeout", error_class="session_timeout",
            )
            process.kill()
            yield {
                "event_type": "cc_error",
                "session_id": session_id,
                "error": error_text,
            }

        # Wait for process to finish
        await process.wait()
        stderr_output = await stderr_task

        # Check for process-level errors
        if process.returncode != 0 and not error_text:
            error_text = f"Codex exited with code {process.returncode}"
            if stderr_output:
                error_text += f": {stderr_output[:500]}"
            yield {
                "event_type": "cc_error",
                "session_id": session_id,
                "error": error_text,
            }

    except Exception as e:
        error_text = str(e)
        yield {
            "event_type": "cc_error",
            "session_id": session_id,
            "error": error_text,
        }
    finally:
        # Clean up temp file
        if instructions_file and os.path.exists(instructions_file.name):
            try:
                os.unlink(instructions_file.name)
            except Exception:
                pass

    # Finalize session
    full_output = "\n".join(accumulated_text)
    status = "failed" if error_text else "completed"

    _active_sessions[session_id]["status"] = status
    _active_sessions[session_id]["completed_at"] = datetime.utcnow().isoformat()

    # Persist to DB (reuses ClaudeCodeSessionModel table)
    await _save_session_to_db(
        session_id=session_id,
        conversation_id=conversation_id,
        task=task,
        status=status,
        cwd=project_root,
        output_summary=full_output[:5000],
        error=error_text,
    )

    # Clean up in-memory tracking
    _active_sessions.pop(session_id, None)

    yield {
        "event_type": "cc_done",
        "session_id": session_id,
        "status": status,
        "output_summary": full_output[:2000],
    }


async def _save_session_to_db(
    session_id: str,
    conversation_id: Optional[str],
    task: str,
    status: str,
    cwd: str,
    output_summary: str,
    error: Optional[str],
) -> None:
    """Persist a completed session to the database."""
    try:
        async with async_session() as session:
            db_record = ClaudeCodeSessionModel(
                id=session_id,
                conversation_id=conversation_id,
                task=task,
                status=status,
                cwd=cwd,
                output_summary=json.dumps({"text": output_summary}),
                error=error,
                completed_at=datetime.utcnow(),
            )
            session.add(db_record)
            await session.commit()
    except Exception as e:
        print(f"Failed to save CC session to DB: {e}")


async def get_session(session_id: str) -> Optional[dict]:
    """Look up a session by ID (in-memory first, then DB)."""
    if session_id in _active_sessions:
        return _active_sessions[session_id]

    try:
        async with async_session() as session:
            result = await session.execute(
                select(ClaudeCodeSessionModel).where(ClaudeCodeSessionModel.id == session_id)
            )
            record = result.scalar_one_or_none()
            if record:
                return {
                    "id": record.id,
                    "conversation_id": record.conversation_id,
                    "task": record.task,
                    "status": record.status,
                    "cwd": record.cwd,
                    "output_summary": record.output_summary,
                    "error": record.error,
                    "started_at": record.started_at.isoformat() if record.started_at else None,
                    "completed_at": record.completed_at.isoformat() if record.completed_at else None,
                }
    except Exception as e:
        print(f"Failed to look up CC session: {e}")

    return None


async def list_sessions(conversation_id: Optional[str] = None, limit: int = 10) -> List[dict]:
    """List recent sessions."""
    try:
        async with async_session() as session:
            q = select(ClaudeCodeSessionModel).order_by(desc(ClaudeCodeSessionModel.created_at)).limit(limit)
            if conversation_id:
                q = q.where(ClaudeCodeSessionModel.conversation_id == conversation_id)

            result = await session.execute(q)
            records = result.scalars().all()

            return [
                {
                    "id": r.id,
                    "conversation_id": r.conversation_id,
                    "task": r.task[:100],
                    "status": r.status,
                    "error": r.error,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in records
            ]
    except Exception as e:
        print(f"Failed to list CC sessions: {e}")
        return []
