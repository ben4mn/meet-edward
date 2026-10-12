"""Persistently remove dead assistant turns from a conversation checkpoint.

A turn that produced neither text nor tool calls, plus the nudge it provoked,
is scaffolding from a failed attempt. AgentState reduces `messages` with
`add_messages`, which merges by id — omitting a message from a state update
leaves it in the checkpoint — so removal needs an explicit RemoveMessage.

Usage:
    python scripts/prune_dead_turns.py <conversation_id> [--apply]

Without --apply it reports what it would remove and changes nothing.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Sequence

from langchain_core.messages import RemoveMessage

from services.database import DATABASE_URL
from services.graph import get_graph, initialize_graph
from services.graph.history_window import dead_turn_ids


@dataclass(frozen=True)
class PruneReport:
    messages: int
    characters: int
    doomed: List[str]


def plan_prune(messages: Sequence) -> PruneReport:
    """Pure: what the script would remove, and the thread's size."""
    chars = sum(len(str(getattr(m, "content", ""))) for m in messages)
    return PruneReport(messages=len(messages), characters=chars, doomed=dead_turn_ids(list(messages)))


def chunks(seq: Sequence, size: int) -> Iterator[list]:
    """Yield ``seq`` in lists of at most ``size``."""
    for i in range(0, len(seq), size):
        yield list(seq[i : i + size])


async def main(conversation_id: str, apply: bool) -> int:
    await initialize_graph(DATABASE_URL)
    graph = await get_graph()
    config = {"configurable": {"thread_id": conversation_id}}

    state = await graph.aget_state(config)
    if not state.values:
        print(f"No state for {conversation_id}")
        return 1

    messages = list(state.values.get("messages", []))
    report = plan_prune(messages)
    doomed, chars = report.doomed, report.characters

    print(f"thread          : {conversation_id}")
    print(f"messages        : {len(messages)}")
    print(f"characters      : {chars:,}")
    print(f"dead turns      : {len(doomed)}")

    if not doomed:
        print("nothing to prune")
        return 0

    if not apply:
        print("\ndry run — pass --apply to remove them")
        return 0

    # Chunked so one oversized update doesn't blow the write.
    done = 0
    for batch in chunks(doomed, 200):
        done += len(batch)
        # as_node is required outside a run: the graph cannot infer which
        # node an offline edit belongs to. "respond" is the node that owns
        # `messages`.
        await graph.aupdate_state(
            config,
            {"messages": [RemoveMessage(id=mid) for mid in batch]},
            as_node="respond",
        )
        print(f"  removed {done}/{len(doomed)}")

    after = await graph.aget_state(config)
    after_messages = list(after.values.get("messages", []))
    after_chars = sum(len(str(getattr(m, "content", ""))) for m in after_messages)
    print(f"\nmessages        : {len(messages)} -> {len(after_messages)}")
    print(f"characters      : {chars:,} -> {after_chars:,}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(
        asyncio.run(main(sys.argv[1], "--apply" in sys.argv))
    )
