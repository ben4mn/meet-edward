"""
Open loop nudge service for Edward.

Daily check for stale open loops that haven't been nudged recently.
Generates gentle nudge messages and delivers via push notification.
"""

import asyncio
from datetime import datetime, timedelta

from services.timezone_utils import now_local

_task: asyncio.Task | None = None
_nudge_days: int = 7
_DAILY_CHECK_SECONDS = 24 * 3600


def is_open_loop_nudge_running() -> bool:
    return _task is not None and not _task.done()


async def start_open_loop_nudge(config) -> None:
    global _task, _nudge_days
    if _task is not None:
        return
    _nudge_days = getattr(config, "open_loop_nudge_days", 7) or 7
    _task = asyncio.create_task(_nudge_loop())
    print(f"Open loop nudge started (threshold={_nudge_days}d)")


async def stop_open_loop_nudge() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    print("Open loop nudge stopped")


async def _nudge_loop() -> None:
    # Wait 2 hours on first run to avoid startup load
    await asyncio.sleep(7200)

    while True:
        try:
            from services.open_loop_service import reactivate_snoozed_loops
            reactivated = await reactivate_snoozed_loops()
            if reactivated:
                print(f"[NUDGE] Reactivated {reactivated} snoozed loops")

            await _check_and_nudge()
        except Exception as e:
            print(f"[NUDGE] Check failed: {e}")

        await asyncio.sleep(_DAILY_CHECK_SECONDS)


async def _check_and_nudge() -> None:
    from services.open_loop_service import get_active_open_loops, update_nudge

    loops = await get_active_open_loops(limit=20)
    if not loops:
        return

    now = datetime.utcnow()
    nudge_threshold = timedelta(days=_nudge_days)
    loops_to_nudge = []

    for loop in loops:
        age = now - loop.detected_at if loop.detected_at else timedelta(days=0)
        if age < nudge_threshold:
            continue

        if loop.last_nudged_at:
            since_nudge = now - loop.last_nudged_at
            if since_nudge < nudge_threshold:
                continue

        loops_to_nudge.append(loop)

    if not loops_to_nudge:
        return

    from services.llm_utils import build_background_llm, make_background_system_message
    from langchain_core.messages import HumanMessage

    loop_descriptions = "\n".join(
        f"- {l.description} ({(now - l.detected_at).days}d ago)"
        for l in loops_to_nudge[:5]
    )

    llm = build_background_llm(max_tokens=256)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(
                    "Generate a gentle, friendly reminder about unresolved items. "
                    "Be brief — 1-2 sentences. Don't be pushy. "
                    "Frame as a helpful nudge, not a nag."
                ),
                HumanMessage(content=f"These items have been open for a while:\n{loop_descriptions}"),
            ]),
            timeout=8.0,
        )

        nudge_text = response.content
        if isinstance(nudge_text, list):
            nudge_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in nudge_text
            )
        nudge_text = nudge_text.strip()

    except Exception as e:
        print(f"[NUDGE] LLM generation failed: {e}")
        nudge_text = f"You have {len(loops_to_nudge)} items that could use attention."

    try:
        from services.push_service import send_push_to_all
        await send_push_to_all(
            title="Open items reminder",
            body=nudge_text[:200],
        )
        print(f"[NUDGE] Sent nudge for {len(loops_to_nudge)} open loops")
    except Exception as e:
        print(f"[NUDGE] Push delivery failed: {e}")

    for loop in loops_to_nudge:
        await update_nudge(loop.id)
