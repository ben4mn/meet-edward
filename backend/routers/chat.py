from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import asyncio
import contextvars
import json
import uuid
import base64
from typing import Optional

from langchain_core.messages import HumanMessage

from models import ChatRequest
from services.graph import get_graph, stream_with_memory, stream_with_memory_events, chat_with_memory, EventType
from services.settings_service import get_settings
from services.owner import owner_name, owner_text
from services.conversation_service import (
    create_conversation,
    conversation_exists,
    increment_message_count,
)
from services.memory_service import retrieve_memories
from services.voice_service import get_voice_service
from services.voice.memo import (
    compose_memo_message,
    is_audio_mime,
    normalize_audio_mime,
)

router = APIRouter()

# Max file size for chat uploads (10 MB)
CHAT_UPLOAD_MAX_SIZE = 10 * 1024 * 1024

# Allowed MIME types for chat attachments
CHAT_ATTACHMENT_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf",
    "text/plain", "text/csv", "application/json",
}


OPEN_ENTRY_REFUSAL = "Edward is still writing this entry — open it again in a moment."


def _refuse_open_entry(conversation_id: Optional[str], is_new_conversation: bool) -> None:
    """A wake's push links to its journal entry before the entry is written
    (thought journal, 2026-09-25); tapped in that window the page shows a
    placeholder with a composer. A reply there used to create an ordinary
    conversation under the entry's id, which the wake's entry then collided
    with and was lost in. Refuse it until the entry exists."""
    if not conversation_id or not is_new_conversation:
        return
    try:
        from services.thought_journal import is_open_entry

        is_open = is_open_entry(conversation_id)
    except Exception:  # noqa: BLE001
        return
    if is_open:
        raise HTTPException(status_code=409, detail=OPEN_ENTRY_REFUSAL)


async def _message_with_artifact_context(
    *,
    conversation_id: str,
    message: str,
    attachments: list[dict] | None,
) -> str:
    """The message as the model sees it. Until the mind rebuild (Phase 8)
    this ran attachments and links through the artifact-intelligence
    engine and prepended its fact cards; that engine is gone, and what a
    document says reaches Edward through the attachment itself."""
    return message


async def _parse_chat_request(request: Request):
    """
    Parse a chat request, handling both JSON and multipart/form-data.

    Returns:
        Tuple of (message, conversation_id, attachments)
        where attachments is a list of dicts with keys: filename, mime_type, data (base64), size, file_id
    """
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        message = form.get("message", "")
        conversation_id = form.get("conversation_id") or None

        attachments = []
        memo_transcripts: list[str] = []
        # Get uploaded files
        files = form.getlist("files") if hasattr(form, "getlist") else []
        # Also check for single file field
        if not files and "files" in form:
            files = [form["files"]]

        for upload_file in files:
            if hasattr(upload_file, "read"):
                file_data = await upload_file.read()

                if len(file_data) > CHAT_UPLOAD_MAX_SIZE:
                    continue  # Skip oversized files

                mime_type = upload_file.content_type or "application/octet-stream"
                filename = upload_file.filename or "unnamed"
                normalized = normalize_audio_mime(mime_type, filename)
                if is_audio_mime(normalized):
                    mime_type = normalized

                # Store file in persistent storage
                file_id = None
                try:
                    from services.file_storage_service import store_file, ALLOWED_MIME_TYPES
                    # Temporarily add chat attachment types to allowed list if needed
                    stored = await store_file(
                        file_data=file_data,
                        filename=filename,
                        mime_type=mime_type,
                        category="upload",
                        source="user",
                        conversation_id=conversation_id,
                    )
                    file_id = stored.id
                except Exception as e:
                    print(f"Failed to store uploaded file: {e}")

                if is_audio_mime(mime_type):
                    # Voice memo: transcribe now, keep the audio out of the
                    # model's context. A failed transcription still posts the
                    # memo so nothing the user said is silently dropped.
                    transcript = ""
                    try:
                        result = await get_voice_service().transcribe(
                            file_data, filename=filename, mime_type=mime_type
                        )
                        transcript = (result.text or "").strip()
                    except Exception as e:
                        print(f"[chat] voice memo transcription failed: {e}")
                    memo_transcripts.append(transcript)
                    attachments.append({
                        "kind": "voice_memo",
                        "filename": filename,
                        "mime_type": mime_type,
                        "size": len(file_data),
                        "file_id": file_id,
                        "transcript": transcript,
                    })
                    continue

                attachments.append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "data": base64.standard_b64encode(file_data).decode("utf-8"),
                    "size": len(file_data),
                    "file_id": file_id,
                })

        if memo_transcripts:
            message = compose_memo_message(message, " ".join(t for t in memo_transcripts if t))

        return message, conversation_id, attachments
    else:
        body = await request.json()
        return body.get("message", ""), body.get("conversation_id"), []


