#!/usr/bin/env python3
"""EyeBuddy app: camera, serial log and ST-Link controls in one terminal window.

One file. Run it on a Debian or Ubuntu machine and it installs the toolkit it needs, then
starts. `--install` also puts it on PATH and in the launcher. It is a Textual TUI on purpose:
it streams over a plain SSH session (e.g. from the bench machine to wherever you are sitting),
no X11 forwarding or GPU needed.

The ST-Link noctalia plugin stays the backend. Actions go through `noctalia msg plugin`,
state comes back through the plugin's state.json / job.json.
"""
import argparse
import collections
import colorsys
import glob
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# textual/pillow/textual-image are not distro packages worth depending on; pip is the only sane
# source. textual-image draws both the camera and the chart with real terminal pixels (Kitty's
# graphics protocol, or Sixel) when the terminal on the viewing end supports it, falling back to
# coloured half-cells otherwise -- detected by asking the terminal, not by guessing from $TERM,
# so it works the same whether you are sitting at the machine or over SSH.
PIP_PACKAGES = ["textual", "pyserial", "pillow", "textual-image==0.13.2", "pycairo"]
# pycairo has no prebuilt wheel -- pip compiles it against the system's cairo, so that has to be
# in place first (SYSTEM_PACKAGES below). Camera packages are only needed outside of the Nix
# build (which wires ffmpeg onto PATH itself), and only if the camera is actually wanted.
SYSTEM_PACKAGES = {"apt-get": ["libcairo2-dev", "pkg-config"], "dnf": ["cairo-devel", "pkgconf-pkg-config"],
                   "pacman": ["cairo", "pkgconf"]}
CAMERA_PACKAGES = {"apt-get": ["ffmpeg", "v4l-utils"], "dnf": ["ffmpeg", "v4l-utils"],
                    "pacman": ["ffmpeg", "v4l-utils"]}
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=EyeBuddy
Comment=Camera, serial log and ST-Link controls for the eyebuds bench, in a terminal
Exec={command}
Icon=utilities-terminal
Terminal=true
Categories=Development;Utility;
"""


def package_manager():
    return next((name for name in CAMERA_PACKAGES if shutil.which(name)), None)


def install_packages(manager, packages):
    """Install with the system package manager, asking for the password the way the session can."""
    verb = ["-S", "--noconfirm"] if manager == "pacman" else ["install", "-y"]
    command = [shutil.which(manager)] + verb + packages
    if os.geteuid():
        lift = "sudo" if sys.stdin.isatty() and shutil.which("sudo") else "pkexec"
        if not shutil.which(lift):
            return False
        command = [shutil.which(lift)] + command
    return subprocess.run(command).returncode == 0


def pip_install(packages):
    """A plain `pip install --user`, escalating to --break-system-packages once PEP 668 refuses."""
    base = [sys.executable, "-m", "pip", "install", "--user"]
    if subprocess.run(base + packages).returncode == 0:
        return True
    return subprocess.run(base + ["--break-system-packages"] + packages).returncode == 0


def ensure_toolkit():
    """Import the toolkit, and when it is not there, install it once and start again."""
    try:
        import cairo, textual, textual_image, serial  # noqa: F401
        from PIL import Image  # noqa: F401
        return
    except ImportError as error:
        if os.environ.get("EYEBUDDY_BOOTSTRAPPED"):
            sys.exit(f"EyeBuddy needs a few packages: {error}")
        print(f"EyeBuddy needs a few packages: {error}")
        manager = package_manager()
        if manager:  # cairo's own headers, so pip can build pycairo against them
            install_packages(manager, SYSTEM_PACKAGES[manager])
        if not pip_install(PIP_PACKAGES):
            sys.exit("That install did not go through. Try it yourself:\n"
                     f"  {sys.executable} -m pip install --user {' '.join(PIP_PACKAGES)}")
        os.environ["EYEBUDDY_BOOTSTRAPPED"] = "1"  # one attempt, so a bad install cannot loop
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])


def install_launcher():
    """Copy the file onto PATH and write the desktop entry that starts it."""
    target = Path.home() / ".local/bin/eyebuddy"
    target.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != target.resolve():
        shutil.copy2(__file__, target)
    target.chmod(0o755)
    desktop = Path.home() / ".local/share/applications/eyebuddy.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(DESKTOP_ENTRY.format(command=target))
    subprocess.run(["update-desktop-database", str(desktop.parent)], check=False,
                   stderr=subprocess.DEVNULL)
    print(f"Installed {target}")
    if str(target.parent) not in os.environ.get("PATH", "").split(":"):
        print(f"Add {target.parent} to your PATH to start it by name.")


ensure_toolkit()

# Kitty's graphics find their image by the exact RGB colour of the placeholder cells, and SSH
# does not carry COLORTERM across -- without it Rich falls back to 16 colours, the colours no
# longer name the image and the chart and camera come out blank. Kitty always does truecolor.
if os.environ.get("TERM") == "xterm-kitty" or os.environ.get("KITTY_WINDOW_ID"):
    os.environ.setdefault("COLORTERM", "truecolor")

import cairo  # noqa: E402
import serial  # noqa: E402
from PIL import Image  # noqa: E402
from rich.style import Style  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.widgets import ProgressBar, RichLog, Static  # noqa: E402
from textual_image._terminal import get_cell_size  # noqa: E402
from textual_image.renderable import Image as _AutoRenderable  # noqa: E402
from textual_image.renderable.tgp import Image as _TGPRenderable  # noqa: E402
from textual_image.widget import AutoImage  # noqa: E402

PLUGIN = "erik/stlink:service"
DATA_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/plugins/data/erik/stlink"
# Remembered between runs: camera rotation and size, raw/pretty view.
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "eyebuds-dev/settings.json"
STATE_TEXT = {
    "running": "Running", "halted": "Halted", "reset": "In reset",
    "debug-running": "Running", "unknown": "Unknown",
}
RECENT_LINES = 12  # how many distinct non-numeric patterns are held open for a repeat count
OUTLIER_SIGMA = 5  # how far from a field's mean a value has to be before it is called out
OUTLIER_QUIET = 20  # seconds before the same field may warn again
OUTLIER_TTL = 300  # seconds an outlier stays listed
OUTLIER_SHOWN = 6  # how many of them are listed at once
RANGE_ROWS = 24  # fields the stats table has room to be useful about
CHART_WINDOW = 120  # seconds the chart shows; older samples slide out and are dropped
NUM_GAP = 7  # space between two number columns in the table strip; each is as wide as its widest number
NUM_ROW = 13  # smallest height one row of the table strip may have
AXIS_GUTTER = 38  # left margin the bands write their y values in
TIME_AXIS = 14  # bottom margin the clock ticks are written in
BAND_FLOOR = 46  # smallest height a band is given before the rest is shared out
EVENT_BAND = 72  # timeline of one-off events, kept off the metric chart
LABEL_PLATE = 17  # height of the rounded plate behind a label
LABEL_GAP = 19  # how close two labels may sit before they push each other away
LABEL_SPRING = 55  # how hard a label is pulled back to the height of its own line
LABEL_PUSH = 900  # how hard overlapping labels shove each other apart
LABEL_DAMPING = 11  # how quickly that motion settles
CAMERA_FPS = [6, 10, 15, 24, 30]  # pictures sent per second, cycled with X; SSH bandwidth is the limit
CHART_TEXT_CELL = 15  # cell height (px) the chart's text sizes are drawn for; taller cells scale it up
CHART_FPS = 3  # same, for the chart -- slow enough to be light, fast enough for the labels to glide
# The sensor trades resolution for framerate: 30 fps at 640x480, 7 fps at 1600x1200.
# Swiping the companion app's touch pad over adb: the pad fills the lower screen, so a stroke
# around this point (fractions of the screen) and this long stays on it in either direction,
# and well past the app's 15% swipe threshold.
SWIPE_CENTER = (0.5, 0.62)
SWIPE_SPAN = 0.3  # of the screen width, for both directions
SWIPE_MS = 150
SWIPE_INTERVALS = [0.5, 1, 2, 5, 10]  # seconds between swipes, cycled with I
CAMERA_SIZES = [("640x480", 30), ("800x600", 20), ("1280x720", 11), ("1600x1200", 7)]


def camera_modes(device):
    """Discrete sizes and their best framerate, from v4l2-ctl. Falls back to the known list."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", device, "--list-formats-ext"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return CAMERA_SIZES
    modes = {}
    size = None
    for line in out.splitlines():
        if m := re.search(r"Size: Discrete (\d+x\d+)", line):
            size = m.group(1)
        elif size and (m := re.search(r"\(([\d.]+) fps\)", line)):
            modes[size] = max(modes.get(size, 0), round(float(m.group(1))))
    return sorted(modes.items(), key=lambda kv: int(kv[0].split("x")[0])) or CAMERA_SIZES


# The firmware colours its log levels with SGR sequences. SGR is rendered with Rich styles.
ANSI = re.compile(r"\x1b\[([0-9;?]*)([ -/]*[@-~])")
# "00:08:05.905 TRACE REND jpeg_lcd.c:398: Display rendering rate: 29.9 fps"
LOG_LINE = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3})\s+(\w+)\s+(\S+)\s+(\S+:\d+):\s*(.*?)\s*$")


