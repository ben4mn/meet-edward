"""Contacts reads the AddressBook SQLite first, across every source
(2026-10-11, the integrations audit's contacts track).

Before this, a name search went to Contacts.app over AppleScript — behind an
``is_available()`` precheck that ran ``open -ga Contacts`` and an osascript on
every lookup — and only a phone reverse-lookup read the SQLite stores, which
never matched an email. So an Apple-ID email sender stayed "unknown" and was
held, and every ``messages(who)`` woke Contacts.app on the Mac.

Now: every ``Sources/*/AddressBook-v22.abcddb`` plus the top-level file are
read read-only (``mode=ro``); a name matches when every word appears in the
first, middle, last or nick name or the organisation; ``lookup_handle`` matches
a phone on its last 10 digits or an email without case; a miss is a success
with no matches. AppleScript runs only when no SQLite source exists or every
source fails to open or has drifted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services import contacts_service as cs

pytestmark = pytest.mark.unit

SCHEMA = """
CREATE TABLE ZABCDRECORD (
    Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME VARCHAR, ZMIDDLENAME VARCHAR, ZLASTNAME VARCHAR,
    ZNICKNAME VARCHAR, ZORGANIZATION VARCHAR, ZLINKID VARCHAR
);
CREATE TABLE ZABCDPHONENUMBER (
    Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER VARCHAR, ZLASTFOURDIGITS VARCHAR
);
CREATE TABLE ZABCDEMAILADDRESS (
    Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS VARCHAR, ZADDRESSNORMALIZED VARCHAR
);
"""


def _store(path: Path, people: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    for pk, p in enumerate(people, start=1):
        conn.execute(
            "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZMIDDLENAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION, ZLINKID) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pk, p.get("first"), p.get("middle"), p.get("last"), p.get("nick"), p.get("org"), p.get("link")))
        for phone in p.get("phones", []):
            digits = "".join(ch for ch in phone if ch.isdigit())
            conn.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER, ZLASTFOURDIGITS) VALUES (?, ?, ?)",
                         (pk, phone, digits[-4:]))
        for email in p.get("emails", []):
            conn.execute("INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS, ZADDRESSNORMALIZED) VALUES (?, ?, ?)",
                         (pk, email, email.lower()))
    conn.commit()
    conn.close()
    return path


ROWAN = {"first": "Rowan", "last": "Hale", "nick": "Sunny", "phones": ["(555) 555-0171"],
           "emails": ["rowan.hale@example.com"]}
TOBIAS = {"first": "Tobias", "middle": "J", "last": "Rivera", "org": "Acme Plumbing",
          "phones": ["+1 201 555 0101"], "emails": []}
WORK_TOBIAS = {"first": "Tobias", "last": "Rivera", "phones": ["+12015550101"], "emails": ["tom@acme.example"]}
OTHER_TOBIAS = {"first": "Tobias", "last": "Rivera", "phones": ["+13035550199"], "emails": []}


@pytest.fixture
def addressbook(tmp_path, monkeypatch):
    """Two Sources and a top-level file, the way macOS lays them out."""
    base = tmp_path / "AddressBook"
    _store(base / "Sources" / "A" / "AddressBook-v22.abcddb", [ROWAN, TOBIAS])
    _store(base / "Sources" / "B" / "AddressBook-v22.abcddb", [WORK_TOBIAS, OTHER_TOBIAS])
    _store(base / "AddressBook-v22.abcddb", [])
    monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(base))
    return base


@pytest.fixture
def no_subprocess(monkeypatch):
    """Every subprocess is recorded and refused: the SQLite path runs none
    (no ``open -ga Contacts``, no osascript)."""
    calls: list = []

    def refuse(args, *a, **k):
        calls.append(args)
        raise AssertionError(f"no subprocess on the SQLite path: {args}")

    monkeypatch.setattr(cs.subprocess, "run", refuse)
    monkeypatch.setattr(cs.subprocess, "Popen", refuse)
    monkeypatch.setattr(cs, "is_available", lambda: (_ for _ in ()).throw(AssertionError("no precheck")))
    return calls


def _names(result: dict) -> list[str]:
    assert result["success"] is True, result
    return [m["name"] for m in result["matches"]]


class TestNameSearch:
    def test_every_word_must_appear_in_a_name_field_nickname_included(self, addressbook, no_subprocess):
        result = cs.lookup_contact("rowan sunny")
        assert _names(result) == ["Rowan Hale"]
        person = result["matches"][0]
        assert person["phones"] == ["(555) 555-0171"]
        assert person["emails"] == ["rowan.hale@example.com"]
        assert person["nickname"] == "Sunny"
        assert no_subprocess == []

    def test_the_organisation_and_middle_name_count(self, addressbook, no_subprocess):
        assert _names(cs.lookup_contact("acme")) == ["Tobias J Rivera"]
        assert cs.lookup_contact("acme")["matches"][0]["organization"] == "Acme Plumbing"
        assert _names(cs.lookup_contact("tobias j")) == ["Tobias J Rivera"]
        assert no_subprocess == []

    def test_case_and_order_do_not_matter(self, addressbook, no_subprocess):
        assert _names(cs.lookup_contact("Hale rowan")) == ["Rowan Hale"]

    def test_the_same_person_in_two_sources_is_one_match(self, addressbook, no_subprocess):
        """Tobias Rivera is in A (his mobile, middle initial) and B (the same
        mobile plus a work email): one person. A namesake with another number
        stays a second match, so a send to "Tobias Rivera" asks which."""
        matches = cs.lookup_contact("rivera")["matches"]
        by_phone = {tuple(m["phones"]): m for m in matches}
        assert len(matches) == 2, matches
        merged = next(m for m in matches if "tom@acme.example" in m["emails"])
        assert len(merged["phones"]) == 1, "one number, written two ways, is one number"
        assert ("+13035550199",) in by_phone

    def test_one_person_split_across_sources_with_no_shared_handle_is_one_match(self, tmp_path, monkeypatch,
                                                                                 no_subprocess):
        """Dana's iCloud card has her phone, her Google card her email: the
        same full name and nothing that disagrees is one person, as
        Contacts.app's unified card showed her — so send_message(to="Dana
        Whitfield") doesn't stop as ambiguous (review, 2026-10-11)."""
        base = tmp_path / "AB"
        _store(base / "Sources" / "C" / "AddressBook-v22.abcddb",
               [{"first": "Dana", "last": "Whitfield", "phones": ["(201) 555-0142"]}])
        _store(base / "Sources" / "D" / "AddressBook-v22.abcddb",
               [{"first": "dana", "last": "WHITFIELD", "emails": ["dana@example.org"]}])
        monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(base))
        [dana] = cs.lookup_contact("dana whitfield")["matches"]
        assert dana["phones"] == ["(201) 555-0142"] and dana["emails"] == ["dana@example.org"]

    def test_namesakes_whose_handles_disagree_stay_apart(self, tmp_path, monkeypatch, no_subprocess):
        base = tmp_path / "AB"
        _store(base / "Sources" / "C" / "AddressBook-v22.abcddb",
               [{"first": "Sam", "last": "Lee", "phones": ["+12015550111"]},
                {"first": "Pat", "last": "Kim", "emails": ["pat@one.example"]}])
        _store(base / "Sources" / "D" / "AddressBook-v22.abcddb",
               [{"first": "Sam", "last": "Lee", "phones": ["+13035550112"], "emails": ["sam@x.example"]},
                {"first": "Pat", "last": "Kim", "emails": ["pat@two.example"]}])
        monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(base))
        assert len(cs.lookup_contact("sam lee")["matches"]) == 2, "different numbers: two people"
        assert len(cs.lookup_contact("pat kim")["matches"]) == 2, "different addresses: two people"

    def test_cards_contacts_app_linked_are_one_person(self, tmp_path, monkeypatch, no_subprocess):
        """Contacts.app's own link between cards (``ZLINKID``) makes one
        person even when the names and handles differ."""
        base = tmp_path / "AB"
        _store(base / "Sources" / "C" / "AddressBook-v22.abcddb",
               [{"first": "Jo", "last": "Park", "phones": ["+12015550113"], "link": "LINK-1"}])
        _store(base / "Sources" / "D" / "AddressBook-v22.abcddb",
               [{"first": "Joanna", "last": "Park", "emails": ["jo@park.example"], "link": "LINK-1"},
                {"first": "Min", "last": "Park", "phones": ["+12015550114"], "link": "LINK-2"}])
        monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(base))
        matches = cs.lookup_contact("park")["matches"]
        assert len(matches) == 2, matches
        jo = next(m for m in matches if "+12015550113" in m["phones"])
        assert jo["emails"] == ["jo@park.example"]
        assert all(set(m) <= {"name", "phones", "emails", "nickname", "organization"} for m in matches)

    def test_a_miss_is_success_with_no_matches(self, addressbook, no_subprocess):
        assert cs.lookup_contact("nobody by this name") == {"success": True, "matches": []}
        assert no_subprocess == []


