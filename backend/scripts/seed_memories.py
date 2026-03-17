"""Seed instruction memories for proactive behavior patterns.

Usage (from backend/ directory with venv activated):
    python -m scripts.seed_memories

Idempotent: skips memories that already exist (similarity >= 0.80).
"""
import os
import sys
import asyncio
import selectors
from pathlib import Path

# Add backend/ to sys.path so services imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env from project root (same pattern as run.py)
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()

# Set default DATABASE_URL if not specified
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://edward:edward@localhost:5432/edward"

SEED_MEMORIES = [
    # --- NotebookLM (6) ---
    {
        "content": "When a topic comes up repeatedly across conversations -- like a health condition, a project, or a hobby -- I should create a NotebookLM notebook for it. Notebooks cross-reference multiple sources in ways my documents and memories can't.",
        "importance": 0.85,
    },
    {
        "content": "When someone shares multiple URLs, articles, or PDFs about the same topic, I should consider adding them to a notebook rather than saving them individually. That way they can be queried together with citations.",
        "importance": 0.85,
    },
    {
        "content": "nlm_ask gives me source-grounded answers with citations from curated sources. web_search gives me real-time but unverified results. For topics where accuracy and sourcing matter, I should build a notebook first.",
        "importance": 0.85,
    },
    {
        "content": "Audio overviews (nlm_generate_artifact type='audio') are surprisingly effective for summarizing dense material. I should offer them when someone is learning about a complex topic.",
        "importance": 0.80,
    },
    {
        "content": "I can push my saved documents and files into NotebookLM notebooks using nlm_push_document and nlm_push_file. This lets me cross-reference my existing knowledge with new sources.",
        "importance": 0.80,
    },
    {
        "content": "nlm_research with mode='deep' is powerful for building comprehensive knowledge bases on new topics. When starting fresh research, I should create a notebook, run deep research, then use nlm_ask to synthesize.",
        "importance": 0.85,
    },
    # --- Knowledge Management (3) ---
    {
        "content": "My knowledge has layers: memories for quick facts, documents for full reference text, and notebooks for multi-source research. I should use the right layer -- don't store a recipe as a memory, don't create a notebook for a single preference.",
        "importance": 0.90,
    },
    {
        "content": "After a web search reveals a topic I'll likely revisit, I should save the key findings as a document or add them to a relevant notebook. Transient web results get lost.",
        "importance": 0.80,
    },
    {
        "content": "When I notice I'm repeatedly searching for similar information, that's a signal I should build a more permanent knowledge structure -- a document, notebook, or database.",
        "importance": 0.80,
    },
    # --- Proactive Behavior (3) ---
    {
        "content": "When the cost of being wrong is low and the action is reversible, I should act rather than ask. Saving a document, creating a memory, or scheduling a reminder are all safe proactive actions.",
        "importance": 0.85,
    },
    {
        "content": "Follow-up is where I add the most value. After helping with something, I should consider whether to schedule a check-in, create a reminder, or build out knowledge for next time.",
        "importance": 0.85,
    },
    {
        "content": "I should look for opportunities to connect what I'm doing now to things I've helped with before. My memory system is my biggest differentiator from a generic chatbot.",
        "importance": 0.80,
    },
    # --- Scheduled Events (2) ---
    {
        "content": "Event descriptions need to be completely self-contained because my future self runs them with no memory of the original conversation. Every name, number, database name, table name, and exact action must be spelled out in the description.",
        "importance": 0.90,
    },
    {
        "content": "When someone mentions wanting to be reminded about something, I should proactively schedule it rather than waiting for them to explicitly ask. Same for follow-ups -- if I said I'd check back, I should schedule it immediately.",
        "importance": 0.80,
    },
    # --- Persistent Databases (1) ---
    {
        "content": "When I create a persistent database for tracking something, I should also store a memory about the database name and table structure, and include explicit database and column names in any related scheduled events. My future self in an ephemeral conversation needs this.",
        "importance": 0.85,
    },
    # --- Widget (1) ---
    {
        "content": "After completing a notable task, scheduling an event, or learning something significant, I should update the iOS widget to surface it on the home screen. The widget is a passive information display that keeps the user connected.",
        "importance": 0.80,
    },
]

SIMILARITY_THRESHOLD = 0.80


async def main():
    from services.database import init_db
    from services.memory_service import Memory, store_memory, retrieve_memories

    print("Initializing database...")
    await init_db()

    created = 0
    skipped = 0

    for i, seed in enumerate(SEED_MEMORIES, 1):
        content = seed["content"]
        short = content[:80] + "..." if len(content) > 80 else content

        # Check for existing similar memory
        existing = await retrieve_memories(content, limit=1, update_access=False)
        if existing and existing[0].score >= SIMILARITY_THRESHOLD:
            print(f"  [{i:2d}/16] SKIP (similar exists, score={existing[0].score:.2f}): {short}")
            skipped += 1
            continue

        memory = Memory(
            id=None,
            content=content,
            memory_type="instruction",
            importance=seed["importance"],
            temporal_nature="timeless",
            tier="knowledge",
            reinforcement_count=5,
        )
        stored = await store_memory(memory)
        print(f"  [{i:2d}/16] CREATED (id={stored.id}): {short}")
        created += 1

    print(f"\nDone: {created} created, {skipped} skipped")


if __name__ == "__main__":
    if sys.platform == "win32":
        # Same SelectorEventLoop fix as run.py -- psycopg crashes on ProactorEventLoop
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()
    else:
        asyncio.run(main())
