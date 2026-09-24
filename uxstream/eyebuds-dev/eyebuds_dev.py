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
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# textual/plotext/pillow/textual-image are not distro packages worth depending on; pip is the
# only sane source. plotext is pinned below 6.0: from 6.0 on it ships a compiled C++ kernel that
# a plain `pip install` on NixOS cannot load (no FHS libstdc++), while 5.3.2 is pure Python and
# draws the same braille/block charts. textual-image draws the camera picture with real terminal
# pixels (Kitty's graphics protocol, or Sixel) when the terminal on the viewing end supports it,
# falling back to coloured half-cells otherwise -- detected by asking the terminal, not by
# guessing from $TERM, so it works the same whether you are sitting at the machine or over SSH.
PIP_PACKAGES = ["textual", "plotext==5.3.2", "pyserial", "pillow", "textual-image"]
# Only the camera needs these, and only outside of the Nix build (which wires ffmpeg onto PATH
# itself). Package name is the same across the big three.
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
        import textual, textual_image, plotext, serial  # noqa: F401
        from PIL import Image  # noqa: F401
        return
    except ImportError as error:
        if os.environ.get("EYEBUDDY_BOOTSTRAPPED"):
            sys.exit(f"EyeBuddy needs a few Python packages: {error}")
        print(f"EyeBuddy needs a few Python packages: {error}")
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

import plotext as plt  # noqa: E402
import serial  # noqa: E402
from PIL import Image  # noqa: E402
from rich.color import Color  # noqa: E402
from rich.style import Style  # noqa: E402
from rich.text import Text  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.widgets import DataTable, Footer, ProgressBar, RichLog, Static  # noqa: E402
from textual_image.renderable import Image as _AutoRenderable  # noqa: E402
from textual_image.widget import AutoImage  # noqa: E402

PLUGIN = "erik/stlink:service"
DATA_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/plugins/data/erik/stlink"
# Remembered between runs: camera rotation and size, mutes, raw/pretty view.
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
MAX_BANDS = 4  # stacked panels the chart has room to draw
MAX_PER_BAND = 5  # lines sharing one band's axis before the rest wait their turn
CAMERA_FPS = 6  # the terminal redraws the picture this often; SSH bandwidth is the limit, not the sensor
# The sensor trades resolution for framerate: 30 fps at 640x480, 7 fps at 1600x1200.
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
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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

    def latest(self):
        with self._lock:
            return self._frame

    def stop(self):
        self._stop.set()
        if self.proc:
            self.proc.terminate()


class SerialReader(threading.Thread):
    """Reads the first ttyACM/ttyUSB port, logs to a file and hands lines to the UI.

    Reconnects on its own, so a reflash that re-enumerates the port just shows a gap.
    """

    def __init__(self, baud, logdir, on_line):
        super().__init__(daemon=True)
        self.baud, self.logdir, self.on_line = baud, Path(logdir), on_line
        self.port = ""  # not None, so the first look with nothing plugged in still says so
        self.warned = False

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
                    log.write(data)
                    log.flush()
                    self.on_line(data.decode("utf-8", "replace"), None)


class ChartModel:
    """The live series every telemetry field feeds: a rolling window per key, nothing about how
    to draw it. ChartPanel below turns whichever of these are worth showing into a plotext figure.
    """

    def __init__(self):
        self.series = {}

    def add_series(self, key, color, label, unit, pattern):
        band, factor = band_of(label, unit)
        self.series[key] = {"key": key, "color": color, "label": label, "unit": unit, "pattern": pattern,
                            "group": band, "factor": factor, "t": collections.deque(), "v": collections.deque(),
                            "value": None, "lo": None, "hi": None, "flag": 0.0}

    def drop_pattern(self, pattern):
        for key in [k for k, s in self.series.items() if s["pattern"] == pattern]:
            del self.series[key]

    def flag(self, key):
        if key in self.series:
            self.series[key]["flag"] = time.time()

    def push(self, key, value, lo, hi):
        s = self.series.get(key)
        if s is None:
            return
        factor = s["factor"]
        value, lo, hi = value * factor, lo * factor, hi * factor
        now = time.time()
        s["value"], s["lo"], s["hi"] = value, lo, hi
        s["t"].append(now)
        s["v"].append(value)
        cutoff = now - CHART_WINDOW
        while s["t"] and s["t"][0] < cutoff:  # what leaves the window is forgotten
            s["t"].popleft()
            s["v"].popleft()

    def worth_listing(self, limit):
        """The fields worth a row: still reporting, actually moving, most restless first."""
        now = time.time()
        scored = []
        for s in self.series.values():
            if s["lo"] is None or s["hi"] <= s["lo"] or not s["t"]:
                continue  # never seen two different values, so there is nothing to compare
            if s["t"][-1] < now - CHART_WINDOW:
                continue  # gone quiet
            lo_v, hi_v = min(s["v"]), max(s["v"])
            scale = max(abs(hi_v), abs(lo_v), 1e-9)
            movement = (hi_v - lo_v) / scale
            if movement < (0.35 if s["key"].endswith("#rate") else 0.005):
                continue
            recent = now - s["flag"] < OUTLIER_TTL
            scored.append((movement + (10 if recent else 0), s))
        # Round-robin over the log lines they came from, so one chatty message cannot fill the
        # table with variations on itself before other messages get a row at all.
        buckets = {}
        for score, s in sorted(scored, key=lambda pair: -pair[0]):
            buckets.setdefault(s["pattern"], []).append(s)
        chosen = []
        while len(chosen) < limit and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(chosen) < limit:
                    chosen.append(bucket.pop(0))
        return chosen


