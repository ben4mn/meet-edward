# ADAPT: the Codex OAuth path borrows the Codex CLI's login and client id.
#   How it works here: reads ~/.codex/auth.json (CODEX_AUTH_PATH) written by
#   `codex login` and refreshes it at auth.openai.com with the CLI's own OAuth
#   client id, to reach chatgpt.com/backend-api/codex — unofficial, may break
#   or conflict with OpenAI's terms. Used only when EDWARD_LLM_PROVIDER=codex.
#   To adapt: keep the default provider (the Claude API, ANTHROPIC_API_KEY).
#   OPENAI_API_KEY, if set, is only used as the bearer token for that same
#   chatgpt.com endpoint (codex_chat_model.CODEX_RESPONSES_URL); this build has
#   no path to the public OpenAI API.
"""OpenAI auth manager — supports OPENAI_API_KEY env var and Codex OAuth fallback."""

import asyncio
import json
import os
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CODEX_AUTH_PATH = os.getenv("CODEX_AUTH_PATH", str(Path.home() / ".codex" / "auth.json"))
OPENAI_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
# The Codex CLI's OAuth client id (``strings $(which codex)``). The previous
# value, app_scp_cs_prod_001, was refused with "Invalid client specified"
# on every refresh Edward ever attempted; he only stayed signed in because
# the CLI rewrote auth.json on its own schedule. Unofficial: the CLI may
# change it at any release.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Single-flight refresh (2026-09-25 audit: 29 refreshes in one outage hour,
# two in the same second). A successful refresh this recent is reused by
# the next caller that sees a 401 instead of minting another token.
#
# There is no proactive refresh (2026-09-27, followups L4). A
# ``get_access_token`` that refreshed before the JWT's ``exp`` had no
# callers: everything reads ``get_api_key()``, and an expired token (they
# last ~8 days) costs one refused request, which ``ChatCodexResponses``
# answers by refreshing once, single-flight, and retrying. It was not wired
# into the usage meter's read either: tests reach that read through the
# singleton, which loads the real ~/.codex/auth.json, and a refresh there
# would rotate the pair ``codex exec`` uses from a test run.
REFRESH_REUSE_SECONDS = 60.0


