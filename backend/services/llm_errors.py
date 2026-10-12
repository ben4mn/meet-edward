"""One provider-neutral error for a model call that failed (2026-10-10).

``ChatClaude`` raises it for every Anthropic failure — an HTTP status the
SDK gave up on, a connection that never opened or dropped mid-stream, an
``error`` event inside the stream, a stream that ended before
``message_stop``, the request's own deadline. ``retryable`` says whether
the same call is likely to work in a few minutes: overloaded, 5xx, 429,
408/409 and connection faults are; a 400, an auth failure, a refused model
and the deadline are not. ``mind_service.is_transient_error`` reads it, so a
wake that died on an overloaded API is asked for again and one that sent a
bad request is not.

A ``ValueError`` like ``CodexStreamError``, so the handlers that already
catch model errors still see it. ``refresh_attempted`` is set so the tool
loop never answers an Anthropic 401 by refreshing the Codex login.
"""

from __future__ import annotations

from typing import Any, Optional

# Statuses that mean "the service, not the request".
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
# Anthropic error ``type`` values (in a status body or a stream ``error``
# event) that mean the same.
RETRYABLE_ERROR_TYPES = frozenset({
    "overloaded_error", "api_error", "rate_limit_error", "timeout_error", "internal_server_error",
    "service_unavailable", "stream_truncated", "connection_error",
})


class LLMProviderError(ValueError):
    """A failed model call, whichever provider made it."""

    refresh_attempted = True

    def __init__(self, code: str, message: str = "", *, retryable: Optional[bool] = None,
                 http_status: Optional[int] = None, provider: str = "", request_id: Optional[str] = None,
                 body: Any = None):
        self.code = str(code or "unknown")[:80]
        self.message = str(message or "")
        self.http_status = http_status
        self.provider = provider
        self.request_id = request_id
        self.body = body
        self.retryable = is_retryable(self.code, http_status) if retryable is None else bool(retryable)
        status = f"HTTP {http_status}, " if http_status else ""
        name = provider.capitalize() if provider else "LLM"
        super().__init__(f"{name} API error ({status}{self.code}): {self.message[:300]}")


def is_retryable(code: Any, http_status: Optional[int] = None) -> bool:
    """Transient: a retryable status or error type. Pure."""
    if http_status is not None:
        if http_status in RETRYABLE_HTTP_STATUSES or http_status >= 500:
            return True
        if 400 <= http_status < 500:
            return False
    return str(code or "").strip().lower() in RETRYABLE_ERROR_TYPES
