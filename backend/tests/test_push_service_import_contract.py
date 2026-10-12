"""
Contract guard: every ``from services.push_service import <name>`` in
the codebase must resolve.

Background: on 2026-05-27 we discovered three proactive services
(open_loop_nudge, morning_briefing, meeting_prep) and alexa_service
were importing names that no longer exist (`send_push_to_all`,
`send_notification`). The imports are nested inside functions, so
the modules loaded fine and the breakage only fired at runtime —
silently printing ``Push delivery failed: cannot import name X`` and
the user stopped getting morning briefings without noticing for days.

This static scan catches that class of drift before it ships.
"""

import ast
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _push_service_exports() -> set[str]:
    """Names defined at module top level in services/push_service.py."""
    src = (BACKEND_DIR / "services" / "push_service.py").read_text()
    tree = ast.parse(src)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def _imports_of_push_service() -> list[tuple[Path, int, str]]:
    """Find every ``from services.push_service import <name>`` site,
    including nested-inside-functions imports. Returns
    (file, line_no, imported_name) tuples."""
    hits: list[tuple[Path, int, str]] = []
    pattern = re.compile(r"from\s+services\.push_service\s+import\s+(.+)")
    for path in BACKEND_DIR.rglob("*.py"):
        if ".venv" in path.parts or "/tests/" in str(path):
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            m = pattern.search(line)
            if not m:
                continue
            for chunk in m.group(1).split(","):
                # Strip trailing comments, parens (multi-line imports),
                # and ``as`` aliases. Skip anything that isn't a Python
                # identifier — that's the ``(`` / ``)`` from
                # ``from x import (\n  a, b,\n)`` syntax.
                chunk = chunk.split("#")[0].strip(" ()\t")
                if " as " in chunk:
                    chunk = chunk.split(" as ")[0].strip()
                if chunk and chunk.isidentifier():
                    hits.append((path.relative_to(BACKEND_DIR), i, chunk))
    return hits


def test_every_push_service_import_resolves():
    exports = _push_service_exports()
    broken = [
        f"{path}:{line}  imports `{name}`  (not defined in push_service.py)"
        for path, line, name in _imports_of_push_service()
        if name not in exports
    ]
    assert not broken, (
        "Stale imports from services.push_service detected — these will "
        "silently fail at runtime:\n  " + "\n  ".join(broken)
    )
