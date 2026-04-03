from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

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

class Settings(BaseModel):
    name: str = Field(default="Edward", description="The assistant's name")
    personality: str = Field(
        default="You are Edward, a helpful and friendly AI assistant.",
        description="The assistant's personality and behavior"
    )
    model: str = Field(
        default="claude-sonnet-4-6",
        description="The Claude model to use"
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Response creativity (0-1)"
    )
    system_prompt: str = Field(
        default="You are Edward, a personal AI assistant who learns and grows. Be concise, helpful, and genuine. A tad cheeky when the moment calls for it.",
        description="The system prompt sent to Claude"
    )

class SettingsUpdate(BaseModel):
    name: Optional[str] = None
    personality: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    system_prompt: Optional[str] = None


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
