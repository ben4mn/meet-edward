"""Who is really on the other end of a request. Pure but for the request
and ``FORWARDED_ALLOW_IPS``.

Edward's backend is reached three ways, and each looks different here:

* **Next's ``/api`` rewrite** on the same Mac (every browser request, the
  phone's and the hub's included): the peer is 127.0.0.1, Next adds
  ``X-Forwarded-Host`` (the host the browser used) and passes every header
  the client sent through untouched. It adds no ``X-Forwarded-For``.
* **A tunnel** (cloudflared) to :3000 or :8000: the peer is 127.0.0.1,
  Cloudflare sets ``CF-Connecting-IP`` (overwriting any the client sent)
  and appends the client to ``X-Forwarded-For``.
* **Straight to :8000**: from this Mac (curl, Edward's Chrome extension)
  the peer is loopback with no proxy headers; from the LAN the peer is the
  device's own address.

Uvicorn's proxy-header handling (on by default, trusting 127.0.0.1) may
already have replaced ``request.client`` with an ``X-Forwarded-For`` entry
before the app sees it; it marks that by setting the port to 0, so a
rewritten client still counts as having come through a loopback proxy —
unless ``FORWARDED_ALLOW_IPS`` trusts more than loopback, when it proves
nothing about the real peer (leave it unset).

Three questions:

* :func:`client_ip` — the key the login limiter counts by
  (``routers/auth.py``, ``routers/photo_door.py``).
* :func:`is_local_client` — was the original client on this Mac? A second
  check on the first password (``POST /api/auth/setup``), behind the
  one-time setup code (services/setup_code.py), which is the real proof.
* :func:`is_direct_loopback` — a loopback peer with no proxy header at all:
  the only shape of the Chrome extension's socket
  (``routers/browser_ext.py``).

The limit: a LAN device that reaches Next on :3000 controls every header
Next passes through (``X-Forwarded-For``, even ``CF-Connecting-IP``), so a
per-client key can be rotated from the LAN. The login limiter also keeps a
global ceiling for that reason, and :func:`is_local_client` checks the
host the browser asked for (``X-Forwarded-Host``), which a browser cannot
forge but a hand-made request can — so nothing that matters is decided by
it alone.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, List, Optional, Tuple

# Any of these means the request came through a proxy or a tunnel.
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded", "cf-connecting-ip")
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
# A rate-limit key is a header value; keep it short.
MAX_KEY_CHARS = 64


def _strip_host(value: Any) -> str:
    """``[::1]:3000`` -> ``::1``, ``127.0.0.1:3000`` -> ``127.0.0.1``,
    ``localhost:3000`` -> ``localhost``; quotes and whitespace dropped."""
    h = str(value or "").strip().strip('"').strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end > 0 else h[1:]
    if h.count(":") == 1:  # host:port (a bare IPv6 address has several)
        h = h.split(":", 1)[0]
    return h


def is_loopback(host: Any) -> bool:
    """127.0.0.0/8, ::1, an IPv4-mapped loopback, or ``localhost``."""
    h = _strip_host(host)
    if not h:
        return False
    if h in _LOOPBACK_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_loopback


def _peer(conn: Any) -> Tuple[str, Optional[int]]:
    client = getattr(conn, "client", None)
    if not client:
        return "", None
    return str(getattr(client, "host", "") or ""), getattr(client, "port", None)


def _rewritten_by_uvicorn(conn: Any) -> bool:
    """Uvicorn's ProxyHeadersMiddleware replaced the client with an
    ``X-Forwarded-For`` entry (it does so only for a trusted peer, 127.0.0.1
    by default, and leaves port 0 behind)."""
    _, port = _peer(conn)
    return port == 0 and "x-forwarded-for" in conn.headers


def _uvicorn_trusts_only_loopback() -> bool:
    """Uvicorn rewrites the client only for peers in ``FORWARDED_ALLOW_IPS``
    (default 127.0.0.1; start.sh sets nothing). Wider than loopback, a
    rewritten client no longer proves the real peer was this Mac."""
    raw = os.environ.get("FORWARDED_ALLOW_IPS")
    if raw is None:
        return True
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    return bool(entries) and all(is_loopback(e) for e in entries)


def peer_is_loopback(conn: Any) -> bool:
    """Did the connection itself come from this Mac (a proxy here, a tunnel
    here, or a local program)?"""
    host, _ = _peer(conn)
    if _rewritten_by_uvicorn(conn):
        return _uvicorn_trusts_only_loopback()
    return is_loopback(host)


def _xff_entries(conn: Any) -> List[str]:
    out: List[str] = []
    for value in conn.headers.getlist("x-forwarded-for"):
        out.extend(p.strip() for p in str(value).split(","))
    return [p for p in out if p]


def _forwarded_for(conn: Any) -> Tuple[bool, List[str]]:
    """(present, the ``for=`` values of every RFC 7239 ``Forwarded`` header)."""
    values = conn.headers.getlist("forwarded")
    out: List[str] = []
    for value in values:
        for element in str(value).split(","):
            for pair in element.split(";"):
                name, _, v = pair.partition("=")
                if name.strip().lower() == "for":
                    out.append(v.strip())
    return bool(values), out


def _key(value: Any) -> str:
    return str(value or "").strip()[:MAX_KEY_CHARS]


def client_ip(conn: Any) -> str:
    """The client a request stands for, as a rate-limit key.

    The peer itself unless it is loopback (a proxy on this Mac or a tunnel);
    then ``CF-Connecting-IP`` when present, else the right-most
    ``X-Forwarded-For`` entry that is not loopback (each proxy appends, so
    the left-most entries are whatever the client wrote), else the peer.
    """
    host, _ = _peer(conn)
    if not peer_is_loopback(conn):
        return _key(host) or "unknown"
    cf = _key(conn.headers.get("cf-connecting-ip"))
    if cf:
        return cf
    for entry in reversed(_xff_entries(conn)):
        if not is_loopback(entry):
            return _key(entry)
    return _key(host) or "unknown"


def is_local_client(conn: Any) -> bool:
    """Was the original client on this Mac?

    A loopback peer, no ``CF-Connecting-IP``, every ``X-Forwarded-For`` /
    ``X-Real-IP`` / ``Forwarded`` entry loopback, and, through Next's proxy,
    a loopback host in ``X-Forwarded-Host`` (the browser opened
    ``http://localhost:3000`` on this Mac; ``http://my-mac.local:3000`` from
    a phone is not local).
    """
    if not peer_is_loopback(conn):
        return False
    headers = conn.headers
    if "cf-connecting-ip" in headers:
        return False
    if any(not is_loopback(e) for e in _xff_entries(conn)):
        return False
    real = headers.get("x-real-ip")
    if real is not None and not is_loopback(real):
        return False
    present, fors = _forwarded_for(conn)
    if present and (not fors or any(not is_loopback(f) for f in fors)):
        return False
    forwarded_hosts = headers.getlist("x-forwarded-host")
    for value in forwarded_hosts:
        hosts = [h for h in str(value).split(",") if h.strip()]
        if not hosts or any(not is_loopback(h) for h in hosts):
            return False
    return True


def is_direct_loopback(conn: Any) -> bool:
    """A loopback peer that came through nothing: no proxy header at all
    (``PROXY_HEADERS``), so neither Next's rewrite nor a tunnel relayed it."""
    host, _ = _peer(conn)
    if not is_loopback(host):
        return False
    return not any(name in conn.headers for name in PROXY_HEADERS)