def hanging(head, body):
    """`head` then `body`, with the lines `body` wraps onto indented under its own start rather
    than back at the margin -- so the timestamp and tags stand alone down the left edge."""
    grid = Table.grid()
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row(head, body)
    return grid


def hanging_at_message(text):
    """A whole log line as one Text, hung at where its message starts (if it is a log line)."""
    m = LOG_LINE.match(text.plain)
    if not m or not m.start(5):
        return text
    head, body = text.divide([m.start(5)])
    return hanging(head, body)
LEVELS = {  # glyph and colour per level, replacing the word
    "TRACE": ("·", "#7f848e"), "DEBUG": ("○", "#61afef"), "INFO": ("●", "#98c379"),
    "WARN": ("▲", "#e5c07b"), "WARNING": ("▲", "#e5c07b"), "ERROR": ("✖", "#e06c75"), "FATAL": ("✖", "#e06c75"),
}
NUMBERS = re.compile(r"-?\d+(?:\.\d+)?")

# Which direction is good, guessed from the field's label and unit.
GOOD_LOW = re.compile(r"drop|err|fail|defer|queue|retry|lost|miss|latency|delay|jitter|usage|load|temp|reason",
                      re.IGNORECASE)
GOOD_HIGH = re.compile(r"rate|fps|bitrate|rssi|signal|throughput|speed|bandwidth|kbps|mbps|level", re.IGNORECASE)

# Bands are scale groups: everything in one shares an axis, so a height reads as a real value.
# (pattern the unit must match, band, factor into the band's base unit)
UNIT_BANDS = (
    (re.compile(r"^(/s|fps|hz)$", re.IGNORECASE), "per second", 1.0),
    (re.compile(r"^(us|µs)$", re.IGNORECASE), "milliseconds", 0.001),
    (re.compile(r"^ms$", re.IGNORECASE), "milliseconds", 1.0),
    (re.compile(r"^(s|sec|secs)$", re.IGNORECASE), "milliseconds", 1000.0),
    (re.compile(r"^%$"), "percent", 1.0),
    (re.compile(r"^(db|dbm)$", re.IGNORECASE), "decibel", 1.0),
    (re.compile(r"^(b|byte|bytes)$", re.IGNORECASE), "bytes", 1.0),
    (re.compile(r"^(kb|kib)$", re.IGNORECASE), "bytes", 1024.0),
    (re.compile(r"^mb$", re.IGNORECASE), "bytes", 1048576.0),
    (re.compile(r"^bps$", re.IGNORECASE), "bits per second", 1.0),
    (re.compile(r"^kbps$", re.IGNORECASE), "bits per second", 1000.0),
    (re.compile(r"^mbps$", re.IGNORECASE), "bits per second", 1000000.0),
)
BAND_UNIT = {"per second": "/s", "milliseconds": "ms", "percent": "%", "decibel": "dB",
             "bytes": "B", "bits per second": "bps", "count": ""}
BAND_ORDER = ["per second", "bits per second", "milliseconds", "percent", "decibel", "bytes", "count"]


def band_of(label, unit):
    """The band a field belongs to, and the factor into that band's base unit."""
    for pattern, band, factor in UNIT_BANDS:
        if pattern.match(unit):
            return band, factor
    if re.search(r"\brate\b|\bfps\b", label, re.IGNORECASE):
        return "per second", 1.0
    if re.search(r"rssi|signal", label, re.IGNORECASE):
        return "decibel", 1.0
    if re.search(r"usage|percent", label, re.IGNORECASE):
        return "percent", 1.0
    return "count", 1.0


def fmt_si(value):
    """A number short enough for a table cell."""
    if value is None:
        return "-"
    size = abs(value)
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if size >= limit:
            return f"{value / limit:.3g}{suffix}"
    if size >= 0.01 or value == 0:
        return f"{value:.4g}"
    return f"{value:.1e}"


def polarity_of(label, unit):
    """-1 when lower is better, 1 when higher is better, 0 when it is just a number."""
    text = f"{label} {unit}"
    if GOOD_LOW.search(text):
        return -1
    if GOOD_HIGH.search(text):
        return 1
    return 0


def spread(values, gap, low, high):
    """Sorted values nudged apart to at least `gap`, kept inside [low, high] where they fit."""
    out = list(values)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + gap)
    if out and out[-1] > high:
        out[-1] = high
        for i in range(len(out) - 2, -1, -1):
            out[i] = min(out[i], out[i + 1] - gap)
    if out and out[0] < low:
        out[0] = low
        for i in range(1, len(out)):
            out[i] = max(out[i], out[i - 1] + gap)
    return out


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def spline(cr, points):
    """Catmull-Rom through the points, as cubic beziers: no overshoot, no corners."""
    if len(points) < 2:
        return
    cr.move_to(*points[0])
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i else points[0]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                    p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6,
                    p2[0], p2[1])


def cairo_to_pil(surface, w, h):
    """A finished ImageSurface as a plain PIL image, ready for textual-image to show."""
    surface.flush()
    stride = surface.get_stride()
    buf = bytes(surface.get_data())
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", stride, 1).convert("RGB")


SGR_COLORS = {
    30: "#3b4252", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b", 34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#c8ccd4",
    90: "#7f848e", 91: "#ef8a8a", 92: "#b5e890", 93: "#f0d38a", 94: "#8ac4ff", 95: "#dc9bf0", 96: "#7ad4df", 97: "#ffffff",
}


def ts_seconds(ts):
    """Firmware timestamp HH:MM:SS.mmm as seconds."""
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def field_label(text, drop_unit=""):
    """Label for a numeric field: the words right before it, minus the previous field's unit."""
    if drop_unit:
        text = re.sub(r"^\s*" + re.escape(drop_unit) + r"\b", "", text)
    words = re.sub(r"%%", "%", re.sub(r"[^\w%/ -]", " ", text)).split()
    return " ".join(words[-3:])[:22] or "?"


def field_unit(text):
    """Unit right after a number: the first word, if it looks like one."""
    m = re.match(r"\s*([A-Za-z%/]{1,6})\b", text)
    return m.group(1) if m else ""


def pattern_of(plain):
    """Mute key for a log line: module, location and message with every number replaced by #."""
    m = LOG_LINE.match(plain.rstrip("\r\n"))
    body = f"{m.group(3)} {m.group(4)} {m.group(5)}" if m else plain.strip()
    return NUMBERS.sub("#", body)


def send(action, **payload):
    cmd = ["noctalia", "msg", "plugin", PLUGIN, "all", action]
    if payload:
        cmd.append(json.dumps(payload))
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass  # no noctalia here, so there is no ST-Link backend to talk to either


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_settings(**values):
    current = read_json(SETTINGS) or {}
    current.update(values)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(current))


class CameraCapture(threading.Thread):
    """Raw RGB frames from `ffmpeg -f v4l2`, kept as only the latest one (old frames are dropped)."""

    def __init__(self, device, size):
        super().__init__(daemon=True)
        self.device = device
        self.width, self.height = (int(v) for v in size.split("x"))
        self._frame = None
        self._lock = threading.Lock()
        self.error = None
        self._stop = threading.Event()
        self.proc = None

    def run(self):
        frame_bytes = self.width * self.height * 3
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2",
               "-video_size", f"{self.width}x{self.height}", "-i", self.device,
               "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            self.error = str(exc)
            return
        buf = b""
        while not self._stop.is_set():
            chunk = self.proc.stdout.read(frame_bytes - len(buf))
            if not chunk:
                break
            buf += chunk
            if len(buf) >= frame_bytes:
                with self._lock:
                    self._frame = (self.width, self.height, buf)
                buf = b""
        self.proc.stdout.close()
        if not self._stop.is_set():
            # ffmpeg gave up -- most often "Device or resource busy", another program holding it
            lines = self.proc.stderr.read().decode(errors="replace").strip().splitlines()
            self.error = lines[-1] if lines else f"ffmpeg exited ({self.proc.wait()})"

    def latest(self):
        with self._lock:
            return self._frame

    def stop(self):
        self._stop.set()
        if self.proc:
            self.proc.terminate()


