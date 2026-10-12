"""Persistence for the Codex rate gate's meter.

The meter is the account's usage window as the last Codex response reported
it. Carrying it across restarts means a launchd bounce does not begin blind: the first background calls after start are
paced against the last known numbers (aged on read) until the next response
refreshes them.

Every operation here is best-effort. The gate is a pacer, not a source of
truth: if the row cannot be read or written, the worst case is a cold start,
so nothing in this module may raise into a caller's path.

The table keeps the ``budget`` / ``last_grow_wall`` columns of the retired
budget pacer so an old row stays readable; they are written as zero.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text

from services.database import async_session

logger = logging.getLogger(__name__)

_ROW_ID = "default"


async def load_gate_state() -> Optional[dict]:
    """The persisted state, or None when there is none."""
    try:
        async with async_session() as session:
            row = (
                await session.execute(
                    text("SELECT meter_json FROM llm_rate_gate_state WHERE id = :id"),
                    {"id": _ROW_ID},
                )
            ).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_rate_gate_store: load failed (non-fatal): %s", exc)
        return None

    if row is None:
        return None
    raw = row[0]
    if not raw:
        return {"meter": None}
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return {"meter": None}
    return state if isinstance(state, dict) else {"meter": None}


async def save_gate_state(state: dict) -> None:
    """Upsert the state. The gate has one, not a history."""
    try:
        payload = json.dumps(state)
    except (TypeError, ValueError) as exc:
        logger.warning("llm_rate_gate_store: state not serialisable (non-fatal): %s", exc)
        return
    try:
        async with async_session() as session:
            await session.execute(
                text(
                    "INSERT INTO llm_rate_gate_state (id, budget, last_grow_wall, meter_json, "
                    "updated_at) VALUES (:id, 0, 0, :meter, NOW()) "
                    "ON CONFLICT (id) DO UPDATE SET meter_json = EXCLUDED.meter_json, "
                    "updated_at = EXCLUDED.updated_at"
                ),
                {"id": _ROW_ID, "meter": payload},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_rate_gate_store: save failed (non-fatal): %s", exc)


async def restore_gate_from_db(gate) -> None:
    """Resume a gate from its persisted state, if there is one."""
    state = await load_gate_state()
    if state is not None:
        gate.restore_state(state)
        if gate.meter is not None:
            allowed, reason = gate.background_decision()
            logger.info(
                "llm_rate_gate_store: restored meter (%s); background %s — %s",
                gate.meter.plan_type or "plan unknown", "allowed" if allowed else "deferred", reason,
            )


async def persist_gate(gate) -> None:
    """Write the gate's current state out."""
    await save_gate_state(gate.export_state())
