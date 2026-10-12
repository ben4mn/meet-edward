import json

import pytest
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _disable_contacts_lookup(monkeypatch):
    from services import contacts_service

    for name in ("lookup_phone", "lookup_handle"):
        monkeypatch.setattr(
            contacts_service,
            name,
            lambda _handle: {"success": True, "matches": []},
        )


@pytest.mark.asyncio
async def test_phone_only_inbound_uses_historical_name_for_push_subject(clean_db):
    from services.contact_identity_service import resolve_contact_identity
    from services.database import HeartbeatEventModel, async_session
    from services.contact_identity_service import group_events_by_sender as _group_events_by_sender

    async with async_session() as session:
        session.add(HeartbeatEventModel(
            source="imessage",
            event_type="message_received",
            sender="+18065550123",
            contact_name="Avery Chen",
            person_key="person:avery-chen:test",
            display_label="Avery Chen",
            identity_provenance="address_book_match",
            chat_identifier="iMessage;-;+18065550123",
            summary="Earlier named event",
            raw_data=json.dumps({"full_text": "Earlier named event"}),
            is_from_user=False,
        ))
        await session.commit()

    identity = await resolve_contact_identity(
        sender="+18065550123",
        chat_identifier="iMessage;-;+18065550123",
        source="imessage",
    )
    assert identity.display_label == "Avery Chen"
    assert identity.provenance == "thread_name_match"

    event = HeartbeatEventModel(
        source="imessage",
        event_type="message_received",
        sender="+18065550123",
        person_key=identity.person_key,
        display_label=identity.display_label,
        identity_provenance=identity.provenance,
        chat_identifier="iMessage;-;+18065550123",
        summary="Can you call me?",
        is_from_user=False,
    )
    assert list(_group_events_by_sender([event]).keys()) == ["Avery Chen"]


@pytest.mark.asyncio
async def test_multiple_aliases_merge_to_one_stable_person_key(clean_db):
    from services.contact_identity_service import resolve_contact_identity
    from services.database import ContactIdentityAliasModel, ExternalContactModel, async_session

    async with async_session() as session:
        session.add(ExternalContactModel(
            phone_number="+18065550123",
            conversation_id="conv-avery",
            contact_name="Avery Chen",
            platform="sms",
        ))
        await session.commit()

    phone_identity = await resolve_contact_identity(
        sender="+18065550123",
        source="imessage",
    )
    email_identity = await resolve_contact_identity(
        sender="avery@example.com",
        contact_name="Avery Chen",
        source="gmail",
    )

    assert phone_identity.display_label == "Avery Chen"
    assert email_identity.display_label == "Avery Chen"
    assert phone_identity.person_key == email_identity.person_key

    async with async_session() as session:
        aliases = list((await session.execute(
            select(ContactIdentityAliasModel).where(
                ContactIdentityAliasModel.person_key == phone_identity.person_key
            )
        )).scalars().all())
    assert {alias.alias_type for alias in aliases} == {"phone", "email"}



@pytest.mark.asyncio
async def test_the_identity_log_masks_numbers_addresses_and_names(clean_db, caplog, monkeypatch):
    """2026-10-11 (the integrations audit): ``contact_identity_resolved``
    logged every sender's full phone number and the name-derived person key
    at INFO (405 lines in one day's log). The person key keeps its hash, a
    phone its last four digits, an address its first letter and domain."""
    import logging
    import re

    from services import contacts_service
    from services.contact_identity_service import resolve_contact_identity

    def unreachable(_phone):
        raise RuntimeError("Contacts unreachable")

    monkeypatch.setattr(contacts_service, "lookup_phone", unreachable)
    monkeypatch.setattr(contacts_service, "lookup_handle", unreachable)
    caplog.set_level(logging.DEBUG, logger="services.contact_identity_service")

    phone = await resolve_contact_identity(sender="+18065550123", contact_name="Avery Chen", source="imessage")
    email = await resolve_contact_identity(sender="Jordan Vale <jordan.vale@example.com>", source="email")
    assert phone.person_key.startswith("person:avery-chen:"), "the stored key is unchanged"

    lines = [r.getMessage() for r in caplog.records if r.name == "services.contact_identity_service"]
    resolved = [line for line in lines if "contact_identity_resolved" in line]
    assert len(resolved) == 2
    text = "\n".join(lines).lower()
    for leaked in ("8065550123", "+1806", "avery", "chen", "jordan"):
        assert leaked not in text, leaked
    digest = phone.person_key.rsplit(":", 1)[1]
    assert f"person_key=person:{digest} " in resolved[0] and "alias_key=phone:•••0123 " in resolved[0]
    assert re.search(r"person_key=alias:[0-9a-f]{16} ", resolved[1])
    assert "alias_key=email:j•••@example.com " in resolved[1]
