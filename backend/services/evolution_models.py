"""Pydantic schemas for the evolution service."""

from typing import Optional
from pydantic import BaseModel


class EvolutionConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    min_interval_seconds: Optional[int] = None
    auto_trigger: Optional[bool] = None
    require_tests: Optional[bool] = None
    max_files_per_cycle: Optional[int] = None


class EvolutionTriggerRequest(BaseModel):
    description: str
    trigger: str = "manual"
