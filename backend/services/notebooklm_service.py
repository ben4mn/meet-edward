"""
Google NotebookLM service for Edward.

Uses notebooklm-mcp-cli library (cookie-based auth, 3-layer recovery).
Initial setup: run `nlm login` to extract cookies from Chrome.
"""

import asyncio
from typing import Optional, List, Dict, Any
from pathlib import Path


# Singleton client
_client: Optional[Any] = None


def is_configured() -> bool:
    """Check if NotebookLM credentials are configured."""
    try:
        from notebooklm_tools.core.auth import load_cached_tokens
        tokens = load_cached_tokens()
        return tokens is not None and bool(getattr(tokens, 'cookies', None))
    except Exception:
        return False


def get_status() -> dict:
    """Get NotebookLM service status."""
    if not is_configured():
        return {
            "status": "error",
            "status_message": "NotebookLM credentials not found. Run: nlm login",
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

    Client is synchronous — all calls must be wrapped in asyncio.to_thread().
    """
    global _client

    if _client is not None:
        return _client

    try:
        from notebooklm_tools.core.client import NotebookLMClient
        from notebooklm_tools.core.auth import load_cached_tokens
    except ImportError:
        raise Exception(
            "notebooklm-mcp-cli not installed. Run: pip install notebooklm-mcp-cli"
        )

    tokens = load_cached_tokens()
    if not tokens or not getattr(tokens, 'cookies', None):
        raise Exception(
            "NotebookLM credentials not found. Run: nlm login"
        )

    # Pass cached csrf_token/session_id so constructor skips the blocking
    # HTTPS fetch to notebooklm.google.com. Still wrap in to_thread as
    # safety net (httpx.Client creation can do DNS resolution, etc.).
    csrf = getattr(tokens, 'csrf_token', '') or ''
    sid = getattr(tokens, 'session_id', '') or ''
    client = await asyncio.to_thread(
        NotebookLMClient, tokens.cookies, csrf, sid
    )
    _client = client
    return client


def _extract_field(obj, field: str, default=None):
    """Extract a field from either a dict or object response."""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


async def _resolve_notebook_id(notebook_name: str) -> Optional[str]:
    """Resolve notebook name to ID via case-insensitive match."""
    client = await _get_client()
    notebooks = await asyncio.to_thread(client.list_notebooks)

    name_lower = notebook_name.lower()
    for nb in notebooks:
        title = _extract_field(nb, "title", "")
        nb_id = _extract_field(nb, "id", None)
        if title.lower() == name_lower:
            return nb_id

    return None


# ============================================================================
# NOTEBOOK OPERATIONS
# ============================================================================


async def list_notebooks() -> List[Dict[str, Any]]:
    """List all notebooks."""
    client = await _get_client()
    notebooks = await asyncio.to_thread(client.list_notebooks)

    return [
        {
            "id": _extract_field(nb, "id", None),
            "name": _extract_field(nb, "title", "Untitled"),
        }
        for nb in notebooks
    ]


async def create_notebook(name: str) -> Dict[str, Any]:
    """Create a new notebook."""
    client = await _get_client()
    notebook = await asyncio.to_thread(client.create_notebook, title=name)

    return {
        "id": _extract_field(notebook, "id", None),
        "name": _extract_field(notebook, "title", name),
    }


async def delete_notebook(notebook_name: str) -> bool:
    """Delete a notebook by name. Returns True if deleted, False if not found."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        return False

    client = await _get_client()
    await asyncio.to_thread(client.delete_notebook, notebook_id)
    return True


async def get_notebook(notebook_name: str) -> Dict[str, Any]:
    """Get notebook details."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.get_notebook, notebook_id)
    if isinstance(result, dict):
        return result
    return {"id": notebook_id, "name": notebook_name}


async def describe_notebook(notebook_name: str) -> Dict[str, Any]:
    """Get AI-generated notebook summary and suggested topics."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.get_notebook_summary, notebook_id)
    if isinstance(result, dict):
        return {
            "summary": result.get("summary", ""),
            "suggested_topics": result.get("suggested_topics", []),
        }
    return {
        "summary": getattr(result, "summary", str(result)),
        "suggested_topics": getattr(result, "suggested_topics", []),
    }


async def rename_notebook(notebook_name: str, new_title: str) -> bool:
    """Rename a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.rename_notebook, notebook_id, new_title)
    return bool(result)


# ============================================================================
# SOURCE OPERATIONS
# ============================================================================


def _source_dict(source, default_type: str = "unknown") -> Dict[str, Any]:
    """Build standard source dict from library response."""
    return {
        "source_id": _extract_field(source, "id", None),
        "title": _extract_field(source, "title", "Untitled"),
        "type": _extract_field(source, "type", default_type),
        "status": _extract_field(source, "status", "unknown"),
    }


async def add_url_source(notebook_name: str, url: str) -> Dict[str, Any]:
    """Add a URL source to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    source = await asyncio.to_thread(client.add_url_source, notebook_id, url, wait=True)
    return _source_dict(source, "url")


async def add_youtube_source(notebook_name: str, url: str) -> Dict[str, Any]:
    """Add a YouTube video source to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    # New library auto-detects YouTube URLs in add_url_source
    source = await asyncio.to_thread(client.add_url_source, notebook_id, url)
    return _source_dict(source, "youtube")


async def add_text_source(
    notebook_name: str, text: str, title: Optional[str] = None
) -> Dict[str, Any]:
    """Add a text source to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    # NOTE: arg order swap from old library — new: (nb_id, text, title=title)
    source = await asyncio.to_thread(
        client.add_text_source, notebook_id, text, title=title or "Text Source"
    )
    return _source_dict(source, "text")


async def add_file_source(notebook_name: str, file_path: str) -> Dict[str, Any]:
    """Add a file source (PDF, etc.) to a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    source = await asyncio.to_thread(client.add_file, notebook_id, file_path)
    return _source_dict(source, "file")


async def add_drive_source(
    notebook_name: str,
    document_id: str,
    title: str,
    mime_type: str = "application/vnd.google-apps.document",
) -> Dict[str, Any]:
    """Add a Google Drive document as source."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(
        client.add_drive_source, notebook_id, document_id, title, mime_type, wait=True
    )
    return _source_dict(result, "drive")


