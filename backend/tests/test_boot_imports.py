"""The backend still boots after the retirements of 2026-10-11.

The proactivity rebuild deleted the surplus queue (``surplus_service``,
``surplus_jobs``), the interrupt ladder (``interrupt_service``,
``interrupt_logic``) and the empty ``services/proactive`` package. A
leftover import of any of them is an ``ImportError`` at boot — and under
launchd's ``KeepAlive`` a respawn loop — or, inside the lifespan's
``try`` blocks, a step silently skipped (the interrupt start sat in the
heartbeat's ``try``: its ImportError would also have skipped
``backfill_device_kinds``). So: the modules the lifespan and the
scheduler reach import cleanly, the retired ones are really gone, and no
code that runs at boot names them.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

RETIRED_MODULES = (
    "services.surplus_service",
    "services.surplus_jobs",
    "services.interrupt_service",
    "services.interrupt_logic",
    "services.proactive",
)

BOOT_MODULES = (
    "main",
    "services.mind_service",
    "services.wake_state",
    "services.expression_service",
    "services.scheduler_service",
    "services.push_service",
    "services.today_service",
    "services.health_report_service",
    "services.codex_usage_service",
    "services.screen_thoughts",
    "services.display_signals",
    "routers.push",
    "routers.presence",
)


@pytest.mark.parametrize("name", BOOT_MODULES)
def test_boot_modules_import(name):
    assert importlib.import_module(name) is not None


@pytest.mark.parametrize("name", RETIRED_MODULES)
def test_retired_modules_are_gone(name):
    with pytest.raises(ImportError):
        importlib.import_module(name)


def test_the_mind_keeps_its_quota_word():
    """``mind_service`` imports ``quota_state`` from the pulse's module; the
    retirements must never take it (or the wake state's helper) away."""
    from services import expression_service

    assert callable(expression_service.quota_state)
    assert callable(expression_service._quota_state)
    assert callable(expression_service.quota_state_for)


# One file owned by another branch of the rebuild keeps a guarded, lazily
# imported interrupt branch whose ImportError is caught: the legacy
# ``send_push_notification(urgency="high")`` falls back to a plain push
# (AREA 2 deletes it). The judgement check's port (1B) dropped its dry-run
# patch, so it is scanned like everything else since the integration.
_STATIC_SCAN_EXEMPT = {BACKEND / "services" / "graph" / "tools.py"}
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+services(?:\.(?P<a>surplus_service|surplus_jobs|interrupt_service|interrupt_logic|proactive))?"
    r"\s+import\s+(?P<names>[^\n#]+)|from\s+services\.(?P<b>surplus_service|surplus_jobs|interrupt_service|"
    r"interrupt_logic|proactive)(?:\.\w+)*\s+import|import\s+services\.(?P<c>surplus_service|surplus_jobs|"
    r"interrupt_service|interrupt_logic|proactive)\b)",
    re.M,
)
_RETIRED_NAMES = {"surplus_service", "surplus_jobs", "interrupt_service", "interrupt_logic", "proactive"}


def _retired_imports(text: str) -> list[str]:
    hits = []
    for m in _IMPORT_RE.finditer(text):
        if m.group("a") or m.group("b") or m.group("c"):
            hits.append(m.group(0).strip())
            continue
        names = {n.strip().split(" as ")[0] for n in (m.group("names") or "").strip("() ").split(",")}
        if names & _RETIRED_NAMES:
            hits.append(m.group(0).strip())
    return hits


def test_no_code_that_runs_names_a_retired_module():
    files = [BACKEND / "main.py", *(BACKEND / "services").rglob("*.py"), *(BACKEND / "routers").rglob("*.py"),
             *(BACKEND / "scripts").rglob("*.py")]
    offenders = {}
    for path in files:
        if path in _STATIC_SCAN_EXEMPT:
            continue
        hits = _retired_imports(path.read_text())
        if hits:
            offenders[str(path.relative_to(BACKEND))] = hits
    assert not offenders, offenders


def test_the_scan_sees_each_import_shape():
    assert _retired_imports("    from services import interrupt_service, push_ledger\n")
    assert _retired_imports("from services.surplus_service import TICK_TIMEOUT_SECONDS\n")
    assert _retired_imports("        from services.proactive import start_proactive_services\n")
    assert _retired_imports("import services.interrupt_logic\n")
    assert not _retired_imports("from services.proactive_models import ProactiveConfigSchema\n")
    assert not _retired_imports("from services import push_ledger\n")
