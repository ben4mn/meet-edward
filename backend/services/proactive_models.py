"""Pydantic schemas for the proactive intelligence system."""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ProactiveConfigSchema(BaseModel):
    """What the owner still configures here. The retired jobs' fields (morning briefing,
    meeting prep, pattern detection, open-loop nudge, relationship
    maintenance) and ``extraction_depth`` (read by nothing) are gone with
    the jobs (Phase 7); the columns stay until the tables are folded."""
    dossier_enabled: bool = True
    dossier_min_acted_pushes: int = 2
    dossier_min_inbound_threads: int = 3
    dossier_window_days: int = 7


class ProactiveConfigUpdate(BaseModel):
    dossier_enabled: Optional[bool] = None
    dossier_min_acted_pushes: Optional[int] = None
    dossier_min_inbound_threads: Optional[int] = None
    dossier_window_days: Optional[int] = None


class OpenLoopSchema(BaseModel):
    id: str
    description: str
    source_memory_id: Optional[str] = None
    source_conversation_id: Optional[str] = None
    entity_id: Optional[str] = None
    status: str = "open"
    priority: float = 0.5
    detected_at: Optional[str] = None
    last_nudged_at: Optional[str] = None
    resolved_at: Optional[str] = None
    snooze_until: Optional[str] = None
    nudge_count: int = 0


class OpenLoopUpdate(BaseModel):
    status: Optional[str] = None  # "resolved" or "snoozed"
    snooze_until: Optional[str] = None  # ISO datetime for snooze


class WakeSchema(BaseModel):
    kind: str
    cron: str


class ProactiveStatusSchema(BaseModel):
    config: ProactiveConfigSchema
    entity_count: int = 0
    open_loop_count: int = 0
    # The wakes Edward takes on his own (scheduler_service.WAKEUP_TICKS).
    wakes: List[WakeSchema] = []
