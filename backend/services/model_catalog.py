"""The Codex model catalog: which GPT models this ChatGPT account can run.

The Codex CLI keeps the account's live catalog in ``~/.codex/models_cache.json``
(slug, display name, visibility, input modalities, reasoning levels). Edward
reads that file instead of carrying a hand-maintained list — the static list
in ``routers/settings.py`` fell behind twice (2026-05: gpt-5.2-codex; 2026-09:
gpt-5.4 retired, gpt-5.6-sol never listed), and every stale id turned into
HTTP 400 on every turn until someone noticed.

Everything here is pure and tolerant: a missing or corrupt file falls back to
:data:`STATIC_FALLBACK` and ``catalog_is_live()`` says so, so validation can
be strict when we know the catalog and lenient when we do not.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CATALOG_PATH = os.getenv(
    "CODEX_MODELS_CACHE_PATH", str(Path.home() / ".codex" / "models_cache.json")
)

# The model Edward prefers when nothing else decides — offered first in the
# picker, and the target when a saved id is no longer in the catalog.
PREFERRED_MODEL = "gpt-6-astra"


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    display_name: str
    supports_images: bool = True
    default_effort: Optional[str] = None
    efforts: Tuple[str, ...] = ()
    context_window: Optional[int] = None
    listed: bool = True
    description: str = ""


# What the catalog looked like on 2026-09-04. Used only when the cache file
# cannot be read; kept short and never treated as authoritative.
STATIC_FALLBACK: Tuple[ModelInfo, ...] = (
    ModelInfo("gpt-6-astra", "GPT-6 Astra", True, "medium", ("low", "medium", "high", "xhigh"), 272000),
    ModelInfo("gpt-5.6-terra", "GPT-5.6 Terra", True, "medium", ("low", "medium", "high", "xhigh"), 272000),
    ModelInfo("gpt-5.6-luna", "GPT-5.6 Luna", True, "medium", ("low", "medium", "high", "xhigh"), 272000),
    ModelInfo("gpt-5.5", "GPT-5.5", True, "medium", ("low", "medium", "high"), 272000),
)

_cache: Dict[str, object] = {"key": None, "models": None, "live": False}


def clear_cache() -> None:
    _cache.update(key=None, models=None, live=False)


def _parse(raw: dict) -> List[ModelInfo]:
    out: List[ModelInfo] = []
    for m in raw.get("models") or []:
        if not isinstance(m, dict) or not m.get("slug"):
            continue
        levels = m.get("supported_reasoning_levels") or []
        efforts = tuple(
            (lvl.get("effort") if isinstance(lvl, dict) else str(lvl))
            for lvl in levels
            if (lvl.get("effort") if isinstance(lvl, dict) else lvl)
        )
        default_effort = m.get("default_reasoning_level")
        if default_effort and efforts and default_effort not in efforts:
            default_effort = None
        modalities = m.get("input_modalities")
        supports_images = True if modalities is None else ("image" in modalities)
        out.append(ModelInfo(
            slug=str(m["slug"]),
            display_name=str(m.get("display_name") or m["slug"]),
            supports_images=supports_images,
            default_effort=default_effort,
            efforts=efforts,
            context_window=m.get("context_window"),
            listed=(m.get("visibility", "list") == "list") and bool(m.get("supported_in_api", True)),
            description=str(m.get("description") or ""),
        ))
    return out


def load_catalog(path: Optional[str] = None) -> List[ModelInfo]:
    """Every model in the cache file (hidden ones included); the static
    fallback when the file is unreadable. Re-read when the file changes."""
    p = Path(path or CATALOG_PATH).expanduser()
    try:
        stat = p.stat()
        key = (str(p), stat.st_mtime_ns, stat.st_size)
    except OSError:
        _cache.update(key=None, models=list(STATIC_FALLBACK), live=False)
        return list(STATIC_FALLBACK)
    if _cache["key"] == key and _cache["models"] is not None:
        return list(_cache["models"])  # type: ignore[arg-type]
    try:
        models = _parse(json.loads(p.read_text()))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("model_catalog: cannot read %s (%s); using the static fallback", p, exc)
        _cache.update(key=None, models=list(STATIC_FALLBACK), live=False)
        return list(STATIC_FALLBACK)
    if not models:
        _cache.update(key=None, models=list(STATIC_FALLBACK), live=False)
        return list(STATIC_FALLBACK)
    _cache.update(key=key, models=models, live=True)
    return list(models)


def catalog_is_live() -> bool:
    load_catalog()
    return bool(_cache["live"])


def listed_models() -> List[ModelInfo]:
    return [m for m in load_catalog() if m.listed]


def get_model(slug: Optional[str]) -> Optional[ModelInfo]:
    if not slug:
        return None
    for m in load_catalog():
        if m.slug == slug:
            return m
    return None


def is_known(slug: Optional[str]) -> bool:
    return get_model(slug) is not None


def supports_images(slug: Optional[str]) -> bool:
    """Unknown models are assumed able to see — a wrong 'no' would hide
    every photo, a wrong 'yes' costs one clear 400."""
    m = get_model(slug)
    return True if m is None else m.supports_images


def default_effort(slug: Optional[str]) -> Optional[str]:
    m = get_model(slug)
    return m.default_effort if m else None


def effort_for(slug: Optional[str], requested: Optional[str]) -> Optional[str]:
    """The reasoning effort to send: the requested one when the model
    supports it, else the model's default, else the request as given."""
    m = get_model(slug)
    if m is None:
        return requested
    if requested and (not m.efforts or requested in m.efforts):
        return requested
    return m.default_effort


