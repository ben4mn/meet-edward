#!/bin/bash
# Tests for setup.sh's .env writing: what it writes must read back
# unchanged through bash `source` (an owner's own shell), python-dotenv (the
# backend and the graph worker, which load .env themselves) and
# scripts/lib/edward-env.sh (backend/start.sh and the scripts launchd runs). And its
# machine check: an Intel Mac, a Rosetta shell or an x86_64 python stops it
# before anything is installed. Nothing here installs anything or touches
# Postgres (the runs of setup.sh itself have brew stubbed out).
#
# Run: bash scripts/test_setup.sh

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export EDWARD_SETUP_ENV_FILE="$TMP/.env"
SETUP_LIB_ONLY=1 . "$REPO/setup.sh" --ignored-when-sourced
set +e   # setup.sh's own `set -e` must not end the test run

# After sourcing: setup.sh has an ok() of its own.
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }
contains() { case "$2" in *"$3"*) ok "$1";; *) nope "$1 (no '$3' in: $2)";; esac; }

PYDOTENV=""
for py in ${EDWARD_TEST_PYTHON:-} "$REPO/backend/.venv/bin/python" python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import dotenv' 2>/dev/null; then PYDOTENV="$py"; break; fi
done

roundtrip() {  # roundtrip LABEL VALUE
    local label="$1" value="$2"
    printf '# Edward\n# EDWARD_OWNER_NAME=\nOTHER=keep\n' > "$EDWARD_SETUP_ENV_FILE"
    set_env EDWARD_OWNER_NAME "$value" >/dev/null
    check "$label: bash source" "$(bash -c 'set -a; . "$1"; printf %s "$EDWARD_OWNER_NAME"' _ "$EDWARD_SETUP_ENV_FILE")" "$value"
    check "$label: edward-env.sh" "$(current EDWARD_OWNER_NAME)" "$value"
    if [ -n "$PYDOTENV" ]; then
        check "$label: python-dotenv" \
            "$("$PYDOTENV" -c 'import sys, dotenv; print(dotenv.dotenv_values(sys.argv[1])["EDWARD_OWNER_NAME"], end="")' "$EDWARD_SETUP_ENV_FILE")" "$value"
    fi
    check "$label: the commented line became the setting" "$(grep -c '^# EDWARD_OWNER_NAME=' "$EDWARD_SETUP_ENV_FILE")" "0"
    check "$label: other lines kept" "$(grep -c '^OTHER=keep$' "$EDWARD_SETUP_ENV_FILE")" "1"
}

echo "set_env round trips:"
roundtrip "a plain name" "Sam"
roundtrip "a name with a space" "Mary Jane"
roundtrip "an apostrophe" "D'Arcy"
roundtrip "an API key" "sk-ant-api03-AbC_123-xyz"
roundtrip "a time zone" "America/Argentina/Buenos_Aires"
[ -n "$PYDOTENV" ] || echo "  skip - python-dotenv checks (no python with dotenv; set EDWARD_TEST_PYTHON)"

echo "set_env edits in place:"
printf 'ANTHROPIC_API_KEY=\nEDWARD_LLM_PROVIDER=codex\n' > "$EDWARD_SETUP_ENV_FILE"
set_env EDWARD_LLM_PROVIDER anthropic
check "replaces an existing value" "$(current EDWARD_LLM_PROVIDER)" "anthropic"
check "one line, not two" "$(grep -c '^EDWARD_LLM_PROVIDER=' "$EDWARD_SETUP_ENV_FILE")" "1"
set_env TIMEZONE Europe/Paris
check "appends a missing key" "$(tail -1 "$EDWARD_SETUP_ENV_FILE")" "TIMEZONE=Europe/Paris"
check "the file stays private" "$(stat -f %Lp "$EDWARD_SETUP_ENV_FILE" 2>/dev/null || stat -c %a "$EDWARD_SETUP_ENV_FILE")" "600"

echo "values .env cannot hold:"
printf 'X=1\n' > "$EDWARD_SETUP_ENV_FILE"
OUT="$(set_env EDWARD_OWNER_NAME 'it'"'"'s $HOME' 2>&1)"
check "refused, not mangled" "$(grep -c EDWARD_OWNER_NAME "$EDWARD_SETUP_ENV_FILE")" "0"
case "$OUT" in *"by hand"*) ok "says to set it by hand";; *) nope "says to set it by hand ($OUT)";; esac

