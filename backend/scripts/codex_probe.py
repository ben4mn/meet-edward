"""What the backend's Codex would run with, checked by hand — no model call.

Evolution v2 Phase 0 (2026-09-27; docs/architecture/codex-and-orchestrator.md).
Prints:

1. ``codex --version`` and the argv contract — every flag
   ``codex_cli_service._build_codex_cmd`` can emit, and any the installed
   ``codex exec --help`` does not list (``run_codex`` refuses to spawn then);
2. whether a command inside Codex's sandbox (``codex sandbox``, seatbelt)
   can reach localhost Postgres — the test cluster on :5433 and production
   on :5432 — under the read-only and workspace-write policies, and with
   workspace-write's ``network_access`` knob on. The command is a TCP
   connect from Python; nothing talks to Postgres and nothing calls a model.

The sandboxed command gets the same allowlisted environment a real session
gets (``sandbox_env``). ``codex sandbox`` has no ``--ignore-user-config``,
so it reads the owner's ``~/.codex/config.toml`` for everything not overridden
here; that is harmless for a TCP connect.

    cd backend && .venv/bin/python scripts/codex_probe.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.codex_cli_service import argv_contract, codex_preflight  # noqa: E402
from services.sandbox_env import sandbox_env  # noqa: E402

PORTS = (5433, 5432)
CONNECT = """
import socket
out = []
for port in {ports}:
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("127.0.0.1", port))
        out.append(f"{{port}} reachable")
    except Exception as e:
        out.append(f"{{port}} blocked ({{type(e).__name__}}: {{e}})")
    finally:
        s.close()
print("; ".join(out))
""".format(ports=PORTS)

POLICIES = (
    ("read-only", ["-c", 'sandbox_mode="read-only"']),
    ("workspace-write", ["-c", 'sandbox_mode="workspace-write"']),
    ("workspace-write + network_access=true",
     ["-c", 'sandbox_mode="workspace-write"', "-c", "sandbox_workspace_write.network_access=true"]),
)


def sandbox_reach(extra: list, cwd: str) -> str:
    # No -C: with it, codex sandbox also demands --permission-profile.
    argv = ["codex", "sandbox", *extra, "--", sys.executable, "-c", CONNECT]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=cwd, env=sandbox_env())
    except FileNotFoundError:
        return "codex not installed"
    except subprocess.TimeoutExpired:
        return "timed out"
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if lines:
        return lines[-1]
    return f"no output (exit {proc.returncode}): {(proc.stderr or '').strip()[-300:]}"


def main() -> int:
    pre = codex_preflight(force=True)
    print(f"version:        {pre.get('version')}")
    print(f"argv contract:  {' '.join(argv_contract())}")
    print(f"missing flags:  {', '.join(pre.get('missing') or []) or 'none'}")
    print(f"preflight:      {'ok' if pre.get('ok') else 'FAILED — ' + str(pre.get('error'))}")
    print("sandbox network to localhost Postgres (TCP connect only):")
    with tempfile.TemporaryDirectory(prefix="codex-probe-") as cwd:
        for label, extra in POLICIES:
            print(f"  {label:<40} {sandbox_reach(extra, os.path.realpath(cwd))}")
    return 0 if pre.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
