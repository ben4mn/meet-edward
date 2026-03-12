from .streaming import stream_with_memory, stream_with_memory_events, chat_with_memory, EventType, create_event

# get_graph_structure — try legacy module, fall back to static dict
try:
    from .graph import get_graph_structure
except Exception:
    def get_graph_structure() -> dict:
        return {
            "nodes": [
                {"id": "preprocess", "name": "Preprocess", "description": "Initialize state and prepare for processing"},
                {"id": "retrieve_memory", "name": "Retrieve Memory", "description": "Query pgvector for relevant memories based on user message"},
                {"id": "respond", "name": "Respond", "description": "Generate response using Claude with memory context"},
                {"id": "extract_memory", "name": "Extract Memory", "description": "Analyze conversation and store new memories"},
            ],
            "edges": [
                {"from": "__start__", "to": "preprocess"},
                {"from": "preprocess", "to": "retrieve_memory"},
                {"from": "retrieve_memory", "to": "respond"},
                {"from": "respond", "to": "extract_memory"},
                {"from": "extract_memory", "to": "__end__"},
            ],
        }

# Legacy LangGraph graph for migrating old conversations
_legacy_graph = None
_legacy_pool = None


async def initialize_checkpoint_store(database_url: str):
    """Initialize the checkpoint store and optionally legacy LangGraph for migration."""
    # The new checkpoint store (conversation_messages table) is created by
    # init_db() in database.py via SQLAlchemy create_all. No extra init needed.

    # Try to init legacy LangGraph for reading old conversations
    global _legacy_graph, _legacy_pool
    try:
        from psycopg_pool import AsyncConnectionPool
        from psycopg import AsyncConnection
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

        async with await AsyncConnection.connect(checkpoint_url, autocommit=True) as conn:
            checkpointer_setup = AsyncPostgresSaver(conn)
            await checkpointer_setup.setup()

        pool = AsyncConnectionPool(conninfo=checkpoint_url, open=False)
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)

        from .graph import create_edward_graph
        graph = create_edward_graph()
        _legacy_graph = graph.compile(checkpointer=checkpointer)
        _legacy_pool = pool

        print("Legacy LangGraph checkpoint store initialized (for migration)")
    except Exception as e:
        print(f"Legacy LangGraph init skipped: {e}")
        _legacy_graph = None
        _legacy_pool = None


async def get_legacy_graph():
    """Get the legacy graph for reading old conversations. Returns None if unavailable."""
    return _legacy_graph


async def shutdown_legacy_graph():
    """Shutdown the legacy graph connection pool."""
    global _legacy_graph, _legacy_pool
    if _legacy_pool:
        try:
            await _legacy_pool.close()
        except Exception:
            pass
    _legacy_graph = None
    _legacy_pool = None


__all__ = [
    "get_graph_structure",
    "stream_with_memory",
    "stream_with_memory_events",
    "chat_with_memory",
    "EventType",
    "create_event",
    "initialize_checkpoint_store",
    "get_legacy_graph",
    "shutdown_legacy_graph",
]