class AdbSwiper(threading.Thread):
    """Swipes the phone's touch pad over adb on a timer, back and forth along one axis so the
    app ends where it started. `axis` is None (idle), "vertical" or "horizontal".
    """

    def __init__(self, interval, on_note):
        super().__init__(daemon=True)
        self.axis, self.interval, self.on_note = None, interval, on_note
        self.screen = None  # (width, height) in pixels, asked for once a device answers
        self.failed = False
        self._wake = threading.Event()

    def set(self, axis, interval):
        self.axis, self.interval = axis, interval
        self._wake.set()

    def run(self):
        forward = True
        while True:
            self._wake.wait(self.interval if self.axis else None)
            self._wake.clear()
            if self.axis and self._swipe(self.axis, forward):
                forward = not forward

    def _adb(self, *args):
        result = subprocess.run(["adb", "shell", *args], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise OSError((result.stderr or result.stdout).strip() or f"adb exited {result.returncode}")
        return result.stdout

    def _swipe(self, axis, forward):
        try:
            if not self.screen:
                # "Physical size: WxH", then "Override size: WxH" if set -- the last one is in effect
                sizes = re.findall(r"(\d+)x(\d+)", self._adb("wm", "size"))
                if not sizes:
                    raise OSError("adb: could not read the screen size")
                self.screen = tuple(int(v) for v in sizes[-1])
            w, h = self.screen
            cx, cy, half = w * SWIPE_CENTER[0], h * SWIPE_CENTER[1], w * SWIPE_SPAN / 2
            sign = 1 if forward else -1
            dx, dy = (sign * half, 0) if axis == "horizontal" else (0, sign * half)
            self._adb("input", "swipe", *(str(round(v)) for v in (cx - dx, cy - dy, cx + dx, cy + dy)),
                      str(SWIPE_MS))
        except (OSError, subprocess.SubprocessError) as exc:
            self.screen = None  # another phone may be plugged in by the next try
            if not self.failed:  # said once, not on every tick until a phone turns up
                self.failed = True
                self.on_note(f"Swipe failed: {exc}")
            return False
        if self.failed:
            self.failed = False
            self.on_note("Swiping again.")
        return True


class SerialReader(threading.Thread):
    """Reads the first ttyACM/ttyUSB port, logs to a file and hands lines to the UI.

    Reconnects on its own, so a reflash that re-enumerates the port just shows a gap.
    """

    def __init__(self, baud, logdir, on_line):
        super().__init__(daemon=True)
        self.baud, self.logdir, self.on_line = baud, Path(logdir), on_line
        self.port = ""  # not None, so the first look with nothing plugged in still says so
        self.warned = False
        self.last_error = None

    def run(self):
        while True:
            ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
            if not ports:
                if self.port is not None:  # said once, not once a second until something turns up
                    self.on_line("(no serial port)\n", "No port")
                    self.port = None
                time.sleep(1)
                continue
            self.port = ports[0]
            try:
                self._pump()
            except (serial.SerialException, OSError) as exc:
                if str(exc) != self.last_error:  # a port stuck failing says so once, not every second
                    self.last_error = str(exc)
                    self.on_line(f"(port gone: {exc})\n", "Disconnected")
                if "Permission denied" in str(exc) and not self.warned:
                    self.warned = True  # the usual first-run trip-up on a distribution that is not NixOS
                    self.on_line(f"(reading {self.port} needs the dialout group: "
                                f"sudo usermod -aG dialout $USER, then log out and in)\n", None)
                time.sleep(1)

    def _pump(self):
        self.logdir.mkdir(parents=True, exist_ok=True)
        logfile = self.logdir / f"{datetime.now():%Y%m%d-%H%M%S}-{Path(self.port).name}.log"
        with serial.Serial(self.port, self.baud, timeout=0.2) as ser, logfile.open("ab") as log:
            self.on_line("", f"{self.port} @ {self.baud} → {logfile.name}")
            while True:
                data = ser.read(4096)
                if data:
                    self.last_error = None
                    log.write(data)
                    log.flush()
                    self.on_line(data.decode("utf-8", "replace"), None)


# Cold to hot, for where a value sits within its own range: blue at its low, red at its high.
HEAT_STOPS = [(0.0, (0.30, 0.55, 1.00)), (0.35, (0.25, 0.85, 0.85)),
              (0.65, (0.95, 0.85, 0.30)), (1.0, (1.00, 0.35, 0.30))]


def heat(t):
    """The HEAT_STOPS colour at `t` in 0..1."""
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(HEAT_STOPS, HEAT_STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))
    return HEAT_STOPS[-1][1]


def relative(value, lo, hi):
    return 0.5 if value is None or lo is None or hi is None or hi <= lo else (value - lo) / (hi - lo)


