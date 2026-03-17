import logging
import json
from datetime import datetime

_logger = logging.getLogger("governance.sample")


def log_turn_sample(
    conversation_id: str,
    message_preview: str,
    response_preview: str,
    tool_calls_made: list[str],
    has_plan: bool,
    plan_completed: bool,
) -> None:
    """
    Emit a structured sample record for governance measurement.
    Zero latency — pure logging, no LLM calls, no DB writes.
    """
    record = {
        "ts": datetime.utcnow().isoformat(),
        "conv": conversation_id,
        "msg": message_preview[:100],
        "resp": response_preview[:200],
        "tools": tool_calls_made,
        "has_plan": has_plan,
        "plan_completed": plan_completed,
    }
    _logger.debug(json.dumps(record))
