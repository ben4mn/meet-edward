"""The live voice session: browser mic PCM in, Scribe events, LLM turn,
PCM out, bridges and barge-in. ElevenLabs and the LLM are fakes; the
FastAPI WebSocket plumbing is real.

Fake Scribe convention: a mic frame whose payload is ``P:<text>`` makes it
emit a partial transcript, ``C:<text>`` a committed one. That lets the test
drive the whole session from the client side of the socket.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.voice.scribe_protocol import ScribeEvent
from routers import voice_ws


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------

class FakeScribe:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.commits = 0
        self.audio = []
        self.closed = False

    async def send_audio(self, pcm: bytes):
        self.audio.append(pcm)
        txt = pcm.decode(errors="ignore")
        if txt.startswith("P:"):
            await self.q.put(ScribeEvent(kind="partial", text=txt[2:]))
        elif txt.startswith("C:"):
            await self.q.put(ScribeEvent(kind="committed", text=txt[2:]))

    async def commit(self):
        self.commits += 1

    async def events(self):
        yield ScribeEvent(kind="session_started")
        while True:
            ev = await self.q.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self.closed = True
        await self.q.put(None)


class FakeTTS:
    instances = []

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.texts = []
        self.closed = False
        FakeTTS.instances.append(self)

    async def send_text(self, text, flush=False):
        self.texts.append(text)
        await self.q.put(b"A:" + text.encode())

    async def finish(self):
        await self.q.put(None)

    async def audio(self):
        while True:
            chunk = await self.q.get()
            if chunk is None:
                return
            yield chunk

    async def close(self):
        self.closed = True
        await self.q.put(None)


class _Ctx:
    def __init__(self, stream):
        self.stream = stream

    async def __aenter__(self):
        return self.stream

    async def __aexit__(self, *exc):
        await self.stream.close()


class FakeVoiceService:
    def __init__(self):
        self.scribe = FakeScribe()
        self.commit_strategies = []
        self.tunings = []
        self.tts_models = []

    def open_stt_stream(self, commit_strategy="vad", language="en", **tuning):
        self.commit_strategies.append(commit_strategy)
        self.tunings.append(tuning)
        return _Ctx(self.scribe)

    def open_tts_stream(self, model_id=None):
        self.tts_models.append(model_id)
        return _Ctx(FakeTTS())


def make_fake_llm(script, calls):
    async def fake_stream(message, conversation_id, system_prompt, model, temperature, graph,
                          attachments=None, provider="anthropic", debug_streaming=False,
                          fast_context=False, **kw):
        from services import llm_spend

        call = dict(message=message, conversation_id=conversation_id, model=model,
                    system_prompt=system_prompt, fast_context=fast_context, provider=provider,
                    turn_source=kw.get("turn_source"), own_work=llm_spend.in_own_work())
        calls.append(call)
        for kind, val in script:
            if kind == "sleep":
                await asyncio.sleep(val)
            elif kind == "content":
                yield {"type": "content", "content": val}
            elif kind == "tool_start":
                yield {"type": "tool_start", "tool_name": val}
            elif kind == "tool_end":
                yield {"type": "tool_end", "tool_name": val, "result": ""}
        # Whose spend the turn's last call was, after any handoff.
        call["own_work_at_end"] = llm_spend.in_own_work()
        yield {"type": "done"}
    return fake_stream


# --------------------------------------------------------------------------
# fixture: an app with everything but the socket faked
# --------------------------------------------------------------------------

@pytest.fixture
def harness(monkeypatch):
    FakeTTS.instances.clear()
    svc = FakeVoiceService()
    calls = []
    created = []
    script = [("content", "It's two twenty. "), ("content", "Sunny out.")]
    state = {"script": script, "configured": False, "skill": True}

    monkeypatch.setattr(voice_ws, "get_voice_service", lambda: svc)
    monkeypatch.setattr(voice_ws, "stream_with_memory_events",
                        lambda *a, **k: make_fake_llm(state["script"], calls)(*a, **k))
    monkeypatch.setattr(voice_ws.auth_service, "is_password_configured_cached", lambda: state["configured"])
    monkeypatch.setattr(voice_ws.auth_service, "verify_token", lambda t: t == "good")

    async def _skill(name):
        return state["skill"]

    async def _exists(cid):
        return cid == "existing-conv"

    async def _create(cid, title, **kw):
        created.append((cid, title, kw))

    async def _inc(cid):
        pass

    async def _settings():
        return SimpleNamespace(system_prompt="You are Edward.", model="gpt-6-astra",
                               temperature=0.7, provider="openai")

    async def _graph():
        return object()

    monkeypatch.setattr(voice_ws, "is_skill_enabled", _skill)
    monkeypatch.setattr(voice_ws, "conversation_exists", _exists)
    monkeypatch.setattr(voice_ws, "create_conversation", _create)
    monkeypatch.setattr(voice_ws, "increment_message_count", _inc)
    monkeypatch.setattr(voice_ws, "get_settings", _settings)
    monkeypatch.setattr(voice_ws, "get_graph", _graph)
    monkeypatch.setattr(voice_ws, "BRIDGE_THRESHOLD_S", 30.0)

    app = FastAPI()
    app.include_router(voice_ws.router, prefix="/api")
    # Signed in: the socket needs a session even before a password is set.
    return SimpleNamespace(app=app, client=TestClient(app, cookies={"edward_token": "good"}), svc=svc, calls=calls,
                           created=created, state=state)


def _recv(ws):
    """Return ("json", obj) or ("bytes", payload)."""
    raw = ws.receive()
    if raw.get("type") == "websocket.close":
        raise WebSocketDisconnect(raw.get("code", 1000))
    if raw.get("bytes") is not None:
        return "bytes", raw["bytes"]
    return "json", json.loads(raw["text"])


def _drain_until(ws, pred, limit=60):
    got = []
    for _ in range(limit):
        kind, payload = _recv(ws)
        got.append((kind, payload))
        if pred(kind, payload):
            return got
        if kind == "json" and payload.get("type") == "error":
            raise AssertionError(f"server error: {payload}")
    raise AssertionError(f"predicate never satisfied; got {got}")


def _is(kind_name):
    return lambda k, p: k == "json" and p.get("type") == kind_name


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def test_password_configured_and_no_cookie_closes_4001(harness):
    harness.state["configured"] = True
    harness.client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as exc:
        with harness.client.websocket_connect("/api/voice/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4001


def test_no_password_yet_and_no_cookie_closes_4001(harness):
    """Before setup the socket used to be open to anyone on the LAN."""
    harness.state["configured"] = False
    harness.client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as exc:
        with harness.client.websocket_connect("/api/voice/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4001
    assert harness.calls == []


def test_a_bad_cookie_closes_4001(harness):
    harness.client.cookies.set("edward_token", "forged")
    with pytest.raises(WebSocketDisconnect) as exc:
        with harness.client.websocket_connect("/api/voice/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4001


def test_voice_skill_disabled_closes_4003(harness):
    harness.state["skill"] = False
    with pytest.raises(WebSocketDisconnect) as exc:
        with harness.client.websocket_connect("/api/voice/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4003


# --------------------------------------------------------------------------
# a full turn
# --------------------------------------------------------------------------

def test_full_turn_streams_transcript_text_and_pcm(harness):
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        got = _drain_until(ws, _is("session_started"))
        started = got[-1][1]
        assert started["conversation_id"]
        assert started["mode"] == "auto"
        assert started["sample_rate_in"] == 16000
        assert started["sample_rate_out"] == 24000

        ws.send_bytes(b"\x01" + b"P:what time")
        got = _drain_until(ws, _is("partial_transcript"))
        assert got[-1][1]["text"] == "what time"

        ws.send_bytes(b"\x01" + b"C:what time is it")
        got = _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})

    types = [p.get("type") for k, p in got if k == "json"]
    assert "transcript" in types
    assert types.index("transcript") < types.index("response_text")
    states = [p["state"] for k, p in got if k == "json" and p.get("type") == "state"]
    assert states[:2] == ["thinking", "speaking"]
    assert states[-1] == "listening"

    audio = [p for k, p in got if k == "bytes"]
    assert audio and all(a[0:1] == b"\x02" for a in audio)
    assert b"".join(a[1:] for a in audio) == b"A:It's two twenty.A:Sunny out."

    texts = [p["text"] for k, p in got if k == "json" and p.get("type") == "response_text"]
    assert texts == ["It's two twenty.", "Sunny out."]

    # The LLM got the spoken-style prompt, the fast tier, and the fast context plan.
    call = harness.calls[0]
    assert call["message"] == "what time is it"
    assert call["model"] == "gpt-5.6-luna"
    assert "VOICE MODE" in call["system_prompt"]
    assert call["fast_context"] is True
    # New conversation was created on the voice channel.
    assert harness.created and harness.created[0][2].get("channel") == "voice"
    # Mic frames reached Scribe without their prefix byte.
    assert harness.svc.scribe.audio[0] == b"P:what time"


def test_existing_conversation_is_continued_not_recreated(harness):
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "conversation_id": "existing-conv"})
        got = _drain_until(ws, _is("session_started"))
        assert got[-1][1]["conversation_id"] == "existing-conv"
        ws.send_json({"type": "end_session"})
    assert harness.created == []


def test_tool_activity_is_relayed(harness):
    harness.state["script"] = [("tool_start", "web_search"), ("tool_end", "web_search"), ("content", "Found it.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:look it up")
        got = _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})
    acts = [p for k, p in got if k == "json" and p.get("type") == "tool_activity"]
    assert [a["status"] for a in acts] == ["started", "completed"]
    assert acts[0]["tool_name"] == "web_search"


# --------------------------------------------------------------------------
# bridges
# --------------------------------------------------------------------------

def test_slow_first_token_gets_a_spoken_bridge(harness, monkeypatch):
    monkeypatch.setattr(voice_ws, "BRIDGE_THRESHOLD_S", 0.0)
    harness.state["script"] = [("sleep", 0.3), ("content", "Here you go.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:something hard")
        got = _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})
    types = [p.get("type") for k, p in got if k == "json"]
    assert "bridge" in types
    assert types.index("bridge") < types.index("response_text")
    bridge_text = [p["text"] for k, p in got if k == "json" and p.get("type") == "bridge"][0]
    from services.voice.bridges import BRIDGES
    assert bridge_text in BRIDGES
    # The bridge was actually spoken, before the answer.
    spoken = [t for inst in FakeTTS.instances for t in inst.texts]
    assert spoken[0] == bridge_text


def test_fast_first_token_gets_no_bridge(harness):
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:quick one")
        got = _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})
    assert not any(k == "json" and p.get("type") == "bridge" for k, p in got)


# --------------------------------------------------------------------------
# barge-in and push-to-talk
# --------------------------------------------------------------------------

def test_speaking_over_edward_cancels_the_turn(harness):
    harness.state["script"] = [("content", "First sentence. "), ("sleep", 1.5), ("content", "Second one.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:tell me a story")
        _drain_until(ws, lambda k, p: k == "bytes")
        ws.send_bytes(b"\x01" + b"P:stop right there")
        got = _drain_until(ws, lambda k, p: k == "json" and p.get("type") == "state" and p.get("state") == "listening")
        assert got[-1][1].get("reason") == "barge_in"
        ws.send_json({"type": "end_session"})
    assert FakeTTS.instances[-1].closed is True
    assert not any(k == "json" and p.get("type") == "response_done" for k, p in got)


def test_single_word_while_speaking_is_not_a_barge_in(harness):
    harness.state["script"] = [("content", "First sentence. "), ("sleep", 0.4), ("content", "Second one.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:tell me a story")
        _drain_until(ws, lambda k, p: k == "bytes")
        ws.send_bytes(b"\x01" + b"P:um")
        got = _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})
    assert not any(k == "json" and p.get("type") == "state" and p.get("reason") == "barge_in" for k, p in got)


def test_push_to_talk_uses_manual_commits(harness):
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "mode": "ptt"})
        got = _drain_until(ws, _is("session_started"))
        assert got[-1][1]["mode"] == "ptt"
        ws.send_bytes(b"\x01" + b"\x00\x00\x00\x00")
        ws.send_json({"type": "commit"})
        # Give the relay a moment, then close.
        ws.send_json({"type": "end_session"})
    assert harness.svc.commit_strategies == ["manual"]
    assert harness.svc.scribe.commits == 1


def test_interrupt_message_returns_to_listening(harness):
    harness.state["script"] = [("content", "First sentence. "), ("sleep", 1.5), ("content", "Second one.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:go on")
        _drain_until(ws, lambda k, p: k == "bytes")
        ws.send_json({"type": "interrupt"})
        got = _drain_until(ws, lambda k, p: k == "json" and p.get("type") == "state" and p.get("state") == "listening")
        assert got[-1][1].get("reason") == "interrupt"
        ws.send_json({"type": "end_session"})


def test_opening_and_closing_without_speaking_creates_no_conversation(harness):
    # Opening voice mode and backing out used to leave an empty "Voice
    # conversation" in the sidebar (and React's dev double-mount left two).
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        got = _drain_until(ws, _is("session_started"))
        assert got[-1][1]["conversation_id"]
        ws.send_json({"type": "end_session"})
    assert harness.created == []


# --------------------------------------------------------------------------
# sensitivity: a noisy room tunes Scribe and raises the barge-in bar
# --------------------------------------------------------------------------

def test_noisy_room_sensitivity_tunes_scribe_and_barge_in(harness):
    harness.state["script"] = [("sleep", 0.05), ("content", "One. "), ("sleep", 0.3), ("content", "Two.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "sensitivity": "low"})
        got = _drain_until(ws, _is("session_started"))
        assert got[-1][1]["sensitivity"] == "low"
        assert harness.svc.tunings[-1]["vad_threshold"] > 0.5
        assert harness.svc.tunings[-1]["min_speech_duration_ms"] >= 200

        ws.send_bytes(b"\x01" + b"C:what time is it")
        _drain_until(ws, lambda k, p: k == "bytes")  # Edward is speaking
        # Two words of chatter would have cut him off at "normal"; not here.
        ws.send_bytes(b"\x01" + b"P:stop now")
        got = _drain_until(ws, _is("response_done"))
        reasons = [p.get("reason") for k, p in got if k == "json" and p.get("type") == "state"]
        assert "barge_in" not in reasons


def test_default_sensitivity_leaves_scribe_defaults(harness):
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        got = _drain_until(ws, _is("session_started"))
        assert got[-1][1]["sensitivity"] == "normal"
        assert harness.svc.tunings[-1] == {}


def test_session_end_records_a_voice_session_summary(harness, monkeypatch):
    """One ledger row per voice session: turns, bridges, barge-ins,
    interrupts, noise restarts, errors, duration — what a health check
    reads to judge how voice felt."""
    from services import event_ledger

    recorded = []
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: recorded.append(kw))

    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "sensitivity": "low"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:what time is it")
        _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})

    assert len(recorded) == 1, recorded
    ev = recorded[0]
    assert ev["kind"] == "voice_session" and ev["source"] == "low" and ev["outcome"] == "ended"
    assert ev["detail"]["turns"] == 1 and ev["detail"]["mode"] == "auto"
    assert ev["detail"]["duration_s"] >= 0


# --------------------------------------------------------------------------
# voice keeps working: a tool-heavy spoken turn hands off and reports back
# (plan Phase 5, 2026-09-24)
# --------------------------------------------------------------------------

import time as _time

from services.voice.handoff import HANDOFF_LINES, STOPPED_LINE, WAIT_LINES

THREE_TOOLS = [
    ("tool_start", "web_search"), ("tool_end", "web_search"),
    ("tool_start", "fetch_page_content"), ("tool_end", "fetch_page_content"),
    ("tool_start", "save_document"),
]


def _capture_pushes(monkeypatch):
    from services import push_service

    pushes = []

    async def _send(**kw):
        pushes.append(kw)
        return {"sent": 1, "failed": 0, "total": 1}

    monkeypatch.setattr(push_service, "is_configured", lambda: True)
    monkeypatch.setattr(push_service, "send_push_notification", _send)
    return pushes


def _capture_ledger(monkeypatch):
    from services import event_ledger

    rows = []
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: rows.append(kw))
    return rows


def _wait_for(pred, timeout=4.0):
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if pred():
            return True
        _time.sleep(0.02)
    return False


def _texts(got, kind="response_text"):
    return [p["text"] for k, p in got if k == "json" and p.get("type") == kind]


def _said(text_part):
    return lambda k, p: k == "json" and p.get("type") == "response_text" and text_part in p.get("text", "")


def _state(reason):
    return lambda k, p: k == "json" and p.get("type") == "state" and p.get("reason") == reason


def test_third_tool_call_hands_off_and_the_result_is_spoken_when_idle(harness, monkeypatch):
    pushes = _capture_pushes(monkeypatch)
    ledger = _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.4), ("tool_end", "save_document"),
        ("content", "I saved the recipe to your documents. It serves four. Want a shopping list?"),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:find a lasagne recipe and save it")
        got = _drain_until(ws, _is("handoff"))
        line = got[-1][1]["text"]
        assert line in HANDOFF_LINES
        got = _drain_until(ws, _state("handoff"))
        # The session is Ellis's again while the work carries on.
        assert got[-1][1]["state"] == "listening"
        got = _drain_until(ws, _said("that's done"))
        seen = list(got)
        assert got[-1][1]["text"] == (
            "Okay, that's done. I saved the recipe to your documents. It serves four."
        )
        seen += _drain_until(ws, _is("response_done"))
        if not any(k == "json" and p.get("type") == "handoff_done" for k, p in seen):
            seen += _drain_until(ws, _is("handoff_done"))
        done = [p for k, p in seen if k == "json" and p.get("type") == "handoff_done"]
        assert done and done[0]["outcome"] == "spoken", "the view learns the background work ended"
        ws.send_json({"type": "end_session"})

    spoken = [t for inst in FakeTTS.instances for t in inst.texts]
    assert line in spoken
    assert "Okay, that's done. I saved the recipe to your documents. It serves four." in spoken
    assert pushes == []
    handoffs = [r for r in ledger if r["kind"] == "voice_handoff"]
    assert len(handoffs) == 1 and handoffs[0]["outcome"] == "spoken"
    assert handoffs[0]["detail"]["tool_calls"] == 3
    session = [r for r in ledger if r["kind"] == "voice_session"][0]
    assert session["detail"]["handoffs"] == 1


def test_still_running_tools_after_the_threshold_hands_off(harness, monkeypatch):
    _capture_pushes(monkeypatch)
    _capture_ledger(monkeypatch)
    monkeypatch.setattr(voice_ws, "VOICE_HANDOFF_SECONDS", 0.2)
    harness.state["script"] = [
        ("tool_start", "browser_open"), ("sleep", 0.7), ("tool_end", "browser_open"),
        ("content", "The page says it's sold out."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:are the tickets still on sale")
        got = _drain_until(ws, _is("handoff"))
        assert got[-1][1]["text"] in HANDOFF_LINES
        got = _drain_until(ws, _said("sold out"))
        assert got[-1][1]["text"] == "Okay, that's done. The page says it's sold out."
        ws.send_json({"type": "end_session"})


def test_a_short_tool_turn_is_answered_live_not_handed_off(harness):
    harness.state["script"] = [("tool_start", "web_search"), ("tool_end", "web_search"), ("content", "Found it.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:look it up")
        got = _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})
    assert not any(k == "json" and p.get("type") == "handoff" for k, p in got)
    assert _texts(got) == ["Found it."]


def test_barge_in_after_the_handoff_does_not_cancel_the_work(harness, monkeypatch):
    _capture_pushes(monkeypatch)
    _capture_ledger(monkeypatch)

    # Hold the handoff line's audio open long enough to talk over it.
    async def slow_finish(self):
        await asyncio.sleep(0.4)
        await self.q.put(None)

    monkeypatch.setattr(FakeTTS, "finish", slow_finish)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 1.0), ("tool_end", "save_document"), ("content", "All booked."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:book the three flights")
        _drain_until(ws, _is("handoff"))
        _drain_until(ws, lambda k, p: k == "bytes")  # the handoff line is playing
        ws.send_bytes(b"\x01" + b"P:hang on a second")
        got = _drain_until(ws, lambda k, p: k == "json" and p.get("type") == "state" and p.get("state") == "listening")
        assert got[-1][1].get("reason") == "barge_in"
        got = _drain_until(ws, _said("All booked"))
        assert got[-1][1]["text"] == "Okay, that's done. All booked."
        ws.send_json({"type": "end_session"})


def test_the_result_is_pushed_when_the_session_has_ended(harness, monkeypatch):
    pushes = _capture_pushes(monkeypatch)
    _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.5), ("tool_end", "save_document"),
        ("content", "All three are booked. Details are in the chat. More text."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        cid = _drain_until(ws, _is("session_started"))[-1][1]["conversation_id"]
        ws.send_bytes(b"\x01" + b"C:book the three flights")
        _drain_until(ws, _is("handoff"))
        ws.send_json({"type": "end_session"})
        # Ending the session must not cancel the work; the push lands after.
        assert _wait_for(lambda: pushes), "no push after the session ended"

    push = pushes[0]
    assert push["source"] == "voice_handoff"
    assert push["url"] == f"/chat?c={cid}"
    assert push["conversation_id"] == cid
    assert push["body"] == "All three are booked. Details are in the chat."


def test_interrupt_still_cancels_a_handed_off_turn(harness, monkeypatch):
    pushes = _capture_pushes(monkeypatch)
    ledger = _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.8), ("tool_end", "save_document"), ("content", "Should never be heard."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:book the three flights")
        _drain_until(ws, _is("handoff"))
        _drain_until(ws, _state("handoff"))
        ws.send_json({"type": "interrupt"})
        _drain_until(ws, _state("interrupt"))
        _time.sleep(1.2)
        ws.send_json({"type": "ping"})
        got = _drain_until(ws, _is("pong"))
        ws.send_json({"type": "end_session"})
        _time.sleep(0.3)
    assert not any("Should never" in t for t in _texts(got))
    assert pushes == []
    handoffs = [r for r in ledger if r["kind"] == "voice_handoff"]
    assert [r["outcome"] for r in handoffs] == ["cancelled"]


def test_saying_stop_during_a_handed_off_turn_cancels_it(harness, monkeypatch):
    pushes = _capture_pushes(monkeypatch)
    _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.8), ("tool_end", "save_document"), ("content", "Should never be heard."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:book the three flights")
        _drain_until(ws, _is("handoff"))
        _drain_until(ws, _state("handoff"))
        ws.send_bytes(b"\x01" + b"C:never mind")
        got = _drain_until(ws, _said(STOPPED_LINE))
        _time.sleep(1.0)
        ws.send_json({"type": "ping"})
        got += _drain_until(ws, _is("pong"))
        ws.send_json({"type": "end_session"})
        _time.sleep(0.3)
    assert not any("Should never" in t for t in _texts(got))
    # "never mind" was a command, not a question: no LLM turn for it.
    assert [c["message"] for c in harness.calls] == ["book the three flights"]
    assert pushes == []


def test_a_new_question_waits_for_the_handed_off_turn(harness, monkeypatch):
    """Two turns writing one conversation's checkpoint at once would lose
    one of them, so the second waits — and hears the first one's result
    before its own answer."""
    _capture_pushes(monkeypatch)
    _capture_ledger(monkeypatch)
    scripts = [
        THREE_TOOLS + [("sleep", 0.6), ("tool_end", "save_document"), ("content", "The report is ready.")],
        [("content", "It's sunny.")],
    ]
    spans = []

    async def fake_stream(message, conversation_id, **kw):
        script = scripts[len(spans)]
        span = {"message": message, "start": _time.monotonic(), "end": None}
        spans.append(span)
        for kind, val in script:
            if kind == "sleep":
                await asyncio.sleep(val)
            elif kind == "content":
                yield {"type": "content", "content": val}
            elif kind == "tool_start":
                yield {"type": "tool_start", "tool_name": val}
            elif kind == "tool_end":
                yield {"type": "tool_end", "tool_name": val, "result": ""}
        span["end"] = _time.monotonic()
        yield {"type": "done"}

    monkeypatch.setattr(voice_ws, "stream_with_memory_events", fake_stream)
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:write up the report")
        _drain_until(ws, _is("handoff"))
        ws.send_bytes(b"\x01" + b"C:what's the weather like")
        got = _drain_until(ws, _said("It's sunny."))
        ws.send_json({"type": "end_session"})

    bridges = _texts(got, "bridge")
    assert bridges and bridges[0] in WAIT_LINES
    said = _texts(got)
    assert said.index("Okay, that's done. The report is ready.") < said.index("It's sunny.")
    assert [s["message"] for s in spans] == ["write up the report", "what's the weather like"]
    assert spans[1]["start"] >= spans[0]["end"]


# --------------------------------------------------------------------------
# the household hub's voice is not Ellis (security fix, 2026-10-11)
# --------------------------------------------------------------------------

def _one_turn(harness, start):
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json(start)
        started = _drain_until(ws, _is("session_started"))[-1][1]
        ws.send_bytes(b"\x01" + b"C:what time is it")
        _drain_until(ws, _is("response_done"))
        ws.send_json({"type": "end_session"})
    return started


@pytest.mark.parametrize("start", [{"type": "start_session"}, {"type": "start_session", "surface": "app"}])
def test_the_apps_live_voice_is_owner(harness, start):
    """The chat page's voice sends no surface (or ``app``): Ellis's own
    ``voice`` turns, as before."""
    started = _one_turn(harness, start)
    assert started["surface"] == "app"
    assert [c["turn_source"] for c in harness.calls] == ["voice"]


def test_a_hub_session_runs_its_turns_as_hub_voice(harness):
    """``/hub/ask`` declares ``surface: "hub"``: whoever is at the panel is
    someone in Ellis's home, not necessarily Ellis (``toolset.policy``) — and
    still a spoken turn: the voice prompt, the voice model, fast context."""
    started = _one_turn(harness, {"type": "start_session", "surface": "hub"})
    assert started["surface"] == "hub"
    call = harness.calls[0]
    assert call["turn_source"] == "hub_voice"
    assert call["model"] == "gpt-5.6-luna"
    assert "VOICE MODE" in call["system_prompt"]
    assert call["fast_context"] is True


@pytest.mark.parametrize("surface", ["kitchen", "", None, 3, ["hub"]])
def test_an_unknown_surface_is_the_app(harness, surface):
    started = _one_turn(harness, {"type": "start_session", "surface": surface})
    assert started["surface"] == "app"
    assert harness.calls[0]["turn_source"] == "voice"


def test_a_hub_turn_handed_off_stays_hub_voice(harness, monkeypatch):
    """The detached continuation is the same turn (one stream), and its
    ledger row says whose session it was; so does the session summary."""
    _capture_pushes(monkeypatch)
    ledger = _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.4), ("tool_end", "save_document"), ("content", "The list is on the hub."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "surface": "hub"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:make a shopping list for lasagne")
        _drain_until(ws, _is("handoff"))
        _drain_until(ws, _said("on the hub"))
        ws.send_json({"type": "end_session"})
    assert _wait_for(lambda: any(r["kind"] == "voice_session" for r in ledger))

    assert [c["turn_source"] for c in harness.calls] == ["hub_voice"]
    handoffs = [r for r in ledger if r["kind"] == "voice_handoff"]
    assert len(handoffs) == 1 and handoffs[0]["source"] == "hub_voice"
    session = [r for r in ledger if r["kind"] == "voice_session"][0]
    assert session["detail"]["surface"] == "hub"


def test_an_app_turn_handed_off_is_still_owners_voice(harness, monkeypatch):
    _capture_pushes(monkeypatch)
    ledger = _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.4), ("tool_end", "save_document"), ("content", "Saved it."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:save the recipe")
        _drain_until(ws, _is("handoff"))
        _drain_until(ws, _said("Saved it"))
        ws.send_json({"type": "end_session"})
    assert _wait_for(lambda: any(r["kind"] == "voice_session" for r in ledger))
    handoffs = [r for r in ledger if r["kind"] == "voice_handoff"]
    assert len(handoffs) == 1 and handoffs[0]["source"] == "voice"
    session = [r for r in ledger if r["kind"] == "voice_session"][0]
    assert session["detail"]["surface"] == "app"


@pytest.mark.parametrize("start,own", [({"type": "start_session", "surface": "hub"}, True),
                                        ({"type": "start_session"}, False)])
def test_the_hub_spends_edwards_own_share_never_owners_reserve(harness, start, own):
    """Someone at the hub is not Ellis: like a friend's ping, the hub's turns
    run in Edward's own share (``llm_spend.own_work_scope``), which stops at
    the daily cap. Ellis's own voice keeps his reserve."""
    _one_turn(harness, start)
    assert (harness.calls[0]["own_work"], harness.calls[0]["own_work_at_end"]) == (own, own)


