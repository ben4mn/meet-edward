"""The monotonic outgoing-send counter.

The legacy heartbeat reply turns (``_execute_classification``,
``_handle_respond_failure``, ``MAX_RESPOND_ATTEMPTS``) left with the legacy
triage engine; the mind path is the only path. What survives from this file
is the counter they bracketed sends with: anything that needs to know
whether a message already went out during a turn reads
``imessage_service.outgoing_send_count()`` before and after.
"""


def test_send_counter_is_monotonic():
    """A bracket needs a counter that only goes up: the old bounded deque
    (maxlen 20) pinned its length at 20 on a busy day, so
    `sends_after > sends_before` was never true. The deque itself is gone
    (echoes are recognised by ROWID, ``services/imessage_sent.py``).
    """
    from services import imessage_service as imsg

    assert not hasattr(imsg, "_recent_edward_sends"), "the echo deque is deleted (pings-media-spec §2.5.7)"
    before = imsg.outgoing_send_count()
    for _ in range(25):
        imsg._note_outgoing_send_count()
    assert imsg.outgoing_send_count() - before == 25
