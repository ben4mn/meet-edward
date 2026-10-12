"""No real client address ships in the source tree.

The private build's watchdog fixtures were copied from a live access log and
carried the household's public IPv4 and a phone's IPv6. Fixtures use the
documentation ranges instead: 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24
(RFC 5737) for IPv4, 2001:db8::/32 (RFC 3849) for IPv6, private and loopback
space for a LAN, and a handful of famous public examples (example.com's block,
the public resolvers, 1.2.3.4). Anything else that parses as a routable address
fails here, with the file and line to fix.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "storage", "data", "build", "dist",
              ".build", ".pytest_cache"}
_SKIP_FILES = {"package-lock.json"}
_MAX_BYTES = 2_000_000

_V4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_V6 = re.compile(r"(?<![0-9A-Za-z:])(?:[0-9A-Fa-f]{1,4}:){2,7}:?[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{1,4})*"
                 r"(?![0-9A-Za-z:])")

_OK_V4 = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
    # famous public examples, never a person's address
    "93.184.216.0/24", "1.2.3.4/32",
    # the public resolvers (Cloudflare, Google, Quad9, OpenDNS)
    "1.1.1.1/32", "1.0.0.1/32", "8.8.8.8/32", "8.8.4.4/32", "9.9.9.9/32", "149.112.112.112/32",
    "208.67.222.222/32", "208.67.220.220/32",
)]
_OK_V6 = [ipaddress.ip_network(n) for n in (
    "2001:db8::/32", "64:ff9b::/96",
    # the public resolvers (Cloudflare, Google, Quad9)
    "2606:4700::/32", "2001:4860::/32", "2620:fe::/48",
)]


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30, check=True).stdout
        names = [n for n in out.split("\n") if n]
    except (OSError, subprocess.SubprocessError):
        names = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".next")]
            names += [os.path.relpath(os.path.join(dirpath, f), ROOT) for f in filenames]
    files = []
    for name in names:
        parts = Path(name).parts
        if any(p in _SKIP_DIRS or p.startswith(".next") for p in parts[:-1]) or parts[-1] in _SKIP_FILES:
            continue
        files.append(ROOT / name)
    return files


def routable_addresses(text: str) -> list[str]:
    """Every address in ``text`` outside the documentation, private and example space."""
    found = []
    for m in _V4.findall(text):
        try:
            ip = ipaddress.ip_address(m)
        except ValueError:
            continue
        if not any(ip in n for n in _OK_V4):
            found.append(m)
    for m in _V6.findall(text):
        try:
            ip = ipaddress.ip_address(m)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_site_local:
            continue
        if ip in ipaddress.ip_network("fc00::/7") or any(ip in n for n in _OK_V6):
            continue
        if ip.ipv4_mapped is not None and any(ip.ipv4_mapped in n for n in _OK_V4):
            continue
        found.append(m)
    return found


@pytest.mark.parametrize("text, hits", [
    ('INFO:     203.0.113.17:0 - "GET /api/screen HTTP/1.1" 200 OK', []),
    ('INFO:     2001:db8:2e06:a674::9e8f:674:0 - "GET /api/hub/health HTTP/1.1" 200 OK', []),
    ('INFO:     192.168.1.40:0 - "GET /api/hub/board HTTP/1.1" 200 OK', []),
    ('resolve to ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"]', []),
    ("at 12:30:45 on 2026.10.12, version 1.2.3", []),
    # Spelled in two pieces so this file never trips its own tree scan.
    ('INFO:     100.200.' + '30.4:0 - "GET /api/screen HTTP/1.1" 200 OK', ["100.200." + "30.4"]),
    ("client 2a00:1450:4001:" + "80b::200e said hi", ["2a00:1450:4001:" + "80b::200e"]),
])
def test_the_scanner_tells_documentation_space_from_a_real_address(text, hits):
    assert routable_addresses(text) == hits


def test_no_routable_address_anywhere_in_the_tree():
    offenders = []
    for path in _tracked_files():
        try:
            if not path.is_file() or path.stat().st_size > _MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(ch in text for ch in ".:"):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for addr in routable_addresses(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {addr}")
    assert not offenders, (
        "Routable IP addresses in the tree — use 192.0.2.x / 198.51.100.x / 203.0.113.x or 2001:db8::/32:\n  "
        + "\n  ".join(offenders)
    )