def test_a_handed_off_hub_turn_stays_in_edwards_share(harness, monkeypatch):
    _capture_pushes(monkeypatch)
    _capture_ledger(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.4), ("tool_end", "save_document"), ("content", "The list is on the hub."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "surface": "hub"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:make a shopping list for lasagne")
        _drain_until(ws, _is("handoff"))
        _drain_until(ws, _said("on the hub"))
        ws.send_json({"type": "end_session"})
    assert _wait_for(lambda: "own_work_at_end" in harness.calls[0])
    assert (harness.calls[0]["own_work"], harness.calls[0]["own_work_at_end"]) == (True, True)


def _capture_reach(monkeypatch):
    from services import reach_service

    reaches = []

    async def tell_owner(message, title=None, urgent=False, link=None, **kw):
        reaches.append(dict(message=message, title=title, urgent=urgent, link=link, **kw))
        return "Left Ellis a note on Today."

    monkeypatch.setattr(reach_service, "tell_owner", tell_owner)
    return reaches


def test_a_hub_result_after_the_session_ended_is_a_note_through_reach(harness, monkeypatch):
    """The hub's session is gone (whoever asked walked away): the result is
    left for Ellis through ``reach_service`` as the hub (quiet hours, one
    follow-up, the lock-screen secret check), never a raw push."""
    pushes = _capture_pushes(monkeypatch)
    ledger = _capture_ledger(monkeypatch)
    reaches = _capture_reach(monkeypatch)
    harness.state["script"] = THREE_TOOLS + [
        ("sleep", 0.5), ("tool_end", "save_document"),
        ("content", "The lasagne list is saved. Details are in the chat. More text."),
    ]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "surface": "hub"})
        cid = _drain_until(ws, _is("session_started"))[-1][1]["conversation_id"]
        ws.send_bytes(b"\x01" + b"C:make a shopping list for lasagne")
        _drain_until(ws, _is("handoff"))
        ws.send_json({"type": "end_session"})
        assert _wait_for(lambda: reaches), "nothing reached Ellis after the hub's session ended"

    assert pushes == []
    reach = reaches[0]
    assert reach["origin"] == "hub" and reach["urgent"] is False
    assert reach["link"] == f"/chat?c={cid}"
    assert reach["message"] == "The lasagne list is saved. Details are in the chat."
    assert _wait_for(lambda: any(r["kind"] == "voice_handoff" for r in ledger))
    assert [r["outcome"] for r in ledger if r["kind"] == "voice_handoff"] == ["noted"]


def test_a_hub_result_that_reach_refuses_is_not_delivered(harness, monkeypatch):
    from services import reach_service

    pushes = _capture_pushes(monkeypatch)
    ledger = _capture_ledger(monkeypatch)

    async def refuse(message, title=None, urgent=False, link=None, **kw):
        return reach_service.REFUSE_SECRET

    monkeypatch.setattr(reach_service, "tell_owner", refuse)
    harness.state["script"] = THREE_TOOLS + [("sleep", 0.5), ("tool_end", "save_document"), ("content", "Done.")]
    with harness.client.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start_session", "surface": "hub"})
        _drain_until(ws, _is("session_started"))
        ws.send_bytes(b"\x01" + b"C:make a shopping list for lasagne")
        _drain_until(ws, _is("handoff"))
        ws.send_json({"type": "end_session"})
        assert _wait_for(lambda: any(r["kind"] == "voice_handoff" for r in ledger))
    assert pushes == []
    assert [r["outcome"] for r in ledger if r["kind"] == "voice_handoff"] == ["push_failed"]
