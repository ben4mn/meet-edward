#!/bin/bash
# First-time setup for Edward on a Mac. Safe to run again: every step checks
# what is already there and only does what is missing.
#
#   ./setup.sh                    # install everything, ask for the few settings
#   ./setup.sh --with-test-db     # also the backend tests' own Postgres on :5433
#   ./setup.sh --launchagents     # also install the LaunchAgents at the end
#   ./setup.sh --no-helpers       # skip building the Swift helpers
#   ./setup.sh --non-interactive  # never prompt; take EDWARD_OWNER_NAME,
#                                 # ANTHROPIC_API_KEY and TIMEZONE from the environment
#
# What it does:
#   1. Checks for macOS on Apple silicon (an Intel Mac, a Rosetta shell or an
#      x86_64 python stops it before anything is installed: the pinned torch
#      has no x86_64 macOS wheel), the Xcode command-line tools and Homebrew.
#   2. brew install python@3.12 postgresql@16 node, and starts Postgres 16.
#   3. Builds pgvector (v0.8.2) from source against Postgres 16 -- Homebrew's
#      pgvector bottle no longer ships files for postgresql@16.
#   4. Creates the role and database edward/edward (password edward) with the
#      vector extension.
#   5. Makes backend/.venv with python3.12 and installs requirements.txt with
#      the pins in constraints.txt; installs Playwright's headless shell
#      (Edward's own invisible browser).
#   6. npm ci in frontend/.
#   7. Creates .env from .env.example if there is none and asks for your
#      name, your Anthropic API key and your time zone.
#   8. Optionally: the test cluster, the Swift media and EventKit helpers,
#      the LaunchAgents.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$REPO/scripts/lib/edward-env.sh"

PGVECTOR_VERSION="v0.8.2"
WITH_TEST_DB=false
WITH_HELPERS=true
WITH_AGENTS=false
INTERACTIVE=true
[ -t 0 ] || INTERACTIVE=false

[ -n "${SETUP_LIB_ONLY:-}" ] && set --   # sourced by the tests: no options
for arg in "$@"; do
    case "$arg" in
        --with-test-db) WITH_TEST_DB=true ;;
        --no-helpers) WITH_HELPERS=false ;;
        --launchagents) WITH_AGENTS=true ;;
        --non-interactive|--yes|-y) INTERACTIVE=false ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
        *) echo "Unknown option: $arg (see ./setup.sh --help)" >&2; exit 64 ;;
    esac
done

BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'
step() { echo; echo "${BOLD}==> $*${NC}"; }
ok()   { echo "    ${GREEN}ok${NC}  $*"; }
note() { echo "    ${YELLOW}!!${NC}  $*"; }
die()  { echo; echo "${RED}setup: $*${NC}" >&2; exit 1; }

WARNINGS=()
warn_later() { WARNINGS+=("$1"); note "$1"; }

ENV_FILE="${EDWARD_SETUP_ENV_FILE:-$REPO/.env}"

# env_quote VALUE -> VALUE as .env text that bash `source`, python-dotenv
# and scripts/lib/edward-env.sh all read back unchanged.
env_quote() {
    local v="$1"
    case "$v" in
        *[!A-Za-z0-9_./:@+-]*|"") ;;
        *) printf '%s' "$v"; return 0 ;;
    esac
    case "$v" in
        *"'"*)
            case "$v" in
                *'"'*|*'$'*|*'\'*|*'`'*) return 1 ;;
            esac
            printf '"%s"' "$v" ;;
        *) printf "'%s'" "$v" ;;
    esac
}

# set_env KEY VALUE -> KEY=VALUE in .env: replaces the first `KEY=` line
# (or a commented `# KEY=` one), else appends. Never echoes the value.
set_env() {
    local key="$1" quoted tmp
    quoted="$(env_quote "$2")" || { warn_later "$key has characters .env cannot hold safely; set it in .env by hand"; return 0; }
    tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    KEY="$key" LINE="$key=$quoted" awk '
        BEGIN { k = ENVIRON["KEY"]; line = ENVIRON["LINE"]; done = 0 }
        !done && ($0 ~ "^[[:space:]]*(export[[:space:]]+)?" k "=" || $0 ~ "^#[[:space:]]*" k "=") { print line; done = 1; next }
        { print }
        END { if (!done) print line }
    ' "$ENV_FILE" > "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$ENV_FILE"
}

current() { edward_dotenv_get "$1" "$ENV_FILE" 2>/dev/null || true; }

