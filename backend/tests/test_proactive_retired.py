"""The five proactive jobs are gone — mind rebuild Phase 7.

Morning briefing, meeting prep, open-loop nudge, weekly patterns and
relationship maintenance were templated services with their own prompts,
schedules and push calls. The morning and evening wakes carry their inputs
already, meeting prep is something the mind schedules for itself with
``wake_me``, and weekly patterns is the Sunday ``reflect`` wake. What Ellis
still owns (open loops, entities, dossier thresholds) stays reachable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
RETIRED = ("morning_briefing", "meeting_prep", "open_loop_nudge", "pattern_detection",
           "relationship_maintenance_job")


def test_job_modules_are_gone():
    for name in RETIRED:
        assert not (BACKEND / "services" / "proactive" / f"{name}.py").exists(), name
    for path in list((BACKEND / "services").rglob("*.py")) + list((BACKEND / "routers").rglob("*.py")) + [BACKEND / "main.py"]:
        text = path.read_text()
        for name in RETIRED:
            assert f"proactive.{name}" not in text, f"{path} still imports {name}"


def test_the_proactive_package_is_gone():
    """It started nothing since Phase 7; the package and its lifespan
    calls went with the proactivity rebuild (2026-10-11)."""
    assert not (BACKEND / "services" / "proactive").exists()
    with pytest.raises(ImportError):
        import services.proactive  # noqa: F401
    assert "start_proactive_services" not in (BACKEND / "main.py").read_text()


def test_status_and_config_schemas_carry_no_retired_job():
    from services.proactive_models import ProactiveConfigSchema, ProactiveConfigUpdate, ProactiveStatusSchema

    assert not [f for f in ProactiveStatusSchema.model_fields if f.endswith("_running")]
    assert "wakes" in ProactiveStatusSchema.model_fields
    for schema in (ProactiveConfigSchema, ProactiveConfigUpdate):
        fields = set(schema.model_fields)
        assert not {f for f in fields if f.startswith(("morning_briefing", "meeting_", "open_loop_nudge",
                                                       "pattern_detection", "relationship_maintenance"))}, fields
        assert "extraction_depth" not in fields, "nothing read it"
        assert {"dossier_enabled", "dossier_min_acted_pushes", "dossier_min_inbound_threads",
                "dossier_window_days"} <= fields


def test_note_kinds_are_mind_whisper_and_idea():
    from services.notes_service import _TTL_HOURS, ttl_hours_for

    # Plan Phase 5 (2026-09-24) added ``idea`` (suggest_idea, a week on
    # Today); the templated kinds stay gone.
    assert set(_TTL_HOURS) == {"mind", "whisper", "idea"}
    assert ttl_hours_for("mind") == 24
    assert ttl_hours_for("idea") == 24 * 7


def test_outreach_endpoints_are_gone():
    from main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if "outreach" in p], paths
    assert "/api/proactive/open-loops" in paths and "/api/proactive/entities" in paths


@pytest.mark.integration
async def test_notes_from_the_mind_reach_today(clean_db):
    from services.graph.tools import leave_note
    from services.today_service import TodayInputs, _notes, compose_today

    out = await leave_note.ainvoke({"title": "ACME moved 8%", "body": "Parker's text this morning; worth a look.",
                                    "needs_you": True})
    assert out.startswith("ok"), out
    notes = await _notes()
    assert notes and notes[0]["kind"] == "mind" and notes[0]["needs_you"]
    today = compose_today(TodayInputs(notes=notes))
    need = next(n for n in today["needs"] if n["id"] == f"note:{notes[0]['id']}")
    assert need["kind"] == "note" and need["title"] == "ACME moved 8%"


@pytest.mark.integration
async def test_status_endpoint_lists_the_wakes(clean_db):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        r = await c.get("/api/proactive/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert not [k for k in body if k.endswith("_running")]
        assert {(w["kind"], w["cron"]) for w in body["wakes"]} == {("nightly", "0 3 * * *"), ("free", "0 13 * * *")}
        r = await c.patch("/api/proactive/config", json={"dossier_window_days": 10})
        assert r.status_code == 200 and r.json()["dossier_window_days"] == 10
