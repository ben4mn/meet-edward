"""Hub health tile — turning the watchdog's output into something a
wall panel can show honestly.

`scripts/health_check.sh` writes ~/.edward-health/status.json on a 5-minute
LaunchAgent. The failure this module guards against: reading only the
`status` field, so a green file that nothing has refreshed for three days
still renders green. That is the 2026-08-12 shape — a dead dependency
sitting unnoticed while everything downstream looked fine. An un-refreshed
file degrades to "stale"; a missing or unreadable one to "unknown". Neither
is ever allowed to read as healthy.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# The shallow check runs every 5 minutes; 15 allows a couple of misses
# before we call the watchdog itself suspect.
WATCHDOG_STALE_AFTER_MINUTES = 15

# Worst-wins ordering. "unknown" outranks "repaired" deliberately: a clean
# self-repair is a solved problem, whereas not knowing is not.
SEVERITY: Dict[str, int] = {
    "healthy": 0,
    "repaired": 1,
    "stale": 2,
    "unknown": 2,
    "problem": 3,
}

_UTC = timezone.utc


def _parse_stamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def assess_watchdog(raw: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=_UTC)

    if not isinstance(raw, dict):
        return {
            "status": "unknown",
            "stale": True,
            "age_minutes": None,
            "checked_at": None,
            "problems": [],
            "repairs": [],
        }

    problems = list(raw.get("problems") or [])
    repairs = list(raw.get("repairs") or [])
    checked = _parse_stamp(raw.get("checked_at"))

    if checked is None:
        status, stale, age = "unknown", True, None
    else:
        age = int((now - checked).total_seconds() // 60)
        stale = age > WATCHDOG_STALE_AFTER_MINUTES
        if stale:
            # Whatever the file claims, nobody has confirmed it recently.
            status = "stale"
        elif problems:
            status = "problem"
        elif repairs:
            status = "repaired"
        else:
            status = "healthy" if raw.get("status") == "healthy" else "problem"

    return {
        "status": status,
        "stale": stale,
        "age_minutes": age,
        "checked_at": raw.get("checked_at"),
        "problems": problems,
        "repairs": repairs,
    }


def disk_status(free_gb: float, pct: float) -> str:
    """Healthy only if there is both room in bytes and room in proportion."""
    if free_gb < DISK_FREE_WARN_GB or pct >= DISK_PCT_PROBLEM:
        return "problem"
    return "healthy"


def backup_status(dir_present: bool, age_hours: Optional[float]) -> str:
    """Backups may live on a removable drive, so "gone" is a real state.

    An unplugged backup drive means the directory simply does not exist.
    That is a problem, not an unknown — no backups at all is the loudest
    thing this panel can have to say.
    """
    if not dir_present or age_hours is None:
        return "problem"
    return "healthy" if age_hours <= BACKUP_STALE_AFTER_HOURS else "problem"


def rollup(statuses: List[str]) -> str:
    if not statuses:
        return "unknown"
    return max(statuses, key=lambda s: SEVERITY.get(s, SEVERITY["unknown"]))


# ---------------------------------------------------------------------------
# Collection (I/O — the pure logic above is what the tests cover)
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

logger = logging.getLogger(__name__)

STATUS_FILE = Path.home() / ".edward-health" / "status.json"

# Edward's LaunchAgents under this host's prefix (``EDWARD_LAUNCHD_PREFIX``),
# plus a tunnel in front of him when ``EDWARD_TUNNEL_LABEL`` names one.
AGENT_SERVICES = ["backend", "frontend", "graph-worker", "healthcheck", "backup"]
# Run on a schedule, not KeepAlive: not running with a clean exit is fine.
PERIODIC_SERVICES = ("healthcheck", "backup")


def agent_labels() -> List[str]:
    """The LaunchAgent labels the tile watches, read from the environment."""
    from services.host_config import launchd_label, tunnel_label

    labels = [launchd_label(s) for s in AGENT_SERVICES]
    tunnel = tunnel_label()
    if tunnel:
        labels.insert(3, tunnel)
    return labels


def short_name(label: str) -> str:
    """``com.meet-edward.backend`` -> ``backend``; a tunnel keeps its last part."""
    from services.host_config import launchd_prefix

    prefix = launchd_prefix() + "."
    if label.startswith(prefix):
        return label[len(prefix):]
    return label.rsplit(".", 1)[-1]


def backup_dir() -> Path:
    """Where the nightly dumps land (``EDWARD_BACKUP_DIR``)."""
    from services.host_config import backup_dir as configured

    return configured()


DISK_FREE_WARN_GB = 15

# A byte floor alone lies on a big volume: 16 GB free sounds fine until you
# notice it is 93% of the disk, on the volume taking daily pg_dumps. Flag on
# either signal.
DISK_PCT_PROBLEM = 90.0

# How old the newest dump may be before the panel calls it stale.
BACKUP_STALE_AFTER_HOURS = 36


def _read_status_file() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return None


async def _launchd_agents() -> List[Dict[str, Any]]:
    """Parse `launchctl list`: columns are PID, last exit status, label."""
    labels = agent_labels()
    try:
        proc = await asyncio.create_subprocess_exec(
            "launchctl", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        return [{"label": lbl, "name": short_name(lbl), "status": "unknown", "pid": None, "exit": None}
                for lbl in labels]

    by_label: Dict[str, Dict[str, Any]] = {}
    for line in out.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid_s, exit_s, label = (p.strip() for p in parts)
        if label not in labels:
            continue
        pid = None if pid_s == "-" else int(pid_s) if pid_s.isdigit() else None
        try:
            last_exit = int(exit_s)
        except ValueError:
            last_exit = None

        if pid is not None:
            status = "healthy"
        elif last_exit not in (0, None):
            status = "problem"
        else:
            # Not running, clean exit — fine for the periodic ones
            # (healthcheck, backup), not for the KeepAlive services.
            status = "healthy" if label.endswith(PERIODIC_SERVICES) else "problem"

        by_label[label] = {
            "label": label, "name": short_name(label), "status": status, "pid": pid, "exit": last_exit,
        }

    return [
        by_label.get(lbl, {"label": lbl, "name": short_name(lbl), "status": "unknown", "pid": None, "exit": None})
        for lbl in labels
    ]


async def _db_probe() -> Dict[str, Any]:
    from sqlalchemy import text
    from services.database import async_session

    try:
        async with async_session() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=5)
        return {"label": "postgres", "status": "healthy", "detail": None}
    except Exception as exc:
        return {"label": "postgres", "status": "problem", "detail": str(exc)[:120]}


def _disk() -> Dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(Path.home()))
        free_gb = usage.free / 1024 ** 3
        pct = (usage.used / usage.total) * 100 if usage.total else 0.0
        return {
            "label": "disk",
            "status": disk_status(free_gb, pct),
            "detail": f"{free_gb:.0f} GB free ({pct:.0f}% used)",
            "pct": round(pct, 1),
        }
    except Exception:
        return {"label": "disk", "status": "unknown", "detail": None}


def _backup(now: datetime) -> Dict[str, Any]:
    """Newest dump on the backup volume, and whether that volume is there."""
    where = backup_dir()
    try:
        dumps = sorted(where.glob("edward-db-*.dump"))
    except Exception:
        dumps = []

    present = where.is_dir()
    if not present:
        return {
            "label": "backup",
            "status": backup_status(False, None),
            "detail": f"backup folder missing ({where})",
        }

    if not dumps:
        return {
            "label": "backup",
            "status": backup_status(True, None),
            "detail": "no dumps found",
        }

    newest = max(dumps, key=lambda p: p.stat().st_mtime)
    age_h = (now.timestamp() - newest.stat().st_mtime) / 3600
    return {
        "label": "backup",
        "status": backup_status(True, age_h),
        "detail": f"{age_h:.0f}h ago · {len(dumps)} dumps",
    }


async def collect() -> Dict[str, Any]:
    """Everything the health tile renders. Never raises — a panel that
    crashes tells you less than one showing 'unknown'."""
    now = datetime.now(_UTC)

    watchdog = assess_watchdog(_read_status_file(), now)
    agents, db = await asyncio.gather(_launchd_agents(), _db_probe())
    components = [db, _disk(), _backup(now)]

    overall = rollup(
        [watchdog["status"]]
        + [a["status"] for a in agents]
        + [c["status"] for c in components]
    )

    from services.host_config import launchd_prefix

    return {
        "overall": overall,
        "watchdog": watchdog,
        "agents": agents,
        "label_prefix": launchd_prefix(),
        "components": components,
        "host": os.uname().nodename,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
    }
