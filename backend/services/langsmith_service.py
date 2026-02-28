"""
LangSmith integration service for trace inspection.

Provides functions to query LangSmith for conversation traces,
enabling the debug panel to show per-node timing and token usage.

LangGraph creates separate root-level traces for each operation (LLM call,
tool call, state update) rather than nesting them. We find runs tagged with
thread_id metadata, then use their timestamps to find all related runs.
"""

import os
import logging
from typing import Optional
from datetime import timedelta

logger = logging.getLogger(__name__)

_client = None
_checked = False


def get_client():
    """Get or create a lazy singleton LangSmith client. Returns None if not configured."""
    global _client, _checked
    if _checked:
        return _client
    _checked = True

    api_key = os.environ.get("LANGCHAIN_API_KEY")
    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() == "true"

    if not api_key or not tracing:
        logger.info("LangSmith not configured (missing LANGCHAIN_API_KEY or LANGCHAIN_TRACING_V2)")
        return None

    try:
        from langsmith import Client
        _client = Client()
        logger.info("LangSmith client initialized")
        return _client
    except Exception as e:
        logger.warning(f"Failed to initialize LangSmith client: {e}")
        return None


def is_configured() -> bool:
    """Check if LangSmith is configured and available."""
    return get_client() is not None


def _serialize_run(run) -> dict:
    """Convert a LangSmith Run object to a JSON-safe dict."""
    latency_ms = None
    if run.end_time and run.start_time:
        latency_ms = int((run.end_time - run.start_time).total_seconds() * 1000)

    token_usage = {}
    if run.total_tokens:
        token_usage["total"] = run.total_tokens
    if run.prompt_tokens:
        token_usage["prompt"] = run.prompt_tokens
    if run.completion_tokens:
        token_usage["completion"] = run.completion_tokens

    langsmith_url = None
    if run.trace_id and run.session_id:
        langsmith_url = f"https://smith.langchain.com/public/{run.session_id}/r/{run.id}?trace={run.trace_id}"

    return {
        "id": str(run.id),
        "trace_id": str(run.trace_id) if run.trace_id else None,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "name": run.name,
        "run_type": run.run_type,
        "status": run.status,
        "error": run.error,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "end_time": run.end_time.isoformat() if run.end_time else None,
        "latency_ms": latency_ms,
        "token_usage": token_usage if token_usage else None,
        "langsmith_url": langsmith_url,
    }


def _get_project_name() -> str:
    return os.environ.get("LANGCHAIN_PROJECT", "default")


def _get_tagged_runs(client, project: str, conversation_id: str, limit: int = 50) -> list:
    """Get runs tagged with thread_id matching this conversation."""
    return list(client.list_runs(
        project_name=project,
        filter=f'and(in(metadata_key, ["thread_id"]), eq(metadata_value, "{conversation_id}"))',
        limit=limit,
    ))


def _group_into_turns(tagged_runs: list) -> list[tuple]:
    """
    Group tagged runs into conversation turns based on timestamp proximity.

    Returns list of (turn_start, turn_end) datetime tuples, most recent first.
    Tagged runs within 60s of each other belong to the same turn.
    """
    if not tagged_runs:
        return []

    tagged_runs.sort(key=lambda r: r.start_time)

    turns = []
    current_start = tagged_runs[0].start_time
    current_end = tagged_runs[0].end_time or tagged_runs[0].start_time

    for run in tagged_runs[1:]:
        if run.start_time - current_end > timedelta(seconds=60):
            turns.append((current_start, current_end))
            current_start = run.start_time
            current_end = run.end_time or run.start_time
        else:
            current_end = max(current_end, run.end_time or run.start_time)

    turns.append((current_start, current_end))
    turns.reverse()  # Most recent first
    return turns


def _get_runs_in_window(client, project: str, start: 'datetime', end: 'datetime') -> list:
    """Get all root runs within a time window, with padding."""
    padded_start = start - timedelta(seconds=120)
    padded_end = end + timedelta(seconds=10)

    all_runs = list(client.list_runs(
        project_name=project,
        is_root=True,
        start_time=padded_start,
        limit=100,
    ))

    # Filter to runs that actually fall within the padded window
    return [r for r in all_runs if r.start_time and r.start_time <= padded_end]


