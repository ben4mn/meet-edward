"""``bash`` and ``str_replace_based_edit_tool`` — Edward's hands on his own
computer.

Both are Anthropic's trained-in client tools (``bash_20250124`` and
``text_editor_20250728``): under the Claude provider they go to the API as
those native dicts (carried in ``tool.metadata["anthropic_tool"]``), and the
model's calls come back as ordinary ``tool_calls`` named ``bash`` /
``str_replace_based_edit_tool`` that these LangChain bodies execute. Under
the Codex fallback they go as ordinary custom tools, which is why the
``args_schema`` and the bodies live here.

Behaviour is :mod:`services.computer.shell` and
:mod:`services.computer.editor`; the rails are the Seatbelt profile plus the
in-process ``can_write`` checks.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from services.computer import editor as _editor
from services.computer import shell as _shell
from services.toolset.result import ToolFailure

logger = logging.getLogger(__name__)

# The Anthropic client/native tool definitions (sent verbatim under Claude).
BASH_ANTHROPIC = {"type": "bash_20250124", "name": "bash"}
EDITOR_ANTHROPIC = {
    "type": "text_editor_20250728",
    "name": "str_replace_based_edit_tool",
    "max_characters": 30000,
}


def _conversation_id() -> str:
    from services.graph.tools import get_current_conversation_id
    cid = get_current_conversation_id()
    if not cid:
        raise ToolFailure("no conversation context for bash")
    return cid


@tool
async def bash(command: Optional[str] = None, restart: bool = False) -> str:
    """Run a shell command in your persistent bash for this thread (starts in
    ~/edward-home). Use it to work: run scripts, install packages into your
    venv, inspect files, drive CLIs. Pass restart=true to reset a wedged
    shell. Commands are killed after a few minutes — for long work use
    `nohup CMD > ~/edward-home/jobs/NAME.log 2>&1 &` and come back later.

    Args:
        command: the shell command to run.
        restart: reset the shell (new cwd and environment) instead of running.
    """
    cid = _conversation_id()
    result = await _shell.run(cid, command=command, restart=restart)
    return _shell.format_for_model(result)


@tool
async def str_replace_based_edit_tool(
    command: str,
    path: str,
    file_text: Optional[str] = None,
    view_range: Optional[List[int]] = None,
    old_str: Optional[str] = None,
    new_str: Optional[str] = None,
    insert_line: Optional[int] = None,
    insert_text: Optional[str] = None,
) -> str:
    """View, create and edit files. Commands: view (a file's numbered lines
    or a directory), create (write a new file), str_replace (replace one
    exact match of old_str with new_str), insert (add text after a line).
    Overwritten files keep a copy in ~/edward-home/.trash.

    Args:
        command: one of view, create, str_replace, insert.
        path: the file (~ and relative paths resolve against ~/edward-home).
        file_text: for create — the full contents.
        view_range: for view — [start_line, end_line] (end -1 for EOF).
        old_str: for str_replace — the exact text to replace (must be unique).
        new_str: for str_replace — the replacement (or insert text).
        insert_line: for insert — the line to insert after (0 = top).
        insert_text: for insert — the text to add.
    """
    return await _editor.edit(
        command, path,
        file_text=file_text, view_range=view_range,
        old_str=old_str, new_str=new_str,
        insert_line=insert_line, insert_text=insert_text,
    )


NATIVE_FLAG = "EDWARD_NATIVE_COMPUTER_TOOLS"
RESOLVER = "services.toolset.computer_tools:native_spec"
_NATIVE = {"bash": BASH_ANTHROPIC, "str_replace_based_edit_tool": EDITOR_ANTHROPIC}


def native_spec(name: str, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The Anthropic-defined dict ``name`` binds as, or None to bind the
    tool's own schema (``EDWARD_NATIVE_COMPUTER_TOOLS=off``, read per bind).
    ``ChatClaude`` calls this through ``metadata["anthropic_tool_resolver"]``,
    so the flag holds whoever binds the tool."""
    if (os.getenv(NATIVE_FLAG) or "on").strip().lower() in ("off", "0", "false", "no", "disabled"):
        return None
    spec = _NATIVE.get(name)
    return dict(spec) if spec else None


# Attach the native/server metadata and per-tool deadlines. ``bash`` has no
# outer loop deadline (it enforces its own); the editor is quick.
bash.metadata = {"anthropic_tool": BASH_ANTHROPIC, "anthropic_tool_resolver": RESOLVER, "timeout_s": None}
str_replace_based_edit_tool.metadata = {"anthropic_tool": EDITOR_ANTHROPIC, "anthropic_tool_resolver": RESOLVER,
                                        "timeout_s": 120}

COMPUTER_TOOLS = [bash, str_replace_based_edit_tool]
