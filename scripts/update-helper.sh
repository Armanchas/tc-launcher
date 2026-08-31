#!/bin/sh
# Swap a running launcher for a downloaded one, then restart it.
#
#   update-helper.sh <pid-to-wait-for> <path-to-replace> <downloaded-file>
#
# Waits by PID, not by image name: prospect-og's helper used
# `pgrep -f "$EXE_NAME"`, which matches alarmingly broadly.
#
# A running AppImage is FUSE-mounted and cannot be overwritten in place, which
# is why this must run detached, after the launcher exits.
set -eu

PID="$1"
OLD="$2"
NEW="$3"

i=0
while kill -0 "$PID" 2>/dev/null; do
    sleep 1
    i=$((i + 1))
    [ "$i" -ge 120 ] && exit 1   # give up rather than spin forever
done

mv -f "$NEW" "$OLD"
chmod +x "$OLD"
"$OLD" &
exit 0
