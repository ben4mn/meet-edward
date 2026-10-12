"""One row per piece of media Edward has seen (2026-10-11).

Own MetaData, created by ``services.database.init_db``. Additive.
"""

from sqlalchemy import BigInteger, Column, DateTime, Float, Index, Integer, MetaData, String, Table, Text, text

METADATA = MetaData()

media_items = Table(
    "media_items", METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),     # shown as "m<id>"
    Column("source", String(20), nullable=False),                    # imessage | chat_upload | twilio | email | look
    Column("source_ref", String(255), nullable=False),               # imessage:att:<rowid> | imessage:link:<rowid> | file:<id> | url:<sha1> | ...
    Column("kind", String(20), nullable=False),                      # image | sticker | gif | audio | video | pdf | document | contact | link | payment | file
    Column("status", String(20), nullable=False, server_default=text("'new'")),  # new | captured | ready | partial | missing | skipped | failed
    Column("heartbeat_event_id", String),
    Column("conversation_id", String),
    Column("chat_identifier", String(255)),
    Column("sender", String(255)),
    Column("mime_type", String(120)),
    Column("uti", String(120)),
    Column("original_name", String(255)),
    Column("original_path", Text),
    Column("sha256", String(64)),
    Column("stored_path", Text),
    Column("file_id", String),                                       # normalized JPEG in file storage (PWA chips)
    Column("derived_json", Text),                                    # {"jpeg": path, "frames": [...], "m4a": path, "preview": path}
    Column("bytes", BigInteger),
    Column("width", Integer),
    Column("height", Integer),
    Column("duration_s", Float),
    Column("url", Text),
    Column("final_url", Text),
    Column("link_json", Text),                                       # LPLinkMetadata / fetch result
    Column("description", Text),
    Column("transcript", Text),
    Column("text_content", Text),
    Column("sensitive", String(40)),
    Column("error", Text),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("next_attempt_at", DateTime),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("ready_at", DateTime),
    Index("ux_media_items_source_ref", "source_ref", unique=True),
    Index("ix_media_items_event", "heartbeat_event_id"),
    Index("ix_media_items_chat_created", "chat_identifier", "created_at"),
    Index("ix_media_items_status", "status"),
)
