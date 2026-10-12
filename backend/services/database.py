import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, Float, Text, DateTime, Integer, BigInteger, Boolean, func, Index, Time, text, Numeric
from pgvector.sqlalchemy import Vector

from models.schemas import DEFAULT_CHAT_MODEL, DEFAULT_SYSTEM_PROMPT
from services.timezone_utils import FALLBACK_TZ, default_timezone

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://edward:edward@localhost:5432/edward"
)

# Convert postgresql:// to postgresql+asyncpg:// if needed
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Per-connection server_settings:
# - timezone=UTC: our `created_at` columns are TIMESTAMP WITHOUT TIME ZONE,
#   so `server_default=func.now()` is cast through the session's TimeZone
#   setting. Without this pin, rows stored while the cluster was set to
#   America/Chicago end up holding local wall-clock time mislabelled as
#   UTC by the API serializer (iMessage events rendered 5h off).
# - tcp_keepalives_*: on 2026-05-22 a crashed backend left ~15 sessions
#   pinned to heartbeat_config for 4 days because Postgres never noticed
#   the dead client. With keepalives the server probes idle sockets after
#   60s and reaps within ~120s of an unresponsive client.
CONNECT_ARGS = {
    "server_settings": {
        "timezone": "UTC",
        "tcp_keepalives_idle": "60",
        "tcp_keepalives_interval": "10",
        "tcp_keepalives_count": "6",
    }
}

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args=CONNECT_ARGS,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class SettingsModel(Base):
    __tablename__ = "settings"

    id = Column(String, primary_key=True, default="default")
    name = Column(String, default="Edward")
    personality = Column(Text, default="You are Edward, a helpful and friendly AI assistant.")
    model = Column(String, default=DEFAULT_CHAT_MODEL)
    temperature = Column(Float, default=0.7)
    # The default persona names the owner with the token {OWNER}, rendered
    # at compose time (graph/streaming.compose_system_prompt): the name itself
    # is never stored.
    system_prompt = Column(Text, default=DEFAULT_SYSTEM_PROMPT)
    provider = Column(String, default="anthropic")  # "anthropic" or "openai" (the Codex fallback)
    password_hash = Column(String, nullable=True)  # Hashed password for web authentication
    debug_streaming = Column(Boolean, default=False, server_default="false", nullable=False)
    # A fresh install's row takes TIMEZONE from .env (setup.sh writes it),
    # else America/Chicago (``timezone_utils.default_timezone``); the
    # server default only fills rows that predate the column.
    timezone = Column(String, default=default_timezone, server_default=FALLBACK_TZ, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ConversationModel(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    title = Column(String, default="New Conversation")
    source = Column(String, default="user")  # "user" | "scheduled_event" | "external_message" | "edward_thought" | ...
    channel = Column(String, default="text")  # "text" | "voice"
    notified_user = Column(Boolean, default=False)  # True if Edward sent push/message to get attention
    search_tags = Column(Text, nullable=True)  # Comma-separated keywords for FTS
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    message_count = Column(Integer, default=0)
    # For Edward-initiated proactive pushes (source="edward_thought"): the
    # narrative he wrote into the push body, surfaced as a synthetic first
    # assistant message in the chat UI so the user can read it and reply.
    initial_message = Column(Text, nullable=True)
    # JSON-encoded signal pack from the wake-up tick (or other context
    # carrier) so that the owner's first reply turn can recover what Edward was
    # actually thinking about. Without it the conversation is amnesiac
    # after the opener.
    context_pack = Column(Text, nullable=True)


class MemoryModel(Base):
    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=True)  # all-MiniLM-L6-v2 produces 384-dim vectors
    memory_type = Column(String, nullable=False)  # fact, preference, context, instruction
    importance = Column(Float, default=0.5)  # 0-1 scale
    source_conversation_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())  # Track modifications
    last_accessed = Column(DateTime, nullable=True)  # Set on first actual access
    access_count = Column(Integer, default=0)  # Times retrieval RETURNED this
    # "Returned by retrieval" and "actually shown to the model" are different
    # questions. access_count answers the first and feeds the decay curve;
    # used_count answers the second and feeds the tier ladder.
    used_count = Column(Integer, default=0, nullable=False, server_default="0")
    last_used_at = Column(DateTime, nullable=True)
    temporal_nature = Column(String, default="timeless")  # timeless, temporary, evolving
    tier = Column(String, default="observation")  # observation, belief, knowledge
    reinforcement_count = Column(Integer, default=0)
    source_type = Column(String, default="inferred")  # user_stated, inferred, tool_derived, imported
    confidence = Column(Float, default=0.5)  # 0-1 scale, independent of importance
    user_id = Column(String, nullable=True, index=True)  # For future multi-user support
    valid_from = Column(DateTime, nullable=True)  # When the belief began (NULL = from created_at)
    valid_until = Column(DateTime, nullable=True)  # When the belief was retired (NULL = still valid)
    # Mind rebuild Phase 1 (2026-09-18): one store for what Edward heard,
    # read, thought and did. ``origin`` says how the memory came to be;
    # ``about`` says whom it concerns. Both are orthogonal to ``tier``.
    origin = Column(String, nullable=False, default="heard", server_default="heard")  # heard, read, thought, did
    about = Column(String, nullable=False, default="owner", server_default="owner")  # owner, contact, self, world
    # --- provenance (2026-09-24) ---
    # Where the row came from, as a small JSON (services/provenance_service.py):
    # the turn, the extraction profile, event ids / chat identifiers when the
    # caller knew them. ``how_do_i_know`` answers from this, never from narration.
    source_ref = Column(Text, nullable=True)

    __table_args__ = (
        # Index for vector similarity search (using HNSW for fast approximate search)
        Index('ix_memories_embedding', 'embedding', postgresql_using='hnsw',
              postgresql_with={'m': 16, 'ef_construction': 64},
              postgresql_ops={'embedding': 'vector_cosine_ops'}),
        # GIN index for full-text search on content
        Index('ix_memories_content_fts', text("to_tsvector('english', content)"),
              postgresql_using='gin'),
        # Retrieval filters frequently on valid_until — partial index for the
        # common case (valid memories, i.e. NULL or future).
        Index('ix_memories_valid_until', 'valid_until'),
    )


class DocumentModel(Base):
    """Persistent document store for full documents (recipes, notes, reference guides, etc.)."""
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, nullable=True)  # Comma-separated: "work,reference,api"
    embedding = Column(Vector(384), nullable=True)
    source_conversation_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_accessed = Column(DateTime, server_default=func.now())
    access_count = Column(Integer, default=0)
    user_id = Column(String, nullable=True, index=True)

    __table_args__ = (
        Index('ix_documents_embedding', 'embedding', postgresql_using='hnsw',
              postgresql_with={'m': 16, 'ef_construction': 64},
              postgresql_ops={'embedding': 'vector_cosine_ops'}),
        Index('ix_documents_content_fts',
              text("to_tsvector('english', title || ' ' || content)"),
              postgresql_using='gin'),
    )


class ExternalContactModel(Base):
    """External contacts for SMS/messaging conversations."""
    __tablename__ = "external_contacts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    phone_number = Column(String, unique=True, nullable=False, index=True)
    conversation_id = Column(String, nullable=False)  # References conversations table
    contact_name = Column(String, nullable=True)  # User can set a friendly name
    platform = Column(String, default="sms")  # sms, whatsapp, etc.
    last_channel = Column(String, default="sms")  # sms or whatsapp — tracks which channel to reply on
    created_at = Column(DateTime, server_default=func.now())
    last_contacted = Column(DateTime, server_default=func.now())


class ScheduledEventModel(Base):
    """Scheduled events for reminders, messages, and self-assigned tasks."""
    __tablename__ = "scheduled_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, nullable=True)  # Which conversation to execute in
    description = Column(Text, nullable=False)  # What Edward should do
    scheduled_at = Column(DateTime, nullable=False)  # Original scheduled time
    next_fire_at = Column(DateTime, nullable=False, index=True)  # Next execution time
    recurrence_pattern = Column(String, nullable=True)  # Cron string for recurring events
    status = Column(String, default="pending")  # pending/processing/completed/cancelled/failed
    created_by = Column(String, default="edward")  # "user" or "edward"
    delivery_channel = Column(String, nullable=True)  # sms/imessage/null for auto
    last_fired_at = Column(DateTime, nullable=True)
    fire_count = Column(Integer, default=0)
    last_result = Column(Text, nullable=True)
    event_metadata = Column(Text, nullable=True)  # JSON text for extra data
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SkillModel(Base):
    """Skills/integrations configuration and status."""
    __tablename__ = "skills"

    id = Column(String, primary_key=True)  # 'imessage_applescript', 'twilio_sms', etc.
    enabled = Column(Boolean, default=False)
    last_error = Column(Text, nullable=True)  # Last error message if any
    last_connected_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FileModel(Base):
    """Persistent file storage for uploads, generated artifacts, and processed files."""
    __tablename__ = "files"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(512), nullable=False)  # Relative to FILE_STORAGE_ROOT
    mime_type = Column(String(127), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    category = Column(String(50), default="general")  # upload, generated, artifact, processed, general
    description = Column(Text, nullable=True)
    tags = Column(String, nullable=True)  # Comma-separated
    source = Column(String(50), default="user")  # user, edward, sandbox
    source_conversation_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_accessed = Column(DateTime, server_default=func.now())
    access_count = Column(Integer, default=0)

    __table_args__ = (
        Index('ix_files_category', 'category'),
        Index('ix_files_source', 'source'),
        Index('ix_files_created_at', 'created_at'),
    )


class WidgetStateModel(Base):
    """Single-row table storing current widget content for iOS Scriptable widget."""
    __tablename__ = "widget_state"

    id = Column(String, primary_key=True, default="default")
    title = Column(String, default="Edward")
    subtitle = Column(String, nullable=True)
    theme = Column(Text, nullable=True)  # JSON: {backgroundColor, textColor, secondaryTextColor, accentColor}
    sections = Column(Text, nullable=True)  # JSON array of section objects
    script = Column(Text, nullable=True)  # Raw Scriptable JS code (overrides structured data when set)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String, default="system")  # "edward" or "system"


class WidgetTokenModel(Base):
    """API tokens for Scriptable widget access."""
    __tablename__ = "widget_tokens"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    token = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)


class PushSubscriptionModel(Base):
    """Web Push notification subscriptions."""
    __tablename__ = "push_subscriptions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    endpoint = Column(String, unique=True, nullable=False)  # Push service URL
    p256dh_key = Column(String, nullable=False)  # Client public key
    auth_key = Column(String, nullable=False)  # Shared auth secret
    user_agent = Column(String, nullable=True)
    # Session E (2026-09-02): device identity so a push can be *targeted*.
    device_id = Column(String(120), nullable=True)
    device_name = Column(String(120), nullable=True)
    kind = Column(String(20), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)  # For debugging
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, server_default=func.now())
    failed_count = Column(Integer, default=0)  # Track failed deliveries
    is_active = Column(Boolean, default=True)  # Soft disable on repeated failures


class PushLogModel(Base):
    """The push ledger (2026-09-12): one row per notification sent to the
    owner's phone, whichever code path sent it, opened when they tap it or open the
    conversation it pointed at. See services/push_ledger.py."""
    __tablename__ = "push_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sent_at = Column(DateTime, server_default=func.now(), index=True)
    source = Column(String(40), nullable=False, default="unknown")
    title = Column(String(200), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    url = Column(String, nullable=True)
    tag = Column(String(120), nullable=True)
    conversation_id = Column(String, nullable=True, index=True)
    delivery_id = Column(String, nullable=True, index=True)
    sent = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    held_reason = Column(String(40), nullable=True)
    opened_at = Column(DateTime, nullable=True)
    open_source = Column(String(20), nullable=True)
    # "Snooze 1h" on the notification: re-sent once when snoozed_until passes.
    snoozed_until = Column(DateTime, nullable=True, index=True)
    resent_at = Column(DateTime, nullable=True)


class EdwardNoteModel(Base):
    """Edward's notes (2026-09-12): what the templated services have to say,
    as inputs for Today and the wake-up ticks rather than phone pushes.
    See services/notes_service.py."""
    __tablename__ = "edward_notes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String(40), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    url = Column(String, nullable=True)
    needs_you = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=True)
    # The owner pressed "Done" on it (2026-10-11): a dismissal, not an expiry —
    # the noticing ladder reads it (services/noticing_service.py).
    dismissed_at = Column(DateTime, nullable=True)


