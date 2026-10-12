#!/usr/bin/env bash
# The backend suite's own Postgres cluster: :5433, fsync off, loopback only.
#
# Why: edward_test used to live in the production cluster (:5432). A full
# backend run (conftest.clean_db TRUNCATEs ~60 tables before each of ~4,800
# tests) made production's checkpointer fsync 100K–700K relation files;
# commits stalled and the live backend's pool (10 + 5) ran out — 20
# QueuePool tracebacks on 2026-09-26 21:31–21:40, the same stall on 9/5,
# 9/7, 9/10, 9/11, 9/12 and 9/25. A second cluster shares no checkpointer,
# no WAL and no fsync with production, and with fsync off it never syncs.
# Nothing in it is worth keeping: a crash may lose it, `init` rebuilds it.
#
# Usage: scripts/test-pg-cluster.sh ensure|init|start|stop|status|url [db]|createdb <name_test>
#   ensure    initialise if missing, start if down, create edward_test if missing (idempotent)
#   url [db]  the SQLAlchemy URL for edward_test (or <db>) on the test cluster
#   createdb  a separate *_test database (vector included) for a parallel run
# Env: EDWARD_TEST_PGDATA (default ~/.edward-test-pg), EDWARD_TEST_PG_BIN.
# Sourcing with TEST_PG_LIB_ONLY=1 loads the functions without running a command.

TEST_PG_PORT=5433
# Homebrew's prefix: /opt/homebrew on Apple silicon, /usr/local on Intel.
_TEST_PG_BREW="${HOMEBREW_PREFIX:-}"
if [ -z "$_TEST_PG_BREW" ]; then
  if [ -d /opt/homebrew ]; then _TEST_PG_BREW=/opt/homebrew; else _TEST_PG_BREW=/usr/local; fi
fi
# The main (production) cluster `brew services start postgresql@16` runs.
PRODUCTION_PGDATA="${EDWARD_PGDATA:-$_TEST_PG_BREW/var/postgresql@16}"
TEST_PGDATA="${EDWARD_TEST_PGDATA:-$HOME/.edward-test-pg}"
# The same binaries as production, so the pgvector setup.sh builds from
# source for pg16 is available here too.
TEST_PG_BIN="${EDWARD_TEST_PG_BIN:-$_TEST_PG_BREW/opt/postgresql@16/bin}"
TEST_PG_LOG="${EDWARD_TEST_PG_LOG:-/tmp/edward-test-pg.log}"

test_pg_conf() {
  cat <<CONF
# edward test cluster — throwaway data, never production (scripts/test-pg-cluster.sh)
port = $TEST_PG_PORT
listen_addresses = 'localhost'
unix_socket_directories = '/tmp'
fsync = off
synchronous_commit = off
full_page_writes = off
max_connections = 300
shared_buffers = 128MB
max_wal_size = 2GB
checkpoint_timeout = 30min
autovacuum = on
log_min_duration_statement = -1
CONF
}

test_pg_url() {
  local db="${1:-edward_test}"
  echo "postgresql+asyncpg://edward:edward@localhost:$TEST_PG_PORT/$db"
}

refuse_production_datadir() {
  local dir="${1%/}"
  if [ "$dir" = "${PRODUCTION_PGDATA%/}" ]; then
    echo "test-pg-cluster: refusing the production data directory $dir" >&2
    return 1
  fi
  return 0
}

valid_test_db_name() {
  case "$1" in
    *[!a-z0-9_]*|'') echo "test-pg-cluster: '$1' is not a plain lower-case name" >&2; return 1;;
    *_test) return 0;;
    *) echo "test-pg-cluster: '$1' does not end in _test" >&2; return 1;;
  esac
}

_pg() { "$TEST_PG_BIN/$1" "${@:2}"; }

test_pg_running() {
  _pg pg_isready -q -h localhost -p "$TEST_PG_PORT" >/dev/null 2>&1
}

_superuser_psql() {
  # The OS user owns the cluster (initdb's default superuser).
  _pg psql -X -q -h localhost -p "$TEST_PG_PORT" -d postgres -v ON_ERROR_STOP=1 "$@"
}

test_pg_init() {
  refuse_production_datadir "$TEST_PGDATA" || return 1
  if [ -f "$TEST_PGDATA/PG_VERSION" ]; then
    return 0
  fi
  mkdir -p "$TEST_PGDATA" || return 1
  _pg initdb -D "$TEST_PGDATA" --auth=trust --encoding=UTF8 --locale=C -U "$(id -un)" >/dev/null || return 1
  test_pg_conf >> "$TEST_PGDATA/postgresql.conf"
}

test_pg_start() {
  refuse_production_datadir "$TEST_PGDATA" || return 1
  if test_pg_running; then return 0; fi
  # A crash leaves postmaster.pid behind; pg_ctl copes with a dead pid.
  # LC_ALL: without a valid locale macOS's postmaster goes multithreaded
  # during startup and refuses to run ("postmaster became multithreaded").
  LC_ALL=C _pg pg_ctl -D "$TEST_PGDATA" -l "$TEST_PG_LOG" -o "-p $TEST_PG_PORT" -w -t 60 start >/dev/null || return 1
}

test_pg_stop() {
  refuse_production_datadir "$TEST_PGDATA" || return 1
  test_pg_running || return 0
  _pg pg_ctl -D "$TEST_PGDATA" -m fast -w stop >/dev/null
}

test_pg_createdb() {
  local db="$1"
  valid_test_db_name "$db" || return 1
  if [ "$(_superuser_psql -Atc "select 1 from pg_database where datname='$db'")" = "1" ]; then
    return 0
  fi
  _superuser_psql -c "create database $db owner edward template template1" >/dev/null
}

test_pg_bootstrap() {
  # The app's role, and pgvector in template1 so every new *_test database
  # has it without a superuser (init_db's CREATE EXTENSION IF NOT EXISTS is
  # then a no-op for the unprivileged `edward` role).
  _superuser_psql <<'SQL' >/dev/null
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'edward') then
    create role edward login password 'edward' createdb;
  end if;
end $$;
SQL
  _pg psql -X -q -h localhost -p "$TEST_PG_PORT" -d template1 -v ON_ERROR_STOP=1 \
    -c "create extension if not exists vector" >/dev/null || return 1
  test_pg_createdb edward_test
}

test_pg_ensure() {
  test_pg_init && test_pg_start && test_pg_bootstrap
}

test_pg_main() {
  case "${1:-}" in
    ensure) test_pg_ensure ;;
    init) test_pg_init && test_pg_start && test_pg_bootstrap ;;
    start) test_pg_start ;;
    stop) test_pg_stop ;;
    status)
      if test_pg_running; then echo "test cluster up on :$TEST_PG_PORT ($TEST_PGDATA)"; else echo "test cluster down ($TEST_PGDATA)"; return 3; fi ;;
    url) test_pg_url "${2:-edward_test}" ;;
    createdb) test_pg_ensure && test_pg_createdb "${2:-}" && test_pg_url "$2" ;;
    *) echo "usage: $0 ensure|init|start|stop|status|url [db]|createdb <name_test>" >&2; return 64 ;;
  esac
}

if [ -z "${TEST_PG_LIB_ONLY:-}" ]; then
  set -uo pipefail
  test_pg_main "$@"
  exit $?
fi
