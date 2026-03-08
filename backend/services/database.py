import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, Float, Text, DateTime, Integer, Boolean, func, Index, text
from pgvector.sqlalchemy import Vector

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://edward:edward@localhost:5432/edward"
)

# Convert postgresql:// to postgresql+asyncpg:// if needed
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=3600,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class SettingsModel(Base):
    __tablename__ = "settings"

    id = Column(String, primary_key=True, default="default")
    name = Column(String, default="Edward")
    personality = Column(Text, default="You are Edward, a helpful and friendly AI assistant.")
    model = Column(String, default="claude-sonnet-4-5-20250929")
    temperature = Column(Float, default=0.7)
    system_prompt = Column(Text, default="You are Edward (Enhanced Digital Workflow Assistant for Routine Decisions), a helpful AI assistant. Be concise, friendly, helpful, and a tad cheeky when you feel like it.")
    password_hash = Column(String, nullable=True)  # Hashed password for web authentication
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ConversationModel(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    title = Column(String, default="New Conversation")
    source = Column(String, default="user")  # "user" | "scheduled_event" | "external_message"
    channel = Column(String, default="text")  # "text" | "voice"
    notified_user = Column(Boolean, default=False)  # True if Edward sent push/message to get attention
    search_tags = Column(Text, nullable=True)  # Comma-separated keywords for FTS
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    message_count = Column(Integer, default=0)


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
    access_count = Column(Integer, default=0)
    temporal_nature = Column(String, default="timeless")  # timeless, temporary, evolving
    tier = Column(String, default="observation")  # observation, belief, knowledge
    reinforcement_count = Column(Integer, default=0)
    user_id = Column(String, nullable=True, index=True)  # For future multi-user support

    __table_args__ = (
        # Index for vector similarity search (using HNSW for fast approximate search)
        Index('ix_memories_embedding', 'embedding', postgresql_using='hnsw',
              postgresql_with={'m': 16, 'ef_construction': 64},
              postgresql_ops={'embedding': 'vector_cosine_ops'}),
        # GIN index for full-text search on content
        Index('ix_memories_content_fts', text("to_tsvector('english', content)"),
              postgresql_using='gin'),
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
    user_agent = Column(String, nullable=True)  # For debugging
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, server_default=func.now())
    failed_count = Column(Integer, default=0)  # Track failed deliveries
    is_active = Column(Boolean, default=True)  # Soft disable on repeated failures


class PersistentDatabaseModel(Base):
    """Persistent SQL databases that survive across conversations."""
    __tablename__ = "persistent_databases"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(50), unique=True, nullable=False)  # User-friendly name, e.g., "lana_tracking"
    schema_name = Column(String(64), unique=True, nullable=False)  # PostgreSQL schema, e.g., "edward_db_lana_tracking"
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
    chat_identifier = Column(String(255), nullable=True)
    chat_name = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    raw_data = Column(Text, nullable=True)
    source_id = Column(String(255), nullable=True, unique=True)
    is_from_user = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    triage_status = Column(String(20), default="pending")
    triage_cycle_id = Column(String, nullable=True)
    briefed = Column(Boolean, default=False)


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


class HeartbeatConfigModel(Base):
    """Configuration for the heartbeat system."""
    __tablename__ = "heartbeat_config"

    id = Column(String, primary_key=True, default="default")
    enabled = Column(Boolean, default=True)
    triage_interval_seconds = Column(Integer, default=900)
    digest_token_cap = Column(Integer, default=800)
    allowed_senders = Column(Text, nullable=True)  # JSON array of {"identifier": "...", "label": "..."}
    # Per-track configuration
    imessage_enabled = Column(Boolean, default=True)
    imessage_poll_seconds = Column(Integer, default=10)
    calendar_enabled = Column(Boolean, default=False)
    calendar_poll_seconds = Column(Integer, default=300)
    calendar_lookahead_minutes = Column(Integer, default=30)
    email_enabled = Column(Boolean, default=False)
    email_poll_seconds = Column(Integer, default=300)
    whatsapp_enabled = Column(Boolean, default=False)
    whatsapp_poll_seconds = Column(Integer, default=30)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


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


