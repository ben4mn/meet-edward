from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AllowedSender(BaseModel):
    identifier: str  # Phone number or email
    label: str  # Display name


class HeartbeatEventSchema(BaseModel):
    id: str
    source: str
    event_type: str
    sender: Optional[str] = None
    contact_name: Optional[str] = None
    chat_identifier: Optional[str] = None
    chat_name: Optional[str] = None
    summary: Optional[str] = None
    raw_data: Optional[dict] = None
    is_from_user: bool = False
    created_at: str
    triage_status: str = "pending"
    briefed: bool = False


class TriageCycleSchema(BaseModel):
    id: str
    cycle_number: int
    events_total: int = 0
    events_rule_filtered: int = 0
    events_dismissed: int = 0
    events_noted: int = 0
    events_acted: int = 0
    events_escalated: int = 0
    layer_reached: int = 1
    haiku_input_tokens: int = 0
    haiku_output_tokens: int = 0
    sonnet_wakes: int = 0
    duration_ms: int = 0
    summary: Optional[str] = None
    created_at: str


class HeartbeatConfigSchema(BaseModel):
    enabled: bool = True
    triage_interval_seconds: int = 900
    digest_token_cap: int = 800
    allowed_senders: list[AllowedSender] = []
    imessage_enabled: bool = True
    imessage_poll_seconds: int = 10
    calendar_enabled: bool = False
    calendar_poll_seconds: int = 300
    calendar_lookahead_minutes: int = 30
    email_enabled: bool = False
    email_poll_seconds: int = 300
    whatsapp_enabled: bool = False
    whatsapp_poll_seconds: int = 30


class HeartbeatConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    triage_interval_seconds: Optional[int] = None
    digest_token_cap: Optional[int] = None
    allowed_senders: Optional[list[AllowedSender]] = None
    imessage_enabled: Optional[bool] = None
    imessage_poll_seconds: Optional[int] = None
    calendar_enabled: Optional[bool] = None
    calendar_poll_seconds: Optional[int] = None
    calendar_lookahead_minutes: Optional[int] = None
    email_enabled: Optional[bool] = None
    email_poll_seconds: Optional[int] = None
    whatsapp_enabled: Optional[bool] = None
    whatsapp_poll_seconds: Optional[int] = None


class HeartbeatStatusSchema(BaseModel):
    running: bool = False
    enabled: bool = True
    triage_interval_seconds: int = 900
    pending_count: int = 0
    last_triage_at: Optional[str] = None
    next_triage_at: Optional[str] = None
    listener_status: str = "stopped"
    allowed_senders: list[AllowedSender] = []
    tracks: dict = {}
