"""Edward's screen is a tab in the owner's own Chrome.

Why (2026-09-07): a dedicated fullscreen Chrome lived in its own macOS
Space, which the owner could not reach with a mouse, and a second window
opened beside the first. So: check whether Chrome has a tab on the screen
page; if so bring it forward and size it to the display; if not, open one.
Never a fullscreen Space. The window is the owner's; Edward's hands in it
go through the extension (``browser_hands``).

Only with ``EDWARD_CHROME_HANDS=on``: while the hands are off nothing here
opens, raises, resizes, relaunches or quits Chrome — the /screen page
still works when someone opens it by hand.

``is_screen_url`` / ``ensure_script`` / ``parse_result`` are pure;
``ensure_screen_tab`` runs the AppleScript (needs Automation permission
for Google Chrome the first time — macOS asks on screen).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from services import host_config

SCREEN_PATHS = ("/screen", "/display", "/fullscreen")
OSASCRIPT_TIMEOUT_S = 20


def screen_hosts() -> Tuple[str, ...]:
    """The hosts that serve Edward's own /screen: ``host_config.own_hosts()``
    (the public URL's host, localhost, this Mac's Bonjour name) plus the
    ``www.`` form of a public hostname. Read per call."""
    hosts = set(host_config.own_hosts())
    for h in list(hosts):
        if ("." in h and not h.startswith("www.") and not h.endswith(".local")
                and not h.replace(".", "").isdigit()):
            hosts.add("www." + h)
    return tuple(sorted(hosts))


def __getattr__(name: str) -> Any:
    # ``SCREEN_HOSTS`` was a fixed tuple; it is computed on each read now.
    if name == "SCREEN_HOSTS":
        return screen_hosts()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def public_screen_url() -> str:
    return host_config.public_screen_url()


def hands_enabled() -> bool:
    """May anything here touch the owner's Chrome (``EDWARD_CHROME_HANDS=on``)?"""
    from services.browser_ext_service import hands_enabled as _enabled

    return _enabled()


def _hands_off_result() -> Dict[str, Any]:
    from services.browser_ext_service import HANDS_OFF

    return {"ok": False, "opened": False, "off": True, "error": HANDS_OFF}


def is_screen_url(url: Optional[str]) -> bool:
    p = urlparse(str(url or ""))
    return (p.hostname or "").lower() in screen_hosts() and p.path.rstrip("/") in SCREEN_PATHS


# ADAPT: the screen is a tab in Google Chrome, found and sized over AppleScript (Finder, System Events)
#   How it works here: Chrome is the owner's everyday browser on this Mac; the script reuses or opens the
#   /screen tab, leaves any fullscreen Space and fills the desktop (needs Automation and Accessibility grants).
#   To adapt: keep EDWARD_CHROME_HANDS off and open /screen yourself on any display, or ask Claude to write
#   an equivalent for your browser (Safari, Arc) behind the same ensure_screen_tab() seam.
def ensure_script(new_url: str) -> str:
    """Find the screen tab (any of its hosts/paths) or open one; leave any
    fullscreen Space; fill the desktop; bring Chrome forward. Prints
    'found' or 'opened'."""
    hosts = ", ".join(f'"{h}"' for h in screen_hosts())
    paths = ", ".join(f'"{p}"' for p in SCREEN_PATHS)
    return f'''
on isScreenURL(u)
  set hosts to {{{hosts}}}
  set paths to {{{paths}}}
  set hostOK to false
  repeat with h in hosts
    if u contains ("://" & h & "/") or u contains ("://" & h & ":") then set hostOK to true
  end repeat
  if not hostOK then return false
  repeat with p in paths
    if u contains (p & "?") or u ends with p or u ends with (p & "/") then return true
  end repeat
  return false
end isScreenURL

tell application "Finder" to set {{x0, y0, x1, y1}} to bounds of window of desktop

set outcome to "opened"
tell application "Google Chrome"
  set target to missing value
  repeat with w in windows
    set i to 1
    repeat with t in tabs of w
      if my isScreenURL(URL of t) then
        set active tab index of w to i
        set target to w
        set outcome to "found"
        exit repeat
      end if
      set i to i + 1
    end repeat
    if target is not missing value then exit repeat
  end repeat
  if target is missing value then
    if (count of windows) = 0 then
      make new window
      set target to window 1
      set URL of active tab of target to "{new_url}"
    else
      set target to window 1
      set cur to URL of active tab of target
      if cur is "chrome://newtab/" or cur is "about:blank" or cur is "" then
        set URL of active tab of target to "{new_url}"
      else
        tell target to make new tab with properties {{URL:"{new_url}"}}
      end if
    end if
  end if
  set index of target to 1
  activate
end tell

-- Leave a fullscreen Space if the window is in one.
try
  tell application "System Events" to tell process "Google Chrome"
    if (value of attribute "AXFullScreen" of window 1) is true then
      set value of attribute "AXFullScreen" of window 1 to false
      delay 1.2
    end if
  end tell
end try

-- Accessibility only lists windows on the current Space. Chrome says it
-- has the window; if System Events cannot see it, it lives on another
-- Space (not everyone can switch Spaces with a mouse). Python decides what to do.
set hereCount to -1
try
  tell application "System Events" to set hereCount to (count of windows of process "Google Chrome")
end try
if hereCount is 0 then
  set outcome to outcome & "+elsewhere"
else if hereCount > 0 then
  set outcome to outcome & "+here"
end if

tell application "Google Chrome" to set bounds of window 1 to {{x0, y0, x1, y1}}
return outcome
'''


