from pydantic import BaseModel
from typing import Literal, Optional
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
    max_cluster_size: int = 20
    max_pairs_per_cluster: int = 30
    max_llm_calls_per_cycle: int = 15


class ConsolidationConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = None
    lookback_hours: Optional[int] = None
    max_cluster_size: Optional[int] = None
    max_pairs_per_cluster: Optional[int] = None
    max_llm_calls_per_cycle: Optional[int] = None


class FlagResolveUpdate(BaseModel):
    resolved: bool


class FlagKeepUpdate(BaseModel):
    """The owner's call on an open contradiction: keep memory ``a`` (``memory_id``)
    or ``b`` (``related_memory_id``); the other is retired."""
    keep: Literal["a", "b"]


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
    # Filled when the list is requested with ``with_content=true``: the
    # Dreaming panel shows a connection as its two memories.
    memory_a_content: Optional[str] = None
    memory_b_content: Optional[str] = None


class MemoryFlagSchema(BaseModel):
    id: str
    memory_id: str
    flag_type: str
    description: str
    related_memory_id: Optional[str] = None
    resolved: bool = False
    resolved_at: Optional[str] = None
    created_at: str
    # Filled when the list is requested with ``with_content=true`` so the
    # Dreams panel can show both sides of a contradiction.
    memory_content: Optional[str] = None
    related_memory_content: Optional[str] = None
