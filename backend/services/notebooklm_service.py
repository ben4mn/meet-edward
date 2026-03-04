"""
Google NotebookLM service for Edward.

Provides programmatic access to Google NotebookLM for creating knowledge bases,
adding sources, querying notebooks, running research, and generating artifacts.

Uses the notebooklm-py library (undocumented Google APIs).
Credentials persist ~1-2 weeks after browser login via `notebooklm login`.
"""

import os
from typing import Optional, List, Dict, Any
from pathlib import Path


# Configuration
NOTEBOOKLM_STORAGE_PATH = os.getenv("NOTEBOOKLM_STORAGE_PATH")
NOTEBOOKLM_AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON")

# Default storage location
DEFAULT_STORAGE_PATH = Path.home() / ".notebooklm" / "storage_state.json"

# Singleton client and context manager references
_client: Optional[Any] = None
_context_manager: Optional[Any] = None


def is_configured() -> bool:
    """Check if NotebookLM credentials are configured."""
    if NOTEBOOKLM_AUTH_JSON:
        return True

    storage_path = Path(NOTEBOOKLM_STORAGE_PATH) if NOTEBOOKLM_STORAGE_PATH else DEFAULT_STORAGE_PATH
    return storage_path.exists()


def get_status() -> dict:
    """Get NotebookLM service status."""
    if not is_configured():
        return {
            "status": "error",
            "status_message": "NotebookLM credentials not found. Run: notebooklm login",
        }

    if _client is not None:
        return {
            "status": "connected",
            "status_message": "NotebookLM client active",
        }

    return {
        "status": "connected",
        "status_message": "Credentials configured",
    }


async def _get_client() -> Any:
    """
    Get or create the NotebookLM client singleton.

    Uses async context manager for proper lifecycle management.
    Client is created on first use and kept alive until shutdown.
    """
    global _client, _context_manager

    if _client is not None:
        return _client

    try:
        from notebooklm import NotebookLMClient
    except ImportError:
        raise Exception(
            "notebooklm-py not installed. Run: pip install 'notebooklm-py[browser]'"
        )

    context_manager = await NotebookLMClient.from_storage()
    client = await context_manager.__aenter__()

    _client = client
    _context_manager = context_manager

    return client


async def _resolve_notebook_id(notebook_name: str) -> Optional[str]:
    """Resolve notebook name to ID via case-insensitive match."""
    client = await _get_client()
    notebooks = await client.notebooks.list()

    name_lower = notebook_name.lower()
    for nb in notebooks:
        if getattr(nb, "title", "").lower() == name_lower:
            return getattr(nb, "id", None)

    return None


# ============================================================================
# NOTEBOOK OPERATIONS
# ============================================================================


async def list_notebooks() -> List[Dict[str, Any]]:
    """List all notebooks."""
    client = await _get_client()
    notebooks = await client.notebooks.list()

    return [
        {
            "id": getattr(nb, "id", None),
            "name": getattr(nb, "title", "Untitled"),
        }
        for nb in notebooks
    ]


async def create_notebook(name: str) -> Dict[str, Any]:
    """Create a new notebook."""
    client = await _get_client()
    notebook = await client.notebooks.create(name)

    return {
        "id": getattr(notebook, "id", None),
        "name": getattr(notebook, "title", name),
    }


async def delete_notebook(notebook_name: str) -> bool:
    """Delete a notebook by name. Returns True if deleted, False if not found."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        return False

    client = await _get_client()
    await client.notebooks.delete(notebook_id)
    return True


# ============================================================================
# SOURCE OPERATIONS
# ============================================================================


async def add_url_source(notebook_name: str, url: str) -> Dict[str, Any]:
    """Add a URL source to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    source = await client.sources.add_url(notebook_id, url, wait=True)

    return {
        "source_id": getattr(source, "id", None),
        "title": getattr(source, "title", "Untitled"),
        "type": getattr(source, "type", "url"),
        "status": getattr(source, "status", "unknown"),
    }


async def add_youtube_source(notebook_name: str, url: str) -> Dict[str, Any]:
    """Add a YouTube video source to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    source = await client.sources.add_youtube(notebook_id, url)

    return {
        "source_id": getattr(source, "id", None),
        "title": getattr(source, "title", "Untitled"),
        "type": getattr(source, "type", "youtube"),
        "status": getattr(source, "status", "unknown"),
    }


async def add_text_source(
    notebook_name: str, text: str, title: Optional[str] = None
) -> Dict[str, Any]:
    """Add a text source to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    source = await client.sources.add_text(notebook_id, title or "Text Source", text)

    return {
        "source_id": getattr(source, "id", None),
        "title": getattr(source, "title", title or "Text Source"),
        "type": getattr(source, "type", "text"),
        "status": getattr(source, "status", "unknown"),
    }


