"""The dead calendar listener is gone (2026-10-11, integrations audit item 6).

``listener_calendar.py`` polled Apple Calendar through apple-mcp for
upcoming events: ``calendar_enabled`` was false in production, it stored 0
events ever, and in 32 h it reopened 48 apple-mcp sessions. Every calendar
reader goes through ``display_service.get_calendar`` (Calendar's database
first, apple-mcp as the fallback), whose apple-mcp lookup —
``_find_calendar_tool`` — now lives in ``display_service`` itself. The
``calendar_*`` columns stay (``init_db`` drops nothing here), so an old
PATCH still saves; only the track is gone.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_the_module_is_gone_and_nothing_imports_it():
    assert importlib.util.find_spec("services.heartbeat.listener_calendar") is None
    offenders = []
    for path in list((BACKEND / "services").rglob("*.py")) + list((BACKEND / "routers").rglob("*.py")) + \
            [BACKEND / "main.py"]:
        if "listener_calendar" in path.read_text(errors="replace"):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == []


def test_the_apple_mcp_lookup_lives_in_display_service():
    from services import display_service

    assert callable(display_service._find_calendar_tool)
    assert "_find_calendar_tool()" in inspect.getsource(display_service._calendar_via_mcp)


def test_today_listens_to_no_calendar_track():
    from services.today_service import LISTEN_TRACKS

    assert "calendar" not in LISTEN_TRACKS
    assert {"imessage", "email", "gmail"} <= set(LISTEN_TRACKS)


def test_the_config_patch_no_longer_syncs_a_calendar_listener():
    from routers import heartbeat

    params = list(inspect.signature(heartbeat._sync_listeners).parameters)
    assert params == ["config", "prev_imessage", "prev_email"]


@pytest.mark.integration
async def test_the_status_has_no_calendar_track(clean_db):
    from services.heartbeat import heartbeat_service

    status = await heartbeat_service.get_heartbeat_status()
    assert "calendar" not in status["tracks"]
    assert {"imessage", "email", "gmail"} <= set(status["tracks"])