class MultiGraph:
    """Every tracked field in one chart, each line labelled where it ends.

    x is the last CHART_WINDOW seconds. Lines are grouped into bands by unit, and one band is
    one axis shared by its lines, so a curve's height reads as a real value against the ticks in
    the left gutter. This is the chart from the original desktop app, drawing included -- it
    still uses Cairo, just onto an off-screen surface instead of a GTK window, and `render()`
    hands back a plain image for ChartView to show through Kitty graphics or Sixel.
    """

    def __init__(self):
        self.series = {}
        self.t0 = None
        self.visible = set()  # the keys worth a label and a row, chosen by the window
        self.events = collections.deque()  # one-off lines, drawn on their own timeline
        self.now = self.span = 0
        self.scales = {}  # band -> the lo/hi/log axis its lines are drawn against
        self.titles = {}  # band -> what is written in its top left corner
        self.plot_l, self.plot_r = AXIS_GUTTER, 0
        self.num_column = 0

    def add_series(self, key, color, label, polarity, unit, pattern):
        band, factor = band_of(label, unit)
        self.series[key] = {"key": key, "t": collections.deque(), "v": collections.deque(), "color": color,
                            "label": label, "polarity": polarity, "group": band, "band_key": None,
                            "factor": factor, "unit": unit, "pattern": pattern,
                            "value": None, "lo": None, "hi": None, "ever_lo": None, "ever_hi": None,
                            "worst": None, "best": None, "flag": 0.0,
                            "y": None, "label_y": None, "label_vy": 0.0, "band": None}

    def flag(self, key):
        if key in self.series:
            self.series[key]["flag"] = time.time()

    def push(self, key, level, value, lo, hi, ever_lo=None, ever_hi=None):
        series = self.series.get(key)
        if series is None:
            return
        now = time.time()
        # Worst means lowest where high is good, highest where low is good, and the biggest
        # excursion either way when the field has no direction.
        polarity = series["polarity"]
        badness = (1 - level) if polarity > 0 else level if polarity < 0 else abs(level - 0.5) * 2
        factor = series["factor"]  # everything in a band is stored in that band's base unit
        value, lo, hi = value * factor, lo * factor, hi * factor
        series["ever_lo"] = None if ever_lo is None else ever_lo * factor
        series["ever_hi"] = None if ever_hi is None else ever_hi * factor
        sample = (badness, value, now)
        if series["worst"] is None or badness > series["worst"][0]:
            series["worst"] = sample
        if series["best"] is None or badness < series["best"][0]:
            series["best"] = sample
        series["value"], series["lo"], series["hi"] = value, lo, hi
        self.t0 = now if self.t0 is None else self.t0
        series["t"].append(now)
        series["v"].append(value)
        cutoff = now - CHART_WINDOW
        while series["t"] and series["t"][0] < cutoff:  # what leaves the window is forgotten
            series["t"].popleft()
            series["v"].popleft()

    def set_visible(self, keys):
        self.visible = set(keys)

    def add_event(self, text, color):
        """A one-off line worth marking on the rail: a state change, a warning, an error."""
        now = time.time()
        self.events.append((now, text, color))
        while self.events and self.events[0][0] < now - CHART_WINDOW:
            self.events.popleft()
        self.t0 = now if self.t0 is None else self.t0

    def worth_listing(self, limit):
        """The fields worth a row: still reporting, actually moving, most restless first."""
        now = time.time()
        scored = []
        for s in self.series.values():
            if s["lo"] is None or s["hi"] <= s["lo"] or not s["t"]:
                continue  # never seen two different values, so there is nothing to compare
            if s["t"][-1] < now - CHART_WINDOW:
                continue  # gone quiet, and its line has already slid off the chart
            low, high = min(s["v"]), max(s["v"])
            scale = max(abs(high), abs(low), 1e-9)
            movement = (high - low) / scale  # what it does on the chart, not the whole session
            if movement < (0.35 if s["key"].endswith("#rate") else 0.005):
                continue
            recent = now - s["flag"] < OUTLIER_TTL
            scored.append((movement + (10 if recent else 0), s))
        # Round-robin over the log lines they came from, so one chatty message cannot fill the
        # table with variations on itself before other messages get a row at all.
        buckets = {}
        for score, s in sorted(scored, reverse=True, key=lambda pair: pair[0]):
            buckets.setdefault(s["pattern"], []).append(s)
        chosen = []
        while len(chosen) < limit and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(chosen) < limit:
                    chosen.append(bucket.pop(0))
        return chosen

    def step_labels(self, dt):
        """Labels are beads on a thread: pulled to their line's height, pushed off each other."""
        bands = {}
        for series in self.series.values():
            if (series["key"] in self.visible and series["y"] is not None
                    and series["label_y"] is not None and series["band"] is not None):
                bands.setdefault(series["band"], []).append(series)
        for band, beads in bands.items():
            beads.sort(key=lambda s: s["label_y"])
            for a, b in zip(beads, beads[1:]):
                overlap = LABEL_GAP - (b["label_y"] - a["label_y"])
                if overlap > 0:
                    a["label_vy"] -= LABEL_PUSH * overlap * dt / LABEL_GAP
                    b["label_vy"] += LABEL_PUSH * overlap * dt / LABEL_GAP
            top, height = band
            for bead in beads:
                bead["label_vy"] += (bead["y"] - bead["label_y"]) * LABEL_SPRING * dt
                bead["label_vy"] *= max(0.0, 1 - LABEL_DAMPING * dt)
                bead["label_y"] += bead["label_vy"] * dt
            # The spring alone lets labels pile up against a band edge, so separate them for real.
            beads.sort(key=lambda s: s["label_y"])
            placed = spread([b["label_y"] for b in beads], LABEL_GAP, top + 10, top + height - 6)
            for bead, y in zip(beads, placed):
                if abs(y - bead["label_y"]) > 0.05:
                    bead["label_vy"] *= 0.5
                bead["label_y"] = y

    def _x(self, t):
        """Time to x, with now at the right edge: samples drift left and off the chart."""
        span = self.plot_r - self.plot_l
        return self.plot_r - 1 - (self.now - t) / self.span * (span - 2)

    def _scale(self, band, members):
        """One axis for a band: the union of its lines, logarithmic when they span decades."""
        lo = min(min(s["v"]) for s in members)
        hi = max(max(s["v"]) for s in members)
        tops = [top for top in (max(s["v"]) for s in members) if top > 0]
        positive = [v for s in members for v in s["v"] if v > 0]
        if band != "percent" and lo >= 0 and tops and (
                max(tops) / min(tops) > 50  # lines that live on scales this far apart
                or hi / min(positive) > 1000):  # or one line that alone covers three decades
            return {"lo": max(hi / 1e4, min(positive)), "hi": hi, "log": True}  # four decades at most
        if hi <= lo:
            hi = lo + 1
        if 0 < lo < hi * 0.25:
            lo = 0.0  # a zero baseline where it costs almost nothing, so heights compare
        return {"lo": lo, "hi": hi + (hi - lo) * 0.08, "log": False}

    def _norm(self, band, value):
        scale = self.scales[band]
        lo, hi, v = scale["lo"], scale["hi"], value
        if scale["log"]:
            lo, hi, v = math.log10(lo), math.log10(hi), math.log10(max(value, lo))
        return min(1.0, max(0.0, (v - lo) / (hi - lo))) if hi > lo else 0.5

    def _ticks(self, band):
        """Round values to draw a band's gridlines at."""
        scale = self.scales[band]
        if scale["log"]:
            first = math.floor(math.log10(scale["lo"]))
            decades = [10.0 ** e for e in range(first, math.ceil(math.log10(scale["hi"])) + 1)]
            return [v for v in decades if scale["lo"] <= v <= scale["hi"]]
        span = scale["hi"] - scale["lo"]
        step = 10.0 ** math.floor(math.log10(span / 3)) if span > 0 else 1.0
        for mult in (1, 2, 5, 10):
            if span / (step * mult) <= 5:
                step *= mult
                break
        first = math.ceil(scale["lo"] / step) * step
        return [first + i * step for i in range(6) if first + i * step <= scale["hi"]]

    def _bands(self, h):
        """A band per unit and per thousandfold within it, sized by how many lines it carries.

        Splitting on magnitude is what keeps an axis readable: a line peaking at 30k and one
        peaking at 10 cannot share a scale that either of them can be read off.
        """
        members = {}
        for series in self.series.values():
            if series["key"] in self.visible and len(series["v"]) >= 2:
                decade = int(math.log10(max(abs(series["hi"]), 1)) // 3)
                series["band_key"] = key = (series["group"], decade)
                members.setdefault(key, []).append(series)
        if not members:
            return {}
        self.scales = {key: self._scale(key[0], group) for key, group in members.items()}
        # A band says its unit, and how big its lines are too once a unit runs across several bands.
        split = collections.Counter(band for band, _ in members)
        self.titles = {(band, decade): (BAND_UNIT.get(band) or band)
                       + (f" {fmt_si(1000.0 ** decade)}+" if split[band] > 1 else "")
                       + (" · log" if self.scales[(band, decade)]["log"] else "")
                       for band, decade in members}
        live = sorted(members, key=lambda key: (BAND_ORDER.index(key[0]) if key[0] in BAND_ORDER
                                                else len(BAND_ORDER), key[1]))
        # Each band gets a floor, then the rest is shared out by how many lines it carries.
        floor = min(BAND_FLOOR, h / len(live))
        spare = h - floor * len(live)
        total = sum(len(members[band]) for band in live)
        bands, top = {}, 0.0
        for band in live:
            height = floor + spare * len(members[band]) / total
            bands[band] = (top, height)
            top += height
        return bands

    def render(self, w, h, scale=1.0):
        """Draw the current state at exactly `w`x`h` pixels and hand back a PIL image.

        `scale` enlarges everything -- text, lines, gutters -- while the surface keeps every
        pixel, so a big-font terminal gets a chart in proportion to its own text, still sharp.
        """
        pw, ph = max(1, int(w)), max(1, int(h))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pw, ph)
        cr = cairo.Context(surface)
        cr.scale(scale, scale)
        w, h = pw / scale, ph / scale
        cr.set_source_rgb(0.086, 0.09, 0.106)  # opaque backdrop -- the terminal behind it never shows
        cr.paint()
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        # The strip on the right, one row of numbers per line: columns just as wide as the widest
        # number now in them, so the chart keeps every pixel the numbers do not need.
        cr.set_font_size(10)
        texts = ["ever", "min", "now", "max"] + [fmt_si(s[k]) for s in self.series.values()
                                                 if s["key"] in self.visible
                                                 for k in ("ever_lo", "lo", "value", "hi", "ever_hi")]
        self.num_column = max(cr.text_extents(t).width for t in texts) + NUM_GAP
        table = 5 * self.num_column + 22
        self.plot_l, self.plot_r = AXIS_GUTTER, w - table
        if self.t0 is None:
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.set_font_size(12)
            text = "waiting for telemetry…"
            extents = cr.text_extents(text)
            cr.move_to((w - extents.width) / 2, h / 2)
            cr.show_text(text)
            return cairo_to_pil(surface, pw, ph)
        self.now = time.time()
        self.span = CHART_WINDOW
        event_h = EVENT_BAND if any(when >= self.now - self.span for when, _, _ in self.events) else 0
        bands = self._bands(h - event_h - TIME_AXIS)
        if event_h:
            self._draw_events(cr, event_h)
        for i, (band, (top, height)) in enumerate(sorted(bands.items(), key=lambda kv: kv[1][0])):
            top += event_h
            cr.set_source_rgba(1, 1, 1, 0.05 if i % 2 else 0.03)  # alternating, so bands separate
            cr.rectangle(0, top, self.plot_r, height)
            cr.fill()
            cr.set_font_size(9)
            for value in self._ticks(band):
                y = top + height - 3 - self._norm(band, value) * (height - 6)
                cr.set_source_rgba(1, 1, 1, 0.07)
                cr.set_line_width(1)
                cr.move_to(self.plot_l, y)
                cr.line_to(self.plot_r, y)
                cr.stroke()
                if y > top + 18:
                    text = fmt_si(value)
                    cr.set_source_rgba(1, 1, 1, 0.4)
                    cr.move_to(self.plot_l - 4 - cr.text_extents(text).width, y + 3)
                    cr.show_text(text)
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.move_to(4, top + 11)
            cr.show_text(self.titles[band])
        self._draw_time_axis(cr, h)

        cr.set_font_size(11)
        labels = []
        for key, series in self.series.items():
            if key not in self.visible or len(series["v"]) < 2 or series.get("band_key") not in bands:
                continue  # not listed in the table, empty, or its band is gone
            band = series["band_key"]
            top, height = bands[band]
            top += event_h
            series["band"] = (top, height)
            rgb = tuple(int(series["color"][i:i + 2], 16) / 255 for i in (1, 3, 5))
            points = [(self._x(t), top + height - 3 - self._norm(band, v) * (height - 6))
                      for t, v in zip(series["t"], series["v"])]
            # Hot/cold along the line itself: a gradient from the height of its lowest sample in
            # the window (cold) to its highest (hot), so every stretch is coloured by where it
            # stands against the rest of the line.
            low, high = min(series["v"]), max(series["v"])
            y_low = top + height - 3 - self._norm(band, low) * (height - 6)
            y_high = top + height - 3 - self._norm(band, high) * (height - 6)
            if y_low - y_high > 1:
                gradient = cairo.LinearGradient(0, y_low, 0, y_high)
                for stop, color in HEAT_STOPS:
                    gradient.add_color_stop_rgb(stop, *color)
                cr.set_source(gradient)
            else:
                cr.set_source_rgb(*heat(0.5))
            cr.set_line_width(1.8)
            spline(cr, points)
            cr.stroke()
            for kind, fill in (("worst", True), ("best", False)):
                mark = series[kind]
                if mark and len(series["v"]) > 4 and mark[2] > self.now - self.span:
                    cr.set_source_rgb(*heat(relative(mark[1], low, high)))
                    cr.arc(self._x(mark[2]), top + height - 3 - self._norm(band, mark[1]) * (height - 6),
                           2.5, 0, 6.2832)
                    cr.fill() if fill else cr.stroke()
            series["y"] = points[-1][1]
            if series["label_y"] is None:
                series["label_y"] = series["y"]

            flag = "! " if time.time() - series["flag"] < 10 else ""
            text = f"{flag}{series['label']}"  # the numbers live in the table
            labels.append({"key": key, "series": series, "rgb": rgb, "text": text,
                           "width": cr.text_extents(text).width, "y": series["label_y"],
                           "tip": (points[-1][0], series["y"])})

        for label in labels:
            rgb, ly, width = label["rgb"], label["y"], label["width"]
            tip_x, tip_y = label["tip"]
            tx = max(self.plot_l + 4, tip_x - 8 - width)  # just left of the newest sample
            if abs(ly - tip_y) > 1:
                cr.set_source_rgba(*rgb, 0.4)
                cr.set_line_width(1)
                cr.move_to(tip_x, tip_y)
                cr.line_to(tx + width + 4, ly + 2)
                cr.stroke()
            cr.set_source_rgba(0, 0, 0, 0.45)  # a dark rounded plate keeps the name readable
            rounded_rect(cr, tx - 5, ly - 6, width + 10, LABEL_PLATE, 5)
            cr.fill()
            cr.set_source_rgb(*rgb)
            cr.set_font_size(11)
            cr.move_to(tx, ly + 6)
            cr.show_text(label["text"])
        self._draw_table(cr, w, h, labels)
        return cairo_to_pil(surface, pw, ph)

    def _draw_table(self, cr, w, h, labels):
        """The strip on the right: one row per line, in its colour, never two on the same height."""
        cr.set_font_size(10)
        cr.set_source_rgba(1, 1, 1, 0.35)
        # Right edges of: all-time min, this session's min, now, its max, all-time max.
        columns = [w - 6 - i * self.num_column for i in (4, 3, 2, 1, 0)]
        for heading, right in zip(("ever", "min", "now", "max", "ever"), columns):
            cr.move_to(right - cr.text_extents(heading).width, 12)
            cr.show_text(heading)
        labels = sorted(labels, key=lambda label: label["y"])
        swatch = self.plot_r + 6
        for label, ry in zip(labels, spread([label["y"] for label in labels], NUM_ROW, 24, h - 6)):
            series, rgb = label["series"], label["rgb"]
            if abs(ry - label["y"]) > 2:  # a leader back to the label, once the row has moved off it
                cr.set_source_rgba(*rgb, 0.25)
                cr.set_line_width(1)
                cr.move_to(self.plot_r - 2, label["y"] + 1)
                cr.line_to(swatch, ry + 1)
                cr.stroke()
            cr.set_source_rgba(*rgb, 0.9)  # the row carries the line's colour, so the two pair up
            cr.set_line_width(2.5)
            cr.move_to(swatch, ry + 1)
            cr.line_to(swatch + 8, ry + 1)
            cr.stroke()
            # min is as cold and max as hot as it gets; now is coloured by where it sits between.
            # The all-time columns only speak up where earlier runs went further than this one.
            now_heat = relative(series["value"], series["lo"], series["hi"])
            ever_lo = series["ever_lo"] if fmt_si(series["ever_lo"]) != fmt_si(series["lo"]) else None
            ever_hi = series["ever_hi"] if fmt_si(series["ever_hi"]) != fmt_si(series["hi"]) else None
            for value, right, level, alpha in zip(
                    (ever_lo, series["lo"], series["value"], series["hi"], ever_hi), columns,
                    (0.0, 0.0, now_heat, 1.0, 1.0), (0.42, 0.6, 1, 0.6, 0.42)):
                if value is None:
                    continue
                number = fmt_si(value)
                cr.set_source_rgba(*heat(level), alpha)
                cr.move_to(right - cr.text_extents(number).width, ry + 4)
                cr.show_text(number)

    def _draw_time_axis(self, cr, h):
        """Clock ticks along the bottom, so a spike on the chart can be found in the log."""
        cr.set_font_size(9)
        step = 30
        marks = [t for t in (math.ceil((self.now - self.span) / step) * step + i * step
                             for i in range(int(self.span / step) + 2)) if t <= self.now]
        for t in marks:
            x = self._x(t)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.set_line_width(1)
            cr.move_to(x, 0)
            cr.line_to(x, h - TIME_AXIS)
            cr.stroke()
            text = datetime.fromtimestamp(t).strftime("%H:%M:%S")
            cr.set_source_rgba(1, 1, 1, 0.4)
            cr.move_to(min(x + 3, self.plot_r - cr.text_extents(text).width), h - 4)
            cr.show_text(text)

    def _draw_events(self, cr, height):
        """A stem timeline of its own, so warnings do not stripe the metric chart."""
        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.rectangle(0, 0, self.plot_r, height)
        cr.fill()
        cr.set_font_size(9)
        cr.set_source_rgba(1, 1, 1, 0.3)
        cr.move_to(4, 12)
        cr.show_text("events")
        base = height - 6
        cap = 42  # dots sit here; names live in the rows above
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1)
        cr.move_to(0, base)
        cr.line_to(self.plot_r, base)
        cr.stroke()
        rows = [[] for _ in range(3)]  # x ranges already spoken for, per row of names
        for when, text, color in self.events:
            if when < self.now - self.span:
                continue
            x = self._x(when)
            rgb = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
            cr.set_source_rgb(*rgb)
            cr.set_line_width(1.2)
            cr.move_to(x, base)
            cr.line_to(x, cap)
            cr.stroke()
            cr.arc(x, cap, 2.6, 0, 6.2832)
            cr.fill()
            text = text[:26]
            width = cr.text_extents(text).width + 8
            x0 = min(x + 5, self.plot_r - width)
            if x0 < 46:  # leave the band name alone
                continue
            for i, taken in enumerate(rows):
                if all(x0 >= end or x0 + width <= start for start, end in taken):
                    taken.append((x0, x0 + width))
                    cr.move_to(x0, 13 + i * 12)
                    cr.show_text(text)
                    break


