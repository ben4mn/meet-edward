"""Render the macOS Seatbelt (``sandbox-exec``) profile from the rails
table. The shell runs under ``sandbox-exec -f <profile> /bin/bash …``.

The profile is ``(allow default)`` plus a short list of denies. SBPL takes
the **last matching rule**, so the write-deny lines sit after the allow and
win. Verified on macOS 15.7: write-deny by subpath, exec-deny
of Chrome's binary (exit 126), ``(deny lsopen)`` (``open -a`` fails -54),
``(deny appleevent-send)`` (Finder AE -600), ``(deny signal (target
others))`` (kill of another session's process EPERM; own children still
killable), and the TCP/unix denies on the database port. Verified on
macOS 15 (2026-10-12): the read-deny on a literal holds through a symlink,
a relative or ``..`` path, ``cp -c`` (clone) and ``ln`` (hard link), and
with ``file-write*`` on the same literal the file can't be moved away;
``(remote tcp "*:PORT")`` refuses IPv4 and IPv6 loopback alike.

Seatbelt matches the path at the time of each operation, so a rule on a
path protects what sits there only while nothing above it can be renamed
(review 2026-10-12: ``mv ~/code ~/code2`` carried the live repo and its
key out from under both rules). Every directory above a write-denied root
or a credential, up to ``/``, gets ``(deny file-write* (literal A))``:
verified on macOS 15 (2026-10-12) that it refuses renaming or removing
the directory itself while creating, renaming and removing files *inside*
it still works. A ``subpath`` on a link's own path keeps the link from
being removed or repointed (writes through it are matched where it
resolves, so both are listed). Seatbelt resolves a path's case itself: a
case variant of a denied path is refused.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Sequence

from services import sandbox_env
from services.computer import home, rails

PRODUCTION_PG_PORT = sandbox_env.PRODUCTION_PG_PORT
TEST_PG_PORT = sandbox_env.TEST_PG_PORT


def _scheme_str(value: str) -> str:
    """Quote a path/string for SBPL (backslash-escape ``\\`` and ``"``)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_profile(
    *,
    write_deny: Optional[List[str]] = None,
    read_deny: Optional[List[str]] = None,
    pg_port: int = PRODUCTION_PG_PORT,
    pg_socket: str = "/private/tmp/.s.PGSQL.5432",
    test_pg_port: Optional[int] = TEST_PG_PORT,
    test_pg_socket: str = f"/private/tmp/.s.PGSQL.{TEST_PG_PORT}",
    app_ports: Optional[Sequence[int]] = None,
    chrome_glob: str = "/Applications/Google Chrome",
) -> str:
    """The profile text. Parameters exist so the sandbox test can render it
    against scratch paths and throwaway ports, never the live ones.
    ``app_ports`` defaults to ``rails.denied_ports()`` (Edward's API and the
    WhatsApp bridge); the directories above ``write_deny`` and
    ``read_deny`` are pinned whatever is passed."""
    deny = write_deny if write_deny is not None else rails.write_deny_roots()
    secrets = read_deny if read_deny is not None else rails.read_deny_files()
    ports = list(app_ports) if app_ports is not None else list(rails.denied_ports())
    ancestors = rails.protected_ancestors(deny, secrets)
    lines: List[str] = [
        "(version 1)",
        "(allow default)",
        ";; ---------------------------------------------------------------- writes",
        ";; Reads are allowed everywhere (by design); output is redacted.",
        ";; The live repo, the releases, Edward's own config, and the data/kegs",
        ";; production's Postgres and FDA Python run on are not his to overwrite.",
    ]
    if deny:
        subpaths = " ".join(f'(subpath "{_scheme_str(p)}")' for p in deny)
        lines.append(f"(deny file-write* {subpaths})")
    if secrets:
        literals = " ".join(f'(literal "{_scheme_str(p)}")' for p in secrets)
        lines += [
            ";; ------------------------------------------------- his own credentials",
            ";; The one exception to open reads: the session-signing key, the",
            ";; first-password setup code and .env. Code in this shell could use",
            ";; them without the model ever seeing (or redaction ever hiding) them.",
            ";; Not readable, replaceable, movable or linkable.",
            f"(deny file-read-data file-write* file-link {literals})",
        ]
    if ancestors:
        pinned = " ".join(f'(literal "{_scheme_str(p)}")' for p in ancestors)
        lines += [
            ";; -------------------------------------------------- the paths above them",
            ";; Every directory above those, up to /, can't itself be renamed, moved or",
            ";; removed (rules match paths, so renaming a parent would carry what they",
            ";; protect out from under them). Their contents stay his to change.",
            f"(deny file-write* {pinned})",
        ]
    name = _scheme_str(os.path.basename(chrome_glob.rstrip("/")) or "Google Chrome")
    bundle_re = f"^{_scheme_str(chrome_glob)}[^/]*\\.app(/|$)"
    lines += [
        ";; ------------------------------------------------------------- processes",
        ";; Never launch or script Google Chrome: a second instance hijacks",
        ";; `open -a` and `tell application \"Google Chrome\"`. The rail is the",
        ";; bundle, not its path (review 2026-10-11): its executable is refused",
        ";; wherever it sits (a download, a copy, a renamed bundle), and the",
        ";; installed bundle can't be read (copied), moved, linked or changed.",
        f'(deny process-exec (regex #"^{_scheme_str(chrome_glob)}[^/]*\\.app/") '
        f'(regex #"/{name}( Beta| Canary| Dev)?\\.app/Contents/MacOS/") '
        f'(regex #"/Contents/MacOS/{name}( Beta| Canary| Dev)?$"))',
        f'(deny file-read-data file-write* file-link (regex #"{bundle_re}"))',
        "(deny lsopen)                 ;; no app launches via LaunchServices (open -a, open URL)",
        "(deny appleevent-send)        ;; no AppleScript control of Messages, Mail, Chrome, …",
        "(deny signal (target others)) ;; signal only processes he started in this session",
        ";; --------------------------------------------------------------- network",
        ";; Never a database of Edward's: production, nor the test cluster (its",
        ";; OS-user superuser trusts loopback; a superuser's server-side file reads and",
        ";; COPY ... TO PROGRAM run outside this sandbox).",
        f'(deny network-outbound (remote tcp "*:{int(pg_port)}"))',
    ]
    for sock in _socket_paths(pg_socket):
        lines.append(f'(deny network-outbound (remote unix-socket (path-literal "{_scheme_str(sock)}")))')
    if test_pg_port is not None:
        lines.append(f'(deny network-outbound (remote tcp "*:{int(test_pg_port)}"))')
        for sock in _socket_paths(test_pg_socket):
            lines.append(f'(deny network-outbound (remote unix-socket (path-literal "{_scheme_str(sock)}")))')
    if ports:
        lines.append(";; Never Edward's own API (the backend, the frontend's /api proxy) nor a local")
        lines.append(";; service that sends as the owner (the WhatsApp bridge), on any address.")
        lines += [f'(deny network-outbound (remote tcp "*:{int(port)}"))' for port in ports]
    return "\n".join(lines) + "\n"


def _socket_paths(path: str) -> List[str]:
    """A unix socket's path as given and with /tmp spelled both ways
    (``/tmp`` is a link to ``/private/tmp``)."""
    out = [path]
    if path.startswith("/private/tmp/"):
        out.append(path[len("/private"):])
    elif path.startswith("/tmp/"):
        out.append("/private" + path)
    return out


def profile_path() -> Path:
    return home.computer_dir() / "profile.sb"


def write_profile() -> Path:
    """Write the live profile to ``~/.edward-computer/profile.sb``."""
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_profile(), encoding="utf-8")
    return path
