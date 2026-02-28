from .graph import create_edward_graph, get_graph_structure
from .state import AgentState
from .streaming import stream_with_memory, stream_with_memory_events, chat_with_memory, EventType, create_event
from psycopg_pool import AsyncConnectionPool
from psycopg import AsyncConnection
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

_compiled_graph = None
_connection_pool = None


async def initialize_graph(database_url: str):
    """Initialize the LangGraph with PostgreSQL checkpointing."""
    global _compiled_graph, _connection_pool

    # Convert asyncpg URL to standard postgresql URL for checkpointer
    checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Run setup with autocommit connection first
    async with await AsyncConnection.connect(checkpoint_url, autocommit=True) as conn:
        checkpointer_setup = AsyncPostgresSaver(conn)
        await checkpointer_setup.setup()

    # Create connection pool for runtime
    _connection_pool = AsyncConnectionPool(conninfo=checkpoint_url, open=False)
    await _connection_pool.open()

    # Create checkpointer with pool
    checkpointer = AsyncPostgresSaver(_connection_pool)

    graph = create_edward_graph()
    _compiled_graph = graph.compile(checkpointer=checkpointer)

    return _compiled_graph


async def get_graph():
    """Get the compiled graph instance."""
    if _compiled_graph is None:
        raise RuntimeError("Graph not initialized. Call initialize_graph() first.")
    return _compiled_graph


__all__ = [
    "create_edward_graph",
    "get_graph_structure",
    "AgentState",
    "stream_with_memory",
    "stream_with_memory_events",
    "chat_with_memory",
    "EventType",
    "create_event",
    "initialize_graph",
    "get_graph",
]
