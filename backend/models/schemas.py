from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

from services.claude_routing import DEFAULT_ROUTES
from services.timezone_utils import default_timezone

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class Message(BaseModel):
    role: MessageRole
    content: str

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None

class ChatResponse(BaseModel):
    message: str
    conversation_id: str

# The default persona (settings.system_prompt on a fresh install). The
# owner is named with the literal token {OWNER}: compose_system_prompt
# (services/graph/streaming.py) renders it through services.owner.owner_text
# at compose time, so a change to EDWARD_OWNER_NAME applies without a
# database edit. Edit freely in Settings; keep the token where the name goes.
DEFAULT_SYSTEM_PROMPT = (
    "You are Edward, {OWNER}'s personal assistant, with a long memory. You run "
    "on {OWNER}'s Mac, keep an eye on their calendar and messages, hold the "
    "small threads of their days together, and act on their behalf. You are "
    "their colleague, not their app.\n\n"
    "Voice: dry, warm, first person, brief. Declarative, with a flash of wit "
    "when it lands — never reach for it. You don't hedge, fawn, moralize, or "
    "pad sentences. You don't offer to help — you help, then say what you did. "
    "Never apologetic for taking action.\n\n"
    "Stance: act first, explain after. If intent is obvious, do it. If you see "
    "a need coming — a follow-up, a nudge, a travel-time check — schedule it "
    "yourself without being asked. Initiative needs purpose: move when you "
    "add value, stay quiet when you don't.\n\n"
    "Standing context: the time, the date and the Recent Awareness block below "
    "reflect what happened while {OWNER} was away. Use them — don't pretend "
    "you're seeing things fresh."
)

# The default chat model: the Claude chat route (services/claude_routing).
DEFAULT_CHAT_MODEL = DEFAULT_ROUTES["chat"].model


class Settings(BaseModel):
    name: str = Field(default="Edward", description="The assistant's name")
    personality: str = Field(
        default="You are Edward, a helpful and friendly AI assistant.",
        description="The assistant's personality and behavior"
    )
    provider: str = Field(
        default="anthropic",
        description="LLM provider: anthropic (default) or openai (the Codex fallback)"
    )
    model: str = Field(
        default=DEFAULT_CHAT_MODEL,
        description="The model to use"
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Response creativity (0-1)"
    )
    system_prompt: str = Field(
        default=DEFAULT_SYSTEM_PROMPT,
        description="The system prompt sent to the LLM"
    )
    debug_streaming: bool = Field(
        default=False,
        description="When on, surface internal SSE events (memory matches, "
                    "background LLM activity) so the debug panel can show "
                    "what Edward is doing under the hood. Off by default to "
                    "keep the main UX quiet."
    )
    timezone: str = Field(
        # TIMEZONE from .env, else America/Chicago (timezone_utils.default_timezone).
        default_factory=default_timezone,
        description="IANA timezone (e.g. America/New_York). Single source of "
                    "truth for what Edward considers 'local' and how every "
                    "frontend surface renders timestamps."
    )

class SettingsUpdate(BaseModel):
    name: Optional[str] = None
    personality: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    system_prompt: Optional[str] = None
    debug_streaming: Optional[bool] = None
    timezone: Optional[str] = None


# Conversation schemas
class Conversation(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ConversationMessage(BaseModel):
    role: str
    content: str


class ConversationWithMessages(Conversation):
    messages: List[ConversationMessage]


# Skills schemas
class SkillStatus(str, Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    ERROR = "error"
    DISABLED = "disabled"


class SkillMetadata(BaseModel):
    phone_number: Optional[str] = None
    tools_count: Optional[int] = None


class Skill(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    status: SkillStatus
    status_message: Optional[str] = None
    metadata: Optional[SkillMetadata] = None


class SkillsResponse(BaseModel):
    skills: List[Skill]
    last_reload: Optional[str] = None


class SkillUpdateRequest(BaseModel):
    enabled: bool


# Scheduled events schemas
class ScheduledEvent(BaseModel):
    id: str
    conversation_id: Optional[str] = None
    description: str
    scheduled_at: str
    next_fire_at: str
    recurrence_pattern: Optional[str] = None
    status: str
    created_by: str
    delivery_channel: Optional[str] = None
    last_fired_at: Optional[str] = None
    fire_count: int = 0
    last_result: Optional[str] = None
    created_at: str
    updated_at: str
    # What kind of event it is (``mind_wake``, a goal check-in, a tick, the
    # pulse …) lives only here (2026-09-26); None for the owner's plain reminders.
    event_metadata: Optional[dict] = None
    event_type: Optional[str] = None


class ScheduledEventCreate(BaseModel):
    description: str
    scheduled_at: str  # ISO 8601 datetime
    recurrence_pattern: Optional[str] = None
    conversation_id: Optional[str] = None
    delivery_channel: Optional[str] = None
    created_by: str = "user"


class ScheduledEventUpdate(BaseModel):
    description: Optional[str] = None
    scheduled_at: Optional[str] = None
    recurrence_pattern: Optional[str] = None
    status: Optional[str] = None
    delivery_channel: Optional[str] = None
