from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from services.llm_utils import build_chat_llm
from services.owner import owner_text
from .state import AgentState, Memory


async def preprocess_node(state: AgentState) -> dict:
    """Preprocess the incoming message and prepare state.

    Side effect: scans the latest user turn for correction signal and
    fires off a self_observation if it looks like one. Used to feed
    the dream-synthesis judge with real learn-loop input.
    """
    import asyncio
    from langchain_core.messages import HumanMessage

    try:
        last_user = next(
            (m for m in reversed(state.get("messages", []))
             if isinstance(m, HumanMessage)),
            None,
        )
        if last_user is not None:
            content = last_user.content
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            from services.observation_emitters import (
                emit_correction_observation,
                is_correction_signal,
            )
            if is_correction_signal(content):
                asyncio.create_task(emit_correction_observation(content))
    except Exception:
        pass

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
    """Generate a response using the chat LLM (llm_utils.build_chat_llm)."""
    try:
        llm = build_chat_llm(
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=4096,
        )

        # Build system prompt with memory context. The base prompt may name
        # the owner with the {OWNER} token (settings.system_prompt).
        system_prompt = owner_text(state["system_prompt"])

        # Context pack: when the owner replies in an edward_thought conversation,
        # hand Edward back the original wake-up signals so he remembers
        # what he was talking about. Only inject on the first user reply
        # (1 human message in state) — after that, chat history carries it.
        try:
            from langchain_core.messages import HumanMessage as _HM
            user_msgs = [m for m in state.get("messages", []) if isinstance(m, _HM)]
            conv_id = state.get("conversation_id")
            if conv_id and len(user_msgs) <= 1:
                import json as _json
                from sqlalchemy import select as _select
                from services.database import (
                    async_session as _async_session,
                    ConversationModel as _ConvModel,
                )
                async with _async_session() as _s:
                    _row = (await _s.execute(
                        _select(_ConvModel).where(_ConvModel.id == conv_id)
                    )).scalar_one_or_none()
                if _row and _row.context_pack:
                    try:
                        _pack = _json.loads(_row.context_pack)
                        _pack_str = _json.dumps(_pack, indent=2, default=str)[:4000]
                    except (ValueError, TypeError):
                        _pack_str = (_row.context_pack or "")[:4000]
                    system_prompt = (
                        system_prompt
                        + "\n\n## What I was thinking about (signal pack from when I pinged you):\n"
                        + "```json\n" + _pack_str + "\n```\n"
                        + owner_text("Use these specifics when {OWNER} asks about them.")
                    )
        except Exception:
            # Context-pack injection is best-effort; never fail the chat.
            pass

        # Inject retrieved memories if available
        retrieved_memories = state.get("retrieved_memories", [])
        if retrieved_memories:
            from services.graph.streaming import format_memory_line

            memory_context = "\n\n## Relevant Context from Previous Conversations:\n"
            for memory in retrieved_memories:
                memory_context += format_memory_line(memory) + "\n"
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
