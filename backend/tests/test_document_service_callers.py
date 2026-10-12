"""
Regression: prevent silent import drift between document_service and its
internal callers (consolidation, proactive/pattern_detection).

Failure signature in /tmp/edward-backend.log before the fix:

    consolidation entity narrative step failed: cannot import name
    'store_document' from 'services.document_service'

The bad imports lived inside ``try:`` blocks in consolidation_service.py
and pattern_detection.py, so the ImportError was caught and logged at
WARNING but the entity-narrative + weekly-pattern documents were never
actually written. Static analysis would have caught it; this test does
that for the boundary going forward.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _imported_names_from(module_path: Path, source_module: str) -> set[str]:
    tree = ast.parse(module_path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == source_module:
            for alias in node.names:
                names.add(alias.name)
    return names


@pytest.mark.parametrize(
    "caller_relpath",
    [
        "services/consolidation_service.py",
    ],
)
def test_document_service_imports_resolve(caller_relpath: str) -> None:
    from services import document_service

    caller_path = BACKEND_DIR / caller_relpath
    requested = _imported_names_from(caller_path, "services.document_service")

    assert requested, (
        f"{caller_relpath} doesn't import anything from services.document_service — "
        "did the path move? Update this test."
    )

    missing = sorted(n for n in requested if not hasattr(document_service, n))
    assert not missing, (
        f"{caller_relpath} imports {missing} from services.document_service, but "
        f"those symbols don't exist. Available symbols: "
        f"{sorted(a for a in dir(document_service) if not a.startswith('_'))}"
    )
