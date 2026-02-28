from langgraph.graph import StateGraph, END
from .state import AgentState
from .nodes import preprocess_node, retrieve_memory_node, respond_node, extract_memory_node


def create_edward_graph() -> StateGraph:
    """
    Create the Edward agent state graph.

    Flow:
        preprocess -> retrieve_memory -> respond -> extract_memory -> END

    - preprocess: Initialize state, clear errors
    - retrieve_memory: Query pgvector for relevant memories
    - respond: Generate response using Claude (with memory context injected)
    - extract_memory: Analyze conversation and store new memories
    """
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("preprocess", preprocess_node)
    graph.add_node("retrieve_memory", retrieve_memory_node)
    graph.add_node("respond", respond_node)
    graph.add_node("extract_memory", extract_memory_node)

    # Define edges (linear flow for now)
    graph.set_entry_point("preprocess")
    graph.add_edge("preprocess", "retrieve_memory")
    graph.add_edge("retrieve_memory", "respond")
    graph.add_edge("respond", "extract_memory")
    graph.add_edge("extract_memory", END)

    return graph


def get_graph_structure() -> dict:
    """
    Return a serializable representation of the graph structure.
    Useful for debugging and visualization.
    """
    return {
        "nodes": [
            {
                "id": "preprocess",
                "name": "Preprocess",
                "description": "Initialize state and prepare for processing"
            },
            {
                "id": "retrieve_memory",
                "name": "Retrieve Memory",
                "description": "Query pgvector for relevant memories based on user message"
            },
            {
                "id": "respond",
                "name": "Respond",
                "description": "Generate response using Claude with memory context"
            },
            {
                "id": "extract_memory",
                "name": "Extract Memory",
                "description": "Analyze conversation and store new memories"
            }
        ],
        "edges": [
            {"from": "__start__", "to": "preprocess"},
            {"from": "preprocess", "to": "retrieve_memory"},
            {"from": "retrieve_memory", "to": "respond"},
            {"from": "respond", "to": "extract_memory"},
            {"from": "extract_memory", "to": "__end__"}
        ]
    }
