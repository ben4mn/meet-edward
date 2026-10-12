"""The household hub's voice is not Ellis (security fix, 2026-10-11).

``/hub/ask`` on the pet-station panel runs live voice over the same socket
as the app's. Its turns used to run as ``voice`` — Ellis's own source — so
anyone standing at the panel got ``owner_asked=True``: the recipient rule,
stand-downs, never-text-Ellis and the Chrome typing rule all lifted, and
Edward would iMessage anyone from Ellis's number for them
(``docs/research-2026-10-11/integrations-audit.md`` §Security 2).

A hub session's turns are ``hub_voice``: someone in Ellis's home, not
necessarily Ellis. They may reach people (through the recipient rule), push
and leave a task behind, but never wake Edward later, and no list that
means "Ellis is speaking" may ever name them.
"""

from __future__ import annotations

import importlib

import pytest

from services.toolset import policy as P

pytestmark = pytest.mark.unit

HUB = "hub_voice"


def test_the_source_has_one_name():
    assert P.HUB_VOICE_SOURCE == HUB


def test_the_hubs_default_policy_is_someone_in_owners_home():
    assert P.default_policy(HUB) == P.TurnPolicy(
        source=HUB, owner_asked=False, may_reach_people=True, may_push=True, may_schedule=True,
        may_wake_later=False)


def test_it_cannot_forget_and_gets_the_autonomous_turn_cap(monkeypatch):
    monkeypatch.delenv("EDWARD_AUTONOMOUS_TURN_MAX_USD", raising=False)
    p = P.default_policy(HUB)
    assert p.may_forget() is False
    assert p.turn_cap_usd() == P.DEFAULT_AUTONOMOUS_TURN_MAX_USD


# Every list whose meaning is "Ellis himself is speaking in this turn". The
# hub's voice must never join one: each would hand the panel one of Ellis's
# rights (his reach, his reserve, his standing instructions, his forget,
# his hold on the screen, his exemption from stand-downs or the taint gate).
OWNER_LISTS = [
    ("services.toolset.policy", "OWNER_SOURCES"),
    ("services.toolset.policy", "FORGET_SOURCES"),
    ("services.toolset._compat", "OWNER_SOURCES"),
    ("services.memory_service", "OWNER_SPEAKS_SOURCES"),
    ("services.memory_decay_service", "OWNERS_TURN_SOURCES"),
    ("services.llm_spend", "OWNER_TURN_SOURCES"),
    ("services.scheduled_events_service", "OWNER_TURN_SOURCES"),
    ("services.reach_service", "OWNER_TURN_SOURCES"),
    ("services.screen_service", "OWNER_SOURCES"),
    ("services.taint_service", "OWNER_PRESENT_SOURCES"),
    ("services.thought_journal", "OWNER_PRESENT_SOURCES"),
    ("services.graph.tools", "STANDDOWN_EXEMPT_SOURCES"),
    ("services.graph.tools", "FORGET_TURN_SOURCES"),
    ("services.mind_service", "_FALLBACK_OWNER_TURN_SOURCES"),
]


@pytest.mark.parametrize("module,name", OWNER_LISTS, ids=[f"{m}.{n}" for m, n in OWNER_LISTS])
def test_hub_voice_is_in_no_list_that_means_owner_is_speaking(module, name):
    sources = getattr(importlib.import_module(module), name)
    assert "voice" in sources, f"{module}.{name} still names Ellis's own voice (the list is the one meant)"
    assert HUB not in sources, f"{module}.{name} must never name {HUB!r}: the hub panel is not Ellis"


def test_typing_in_owners_chrome_needs_owner_not_the_hub():
    page = "https://www.facebook.com/messages/t/123"
    assert P.chrome_action_allowed("type", {"text": "hi"}, P.default_policy(HUB), current_url=page) \
        .startswith("Tool error: typing into www.facebook.com")
    assert P.chrome_action_allowed("click", {}, P.default_policy(HUB), element_text="Send") \
        .startswith("Tool error: clicking \"Send\"")
    assert P.chrome_action_allowed("type", {"text": "hi"}, P.default_policy("voice"), current_url=page) is None


def test_memory_never_reads_the_hub_as_owner_speaking():
    from services import memory_service

    assert memory_service.is_owners_own_turn(HUB) is False


def test_someone_at_the_hub_is_waiting_for_the_answer():
    """Not Ellis, but someone is standing there: a spoken turn that ends
    empty after its tools gets the closing call, never silence."""
    from services.graph import tool_loop

    assert tool_loop._owner_facing(P.default_policy(HUB)) is True
    assert tool_loop._owner_facing(P.default_policy("voice")) is True
    assert tool_loop._owner_facing(P.default_policy("mind")) is False


# --------------------------------------------------------------------------
# the legacy loop (EDWARD_TOOLSET=legacy, the rollback flag)
# --------------------------------------------------------------------------
# No legacy tool reads the turn's policy: send_imessage / send_sms /
# send_message have no recipient rule, and execute_shell is bash as Ellis with
# no sandbox (it can osascript Messages itself). On that loop a hub turn
# goes without what would act as Ellis; it still reaches him through
# leave_note / send_push_notification (test_legacy_reach_tools.py).

class _Turn:
    def __init__(self, source):
        self.source = source

    def __enter__(self):
        from services import turn_ledger

        rec = turn_ledger.TurnRecord.start(conversation_id="hub-voice-1", source=self.source, model=None,
                                           provider=None)
        self._token = turn_ledger._current.set(rec)
        return rec

    def __exit__(self, *exc):
        from services import turn_ledger

        turn_ledger._current.reset(self._token)
        return False