class EvolutionConfigModel(Base):
    """Configuration for the self-evolution engine."""
    __tablename__ = "evolution_config"

    id = Column(String, primary_key=True, default="default")
    enabled = Column(Boolean, default=False)
    min_interval_seconds = Column(Integer, default=3600)
    auto_trigger = Column(Boolean, default=False)
    require_tests = Column(Boolean, default=True)
    max_files_per_cycle = Column(Integer, default=20)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EvolutionHistoryModel(Base):
    """History of evolution cycles."""
    __tablename__ = "evolution_history"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    trigger = Column(String, default="manual")  # manual, auto, llm
    description = Column(Text, nullable=False)
    branch_name = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending, branching, coding, validating, testing, reviewing, deploying, completed, failed, rolled_back
    step = Column(String, nullable=True)  # Current step name
    files_changed = Column(Text, nullable=True)  # JSON array
    test_output = Column(Text, nullable=True)
    review_summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    rollback_tag = Column(String, nullable=True)
    cc_session_id = Column(String, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class OrchestratorTaskModel(Base):
    """Tasks spawned by the orchestrator for parallel worker execution."""
    __tablename__ = "orchestrator_tasks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_conversation_id = Column(String, nullable=False, index=True)
    worker_conversation_id = Column(String, nullable=True, index=True)
    task_description = Column(Text, nullable=False)
    task_type = Column(String, default="internal_worker")  # internal_worker, cc_session
    model = Column(String, default="claude-haiku-4-5-20251001")
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


class OrchestratorConfigModel(Base):
    """Configuration for the orchestrator system."""
    __tablename__ = "orchestrator_config"

    id = Column(String, primary_key=True, default="default")
    enabled = Column(Boolean, default=False)
    max_concurrent_workers = Column(Integer, default=5)
    max_concurrent_cc_sessions = Column(Integer, default=2)
    default_worker_model = Column(String, default="claude-haiku-4-5-20251001")
    default_worker_timeout = Column(Integer, default=300)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ConversationMessagesModel(Base):
    """Simple message checkpoint store replacing LangGraph."""
    __tablename__ = "conversation_messages"

    conversation_id = Column(String, primary_key=True)
    messages = Column(Text, nullable=False, default="[]")  # JSONB via PostgreSQL
    metadata_ = Column("metadata", Text, nullable=True)  # Settings snapshot
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


async def init_db():
    """Initialize the database tables."""
    async with engine.begin() as conn:
        # Enable pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Create tables
        await conn.run_sync(Base.metadata.create_all)

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
        # Create index on user_id if it doesn't exist
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_memories_user_id ON memories(user_id);
        """))

        # Create conversations table if it doesn't exist (handled by create_all above)
        # Add index for sorting conversations by updated_at
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_conversations_updated_at ON conversations(updated_at DESC);
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

        # Heartbeat: per-track config columns
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS imessage_enabled BOOLEAN DEFAULT true;
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
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS whatsapp_enabled BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
            ALTER TABLE heartbeat_config ADD COLUMN IF NOT EXISTS whatsapp_poll_seconds INTEGER DEFAULT 30;
        """))

        # Memory: tier system columns
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS tier VARCHAR DEFAULT 'observation';
        """))
        await conn.execute(text("""
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS reinforcement_count INTEGER DEFAULT 0;
        """))

        # Consolidation: merge/promotion tracking columns
        await conn.execute(text("""
            ALTER TABLE consolidation_cycles ADD COLUMN IF NOT EXISTS merges_performed INTEGER DEFAULT 0;
        """))
        await conn.execute(text("""
            ALTER TABLE consolidation_cycles ADD COLUMN IF NOT EXISTS promotions INTEGER DEFAULT 0;
        """))

        # Heartbeat: migration for contact_name column
        await conn.execute(text("""
            ALTER TABLE heartbeat_events ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255);
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

        # Evolution history: indexes
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_evolution_history_status ON evolution_history(status);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_evolution_history_created ON evolution_history(created_at DESC);
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

        # Create default evolution config if not exists
        result = await session.execute(select(EvolutionConfigModel).where(EvolutionConfigModel.id == "default"))
        if not result.scalar_one_or_none():
            default_evolution = EvolutionConfigModel(id="default")
            session.add(default_evolution)
            await session.commit()

        # Create default orchestrator config if not exists
        result = await session.execute(select(OrchestratorConfigModel).where(OrchestratorConfigModel.id == "default"))
        if not result.scalar_one_or_none():
            default_orchestrator = OrchestratorConfigModel(id="default")
            session.add(default_orchestrator)
            await session.commit()

async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
