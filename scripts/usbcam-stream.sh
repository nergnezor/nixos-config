# Encode the USB camera to an MPEG-TS stream on stdout, for viewing over ssh:
#   ssh nixos-nitro usbcam-stream | mpv --profile=low-latency --untimed --video-rotate=270 -
DEVICE="${1:-/dev/video0}"
exec ffmpeg -loglevel error -nostdin \
  -f v4l2 -input_format yuyv422 -i "$DEVICE" \
  -c:v libx264 -preset ultrafast -tune zerolatency \
  -f mpegts -
