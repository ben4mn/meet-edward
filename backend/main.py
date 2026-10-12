import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from routers import presence, chat, settings, debug, memories, conversations, webhooks, skills, events, auth, push, voice, voice_ws, documents, files, widget, databases, heartbeat, custom_mcp, consolidation, orchestrator, email, usage, alexa, proactive, gmail, memory_graph, display, journal, hub, photo_door, today, screen, browser_ext, wishlist, signals, health_report, mind, spend, approvals, notes
from services.database import init_db, DATABASE_URL
from services.graph import initialize_graph

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. Logging first: every print() and logger.info from here on is
    # timestamped in /tmp/edward-backend.log (see services/log_setup.py).
    from services.log_setup import configure_logging
    configure_logging(wrap_stdio=True)

    await init_db()
    await initialize_graph(DATABASE_URL)

    # No password yet: the one-time setup code the first password needs,
    # in backend/.setup_code (0600). The log names the file, never the code.
    # Once a password exists, a leftover file is deleted.
    try:
        from services.setup_code import ensure_for_startup
        await ensure_for_startup()
    except Exception as e:  # noqa: BLE001 — /api/auth/setup makes it on demand
        print(f"[startup] setup code not prepared (non-fatal): {e}")

    # Seed the in-process timezone cache from settings so the scheduler,
    # heartbeat, and chat all use the user's configured zone (not the env-var
    # fallback). settings_service also calls set_user_tz on every save.
    try:
        from services.settings_service import get_settings
        from services.timezone_utils import set_user_tz
        _settings = await get_settings()
        if getattr(_settings, "timezone", None):
            set_user_tz(_settings.timezone)
            print(f"[startup] timezone seeded: {_settings.timezone}")
    except Exception as e:
        print(f"Timezone seed error (using fallback): {e}")

    # Eager-load sentence-transformers on the MAIN thread. Previously I
    # tried ``await asyncio.to_thread(get_embedding_model)`` — that still
    # runs the load in a ThreadPoolExecutor worker, so any fork-hazard
    # interaction with macOS's Objective-C runtime happens off-main and
    # later segfaults. Sync load here pins everything to the main thread.
    try:
        from services.memory_service import get_embedding_model
        model = get_embedding_model()
        print(f"[startup] embedding model preloaded: {type(model).__name__}")
    except Exception as e:
        print(f"Embedding model preload error: {e}")

    # Resume the Codex rate gate's meter (the account's usage window as the
    # last Codex response reported it) so background pacing does not start
    # blind after a bounce, and wire persistence + stale-meter refresh.
    # Must run before any background loop starts. Only the Codex fallback
    # has that meter: under the Claude API (the default provider) there is
    # nothing to restore or fetch, and spend is paced by llm_spend's cap.
    from services.claude_routing import llm_provider
    if llm_provider() == "codex":
        try:
            from services.llm_rate_gate import get_llm_rate_gate
            from services.llm_rate_gate_store import persist_gate, restore_gate_from_db
            from services.codex_usage_service import refresh_gate_meter

            _gate = get_llm_rate_gate()
            await restore_gate_from_db(_gate)
            _gate.on_state_change = lambda _state: asyncio.create_task(persist_gate(_gate))
            _gate.refresh_meter = refresh_gate_meter
            if _gate.meter is None:
                try:
                    await asyncio.wait_for(refresh_gate_meter(), timeout=8)
                except Exception as e:  # noqa: BLE001
                    print(f"[startup] Codex usage meter not fetched (non-fatal): {e}")
            _status = _gate.status()
            _primary = _status["windows"].get("primary")
            print(
                "[startup] Codex rate gate: "
                + (f"{_primary['used_percent']:.0f}% of the {_primary['window_minutes'] // 60}h window used"
                   if _primary else "no meter yet")
                + f"; background {'allowed' if _status['background_allowed'] else 'deferred'} — {_status['background_reason']}"
            )
        except Exception as e:
            print(f"Rate gate restore error (non-fatal): {e}")
    else:
        print(f"[startup] LLM provider: {llm_provider()} (no Codex rate-gate meter to restore)")

    # Ensure file storage directory exists
    from services.file_storage_service import ensure_storage_dir
    ensure_storage_dir()

    # Initialize skills service (manages MCP and other integrations)
    try:
        from services.skills_service import init_skills
        await init_skills()
    except Exception as e:
        print(f"Skills initialization error: {e}")

    # Initialize MCP clients (optional, won't fail if unavailable)
    try:
        from services.mcp_client import initialize_whatsapp_mcp, sweep_orphaned_mcp_servers
        # Servers a SIGKILLed predecessor left behind (re-parented to launchd).
        sweep_orphaned_mcp_servers()
        await initialize_whatsapp_mcp()
    except Exception as e:
        print(f"WhatsApp MCP initialization skipped: {e}")

    try:
        from services.mcp_client import initialize_apple_mcp
        await initialize_apple_mcp()
    except Exception as e:
        print(f"Apple Services MCP initialization skipped: {e}")

    # Initialize custom MCP servers (Edward-added servers from DB)
    try:
        from services.custom_mcp_service import initialize_custom_servers
        await initialize_custom_servers()
    except Exception as e:
        print(f"Custom MCP servers initialization skipped: {e}")

    # Second skills sweep: MCP-backed status getters return "connecting"
    # until their subprocess is up. Now that WhatsApp / Apple / custom MCP
    # are all initialized, re-run get_all_skills so their rows refresh.
    try:
        from services.skills_service import get_all_skills
        await get_all_skills()
    except Exception as e:
        print(f"Skills post-MCP refresh error: {e}")

    # Initialize tool registry (must be after skills and MCP)
    try:
        from services.tool_registry import initialize_registry
        await initialize_registry()
    except Exception as e:
        print(f"Tool registry initialization error: {e}")

    # Edward's computer: lay out ~/edward-home, write the Seatbelt profile,
    # and sweep shell process groups a previous run left behind.
    try:
        from services import computer
        await computer.startup()
    except Exception as e:
        print(f"Computer initialization error: {e}")

    # Start the scheduled events scheduler
    try:
        from services.scheduler_service import start_scheduler
        await start_scheduler()
    except Exception as e:
        print(f"Scheduler initialization error: {e}")

    # Start the heartbeat system (iMessage listener + triage loop)
    try:
        from services.heartbeat import start_heartbeat
        await start_heartbeat()
        # Presence: the host Mac reports its own idle time every 30s so
        # Edward has one "computer" signal before any companion exists.
        from services.presence_service import start_local_mac_probe
        await start_local_mac_probe()
        from services.push_service import backfill_device_kinds
        await backfill_device_kinds()
    except Exception as e:
        print(f"Heartbeat initialization error: {e}")

    # Pings (2026-10-11): the runner answers every @Edward, the owner's
    # self-chat and replies to Edward, one turn each, until a send lands.
    # The listener kicks it; EDWARD_PINGS=off leaves it stopped.
    try:
        from services.pings import hooks as pings_hooks
        await pings_hooks.start()
    except Exception as e:
        print(f"Ping runner initialization error: {e}")

    # Edward's hands in the owner's Chrome (the extension link).
    try:
        from services import browser_hands
        browser_hands.start()
    except Exception as e:
        print(f"Browser hands initialization error: {e}")

    # Start the memory consolidation service. Stagger the heavy background
    # loops with distinct startup offsets so they don't all fire their first
    # cycle at boot and stampede the Codex rate gate (the gate paces them
    # regardless, but phase-offsets spread the work cleanly over time).
    try:
        from services.consolidation_service import start_consolidation
        await start_consolidation(startup_delay=60)
    except Exception as e:
        print(f"Consolidation initialization error: {e}")

    # Initialize orchestrator (recover crashed tasks)
    try:
        from services.orchestrator_service import start_orchestrator
        await start_orchestrator()
    except Exception as e:
        print(f"Orchestrator initialization error: {e}")

    # Graph memory (Graphiti/Kuzu) now runs as a separate launchd-managed
    # worker (<EDWARD_LAUNCHD_PREFIX>.graph-worker, host_config.launchd_label)
    # — nothing to start in-process.

    # G4: pre-warm the greeting cache so the first chat-page load after
    # boot is instant. Fire-and-forget — never blocks startup.
    try:
        import asyncio as _asyncio
        from routers.chat import pre_warm_greeting_cache
        _asyncio.create_task(pre_warm_greeting_cache())
    except Exception as e:
        print(f"Greeting pre-warm scheduling failed (non-fatal): {e}")

    yield

    # Shutdown
    try:
        from services import computer
        await computer.shutdown()
    except Exception as e:
        print(f"Computer shutdown error: {e}")

    try:
        from services.orchestrator_service import stop_orchestrator
        await stop_orchestrator()
    except Exception as e:
        print(f"Orchestrator shutdown error: {e}")

    try:
        from services.consolidation_service import stop_consolidation
        await stop_consolidation()
    except Exception as e:
        print(f"Consolidation shutdown error: {e}")

    try:
        from services.pings import hooks as pings_hooks
        await pings_hooks.stop()
    except Exception as e:
        print(f"Ping runner shutdown error: {e}")

    try:
        from services.heartbeat import stop_heartbeat
        await stop_heartbeat()
        from services.presence_service import stop_local_mac_probe
        await stop_local_mac_probe()
    except Exception as e:
        print(f"Heartbeat shutdown error: {e}")

    try:
        from services.scheduler_service import stop_scheduler
        await stop_scheduler()
    except Exception as e:
        print(f"Scheduler shutdown error: {e}")

    try:
        from services.custom_mcp_service import shutdown_custom_servers
        await shutdown_custom_servers()
    except Exception as e:
        print(f"Custom MCP servers shutdown error: {e}")

    try:
        from services.mcp_client import shutdown_whatsapp_mcp, shutdown_apple_mcp
        await shutdown_whatsapp_mcp()
        await shutdown_apple_mcp()
    except Exception as e:
        print(f"MCP shutdown error: {e}")

    try:
        from services.voice_service import shutdown_voice_service
        await shutdown_voice_service()
    except Exception as e:
        print(f"Voice service shutdown error: {e}")

    try:
        # Edward's own headless browser (2026-10-11): Playwright's shell, started lazily.
        from services import headless_browser
        await headless_browser.shutdown()
    except Exception as e:
        print(f"Headless browser shutdown error: {e}")

    # Graph memory worker is launchd-managed — no in-process teardown.

