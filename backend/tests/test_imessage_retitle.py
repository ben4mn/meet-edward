"""iMessage conversation titles should carry the contact's name, not the number.

``get_or_create_for_chat`` creates the conversations row the first time a
chat is seen. The listener often only knows the phone number at that
point, so the title is minted as ``iMessage: +15555550173``. When the
contact name resolves on a later poll, the mapping row's ``chat_name`` is
refreshed — but the conversation title was not, so the sidebar and the
inbox kept showing bare numbers indefinitely (2026-09-05).

``retitled_for`` is the pure decision: given what we now know and what the
title currently is, return the better title, or ``None`` when the current
title should be left alone (a human-edited title must never be clobbered).
"""

from services.imessage_conversation_service import retitled_for, title_for_chat


def test_title_for_chat_prefers_name_then_identifier():
    assert title_for_chat("+15555550173", None) == "iMessage: +15555550173"
    assert title_for_chat("+15555550173", "Ian Cho") == "iMessage: Ian Cho"


def test_title_for_chat_truncates_long_names_like_before():
    long = "x" * 80
    assert title_for_chat("chat1", long) == "iMessage: " + "x" * 50


def test_retitle_when_title_is_still_the_bare_identifier():
    assert (
        retitled_for(
            chat_identifier="+15555550173",
            chat_name="Ian Cho",
            current_title="iMessage: +15555550173",
        )
        == "iMessage: Ian Cho"
    )


def test_no_retitle_without_a_name():
    assert (
        retitled_for(
            chat_identifier="+15555550173",
            chat_name=None,
            current_title="iMessage: +15555550173",
        )
        is None
    )


def test_no_retitle_when_already_named_or_edited_by_a_human():
    assert (
        retitled_for(
            chat_identifier="+15555550173",
            chat_name="Ian Cho",
            current_title="iMessage: Ian Cho",
        )
        is None
    )
    assert (
        retitled_for(
            chat_identifier="+15555550173",
            chat_name="Ian Cho",
            current_title="Ian about Sunday",
        )
        is None
    )


def test_retitle_ignores_group_chat_ids_that_are_not_numbers_too():
    # Group chats are identified by chat123... ids; same rule applies.
    assert (
        retitled_for(
            chat_identifier="chat100000000000000003",
            chat_name="Book Club",
            current_title="iMessage: chat100000000000000003",
        )
        == "iMessage: Book Club"
    )
