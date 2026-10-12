"""An Apple-ID email sender gets a name (2026-10-11, the integrations audit).

Friends who iMessage from an Apple ID email arrive in chat.db with the email
as their handle. The listener named senders through ``lookup_phone``, which
read only phone numbers, and ``contact_identity_service`` looked up phones
only, so a friend in Ellis's contacts stayed nameless — and a nameless sender's
ping is held as "not in Ellis's contacts". Both now ask ``lookup_handle``,
which matches an email without case.
"""

from __future__ import annotations

import sqlite3

import pytest

from services.heartbeat import listener_imessage as listener


def _addressbook(base, people):
    path = base / "Sources" / "A" / "AddressBook-v22.abcddb"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME, ZMIDDLENAME, ZLASTNAME, ZNICKNAME,
                                  ZORGANIZATION);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER, ZFULLNUMBER, ZLASTFOURDIGITS);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER, ZADDRESS, ZADDRESSNORMALIZED);
    """)
    for pk, (first, last, phone, email) in enumerate(people, start=1):
        conn.execute("INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME) VALUES (?, ?, ?)", (pk, first, last))
        if phone:
            conn.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER, ZLASTFOURDIGITS) VALUES (?, ?, ?)",
                         (pk, phone, phone[-4:]))
        if email:
            conn.execute("INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS, ZADDRESSNORMALIZED) VALUES (?, ?, ?)",
                         (pk, email, email.lower()))
    conn.commit()
    conn.close()


@pytest.fixture
def addressbook(tmp_path, monkeypatch):
    base = tmp_path / "AddressBook"
    _addressbook(base, [("Jordan", "Vale", None, "jordan.vale@example.com"),
                        ("Avery", "Chen", "+18065550123", None)])
    monkeypatch.setenv("EDWARD_ADDRESSBOOK_DIR", str(base))
    monkeypatch.setattr(listener, "_contact_name_cache", {})
    return base


async def test_an_apple_id_email_sender_gets_a_name(addressbook):
    assert await listener._resolve_contact_name("jordan.vale@example.com") == "Jordan Vale"
    assert await listener._resolve_contact_name("+18065550123") == "Avery Chen"
    assert await listener._resolve_contact_name("stranger@example.com") is None


async def test_the_listener_asks_lookup_handle_and_caches_by_handle(monkeypatch):
    from services import contacts_service

    monkeypatch.setattr(listener, "_contact_name_cache", {})
    asked: list = []

    def lookup_handle(handle):
        asked.append(handle)
        return {"success": True, "matches": [{"name": "Jordan Vale", "phones": [], "emails": [handle]}]}

    def lookup_phone(_handle):
        raise AssertionError("a handle is named through lookup_handle")

    monkeypatch.setattr(contacts_service, "lookup_handle", lookup_handle)
    monkeypatch.setattr(contacts_service, "lookup_phone", lookup_phone)
    assert await listener._resolve_contact_name("jordan.vale@example.com") == "Jordan Vale"
    assert await listener._resolve_contact_name("jordan.vale@example.com") == "Jordan Vale"
    assert asked == ["jordan.vale@example.com"]


@pytest.mark.integration
async def test_the_identity_of_an_email_sender_is_an_address_book_match(clean_db, addressbook):
    from services.contact_identity_service import ADDRESS_BOOK_MATCH, resolve_contact_identity

    identity = await resolve_contact_identity(sender="jordan.vale@example.com", source="imessage")
    assert identity.display_label == "Jordan Vale"
    assert identity.provenance == ADDRESS_BOOK_MATCH

    phone = await resolve_contact_identity(sender="+18065550123", source="imessage")
    assert phone.display_label == "Avery Chen" and phone.provenance == ADDRESS_BOOK_MATCH