class TestHandles:
    def test_a_phone_matches_on_its_last_ten_digits(self, addressbook, no_subprocess):
        for spelling in ("+15555550171", "555-555-0171", "1 (555) 555 0171"):
            assert _names(cs.lookup_handle(spelling)) == ["Rowan Hale"], spelling
        assert no_subprocess == []

    def test_an_email_matches_without_case(self, addressbook, no_subprocess):
        result = cs.lookup_handle("rowan.hale@example.com")
        assert _names(result) == ["Rowan Hale"]
        assert result["matches"][0]["phones"] == ["(555) 555-0171"]

    def test_a_shared_last_four_is_not_a_match(self, addressbook, no_subprocess):
        # The same last four (and last seven) as Rowan's number, another area code.
        assert cs.lookup_handle("+19995550171") == {"success": True, "matches": []}

    def test_lookup_contact_hands_a_phone_or_an_email_to_lookup_handle(self, addressbook, no_subprocess):
        assert _names(cs.lookup_contact("tom@acme.example")) == ["Tobias Rivera"]
        assert _names(cs.lookup_contact("(303) 555-0199")) == ["Tobias Rivera"]

    def test_lookup_phone_is_lookup_handle(self, addressbook, no_subprocess):
        result = cs.lookup_phone("+15555550171")
        assert _names(result) == ["Rowan Hale"]
        assert result["matches"][0]["emails"] == ["rowan.hale@example.com"]

    def test_a_handle_miss_is_success_with_no_matches(self, addressbook, no_subprocess):
        assert cs.lookup_handle("stranger@example.com") == {"success": True, "matches": []}
        assert cs.lookup_handle("+12015550198") == {"success": True, "matches": []}
        assert no_subprocess == []


