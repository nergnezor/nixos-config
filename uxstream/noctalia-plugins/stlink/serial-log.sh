#!/usr/bin/env bash
# Open a kitty running picocom on the first serial port, logging to a timestamped file.
#   serial-log.sh [baud] [logdir]
set -euo pipefail
BAUD="${1:-2000000}"
LOGDIR="${2:-/tmp/serial-logs}"
mkdir -p "$LOGDIR"
PORT=""
for p in /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$p" ] && PORT="$p" && break
done
if [ -z "$PORT" ]; then
    echo "no /dev/ttyACM* or /dev/ttyUSB* found" >&2
    exit 1
fi
LOGFILE="$LOGDIR/$(date +%Y%m%d-%H%M%S)-$(basename "$PORT").log"
exec kitty --title "picocom $PORT @ $BAUD" picocom -b "$BAUD" --logfile "$LOGFILE" "$PORT"
