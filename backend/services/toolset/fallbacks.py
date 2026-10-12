"""Stand-ins for the tools that live in their own modules.

``bash`` and ``str_replace_based_edit_tool`` are
``toolset/computer_tools.py``'s; ``web_search``, ``web_fetch``, ``browser``,
``chrome`` and ``look`` are ``toolset/web.py``'s, ``browsers.py``'s and
``look.py``'s. The registry imports those when present. When one is missing
or fails to import:

* ``web_search`` / ``web_fetch`` run today's Brave search and page fetch;
* ``chrome`` dispatches to today's Chrome hands (``browser_*``) under the
  Chrome rails (``policy.chrome_action_allowed``);
* ``look`` describes a screen, a Chrome tab, an iMessage attachment or a
  local image in words (today's behaviour);
* ``bash``, the editor and ``browser`` answer ``Tool error`` — honestly
  not installed, never a pretend result.

Each has the real tool's name, so the toolset keeps its order and the
cached prefix its shape. Nothing here is reached while those modules load.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from services.owner import owner_doc, owner_name
from services.toolset import policy as policy_mod
from services.toolset.result import ToolFailure


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _checked(reply: Any, heads=("Tool error:", "Failed", "Could not", "Error", "Search failed", "No search")) -> str:
    text = str(reply if reply is not None else "")
    if text.strip().startswith(heads):
        raise ToolFailure(text.strip().removeprefix("Tool error:").strip())
    return text


@tool
async def bash(command: Optional[str] = None, restart: bool = False) -> str:
    """Run a command in your persistent shell (starting in ~/edward-home)."""
    raise ToolFailure("your shell isn't installed on this build yet — say what you'd run, or use another route")


@tool
async def str_replace_based_edit_tool(command: str, path: str, file_text: Optional[str] = None,
                                      old_str: Optional[str] = None, new_str: Optional[str] = None,
                                      insert_line: Optional[int] = None, view_range: Optional[List[int]] = None) -> str:
    """View, create and edit files."""
    raise ToolFailure("the file editor isn't installed on this build yet")


@tool
async def web_search(query: str, count: int = 5) -> str:
    """Search the web; returns titles, links and snippets."""
    return _checked(await _legacy().web_search.ainvoke({"query": query, "count": count}))


@tool
async def web_fetch(url: str) -> str:
    """Read a web page as text."""
    return _checked(await _legacy().fetch_page_content.ainvoke({"url": url}))


@tool
async def browser(action: str, url: Optional[str] = None, ref: Optional[int] = None, text: Optional[str] = None,
                  submit: bool = True, key: Optional[str] = None, direction: Optional[str] = None,
                  amount: Optional[int] = None, seconds: Optional[float] = None, query: Optional[str] = None) -> str:
    """Your own invisible browser for pages web_fetch can't read."""
    raise ToolFailure("your own browser isn't installed on this build yet — use web_fetch, or chrome if it needs "
                      f"{owner_name()}'s session")


_CHROME_LEGACY = {
    "tabs": ("browser_tabs", ()),
    "open": ("browser_open", ("url", "fullscreen", "new_tab")),
    "use": ("browser_use_tab", ("tab_id",)),
    "read": ("browser_read", ()),
    "find": ("browser_find", ("query",)),
    "click": ("browser_click", ("ref",)),
    "type": ("browser_type", ("text", "ref", "submit")),
    "key": ("browser_key", ("key",)),
    "scroll": ("browser_scroll", ("direction", "amount")),
    "back": ("browser_back", ()),
    "wait": ("browser_wait", ("text", "seconds")),
    "fullscreen": ("browser_fullscreen", ("on",)),
    "steps": ("browser_steps", ("steps",)),
    "done": ("browser_done", ("close_tab",)),
}


def _working_tab_url() -> Optional[str]:
    try:
        from services import browser_hands

        return ((browser_hands.status() or {}).get("tab") or {}).get("url")
    except Exception:  # noqa: BLE001
        return None


@tool
@owner_doc
async def chrome(action: str, url: Optional[str] = None, tab_id: Optional[int] = None, new_tab: bool = True,
                 fullscreen: Optional[bool] = None, ref: Optional[int] = None, text: Optional[str] = None,
                 submit: bool = True, key: Optional[str] = None, direction: Optional[str] = None,
                 amount: Optional[int] = None, seconds: Optional[float] = None, query: Optional[str] = None,
                 steps: Optional[List[Dict]] = None, close_tab: Optional[bool] = None) -> str:
    """{OWNER}'s real Chrome on this Mac, with their logins; it is in front of them. Use it only when you need
    their session or to put something on their monitor. action: tabs|open|use|read|find|click|type|key|scroll|back|
    wait|fullscreen|steps|done. done when finished."""
    act = (action or "").strip().lower()
    if act not in _CHROME_LEGACY:
        raise ToolFailure(f"action must be one of {', '.join(_CHROME_LEGACY)}")
    given: Dict[str, Any] = {"url": url, "tab_id": tab_id, "new_tab": new_tab, "fullscreen": fullscreen, "ref": ref,
                             "text": text, "submit": submit, "key": key, "direction": direction, "amount": amount,
                             "seconds": seconds, "query": query, "steps": steps, "close_tab": close_tab,
                             "on": True if fullscreen is None else fullscreen}
    refusal = policy_mod.chrome_action_allowed(act, given, current_url=_working_tab_url())
    if refusal:
        raise ToolFailure(refusal)
    if policy_mod.current_policy().dry_run_outward and act in ("type", "steps", "click", "key"):
        return f"[dry run] Would have done chrome {act}."
    name, keys = _CHROME_LEGACY[act]
    args = {k: given[k] for k in keys if given.get(k) is not None}
    return _checked(await getattr(_legacy(), name).ainvoke(args))


@tool
async def look(target: str, question: Optional[str] = None) -> str:
    """See an image, a screen or a Chrome tab: target = a path, attachment:<id>, screen or chrome."""
    t = (target or "").strip()
    legacy = _legacy()
    if t in ("screen", "display"):
        return _checked(await legacy.look_at_screen.ainvoke({"at": "display"}))
    if t in ("chrome", "page"):
        return _checked(await legacy.look_at_screen.ainvoke({"at": "page"}))
    if t.startswith("attachment:"):
        rowid = t.split(":", 1)[1].strip().lstrip("#")
        if not rowid.isdigit():
            raise ToolFailure("attachment:<rowid> needs the number shown next to the attachment")
        reply = str(await legacy.get_imessage_attachment.ainvoke({"attachment_rowid": int(rowid)}))
        if reply.startswith(("Attachment #", "Could not read", "Image '")) and "not on this Mac" in reply:
            raise ToolFailure(reply)
        if reply.startswith(("Attachment #", "Could not read")):
            raise ToolFailure(reply)
        return reply
    if t.startswith(("/", "~")):
        import os

        from services import attachment_vision

        path = os.path.expanduser(t)
        if not os.path.isfile(path):
            raise ToolFailure(f"no such file: {t}")
        try:
            return f"{t}: " + await attachment_vision.describe_image(path, priority="interactive")
        except Exception as exc:  # noqa: BLE001
            raise ToolFailure(f"couldn't read {t} ({type(exc).__name__}: {exc})") from exc
    raise ToolFailure("this build's look reads screen, chrome, attachment:<id> and local image paths; URLs and "
                      "media:<id> come with the full look tool")


FALLBACKS = {
    "bash": bash,
    "str_replace_based_edit_tool": str_replace_based_edit_tool,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "browser": browser,
    "chrome": chrome,
    "look": look,
}
