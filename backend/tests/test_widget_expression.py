"""Tests for services/widget_expression_service.py — the iPhone widget as
Edward's expression space.

Why (2026-09-02, Ellis): the widget only ever showed auto-generated "next 3
events" content and nothing refreshed it proactively. Ellis wants Edward to
own it — whatever Edward wants to say first, then the house at a glance.
Timed doses appear only while someone is on a protocol (Biscuit is out of
recovery as of 2026-09-02). The composed default is a floor: Edward
may overwrite it with `update_widget` at any time, and his custom content
is left alone for a while before the auto refresh resumes.

Hub design rule 1 applies here too: window items (free-fed food) never
render as overdue, and `needs_review` doses keep their badge.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.widget_expression_service import (
    CUSTOM_HOLD_HOURS,
    compose_widget_sections,
    should_auto_refresh,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 2, 18, 30, tzinfo=timezone.utc)


def _board(biscuit_meds=None, feedings=None, alerts=None):
    return {
        "care_day": "2026-09-02",
        "window": "pm",
        "pets": [
            {
                "id": "biscuit", "name": "Biscuit", "species": "dog",
                "feedings": feedings or {"am": {"done": True, "by": "Ellis", "at_local": "6:05a", "overdue": False},
                                         "pm": {"done": False, "by": None, "at_local": None, "overdue": False}},
                "meds": biscuit_meds if biscuit_meds is not None else [],
            },
            {"id": "maple", "name": "Maple", "species": "cat",
             "feedings": {"am": {"done": True, "overdue": False}, "pm": {"done": False, "overdue": False}},
             "meds": []},
        ],
        "alerts": alerts or [],
    }


def _med(name="Carprofen", status="due", at_time="18:00", minutes_late=None, needs_review=False, by=None):
    return {"id": name.lower(), "name": name, "dose": "100mg", "kind": "med", "window": "pm", "at_time": at_time,
            "status": status, "by": by, "at_local": None, "needs_review": needs_review, "minutes_late": minutes_late}


def _find(sections, type_, **match):
    for s in sections:
        if s.get("type") == type_ and all(s.get(k) == v for k, v in match.items()):
            return s
    return None


class TestCompose:
    def test_biscuit_timed_dose_overdue_shows_amber(self):
        board = _board(biscuit_meds=[_med(status="overdue", minutes_late=25)],
                       alerts=[{"pet": "Biscuit", "item": "Carprofen", "minutes_late": 25}])
        out = compose_widget_sections(board=board, attention=None, now=NOW)
        biscuit = _find(out["sections"], "list", role="doses")
        assert biscuit is not None
        row = biscuit["items"][0]
        assert "Carprofen" in row["label"] and "25m late" in row["detail"]
        assert row["icon"] == "⚠️"
        assert out["theme"]["accentColor"] == "#f5a524"   # amber = something is actually late

    def test_window_items_never_overdue_on_widget(self):
        board = _board(feedings={"am": {"done": False, "overdue": False}, "pm": {"done": False, "overdue": False}})
        out = compose_widget_sections(board=board, attention=None, now=NOW)
        text = str(out)
        assert "late" not in text.lower()
        assert out["theme"]["accentColor"] != "#f5a524"

    def test_missed_dose_is_shown_but_not_alarming(self):
        board = _board(biscuit_meds=[_med(status="missed", minutes_late=300)])
        out = compose_widget_sections(board=board, attention=None, now=NOW)
        row = _find(out["sections"], "list", role="doses")["items"][0]
        assert "missed" in row["detail"].lower()
        assert out["theme"]["accentColor"] != "#f5a524"

    def test_needs_review_badge_survives(self):
        board = _board(biscuit_meds=[_med(needs_review=True)])
        out = compose_widget_sections(board=board, attention=None, now=NOW)
        row = _find(out["sections"], "list", role="doses")["items"][0]
        assert "?" in row["label"]

    def test_given_dose_shows_who_and_when(self):
        med = _med(status="given", by="Rowan")
        med["at_local"] = "6:02p"
        out = compose_widget_sections(board=_board(biscuit_meds=[med]), attention=None, now=NOW)
        row = _find(out["sections"], "list", role="doses")["items"][0]
        assert row["icon"] == "✅" and "Rowan" in row["detail"] and "6:02p" in row["detail"]

    def test_compose_prefers_edwards_line_when_present(self):
        out = compose_widget_sections(board=_board(biscuit_meds=[_med()]), attention=None, now=NOW, edwards_line="Storm rolling in at 9; walk Biscuit early.")
        text = _find(out["sections"], "text", role="edward")
        assert text is not None and "walk Biscuit early" in text["content"]
        assert out["sections"].index(text) < out["sections"].index(_find(out["sections"], "list", role="doses"))

    def test_no_edwards_line_gives_a_quiet_default(self):
        out = compose_widget_sections(board=_board(), attention=None, now=NOW)
        assert _find(out["sections"], "text", role="edward") is None
        assert out["subtitle"]

    def test_no_timed_doses_means_no_dose_section_at_all(self):
        """Biscuit is out of recovery: with nothing on a protocol the widget
        is Edward's line + bowls, not an empty medical panel."""
        out = compose_widget_sections(board=_board(), attention=None, now=NOW)
        assert _find(out["sections"], "list", role="doses") is None
        assert _find(out["sections"], "header", role="doses-header") is None
        assert _find(out["sections"], "stats_row", role="bowls") is not None

    def test_dose_rows_name_the_pet(self):
        out = compose_widget_sections(board=_board(biscuit_meds=[_med()]), attention=None, now=NOW)
        assert _find(out["sections"], "list", role="doses")["items"][0]["label"].startswith("Biscuit · ")

    def test_feedings_row_summarises_bowls(self):
        out = compose_widget_sections(board=_board(), attention=None, now=NOW)
        stats = _find(out["sections"], "stats_row", role="bowls")
        labels = {s["label"]: s["value"] for s in stats["stats"]}
        assert labels["Biscuit"] == "AM ✓ · PM –"
        assert labels["Maple"] == "AM ✓ · PM –"

    def test_next_due_appears_when_nothing_is_late(self):
        out = compose_widget_sections(board=_board(biscuit_meds=[_med(status="due", at_time="18:00")]), attention=None, now=NOW)
        row = _find(out["sections"], "list", role="doses")["items"][0]
        assert "18:00" in row["detail"] or "6:00" in row["detail"]

    def test_sections_fit_the_large_widget(self):
        board = _board(biscuit_meds=[_med(f"Med{i}") for i in range(8)])
        out = compose_widget_sections(board=board, attention=None, now=NOW, edwards_line="x")
        biscuit = _find(out["sections"], "list", role="doses")
        assert len(biscuit["items"]) <= 3
        assert len(out["sections"]) <= 7


