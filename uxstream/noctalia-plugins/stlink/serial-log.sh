#!/usr/bin/env bash
# Open a kitty running picocom on the ST-Link's serial port (else the first one), logging to a timestamped file.
#   serial-log.sh [baud] [logdir]
set -euo pipefail
BAUD="${1:-2000000}"
LOGDIR="${2:-/tmp/serial-logs}"
mkdir -p "$LOGDIR"
PORT=""
# The ST-Link first, as other USB serial devices (such as a ZMK keyboard) can take ttyACM0.
for p in /dev/serial/by-id/*STLINK* /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$p" ] && PORT="$(readlink -f "$p")" && break
done
if [ -z "$PORT" ]; then
    echo "no /dev/ttyACM* or /dev/ttyUSB* found" >&2
    exit 1
fi
LOGFILE="$LOGDIR/$(date +%Y%m%d-%H%M%S)-$(basename "$PORT").log"
exec kitty --title "picocom $PORT @ $BAUD" picocom -b "$BAUD" --logfile "$LOGFILE" "$PORT"
