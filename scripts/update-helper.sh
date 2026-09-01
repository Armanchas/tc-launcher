#!/bin/sh
# Swap a running launcher for a downloaded one, then restart it.
#
#   update-helper.sh <pid-to-wait-for> <path-to-replace> <downloaded-file>
#
# Waits by PID, not by image name: the old launcher used `pgrep -f`, which
# matches alarmingly broadly.
#
# A running AppImage is FUSE-mounted and cannot be overwritten in place, which
# is why this runs detached, after the launcher exits. The move is RETRIED
# rather than attempted once, because the squashfs unmount does not necessarily
# complete the instant the process does. The Windows helper retries for the
# same reason, from a different cause.
set -u

PID="$1"
OLD="$2"
NEW="$3"

LOGDIR="${HOME:-/tmp}/.tclauncher"
mkdir -p "$LOGDIR" 2>/dev/null || true
LOG="$LOGDIR/update-helper.log"
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$LOG" 2>/dev/null || true; }

log "start pid=$PID old=$OLD new=$NEW"

i=0
while kill -0 "$PID" 2>/dev/null; do
    sleep 1
    i=$((i + 1))
    if [ "$i" -ge 120 ]; then
        log "GAVE UP: pid $PID still running after 120s"
        exit 1
    fi
done

m=0
until mv -f "$NEW" "$OLD" 2>/dev/null; do
    m=$((m + 1))
    if [ "$m" -ge 30 ]; then
        log "GAVE UP: could not replace $OLD after 30 tries; $NEW left in place"
        exit 1
    fi
    sleep 1
done

chmod +x "$OLD" 2>/dev/null || true
log "swapped after $m retries; relaunching"
"$OLD" &
exit 0