class TestShouldAutoRefresh:
    def test_refreshes_when_state_is_auto(self):
        assert should_auto_refresh(updated_by="system", updated_at=NOW - timedelta(hours=1), now=NOW) is True

    def test_holds_off_while_edward_custom_content_is_fresh(self):
        assert should_auto_refresh(updated_by="edward", updated_at=NOW - timedelta(minutes=30), now=NOW) is False

    def test_resumes_after_the_custom_hold(self):
        assert should_auto_refresh(updated_by="edward", updated_at=NOW - timedelta(hours=CUSTOM_HOLD_HOURS + 1), now=NOW) is True

    def test_refreshes_when_never_set(self):
        assert should_auto_refresh(updated_by=None, updated_at=None, now=NOW) is True

    def test_forced_refresh_ignores_the_hold(self):
        assert should_auto_refresh(updated_by="edward", updated_at=NOW, now=NOW, force=True) is True


@pytest.mark.integration
class TestStaleScript:
    """Regression (2026-09-03): a raw Scriptable script Edward set on Aug 23
    silently overrode every refresh, so the phone kept showing 'Biscuit
    recovery' twelve days later. A script is custom content like any other:
    honoured for CUSTOM_HOLD_HOURS, then replaced by the composed widget."""

    async def _seed(self, updated_at, script="// old"):
        from sqlalchemy import update
        from services import database
        from services.database import WidgetStateModel
        async with database.async_session() as session:
            session.add(WidgetStateModel(id="default", title="Edward", subtitle="old", script=script,
                                         sections="[]", updated_by="edward"))
            await session.commit()
            await session.execute(update(WidgetStateModel).values(updated_at=updated_at))
            await session.commit()

    async def test_old_script_is_cleared_and_replaced(self, clean_db, monkeypatch):
        from sqlalchemy import select
        from services import database, hub_service, widget_expression_service as wes
        from services.database import WidgetStateModel

        async def fake_board():
            return _board()

        monkeypatch.setattr(hub_service, "get_board", fake_board)
        await self._seed(datetime.utcnow() - timedelta(days=12))
        out = await wes.refresh_widget_expression("cron")
        assert out is not None
        async with database.async_session() as session:
            row = (await session.execute(select(WidgetStateModel))).scalar_one()
        assert row.script is None and row.updated_by == "system" and "bowls" in (row.sections or "")

    async def test_fresh_script_is_left_alone(self, clean_db, monkeypatch):
        from services import hub_service, widget_expression_service as wes

        async def fake_board():
            return _board()

        monkeypatch.setattr(hub_service, "get_board", fake_board)
        await self._seed(datetime.utcnow() - timedelta(minutes=10))
        assert await wes.refresh_widget_expression("cron") is None

    async def test_refresh_endpoint_forces_a_recompose(self, clean_db, monkeypatch):
        from httpx import ASGITransport, AsyncClient
        from main import app
        from services import hub_service
        from services.auth_service import create_access_token

        async def fake_board():
            return _board()

        monkeypatch.setattr(hub_service, "get_board", fake_board)
        await self._seed(datetime.utcnow() - timedelta(minutes=10))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={"edward_token": create_access_token()}) as client:
            r = await client.post("/api/widget/refresh")
            assert r.status_code == 200, r.text
            assert r.json()["refreshed"] is True
            w = await client.get("/api/widget/token")
            token = w.json()["token"]
            r = await client.get(f"/api/widget?token={token}")
        body = r.json()
        assert body.get("script") in (None, "") and any(s.get("role") == "bowls" for s in body["sections"])
