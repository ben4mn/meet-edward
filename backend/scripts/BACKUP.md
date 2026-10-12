# Backing up Edward's memory

Edward's long-term memory lives in one local PostgreSQL database (`edward`). It holds the memories with their pgvector embeddings, their connections, documents, conversations and checkpoints, the ledgers, and the `edward_db_*` schemas of the persistent-database feature. Nothing replicates it. These backups are the safety net against a bad migration, an errant `DELETE` or a failing disk.

## What runs every night

`scripts/install-launchagents.sh` installs the `<prefix>.backup` LaunchAgent (default label `com.meet-edward.backup`; the prefix is `EDWARD_LAUNCHD_PREFIX`) unless you pass `--without-backup`. It runs at **03:30**, or at the next wake if the Mac was asleep, and calls [`backup_memories.sh`](backup_memories.sh) through [`run_backup_with_fda.py`](run_backup_with_fda.py) under the python that may hold Full Disk Access ([below](#a-removable-drive)).

Each run:

1. Dumps the whole database with `pg_dump -Fc` (compressed, and restorable selectively), using postgresql@16's own `pg_dump`.
2. **Verifies** the dump: `pg_restore -l` must read it and list the `memories` table data. A dump that fails is deleted and older backups are kept. Nothing rotates on a failure.
3. Stamps the verified dump in `~/.edward-health/backup.ok` (`EDWARD_HEALTH_STATE`). The health check's daily deep run reads the stamp to report a stale backup.
4. Garbage-collects superseded LangGraph checkpoints (`gc_checkpoints.py --apply --vacuum`). This happens only after the dump verified, so tonight's backup can undo it, and a failure here is only a warning.
5. Copies the Kuzu knowledge-graph file, best-effort. The copy may be slightly stale while the worker holds the file open, and the graph can be rebuilt from Postgres.
6. Rotates: it keeps the newest **14** dumps (`EDWARD_BACKUP_KEEP`) and **3** graph copies (`EDWARD_BACKUP_KEEP_KUZU`). Graph copies can run to gigabytes, which is why fewer are kept.

The log goes to `/tmp/edward-backup.log`, and to `backup.log` in the backup folder.

Everything it writes is readable by your macOS user alone: the script runs under `umask 077`, sets the backup folder to 700 and `backup.log`, every dump and every graph copy to 600, and tightens older ones on the next run. A dump is your whole database (memories, conversations, tokens), and under the default umask it would be readable by every account on the Mac. On a drive without Unix permissions (exFAT) the chmod may fail; that is a warning, and the backup still runs.

## Settings

The scripts read the environment first, then the checkout's `.env` (launchd doesn't load it for them), then the default.

| Setting | Default | What it is |
|---|---|---|
| `EDWARD_BACKUP_DIR` | `~/edward-backups` | Where the dumps go. Keep it outside the git checkout: dumps are private and never committed. |
| `EDWARD_BACKUP_USER` | `$PGUSER`, else your macOS user | The role `pg_dump` connects as. With Homebrew's Postgres your macOS user is the superuser, which can also read the `edward_db_*` schemas it owns (`pg_dump -U edward` cannot lock them). |
| `EDWARD_DB_NAME` | `edward` | The database |
| `EDWARD_BACKUP_KEEP` / `EDWARD_BACKUP_KEEP_KUZU` | `14` / `3` | How many to keep |
| `EDWARD_FDA_PYTHON` | The real path of the python3.12 behind `backend/.venv` | The interpreter the agent runs the backup under (see below) |

## A removable drive

The default folder is on the Mac's own disk, which protects against damage to the data but not against losing the disk. To back up to an external drive, set `EDWARD_BACKUP_DIR=/Volumes/<drive>/edward-backups`.

- While the drive is **not mounted**, the backup refuses to run. Otherwise `mkdir -p` would quietly create `/Volumes/<drive>/…` on the internal disk, and those backups would vanish when the real drive was mounted over them.
- macOS gates removable volumes per process. A LaunchAgent has no grant even when your terminal does, and the run would die with a bare "Operation not permitted". So the agent runs the backup under one python binary, and the backup first checks that it can write, and says how to fix it if not. Grant **Full Disk Access** to that python. `install-launchagents.sh` prints its path at the end, and [ADAPTING.md](../../docs/ADAPTING.md#3-macos-permissions) explains how to grant it.
- After `brew upgrade python@3.12` the python's path changes and the grant no longer applies. Grant the new path and re-run `bash scripts/install-launchagents.sh`.

## Running a backup by hand

From the checkout:

```bash
bash backend/scripts/backup_memories.sh
```

## Restoring

[`restore_memories.sh`](restore_memories.sh) defaults to a **safe dry run**. It restores into a throwaway `edward_restore_test` database, prints row counts, and leaves the live database alone:

```bash
bash backend/scripts/restore_memories.sh                 # newest dump -> verification DB
bash backend/scripts/restore_memories.sh <dump-file>     # a specific dump -> verification DB
```

It reads the same `EDWARD_BACKUP_DIR` the backup writes. `PGHOST` and `PGPORT` pick the Postgres server, so to try a restore without touching the main server, use the test cluster (`scripts/test-pg-cluster.sh ensure` first):

```bash
PGHOST=localhost PGPORT=5433 bash backend/scripts/restore_memories.sh <dump-file>
```

To actually **replace** the live database:

```bash
bash backend/scripts/restore_memories.sh <dump-file> --force-live
```

It asks you to type `REPLACE`, snapshots the current live database to `pre-restore-snapshot-<time>.dump` in the backup folder, and waits while you stop what holds the database open (`launchctl bootout gui/$UID/<prefix>.backend`, and the graph worker if you run it). Then it restores. Its closing line suggests `launchctl kickstart`, but an agent you booted out is no longer loaded, so start it again with `bash scripts/install-launchagents.sh` (it loads any of its agents that is not loaded) or, without agents, `./restart.sh backend`.

## Checking a backup by hand

```bash
pg_restore -l <dump> | grep 'TABLE DATA public memories'    # is the data there?
```

For full proof, restore it into the verification database (the dry run above) and compare the counts.

## Caveats

- **These backups are local.** They protect against logical damage and a failing database, not against losing the machine (or the drive). For an offsite copy, periodically copy the backup folder to storage you trust. The dumps hold your memories, so never push them to git.
- **Postgres is the source of truth.** The graph copy is a convenience, and the graph can be rebuilt from Postgres.
- **Size.** Dumps grow with the install: from megabytes on a new one to hundreds of megabytes on a long-lived one, and with the graph on, 14 dumps plus 3 graph copies can reach tens of GB. If the internal disk can't spare that, point `EDWARD_BACKUP_DIR` at a bigger disk.
