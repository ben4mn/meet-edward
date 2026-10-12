"""R/G TDD for the SQLite-based contact lookup that bypasses AppleScript.

AppleScript to Contacts.app is unreliable in this macOS version —
errors -600, -1708, -1712 all hit during real lookups. Reading
``~/Library/Application Support/AddressBook/.../AddressBook-v22.abcddb``
directly via sqlite3 is deterministic, fast, and survives system idle.

The lookup matches by last-10-digit normalization since the address
book stores numbers in various formats (``+15555550171``,
``(555) 555-0171``, etc.).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _make_fixture_addressbook(path: Path, *, name: str, phone: str) -> None:
    """Build a minimal AddressBook SQLite mirroring the real schema."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE ZABCDRECORD (
            Z_PK INTEGER PRIMARY KEY,
            ZFIRSTNAME VARCHAR,
            ZLASTNAME VARCHAR,
            ZORGANIZATION VARCHAR
        );
        CREATE TABLE ZABCDPHONENUMBER (
            Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER,
            ZFULLNUMBER VARCHAR,
            ZLASTFOURDIGITS VARCHAR
        );
    """)
    first, _, last = name.partition(" ")
    conn.execute(
        "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME) VALUES (1, ?, ?)",
        (first, last),
    )
    conn.execute(
        "INSERT INTO ZABCDPHONENUMBER (Z_PK, ZOWNER, ZFULLNUMBER, ZLASTFOURDIGITS) "
        "VALUES (1, 1, ?, ?)",
        (phone, phone[-4:]),
    )
    conn.commit()
    conn.close()


def test_sqlite_lookup_returns_name_for_known_phone(tmp_path, monkeypatch):
    from services import contacts_service as cs

    fake_db = tmp_path / "AddressBook-v22.abcddb"
    _make_fixture_addressbook(fake_db, name="Rowan Hale", phone="+15555550171")
    monkeypatch.setattr(cs, "_addressbook_sqlite_path", lambda: str(fake_db))
    monkeypatch.setattr(cs, "_addressbook_sqlite_paths", lambda: [str(fake_db)])

    name = cs._lookup_phone_via_sqlite("+15555550171")
    assert name == "Rowan Hale"


def test_sqlite_lookup_handles_alternate_formats(tmp_path, monkeypatch):
    """Address book stores "(555) 555-0171"; chat.db gives "+15555550171".
    Last-10-digit normalization must bridge them."""
    from services import contacts_service as cs

    fake_db = tmp_path / "AddressBook-v22.abcddb"
    _make_fixture_addressbook(
        fake_db, name="Rowan Hale", phone="(555) 555-0171"
    )
    monkeypatch.setattr(cs, "_addressbook_sqlite_path", lambda: str(fake_db))
    monkeypatch.setattr(cs, "_addressbook_sqlite_paths", lambda: [str(fake_db)])

    name = cs._lookup_phone_via_sqlite("+15555550171")
    assert name == "Rowan Hale"


def test_sqlite_lookup_returns_none_for_unknown(tmp_path, monkeypatch):
    from services import contacts_service as cs

    fake_db = tmp_path / "AddressBook-v22.abcddb"
    _make_fixture_addressbook(fake_db, name="Rowan Hale", phone="+15555550171")
    monkeypatch.setattr(cs, "_addressbook_sqlite_path", lambda: str(fake_db))
    monkeypatch.setattr(cs, "_addressbook_sqlite_paths", lambda: [str(fake_db)])

    assert cs._lookup_phone_via_sqlite("+19998887777") is None


def test_sqlite_lookup_returns_none_when_db_missing(tmp_path, monkeypatch):
    from services import contacts_service as cs

    monkeypatch.setattr(
        cs, "_addressbook_sqlite_path", lambda: str(tmp_path / "nope.db")
    )
    # Also blank out the multi-path scanner so real prod DBs don't satisfy
    # the lookup during test runs.
    monkeypatch.setattr(cs, "_addressbook_sqlite_paths", lambda: [])
    assert cs._lookup_phone_via_sqlite("+15555550171") is None


def test_lookup_phone_uses_sqlite_path_first(tmp_path, monkeypatch):
    """Public lookup_phone should hit the SQLite path before AppleScript."""
    from services import contacts_service as cs

    fake_db = tmp_path / "AddressBook-v22.abcddb"
    _make_fixture_addressbook(fake_db, name="Rowan Hale", phone="+15555550171")
    monkeypatch.setattr(cs, "_addressbook_sqlite_path", lambda: str(fake_db))
    monkeypatch.setattr(cs, "_addressbook_sqlite_paths", lambda: [str(fake_db)])

    # Sabotage the AppleScript path so we know SQLite is the one returning data
    def _crash_subprocess(*args, **kwargs):
        raise AssertionError("AppleScript should not be called when SQLite succeeds")
    monkeypatch.setattr(cs.subprocess, "run", _crash_subprocess)
    monkeypatch.setattr(cs, "is_available", lambda: True)

    result = cs.lookup_phone("+15555550171")
    assert result["success"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["name"] == "Rowan Hale"
