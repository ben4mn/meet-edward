"""
Self-evolution engine for Edward.

Manages the pipeline: branch -> code -> validate -> test -> review -> merge.
Uses Claude Code (via claude_code_service) for the coding and review steps.
Merging to main triggers uvicorn --reload automatically.
"""

import os
import json
import time
import uuid
import asyncio
import subprocess
from typing import Optional, Tuple, List
from datetime import datetime

from sqlalchemy import select, desc
from services.database import async_session, EvolutionConfigModel, EvolutionHistoryModel


# Project root (one level up from backend/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Protected paths that cannot be modified by evolution
PROTECTED_PATHS = [
    "backend/services/auth_service.py",
    "backend/routers/auth.py",
    "backend/services/evolution_service.py",
    "backend/services/evolution_models.py",
    ".env",
    ".env.local",
    ".env.production",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.prod.yml",
    ".git/",
]

# Global lock for single-cycle enforcement
_current_cycle: Optional[str] = None
_current_cycle_lock = asyncio.Lock()


# ============================================================================
# Config / Status
# ============================================================================

async def get_config() -> dict:
    """Read evolution config from DB."""
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionConfigModel).where(EvolutionConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()
        if not config:
            return {
                "enabled": False,
                "min_interval_seconds": 3600,
                "auto_trigger": False,
                "require_tests": True,
                "max_files_per_cycle": 20,
            }
        return {
            "enabled": config.enabled,
            "min_interval_seconds": config.min_interval_seconds,
            "auto_trigger": config.auto_trigger,
            "require_tests": config.require_tests,
            "max_files_per_cycle": config.max_files_per_cycle,
        }


async def update_config(updates: dict) -> dict:
    """Patch evolution config."""
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionConfigModel).where(EvolutionConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()
        if not config:
            config = EvolutionConfigModel(id="default")
            session.add(config)

        if updates.get("enabled") is not None:
            config.enabled = updates["enabled"]
        if updates.get("min_interval_seconds") is not None:
            config.min_interval_seconds = max(300, updates["min_interval_seconds"])
        if updates.get("auto_trigger") is not None:
            config.auto_trigger = updates["auto_trigger"]
        if updates.get("require_tests") is not None:
            config.require_tests = updates["require_tests"]
        if updates.get("max_files_per_cycle") is not None:
            config.max_files_per_cycle = max(1, min(50, updates["max_files_per_cycle"]))

        await session.commit()
        await session.refresh(config)

        return {
            "enabled": config.enabled,
            "min_interval_seconds": config.min_interval_seconds,
            "auto_trigger": config.auto_trigger,
            "require_tests": config.require_tests,
            "max_files_per_cycle": config.max_files_per_cycle,
        }


async def get_status() -> dict:
    """Get evolution status including config, active cycle, and last cycle time."""
    config = await get_config()

    # Get current cycle info
    current = None
    if _current_cycle:
        current = await _get_cycle(_current_cycle)

    # Get last completed cycle
    last_cycle_at = None
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionHistoryModel)
            .where(EvolutionHistoryModel.status.in_(["completed", "failed", "rolled_back"]))
            .order_by(desc(EvolutionHistoryModel.completed_at))
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last and last.completed_at:
            last_cycle_at = last.completed_at.isoformat()

    return {
        "config": config,
        "current_cycle": current,
        "last_cycle_at": last_cycle_at,
    }


async def get_history(limit: int = 20, offset: int = 0) -> list:
    """Query evolution history ordered by created_at desc."""
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionHistoryModel)
            .order_by(desc(EvolutionHistoryModel.created_at))
            .limit(limit)
            .offset(offset)
        )
        records = result.scalars().all()
        return [_cycle_to_dict(r) for r in records]


async def can_evolve() -> Tuple[bool, str]:
    """Check if evolution can proceed. Returns (ok, reason)."""
    config = await get_config()

    if not config["enabled"]:
        return False, "Evolution is disabled. Enable it via PATCH /api/evolution/config"

    if _current_cycle:
        return False, f"An evolution cycle is already active: {_current_cycle}"

    # Check rate limit
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionHistoryModel)
            .where(EvolutionHistoryModel.status.in_(["completed", "deploying"]))
            .order_by(desc(EvolutionHistoryModel.completed_at))
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last and last.completed_at:
            elapsed = (datetime.utcnow() - last.completed_at).total_seconds()
            if elapsed < config["min_interval_seconds"]:
                remaining = int(config["min_interval_seconds"] - elapsed)
                return False, f"Rate limited. Next cycle available in {remaining}s"

    return True, ""


# ============================================================================
# Evolution Pipeline
# ============================================================================

