"""Where this Edward runs: its checkout, its public URL, its launchd labels.

One place for the facts that differ from one host to the next, all from the
environment with defaults that work on a fresh checkout:

* ``EDWARD_LIVE_REPO`` — the checkout the running backend serves from
  (default: this checkout). Edward's own shell may never write here.
* ``EDWARD_PUBLIC_BASE_URL`` — the address the owner opens Edward at
  (default ``http://localhost:3000``). Links in texts and pushes use it.
* ``EDWARD_PUBLIC_SCREEN_URL`` — the ``/screen`` page (default: the base
  URL plus ``/screen``).
* ``EDWARD_CORS_ORIGINS`` — extra browser origins allowed to call the API
  (comma-separated); the base URL's origin is always allowed.
* ``EDWARD_LAUNCHD_PREFIX`` — the prefix of Edward's LaunchAgent labels
  (default ``com.meet-edward``: ``com.meet-edward.backend`` …;
  ``scripts/install-launchagents.sh`` installs them).
* ``EDWARD_TUNNEL_LABEL`` — the LaunchAgent label of a tunnel in front of
  Edward, if any (e.g. ``com.cloudflare.edward``); unset, nothing checks one.
* ``EDWARD_BACKUP_DIR`` — where nightly backups go (default
  ``~/edward-backups``).

Read on every call, so tests can ``monkeypatch.setenv`` them.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import FrozenSet, Optional, Tuple
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://localhost:3000"
DEFAULT_LAUNCHD_PREFIX = "com.meet-edward"

# backend/services/host_config.py -> the checkout root
_CHECKOUT = Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    """The live checkout: ``EDWARD_LIVE_REPO``, else the one this code is in."""
    raw = (os.getenv("EDWARD_LIVE_REPO") or "").strip()
    return Path(raw).expanduser() if raw else _CHECKOUT


def public_base_url() -> str:
    raw = (os.getenv("EDWARD_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    screen = (os.getenv("EDWARD_PUBLIC_SCREEN_URL") or "").strip()
    if screen:
        p = urlparse(screen)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    return DEFAULT_BASE_URL


def public_screen_url() -> str:
    raw = (os.getenv("EDWARD_PUBLIC_SCREEN_URL") or "").strip()
    return raw or public_base_url() + "/screen"


def cors_origins() -> Tuple[str, ...]:
    origins = [public_base_url()]
    raw = os.getenv("EDWARD_CORS_ORIGINS") or ""
    origins += [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    seen, out = set(), []
    for o in origins:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return tuple(out)


def local_hostname() -> str:
    """This Mac's Bonjour name (``my-mac.local``), lowercased."""
    name = (socket.gethostname() or "").strip().lower()
    if name and not name.endswith(".local"):
        name += ".local"
    return name


def own_hosts() -> FrozenSet[str]:
    """Hostnames that are Edward's own pages (his public URL, this Mac)."""
    hosts = {"localhost", "127.0.0.1"}
    for url in (public_base_url(), public_screen_url()):
        h = urlparse(url).hostname
        if h:
            hosts.add(h.lower())
    lh = local_hostname()
    if lh:
        hosts.add(lh)
    return frozenset(hosts)


def launchd_prefix() -> str:
    return (os.getenv("EDWARD_LAUNCHD_PREFIX") or "").strip().rstrip(".") or DEFAULT_LAUNCHD_PREFIX


def launchd_label(service: str) -> str:
    """``backend`` -> ``com.meet-edward.backend``."""
    return f"{launchd_prefix()}.{service}"


def tunnel_label() -> Optional[str]:
    return (os.getenv("EDWARD_TUNNEL_LABEL") or "").strip() or None


def backup_dir() -> Path:
    raw = (os.getenv("EDWARD_BACKUP_DIR") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "edward-backups"
