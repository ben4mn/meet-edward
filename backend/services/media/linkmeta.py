"""iMessage's own link previews, read from chat.db (2026-10-11).

When someone sends a link, Messages fetches a preview on the sender's phone
and stores it with the message: ``message.payload_data`` is an
NSKeyedArchiver plist holding an ``LPLinkMetadata`` (title, summary, site,
the URL and the original URL), and the preview picture is the
``.pluginPayloadAttachment`` file beside it. That is free, offline and
works for pages that block bots or need a login.
"""

from __future__ import annotations

import plistlib
from typing import Any, Dict, Optional


def _resolve(objects: list, value: Any, depth: int = 0) -> Any:
    """Follow plistlib.UID references into $objects, recursively (bounded)."""
    if depth > 12:
        return None
    if isinstance(value, plistlib.UID):
        idx = value.data
        if 0 <= idx < len(objects):
            return _resolve(objects, objects[idx], depth + 1)
        return None
    if isinstance(value, dict):
        if "NS.relative" in value:  # NSURL
            rel = _resolve(objects, value.get("NS.relative"), depth + 1)
            return rel if isinstance(rel, str) else None
        if "NS.string" in value:
            s = _resolve(objects, value.get("NS.string"), depth + 1)
            return s if isinstance(s, str) else None
        if "NS.keys" in value and "NS.objects" in value:
            keys = [_resolve(objects, k, depth + 1) for k in value["NS.keys"]]
            vals = [_resolve(objects, v, depth + 1) for v in value["NS.objects"]]
            return {k: v for k, v in zip(keys, vals) if isinstance(k, str)}
        return {k: _resolve(objects, v, depth + 1) for k, v in value.items() if k != "$class"}
    if isinstance(value, list):
        return [_resolve(objects, v, depth + 1) for v in value]
    if value == "$null":
        return None
    return value


def _find(tree: Any, keys, depth: int = 0) -> Optional[str]:
    if depth > 8:
        return None
    if isinstance(tree, dict):
        for k in keys:
            v = tree.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in tree.values():
            found = _find(v, keys, depth + 1)
            if found:
                return found
    elif isinstance(tree, list):
        for v in tree:
            found = _find(v, keys, depth + 1)
            if found:
                return found
    return None


def parse_link_payload(blob: Optional[bytes]) -> Optional[Dict[str, Optional[str]]]:
    """{url, original_url, title, summary, site} from a URL balloon's
    payload_data, or None when the blob is not a readable preview. Pure."""
    if not blob:
        return None
    try:
        plist = plistlib.loads(bytes(blob))
    except Exception:  # noqa: BLE001 — anything unreadable is "no preview"
        return None
    objects = plist.get("$objects") if isinstance(plist, dict) else None
    top = plist.get("$top") if isinstance(plist, dict) else None
    if not isinstance(objects, list) or not isinstance(top, dict):
        return None
    root = _resolve(objects, top.get("root", next(iter(top.values()), None)))
    meta = (root.get("richLinkMetadata") if isinstance(root, dict) and isinstance(root.get("richLinkMetadata"), dict)
            else root)
    out = {
        "url": _find(meta, ("URL", "url")),
        "original_url": _find(meta, ("originalURL",)),
        "title": _find(meta, ("title",)),
        "summary": _find(meta, ("summary", "itemDescription")),
        "site": _find(meta, ("siteName",)),
    }
    return out if (out["url"] or out["original_url"] or out["title"]) else None