echo "the template setup.sh copies:"
EX="$REPO/.env.example"
for key in EDWARD_OWNER_NAME ANTHROPIC_API_KEY EDWARD_LLM_PROVIDER TIMEZONE; do
    check "$key is a live line in .env.example" "$(grep -cE "^${key}=" "$EX")" "1"
done
check "the example's provider is anthropic" "$(edward_dotenv_get EDWARD_LLM_PROVIDER "$EX")" "anthropic"
check "the example holds no key" "$(edward_dotenv_get ANTHROPIC_API_KEY "$EX")" ""
check "bash can source the example" "$(bash -c 'set -a; . "$1" >/dev/null 2>&1 && echo yes' _ "$EX")" "yes"

echo "arch_problem (Apple silicon only: torch has no x86_64 macOS wheel):"
check "Apple silicon, native -> fine" "$(arch_problem arm64 1 0)" ""
check "arm64 with no sysctl answers -> fine" "$(arch_problem arm64 "" "")" ""
OUT="$(arch_problem x86_64 "" "")"
contains "an Intel Mac -> stops" "$OUT" "Intel"
contains "... and says why (torch)" "$OUT" "PyTorch"
OUT="$(arch_problem x86_64 1 1)"
contains "a Rosetta shell on Apple silicon -> stops" "$OUT" "Rosetta"
contains "... and says how to run it natively" "$OUT" "arch -arm64 /bin/bash ./setup.sh"
contains "x86_64 on Apple-silicon hardware is Rosetta, not Intel" "$(arch_problem x86_64 1 "")" "Rosetta"

echo "python_arch_problem:"
mkdir -p "$TMP/py"
printf '#!/bin/sh\necho x86_64\n' > "$TMP/py/x86"; chmod +x "$TMP/py/x86"
printf '#!/bin/sh\necho arm64\n' > "$TMP/py/arm"; chmod +x "$TMP/py/arm"
OUT="$(python_arch_problem "$TMP/py/x86")"
contains "an x86_64 python -> stops" "$OUT" "x86_64 python"
contains "... names it" "$OUT" "$TMP/py/x86"
check "an arm64 python -> fine" "$(python_arch_problem "$TMP/py/arm")" ""
check "no python yet -> fine (Homebrew installs a native one)" "$(python_arch_problem "$TMP/py/none")" ""

echo "setup.sh stops before installing anything:"
# Run setup.sh itself with stubs first on PATH: uname, sysctl, xcode-select
# and brew. brew records every call and installs nothing; xcode-select fails
# unless the run is meant to get past it. Nothing here can install anything.
fake_run() {  # fake_run MACHINE HW_ARM64 TRANSLATED XCODE_OK -> output; rc in $TMP/rc
    local d="$TMP/stubs"
    rm -rf "$d"; mkdir -p "$d" "$TMP/brew/opt/python@3.12/bin"
    : > "$TMP/brew.log"
    cat > "$d/uname" <<STUB
#!/bin/sh
case "\$1" in -s) echo Darwin ;; -m) echo "$1" ;; *) /usr/bin/uname "\$@" ;; esac
STUB
    cat > "$d/sysctl" <<STUB
#!/bin/sh
case "\$2" in
  hw.optional.arm64) [ -n "$2" ] && echo "$2" && exit 0; exit 1 ;;
  sysctl.proc_translated) [ -n "$3" ] && echo "$3" && exit 0; exit 1 ;;
esac
exit 1
STUB
    if [ "$4" = yes ]; then
        printf '#!/bin/sh\necho /Library/Developer/CommandLineTools\n' > "$d/xcode-select"
    else
        printf '#!/bin/sh\nexit 2\n' > "$d/xcode-select"
    fi
    cat > "$d/brew" <<STUB
