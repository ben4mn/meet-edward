"""The ping ledger and the send ledger's read-back columns (AREA 3, 2026-10-11).

Own MetaData, so ``Base.metadata.create_all`` never builds these from
another definition. ``services.database.init_db`` runs ``METADATA.create_all``
and then ``ALTERS``. Additive and idempotent.

Time columns here are naive UTC (``hooks.utcnow``). The send ledger's
``sent_at`` and ``confirmed_at`` are timestamptz: bind aware UTC there.
"""

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Float, Index, Integer, MetaData, String, Table,
                        Text, text)

METADATA = MetaData()

# status: open | running | answered | fallback | declined | failed | held | limited | expired | legacy
pings = Table(
    "pings", METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_ref", String(255), nullable=False),      # imessage:<rowid> | app:<turn id> | ...
    Column("channel", String(20), nullable=False),          # imessage | twilio_sms | twilio_whatsapp | app | widget
    Column("kind", String(20), nullable=False),             # mention | owner_direct | reply_to_edward | vocative | turn
    Column("status", String(20), nullable=False, server_default=text("'open'")),
    Column("must_answer", Boolean, nullable=False, server_default=text("true")),
    Column("from_owner", Boolean, nullable=False, server_default=text("false")),
    Column("third_party", Boolean, nullable=False, server_default=text("false")),
    Column("sender_known", Boolean, nullable=False, server_default=text("true")),
    Column("heartbeat_event_id", String),
    Column("message_rowid", BigInteger),
    Column("message_guid", String(80)),
    Column("chat_identifier", String(255)),
    Column("chat_guid", String(255)),
    Column("chat_name", String(255)),
    Column("is_group", Boolean, nullable=False, server_default=text("false")),
    Column("sender", String(255)),
    Column("sender_label", String(255)),
    Column("text", Text),
    Column("media_ids", Text),                              # P1: JSON list of media_items ids
    Column("conversation_id", String),
    Column("merged_into", Integer),                         # P1: coalescing
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("next_attempt_at", DateTime),
    Column("asked_at", DateTime, nullable=False),
    Column("detected_at", DateTime, nullable=False),
    Column("started_at", DateTime),
    Column("first_send_at", DateTime),
    Column("answered_at", DateTime),
    Column("reply_message_rowid", BigInteger),
    Column("reply_route", String(40)),
    Column("latency_s", Float),
    Column("last_error", Text),
    Column("note", Text),
    Column("updated_at", DateTime, nullable=False),
    Index("ux_pings_source_ref", "source_ref", unique=True),
    Index("ix_pings_event", "heartbeat_event_id"),
    Index("ix_pings_status_due", "status", "next_attempt_at"),
    Index("ix_pings_chat_asked", "chat_identifier", "asked_at"),
)

ALTERS = (
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS chat_guid VARCHAR(255)",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS route VARCHAR(40)",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS ok BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS error TEXT",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS message_rowid BIGINT",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS message_guid VARCHAR(80)",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS text_norm_hash VARCHAR(64)",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS text_len INTEGER",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS ping_id INTEGER",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS purpose VARCHAR(20)",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS turn_source VARCHAR(30)",
    "ALTER TABLE imessage_outgoing_sends ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_outgoing_sends_rowid ON imessage_outgoing_sends (message_rowid)",
    "CREATE INDEX IF NOT EXISTS ix_outgoing_sends_chat_time ON imessage_outgoing_sends (chat_identifier, sent_at)",
    "CREATE INDEX IF NOT EXISTS ix_outgoing_sends_ping ON imessage_outgoing_sends (ping_id)",
)