def recommended_model() -> str:
    listed = listed_models()
    if any(m.slug == PREFERRED_MODEL for m in listed):
        return PREFERRED_MODEL
    return listed[0].slug if listed else PREFERRED_MODEL


def resolve_chat_model(requested: Optional[str]) -> Tuple[str, Optional[str]]:
    """(model to use, reason it changed or None).

    Known ids pass through. Anthropic ids and empty values go to the
    recommended model (this catalog serves the Codex fallback only). With a live catalog an unknown
    GPT id is rewritten too — that is the retired-model case (gpt-5.4 on
    2026-09-04) which used to fail every turn. Without a live catalog a GPT
    id is trusted: a stale cache must never veto a real model.
    """
    fallback = recommended_model()
    if not requested or not str(requested).startswith("gpt-"):
        return fallback, f"'{requested or ''}' is not a GPT model; using {fallback}"
    if is_known(requested):
        return requested, None
    if catalog_is_live():
        return fallback, (
            f"'{requested}' is not in the Codex catalog "
            f"({', '.join(m.slug for m in listed_models())}); using {fallback}"
        )
    return requested, None


def validate_model_choice(requested: Optional[str]) -> Optional[str]:
    """None when the id may be saved, else a message naming the catalog."""
    model, _ = resolve_chat_model(requested)
    if model == requested:
        return None
    offered = ", ".join(m.slug for m in listed_models())
    return (
        f"'{requested or ''}' is not a model this ChatGPT account can run. "
        f"Pick one of: {offered} (recommended: {recommended_model()})."
    )


def vision_model(preferred: Optional[str]) -> str:
    """A model that accepts images: the preferred one when it can see,
    else the recommended one, else the first listed model that can."""
    model, _ = resolve_chat_model(preferred)
    if supports_images(model):
        return model
    rec = recommended_model()
    if supports_images(rec):
        return rec
    for m in listed_models():
        if m.supports_images:
            return m.slug
    return model


def picker_entries() -> List[dict]:
    rec = recommended_model()
    out = []
    for m in listed_models():
        out.append({
            "id": m.slug,
            "name": f"{m.display_name} (Recommended)" if m.slug == rec else m.display_name,
            "description": m.description,
            "supports_images": m.supports_images,
            "default_effort": m.default_effort,
            "context_window": m.context_window,
        })
    return out
