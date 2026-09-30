# Connect adb to a phone over wifi:
#   adb-wifi                   phone on USB: switch it to wifi and connect; otherwise reconnect to the last one
#   adb-wifi 192.168.1.23[:P]  connect to that address (port 5555 unless given)
#   adb-wifi pair IP:PORT CODE pair with Android 11+ "Wireless debugging" -> "Pair device with pairing code"
# This adb build has no mDNS, so the phone's address has to come from USB or from its screen.
last="${XDG_STATE_HOME:-$HOME/.local/state}/adb-wifi-last"

connect() {
  for _ in 1 2 3 4 5; do
    if adb connect "$1" | grep -q "connected to"; then
      mkdir -p "$(dirname "$last")"
      echo "$1" > "$last"
      echo "connected to $1"
      return 0
    fi
    sleep 1
  done
  echo "could not connect to $1 -- same wifi as this machine?" >&2
  return 1
}

case "${1:-}" in
  pair)
    [ $# -eq 3 ] || { echo "usage: adb-wifi pair IP:PORT CODE" >&2; exit 2; }
    adb pair "$2" "$3"
    echo "paired. Now run: adb-wifi IP:PORT  (the address under 'Wireless debugging', not the pairing port)"
    ;;
  "")
    usb="$(adb devices | awk 'NR > 1 && $2 == "device" && $1 !~ /:/ { print $1; exit }')"
    if [ -n "$usb" ]; then
      ip="$(adb -s "$usb" shell ip -f inet addr show wlan0 | awk '/inet / { sub(/\/.*/, "", $2); print $2; exit }')"
      [ -n "$ip" ] || { echo "phone $usb has no wifi address -- is wifi on?" >&2; exit 1; }
      adb -s "$usb" tcpip 5555 >/dev/null
      connect "$ip:5555"
      echo "USB can be unplugged now (wifi mode lasts until the phone reboots)"
    elif [ -s "$last" ]; then
      connect "$(cat "$last")"
    else
      echo "no phone on USB and no earlier address. Plug it in once, or: adb-wifi IP[:PORT]" >&2
      exit 1
    fi
    ;;
  *:*) connect "$1" ;;
  *) connect "$1:5555" ;;
esac
