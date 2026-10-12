"""
WebSocket endpoint for live voice conversations — ElevenLabs end to end.

The browser streams raw mic PCM the whole time the session is open.
The backend relays it to Scribe v2 Realtime, which does voice-activity
detection server-side and hands back partial transcripts as the user
speaks and a committed transcript when they stop. The committed text
runs an ordinary Edward turn (full tools, same conversation, memory)
on the fast model tier; tokens are cut into sentences and pushed into
an ElevenLabs stream-input socket, and the PCM that comes back is
relayed to the browser.

Protocol:
  Client -> Server:
    JSON   { "type": "start_session", "conversation_id"?: str, "mode"?: "auto" | "ptt",
             "sensitivity"?: "high" | "normal" | "low", "surface"?: "app" | "hub" }
           (sensitivity: how much of the room the mic should hear. "low" is a
            noisy room — Scribe gets a higher VAD threshold and it takes three
            words of overlap, not two, to cut Edward off. The browser also runs
            a noise gate in front of the socket at "normal" and "low".)
           (surface: who is speaking. "app" — the default, and anything
            unknown — is the chat page's live voice: the owner, turn_source
            "voice". "hub" is the household hub's /hub/ask: someone in the
            owner's home, not necessarily the owner, turn_source "hub_voice",
            which is not in any list that means the owner is speaking
            (2026-10-11).)
    Binary 0x01 + PCM16 mono 16 kHz mic frames (continuous while the session is open)
    JSON   { "type": "commit" }       (ptt: finger lifted; auto mode ignores it)
    JSON   { "type": "interrupt" }
    JSON   { "type": "end_session" }

  Server -> Client:
    JSON   { "type": "session_started", "conversation_id", "mode", "sensitivity", "surface", "sample_rate_in", "sample_rate_out" }
    JSON   { "type": "partial_transcript", "text" }
    JSON   { "type": "transcript", "text" }                     (committed)
    JSON   { "type": "state", "state": "listening"|"thinking"|"speaking", "reason"? }
    JSON   { "type": "bridge", "text" }                         (spoken filler while thinking)
    JSON   { "type": "response_text", "text", "is_final" }
    JSON   { "type": "tool_activity", "tool_name", "status", "message" }
    Binary 0x02 + PCM16 mono 24 kHz
    JSON   { "type": "response_done" }
    JSON   { "type": "handoff", "text", "conversation_id" }        (the turn now finishes on its own)
    JSON   { "type": "handoff_done", "outcome", "conversation_id" } (a handed-off turn finished; session still open)
    JSON   { "type": "error", "message", "code"? }

Voice keeps working (2026-09-24): a spoken turn that is
still running tools after ``VOICE_HANDOFF_SECONDS`` or at its third tool
call says a handoff line (``handoff`` + ``response_text``, then
``state: listening, reason: handoff`` and ``response_done``) and keeps
going detached from the session: barge-in and ending the session no
longer cancel it; ``interrupt`` or a spoken "stop" still does. Its result
is spoken as an ordinary ``response_text`` when the session is open and
quiet, spoken first by the next turn when it is busy, and pushed (linking
the thread) when the session has ended. A new question asked meanwhile
waits for it — two turns must never write one checkpoint at once. The
decisions are pure, in ``services/voice/handoff.py``.
"""

from __future__ import annotations

import asyncio
import time as _time
import json
import logging
import random
import uuid
from contextlib import nullcontext
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import auth_service, display_signals, llm_spend
from services.conversation_service import (
    conversation_exists,
    create_conversation,
    increment_message_count,
)
from services.graph import get_graph, stream_with_memory_events
from services.settings_service import get_settings
from services.skills_service import is_skill_enabled
from services.voice.bridges import DEFAULT_BRIDGE_THRESHOLD_S, bridge_due, pick_bridge
from services.voice.handoff import (
    DEFAULT_HANDOFF_SECONDS,
    HANDOFF_TOOL_CALLS,
    PHASE_ANSWERING,
    PHASE_HANDED_OFF,
    PHASE_THINKING,
    PHASE_TOOLS,
    STOPPED_LINE,
    delivery_for,
    is_stop_phrase,
    pick_handoff_line,
    pick_wait_line,
    push_text_for,
    result_speech,
    should_hand_off,
)
from services.voice.prompt import VOICE_MODEL, voice_system_prompt
from services.voice.scribe_protocol import (
    SCRIBE_SAMPLE_RATE,
    ScribeEvent,
    is_recoverable_error,
    normalize_sensitivity,
    scribe_tuning,
)
from services.voice.sentence_feed import SentenceFeed
from services.voice.tts_protocol import TTS_SAMPLE_RATE
from services.voice.turn_state import barge_in_min_words, decide
from services.voice_service import get_voice_service