if _AutoRenderable is _TGPRenderable:
    class _CameraRenderable(_TGPRenderable):
        """Kitty graphics, sent at the picture's own size and scaled up to the panel by Kitty --
        never shrunk below what the camera delivers, never blown up into more bytes than it has."""

        def _send_image_to_terminal(self, width, height):
            own_w, own_h = self._image_data.width, self._image_data.height
            if own_w < width and own_h < height:
                width, height = own_w, own_h
            super()._send_image_to_terminal(width, height)
else:
    _CameraRenderable = _AutoRenderable  # Sixel and half-cells draw what they are given


class CameraView(AutoImage, Renderable=_CameraRenderable):
    """The camera picture, drawn at any angle and grown to fill its box.

    AutoImage draws with the terminal's real pixels -- Kitty's graphics protocol or Sixel --
    when the terminal on the viewing end supports it (asked for directly, so this is the same
    over SSH as sitting at the machine), and falls back to coloured half-cells otherwise.
    """

    def __init__(self, capture, **kwargs):
        super().__init__(None, **kwargs)
        self.capture = capture
        self.angle = 0.0
        self.shown = None  # the frame on screen, so a tick with no new frame sends nothing

    def set_capture(self, capture):
        self.capture = capture
        if capture is None:
            self.image = None

    def set_angle(self, angle):
        self.angle = angle % 360

    def redraw(self):
        frame = self.capture.latest() if self.capture else None
        if not frame or frame is self.shown:
            return
        self.shown = frame
        fw, fh, data = frame
        img = Image.frombytes("RGB", (fw, fh), data)
        # The box follows the nearest quarter turn -- that is the only turn that changes which
        # way the picture stands, so any other angle keeps the native box and crops into it.
        box_w, box_h = (fw, fh) if round(self.angle / 90) % 2 == 0 else (fh, fw)
        radians = math.radians(self.angle)
        cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
        # Grown until the rotated picture covers the whole box, so a turn crops the corners
        # instead of shrinking the whole picture into a diamond of empty space.
        scale = max((box_w * cos + box_h * sin) / fw, (box_w * sin + box_h * cos) / fh)
        # Only ever shrunk to fit a panel smaller than the camera's own picture; a bigger panel is
        # filled by Kitty scaling the picture up, so no detail is lost and no bytes are wasted.
        cell_w, cell_h = get_cell_size()
        shrink = min(1.0, self.size.height * cell_h / box_h) if self.size.height else 1.0
        box_w, box_h = max(1, round(box_w * shrink)), max(1, round(box_h * shrink))
        scale *= shrink
        img = img.resize((max(1, round(fw * scale)), max(1, round(fh * scale))), Image.BILINEAR)
        if self.angle:
            img = img.rotate(-self.angle, resample=Image.BILINEAR, expand=True)
        left = (img.width - box_w) // 2
        top = (img.height - box_h) // 2
        self.image = img.crop((left, top, left + box_w, top + box_h))
        # Beside the panels the band's height is fixed, so the width follows the picture's
        # shape: just wide enough, with the panels taking whatever is left.
        cols = max(1, round(self.size.height * cell_h * box_w / box_h / cell_w))
        if self.parent.styles.width != cols + 2:  # + the frame's two border columns
            self.parent.styles.width = cols + 2


