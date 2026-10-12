#!/usr/bin/env bash
# Run a backend test command on the test Postgres cluster, one run per database.
# Usage: scripts/with-test-db-lock.sh <command...>
#
# - The test database lives on its own cluster on :5433 (scripts/test-pg-cluster.sh);
#   this starts it if it is down. A URL on the production cluster (:5432, or no
#   port) is refused with exit 78: a full run there stalled production's
#   checkpointer and exhausted the live pool (2026-09-26 21:31–21:40).
# - The live DATABASE_URL never reaches the child: it is dropped, and the child
#   gets TEST_DATABASE_URL (default edward_test on :5433; export your own
#   `…:5433/<name>_test` to run in parallel on a separate database — see
#   `scripts/test-pg-cluster.sh createdb`).
# - Several runs on the *same* database would truncate each other's rows through
#   conftest.clean_db, so each database has its own mkdir lock (waits up to
#   30 min, breaks a lock older than 20 min, exit 75 on giving up).
HERE="$(cd "$(dirname "$0")" && pwd)"
TEST_PG_LIB_ONLY=1 . "$HERE/test-pg-cluster.sh"

_url_port() {
  local rest="${1#*://}"
  local hostport="${rest%%/*}"
  hostport="${hostport##*@}"
  case "$hostport" in
    *:*) echo "${hostport##*:}" ;;
    *) echo 5432 ;;
  esac
}
_url_db() {
  local db="${1##*/}"
  echo "${db%%\?*}"
}

URL="${TEST_DATABASE_URL:-}"
if [ -z "$URL" ]; then
  case "$(_url_db "${DATABASE_URL:-}")" in
    *_test) URL="$DATABASE_URL" ;;
  esac
fi
URL="${URL:-$(test_pg_url)}"

if [ "$(_url_port "$URL")" != "$TEST_PG_PORT" ]; then
  echo "with-test-db-lock: refusing $URL — tests run on the test cluster (:$TEST_PG_PORT), never :5432." >&2
  echo "  start it with: scripts/test-pg-cluster.sh ensure" >&2
  exit 78
fi
DB="$(_url_db "$URL")"
valid_test_db_name "$DB" || exit 78

if [ -z "${TEST_PG_SKIP_ENSURE:-}" ]; then
  test_pg_ensure || { echo "with-test-db-lock: could not start the test cluster (log: $TEST_PG_LOG)" >&2; exit 69; }
  test_pg_createdb "$DB" || exit 69
fi

unset DATABASE_URL
export TEST_DATABASE_URL="$URL"
export EDWARD_TEST_DB_LOCK="/tmp/edward-test-db.$DB.lock"
LOCK="$EDWARD_TEST_DB_LOCK"
for _ in $(seq 1 1800); do
  if mkdir "$LOCK" 2>/dev/null; then
    trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM
    "$@"
    exit $?
  fi
  # a lock older than 20 minutes is a dead run's
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then rmdir "$LOCK" 2>/dev/null; fi
  sleep 1
done
echo "with-test-db-lock: gave up waiting for $LOCK" >&2
exit 75
