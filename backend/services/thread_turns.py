"""One turn at a time in a thread — and never a turn waiting on itself.

Why (2026-09-27; docs/architecture/tool-loop.md): ``mind_service.thread_lock`` kept two wakes
from interleaving in ``edward-mind``, but nothing else took it. Chat and
voice streams, a scheduled event firing in the owner's own thread ("text me at
5") or falling back to ``edward-mind``, a heartbeat turn in a contact's
thread — all ran unlocked. A turn reads the checkpoint at its start and
writes it at its end, so two overlapping turns in one thread (a wake and
the memory report in ``edward-mind`` that morning, or the owner on two devices)
each wrote their own copy and the last write won: the other turn's messages
were gone from the thread.

``streaming.chat_with_memory`` and ``streaming.stream_with_memory_events``
now hold the same per-thread lock around the whole turn (``acquire``), so a
second turn in the thread waits for the first. Different threads still run
side by side.

Re-entry. ``asyncio.Lock`` is not re-entrant, and two callers already hold
the lock when they reach a turn:

* ``mind_service._wake_once`` takes ``thread_lock(conversation_id)`` and
  then calls ``chat_with_memory`` for the wake's turn. Every ``wake`` runs
  with ``turn_source`` ``mind`` or ``pulse`` (the surfaces pulse) — pinned
  by ``tests/test_thread_serialization.py`` — so those turns are
  ``CALLER_SERIALIZED_SOURCES`` and never take the lock again.
* ``approvals_service.append_thread_note`` holds it only to write a note
  into the checkpoint; it runs no turn, and it is spawned as its own task,
  so a turn never waits on it.

A turn that starts a turn in the same thread from inside itself (in the
same task, or a task it created and may await) is seen through a context
variable of the threads this context holds, and runs without waiting — a
turn must never deadlock on itself. The marker is set per step in the
streaming path (``holding``), never across a ``yield``: a context variable
set inside an async generator would leak into its consumer, and could not
be reset when the loop's finalizer closes the generator from another
context.

A stream nobody closes. A chat stream holds its thread until ``done`` or
until it is closed. When the owner presses Stop, iOS kills a hidden tab or the
network drops, the SSE plumbing (``routers/chat.py``, behind
``AuthMiddleware``) abandons the stream in a reference cycle without
closing it, so it lets go of the thread only when the cyclic GC collects
that cycle (review of batch B, 2026-09-27: over a minute in a quiet
process). A turn that has to wait on its thread therefore nudges the young
GC generations once a second (``GC_NUDGE_SECONDS``) while it waits: the
cycle is new objects, a young collection finalizes it, and the stream's own
``finally`` frees the thread. Closing the stream on disconnect in the
router is the real fix; this keeps any consumer that leaks one from
stalling the thread.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import time
from contextlib import contextmanager
from typing import FrozenSet, Iterator, Optional

# Turns whose caller already holds ``thread_lock(conversation_id)``: every
# ``mind_service.wake`` (the mind's wakes, the research and free-time wakes,
# the surfaces pulse) runs its turn as ``mind`` or ``pulse`` inside the lock.
CALLER_SERIALIZED_SOURCES: FrozenSet[str] = frozenset({"mind", "pulse"})

# A wait longer than this is worth a line in the log (a chat turn queued
# behind a wake or a scheduled event).
SLOW_WAIT_LOG_SECONDS = 1.0

# While a turn waits on its thread, the young GC generations are collected
# this often, so a stream abandoned in a reference cycle frees the thread
# (see "A stream nobody closes" above). Generations 0 and 1 only: cheap, and
# a full collection would stall the loop — voice audio included.
GC_NUDGE_SECONDS = 1.0

# The longest a turn waits for its thread. Past it the turn runs anyway,
# unlocked, with a WARNING in the log — two turns in one thread was the
# state of things before 2026-09-27; a thread held forever by something
# nobody frees is worse. Longer than any holder's own bound, so it never
# cuts in on a turn still inside its deadline: the Sunday reflection's tick
# (900 s turn + 45 s pack + 120 s margin = 1,065 s) and a streamed turn
# (600 s of tools + its 300 s closing call). Pinned against those
# constants in tests/test_thread_serialization.py.
ACQUIRE_BACKSTOP_SECONDS = 20 * 60

_held: contextvars.ContextVar[FrozenSet[str]] = contextvars.ContextVar("edward_held_threads", default=frozenset())


def held_threads() -> FrozenSet[str]:
    """The threads whose lock this context holds (for a nested turn)."""
    return _held.get()


def needs_lock(conversation_id: Optional[str], turn_source: Optional[str],
               held: FrozenSet[str] = frozenset()) -> bool:
    """Should this turn take its thread's lock? Pure.

    No for a turn without a thread, a turn whose caller holds the lock
    (``CALLER_SERIALIZED_SOURCES``), and a turn nested in one that holds it.
    """
    if not conversation_id:
        return False
    if turn_source in CALLER_SERIALIZED_SOURCES:
        return False
    return conversation_id not in held


@contextmanager
def holding(conversation_id: Optional[str]) -> Iterator[None]:
    """Mark ``conversation_id`` as held by this context while inside, so a
    turn started from here (or from a task created here) does not wait on
    it. Enter and leave within one step — never across a ``yield``."""
    if not conversation_id:
        yield
        return
    token = _held.set(_held.get() | {conversation_id})
    try:
        yield
    finally:
        _held.reset(token)


class TurnSlot:
    """A thread's lock, held for one turn. ``release`` is idempotent, so a
    streaming turn can let go as its last event passes and again on close."""

    __slots__ = ("conversation_id", "_lock", "waited_s")

    def __init__(self, conversation_id: Optional[str], lock: Optional[asyncio.Lock], waited_s: float = 0.0):
        self.conversation_id = conversation_id
        self._lock = lock
        self.waited_s = waited_s

    @property
    def locked(self) -> bool:
        return self._lock is not None

    def release(self) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.release()


async def acquire(conversation_id: Optional[str], turn_source: Optional[str]) -> TurnSlot:
    """Wait for the thread, then hold it for one turn (see ``needs_lock``).

    A cancellation while waiting propagates with nothing held. A wait past
    ``ACQUIRE_BACKSTOP_SECONDS`` returns an unlocked slot: the turn runs.
    """
    if not needs_lock(conversation_id, turn_source, held_threads()):
        return TurnSlot(conversation_id, None)
    from services.mind_service import thread_lock

    lock = thread_lock(conversation_id)
    started = time.monotonic()
    if not lock.locked():
        await lock.acquire()
    else:
        nudger = asyncio.create_task(_nudge_gc_while_waiting())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=ACQUIRE_BACKSTOP_SECONDS)
        except asyncio.TimeoutError:
            waited = time.monotonic() - started
            print(f"[turn] WARNING: {turn_source or '?'} turn in {conversation_id} waited {waited:.0f}s "
                  "for the turn before it; running without the thread's lock")
            return TurnSlot(conversation_id, None, waited)
        finally:
            nudger.cancel()
    waited = time.monotonic() - started
    if waited >= SLOW_WAIT_LOG_SECONDS:
        print(f"[turn] {turn_source or '?'} turn in {conversation_id} waited {waited:.1f}s for the turn before it")
    return TurnSlot(conversation_id, lock, waited)


async def _nudge_gc_while_waiting() -> None:
    """Collect the young generations every ``GC_NUDGE_SECONDS`` until
    cancelled (the wait is over): an abandoned stream is finalized and
    frees the thread it holds."""
    while True:
        await asyncio.sleep(GC_NUDGE_SECONDS)
        gc.collect(1)
