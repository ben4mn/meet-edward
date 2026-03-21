"""Pydantic schemas for the proactive intelligence system."""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ProactiveConfigSchema(BaseModel):
    morning_briefing_enabled: bool = False
    morning_briefing_time: str = "08:00"
    morning_briefing_channel: str = "push"
    meeting_prep_enabled: bool = False
    meeting_prep_advance_minutes: int = 30
    pattern_detection_enabled: bool = False
    open_loop_nudge_enabled: bool = False
    open_loop_nudge_days: int = 7
    extraction_depth: str = "standard"


class ProactiveConfigUpdate(BaseModel):
    morning_briefing_enabled: Optional[bool] = None
    morning_briefing_time: Optional[str] = None
    morning_briefing_channel: Optional[str] = None
    meeting_prep_enabled: Optional[bool] = None
    meeting_prep_advance_minutes: Optional[int] = None
    pattern_detection_enabled: Optional[bool] = None
    open_loop_nudge_enabled: Optional[bool] = None
    open_loop_nudge_days: Optional[int] = None
    extraction_depth: Optional[str] = None


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


class ProactiveStatusSchema(BaseModel):
    config: ProactiveConfigSchema
    entity_count: int = 0
    open_loop_count: int = 0
    morning_briefing_running: bool = False
    meeting_prep_running: bool = False
    pattern_detection_running: bool = False
    open_loop_nudge_running: bool = False
