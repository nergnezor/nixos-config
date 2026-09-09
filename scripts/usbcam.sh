# Low-latency USB camera viewer (mpv + v4l2).
# Rotation: -r / keys r and R. Device: -d. List: -l.
# Installed via home.nix writeShellApplication (adds bash shebang + PATH).

DEVICE="/dev/video0"
ROTATE=0
SIZE="" # e.g. 640x480 — empty lets the driver pick
FPS=""
LIST=0

usage() {
  cat <<'EOF'
Usage: usbcam [options]

  -d DEV     V4L device (default: /dev/video0; also accepts 0, 1, ...)
  -r DEG     Start rotation clockwise: 0, 90, 180, or 270 (default: 0)
  -s WxH     Capture size, e.g. 640x480 (default: driver default)
  -f FPS     Capture framerate (default: driver default / max for size)
  -l         List video capture devices and formats, then exit
  -h         This help

In the viewer:
  r / R      Rotate +90 / -90
  f          Toggle fullscreen
  q / Esc    Quit
EOF
}

while getopts ":d:r:s:f:lh" opt; do
  case "$opt" in
    d) DEVICE="$OPTARG" ;;
    r) ROTATE="$OPTARG" ;;
    s) SIZE="$OPTARG" ;;
    f) FPS="$OPTARG" ;;
    l) LIST=1 ;;
    h) usage; exit 0 ;;
    \?) echo "unknown option: -$OPTARG" >&2; usage >&2; exit 1 ;;
    :) echo "option -$OPTARG needs an argument" >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

# Allow a bare device arg: usbcam 1  or  usbcam /dev/video2
if [[ $# -ge 1 ]]; then
  DEVICE="$1"
fi

if [[ "$DEVICE" =~ ^[0-9]+$ ]]; then
  DEVICE="/dev/video$DEVICE"
fi

case "$ROTATE" in
  0|90|180|270) ;;
  *) echo "rotation must be 0, 90, 180, or 270" >&2; exit 1 ;;
esac

if [[ "$LIST" -eq 1 ]]; then
  echo "=== devices ==="
  v4l2-ctl --list-devices || true
  echo
  for d in /dev/video*; do
    [[ -e "$d" ]] || continue
    caps=$(v4l2-ctl -d "$d" --all 2>/dev/null | grep -E 'Device Caps|Video Capture' | head -5 || true)
    if echo "$caps" | grep -q 'Video Capture'; then
      echo "=== $d formats ==="
      v4l2-ctl -d "$d" --list-formats-ext 2>/dev/null || true
      echo
    fi
  done
  exit 0
fi

if [[ ! -e "$DEVICE" ]]; then
  echo "no such device: $DEVICE" >&2
  echo "try: usbcam -l" >&2
  exit 1
fi

LAVF_O=()
if [[ -n "$SIZE" ]]; then
  LAVF_O+=("video_size=${SIZE}")
fi
if [[ -n "$FPS" ]]; then
  LAVF_O+=("framerate=${FPS}")
fi

DEMUXER_ARGS=()
if [[ ${#LAVF_O[@]} -gt 0 ]]; then
  IFS=,
  DEMUXER_ARGS=(--demuxer-lavf-o="${LAVF_O[*]}")
  unset IFS
fi

# Temporary input.conf so rotate keys work without touching ~/.config/mpv.
INPUT_CONF=$(mktemp)
trap 'rm -f "$INPUT_CONF"' EXIT
cat >"$INPUT_CONF" <<'EOF'
r cycle_values video-rotate 0 90 180 270
R cycle_values video-rotate 0 270 180 90
f cycle fullscreen
EOF

# No exec: trap must run after mpv exits to remove INPUT_CONF.
mpv \
  "av://v4l2:${DEVICE}" \
  --title="usbcam ${DEVICE}" \
  --profile=low-latency \
  --untimed \
  --no-cache \
  --cache=no \
  --demuxer-readahead-secs=0 \
  --vd-lavc-threads=1 \
  --hwdec=auto-safe \
  --vo=gpu \
  --video-rotate="$ROTATE" \
  --input-conf="$INPUT_CONF" \
  --no-osc \
  "${DEMUXER_ARGS[@]}"
