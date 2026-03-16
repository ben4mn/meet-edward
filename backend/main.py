import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from routers import chat, settings, debug, memories, conversations, webhooks, skills, events, auth, push, documents, files, widget, databases, heartbeat, custom_mcp, consolidation, evolution, orchestrator
from services.database import init_db, DATABASE_URL
from services.graph import initialize_graph

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await initialize_graph(DATABASE_URL)

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
        from services.mcp_client import initialize_whatsapp_mcp
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

    # Initialize NotebookLM client (if credentials exist)
    try:
        from services.notebooklm_service import initialize_notebooklm
        await initialize_notebooklm()
    except Exception as e:
        print(f"NotebookLM initialization skipped: {e}")

    # Initialize tool registry (must be after skills and MCP)
    try:
        from services.tool_registry import initialize_registry
        await initialize_registry()
    except Exception as e:
        print(f"Tool registry initialization error: {e}")

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
    except Exception as e:
        print(f"Heartbeat initialization error: {e}")

    # Start the memory consolidation service
    try:
        from services.consolidation_service import start_consolidation
        await start_consolidation()
    except Exception as e:
        print(f"Consolidation initialization error: {e}")

    # Check for pending evolution deploys (finalize after restart)
    try:
        from services.evolution_service import check_pending_deploy
        await check_pending_deploy()
    except Exception as e:
        print(f"Evolution deploy check error: {e}")

    # Initialize orchestrator (recover crashed tasks)
    try:
        from services.orchestrator_service import start_orchestrator
        await start_orchestrator()
    except Exception as e:
        print(f"Orchestrator initialization error: {e}")

    yield

    # Shutdown
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
        from services.heartbeat import stop_heartbeat
        await stop_heartbeat()
    except Exception as e:
        print(f"Heartbeat shutdown error: {e}")

    try:
        from services.scheduler_service import stop_scheduler
        await stop_scheduler()
    except Exception as e:
        print(f"Scheduler shutdown error: {e}")

    try:
        from services.notebooklm_service import shutdown_notebooklm
        await shutdown_notebooklm()
    except Exception as e:
        print(f"NotebookLM shutdown error: {e}")

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


app = FastAPI(
    title="Edward API",
    description="Backend API for Edward AI Assistant",
    version="0.1.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:3003",
    ],
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
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(debug.router, prefix="/api", tags=["debug"])
app.include_router(memories.router, prefix="/api", tags=["memories"])
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(skills.router, prefix="/api", tags=["skills"])
app.include_router(events.router, prefix="/api", tags=["events"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(push.router, prefix="/api", tags=["push"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(files.router, prefix="/api", tags=["files"])
app.include_router(widget.router, prefix="/api", tags=["widget"])
app.include_router(databases.router, prefix="/api", tags=["databases"])
app.include_router(heartbeat.router, prefix="/api", tags=["heartbeat"])
app.include_router(custom_mcp.router, prefix="/api", tags=["custom-mcp"])
app.include_router(consolidation.router, prefix="/api", tags=["consolidation"])
app.include_router(evolution.router, prefix="/api", tags=["evolution"])
app.include_router(orchestrator.router, prefix="/api", tags=["orchestrator"])

@app.get("/")
async def root():
    return {"message": "Edward API is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
