# Open a terminal running picocom at 2 Mbaud on the first serial port, logging to /tmp/serial-logs
LOGDIR="/tmp/serial-logs"
mkdir -p "$LOGDIR"
PORT=""
for p in /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$p" ] && PORT="$p" && break
done
if [ -z "$PORT" ]; then
    notify-send "Serial log" "Ingen /dev/ttyACM* eller /dev/ttyUSB* hittades"
    exit 1
fi
LOGFILE="$LOGDIR/$(date +%Y%m%d-%H%M%S)-$(basename "$PORT").log"
exec kitty --title "picocom $PORT @ 2M" picocom -b 2000000 --logfile "$LOGFILE" "$PORT"