def get_traces_for_conversation(conversation_id: str, limit: int = 10) -> list[dict]:
    """
    Get conversation turns as trace summaries.

    Each "trace" represents one conversation turn (user message -> response).
    """
    client = get_client()
    if not client:
        return []

    try:
        project = _get_project_name()
        tagged = _get_tagged_runs(client, project, conversation_id)
        turns = _group_into_turns(tagged)[:limit]

        results = []
        for turn_start, turn_end in turns:
            from datetime import datetime
            total_ms = int((turn_end - turn_start).total_seconds() * 1000)
            results.append({
                "id": turn_start.isoformat(),
                "trace_id": turn_start.isoformat(),
                "parent_run_id": None,
                "name": "conversation_turn",
                "run_type": "chain",
                "status": "success",
                "error": None,
                "start_time": turn_start.isoformat(),
                "end_time": turn_end.isoformat(),
                "latency_ms": total_ms,
                "token_usage": None,
                "langsmith_url": None,
            })

        return results
    except Exception as e:
        logger.error(f"Failed to get traces for conversation {conversation_id}: {e}")
        return []


def get_trace_detail(trace_id: str) -> list[dict]:
    """
    Get all runs for a conversation turn identified by its start_time ISO string.

    Since LangGraph creates flat root-level traces, we use the time window
    to find all related runs.
    """
    client = get_client()
    if not client:
        return []

    try:
        from datetime import datetime
        project = _get_project_name()

        # trace_id is actually a start_time ISO string from get_traces_for_conversation
        turn_start = datetime.fromisoformat(trace_id)
        # Fetch a wide window and let the caller handle grouping
        turn_end = turn_start + timedelta(seconds=120)

        runs = _get_runs_in_window(client, project, turn_start, turn_end)
        result = [_serialize_run(r) for r in runs]
        result.sort(key=lambda r: r["start_time"] or "")
        return result
    except Exception as e:
        logger.error(f"Failed to get trace detail for {trace_id}: {e}")
        return []


def get_latest_trace(conversation_id: str) -> Optional[dict]:
    """Get the most recent conversation turn with all related runs."""
    client = get_client()
    if not client:
        return None

    try:
        project = _get_project_name()
        tagged = _get_tagged_runs(client, project, conversation_id)
        turns = _group_into_turns(tagged)

        if not turns:
            return None

        turn_start, turn_end = turns[0]  # Most recent
        runs_raw = _get_runs_in_window(client, project, turn_start, turn_end)
        runs = [_serialize_run(r) for r in runs_raw]
        runs.sort(key=lambda r: r["start_time"] or "")

        # Compute total latency
        start_times = [r["start_time"] for r in runs if r["start_time"]]
        end_times = [r["end_time"] for r in runs if r["end_time"]]

        from datetime import datetime
        total_ms = None
        if start_times and end_times:
            first = datetime.fromisoformat(min(start_times))
            last = datetime.fromisoformat(max(end_times))
            total_ms = int((last - first).total_seconds() * 1000)

        # Total tokens across all runs
        total_tokens = sum(
            (r["token_usage"].get("total", 0) if r["token_usage"] else 0)
            for r in runs
        )

        root = {
            "id": turn_start.isoformat(),
            "trace_id": turn_start.isoformat(),
            "parent_run_id": None,
            "name": "conversation_turn",
            "run_type": "chain",
            "status": "success",
            "error": None,
            "start_time": turn_start.isoformat(),
            "end_time": turn_end.isoformat(),
            "latency_ms": total_ms,
            "token_usage": {"total": total_tokens} if total_tokens else None,
            "langsmith_url": None,
        }

        return {
            "root": root,
            "runs": runs,
        }
    except Exception as e:
        logger.error(f"Failed to get latest trace for {conversation_id}: {e}")
        return None