async def list_sources(notebook_name: str) -> List[Dict[str, Any]]:
    """List all sources in a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    sources = await asyncio.to_thread(client.get_notebook_sources_with_types, notebook_id)

    return [_source_dict(s) for s in sources]


async def get_source_fulltext(notebook_name: str, source_id: str) -> str:
    """Get the indexed fulltext of a source."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    # New API: no nb_id needed, returns dict
    result = await asyncio.to_thread(client.get_source_fulltext, source_id)

    if isinstance(result, dict):
        return result.get("content", result.get("text", str(result)))
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
    sources = await asyncio.to_thread(client.get_notebook_sources_with_types, notebook_id)
    source_title = None
    for s in sources:
        sid = _extract_field(s, "id", None)
        if sid == source_id:
            source_title = _extract_field(s, "title", "Untitled")
            break

    if source_title is None:
        raise Exception(f"Source '{source_id}' not found in notebook '{notebook_name}'")

    # New API: no nb_id needed for delete
    await asyncio.to_thread(client.delete_source, source_id)
    return {"source_id": source_id, "title": source_title}


async def rename_source(notebook_name: str, source_id: str, new_title: str) -> bool:
    """Rename a source in a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.rename_source, notebook_id, source_id, new_title)
    return bool(result)


async def describe_source(source_id: str) -> Dict[str, Any]:
    """Get AI-generated source summary with keywords."""
    client = await _get_client()
    result = await asyncio.to_thread(client.get_source_guide, source_id)
    if isinstance(result, dict):
        return {
            "summary": result.get("summary", ""),
            "keywords": result.get("keywords", []),
        }
    return {
        "summary": getattr(result, "summary", str(result)),
        "keywords": getattr(result, "keywords", []),
    }


# ============================================================================
# CHAT/QUERY OPERATIONS
# ============================================================================


async def ask_notebook(notebook_name: str, question: str) -> Dict[str, Any]:
    """Ask a question grounded in notebook sources."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    result = await asyncio.to_thread(client.query, notebook_id, question)

    if isinstance(result, dict):
        return {
            "answer": result.get("answer", result.get("text", str(result))),
            "sources": result.get("citations", result.get("sources", [])),
        }
    return {
        "answer": getattr(result, "answer", str(result)),
        "sources": getattr(result, "citations", getattr(result, "sources", [])),
    }


