"""The EventKit helper, from Python (2026-10-11).

``edward-eventkit`` is the one write path to Ellis's calendar and Reminders
(``schedule``'s event_* / reminder_* actions, when Ellis asks). The wrapper
hands it one JSON object on stdin — never argv, where ``ps`` would show an
event's title and notes — reads one JSON line back, and turns its exit code
into an honest exception: 4 is macOS's access (with the System Settings
sentence Ellis needs), 3 a failure in the helper's own words, anything
unreadable "unavailable". No test runs the real helper here: ``subprocess``
is stubbed, and conftest points ``EDWARD_EVENTKIT`` at a file that does not
exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import types

import pytest

from services import eventkit_service as ek

pytestmark = pytest.mark.unit


@pytest.fixture
def helper(tmp_path, monkeypatch):
    """A fake installed helper and a recorder for what subprocess.run got."""
    binary = tmp_path / "edward-eventkit"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("EDWARD_EVENTKIT", str(binary))
    calls = []
    replies = []

    def fake_run(argv, input=None, capture_output=False, timeout=None, **kw):
        calls.append({"argv": list(argv), "input": input, "timeout": timeout})
        code, out = replies.pop(0) if replies else (0, {"ok": True})
        if isinstance(out, BaseException):
            raise out
        stdout = out if isinstance(out, bytes) else (json.dumps(out) + "\n").encode()
        return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")

    monkeypatch.setattr(ek.subprocess, "run", fake_run)
    ek._access_cache.clear()
    return types.SimpleNamespace(binary=binary, calls=calls, replies=replies)


class TestRun:
    def test_the_payload_goes_on_stdin_never_argv(self, helper):
        helper.replies.append((0, {"ok": True, "event": {"id": "E1"}}))
        out = ek._run("event_add", {"title": "Dentist — private", "notes": "root canal"})
        assert out["event"]["id"] == "E1"
        (call,) = helper.calls
        assert call["argv"] == [str(helper.binary), "event_add"]
        assert json.loads(call["input"]) == {"title": "Dentist — private", "notes": "root canal"}
        assert "Dentist" not in " ".join(call["argv"])
        assert call["timeout"] == ek.RUN_TIMEOUT_S

    def test_the_last_line_is_the_answer(self, helper):
        helper.replies.append((0, b'some framework noise\n{"ok": true, "items": []}\n'))
        assert ek._run("calendars", {}) == {"ok": True, "items": []}

    def test_access_is_denied_with_the_settings_sentence(self, helper):
        helper.replies.append((4, {"ok": False, "code": "access", "entity": "reminder", "status": "denied",
                                   "error": "no"}))
        with pytest.raises(ek.EventKitDenied) as exc:
            ek._run("reminder_add", {"title": "x"})
        assert exc.value.entity == "reminder" and exc.value.status == "denied"
        assert "System Settings → Privacy & Security → Reminders → turn on python3.12" in str(exc.value)

    def test_calendar_access_names_full_access(self, helper):
        # Measured 2026-10-11 under the backend's python3.12: Calendar is
        # write-only (adding to the default calendar works; moving, changing
        # and deleting need Full Access).
        helper.replies.append((4, {"ok": False, "code": "access", "entity": "event", "status": "writeOnly"}))
        with pytest.raises(ek.EventKitDenied) as exc:
            ek._run("event_update", {"id": "x"})
        assert "Calendars" in str(exc.value) and "Full Access" in str(exc.value)
        assert "write-only" in str(exc.value)

    def test_never_asked_says_how_to_ask_from_the_minis_screen(self, helper):
        # Measured 2026-10-11: Reminders is notDetermined for python3.12, so
        # it isn't in System Settings' list until macOS has asked once.
        helper.replies.append((4, {"ok": False, "code": "access", "entity": "reminder", "status": "notDetermined"}))
        with pytest.raises(ek.EventKitDenied) as exc:
            ek._run("reminder_add", {"title": "x"})
        assert "eventkit_request_access.sh" in str(exc.value) and "Allow" in str(exc.value)

    def test_a_failure_is_the_helpers_own_sentence(self, helper):
        helper.replies.append((3, {"ok": False, "code": "not_found", "error": "I couldn't find that event."}))
        with pytest.raises(ek.EventKitFailed) as exc:
            ek._run("event_delete", {"id": "x"})
        assert exc.value.code == "not_found" and str(exc.value) == "I couldn't find that event."

    def test_bad_input_is_a_failure_too(self, helper):
        helper.replies.append((2, {"ok": False, "code": "bad_input", "error": "event_add needs a title"}))
        with pytest.raises(ek.EventKitFailed, match="needs a title"):
            ek._run("event_add", {})

    def test_a_missing_helper_is_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EDWARD_EVENTKIT", str(tmp_path / "nope"))
        with pytest.raises(ek.EventKitUnavailable, match="isn't installed"):
            ek._run("event_add", {"title": "x"})

    def test_a_timeout_leaves_the_outcome_unknown(self, helper):
        """Killed at 20 s, the helper may already have saved: "I don't know",
        never "it didn't happen" (review 2026-10-11)."""
        helper.replies.append((0, subprocess.TimeoutExpired(["x"], 20)))
        with pytest.raises(ek.EventKitOutcomeUnknown, match="longer than") as exc:
            ek._run("event_add", {"title": "x"})
        assert not isinstance(exc.value, ek.EventKitUnavailable)

    def test_no_answer_leaves_the_outcome_unknown(self, helper):
        helper.replies.append((139, b"Segmentation fault\n"))
        with pytest.raises(ek.EventKitOutcomeUnknown, match="exit 139"):
            ek._run("event_add", {"title": "x"})
        helper.replies.append((0, b""))
        with pytest.raises(ek.EventKitOutcomeUnknown, match="no answer"):
            ek._run("reminder_add", {"title": "x"})

    def test_a_helper_that_never_started_is_unavailable(self, helper):
        helper.replies.append((0, OSError("exec format error")))
        with pytest.raises(ek.EventKitUnavailable, match="wouldn't start") as exc:
            ek._run("event_add", {"title": "x"})
        assert not isinstance(exc.value, ek.EventKitOutcomeUnknown)

    def test_the_path_is_read_per_call(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EDWARD_EVENTKIT", str(tmp_path / "a"))
        assert ek.binary_path() == tmp_path / "a"
        monkeypatch.delenv("EDWARD_EVENTKIT")
        assert ek.binary_path() == ek.Path(os.path.expanduser(ek.DEFAULT_PATH))


class TestTypedHelpers:
    async def test_each_names_its_command(self, monkeypatch):
        seen = []

        def fake(command, payload=None, timeout_s=ek.RUN_TIMEOUT_S):
            seen.append((command, payload))
            return {"ok": True}

        monkeypatch.setattr(ek, "_run", fake)
        await ek.add_event({"title": "a"})
        await ek.update_event({"id": "b"})
        await ek.delete_event({"id": "c"})
        await ek.add_reminder({"title": "d"})
        await ek.complete_reminder({"ids": ["e"]})
        await ek.delete_reminder({"ids": ["f"]})
        assert [c for c, _ in seen] == ["event_add", "event_update", "event_delete", "reminder_add",
                                        "reminder_complete", "reminder_delete"]
        assert seen[0][1] == {"title": "a"}


class TestAccessStatus:
    def test_not_installed_never_runs_anything(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EDWARD_EVENTKIT", str(tmp_path / "nope"))
        ek._access_cache.clear()

        def boom(*a, **k):
            raise AssertionError("must not run")

        monkeypatch.setattr(ek.subprocess, "run", boom)
        st = ek.access_status()
        assert st["installed"] is False

    def test_cached_and_never_raises(self, helper):
        helper.replies.append((0, {"ok": True, "calendar": "fullAccess", "reminders": "denied"}))
        st = ek.access_status()
        assert st == {"installed": True, "calendar": "fullAccess", "reminders": "denied"}
        assert ek.access_status() == st
        assert len(helper.calls) == 1 and helper.calls[0]["argv"][-1] == "access"
        ek._access_cache.clear()
        helper.replies.append((0, subprocess.TimeoutExpired(["x"], 5)))
        st = ek.access_status()
        assert st["installed"] is True and "longer than" in st["error"]


def test_the_health_report_has_the_helper_and_reminders(monkeypatch, tmp_path):
    from services import health_report_service as hr

    monkeypatch.setenv("EDWARD_EVENTKIT", str(tmp_path / "nope"))
    ek._access_cache.clear()
    sec = hr.calendar_section()
    assert sec["eventkit"]["installed"] is False
    assert sec["reminders"]["source"] in ("unread", "sqlite", "unavailable")
    assert "source" in sec  # the calendar's own line is still there


@pytest.mark.integration
async def test_the_health_report_reads_the_calendar_off_the_event_loop(clean_db, monkeypatch):
    """calendar_section runs the helper's ``access`` (up to 5 s of
    subprocess.run) and reads two databases: never on the event loop that
    serves every request (review 2026-10-11)."""
    import threading

    from services import health_report_service as hr

    seen = []

    def calendar_section():
        seen.append(threading.current_thread() is threading.main_thread())
        return {"source": "unread"}

    monkeypatch.setattr(hr, "calendar_section", calendar_section)
    rep = await hr.build_report(days=1)
    assert rep["calendar"] == {"source": "unread"}
    assert seen == [False]


def test_tests_never_reach_the_real_helper():
    path = os.environ.get("EDWARD_EVENTKIT") or ""
    assert "edward-tests-no-eventkit" in path and not os.path.exists(path)


def test_even_an_installed_helper_is_never_run_unless_a_test_stubs_it(tmp_path, monkeypatch):
    """The env var is one guard; once build_eventkit.sh has run on the mini
    the default path exists, so a test that clears it and forgets a stub
    would write to Ellis's real calendar. Conftest refuses ``_run`` itself
    unless the test patched it or ``subprocess.run`` (review 2026-10-11)."""
    binary = tmp_path / "edward-eventkit"
    marker = tmp_path / "ran"
    binary.write_text(f"#!/bin/sh\ntouch {marker}\necho '{{\"ok\": true}}'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("EDWARD_EVENTKIT", str(binary))
    with pytest.raises(RuntimeError, match="tests never reach EventKit"):
        ek._run("event_add", {"title": "x"})
    assert not marker.exists()