def parse_result(stdout: str) -> Dict[str, Any]:
    line = (stdout or "").strip().splitlines()[-1].strip() if (stdout or "").strip() else ""
    base, _, extra = line.partition("+")
    if base in ("found", "opened"):
        return {"ok": True, "opened": base == "opened", "elsewhere": extra == "elsewhere", "moved": False}
    return {"ok": False, "opened": False, "error": line or "no result"}


OTHER_TABS_SCRIPT = """
tell application "Google Chrome"
  set n to 0
  repeat with w in windows
    repeat with t in tabs of w
      set u to URL of t
      if not (u contains "/screen" or u contains "/display" or u contains "/fullscreen" or u is "chrome://newtab/" or u is "about:blank") then set n to n + 1
    end repeat
  end repeat
  return n
end tell
"""


async def _osascript(script: str, *, timeout: float = OSASCRIPT_TIMEOUT_S) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(script.encode()), timeout=timeout)
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def _run(*argv: str, timeout: float = 10) -> int:
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.wait_for(proc.wait(), timeout=timeout)
    return proc.returncode or 0


# The Edward extension (chrome-extension/) is loaded once by the owner via
# chrome://extensions → Load unpacked; branded Chrome ignores
# --load-extension, so it is not passed. Unpacked extensions persist in
# the profile and reconnect on every launch.
CHROME_LAUNCH_FLAGS = (
    "--autoplay-policy=no-user-gesture-required",
)
# Hides Chrome's "Edward started debugging this browser" bar (over
# full-screen video, say). That bar is the honest signal that Edward is
# driving, so it is shown unless EDWARD_CHROME_SILENT_DEBUGGER=on.
SILENT_DEBUGGER_FLAG = "--silent-debugger-extension-api"


def silent_debugger() -> bool:
    return (os.getenv("EDWARD_CHROME_SILENT_DEBUGGER") or "off").strip().lower() in ("on", "1", "true", "yes")


def chrome_launch_flags() -> Tuple[str, ...]:
    return CHROME_LAUNCH_FLAGS + ((SILENT_DEBUGGER_FLAG,) if silent_debugger() else ())


def launch_argv(url: str) -> list:
    """`open -a` a fresh Chrome on the URL with audible autoplay allowed —
    otherwise a video Edward puts in his tile sits on its play button until
    someone clicks the page. Flags only apply to a fresh launch."""
    return ["open", "-a", "Google Chrome", url, "--args", *chrome_launch_flags()]


async def _launch_chrome(url: str) -> None:
    if not hands_enabled():
        return
    await _run(*launch_argv(url))
    for _ in range(40):
        if await _chrome_ui_running():
            break
        await asyncio.sleep(0.25)
    await asyncio.sleep(3)


# A Chrome process with no renderers and a link that has been down this
# long is a window-less zombie: its extension worker is unloaded and can
# never reconnect. Quit it and launch fresh.
RELAUNCH_AFTER_S = 60.0


def chrome_needs_relaunch(*, process_running: bool, renderers: int, link_connected: bool, link_down_s: Optional[float]) -> bool:
    """Pure: relaunch only a running, window-less Chrome whose extension link
    has been down past the grace period. A Chrome with renderers may be in
    use by the owner (the extension may just need its reload arrow); no
    process at all is an ordinary launch, not a relaunch."""
    if not process_running or link_connected or renderers > 0:
        return False
    return link_down_s is not None and link_down_s >= RELAUNCH_AFTER_S


