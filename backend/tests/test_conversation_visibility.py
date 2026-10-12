"""Which conversations the sidebar and Today feed show by default.

Edward talks to himself a lot: the expression pulse, the wake-up ticks and
the scheduled events all leave ``edward_thought`` / ``scheduled_event`` /
``heartbeat`` conversations behind. On 2026-09-05 34 of the 40 most recent
conversations were his own thoughts titled "Edward", and Ellis's four chats
were buried under them.

The rule: a conversation is shown by default when it is Ellis's, or when
Edward reached out about it (``notified_user``), or when Ellis engaged with
it (more than two messages). Everything else is a *thought* and is only
listed on request (``include_scheduled=True``).

The same rule is expressed twice — once as a pure predicate (tested here,
used by the Today composer) and once as SQL in ``get_conversations`` /
``search_conversations``. ``EDWARD_OWN_SOURCES`` is the single list both
read from so they cannot drift.
"""

from types import SimpleNamespace

from services import conversation_service as svc


def _conv(source, message_count=1, notified_user=False):
    return SimpleNamespace(
        source=source, message_count=message_count, notified_user=notified_user
    )


def test_edward_thought_is_one_of_his_own_sources():
    assert "edward_thought" in svc.EDWARD_OWN_SOURCES
    assert "scheduled_event" in svc.EDWARD_OWN_SOURCES
    assert "heartbeat" in svc.EDWARD_OWN_SOURCES
    # Ellis's chats and inbound SMS/WhatsApp threads are never hidden.
    assert "user" not in svc.EDWARD_OWN_SOURCES
    assert "external_message" not in svc.EDWARD_OWN_SOURCES


def test_user_conversation_always_visible():
    assert svc.is_conversation_visible(_conv("user")) is True
    assert svc.is_conversation_visible(_conv("external_message")) is True


def test_edward_thought_hidden_by_default():
    assert svc.is_conversation_visible(_conv("edward_thought")) is False
    assert svc.is_conversation_visible(_conv("scheduled_event")) is False
    assert svc.is_conversation_visible(_conv("heartbeat")) is False


def test_thought_visible_once_owner_engaged():
    assert svc.is_conversation_visible(_conv("edward_thought", message_count=3)) is True


def test_thought_visible_when_edward_reached_out():
    assert (
        svc.is_conversation_visible(
            _conv("heartbeat", message_count=2, notified_user=True)
        )
        is True
    )
    # notified but with only the trigger message: still a thought.
    assert (
        svc.is_conversation_visible(
            _conv("heartbeat", message_count=1, notified_user=True)
        )
        is False
    )


def test_include_scheduled_shows_everything():
    assert svc.is_conversation_visible(_conv("edward_thought"), include_scheduled=True) is True


# --- sidebar groups -------------------------------------------------------

def test_sources_for_group_yours_is_owner_and_inbound():
    assert set(svc.sources_for_group("yours")) == {"user", "external_message"}


def test_sources_for_group_edwards_is_everything_he_started():
    s = set(svc.sources_for_group("edwards"))
    assert {"edward_thought", "scheduled_event", "heartbeat", "orchestrator_worker"} <= s
    assert "user" not in s and "external_message" not in s


def test_sources_for_group_unknown_is_none():
    assert svc.sources_for_group(None) is None
    assert svc.sources_for_group("all") is None
    assert svc.sources_for_group("garbage") is None
