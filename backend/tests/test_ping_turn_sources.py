"""Whose words a ping turn carries (AREA 3, pings-media-spec §2.7, §4.4).

A ping turn's "user" message is the ping. In Ellis's self-chat that is Ellis
himself (``ping_owner``): his instruction there is his, as in the app. Every
other ping (``ping``) — a friend's ``@Edward``, and Ellis's ``@Edward`` in a
group, whose trigger also carries other people's lines — is someone else
speaking, so an instruction in it is ``read``, never Ellis's ``heard/owner``.
AREA 4's spend ledger keeps the same list of Ellis's own turns.
"""

from __future__ import annotations

from services import llm_spend, memory_service


def test_ping_owner_is_owners_own_turn():
    assert memory_service.is_owners_own_turn("ping_owner")
    assert not memory_service.someone_else_speaks("ping_owner")


def test_a_ping_is_someone_else_speaking():
    assert memory_service.someone_else_speaks("ping")
    assert not memory_service.is_owners_own_turn("ping")


def test_the_spend_ledger_keeps_the_same_list():
    assert "ping_owner" in llm_spend.OWNER_TURN_SOURCES
    assert set(llm_spend.OWNER_TURN_SOURCES) == set(memory_service.OWNER_SPEAKS_SOURCES)


def _instruction():
    return {"content": "Always text me the gate number", "memory_type": "instruction", "origin": "heard",
            "about": "owner", "temporal_nature": "permanent", "source_type": "user_stated", "confidence": 0.9}


def test_a_self_chat_instruction_is_heard_from_owner():
    fields, _misses = memory_service.coerce_extracted_fields(_instruction(), profile="chat",
                                                            turn_source="ping_owner")
    assert (fields["origin"], fields["about"]) == ("heard", "owner")


def test_an_instruction_in_a_friends_ping_is_read_not_heard():
    fields, _misses = memory_service.coerce_extracted_fields(_instruction(), profile="heartbeat",
                                                            turn_source="ping")
    assert fields["origin"] == "read"


def test_an_event_owner_makes_in_his_self_chat_is_his():
    """The spend cap's 'whose event' reads the turn it was made in."""
    from services import scheduled_events_service

    assert set(scheduled_events_service.OWNER_TURN_SOURCES) == set(memory_service.OWNER_SPEAKS_SOURCES)


def test_the_screen_hold_keeps_the_same_list():
    """Ellis in his self-chat is Ellis at the screen too (§4.4's lead row): what
    he puts up from there holds like what he puts up from the app."""
    from services import screen_service

    assert "ping_owner" in screen_service.OWNER_SOURCES
    assert set(screen_service.OWNER_SOURCES) == set(memory_service.OWNER_SPEAKS_SOURCES)


def test_decay_reads_owners_own_words_from_the_same_list():
    """The AREA 1 + 3 merge: ``owner_ping`` (AREA 1: his ``@Edward`` text read
    alone) and ``ping_owner`` (AREA 3: his self-chat) are both Ellis speaking,
    so an instruction from either is his own word to decay too."""
    from services import memory_decay_service

    assert set(memory_decay_service.OWNERS_TURN_SOURCES) == set(memory_service.OWNER_SPEAKS_SOURCES)


def test_forget_runs_in_owners_self_chat_by_both_lists():
    """Ellis's self-chat is his own conversation: ``memory(forget)`` (the
    toolset's policy) and the old ``forget_about`` body read the same list,
    and it names ``ping_owner``."""
    from services.graph import tools
    from services.toolset import policy

    assert "ping_owner" in tools.FORGET_TURN_SOURCES
    assert set(tools.FORGET_TURN_SOURCES) == set(policy.FORGET_SOURCES)
    assert "ping" not in policy.FORGET_SOURCES


def test_every_owner_is_asking_fallback_names_his_self_chat():
    """With no policy or ping context to read, the turn's source decides
    whether Ellis is asking: the toolset's seam and reach_service read the
    same turns as the policy, his self-chat included — otherwise his own
    ping's ``tell_owner`` would be refused as ``other``."""
    from services import reach_service
    from services.toolset import _compat, policy

    assert set(_compat.OWNER_SOURCES) == set(policy.OWNER_SOURCES)
    assert set(reach_service.OWNER_TURN_SOURCES) == set(policy.OWNER_SOURCES)
    assert reach_service.derive_origin(turn_source="ping_owner") == "owner"
