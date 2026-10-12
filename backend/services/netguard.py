"""Edward fetches only the public internet (2026-10-11).

A link someone texts Edward is untrusted. Fetched naively it can point at
this Mac: Postgres (:5432, :5433), the backend (:8000), the frontend
(:3000), the browser extension's socket, the router at 192.168.x.1, the
tailnet (100.64/10). Every fetch of a URL that came from a message, a page
or a model goes through ``assert_public_url`` first, again on every
redirect, and the connected peer is checked once more after connecting
(``guarded_get``), so DNS that changes between the check and the connect
is caught too.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/18.6 Safari/605.1.15")
_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa", ".localdomain")
_BLOCKED_NAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback", "broadcasthost"})
_EXTRA_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",   # carrier-grade NAT, and Tailscale's addresses
    "198.18.0.0/15",   # benchmarking
    "0.0.0.0/8",
))


class BlockedURL(ValueError):
    """The URL is not on the public internet (or not http/https)."""


def is_public_ip(ip: str) -> bool:
    """True only for a globally routable unicast address. Pure."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped or addr.sixtofour
        if mapped is not None:
            return is_public_ip(str(mapped))
        if addr in ipaddress.ip_network("64:ff9b::/96"):
            return is_public_ip(str(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)))
    if any(addr in net for net in _EXTRA_BLOCKED_NETS if net.version == addr.version):
        return False
    return bool(addr.is_global) and not (addr.is_multicast or addr.is_reserved or addr.is_loopback
                                         or addr.is_link_local or addr.is_private or addr.is_unspecified)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_url_shape(url: str) -> Tuple[str, int]:
    """(host, port) of an http(s) URL with a plausible public host, else
    BlockedURL. No DNS. Pure (but for this Mac's own hostname)."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedURL(f"only http and https links are fetched (got {parts.scheme or 'none'!r})")
    if parts.username or parts.password:
        raise BlockedURL("links with a user name or password in them are not fetched")
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        raise BlockedURL("the link has no host")
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES) or ("." not in host and not _is_ip_literal(host)):
        raise BlockedURL(f"{host} is not a public internet address")
    if host == socket.gethostname().lower().strip("."):
        raise BlockedURL(f"{host} is this Mac")
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    return host, port


async def resolve(host: str, port: int) -> Iterable[str]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def assert_public_url(url: str) -> None:
    """BlockedURL unless every address the host resolves to is public."""
    host, port = check_url_shape(url)
    try:
        addrs = list(await resolve(host, port))
    except OSError as exc:
        raise BlockedURL(f"could not resolve {host}: {exc}") from exc
    if not addrs:
        raise BlockedURL(f"{host} did not resolve")
    bad = [a for a in addrs if not is_public_ip(a)]
    if bad:
        raise BlockedURL(f"{host} points at a private address ({bad[0]})")


# ------------------------------------------------------------ fetching ----

@dataclass
class Fetched:
    status: int
    final_url: str
    headers: Dict[str, str]
    content: bytes
    truncated: bool


def _peer_ip(response: Any) -> Optional[str]:
    """The address the connection actually reached, when httpx exposes it."""
    stream = (getattr(response, "extensions", None) or {}).get("network_stream")
    if stream is None:
        return None
    try:
        addr = stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001
        return None
    if isinstance(addr, (tuple, list)) and addr:
        return str(addr[0])
    return None


async def guarded_get(url: str, *, max_bytes: int, timeout_s: float, max_redirects: int = 5,
                      headers: Optional[Dict[str, str]] = None, auth: Any = None,
                      transport: Any = None) -> Fetched:
    """GET a public URL: every hop checked by ``assert_public_url`` before it
    is requested, the connected peer checked again (DNS that changed between
    the check and the connect), the body read up to ``max_bytes``.
    Raises BlockedURL or httpx.HTTPError. ``transport`` is for tests."""
    import httpx

    base_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    base_headers.update(headers or {})
    current = str(url)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_s, transport=transport,
                                 headers=base_headers, auth=auth) as client:
        for _hop in range(max_redirects + 1):
            await assert_public_url(current)
            async with client.stream("GET", current) as response:
                peer = _peer_ip(response)
                if peer is not None and not is_public_ip(peer):
                    raise BlockedURL(f"{urlsplit(current).hostname} connected to a private address ({peer})")
                if response.is_redirect and response.headers.get("location"):
                    current = str(response.url.join(response.headers["location"]))
                    continue
                chunks, total, truncated = [], 0, False
                async for chunk in response.aiter_bytes():
                    if total + len(chunk) > max_bytes:
                        chunks.append(chunk[: max_bytes - total])
                        total = max_bytes
                        truncated = True
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                return Fetched(status=response.status_code, final_url=str(response.url),
                               headers={k.lower(): v for k, v in response.headers.items()},
                               content=b"".join(chunks), truncated=truncated)
    raise BlockedURL(f"more than {max_redirects} redirects")
