"""sentence-transformers must not paint tqdm progress bars into the log.

/tmp/edward-backend.log carried ~400 "Batches: 0%|          | 0/1" lines a
day from ``model.encode(...)`` calls; they are noise in exactly the log Ellis
reads to find real failures. Every encode call passes
``show_progress_bar=False``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_every_encode_call_disables_the_progress_bar():
    root = Path(__file__).resolve().parents[1] / "services"
    offenders = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"model\.encode\((.*?)\)\n", src, re.S):
            if "show_progress_bar=False" not in m.group(1):
                offenders.append(f"{path.relative_to(root.parent)}: {m.group(0).strip()[:80]}")
    assert not offenders, "\n".join(offenders)
