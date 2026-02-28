from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ConsolidationStatusSchema(BaseModel):
    running: bool = False
    enabled: bool = False
    interval_seconds: int = 3600
    lookback_hours: int = 2
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    cycle_count: int = 0
    total_connections: int = 0
    total_flags: int = 0


class ConsolidationConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = None
    lookback_hours: Optional[int] = None


class ConsolidationCycleSchema(BaseModel):
    id: str
    memories_reviewed: int = 0
    clusters_found: int = 0
    connections_created: int = 0
    flags_created: int = 0
    contradictions_found: int = 0
    haiku_calls: int = 0
    duration_ms: int = 0
    created_at: str


class MemoryConnectionSchema(BaseModel):
    id: str
    memory_id_a: str
    memory_id_b: str
    connection_type: str
    strength: float = 0.5
    created_at: str


class MemoryFlagSchema(BaseModel):
    id: str
    memory_id: str
    flag_type: str
    description: str
    related_memory_id: Optional[str] = None
    resolved: bool = False
    resolved_at: Optional[str] = None
    created_at: str
