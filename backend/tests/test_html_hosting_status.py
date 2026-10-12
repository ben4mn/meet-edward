"""html_hosting must not report 'connected' for a key it never tried.

Regression origin (2026-08-25): get_status() returned
{"status": "connected", "status_message": "API key configured"} whenever the
env var was non-empty. A revoked or typo'd key would still show green in the
settings UI, so there was no way to tell a credential problem from anything
else. (The key turned out to be valid; the UI simply could not have told us.)
"""

import pytest

from services import html_hosting_service as svc

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_verification():
    svc.reset_verification()
    yield
    svc.reset_verification()


@pytest.fixture
def host(monkeypatch):
    """A configured page host (there is no default host: HTML_HOSTING_URL
    must be set)."""
    monkeypatch.setattr(svc, "HTML_HOSTING_URL", "https://pages.example.com")
    return monkeypatch


class TestInterpretProbe:
    def test_200_is_healthy(self):
        ok, msg = svc.interpret_probe(200, None)
        assert ok is True
        assert "reachable" in msg.lower() or "ok" in msg.lower()

    def test_401_names_a_missing_key(self):
        ok, msg = svc.interpret_probe(401, None)
        assert ok is False
        assert "key" in msg.lower()

    def test_403_names_an_invalid_key(self):
        ok, msg = svc.interpret_probe(403, None)
        assert ok is False
        assert "invalid" in msg.lower()

    def test_transport_error_is_reported_as_unreachable(self):
        ok, msg = svc.interpret_probe(None, "connection refused")
        assert ok is False
        assert "connection refused" in msg

    def test_unexpected_status_is_not_treated_as_healthy(self):
        ok, _ = svc.interpret_probe(500, None)
        assert ok is False


class TestGetStatus:
    def test_missing_key_is_an_error(self, host):
        host.setattr(svc, "HTML_HOSTING_API_KEY", "")
        assert svc.get_status()["status"] == "error"

    def test_missing_host_is_an_error_even_with_a_verified_key(self, monkeypatch):
        monkeypatch.setattr(svc, "HTML_HOSTING_API_KEY", "abc123")
        monkeypatch.setattr(svc, "HTML_HOSTING_URL", "")
        svc.record_verification(True, "API reachable")
        status = svc.get_status()
        assert status["status"] == "error"
        assert "HTML_HOSTING_URL" in status["status_message"]
        assert svc.is_configured() is False

    def test_unverified_key_is_not_reported_as_connected(self, host):
        host.setattr(svc, "HTML_HOSTING_API_KEY", "abc123")
        status = svc.get_status()
        assert status["status"] != "connected", (
            "an unverified key must not show as connected"
        )
        assert "verif" in status["status_message"].lower()

    def test_successful_verification_reports_connected(self, host):
        host.setattr(svc, "HTML_HOSTING_API_KEY", "abc123")
        svc.record_verification(True, "API reachable")
        assert svc.get_status()["status"] == "connected"

    def test_failed_verification_surfaces_the_reason(self, host):
        host.setattr(svc, "HTML_HOSTING_API_KEY", "abc123")
        svc.record_verification(False, "Invalid API key")
        status = svc.get_status()
        assert status["status"] == "error"
        assert "Invalid API key" in status["status_message"]


class TestStatusIsRenderable:
    """get_all_skills() does SkillStatus(status_info["status"]), so any value
    outside the enum raises ValueError and takes down the whole skills
    endpoint — not just this one row."""

    @pytest.mark.parametrize(
        "setup",
        [
            "missing_key",
            "missing_host",
            "unverified",
            "verified_ok",
            "verified_bad",
        ],
    )
    def test_every_status_is_a_valid_skill_status(self, setup, monkeypatch):
        from models.schemas import SkillStatus

        monkeypatch.setattr(svc, "HTML_HOSTING_URL", "" if setup == "missing_host" else "https://pages.example.com")
        if setup == "missing_key":
            monkeypatch.setattr(svc, "HTML_HOSTING_API_KEY", "")
        else:
            monkeypatch.setattr(svc, "HTML_HOSTING_API_KEY", "abc123")
        if setup == "verified_ok":
            svc.record_verification(True, "ok")
        if setup == "verified_bad":
            svc.record_verification(False, "nope")

        SkillStatus(svc.get_status()["status"])  # must not raise
