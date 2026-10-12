"""The judgement check starts the test cluster it runs on (pre-deploy
review, 2026-09-27).

``scripts/judgement_eval.py`` — the deploy gate before the mind runs
unattended — takes its database from ``sandbox_env.safe_test_database_url``,
whose default moved to ``edward_test`` on the :5433 test cluster. Only
``with-test-db-lock.sh`` and a hand-run ``test-pg-cluster.sh ensure`` start
that cluster, and nothing brings it back after a reboot, so after one the
documented gate failed at ``init_db`` with a refused connection before any
scenario ran — a failure unrelated to the code. It now ensures the cluster
(and a separate ``*_test`` database, when it names one) before it connects.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services import sandbox_env

pytestmark = pytest.mark.unit


class _Runner:
    def __init__(self, returncode=0, raises=None):
        self.calls = []
        self.returncode, self.raises = returncode, raises

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, "", "pg_ctl: could not start server")


def test_a_url_on_the_test_cluster_ensures_it():
    run = _Runner()
    assert sandbox_env.ensure_test_cluster(sandbox_env.DEFAULT_TEST_DATABASE_URL, runner=run) is None
    assert run.calls == [[sandbox_env.TEST_CLUSTER_SCRIPT, "ensure"]]


def test_a_separate_test_database_is_created_too():
    run = _Runner()
    url = f"postgresql+asyncpg://edward:edward@localhost:{sandbox_env.TEST_PG_PORT}/edward_ffix_test"
    assert sandbox_env.ensure_test_cluster(url, runner=run) is None
    assert run.calls == [[sandbox_env.TEST_CLUSTER_SCRIPT, "ensure"],
                         [sandbox_env.TEST_CLUSTER_SCRIPT, "createdb", "edward_ffix_test"]]


def test_a_url_elsewhere_starts_nothing():
    run = _Runner()
    assert sandbox_env.ensure_test_cluster("postgresql+asyncpg://e:e@db.example:6543/edward_test", runner=run) is None
    assert run.calls == []


@pytest.mark.parametrize("run", [_Runner(returncode=1), _Runner(raises=FileNotFoundError("no bash"))])
def test_a_cluster_that_will_not_start_says_so(run):
    problem = sandbox_env.ensure_test_cluster(sandbox_env.DEFAULT_TEST_DATABASE_URL, runner=run)
    assert problem and "test-pg-cluster.sh" in problem


def test_the_cluster_script_is_where_it_is_looked_for():
    script = Path(sandbox_env.TEST_CLUSTER_SCRIPT)
    assert script.name == "test-pg-cluster.sh" and script.is_file()
    assert os.access(script, os.X_OK)


def test_judgement_eval_ensures_the_cluster_before_it_connects():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "judgement_eval.py").read_text()
    main = source[source.index("async def main("):]
    assert "ensure_test_cluster(" in main
    assert main.index("ensure_test_cluster(") < main.index("database.init_db()")
