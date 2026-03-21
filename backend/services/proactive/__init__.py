"""
Proactive intelligence services for Edward.

Manages autonomous background services:
- Morning briefing
- Meeting prep
- Pattern detection (weekly)
- Open loop nudges (daily)
"""

from services.proactive.morning_briefing import (
    start_morning_briefing,
    stop_morning_briefing,
    is_morning_briefing_running,
)
from services.proactive.meeting_prep import (
    start_meeting_prep,
    stop_meeting_prep,
    is_meeting_prep_running,
)
from services.proactive.pattern_detection import (
    start_pattern_detection,
    stop_pattern_detection,
    is_pattern_detection_running,
)
from services.proactive.open_loop_nudge import (
    start_open_loop_nudge,
    stop_open_loop_nudge,
    is_open_loop_nudge_running,
)


async def start_proactive_services() -> None:
    """Start all proactive services based on config."""
    from sqlalchemy import select
    from services.database import async_session, ProactiveConfigModel

    async with async_session() as session:
        result = await session.execute(
            select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()

    if not config:
        print("Proactive services: no config found, skipping")
        return

    if config.morning_briefing_enabled:
        await start_morning_briefing(config)
    if config.meeting_prep_enabled:
        await start_meeting_prep(config)
    if config.pattern_detection_enabled:
        await start_pattern_detection()
    if config.open_loop_nudge_enabled:
        await start_open_loop_nudge(config)

    print("Proactive services initialized")


async def stop_proactive_services() -> None:
    """Stop all proactive services."""
    await stop_morning_briefing()
    await stop_meeting_prep()
    await stop_pattern_detection()
    await stop_open_loop_nudge()
    print("Proactive services stopped")
