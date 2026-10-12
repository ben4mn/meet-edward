"""Goals and ideas leave Today (proactivity rebuild, unit G, 2026-10-11).

Plan Phase 5 (2026-09-24) gave Ellis goals (documents with check-in wakes)
and Edward ideas (``edward_notes`` of kind ``idea``) on Today. In the
following two weeks Ellis set 0 goals and Edward left 1 idea. The feature
goes: ``goals_service``, ``/api/goals*``, ``/api/ideas*`` and Today's goals
and ideas lists. What stays:

* every goal document (they are documents) and the idea notes in the table;
* an old idea note is still neither a need nor history on Today;
* deleting or forgetting a document no longer reaches for check-in wakes.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


@pytest.mark.unit
@pytest.mark.parametrize("name", ["services.goals_service", "routers.goals", "routers.ideas"])
def test_the_modules_are_gone(name):
    with pytest.raises(ImportError):
        importlib.import_module(name)


@pytest.mark.unit
def test_no_goal_or_idea_route_is_served():
    from main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if p.startswith(("/api/goals", "/api/ideas"))], paths


@pytest.mark.unit
def test_today_carries_no_goals_or_ideas():
    from services.today_service import TodayInputs, compose_today

    out = compose_today(TodayInputs(), now=NOW)
    assert "goals" not in out and "ideas" not in out
    assert not hasattr(TodayInputs(), "goals") and not hasattr(TodayInputs(), "ideas")


@pytest.mark.unit
def test_an_old_idea_note_is_still_neither_a_need_nor_history():
    from services.today_service import TodayInputs, compose_today

    notes = [{"id": "n1", "kind": "idea", "title": "Try X", "body": "why", "href": "/chat?prompt=x",
              "needs_you": True, "created_at": _iso(NOW - timedelta(hours=1)),
              "expires_at": _iso(NOW + timedelta(days=6))},
             {"id": "n2", "kind": "mind", "title": "FYI", "body": "b", "href": "/today", "needs_you": False,
              "created_at": _iso(NOW - timedelta(hours=1)), "expires_at": _iso(NOW + timedelta(hours=20))}]
    out = compose_today(TodayInputs(notes=notes), now=NOW)
    assert out["needs"] == []
    assert [d["id"] for d in out["did"]] == ["note:n2"]


@pytest.mark.unit
def test_live_ideas_is_gone_from_notes():
    from services import notes_service

    assert not hasattr(notes_service, "live_ideas") and not hasattr(notes_service, "resolve_idea")


@pytest.mark.integration
async def test_forgetting_a_document_never_reaches_for_goals(clean_db):
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "services" / "forget_service.py").read_text()
    assert "goals_service" not in src and "retire_check_ins_for" not in src
