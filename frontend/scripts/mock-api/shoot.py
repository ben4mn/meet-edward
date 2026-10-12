"""Screenshot pages of the Edward UX harness and report console errors and failed API calls.

Uses the Playwright package in the backend venv with its bundled headless shell.
It never launches Google Chrome. Service workers are blocked so every load
hits the dev server fresh.

Examples
--------
    PY=../backend/.venv/bin/python   # from frontend/; any python with playwright
    # iPhone PWA (390x844, dark, standalone), several pages:
    $PY scripts/mock-api/shoot.py --urls /today /chat "/chat?c=c-cabin-weekend" /settings
    # Desktop, light, full page:
    $PY scripts/mock-api/shoot.py --urls /settings/all --width 1440 --height 900 --scheme light --full-page
    # Drive the UI: send a message, capture mid-stream and after.
    $PY scripts/mock-api/shoot.py --urls /chat --prefix send --actions '[
        {"fill": "textarea", "text": "what is the weather for kayaks saturday"},
        {"press": "Enter"}, {"wait": 1800}, {"shot": "mid"},
        {"waitFor": "text=Book the", "timeout": 20000}, {"wait": 800}, {"shot": "after"}]'

Actions (a JSON list, run in order after the page's first screenshot):
    {"click": sel}               click (Playwright selector: css, text=…, role=…)
    {"tap": sel}                 tap (touch)
    {"fill": sel, "text": s}     set an input's value
    {"type": sel, "text": s}     type key by key (optional "delay" ms)
    {"press": key}               keyboard press on the focused element ("Enter")
    {"hover": sel}
    {"upload": sel, "files": [path, …]}  set files on an <input type=file> (e.g. "input[type=file]")
    {"wait": ms}                 sleep
    {"waitFor": sel, "timeout": ms}   wait for a selector to be visible
    {"goto": path}               navigate (relative to --base)
    {"scroll": sel | int}        scroll an element into view, or the window to y
    {"eval": js}                 run JavaScript in the page; the result is printed
    {"shot": name, "fullPage": bool}  screenshot to <out>/<prefix>-<name>.png
    {"reset": true}              POST /__mock/reset on the mock (fresh fixtures)

Exit status is 1 when any page logged a console error, threw, or had an
/api request fail or answer >= 400 (unless --allow-errors). A JSON report
is printed to stdout and written to <out>/report.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1")

# Makes the page believe it runs as the installed PWA (display-mode: standalone,
# navigator.standalone) — how the owner uses it on an iPhone.
STANDALONE_JS = """
(() => {
  try { Object.defineProperty(navigator, 'standalone', { get: () => true }); } catch (e) {}
  const orig = window.matchMedia.bind(window);
  window.matchMedia = (q) => {
    if (/display-mode:\\s*standalone/.test(q)) {
      return { matches: true, media: q, onchange: null, addListener() {}, removeListener() {},
               addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } };
    }
    return orig(q);
  };
})();
"""

NOISE = [
    re.compile(r"Download the React DevTools"),
    re.compile(r"\[HMR\]"),
    re.compile(r"\[Fast Refresh\]"),
    re.compile(r"workbox", re.I),
]
# Playwright's service_workers="block" makes register() resolve to undefined;
# the app then logs this. It is the harness, not the app.
SW_BLOCKED_NOISE = re.compile(r"\[SW\] Registration failed: TypeError: Cannot read properties of undefined \(reading 'scope'\)")


def slug(path: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", path.strip("/")).strip("-")
    return s or "root"


def mock_reset(mock: str) -> None:
    req = urllib.request.Request(f"{mock}/__mock/reset", method="POST")
    urllib.request.urlopen(req, timeout=10).read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--urls", nargs="+", required=True, help="paths (or full URLs); commas also split")
    ap.add_argument("--base", default="http://localhost:3100")
    ap.add_argument("--mock", default="http://127.0.0.1:8100")
    ap.add_argument("--width", type=int, default=390)
    ap.add_argument("--height", type=int, default=844)
    ap.add_argument("--scheme", choices=["dark", "light"], default="dark")
    ap.add_argument("--scale", type=float, default=2.0, help="device scale factor")
    ap.add_argument("--out", default="/private/tmp/edward-ux-shots")
    ap.add_argument("--prefix", default="", help="filename prefix")
    ap.add_argument("--full-page", action="store_true")
    ap.add_argument("--wait", type=int, default=1800, help="ms to wait after load before the first shot")
    ap.add_argument("--actions", default=None, help="JSON list of actions (see module doc)")
    ap.add_argument("--desktop", action="store_true", help="no mobile emulation even when narrow")
    ap.add_argument("--no-standalone", action="store_true", help="do not pretend to be the installed PWA")
    ap.add_argument("--reset", action="store_true", help="reset mock fixtures before starting")
    ap.add_argument("--allow-errors", action="store_true")
    ap.add_argument("--no-shot", action="store_true", help="skip the automatic first screenshot")
    ap.add_argument("--allow-sw", action="store_true", help="let the service worker register (default: blocked)")
    args = ap.parse_args()

    urls: List[str] = [u for chunk in args.urls for u in chunk.split(",") if u.strip()]
    actions: List[Dict[str, Any]] = json.loads(args.actions) if args.actions else []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mobile = args.width < 768 and not args.desktop
    if args.reset:
        mock_reset(args.mock)

    report: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kw: Dict[str, Any] = dict(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
            color_scheme=args.scheme,
            service_workers="allow" if args.allow_sw else "block",
            timezone_id="America/Chicago",
            locale="en-US",
        )
        if mobile:
            ctx_kw.update(is_mobile=True, has_touch=True, user_agent=IPHONE_UA)
        ctx = browser.new_context(**ctx_kw)
        if not args.no_standalone and mobile:
            ctx.add_init_script(STANDALONE_JS)

        for url in urls:
            full = url if url.startswith("http") else args.base.rstrip("/") + "/" + url.lstrip("/")
            name = (args.prefix + "-" if args.prefix else "") + slug(url)
            page = ctx.new_page()
            entry: Dict[str, Any] = {"url": full, "shots": [], "console_errors": [], "console_warnings": [],
                                     "page_errors": [], "api_failures": [], "request_failures": [], "requests_aborted": [],
                                     "evals": []}

            def on_console(msg, entry=entry):
                text = msg.text
                if any(n.search(text) for n in NOISE):
                    return
                if not args.allow_sw and SW_BLOCKED_NOISE.search(text):
                    return
                if msg.type == "error":
                    entry["console_errors"].append(text[:500])
                elif msg.type == "warning":
                    entry["console_warnings"].append(text[:300])

            def on_response(resp, entry=entry):
                if "/api/" in resp.url and resp.status >= 400:
                    entry["api_failures"].append(f"{resp.status} {resp.request.method} {resp.url}")

            def on_failed(req, entry=entry):
                failure = req.failure or ""
                if "net::ERR_ABORTED" in failure:
                    # Navigation cancels in-flight fetches (the presence beacon,
                    # prefetches, a stopped stream): not an app failure.
                    entry["requests_aborted"].append(f"{req.method} {req.url}")
                    return
                entry["request_failures"].append(f"{req.method} {req.url} — {failure}")

            page.on("console", on_console)
            page.on("pageerror", lambda exc, entry=entry: entry["page_errors"].append(str(exc)[:500]))
            page.on("response", on_response)
            page.on("requestfailed", on_failed)

            t0 = time.monotonic()
            try:
                page.goto(full, wait_until="domcontentloaded", timeout=120_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:  # noqa: BLE001 — pollers keep the network busy
                    pass
                page.wait_for_timeout(args.wait)
                entry["final_url"] = page.url
                entry["title"] = page.title()
                if not args.no_shot:
                    path = out / f"{name}.png"
                    page.screenshot(path=str(path), full_page=args.full_page)
                    entry["shots"].append(str(path))

                for i, a in enumerate(actions):
                    timeout = int(a.get("timeout", 15_000))
                    if "click" in a:
                        page.click(a["click"], timeout=timeout)
                    elif "tap" in a:
                        page.tap(a["tap"], timeout=timeout)
                    elif "fill" in a:
                        page.fill(a["fill"], a.get("text", ""), timeout=timeout)
                    elif "type" in a:
                        page.type(a["type"], a.get("text", ""), delay=int(a.get("delay", 20)), timeout=timeout)
                    elif "press" in a:
                        page.keyboard.press(a["press"])
                    elif "hover" in a:
                        page.hover(a["hover"], timeout=timeout)
                    elif "upload" in a:
                        page.set_input_files(a["upload"], a.get("files", []), timeout=timeout)
                    elif "wait" in a:
                        page.wait_for_timeout(int(a["wait"]))
                    elif "waitFor" in a:
                        page.wait_for_selector(a["waitFor"], state="visible", timeout=timeout)
                    elif "goto" in a:
                        target = a["goto"] if a["goto"].startswith("http") else args.base.rstrip("/") + "/" + a["goto"].lstrip("/")
                        page.goto(target, wait_until="domcontentloaded", timeout=120_000)
                        page.wait_for_timeout(int(a.get("settle", 1200)))
                    elif "scroll" in a:
                        if isinstance(a["scroll"], (int, float)):
                            page.evaluate(f"window.scrollTo(0, {int(a['scroll'])})")
                        else:
                            page.locator(a["scroll"]).first.scroll_into_view_if_needed(timeout=timeout)
                    elif "eval" in a:
                        entry["evals"].append(page.evaluate(a["eval"]))
                    elif "reset" in a:
                        mock_reset(args.mock)
                    elif "shot" in a:
                        path = out / f"{name}-{a['shot']}.png"
                        page.screenshot(path=str(path), full_page=bool(a.get("fullPage", args.full_page)))
                        entry["shots"].append(str(path))
                    else:
                        raise ValueError(f"unknown action #{i}: {a}")
            except Exception as exc:  # noqa: BLE001
                entry["page_errors"].append(f"harness: {type(exc).__name__}: {str(exc)[:400]}")
                try:
                    path = out / f"{name}-failure.png"
                    page.screenshot(path=str(path))
                    entry["shots"].append(str(path))
                except Exception:  # noqa: BLE001
                    pass
            entry["seconds"] = round(time.monotonic() - t0, 1)
            entry["ok"] = not (entry["console_errors"] or entry["page_errors"] or entry["api_failures"]
                               or [f for f in entry["request_failures"] if "/api/" in f])
            report.append(entry)
            page.close()
        ctx.close()
        browser.close()

    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    bad = [r for r in report if not r["ok"]]
    return 1 if bad and not args.allow_errors else 0


if __name__ == "__main__":
    sys.exit(main())
