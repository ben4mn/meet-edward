#!/usr/bin/env python3
"""Edward's judgement check — run the mind through real situations with
every outward act recorded and never sent, and score what he tried to do.

The design: Edward acts without asking for approval, so check that he has
good judgement. The deploy gate whenever the mind prompt or its tools
change (docs/architecture/testing.md):

    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://edward:edward@localhost:5433/judgement_test \\
      ../scripts/with-test-db-lock.sh .venv/bin/python scripts/judgement_eval.py \\
      --label sonnet-medium --json-out ~/edward-evals/judgement-sonnet-medium.json

What it guarantees:
- Process guards go on before any tool runs (``services.eval_guards``):
  Edward's computer pointed at scratch, Chrome faked, osascript / ``open``
  / screencapture refused at the process level, Edward's live local
  services off limits. The model's provider and key come from ``.env``
  when the shell set none, nothing else from it. It never runs unguarded.
- It only ever touches a TEST database (``*_test`` on the :5433 cluster).
- Every outward act is caught at the transport by a recorder that never
  sends — iMessage (``send_imessage``, ``send_imessage_to_chat``, the pings'
  ``send_to_chat``), Twilio, the push sender and the note writer — and the
  score reads the recorders (plus anything the taint gate held). A text to
  the owner is refused by the send guard first; the report shows the
  attempt.
- ``EDWARD_DRY_RUN_OUTWARD=1``: the executor answers every
  outward, surface-changing or committing call before the tool runs and
  records it (``tool_loop.dry_run_calls``); the score reads those records
  as well as the recorders and the held approvals.
- No forced tool: a mention scenario says where to reply (``reply_to``).
- Production's toolset (every skill the toolset reads is on in the test
  DB), each wake on its scenario's clock (``scenario_clock``), and his own
  browser served the scenario's pages for its wake (``install_web``).
- It uses the real model on the configured provider (Claude: ≈ $1–4 a run).

Exit code 0: no safety failures. 2: a safety failure — don't run the mind
unattended until it is understood. 1: the check itself broke.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SCENARIO_NAMES = ("mention_answerable", "standing_instruction", "follow_through_reply", "nightly_silent")


def install_eval_guards() -> str:
    """The process guards (``services.eval_guards.install_for_eval``);
    returns which. Called before the check imports anything else of
    Edward's."""
    from services import eval_guards

    which = eval_guards.install_for_eval("judgement")
    print(f"(process guards: {which})")
    return which


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Edward's judgement check (real model, nothing sent).")
    p.add_argument("--label", default=None, help="a name for this run (kept in the JSON report)")
    p.add_argument("--json-out", default=None, help="write the machine-readable report here")
    p.add_argument("--scenarios", default=None, help="comma-separated scenario names (default: all)")
    return p.parse_args(argv)


def selected(args: argparse.Namespace) -> Optional[List[str]]:
    """The scenario names asked for, or None for all. Unknown → exit."""
    if not args.scenarios:
        return None
    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIO_NAMES]
    if unknown:
        raise SystemExit(f"judgement_eval: unknown scenario(s) {unknown}; known: {list(SCENARIO_NAMES)}")
    return names


def _prepare_environment() -> str:
    """The test database and this process's flags; returns the URL."""
    from services.sandbox_env import is_test_db_url, safe_test_database_url

    url = safe_test_database_url(os.environ.get("DATABASE_URL"))
    if not is_test_db_url(url):  # pragma: no cover — safe_test_database_url never returns one
        raise SystemExit("judgement_eval: refusing to run without a *_test database")
    os.environ["DATABASE_URL"] = url
    os.environ["TEST_DATABASE_URL"] = url
    # The taint gate as production runs it: off since the toolset
    # (trust-boundary.md). ``on`` held every correct mention reply and its
    # "Approve Edward's send?" push scored as Edward buzzing the owner (the
    # deploy gate's first real run, 2026-10-11); the dry run records every send, and
    # a held send is still counted if the gate is ever turned on.
    os.environ["EDWARD_TAINT_APPROVALS"] = "off"
    # The dry run: the executor records every
    # outward, surface-changing or committing call — the owner's Chrome, the
    # shell, the screens and every send included — and runs none of them;
    # ``run_scenario`` scores those records with the recorders'.
    os.environ["EDWARD_DRY_RUN_OUTWARD"] = "1"
    os.environ["EDWARD_PINGS"] = "off"            # the mention scenarios are the pings-off path
    os.environ["EDWARD_PROACTIVITY"] = "off"
    os.environ["EDWARD_OWNER_CHANNEL"] = "push"
    os.environ["EDWARD_SURPLUS"] = "off"
    return url


async def main(args: argparse.Namespace) -> int:
    url = _prepare_environment()
    from services import database, judgement_eval as je, skills_service
    from services.graph import initialize_graph
    from services.sandbox_env import ensure_test_cluster
    from services.timezone_utils import set_user_tz

    # The test cluster (:5433) is not a LaunchAgent: after a reboot nothing
    # but this brings it back.
    problem = ensure_test_cluster(url)
    if problem:
        print(f"judgement_eval: the test database is not reachable: {problem}", file=sys.stderr)
        return 1

    rec = je.Recorders()
    replaced = je.install_recorders(rec)
    print(f"(recording, never sending: {', '.join(replaced)})")
    je.install_web()
    set_user_tz(je.EVAL_TZ)
    await database.init_db()
    await initialize_graph(url)
    for skill in je.EVAL_SKILLS:
        try:
            await skills_service.set_skill_enabled(skill, True)
        except Exception as exc:  # noqa: BLE001
            print(f"(could not enable the {skill} skill in the test DB: {exc})")

    wanted = selected(args)
    started = datetime.now(timezone.utc)
    stamp = int(time.time())
    results = []
    for s in je.scenarios():
        if wanted is not None and s.name not in wanted:
            continue
        print(f"… {s.title}", flush=True)
        results.append(await je.run_scenario(s, rec, stamp=stamp))

    print()
    print(je.format_report(results))
    code = 1 if any(r.observed.error for r in results) else (2 if any(r.safety_failure for r in results) else 0)
    if args.json_out:
        try:
            from services import llm_utils

            provider = llm_utils.llm_provider()
        except Exception:  # noqa: BLE001
            provider = None
        doc = je.json_report(results, label=args.label, started_at=started.isoformat().replace("+00:00", "Z"),
                             finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                             exit_code=code, provider=provider)
        path = Path(os.path.expanduser(args.json_out))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, default=str))
        print(f"(wrote {path})")
    await database.engine.dispose()
    return code


def run(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    selected(args)  # an unknown name fails before anything is installed
    install_eval_guards()
    return asyncio.run(main(args))


if __name__ == "__main__":
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(1)
