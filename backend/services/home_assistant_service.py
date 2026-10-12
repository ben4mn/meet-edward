"""
Home Assistant service for Edward.

Provides REST API client for Home Assistant to interact with Alexa/Echo devices
via the Alexa Media Player integration.
"""

import os
from typing import Optional, List, Dict
import httpx


# Configuration
HA_URL = os.getenv("HOME_ASSISTANT_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN")
HA_ECHO_PREFIX = os.getenv("HOME_ASSISTANT_ECHO_PREFIX", "media_player.echo")
# The Echo a sound or a volume change goes to when none is named (an
# announcement with no device goes to every Echo). The original household's
# was ``media_player.echo_dot``; set HOME_ASSISTANT_DEFAULT_DEVICE to yours.
HA_DEFAULT_DEVICE = os.getenv("HOME_ASSISTANT_DEFAULT_DEVICE") or f"{HA_ECHO_PREFIX}_dot"

# Timeout for HA API calls
_TIMEOUT = 10


def is_configured() -> bool:
    """Check if Home Assistant is configured."""
    return bool(HA_TOKEN)


def get_status() -> dict:
    """Get Home Assistant connection status for the skills panel."""
    if not HA_TOKEN:
        return {
            "status": "error",
            "status_message": "HOME_ASSISTANT_TOKEN not set",
        }

    return {
        "status": "connected",
        "status_message": f"Configured ({HA_URL})",
    }


def _headers() -> dict:
    """Build authorization headers for HA API."""
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


async def health_check() -> bool:
    """Check if Home Assistant is reachable."""
    if not HA_TOKEN:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{HA_URL}/api/", headers=_headers())
            return resp.status_code == 200
    except Exception:
        return False


async def get_echo_devices() -> List[Dict]:
    """
    Discover Echo devices registered in Home Assistant.

    Returns list of dicts with entity_id, friendly_name, and state.
    """
    if not HA_TOKEN:
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{HA_URL}/api/states", headers=_headers())
            resp.raise_for_status()

        entities = resp.json()
        devices = []
        for entity in entities:
            eid = entity.get("entity_id", "")
            if eid.startswith(HA_ECHO_PREFIX):
                devices.append({
                    "entity_id": eid,
                    "friendly_name": entity.get("attributes", {}).get("friendly_name", eid),
                    "state": entity.get("state", "unknown"),
                })
        return devices
    except Exception as e:
        print(f"HA device discovery error: {e}")
        return []


async def announce(message: str, device: Optional[str] = None) -> dict:
    """
    Send a TTS announcement to an Echo device via Home Assistant.

    Uses the notify.alexa_media service for announcements (plays on all
    devices if no specific device is given).

    Args:
        message: Text to announce
        device: Optional entity_id or friendly name of target device

    Returns:
        Dict with success status and details
    """
    if not HA_TOKEN:
        return {"success": False, "error": "HOME_ASSISTANT_TOKEN not set"}

    target = await _resolve_device(device)

    try:
        # Use notify.alexa_media for announcement-style TTS
        payload = {
            "message": message,
            "data": {"type": "announce"},
        }
        if target:
            payload["target"] = target

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{HA_URL}/api/services/notify/alexa_media",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()

        device_label = target or "all devices"
        return {"success": True, "device": device_label, "message": message}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HA API error: {e.response.status_code} {e.response.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def play_sound(sound_type: str = "notification", device: Optional[str] = None) -> dict:
    """
    Play a notification sound on an Echo device.

    Args:
        sound_type: Type of sound (notification, alarm, doorbell)
        device: Optional entity_id or friendly name

    Returns:
        Dict with success status
    """
    if not HA_TOKEN:
        return {"success": False, "error": "HOME_ASSISTANT_TOKEN not set"}

    # Map sound types to Alexa sound library
    sound_map = {
        "notification": "amzn_sfx_bell_short_chime_02",
        "alarm": "amzn_sfx_alarm_clock_01",
        "doorbell": "amzn_sfx_doorbell_chime_02",
        "alert": "amzn_sfx_trumpet_bugle_04",
    }
    sound_id = sound_map.get(sound_type, sound_map["notification"])

    target = await _resolve_device(device)
    entity_id = target or HA_DEFAULT_DEVICE

    try:
        payload = {
            "entity_id": entity_id,
            "media_content_id": f"sound:{sound_id}",
            "media_content_type": "sound",
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{HA_URL}/api/services/media_player/play_media",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()

        return {"success": True, "sound": sound_type, "device": entity_id}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HA API error: {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def set_volume(volume: float, device: Optional[str] = None) -> dict:
    """
    Set volume on an Echo device.

    Args:
        volume: Volume level 0.0 to 1.0
        device: Optional entity_id or friendly name

    Returns:
        Dict with success status
    """
    if not HA_TOKEN:
        return {"success": False, "error": "HOME_ASSISTANT_TOKEN not set"}

    volume = max(0.0, min(1.0, volume))
    target = await _resolve_device(device)
    entity_id = target or HA_DEFAULT_DEVICE

    try:
        payload = {
            "entity_id": entity_id,
            "volume_level": volume,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{HA_URL}/api/services/media_player/volume_set",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()

        return {"success": True, "volume": volume, "device": entity_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _resolve_device(device: Optional[str]) -> Optional[str]:
    """
    Resolve a device name or entity_id to an entity_id.

    If already an entity_id (starts with media_player.), returns as-is.
    Otherwise searches by friendly name.
    """
    if not device:
        return None

    if device.startswith("media_player."):
        return device

    # Search by friendly name
    devices = await get_echo_devices()
    for d in devices:
        if d["friendly_name"].lower() == device.lower():
            return d["entity_id"]

    # Fuzzy: check if device name is contained in friendly name
    for d in devices:
        if device.lower() in d["friendly_name"].lower():
            return d["entity_id"]

    return None
