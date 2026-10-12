"""The manual's Rails line says "the computer enforces these" (area 2
integration, 2026-10-11): every program it names as refused must be
refused by the Seatbelt profile or the precheck. Under Ellis's ruling the
profile denies writes to the live repo, launching or scripting Chrome,
signals to other processes and :5432 — not sudo, launchctl or crontab — so
the manual must not tell the model a rail exists that doesn't."""

from __future__ import annotations

import re

from services.computer import profile, rails
from services.toolset.manual import TOOL_MANUAL

PROGRAMS = {
    "sudo": "/usr/bin/sudo",
    "launchctl": "/bin/launchctl",
    "crontab": "/usr/bin/crontab",
    "security": "/usr/bin/security",
    "tccutil": "/usr/bin/tccutil",
}


def _rails_paragraph() -> str:
    m = re.search(r"\nRails \(the computer enforces these[^\n]*\n(.+)\Z", TOOL_MANUAL, re.S)
    assert m, "the manual has a Rails paragraph"
    return m.group(1)


def _enforced(program: str, path: str) -> bool:
    text = profile.render_profile()
    if re.search(rf"\(deny process-exec[^\n]*{re.escape(path)}", text):
        return True
    return rails.precheck(f"{program} -h") is not None


def test_every_refused_program_in_the_rails_paragraph_is_refused():
    para = _rails_paragraph()
    claimed = [p for p in PROGRAMS if re.search(rf"\b{p}\b", para)]
    unenforced = [p for p in claimed if not _enforced(p, PROGRAMS[p])]
    assert not unenforced, f"the manual says the computer refuses {unenforced}, but nothing does"
