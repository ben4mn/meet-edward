#!/bin/bash
# Tests for test-pg-cluster.sh and with-test-db-lock.sh — the pieces that
# keep the backend suite off the production Postgres cluster.
#
# Why: a full backend run on :5432 stalled production's checkpointer and
# exhausted the live pool (2026-09-26 21:31–21:40 and six earlier days).
# edward_test now lives on its own cluster on :5433 with fsync off.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }
contains() { case "$2" in *"$3"*) ok "$1";; *) nope "$1 (no '$3' in output)";; esac; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Source the script under test without running a command.
TEST_PG_LIB_ONLY=1 . "$HERE/test-pg-cluster.sh"

echo "the test cluster's settings:"
check "port is 5433" "$TEST_PG_PORT" "5433"
CONF="$(test_pg_conf)"
contains "fsync off" "$CONF" "fsync = off"
contains "synchronous_commit off" "$CONF" "synchronous_commit = off"
contains "full_page_writes off" "$CONF" "full_page_writes = off"
contains "listens on 5433" "$CONF" "port = 5433"
contains "loopback only" "$CONF" "listen_addresses = 'localhost'"

echo "test_pg_url:"
check "default database" "$(test_pg_url)" "postgresql+asyncpg://edward:edward@localhost:5433/edward_test"
check "named database" "$(test_pg_url edward_batch_a_test)" "postgresql+asyncpg://edward:edward@localhost:5433/edward_batch_a_test"

echo "refuse_production_datadir:"
refuse_production_datadir "$PRODUCTION_PGDATA" 2>/dev/null; check "production data dir refused" "$?" "1"
refuse_production_datadir "$PRODUCTION_PGDATA/" 2>/dev/null; check "production data dir with slash refused" "$?" "1"
check "production data dir is Homebrew's postgresql@16" "${PRODUCTION_PGDATA##*/var/}" "postgresql@16"
refuse_production_datadir "$TMP/pg" 2>/dev/null; check "a scratch dir is fine" "$?" "0"

echo "valid_test_db_name:"
valid_test_db_name "edward_test"; check "edward_test ok" "$?" "0"
valid_test_db_name "edward_batch_a_test"; check "a batch db ok" "$?" "0"
valid_test_db_name "edward" 2>/dev/null; check "the live name refused" "$?" "1"
valid_test_db_name "x_test; drop" 2>/dev/null; check "odd characters refused" "$?" "1"

echo "with-test-db-lock.sh:"
# The lock script must refuse a run pointed at the production cluster.
OUT="$(TEST_PG_SKIP_ENSURE=1 DATABASE_URL='postgresql+asyncpg://edward:edward@localhost:5432/edward_test' \
  "$HERE/with-test-db-lock.sh" true 2>&1)"; RC=$?
check "a :5432 test url is refused" "$RC" "78"
contains "the refusal names the test cluster" "$OUT" "5433"
OUT="$(TEST_PG_SKIP_ENSURE=1 TEST_DATABASE_URL='postgresql://edward:edward@localhost/edward_test' \
  "$HERE/with-test-db-lock.sh" true 2>&1)"; RC=$?
check "a portless (=5432) test url is refused" "$RC" "78"
# The live URL is dropped from the child's environment, and the child is
# pointed at the test cluster.
OUT="$(TEST_PG_SKIP_ENSURE=1 DATABASE_URL='postgresql+asyncpg://edward:edward@localhost:5432/edward' \
  "$HERE/with-test-db-lock.sh" sh -c 'echo "DB=${DATABASE_URL:-unset} TEST=$TEST_DATABASE_URL"' 2>&1)"; RC=$?
check "a live url in the environment still runs" "$RC" "0"
contains "the live url is dropped" "$OUT" "DB=unset"
contains "the child gets the test cluster" "$OUT" "TEST=postgresql+asyncpg://edward:edward@localhost:5433/edward_test"
# Different databases take different locks, so parallel runs on their own
# databases never wait on each other.
check "lock per database" "$(TEST_PG_SKIP_ENSURE=1 TEST_DATABASE_URL="$(test_pg_url edward_batch_a_test)" \
  "$HERE/with-test-db-lock.sh" sh -c 'echo "$EDWARD_TEST_DB_LOCK"')" "/tmp/edward-test-db.edward_batch_a_test.lock"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
