"""Guard: tests must never resolve the live Kuzu database path.

Kuzu is single-writer. The graph-worker LaunchAgent owns
``backend/storage/graphiti.kuzu`` exclusively; a pytest run that opens
the same file (directly or via ``_db_path()``) can corrupt it — crash
bursts in the worker correlated with pytest runs before this guard.

conftest.py must point GRAPHITI_DB_PATH at a per-run temp location so
any code path that resolves the DB path during tests stays off the
live file.
"""

import os
from pathlib import Path


def test_graphiti_db_path_is_set_and_not_live():
    db_path = os.environ.get("GRAPHITI_DB_PATH")
    assert db_path, "conftest.py must set GRAPHITI_DB_PATH for test runs"

    live = (Path(__file__).resolve().parent.parent / "storage").resolve()
    resolved = Path(db_path).resolve()
    assert not str(resolved).startswith(str(live)), (
        f"GRAPHITI_DB_PATH ({resolved}) points inside the live storage dir "
        f"({live}) — tests must not open the production Kuzu file"
    )


def test_worker_db_path_resolves_to_test_path():
    from workers.graph_worker import _db_path

    assert _db_path() == os.environ["GRAPHITI_DB_PATH"]