# Apple silicon only. torch (backend/constraints.txt, through
# sentence-transformers) publishes no macOS x86_64 wheel and no sdist, so
# pip cannot install the backend on an Intel Mac -- or under Rosetta, where
# this shell, Homebrew (/usr/local) and its python are all x86_64.

# arch_problem MACHINE HW_ARM64 TRANSLATED -> why this Mac cannot run Edward,
# or nothing when it can. MACHINE is `uname -m`; HW_ARM64 is `sysctl -n
# hw.optional.arm64` (1 on Apple silicon, under Rosetta too; empty on
# Intel); TRANSLATED is `sysctl -n sysctl.proc_translated` (1 when this
# shell runs under Rosetta).
arch_problem() {
    local machine="$1" hw_arm64="${2:-}" translated="${3:-}"
    if [ "$machine" = "arm64" ] && [ "$translated" != "1" ]; then
        return 0
    fi
    if [ "$hw_arm64" = "1" ] || [ "$translated" = "1" ]; then
        printf '%s\n' "this shell runs under Rosetta (x86_64) on an Apple-silicon Mac, and the backend's pinned
  PyTorch (backend/constraints.txt) has no x86_64 macOS wheel. Run ./setup.sh from a native
  arm64 terminal (check with: uname -m), with the native Homebrew in /opt/homebrew:
    arch -arm64 /bin/bash ./setup.sh"
        return 0
    fi
    printf '%s\n' "Edward needs a Mac with Apple silicon; this one is Intel ($machine). The backend's pinned
  PyTorch (backend/constraints.txt, through sentence-transformers) publishes no macOS x86_64
  wheel, so its dependencies cannot be installed here. Nothing was installed."
}

# python_arch_problem PYTHON -> why PYTHON cannot run the backend (an x86_64
# build, which runs under Rosetta on Apple silicon), or nothing when it can
# or does not exist.
python_arch_problem() {
    local py="$1" machine
    [ -x "$py" ] || return 0
    machine="$("$py" -c 'import platform; print(platform.machine())' 2>/dev/null || true)"
    case "$machine" in
        arm64|"") return 0 ;;
    esac
    printf '%s\n' "$py is an $machine python (it runs under Rosetta), and the backend's pinned PyTorch
  (backend/constraints.txt) has no $machine macOS wheel. Use an arm64 python@3.12 from the
  native Homebrew in /opt/homebrew (move an x86_64 backend/.venv aside first:
  mv backend/.venv backend/.venv.x86_64), then run ./setup.sh again."
}

# check_python_arch PYTHON -> stops setup when PYTHON is an x86_64 build.
check_python_arch() {
    local why
    why="$(python_arch_problem "$1")"
    [ -z "$why" ] || die "$why"
    return 0
}

