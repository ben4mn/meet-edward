"""Egress secret scan: credentials never leave this machine.

Why (2026-09-24; docs/architecture/trust-boundary.md): one process holds
Full Disk Access, the Codex tokens in ``~/.codex/auth.json``, VAPID and
Twilio keys in ``.env``, and the owner's logged-in Chrome — and it reads
untrusted inbound text and web pages. Muse's worst export was a user
asking the agent, in plain chat, to zip its runtime into an *approved*
destination: egress policy never looked at what was leaving. So everything that leaves through
a message, a hosted page, a browser field, a URL or a search query is
scanned here first, and a finding blocks the call. The model cannot argue
with a regex.

Pure and table-tested (``tests/test_secret_scan.py``): ``find_secrets``
reads one string, ``scan_egress`` knows which arguments of which tools
leave the machine. The one piece of I/O, ``known_secrets()``, collects the
live values of this process's own secrets (env vars named like keys, the
Codex OAuth tokens, and the session-signing key — ``JWT_SECRET_KEY`` or
``backend/.jwt_secret``, which mints owner sessions) so an exact copy of
one is caught even when it has no recognisable shape.

Quiet on ordinary text is a requirement, not a nicety: phone numbers,
URLs, hex colours, UUIDs and commit hashes in prose never trip it, and a
generic ``key = value`` only counts when the value looks random.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote_plus


@dataclass(frozen=True)
class Finding:
    kind: str       # "openai_key", "jwt", "env:TWILIO_AUTH_TOKEN", …
    redacted: str   # a short head and the length; never the secret


# ------------------------------------------------------------ helpers --

def _entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def looks_random(value: str, min_len: int = 16) -> bool:
    """A credential-shaped value: long, at least two of lower/upper/digit,
    no URL, and enough entropy that it is not a word or a placeholder
    (``YOUR_API_KEY_HERE``, ``correcthorsebatterystaple``, ``********``)."""
    if len(value) < min_len or "://" in value:
        return False
    classes = sum((any(c.islower() for c in value), any(c.isupper() for c in value),
                   any(c.isdigit() for c in value)))
    if classes < 2:
        return False
    return _entropy(value) >= 3.0


def _redact(secret: str, keep: int = 3) -> str:
    keep = max(0, min(keep, 8, len(secret) // 3))
    return f"{secret[:keep]}…({len(secret)})"


class _Taken:
    """The spans already claimed, disjoint and sorted by start, so a new
    span's overlap is a bisect, not a scan of every span so far (that was
    O(k²): 16 000 keys in one result took ~5 s)."""

    def __init__(self) -> None:
        self.starts: List[int] = []
        self.ends: List[int] = []

    def overlaps(self, a: int, b: int) -> bool:
        i = bisect.bisect_right(self.starts, a)
        # The span starting at or before ``a`` (the latest, and with disjoint
        # spans the latest-ending), and the first starting after it.
        if i and a < self.ends[i - 1] and self.starts[i - 1] < b:
            return True
        return i < len(self.starts) and self.starts[i] < b

    def add(self, a: int, b: int) -> None:
        i = bisect.bisect_right(self.starts, a)
        self.starts.insert(i, a)
        self.ends.insert(i, b)


# -------------------------------------------------------------- rules --
# (kind, pattern, group holding the secret, validator, head chars kept in
# the redaction). Order matters: a later match that overlaps an earlier
# one is the same secret seen twice (``OPENAI_API_KEY=sk-…`` is an OpenAI
# key, not also a generic ``key=value``), so it is dropped.

_Validator = Optional[Any]

_RULES: List[Tuple[str, "re.Pattern[str]", int, _Validator, int]] = [
    ("private_key", re.compile(r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----"), 0, None, 0),
    ("anthropic_key", re.compile(r"(?<![A-Za-z0-9_-])sk-ant-([A-Za-z0-9_-]{20,})"), 0,
     lambda m: looks_random(m.group(1), 20), 7),
    ("openai_key", re.compile(r"(?<![A-Za-z0-9_-])sk-(?!ant-)(?:proj-|svcacct-|admin-)?([A-Za-z0-9_-]{20,})"), 0,
     lambda m: looks_random(m.group(1), 20), 3),
    ("github_token", re.compile(r"\b(?:gh[pousr]_([A-Za-z0-9]{36,})|github_pat_([A-Za-z0-9_]{22,}))"), 0,
     lambda m: looks_random(m.group(1) or m.group(2), 20), 4),
    ("slack_token", re.compile(r"\bxox[abprs]-([A-Za-z0-9-]{10,})"), 0,
     lambda m: any(c.isdigit() for c in m.group(1)), 5),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0, None, 4),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"), 0, None, 4),
    # Google OAuth access (``ya29.…``) and refresh (``1//…``) tokens, bare —
    # what a Gmail or Calendar read could hand back. ``1//`` after a digit,
    # a slash or a colon is a URL (``:3001//api``), not a token.
    ("google_oauth_token", re.compile(r"(?<![A-Za-z0-9_-])ya29\.([0-9A-Za-z_-]{20,})"), 0,
     lambda m: looks_random(m.group(1), 20), 5),
    ("google_refresh_token", re.compile(r"(?<![A-Za-z0-9_/.:-])1//([0-9A-Za-z_-]{20,})"), 0,
     lambda m: looks_random(m.group(1), 20), 4),
    # Anchored like the keys above, not ``\b``: inside a run of token
    # characters ``\b`` matches after every ``-``, so each ``-eyJ`` rescanned
    # to the run's end (quadratic: ~3 s for 100K crafted chars).
    ("jwt", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*"), 0,
     None, 3),
    ("oauth_token", re.compile(
        r"""["'](?:refresh_token|access_token|id_token)["']\s*:\s*["']([^"'\s]{20,})["']"""), 1,
     lambda m: looks_random(m.group(1), 20), 3),
    ("vapid_private_key", re.compile(
        r"""(?i)vapid[_-]?private[_-]?key["']?\s*[:=]\s*["']?([A-Za-z0-9_-]{40,})"""), 1,
     lambda m: looks_random(m.group(1), 40), 3),
    ("bearer_token", re.compile(r"\b[Bb]earer\s+([A-Za-z0-9._~+/=-]{20,})"), 1,
     lambda m: looks_random(m.group(1), 20), 3),
    # The key name's prefix is bounded: unbounded, every ``-``/``_`` in a long
    # run (a base64url blob in a page) restarted a scan to the run's end —
    # quadratic, ~10 s for 100K chars on the event loop.
    ("generic_secret", re.compile(
        r"""(?i)(?<![A-Za-z0-9])[A-Za-z0-9_.-]{0,64}?"""
        r"""(?:api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key|access[_-]?key)"""
        r"""["']?\s*[:=]\s*["']?([^\s"',;<>]{16,})"""), 1,
     lambda m: looks_random(m.group(1), 16), 3),
]

