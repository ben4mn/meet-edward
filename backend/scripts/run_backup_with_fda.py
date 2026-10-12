"""launchd entry point for the nightly backup.

Why this exists: macOS gates removable volumes (and other protected places)
per *responsible process*. The nightly job used to be
``/bin/bash backup_memories.sh``, and /bin/bash never held Full Disk Access,
so every scheduled run onto a removable drive died on "mounted but not
writable" while manual runs from a terminal (which did hold FDA) succeeded.
The Homebrew python3.12 that the backend runs under can hold FDA (it is how
the iMessage listener reads chat.db), and a launchd job's children inherit
its TCC identity — so run the same shell script with that python as the
responsible process. ``scripts/install-launchagents.sh`` schedules it this
way, with the python's resolved path in the agent. A backup dir on the
internal disk (the default, ~/edward-backups) needs no Full Disk Access.
"""
# ADAPT: Full Disk Access is granted to one python binary, by its path.
#   How it works here: the backup agent runs this file with the Homebrew python3.12
#   the backend's venv is built on (its real .../Cellar/python@3.12/<version>/... path).
#   To adapt: grant that path Full Disk Access (install-launchagents.sh prints it);
#   after `brew upgrade python@3.12` grant the new path and re-run the installer.
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "backup_memories.sh")

if __name__ == "__main__":
    sys.exit(subprocess.call(["/bin/bash", SCRIPT] + sys.argv[1:]))