# print_next_steps WITH_AGENTS PREFIX FDA_PY -> the closing "Next steps".
# Until the first password is set (or while the backend has not answered
# yet) step 2 also says where its one-time setup code is
# (scripts/lib/edward-env.sh: the same line restart.sh and
# install-launchagents.sh print).
print_next_steps() {
    local with_agents="$1" prefix="$2" fda_py="$3" start_edward setup_note
    setup_note="$(edward_setup_code_note)"
    if [ -n "$setup_note" ]; then
        setup_note="
     $setup_note"
    fi
    if $with_agents; then
        start_edward="  2. launchd is starting Edward ($prefix.*). The first start takes a few minutes
     (the embedding model, the first frontend build): tail -f /tmp/edward-backend.log
     Restart him with ./restart.sh."
    else
        start_edward="  2. Start Edward, one way or the other (the first start takes a few minutes):
       ./restart.sh
     by hand, now; or, to keep him running at login under launchd ($prefix.*):
       bash scripts/install-launchagents.sh
     (it stops an Edward this checkout started by hand first)."
    fi
    cat <<EOF

Next steps:
  1. Look through .env: every setting is described in .env.example
     (integrations, voice, push, a public URL, quiet hours …).
$start_edward
     Then open http://localhost:3000 and choose a password.$setup_note
  3. Check your time zone in the app (http://localhost:3000/settings/locale):
     reminders, quiet hours and "today" all follow it.
  4. macOS permissions (System Settings > Privacy & Security), for what you use:
       - Full Disk Access for $fda_py
         (iMessage and Mail are read through it; backups to a removable drive need it too)
       - Automation: macOS asks the first time Edward sends a message or drives Chrome
       - Calendars and Reminders: bash backend/scripts/eventkit_request_access.sh
  5. Optional: ./setup.sh --with-test-db, then the backend tests:
       cd backend && ../scripts/with-test-db-lock.sh .venv/bin/python -m pytest tests/test_<name>.py -q
EOF
}

# Sourced by scripts/test_setup.sh: the helpers above, nothing else.
if [ -n "${SETUP_LIB_ONLY:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

# ── 1. The machine ────────────────────────────────────────────────────
step "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || die "Edward runs on macOS only (it reads iMessage, Calendar and Mail through macOS)."
# Before anything is installed: on an Intel Mac, or under Rosetta, pip would
# fail at step 5 after the Homebrew packages and the database were set up.
ARCH_WHY="$(arch_problem "$(uname -m)" "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" \
    "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)")"
[ -z "$ARCH_WHY" ] || die "$ARCH_WHY"
ok "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') on $(uname -m)"

if ! xcode-select -p >/dev/null 2>&1; then
    die "the Xcode command-line tools are missing (they compile pgvector).
  Run:  xcode-select --install
  then run ./setup.sh again."
fi
ok "Xcode command-line tools at $(xcode-select -p)"

if ! command -v brew >/dev/null 2>&1; then
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
    done
fi
command -v brew >/dev/null 2>&1 || die "Homebrew is missing. Install it from https://brew.sh, then run ./setup.sh again."
BREW_PREFIX="$(brew --prefix)"
export HOMEBREW_PREFIX="$BREW_PREFIX"
ok "Homebrew at $BREW_PREFIX"

# The pythons the backend would run on, when they are already here: an
# x86_64 python@3.12 (a Rosetta Homebrew's) or backend/.venv stops setup now.
PY312="$BREW_PREFIX/opt/python@3.12/bin/python3.12"
VENV="$REPO/backend/.venv"
check_python_arch "$PY312"
check_python_arch "$VENV/bin/python"

# ── 2. Packages and Postgres ──────────────────────────────────────────
step "Installing Homebrew packages (python@3.12, postgresql@16, node)"
for formula in python@3.12 postgresql@16 node; do
    if brew list --versions "$formula" >/dev/null 2>&1; then
        ok "$formula $(brew list --versions "$formula" | awk '{print $2}')"
    else
        echo "    installing $formula ..."
        brew install "$formula"
        ok "$formula installed"
    fi
done

PG_BIN="$BREW_PREFIX/opt/postgresql@16/bin"
PG_CONFIG="$PG_BIN/pg_config"
[ -x "$PG_CONFIG" ] || die "postgresql@16 is installed but $PG_CONFIG is missing"
[ -x "$PY312" ] || die "python@3.12 is installed but $PY312 is missing"
check_python_arch "$PY312"
export PATH="$PG_BIN:$BREW_PREFIX/bin:$PATH"

step "Starting PostgreSQL 16"
if "$PG_BIN/pg_isready" -q 2>/dev/null; then
    ok "a Postgres server is already accepting connections"
else
    brew services start postgresql@16 >/dev/null
    for _ in $(seq 1 30); do
        "$PG_BIN/pg_isready" -q 2>/dev/null && break
        sleep 1
    done
    "$PG_BIN/pg_isready" -q 2>/dev/null || die "PostgreSQL 16 did not start; see $BREW_PREFIX/var/log/postgresql@16.log"
    ok "postgresql@16 started (brew services)"
fi
SERVER_VERSION="$("$PG_BIN/psql" -X -d postgres -tAc 'show server_version_num' 2>/dev/null || true)"
case "$SERVER_VERSION" in
    16*) ok "server is PostgreSQL 16" ;;
    "") die "cannot connect to Postgres as $(id -un). Is another server (Postgres.app, a Docker container) holding port 5432?" ;;
    *) die "the server on port 5432 is not PostgreSQL 16 (server_version_num $SERVER_VERSION). Stop it, or point DATABASE_URL at a Postgres 16 with pgvector, and run ./setup.sh again." ;;
esac

# ── 3. pgvector ───────────────────────────────────────────────────────
step "pgvector for Postgres 16"
PG_SHAREDIR="$("$PG_CONFIG" --sharedir)"
PG_PKGLIBDIR="$("$PG_CONFIG" --pkglibdir)"
if [ -f "$PG_SHAREDIR/extension/vector.control" ] \
        && { [ -f "$PG_PKGLIBDIR/vector.dylib" ] || [ -f "$PG_PKGLIBDIR/vector.so" ]; }; then
    ok "already installed ($(sed -n "s/^default_version = '\(.*\)'/\1/p" "$PG_SHAREDIR/extension/vector.control"))"