app = FastAPI(
    title="Edward API",
    description="Backend API for Edward AI Assistant",
    version="0.1.0",
    lifespan=lifespan
)

# Configure CORS: local dev origins, plus the public address the owner opens
# Edward at and any extra origins (host_config: EDWARD_PUBLIC_BASE_URL,
# EDWARD_CORS_ORIGINS).
from services.host_config import cors_origins as _cors_origins

_LOCAL_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://localhost:3003",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:3002",
    "http://127.0.0.1:3003",
    "http://frontend:3000",
]
_ALLOWED_ORIGINS = list(dict.fromkeys(_LOCAL_DEV_ORIGINS + list(_cors_origins())))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=r"^http://192\.168\.\d+\.\d+:(3000|3001|3002|3003|8000)$|^http://10\.\d+\.\d+\.\d+:(3000|3001|3002|3003|8000)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add authentication middleware (after CORS so preflight requests work)
from middleware.auth import AuthMiddleware
app.add_middleware(AuthMiddleware)

# Include routers
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(presence.router, prefix="/api", tags=["presence"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(debug.router, prefix="/api", tags=["debug"])
app.include_router(memories.router, prefix="/api", tags=["memories"])
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(skills.router, prefix="/api", tags=["skills"])
app.include_router(events.router, prefix="/api", tags=["events"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(push.router, prefix="/api", tags=["push"])
app.include_router(voice.router, prefix="/api", tags=["voice"])
app.include_router(voice_ws.router, prefix="/api", tags=["voice-ws"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(files.router, prefix="/api", tags=["files"])
app.include_router(widget.router, prefix="/api", tags=["widget"])
app.include_router(databases.router, prefix="/api", tags=["databases"])
app.include_router(heartbeat.router, prefix="/api", tags=["heartbeat"])
app.include_router(custom_mcp.router, prefix="/api", tags=["custom-mcp"])
app.include_router(consolidation.router, prefix="/api", tags=["consolidation"])
app.include_router(orchestrator.router, prefix="/api", tags=["orchestrator"])
app.include_router(email.router, prefix="/api", tags=["email"])
app.include_router(usage.router, prefix="/api", tags=["usage"])
app.include_router(alexa.router, prefix="/api", tags=["alexa"])
app.include_router(proactive.router, prefix="/api", tags=["proactive"])
app.include_router(gmail.router, prefix="/api", tags=["gmail"])
app.include_router(journal.router, prefix="/api", tags=["journal"])
app.include_router(memory_graph.router, tags=["memory-graph"])
app.include_router(display.router, prefix="/api", tags=["display"])
app.include_router(hub.router, prefix="/api", tags=["hub"])
app.include_router(photo_door.router, prefix="/api", tags=["photo-door"])
app.include_router(screen.router, prefix="/api", tags=["screen"])
app.include_router(browser_ext.router, prefix="/api", tags=["browser-ext"])
app.include_router(today.router, prefix="/api", tags=["today"])
app.include_router(wishlist.router, prefix="/api", tags=["wishlist"])
app.include_router(mind.router, prefix="/api", tags=["mind"])
app.include_router(signals.router, prefix="/api", tags=["signals"])
app.include_router(health_report.router, prefix="/api", tags=["health"])
app.include_router(spend.router, prefix="/api", tags=["health"])
app.include_router(approvals.router, prefix="/api", tags=["approvals"])
app.include_router(notes.router, prefix="/api", tags=["notes"])

@app.get("/")
async def root():
    return {"message": "Edward API is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