async def evolve(
    description: str,
    trigger: str = "manual",
    conversation_id: Optional[str] = None,
) -> str:
    """
    Run the full evolution pipeline.

    Returns a summary string suitable for LLM tool response.
    """
    global _current_cycle

    ok, reason = await can_evolve()
    if not ok:
        return f"Cannot evolve: {reason}"

    cycle_id = str(uuid.uuid4())
    branch_name = f"evolve/{cycle_id[:8]}"
    rollback_tag = f"pre-evolve-{cycle_id[:8]}"
    start_time = time.time()

    # Create history record
    async with async_session() as session:
        record = EvolutionHistoryModel(
            id=cycle_id,
            trigger=trigger,
            description=description,
            branch_name=branch_name,
            status="pending",
            step="decide",
            rollback_tag=rollback_tag,
            started_at=datetime.utcnow(),
        )
        session.add(record)
        await session.commit()

    async with _current_cycle_lock:
        _current_cycle = cycle_id

    try:
        # Send push notification for cycle start
        await _notify(f"Evolution started: {description[:60]}...", "Starting evolution cycle")

        # Step 1: BRANCH
        await _update_cycle(cycle_id, status="branching", step="branch")
        _git("tag", rollback_tag, "main")
        _git("checkout", "-b", branch_name, "main")

        # Step 2: CODE via Claude Code
        await _update_cycle(cycle_id, status="coding", step="code")
        cc_output = await _run_cc_for_evolution(description, cycle_id)

        # Step 3: VALIDATE — check for protected file modifications
        await _update_cycle(cycle_id, status="validating", step="validate")
        changed_files = _get_changed_files(branch_name)

        if not changed_files:
            await _cleanup_branch(branch_name, rollback_tag)
            await _update_cycle(
                cycle_id, status="failed", step="validate",
                error="No files were changed by Claude Code",
                duration_ms=_elapsed_ms(start_time),
            )
            return "Evolution cycle failed: Claude Code made no changes."

        # Check protected paths
        config = await get_config()
        violations = _check_protected_files(changed_files)
        if violations:
            await _cleanup_branch(branch_name, rollback_tag)
            error = f"Protected files modified: {', '.join(violations)}"
            await _update_cycle(
                cycle_id, status="failed", step="validate",
                error=error, duration_ms=_elapsed_ms(start_time),
            )
            return f"Evolution cycle failed: {error}"

        # Check max files
        if len(changed_files) > config["max_files_per_cycle"]:
            await _cleanup_branch(branch_name, rollback_tag)
            error = f"Too many files changed: {len(changed_files)} (max: {config['max_files_per_cycle']})"
            await _update_cycle(
                cycle_id, status="failed", step="validate",
                error=error, duration_ms=_elapsed_ms(start_time),
            )
            return f"Evolution cycle failed: {error}"

        await _update_cycle(cycle_id, files_changed=json.dumps(changed_files))

        # Step 4: TEST
        if config["require_tests"]:
            await _update_cycle(cycle_id, status="testing", step="test")
            test_ok, test_output = await _run_tests()
            await _update_cycle(cycle_id, test_output=test_output)

            if not test_ok:
                await _cleanup_branch(branch_name, rollback_tag)
                await _update_cycle(
                    cycle_id, status="failed", step="test",
                    error="Tests failed", duration_ms=_elapsed_ms(start_time),
                )
                return f"Evolution cycle failed: Tests failed.\n{test_output}"

        # Step 5: REVIEW
        await _update_cycle(cycle_id, status="reviewing", step="review")
        approved, review_summary = await _run_review(branch_name)
        await _update_cycle(cycle_id, review_summary=review_summary)

        if not approved:
            await _cleanup_branch(branch_name, rollback_tag)
            await _update_cycle(
                cycle_id, status="failed", step="review",
                error="Review rejected the changes",
                duration_ms=_elapsed_ms(start_time),
            )
            return f"Evolution cycle failed: Review rejected.\n{review_summary}"

        # Step 6: MERGE
        # CRITICAL: Save deploying status BEFORE merge (merge triggers reload)
        await _update_cycle(cycle_id, status="deploying", step="merge")

        _git("checkout", "main")
        _git("merge", "--no-ff", branch_name, "-m", f"evolve: {description[:80]}")

        # After merge, uvicorn --reload will restart the process.
        # Post-restart verification happens in check_pending_deploy().
        duration_ms = _elapsed_ms(start_time)
        await _update_cycle(cycle_id, duration_ms=duration_ms)

        return (
            f"Evolution cycle complete. Merged {len(changed_files)} file(s) to main.\n"
            f"Branch: {branch_name}\n"
            f"Files: {', '.join(changed_files)}\n"
            f"Review: {review_summary[:200]}\n"
            f"Duration: {duration_ms}ms\n"
            f"The server will auto-reload momentarily."
        )

    except Exception as e:
        error_msg = str(e)
        # Try to clean up
        try:
            _git("checkout", "main")
            _git("branch", "-D", branch_name)
        except Exception:
            pass

        await _update_cycle(
            cycle_id, status="failed", step="error",
            error=error_msg, duration_ms=_elapsed_ms(start_time),
        )
        return f"Evolution cycle failed: {error_msg}"
    finally:
        async with _current_cycle_lock:
            _current_cycle = None