async def _chrome_renderers() -> int:
    proc = await asyncio.create_subprocess_exec("ps", "-eo", "command", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return sum(1 for line in out.decode(errors="replace").splitlines() if "Google Chrome Helper (Renderer)" in line)


async def _quit_chrome() -> None:
    if not hands_enabled():
        return
    try:
        await _osascript('tell application "Google Chrome" to quit')
    except Exception:  # noqa: BLE001 — a profile-less Chrome may not answer AppleScript
        pass
    for _ in range(20):
        if not await _chrome_ui_running():
            return
        await asyncio.sleep(0.5)
    await _run("pkill", "-TERM", "-f", "MacOS/Google Chrome$")
    for _ in range(20):
        if not await _chrome_ui_running():
            return
        await asyncio.sleep(0.5)


async def recover_hands(*, url: Optional[str] = None) -> Dict[str, Any]:
    """Called by browser_ext_service when the link has stayed down: relaunch
    a zombie Chrome, launch a missing one, otherwise leave the owner's
    Chrome be. With the hands off it does nothing. Never raises."""
    if not hands_enabled():
        return {"action": "none", "reason": "Chrome hands are off (EDWARD_CHROME_HANDS)"}
    try:
        from services import browser_ext_service as ext

        st = ext.status()
        running = await _chrome_ui_running()
        renderers = await _chrome_renderers() if running else 0
        if st.get("connected"):
            return {"action": "none", "reason": "link is up"}
        if not running:
            print("[screen] Chrome is not running; bringing the screen tab up launches it")
            res = await ensure_screen_tab(url=url)
            return {"action": "launched", "result": res}
        if not chrome_needs_relaunch(process_running=running, renderers=renderers, link_connected=False, link_down_s=st.get("down_for_s")):
            return {"action": "none", "reason": f"Chrome has {renderers} renderer(s); leaving it to the owner"}
        print(f"[screen] Chrome is running with no windows and the extension link has been down {st.get('down_for_s')}s; relaunching")
        await _quit_chrome()
        res = await ensure_screen_tab(url=url)
        return {"action": "relaunched", "result": res}
    except Exception as exc:  # noqa: BLE001
        return {"action": "error", "error": str(exc)[:200]}


async def _chrome_ui_running() -> bool:
    """The owner's Chrome (not the headless one)."""
    proc = await asyncio.create_subprocess_exec("ps", "-eo", "command", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    for line in out.decode(errors="replace").splitlines():
        if "MacOS/Google Chrome" in line and "Helper" not in line and "--headless" not in line:
            return True
    return False


async def _relaunch_on_this_space(url: str) -> bool:
    """A window on another Space cannot be pulled over by a background
    process, and a new window opens beside it there. A freshly launched
    Chrome opens on the desktop the owner is on — so, when Chrome holds
    nothing but Edward's tab, quit it and open it again with the URL."""
    if not hands_enabled():
        return False
    rc, out, _ = await _osascript(OTHER_TABS_SCRIPT)
    if rc != 0 or (out.strip() or "0") != "0":
        print(f"[screen] screen tab is on another desktop but Chrome has other tabs open ({out.strip()}); leaving it")
        return False
    await _osascript('tell application "Google Chrome" to quit')
    for _ in range(40):
        if not await _chrome_ui_running():
            break
        await asyncio.sleep(0.25)
    await _launch_chrome(url)
    return True


async def ensure_screen_tab(*, url: Optional[str] = None) -> Dict[str, Any]:
    """Bring Edward's screen up in the owner's Chrome. Never raises; with
    the hands off it touches nothing and says so."""
    if not hands_enabled():
        return _hands_off_result()
    url = url or public_screen_url()
    script = ensure_script(url)
    try:
        if not await _chrome_ui_running():
            await _launch_chrome(url)   # a launch we control: autoplay allowed, on this desktop
        rc, out, err = await _osascript(script)
    except asyncio.TimeoutError:
        print("[screen] osascript timed out bringing the screen up (is macOS asking to allow controlling Chrome?)")
        return {"ok": False, "opened": False, "error": "osascript timed out (is macOS asking to allow controlling Chrome?)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "opened": False, "error": str(exc)[:200]}
    if rc != 0:
        msg = err.strip()[-300:]
        print(f"[screen] could not bring the screen up in Chrome: {msg}")
        return {"ok": False, "opened": False, "error": msg or f"osascript exit {rc}"}
    res = parse_result(out)
    if res.get("elsewhere"):
        try:
            if await _relaunch_on_this_space(url):
                rc, out, err = await _osascript(script)   # size it again, now on this desktop
                res = parse_result(out) if rc == 0 else res
                res["moved"] = True
        except Exception as exc:  # noqa: BLE001
            res["error"] = f"relaunch failed: {str(exc)[:160]}"
    # AppleScript's `activate` is ignored when another app has focus;
    # LaunchServices activation is not.
    try:
        await _run("open", "-a", "Google Chrome")
    except Exception as exc:  # noqa: BLE001
        res["activate_error"] = str(exc)[:120]
    print(f"[screen] screen tab {'opened' if res.get('opened') else 'found'} in Chrome" if res.get("ok") else f"[screen] {res}")
    return res


def ensure_in_background(*, url: Optional[str] = None) -> None:
    if not hands_enabled():
        return
    try:
        asyncio.get_running_loop().create_task(ensure_screen_tab(url=url))
    except RuntimeError:
        pass
