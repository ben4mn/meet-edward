"""Pydantic schemas for the orchestrator system."""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class OrchestratorConfigResponse(BaseModel):
    enabled: bool = False
    max_concurrent_workers: int = 5
    max_concurrent_cc_sessions: int = 2
    default_worker_model: str = "claude-haiku-4-5-20251001"
    default_worker_timeout: int = 300


class OrchestratorConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_concurrent_workers: Optional[int] = None
    max_concurrent_cc_sessions: Optional[int] = None
    default_worker_model: Optional[str] = None
    default_worker_timeout: Optional[int] = None


class OrchestratorTaskResponse(BaseModel):
    id: str
    parent_conversation_id: str
    worker_conversation_id: Optional[str] = None
    task_description: str
    task_type: str = "internal_worker"
    model: Optional[str] = "claude-haiku-4-5-20251001"
    status: str = "pending"
    context_mode: str = "scoped"
    result_summary: Optional[str] = None
    error: Optional[str] = None
    timeout_seconds: int = 300
    cc_session_id: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


class OrchestratorStatusResponse(BaseModel):
    config: OrchestratorConfigResponse
    active_count: int = 0
    recent_tasks: List[OrchestratorTaskResponse] = []


class WorkerMessageRequest(BaseModel):
    message: str