async def configure_chat(
    notebook_name: str,
    goal: str = "default",
    custom_prompt: Optional[str] = None,
    response_length: str = "default",
) -> Dict[str, Any]:
    """Configure notebook chat settings."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(
        client.configure_chat, notebook_id, goal, custom_prompt, response_length
    )
    if isinstance(result, dict):
        return result
    return {"status": "configured"}


# ============================================================================
# RESEARCH OPERATIONS
# ============================================================================


async def web_research(
    notebook_name: str, query: str, mode: str = "fast"
) -> Dict[str, Any]:
    """Start web research (async — returns task_id for polling)."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    result = await asyncio.to_thread(client.start_research, notebook_id, query, mode=mode)

    if isinstance(result, dict):
        return {
            "status": "started",
            "task_id": result.get("task_id"),
            "result": str(result),
        }
    return {
        "status": "started",
        "task_id": getattr(result, "task_id", None),
        "result": str(result) if result else "Research started",
    }


async def poll_research(notebook_name: str, task_id: Optional[str] = None) -> Dict[str, Any]:
    """Check research progress."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    kwargs = {}
    if task_id:
        kwargs["target_task_id"] = task_id
    result = await asyncio.to_thread(client.poll_research, notebook_id, **kwargs)
    if isinstance(result, dict):
        return result
    return {"status": str(result)}


async def import_research_sources(
    notebook_name: str,
    task_id: str,
    source_indices: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Import discovered research sources into notebook.

    The library expects source dicts (with url, title, result_type), not indices.
    We poll the research first to get the source dicts, filter by indices if given,
    then pass the filtered dicts to the library.
    """
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()

    # Poll research to get the full source dicts
    poll_result = await asyncio.to_thread(
        client.poll_research, notebook_id, target_task_id=task_id
    )
    if not poll_result or not isinstance(poll_result, dict):
        raise Exception(f"Research task '{task_id}' not found or no results yet")

    all_sources = poll_result.get("sources", [])
    if not all_sources:
        raise Exception("No sources found in research results. Is the research complete?")

    # Filter by indices if specified, otherwise import all
    if source_indices is not None:
        sources_to_import = [
            s for s in all_sources
            if s.get("index") in source_indices
        ]
        if not sources_to_import:
            raise Exception(
                f"No sources match indices {source_indices}. "
                f"Available indices: {[s.get('index') for s in all_sources]}"
            )
    else:
        sources_to_import = all_sources

    result = await asyncio.to_thread(
        client.import_research_sources, notebook_id, task_id, sources_to_import
    )
    if isinstance(result, list):
        return result
    return [{"status": str(result)}]


# ============================================================================
# ARTIFACT GENERATION
# ============================================================================