class ChartView(AutoImage, Renderable=_AutoRenderable):
    """The chart as one image: Cairo draws it at exactly the panel's pixel size, and AutoImage
    shows it through Kitty graphics or Sixel where the terminal supports it.
    """

    def __init__(self, model, **kwargs):
        super().__init__(None, **kwargs)
        self.model = model
        self.last_frame = None

    def redraw(self):
        w, h = self.size.width, self.size.height
        if w < 4 or h < 4:
            return
        cell_w, cell_h = get_cell_size()
        now = time.monotonic()
        dt = min(now - self.last_frame, 0.5) if self.last_frame else 0
        self.last_frame = now
        if dt:
            self.model.step_labels(dt)
        # Drawn in proportion to the terminal's own text: the Cairo sizes were picked for a
        # CHART_TEXT_CELL-high line, so a taller cell scales the whole chart up with it.
        self.image = self.model.render(w * cell_w, h * cell_h, max(1.0, cell_h / CHART_TEXT_CELL))


class EyeBuddyApp(App):
    TITLE = "EyeBuddy"
    CSS = """
    Screen { background: $surface; }
    * { scrollbar-size: 0 0; }
    #status { height: 1; background: $panel; color: $text; padding: 0 1; }
    #main { height: 1fr; }
    #bottom { height: 36; }
    #chart { height: 1fr; border: round $boost; }  /* yields to the camera band when short */
    #log { height: 1fr; border: round $boost; }
    /* Along the bottom: keys, the panels, the camera. When room runs short the panels give it
       up -- the key list keeps its width and the camera is never squeezed. */
    #sidebar { width: 1fr; min-width: 0; }
    #camera-wrap { width: 40; border: round $boost; }
    #camera { height: 1fr; width: 1fr; }
    #build { height: auto; border: round $boost; }
    #settings { height: auto; padding: 0 1; }
    #swipe { height: auto; padding: 0 1; border: round $boost; }
    #build-info { height: auto; padding: 0 1; }
    #progress { height: 1; margin: 0 1; }
    /* Each box names the keys that act on it along its bottom edge, instead of one long footer. */
    #keys { width: auto; height: 1fr; padding: 0 1; border: round $boost; }
    #build, #swipe, #log, #outliers, #keys, #camera-wrap { border-title-color: $text; }
    #outliers { height: 1fr; padding: 0 1; border: round $boost; }
    """
    BINDINGS = [
        Binding("s", "toggle_mcu", "Halt/Resume"),
        Binding("r", "reset_mcu", "Reset"),
        Binding("h", "reset_halt", "Reset+halt"),
        Binding("q", "rotate_quarter", "Turn 90°"),
        Binding("comma", "rotate_dec", "Turn -1°", show=False),
        Binding("full_stop", "rotate_inc", "Turn +1°", show=False),
        Binding("z", "next_size", "Cam size"),
        Binding("x", "next_fps", "Cam fps"),
        Binding("k", "toggle_camera", "Cam on/off"),
        Binding("d", "toggle_build_type", "Debug/Release"),
        Binding("e", "toggle_build_env", "Staging/Prod"),
        Binding("b", "build", "Build"),
        Binding("f", "flash", "Flash"),
        Binding("a", "build_flash", "Build+flash"),
        Binding("o", "open_log", "Open log"),
        Binding("c", "clear_log", "Clear log"),
        Binding("g", "follow_end", "Follow end"),
        Binding("v", "toggle_pretty", "Raw/pretty"),
        Binding("w", "cycle_swipe", "Swipe"),
        Binding("i", "cycle_swipe_interval", "Swipe interval", show=False),
        Binding("question_mark", "toggle_keys", "Minimise keys"),
    ]

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.settings = read_json(SETTINGS) or {}
        self.angle = self.settings.get("rotation", args.rotate) % 360
        self.modes = camera_modes(args.device) if not args.no_camera else CAMERA_SIZES
        self.camera_size = self.settings.get("size", self.modes[0][0])
        self.camera_fps = self.settings.get("camera_fps", 30)
        self.camera_capture = None
        self.camera_error = None
        self.camera_started = 0.0
        self.camera_enabled = (not args.no_camera and Path(args.device).exists()
                               and shutil.which("ffmpeg") is not None)
        self.build_type = self.settings.get("build_type", "debug")
        self.build_env = self.settings.get("build_env", "production")
        self.job_active = False
        self.stlink = shutil.which("noctalia") is not None  # the ST-Link backend, absent elsewhere
        self.mcu_state = None
        self.mcu_text = "No ST-Link" if not self.stlink else "…"
        self.serial_state = ""
        self.pretty = self.settings.get("pretty", True)
        self.keys_compact = self.settings.get("keys_compact", False)
        self.swipe_axis = None  # never on at start: a phone swiped on its own is a surprise
        self.swipe_interval = self.settings.get("swipe_interval", 2)
        self.ranges = self.settings.get("ranges", {})  # pattern -> [[min, max], ...]
        self.ranges_dirty = False
        self.stats = {}  # (pattern, field index) -> [n, mean, m2, last_warning], for outliers
        self.rows = {}  # pattern -> row state (numeric fields, their ranges, the arrival rate)
        self.seen = {}  # pattern -> occurrences before it earns a place on the chart
        self.module_hue = {}  # module -> its hue on the chart, handed out as modules turn up
        self.chart = MultiGraph()
        self.lines = collections.deque(maxlen=5000)  # complete raw backlog, replayed on raw/pretty
        self.partial = ""
        self.recent = collections.OrderedDict()  # pattern -> pending repeat count, for the log
        self.outliers = collections.deque(maxlen=OUTLIER_SHOWN)

    # --- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        # Chart and log get the full width; the panels share the bottom band with the camera,
        # which takes only the width its aspect ratio needs and leaves the rest to them.
        with Vertical(id="main"):
            yield ChartView(self.chart, id="chart")
            yield RichLog(id="log", max_lines=5000, wrap=True, highlight=False, markup=False)
        with Horizontal(id="bottom"):
            yield Static(self._keys_text(), id="keys")
            with Vertical(id="sidebar"):
                with Vertical(id="build"):
                    yield Static(id="settings")
                    yield Static(id="build-info")
                    yield ProgressBar(id="progress", total=100, show_eta=False)
                yield Static(id="swipe")
                yield Static(id="outliers")
            with Vertical(id="camera-wrap"):
                yield CameraView(self.camera_capture, id="camera")

    # Every key in one list at the left of the bottom band, grouped by what it acts on.
    # Each key with a Nerd Font icon (Kitty ships the symbols), all the minimised list (?) shows
    # beside the letter. Plain glyphs in the text colour, not emoji.
    KEYS = [
        ("ST-Link", [("s", "\uf04c", "halt / resume"), ("r", "\uf021", "reset"), ("h", "\uf04d", "reset + halt")]),
        ("Build", [("d", "\uf188", "debug / release"), ("e", "\uf0ac", "staging / prod"), ("b", "\uf0ad", "build"),
                   ("f", "\uf0e7", "flash"), ("a", "\uf135", "build + flash")]),
        ("Camera", [("q", "\uf01e", "turn 90°"), (",.", "\uf14e", "turn ∓1°"), ("z", "\uf00e", "size"),
                    ("x", "\uf008", "frame rate"), ("k", "\uf030", "on / off")]),
        ("Log", [("v", "\uf06e", "raw / pretty"), ("c", "\uf12d", "clear"), ("g", "\uf103", "follow end"),
                 ("o", "\uf0f6", "open file")]),
        ("Phone", [("w", "\uf25a", "swipe direction"), ("i", "\uf017", "swipe interval")]),
        ("App", [("?", "\uf11c", "minimise keys"), ("^p", "\uf120", "palette"), ("^q", "\uf011", "quit")]),
    ]

    def _keys_text(self):
        text = Text()
        for group, keys in self.KEYS:
            if text:
                text.append("\n")
            if not self.keys_compact:
                text.append(group + "\n", style="dim")
            for key, icon, what in keys:
                text.append(icon + " ", style="#7f848e")
                text.append(key.ljust(2 if self.keys_compact else 3), style="bold #e5c07b")
                if not self.keys_compact:
                    text.append(what)
                text.append("\n")
        text.rstrip()
        return text

    def action_toggle_keys(self):
        self.keys_compact = not self.keys_compact
        save_settings(keys_compact=self.keys_compact)
        keys = self.query_one("#keys", Static)
        keys.update(self._keys_text())

    def on_mount(self):
        self.log_view = self.query_one("#log", RichLog)
        self.camera_view = self.query_one("#camera", CameraView)
        self.camera_view.set_angle(self.angle)
        self.chart_view = self.query_one("#chart", ChartView)
        titles = {"#build": "Build", "#swipe": "Phone touch pad", "#log": "Log",
                  "#outliers": "Outliers", "#keys": "Keys"}
        for selector, title in titles.items():
            self.query_one(selector).border_title = title
        self._refresh_settings_panel()
        self._update_status()
        if self.camera_enabled:
            self._restart_camera()
        elif not self.args.no_camera:
            why = "ffmpeg not found" if not shutil.which("ffmpeg") else f"no camera at {self.args.device}"
            self._note(f"No camera: {why}. Press K once it's available.")
        self.serial = SerialReader(self.args.baud, self.args.logdir,
                                   lambda text, status: self.call_from_thread(self._on_serial, text, status))
        self.serial.start()
        self.swiper = AdbSwiper(self.swipe_interval, lambda text: self.call_from_thread(self._note, text))
        self.swiper.start()
        self.camera_timer = self.set_interval(1 / self.camera_fps, self._redraw_camera)
        self.set_interval(1 / CHART_FPS, self._redraw_chart)
        self.set_interval(2.0, self._update_chart_visible)
        self.set_interval(1.0, self._poll_state)
        self.set_interval(0.5, self._poll_job)
        self.set_interval(2.0, self._flush_recent)
        self.set_interval(5.0, self._update_outliers)
        self.set_interval(5.0, self._flush_ranges)

    # --- camera -----------------------------------------------------------

    def _restart_camera(self):
        if self.camera_capture:
            self.camera_capture.stop()
        self.camera_capture = CameraCapture(self.args.device, self.camera_size)
        self.camera_capture.start()
        self.camera_started = time.monotonic()
        self.camera_view.set_capture(self.camera_capture)

    def action_next_fps(self):
        index = CAMERA_FPS.index(self.camera_fps) + 1 if self.camera_fps in CAMERA_FPS else 0
        self.camera_fps = CAMERA_FPS[index % len(CAMERA_FPS)]
        save_settings(camera_fps=self.camera_fps)
        self.camera_timer.stop()
        self.camera_timer = self.set_interval(1 / self.camera_fps, self._redraw_camera)
        self._refresh_settings_panel()

    def _redraw_camera(self):
        if not self.camera_enabled:
            return
        capture = self.camera_capture
        if capture and not capture.is_alive() and capture.error:
            # Said once per cause, then tried again every few seconds -- a camera held by another
            # program comes back by itself once that program lets go.
            if capture.error != self.camera_error:
                self.camera_error = capture.error
                self._note(f"Camera: {capture.error} -- retrying.")
                self._refresh_settings_panel()
            if time.monotonic() - self.camera_started > 3:
                self._restart_camera()
            return
        if self.camera_error and capture and capture.latest():
            self.camera_error = None
            self._refresh_settings_panel()
        self.camera_view.redraw()

    def action_toggle_camera(self):
        if self.camera_capture:
            self.camera_capture.stop()
            self.camera_capture = None
            self.camera_view.set_capture(None)
            self.camera_enabled = False
        elif shutil.which("ffmpeg") and Path(self.args.device).exists():
            self.camera_enabled = True
            self._restart_camera()
        else:
            self._note("No camera: ffmpeg or the camera device is missing.")
        self._refresh_settings_panel()

    # --- adb swipes on the phone's touch pad ------------------------------------

    def action_cycle_swipe(self):
        order = [None, "vertical", "horizontal"]
        self.swipe_axis = order[(order.index(self.swipe_axis) + 1) % len(order)]
        if self.swipe_axis and not shutil.which("adb"):
            self.swipe_axis = None
            self._note("No adb on PATH.")
        self.swiper.set(self.swipe_axis, self.swipe_interval)
        self._refresh_settings_panel()

    def action_cycle_swipe_interval(self):
        index = SWIPE_INTERVALS.index(self.swipe_interval) + 1 if self.swipe_interval in SWIPE_INTERVALS else 0
        self.swipe_interval = SWIPE_INTERVALS[index % len(SWIPE_INTERVALS)]
        save_settings(swipe_interval=self.swipe_interval)
        self.swiper.set(self.swipe_axis, self.swipe_interval)
        self._refresh_settings_panel()

    def action_next_size(self):
        if not self.camera_enabled:
            return
        sizes = [size for size, _ in self.modes]
        index = (sizes.index(self.camera_size) + 1) if self.camera_size in sizes else 0
        self.camera_size = sizes[index % len(sizes)]
        save_settings(size=self.camera_size)
        self._restart_camera()
        self._refresh_settings_panel()

    def action_rotate_quarter(self):
        self._rotate(90)

    def action_rotate_dec(self):
        self._rotate(-1)

    def action_rotate_inc(self):
        self._rotate(1)

    def _rotate(self, delta):
        self.angle = (self.angle + delta) % 360
        self.camera_view.set_angle(self.angle)
        save_settings(rotation=self.angle)
        self._refresh_settings_panel()

    # --- chart ------------------------------------------------------

    def _update_chart_visible(self):
        self.chart.set_visible(s["key"] for s in self.chart.worth_listing(RANGE_ROWS))

    def _redraw_chart(self):
        self.chart_view.redraw()

    # --- serial / log ---------------------------------------------------------

    def _on_serial(self, text, status):
        if status:
            self.serial_state = status
            self._update_status()
        self.partial += text
        *complete, self.partial = self.partial.split("\n")
        for line in complete:
            line += "\n"
            self.lines.append(line)
            self._show_line(line)
        self._update_status()

    def _show_line(self, line):
        if self.pretty and self._to_table(line):
            return
        if self.pretty:
            self._render_pretty(line)
        else:
            self.log_view.write(hanging_at_message(Text.from_ansi(line.rstrip("\n"))))

    def _to_table(self, raw):
        """Route a repeating telemetry line to the chart. Returns False for lines the log keeps."""
        plain = ANSI.sub("", raw)
        m = LOG_LINE.match(plain.rstrip("\r\n"))
        if not m:
            return False
        level, module, message = m.group(2).upper(), m.group(3), m.group(5)
        if level in ("WARN", "WARNING", "ERROR", "FATAL"):
            self.chart.add_event(f"{module} {message}", LEVELS.get(level, ("", "#e06c75"))[1])
            return False
        key = pattern_of(plain)
        if key not in self.rows:
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] < 2:
                # Said once so far. Text without numbers is a state change worth marking; a line
                # carrying numbers is probably telemetry that will earn its own line shortly.
                if not NUMBERS.search(message):
                    self.chart.add_event(f"{module} {message}", self._module_color(raw, module))
                return False
            self.rows[key] = self._make_row(key, m.groups())
        self._update_row(self.rows[key], m.groups())
        return True

    def _series_color(self, module, index):
        """Hue per module, lightness per field, so one log line's fields read as a family."""
        hue = self.module_hue.setdefault(module, len(self.module_hue) * 0.618 % 1.0)
        r, g, b = colorsys.hls_to_rgb(hue, (0.70, 0.55, 0.80, 0.46)[index % 4], 0.62)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

    def _make_row(self, key, fields):
        """Register one series per numeric field. The chart labels them, so nothing else to build."""
        ts, level, module, location, message = fields
        fields_out = []
        unit = ""
        at = 0
        for i, num in enumerate(NUMBERS.finditer(message)):
            text = field_label(message[at:num.start()], unit)
            unit = field_unit(message[num.end():])
            series = f"{key}#{i}"
            self.chart.add_series(series, self._series_color(module, i),
                                  f"{module} {text}" if text else message[:24], polarity_of(text, unit), unit, key)
            fields_out.append({"series": series, "label": text, "unit": unit})
            at = num.end()
        # How often this line arrives, so a stream that reports steady numbers still shows up.
        rate_series = f"{key}#rate"
        self.chart.add_series(rate_series, self._series_color(module, len(fields_out)),
                              f"{module} {(fields_out[0]['label'] if fields_out else message)[:18]} rate", 0, "/s", key)
        fields_out.append({"series": rate_series, "label": "rate", "unit": "/s"})
        ranges = self.ranges.setdefault(key, [[None, None] for _ in fields_out])
        while len(ranges) < len(fields_out):
            ranges.append([None, None])
        # `ranges` is every run's (saved); `session` only this one's -- the table shows both.
        return {"fields": fields_out, "ranges": ranges, "session": [[None, None] for _ in fields_out],
                "key": key, "module": module, "last_ts": None,
                "message": message, "numeric": len(fields_out) > 1, "rate": None}

    def _update_row(self, state, fields):
        ts, level, module, location, message = fields
        now = ts_seconds(ts)
        previous, state["last_ts"] = state["last_ts"], now
        rate_field = state["fields"][-1]
        if previous is not None and 0 < now - previous < 60:
            # Smoothed, because the jitter between two log lines says nothing on its own.
            rate = 1 / (now - previous)
            state["rate"] = rate if state["rate"] is None else state["rate"] * 0.7 + rate * 0.3
            rate = state["rate"]
            for rng in (state["ranges"][-1], state["session"][-1]):
                rng[0] = rate if rng[0] is None else min(rng[0], rate)
                rng[1] = rate if rng[1] is None else max(rng[1], rate)
            lo, hi = state["session"][-1]
            self.chart.push(rate_field["series"], (rate - lo) / (hi - lo) if hi > lo else 0.5, rate, lo, hi,
                            *state["ranges"][-1])
        if not state["numeric"]:
            return
        for i, (field, num, rng, ses) in enumerate(zip(state["fields"], NUMBERS.finditer(message),
                                                       state["ranges"], state["session"])):
            v = float(num.group())
            if rng[0] is None or v < rng[0] or rng[1] is None or v > rng[1]:
                rng[0] = v if rng[0] is None else min(rng[0], v)
                rng[1] = v if rng[1] is None else max(rng[1], v)
                self.ranges_dirty = True
            ses[0] = v if ses[0] is None else min(ses[0], v)
            ses[1] = v if ses[1] is None else max(ses[1], v)
            lo, hi = ses
            if self._check_outlier(state["key"], i, v, field["label"], module):
                self.chart.flag(field["series"])
            self.chart.push(field["series"], (v - lo) / (hi - lo) if hi > lo else 0.5, v, lo, hi, *rng)

    def _check_outlier(self, key, index, value, label, module):
        """Welford mean/variance per field; a value far outside it is worth saying out loud."""
        st = self.stats.setdefault((key, index), [0, 0.0, 0.0, 0.0])
        st[0] += 1
        delta = value - st[1]
        st[1] += delta / st[0]
        st[2] += delta * (value - st[1])
        if st[0] < 30:
            return False
        sigma = (st[2] / (st[0] - 1)) ** 0.5
        if sigma <= 0 or abs(value - st[1]) < OUTLIER_SIGMA * sigma:
            return False
        now = time.time()
        if now - st[3] < OUTLIER_QUIET:
            return True
        st[3] = now
        self._warn(f"{module} {label}: {value:g} (mean {st[1]:.3g} ±{sigma:.2g})")
        return True

    def _flush_ranges(self):
        if self.ranges_dirty:
            self.ranges_dirty = False
            save_settings(ranges=dict(list(self.ranges.items())[-200:]))  # bounded, not unbounded history

    def _update_outliers(self):
        cutoff = time.time() - OUTLIER_TTL
        while self.outliers and self.outliers[0][0] < cutoff:
            self.outliers.popleft()
        panel = self.query_one("#outliers", Static)
        if not self.outliers:
            panel.update("")
            return
        grid = Table.grid()
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        for when, msg in reversed(self.outliers):
            grid.add_row(Text("! ", style=Style(color="#e5c07b", bold=True)), msg)
        panel.update(grid)

    # --- pretty log rendering ------------------------------------------------

    def _module_color(self, raw, module):
        # The firmware colours the module tag itself. Fall back to a stable hash colour.
        m = re.search(r"\x1b\[(?:1;)?(3[0-7]|9[0-7])m" + re.escape(module), raw)
        if m:
            return SGR_COLORS[int(m.group(1))]
        return list(SGR_COLORS.values())[hash(module) % 8]

    def _render_pretty(self, raw):
        plain = ANSI.sub("", raw)
        m = LOG_LINE.match(plain.rstrip("\r\n"))
        if not m:
            self._flush_recent()
            self.log_view.write(Text.from_ansi(raw.rstrip("\n")))
            return
        key = pattern_of(plain)
        if key in self.recent:
            state = self.recent[key]
            state["count"] += 1
            state["fields"] = m.groups()
            state["raw"] = raw
            return  # collapsed; a summary line lands when the window flushes
        if len(self.recent) >= RECENT_LINES:
            self._flush_recent()
        self.recent[key] = {"count": 1, "fields": m.groups(), "raw": raw}
        self._write_pretty(m.groups(), 1, raw)

    def _flush_recent(self):
        for state in self.recent.values():
            if state["count"] > 1:
                self._write_pretty(state["fields"], state["count"], state["raw"])
        self.recent.clear()

    def _write_pretty(self, fields, count, raw):
        ts, level, module, location, message = fields
        glyph, color = LEVELS.get(level.upper(), ("·", "#7f848e"))
        loud = level.upper() in ("WARN", "WARNING", "ERROR", "FATAL")
        head = Text()
        head.append(ts[3:] + " ", style=Style(color="#7f848e"))
        head.append(glyph + " ", style=Style(color=color, bold=True))
        head.append(f"{module:<4} ", style=Style(color=self._module_color(raw, module), bold=True))
        text = Text(message, style=Style(color=color if loud else None))
        if count > 1:
            text.append(f"  ×{count}", style=Style(color="#e5c07b", bold=True))
        text.append("  " + location, style=Style(color="#5c6370"))
        self.log_view.write(hanging(head, text))

    def _note(self, text):
        """A line from the app itself, in among the firmware's own."""
        self._flush_recent()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.write(hanging(Text(f"{stamp} · ", style=Style(color="#56b6c2")),
                                    Text(text, style=Style(color="#56b6c2"))))

    def _warn(self, text):
        self.outliers.append((time.time(), text))
        self._flush_recent()
        stamp = datetime.now().strftime("%H:%M:%S")
        style = Style(color="#e5c07b", bold=True)
        self.log_view.write(hanging(Text(f"{stamp} ▲ ", style=style), Text(text, style=style)))

    # --- view toggles --------------------------------------------------------

    def action_toggle_pretty(self):
        self.pretty = not self.pretty
        save_settings(pretty=self.pretty)
        self._refilter()

    def action_clear_log(self):
        self.lines.clear()
        self.partial = ""
        self.log_view.clear()
        self.recent.clear()
        self._clear_table()
        self._update_status()

    def action_follow_end(self):
        self.log_view.scroll_end(animate=False)

    def _clear_table(self):
        self.rows.clear()
        self.seen.clear()
        self.chart.series.clear()

    def _refilter(self):
        self.log_view.clear()
        self.recent.clear()
        self._clear_table()
        for line in self.lines:
            self._show_line(line)
        self._update_status()

    # --- ST-Link / build -----------------------------------------------------

    def action_toggle_mcu(self):
        send("toggle")

    def action_reset_mcu(self):
        send("reset")

    def action_reset_halt(self):
        send("reset_halt")

    def action_open_log(self):
        send("open_log")

    def action_toggle_build_type(self):
        self.build_type = "release" if self.build_type == "debug" else "debug"
        save_settings(build_type=self.build_type)
        self._refresh_settings_panel()

    def action_toggle_build_env(self):
        self.build_env = "staging" if self.build_env == "production" else "production"
        save_settings(build_env=self.build_env)
        self._refresh_settings_panel()

    def action_build(self):
        self._build("build")

    def action_flash(self):
        self._build("flash")

    def action_build_flash(self):
        self._build("both")

    def _build(self, mode):
        if not self.job_active:
            send("build", mode=mode, build=self.build_type, env=self.build_env)

    def _poll_state(self):
        state = read_json(DATA_DIR / "state.json")
        if state:
            self.mcu_state = state.get("state")
            if not state.get("probe"):
                text = "No ST-Link"
            elif state.get("debugger"):
                text = f"Held by {state['debugger']}"
            else:
                text = f"{state['probe']} · {STATE_TEXT.get(self.mcu_state, self.mcu_state)}"
            self.mcu_text = text
            self._update_status()
            self._refresh_settings_panel()

    def _poll_job(self):
        job = read_json(DATA_DIR / "job.json")
        if job:
            self.job_active = job.get("phase") in ("build", "flash")
            percent = max(0, min(100, int(job.get("percent") or 0)))
            self.query_one("#progress", ProgressBar).update(progress=percent)
            msg = job.get("message", "") + (f" · {percent}%" if self.job_active else "")
            style = "bold $error" if job.get("phase") == "error" else ""
            self.query_one("#build-info", Static).update(Text(msg, style=style))

    # --- status / settings panels --------------------------------------------

    def _update_status(self):
        parts = [self.mcu_text, self.serial_state]
        self.query_one("#status", Static).update("EyeBuddy — " + " · ".join(p for p in parts if p))

    def _refresh_settings_panel(self):
        self.query_one("#settings", Static).update(f"{self.build_type} / {self.build_env}")
        camera = self.query_one("#camera-wrap")
        if self.camera_enabled:
            sensor = dict(self.modes).get(self.camera_size)
            shown = min(self.camera_fps, sensor) if sensor else self.camera_fps
            camera.border_title = (f"Camera: {self.camera_error[:40]}" if self.camera_error
                                   else f"{self.camera_size} · {shown} fps · {self.angle:.0f}°")
        else:
            camera.border_title = "Camera off"
        self.query_one("#swipe", Static).update(
            f"{self.swipe_axis or 'off'} · every {self.swipe_interval:g}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-d", "--device", default="/dev/video0")
    parser.add_argument("-r", "--rotate", type=int, default=270, help="degrees to turn the picture")
    parser.add_argument("-b", "--baud", type=int, default=2000000)
    parser.add_argument("--logdir", default="/tmp/serial-logs")
    parser.add_argument("--no-camera", action="store_true", help="log and chart only, no video")
    parser.add_argument("--install", action="store_true", help="put it on PATH and in the launcher")
    args = parser.parse_args()
    if args.install:
        install_launcher()

    # A dropped SSH session must take the app with it: left running with no terminal it keeps
    # holding the camera and the serial port, and the next start finds both busy.
    app = EyeBuddyApp(args)

    def hang_up(*_):
        if app.camera_capture:
            app.camera_capture.stop()  # ffmpeg, which would otherwise hold on to the camera
        os._exit(0)

    signal.signal(signal.SIGHUP, hang_up)
    app.run()


if __name__ == "__main__":
    main()
