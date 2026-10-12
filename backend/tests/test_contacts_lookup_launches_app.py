"""R/G TDD for the contact-resolution bug.

In prod, every heartbeat event lands with contact_name=NULL because
``lookup_phone`` hits AppleScript error -600 ("Application isn't
running"). Triage sees anonymous phone numbers instead of names → it
under-prioritizes familiar contacts and dismisses actionable messages.

Fix: prepend a ``launch`` command to the AppleScript so Contacts.app
gets loaded silently before the ``search for`` runs.

Since 2026-10-11 AppleScript is only the fallback: the AddressBook SQLite
stores come first, and the script runs only when no store exists or none
can be read (``tests/test_contacts_sqlite.py``). These pin the fallback.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock


def _no_sources():
    import services.contacts_service as cs

    return (patch.object(cs, "_addressbook_sqlite_paths", return_value=[]),
            patch.object(cs, "_addressbook_sqlite_path", return_value=None))


def test_lookup_phone_fallback_script_launches_contacts_app():
    """With no SQLite store, the AppleScript fallback must include a
    ``launch`` directive for Contacts.app — otherwise macOS returns
    error -600 if the app isn't running."""
    import services.contacts_service as cs

    captured_args = []

    def _fake_run(args, capture_output, text=False, timeout=None):
        captured_args.append(args)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        return mock_result

    paths, path = _no_sources()
    with paths, path:
        with patch.object(cs.subprocess, "run", side_effect=_fake_run):
            cs.lookup_phone("+15555550171")

    # The first call is `open -ga Contacts` (silent wake); subsequent
    # calls are the actual osascript invocation. Find the osascript one.
    osascript_calls = [
        a for a in captured_args
        if a and "osascript" in str(a[0])
    ]
    assert osascript_calls, "an osascript call should have been issued"
    script = " ".join(str(arg) for arg in osascript_calls[0])
    assert "launch" in script.lower(), (
        f"AppleScript must include 'launch' to wake Contacts.app; "
        f"script: {script[:500]}"
    )


def test_lookup_phone_fails_honestly_when_no_store_and_applescript_fails():
    """No SQLite store and Contacts.app answers an error: the lookup says
    so (``success: False``) rather than passing it off as no match."""
    import services.contacts_service as cs

    def _failing_run(args, capture_output, text=False, timeout=None):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "execution error: Contacts got an error: Application isn't running. (-600)"
        return mock_result

    paths, path = _no_sources()
    with paths, path:
        with patch.object(cs.subprocess, "run", side_effect=_failing_run):
            result = cs.lookup_phone("+15555550171")
    assert result["success"] is False
    assert "-600" in result["error"]


def test_lookup_contact_by_name_also_launches():
    """The other lookup path (search by name) has the same Contacts.app
    dependency and needs the same launch fix."""
    import services.contacts_service as cs

    captured_args = []

    def _fake_run(args, capture_output, text=False, timeout=None):
        captured_args.append(args)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        return mock_result

    paths, path = _no_sources()
    with paths, path:
        with patch.object(cs.subprocess, "run", side_effect=_fake_run):
            cs.lookup_contact("Rowan")

    osascript_calls = [
        a for a in captured_args
        if a and "osascript" in str(a[0])
    ]
    assert osascript_calls, "an osascript call should have been issued"
    script = " ".join(str(arg) for arg in osascript_calls[0])
    assert "launch" in script.lower()