def _utcnow_naive():
    """Naive UTC, the column convention (every timestamp stamped from Python)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


class NoticingModel(Base):
    """One private noticing (2026-10-11): something in the owner's messages
    that could matter to them, a code-made security or care item, and what the
    pure gate decided about it. See services/noticing_service.py and
    services/speak_gate.py. Naive UTC timestamps stamped from Python; the
    ``claim`` never reaches a glanceable surface. Pruned at 30 days."""
    __tablename__ = "noticings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, nullable=False, default=lambda: _utcnow_naive(), server_default=func.now())
    updated_at = Column(DateTime, nullable=True)
    origin = Column(String(16), nullable=False, default="noticer")      # noticer | care | code | sweep
    pass_id = Column(String(36), nullable=True)
    kind = Column(String(24), nullable=False)       # deadline|conflict|unanswered|security|household|follow_through
    category = Column(String(24), nullable=True)
    claim = Column(Text, nullable=False)
    sensitive = Column(Boolean, nullable=False, default=False)
    sources_json = Column(Text, nullable=False, default="[]")
    chat_identifier = Column(String(255), nullable=True)
    who_json = Column(Text, nullable=True)
    dedupe_key = Column(String(160), nullable=True)
    closes_at = Column(DateTime, nullable=True)
    suggested = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, default="open")  # open|held|shadow|silent|spoken|dropped|failed|expired
    due_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    decision = Column(String(16), nullable=True)
    decision_reason = Column(Text, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    floor_push = Column(Boolean, nullable=False, default=False)
    surfaced_at = Column(DateTime, nullable=True)
    channel = Column(String(16), nullable=True)
    push_log_id = Column(String(36), nullable=True)
    note_id = Column(String(36), nullable=True)
    thought_id = Column(String(36), nullable=True)
    ledger_id = Column(Integer, nullable=True)
    outcome = Column(String(16), nullable=True)     # opened|acted|seen|dismissed|ignored|rejected
    outcome_at = Column(DateTime, nullable=True)


class PersistentDatabaseModel(Base):
    """Persistent SQL databases that survive across conversations."""
    __tablename__ = "persistent_databases"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(50), unique=True, nullable=False)  # User-friendly name, e.g., "biscuit_tracking"
    schema_name = Column(String(64), unique=True, nullable=False)  # PostgreSQL schema, e.g., "edward_db_biscuit_tracking"
    description = Column(Text, nullable=True)  # Optional description of the database purpose
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_accessed = Column(DateTime, server_default=func.now())
    row_count = Column(Integer, default=0)  # Approximate row count across all tables
    user_id = Column(String, nullable=True)  # Future multi-tenant support


class CustomMCPServerModel(Base):
    """Custom MCP servers added by Edward at runtime."""
    __tablename__ = "custom_mcp_servers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    package_name = Column(String(255), nullable=False)
    runtime = Column(String(10), nullable=False)  # "npx" or "uvx"
    args = Column(Text, nullable=True)  # JSON array of extra args
    env_vars = Column(Text, nullable=True)  # JSON object of env vars
    tool_prefix = Column(String(50), nullable=False)
    enabled = Column(Boolean, default=True)
    tool_names = Column(Text, nullable=True)  # JSON array of tool names
    source_url = Column(Text, nullable=True)
    added_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatEventModel(Base):
    """Events ingested by the heartbeat system from iMessage and other sources."""
    __tablename__ = "heartbeat_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(String(50), nullable=False)       # "imessage"
    event_type = Column(String(50), nullable=False)    # "message_received", "message_sent"
    sender = Column(String(255), nullable=True)
    contact_name = Column(String(255), nullable=True)
    person_key = Column(String(160), nullable=True)
    display_label = Column(String(255), nullable=True)
    identity_provenance = Column(String(80), nullable=True)
    chat_identifier = Column(String(255), nullable=True)
    chat_name = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    raw_data = Column(Text, nullable=True)
    source_id = Column(String(255), nullable=True, unique=True)
    is_from_user = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    triage_status = Column(String(20), default="pending")
    # Failed classify/respond attempts — drives retry-then-degrade instead of
    # silently dropping events (2026-07-07 heartbeat work).
    triage_attempts = Column(Integer, default=0)
    triage_cycle_id = Column(String, nullable=True)
    briefed = Column(Boolean, default=False)
    # D1+B — LLM-initiated push dispatcher. ``notified_at`` is NULL until
    # the dispatcher covers the event (either via a direct push or a
    # multi-event digest). ``notification_batch_id`` links events that
    # were grouped into the same digest.
    notified_at = Column(DateTime, nullable=True)
    notification_batch_id = Column(String, nullable=True)
    # D3 — was-this-useful feedback. ``user_acted_on_it`` records whether
    # the user actually engaged with the pushed conversation (e.g. replied)
    # so the dispatcher can soften cadence for senders the user ignores.
    user_acted_on_it = Column(Boolean, default=False)
    user_acted_at = Column(DateTime, nullable=True)
    # Settle-before-act (2026-09-02, services/heartbeat/act_queue.py).
    inbound_at = Column(DateTime, nullable=True)          # Apple message timestamp, UTC
    watch_until = Column(DateTime, nullable=True)         # end of the settle window
    acted_at = Column(DateTime, nullable=True)            # when Layer 3 finished
    handled_by_owner_at = Column(DateTime, nullable=True) # the owner replied first; no turn taken
    # --- mind wake attempts (2026-09-24) ---
    # Wakes that were served this escalation and then failed or timed out
    # (mind_service._note_failed_attempt). At three the mind gives up on it:
    # notified_at is stamped with batch "mind:gave-up" so it stops being
    # re-served to every wake; Today still lists it.
    mind_attempts = Column(Integer, nullable=True)


class InterruptDeliveryModel(Base):
    """One row per interrupt delivery attempt ladder (services/interrupt_service.py)."""
    __tablename__ = "interrupt_deliveries"

    id = Column(String, primary_key=True)
    interrupt_id = Column(String, nullable=False)
    conversation_id = Column(String, nullable=True)
    event_ids = Column(Text, nullable=True)
    title = Column(String(255), nullable=True)
    urgency = Column(String(10), nullable=True)
    plan = Column(Text, nullable=True)
    step_index = Column(Integer, default=0)
    surface = Column(String(20), nullable=True)
    target = Column(String(120), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    acked_at = Column(DateTime, nullable=True)
    ack_source = Column(String(20), nullable=True)
    outcome = Column(String(20), default="pending")
    created_at = Column(DateTime, server_default=func.now())


class ScreenStateModel(Base):
    """Edward's screen on the host Mac: his scene (the stage, 2026-09-28), the
    lines he said there, the owner's hold, and what is on top (services/screen_service.py).
    The ``layout*`` columns are the old 6×4 tile layout, read only to convert it."""
    __tablename__ = "screen_state"

    id = Column(String, primary_key=True, default="mini")
    scene = Column(Text, nullable=True)             # JSON {pieces, me}, NULL = the stock scene (or a legacy layout)
    scene_why = Column(String(300), nullable=True)
    scene_updated_at = Column(DateTime, nullable=True)
    scene_updated_by = Column(String(40), nullable=True)
    asked_at = Column(DateTime, nullable=True)      # the owner's ask holds the screen until asked_until
    asked_until = Column(DateTime, nullable=True)
    lines = Column(Text, nullable=True)             # JSON list: the last few lines he said on screen
    layout = Column(Text, nullable=True)            # legacy: JSON list of tiles
    layout_note = Column(String(300), nullable=True)
    layout_updated_at = Column(DateTime, nullable=True)
    layout_updated_by = Column(String(40), nullable=True)
    show = Column(Text, nullable=True)              # JSON show content, NULL = nothing on top
    show_dismissed_at = Column(DateTime, nullable=True)
    show_dismissed_by = Column(String(40), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HubCanvasModel(Base):
    """What the pet-station screen is showing right now (services/hub_canvas_service.py)."""
    __tablename__ = "hub_canvas"

    id = Column(String, primary_key=True, default="default")
    content = Column(Text, nullable=True)          # JSON CanvasContent, NULL = nothing
    dismissed_at = Column(DateTime, nullable=True)
    dismissed_by = Column(String(40), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PresenceBeaconModel(Base):
    """Last beacon per device/surface (see services/presence_logic.py)."""
    __tablename__ = "presence_beacons"

    device_id = Column(String(120), primary_key=True)
    device_name = Column(String(120), nullable=True)
    kind = Column(String(20), nullable=False)
    state = Column(String(20), nullable=False)
    idle_seconds = Column(Integer, default=0)
    app = Column(String(200), nullable=True)
    capabilities = Column(Text, nullable=True)
    last_seen_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class TriageDecisionModel(Base):
    """One row per triage decision stage — the trail behind an act/hold/skip.

    Added 2026-09-02 so the settle-before-act work is measurable (see
    services/heartbeat/decision_trail.py).
    """
    __tablename__ = "triage_decisions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = Column(String, nullable=False, index=True)
    cycle_id = Column(String, nullable=True)
    stage = Column(String(20), nullable=False)          # settle | layer2 | layer3 | dispatch | delivery
    decision = Column(String(30), nullable=False)       # hold | act_now | handled_by_owner | acted | push | ...
    reason_code = Column(String(60), nullable=False)
    reason = Column(Text, nullable=True)
    attention = Column(Text, nullable=True)             # JSON AttentionState snapshot
    owner_replied_since = Column(Boolean, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CoordinationThreadStateModel(Base):
    """Compact per-chat state for active logistics/meetup coordination."""
    __tablename__ = "coordination_thread_state"

    state_key = Column(String(320), primary_key=True)
    source = Column(String(50), nullable=False)
    chat_identifier = Column(String(255), nullable=False)
    status = Column(String(20), default="active", nullable=False)
    meeting_place_mentioned = Column(Boolean, default=False, nullable=False)
    time_window_active = Column(Boolean, default=False, nullable=False)
    transport_or_arrival_active = Column(Boolean, default=False, nullable=False)
    constraint_negotiation_active = Column(Boolean, default=False, nullable=False)
    state_type = Column(String(40), nullable=True)
    open_question_present = Column(Boolean, default=False, nullable=False)
    proposed_next_step = Column(Text, nullable=True)
    last_plan_change_at = Column(DateTime, nullable=True)
    awaiting_actor = Column(String(20), nullable=True)
    closure_evidence = Column(Text, nullable=True)
    confidence = Column(Float, default=0.0, nullable=False)
    seeded_at = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    seed_event_id = Column(String, nullable=True)
    last_event_id = Column(String, nullable=True)
    provenance = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_coordination_thread_state_chat", "source", "chat_identifier"),
        Index("ix_coordination_thread_state_expires", "expires_at"),
    )


class LiveConversationSessionModel(Base):
    """Short-lived per-chat state for hot reciprocal conversation sessions."""
    __tablename__ = "live_conversation_sessions"

    session_key = Column(String(320), primary_key=True)
    source = Column(String(50), nullable=False)
    chat_identifier = Column(String(255), nullable=False)
    contact_key = Column(String(255), nullable=True, index=True)
    session_active = Column(Boolean, default=False, nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    last_turn_at = Column(DateTime, nullable=False)
    recent_turn_count = Column(Integer, default=0, nullable=False)
    owner_last_outbound_at = Column(DateTime, nullable=True)
    reciprocal_burst_score = Column(Float, default=0.0, nullable=False)
    last_question_or_proposal_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_event_id = Column(String, nullable=True)
    recent_turns_json = Column(Text, nullable=True)
    reason_codes = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_live_conversation_sessions_chat", "source", "chat_identifier"),
        Index("ix_live_conversation_sessions_active_expires", "session_active", "expires_at"),
    )


class ConversationMomentumRecordModel(Base):
    """Short-lived per-chat momentum state for low-content continuation rescue."""
    __tablename__ = "conversation_momentum_records"

    momentum_key = Column(String(320), primary_key=True)
    source = Column(String(50), nullable=False)
    chat_identifier = Column(String(255), nullable=False)
    active = Column(Boolean, default=False, nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    last_turn_at = Column(DateTime, nullable=False)
    last_owner_turn_at = Column(DateTime, nullable=True)
    last_inbound_turn_at = Column(DateTime, nullable=True)
    recent_turn_count = Column(Integer, default=0, nullable=False)
    exchange_density = Column(Float, default=0.0, nullable=False)
    turn_taking_recency_score = Column(Float, default=0.0, nullable=False)
    participant_count = Column(Integer, default=0, nullable=False)
    unresolved_question_to_owner = Column(Boolean, default=False, nullable=False)
    unresolved_question_from_owner = Column(Boolean, default=False, nullable=False)
    latest_followup_type = Column(String(80), nullable=True)
    latest_followup_to_owner = Column(Boolean, default=False, nullable=False)
    momentum_score = Column(Float, default=0.0, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_event_id = Column(String, nullable=True)
    evidence_json = Column(Text, nullable=True)
    reason_codes_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_conversation_momentum_chat", "source", "chat_identifier"),
        Index("ix_conversation_momentum_active_expires", "active", "expires_at"),
    )


class CoordinationCaseModel(Base):
    """Bounded extracted state for high-confidence multi-party coordination."""
    __tablename__ = "coordination_cases"

    case_id = Column(String(320), primary_key=True)
    source = Column(String(50), nullable=False, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    proposal_type = Column(String(40), nullable=False, index=True)
    status = Column(String(30), default="active", nullable=False, index=True)
    confidence = Column(Float, default=0.0, nullable=False)
    participants = Column(Text, nullable=True)
    options = Column(Text, nullable=True)
    participant_states = Column(Text, nullable=True)
    last_summary = Column(Text, nullable=True)
    next_required_action = Column(Text, nullable=True)
    blocking_party = Column(String(255), nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    seed_event_id = Column(String, nullable=True)
    last_event_id = Column(String, nullable=True)
    provenance = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_coordination_cases_chat_status", "source", "chat_identifier", "status"),
        Index("ix_coordination_cases_expires", "expires_at"),
    )


class CoordinationContextStateModel(Base):
    """Short-lived per-chat marker for same-day logistics coordination."""
    __tablename__ = "coordination_context_state"

    state_key = Column(String(320), primary_key=True)
    source = Column(String(50), nullable=False)
    chat_identifier = Column(String(255), nullable=False)
    status = Column(String(20), default="active", nullable=False)
    last_coordination_at = Column(DateTime, nullable=False)
    coordination_topic = Column(String(120), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    seed_event_id = Column(String, nullable=True)
    last_event_id = Column(String, nullable=True)
    reason_codes = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_coordination_context_state_chat", "source", "chat_identifier"),
        Index("ix_coordination_context_state_expires", "expires_at"),
    )


class ActionEpisodeTrackerModel(Base):
    """Short-lived action episodes seeded from the owner's outbound instructions."""
    __tablename__ = "action_episode_tracker"

    episode_id = Column(String(320), primary_key=True)
    state_key = Column(String(320), nullable=False, index=True)
    source = Column(String(50), nullable=False)
    chat_identifier = Column(String(255), nullable=False)
    status = Column(String(20), default="active", nullable=False, index=True)
    episode_type = Column(String(40), nullable=False, index=True)
    started_by_outbound_id = Column(String, nullable=False)
    counterparty = Column(String(255), nullable=True)
    started_at = Column(DateTime, nullable=False)
    last_activity_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    expected_reply_signals = Column(Text, nullable=True)
    referenced_entities = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    provenance = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_action_episode_tracker_chat_status", "source", "chat_identifier", "status"),
        Index("ix_action_episode_tracker_state_expires", "state_key", "expires_at"),
    )


class AdminObligationStateModel(Base):
    """Compact per-chat state for active administrative/financial obligations."""
    __tablename__ = "admin_obligation_state"

    state_key = Column(String(320), primary_key=True)
    source = Column(String(50), nullable=False)
    chat_identifier = Column(String(255), nullable=False)
    status = Column(String(20), default="open", nullable=False)
    topic = Column(String(160), nullable=True)
    vendor_label = Column(String(255), nullable=True)
    last_due_at = Column(DateTime, nullable=True)
    seeded_at = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    seed_event_id = Column(String, nullable=True)
    last_event_id = Column(String, nullable=True)
    provenance = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_admin_obligation_state_chat", "source", "chat_identifier"),
        Index("ix_admin_obligation_state_expires", "expires_at"),
    )


