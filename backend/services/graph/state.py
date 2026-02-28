from typing import TypedDict, Annotated, Sequence, Optional, List, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class Memory(TypedDict):
    """A memory item retrieved from or to be stored in the database."""
    id: Optional[str]
    content: str
    memory_type: str  # fact, preference, context, instruction
    importance: float
    temporal_nature: str  # timeless, temporary, evolving
    tier: str  # observation, belief, knowledge
    reinforcement_count: int
    score: Optional[float]  # Relevance score during retrieval


class PlanStep(TypedDict):
    """A single step in a task plan."""
    id: str                    # "step-1", "step-2", etc.
    title: str                 # Short description
    status: str                # "pending" | "in_progress" | "completed" | "error"
    result: Optional[str]      # Brief result summary after completion


class AgentState(TypedDict):
    """State for the Edward agent graph."""
    # Core conversation state
    messages: Annotated[Sequence[BaseMessage], add_messages]
    conversation_id: str
    system_prompt: str
    model: str
    temperature: float
    current_response: str
    is_complete: bool
    error: Optional[str]

    # Memory state
    retrieved_memories: List[Memory]
    memories_to_store: List[Memory]

    # Plan state
    plan_steps: Optional[List[PlanStep]]

    # Debug/observability state
    current_node: str
    node_history: List[str]
