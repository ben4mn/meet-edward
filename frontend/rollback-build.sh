#!/bin/bash
# Serve the build before the last swap again: .next and .next.prev trade
# places, the restored build's service worker goes back into public/, and
# its BUILD_ID is touched so the restart serves it instead of rebuilding
# the code being rolled back (ensure-build.sh, rollback_to_previous).
#
#   bash frontend/rollback-build.sh
#   launchctl kickstart -k gui/$UID/<prefix>.frontend   (or ./restart.sh frontend)
#
# Running it again undoes it. The rollback holds until the next source
# change, which rebuilds in place as any change does -- or deploy a fix
# with deploy-build.sh. Refused while a deploy build runs or a staged build
# waits.
#
# Usage: bash rollback-build.sh [frontend_dir]   (default: this script's dir)
# Exit: 0 rolled back, 1 refused (nothing changed).
# Tests: bash frontend/test_ensure_build.sh

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENSURE_BUILD_LIB_ONLY=1 . "$HERE/ensure-build.sh"

rollback_to_previous "$(cd "${1:-$HERE}" && pwd)"