async def generate_artifact(
    notebook_name: str,
    artifact_type: str,
    instructions: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate an artifact from notebook sources."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()

    method_map = {
        "audio": lambda: client.create_audio_overview(notebook_id, focus_prompt=instructions or ""),
        "video": lambda: client.create_video_overview(notebook_id, focus_prompt=instructions or ""),
        "quiz": lambda: client.create_quiz(notebook_id, focus_prompt=instructions or ""),
        "flashcards": lambda: client.create_flashcards(notebook_id, focus_prompt=instructions or ""),
        "slide_deck": lambda: client.create_slide_deck(notebook_id, focus_prompt=instructions or ""),
        "infographic": lambda: client.create_infographic(notebook_id, focus_prompt=instructions or ""),
        "mind_map": lambda: client.generate_mind_map(notebook_id),
        "data_table": lambda: client.create_data_table(notebook_id, description=instructions or ""),
        "report": lambda: client.create_report(notebook_id, custom_prompt=instructions or ""),
    }

    if artifact_type not in method_map:
        raise Exception(
            f"Unknown artifact type: {artifact_type}. "
            "Valid: audio, video, quiz, flashcards, slide_deck, infographic, "
            "mind_map, data_table, report"
        )

    result = await asyncio.to_thread(method_map[artifact_type])

    if isinstance(result, dict):
        return {
            "task_id": result.get("task_id", result.get("artifact_id")),
            "status": result.get("status", "started"),
        }
    return {
        "task_id": getattr(result, "task_id", getattr(result, "artifact_id", None)),
        "status": getattr(result, "status", "started"),
    }


async def wait_artifact(notebook_name: str, task_id: str) -> Dict[str, Any]:
    """Check artifact generation status."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")

    client = await _get_client()
    # New API: poll_studio_status returns list of all artifacts
    result = await asyncio.to_thread(client.poll_studio_status, notebook_id)

    # Find the matching artifact by task_id
    if isinstance(result, list):
        for artifact in result:
            aid = (
                _extract_field(artifact, "task_id")
                or _extract_field(artifact, "artifact_id")
                or _extract_field(artifact, "id")
            )
            if aid == task_id:
                status = _extract_field(artifact, "status", "unknown")
                return {"status": status, "ready": status in ("completed", "done", "ready")}
        # Not found by ID — return overall status
        return {"status": "unknown", "ready": False}

    status = _extract_field(result, "status", "unknown")
    return {"status": status, "ready": status in ("completed", "done", "ready")}


async def delete_artifact(notebook_name: str, artifact_id: str) -> bool:
    """Delete a studio artifact."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.delete_studio_artifact, artifact_id, notebook_id)
    return bool(result)


async def revise_slides(
    notebook_name: str,
    artifact_id: str,
    slide_instructions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Revise individual slides in a slide deck."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    # Convert dict list to tuple list for library API
    instructions_tuples = [
        (s["slide_number"], s["instruction"]) for s in slide_instructions
    ]
    result = await asyncio.to_thread(client.revise_slide_deck, artifact_id, instructions_tuples)
    if isinstance(result, dict):
        return result
    return {"status": "revised"}


# ============================================================================
# SHARING
# ============================================================================


async def get_share_status(notebook_name: str) -> Dict[str, Any]:
    """Get notebook sharing settings and collaborators."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.get_share_status, notebook_id)
    if isinstance(result, dict):
        return result
    # ShareStatus object — extract fields defensively
    return {
        "is_public": getattr(result, "is_public", False),
        "public_url": getattr(result, "public_url", None),
        "collaborators": getattr(result, "collaborators", []),
    }


async def share_public(notebook_name: str, is_public: bool = True) -> Dict[str, Any]:
    """Enable or disable public link access."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(client.set_public_access, notebook_id, is_public)
    if isinstance(result, str):
        return {"public_url": result, "is_public": is_public}
    return {"status": str(result), "is_public": is_public}


async def share_invite(
    notebook_name: str,
    email: str,
    role: str = "viewer",
) -> bool:
    """Invite a collaborator by email."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()
    result = await asyncio.to_thread(
        client.add_collaborator, notebook_id, email, role
    )
    return bool(result)


# ============================================================================
# NOTES
# ============================================================================


async def manage_note(
    notebook_name: str,
    action: str,
    note_id: Optional[str] = None,
    content: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Create, list, update, or delete notes in a notebook."""
    notebook_id = await _resolve_notebook_id(notebook_name)
    if not notebook_id:
        raise Exception(f"Notebook '{notebook_name}' not found")
    client = await _get_client()

    if action == "create":
        if not content:
            raise Exception("Content required for creating a note")
        result = await asyncio.to_thread(
            client.create_note, notebook_id, content, title=title
        )
    elif action == "list":
        result = await asyncio.to_thread(client.list_notes, notebook_id)
        if isinstance(result, list):
            return {"notes": result}
        return {"notes": []}
    elif action == "update":
        if not note_id:
            raise Exception("note_id required for updating a note")
        result = await asyncio.to_thread(
            client.update_note, note_id, content=content, title=title, notebook_id=notebook_id
        )
    elif action == "delete":
        if not note_id:
            raise Exception("note_id required for deleting a note")
        result = await asyncio.to_thread(client.delete_note, note_id, notebook_id)
        return {"deleted": bool(result)}
    else:
        raise Exception(f"Unknown action: {action}. Use: create, list, update, delete")

    if isinstance(result, dict):
        return result
    return {"status": str(result)}


# ============================================================================
# LIFECYCLE HOOKS
# ============================================================================


async def initialize_notebooklm():
    """Initialize NotebookLM client on startup."""
    if not is_configured():
        print("NotebookLM credentials not found, skipping initialization")
        return

    try:
        await _get_client()
        print("NotebookLM client initialized")
    except Exception as e:
        print(f"NotebookLM client initialization failed: {e}")


async def shutdown_notebooklm():
    """Shutdown NotebookLM client."""
    global _client
    if _client is not None:
        try:
            if hasattr(_client, 'close'):
                _client.close()
            print("NotebookLM client shutdown complete")
        except Exception as e:
            print(f"NotebookLM shutdown error: {e}")
        finally:
            _client = None
