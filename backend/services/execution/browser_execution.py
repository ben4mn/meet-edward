"""
Browser automation execution service.

Provides headless browser automation via Lightpanda (CDP-compatible, Zig-based):
- Lightpanda subprocess managed as a long-lived server
- Fresh Playwright CDP connection per action (Lightpanda supports one page per connection)
- Navigation, clicking, typing, text extraction
- Security: blocked file://, javascript:, data: URL schemes
- 60s timeout per action, 100KB output limit on extracted text

Note: Lightpanda is a "true headless" browser — screenshots are not supported.
Text extraction and DOM interaction work normally.

Env vars:
  LIGHTPANDA_PATH  — path to lightpanda binary (default: ~/bin/lightpanda)
  LIGHTPANDA_PORT  — CDP server port (default: 9222)
"""

import asyncio
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict

from services.execution.base import ExecutionResult, _get_sandbox_dir


# Browser automation limits
BROWSER_LIMITS = {
    "timeout_ms": 60_000,  # 60s per action
    "max_output_bytes": 100_000,  # 100KB text extraction limit
}

# Blocked URL schemes for security
BLOCKED_SCHEMES = ("file://", "javascript:", "data:")

# Lightpanda process (long-lived)
_lightpanda_process = None
_lightpanda_lock = asyncio.Lock()

# Per-conversation last-visited URL (for context in multi-step interactions)
_last_urls: Dict[str, str] = {}


def _get_lightpanda_path() -> str:
    return os.environ.get(
        "LIGHTPANDA_PATH",
        str(Path.home() / "bin" / "lightpanda"),
    )


def _get_lightpanda_port() -> str:
    return os.environ.get("LIGHTPANDA_PORT", "9222")


def _validate_url(url: str) -> Optional[str]:
    url_lower = url.lower().strip()
    for scheme in BLOCKED_SCHEMES:
        if url_lower.startswith(scheme):
            return f"URL scheme '{scheme}' is blocked for security reasons."
    return None


def _is_lightpanda_alive() -> bool:
    if _lightpanda_process is None or _lightpanda_process.poll() is not None:
        return False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", int(_get_lightpanda_port())))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