async def add_file_source(notebook_name: str, file_path: str) -> Dict[str, Any]:
    """Add a file source (PDF, etc.) to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    source = await client.sources.add_file(notebook_id, file_path)

    return {
        "source_id": getattr(source, "id", None),
        "title": getattr(source, "title", Path(file_path).name),
        "type": getattr(source, "type", "file"),
        "status": getattr(source, "status", "unknown"),
    }


async def list_sources(notebook_name: str) -> List[Dict[str, Any]]:
    """List all sources in a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    sources = await client.sources.list(notebook_id)

    return [
        {
            "source_id": getattr(s, "id", None),
            "title": getattr(s, "title", "Untitled"),
            "type": getattr(s, "type", "unknown"),
            "status": getattr(s, "status", "unknown"),
        }
        for s in sources
    ]


async def get_source_fulltext(notebook_name: str, source_id: str) -> str:
    """Get the indexed fulltext of a source."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    result = await client.sources.get_fulltext(notebook_id, source_id)

    # Result may be a dataclass with .content or a plain string
    if hasattr(result, "content"):
        return getattr(result, "content", "")
    return str(result) if result else ""


async def delete_source(notebook_name: str, source_id: str) -> Dict[str, Any]:
    """Delete a source from a notebook. Returns source title if found."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()

    # Resolve source to get title for confirmation message
    sources = await client.sources.list(notebook_id)
    source_title = None
    for s in sources:
        if getattr(s, "id", None) == source_id:
            source_title = getattr(s, "title", "Untitled")
            break

    if source_title is None:
        raise Exception(f"Source '{source_id}' not found in notebook '{notebook_name}'")

    await client.sources.delete(notebook_id, source_id)
    return {"source_id": source_id, "title": source_title}


# ============================================================================
# CHAT/QUERY OPERATIONS
# ============================================================================


async def ask_notebook(notebook_name: str, question: str) -> Dict[str, Any]:
    """Ask a question grounded in notebook sources."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    result = await client.chat.ask(notebook_id, question)

    return {
        "answer": getattr(result, "answer", str(result)),
        "sources": getattr(result, "sources", []),
    }


# ============================================================================
# RESEARCH OPERATIONS
# ============================================================================


async def web_research(
    notebook_name: str, query: str, mode: str = "fast"
) -> Dict[str, Any]:
    """Run web research and auto-import discovered sources."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    result = await client.research.web_search(notebook_id, query, mode=mode)

    return {
        "status": "completed",
        "result": str(result) if result else "Research completed",
    }


# ============================================================================
# ARTIFACT GENERATION
# ============================================================================


async def generate_artifact(
    notebook_name: str,
    artifact_type: str,
    instructions: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate an artifact from notebook sources.

    Args:
        notebook_name: Notebook name
        artifact_type: audio, video, quiz, flashcards, slide_deck, infographic,
                       mind_map, data_table, report
        instructions: Optional generation instructions
    """
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()

    if artifact_type == "audio":
        result = await client.artifacts.generate_audio(
            notebook_id, instructions=instructions or ""
        )
    elif artifact_type == "video":
        result = await client.artifacts.generate_video(notebook_id)
    elif artifact_type == "quiz":
        result = await client.artifacts.generate_quiz(notebook_id)
    elif artifact_type == "flashcards":
        result = await client.artifacts.generate_flashcards(notebook_id)
    elif artifact_type == "slide_deck":
        result = await client.artifacts.generate_slide_deck(notebook_id)
    elif artifact_type == "infographic":
        result = await client.artifacts.generate_infographic(notebook_id)
    elif artifact_type == "mind_map":
        result = await client.artifacts.generate_mind_map(notebook_id)
    elif artifact_type == "data_table":
        result = await client.artifacts.generate_data_table(
            notebook_id, description=instructions or ""
        )
    elif artifact_type == "report":
        result = await client.artifacts.generate_report(notebook_id)
    else:
        raise Exception(
            f"Unknown artifact type: {artifact_type}. "
            "Valid: audio, video, quiz, flashcards, slide_deck, infographic, "
            "mind_map, data_table, report"
        )

    return {
        "task_id": getattr(result, "task_id", None),
        "status": getattr(result, "status", "started"),
    }


async def wait_artifact(notebook_name: str, task_id: str) -> Dict[str, Any]:
    """Wait for artifact generation to complete."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    result = await client.artifacts.wait_for_completion(notebook_id, task_id)

    status = getattr(result, "status", "unknown")
    return {
        "status": status,
        "ready": status == "completed",
    }


# ============================================================================
# LIFECYCLE HOOKS
# ============================================================================


async def initialize_notebooklm():
    """
    Initialize NotebookLM client on startup.

    Checks if credentials exist and attempts to create the client.
    Gracefully fails if credentials are missing or invalid.
    """
    if not is_configured():
        print("NotebookLM credentials not found, skipping initialization")
        return

    try:
        await _get_client()
        print("NotebookLM client initialized")
    except Exception as e:
        print(f"NotebookLM client initialization failed: {e}")


async def shutdown_notebooklm():
    """Shutdown NotebookLM client. Called from main.py lifespan shutdown."""
    global _client, _context_manager

    if _context_manager is not None:
        try:
            await _context_manager.__aexit__(None, None, None)
            print("NotebookLM client shutdown complete")
        except Exception as e:
            print(f"NotebookLM shutdown error: {e}")
        finally:
            _client = None
            _context_manager = None
