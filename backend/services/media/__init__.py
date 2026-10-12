"""Media: Edward sees what people send him (2026-10-11; docs/architecture/media.md).

``view`` is the one call the ``look`` tool makes. It is a lazy wrapper, so
importing this package never pulls the converters in. The body lives in
``services/media/viewer.py``: a submodule named ``view`` would shadow this
function once imported.

Refs: ``attachment:<chat.db rowid>``, ``file:<id>`` and local paths. Any
other ref (a URL; ``media:<id>``, which needs a media store this build does
not have) returns an error starting ``unsupported:``, and ``look`` then uses
its own path for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MediaView:
    kind: str                                            # image|video|audio|pdf|link|file
    images: List[bytes] = field(default_factory=list)    # JPEG, long edge <= 1600, never upscaled
    text: str = ""                                       # description / transcript / link metadata / extracted text
    pdf: Optional[bytes] = None                          # for a document block (<= 15 MB, <= 100 pages)
    error: Optional[str] = None


async def view(ref: str, *, max_images: int = 6) -> MediaView:
    """What ``ref`` looks like."""
    try:
        from services.media.viewer import view as _view
    except ImportError:
        return MediaView(kind="file", error="unsupported: media is not installed yet")
    return await _view(ref, max_images=max_images)