async def _ensure_lightpanda() -> None:
    global _lightpanda_process

    async with _lightpanda_lock:
        if _is_lightpanda_alive():
            return

        if _lightpanda_process:
            _lightpanda_process.terminate()
            try:
                _lightpanda_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _lightpanda_process.kill()
            _lightpanda_process = None

        lp_path = _get_lightpanda_path()
        lp_port = _get_lightpanda_port()

        _lightpanda_process = subprocess.Popen(
            [lp_path, "serve", "--host", "127.0.0.1", "--port", lp_port, "--timeout", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        for _ in range(10):
            await asyncio.sleep(0.3)
            if _lightpanda_process.poll() is not None:
                stderr = _lightpanda_process.stderr.read().decode() if _lightpanda_process.stderr else ""
                _lightpanda_process = None
                raise RuntimeError(f"Lightpanda exited unexpectedly: {stderr}")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect(("127.0.0.1", int(lp_port)))
                s.close()
                print(f"Browser automation: Lightpanda started (port {lp_port})")
                return
            except (ConnectionRefusedError, OSError):
                continue

        raise RuntimeError(f"Lightpanda failed to start on port {lp_port}")


async def _run_with_page(callback):
    await _ensure_lightpanda()
    lp_port = _get_lightpanda_port()

    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{lp_port}")
        try:
            context = await browser.new_context()
            page = await context.new_page()
            result = await callback(page)
            return result
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


def _truncate_text(text: str) -> tuple[str, bool]:
    max_bytes = BROWSER_LIMITS["max_output_bytes"]
    if len(text) > max_bytes:
        return text[:max_bytes] + "\n... [output truncated]", True
    return text, False


async def navigate(
    conversation_id: str,
    url: str,
    wait_for: Optional[str] = None,
) -> ExecutionResult:
    """Navigate to a URL and return page title + text content."""
    error = _validate_url(url)
    if error:
        return ExecutionResult(success=False, output="", error=error)

    start_time = time.time()
    timeout_ms = BROWSER_LIMITS["timeout_ms"]

    try:
        async def _nav(page):
            wait_until = wait_for or "domcontentloaded"
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            title = await page.title()
            text_content = await page.inner_text("body")
            return title, text_content

        title, text_content = await _run_with_page(_nav)
        _last_urls[conversation_id] = url

        text_content, truncated = _truncate_text(text_content)
        duration_ms = int((time.time() - start_time) * 1000)

        output = f"Title: {title}\n\n{text_content}"
        return ExecutionResult(
            success=True,
            output=output,
            duration_ms=duration_ms,
            truncated=truncated,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Navigation failed: {str(e)}",
            duration_ms=duration_ms,
        )


async def click(conversation_id: str, selector: str) -> ExecutionResult:
    """Navigate to the last URL and click an element."""
    last_url = _last_urls.get(conversation_id)
    if not last_url:
        return ExecutionResult(
            success=False, output="",
            error="No page loaded. Use browse_web first to navigate to a URL.",
        )

    start_time = time.time()
    timeout_ms = BROWSER_LIMITS["timeout_ms"]

    try:
        async def _click(page):
            await page.goto(last_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.click(selector, timeout=timeout_ms)

        await _run_with_page(_click)

        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=True,
            output=f"Clicked element: {selector}",
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Click failed: {str(e)}",
            duration_ms=duration_ms,
        )


async def type_text(
    conversation_id: str, selector: str, text: str
) -> ExecutionResult:
    """Navigate to the last URL and type text into an input field."""
    last_url = _last_urls.get(conversation_id)
    if not last_url:
        return ExecutionResult(
            success=False, output="",
            error="No page loaded. Use browse_web first to navigate to a URL.",
        )

    start_time = time.time()
    timeout_ms = BROWSER_LIMITS["timeout_ms"]

    try:
        async def _type(page):
            await page.goto(last_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.fill(selector, text, timeout=timeout_ms)

        await _run_with_page(_type)

        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=True,
            output=f"Typed text into: {selector}",
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Type failed: {str(e)}",
            duration_ms=duration_ms,
        )


async def extract_text(
    conversation_id: str, selector: Optional[str] = None
) -> ExecutionResult:
    """Navigate to the last URL and extract text content."""
    last_url = _last_urls.get(conversation_id)
    if not last_url:
        return ExecutionResult(
            success=False, output="",
            error="No page loaded. Use browse_web first to navigate to a URL.",
        )

    start_time = time.time()
    timeout_ms = BROWSER_LIMITS["timeout_ms"]

    try:
        async def _extract(page):
            await page.goto(last_url, wait_until="domcontentloaded", timeout=timeout_ms)
            target_selector = selector or "body"
            return await page.inner_text(target_selector, timeout=timeout_ms)

        text = await _run_with_page(_extract)

        text, truncated = _truncate_text(text)
        duration_ms = int((time.time() - start_time) * 1000)

        return ExecutionResult(
            success=True,
            output=text,
            duration_ms=duration_ms,
            truncated=truncated,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            output="",
            error=f"Text extraction failed: {str(e)}",
            duration_ms=duration_ms,
        )


async def screenshot(conversation_id: str) -> ExecutionResult:
    """Screenshots are not supported with Lightpanda."""
    return ExecutionResult(
        success=False,
        output="",
        error="Screenshots are not supported with Lightpanda (no visual rendering engine). Use browser_extract to get page text content instead.",
    )


async def close_context(conversation_id: str) -> ExecutionResult:
    """Clear tracked state for a conversation."""
    _last_urls.pop(conversation_id, None)
    return ExecutionResult(
        success=True,
        output="Browser session closed.",
        duration_ms=0,
    )


async def shutdown_browser() -> None:
    """Shut down the Lightpanda process."""
    global _lightpanda_process

    _last_urls.clear()

    if _lightpanda_process:
        _lightpanda_process.terminate()
        try:
            _lightpanda_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _lightpanda_process.kill()
        _lightpanda_process = None
        print("Browser automation: Lightpanda shut down")


def is_available() -> bool:
    """Check if browser automation is available (Lightpanda binary found)."""
    import shutil
    lp_path = _get_lightpanda_path()
    return shutil.which(lp_path) is not None or os.path.isfile(lp_path)


def get_status() -> dict:
    """Get the status of the browser automation service."""
    available = is_available()
    active_convs = len(_last_urls)
    return {
        "status": "connected" if available else "error",
        "status_message": (
            f"Lightpanda available ({active_convs} active conversation{'s' if active_convs != 1 else ''})"
            if available
            else "Lightpanda not installed. See https://github.com/lightpanda-io/browser"
        ),
        "metadata": {
            "timeout_ms": BROWSER_LIMITS["timeout_ms"],
            "max_output_bytes": BROWSER_LIMITS["max_output_bytes"],
            "active_conversations": active_convs,
        },
    }
