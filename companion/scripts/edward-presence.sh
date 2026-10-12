#!/bin/bash
# Report this Mac's presence to Edward every time launchd runs it.
#
# Install on each Mac of your own (not a shared or work machine):
#   1. cp edward-presence.sh ~/bin/  (chmod +x)
#   2. Create ~/.config/edward/presence.env (chmod 600) with:
#        EDWARD_URL=https://YOUR-EDWARD-HOST   (EDWARD_PUBLIC_BASE_URL on the backend)
#        EDWARD_TOKEN=<widget token from Settings → Widget>
#      The widget token can chat with Edward as you (POST /api/widget/chat),
#      not only report presence: keep it private, and regenerate it if it leaks.
#   3. cp com.meet-edward.presence.plist ~/Library/LaunchAgents/ and
#      launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.meet-edward.presence.plist
#
# What it sends: device name, idle seconds (from IOHIDSystem), and whether
# the screen is locked. Nothing else. The frontmost app is NOT reported by
# this script — that is an opt-in feature of the menu-bar companion.
set -u
ENV_FILE="${EDWARD_PRESENCE_ENV:-$HOME/.config/edward/presence.env}"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
: "${EDWARD_URL:?set EDWARD_URL in $ENV_FILE}"
: "${EDWARD_TOKEN:?set EDWARD_TOKEN in $ENV_FILE}"

IDLE=$(ioreg -c IOHIDSystem -d 4 | awk -F'= ' '/HIDIdleTime/ {print int($2/1000000000); exit}')
IDLE=${IDLE:-0}
STATE=active
if /usr/bin/python3 -c 'import Quartz,sys; d=Quartz.CGSessionCopyCurrentDictionary() or {}; sys.exit(0 if d.get("CGSSessionScreenIsLocked",0) else 1)' 2>/dev/null; then
  STATE=locked
elif [ "$IDLE" -ge 120 ]; then
  STATE=idle
fi
NAME=$(scutil --get ComputerName 2>/dev/null || hostname -s)
ID=$(echo "$NAME" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')

exec curl -s -m 5 -o /dev/null -X POST "$EDWARD_URL/api/presence/beacon" \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$EDWARD_TOKEN\",\"device_id\":\"$ID\",\"device_name\":\"$NAME\",\"kind\":\"mac\",\"state\":\"$STATE\",\"idle_seconds\":$IDLE}"