logger = logging.getLogger(__name__)

router = APIRouter()

PREFIX_MIC_AUDIO = b"\x01"
PREFIX_TTS_AUDIO = b"\x02"

# How long Edward stays silent before saying a bridge line. Patched in tests.
BRIDGE_THRESHOLD_S = DEFAULT_BRIDGE_THRESHOLD_S

# Scribe sessions have a server-side time limit; reopen a few times before giving up.
MAX_STT_RECONNECTS = 5

# How long a spoken turn may run tools, and how many tool calls it may
# start, before Edward says he'll report back and lets it finish on its
# own. Patched in tests.
VOICE_HANDOFF_SECONDS = DEFAULT_HANDOFF_SECONDS
VOICE_HANDOFF_TOOL_CALLS = HANDOFF_TOOL_CALLS

# A handed-off turn outlives its socket. The loop only holds weak
# references to tasks, so keep a strong one until it finishes.
_DETACHED_TURNS: set = set()

# Whose voice a session is (security fix, 2026-10-11). The chat page's live
# voice is the owner; the household hub's panel is anyone standing at it, so
# its turns run as ``hub_voice`` (``toolset.policy.default_policy``: the
# recipient rule, never-text-the-owner, stand-downs and the Chrome typing
# rule hold) on the same voice route. Before this, the hub ran as the owner.
TURN_SOURCE_BY_SURFACE = {"app": "voice", "hub": "hub_voice"}


def normalize_surface(raw) -> str:
    """``app`` or ``hub``; anything else (an old client sends nothing) is the app."""
    value = raw.strip().lower() if isinstance(raw, str) else ""
    return value if value in TURN_SOURCE_BY_SURFACE else "app"