@router.get("/chat/{conversation_id}/processing")
async def is_processing(conversation_id: str):
    """Check if a conversation is currently being streamed/processed."""
    from services.heartbeat.heartbeat_service import _active_chats
    return {"processing": conversation_id in _active_chats}


@router.post("/chat")
async def chat(request: Request):
    """Send a message to Edward and get a streaming response.

    Accepts either JSON body or multipart/form-data with file attachments.
    """
    try:
        message, conversation_id, attachments = await _parse_chat_request(request)

        if not message and not attachments:
            raise HTTPException(status_code=400, detail="Message or files required")

        settings = await get_settings()
        graph = await get_graph()

        # A client may hand us an id that has no row yet (voice mode
        # allocates the id up front and only creates the row on the first
        # spoken turn). Treat that like a new conversation so the message
        # shows up in the sidebar instead of vanishing.
        is_new_conversation = conversation_id is None or not await conversation_exists(conversation_id)
        _refuse_open_entry(conversation_id, is_new_conversation)
        conversation_id = conversation_id or str(uuid.uuid4())

        # Create or update conversation record
        if is_new_conversation:
            # Use first 50 chars of message as title
            title = (message or "Image upload")[:50].strip()
            if len(title) > 50:
                title += "..."
            await create_conversation(conversation_id, title)
        else:
            await increment_message_count(conversation_id)

        message_for_model = await _message_with_artifact_context(
            conversation_id=conversation_id,
            message=message,
            attachments=attachments if attachments else None,
        )

        # D3 — user is actively chatting. Fire-and-forget mark recent
        # pushes as acted so the dispatcher's base-rate prompt reflects
        # engagement. Failures are swallowed.
        try:
            import asyncio as _asyncio
            from services.push_engagement import mark_recent_pushes_acted
            _asyncio.create_task(mark_recent_pushes_acted(within_minutes=30))
        except Exception:
            pass

        async def generate():
            # Register active chat so heartbeat defers triage
            from services.heartbeat.heartbeat_service import register_active_chat, unregister_active_chat
            from services.graph.tools import set_current_conversation_id

            # Set the conversation ContextVar here, before the stream
            # starts: N9's heartbeat wrapper runs the turn in a copy of
            # this context taken as it starts, so every step and every
            # task the turn spawns sees it from the first step on. (Until
            # 2026-09-27 each step copied this context afresh, and this
            # line was the only way a tool call saw its conversation.)
            set_current_conversation_id(conversation_id)
            register_active_chat(conversation_id)
            try:
                # Voice memo: the user bubble was posted with an empty
                # message; tell the UI what was actually said, and where
                # the audio lives, before Edward starts answering.
                memo_attachments = [a for a in (attachments or []) if a.get("kind") == "voice_memo"]
                if memo_attachments:
                    yield "data: " + json.dumps({
                        "type": "user_message",
                        "conversation_id": conversation_id,
                        "content": message,
                        "attachments": [
                            {
                                "file_id": a.get("file_id"),
                                "filename": a.get("filename"),
                                "mime_type": a.get("mime_type"),
                                "size": a.get("size", 0),
                                "transcript": a.get("transcript", ""),
                            }
                            for a in memo_attachments
                        ],
                    }) + "\n\n"
                # N9: wrap with heartbeats so quiet stretches between
                # LLM calls don't trip cloudflared/Next.js read timeouts.
                # Real events stream untouched; idle gaps get an
                # invisible ": heartbeat" SSE comment every 10s.
                inner = _wrap_chat_stream(
                    message=message_for_model,
                    conversation_id=conversation_id,
                    system_prompt=settings.system_prompt,
                    model=settings.model,
                    temperature=settings.temperature,
                    graph=graph,
                    attachments=attachments if attachments else None,
                    provider=settings.provider,
                    debug_streaming=getattr(settings, "debug_streaming", False),
                )
                async for chunk in _yield_with_heartbeats(inner, heartbeat_interval=10.0):
                    yield chunk
            finally:
                unregister_active_chat(conversation_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                # no-transform stops upstream proxies from rewriting the
                # body; X-Accel-Buffering: no tells cloudflared / Next.js
                # rewrites not to coalesce per-token frames into one big
                # chunk at the end (the bug fix this header lands).
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/simple")
async def chat_simple(request: Request):
    """Send a message to Edward and get a non-streaming response.

    Accepts either JSON body or multipart/form-data with file attachments.
    """
    try:
        message, conversation_id, attachments = await _parse_chat_request(request)

        if not message and not attachments:
            raise HTTPException(status_code=400, detail="Message or files required")

        settings = await get_settings()
        graph = await get_graph()

        is_new_conversation = conversation_id is None or not await conversation_exists(conversation_id)
        _refuse_open_entry(conversation_id, is_new_conversation)
        conversation_id = conversation_id or str(uuid.uuid4())

        # Create or update conversation record
        if is_new_conversation:
            title = (message or "Image upload")[:50].strip()
            if len(title) > 50:
                title += "..."
            await create_conversation(conversation_id, title)
        else:
            await increment_message_count(conversation_id)

        message_for_model = await _message_with_artifact_context(
            conversation_id=conversation_id,
            message=message,
            attachments=attachments if attachments else None,
        )

        response = await chat_with_memory(
            turn_source="chat",
            message=message_for_model,
            conversation_id=conversation_id,
            system_prompt=settings.system_prompt,
            model=settings.model,
            temperature=settings.temperature,
            graph=graph,
            attachments=attachments if attachments else None,
            provider=settings.provider,
        )

        return {
            "message": response,
            "conversation_id": conversation_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


GREETING_PROMPT = owner_text("""Generate a very short greeting (1 sentence, under 10 words). You are Edward, greeting {OWNER}.
It is {when} for {OWNER} — match the time of day (no "morning" at night).
Tone: dry, chill, occasionally sassy or deadpan. Never cheery or motivational.
Think more "what's up" than "let's crush this day!" — you're not a life coach.
Vary the shape: don't open with "Evening, {OWNER}" (or any "<time>, {OWNER}") every time —
sometimes no name, sometimes an observation, sometimes just a word.
Never describe yourself as operational, online, or a system.
If context is provided, you may reference ONE thing subtly — but don't always reference context.
Vary your greetings. Don't ask how you can help. Don't mention hobbies every time.
NEVER give advice, instructions, or tell the user what to do. This is a greeting, not a directive.

{context}

Return ONLY the greeting text, nothing else.""")


async def _build_greeting_context() -> str:
    """Build diverse greeting context from multiple memory sources.

    Strategy: sample across different memory types and queries to avoid
    fixating on one topic. Pick at most 1 item per category.
    """
    import random

    context_items = []
    seen_ids = set()
    seen_topics = set()  # Track topic keywords to avoid repetition

    def _add(mem):
        if mem.id in seen_ids:
            return
        # Skip memories that are too operational/work-coordination focused
        content_lower = mem.content.lower()
        skip_phrases = [
            "scheduled event", "no scheduled", "tomorrow", "next fire", "webhook",
            "syncing up", "overlap", "minimize overlap", "coordinate",
            "orchestrat", "worker", "pipeline", "deploy", "merge",
            "edward reported", "edward cannot", "edward has access",
        ]
        if any(p in content_lower for p in skip_phrases):
            return
        # Check topic overlap — skip if we already have a memory about the same thing
        words = set(content_lower.split())
        topic_overlap = len(words & seen_topics) / max(len(words), 1)
        if topic_overlap > 0.3 and len(seen_topics) > 5:
            return
        seen_ids.add(mem.id)
        seen_topics.update(words)
        context_items.append(mem.content)

    # G2: parallelize the 4 independent reads. Each branch isolates its
    # own try/except so a slow/broken one can't poison the bundle, and
    # asyncio.gather lets them run concurrently — ~150-300ms saved.
    pref_queries = [
        "food restaurant cooking",
        "music entertainment shows",
        "hobbies interests weekend activities",
        "technology tools software",
        "pets animals family",
    ]
    recent_queries = [
        "recent plans upcoming events",
        "current project work",
        "recent conversation topic",
    ]
    pref_query = random.choice(pref_queries)
    recent_query = random.choice(recent_queries)

    async def _facts_branch():
        try:
            from services.memory_service import retrieve_memories as _rm
            return await _rm(
                "user name job role location",
                limit=5, min_score=0.2, memory_types=["fact"],
            )
        except Exception as e:
            print(f"Greeting facts branch failed: {e}")
            return []

    async def _prefs_branch():
        try:
            from services.memory_service import retrieve_memories as _rm
            return await _rm(
                pref_query, limit=3, min_score=0.2, memory_types=["preference"],
            )
        except Exception as e:
            print(f"Greeting prefs branch failed: {e}")
            return []

    async def _recent_branch():
        try:
            from services.memory_service import retrieve_memories as _rm
            return await _rm(
                recent_query, limit=5, min_score=0.2, memory_types=["context"],
            )
        except Exception as e:
            print(f"Greeting recent branch failed: {e}")
            return []

    async def _loops_branch():
        try:
            from services.open_loop_service import get_active_open_loops
            return await get_active_open_loops(limit=3)
        except Exception as e:
            print(f"Greeting loops branch failed: {e}")
            return []

    facts, prefs, recent, loops = await asyncio.gather(
        _facts_branch(), _prefs_branch(), _recent_branch(), _loops_branch()
    )

    try:
        if facts:
            _add(random.choice(facts))
        if prefs:
            _add(random.choice(prefs))
        if recent:
            from datetime import datetime, timedelta, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            fresh = [
                m for m in recent
                if hasattr(m, "updated_at") and m.updated_at
                and m.updated_at.replace(tzinfo=timezone.utc) > cutoff
            ]
            if fresh:
                _add(random.choice(fresh))
            else:
                _add(random.choice(recent))
        if loops:
            loop = random.choice(loops)
            context_items.append(f"Unresolved: {loop.description}")
    except Exception as e:
        print(f"Greeting context assemble failed: {e}")

    if not context_items:
        return ""

    # Shuffle so the LLM doesn't always fixate on the first item
    random.shuffle(context_items)
    return "Context about the user (reference at most ONE, or none):\n" + "\n".join(
        f"- {item}" for item in context_items[:3]
    )


# G3: TTL cache for greetings, stale-while-revalidate since 2026-09-06.
# Within the soft TTL the cached line is returned. Past it the cached line
# is *still* returned at once and one background task builds the next
# one, so opening the app never waits ~2s on the LLM. Past the hard TTL
# the line is too old to show (wrong time of day) and we wait for a fresh
# one. Startup pre-warms the cache so the very first open is instant too.
_GREETING_CACHE_TTL_SECONDS = 60.0
_GREETING_CACHE_HARD_TTL_SECONDS = 30 * 60.0
_greeting_cache: dict = {"timestamp": 0.0, "value": None}
_greeting_refresh_task: Optional[asyncio.Task] = None


def _clear_greeting_cache() -> None:
    """Test hook + manual invalidate."""
    _greeting_cache["timestamp"] = 0.0
    _greeting_cache["value"] = None


# ?fresh=1 (review 2026-09-26, greeting-stale-after-action): Today asks for
# a fresh line ~800 ms after the owner resolves a need, so the hero stops
# saying "Biscuit's meds are waiting" after they logged them. A cached line older than
# this is rebuilt instead of served from the soft cache — and a forced
# rebuild runs at most once per this window, so a run of taps (or a model
# that keeps failing) costs one LLM call.
_GREETING_FRESH_MIN_AGE_SECONDS = 20.0
_greeting_fresh_gate: dict = {"last": 0.0}


def _reset_fresh_greeting_gate() -> None:
    """Test hook."""
    _greeting_fresh_gate["last"] = 0.0


def _greeting_wants_rebuild(fresh: bool) -> bool:
    """A forced rebuild is due: asked for, the cached line is old enough,
    and no forced rebuild ran in the last window."""
    import time as _time
    if not fresh or not _greeting_cache["value"]:
        return False
    now = _time.time()
    if now - _greeting_cache["timestamp"] < _GREETING_FRESH_MIN_AGE_SECONDS:
        return False
    if now - _greeting_fresh_gate["last"] < _GREETING_FRESH_MIN_AGE_SECONDS:
        return False
    _greeting_fresh_gate["last"] = now
    return True


def _greeting_cache_state() -> str:
    """'fresh' | 'stale' | 'expired' | 'empty'."""
    import time as _time
    if not _greeting_cache["value"]:
        return "empty"
    age = _time.time() - _greeting_cache["timestamp"]
    if age < _GREETING_CACHE_TTL_SECONDS:
        return "fresh"
    if age < _GREETING_CACHE_HARD_TTL_SECONDS:
        return "stale"
    return "expired"


async def _refresh_greeting_cache() -> None:
    import time as _time
    try:
        fresh = await _generate_fresh_greeting()
    except Exception as e:
        print(f"[greeting] background refresh failed (keeping the old line): {e}")
        return
    if fresh:
        _greeting_cache["timestamp"] = _time.time()
        _greeting_cache["value"] = fresh


def _kick_greeting_refresh() -> None:
    """Start one background refresh unless one is already running."""
    global _greeting_refresh_task
    task = _greeting_refresh_task
    if task is not None and not task.done():
        return
    _greeting_refresh_task = asyncio.create_task(_refresh_greeting_cache())


async def _await_greeting_refresh() -> None:
    """Test hook: wait for the in-flight background refresh, if any."""
    task = _greeting_refresh_task
    if task is not None and not task.done():
        await task


async def _generate_fresh_greeting() -> str:
    """Build context + invoke the LLM. No caching — see _get_or_build_greeting."""
    pieces = []
    async for piece in _astream_fresh_greeting():
        pieces.append(piece)
    return "".join(pieces).strip()


async def _astream_fresh_greeting():
    """Build context + stream the LLM via _astream so callers can
    relay deltas as SSE. Used by both the streaming endpoint and
    _generate_fresh_greeting (which collects + returns the joined text)."""
    context = await _build_greeting_context()

    from services.llm_utils import build_background_llm
    # "triage" tier: the greeting is on the screen the moment the app opens;
    # it must not queue behind bulk enrichment (it did: "Greeting stream
    # failed: ... background LLM call deferred", 2026-09-09).
    llm = build_background_llm(temperature=0.7, max_tokens=100, priority="triage")

    from services.timezone_utils import now_local
    prompt = GREETING_PROMPT.format(
        when=now_local().strftime("%-I:%M %p on %A"),
        context=context if context else "No specific context available.",
    )

    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        # Codex/OpenAI chunks have .content; flatten list/dict shapes.
        content = getattr(chunk, "content", None)
        if isinstance(content, str) and content:
            yield content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block and block["text"]:
                    yield block["text"]


async def pre_warm_greeting_cache() -> None:
    """G4: build one greeting at startup so the first user-facing
    request after a backend bounce hits the cache (instant) instead of
    paying the full ~3-5s cold cost.

    Failures are swallowed — startup must never block on this. The
    next real user request will retry naturally.
    """
    try:
        await _get_or_build_greeting()
        print("[greeting] cache pre-warmed at startup")
    except Exception as e:
        print(f"[greeting] pre-warm failed (non-fatal): {e}")


async def _get_or_build_greeting(fresh: bool = False) -> str:
    """Return a cached greeting if fresh, otherwise generate a new one
    and stash it. The cache is per-process, intentionally simple — the
    only invalidation is TTL, _clear_greeting_cache, or ``fresh`` (the
    owner just resolved something; see _greeting_wants_rebuild)."""
    import time as _time
    if _greeting_wants_rebuild(fresh):
        try:
            rebuilt = await _generate_fresh_greeting()
        except Exception as e:
            print(f"[greeting] fresh rebuild failed (keeping the old line): {e}")
            return _greeting_cache["value"]
        if rebuilt:
            _greeting_cache["timestamp"] = _time.time()
            _greeting_cache["value"] = rebuilt
        return _greeting_cache["value"]
    state = _greeting_cache_state()
    if state == "fresh":
        return _greeting_cache["value"]
    if state == "stale":
        _kick_greeting_refresh()
        return _greeting_cache["value"]

    fresh = await _generate_fresh_greeting()
    _greeting_cache["timestamp"] = _time.time()
    _greeting_cache["value"] = fresh
    return fresh


# The cancel message ``_yield_with_heartbeats`` sends the step it abandons,
# so ``_wrap_chat_stream`` can tell the owner leaving from a crash.
CLIENT_GONE = "chat client disconnected"


async def _yield_with_heartbeats(stream, heartbeat_interval: float = 10.0):
    """N9: wrap an async event stream with periodic SSE heartbeat
    comments so quiet stretches don't trip upstream proxy timeouts.

    Edward's chat sometimes goes silent for 30+ seconds between LLM
    calls (waiting on the model to compose its next move). cloudflared
    and Next.js dev cut SSE connections on extended silence — frontend
    sees a stream error and shows the canned message before any
    meaningful response can reach the user.

    SSE comment lines (``: heartbeat\\n\\n``) are valid framing —
    invisible to JSON-parsing clients but treated as liveness by
    proxies. We yield one whenever the inner stream hasn't produced
    a real event for ``heartbeat_interval`` seconds.

    Yields strings ready for the SSE response body — either
    ``data: {...json...}\\n\\n`` for real events or ``: heartbeat\\n\\n``
    for keepalives.

    Every step runs in one context, copied from the caller's when the
    stream starts (review of edward/audit-small, 2026-09-27). A task per
    step used to copy the caller's context afresh, so what the turn set in
    its first step — the turn record ``turn_ledger.begin_turn`` sets — was
    gone from the second step on and in the post-turn work: no "Already
    sent" guard for a PWA chat send, no turn in the spend ledger's labels,
    in the memory credit or in what the turn remembered. One shared copy
    keeps the turn's context for the whole turn and out of the caller's.
    """
    agen = stream.__aiter__()
    loop = asyncio.get_running_loop()
    turn_context = contextvars.copy_context()
    next_task = None
    try:
        while True:
            next_task = loop.create_task(agen.__anext__(), context=turn_context)
            # Loop heartbeats until the next event arrives (or stream ends).
            # The shield is for the heartbeat timeout only: a quiet stretch
            # must not cancel the step.
            while True:
                try:
                    event = await asyncio.wait_for(
                        asyncio.shield(next_task), timeout=heartbeat_interval
                    )
                    break  # got a real event
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                except StopAsyncIteration:
                    return
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        # The consumer went away mid-step: Stop in the PWA, iOS killing a
        # hidden tab, a dropped network (review of batch B, 2026-09-27).
        # The shield kept the step running — an LLM call of up to 300 s —
        # holding the thread's slot while the owner's next message waited.
        # Cancel it: the turn's own finally frees the thread at once. The
        # turn's last step — saving the answer the owner already has — is shielded in
        # ``streaming._stream_with_memory_events_impl`` and still lands.
        if next_task is not None and not next_task.done():
            next_task.cancel(CLIENT_GONE)


async def _format_chat_error_explanation(
    *,
    user_message: str,
    partial_response: str,
    last_tool_name: Optional[str],
    error_text: str,
    error: Optional[BaseException] = None,
) -> str:
    """N7: when the chat path explodes mid-stream, ask a fast LLM to
    write a 1-2 sentence Edward-voiced apology that names what was
    being attempted and what went wrong. Fall back to a templated
    message that still names the failing piece if the explainer LLM
    also fails — never return a raw "Sorry, I encountered an error".

    A turn the daily Claude spend cap refused (``LLMSpendCapReached``,
    2026-10-10) says the cap's own sentence: it is already in Edward's
    voice, and an explainer call would be refused by the same cap."""
    try:
        from services.llm_spend import LLMSpendCapReached

        if isinstance(error, LLMSpendCapReached) and str(error).strip():
            return str(error).strip()
    except Exception:  # noqa: BLE001
        pass
    tool_summary = (
        f"Last tool attempted: {last_tool_name}." if last_tool_name else "No tool was running."
    )
    partial_summary = (
        f"Edward had started replying with: {partial_response!r}."
        if partial_response else "Edward had not started replying yet."
    )
    explainer_prompt = (
        "You are Edward. Something just broke while you were responding to "
        f"{owner_name()}. Write 1-2 short sentences in Edward's dry/chill tone telling "
        "them what you were trying to do and what went wrong. Be specific "
        "about the tool/error so they can decide whether to retry. Don't "
        "apologize excessively — just name the issue and offer to try a "
        "different angle if it makes sense. End by suggesting one concrete "
        "next step if you can think of one.\n\n"
        f"User asked: {user_message!r}\n"
        f"{tool_summary}\n"
        f"{partial_summary}\n"
        f"Underlying error: {error_text}\n\n"
        "Reply with ONLY the message text, no preamble."
    )
    try:
        from services.llm_utils import build_background_llm
        # The owner is waiting on this line; never defer it behind enrichment.
        llm = build_background_llm(temperature=0.7, max_tokens=200, priority="triage")
        resp = await llm.ainvoke([HumanMessage(content=explainer_prompt)])
        content = resp.content
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        text = (content or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"[chat] error-explainer LLM also failed: {e}")

    # Templated fallback — still names the failing piece so the user has
    # actionable info, never the generic frontend canned string.
    if last_tool_name:
        return (
            f"Tried to use {last_tool_name} and it didn't come back. "
            f"({error_text}.) Want me to try a different approach?"
        )
    return f"Hit an issue mid-response: {error_text}. Try again?"


async def _wrap_chat_stream(
    *,
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments,
    provider: str,
    debug_streaming: bool = False,
):
    """N7: wrap stream_with_memory_events so an unhandled exception
    inside it surfaces as an LLM-voiced CONTENT event + a DONE event,
    instead of letting the SSE stream raise (which makes the frontend
    show 'Sorry, I encountered an error').

    We track the partial response and last tool seen so the explainer
    LLM has real context to write about.
    """
    from services.graph.streaming import EventType, create_event

    partial_response_parts: list[str] = []
    last_tool_name: Optional[str] = None

    try:
        async for event in stream_with_memory_events(
            message=message,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            graph=graph,
            attachments=attachments,
            provider=provider,
            debug_streaming=debug_streaming,
        ):
            if isinstance(event, dict):
                if event.get("type") == "content" and event.get("content"):
                    partial_response_parts.append(event["content"])
                elif event.get("type") in ("tool_start", "tool_end") and event.get("tool_name"):
                    last_tool_name = event["tool_name"]
            yield event
    except BaseException as e:
        if isinstance(e, GeneratorExit) or (isinstance(e, asyncio.CancelledError) and e.args == (CLIENT_GONE,)):
            # Not a crash: closed by the consumer, or the owner left
            # (``_yield_with_heartbeats``). Nobody would read an explanation,
            # writing one is a Codex call, and yielding after GeneratorExit
            # raises "async generator ignored GeneratorExit" (review of
            # batch B, 2026-09-27).
            raise
        # B6: catch BaseException not just Exception. CancelledError
        # propagates through the chat task when an unrelated sibling
        # (MCP session holder, consolidation worker) crashes inside an
        # anyio TaskGroup — without this catch the SSE stream closes
        # with no content event and the frontend shows the canned
        # 'encountered an error'. We swallow it gracefully by emitting
        # a real explainer + DONE so the user sees what happened.
        print(f"[chat] stream raised mid-flight: {type(e).__name__}: {e}")
        explanation = await _format_chat_error_explanation(
            user_message=message,
            partial_response="".join(partial_response_parts),
            last_tool_name=last_tool_name,
            error_text=f"{type(e).__name__}: {e}",
            error=e,
        )
        yield create_event(EventType.CONTENT, conversation_id, content=explanation)
        yield create_event(EventType.DONE, conversation_id)
        if isinstance(e, asyncio.CancelledError):
            # Don't swallow cancellation entirely — let asyncio finalize
            # the task properly so the parent doesn't hang. The events
            # above already flushed to the SSE buffer.
            return


@router.post("/greeting")
async def get_greeting(fresh: bool = False):
    """Return a personalized greeting. Cached per-process for
    _GREETING_CACHE_TTL_SECONDS so refreshes are instant; ``?fresh=1``
    rebuilds a line older than ~20 s (after the owner resolved something)."""
    try:
        greeting = await _get_or_build_greeting(fresh=fresh)
        return {"greeting": greeting}
    except Exception as e:
        print(f"Greeting generation failed: {e}")
        err = str(e).lower()
        if "usage limit" in err or "rate limit" in err:
            return {"greeting": "Hey — I'm rate-limited right now. Try again in a bit."}
        return {"greeting": "Hey."}


async def _stream_greeting(fresh: bool = False):
    """G5: SSE generator that emits the greeting one delta at a time.
    Cache hit → single chunk with cached text (no LLM call).
    Cache miss → relay each _astream_fresh_greeting delta as an SSE
    data: event, then populate the cache so subsequent calls are fast.
    ``fresh`` → a line older than ~20 s is a miss (at most one per 20 s).
    """
    import time as _time
    state = _greeting_cache_state()
    rebuild = _greeting_wants_rebuild(fresh)
    stale_line = _greeting_cache["value"] if rebuild else None
    if state in ("fresh", "stale") and not rebuild:
        if state == "stale":
            _kick_greeting_refresh()
        yield f"data: {json.dumps({'chunk': _greeting_cache['value']})}\n\n"
        yield "data: [DONE]\n\n"
        return

    pieces = []
    try:
        async for piece in _astream_fresh_greeting():
            pieces.append(piece)
            yield f"data: {json.dumps({'chunk': piece})}\n\n"
    except Exception as e:
        print(f"Greeting stream failed: {e}")
        err = str(e).lower()
        if stale_line and not pieces:
            # A forced rebuild that failed keeps the line the owner already had.
            yield f"data: {json.dumps({'chunk': stale_line})}\n\n"
        elif "usage limit" in err or "rate limit" in err:
            yield f"data: {json.dumps({'chunk': 'Hey — Im rate-limited right now. Try again in a bit.'})}\n\n"
        else:
            yield f"data: {json.dumps({'chunk': 'Hey.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    full = "".join(pieces).strip()
    if full:
        _greeting_cache["timestamp"] = _time.time()
        _greeting_cache["value"] = full
    yield "data: [DONE]\n\n"


@router.get("/greeting/stream")
async def stream_greeting(fresh: bool = False):
    """SSE endpoint — emits one chunk per Codex delta on cache miss,
    or a single chunk with the cached text on hit. Frontend consumes
    via fetchGreetingStream and appends deltas as they arrive."""
    return StreamingResponse(
        _stream_greeting(fresh=fresh),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