def _write_auth_file(path: str, tokens_update: dict) -> None:
    """Write refreshed tokens back to auth.json the way the CLI does.

    Refresh tokens rotate: once Edward has refreshed, the pair on disk is
    dead for ``codex exec`` (orchestrator CC tasks) unless it is replaced. Other keys
    (auth_mode, OPENAI_API_KEY, account_id) are preserved; the write is
    atomic (temp file + rename) and the file stays owner-only.
    """
    import tempfile
    from datetime import datetime, timezone

    p = Path(path)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    tokens.update({k: v for k, v in tokens_update.items() if v})
    data["tokens"] = tokens
    data.setdefault("auth_mode", "chatgpt")
    data.setdefault("OPENAI_API_KEY", None)
    data["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".auth-", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class CodexAuthManager:
    """Manages OpenAI API credentials.

    Priority:
    1. OPENAI_API_KEY env var (standard API key — works immediately)
    2. Codex OAuth tokens from ~/.codex/auth.json (ChatGPT subscription — requires API scopes)
    """

    def __init__(self):
        self._api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._account_id: Optional[str] = None
        self._last_refresh: Optional[float] = None
        self._auth_source: Optional[str] = None  # "api_key" or "codex_oauth"
        # Single-flight refresh state (see ``_refresh_once``).
        self._refresh_lock: Optional[asyncio.Lock] = None
        self._refresh_lock_loop = None
        self._refresh_generation = 0
        self._last_refresh_outcome = False
        self._last_refresh_ok_at: Optional[float] = None  # time.monotonic()

        if self._api_key:
            self._auth_source = "api_key"
            logger.info("OpenAI API key found in environment")
        else:
            self._load_codex_tokens()

    def _load_codex_tokens(self) -> None:
        """Read tokens from ~/.codex/auth.json."""
        try:
            path = Path(CODEX_AUTH_PATH)
            if not path.exists():
                logger.info("Codex auth file not found at %s", CODEX_AUTH_PATH)
                return

            data = json.loads(path.read_text())
            tokens = data.get("tokens", {})
            self._access_token = tokens.get("access_token")
            self._refresh_token = tokens.get("refresh_token")
            self._account_id = tokens.get("account_id")
            self._last_refresh = time.time()

            if self._access_token:
                self._auth_source = "codex_oauth"
                logger.info("Codex OAuth tokens loaded (note: may lack API scopes)")
            else:
                logger.warning("Codex auth file exists but no access_token found")
        except Exception as e:
            logger.warning("Failed to load Codex auth tokens: %s", e)

    def is_available(self) -> bool:
        """Check if any OpenAI credentials are usable."""
        if self._api_key:
            return True
        if self._access_token:
            return True
        # Try reloading in case tokens were added since startup
        self._api_key = os.getenv("OPENAI_API_KEY")
        if self._api_key:
            self._auth_source = "api_key"
            return True
        self._load_codex_tokens()
        return self._access_token is not None

    def get_api_key(self) -> Optional[str]:
        """Return the best available API key/token (sync, for _build_llm)."""
        if self._api_key:
            return self._api_key
        return self._access_token

    def _lock(self) -> asyncio.Lock:
        """The refresh lock, made for the running loop (a Lock is bound to
        the loop it was first used on; tests run one loop per test)."""
        loop = asyncio.get_running_loop()
        if self._refresh_lock is None or self._refresh_lock_loop is not loop:
            self._refresh_lock = asyncio.Lock()
            self._refresh_lock_loop = loop
        return self._refresh_lock

    async def _refresh_once(self) -> bool:
        """``_refresh_codex`` single-flight.

        Callers queue on one lock. One that waited behind a refresh shares
        that refresh's outcome; a successful refresh under
        ``REFRESH_REUSE_SECONDS`` old is reused without a POST. Refresh
        tokens rotate, so parallel refreshes also raced for auth.json.
        """
        generation = self._refresh_generation
        async with self._lock():
            if self._refresh_generation != generation:
                return self._last_refresh_outcome
            if (self._last_refresh_ok_at is not None and self._access_token
                    and time.monotonic() - self._last_refresh_ok_at < REFRESH_REUSE_SECONDS):
                return True
            outcome = await self._refresh_codex()
            self._refresh_generation += 1
            self._last_refresh_outcome = outcome
            if outcome:
                self._last_refresh_ok_at = time.monotonic()
            return outcome

    async def _refresh_codex(self) -> bool:
        """Refresh the Codex OAuth access token.

        Returns True only if the access token actually *changed*. The
        file-reload fallback used to return True any time auth.json had
        SOME token, even the same dead one we already had — so the 401
        retry path would replay the same revoked token and surface a
        confusing "refresh succeeded but call still failed" error. We
        compare before/after to give callers an honest signal.
        """
        token_before = self._access_token

        if not self._refresh_token:
            logger.warning("No refresh token available — cannot refresh via endpoint")
            self._load_codex_tokens()
            return self._access_token is not None and self._access_token != token_before

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    OPENAI_TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": CODEX_CLIENT_ID,
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    self._access_token = data.get("access_token", self._access_token)
                    if data.get("refresh_token"):
                        self._refresh_token = data["refresh_token"]
                    self._last_refresh = time.time()
                    logger.info("Codex OAuth token refreshed via endpoint")
                    try:
                        _write_auth_file(CODEX_AUTH_PATH, {
                            "access_token": self._access_token,
                            "refresh_token": self._refresh_token,
                            "id_token": data.get("id_token"),
                        })
                    except Exception as write_exc:  # noqa: BLE001
                        logger.warning(
                            "Refreshed Codex token could not be written to %s (%s); "
                            "the CLI's copy is now stale until its next login", CODEX_AUTH_PATH, write_exc,
                        )
                    return self._access_token is not None and self._access_token != token_before
                else:
                    logger.warning("Token refresh failed (HTTP %d): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("Token refresh request failed: %s", e)

        # Fallback: re-read file in case an external `codex login` minted
        # fresh tokens. Only honest-True if the token on disk is genuinely
        # different from what we held going in.
        self._load_codex_tokens()
        if self._access_token and self._access_token != token_before:
            logger.info("Codex token picked up from rewritten auth.json")
            return True
        return False

    async def force_refresh(self) -> bool:
        """Refresh after a 401 — single-flight, so concurrent 401s make one
        refresh, and one that finished under a minute ago is reused."""
        if self._api_key:
            return True  # API keys don't need refresh
        return await self._refresh_once()

    def get_status(self) -> dict:
        """Return status info for health checks."""
        return {
            "available": self.is_available(),
            "auth_source": self._auth_source,
            "has_api_key": self._api_key is not None,
            "has_codex_token": self._access_token is not None,
            "has_refresh_token": self._refresh_token is not None,
            "last_refresh": self._last_refresh,
            "auth_file_exists": Path(CODEX_AUTH_PATH).exists(),
        }


# Singleton
_manager: Optional[CodexAuthManager] = None


def get_codex_auth_manager() -> CodexAuthManager:
    """Get or create the singleton CodexAuthManager."""
    global _manager
    if _manager is None:
        _manager = CodexAuthManager()
    return _manager
