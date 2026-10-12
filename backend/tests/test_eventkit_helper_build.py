"""The EventKit helper builds and proves itself without touching Ellis's data.

``backend/scripts/build_eventkit.sh`` compiles ``backend/tools/eventkit/
edward_eventkit.swift`` with the command-line tools' swiftc, embeds the
Info.plist (EventKit refuses a process whose main bundle lacks the usage
strings), signs it ad hoc and runs its ``selftest`` before moving it into
place. This test builds into a temp directory and runs only ``selftest``
and a bad command: never ``access``, ``request`` or a write — those would
reach EventKit, and a ``request`` could pop a consent dialog on the Mac.
"""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "build_eventkit.sh"
PLIST = BACKEND / "tools" / "eventkit" / "Info.plist"
SOURCE = BACKEND / "tools" / "eventkit" / "edward_eventkit.swift"
HAS_SWIFTC = shutil.which("swiftc") is not None and shutil.which("codesign") is not None

USAGE_KEYS = ("NSCalendarsFullAccessUsageDescription", "NSRemindersFullAccessUsageDescription",
              "NSCalendarsUsageDescription", "NSRemindersUsageDescription")


def test_the_plist_carries_every_usage_string():
    info = plistlib.loads(PLIST.read_bytes())
    assert info["CFBundleIdentifier"] == "com.meet-edward.eventkit"
    for key in USAGE_KEYS:
        assert info[key] == "Edward adds and changes events and reminders when you ask.", key


def test_the_source_reads_its_input_from_stdin_and_never_requests_by_itself():
    src = SOURCE.read_text()
    assert "standardInput" in src
    # requestFullAccess* appears only under the manual `request` command.
    request_lines = [n for n, line in enumerate(src.splitlines()) if "requestFullAccessTo" in line]
    assert request_lines, "the manual request command exists"
    assert 'case "request"' in src


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not HAS_SWIFTC:
        pytest.skip("needs swiftc and codesign (Xcode command-line tools)")
    out = tmp_path_factory.mktemp("eventkit") / "edward-eventkit"
    r = subprocess.run(["bash", str(SCRIPT), str(out)], capture_output=True, timeout=600)
    if r.returncode != 0:
        pytest.fail((r.stdout + r.stderr).decode()[-3000:])
    assert b"built" in r.stdout
    return out


def _call(binary: Path, *args: str, stdin: bytes = b"") -> tuple:
    r = subprocess.run([str(binary), *args], input=stdin, capture_output=True, timeout=60)
    line = r.stdout.decode().strip().splitlines()[-1]
    return r.returncode, json.loads(line)


def test_selftest_proves_the_plist_is_embedded(built):
    code, out = _call(built, "selftest")
    assert code == 0 and out["ok"] is True, out
    assert out["checks"]["plist"] is True
    assert out["bundle_id"] == "com.meet-edward.eventkit"
    assert isinstance(out["attendees_api"], bool)


def test_selftest_counts_invitees_only_from_the_saved_event(built):
    """The pure rule the helper applies after a save: an invitee a fresh
    read of the event still has is saved, one it lost failed, and with no
    read-back at all every one is unverified (review 2026-10-11)."""
    code, out = _call(built, "selftest")
    assert code == 0 and out["checks"].get("invites") is True, out


def test_invitees_are_read_back_through_a_fresh_store():
    """confirmInvites used to read ``event.attendees`` off the very object
    invite() had just set, so nothing could ever count as dropped and the
    tool said "Invited" whenever the private path ran. The read-back must
    come from a new EKEventStore, in both writes that invite."""
    src = SOURCE.read_text()

    def body(name: str) -> str:
        return src.split(f"func {name}", 1)[1].split("\nfunc ", 1)[0]

    assert "EKEventStore()" in body("persistedAttendees")
    assert "persistedAttendees(" in body("inviteReply")
    assert "inviteReply(" in body("eventAdd") and "inviteReply(" in body("eventUpdate")
    assert '"attendees_invited"' not in src


def test_a_repeating_event_without_an_occurrence_is_asked_about(built):
    """With no occurrence to go on, EventKit hands back a repeating event's
    master, and changing "this" one would change its first occurrence —
    maybe years back. The helper asks which instead (review 2026-10-11)."""
    code, out = _call(built, "selftest")
    assert code == 0 and out["checks"].get("occurrence") is True, out
    src = SOURCE.read_text()
    find = src.split("func findEvent", 1)[1].split("\nfunc ", 1)[0]
    assert "needsOccurrence(" in find and '"ambiguous"' in find


def test_a_bad_command_is_a_usage_error(built):
    code, out = _call(built, "frobnicate")
    assert code == 2 and out["ok"] is False and out["code"] == "usage"
    code, out = _call(built)
    assert code == 2 and out["ok"] is False


def test_bad_json_is_bad_input_before_eventkit_is_touched(built):
    code, out = _call(built, "event_add", stdin=b"not json")
    assert code == 2 and out["code"] == "bad_input"


def test_the_access_request_runs_under_the_backends_python(tmp_path):
    """macOS grants EventKit access to the responsible process. Run from
    Terminal, ``request`` would grant Terminal; the backend's python3.12 is
    what spawns the helper, so the script asks through a one-shot launchd
    job under that python. ``--dry-run`` prints the job and runs nothing."""
    script = BACKEND / "scripts" / "eventkit_request_access.sh"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "EDWARD_PYTHON": "/x/venv/bin/python",
           "EDWARD_EVENTKIT": "/y/edward-eventkit"}
    r = subprocess.run(["bash", str(script), "--dry-run"], capture_output=True, timeout=30, env=env)
    out = r.stdout.decode()
    assert r.returncode == 0, r.stderr.decode()
    assert "<string>/x/venv/bin/python</string>" in out
    assert "/y/edward-eventkit" in out and "events" in out and "reminders" in out
    assert "launchctl bootstrap gui/" in out and "launchctl bootout gui/" in out


def test_a_failed_build_never_replaces_a_working_helper(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("needs bash")
    out = tmp_path / "edward-eventkit"
    out.write_text("previous helper")
    fake_src = tmp_path / "src"
    (fake_src / "tools" / "eventkit").mkdir(parents=True)
    (fake_src / "scripts").mkdir()
    (fake_src / "tools" / "eventkit" / "edward_eventkit.swift").write_text("this is not swift")
    shutil.copy(PLIST, fake_src / "tools" / "eventkit" / "Info.plist")
    shutil.copy(SCRIPT, fake_src / "scripts" / "build_eventkit.sh")
    r = subprocess.run(["bash", str(fake_src / "scripts" / "build_eventkit.sh"), str(out)], capture_output=True,
                       timeout=300)
    assert r.returncode != 0
    assert out.read_text() == "previous helper"