async def _push_handoff_result(conversation_id: str, answer: str, failed: bool = False) -> bool:
    """The session is gone: tell the owner the work is done and link the
    thread, where the full answer already is."""
    try:
        from services import push_service

        if not push_service.is_configured():
            return False
        title, body = push_text_for(answer, failed=failed)
        await push_service.send_push_notification(
            title=title,
            body=body,
            url=f"/chat?c={conversation_id}",
            tag=f"voice-handoff-{conversation_id}",
            source="voice_handoff",
            conversation_id=conversation_id,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("voice handoff push failed (conversation=%s): %s", conversation_id, e)
        return False


HUB_RESULT_TITLE = "Voice at the hub"


async def _leave_hub_result(conversation_id: str, answer: str, failed: bool = False) -> bool:
    """The hub's session is gone: whoever asked has walked away, and they
    are not necessarily the owner. The result is left for the owner as the hub's reach
    (``reach_service``, origin ``hub``: one follow-up, the lock-screen
    secret check) — a note on Today linking the thread, never a raw push."""
    try:
        from services import reach_service

        _title, body = push_text_for(answer, failed=failed)
        out = await reach_service.tell_owner(
            body, title=HUB_RESULT_TITLE, urgent=False, link=f"/chat?c={conversation_id}", origin="hub",
            dedupe_key=f"hub handoff {conversation_id} {body}")
        return not str(out or "").startswith("Tool error:")
    except Exception as e:  # noqa: BLE001
        logger.warning("voice handoff note failed (conversation=%s): %s", conversation_id, e)
        return False


async def _close_tts_ctx(tts_ctx, tts_open: asyncio.Task) -> None:
    """Close a turn's TTS socket if its handshake finished, else abandon it."""
    if tts_open.done() and not tts_open.cancelled() and tts_open.exception() is None:
        try:
            await tts_ctx.__aexit__(None, None, None)
        except Exception:
            pass
    else:
        tts_open.cancel()


def _record_handoff(conversation_id: Optional[str], outcome: str, detail: dict, source: str = "voice") -> None:
    try:
        from services import event_ledger

        event_ledger.record_event(
            kind="voice_handoff", source=source, outcome=outcome, ref_id=conversation_id, detail=detail,
        )
    except Exception:  # noqa: BLE001
        pass


class VoiceSession:
    """One browser socket: mic in, Scribe events, turns, PCM out."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.conversation_id: Optional[str] = None
        self.mode = "auto"
        self.sensitivity = "normal"
        self.surface = "app"
        self.state = "listening"
        self.active = False
        self.closed = False

        self._stt_ctx = None
        self._stt = None
        self._stt_task: Optional[asyncio.Task] = None
        self._turn_task: Optional[asyncio.Task] = None
        self._turn_gen = 0
        self._tts = None
        self._last_bridge: Optional[str] = None
        self._conversation_ready = False
        # Voice keeps working: at most one handed-off turn at a time (a new
        # turn waits for it), and results waiting for a quiet moment.
        self._detached_task: Optional[asyncio.Task] = None
        self._held_results: list = []   # [(conversation_id, answer, failed)]
        self._last_handoff: Optional[str] = None
        self._last_wait: Optional[str] = None
        # Session summary for the event ledger (one row at end()).
        from services.event_ledger import new_voice_stats
        self.stats = new_voice_stats()
        self.stats["handoffs"] = 0
        self._started_mono = _time.monotonic()
        self._summary_done = False

    # ---- sending -------------------------------------------------------------

    async def send_json(self, data: dict) -> bool:
        if self.closed:
            return False
        if data.get("type") == "error":
            self.stats["errors"] += 1
        try:
            await self.ws.send_text(json.dumps(data))
            return True
        except Exception as e:
            logger.debug("voice send failed (%s): %s", data.get("type"), e)
            self.closed = True
            return False

    async def send_audio(self, pcm: bytes) -> bool:
        if self.closed:
            return False
        try:
            await self.ws.send_bytes(PREFIX_TTS_AUDIO + pcm)
            return True
        except Exception:
            self.closed = True
            return False

    async def _set_state(self, state: str, reason: Optional[str] = None) -> None:
        self.state = state
        msg = {"type": "state", "state": state}
        if reason:
            msg["reason"] = reason
        await self.send_json(msg)
        if state == "speaking":
            display_signals.mark_voice_speaking()
        else:
            display_signals.mark_voice_listening()

    @property
    def turn_source(self) -> str:
        """``voice`` (the owner, the app) or ``hub_voice`` (the household hub)."""
        return TURN_SOURCE_BY_SURFACE[self.surface]

    async def _report_result(self, conversation_id: str, answer: str, failed: bool = False) -> bool:
        """With the session gone: a push for the owner's own voice, a note through
        ``reach_service`` for the hub's (``_leave_hub_result``)."""
        if self.surface == "hub":
            return await _leave_hub_result(conversation_id, answer, failed=failed)
        return await _push_handoff_result(conversation_id, answer, failed=failed)

    def _spend_scope(self):
        """Whose money a turn spends. Someone at the hub is not the owner:
        like a friend's ping, its turns run in Edward's own share, which
        stops at the daily cap and never reaches into the owner's reserve. The task a turn
        runs in copies the scope, so a handed-off continuation keeps it."""
        return llm_spend.own_work_scope() if self.surface == "hub" else nullcontext()

    # ---- session lifecycle ---------------------------------------------------

    async def start(self, requested_id: Optional[str], mode: str, sensitivity: Optional[str] = None,
                    surface: Optional[str] = None) -> None:
        await self.stop_turn(silent=True)
        await self._close_stt()

        self.mode = "ptt" if mode == "ptt" else "auto"
        self.sensitivity = normalize_sensitivity(sensitivity)
        self.surface = normalize_surface(surface)
        # The conversation row is created on the first committed transcript,
        # not here: opening voice mode and backing out must not leave an
        # empty "Voice conversation" in the sidebar.
        if requested_id and await conversation_exists(requested_id):
            self.conversation_id = requested_id
            self._conversation_ready = True
        else:
            self.conversation_id = requested_id or str(uuid.uuid4())
            self._conversation_ready = False

        self.active = True
        self.state = "listening"
        await self._open_stt()
        self._stt_task = asyncio.create_task(self._stt_loop())
        display_signals.mark_voice_listening()
        await self.send_json({
            "type": "session_started",
            "conversation_id": self.conversation_id,
            "mode": self.mode,
            "sensitivity": self.sensitivity,
            "surface": self.surface,
            "sample_rate_in": SCRIBE_SAMPLE_RATE,
            "sample_rate_out": TTS_SAMPLE_RATE,
        })

    async def end(self) -> None:
        # A handed-off turn is not cancelled here: it finishes on its own
        # and, with the session gone, reports back as a push (the hub's as
        # a note to the owner).
        self.active = False
        await self.stop_turn(silent=True)
        await self._close_stt()
        display_signals.mark_voice_idle()
        # A result that was waiting for a quiet moment will not get one now.
        held, self._held_results = self._held_results, []
        for conversation_id, answer, failed in held:
            await self._report_result(conversation_id, answer, failed=failed)
        self._record_summary()

    # ---- voice keeps working: handed-off turns ---------------------------------

    def session_open(self) -> bool:
        return self.active and not self.closed

    def _turn_busy(self) -> bool:
        """A turn or a spoken line owns the session's voice right now
        (the caller's own task does not count)."""
        task = self._turn_task
        return task is not None and not task.done() and task is not asyncio.current_task()

    def _voice_state(self) -> str:
        """The session state as a finished handed-off turn should see it."""
        if self._turn_busy():
            return "speaking" if self.state == "speaking" else "thinking"
        return self.state

    def detached_running(self) -> bool:
        task = self._detached_task
        return task is not None and not task.done()

    def _detach(self, task: Optional[asyncio.Task]) -> None:
        """Let a turn go: barge-in and end() cancel ``_turn_task`` only."""
        if task is None:
            return
        if self._turn_task is task:
            self._turn_task = None
        self._detached_task = task
        _DETACHED_TURNS.add(task)
        task.add_done_callback(_DETACHED_TURNS.discard)

    async def cancel_detached(self) -> bool:
        """Explicit stop: the handed-off turn and anything it left to say."""
        task, self._detached_task = self._detached_task, None
        self._held_results = []
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    def take_held(self) -> list:
        held, self._held_results = self._held_results, []
        return held

    def _say(self, text: str) -> None:
        """Start speaking one line outside an LLM turn, as the session's turn."""
        self._turn_gen += 1
        self._turn_task = asyncio.create_task(self._speak_line(text, self._turn_gen))

    async def _deliver_held(self) -> None:
        """Speak a held result now that the session has gone quiet."""
        if not self._held_results or not self.session_open() or self._turn_busy():
            return
        if self.state != "listening":
            return
        _conversation_id, answer, failed = self._held_results.pop(0)
        self._say(result_speech(answer, failed=failed))

    async def _speak_line(self, text: str, gen: int) -> None:
        """Say ``text`` like a one-sentence turn. Barge-in cancels it; the
        words are already in the thread, so nothing is lost."""
        try:
            await self._set_state("thinking")
            async with get_voice_service().open_tts_stream() as tts:
                if gen != self._turn_gen or self.closed:
                    return
                self._tts = tts
                pump = asyncio.create_task(self._pump_audio(tts, gen))
                await self.send_json({"type": "response_text", "text": text, "is_final": True})
                await tts.send_text(text, flush=True)
                await tts.finish()
                await pump
            if gen == self._turn_gen and not self.closed:
                self._tts = None
                await self._set_state("listening")
                await self.send_json({"type": "response_done"})
                await self._deliver_held()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("voice line failed: %s", e)
            if gen == self._turn_gen and not self.closed:
                await self._set_state("listening", reason="error")

    async def _close_handoff_line(self, pump: asyncio.Task, tts_ctx, tts_open: asyncio.Task, gen: int) -> None:
        """The handoff line plays out; then the session is the owner's again."""
        try:
            await pump
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            await _close_tts_ctx(tts_ctx, tts_open)
        if gen == self._turn_gen and not self.closed:
            self._tts = None
            await self._set_state("listening", reason="handoff")
            await self.send_json({"type": "response_done"})
        await self._deliver_held()

    async def _handoff_finished(self, conversation_id: str, answer: str, detail: dict, failed: bool = False) -> None:
        how = delivery_for(self.session_open(), self._voice_state())
        outcome = {"speak": "spoken", "hold": "held"}.get(how, "noted" if self.surface == "hub" else "pushed")
        if how == "speak":
            self._say(result_speech(answer, failed=failed))
        elif how == "hold":
            self._held_results.append((conversation_id, answer, failed))
        elif not await self._report_result(conversation_id, answer, failed=failed):
            outcome = "push_failed"
        print(f"[voice] handed-off turn finished ({outcome}, conversation={conversation_id})")
        _record_handoff(conversation_id, "failed" if failed else outcome, detail, source=self.turn_source)
        if self.session_open():
            # The view's "working in the background" pill ends here.
            try:
                await self.send_json({"type": "handoff_done", "outcome": "failed" if failed else outcome,
                                      "conversation_id": conversation_id})
            except Exception:  # noqa: BLE001 — a closed socket is fine
                pass

    def _record_summary(self) -> None:
        """One ledger row per session: what voice felt like."""
        if self._summary_done or not self.conversation_id:
            return
        self._summary_done = True
        try:
            from services import event_ledger

            event_ledger.record_event(
                kind="voice_session", source=self.sensitivity, outcome="ended", ref_id=self.conversation_id,
                detail={**self.stats, "mode": self.mode, "surface": self.surface,
                        "duration_s": int(_time.monotonic() - self._started_mono)},
            )
        except Exception:  # noqa: BLE001
            pass

    # ---- Scribe side ---------------------------------------------------------

    async def _open_stt(self) -> None:
        strategy = "manual" if self.mode == "ptt" else "vad"
        self._stt_ctx = get_voice_service().open_stt_stream(
            commit_strategy=strategy, **scribe_tuning(self.sensitivity)
        )
        self._stt = await self._stt_ctx.__aenter__()

    async def _close_stt(self) -> None:
        task, self._stt_task = self._stt_task, None
        ctx, self._stt_ctx, self._stt = self._stt_ctx, None, None
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _stt_loop(self) -> None:
        """Consume Scribe events; reopen the socket when the server ends a session."""
        reconnects = 0
        while self.active and not self.closed:
            stt = self._stt
            if stt is None:
                return
            fatal = False
            try:
                async for ev in stt.events():
                    if not self.active:
                        return
                    if ev.kind == "error" and not is_recoverable_error(ev.code) and ev.code != "connection_closed":
                        await self.send_json({"type": "error", "message": ev.text or ev.code, "code": ev.code})
                        fatal = True
                        break
                    await self._on_scribe_event(ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Scribe reader error: %s", e)

            if fatal or not self.active or self.closed:
                return
            reconnects += 1
            if reconnects > MAX_STT_RECONNECTS:
                await self.send_json({"type": "error", "message": "Transcription connection lost", "code": "stt_unavailable"})
                return
            try:
                ctx, self._stt_ctx, self._stt = self._stt_ctx, None, None
                if ctx is not None:
                    await ctx.__aexit__(None, None, None)
                await asyncio.sleep(min(0.25 * reconnects, 2.0))
                await self._open_stt()
            except Exception as e:
                logger.warning("Scribe reconnect failed: %s", e)
                await self.send_json({"type": "error", "message": f"Transcription reconnect failed: {e}", "code": "stt_unavailable"})
                return

    async def _on_scribe_event(self, ev: ScribeEvent) -> None:
        if ev.kind == "partial":
            new_state, action = decide(
                self.state, "partial", ev.text,
                barge_in_min_words=barge_in_min_words(self.sensitivity),
            )
            if action == "barge_in":
                self.stats["barge_ins"] += 1
                await self.stop_turn(reason="barge_in")
            if ev.text:
                await self.send_json({"type": "partial_transcript", "text": ev.text})
        elif ev.kind == "committed":
            new_state, action = decide(self.state, "committed", ev.text)
            if action == "ignore":
                return
            if self.detached_running() and is_stop_phrase(ev.text):
                # "Never mind" while a handed-off turn runs is a command,
                # not a question: stop the work, say so, no LLM turn.
                self.stats["interrupts"] += 1
                await self.send_json({"type": "transcript", "text": ev.text})
                await self.stop_turn(silent=True)
                await self.cancel_detached()
                self._say(STOPPED_LINE)
                return
            if action == "restart_turn":
                self.stats["restarts"] += 1
                await self.stop_turn(silent=True)
            self.stats["turns"] += 1
            await self.send_json({"type": "transcript", "text": ev.text})
            await self._set_state("thinking")
            self._turn_gen += 1
            with self._spend_scope():
                self._turn_task = asyncio.create_task(self._run_turn(ev.text, self._turn_gen))
        # session_started / other: nothing to relay

    async def on_mic(self, pcm: bytes) -> None:
        stt = self._stt
        if stt is not None and pcm:
            try:
                await stt.send_audio(pcm)
            except Exception as e:
                logger.debug("mic relay failed: %s", e)

    async def on_commit(self) -> None:
        stt = self._stt
        if stt is not None:
            await stt.commit()

    # ---- turns ---------------------------------------------------------------

    async def stop_turn(self, reason: Optional[str] = None, silent: bool = False) -> None:
        task, self._turn_task = self._turn_task, None
        self._turn_gen += 1
        tts, self._tts = self._tts, None
        if tts is not None:
            try:
                await tts.close()
            except Exception:
                pass
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if not silent:
            await self._set_state("listening", reason=reason)
        else:
            self.state = "listening"

    async def _run_turn(self, transcript: str, gen: int) -> None:
        me = asyncio.current_task()
        register = unregister = None
        try:
            from services.heartbeat.heartbeat_service import register_active_chat, unregister_active_chat
            register, unregister = register_active_chat, unregister_active_chat
        except Exception:
            pass
        try:
            from services.graph.tools import set_current_conversation_id
            set_current_conversation_id(self.conversation_id)
        except Exception:
            pass

        conversation_id = self.conversation_id
        loop = asyncio.get_running_loop()
        started = loop.time()
        # Voice keeps working: once handed off, this turn no longer owns the
        # session's voice. It finishes on its own and reports back.
        handed = {"off": False}
        progress = {"tool_calls": 0, "phase": PHASE_THINKING}
        answer_parts: list = []  # the answer is what streams after the last tool call

        def handoff_detail() -> dict:
            return {"tool_calls": progress["tool_calls"], "elapsed_s": round(loop.time() - started, 1)}

        if register:
            register(conversation_id)
        try:
            if not self._conversation_ready:
                title = transcript.strip()[:50] or "Voice conversation"
                await create_conversation(conversation_id, title, channel="voice")
                self._conversation_ready = True
            else:
                await increment_message_count(conversation_id)
            voice = get_voice_service()
            # Open the TTS socket while settings/graph load and the LLM
            # starts, so the ElevenLabs handshake (~250 ms) overlaps
            # instead of preceding the turn.
            tts_ctx = voice.open_tts_stream()
            tts_open = asyncio.create_task(tts_ctx.__aenter__())

            async def get_tts():
                tts = await tts_open
                self._tts = tts
                return tts

            try:
                settings = await get_settings()
                graph = await get_graph()
                marks = {"first_token": False, "bridge_spoken": False}
                tts_lock = asyncio.Lock()

                async def pump_when_ready():
                    await self._pump_audio(await get_tts(), gen)

                pump = asyncio.create_task(pump_when_ready())

                async def bridge_watch():
                    await asyncio.sleep(BRIDGE_THRESHOLD_S)
                    async with tts_lock:
                        if bridge_due(
                            elapsed_s=loop.time() - started,
                            first_token_seen=marks["first_token"],
                            already_spoken=marks["bridge_spoken"],
                            threshold_s=BRIDGE_THRESHOLD_S,
                        ):
                            line = pick_bridge(random, last=self._last_bridge)
                            self._last_bridge = line
                            marks["bridge_spoken"] = True
                            self.stats["bridges"] += 1
                            await self.send_json({"type": "bridge", "text": line})
                            await (await get_tts()).send_text(line, flush=True)

                bridge_task = asyncio.create_task(bridge_watch())

                async def speak(sentence: str, is_final: bool) -> None:
                    async with tts_lock:
                        marks["first_token"] = True
                        await self.send_json({"type": "response_text", "text": sentence, "is_final": is_final})
                        await (await get_tts()).send_text(sentence, flush=True)

                async def hand_off() -> None:
                    """Say so, and let this turn finish on its own. Caller holds tts_lock."""
                    handed["off"] = True
                    marks["bridge_spoken"] = True
                    progress["phase"] = PHASE_HANDED_OFF
                    self.stats["handoffs"] += 1
                    line = pick_handoff_line(random, last=self._last_handoff)
                    self._last_handoff = line
                    self._detach(me)
                    print(
                        f"[voice] handing off a spoken turn after {progress['tool_calls']} tool calls, "
                        f"{loop.time() - started:.1f}s (conversation={conversation_id})"
                    )
                    try:
                        await self.send_json({"type": "handoff", "text": line, "conversation_id": conversation_id})
                        await self.send_json({"type": "response_text", "text": line, "is_final": True})
                        tts = await get_tts()
                        await tts.send_text(line, flush=True)
                        await tts.finish()
                    except Exception as e:
                        logger.warning("voice handoff line failed: %s", e)
                        pump.cancel()
                    finally:
                        # The line plays out in the session's turn slot, so a
                        # barge-in cuts the line and not the work. A turn that
                        # started meanwhile keeps the slot.
                        closer = asyncio.create_task(self._close_handoff_line(pump, tts_ctx, tts_open, gen))
                        if gen == self._turn_gen and self._turn_task is None:
                            self._turn_task = closer

                async def maybe_hand_off() -> None:
                    async with tts_lock:
                        if handed["off"] or gen != self._turn_gen or self.closed:
                            return
                        if should_hand_off(
                            loop.time() - started, progress["tool_calls"], progress["phase"],
                            seconds=VOICE_HANDOFF_SECONDS, max_tool_calls=VOICE_HANDOFF_TOOL_CALLS,
                        ):
                            await hand_off()

                async def handoff_watch():
                    # A single tool can hold the stream for a minute, so the
                    # clock is watched from outside it.
                    await asyncio.sleep(VOICE_HANDOFF_SECONDS)
                    await maybe_hand_off()

                detached = self._detached_task
                if detached is not None and detached is not me and not detached.done():
                    # Two turns writing one checkpoint at once would lose one
                    # of them: this question waits for the work already
                    # asked for, and says so.
                    async with tts_lock:
                        line = pick_wait_line(random, last=self._last_wait)
                        self._last_wait = line
                        marks["bridge_spoken"] = True
                        await self.send_json({"type": "bridge", "text": line})
                        await (await get_tts()).send_text(line, flush=True)
                    await asyncio.wait({detached})
                    if gen != self._turn_gen or self.closed:
                        bridge_task.cancel()
                        return
                    started = loop.time()  # the wait is not this turn's work
                # A result that landed while someone was talking is said first.
                for _cid, answer, failed in self.take_held():
                    await speak(result_speech(answer, failed=failed), False)

                handoff_task = asyncio.create_task(handoff_watch())

                feed = SentenceFeed()
                try:
                    async for event in stream_with_memory_events(
                        turn_source=self.turn_source,
                        message=transcript,
                        conversation_id=conversation_id,
                        system_prompt=voice_system_prompt(settings.system_prompt),
                        model=VOICE_MODEL,
                        temperature=settings.temperature,
                        graph=graph,
                        provider=settings.provider,
                        fast_context=True,
                    ):
                        if not handed["off"] and (gen != self._turn_gen or self.closed):
                            return
                        etype = event.get("type")
                        if etype == "content":
                            text = event.get("content", "")
                            answer_parts.append(text)
                            if handed["off"]:
                                continue
                            if text.strip():
                                progress["phase"] = PHASE_ANSWERING
                            for sentence in feed.push(text):
                                await speak(sentence, False)
                        elif etype == "tool_start":
                            progress["tool_calls"] += 1
                            answer_parts.clear()
                            if handed["off"]:
                                continue
                            progress["phase"] = PHASE_TOOLS
                            name = event.get("tool_name", "unknown")
                            await self.send_json({"type": "tool_activity", "tool_name": name, "status": "started", "message": f"Running {name}..."})
                            await maybe_hand_off()
                        elif handed["off"]:
                            # Detached: nothing more is relayed to a session
                            # that has moved on; the thread has it all.
                            if etype == "done":
                                break
                        elif etype == "tool_end":
                            name = event.get("tool_name", "unknown")
                            await self.send_json({"type": "tool_activity", "tool_name": name, "status": "completed", "message": f"Completed {name}"})
                        elif etype == "progress" and event.get("step") == "tool_execution":
                            await self.send_json({
                                "type": "tool_activity",
                                "tool_name": event.get("tool_name", "unknown"),
                                "status": event.get("status", "started"),
                                "message": event.get("message", ""),
                            })
                        elif etype == "thinking":
                            await self.send_json({"type": "tool_activity", "tool_name": "thinking", "status": "started", "message": "Thinking..."})
                        elif etype == "done":
                            rest = feed.flush()
                            if rest:
                                await speak(rest, True)
                            break
                finally:
                    bridge_task.cancel()
                    if not handed["off"]:
                        handoff_task.cancel()

                if handed["off"]:
                    await self._handoff_finished(conversation_id, "".join(answer_parts), handoff_detail())
                    return
                if gen != self._turn_gen or self.closed:
                    return
                if not marks["first_token"] and not marks["bridge_spoken"]:
                    # Model produced nothing speakable (tool-only turn or empty).
                    pump.cancel()
                else:
                    tts = await get_tts()
                    await tts.finish()
                    await pump
            finally:
                # After a handoff the line's closer owns the TTS socket.
                if not handed["off"]:
                    await _close_tts_ctx(tts_ctx, tts_open)

            if gen == self._turn_gen and not self.closed:
                self._tts = None
                await self._set_state("listening")
                await self.send_json({"type": "response_done"})
                await self._deliver_held()

        except asyncio.CancelledError:
            logger.info("Voice turn cancelled (conversation=%s)", conversation_id)
            if handed["off"]:
                _record_handoff(conversation_id, "cancelled", handoff_detail(), source=self.turn_source)
            raise
        except Exception as e:
            logger.exception("Voice turn failed")
            if handed["off"]:
                await self._handoff_finished(conversation_id, "", handoff_detail(), failed=True)
            else:
                await self.send_json({"type": "error", "message": f"Processing error: {e}"})
                if gen == self._turn_gen and not self.closed:
                    await self._set_state("listening", reason="error")
        finally:
            if unregister:
                unregister(conversation_id)

    async def _pump_audio(self, tts, gen: int) -> None:
        try:
            async for chunk in tts.audio():
                if gen != self._turn_gen or self.closed:
                    return
                if self.state == "thinking":
                    await self._set_state("speaking")
                if not await self.send_audio(chunk):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("TTS pump error: %s", e)


@router.websocket("/voice/ws")
async def voice_websocket(websocket: WebSocket):
    """Live voice conversation. Auth is checked here because WebSocket
    upgrades bypass the HTTP auth middleware. A valid session is needed
    with or without a password set: before setup the API answers nothing
    but the first-run routes (middleware/auth.py), and an open socket would
    run owner turns for anyone on the LAN."""
    await websocket.accept()

    token = websocket.cookies.get("edward_token")
    if not token or not auth_service.verify_token(token):
        await websocket.close(code=4001, reason="Authentication required")
        return

    if not await is_skill_enabled("voice"):
        await websocket.close(code=4003, reason="Voice skill is not enabled")
        return

    session = VoiceSession(websocket)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                raw = message["bytes"]
                if raw[0:1] == PREFIX_MIC_AUDIO and session.active:
                    await session.on_mic(raw[1:])
                continue

            if message.get("text") is None:
                continue
            try:
                data = json.loads(message["text"])
            except json.JSONDecodeError:
                await session.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = data.get("type")
            if msg_type == "start_session":
                try:
                    await session.start(
                        data.get("conversation_id"),
                        data.get("mode") or "auto",
                        data.get("sensitivity"),
                        data.get("surface"),
                    )
                except Exception as e:
                    logger.exception("Voice session start failed")
                    await session.send_json({"type": "error", "message": f"Could not start voice session: {e}", "code": "start_failed"})
            elif msg_type == "commit":
                if session.active:
                    await session.on_commit()
            elif msg_type == "interrupt":
                if session.active:
                    session.stats["interrupts"] += 1
                    await session.stop_turn(reason="interrupt")
                    # The stop button means everything, handed-off work too.
                    await session.cancel_detached()
            elif msg_type == "end_session":
                await session.end()
                break
            elif msg_type == "ping":
                await session.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected (conversation=%s)", session.conversation_id)
    except Exception as e:
        logger.exception("Voice WebSocket error")
        await session.send_json({"type": "error", "message": str(e)})
    finally:
        session.closed = True
        await session.end()