class ContactIdentityAliasModel(Base):
    """Stable mapping from raw inbound identifiers to canonical people."""
    __tablename__ = "contact_identity_aliases"

    alias_key = Column(String(160), primary_key=True)
    alias_type = Column(String(40), nullable=False)
    raw_identifier = Column(String(255), nullable=False)
    person_key = Column(String(160), nullable=False, index=True)
    display_label = Column(String(255), nullable=False)
    display_name_key = Column(String(160), nullable=True, index=True)
    provenance = Column(String(80), nullable=False)
    source = Column(String(50), nullable=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatFeedbackModel(Base):
    """The owner's verdict on a heartbeat decision, from the heartbeat digest view.

    Not yet consumed by triage; see services/heartbeat_feedback_service.py.
    """
    __tablename__ = "heartbeat_feedback"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = Column(String, nullable=False, index=True)
    verdict = Column(String(40), nullable=False)
    triage_status = Column(String(20), nullable=True)
    contact_key = Column(String(255), nullable=True, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class TriageResultModel(Base):
    """Results of each heartbeat triage cycle."""
    __tablename__ = "triage_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_number = Column(Integer, nullable=False)
    events_total = Column(Integer, default=0)
    events_rule_filtered = Column(Integer, default=0)
    events_dismissed = Column(Integer, default=0)
    events_noted = Column(Integer, default=0)
    events_acted = Column(Integer, default=0)
    events_escalated = Column(Integer, default=0)
    layer_reached = Column(Integer, default=1)
    classification = Column(Text, nullable=True)
    digest_tokens = Column(Integer, default=0)
    haiku_input_tokens = Column(Integer, default=0)
    haiku_output_tokens = Column(Integer, default=0)
    sonnet_wakes = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class ImessageOutgoingSendModel(Base):
    """Ledger of messages Edward sent via AppleScript.

    Used by the heartbeat listener to distinguish Edward's own iCloud-echoed
    replies (is_from_me=True) from `@edward` mentions the user typed on
    another device (also is_from_me=True when signed into the same Apple ID).

    The text is truncated to 200 chars and SHA-256 hashed to keep the index
    compact.

    The pings work (2026-10-11) added the read-back columns: the chat.db row
    each send became (``message_rowid``/``message_guid``), the normalised
    text hash the listener's echo check compares (``text_norm_hash``), the
    route that worked, the ping it answered and the turn that sent it.
    ``ok`` is load-bearing: the echo index (``services/imessage_sent.py``)
    and the dedupes trust only ok rows. ``sent_at`` and ``confirmed_at``
    are timestamptz, so bind **aware UTC** only (a naive cutoff missed a
    row stamped now on this Central-time Mac). ``services.pings.schema.ALTERS``
    adds the same columns to an existing table. Retention: 90 days, by the
    ping sweeper's daily prune (the 10-minute prune this docstring used to
    promise was never written).
    """
    __tablename__ = "imessage_outgoing_sends"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_identifier = Column(String(255), nullable=True)
    text_hash = Column(String(64), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    chat_guid = Column(String(255), nullable=True)
    route = Column(String(40), nullable=True)
    ok = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    error = Column(Text, nullable=True)
    message_rowid = Column(BigInteger, nullable=True)
    message_guid = Column(String(80), nullable=True)
    text_norm_hash = Column(String(64), nullable=True)
    text_len = Column(Integer, nullable=True)
    ping_id = Column(Integer, nullable=True)
    purpose = Column(String(20), nullable=True)
    turn_source = Column(String(30), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_imessage_outgoing_sends_hash_time", "text_hash", "sent_at"),
    )


class IMessageChatConversationModel(Base):
    """Persistent mapping from an iMessage chat (1:1 or group) to a single
    Edward conversation_id.

    Replaces the per-trigger UUID generation that used to fragment every
    new mention or post-window-expiry message into its own orphan
    conversation. With this table, every iMessage in the same chat routes
    to the same LangGraph thread across days, restarts, and listening
    windows — so Edward's checkpoint actually carries his prior replies
    in that chat.
    """
    __tablename__ = "imessage_chat_conversations"

    chat_identifier = Column(String(255), primary_key=True)
    conversation_id = Column(String, nullable=False)  # → conversations.id
    chat_name = Column(String, nullable=True)
    is_group = Column(Boolean, default=False)
    last_active = Column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at = Column(DateTime, server_default=func.now())


class HeartbeatConfigModel(Base):
    """Configuration for the heartbeat system."""
    __tablename__ = "heartbeat_config"

    id = Column(String, primary_key=True, default="default")
    enabled = Column(Boolean, default=True)
    triage_interval_seconds = Column(Integer, default=900)
    digest_token_cap = Column(Integer, default=800)
    dismiss_reply_review_enabled = Column(Boolean, default=True)
    dismiss_reply_review_window_days = Column(Integer, default=30)
    dismiss_reply_review_min_count = Column(Integer, default=2)
    dismiss_reply_review_reply_window_hours = Column(Integer, default=2)
    active_chat_question_override_window_hours = Column(Integer, default=72)
    reply_affinity_enabled = Column(Boolean, default=True)
    reply_affinity_history_messages = Column(Integer, default=30)
    reply_affinity_min_history_messages = Column(Integer, default=8)
    reply_affinity_min_reply_count = Column(Integer, default=3)
    reply_affinity_reply_rate_threshold = Column(Float, default=0.35)
    reply_affinity_reply_window_hours = Column(Integer, default=72)
    reply_affinity_false_dismissal_count_threshold = Column(Integer, default=1)
    contact_reply_prior_enabled = Column(Boolean, default=True)
    contact_reply_prior_window_days = Column(Integer, default=30)
    contact_reply_prior_min_count = Column(Integer, default=2)
    contact_reply_prior_max_boost = Column(Float, default=0.16)
    reply_propensity_window_size = Column(Integer, default=20)
    reply_propensity_smoothing_constant = Column(Float, default=4.0)
    reply_propensity_cutoff = Column(Float, default=0.65)
    reply_propensity_max_boost = Column(Float, default=0.15)
    reply_after_dismiss_enabled = Column(Boolean, default=True)
    reply_after_dismiss_window_hours = Column(Integer, default=24)
    reply_after_dismiss_half_life_hours = Column(Integer, default=168)
    reply_after_dismiss_activation = Column(Float, default=3.0)
    reply_after_dismiss_max_boost = Column(Float, default=0.15)
    reply_after_dismiss_audit_window_days = Column(Integer, default=30)
    reply_after_dismiss_audit_min_dismissals = Column(Integer, default=2)
    reply_after_dismiss_audit_rate_threshold = Column(Float, default=0.4)
    reply_after_dismiss_audit_min_replies = Column(Integer, default=2)
    reply_after_dismiss_audit_max_boost = Column(Float, default=0.15)
    contact_priority_adjustment_enabled = Column(Boolean, default=True)
    contact_priority_adjustment_window_days = Column(Integer, default=30)
    contact_priority_adjustment_lookback_limit = Column(Integer, default=20)
    contact_priority_adjustment_min_dismissed = Column(Integer, default=4)
    contact_priority_adjustment_min_replied = Column(Integer, default=2)
    contact_priority_adjustment_rate_threshold = Column(Float, default=0.6)
    contact_priority_adjustment_smoothing_constant = Column(Float, default=4.0)
    contact_priority_adjustment_max_boost = Column(Float, default=0.18)
    contact_triage_bias_window_days = Column(Integer, default=30)
    contact_triage_bias_min_dismissed = Column(Integer, default=3)
    contact_triage_bias_min_mismatches = Column(Integer, default=3)
    contact_triage_bias_rate_threshold = Column(Float, default=0.67)
    contact_triage_bias_max_boost = Column(Float, default=0.18)
    chat_audit_mismatch_override_enabled = Column(Boolean, default=True)
    chat_audit_mismatch_override_window_days = Column(Integer, default=30)
    chat_audit_mismatch_override_min_count = Column(Integer, default=2)
    chat_audit_mismatch_override_max_boost = Column(Float, default=0.2)
    stable_contact_dismiss_mismatch_override_enabled = Column(Boolean, default=True)
    stable_contact_dismiss_mismatch_override_window_days = Column(Integer, default=30)
    stable_contact_dismiss_mismatch_override_min_count = Column(Integer, default=2)
    stable_contact_dismiss_mismatch_override_max_boost = Column(Float, default=0.2)
    false_dismiss_contact_override_enabled = Column(Boolean, default=True)
    false_dismiss_contact_override_threshold = Column(Integer, default=3)
    false_dismiss_contact_override_lookback_days = Column(Integer, default=30)
    contact_level_triage_override_enabled = Column(Boolean, default=True)
    contact_level_triage_override_window_days = Column(Integer, default=30)
    contact_level_triage_override_min_mismatches = Column(Integer, default=3)
    contact_level_triage_override_max_adjustment = Column(Float, default=0.12)
    contact_triage_mismatch_adjustment_enabled = Column(Boolean, default=True)
    contact_triage_mismatch_adjustment_window_days = Column(Integer, default=30)
    contact_triage_mismatch_adjustment_min_count = Column(Integer, default=3)
    contact_triage_mismatch_adjustment_score_penalty = Column(Float, default=0.2)
    contact_triage_mismatch_adjustment_max_boost = Column(Float, default=0.2)
    contact_engagement_boost_window_days = Column(Integer, default=30)
    contact_engagement_boost_min_dismissals = Column(Integer, default=3)
    contact_engagement_boost_min_replies = Column(Integer, default=2)
    contact_engagement_boost_rate_threshold = Column(Float, default=0.5)
    contact_engagement_boost_max_boost = Column(Float, default=0.2)
    allowed_senders = Column(Text, nullable=True)  # JSON array of {"identifier": "...", "label": "..."}
    # Per-track configuration
    # Settle-before-act (2026-09-02). layer3_async_enabled is the rollout
    # flag: off = legacy inline Layer 3; on = hold ordinary ACT/ESCALATE
    # through a settle window and re-check chat.db for the owner's reply first.
    layer3_async_enabled = Column(Boolean, default=False)
    # Mind rebuild Phase 6a (2026-09-18): when on, a triage cycle applies
    # identity filters only and hands the rest to the mind as a wake. On by
    # default: the legacy triage layers are dormant.
    mind_enabled = Column(Boolean, default=True)
    settle_window_seconds = Column(Integer, default=240)
    settle_window_present_seconds = Column(Integer, default=480)
    # Reading every iMessage on this Mac is opt-in (needs Full Disk Access
    # for chat.db): off until the owner turns Listening on.
    imessage_enabled = Column(Boolean, default=False)
    imessage_poll_seconds = Column(Integer, default=10)
    calendar_enabled = Column(Boolean, default=False)
    calendar_poll_seconds = Column(Integer, default=300)
    calendar_lookahead_minutes = Column(Integer, default=30)
    email_enabled = Column(Boolean, default=False)
    email_poll_seconds = Column(Integer, default=300)
    email_last_rowid = Column(Integer, nullable=True)
    imessage_last_rowid = Column(Integer, nullable=True)
    imessage_thread_limit_direct = Column(Integer, default=30)
    imessage_thread_limit_group = Column(Integer, default=75)
    gmail_enabled = Column(Boolean, default=False)
    gmail_poll_seconds = Column(Integer, default=120)
    # Direct-channel triggering (2026-07-07): JSON list of the owner's own handles —
    # a 1:1 chat with one of these is the self-chat command channel; messages
    # there fast-track like @edward mentions.
    imessage_owner_handles = Column(Text, nullable=True)
    # Fire a debounced immediate triage cycle on any inbound 1:1 message
    # instead of waiting for the scheduled interval.
    imessage_event_triggered_triage = Column(Boolean, default=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChatStanddownModel(Base):
    """Binding per-chat stand-down: someone in the chat told Edward to stay
    out (2026-07-09 incident — a promise to stay out must survive the next
    triage turn). Recorded by Edward via the stand_down tool; enforced by
    triage (messaging tools stripped for the chat); released by a fresh
    @edward mention or expiry."""
    __tablename__ = "chat_standdowns"

    chat_identifier = Column(String(255), primary_key=True)
    requested_by = Column(String(255), nullable=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    released_at = Column(DateTime(timezone=True), nullable=True)


class MemoryEnrichmentModel(Base):
    """Reflection thread output — memories found via multi-query search after each turn."""
    __tablename__ = "memory_enrichments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, nullable=False, index=True)
    memory_id = Column(String, nullable=False)
    memory_content = Column(Text, nullable=False)
    memory_type = Column(String, nullable=False)
    importance = Column(Float, default=0.5)
    temporal_nature = Column(String, default="timeless")
    query_source = Column(String, nullable=True)
    score = Column(Float, default=0.0)
    consumed = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class HeartbeatFalseDismissalSignalModel(Base):
    """Recorded when an auto-dismissed inbound later gets an outbound reply
    in the same chat within the configured review window."""
    __tablename__ = "heartbeat_false_dismissal_signals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_identifier = Column(String(255), nullable=False, index=True)
    normalized_chat_key = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True)
    contact_name = Column(String(255), nullable=True)
    dismissed_event_id = Column(String, nullable=False, unique=True, index=True)
    reply_event_id = Column(String, nullable=False, index=True)
    dismissed_at = Column(DateTime, nullable=False)
    reply_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class HeartbeatChatBackoffModel(Base):
    """Rolling per-chat false-dismissal counts used to soften future dismissals."""
    __tablename__ = "heartbeat_chat_backoffs"

    chat_identifier = Column(String(255), primary_key=True)
    rolling_false_dismissal_count = Column(Integer, default=0)
    last_false_dismissal_at = Column(DateTime, nullable=True)
    last_reply_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatAdaptiveDismissalCounterModel(Base):
    """Persisted dismiss→reply counters keyed by chat when possible, else sender."""
    __tablename__ = "heartbeat_adaptive_dismissal_counters"

    counter_key = Column(String(255), primary_key=True)
    key_type = Column(String(20), nullable=False)  # "chat_identifier" | "sender"
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    dismissed_but_replied_count = Column(Integer, default=0, nullable=False)
    last_dismissed_at = Column(DateTime, nullable=True)
    last_reply_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatContactAffinityModel(Base):
    """Rolling per-contact dismiss audit counters keyed by normalized chat identifier."""
    __tablename__ = "heartbeat_contact_affinity"

    contact_key = Column(String(255), primary_key=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    dismissed_but_replied_count = Column(Integer, default=0, nullable=False)
    audited_dismissal_count = Column(Integer, default=0, nullable=False)
    reply_after_dismiss_count = Column(Integer, default=0, nullable=False)
    reply_after_dismiss_rate = Column(Float, default=0.0, nullable=False)
    keep_prior_score = Column(Float, default=0.0, nullable=False)
    reply_propensity_inbound_count = Column(Integer, default=0, nullable=False)
    reply_propensity_replied_count = Column(Integer, default=0, nullable=False)
    reply_propensity_raw_rate = Column(Float, default=0.0, nullable=False)
    reply_propensity_smoothed_rate = Column(Float, default=0.0, nullable=False)
    reply_propensity_boost = Column(Float, default=0.0, nullable=False)
    last_mismatch_at = Column(DateTime, nullable=True)
    last_audited_at = Column(DateTime, nullable=True)
    last_reply_at = Column(DateTime, nullable=True)
    reply_propensity_computed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatContactTriageCalibrationModel(Base):
    """Per-contact matured triage outcomes used to calibrate future dismisses."""
    __tablename__ = "heartbeat_contact_triage_calibration"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_key = Column(String(255), nullable=False, index=True)
    person_key = Column(String(255), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    event_id = Column(String, nullable=False, unique=True, index=True)
    triage_status = Column(String(40), nullable=False)
    outcome = Column(String(80), nullable=False, index=True)
    user_replied = Column(Boolean, default=False, nullable=False)
    event_at = Column(DateTime, nullable=False, index=True)
    outcome_at = Column(DateTime, nullable=False, index=True)
    reply_event_id = Column(String, nullable=True)
    reply_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatDismissCalibrationProfileModel(Base):
    """Compact rolling false-dismiss profile keyed by person, then chat/contact."""
    __tablename__ = "heartbeat_dismiss_calibration_profiles"

    profile_key = Column(String(255), primary_key=True)
    key_type = Column(String(40), nullable=False)  # "person" | "chat"
    person_key = Column(String(255), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    dismiss_false_positive_count = Column(Integer, default=0, nullable=False)
    dismiss_true_negative_count = Column(Integer, default=0, nullable=False)
    decayed_false_positive_count = Column(Float, default=0.0, nullable=False)
    decayed_true_negative_count = Column(Float, default=0.0, nullable=False)
    recent_false_dismiss_rate = Column(Float, default=0.0, nullable=False)
    message_volume = Column(Integer, default=0, nullable=False)
    last_false_dismiss_at = Column(DateTime, nullable=True)
    last_outcome_at = Column(DateTime, nullable=True)
    active_calibration_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatContactShorthandProfileModel(Base):
    """Bounded per-contact profile for compressed result/update shorthand."""
    __tablename__ = "heartbeat_contact_shorthand_profiles"

    profile_key = Column(String(255), primary_key=True)
    pattern_key = Column(String(80), primary_key=True)
    key_type = Column(String(40), nullable=False)  # "person" | "chat"
    person_key = Column(String(255), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    positive_count = Column(Integer, default=0, nullable=False)
    negative_count = Column(Integer, default=0, nullable=False)
    decayed_positive_count = Column(Float, default=0.0, nullable=False)
    decayed_negative_count = Column(Float, default=0.0, nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    feature_counts = Column(Text, nullable=True)
    exemplar_tokens = Column(Text, nullable=True)
    last_positive_at = Column(DateTime, nullable=True)
    last_negative_at = Column(DateTime, nullable=True)
    last_observed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatReciprocityStyleProfileModel(Base):
    """Rolling per-contact/class profile for low-content social closures."""
    __tablename__ = "heartbeat_reciprocity_style_profiles"

    profile_key = Column(String(255), primary_key=True)
    ack_class = Column(String(40), primary_key=True)
    person_key = Column(String(160), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    decayed_observed_count = Column(Float, default=0.0, nullable=False)
    decayed_replied_count = Column(Float, default=0.0, nullable=False)
    smoothed_reply_rate = Column(Float, default=0.0, nullable=False)
    raw_observed_count = Column(Integer, default=0, nullable=False)
    raw_replied_count = Column(Integer, default=0, nullable=False)
    decayed_recent_thread_observed_count = Column(Float, default=0.0, nullable=False)
    decayed_recent_thread_replied_count = Column(Float, default=0.0, nullable=False)
    raw_recent_thread_observed_count = Column(Integer, default=0, nullable=False)
    raw_recent_thread_replied_count = Column(Integer, default=0, nullable=False)
    last_observed_at = Column(DateTime, nullable=True)
    last_replied_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatSenderReplyAfterDismissModel(Base):
    """Small per-sender counter for dismissed inbound messages the user later replied to.

    ``decayed_count`` is maintained with exponential time decay when new feedback
    arrives and when profiles are loaded for triage.
    """
    __tablename__ = "heartbeat_sender_reply_after_dismiss"

    sender_key = Column(String(255), primary_key=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    decayed_count = Column(Float, default=0.0, nullable=False)
    event_count = Column(Integer, default=0, nullable=False)
    last_event_at = Column(DateTime, nullable=True)
    last_dismissed_at = Column(DateTime, nullable=True)
    last_reply_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatServiceInteractionProfileModel(Base):
    """Compact per-service sender profile for transactional reply prompts."""
    __tablename__ = "heartbeat_service_interaction_profiles"

    service_key = Column(String(255), primary_key=True)
    key_type = Column(String(40), nullable=False)
    source = Column(String(50), nullable=True)
    sender = Column(String(255), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    service_category = Column(String(80), nullable=True)
    known_transactional_sender = Column(Boolean, default=False, nullable=False)
    transactional_prompt_count = Column(Integer, default=0, nullable=False)
    actioned_prompt_count = Column(Integer, default=0, nullable=False)
    reply_prompt_rate = Column(Float, default=0.0, nullable=False)
    last_prompt_at = Column(DateTime, nullable=True)
    last_actioned_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatConversationalArchetypeProfileModel(Base):
    """Small rolling profile of inbound message styles the user tends to reply to."""
    __tablename__ = "heartbeat_conversational_archetype_profiles"

    contact_key = Column(String(255), primary_key=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    sample_count = Column(Integer, default=0, nullable=False)
    profile_json = Column(Text, nullable=True)
    archetype_counts_json = Column(Text, nullable=True)
    last_replied_at = Column(DateTime, nullable=True)
    computed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatInteractionStyleProfileModel(Base):
    """Bounded per-chat/contact profile of reportive styles the owner tends to answer."""
    __tablename__ = "heartbeat_interaction_style_profiles"

    profile_key = Column(String(255), primary_key=True)
    person_key = Column(String(160), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    raw_replied_count = Column(Integer, default=0, nullable=False)
    raw_ignored_count = Column(Integer, default=0, nullable=False)
    profile_json = Column(Text, nullable=True)
    class_counts_json = Column(Text, nullable=True)
    artifact_terms_json = Column(Text, nullable=True)
    computed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatReplyExpectationProfileModel(Base):
    """Compact reply-expectation profile keyed by stable contact and channel."""
    __tablename__ = "heartbeat_reply_expectation_profiles"

    profile_key = Column(String(320), primary_key=True)
    stable_contact_key = Column(String(255), nullable=False, index=True)
    channel_type = Column(String(40), nullable=False, index=True)
    thread_context_key = Column(String(255), nullable=True, index=True)
    person_key = Column(String(160), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    observed_inbound_count = Column(Integer, default=0, nullable=False)
    replied_inbound_count = Column(Integer, default=0, nullable=False)
    decayed_observed_count = Column(Float, default=0.0, nullable=False)
    decayed_replied_count = Column(Float, default=0.0, nullable=False)
    reply_rate = Column(Float, default=0.0, nullable=False)
    median_reply_latency_minutes = Column(Float, nullable=True)
    terse_confirmation_observed_count = Column(Integer, default=0, nullable=False)
    terse_confirmation_replied_count = Column(Integer, default=0, nullable=False)
    terse_confirmation_reply_rate = Column(Float, default=0.0, nullable=False)
    ack_based_rate = Column(Float, default=0.0, nullable=False)
    active_coordination_score = Column(Float, default=0.0, nullable=False)
    mismatch_count = Column(Integer, default=0, nullable=False)
    expectation_score = Column(Float, default=0.0, nullable=False)
    profile_json = Column(Text, nullable=True)
    last_observed_at = Column(DateTime, nullable=True)
    last_replied_at = Column(DateTime, nullable=True)
    computed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_hb_reply_expectation_contact_channel", "stable_contact_key", "channel_type"),
        Index("ix_hb_reply_expectation_contact_context", "stable_contact_key", "thread_context_key"),
    )


class HeartbeatEngagementProfileModel(Base):
    """Compact contact/chat engagement prior used as a bounded triage input."""
    __tablename__ = "heartbeat_engagement_profiles"

    profile_key = Column(String(320), primary_key=True)
    stable_contact_key = Column(String(255), nullable=False, index=True)
    channel_type = Column(String(40), nullable=False, index=True)
    thread_context_key = Column(String(255), nullable=True, index=True)
    person_key = Column(String(160), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    observed_inbound_count = Column(Integer, default=0, nullable=False)
    replied_inbound_count = Column(Integer, default=0, nullable=False)
    decayed_observed_count = Column(Float, default=0.0, nullable=False)
    decayed_replied_count = Column(Float, default=0.0, nullable=False)
    reply_rate = Column(Float, default=0.0, nullable=False)
    decayed_reply_rate = Column(Float, default=0.0, nullable=False)
    median_reply_latency_minutes = Column(Float, nullable=True)
    dismissed_observed_count = Column(Integer, default=0, nullable=False)
    dismissed_then_replied_count = Column(Integer, default=0, nullable=False)
    dismissed_then_replied_fraction = Column(Float, default=0.0, nullable=False)
    thread_participation_breadth = Column(Integer, default=0, nullable=False)
    context_signal_count = Column(Integer, default=0, nullable=False)
    context_signal_replied_count = Column(Integer, default=0, nullable=False)
    context_signal_reply_rate = Column(Float, default=0.0, nullable=False)
    interpersonal_signal_count = Column(Integer, default=0, nullable=False)
    interpersonal_signal_replied_count = Column(Integer, default=0, nullable=False)
    interpersonal_signal_reply_rate = Column(Float, default=0.0, nullable=False)
    engagement_score = Column(Float, default=0.0, nullable=False)
    sufficient_evidence = Column(Boolean, default=False, nullable=False)
    reset_generation = Column(Integer, default=0, nullable=False)
    profile_json = Column(Text, nullable=True)
    last_observed_at = Column(DateTime, nullable=True)
    last_replied_at = Column(DateTime, nullable=True)
    computed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_hb_engagement_contact_channel", "stable_contact_key", "channel_type"),
        Index("ix_hb_engagement_contact_context", "stable_contact_key", "thread_context_key"),
    )


class CommunicationStyleProfileModel(Base):
    """Compact observable communication-format preferences for drafting."""
    __tablename__ = "communication_style_profiles"

    profile_key = Column(String(255), primary_key=True)
    person_key = Column(String(160), nullable=False, index=True)
    channel_type = Column(String(40), nullable=False, index=True)
    interaction_context = Column(String(80), nullable=False, default="general", index=True)
    contact_name = Column(String(255), nullable=True)
    style_profile_json = Column(Text, nullable=True)
    evidence_confidence = Column(Float, default=0.0, nullable=False)
    raw_evidence_count = Column(Integer, default=0, nullable=False)
    decayed_evidence_count = Column(Float, default=0.0, nullable=False)
    last_confirmed_at = Column(DateTime, nullable=True)
    provenance_json = Column(Text, nullable=True)
    computed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_comm_style_person_channel", "person_key", "channel_type"),
        Index("ix_comm_style_person_context", "person_key", "interaction_context"),
    )


class CommunicationPreferenceRecordModel(Base):
    """Explicit stakeholder communication preferences and constraints.

    Unlike communication style profiles, these rows are only created from
    direct instruction/preference language and are used as bounded drafting
    guidance, not autonomous permission.
    """
    __tablename__ = "communication_preference_records"

    preference_id = Column(String(320), primary_key=True)
    stakeholder = Column(String(255), nullable=False, index=True)
    scope = Column(String(40), nullable=False, index=True)
    scope_key = Column(String(255), nullable=True, index=True)
    preference_type = Column(String(40), nullable=False, index=True)
    preference_value = Column(Text, nullable=False)
    conditions = Column(Text, nullable=True)
    applies_to_recipient_classes = Column(Text, nullable=True)
    status = Column(String(20), default="active", nullable=False, index=True)
    strength = Column(String(24), default="medium", nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    evidence_count = Column(Integer, default=1, nullable=False)
    provenance = Column(Text, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, server_default=func.now())
    last_confirmed_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    superseded_by = Column(String(320), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_comm_pref_stakeholder_scope_active", "stakeholder", "scope", "scope_key", "last_confirmed_at",
              postgresql_where=text("status = 'active'")),
        Index("ix_comm_pref_type_active", "preference_type", "confidence",
              postgresql_where=text("status = 'active'")),
    )


class HeartbeatContactExemplarModel(Base):
    """Compact per-contact examples of dismissed messages the owner later answered."""
    __tablename__ = "heartbeat_contact_exemplars"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_key = Column(String(255), nullable=False, index=True)
    person_key = Column(String(160), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    event_id = Column(String, nullable=False, unique=True, index=True)
    polarity = Column(String(16), nullable=False, index=True)
    normalized_text = Column(Text, nullable=False)
    token_features_json = Column(Text, nullable=True)
    rare_terms_json = Column(Text, nullable=True)
    phrase_features_json = Column(Text, nullable=True)
    shape_json = Column(Text, nullable=True)
    thread_topic_terms_json = Column(Text, nullable=True)
    event_at = Column(DateTime, nullable=False, index=True)
    outcome_at = Column(DateTime, nullable=False, index=True)
    reply_event_id = Column(String, nullable=True)
    reply_at = Column(DateTime, nullable=True)
    weight = Column(Float, default=1.0, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HeartbeatContactTopicAffinityModel(Base):
    """Bounded per-contact topic clusters the owner tends to answer despite low salience."""
    __tablename__ = "heartbeat_contact_topic_affinity"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_key = Column(String(255), nullable=False, index=True)
    topic_key = Column(String(255), nullable=False, index=True)
    topic_type = Column(String(40), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    person_key = Column(String(160), nullable=True, index=True)
    chat_identifier = Column(String(255), nullable=True, index=True)
    sender = Column(String(255), nullable=True, index=True)
    contact_name = Column(String(255), nullable=True)
    positive_count = Column(Integer, default=0, nullable=False)
    negative_count = Column(Integer, default=0, nullable=False)
    decayed_positive_count = Column(Float, default=0.0, nullable=False)
    decayed_negative_count = Column(Float, default=0.0, nullable=False)
    reply_rate = Column(Float, default=0.0, nullable=False)
    baseline_reply_rate = Column(Float, default=0.0, nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    last_observed_at = Column(DateTime, nullable=True)
    last_positive_at = Column(DateTime, nullable=True)
    last_negative_at = Column(DateTime, nullable=True)
    active_window_json = Column(Text, nullable=True)
    seasonality_json = Column(Text, nullable=True)
    examples_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_heartbeat_contact_topic_affinity_contact_topic", "contact_key", "topic_key", unique=True),
    )


class GraphIngestQueueModel(Base):
    """Queue for the out-of-process Graphiti worker (C Phase 2 v2).

    The main backend INSERTs a row here whenever ``store_memory`` fires
    (dual-write). The separate ``graph-worker`` LaunchAgent polls this
    table, runs Graphiti's add_episode in its own Python interpreter,
    and marks rows processed. If the worker segfaults (Kuzu/torch C
    crash), only the worker respawns — the backend is untouched.
    """
    __tablename__ = "graph_ingest_queue"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    source_description = Column(Text, nullable=True)
    reference_time = Column(DateTime, nullable=True)
    status = Column(String(20), default="pending", index=True)  # pending/processing/done/failed
    attempts = Column(Integer, default=0)
    error_msg = Column(Text, nullable=True)
    enqueued_at = Column(DateTime, server_default=func.now(), index=True)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Cheap partial index for the worker's "grab the oldest pending
        # row" query — avoids scanning done/failed rows.
        Index(
            'ix_graph_ingest_queue_pending',
            'enqueued_at',
            postgresql_where=text("status = 'pending'"),
        ),
    )


class MemoryConnectionModel(Base):
    """Links between related memories discovered by consolidation."""
    __tablename__ = "memory_connections"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id_a = Column(String, nullable=False, index=True)
    memory_id_b = Column(String, nullable=False, index=True)
    connection_type = Column(String, nullable=False)  # same_person, same_topic, same_event, related
    strength = Column(Float, default=0.5)
    created_at = Column(DateTime, server_default=func.now())


class MemoryFlagModel(Base):
    """Flags for memory quality issues (contradictions, staleness, etc.)."""
    __tablename__ = "memory_flags"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String, nullable=False, index=True)
    flag_type = Column(String, nullable=False)  # contradiction, low_confidence, stale
    description = Column(Text, nullable=False)
    related_memory_id = Column(String, nullable=True)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class ConsolidationCycleModel(Base):
    """Metrics for each consolidation cycle run."""
    __tablename__ = "consolidation_cycles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    memories_reviewed = Column(Integer, default=0)
    clusters_found = Column(Integer, default=0)
    connections_created = Column(Integer, default=0)
    flags_created = Column(Integer, default=0)
    contradictions_found = Column(Integer, default=0)
    merges_performed = Column(Integer, default=0)
    promotions = Column(Integer, default=0)
    haiku_calls = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class ConsolidationConfigModel(Base):
    """Configuration for the memory consolidation service."""
    __tablename__ = "consolidation_config"

    id = Column(String, primary_key=True, default="default")
    enabled = Column(Boolean, default=True)
    interval_seconds = Column(Integer, default=3600)
    lookback_hours = Column(Integer, default=2)
    # Cost caps — prevent runaway LLM spend / O(n^2) pair work when a cycle
    # sees a lot of related memories. See Track A3 in the dreaming redesign.
    max_cluster_size = Column(Integer, default=20)
    max_pairs_per_cluster = Column(Integer, default=30)
    max_llm_calls_per_cycle = Column(Integer, default=15)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ClaudeCodeSessionModel(Base):
    """Claude Code session records."""
    __tablename__ = "claude_code_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, nullable=True, index=True)
    task = Column(Text, nullable=False)
    status = Column(String, default="running")  # running, completed, failed, cancelled
    cwd = Column(String, nullable=True)
    output_summary = Column(Text, nullable=True)  # JSON
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class SelfObservationModel(Base):
    """Structured observations Edward makes about Edward (Track F1).

    Populated by ``@observed_tool``, heartbeat follow-ups, reflection
    miss-detection, etc. Collapsed by ``signature`` so a repeated
    failure becomes one row with an incrementing ``occurrences``.
    Surfaced in the journal, the wakeup signal pack and the Dreaming panel.
    """
    __tablename__ = "self_observations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String, nullable=False, index=True)  # tool_failure, push_ignored, retrieval_miss, ...
    source = Column(String, nullable=False, index=True)
    severity = Column(Float, default=0.3)
    confidence = Column(Float, default=0.7)
    description = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=True)
    signature = Column(String, nullable=False, unique=True, index=True)
    occurrences = Column(Integer, default=1)
    status = Column(String, default="open")  # open, superseded, acted_on, ignored
    created_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, server_default=func.now())


class SelfObservationHitModel(Base):
    """One row per ``record_observation`` call.

    ``self_observations`` collapses repeats into one row with a counter,
    which loses *when*. Hits keep the time series so a health check can
    tell whether a pattern stopped after a fix. Pruned after
    ``turn_ledger.RETENTION_DAYS``; evidence is capped at 500 chars.
    """
    __tablename__ = "self_observation_hits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    observation_id = Column(String, nullable=False, index=True)
    seen_at = Column(DateTime, nullable=False, index=True)
    evidence_json = Column(Text, nullable=True)


class TurnLogModel(Base):
    """One row per LLM turn from every entry point (chat, voice,
    heartbeat, scheduler, workers, pulse …) — the turn ledger.

    Lean on purpose: no message or response text, tools as a compact
    JSON list, pruned after ``turn_ledger.RETENTION_DAYS``. This is what
    a health check reads to judge how Edward thinks.
    """
    __tablename__ = "turn_log"

    id = Column(String, primary_key=True)
    conversation_id = Column(String, nullable=True, index=True)
    source = Column(String(40), nullable=False, index=True)
    model = Column(String, nullable=True)
    provider = Column(String(20), nullable=True)
    fast_context = Column(Boolean, default=False, nullable=False, server_default="false")
    is_worker = Column(Boolean, default=False, nullable=False, server_default="false")
    started_at = Column(DateTime, nullable=False, index=True)
    ended_at = Column(DateTime, nullable=True)
    ttft_ms = Column(Integer, nullable=True)
    total_ms = Column(Integer, nullable=True)
    iterations = Column(Integer, default=0, nullable=False, server_default="0")
    tool_calls = Column(Integer, default=0, nullable=False, server_default="0")
    tool_failures = Column(Integer, default=0, nullable=False, server_default="0")
    tools_json = Column(Text, nullable=True)
    memories_retrieved = Column(Integer, default=0, nullable=False, server_default="0")
    memories_used = Column(Integer, default=0, nullable=False, server_default="0")
    memories_extracted = Column(Integer, nullable=True)
    message_chars = Column(Integer, default=0, nullable=False, server_default="0")
    response_chars = Column(Integer, default=0, nullable=False, server_default="0")
    repeat_of_previous = Column(Boolean, default=False, nullable=False, server_default="false")
    abandoned_previous = Column(Boolean, default=False, nullable=False, server_default="false")
    outcome = Column(String(20), default="running", nullable=False, index=True)
    error_class = Column(String(160), nullable=True)
    # taint and approvals (2026-09-24): the turn read third-party text (by
    # construction or through a tool) — ``taint_service``. ALTERs in init_db.
    tainted = Column(Boolean, default=False, nullable=False, server_default="false")
    taint_reason = Column(String(80), nullable=True)


class LedgerEventModel(Base):
    """Narrow event ledger for a health check: pulse decisions, rate-gate
    deferrals, scheduled-event outcomes, browser-hand failures, voice
    session summaries. See ``services/event_ledger.py``. Pruned with the
    turn ledger (30 days); detail capped at 600 chars."""
    __tablename__ = "ledger_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(30), nullable=False, index=True)
    source = Column(String(60), nullable=False)
    outcome = Column(String(20), nullable=False)
    reason = Column(String(300), nullable=True)
    ref_id = Column(String(80), nullable=True)
    detail_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class UserSignalModel(Base):
    """The owner's implicit verdicts from the UI (a Today need acted on, an
    action that failed, …). Small rows, allowlisted kinds, pruned with
    the turn ledger."""
    __tablename__ = "user_signals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String(40), nullable=False, index=True)
    conversation_id = Column(String, nullable=True)
    ref_id = Column(String, nullable=True)
    detail_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


# --- codex call ledger (2026-09-24) ---
# New tables only (create_all makes them and their indexes); no ALTERs.

class CodexCallModel(Base):
    """One row per Codex HTTP request, success or failure — the spend
    ledger (2026-09-24).

    Written only by ``services/codex_call_ledger.py``: by
    ``ChatCodexResponses`` for every request and by ``codex_cli_service``
    for each ``codex exec`` turn (``turn_source="codex_exec"``). The quota
    is one weekly window shared by every model; ``usage_events`` labelled
    every turn ``chat`` and dropped cached and reasoning tokens, so nothing
    said who spent it. No prompt or response text. Pruned with the turn
    ledger (30 days)."""
    __tablename__ = "codex_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    purpose = Column(String(60), nullable=True)       # rate-gate caller label (build_background_llm / pick_caller)
    priority = Column(String(20), nullable=True)      # interactive | triage | background (None: codex exec, ungated)
    turn_source = Column(String(40), nullable=True)   # turn_ledger's source of the turn that caused the call
    conversation_id = Column(String, nullable=True)
    turn_id = Column(String(40), nullable=True)
    model = Column(String(60), nullable=True)
    effort = Column(String(20), nullable=True)
    input_tokens = Column(Integer, nullable=True)     # includes cached_tokens
    cached_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)    # includes reasoning_tokens
    reasoning_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    ttft_ms = Column(Integer, nullable=True)          # first answer delta (text or tool arguments)
    status = Column(String(20), nullable=False, default="ok")  # ok | error | rate_limited | timeout | cancelled
    error_class = Column(String(160), nullable=True)
    http_status = Column(Integer, nullable=True)


class LLMMeterSampleModel(Base):
    """The Codex usage meter as a time series: each reading the rate gate
    adopts (``x-codex-*`` headers or ``wham/usage``), at most one row a
    minute per window. ``llm_rate_gate_state`` keeps only the latest; the
    spend report regresses these deltas on ``codex_calls`` tokens. Pruned
    with the turn ledger."""
    __tablename__ = "llm_meter_samples"
    __table_args__ = (Index("ix_llm_meter_samples_window_sampled_at", "window", "sampled_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    sampled_at = Column(DateTime, nullable=False, index=True)
    window = Column(String(20), nullable=False)       # primary | secondary
    used_percent = Column(Float, nullable=False)
    window_minutes = Column(Integer, nullable=True)
    resets_at = Column(DateTime, nullable=True)
    source = Column(String(20), nullable=False, default="headers")  # headers | wham


# --- the pay-per-token ledger (2026-10-10) ---
# A new table only (create_all makes it and its index); no ALTERs.

class LLMCallModel(Base):
    """One row per pay-per-token model call (the Claude API), priced in
    dollars — the ledger the daily spend cap reads
    (``services/llm_spend.py``; prices in ``services/llm_pricing.py``).

    Under ``EDWARD_LLM_PROVIDER=anthropic`` there is no weekly window to
    pace against: every token is paid from a prepaid balance, so the cap
    sums ``cost_usd`` over the owner's local day and the credits view sums it
    since the balance was bought. ``input_tokens`` is the uncached
    remainder of the prompt (the API's meaning); ``output_tokens``
    includes ``thinking_tokens``. No prompt or response text.
    ``created_at`` is stamped in UTC by Python, never by the server. Kept
    ``llm_spend.RETENTION_DAYS`` (400) — longer than the other ledgers,
    because the credits remaining are a sum since the purchase date."""
    __tablename__ = "llm_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, index=True)
    provider = Column(String(20), nullable=False, default="anthropic")
    model = Column(String(60), nullable=True)
    purpose = Column(String(60), nullable=True)       # the spend label: mind:<reason>, pulse, surplus:<job>, turn:<source>, a caller
    priority = Column(String(20), nullable=True)      # interactive | triage | background
    input_tokens = Column(Integer, nullable=False, default=0)        # uncached remainder
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)        # includes thinking
    thinking_tokens = Column(Integer, nullable=False, default=0)
    web_searches = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Numeric(14, 8), nullable=False, default=0)
    latency_ms = Column(Integer, nullable=True)
    ttft_ms = Column(Integer, nullable=True)
    ok = Column(Boolean, nullable=False, default=True)
    error = Column(Text, nullable=True)
    conversation_id = Column(String, nullable=True)
    turn_source = Column(String(40), nullable=True)   # turn_ledger's source of the turn that caused the call
    turn_id = Column(String(40), nullable=True)


# --- taint and approvals (2026-09-24) ---
# A queue, not knowledge: a send held for the owner's decision
# (docs/architecture/trust-boundary.md). ``create_all`` makes the table; ``turn_log.tainted`` /
# ``taint_reason`` are added by the matching block in ``init_db``.

class PendingApprovalModel(Base):
    """A send to a third party that a tainted, unattended turn asked to make
    while ``EDWARD_TAINT_APPROVALS=on`` — held until the owner approves or denies
    it out of band (a push with Approve / Deny, or Today). Written only by
    ``services/approvals_service.py``. ``args_json`` is what runs on
    approve, exactly once; ``status`` pending → approved | denied | expired
    (24 h)."""
    __tablename__ = "pending_approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    tool_name = Column(String(80), nullable=False)
    args_json = Column(Text, nullable=False)
    summary = Column(String(300), nullable=True)
    conversation_id = Column(String, nullable=True, index=True)
    turn_id = Column(String(40), nullable=True)
    turn_source = Column(String(40), nullable=True)
    taint_reason = Column(String(80), nullable=True)
    status = Column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    decided_at = Column(DateTime, nullable=True)
    result_text = Column(Text, nullable=True)


class OrchestratorTaskModel(Base):
    """Tasks spawned by the orchestrator for parallel worker execution."""
    __tablename__ = "orchestrator_tasks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_conversation_id = Column(String, nullable=False, index=True)
    worker_conversation_id = Column(String, nullable=True, index=True)
    task_description = Column(Text, nullable=False)
    task_type = Column(String, default="internal_worker")  # internal_worker, cc_session
    model = Column(String, default="gpt-5.6-terra")
    status = Column(String, default="pending")  # pending, running, completed, failed, cancelled
    context_mode = Column(String, default="scoped")  # full, scoped, none
    context_data = Column(Text, nullable=True)  # JSON context for scoped mode
    result_summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    timeout_seconds = Column(Integer, default=300)
    cc_session_id = Column(String, nullable=True)  # Claude Code session ID if task_type=cc_session
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GmailCredentialsModel(Base):
    """OAuth credentials for Gmail API access."""
    __tablename__ = "gmail_credentials"

    id = Column(String, primary_key=True, default="default")
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    email_address = Column(String(255), nullable=True)
    last_history_id = Column(String(50), nullable=True)  # For incremental sync
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EmailActionModel(Base):
    """Audit log for email actions (delete, move to junk, unsubscribe)."""
    __tablename__ = "email_actions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email_rowid = Column(Integer, nullable=True)
    sender = Column(String(255), nullable=True)
    sender_domain = Column(String(255), nullable=True, index=True)
    subject = Column(String(500), nullable=True)
    action = Column(String(50), nullable=False)  # delete, move_to_junk, unsubscribe
    action_source = Column(String(50), nullable=False)  # llm, rule, user, auto
    status = Column(String(20), default="pending")  # pending, completed, failed
    error = Column(Text, nullable=True)
    conversation_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class EmailSenderPrefModel(Base):
    """Learned sender preferences for email handling."""
    __tablename__ = "email_sender_prefs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sender_domain = Column(String(255), unique=True, nullable=False, index=True)
    sender_address = Column(String(255), nullable=True)
    preference = Column(String(20), nullable=False)  # keep, delete, junk, unsubscribe
    source = Column(String(50), nullable=False)  # user, llm, auto_learned
    action_count = Column(Integer, default=0)
    last_applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EmailUnsubQueueModel(Base):
    """Queue of recommended unsubscribe actions."""
    __tablename__ = "email_unsub_queue"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sender_domain = Column(String(255), nullable=False, index=True)
    sender_address = Column(String(255), nullable=True)
    subject_sample = Column(String(500), nullable=True)
    email_count = Column(Integer, default=0)
    unsubscribe_method = Column(String(20), nullable=True)  # one_click, http_get, mailto, unknown
    unsubscribe_url = Column(Text, nullable=True)
    unsubscribe_mailto = Column(String(255), nullable=True)
    status = Column(String(20), default="recommended")  # recommended, approved, executing, completed, failed, skipped
    error = Column(Text, nullable=True)
    approval_conversation_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class UsageEventModel(Base):
    """Per-request token usage tracking."""
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String, nullable=False)       # "anthropic" | "openai"
    model = Column(String, nullable=False)           # e.g. "claude-sonnet-4-5-20250514"
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cache_creation_tokens = Column(Integer, default=0)
    cache_read_tokens = Column(Integer, default=0)
    conversation_id = Column(String, nullable=True)
    source = Column(String, default="chat")          # "chat" | "heartbeat" | "extraction" | "reflection" etc.
    created_at = Column(DateTime, server_default=func.now())


class EntityModel(Base):
    """Core entity table for people, projects, places, orgs, topics."""
    __tablename__ = "entities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)  # person, project, place, org, topic
    aliases = Column(Text, nullable=True)  # JSON array of alternate names
    metadata_json = Column(Text, nullable=True)  # JSON object for flexible properties
    first_seen = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now())
    mention_count = Column(Integer, default=0)

    __table_args__ = (
        Index('ix_entities_name_fts', text("to_tsvector('english', name)"),
              postgresql_using='gin'),
    )


class EntityMentionModel(Base):
    """Links entities to memories and conversations."""
    __tablename__ = "entity_mentions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id = Column(String, nullable=False, index=True)
    memory_id = Column(String, nullable=True)
    conversation_id = Column(String, nullable=True)
    relationship = Column(String, nullable=True)  # subject, mentioned, discussed, etc.
    context_snippet = Column(Text, nullable=True)

    __table_args__ = (
        Index('ix_entity_mentions_memory', 'memory_id'),
        Index('ix_entity_mentions_unique', 'entity_id', 'memory_id',
              unique=True, postgresql_where=text('memory_id IS NOT NULL')),
    )


class OpenLoopModel(Base):
    """Unresolved items Edward should track and potentially nudge about."""
    __tablename__ = "open_loops"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    description = Column(Text, nullable=False)
    source_memory_id = Column(String, nullable=True)
    source_conversation_id = Column(String, nullable=True)
    entity_id = Column(String, nullable=True)
    status = Column(String, default="open")  # open, resolved, snoozed, expired
    priority = Column(Float, default=0.5)
    detected_at = Column(DateTime, server_default=func.now())
    last_nudged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    snooze_until = Column(DateTime, nullable=True)
    nudge_count = Column(Integer, default=0)

    __table_args__ = (
        Index('ix_open_loops_active', 'status',
              postgresql_where=text("status = 'open'")),
    )


class ProactiveConfigModel(Base):
    """Single-row config for all proactive intelligence behaviors."""
    __tablename__ = "proactive_config"

    id = Column(String, primary_key=True, default="default")
    # Morning briefing
    morning_briefing_enabled = Column(Boolean, default=False)
    morning_briefing_time = Column(String, default="08:00")
    morning_briefing_channel = Column(String, default="push")  # push, imessage, chat
    # Meeting prep
    meeting_prep_enabled = Column(Boolean, default=False)
    meeting_prep_advance_minutes = Column(Integer, default=30)
    meeting_context_horizon_minutes = Column(Integer, default=60)
    meeting_followthrough_minutes = Column(Integer, default=90)
    # Pattern detection
    pattern_detection_enabled = Column(Boolean, default=False)
    # Open loop nudges
    open_loop_nudge_enabled = Column(Boolean, default=False)
    open_loop_nudge_days = Column(Integer, default=7)
    # Relationship maintenance
    relationship_maintenance_enabled = Column(Boolean, default=True)
    # Recurring entity dossiers
    dossier_enabled = Column(Boolean, default=True)
    dossier_min_acted_pushes = Column(Integer, default=2)
    dossier_min_inbound_threads = Column(Integer, default=3)
    dossier_window_days = Column(Integer, default=7)
    # Extraction depth
    extraction_depth = Column(String, default="standard")  # light, standard, deep
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class OrchestratorConfigModel(Base):
    """Configuration for the orchestrator system."""
    __tablename__ = "orchestrator_config"

    id = Column(String, primary_key=True, default="default")
    enabled = Column(Boolean, default=False)
    max_concurrent_workers = Column(Integer, default=5)
    max_concurrent_cc_sessions = Column(Integer, default=2)
    default_worker_model = Column(String, default="gpt-5.6-terra")
    default_worker_timeout = Column(Integer, default=300)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DisplayConfigModel(Base):
    """Always-on display tweaks (accent, time-of-day, panel toggles, etc.).

    Single-row config. Mutable both from the display's gear panel and from
    Edward via the ``update_display_settings`` LangGraph tool, so ``data``
    is a free-form JSON blob — keeps shape changes from needing migrations.
    """
    __tablename__ = "display_config"

    id = Column(String, primary_key=True, default="default")
    data = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String, default="system")  # "edward" | "user" | "system"


# ---------------------------------------------------------------------------
# Household hub — pet station (/hub)
# ---------------------------------------------------------------------------

class PetModel(Base):
    """The household's pets (the hub's feeding and meds board)."""
    __tablename__ = "pets"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    species = Column(String)                       # "dog" | "cat"
    photo_url = Column(String)
    sort_order = Column(Integer, default=0)        # left-to-right on the hub
    notes = Column(Text)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class CareItemModel(Base):
    """One dose slot: a bowl, a pill, or a supplement.

    Deliberately one row per *slot*, not per drug — a twice-daily
    medication is two rows (am + pm). That keeps a care day's state a
    simple "is there an event for this item today?" instead of counting
    doses against a schedule.

    ``schedule_type`` carries the alerting contract (see hub_pet_service):
      window : given whenever inside am/pm. Never overdue. Cat wet food.
      timed  : due at ``at_time``, overdue after a grace period (e.g. a 6-and-6 dose).
      prn    : as-needed home pharmacy. Logged when used, never due.
    """
    __tablename__ = "care_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pet_id = Column(String, index=True, nullable=False)
    kind = Column(String, nullable=False)          # "feeding" | "med" | "supplement"
    name = Column(String, nullable=False)
    dose = Column(String)
    schedule_type = Column(String, nullable=False, default="window")
    window = Column(String)                        # "am" | "pm" | NULL for prn
    at_time = Column(Time)                         # wall clock, timed items only
    # Set when this item rides along with another (a supplement that goes
    # into the PM bowl) — one tap logs both.
    bundle_with_id = Column(String, index=True)
    doses_remaining = Column(Integer)
    refill_threshold = Column(Integer)
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    # Seeded from a pet-sitter note with an unconfirmed drug name or
    # strength; the hub badges these until a human confirms them.
    needs_review = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class HubListItemModel(Base):
    """One line on a hub list (Shopping / Household).

    ``done_at`` drives the board's self-clearing: ticked items linger long
    enough to be un-ticked after a mis-tap, then drop off on their own.
    """
    __tablename__ = "hub_list_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    list_name = Column(String, nullable=False, index=True)
    text = Column(Text, nullable=False)
    done = Column(Boolean, default=False)
    created_by = Column(String)
    done_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    done_at = Column(DateTime)


class CareEventModel(Base):
    """A logged give. Presence of a row for (item, care day) = done."""
    __tablename__ = "care_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_item_id = Column(String, index=True, nullable=False)
    given_at = Column(DateTime, nullable=False, index=True)   # naive UTC
    # Free text, not an enum: today it is "owner"/"rowan", but the pet
    # sitter needs to log too and shouldn't require a migration.
    given_by = Column(String)
    method = Column(String, default="tap")         # "tap" | "voice"
    status = Column(String, default="given")       # "given" | "skipped" | "refused"
    note = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class WishlistItemModel(Base):
    """A feature Edward wants the owner to build (2026-09-12, replaces self-evolution).

    Edward logs what he wishes he could do — a tool he lacks, a recurring
    failure a code change would fix, something that would help the owner — and
    the owner reads the list in Settings → Work → Wishlist and moves items
    through open → planned → building → done (or declined). The same wish
    voiced again is merged: ``times_wanted`` counts how often it came up.
    """
    __tablename__ = "wishlist_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(200), nullable=False)
    why = Column(Text, nullable=False, default="")
    detail = Column(Text, nullable=True)
    status = Column(String(20), default="open")      # open | planned | building | done | declined
    priority = Column(Integer, default=2)            # 1 high, 2 normal, 3 low
    source = Column(String(20), default="edward")    # edward | owner
    conversation_id = Column(String, nullable=True)  # the chat where Edward wanted it
    times_wanted = Column(Integer, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


LLM_DB_ROLE = "edward_llm"


def llm_role_grant_statements(schema_name: str) -> list:
    """Grants that give the low-privilege query_persistent_db role full use of
    one edward_db_* schema. The role deliberately has NO grants on public —
    the 2026-07-05 incident showed user SQL from the LLM tool could write any
    public table (it mass-disabled `skills`).
    """
    return [
        f'GRANT USAGE, CREATE ON SCHEMA "{schema_name}" TO {LLM_DB_ROLE}',
        f'GRANT ALL ON ALL TABLES IN SCHEMA "{schema_name}" TO {LLM_DB_ROLE}',
        f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA "{schema_name}" TO {LLM_DB_ROLE}',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema_name}" GRANT ALL ON TABLES TO {LLM_DB_ROLE}',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema_name}" GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {LLM_DB_ROLE}',
    ]


_RESYNC_SEQUENCES_SQL = """
SELECT quote_ident(ns.nspname) || '.' || quote_ident(t.relname) AS tbl,
       quote_ident(a.attname)                                   AS col,
       quote_ident(sn.nspname) || '.' || quote_ident(s.relname)  AS seq
FROM pg_class s
JOIN pg_namespace sn ON sn.oid = s.relnamespace
JOIN pg_depend d  ON d.objid = s.oid AND d.deptype = 'a'
JOIN pg_class t   ON t.oid = d.refobjid
JOIN pg_namespace ns ON ns.oid = t.relnamespace
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
WHERE s.relkind = 'S' AND ns.nspname = 'public'
"""


async def resync_sequences(conn=None) -> list[str]:
    """Advance any owned sequence that has fallen behind its column.

    A ``pg_dump`` restore writes rows with their original ids and never
    touches the owning sequence, so after one the sequence points at a value
    the table has already used and every insert dies on a primary-key
    collision. That is what the 2026-09-07 restore did here, and nothing
    noticed for three days: ``imessage_outgoing_sends`` (the ledger the
    heartbeat listener uses to recognise Edward's own echoed replies) sat at
    ``last_value=20`` against ``max(id)=176``, so echo suppression silently
    degraded to the in-memory deque the ledger was written to replace, and
    ``skills_audit`` — the guardrail from the 2026-07 mass skill-disable —
    was failing to record anything at all.

    ``setval(seq, max(id), true)`` is idempotent and only ever moves a
    sequence forward: a healthy sequence keeps handing out consecutive ids,
    and an empty table is left where it is. Returns the sequences repaired.
    """
    async def _run(c) -> list[str]:
        repaired: list[str] = []
        rows = (await c.execute(text(_RESYNC_SEQUENCES_SQL))).fetchall()
        for tbl, col, seq in rows:
            try:
                behind = (await c.execute(text(
                    f"SELECT last_value, is_called, "
                    f"(SELECT max({col}) FROM {tbl}) AS max_id FROM {seq}"
                ))).first()
                if behind is None:
                    continue
                last_value, is_called, max_id = behind
                if max_id is None:
                    continue
                # Next id is last_value+1 when is_called, else last_value.
                next_id = last_value + 1 if is_called else last_value
                if next_id > max_id:
                    continue
                await c.execute(text(f"SELECT setval('{seq}', :m, true)"), {"m": max_id})
                repaired.append(f"{seq} -> {max_id} (was {last_value})")
            except Exception as exc:
                # One odd table must never stop the backend from starting.
                print(f"[db] sequence resync skipped {seq}: {exc}")
        if repaired:
            print(f"[db] resynced {len(repaired)} sequence(s): {', '.join(repaired)}")
        return repaired

    if conn is not None:
        return await _run(conn)
    async with engine.begin() as own:
        return await _run(own)


async def init_db():
    """Initialize the database tables."""
    async with engine.begin() as conn:
        # Bound this transaction so a stuck ALTER TABLE can never pin a table
        # for days (see 2026-05-22 incident: an unfinished ALTER held an
        # ACCESS EXCLUSIVE lock for 4 days and exhausted the next backend's
        # pool). lock_timeout aborts if we can't acquire the lock quickly;
        # statement_timeout caps any single DDL/migration step.
        await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        await conn.execute(text("SET LOCAL statement_timeout = '60s'"))
        # Enable pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Create tables
        await conn.run_sync(Base.metadata.create_all)

        # Repair any sequence left behind by a restore before anything
        # tries to insert against it (2026-09-07 — see resync_sequences).
        await resync_sequences(conn)

        # Migration: Add new columns to memories table if they don't exist
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS user_id VARCHAR;
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();
        """))
        # Migration: Add temporal_nature column to memories if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS temporal_nature VARCHAR DEFAULT 'timeless';
        """))
        # Migration: Add temporal-edge columns (C Phase 1). NULL valid_until
        # means the belief is still valid; a non-NULL past timestamp means
        # consolidation retired it.
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP;
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP;
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_memories_valid_until ON memories(valid_until);
        """))
        # Create index on user_id if it doesn't exist
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_memories_user_id ON memories(user_id);
        """))


        # Create conversations table if it doesn't exist (handled by create_all above)
        # Add index for sorting conversations by updated_at
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_conversations_updated_at ON conversations(updated_at DESC);
        """))


        # Migration: Add initial_message column for Edward-initiated thought
        # conversations (proactive pushes that need a real chat thread to land on).
        await conn.execute(text("""
            ALTER TABLE conversations ADD COLUMN IF NOT EXISTS initial_message TEXT;
        """))
        # Migration: Add context_pack — JSON of the wake-up signal pack so
        # the owner's first reply turn has the original context. Without this, the
        # thought conversation is amnesiac the moment the owner types.
        await conn.execute(text("""
            ALTER TABLE conversations ADD COLUMN IF NOT EXISTS context_pack TEXT;
        """))

        # Migration: Add source column to conversations if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE conversations ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'user';
        """))

        # Migration: Add notified_user column to conversations if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE conversations ADD COLUMN IF NOT EXISTS notified_user BOOLEAN DEFAULT false;
        """))

        # Migration: Add channel column to conversations if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE conversations ADD COLUMN IF NOT EXISTS channel VARCHAR DEFAULT 'text';
        """))

        # Migration: Add search_tags column and FTS index for conversation search
        await conn.execute(text("""
            ALTER TABLE conversations ADD COLUMN IF NOT EXISTS search_tags TEXT;
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_conversations_search_fts
            ON conversations USING gin(to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(search_tags, '')));
        """))

        # Backfill: make existing conversations searchable by title immediately
        await conn.execute(text("""
            UPDATE conversations SET search_tags = title WHERE search_tags IS NULL AND title IS NOT NULL;
        """))

        # Create external_contacts table index if it doesn't exist
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_external_contacts_phone ON external_contacts(phone_number);
        """))

        # Migration: Add last_channel column to external_contacts if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE external_contacts ADD COLUMN IF NOT EXISTS last_channel VARCHAR DEFAULT 'sms';
        """))

        # Scheduled events: partial index for efficient due-event polling
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_scheduled_events_next_fire
            ON scheduled_events(next_fire_at)
            WHERE status = 'pending';
        """))

        # Skills table is created by create_all above

        # Migration: Add password_hash column to settings if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE settings ADD COLUMN IF NOT EXISTS password_hash VARCHAR;
        """))

        # Migration: Add provider column to settings if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE settings ADD COLUMN IF NOT EXISTS provider VARCHAR DEFAULT 'anthropic';
        """))

        # Migration: debug_streaming flag — gates per-memory and per-tool
        # internal SSE events for the debug panel.
        await conn.execute(text("""
            ALTER TABLE settings ADD COLUMN IF NOT EXISTS debug_streaming BOOLEAN NOT NULL DEFAULT FALSE;
        """))

        # Migration: user-configurable IANA timezone, single source of truth
        # for both Edward's prompt and frontend timestamp rendering.
        await conn.execute(text("""
            ALTER TABLE settings ADD COLUMN IF NOT EXISTS timezone VARCHAR NOT NULL DEFAULT 'America/Chicago';
        """))

        # Push subscriptions table: index on endpoint for upsert operations
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_push_subscriptions_endpoint ON push_subscriptions(endpoint);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_push_subscriptions_active ON push_subscriptions(is_active)
            WHERE is_active = true;
        """))

        # Persistent databases table: index on name for lookups
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_persistent_databases_name ON persistent_databases(name);
        """))

        # Documents table: migration safety for existing databases
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_documents_user_id ON documents(user_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_documents_updated_at ON documents(updated_at DESC);
        """))

        # Files table: indexes for common queries
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_files_category ON files(category);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_files_source ON files(source);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_files_created_at ON files(created_at);
        """))

        # Heartbeat: migration for allowed_senders column
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS allowed_senders TEXT;
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_coordination_thread_state_chat
            ON coordination_thread_state(source, chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_coordination_thread_state_expires
            ON coordination_thread_state(expires_at);
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS state_type VARCHAR(40);
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS open_question_present BOOLEAN NOT NULL DEFAULT FALSE;
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS proposed_next_step TEXT;
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS last_plan_change_at TIMESTAMP;
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS awaiting_actor VARCHAR(20);
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS closure_evidence TEXT;
        """))
        await conn.execute(text("""
            ALTER TABLE coordination_thread_state ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0;
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_live_conversation_sessions_chat
            ON live_conversation_sessions(source, chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_live_conversation_sessions_active_expires
            ON live_conversation_sessions(session_active, expires_at);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_coordination_cases_chat_status
            ON coordination_cases(source, chat_identifier, status);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_coordination_cases_expires
            ON coordination_cases(expires_at);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_admin_obligation_state_chat
            ON admin_obligation_state(source, chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_admin_obligation_state_expires
            ON admin_obligation_state(expires_at);
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS dismiss_reply_review_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS dismiss_reply_review_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS dismiss_reply_review_min_count INTEGER DEFAULT 1;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS dismiss_reply_review_reply_window_hours INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS active_chat_question_override_window_hours INTEGER DEFAULT 72;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_history_messages INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_min_history_messages INTEGER DEFAULT 8;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_min_reply_count INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_reply_rate_threshold DOUBLE PRECISION DEFAULT 0.35;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_reply_window_hours INTEGER DEFAULT 72;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_affinity_false_dismissal_count_threshold INTEGER DEFAULT 1;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_reply_prior_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_reply_prior_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_reply_prior_min_count INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_reply_prior_max_boost DOUBLE PRECISION DEFAULT 0.16;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_propensity_window_size INTEGER DEFAULT 20;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_propensity_smoothing_constant DOUBLE PRECISION DEFAULT 4.0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_propensity_cutoff DOUBLE PRECISION DEFAULT 0.65;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_propensity_max_boost DOUBLE PRECISION DEFAULT 0.15;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_window_hours INTEGER DEFAULT 24;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_half_life_hours INTEGER DEFAULT 168;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_activation DOUBLE PRECISION DEFAULT 3.0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_max_boost DOUBLE PRECISION DEFAULT 0.15;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_audit_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_audit_min_dismissals INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_audit_rate_threshold DOUBLE PRECISION DEFAULT 0.4;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_audit_min_replies INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS reply_after_dismiss_audit_max_boost DOUBLE PRECISION DEFAULT 0.15;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_bias_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_bias_min_dismissed INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_bias_min_mismatches INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_bias_rate_threshold DOUBLE PRECISION DEFAULT 0.67;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_bias_max_boost DOUBLE PRECISION DEFAULT 0.18;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS chat_audit_mismatch_override_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS chat_audit_mismatch_override_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS chat_audit_mismatch_override_min_count INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS chat_audit_mismatch_override_max_boost DOUBLE PRECISION DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS stable_contact_dismiss_mismatch_override_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS stable_contact_dismiss_mismatch_override_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS stable_contact_dismiss_mismatch_override_min_count INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS stable_contact_dismiss_mismatch_override_max_boost DOUBLE PRECISION DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS false_dismiss_contact_override_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS false_dismiss_contact_override_threshold INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS false_dismiss_contact_override_lookback_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_level_triage_override_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_level_triage_override_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_level_triage_override_min_mismatches INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_level_triage_override_max_adjustment DOUBLE PRECISION DEFAULT 0.12;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_mismatch_adjustment_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_mismatch_adjustment_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_mismatch_adjustment_min_count INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_mismatch_adjustment_score_penalty DOUBLE PRECISION DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_triage_mismatch_adjustment_max_boost DOUBLE PRECISION DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_engagement_boost_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_engagement_boost_min_dismissals INTEGER DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_engagement_boost_min_replies INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_engagement_boost_rate_threshold DOUBLE PRECISION DEFAULT 0.5;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_engagement_boost_max_boost DOUBLE PRECISION DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN dismiss_reply_review_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN dismiss_reply_review_min_count SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN dismiss_reply_review_reply_window_hours SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN active_chat_question_override_window_hours SET DEFAULT 72;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_history_messages SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_min_history_messages SET DEFAULT 8;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_min_reply_count SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_reply_rate_threshold SET DEFAULT 0.35;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_reply_window_hours SET DEFAULT 72;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_affinity_false_dismissal_count_threshold SET DEFAULT 1;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_reply_prior_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_reply_prior_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_reply_prior_min_count SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_reply_prior_max_boost SET DEFAULT 0.16;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_propensity_window_size SET DEFAULT 20;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_propensity_smoothing_constant SET DEFAULT 4.0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_propensity_cutoff SET DEFAULT 0.65;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_propensity_max_boost SET DEFAULT 0.15;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_window_hours SET DEFAULT 24;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_half_life_hours SET DEFAULT 168;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_activation SET DEFAULT 3.0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_max_boost SET DEFAULT 0.15;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_audit_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_audit_min_dismissals SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_audit_rate_threshold SET DEFAULT 0.4;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_audit_min_replies SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN reply_after_dismiss_audit_max_boost SET DEFAULT 0.15;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_bias_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_bias_min_dismissed SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_bias_min_mismatches SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_bias_rate_threshold SET DEFAULT 0.67;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_bias_max_boost SET DEFAULT 0.18;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN chat_audit_mismatch_override_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN chat_audit_mismatch_override_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN chat_audit_mismatch_override_min_count SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN chat_audit_mismatch_override_max_boost SET DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN stable_contact_dismiss_mismatch_override_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN stable_contact_dismiss_mismatch_override_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN stable_contact_dismiss_mismatch_override_min_count SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN stable_contact_dismiss_mismatch_override_max_boost SET DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN false_dismiss_contact_override_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN false_dismiss_contact_override_threshold SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN false_dismiss_contact_override_lookback_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_level_triage_override_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_level_triage_override_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_level_triage_override_min_mismatches SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_level_triage_override_max_adjustment SET DEFAULT 0.12;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_mismatch_adjustment_enabled SET DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_mismatch_adjustment_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_mismatch_adjustment_min_count SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_mismatch_adjustment_score_penalty SET DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_triage_mismatch_adjustment_max_boost SET DEFAULT 0.2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_engagement_boost_window_days SET DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_engagement_boost_min_dismissals SET DEFAULT 3;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_engagement_boost_min_replies SET DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_engagement_boost_rate_threshold SET DEFAULT 0.5;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ALTER COLUMN contact_engagement_boost_max_boost SET DEFAULT 0.2;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET dismiss_reply_review_window_days = 30
            WHERE dismiss_reply_review_window_days IS NULL
               OR dismiss_reply_review_window_days IN (14, 21);
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_mismatch_adjustment_enabled = true
            WHERE contact_triage_mismatch_adjustment_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_level_triage_override_enabled = true
            WHERE contact_level_triage_override_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_level_triage_override_window_days = 30
            WHERE contact_level_triage_override_window_days IS NULL
               OR contact_level_triage_override_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_level_triage_override_min_mismatches = 3
            WHERE contact_level_triage_override_min_mismatches IS NULL
               OR contact_level_triage_override_min_mismatches <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_level_triage_override_max_adjustment = 0.12
            WHERE contact_level_triage_override_max_adjustment IS NULL
               OR contact_level_triage_override_max_adjustment < 0
               OR contact_level_triage_override_max_adjustment > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_mismatch_adjustment_window_days = 30
            WHERE contact_triage_mismatch_adjustment_window_days IS NULL
               OR contact_triage_mismatch_adjustment_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_mismatch_adjustment_min_count = 3
            WHERE contact_triage_mismatch_adjustment_min_count IS NULL
               OR contact_triage_mismatch_adjustment_min_count <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_mismatch_adjustment_score_penalty = 0.2
            WHERE contact_triage_mismatch_adjustment_score_penalty IS NULL
               OR contact_triage_mismatch_adjustment_score_penalty < 0
               OR contact_triage_mismatch_adjustment_score_penalty > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_mismatch_adjustment_max_boost = 0.2
            WHERE contact_triage_mismatch_adjustment_max_boost IS NULL
               OR contact_triage_mismatch_adjustment_max_boost < 0
               OR contact_triage_mismatch_adjustment_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_engagement_boost_window_days = 30
            WHERE contact_engagement_boost_window_days IS NULL
               OR contact_engagement_boost_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_engagement_boost_min_dismissals = 3
            WHERE contact_engagement_boost_min_dismissals IS NULL
               OR contact_engagement_boost_min_dismissals <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_engagement_boost_min_replies = 2
            WHERE contact_engagement_boost_min_replies IS NULL
               OR contact_engagement_boost_min_replies <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_engagement_boost_rate_threshold = 0.5
            WHERE contact_engagement_boost_rate_threshold IS NULL
               OR contact_engagement_boost_rate_threshold <= 0
               OR contact_engagement_boost_rate_threshold > 1;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_engagement_boost_max_boost = 0.2
            WHERE contact_engagement_boost_max_boost IS NULL
               OR contact_engagement_boost_max_boost < 0
               OR contact_engagement_boost_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET dismiss_reply_review_min_count = 2
            WHERE dismiss_reply_review_min_count IS NULL
               OR dismiss_reply_review_min_count = 1;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET dismiss_reply_review_reply_window_hours = 2
            WHERE dismiss_reply_review_reply_window_hours IS NULL
               OR dismiss_reply_review_reply_window_hours = 24;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET active_chat_question_override_window_hours = 72
            WHERE active_chat_question_override_window_hours IS NULL
               OR active_chat_question_override_window_hours <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_enabled = true
            WHERE reply_affinity_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_history_messages = 30
            WHERE reply_affinity_history_messages IS NULL
               OR reply_affinity_history_messages <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_min_history_messages = 8
            WHERE reply_affinity_min_history_messages IS NULL
               OR reply_affinity_min_history_messages <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_min_reply_count = 3
            WHERE reply_affinity_min_reply_count IS NULL
               OR reply_affinity_min_reply_count <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_reply_rate_threshold = 0.35
            WHERE reply_affinity_reply_rate_threshold IS NULL
               OR reply_affinity_reply_rate_threshold <= 0
               OR reply_affinity_reply_rate_threshold > 1;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_reply_window_hours = 72
            WHERE reply_affinity_reply_window_hours IS NULL
               OR reply_affinity_reply_window_hours <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_affinity_false_dismissal_count_threshold = 1
            WHERE reply_affinity_false_dismissal_count_threshold IS NULL
               OR reply_affinity_false_dismissal_count_threshold <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_reply_prior_enabled = true
            WHERE contact_reply_prior_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_reply_prior_window_days = 30
            WHERE contact_reply_prior_window_days IS NULL
               OR contact_reply_prior_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_reply_prior_min_count = 2
            WHERE contact_reply_prior_min_count IS NULL
               OR contact_reply_prior_min_count < 2;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_reply_prior_max_boost = 0.16
            WHERE contact_reply_prior_max_boost IS NULL
               OR contact_reply_prior_max_boost < 0
               OR contact_reply_prior_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET chat_audit_mismatch_override_enabled = true
            WHERE chat_audit_mismatch_override_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET chat_audit_mismatch_override_window_days = 30
            WHERE chat_audit_mismatch_override_window_days IS NULL
               OR chat_audit_mismatch_override_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET chat_audit_mismatch_override_min_count = 2
            WHERE chat_audit_mismatch_override_min_count IS NULL
               OR chat_audit_mismatch_override_min_count <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET chat_audit_mismatch_override_max_boost = 0.2
            WHERE chat_audit_mismatch_override_max_boost IS NULL
               OR chat_audit_mismatch_override_max_boost < 0
               OR chat_audit_mismatch_override_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET stable_contact_dismiss_mismatch_override_enabled = true
            WHERE stable_contact_dismiss_mismatch_override_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET stable_contact_dismiss_mismatch_override_window_days = 30
            WHERE stable_contact_dismiss_mismatch_override_window_days IS NULL
               OR stable_contact_dismiss_mismatch_override_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET stable_contact_dismiss_mismatch_override_min_count = 2
            WHERE stable_contact_dismiss_mismatch_override_min_count IS NULL
               OR stable_contact_dismiss_mismatch_override_min_count <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET stable_contact_dismiss_mismatch_override_max_boost = 0.2
            WHERE stable_contact_dismiss_mismatch_override_max_boost IS NULL
               OR stable_contact_dismiss_mismatch_override_max_boost < 0
               OR stable_contact_dismiss_mismatch_override_max_boost > 0.5
               OR stable_contact_dismiss_mismatch_override_max_boost = 0.18;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET false_dismiss_contact_override_enabled = true
            WHERE false_dismiss_contact_override_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET false_dismiss_contact_override_threshold = 3
            WHERE false_dismiss_contact_override_threshold IS NULL
               OR false_dismiss_contact_override_threshold <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET false_dismiss_contact_override_lookback_days = 30
            WHERE false_dismiss_contact_override_lookback_days IS NULL
               OR false_dismiss_contact_override_lookback_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_propensity_window_size = 20
            WHERE reply_propensity_window_size IS NULL
               OR reply_propensity_window_size <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_propensity_smoothing_constant = 4.0
            WHERE reply_propensity_smoothing_constant IS NULL
               OR reply_propensity_smoothing_constant < 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_propensity_cutoff = 0.65
            WHERE reply_propensity_cutoff IS NULL
               OR reply_propensity_cutoff <= 0
               OR reply_propensity_cutoff >= 1;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_propensity_max_boost = 0.15
            WHERE reply_propensity_max_boost IS NULL
               OR reply_propensity_max_boost < 0
               OR reply_propensity_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_enabled = true
            WHERE reply_after_dismiss_enabled IS NULL;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_window_hours = 24
            WHERE reply_after_dismiss_window_hours IS NULL
               OR reply_after_dismiss_window_hours <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_half_life_hours = 168
            WHERE reply_after_dismiss_half_life_hours IS NULL
               OR reply_after_dismiss_half_life_hours <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_activation = 3.0
            WHERE reply_after_dismiss_activation IS NULL
               OR reply_after_dismiss_activation <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_max_boost = 0.15
            WHERE reply_after_dismiss_max_boost IS NULL
               OR reply_after_dismiss_max_boost < 0
               OR reply_after_dismiss_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_audit_window_days = 30
            WHERE reply_after_dismiss_audit_window_days IS NULL
               OR reply_after_dismiss_audit_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_audit_min_dismissals = 2
            WHERE reply_after_dismiss_audit_min_dismissals IS NULL
               OR reply_after_dismiss_audit_min_dismissals <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_audit_rate_threshold = 0.4
            WHERE reply_after_dismiss_audit_rate_threshold IS NULL
               OR reply_after_dismiss_audit_rate_threshold <= 0
               OR reply_after_dismiss_audit_rate_threshold > 1;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_audit_min_replies = 2
            WHERE reply_after_dismiss_audit_min_replies IS NULL
               OR reply_after_dismiss_audit_min_replies <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET reply_after_dismiss_audit_max_boost = 0.15
            WHERE reply_after_dismiss_audit_max_boost IS NULL
               OR reply_after_dismiss_audit_max_boost < 0
               OR reply_after_dismiss_audit_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_bias_window_days = 30
            WHERE contact_triage_bias_window_days IS NULL
               OR contact_triage_bias_window_days <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_bias_min_dismissed = 3
            WHERE contact_triage_bias_min_dismissed IS NULL
               OR contact_triage_bias_min_dismissed <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_bias_min_mismatches = 3
            WHERE contact_triage_bias_min_mismatches IS NULL
               OR contact_triage_bias_min_mismatches <= 0;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_bias_rate_threshold = 0.67
            WHERE contact_triage_bias_rate_threshold IS NULL
               OR contact_triage_bias_rate_threshold <= 0
               OR contact_triage_bias_rate_threshold > 1;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET contact_triage_bias_max_boost = 0.18
            WHERE contact_triage_bias_max_boost IS NULL
               OR contact_triage_bias_max_boost < 0
               OR contact_triage_bias_max_boost > 0.5;
        """))
        await conn.execute(text("""
            UPDATE heartbeat_config
            SET chat_audit_mismatch_override_min_count = 2
            WHERE chat_audit_mismatch_override_min_count IS NULL
               OR chat_audit_mismatch_override_min_count <= 0;
        """))

        # Heartbeat: per-track config columns
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_enabled BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_poll_seconds INTEGER DEFAULT 10;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS calendar_enabled BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS calendar_poll_seconds INTEGER DEFAULT 300;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS calendar_lookahead_minutes INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS email_enabled BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS email_poll_seconds INTEGER DEFAULT 300;
        """))

        # Heartbeat: Gmail track config columns
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS gmail_enabled BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS gmail_poll_seconds INTEGER DEFAULT 120;
        """))

        # Heartbeat: per-contact reply-after-dismiss priority adjustment
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_enabled BOOLEAN DEFAULT true;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_window_days INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_lookback_limit INTEGER DEFAULT 20;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_min_dismissed INTEGER DEFAULT 4;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_min_replied INTEGER DEFAULT 2;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_rate_threshold DOUBLE PRECISION DEFAULT 0.6;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_smoothing_constant DOUBLE PRECISION DEFAULT 4.0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS contact_priority_adjustment_max_boost DOUBLE PRECISION DEFAULT 0.18;
        """))

        # Heartbeat: email persistent high-water mark
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS email_last_rowid INTEGER;
        """))

        # Heartbeat: iMessage persistent high-water mark (Fix #2)
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_last_rowid INTEGER;
        """))

        # Heartbeat: iMessage thread-context depth (group chats need more)
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_thread_limit_direct INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_thread_limit_group INTEGER DEFAULT 75;
        """))

        # Heartbeat: iMessage outgoing-send ledger (self-trigger dedup)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS imessage_outgoing_sends (
                id SERIAL PRIMARY KEY,
                chat_identifier VARCHAR(255),
                text_hash VARCHAR(64) NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))

        # Pings, the send ledger's read-back columns and media items (2026-10-11).
        from services.pings import schema as _pings_schema
        from services.media import schema as _media_schema
        await conn.run_sync(_pings_schema.METADATA.create_all)
        await conn.run_sync(_media_schema.METADATA.create_all)
        for _stmt in _pings_schema.ALTERS:
            await conn.execute(text(_stmt))

        # Persistent iMessage chat → conversation mapping. Stable across
        # listening-window expiry, restarts, days — so the same group chat
        # always lands in the same LangGraph thread.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS imessage_chat_conversations (
                chat_identifier VARCHAR(255) PRIMARY KEY,
                conversation_id VARCHAR NOT NULL,
                chat_name VARCHAR,
                is_group BOOLEAN DEFAULT false,
                last_active TIMESTAMPTZ DEFAULT NOW(),
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_imessage_outgoing_sends_hash_time
            ON imessage_outgoing_sends(text_hash, sent_at);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_false_dismissal_signals (
                id VARCHAR PRIMARY KEY,
                chat_identifier VARCHAR(255) NOT NULL,
                normalized_chat_key VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                dismissed_event_id VARCHAR NOT NULL UNIQUE,
                reply_event_id VARCHAR NOT NULL,
                dismissed_at TIMESTAMP NOT NULL,
                reply_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_false_dismissal_chat_created
            ON heartbeat_false_dismissal_signals(chat_identifier, created_at DESC);
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_false_dismissal_signals
            ADD COLUMN IF NOT EXISTS normalized_chat_key VARCHAR(255);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_false_dismissal_normalized_chat_created
            ON heartbeat_false_dismissal_signals(normalized_chat_key, created_at DESC);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_chat_backoffs (
                chat_identifier VARCHAR(255) PRIMARY KEY,
                rolling_false_dismissal_count INTEGER DEFAULT 0,
                last_false_dismissal_at TIMESTAMP,
                last_reply_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_adaptive_dismissal_counters (
                counter_key VARCHAR(255) PRIMARY KEY,
                key_type VARCHAR(20) NOT NULL,
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                dismissed_but_replied_count INTEGER NOT NULL DEFAULT 0,
                last_dismissed_at TIMESTAMP,
                last_reply_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_adaptive_dismissal_chat
            ON heartbeat_adaptive_dismissal_counters(chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_adaptive_dismissal_sender
            ON heartbeat_adaptive_dismissal_counters(sender);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_contact_affinity (
                contact_key VARCHAR(255) PRIMARY KEY,
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                dismissed_but_replied_count INTEGER NOT NULL DEFAULT 0,
                audited_dismissal_count INTEGER NOT NULL DEFAULT 0,
                reply_after_dismiss_count INTEGER NOT NULL DEFAULT 0,
                reply_after_dismiss_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                keep_prior_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                reply_propensity_inbound_count INTEGER NOT NULL DEFAULT 0,
                reply_propensity_replied_count INTEGER NOT NULL DEFAULT 0,
                reply_propensity_raw_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                reply_propensity_smoothed_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                reply_propensity_boost DOUBLE PRECISION NOT NULL DEFAULT 0,
                last_mismatch_at TIMESTAMP,
                last_audited_at TIMESTAMP,
                last_reply_at TIMESTAMP,
                reply_propensity_computed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS audited_dismissal_count INTEGER NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_after_dismiss_count INTEGER NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_contact_triage_calibration (
                id VARCHAR PRIMARY KEY,
                contact_key VARCHAR(255) NOT NULL,
                person_key VARCHAR(255),
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                event_id VARCHAR NOT NULL UNIQUE,
                triage_status VARCHAR(40) NOT NULL,
                outcome VARCHAR(80) NOT NULL,
                user_replied BOOLEAN NOT NULL DEFAULT false,
                event_at TIMESTAMP NOT NULL,
                outcome_at TIMESTAMP NOT NULL,
                reply_event_id VARCHAR,
                reply_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_triage_calibration_contact_key
            ON heartbeat_contact_triage_calibration(contact_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_triage_calibration_person_key
            ON heartbeat_contact_triage_calibration(person_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_triage_calibration_event_id
            ON heartbeat_contact_triage_calibration(event_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_triage_calibration_outcome
            ON heartbeat_contact_triage_calibration(outcome);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_triage_calibration_event_at
            ON heartbeat_contact_triage_calibration(event_at);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_triage_calibration_outcome_at
            ON heartbeat_contact_triage_calibration(outcome_at);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_dismiss_calibration_profiles (
                profile_key VARCHAR(255) PRIMARY KEY,
                key_type VARCHAR(40) NOT NULL,
                person_key VARCHAR(255),
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                dismiss_false_positive_count INTEGER NOT NULL DEFAULT 0,
                dismiss_true_negative_count INTEGER NOT NULL DEFAULT 0,
                decayed_false_positive_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                decayed_true_negative_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                recent_false_dismiss_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                message_volume INTEGER NOT NULL DEFAULT 0,
                last_false_dismiss_at TIMESTAMP,
                last_outcome_at TIMESTAMP,
                active_calibration_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_dismiss_calibration_person_key
            ON heartbeat_dismiss_calibration_profiles(person_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_dismiss_calibration_chat_identifier
            ON heartbeat_dismiss_calibration_profiles(chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_dismiss_calibration_sender
            ON heartbeat_dismiss_calibration_profiles(sender);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_dismiss_calibration_active_until
            ON heartbeat_dismiss_calibration_profiles(active_calibration_until);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_reciprocity_style_profiles (
                profile_key VARCHAR(255) NOT NULL,
                ack_class VARCHAR(40) NOT NULL,
                person_key VARCHAR(160),
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                decayed_observed_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                decayed_replied_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                smoothed_reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                raw_observed_count INTEGER NOT NULL DEFAULT 0,
                raw_replied_count INTEGER NOT NULL DEFAULT 0,
                decayed_recent_thread_observed_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                decayed_recent_thread_replied_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                raw_recent_thread_observed_count INTEGER NOT NULL DEFAULT 0,
                raw_recent_thread_replied_count INTEGER NOT NULL DEFAULT 0,
                last_observed_at TIMESTAMP,
                last_replied_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (profile_key, ack_class)
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reciprocity_style_person
            ON heartbeat_reciprocity_style_profiles(person_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reciprocity_style_chat
            ON heartbeat_reciprocity_style_profiles(chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reciprocity_style_sender
            ON heartbeat_reciprocity_style_profiles(sender);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_reply_expectation_profiles (
                profile_key VARCHAR(320) PRIMARY KEY,
                stable_contact_key VARCHAR(255) NOT NULL,
                channel_type VARCHAR(40) NOT NULL,
                thread_context_key VARCHAR(255),
                person_key VARCHAR(160),
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                observed_inbound_count INTEGER NOT NULL DEFAULT 0,
                replied_inbound_count INTEGER NOT NULL DEFAULT 0,
                decayed_observed_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                decayed_replied_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                median_reply_latency_minutes DOUBLE PRECISION,
                terse_confirmation_observed_count INTEGER NOT NULL DEFAULT 0,
                terse_confirmation_replied_count INTEGER NOT NULL DEFAULT 0,
                terse_confirmation_reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                ack_based_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                active_coordination_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                mismatch_count INTEGER NOT NULL DEFAULT 0,
                expectation_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                profile_json TEXT,
                last_observed_at TIMESTAMP,
                last_replied_at TIMESTAMP,
                computed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reply_expectation_contact_channel
            ON heartbeat_reply_expectation_profiles(stable_contact_key, channel_type);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reply_expectation_contact_context
            ON heartbeat_reply_expectation_profiles(stable_contact_key, thread_context_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reply_expectation_person_key
            ON heartbeat_reply_expectation_profiles(person_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reply_expectation_chat_identifier
            ON heartbeat_reply_expectation_profiles(chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_reply_expectation_sender
            ON heartbeat_reply_expectation_profiles(sender);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_engagement_profiles (
                profile_key VARCHAR(320) PRIMARY KEY,
                stable_contact_key VARCHAR(255) NOT NULL,
                channel_type VARCHAR(40) NOT NULL,
                thread_context_key VARCHAR(255),
                person_key VARCHAR(160),
                chat_identifier VARCHAR(255),
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                observed_inbound_count INTEGER NOT NULL DEFAULT 0,
                replied_inbound_count INTEGER NOT NULL DEFAULT 0,
                decayed_observed_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                decayed_replied_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                decayed_reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                median_reply_latency_minutes DOUBLE PRECISION,
                dismissed_observed_count INTEGER NOT NULL DEFAULT 0,
                dismissed_then_replied_count INTEGER NOT NULL DEFAULT 0,
                dismissed_then_replied_fraction DOUBLE PRECISION NOT NULL DEFAULT 0,
                thread_participation_breadth INTEGER NOT NULL DEFAULT 0,
                context_signal_count INTEGER NOT NULL DEFAULT 0,
                context_signal_replied_count INTEGER NOT NULL DEFAULT 0,
                context_signal_reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                interpersonal_signal_count INTEGER NOT NULL DEFAULT 0,
                interpersonal_signal_replied_count INTEGER NOT NULL DEFAULT 0,
                interpersonal_signal_reply_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                engagement_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                sufficient_evidence BOOLEAN NOT NULL DEFAULT false,
                reset_generation INTEGER NOT NULL DEFAULT 0,
                profile_json TEXT,
                last_observed_at TIMESTAMP,
                last_replied_at TIMESTAMP,
                computed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_engagement_contact_channel
            ON heartbeat_engagement_profiles(stable_contact_key, channel_type);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_engagement_contact_context
            ON heartbeat_engagement_profiles(stable_contact_key, thread_context_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_engagement_person_key
            ON heartbeat_engagement_profiles(person_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_engagement_chat_identifier
            ON heartbeat_engagement_profiles(chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_engagement_sender
            ON heartbeat_engagement_profiles(sender);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS communication_style_profiles (
                profile_key VARCHAR(255) PRIMARY KEY,
                person_key VARCHAR(160) NOT NULL,
                channel_type VARCHAR(40) NOT NULL,
                interaction_context VARCHAR(80) NOT NULL DEFAULT 'general',
                contact_name VARCHAR(255),
                style_profile_json TEXT,
                evidence_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                raw_evidence_count INTEGER NOT NULL DEFAULT 0,
                decayed_evidence_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                last_confirmed_at TIMESTAMP,
                provenance_json TEXT,
                computed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_comm_style_person_channel
            ON communication_style_profiles(person_key, channel_type);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_comm_style_person_context
            ON communication_style_profiles(person_key, interaction_context);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_communication_style_profiles_person_key
            ON communication_style_profiles(person_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_communication_style_profiles_channel_type
            ON communication_style_profiles(channel_type);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_communication_style_profiles_interaction_context
            ON communication_style_profiles(interaction_context);
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_after_dismiss_rate DOUBLE PRECISION NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS keep_prior_score DOUBLE PRECISION NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS last_audited_at TIMESTAMP;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_propensity_inbound_count INTEGER NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_propensity_replied_count INTEGER NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_propensity_raw_rate DOUBLE PRECISION NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_propensity_smoothed_rate DOUBLE PRECISION NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_propensity_boost DOUBLE PRECISION NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_contact_affinity
            ADD COLUMN IF NOT EXISTS reply_propensity_computed_at TIMESTAMP;
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_affinity_chat
            ON heartbeat_contact_affinity(chat_identifier);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_contact_affinity_sender
            ON heartbeat_contact_affinity(sender);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_sender_reply_after_dismiss (
                sender_key VARCHAR(255) PRIMARY KEY,
                sender VARCHAR(255),
                contact_name VARCHAR(255),
                decayed_count DOUBLE PRECISION NOT NULL DEFAULT 0,
                event_count INTEGER NOT NULL DEFAULT 0,
                last_event_at TIMESTAMP,
                last_dismissed_at TIMESTAMP,
                last_reply_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_sender_reply_after_dismiss_sender
            ON heartbeat_sender_reply_after_dismiss(sender);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS heartbeat_service_interaction_profiles (
                service_key VARCHAR(255) PRIMARY KEY,
                key_type VARCHAR(40) NOT NULL,
                source VARCHAR(50),
                sender VARCHAR(255),
                chat_identifier VARCHAR(255),
                service_category VARCHAR(80),
                known_transactional_sender BOOLEAN NOT NULL DEFAULT false,
                transactional_prompt_count INTEGER NOT NULL DEFAULT 0,
                actioned_prompt_count INTEGER NOT NULL DEFAULT 0,
                reply_prompt_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                last_prompt_at TIMESTAMP,
                last_actioned_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_service_interaction_sender
            ON heartbeat_service_interaction_profiles(sender);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_hb_service_interaction_chat
            ON heartbeat_service_interaction_profiles(chat_identifier);
        """))

        # Memory: tier system columns
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS tier VARCHAR DEFAULT 'observation';
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS reinforcement_count INTEGER DEFAULT 0;
        """))
        # Mind rebuild Phase 1 (2026-09-18): origin / about on every memory.
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS origin VARCHAR NOT NULL DEFAULT 'heard';
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS about VARCHAR NOT NULL DEFAULT 'owner';
        """))

        # Codex rate gate: persist the calibrated budget so it survives a
        # restart. In memory only, it reset to its cold-start value on every
        # bounce and could never grow to the plan's real ceiling.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS llm_rate_gate_state (
                id VARCHAR PRIMARY KEY,
                budget DOUBLE PRECISION NOT NULL,
                last_grow_wall DOUBLE PRECISION NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        # 2026-09-11: the gate paces against the account's real usage meter
        # (x-codex-* headers) instead of a guessed call budget; the meter is
        # what survives a restart now. budget/last_grow_wall stay for old rows.
        await conn.execute(text("""
            ALTER TABLE llm_rate_gate_state ADD COLUMN IF NOT EXISTS meter_json TEXT;
        """))

        # Memory: honest usage signal (P1). used_count counts turns where the
        # memory actually reached the model's context, as distinct from
        # access_count which counts retrieval hits.
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS used_count INTEGER NOT NULL DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP;
        """))

        # Memory: source attribution + confidence columns
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_type VARCHAR DEFAULT 'inferred';
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 0.5;
        """))

        # Consolidation: merge/promotion tracking columns
        await conn.execute(text("""
            ALTER TABLE consolidation_cycles ADD COLUMN IF NOT EXISTS merges_performed INTEGER DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE consolidation_cycles ADD COLUMN IF NOT EXISTS promotions INTEGER DEFAULT 0;
        """))

        # Consolidation: cost-cap columns on consolidation_config (Track A3)
        await conn.execute(text("""
            ALTER TABLE consolidation_config ADD COLUMN IF NOT EXISTS max_cluster_size INTEGER DEFAULT 20;
        """))
        await conn.execute(text("""
            ALTER TABLE consolidation_config ADD COLUMN IF NOT EXISTS max_pairs_per_cluster INTEGER DEFAULT 30;
        """))
        await conn.execute(text("""
            ALTER TABLE consolidation_config ADD COLUMN IF NOT EXISTS max_llm_calls_per_cycle INTEGER DEFAULT 15;
        """))

        # Self-evolution is gone (2026-09-12): Edward keeps a wishlist for
        # the owner instead of changing his own code. Drop the machinery's tables
        # (wishlist_items is created by create_all above). Observations and
        # claude_code_sessions stay — they have live non-evolution callers.
        await conn.execute(text("""
            DROP TABLE IF EXISTS evolution_proposals, dream_signature_cooldowns,
                                 evolution_config, evolution_history CASCADE;
        """))

        # The engine layer is gone (2026-09-18, mind rebuild Phase 8): the
        # per-turn "reply drafting context" engines, the ledgers, the
        # artifact/stakeholder/negotiation/scenario machinery and the folded
        # tables (relationship facts, preferences, decisions, watchpoints —
        # scripts/fold_*.py first). Batches B (dead), C (prompt-only) and D
        # (folded and wake-pack). Batch A (the heartbeat sub-engines) stays
        # until the mind path replaces triage. The memory graph
        # (graph_ingest_queue) is not touched.
        await conn.execute(text("""
            DROP TABLE IF EXISTS
                impact_alert_records,
                timeline_events,
                workflow_templates,
                runbook_records,
                workflow_state_records,
                commitment_records,
                deadline_records,
                assumption_tracking_records,
                stakeholder_awareness_records,
                preference_records,
                preference_contract_records,
                autonomy_audit_log,
                relationship_maintenance_logs,
                scenario_effects,
                artifact_revision_snapshots,
                artifact_change_events,
                impact_candidates,
                artifact_alignment_records,
                artifact_authority_records,
                artifact_fact_cards,
                artifact_deltas,
                playbook_templates,
                active_playbook_runs,
                dependency_edges,
                planned_work_blocks,
                capacity_pressure_events,
                cadence_records,
                evidence_gap_records,
                scenario_cases,
                episode_records,
                precedent_cases,
                commitment_patterns,
                commitment_outcomes,
                attention_items,
                human_capability_map,
                stakeholder_links,
                stakeholder_contexts,
                stakeholder_role_records,
                stakeholder_position_records,
                stakeholder_alignment_map,
                preference_policy_rules,
                constraint_ledger_records,
                objective_records,
                autonomy_policy,
                autonomy_policy_records,
                autonomy_actions,
                meeting_briefs,
                meeting_outcomes,
                risk_register_records,
                assumption_risk_register,
                experiment_records,
                fact_claims,
                claim_records,
                negotiation_states,
                negotiation_items,
                relationship_fact_graph,
                preference_memory_graph,
                decision_memories,
                watchpoint_records,
                workstreams,
                workstream_items,
                initiatives,
                initiative_links,
                commitments_ledger,
                commitment_ledger,
                outbound_commitment_ledger,
                inbound_obligation_records,
                unresolved_asks_ledger,
                open_question_resolver,
                outbound_ask_state,
                outbound_request_records,
                recurring_entity_dossiers,
                open_relationship_threads,
                push_entity_affinity,
                relationship_maintenance_profiles,
                proactive_outreach_candidates,
                watch_target_registry,
                external_signal_snapshots,
                external_signal_events,
                wakeup_ticks
            CASCADE;
        """))

        # Heartbeat: migration for contact_name column
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255);
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS person_key VARCHAR(160);
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS display_label VARCHAR(255);
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS identity_provenance VARCHAR(80);
        """))

        # Migration: Remove server_default from memories.last_accessed
        # Null out last_accessed where it was auto-set to created_at (never actually accessed)
        await conn.execute(text("""
            ALTER TABLE memories ALTER COLUMN last_accessed DROP DEFAULT;
        """))
        await conn.execute(text("""
            UPDATE memories SET last_accessed = NULL
            WHERE last_accessed IS NOT NULL AND access_count = 0;
        """))

        # Heartbeat: indexes for efficient queries
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_heartbeat_events_source_id ON heartbeat_events(source_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_heartbeat_events_status ON heartbeat_events(triage_status)
            WHERE triage_status = 'pending';
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_heartbeat_events_created ON heartbeat_events(created_at DESC);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_heartbeat_events_person_key
            ON heartbeat_events(person_key);
        """))
        # D1+B — push dispatcher columns
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS notified_at TIMESTAMP;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS notification_batch_id VARCHAR;
        """))
        # D3 — push engagement feedback
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS user_acted_on_it BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS user_acted_at TIMESTAMP;
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_heartbeat_events_unnotified
            ON heartbeat_events(sender, created_at)
            WHERE triage_status = 'escalated' AND notified_at IS NULL;
        """))
        await conn.execute(text("""
            ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS meeting_context_horizon_minutes INTEGER DEFAULT 60;
        """))
        await conn.execute(text("""
            ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS meeting_followthrough_minutes INTEGER DEFAULT 90;
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS contact_identity_aliases (
                alias_key VARCHAR(160) PRIMARY KEY,
                alias_type VARCHAR(40) NOT NULL,
                raw_identifier VARCHAR(255) NOT NULL,
                person_key VARCHAR(160) NOT NULL,
                display_label VARCHAR(255) NOT NULL,
                display_name_key VARCHAR(160),
                provenance VARCHAR(80) NOT NULL,
                source VARCHAR(50),
                chat_identifier VARCHAR(255),
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_contact_identity_aliases_person_key
            ON contact_identity_aliases(person_key);
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS communication_preference_records (
                preference_id VARCHAR(320) PRIMARY KEY,
                stakeholder VARCHAR(255) NOT NULL,
                scope VARCHAR(40) NOT NULL,
                scope_key VARCHAR(255),
                preference_type VARCHAR(40) NOT NULL,
                preference_value TEXT NOT NULL,
                conditions TEXT,
                applies_to_recipient_classes TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                strength VARCHAR(24) NOT NULL DEFAULT 'medium',
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                evidence_count INTEGER NOT NULL DEFAULT 1,
                provenance TEXT,
                first_seen_at TIMESTAMP DEFAULT now() NOT NULL,
                last_confirmed_at TIMESTAMP DEFAULT now() NOT NULL,
                superseded_by VARCHAR(320),
                updated_at TIMESTAMP DEFAULT now()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_comm_pref_stakeholder_scope_active
            ON communication_preference_records(stakeholder, scope, scope_key, last_confirmed_at)
            WHERE status = 'active';
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_comm_pref_type_active
            ON communication_preference_records(preference_type, confidence)
            WHERE status = 'active';
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_contact_identity_aliases_display_name_key
            ON contact_identity_aliases(display_name_key);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_contact_identity_aliases_chat
            ON contact_identity_aliases(chat_identifier);
        """))

        # Memory enrichments: partial index for unconsumed enrichments per conversation
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_enrichments_conv_consumed
            ON memory_enrichments(conversation_id)
            WHERE consumed = false;
        """))

        # Memory connections: indexes for lookup by either side
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_memory_connections_a ON memory_connections(memory_id_a);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_memory_connections_b ON memory_connections(memory_id_b);
        """))

        # Memory flags: index for unresolved flags
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_memory_flags_unresolved ON memory_flags(memory_id)
            WHERE resolved = false;
        """))

        # Claude Code sessions: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_cc_sessions_conversation ON claude_code_sessions(conversation_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_cc_sessions_status ON claude_code_sessions(status);
        """))

        # Orchestrator: migration for new columns
        await conn.execute(text("""
            ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS task_type VARCHAR DEFAULT 'internal_worker';
        """))
        await conn.execute(text("""
            ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS cc_session_id VARCHAR;
        """))
        await conn.execute(text("""
            ALTER TABLE orchestrator_config ADD COLUMN IF NOT EXISTS max_concurrent_cc_sessions INTEGER DEFAULT 2;
        """))

        # Email actions: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_email_actions_sender_domain ON email_actions(sender_domain);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_email_actions_created ON email_actions(created_at DESC);
        """))

        # Email sender prefs: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_email_sender_prefs_domain ON email_sender_prefs(sender_domain);
        """))

        # Email unsub queue: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_email_unsub_queue_domain ON email_unsub_queue(sender_domain);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_email_unsub_queue_status ON email_unsub_queue(status);
        """))

        # Usage events: index for per-provider time-range queries
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_usage_events_provider_created ON usage_events(provider, created_at);
        """))

        # Usage events: add cache token columns (idempotent migration)
        for col in ("cache_creation_tokens", "cache_read_tokens"):
            await conn.execute(text(f"""
                DO $$ BEGIN
                    ALTER TABLE usage_events ADD COLUMN {col} INTEGER DEFAULT 0;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """))

        # Orchestrator tasks: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_orchestrator_tasks_parent ON orchestrator_tasks(parent_conversation_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_orchestrator_tasks_worker ON orchestrator_tasks(worker_conversation_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_orchestrator_tasks_active ON orchestrator_tasks(status)
            WHERE status IN ('pending', 'running');
        """))

        # Entity system: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_entities_name ON entities(name);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_entities_type ON entities(entity_type);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_entity_mentions_entity ON entity_mentions(entity_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_entity_mentions_memory ON entity_mentions(memory_id);
        """))

        # Open loops: partial index for active loops
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_open_loops_active ON open_loops(status)
            WHERE status = 'open';
        """))
        for ddl in (
            "ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS dossier_enabled BOOLEAN DEFAULT true",
            "ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS dossier_min_acted_pushes INTEGER DEFAULT 2",
            "ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS dossier_min_inbound_threads INTEGER DEFAULT 3",
            "ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS dossier_window_days INTEGER DEFAULT 7",
            "ALTER TABLE proactive_config ADD COLUMN IF NOT EXISTS relationship_maintenance_enabled BOOLEAN DEFAULT true",
        ):
            await conn.execute(text(ddl))

        # Heartbeat responsiveness (2026-07-07): retry accounting + direct-channel config
        for ddl in (
            "ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS triage_attempts INTEGER DEFAULT 0",
            "ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS inbound_at TIMESTAMP",
            "ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS watch_until TIMESTAMP",
            "ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS acted_at TIMESTAMP",
            "ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS handled_by_owner_at TIMESTAMP",
            "CREATE INDEX IF NOT EXISTS ix_heartbeat_events_status_watch ON heartbeat_events(triage_status, watch_until)",
            "ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS layer3_async_enabled BOOLEAN DEFAULT false",
            "ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS mind_enabled BOOLEAN DEFAULT true",
            "ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS settle_window_seconds INTEGER DEFAULT 240",
            "ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS settle_window_present_seconds INTEGER DEFAULT 480",
            "ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS device_id VARCHAR(120)",
            "ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS device_name VARCHAR(120)",
            "ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS kind VARCHAR(20)",
            "ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP",
            "ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_owner_handles TEXT",
            "ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_event_triggered_triage BOOLEAN DEFAULT true",
            # 2026-09-12: "Snooze 1h" on a notification re-sends the ledger row once.
            "ALTER TABLE push_log ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMP",
            "ALTER TABLE push_log ADD COLUMN IF NOT EXISTS resent_at TIMESTAMP",
        ):
            await conn.execute(text(ddl))

        # --- noticing (2026-10-11) ---
        # The noticings table itself comes from create_all (NoticingModel);
        # its indexes and the note's dismissal stamp are added here. Additive
        # and idempotent; no memory row is touched.
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_noticings_created ON noticings (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_noticings_status_due ON noticings (status, due_at)",
            "CREATE INDEX IF NOT EXISTS idx_noticings_dedupe ON noticings (dedupe_key)",
            "CREATE INDEX IF NOT EXISTS idx_noticings_chat ON noticings (chat_identifier)",
            "CREATE INDEX IF NOT EXISTS idx_noticings_category_surfaced ON noticings (category, surfaced_at)",
            "ALTER TABLE edward_notes ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMP",
        ):
            await conn.execute(text(ddl))

        # --- taint and approvals (2026-09-24) ---
        # Whether a turn read third-party text, and why (taint_service); the
        # pending_approvals table itself comes from create_all.
        for ddl in (
            "ALTER TABLE turn_log ADD COLUMN IF NOT EXISTS tainted BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE turn_log ADD COLUMN IF NOT EXISTS taint_reason VARCHAR(80)",
        ):
            await conn.execute(text(ddl))

        # --- mind wake attempts (2026-09-24) ---
        # A failed or timed-out wake counts an attempt on each escalation it
        # was served; the third gives up on it (mind_service).
        for ddl in (
            "ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS mind_attempts INTEGER",
        ):
            await conn.execute(text(ddl))

        # --- provenance (2026-09-24) ---
        # memories.source_ref: where a memory came from (a JSON the
        # how_do_i_know tool reads back). Nullable; old rows say "not recorded".
        for ddl in (
            "ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_ref TEXT",
        ):
            await conn.execute(text(ddl))

        # --- the stage (2026-09-28) ---
        # The host Mac's screen is one scene Edward writes on instead of a 6×4
        # tile grid, plus the lines he says there and the owner's hold on it. The
        # old layout columns stay: a stored layout is converted on read.
        for ddl in (
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS scene TEXT",
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS scene_why VARCHAR(300)",
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS scene_updated_at TIMESTAMP",
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS scene_updated_by VARCHAR(40)",
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS asked_at TIMESTAMP",
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS asked_until TIMESTAMP",
            "ALTER TABLE screen_state ADD COLUMN IF NOT EXISTS lines TEXT",
        ):
            await conn.execute(text(ddl))

        # Skills guardrails (2026-07-05 mass-disable incident): audit every
        # enabled-flag flip at the Postgres level so writers that bypass the
        # app (raw SQL, other roles) are still recorded.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS skills_audit (
                id SERIAL PRIMARY KEY,
                skill_id TEXT,
                old_enabled BOOLEAN,
                new_enabled BOOLEAN,
                application_name TEXT,
                changed_at TIMESTAMPTZ DEFAULT now()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_skills_audit_changed_at ON skills_audit(changed_at);
        """))
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION skills_enabled_audit_fn() RETURNS trigger AS $fn$
            BEGIN
                INSERT INTO skills_audit (skill_id, old_enabled, new_enabled, application_name)
                VALUES (NEW.id, OLD.enabled, NEW.enabled, current_setting('application_name', true));
                RETURN NEW;
            END
            $fn$ LANGUAGE plpgsql;
        """))
        await conn.execute(text("DROP TRIGGER IF EXISTS skills_enabled_audit ON skills"))
        await conn.execute(text("""
            CREATE TRIGGER skills_enabled_audit
            AFTER UPDATE OF enabled ON skills
            FOR EACH ROW
            WHEN (OLD.enabled IS DISTINCT FROM NEW.enabled)
            EXECUTE FUNCTION skills_enabled_audit_fn();
        """))

        # query_persistent_db write-guard: grants for the low-privilege
        # edward_llm role. The role itself is created out-of-band by an
        # operator (roles are cluster-global and `edward` lacks CREATEROLE);
        # until it exists the persistent-db tool runs in read-only degraded
        # mode.
        role_exists = (await conn.execute(text(
            f"SELECT 1 FROM pg_roles WHERE rolname = '{LLM_DB_ROLE}'"
        ))).scalar()
        if role_exists:
            schema_rows = await conn.execute(text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE 'edward\\_db\\_%'"
            ))
            for (schema_name,) in schema_rows.fetchall():
                for stmt in llm_role_grant_statements(schema_name):
                    await conn.execute(text(stmt))

    # Create default settings if not exists
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(SettingsModel).where(SettingsModel.id == "default"))
        if not result.scalar_one_or_none():
            default_settings = SettingsModel(id="default")
            session.add(default_settings)
            await session.commit()

        # Create default heartbeat config if not exists
        result = await session.execute(select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default"))
        if not result.scalar_one_or_none():
            default_config = HeartbeatConfigModel(id="default")
            session.add(default_config)
            await session.commit()

        # Create default consolidation config if not exists
        result = await session.execute(select(ConsolidationConfigModel).where(ConsolidationConfigModel.id == "default"))
        if not result.scalar_one_or_none():
            default_consolidation = ConsolidationConfigModel(id="default")
            session.add(default_consolidation)
            await session.commit()

        # Create default orchestrator config if not exists
        result = await session.execute(select(OrchestratorConfigModel).where(OrchestratorConfigModel.id == "default"))
        if not result.scalar_one_or_none():
            default_orchestrator = OrchestratorConfigModel(id="default")
            session.add(default_orchestrator)
            await session.commit()

        # Create default proactive config if not exists
        result = await session.execute(select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default"))
        if not result.scalar_one_or_none():
            default_proactive = ProactiveConfigModel(id="default")
            session.add(default_proactive)
            await session.commit()

async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