# A presigned S3 URL carries the access key *id* by design (the signature
# is what is secret, and it expires). Browsing to one is not a leak.
_AWS_ID_CONTEXT = re.compile(r"(?i)(?:X-Amz-Credential=|AWSAccessKeyId=)$")


def find_secrets(text: Any, known: Optional[Mapping[str, str]] = None) -> List[Finding]:
    """Every credential in ``text``, once each. ``known`` maps a name to a
    live secret value (``known_secrets()``); an exact copy of one is a
    finding named ``env:<NAME>`` even when it has no recognisable shape."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return []
    findings: List[Finding] = []
    seen: set = set()
    taken = _Taken()

    for kind, pattern, group, validator, keep in _RULES:
        for m in pattern.finditer(text):
            if validator is not None and not validator(m):
                continue
            start, end = m.span(group)
            if kind == "aws_access_key" and _AWS_ID_CONTEXT.search(text[max(0, start - 20):start]):
                continue
            if taken.overlaps(start, end):
                continue
            taken.add(start, end)
            secret = m.group(group)
            redacted = f"{m.group(1)} block" if kind == "private_key" else _redact(secret, keep)
            key = (kind, secret)
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(kind, redacted))

    for name, value in (known or {}).items():
        if value and len(value) >= MIN_KNOWN_SECRET_CHARS and value in text:
            key = (f"env:{name}", value)
            if key not in seen:
                seen.add(key)
                findings.append(Finding(f"env:{name}", f"<{len(value)} chars>"))
    return findings


# ---------------------------------------------------- credential files --

# Sending a file is egress too: ``whatsapp_send_file(media_path=
# "~/.codex/auth.json")`` would ship the Codex tokens without a single
# key-shaped character in the arguments.
_CREDENTIAL_PATH = re.compile(
    r"(?i)(?:\.codex/auth\.json|(?:^|/)\.env(?:\.[\w-]+)?$|/\.ssh/|(?:^|/)id_(?:rsa|dsa|ecdsa|ed25519)$"
    r"|\.(?:pem|p12|pfx)$|find_my_key\.bin|/\.cloudflared/|/Keychains/|/\.aws/credentials"
    r"|(?:^|/)\.netrc$|(?:^|/)\.pgpass$|(?:^|/)auth\.json$)"
)


def find_credential_paths(text: Any) -> List[Finding]:
    path = str(text or "").strip()
    if not path:
        return []
    m = _CREDENTIAL_PATH.search(os.path.expanduser(path))
    if not m:
        return []
    return [Finding("credential_file", os.path.basename(path.rstrip("/"))[:30] or "file")]


# ------------------------------------------------------------- egress --

ALL_STRINGS = ("*",)

# Tools whose arguments leave this machine, and which arguments do.
# Messaging goes to other people; hosted pages to the public web; browser
# typing into whatever site the owner's Chrome is on; URLs and search
# queries to the sites and search engines that receive them. The owner's
# own surfaces (the push to their phone, the widget, the household screen)
# are not egress.
EGRESS_TOOLS: Dict[str, Tuple[str, ...]] = {
    # The toolset (2026-10-11): the shell's command (``curl`` can carry a
    # secret out — output scanning can't see an upload), a fetched URL, and
    # the one send with its attachments.
    "bash": ("command",),
    "web_fetch": ("url",),
    "send_imessage": ("message",),
    "send_message": ("message", "files", "subject"),
    # A push lands on a lock screen (design decision, 2026-10-11: keep the
    # scan in tell_owner); a note on Today is scanned the same way.
    "tell_owner": ("message", "title"),
    "send_sms": ("message",),
    "send_whatsapp": ("message",),
    "create_hosted_page": ("html", "description", "slug"),
    "update_hosted_page": ("html", "description", "slug"),
    "browser_type": ("text",),
    "browser_steps": ("steps",),      # the text of "type" steps, the url of "open" steps
    "browser_open": ("url",),
    "fetch_page_content": ("url",),
    "web_search": ("query",),
    "webSearch": ("query",),          # apple-mcp's DuckDuckGo search
}
# WhatsApp MCP sends (``whatsapp_send_message``, ``_send_file``,
# ``_send_audio_message``): every string argument, file paths included.
EGRESS_PREFIXES: Dict[str, Tuple[str, ...]] = {"whatsapp_send": ALL_STRINGS}
# apple-mcp multiplexes reads and sends through one tool per app.
OPERATION_EGRESS: Dict[str, frozenset] = {
    "mail": frozenset({"send"}),
    "messages": frozenset({"send", "schedule"}),
}
# The toolset's action tools: which arguments leave the machine, by action.
ACTION_EGRESS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "chrome": {"open": ("url",), "type": ("text",), "steps": ("steps",)},
    "browser": {"open": ("url",), "type": ("text",), "steps": ("steps",)},
    # The owner's calendar and Reminders sync to iCloud, and invitees get the
    # event (2026-10-11).
    "schedule": {"event_add": ("what", "location", "notes", "attendees"),
                 "event_update": ("what", "location", "notes", "attendees"),
                 "reminder_add": ("what", "notes")},
}
URL_ARGS = frozenset({"url", "target"})
_PATH_ARG_HINTS = ("path", "file", "attachment")


def _is_custom_mcp(name: str) -> bool:
    try:
        from services.toolset.names import is_custom_mcp

        return is_custom_mcp(name)
    except Exception:  # noqa: BLE001 — unsure: scan it
        return True


def egress_args(tool_name: str, args: Any = None) -> Tuple[str, ...]:
    """Which of ``tool_name``'s arguments leave the machine (``("*",)`` =
    every string); empty when the call is not egress. A custom MCP tool's
    server is someone else's code, so every string it is handed leaves
    (nested ones too)."""
    name = str(tool_name or "")
    if _is_custom_mcp(name):
        return ALL_STRINGS
    if name in EGRESS_TOOLS:
        return EGRESS_TOOLS[name]
    if name in ACTION_EGRESS:
        action = str((args or {}).get("action") or "").strip().lower() if isinstance(args, dict) else ""
        return ACTION_EGRESS[name].get(action, ())
    if name == "look":
        # ``look`` leaves the machine only for an http(s) URL.
        target = str((args or {}).get("target") or "").strip().lower() if isinstance(args, dict) else ""
        return ("target",) if target.startswith(("http://", "https://")) else ()
    for prefix, which in EGRESS_PREFIXES.items():
        if name.startswith(prefix):
            return which
    ops = OPERATION_EGRESS.get(name)
    if ops is not None:
        op = str((args or {}).get("operation") or "").strip().lower() if isinstance(args, dict) else ""
        return ALL_STRINGS if op in ops else ()
    return ()


def is_egress_tool(tool_name: str, args: Any = None) -> bool:
    return bool(egress_args(tool_name, args))


def _strings(value: Any, label: str) -> Iterable[Tuple[str, str]]:
    if isinstance(value, str):
        yield label, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{label}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value, start=1):
            yield from _strings(v, f"{label}[{i}]")


def _steps(value: Any) -> List[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [{"action": "type", "text": value}]
    return list(value) if isinstance(value, (list, tuple)) else []


def egress_texts(tool_name: str, args: Any) -> List[Tuple[str, str, str]]:
    """``(label, text, how)`` for every leaving string; ``how`` is ``url``,
    ``path`` or ``text`` and picks the checks."""
    which = egress_args(tool_name, args)
    if not which or not isinstance(args, dict):
        return []
    out: List[Tuple[str, str, str]] = []
    names = list(args) if which == ALL_STRINGS else list(which)
    for arg in names:
        if arg not in args:
            continue
        value = args[arg]
        if arg == "steps" and tool_name in ("browser_steps", "chrome", "browser"):
            for i, step in enumerate(_steps(value), start=1):
                if not isinstance(step, dict):
                    continue
                action = str(step.get("action") or "").strip().lower()
                if action == "type" and step.get("text") is not None:
                    out.append((f"steps[{i}].text", str(step["text"]), "text"))
                elif action == "open" and step.get("url") is not None:
                    out.append((f"steps[{i}].url", str(step["url"]), "url"))
            continue
        for label, text in _strings(value, arg):
            leaf = label.rsplit(".", 1)[-1].split("[", 1)[0].lower()
            if leaf in URL_ARGS:
                how = "url"
            elif any(h in leaf for h in _PATH_ARG_HINTS):
                how = "path"
            else:
                how = "text"
            out.append((label, text, how))
    return out


def scan_egress(tool_name: str, args: Any, known: Optional[Mapping[str, str]] = None) -> List[Tuple[str, Finding]]:
    """``(argument, finding)`` for every credential this call would send."""
    hits: List[Tuple[str, Finding]] = []
    for label, text, how in egress_texts(tool_name, args):
        found = list(find_secrets(text, known))
        if how == "url":
            decoded = unquote_plus(text)
            if decoded != text:
                have = set(found)
                found += [f for f in find_secrets(decoded, known) if f not in have]
        elif how == "path":
            found += find_credential_paths(text)
        hits.extend((label, f) for f in found)
    return hits


def kinds_of(hits: Sequence[Any]) -> List[str]:
    """The distinct kinds in ``hits`` (findings or ``(arg, finding)``), sorted."""
    kinds = set()
    for h in hits:
        f = h[1] if isinstance(h, tuple) else h
        kinds.add(f.kind)
    return sorted(kinds)


# ------------------------------------------------------ known secrets --

MIN_KNOWN_SECRET_CHARS = 16
_SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)")
# Named like a key but holding a place, not a secret: a path to a key file
# is fine to mention in a message.
_NOT_A_SECRET_NAME = re.compile(r"(PUBLIC|_PATH$|_DIR$|_FILE$|_URL$|_ID$)")


def known_secret_values(env: Mapping[str, str]) -> Dict[str, str]:
    """This process's own secrets by name: env vars named like a key and
    long enough to be one (``VAPID_PUBLIC_KEY`` is public by design, and
    ``…_KEY_PATH=/Users/…`` is a place)."""
    out: Dict[str, str] = {}
    for name, value in env.items():
        upper = name.upper()
        if _NOT_A_SECRET_NAME.search(upper) or not _SECRET_NAME.search(upper):
            continue
        value = (value or "").strip()
        if len(value) < MIN_KNOWN_SECRET_CHARS or value.startswith(("/", "~")) or "://" in value:
            continue
        if value.lower() in ("true", "false", "none", "null"):
            continue
        out[name] = value
    return out


def codex_auth_values(data: Any) -> Dict[str, str]:
    """The token strings in a parsed ``~/.codex/auth.json``."""
    out: Dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    for key in ("access_token", "refresh_token", "id_token", "account_id"):
        v = tokens.get(key)
        if isinstance(v, str) and len(v) >= MIN_KNOWN_SECRET_CHARS and key != "account_id":
            out[f"codex_{key}"] = v
    v = data.get("OPENAI_API_KEY")
    if isinstance(v, str) and len(v) >= MIN_KNOWN_SECRET_CHARS:
        out["codex_OPENAI_API_KEY"] = v
    return out


_codex_cache: Dict[str, Any] = {"mtime": None, "values": {}}


def _codex_auth_path() -> str:
    return os.path.expanduser(os.getenv("CODEX_AUTH_PATH", "~/.codex/auth.json"))


# The session-signing key (services/auth_service.py). With JWT_SECRET_KEY
# unset — the default — it lives only in this file and never reaches
# os.environ, and a bare token_urlsafe string has no credential shape, so
# without this Edward's shell could `cat` it raw and his tools could send it
# out: whoever holds it can mint the owner's session cookie.
JWT_SECRET_FILE = Path(__file__).resolve().parent.parent / ".jwt_secret"
JWT_SECRET_NAME = "JWT_SECRET_KEY"
_jwt_file_cache: Dict[str, Any] = {"key": None, "value": None}
# Secrets a module registers at import (``register_known_secret``): the
# session key as auth_service resolved it, whatever its source.
_registered: Dict[str, str] = {}


def register_known_secret(name: str, value: Optional[str]) -> None:
    """Add a live secret this process holds outside the environment, so
    :func:`known_secrets` redacts it from tool output and blocks it at
    egress. Short values are ignored, like the environment's."""
    v = (value or "").strip()
    if name and len(v) >= MIN_KNOWN_SECRET_CHARS:
        _registered[str(name)] = v


def _jwt_secret_file_value() -> Optional[str]:
    """``backend/.jwt_secret``'s contents (re-read when it changes); ``None``
    when it is missing or unreadable."""
    try:
        st = os.stat(JWT_SECRET_FILE)
        key = (str(JWT_SECRET_FILE), st.st_mtime_ns, st.st_size)
        if key != _jwt_file_cache["key"]:
            _jwt_file_cache["value"] = JWT_SECRET_FILE.read_text(encoding="utf-8").strip() or None
            _jwt_file_cache["key"] = key
        return _jwt_file_cache["value"]
    except (OSError, ValueError):
        return None


def known_secrets() -> Dict[str, str]:
    """Live secret values to look for: the environment's, the Codex OAuth
    tokens (re-read when ``auth.json`` changes — they rotate about every
    eight days), the session-signing key (``backend/.jwt_secret`` and
    whatever auth_service registered) and anything else registered. Never
    raises."""
    values = known_secret_values(os.environ)
    try:
        path = _codex_auth_path()
        mtime = os.path.getmtime(path)
        if mtime != _codex_cache["mtime"]:
            with open(path, "r", encoding="utf-8") as fh:
                _codex_cache["values"] = codex_auth_values(json.load(fh))
            _codex_cache["mtime"] = mtime
        values.update(_codex_cache["values"])
    except (OSError, ValueError):
        pass
    file_key = _jwt_secret_file_value()
    if file_key and len(file_key) >= MIN_KNOWN_SECRET_CHARS and file_key not in values.values():
        values[JWT_SECRET_NAME if JWT_SECRET_NAME not in values else JWT_SECRET_NAME + "_FILE"] = file_key
    for name, value in _registered.items():
        if value not in values.values():
            values[name if name not in values else name + "_REGISTERED"] = value
    return values


# ---------------------------------------------------------- redaction --
# Edward's shell and editor can read anything (a design decision), so every
# command's output and every file ``view`` is redacted before the model
# reads it: the exact live values of this process's own secrets, and
# anything credential-shaped, become ``[redacted: <kind>]``. The model
# never sees a usable key even when it `cat`s a ``.env``.


def _secret_spans(text: str, known: Optional[Mapping[str, str]] = None) -> List[Tuple[int, int, str]]:
    """``(start, end, kind)`` for every credential in ``text``, non-overlapping
    and left-to-right. Reuses the same rules as :func:`find_secrets`."""
    spans: List[Tuple[int, int, str]] = []
    taken = _Taken()

    for kind, pattern, group, validator, _keep in _RULES:
        for m in pattern.finditer(text):
            if validator is not None and not validator(m):
                continue
            start, end = m.span(group)
            if start == end:
                continue
            if kind == "aws_access_key" and _AWS_ID_CONTEXT.search(text[max(0, start - 20):start]):
                continue
            if taken.overlaps(start, end):
                continue
            taken.add(start, end)
            spans.append((start, end, kind))

    for name, value in (known or {}).items():
        if not value or len(value) < MIN_KNOWN_SECRET_CHARS:
            continue
        idx = text.find(value)
        while idx != -1:
            end = idx + len(value)
            if not taken.overlaps(idx, end):
                taken.add(idx, end)
                spans.append((idx, end, f"env:{name}"))
            idx = text.find(value, end)

    spans.sort(key=lambda s: s[0])
    return spans


def redact_secrets(text: Any, known: Optional[Mapping[str, str]] = None) -> Tuple[str, List[str]]:
    """Replace every credential in ``text`` with ``[redacted: <kind>]``.

    Returns ``(redacted_text, kinds)`` where ``kinds`` is the distinct kinds
    that were redacted, sorted. ``known`` maps a name to a live secret value
    (:func:`known_secrets`); an exact copy of one is redacted as
    ``env:<NAME>`` even when it has no recognisable shape. Never raises.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return text, []
    spans = _secret_spans(text, known)
    if not spans:
        return text, []
    out: List[str] = []
    last = 0
    kinds: set = set()
    for start, end, kind in spans:
        if start < last:  # safety against any residual overlap
            continue
        out.append(text[last:start])
        out.append(f"[redacted: {kind}]")
        kinds.add(kind)
        last = end
    out.append(text[last:])
    return "".join(out), sorted(kinds)