else
    # Homebrew's `pgvector` formula builds for its newest Postgres only, so
    # it is built here against postgresql@16's own pg_config. A `brew
    # reinstall postgresql@16` removes it again: re-run ./setup.sh.
    BUILD_DIR="$(mktemp -d)"
    trap 'rm -rf "$BUILD_DIR"' EXIT
    echo "    building pgvector $PGVECTOR_VERSION from source ..."
    git clone --quiet --branch "$PGVECTOR_VERSION" --depth 1 https://github.com/pgvector/pgvector.git "$BUILD_DIR/pgvector"
    ( cd "$BUILD_DIR/pgvector" && make -s PG_CONFIG="$PG_CONFIG" && make -s install PG_CONFIG="$PG_CONFIG" ) \
        || die "pgvector did not build; see the output above"
    ok "pgvector $PGVECTOR_VERSION installed into $PG_SHAREDIR"
fi

# ── 4. Role, database, extension ──────────────────────────────────────
step "Database edward"
psql_super() { "$PG_BIN/psql" -X -q -v ON_ERROR_STOP=1 "$@"; }
psql_super -d postgres <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'edward') THEN
    CREATE ROLE edward LOGIN PASSWORD 'edward';
  END IF;
END $$;
SQL
ok "role edward"
if [ "$(psql_super -d postgres -tAc "select 1 from pg_database where datname = 'edward'")" != "1" ]; then
    "$PG_BIN/createdb" -O edward edward
    ok "database edward created"
else
    ok "database edward exists"
fi
psql_super -d edward -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
ok "extension vector in edward"

# ── 5. Backend ────────────────────────────────────────────────────────
step "Backend (python3.12 venv)"
if [ -x "$VENV/bin/python" ]; then
    VENV_VERSION="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
    [ "$VENV_VERSION" = "3.12" ] || die "backend/.venv is Python $VENV_VERSION, not 3.12. Move it aside (mv backend/.venv backend/.venv.old) and run ./setup.sh again."
    ok "backend/.venv exists (Python $VENV_VERSION)"
else
    "$PY312" -m venv "$VENV"
    ok "backend/.venv created"
fi
MARKER="$VENV/.deps_installed"   # the same marker backend/start.sh uses
if [ -f "$MARKER" ] && ! [ "$REPO/backend/requirements.txt" -nt "$MARKER" ] \
        && ! [ "$REPO/backend/constraints.txt" -nt "$MARKER" ]; then
    ok "dependencies are current"
else
    echo "    installing dependencies (a few minutes the first time) ..."
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r "$REPO/backend/requirements.txt" -c "$REPO/backend/constraints.txt"
    touch "$MARKER"
    ok "dependencies installed"
fi
# Edward's own headless browser is Playwright's shell, never Google Chrome
# (services/headless_browser.py). Playwright skips it when it is already there.
"$VENV/bin/python" -m playwright install chromium-headless-shell >/dev/null
ok "Playwright's headless shell"

# ── 6. Frontend ───────────────────────────────────────────────────────
step "Frontend (npm)"
FRONTEND="$REPO/frontend"
if [ -d "$FRONTEND/node_modules" ] && [ -f "$FRONTEND/node_modules/.package-lock.json" ] \
        && ! [ "$FRONTEND/package-lock.json" -nt "$FRONTEND/node_modules/.package-lock.json" ]; then
    ok "node_modules is current"
else
    ( cd "$FRONTEND" && npm ci --no-audit --no-fund --loglevel=error )
    ok "npm ci done"
fi

# ── 7. .env ───────────────────────────────────────────────────────────
step "Settings (.env)"
if [ ! -f "$ENV_FILE" ]; then
    cp "$REPO/.env.example" "$ENV_FILE"
    ok "created .env from .env.example"
else
    ok ".env exists (only empty settings below are asked for)"
fi
chmod 600 "$ENV_FILE"


# The owner's name.
if [ -z "$(current EDWARD_OWNER_NAME)" ]; then
    NAME="${EDWARD_OWNER_NAME:-}"
    if [ -z "$NAME" ] && $INTERACTIVE; then
        read -r -p "    What should Edward call you? (your first name): " NAME
    fi
    if [ -n "$NAME" ]; then
        set_env EDWARD_OWNER_NAME "$NAME"
        ok "EDWARD_OWNER_NAME set"
    else
        warn_later "EDWARD_OWNER_NAME is empty: Edward will call you \"the owner\" until you set it in .env"
    fi
else
    ok "EDWARD_OWNER_NAME is set"
fi