class TestAppleScriptOnlyWithoutSources:
    @pytest.fixture
    def fake_osascript(self, monkeypatch):
        calls: list = []

        class Done:
            returncode, stdout, stderr = 0, "", ""

        def run(args, *a, **k):
            calls.append(list(args))
            return Done()

        monkeypatch.setattr(cs.subprocess, "run", run)
        return calls

    def test_no_source_falls_back_to_applescript(self, tmp_path, monkeypatch, fake_osascript):
        monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(tmp_path / "empty"))
        assert cs.lookup_contact("rowan") == {"success": True, "matches": []}
        assert any(c and c[0] == "osascript" for c in fake_osascript)

    def test_a_drifted_store_falls_back_to_applescript(self, tmp_path, monkeypatch, fake_osascript):
        base = tmp_path / "AddressBook"
        (base / "Sources" / "A").mkdir(parents=True)
        conn = sqlite3.connect(str(base / "Sources" / "A" / "AddressBook-v22.abcddb"))
        conn.execute("CREATE TABLE SOMETHING_ELSE (x)")
        conn.commit()
        conn.close()
        monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(base))
        cs.lookup_handle("+15555550171")
        assert any(c and c[0] == "osascript" for c in fake_osascript)

    def test_with_a_source_a_miss_never_reaches_applescript(self, addressbook, fake_osascript):
        cs.lookup_contact("nobody")
        cs.lookup_handle("+12015550198")
        assert fake_osascript == []


class TestStatusAndTools:
    def test_status_counts_the_readable_address_books(self, addressbook, no_subprocess):
        st = cs.get_status()
        assert st["status"] == "connected"
        assert st["status_message"] == "3 address books (AddressBook database)"
        assert no_subprocess == []

    def test_status_says_when_nothing_is_readable(self, tmp_path, monkeypatch, no_subprocess):
        monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(tmp_path / "missing"))
        st = cs.get_status()
        assert st["status"] == "error"
        assert st["status_message"] == "the AddressBook database isn't readable (Full Disk Access?)"
        assert no_subprocess == []

    def test_the_legacy_tools_have_no_precheck(self, addressbook, no_subprocess):
        """``graph.tools.lookup_contact``/``lookup_phone`` (what ``messages(who)``
        calls) used to run ``is_available()`` — ``open -ga Contacts`` and an
        osascript — before every lookup."""
        from services.graph import tools as legacy

        out = legacy.lookup_contact.invoke({"query": "rowan"})
        assert "Rowan Hale" in out and "(555) 555-0171" in out
        out = legacy.lookup_phone.invoke({"phone_number": "+15555550171"})
        assert "Rowan Hale" in out
        out = legacy.lookup_contact.invoke({"query": "rowan.hale@example.com"})
        assert "Rowan Hale" in out
        assert no_subprocess == []