async def check_pending_deploy() -> None:
    """
    Called during lifespan startup to finalize any 'deploying' cycles.

    If we find a cycle with status='deploying', the merge succeeded and
    uvicorn restarted — mark it as completed.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(EvolutionHistoryModel)
                .where(EvolutionHistoryModel.status == "deploying")
            )
            deploying = result.scalars().all()

            for cycle in deploying:
                cycle.status = "completed"
                cycle.step = "done"
                cycle.completed_at = datetime.utcnow()
                print(f"Evolution cycle {cycle.id} marked completed after restart")

                # Clean up branch
                try:
                    if cycle.branch_name:
                        _git("branch", "-D", cycle.branch_name)
                except Exception:
                    pass

            if deploying:
                await session.commit()
                # Send push notification
                for cycle in deploying:
                    await _notify(
                        f"Evolution deployed: {cycle.description[:60]}",
                        "Self-evolution cycle completed successfully"
                    )
    except Exception as e:
        print(f"Error checking pending deploys: {e}")


async def rollback(cycle_id: str) -> str:
    """Rollback a completed evolution cycle."""
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionHistoryModel).where(EvolutionHistoryModel.id == cycle_id)
        )
        cycle = result.scalar_one_or_none()

        if not cycle:
            return f"Cycle {cycle_id} not found."

        if cycle.status not in ("completed", "deploying"):
            return f"Cannot rollback cycle with status '{cycle.status}'. Only completed cycles can be rolled back."

        if not cycle.rollback_tag:
            return "No rollback tag found for this cycle."

        try:
            _git("reset", "--hard", cycle.rollback_tag)
            cycle.status = "rolled_back"
            cycle.step = "rolled_back"
            await session.commit()

            await _notify(
                f"Evolution rolled back: {cycle.description[:60]}",
                "Self-evolution cycle was rolled back"
            )

            return f"Rolled back to tag {cycle.rollback_tag}. Server will auto-reload."
        except Exception as e:
            return f"Rollback failed: {str(e)}"


# ============================================================================
# Internal helpers
# ============================================================================

def _git(*args: str) -> str:
    """Run a git command in the project root."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _get_changed_files(branch_name: str) -> List[str]:
    """Get list of files changed between main and the evolution branch."""
    output = _git("diff", "--name-only", f"main..{branch_name}")
    if not output:
        return []
    return [f.strip() for f in output.split("\n") if f.strip()]


def _check_protected_files(changed_files: List[str]) -> List[str]:
    """Check if any protected files were modified."""
    violations = []
    for f in changed_files:
        for protected in PROTECTED_PATHS:
            if protected.endswith("/"):
                if f.startswith(protected):
                    violations.append(f)
            else:
                if f == protected:
                    violations.append(f)
    return violations


def _cleanup_branch(branch_name: str, rollback_tag: str) -> None:
    """Clean up a failed evolution branch."""
    try:
        _git("checkout", "main")
        _git("branch", "-D", branch_name)
        _git("tag", "-d", rollback_tag)
    except Exception as e:
        print(f"Branch cleanup warning: {e}")


def _elapsed_ms(start_time: float) -> int:
    return int((time.time() - start_time) * 1000)


