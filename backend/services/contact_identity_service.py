"""Deterministic contact identity resolution for heartbeat message metadata."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from email.utils import parseaddr
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, func, or_, select

from services.database import (
    ContactIdentityAliasModel,
    ExternalContactModel,
    HeartbeatEventModel,
    async_session,
)

logger = logging.getLogger(__name__)

RAW_FALLBACK = "raw_fallback"
ADDRESS_BOOK_MATCH = "address_book_match"
THREAD_NAME_MATCH = "thread_name_match"


@dataclass(frozen=True)
class ContactIdentity:
    person_key: str
    display_label: str
    provenance: str
    alias_key: str | None
    raw_identifier: str | None


def _compact_text(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return text or None


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _identifier_parts(raw: str | None) -> tuple[str, str] | tuple[None, None]:
    text = _compact_text(raw)
    if not text:
        return None, None
    _name, parsed_email = parseaddr(text)
    if parsed_email and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", parsed_email):
        return "email", parsed_email.casefold()
    digits = _digits(text)
    if len(digits) >= 7:
        if len(digits) == 10:
            return "phone", f"+1{digits}"
        if text.strip().startswith("+"):
            return "phone", f"+{digits}"
        return "phone", digits
    return "handle", text.casefold()


def alias_key_for(raw_identifier: str | None) -> tuple[str | None, str | None, str | None]:
    alias_type, normalized = _identifier_parts(raw_identifier)
    if not alias_type or not normalized:
        return None, None, None
    return f"{alias_type}:{normalized}", alias_type, normalized


def display_name_key(value: str | None) -> str | None:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
    text = re.sub(r"\s+", " ", text)
    if not text or _looks_like_raw_identifier(text):
        return None
    return text[:160]


def _person_key_from_name(name: str) -> str:
    key = display_name_key(name) or "unknown"
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:96] or "unknown"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"person:{slug}:{digest}"


def _person_key_from_alias(alias_key: str) -> str:
    digest = hashlib.sha1(alias_key.encode("utf-8")).hexdigest()[:16]
    return f"alias:{digest}"


def _looks_like_raw_identifier(value: str | None) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if "@" in text and re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return True
    digits = _digits(text)
    if len(digits) >= 7 and len(digits) >= len(re.sub(r"\s+", "", text)) - 4:
        return True
    return bool(re.fullmatch(r"@?[a-z0-9_.-]{3,}", text.casefold()))


def _looks_like_fallback_label(value: str | None) -> bool:
    return bool(re.search(r"\bcontact\s+\([^)]*\.\.\.\)\s*$", value or "", re.IGNORECASE))


def _is_true_name(candidate: str | None, raw_identifier: str | None) -> bool:
    text = _compact_text(candidate)
    if not text:
        return False
    alias_key, _alias_type, normalized = alias_key_for(raw_identifier)
    if normalized and text.casefold() == normalized.casefold():
        return False
    if alias_key and text.casefold() == alias_key.casefold():
        return False
    return not _looks_like_raw_identifier(text) and not _looks_like_fallback_label(text)


def _redact_identifier(raw_identifier: str | None) -> str:
    text = _compact_text(raw_identifier) or "unknown"
    alias_type, normalized = _identifier_parts(text)
    if alias_type == "phone":
        digits = _digits(normalized)
        prefix = f"+{digits[:4]}" if normalized.startswith("+") else digits[:4]
        return f"{prefix}..."
    if alias_type == "email":
        local, _, domain = normalized.partition("@")
        return f"{local[:1]}...@{domain}" if domain else f"{local[:1]}..."
    if alias_type == "handle":
        handle = normalized
        if not handle.startswith("@") and re.fullmatch(r"[a-z0-9_.-]+", handle):
            handle = f"@{handle}"
        return f"{handle[:5]}..." if len(handle) > 6 else handle
    return "unknown"


def _log_alias(alias_key: str | None) -> str:
    """An alias for the log (2026-10-11): ``phone:•••1234``,
    ``email:j•••@example.com``, ``handle:•••`` — never the whole number,
    address or handle."""
    alias_type, _, value = (alias_key or "").partition(":")
    if not value:
        return "none"
    if alias_type == "phone":
        return f"phone:•••{_digits(value)[-4:]}"
    if alias_type == "email":
        local, _, domain = value.partition("@")
        return f"email:{local[:1]}•••@{domain}"
    return f"{alias_type}:•••"


def _log_person_key(person_key: str | None) -> str:
    """A person key for the log: its kind and hash, never the name-derived
    slug of ``person:<slug>:<hash>``."""
    parts = (person_key or "").split(":")
    if len(parts) >= 2 and parts[0] in ("person", "alias"):
        return f"{parts[0]}:{parts[-1]}"
    return "key:" + hashlib.sha1((person_key or "").encode("utf-8")).hexdigest()[:10]


def fallback_display_label(
    raw_identifier: str | None,
    *,
    chat_name: str | None = None,
    source: str | None = None,
) -> str:
    context = _compact_text(chat_name)
    if context and not _looks_like_raw_identifier(context):
        prefix = f"{context} contact"
    elif source:
        prefix = f"{source.title()} contact"
    else:
        prefix = "Unknown contact"
    return f"{prefix} ({_redact_identifier(raw_identifier)})"


def _raw_mapping(raw_data: str | dict | None) -> dict[str, Any]:
    if isinstance(raw_data, dict):
        return dict(raw_data)
    try:
        parsed = json.loads(raw_data) if raw_data else {}
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _lookup_address_book_name(raw_identifier: str | None) -> str | None:
    """The owner's address book name for a phone or an email (an Apple ID sender
    has an email handle, 2026-10-11), else None."""
    alias_key, alias_type, normalized = alias_key_for(raw_identifier)
    if not alias_key or alias_type not in ("phone", "email"):
        return None

    try:
        async with async_session() as session:
            name = (await session.execute(
                select(ExternalContactModel.contact_name).where(
                    or_(
                        ExternalContactModel.phone_number == raw_identifier,
                        ExternalContactModel.phone_number == normalized,
                    )
                )
            )).scalar_one_or_none()
            if _is_true_name(name, raw_identifier):
                return _compact_text(name)
    except Exception as exc:
        logger.debug("external contact lookup failed alias=%s error=%s", _log_alias(alias_key), exc)

    try:
        from services.contacts_service import lookup_handle

        handle = normalized if alias_type == "email" else (raw_identifier or normalized)
        result = await asyncio.to_thread(lookup_handle, handle)
        if result.get("success") and result.get("matches"):
            name = result["matches"][0].get("name")
            if _is_true_name(name, raw_identifier):
                return _compact_text(name)
    except Exception as exc:
        logger.debug("address book lookup failed alias=%s error=%s", _log_alias(alias_key), exc)
    return None


async def _lookup_thread_name(
    *,
    raw_identifier: str | None,
    chat_identifier: str | None,
    chat_name: str | None,
) -> str | None:
    clauses = []
    if raw_identifier:
        clauses.append(HeartbeatEventModel.sender == raw_identifier)
    alias_key, _alias_type, normalized = alias_key_for(raw_identifier)
    if normalized and normalized != raw_identifier:
        clauses.append(HeartbeatEventModel.sender == normalized)
    if chat_identifier:
        clauses.append(HeartbeatEventModel.chat_identifier == chat_identifier)
    if not clauses:
        return None

    async with async_session() as session:
        rows = list((await session.execute(
            select(
                HeartbeatEventModel.display_label,
                HeartbeatEventModel.contact_name,
                HeartbeatEventModel.sender,
            )
            .where(or_(*clauses))
            .order_by(desc(HeartbeatEventModel.created_at))
            .limit(50)
        )).all())

    for display_label, contact_name, sender in rows:
        for candidate in (display_label, contact_name, sender):
            if _is_true_name(candidate, raw_identifier):
                return _compact_text(candidate)
    return None


async def _person_key_for_display_name(
    name: str,
    *,
    fallback_alias_key: str,
) -> str:
    name_key = display_name_key(name)
    if name_key:
        async with async_session() as session:
            row = (await session.execute(
                select(ContactIdentityAliasModel)
                .where(ContactIdentityAliasModel.display_name_key == name_key)
                .order_by(desc(ContactIdentityAliasModel.updated_at))
                .limit(1)
            )).scalar_one_or_none()
            if row:
                return row.person_key
    return _person_key_from_name(name) if name_key else _person_key_from_alias(fallback_alias_key)


async def resolve_contact_identity(
    *,
    sender: str | None,
    contact_name: str | None = None,
    chat_identifier: str | None = None,
    chat_name: str | None = None,
    source: str | None = None,
    raw_data: str | dict | None = None,
) -> ContactIdentity:
    """Resolve inbound metadata into a stable person key and display label."""
    raw = _compact_text(sender) or _compact_text(contact_name) or _compact_text(chat_identifier)
    alias_key, alias_type, normalized = alias_key_for(raw)
    if not alias_key:
        label = _compact_text(contact_name) or fallback_display_label(
            raw, chat_name=chat_name, source=source
        )
        return ContactIdentity(
            person_key=_person_key_from_name(label),
            display_label=label,
            provenance=THREAD_NAME_MATCH if display_name_key(label) else RAW_FALLBACK,
            alias_key=None,
            raw_identifier=raw,
        )

    address_book_name = await _lookup_address_book_name(raw)
    thread_name = None if address_book_name else await _lookup_thread_name(
        raw_identifier=raw,
        chat_identifier=chat_identifier,
        chat_name=chat_name,
    )

    explicit_name = _compact_text(contact_name) if _is_true_name(contact_name, raw) else None
    payload = _raw_mapping(raw_data)
    payload_name = next(
        (
            _compact_text(payload.get(key))
            for key in ("contact_name", "display_name", "name")
            if _is_true_name(payload.get(key), raw)
        ),
        None,
    )

    true_name = address_book_name or explicit_name or payload_name or thread_name
    provenance = (
        ADDRESS_BOOK_MATCH
        if address_book_name
        else THREAD_NAME_MATCH
        if true_name
        else RAW_FALLBACK
    )

    async with async_session() as session:
        existing = (await session.execute(
            select(ContactIdentityAliasModel).where(
                ContactIdentityAliasModel.alias_key == alias_key
            )
        )).scalar_one_or_none()

        if existing and provenance == RAW_FALLBACK and existing.provenance != RAW_FALLBACK:
            return ContactIdentity(
                person_key=existing.person_key,
                display_label=existing.display_label,
                provenance=existing.provenance,
                alias_key=alias_key,
                raw_identifier=raw,
            )

        if true_name:
            display_label = true_name
            person_key = await _person_key_for_display_name(
                display_label,
                fallback_alias_key=alias_key,
            )
            name_key = display_name_key(display_label)
        elif existing:
            display_label = existing.display_label
            person_key = existing.person_key
            name_key = existing.display_name_key
            provenance = existing.provenance or RAW_FALLBACK
        else:
            display_label = fallback_display_label(
                raw,
                chat_name=chat_name,
                source=source,
            )
            person_key = _person_key_from_alias(alias_key)
            name_key = display_name_key(display_label)

        if existing is None:
            session.add(ContactIdentityAliasModel(
                alias_key=alias_key,
                alias_type=alias_type or "unknown",
                raw_identifier=raw,
                person_key=person_key,
                display_label=display_label,
                display_name_key=name_key,
                provenance=provenance,
                source=source,
                chat_identifier=chat_identifier,
            ))
        else:
            existing.person_key = person_key
            existing.display_label = display_label
            existing.display_name_key = name_key
            existing.provenance = provenance
            existing.source = source or existing.source
            existing.chat_identifier = chat_identifier or existing.chat_identifier
            existing.raw_identifier = raw
        await session.commit()

    logger.info(
        "contact_identity_resolved person_key=%s alias_key=%s provenance=%s",
        _log_person_key(person_key),
        _log_alias(alias_key),
        provenance,
    )
    return ContactIdentity(
        person_key=person_key,
        display_label=display_label,
        provenance=provenance,
        alias_key=alias_key,
        raw_identifier=raw,
    )


def append_identity_raw_data(raw_data: str | None, identity: ContactIdentity) -> str:
    payload = _raw_mapping(raw_data)
    payload["person_key"] = identity.person_key
    payload["display_label"] = identity.display_label
    payload["identity_provenance"] = identity.provenance
    if identity.alias_key:
        payload["identity_alias_key"] = identity.alias_key
    return json.dumps(payload)


# Moved from the notification dispatcher when it was retired (mind rebuild
# Phase 5): how a heartbeat event is labelled and grouped by who sent it.

def event_group_label(event) -> str:
    return (
        (getattr(event, "display_label", None) or "").strip()
        or (getattr(event, "contact_name", None) or "").strip()
        or (getattr(event, "sender", None) or "").strip()
        or (getattr(event, "chat_identifier", None) or "").strip()
        or "Unknown"
    )


def group_events_by_sender(events) -> dict:
    groups: dict = {}
    for evt in events:
        groups.setdefault(event_group_label(evt), []).append(evt)
    return groups