def rgb_of(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


class CameraView(AutoImage, Renderable=_AutoRenderable):
    """The camera picture, drawn at any angle and grown to fill its box.

    AutoImage draws with the terminal's real pixels -- Kitty's graphics protocol or Sixel --
    when the terminal on the viewing end supports it (asked for directly, so this is the same
    over SSH as sitting at the machine), and falls back to coloured half-cells otherwise.
    """

    def __init__(self, capture, **kwargs):
        super().__init__(None, **kwargs)
        self.capture = capture
        self.angle = 0.0

    def set_capture(self, capture):
        self.capture = capture
        if capture is None:
            self.image = None

    def set_angle(self, angle):
        self.angle = angle % 360

    def redraw(self):
        frame = self.capture.latest() if self.capture else None
        if not frame:
            return
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
        img = img.resize((max(1, round(fw * scale)), max(1, round(fh * scale))), Image.BILINEAR)
        if self.angle:
            img = img.rotate(-self.angle, resample=Image.BILINEAR, expand=True)
        left = (img.width - box_w) // 2
        top = (img.height - box_h) // 2
        self.image = img.crop((left, top, left + box_w, top + box_h))


class ChartPanel(Static):
    """Every worth-listing field, banded by unit, drawn by plotext: one subplot per band."""

    def redraw(self, chosen):
        w, h = self.size.width, self.size.height
        if w < 10 or h < 6:
            return
        if not chosen:
            self.update(Text("(waiting for telemetry…)", style="dim"))
            return
        members = {}
        for s in chosen:
            decade = int(math.log10(max(abs(s["hi"] or 1), 1)) // 3)
            members.setdefault((s["group"], decade), []).append(s)
        for key in members:
            members[key] = members[key][:MAX_PER_BAND]
        bands = sorted(members, key=lambda k: (BAND_ORDER.index(k[0]) if k[0] in BAND_ORDER
                                               else len(BAND_ORDER), k[1]))[:MAX_BANDS]
        now = time.time()
        plt.clear_figure()
        plt.theme("pro")
        plt.plotsize(w, h)
        plt.subplots(len(bands), 1)
        for i, band in enumerate(bands, start=1):
            plt.subplot(i, 1)
            plt.title(BAND_UNIT.get(band[0], band[0]) or "count")
            for s in members[band]:
                if len(s["t"]) < 2:
                    continue
                x = [t - now for t in s["t"]]
                plt.plot(x, list(s["v"]), color=rgb_of(s["color"]), label=s["label"][:20])
        self.update(Text.from_ansi(plt.build()))


class EyeBuddyApp(App):
    TITLE = "EyeBuddy"
    CSS = """
    Screen { background: $surface; }
    #status { height: 1; background: $panel; color: $text; padding: 0 1; }
    #body { height: 1fr; }
    #main { width: 1fr; }
    #chart { height: 1fr; min-height: 12; border: round $boost; }
    #stats { height: 10; border: round $boost; }
    #log { height: 1fr; border: round $boost; }
    #sidebar { width: 34; }
    #camera-wrap { height: 24; border: round $boost; align: center middle; }
    #camera { height: 1fr; width: auto; }
    #settings { height: auto; padding: 0 1; border: round $boost; }
    #build-info { height: auto; padding: 0 1; }
    #progress { height: 1; margin: 0 1; }
    #mutes { height: auto; padding: 0 1; }
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
        Binding("m", "mute_last", "Mute last"),
        Binding("u", "unmute_all", "Unmute all"),
        Binding("question_mark", "toggle_dark", "Theme", show=False),
    ] + [Binding(str(n), f"unmute_index({n})", f"Unmute {n}", show=False) for n in range(1, 10)]

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.settings = read_json(SETTINGS) or {}
        self.angle = self.settings.get("rotation", args.rotate) % 360
        self.modes = camera_modes(args.device) if not args.no_camera else CAMERA_SIZES
        self.camera_size = self.settings.get("size", self.modes[0][0])
        self.camera_capture = None
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
        self.muted = set(self.settings.get("muted", []))
        self.ranges = self.settings.get("ranges", {})  # pattern -> [[min, max], ...]
        self.ranges_dirty = False
        self.stats = {}  # (pattern, field index) -> [n, mean, m2, last_warning], for outliers
        self.rows = {}  # pattern -> row state (numeric fields, their ranges, the arrival rate)
        self.seen = {}  # pattern -> occurrences before it earns a place on the chart
        self.module_hue = {}  # module -> its hue on the chart, handed out as modules turn up
        self.chart = ChartModel()
        self.lines = collections.deque(maxlen=5000)  # complete raw backlog, mute-independent
        self.partial = ""
        self.recent = collections.OrderedDict()  # pattern -> pending repeat count, for the log
        self.outliers = collections.deque(maxlen=OUTLIER_SHOWN)

    # --- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        with Horizontal(id="body"):
            with Vertical(id="main"):
                yield ChartPanel(id="chart")
                yield DataTable(id="stats", cursor_type="none")
                yield RichLog(id="log", max_lines=5000, wrap=False, highlight=False, markup=False)
            with Vertical(id="sidebar"):
                yield Static(id="settings")
                yield Static(id="build-info")
                yield ProgressBar(id="progress", total=100, show_eta=False)
                yield Static(id="mutes")
                yield Static(id="outliers")
        # Full terminal width, not squeezed into the sidebar, and centered since the image keeps
        # its own aspect ratio rather than being stretched to fill the band.
        with Vertical(id="camera-wrap"):
            yield CameraView(self.camera_capture, id="camera")
        yield Footer()

    def on_mount(self):
        self.log_view = self.query_one("#log", RichLog)
        self.camera_view = self.query_one("#camera", CameraView)
        self.camera_view.set_angle(self.angle)
        self.chart_panel = self.query_one("#chart", ChartPanel)
        self.query_one("#stats", DataTable).add_columns("", "field", "min", "now", "max")
        self._refresh_settings_panel()
        self._refresh_mutes_panel()
        self._update_status()
        if self.camera_enabled:
            self._restart_camera()
        elif not self.args.no_camera:
            why = "ffmpeg not found" if not shutil.which("ffmpeg") else f"no camera at {self.args.device}"
            self._note(f"No camera: {why}. Press K once it's available.")
        self.serial = SerialReader(self.args.baud, self.args.logdir,
                                   lambda text, status: self.call_from_thread(self._on_serial, text, status))
        self.serial.start()
        self.set_interval(1 / CAMERA_FPS, self._redraw_camera)
        self.set_interval(1.0, self._redraw_chart)
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
        self.camera_view.set_capture(self.camera_capture)

    def _redraw_camera(self):
        if self.camera_enabled:
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

    # --- chart + stats ------------------------------------------------------

    def _redraw_chart(self):
        chosen = self.chart.worth_listing(RANGE_ROWS)
        self.chart_panel.redraw(chosen)
        self._redraw_stats(chosen)

    def _redraw_stats(self, chosen):
        table = self.query_one("#stats", DataTable)
        table.clear()
        for s in chosen:
            if s["lo"] is None:
                continue
            dot = Text("●", style=Style(color=Color.parse(s["color"])))
            table.add_row(dot, s["label"][:22], fmt_si(s["lo"]), fmt_si(s["value"]), fmt_si(s["hi"]))

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
            if self._matches(line):
                self._show_line(line)
        self._update_status()

    def _matches(self, line):
        return not (self.muted and pattern_of(ANSI.sub("", line)) in self.muted)

    def _show_line(self, line):
        if self.pretty and self._to_table(line):
            return
        if self.pretty:
            self._render_pretty(line)
        else:
            self.log_view.write(Text.from_ansi(line.rstrip("\n")))

    def _to_table(self, raw):
        """Route a repeating telemetry line to the chart. Returns False for lines the log keeps."""
        plain = ANSI.sub("", raw)
        m = LOG_LINE.match(plain.rstrip("\r\n"))
        if not m:
            return False
        level, module, message = m.group(2).upper(), m.group(3), m.group(5)
        if level in ("WARN", "WARNING", "ERROR", "FATAL"):
            return False
        key = pattern_of(plain)
        if key not in self.rows:
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] < 2:
                return False  # said once so far; a repeat earns it a row
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
                                  f"{module} {text}" if text else message[:24], unit, key)
            fields_out.append({"series": series, "label": text, "unit": unit})
            at = num.end()
        # How often this line arrives, so a stream that reports steady numbers still shows up.
        rate_series = f"{key}#rate"
        self.chart.add_series(rate_series, self._series_color(module, len(fields_out)),
                              f"{module} {(fields_out[0]['label'] if fields_out else message)[:18]} rate", "/s", key)
        fields_out.append({"series": rate_series, "label": "rate", "unit": "/s"})
        ranges = self.ranges.setdefault(key, [[None, None] for _ in fields_out])
        while len(ranges) < len(fields_out):
            ranges.append([None, None])
        return {"fields": fields_out, "ranges": ranges, "key": key, "module": module, "last_ts": None,
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
            rng = state["ranges"][-1]
            rng[0] = rate if rng[0] is None else min(rng[0], rate)
            rng[1] = rate if rng[1] is None else max(rng[1], rate)
            self.chart.push(rate_field["series"], rate, rng[0], rng[1])
        if not state["numeric"]:
            return
        for i, (field, num, rng) in enumerate(zip(state["fields"], NUMBERS.finditer(message), state["ranges"])):
            v = float(num.group())
            if rng[0] is None or v < rng[0] or rng[1] is None or v > rng[1]:
                rng[0] = v if rng[0] is None else min(rng[0], v)
                rng[1] = v if rng[1] is None else max(rng[1], v)
                self.ranges_dirty = True
            if self._check_outlier(state["key"], i, v, field["label"], module):
                self.chart.flag(field["series"])
            self.chart.push(field["series"], v, rng[0], rng[1])

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
        text = Text()
        for when, msg in reversed(self.outliers):
            text.append("! ", style=Style(color="#e5c07b", bold=True))
            text.append(msg + "\n", style=Style())
        panel.update(text)

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
        text = Text()
        text.append(ts[3:] + " ", style=Style(color="#7f848e"))
        text.append(glyph + " ", style=Style(color=color, bold=True))
        text.append(f"{module:<4} ", style=Style(color=self._module_color(raw, module), bold=True))
        text.append(message, style=Style(color=color if loud else None))
        if count > 1:
            text.append(f"  ×{count}", style=Style(color="#e5c07b", bold=True))
        text.append("  " + location, style=Style(color="#5c6370"))
        self.log_view.write(text)

    def _note(self, text):
        """A line from the app itself, in among the firmware's own."""
        self._flush_recent()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.write(Text(f"{stamp} · {text}", style=Style(color="#56b6c2")))

    def _warn(self, text):
        self.outliers.append((time.time(), text))
        self._flush_recent()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.write(Text(f"{stamp} ▲ {text}", style=Style(color="#e5c07b", bold=True)))

    # --- mute / view toggles -----------------------------------------------

    def _set_muted(self, muted):
        for pattern in muted - self.muted:
            self.chart.drop_pattern(pattern)
            self.rows.pop(pattern, None)
        self.muted = muted
        save_settings(muted=sorted(muted))
        self._refresh_mutes_panel()
        self._refilter()

    def action_mute_last(self):
        if self.lines:
            self._set_muted(self.muted | {pattern_of(ANSI.sub("", self.lines[-1]))})

    def action_unmute_all(self):
        self._set_muted(set())

    def action_unmute_index(self, n):
        ordered = sorted(self.muted)
        if 0 < n <= len(ordered):
            self._set_muted(self.muted - {ordered[n - 1]})

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
            if self._matches(line):
                self._show_line(line)
        self._update_status()

    def _refresh_mutes_panel(self):
        panel = self.query_one("#mutes", Static)
        if not self.muted:
            panel.update("")
            return
        rows = [f"{i}: {p[:36]}" for i, p in enumerate(sorted(self.muted), start=1)]
        panel.update("Muted (press the number to unmute):\n" + "\n".join(rows))

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
        if self.muted:
            parts.append(f"{len(self.muted)} muted")
        self.query_one("#status", Static).update("EyeBuddy — " + " · ".join(p for p in parts if p))

    def _refresh_settings_panel(self):
        lines = [f"Build: {self.build_type} / {self.build_env}"]
        if self.camera_enabled:
            fps = dict(self.modes).get(self.camera_size, "?")
            lines.append(f"Camera: {self.camera_size} @ {fps}fps · {self.angle:.0f}°")
        else:
            lines.append("Camera: off")
        self.query_one("#settings", Static).update("\n".join(lines))


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

    EyeBuddyApp(args).run()


if __name__ == "__main__":
    main()
