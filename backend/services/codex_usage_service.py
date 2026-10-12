"""
Codex usage surface for the settings UI.

One source: the account's real meter (used-percent / window / reset per
window), as the rate gate last saw it in a Codex response's ``x-codex-*``
headers or fetched from ``GET /backend-api/wham/usage``.

The Work room reads ``GET /api/settings/codex-usage`` and renders the
meter and the pace decision without anyone needing to tail logs. (It
used to live under ``/api/evolution/`` and carry the evolution loop's
own throttle signals; self-evolution was removed 2026-09-12.)
"""

import base64
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import httpx
logger = logging.getLogger(__name__)

# The account's usage meter. The same numbers ride every Codex response's
# ``x-codex-*`` headers; this endpoint is for when nothing has called
# recently (startup, a stale sample).
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _account_id_from_token(token: str) -> Optional[str]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
    except Exception:  # noqa: BLE001
        return None


async def fetch_live_usage(timeout: float = 6.0) -> Optional[dict]:
    """``GET wham/usage`` as the signed-in ChatGPT account, or None.

    Only meaningful on the Codex OAuth path; an API-key deployment has no
    such meter and gets None.
    """
    from services.codex_auth_service import get_codex_auth_manager

    manager = get_codex_auth_manager()
    if getattr(manager, "_auth_source", None) != "codex_oauth":
        return None
    token = manager.get_api_key()
    if not token:
        return None
    account_id = getattr(manager, "_account_id", None) or _account_id_from_token(token)
    headers = {"Authorization": f"Bearer {token}", "originator": "edward"}
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(CODEX_USAGE_URL, headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.debug("codex usage fetch failed: %s", exc)
        return None
    if resp.status_code != 200:
        logger.debug("codex usage fetch: HTTP %s", resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def refresh_gate_meter() -> bool:
    """Fetch the live meter and hand it to the rate gate. True when it did."""
    from services.llm_rate_gate import get_llm_rate_gate

    payload = await fetch_live_usage()
    if payload is None:
        return False
    get_llm_rate_gate().note_usage_snapshot(payload)
    return True


def parse_reset_time(err_text: Optional[str]) -> Optional[datetime]:
    """Parse Codex's 'try again at X' into a future ``datetime``.

    Two formats:
      - Long: 'try again at May 27th, 2026 1:15 AM'
      - Short: 'try again at 6:16 AM' (date inferred — today if still
        in the future, else tomorrow).
    """
    if not err_text:
        return None

    # Long form
    m = re.search(
        r"try again at (\w+ \d+(?:st|nd|rd|th)?,\s*\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)",
        err_text,
    )
    if m:
        ts_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", m.group(1))
        for fmt in ("%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p"):
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue

    # Short form
    m = re.search(r"try again at (\d{1,2}:\d{2}\s*[AP]M)\b", err_text)
    if m:
        try:
            t = datetime.strptime(m.group(1).strip(), "%I:%M %p").time()
        except ValueError:
            return None
        now = datetime.now()
        candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    return None


async def get_usage_status(with_surplus: bool = False) -> dict:
    """The meter as the gate sees it, plus its pace decision.

    ``status`` is ``throttled`` while any window is exhausted (until the
    reset it names), else ``available``.

    ``with_surplus`` (the settings route's name, kept): the payload also
    says which ``provider`` Edward runs on and carries the ``claude`` block
    (today's spend, the cap, the credits left — ``llm_spend.claude_block``,
    2026-10-10). Under ``EDWARD_LLM_PROVIDER=anthropic`` no Codex meter is
    fetched and the windows are empty.

    The surplus queue and its ``surplus`` / ``spend_top`` blocks are gone
    (2026-10-11, ``docs/architecture/surplus.md``): their unguarded import
    would have made this card 500 on the Codex provider.
    """
    from services import llm_spend

    if llm_spend.is_anthropic():
        return await _claude_status(with_surplus)
    meter = await _meter_status()
    primary = meter["windows"].get("primary")
    throttled = bool(meter["limit_reached"] and primary)

    out = {
        "status": "throttled" if throttled else "available",
        "plan_type": meter["plan_type"],
        "meter": {
            "windows": {
                name: {
                    "used_percent": w["used_percent"],
                    "window_minutes": w["window_minutes"],
                    "reset_after_seconds": w["reset_after_seconds"],
                    "reset_at": datetime.fromtimestamp(w["reset_at"]).astimezone().isoformat(),
                    "elapsed_fraction": w["elapsed_fraction"],
                }
                for name, w in meter["windows"].items()
            },
            "age_seconds": meter["meter_age_seconds"],
            "limit_reached": meter["limit_reached"],
        },
        "background_allowed": meter["background_allowed"],
        "background_reason": meter["background_reason"],
        "background_ceiling_percent": meter["background_ceiling_percent"],
        "cooldown_remaining": meter["cooldown_remaining"],
        "calls_last_hour": meter["calls_last_hour"],
    }
    if with_surplus:
        out["provider"] = "codex"
        out["claude"] = await _claude_block()
    return out


async def _claude_block() -> dict:
    from services import llm_spend

    try:
        return await llm_spend.claude_block()
    except Exception as exc:  # noqa: BLE001
        logger.debug("codex usage: claude block failed: %s", exc)
        return {"error": str(exc)[:120]}


async def _claude_status(with_surplus: bool) -> dict:
    """The card's payload under pay-per-token: the spend, not a meter."""
    from services.llm_rate_gate import get_llm_rate_gate

    block = await _claude_block()
    gate = get_llm_rate_gate()
    allowed, reason = gate.background_decision()
    if "background_allowed" in block:
        allowed, reason = bool(block["background_allowed"]), str(block.get("background_reason") or reason)
    out = {
        "provider": "anthropic",
        "status": "throttled" if block.get("interactive_allowed") is False else "available",
        "plan_type": None,
        "meter": {"windows": {}, "age_seconds": None, "limit_reached": False},
        "background_allowed": allowed,
        "background_reason": reason,
        "background_ceiling_percent": round(float(block.get("background_share") or 0.0) * 100.0, 1),
        "cooldown_remaining": 0.0,
        "calls_last_hour": gate.status().get("calls_last_hour", 0),
        "claude": block,
    }
    return out


async def _meter_status() -> dict:
    """The gate's view of the meter, fetching one when it has none."""
    from services.llm_rate_gate import get_llm_rate_gate

    gate = get_llm_rate_gate()
    if gate.meter is None:
        try:
            await refresh_gate_meter()
        except Exception as exc:  # noqa: BLE001
            logger.debug("codex usage: meter fetch failed: %s", exc)
    return gate.status()