@pytest.fixture
def legacy_loop(monkeypatch):
    from services.graph import tools as legacy

    monkeypatch.setenv("EDWARD_TOOLSET", "legacy")

    async def owner_handles():
        return ["+12015550100"]

    async def nobody_waiting():
        return []

    monkeypatch.setattr(legacy, "_owner_handles", owner_handles)
    monkeypatch.setattr(legacy, "_chats_where_owner_is_waiting", nobody_waiting)
    return legacy


@pytest.mark.parametrize("name,args", [
    ("send_imessage", {"chat_id": "chat5", "message": "hi"}),
    ("send_imessage", {"contact": "Parker", "message": "hi"}),
    ("send_sms", {"phone_number": "+12015550111", "message": "hi"}),
    ("send_message", {"recipient": "Parker", "message": "hi"}),
    ("send_whatsapp", {"phone_number": "+12015550111", "message": "hi"}),
    ("execute_shell", {"command": "osascript -e 'tell application \"Messages\" to send \"hi\"'"}),
    ("schedule_event", {"description": "text Parker"}),
    ("browser_type", {"text": "hi"}),
    ("remember_forget", {"memory_id": "m1"}),
])
async def test_on_the_legacy_loop_the_hub_cannot_act_as_owner(legacy_loop, name, args):
    from services.graph import tool_loop

    with _Turn(HUB):
        refused = await tool_loop._boundary_check(name, args)
    assert refused and refused.startswith("Tool error:"), refused
    with _Turn("voice"):
        assert legacy_loop.hub_legacy_refusal(name, args) is None, "Ellis's own voice keeps every legacy tool"


async def test_on_the_legacy_loop_the_hub_cannot_text_owner(legacy_loop):
    with _Turn(HUB):
        refused = await legacy_loop.text_to_owner_refusal("send_imessage", {"contact": "Ellis Reed", "message": "hi"})
    assert refused == legacy_loop.TEXT_TO_OWNER_REFUSAL


async def test_reading_and_remembering_stay_on_the_legacy_loop(legacy_loop):
    for name in ("web_search", "remember", "search_memory", "leave_note", "send_push_notification"):
        with _Turn(HUB):
            assert legacy_loop.hub_legacy_refusal(name, {}) is None, name


def test_the_new_toolset_is_left_to_the_policy(monkeypatch):
    """Under v2 every send is ``send_message``, which runs the recipient rule
    on the hub's policy itself (test_send_message_policy.py)."""
    from services.graph import tools as legacy

    monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
    with _Turn(HUB):
        assert legacy.hub_legacy_refusal("send_message", {"chat_id": "chat5", "message": "hi"}) is None
        assert legacy.hub_legacy_refusal("bash", {"command": "ls"}) is None


# --------------------------------------------------------------------------
# memory: what is said at the hub is heard, never Ellis's own word
# --------------------------------------------------------------------------
# The hub's conversation is a ``user`` one (it shows in Ellis's sidebar), and
# its turns extract under the chat profile, so a guest's "I'm allergic to
# peanuts" came out heard/owner/user_stated and — by its conversation —
# Ellis's own word: ranked as his in a contradiction, asked of him, never
# auto-retired.

class _Row:
    def __init__(self, turn_source, origin="heard", source_type="user_stated"):
        self.id = "m-hub"
        self.importance = 0.5
        self.used_count = 0
        self.origin = origin
        self.source_type = source_type
        self.source_ref = f'{{"kind": "extraction", "profile": "chat", "turn_source": "{turn_source}"}}'
        self.source_conversation_id = "hub-voice-1"


def test_a_row_from_the_hub_is_not_owners_own_word_in_his_conversation():
    from services.consolidation_service import contradiction_priority
    from services.memory_decay_service import owners_own_word

    hub, owners = _Row(HUB), _Row("voice")
    assert owners_own_word(hub, conversation_source="user") is False
    assert owners_own_word(owners, conversation_source="user") is True
    sources = {"hub-voice-1": "user"}
    assert contradiction_priority(hub, _Row("chat", origin="read"), conversation_sources=sources)[0] == 0
    assert contradiction_priority(owners, _Row("chat", origin="read"), conversation_sources=sources)[0] == 1


def test_a_pin_from_the_hub_thread_is_still_owners():
    from services.memory_decay_service import owners_own_word

    row = _Row(HUB)
    row.source_ref = '{"kind": "owner_confirmed"}'
    assert owners_own_word(row, conversation_source="user") is True


def _coerce(item, turn_source):
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(dict(item), profile="chat", turn_source=turn_source)
    return fields


def test_a_fact_said_at_the_hub_is_heard_but_not_user_stated():
    item = {"content": "I am allergic to peanuts", "memory_type": "fact", "origin": "heard", "about": "owner",
            "source_type": "user_stated", "temporal_nature": "timeless", "confidence": 0.9, "importance": 0.8}
    hub = _coerce(item, HUB)
    assert (hub["origin"], hub["source_type"]) == ("heard", "inferred")
    assert _coerce(item, "voice")["source_type"] == "user_stated", "Ellis's own voice still states it"


def test_an_instruction_said_at_the_hub_is_someone_elses_not_edwards():
    item = {"content": "Always text Rowan when Biscuit eats", "memory_type": "instruction", "origin": "heard",
            "about": "owner", "source_type": "user_stated", "temporal_nature": "timeless", "confidence": 0.9,
            "importance": 0.8}
    hub = _coerce(item, HUB)
    assert (hub["origin"], hub["source_type"]) == ("read", "inferred")
    assert _coerce(item, "voice")["origin"] == "heard"
