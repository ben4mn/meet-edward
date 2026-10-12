#!/usr/bin/env python3
"""A live smoke test of ChatClaude — NOT part of the test suite.

Sends a few real requests to the Anthropic API (costs a few cents) and
prints what came back, so a change to ``services/claude_chat_model.py`` can
be checked against the real thing before it is deployed:

1. one tool-using request (a bound ``get_weather`` tool) — stop_reason,
   tool_calls, usage;
2. the identical request again — the cache read on the static prefix;
3. the tool round: the second answer and a tool result streamed back, with
   its thinking blocks replayed verbatim and the per-turn context sent as
   a mid-conversation system message — the streamed text, the reasoning
   summaries, the final usage.

For ``claude-haiku-5-5`` and ``claude-opus-5-5``, both at low effort. At low
effort the models usually skip thinking; ``--effort high --question "…"``
with a question worth weighing makes step 3 replay real thinking blocks.

    cd backend && .venv/bin/python scripts/claude_smoke.py
    (options: --models claude-haiku-5-5 --effort low --env /path/to/.env)

Reads ``ANTHROPIC_API_KEY`` from the environment, else from the repo's
``.env`` (never printed). Writes nothing to any database: the spend-ledger
rows are switched off for this process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.host_config import repo_root  # noqa: E402
from services.owner import owner_text  # noqa: E402

STATIC_PROMPT = owner_text("""You are Edward, {OWNER}'s assistant, running a short connectivity check.

## How you answer
- Use a tool when its result would change your answer. For weather questions, call get_weather.
- Keep answers to one or two sentences. Plain language, first person.
- Never invent a tool result.

## Reference notes (static; they make the prompt long enough to cache)
""") + "\n".join(
    owner_text(f"- Note {i}: Edward keeps his promises small and specific, says what he did and what is left, "
               "and asks only when he cannot go on without {OWNER}. He never guesses a time, a name or a number.")
    for i in range(1, 41)
)


DEFAULT_QUESTION = "What's the weather in Paris right now?"


def _load_env(path: Path) -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        from dotenv import dotenv_values
    except Exception:  # noqa: BLE001
        return
    values = dotenv_values(path) if path.exists() else {}
    key = values.get("ANTHROPIC_API_KEY")
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key


def _usage_line(msg) -> str:
    um = getattr(msg, "usage_metadata", None) or {}
    details = um.get("input_token_details") or {}
    meta = getattr(msg, "response_metadata", None) or {}
    cost = meta.get("estimated_cost_usd")
    cost_text = f"${cost:.5f}" if cost is not None else "-"
    return (f"input={um.get('input_tokens')} (cache_read={details.get('cache_read')}, "
            f"cache_write={details.get('cache_creation')}) output={um.get('output_tokens')} est_cost={cost_text}")


async def _smoke(model: str, effort: str, question: str = DEFAULT_QUESTION) -> dict:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool

    from services.claude_chat_model import CLAUDE_CONTENT_KEY, STATIC_DYNAMIC_SPLIT, ChatClaude

    @tool
    def get_weather(city: str) -> str:
        """Current weather for a city. Call this for any weather question."""
        return "18 C, light rain"

    llm = ChatClaude(model=model, effort=effort, max_tokens=4000, purpose="smoke:claude").bind_tools([get_weather])
    system = SystemMessage(content=STATIC_PROMPT + STATIC_DYNAMIC_SPLIT + "Current date and time: smoke check.")
    messages = [system, HumanMessage(content=question)]
    out: dict = {"model": model}

    print(f"\n=== {model} (effort {effort}) ===")
    t0 = time.monotonic()
    first = await llm.ainvoke(messages)
    print(f"[1] {time.monotonic() - t0:.1f}s stop_reason={first.response_metadata.get('stop_reason')} "
          f"tool_calls={json.dumps(first.tool_calls)}")
    print(f"    text={first.content!r}")
    print(f"    {_usage_line(first)}")
    out["first"] = first

    t0 = time.monotonic()
    second = await llm.ainvoke(messages)
    print(f"[2] {time.monotonic() - t0:.1f}s identical request: stop_reason={second.response_metadata.get('stop_reason')} "
          f"tool_calls={len(second.tool_calls)}")
    print(f"    {_usage_line(second)}")
    out["second"] = second

    if second.tool_calls:
        call = second.tool_calls[0]
        blocks = second.additional_kwargs.get(CLAUDE_CONTENT_KEY) or []
        print(f"    replaying {len(blocks)} raw block(s): {[b.get('type') for b in blocks]}")
        round_two = messages + [second, ToolMessage(content=get_weather.invoke(call["args"]), tool_call_id=call["id"])]
        text, reasoning, final = [], [], None
        t0 = time.monotonic()
        async for chunk in llm.astream(round_two):
            if isinstance(chunk.content, str) and chunk.content:
                text.append(chunk.content)
            if chunk.additional_kwargs.get("reasoning_delta"):
                reasoning.append(chunk.additional_kwargs["reasoning_delta"])
            if chunk.response_metadata.get("stop_reason"):
                final = chunk
        print(f"[3] {time.monotonic() - t0:.1f}s tool round (streamed): "
              f"stop_reason={final.response_metadata.get('stop_reason') if final else None}")
        print(f"    text={''.join(text)!r}")
        print(f"    reasoning deltas={len(reasoning)} ({len(''.join(reasoning))} chars)")
        if final is not None:
            print(f"    {_usage_line(final)}")
            dropped = final.response_metadata.get("input_transformations")
            print(f"    input_transformations={json.dumps(dropped) if dropped else '[] (nothing dropped)'}")
        out["third"] = final
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", default=["claude-haiku-5-5", "claude-opus-5-5"])
    parser.add_argument("--effort", default="low")
    parser.add_argument("--question", default=DEFAULT_QUESTION,
                        help="what to ask; a harder one makes the model think, so step 3 replays thinking blocks")
    # The checkout's .env, else the live repo's (a worktree has none).
    live = repo_root() / ".env"
    local = BACKEND.parent / ".env"
    parser.add_argument("--env", default=str(local if local.exists() else live))
    args = parser.parse_args()

    _load_env(Path(args.env))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (environment or .env)")
        return 1

    from services import codex_call_ledger

    codex_call_ledger.WRITES_ENABLED = False  # nothing reaches a database from here
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("services.claude_chat_model").setLevel(logging.INFO)

    total = 0.0
    for model in args.models:
        try:
            result = await _smoke(model, args.effort, args.question)
        except Exception as exc:  # noqa: BLE001
            print(f"!! {model}: {type(exc).__name__}: {exc}")
            return 2
        for key in ("first", "second", "third"):
            msg = result.get(key)
            cost = (getattr(msg, "response_metadata", None) or {}).get("estimated_cost_usd") if msg else None
            total += cost or 0.0
    print(f"\nestimated total cost: ${total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