#!/bin/sh
echo "brew \$*" >> "$TMP/brew.log"
[ "\$1" = "--prefix" ] && [ \$# -eq 1 ] && echo "$TMP/brew" && exit 0
exit 1
STUB
    chmod +x "$d"/*
    PATH="$d:/usr/bin:/bin:/usr/sbin:/sbin" EDWARD_SETUP_ENV_FILE="$TMP/run.env" \
        /bin/bash "$REPO/setup.sh" --non-interactive --no-helpers </dev/null 2>&1
    echo "$?" > "$TMP/rc"
}
OUT="$(fake_run x86_64 "" "" no)"
check "Intel: exit 1" "$(cat "$TMP/rc")" "1"
contains "Intel: says it needs Apple silicon" "$OUT" "Edward needs a Mac with Apple silicon"
check "Intel: Homebrew never even asked" "$(cat "$TMP/brew.log")" ""
check "Intel: no .env written" "$([ -e "$TMP/run.env" ] && echo yes || echo no)" "no"
OUT="$(fake_run x86_64 1 1 no)"
contains "Rosetta shell: stops and says so" "$OUT" "runs under Rosetta"
check "Rosetta shell: Homebrew never even asked" "$(cat "$TMP/brew.log")" ""
cp "$TMP/py/x86" "$TMP/brew/opt/python@3.12/bin/python3.12"
OUT="$(fake_run arm64 1 0 yes)"
check "x86_64 python@3.12: exit 1" "$(cat "$TMP/rc")" "1"
contains "x86_64 python@3.12: says why" "$OUT" "python3.12 is an x86_64 python"
check "x86_64 python@3.12: Homebrew asked only for its prefix, nothing installed" "$(cat "$TMP/brew.log")" "brew --prefix"
check "the arch check comes before the first brew install in setup.sh" \
    "$(awk '/arch_problem "\$\(uname -m\)"/ { a = NR } /brew install "\$formula"/ && !b { b = NR } END { print (a && b && a < b) ? "yes" : "no" }' "$REPO/setup.sh")" "yes"

echo "the first password's setup code (backend/.setup_code):"
# curl is a function here: nothing reaches a real backend.
STATUS_BODY=""
curl() { [ -n "$STATUS_BODY" ] || return 7; printf '%s' "$STATUS_BODY"; }
lacks() { case "$2" in *"$3"*) nope "$1 ('$3' in: $2)";; *) ok "$1";; esac; }
check "one line, the same in setup.sh, restart.sh and the installer" "$EDWARD_SETUP_CODE_NOTE" \
    "First time? Your setup code is in backend/.setup_code (cat backend/.setup_code)"
STATUS_BODY='{"configured":false,"authenticated":false,"owner_name":"","setup_code_required":true}'
check "no password yet -> unset" "$(edward_password_state)" "unset"
OUT="$(print_next_steps false com.example /opt/py/python3.12)"
contains "no password yet: step 2 says where the code is" "$OUT" \
    "choose a password.
     $EDWARD_SETUP_CODE_NOTE"
lacks "the code itself is never printed (only its file)" "$OUT" "K7QH"
STATUS_BODY='{"configured": true, "authenticated": false}'
check "a password set -> set" "$(edward_password_state)" "set"
OUT="$(print_next_steps true com.example /opt/py/python3.12)"
lacks "a password set: no setup-code line" "$OUT" "setup code"
contains "... the rest of the steps are there" "$OUT" "launchd is starting Edward (com.example.*)"
STATUS_BODY=""
check "no answer -> unknown" "$(edward_password_state)" "unknown"
contains "the backend not up yet (a first start): the line is there" "$(print_next_steps false p /x)" \
    "$EDWARD_SETUP_CODE_NOTE"
STATUS_BODY='<html>502 Bad Gateway</html>'
check "an odd answer -> unknown" "$(edward_password_state)" "unknown"
unset -f curl
check "restart.sh prints the shared line" "$(grep -c 'EDWARD_SETUP_CODE_NOTE' "$REPO/restart.sh")" "1"
check "the installer prints the shared line" "$(grep -c 'edward_setup_code_note' "$REPO/scripts/install-launchagents.sh")" "1"
check "setup.sh's next steps come from the function" "$(grep -c '^print_next_steps "\$WITH_AGENTS"' "$REPO/setup.sh")" "1"

echo
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