# The Anthropic API key.
KEY_NOW="$(current ANTHROPIC_API_KEY)"
if [ -z "$KEY_NOW" ] || [ "$KEY_NOW" = "sk-ant-..." ]; then
    KEY="${ANTHROPIC_API_KEY:-}"
    if [ -z "$KEY" ] && $INTERACTIVE; then
        read -r -s -p "    Anthropic API key (console.anthropic.com > API keys; typing is hidden, Enter to skip): " KEY
        echo
    fi
    if [ -n "$KEY" ]; then
        set_env ANTHROPIC_API_KEY "$KEY"
        ok "ANTHROPIC_API_KEY set"
    else
        warn_later "ANTHROPIC_API_KEY is empty: Edward cannot think until you add it to .env"
    fi
    unset KEY
else
    ok "ANTHROPIC_API_KEY is set"
fi
unset KEY_NOW

# The provider: every model call on the Claude API.
if [ -z "$(current EDWARD_LLM_PROVIDER)" ]; then
    set_env EDWARD_LLM_PROVIDER anthropic
fi
ok "EDWARD_LLM_PROVIDER=$(current EDWARD_LLM_PROVIDER)"

# The time zone (cron times, quiet hours and "today" are all in it).
if [ -z "$(current TIMEZONE)" ]; then
    TZ_DEFAULT="$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' || true)"
    TZ_DEFAULT="${TZ_DEFAULT:-UTC}"
    ZONE="${TIMEZONE:-}"
    if [ -z "$ZONE" ] && $INTERACTIVE; then
        read -r -p "    Your time zone [$TZ_DEFAULT]: " ZONE
    fi
    ZONE="${ZONE:-$TZ_DEFAULT}"
    if [ ! -f "/usr/share/zoneinfo/$ZONE" ]; then
        warn_later "'$ZONE' is not a time zone name (like America/Chicago); using $TZ_DEFAULT"
        ZONE="$TZ_DEFAULT"
    fi
    set_env TIMEZONE "$ZONE"
    ok "TIMEZONE=$ZONE"
else
    ok "TIMEZONE=$(current TIMEZONE)"
fi

# ── 8. Optional pieces ────────────────────────────────────────────────
if $WITH_TEST_DB; then
    step "Test cluster (:5433, for the backend tests)"
    bash "$REPO/scripts/test-pg-cluster.sh" ensure
    ok "$(bash "$REPO/scripts/test-pg-cluster.sh" status)"
fi

# build_helper NAME SCRIPT SOURCE OUTPUT -> builds when the output is
# missing or older than its source; a failure is a warning, not a stop.
build_helper() {
    local name="$1" script="$2" src="$3" out="$4"
    if [ -x "$out" ] && ! [ "$src" -nt "$out" ]; then
        ok "$name is built ($out)"
        return 0
    fi
    if bash "$script" "$out" >/dev/null 2>&1; then
        ok "$name built ($out)"
    else
        warn_later "$name did not build (bash ${script#$REPO/} to see why); Edward works without it, with less"
    fi
}

if $WITH_HELPERS; then
    step "Swift helpers"
    if command -v swiftc >/dev/null 2>&1; then
        # Photos, voice messages and videos in iMessage (services/media/).
        build_helper "media helper" "$REPO/backend/scripts/build_mediatool.sh" \
            "$REPO/backend/tools/media/edward_mediatool.swift" \
            "$(edward_expand_home "$(edward_env EDWARD_MEDIATOOL "$HOME/edward-media/bin/edward-mediatool")")"
        # Calendar and Reminders through EventKit (services/eventkit_service.py).
        build_helper "EventKit helper" "$REPO/backend/scripts/build_eventkit.sh" \
            "$REPO/backend/tools/eventkit/edward_eventkit.swift" \
            "$(edward_expand_home "$(edward_env EDWARD_EVENTKIT "$HOME/edward-eventkit/bin/edward-eventkit")")"
    else
        warn_later "swiftc not found: the media and EventKit helpers were not built (install Xcode or its command-line tools)"
    fi
fi

if $WITH_AGENTS; then
    step "LaunchAgents"
    bash "$REPO/scripts/install-launchagents.sh"
fi

# ── Done ──────────────────────────────────────────────────────────────
PREFIX="$(edward_launchd_prefix)"
FDA_PY="$("$VENV/bin/python" -c 'import os, sys; print(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))' 2>/dev/null || echo "$PY312")"
echo
echo "${BOLD}=== Edward is set up ===${NC}"
if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo
    echo "Still to do:"
    for w in "${WARNINGS[@]}"; do echo "  - $w"; done
fi
print_next_steps "$WITH_AGENTS" "$PREFIX" "$FDA_PY"