async def _update_cycle(cycle_id: str, **kwargs) -> None:
    """Update fields on an evolution history record."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(EvolutionHistoryModel).where(EvolutionHistoryModel.id == cycle_id)
            )
            record = result.scalar_one_or_none()
            if record:
                for key, value in kwargs.items():
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                if kwargs.get("status") in ("completed", "failed", "rolled_back"):
                    record.completed_at = datetime.utcnow()
                await session.commit()
    except Exception as e:
        print(f"Failed to update evolution cycle {cycle_id}: {e}")


async def _get_cycle(cycle_id: str) -> Optional[dict]:
    """Get a single cycle record."""
    async with async_session() as session:
        result = await session.execute(
            select(EvolutionHistoryModel).where(EvolutionHistoryModel.id == cycle_id)
        )
        record = result.scalar_one_or_none()
        if record:
            return _cycle_to_dict(record)
    return None


def _cycle_to_dict(record) -> dict:
    """Convert a DB record to a dict."""
    return {
        "id": record.id,
        "trigger": record.trigger,
        "description": record.description,
        "branch_name": record.branch_name,
        "status": record.status,
        "step": record.step,
        "files_changed": json.loads(record.files_changed) if record.files_changed else [],
        "test_output": record.test_output,
        "review_summary": record.review_summary,
        "error": record.error,
        "rollback_tag": record.rollback_tag,
        "cc_session_id": record.cc_session_id,
        "duration_ms": record.duration_ms,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


async def _run_cc_for_evolution(description: str, cycle_id: str) -> str:
    """Run Claude Code for the coding step."""
    from services.claude_code_service import run_claude_code

    system_prompt = f"""You are modifying the Edward AI assistant codebase.

IMPORTANT RULES:
1. You MUST NOT modify these protected files:
   {json.dumps(PROTECTED_PATHS)}
2. Follow the existing code patterns and conventions in the codebase.
3. Make minimal, focused changes to accomplish the task.
4. Ensure all Python code is syntactically valid.
5. Do not add unnecessary dependencies.

The project root is the working directory. The backend is in backend/ and frontend is in frontend/.
"""

    output_parts = []
    async for event in run_claude_code(
        task=description,
        cwd=PROJECT_ROOT,
        system_prompt=system_prompt,
        max_turns=25,
    ):
        event_type = event.get("event_type")
        if event_type == "cc_text":
            output_parts.append(event.get("text", ""))
        elif event_type == "cc_error":
            raise RuntimeError(f"Claude Code error: {event.get('error')}")
        elif event_type == "cc_started":
            cc_session_id = event.get("session_id")
            await _update_cycle(cycle_id, cc_session_id=cc_session_id)

    return "\n".join(output_parts)


async def _run_tests() -> Tuple[bool, str]:
    """Run validation tests. Returns (passed, output)."""
    outputs = []

    # Test 1: Backend import check
    try:
        result = subprocess.run(
            ["python", "-c", "from main import app"],
            cwd=os.path.join(PROJECT_ROOT, "backend"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            outputs.append(f"Backend import FAILED:\n{result.stderr}")
            return False, "\n".join(outputs)
        outputs.append("Backend import: OK")
    except Exception as e:
        outputs.append(f"Backend import check error: {e}")
        return False, "\n".join(outputs)

    # Test 2: Frontend lint
    try:
        result = subprocess.run(
            ["npm", "run", "lint"],
            cwd=os.path.join(PROJECT_ROOT, "frontend"),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            outputs.append(f"Frontend lint FAILED:\n{result.stdout}\n{result.stderr}")
            return False, "\n".join(outputs)
        outputs.append("Frontend lint: OK")
    except FileNotFoundError:
        outputs.append("Frontend lint: SKIPPED (npm not found)")
    except Exception as e:
        outputs.append(f"Frontend lint error: {e}")

    return True, "\n".join(outputs)


async def _run_review(branch_name: str) -> Tuple[bool, str]:
    """Run a review of the diff using Claude Code (read-only)."""
    from services.claude_code_service import run_claude_code

    diff = _git("diff", f"main..{branch_name}")
    if not diff:
        return False, "No diff to review"

    # Truncate very large diffs
    if len(diff) > 20000:
        diff = diff[:20000] + "\n...[diff truncated]"

    review_prompt = f"""Review this git diff for an AI assistant codebase (Edward).

Check for:
1. Security issues (injection, auth bypass, data leaks)
2. Breaking changes to existing functionality
3. Code quality (reasonable patterns, no obvious bugs)
4. Protected files should NOT be modified: {json.dumps(PROTECTED_PATHS)}

If the changes look safe and reasonable, output EXACTLY the word "APPROVE" on its own line.
If there are concerns, explain them and do NOT output "APPROVE".

Here is the diff:

```
{diff}
```"""

    output_parts = []
    async for event in run_claude_code(
        task=review_prompt,
        cwd=PROJECT_ROOT,
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=5,
    ):
        if event.get("event_type") == "cc_text":
            output_parts.append(event.get("text", ""))
        elif event.get("event_type") == "cc_error":
            return False, f"Review error: {event.get('error')}"

    review_text = "\n".join(output_parts)
    approved = "APPROVE" in review_text
    return approved, review_text


async def _notify(title: str, body: str) -> None:
    """Send push notification (best-effort)."""
    try:
        from services.push_service import send_push_notification, is_configured
        if is_configured():
            await send_push_notification(title=title, body=body, url="/settings#evolution")
    except Exception:
        pass
