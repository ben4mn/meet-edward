"""
Regression: /api/debug/health always reported the database as broken.

Found 2026-08-22. Live response, against a perfectly healthy Postgres:

    {"graph":"healthy",
     "database":"error: Textual SQL expression 'SELECT 1' should be explicitly
                 declared as text('SELECT 1')",
     "memory_service":"healthy","memory_count":1722}

routers/debug.py passed a bare string to session.execute(). SQLAlchemy 2.x
requires text(). The probe never touched the database at all — it raised on the
way in and reported the exception as a DB fault. Anything a human or Edward read
off this endpoint about DB health was wrong.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


ROUTER = pathlib.Path(__file__).resolve().parents[1] / "routers" / "debug.py"


class TestProbeUsesTextConstruct:
    def test_no_bare_string_passed_to_execute(self):
        """Walk the AST: no session.execute("...") with a raw string literal."""
        tree = ast.parse(ROUTER.read_text())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
                node.args[0].value, str
            ):
                offenders.append(node.args[0].value)
        assert not offenders, (
            f"session.execute() called with raw SQL string(s) {offenders!r}; "
            "SQLAlchemy 2.x needs text(...)"
        )

    def test_text_is_imported(self):
        src = ROUTER.read_text()
        assert "from sqlalchemy import" in src and "text" in src


class TestProbeBehaviour:
    @pytest.mark.asyncio
    async def test_healthy_db_reports_healthy(self, monkeypatch):
        """A DB that answers the probe must be reported healthy."""
        import routers.debug as dbg

        executed = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt):
                # SQLAlchemy 2.x rejects raw strings — mimic that contract so a
                # regression here fails loudly instead of silently passing.
                if isinstance(stmt, str):
                    raise TypeError(
                        "Textual SQL expression should be explicitly declared as text()"
                    )
                executed.append(stmt)
                return None

        # debug_health imports these inside the function body, so patch at source.
        monkeypatch.setattr(
            "services.database.async_session", lambda: FakeSession(), raising=True
        )
        monkeypatch.setattr(
            "services.graph.get_graph", _async_return(object()), raising=True
        )

        result = await dbg.debug_health()
        assert result["database"] == "healthy"
        assert executed, "probe should have executed a statement"

    @pytest.mark.asyncio
    async def test_broken_db_still_surfaces_the_error(self, monkeypatch):
        import routers.debug as dbg

        class BoomSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(
            "services.database.async_session", lambda: BoomSession(), raising=True
        )
        monkeypatch.setattr(
            "services.graph.get_graph", _async_return(object()), raising=True
        )

        result = await dbg.debug_health()
        assert "error" in result["database"]
        assert "connection refused" in result["database"]


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner
