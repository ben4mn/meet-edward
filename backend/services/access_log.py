"""Keep secrets in query strings out of the access log.

Regression origin (2026-09-27, followups L10): uvicorn writes every request
line, query string included, to /tmp/edward-backend.log, which is
world-readable. The Scriptable widget authenticates with
``GET /api/widget?token=…`` and the presence stream with
``/api/presence/stream?token=…``, so the widget token sat in the log in
plain text several times a minute (and in its gzipped rotations).

``RedactSecretsFilter`` rewrites the value of any secret-looking query
parameter (``token``, ``key``, ``api_key``, ``code``, ``state``,
``password``, ``secret`` and their ``*_token`` / ``*_key`` / ``*_secret``
relatives) to ``[redacted]`` in a record's arguments and message before
any handler formats it. ``backend/log_config.json`` attaches it to
uvicorn's handlers; ``backend/start.sh`` passes that file to uvicorn with
``--log-config``.

Stdlib only: uvicorn imports this while it configures logging, before the
app (and its dependencies) load.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote_plus

REDACTED = "[redacted]"

SECRET_PARAMS = frozenset(
    {
        "token",
        "key",
        "api_key",
        "apikey",
        "code",
        "state",  # an OAuth callback's state is what makes its code the owner's (Gmail, 2026-10-11)
        "password",
        "passwd",
        "secret",
        "client_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "auth",
        "signature",
        "sig",
    }
)
_SECRET_SUFFIXES = ("_token", "_key", "_secret", "_password")

# One query parameter: after "?" or "&", a name, "=", then the value up to
# the next separator, fragment, whitespace or quote (a request line is
# 'GET /path?query HTTP/1.1').
_PARAM = re.compile(r"(?P<lead>[?&])(?P<name>[^=&#?\s\"']+)=(?P<value>[^&#\s\"']*)")


def is_secret_param(name: str) -> bool:
    """True for a query parameter name whose value must never be logged."""
    normal = unquote_plus(name).strip().lower().replace("-", "_")
    return normal in SECRET_PARAMS or normal.endswith(_SECRET_SUFFIXES)


def _redact(match: re.Match) -> str:
    if match.group("value") and is_secret_param(match.group("name")):
        return f"{match.group('lead')}{match.group('name')}={REDACTED}"
    return match.group(0)


def redact_query(text: str) -> str:
    """``text`` with every secret query-parameter value replaced by ``[redacted]``.

    Works on a bare target (``/api/widget?token=abc``) and on a line that
    contains one. A parameter only counts after a ``?`` or ``&``, so text
    without a query string comes back unchanged.
    """
    if "?" not in text or "=" not in text:
        return text
    return _PARAM.sub(_redact, text)


def _secret_matches(text: str):
    if "?" not in text or "=" not in text:
        return []
    return [m for m in _PARAM.finditer(text) if m.group("value") and is_secret_param(m.group("name"))]


def count_secrets(text: str) -> int:
    """How many secret query-parameter values ``text`` carries."""
    return len(_secret_matches(text))


def mask_query(text: str) -> str:
    """``text`` with every value ``redact_query`` would hide overwritten by
    ``*`` of the same length — for scrubbing a log file in place, where a
    line may not change length (pre-deploy review, 2026-09-27:
    ``scripts/scrub_log_secrets.py``)."""
    out = list(text)
    for m in _secret_matches(text):
        start, end = m.span("value")
        out[start:end] = "*" * (end - start)
    return "".join(out)


class RedactSecretsFilter(logging.Filter):
    """Redact secret query parameters in a record before it is formatted.

    uvicorn's access records carry the path as an argument
    (``'%s - "%s %s HTTP/%s" %d'`` with ``(client, method, path, version,
    status)``) and its formatter unpacks those arguments, so the arguments
    are rewritten in place rather than the formatted line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(
                redact_query(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        if isinstance(record.msg, str):
            record.msg = redact_query(record.msg)
        return True
