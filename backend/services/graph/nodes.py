from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langchain_anthropic import ChatAnthropic
from .state import AgentState, Memory


async def preprocess_node(state: AgentState) -> dict:
    """Preprocess the incoming message and prepare state."""
    return {
        "is_complete": False,
        "error": None,
        "current_response": "",
        "retrieved_memories": [],
        "memories_to_store": [],
        "current_node": "preprocess",
        "node_history": state.get("node_history", []) + ["preprocess"]
    }


async def retrieve_memory_node(state: AgentState) -> dict:
    """Retrieve relevant memories for the current conversation."""
    from services.memory_service import retrieve_memories, Memory as ServiceMemory

    # Get the last user message to use as query
    last_user_message = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage) or (hasattr(msg, 'type') and msg.type == 'human'):
            last_user_message = msg.content if hasattr(msg, 'content') else str(msg)
            break

    if not last_user_message:
        return {
            "retrieved_memories": [],
            "current_node": "retrieve_memory",
            "node_history": state.get("node_history", []) + ["retrieve_memory"]
        }

    try:
        # Retrieve relevant memories
        memories = await retrieve_memories(last_user_message, limit=5)

        # Convert to state Memory format
        state_memories = [
            Memory(
                id=m.id,
                content=m.content,
                memory_type=m.memory_type,
                importance=m.importance,
                temporal_nature=m.temporal_nature,
                score=m.score
            )
            for m in memories
        ]

        return {
            "retrieved_memories": state_memories,
            "current_node": "retrieve_memory",
            "node_history": state.get("node_history", []) + ["retrieve_memory"]
        }
    except Exception as e:
        # Don't fail the conversation if memory retrieval fails
        print(f"Memory retrieval failed: {e}")
        return {
            "retrieved_memories": [],
            "current_node": "retrieve_memory",
            "node_history": state.get("node_history", []) + ["retrieve_memory"]
        }


async def respond_node(state: AgentState) -> dict:
    """Generate a response using the Claude model."""
    try:
        llm = ChatAnthropic(
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=4096
        )

        # Build system prompt with memory context
        system_prompt = state["system_prompt"]

        # Inject retrieved memories if available
        retrieved_memories = state.get("retrieved_memories", [])
        if retrieved_memories:
            memory_context = "\n\n## Relevant Context from Previous Conversations:\n"
            for memory in retrieved_memories:
                tn = memory.get('temporal_nature', 'timeless')
                tn_tag = f" [{tn}]" if tn != "timeless" else ""
                memory_context += f"- [{memory['memory_type']}] {memory['content']}{tn_tag}\n"
            system_prompt = system_prompt + memory_context

        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
        response = await llm.ainvoke(messages)

        return {
            "messages": [response],
            "current_response": response.content,
            "is_complete": False,  # Not complete yet - still need to extract memories
            "current_node": "respond",
            "node_history": state.get("node_history", []) + ["respond"]
        }
    except Exception as e:
        return {
            "error": str(e),
            "is_complete": True,
            "current_node": "respond",
            "node_history": state.get("node_history", []) + ["respond"]
        }


async def extract_memory_node(state: AgentState) -> dict:
    """Extract memorable information from the conversation and store it."""
    from services.memory_service import extract_and_store_memories, Memory as ServiceMemory

    # Don't extract if there was an error
    if state.get("error"):
        return {
            "is_complete": True,
            "current_node": "extract_memory",
            "node_history": state.get("node_history", []) + ["extract_memory"]
        }

    try:
        # Convert messages to dict format for extraction
        messages_for_extraction = []
        for msg in state["messages"][-10:]:  # Last 10 messages
            if hasattr(msg, 'type'):
                role = msg.type
            elif hasattr(msg, 'role'):
                role = msg.role
            else:
                role = "unknown"

            content = msg.content if hasattr(msg, 'content') else str(msg)
            messages_for_extraction.append({"role": role, "content": content})

        # Convert retrieved memories back to service format for dedup
        existing_memories = [
            ServiceMemory(
                id=m.get("id"),
                content=m["content"],
                memory_type=m["memory_type"],
                importance=m["importance"],
                temporal_nature=m.get("temporal_nature", "timeless"),
                score=m.get("score", 0.0)
            )
            for m in state.get("retrieved_memories", [])
        ]

        # Extract and store new memories
        stored_memories = await extract_and_store_memories(
            messages=messages_for_extraction,
            conversation_id=state["conversation_id"],
            existing_memories=existing_memories
        )

        # Convert stored memories to state format
        memories_stored = [
            Memory(
                id=m.id,
                content=m.content,
                memory_type=m.memory_type,
                importance=m.importance,
                temporal_nature=m.temporal_nature,
                score=0.0
            )
            for m in stored_memories
        ]

        return {
            "memories_to_store": memories_stored,
            "is_complete": True,
            "current_node": "extract_memory",
            "node_history": state.get("node_history", []) + ["extract_memory"]
        }
    except Exception as e:
        # Don't fail the conversation if memory extraction fails
        print(f"Memory extraction failed: {e}")
        return {
            "is_complete": True,
            "current_node": "extract_memory",
            "node_history": state.get("node_history", []) + ["extract_memory"]
        }
